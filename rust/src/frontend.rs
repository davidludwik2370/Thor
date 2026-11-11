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
    pub executable: PathBuf,
    pub fingerprint: String,
    pub odin: PathBuf,
    pub cache_dir: Option<PathBuf>,
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

fn cached_payload(data: &[u8]) -> Option<Value> {
    let _phase = crate::profile::span("parser.cache_validation");
    let packet: ParsePacket<'_> = serde_json::from_slice(data).ok()?;
    if packet.version != 1
        || packet.schema != "odin-frontend-raw-v1"
        || packet.sha256 != sha256(packet.payload.get().as_bytes())
    {
        return None;
    }
    decode(packet.payload.get().as_bytes()).ok()
}

fn decode(bytes: &[u8]) -> serde_json::Result<Value> {
    let _phase = crate::profile::span("parser.decode");
    serde_json::from_slice(bytes)
}
pub fn executable(name: &str) -> Result<PathBuf> {
    let path = PathBuf::from(name);
    if path.components().count() > 1 && path.is_file() {
        return Ok(fs::canonicalize(path)?);
    }
    for base in env::split_paths(&env::var_os("PATH").unwrap_or_default()) {
        let candidate = base.join(name);
        if candidate.is_file() {
            return Ok(fs::canonicalize(candidate)?);
        }
        #[cfg(windows)]
        {
            let candidate = base.join(format!("{name}.exe"));
            if candidate.is_file() {
                return Ok(fs::canonicalize(candidate)?);
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
pub fn bootstrap(root: &Path, odin: &str) -> Result<(PathBuf, PathBuf)> {
    let _phase = crate::profile::span("parser.bootstrap");
    let odin = executable(odin)?;
    let version = Command::new(&odin).arg("version").output()?;
    if !version.status.success() {
        return Err(Error::new("could not query Odin version"));
    }
    let mut bytes = odin.to_string_lossy().as_bytes().to_vec();
    bytes.extend_from_slice(&version.stdout);
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
        let result = Command::new(&odin)
            .arg("build")
            .arg(source_root)
            .arg("-o:speed")
            .arg(format!("-out:{}", intermediate.display()))
            .status();
        let result = match result {
            Ok(status) if status.success() => {
                fs::rename(&intermediate, &binary).map_err(Error::from)
            }
            Ok(_) => Err(Error::new("could not build Thor's Odin frontend")),
            Err(error) => Err(Error::from(error)),
        };
        let _ = fs::remove_dir_all(work);
        result?;
    }
    Ok((binary, odin))
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
        let _phase = crate::profile::span("parser.source");
        crate::profile::count("parser.source_requests", 1);
        let path = if overlay.is_some() {
            crate::project::absolute(path)?
        } else {
            fs::canonicalize(path)?
        };
        let bytes = if let Some(raw) = overlay {
            raw.as_bytes().to_vec()
        } else {
            fs::read(&path)?
        };
        let raw = std::str::from_utf8(&bytes)
            .map_err(|_| Error::new(format!("source is not UTF-8: {}", path.display())))?;
        let thor = path.extension().is_some_and(|ext| ext == "thor");
        let mut args = vec![path.to_string_lossy().into_owned()];
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
        args.push("--stdin".into());
        let mut key = self.fingerprint.as_bytes().to_vec();
        key.extend_from_slice(&serde_json::to_vec(&args)?);
        key.extend_from_slice(&bytes);
        let cache = self.cache_dir.as_ref().map(|base| {
            base.join("rust-parse-v1")
                .join(format!("{}.json", sha256(&key)))
        });
        if let Some(cache) = &cache {
            if let Ok(data) = fs::read(cache) {
                if let Some(info) = cached_payload(&data) {
                    if let Ok(source) = Source::from_frontend(path.clone(), raw.into(), info, thor)
                    {
                        crate::profile::count("parser.cache_hits", 1);
                        crate::profile::count("parser.nodes", source.nodes.len() as u64);
                        return Ok(source);
                    }
                }
            }
        }
        let raw = raw.to_owned();
        crate::profile::count("parser.processes", 1);
        let (status, stdout, stderr) = self.run(&args, bytes)?;
        let info = decode(&stdout).map_err(|_| {
            Error::new(format!(
                "invalid frontend output for {}: {}",
                path.display(),
                String::from_utf8_lossy(&stderr)
            ))
        })?;
        if !status.success() {
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
                let end = error["pos"]["end_offset"]
                    .as_u64()
                    .map(|n| n as usize)
                    .unwrap_or(offset + length)
                    .min(raw.len());
                let before = &raw[..offset];
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
            }
            return Err(Error {
                message: String::from_utf8_lossy(&stderr).trim().to_string(),
                diagnostics: errors,
                unsupported: false,
                exit_status: 1,
            });
        }
        let source = Source::from_frontend(path, raw, info, thor)?;
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
            let restored = cached_payload(&stored);
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
