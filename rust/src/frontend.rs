use crate::{
    ast::Source,
    diagnostic::{Error, Result},
    digest::sha256,
};
use serde::Deserialize;
use serde_json::{value::RawValue, Value};
use std::{
    env, fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

#[derive(Clone)]
pub struct Frontend {
    pub odin_syntax: bool,
    pub executable: PathBuf,
    pub fingerprint: String,
    pub odin: PathBuf,
    pub odin_root: String,
    pub cache_dir: Option<PathBuf>,
    pub normalized_cache: bool,
    pub test: bool,
    pub target: Option<(String, String)>,
    pub project_name: String,
}

#[derive(Deserialize)]
struct ParsePacket<'a> {
    version: u32,
    schema: &'a str,
    sha256: &'a str,
    #[serde(borrow)]
    payload: &'a RawValue,
}

fn cached_payload(data: &[u8]) -> Option<&[u8]> {
    let _phase = crate::profile::span("parser.cache_validation");
    let packet: ParsePacket<'_> = serde_json::from_slice(data).ok()?;
    if packet.version != 1
        || packet.schema != "odin-frontend-raw-v1"
        || packet.sha256 != sha256(packet.payload.get().as_bytes())
    {
        return None;
    }
    Some(packet.payload.get().as_bytes())
}

fn decode(bytes: &[u8]) -> serde_json::Result<Value> {
    let _phase = crate::profile::span("parser.decode");
    serde_json::from_slice(bytes)
}
pub fn executable(name: &str) -> Result<PathBuf> {
    let path = PathBuf::from(name);
    if path.components().count() > 1 && path.is_file() {
        return Ok(crate::project::canonicalize(path)?);
    }
    for base in env::split_paths(&env::var_os("PATH").unwrap_or_default()) {
        let candidate = base.join(name);
        if candidate.is_file() {
            return Ok(crate::project::canonicalize(candidate)?);
        }
        #[cfg(windows)]
        {
            let candidate = base.join(format!("{name}.exe"));
            if candidate.is_file() {
                return Ok(crate::project::canonicalize(candidate)?);
            }
        }
    }
    Err(Error::new(format!(
        "Odin compiler not found: {name}; set THOR_ODIN or use --odin"
    )))
}
fn odin_sources(path: &Path, files: &mut Vec<PathBuf>) -> Result<()> {
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            odin_sources(&entry.path(), files)?;
        } else if entry.path().extension().is_some_and(|ext| ext == "odin") {
            files.push(entry.path());
        }
    }
    Ok(())
}
pub fn bootstrap(root: &Path, odin: &str) -> Result<(PathBuf, PathBuf, String)> {
    let _phase = crate::profile::span("parser.bootstrap");
    let odin = executable(odin)?;
    let version = crate::project::capture_command(Command::new(&odin).arg("version"), Duration::ZERO)?;
    if !version.status.success() || version.oversized {
        return Err(Error::new("could not query Odin version"));
    }
    let sdk = crate::project::capture_command(Command::new(&odin).arg("root"), Duration::ZERO)?;
    if !sdk.status.success() || sdk.oversized { return Err(Error::new("could not query Odin SDK root")); }
    let odin_root = String::from_utf8_lossy(&sdk.stdout).trim().to_string();
    let builder = if let Some(name) = env::var_os("THOR_FRONTEND_ODIN") {
        executable(&name.to_string_lossy())?
    } else { odin.clone() };
    let mut bytes = odin.to_string_lossy().as_bytes().to_vec();
    bytes.extend_from_slice(b"frontend-bootstrap-custom-synchronizes-v1");
    bytes.extend_from_slice(&version.stdout);
    bytes.extend_from_slice(builder.to_string_lossy().as_bytes());
    if builder != odin {
        let version = crate::project::capture_command(Command::new(&builder).arg("version"), Duration::ZERO)?;
        if !version.status.success() || version.oversized {
            return Err(Error::new("could not query frontend builder version"));
        }
        bytes.extend_from_slice(&version.stdout);
    }
    let source_root = root.join("frontend");
    let mut files = vec![];
    odin_sources(&source_root, &mut files)?;
    files.sort();
    for path in files {
        let relative = path
            .strip_prefix(root)
            .map_err(|_| Error::new("frontend outside toolchain root"))?;
        bytes.extend_from_slice(relative.to_string_lossy().replace('\\', "/").as_bytes());
        bytes.extend_from_slice(&fs::read(path)?);
    }
    let directory = root.join(".thor-cache");
    fs::create_dir_all(&directory)?;
    let binary = directory.join(format!(
        "frontend-{}{}",
        &sha256(&bytes)[..20],
        env::consts::EXE_SUFFIX
    ));
    if !binary.is_file() {
        let work = directory.join(format!("rust-frontend-{}-{}", std::process::id(), unique()));
        fs::create_dir(&work)?;
        let intermediate = work.join(format!("frontend{}", env::consts::EXE_SUFFIX));
        let result = (|| -> Result<()> {
            let output = crate::project::capture_command(
                Command::new(&builder)
                    .arg("build")
                    .arg(source_root)
                    .arg("-o:speed")
                    .arg("-custom-attribute:synchronizes")
                    .arg(format!("-out:{}", intermediate.display())),
                Duration::ZERO,
            )?;
            std::io::stdout().write_all(&output.stdout)?;
            std::io::stderr().write_all(&output.stderr)?;
            if !output.status.success() || output.oversized {
                return Err(Error::new("could not build Thor's Odin frontend"));
            }
            fs::rename(&intermediate, &binary)?;
            Ok(())
        })();
        let _ = fs::remove_dir_all(work);
        result?;
    }
    Ok((binary, odin, odin_root))
}
fn unique() -> String {
    static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let sequence = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let time = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("{time}-{sequence}")
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| Error::new("output has no parent"))?;
    fs::create_dir_all(parent)?;
    let temp = parent.join(format!(".thor-rust-{}-{}", std::process::id(), unique()));
    let result = (|| {
        let mut file = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temp)?;
        file.write_all(bytes)?;
        fs::rename(&temp, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

pub(crate) fn clear_parser_cache(base: &Path) {
    fn digest_name(name: &str) -> bool {
        name.len() == 64 && name.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    }
    fn clear_files(directory: &Path, extension: &str) {
        if directory.is_symlink() {
            return;
        }
        let Ok(entries) = fs::read_dir(directory) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if entry.file_type().is_ok_and(|kind| kind.is_file())
                && path.extension().is_some_and(|value| value == extension)
                && path.file_stem().and_then(|value| value.to_str()).is_some_and(digest_name)
            {
                let _ = fs::remove_file(path);
            }
        }
    }
    clear_files(&base.join("rust-parse-v1"), "json");
    let syntax = base.join("rust-syntax-v1");
    if syntax.is_symlink() {
        return;
    }
    if let Ok(entries) = fs::read_dir(syntax) {
        for entry in entries.flatten() {
            if entry.file_type().is_ok_and(|kind| kind.is_dir())
                && entry.file_name().to_str().is_some_and(digest_name)
            {
                clear_files(&entry.path(), "bin");
            }
        }
    }
}
impl Frontend {
    pub fn parse(&self, path: &Path, package: bool) -> Result<Source> {
        self.parse_overlay(path, package, None)
    }
    pub fn parse_overlay(
        &self,
        path: &Path,
        package: bool,
        overlay: Option<&str>,
    ) -> Result<Source> {
        self.parse_options(path,package,overlay,0)
    }
    pub(crate) fn parse_native_style(&self, source: &Source, style: u8) -> Result<Source> {
        self.parse_options(&source.path,false,Some(&source.raw),style)
    }
    fn parse_options(&self,path:&Path,package:bool,overlay:Option<&str>,style:u8)->Result<Source> {
        let _phase = crate::profile::span("parser.source");
        crate::profile::count("parser.source_requests", 1);
        let path = if overlay.is_some() {
            crate::project::absolute(path)?
        } else {
            crate::project::canonicalize(path)?
        };
        let bytes = if let Some(raw) = overlay {
            raw.as_bytes().to_vec()
        } else {
            fs::read(&path)?
        };
        let raw = std::str::from_utf8(&bytes)
            .map_err(|_| Error::new(format!("source is not UTF-8: {}", path.display())))?;
        let thor = !self.odin_syntax && path.extension().is_some_and(|ext| ext == "thor");
        let mut args = vec![path.to_string_lossy().into_owned()];
        if !self.odin_root.is_empty() {
            args.push(format!("--odin-root={}", self.odin_root));
        }
        if !thor {
            args.push("--odin".into());
        }
        if package {
            args.push("--package".into());
        }
        if self.test {
            args.push("--test".into());
        }
        args.push(format!("--project-name={}", self.project_name));
        if let Some((os, arch)) = &self.target {
            args.push(format!("--target-os={os}"));
            args.push(format!("--target-arch={arch}"));
        }
        if style & 1 != 0 {args.push("--native-vet-style".into());}
        if style & 2 != 0 {args.push("--native-strict-style".into());}
        if style & 4 != 0 {args.push("--native-vet-tabs".into());}
        args.push("--stdin".into());
        let mut key = self.fingerprint.as_bytes().to_vec();
        key.extend_from_slice(&serde_json::to_vec(&args)?);
        key.extend_from_slice(&bytes);
        let key = sha256(&key);
        let normalized = self.cache_dir.as_ref().filter(|_| self.normalized_cache).map(|base| {
            base.join("rust-syntax-v1").join(crate::ast::cache::revision())
                .join(format!("{key}.bin"))
        });
        if let Some(cache) = &normalized {
            if let Ok(data) = fs::read(cache) {
                if let Some(source) = crate::ast::cache::read(path.clone(), raw.into(), thor, &key, &data) {
                    crate::profile::count("parser.syntax_cache_hits", 1);
                    crate::profile::count("parser.nodes", source.nodes.len() as u64);
                    return Ok(source);
                }
            }
        }
        let cache = self.cache_dir.as_ref().map(|base| {
            base.join("rust-parse-v1")
                .join(format!("{key}.json"))
        });
        if let Some(cache) = &cache {
            if let Ok(data) = fs::read(cache) {
                if let Some(packet) = cached_payload(&data) {
                    if let Ok(source) = Source::from_frontend_bytes(path.clone(), raw.into(), packet, thor)
                    {
                        crate::profile::count("parser.cache_hits", 1);
                        crate::profile::count("parser.nodes", source.nodes.len() as u64);
                        if let Some(cache) = &normalized {
                            let _ = atomic_write(cache, &crate::ast::cache::write(&source, &key));
                        }
                        return Ok(source);
                    }
                }
            }
        }
        let raw = raw.to_owned();
        crate::profile::count("parser.processes", 1);
        let (status, stdout, stderr) = self.run(&args, bytes)?;
        if !status.success() {
        let info = decode(&stdout).map_err(|_| {
            Error::new(format!(
                "invalid frontend output for {}: {}",
                path.display(),
                String::from_utf8_lossy(&stderr)
            ))
        })?;
            let mut errors = info["errors"].as_array().cloned().unwrap_or_default();
            for error in &mut errors {
                if error["pos"]["file"].as_str() != path.to_str() {
                    continue;
                }
                let Some(offset) = error["pos"]["offset"].as_u64().map(|n| n as usize) else {
                    continue;
                };
                let mut offset = offset.min(raw.len());
                while !raw.is_char_boundary(offset) {
                    offset -= 1;
                }
                let rest = &raw[offset..];
                let word = rest
                    .chars()
                    .next()
                    .is_some_and(|ch| ch.is_alphanumeric() || ch == '_');
                let length = if word {
                    rest.chars()
                        .take_while(|ch| ch.is_alphanumeric() || *ch == '_')
                        .map(char::len_utf8)
                        .sum()
                } else {
                    rest.chars().next().map_or(0, char::len_utf8)
                };
                let mut end = error["pos"]["end_offset"]
                    .as_u64()
                    .map(|n| n as usize)
                    .unwrap_or(offset + length)
                    .max(offset).min(raw.len());
                while !raw.is_char_boundary(end) { end += 1; }
                let span = crate::diagnostic::whole_word_span(&raw, crate::ast::Span::new(offset, end));
                let (offset, end) = (span.start, span.end);
                let before = &raw[..offset];
                error["pos"]["offset"] = serde_json::json!(offset);
                error["pos"]["line"] =
                    serde_json::json!(before.bytes().filter(|ch| *ch == b'\n').count() + 1);
                error["pos"]["column"] = serde_json::json!(
                    before
                        .rsplit('\n')
                        .next()
                        .unwrap_or("")
                        .encode_utf16()
                        .count()
                        + 1
                );
                error["pos"]["end_offset"] = serde_json::json!(end);
                let before_end = &raw[..end];
                error["pos"]["end_line"] = serde_json::json!(before_end.bytes().filter(|ch| *ch == b'\n').count() + 1);
                error["pos"]["end_column"] = serde_json::json!(before_end.rsplit('\n').next().unwrap_or("").encode_utf16().count() + 1);
            }
            return Err(Error {
                message: String::from_utf8_lossy(&stderr).trim().to_string(),
                diagnostics: errors,
                unsupported: false,
                exit_status: 1,
            });
        }
        let source = Source::from_frontend_bytes(path, raw, &stdout, thor)?;
        if let Some(cache) = &normalized {
            let _ = atomic_write(cache, &crate::ast::cache::write(&source, &key));
        }
        crate::profile::count("parser.nodes", source.nodes.len() as u64);
        if let Some(cache) = cache {
            // Persist only parser output. Normalization and semantic facts must
            // be rebuilt by the current compiler even if its implementation changes.
            let payload = std::str::from_utf8(&stdout)
                .map_err(|_| Error::new("frontend JSON is not UTF-8"))?
                .trim()
                .as_bytes();
            let digest = sha256(payload);
            let mut packet=format!("{{\"version\":1,\"schema\":\"odin-frontend-raw-v1\",\"sha256\":\"{digest}\",\"payload\":").into_bytes();
            packet.extend_from_slice(payload);
            packet.push(b'}');
            let _ = atomic_write(&cache, &packet);
        }
        Ok(source)
    }
    fn run(
        &self,
        args: &[String],
        input: Vec<u8>,
    ) -> Result<(std::process::ExitStatus, Vec<u8>, Vec<u8>)> {
        let mut child = Command::new(&self.executable)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        let mut stdin = child
            .stdin
            .take()
            .ok_or_else(|| Error::new("frontend stdin unavailable"))?;
        let mut stdout = child
            .stdout
            .take()
            .ok_or_else(|| Error::new("frontend stdout unavailable"))?;
        let mut stderr = child
            .stderr
            .take()
            .ok_or_else(|| Error::new("frontend stderr unavailable"))?;
        // Drain pipes while the process runs; a large AST must not fill a pipe
        // and deadlock either input delivery or timeout handling.
        let writer = thread::spawn(move || stdin.write_all(&input));
        let output = thread::spawn(move || {
            let mut data = vec![];
            stdout.read_to_end(&mut data).map(|_| data)
        });
        let errors = thread::spawn(move || {
            let mut data = vec![];
            stderr.read_to_end(&mut data).map(|_| data)
        });
        let start = Instant::now();
        let mut timed_out = false;
        let status = loop {
            if let Some(status) = child.try_wait()? {
                break status;
            }
            if start.elapsed() > Duration::from_secs(30) {
                timed_out = true;
                let _ = child.kill();
                break child.wait()?;
            }
            thread::sleep(Duration::from_millis(2));
        };
        let written = writer
            .join()
            .map_err(|_| Error::new("frontend input worker failed"))?;
        let stdout = output
            .join()
            .map_err(|_| Error::new("frontend output worker failed"))??;
        let stderr = errors
            .join()
            .map_err(|_| Error::new("frontend error worker failed"))??;
        if timed_out {
            return Err(Error::new(format!("parser timed out: {}", args[0])));
        }
        if status.success() {
            written?;
        }
        Ok((status, stdout, stderr))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn packet(payload: &str) -> Vec<u8> {
        format!("{{\"version\":1,\"schema\":\"odin-frontend-raw-v1\",\"sha256\":\"{}\",\"payload\":{payload}}}", sha256(payload.as_bytes())).into_bytes()
    }
    #[test]
    fn cache_checks_stored_bytes_and_accepts_existing_canonical_packets() {
        let payload = serde_json::json!({"nodes":[],"text":"é 😀","excluded":false});
        let canonical = serde_json::to_string(&payload).unwrap();
        let formatted = serde_json::to_string_pretty(&payload).unwrap();
        for raw in [canonical, formatted] {
            let stored = packet(&raw);
            let restored = cached_payload(&stored).and_then(|raw| serde_json::from_slice::<Value>(raw).ok());
            if restored != Some(payload.clone()) {
                panic!("valid parser cache was rejected");
            }
            for replacement in [
                String::from_utf8(stored.clone()).unwrap().replace("é", "e"),
                String::from_utf8(stored.clone())
                    .unwrap()
                    .replace("\"version\":1", "\"version\":2"),
                String::from_utf8(stored.clone())
                    .unwrap()
                    .replace("raw-v1", "raw-v2"),
            ] {
                if cached_payload(replacement.as_bytes()).is_some() {
                    panic!("changed parser packet was accepted");
                }
            }
        }
        for invalid in [b"{broken".as_slice(), b"{}", b"null", b"[]"] {
            if cached_payload(invalid).is_some() {
                panic!("invalid cache was accepted");
            }
        }
    }
    #[test]
    fn concurrent_cache_writes_publish_complete_packets_and_leave_no_temporary_files() {
        let directory =
            std::env::temp_dir().join(format!("thor-atomic-{}-{}", std::process::id(), unique()));
        fs::create_dir(&directory).unwrap();
        let path = directory.join("cache.json");
        let jobs = (0..48).collect::<Vec<_>>();
        let results = crate::parallel::map(&jobs, |number| {
            let payload = format!("{{\"number\":{number}}}");
            atomic_write(&path, &packet(&payload))
        });
        let published = fs::read(&path).unwrap();
        let entries = fs::read_dir(&directory).unwrap().count();
        let valid = cached_payload(&published).is_some();
        fs::remove_dir_all(&directory).unwrap();
        if results.iter().any(Result::is_err) || entries != 1 || !valid {
            panic!("concurrent cache publication failed");
        }
    }
}
