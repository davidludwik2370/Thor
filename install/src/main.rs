//! A standalone installer: installing Thor does not require Cargo or Python.
mod vscode;
mod jsonc;
mod ols;

use flate2::read::GzDecoder;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, env, fs, io::{Read, Write}, path::{Component, Path, PathBuf}, process::Command};

type Result<T> = std::result::Result<T, String>;
const MARKER: &str = ".thor-installation.json";
const MANIFEST: &str = "THOR-DISTRIBUTION.json";
const HELP: &str = "Thor installer
Usage: thor-install [options]

  --bundle FILE           Install a local native distribution (.tar.gz)
  --sha256 HEX            Expected bundle checksum (otherwise FILE.sha256)
  --prefix DIR            Per-user installation directory
  --vscode                Install Thor, standard Odin/OLS and CodeLLDB in VS Code
  --profile NAME          Create/use a named VS Code profile; requires --vscode
  --colors                Enable recommended Thor colors in a NEW profile
  --no-colors             Leave recommended colors disabled in a NEW profile
  --code PATH             VS Code CLI executable (otherwise discover it)
  --code-user-data DIR    Use a separate VS Code user-data directory
  --extensions-dir DIR    Use a separate VS Code extensions directory
  --path                  Add the command directory to user PATH
  --dry-run               Validate the bundle and print the plan without changes
  --doctor                Check the registered installation without installing
  --help                  Show this help

Named profiles start with Dark Modern and optional Thor-only colors.
Existing profiles/keybindings/projects and unrelated settings are preserved.
On ARM64 Linux, --vscode builds native OLS and configures ols.server.path unless
a custom server is already selected. Without --profile, use the default profile.
The repository bootstrap also accepts --release-base URL and --version VERSION.
";

#[derive(Default)]
struct Options {
    bundle: Option<PathBuf>, checksum: Option<String>, prefix: Option<PathBuf>,
    vscode: bool, profile: Option<String>, colors: Option<bool>, code: Option<PathBuf>,
    code_data: Option<PathBuf>, extensions: Option<PathBuf>, path: bool, dry: bool, doctor: bool,
}

fn parse(args: Vec<String>) -> Result<Options> {
    let mut o = Options::default();
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        let mut value = || args.next().ok_or_else(|| format!("{arg} requires a value"));
        match arg.as_str() {
            "--bundle" => o.bundle = Some(value()?.into()),
            "--sha256" => o.checksum = Some(value()?),
            "--prefix" => o.prefix = Some(value()?.into()),
            "--vscode" => o.vscode = true,
            "--profile" => o.profile = Some(value()?),
            "--colors" => o.colors = Some(true), "--no-colors" => o.colors = Some(false),
            "--code" => o.code = Some(value()?.into()),
            "--code-user-data" => o.code_data = Some(value()?.into()),
            "--extensions-dir" => o.extensions = Some(value()?.into()),
            "--path" => o.path = true, "--dry-run" => o.dry = true, "--doctor" => o.doctor = true,
            _ => return Err(format!("unknown option: {arg}\nRun thor-install --help.")),
        }
    }
    if o.profile.is_some() && !o.vscode { return Err("--profile requires --vscode".into()); }
    if o.colors.is_some() && o.profile.is_none() {
        return Err("--colors/--no-colors requires a named --profile; existing settings are never rewritten. Use the extension's Apply Recommended Colors command instead.".into());
    }
    if o.profile.as_ref().is_some_and(|s| s.trim().is_empty() || s.chars().any(char::is_control)) {
        return Err("profile names must be nonempty and contain no control characters".into());
    }
    if o.profile.as_deref()==Some("Default") {return Err("use --vscode without --profile to target the Default profile".into());}
    if o.doctor && (o.dry || o.vscode || o.path || o.bundle.is_some()) {return Err("--doctor cannot be combined with installation options".into());}
    Ok(o)
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.iter().any(|a| a == "--help" || a == "-h") { print!("{HELP}"); return; }
    if let Err(error) = parse(args).and_then(run) {
        eprintln!("thor-install: {error}"); std::process::exit(1);
    }
}

fn home() -> Result<PathBuf> {
    env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).filter(|s| !s.is_empty())
        .map(PathBuf::from).ok_or_else(|| "could not determine the user's home directory".into())
}
fn data_home() -> Result<PathBuf> {
    Ok(if cfg!(windows) {
        env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or(home()?.join("AppData/Local")).join("Thor")
    } else if cfg!(target_os = "macos") { home()?.join("Library/Application Support/Thor") }
    else { env::var_os("XDG_DATA_HOME").map(PathBuf::from).unwrap_or(home()?.join(".local/share")).join("thor") })
}
fn registry() -> Result<PathBuf> {
    Ok(if cfg!(windows) || cfg!(target_os = "macos") { data_home()?.join("installation.json") }
    else { env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).unwrap_or(home()?.join(".config")).join("thor/installation.json") })
}
fn absolute(p: &Path) -> Result<PathBuf> {
    let p = if p.is_absolute() { p.to_owned() } else { env::current_dir().map_err(err)?.join(p) };
    if p.to_string_lossy().chars().any(char::is_control) { return Err("installation paths must not contain control characters".into()); }
    // Resolve existing parents, without requiring the leaf directory to exist.
    let mut missing = vec![]; let mut parent = p.as_path();
    while !parent.exists() {
        missing.push(parent.file_name().ok_or("invalid path")?.to_owned());
        parent = parent.parent().ok_or("invalid path")?;
    }
    let mut result = parent.canonicalize().map_err(err)?;
    for part in missing.iter().rev() { result.push(part); }
    #[cfg(windows)]
    { result=PathBuf::from(windows_path(&result.to_string_lossy())); }
    Ok(result)
}
#[cfg(any(windows,test))]
fn windows_path(value: &str) -> String {
    if let Some(rest)=value.strip_prefix(r"\\?\UNC\") {format!(r"\\{rest}")}
    else {value.strip_prefix(r"\\?\").unwrap_or(value).to_owned()}
}

#[cfg(test)]
mod tests {
    #[test]
    fn windows_canonical_paths_keep_odin_compatible_spelling() {
        let drive=super::windows_path(r"\\?\C:\Users\david\Thor");
        let unc=super::windows_path(r"\\?\UNC\server\share\Thor");
        let ordinary=super::windows_path(r"C:\Thor");
        assert_eq!(drive,r"C:\Users\david\Thor");
        assert_eq!(unc,r"\\server\share\Thor");
        assert_eq!(ordinary,r"C:\Thor");
    }
}
fn err(e: impl std::fmt::Display) -> String { e.to_string() }
fn hex(bytes: impl AsRef<[u8]>) -> String { bytes.as_ref().iter().map(|b| format!("{b:02x}")).collect() }
fn read_json(p: &Path) -> Result<Value> { serde_json::from_slice(&fs::read(p).map_err(err)?).map_err(err) }
fn field<'a>(v: &'a Value, name: &str) -> Result<&'a str> { v[name].as_str().ok_or_else(|| format!("missing distribution field: {name}")) }
fn digest(p: &Path) -> Result<String> {
    let mut input = fs::File::open(p).map_err(err)?; let mut hash = Sha256::new(); let mut buf = [0u8; 65536];
    loop { let n = input.read(&mut buf).map_err(err)?; if n == 0 { break; } hash.update(&buf[..n]); }
    Ok(hex(hash.finalize()))
}
fn target() -> String {
    format!("{}-{}", match env::consts::OS { "macos" => "macos", other => other }, env::consts::ARCH)
}
fn component(s: &str) -> bool { !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c)) && s != "." && s != ".." }
fn safe_path(p: &Path) -> bool {
    !p.as_os_str().is_empty() && p.components().all(|c| matches!(c, Component::Normal(_)))
        && !p.to_string_lossy().contains(['\\', ':'])
}
fn check_checksum(bundle: &Path, checksum: Option<&str>) -> Result<String> {
    let sidecar = PathBuf::from(format!("{}.sha256", bundle.display()));
    let text = match checksum { Some(s) => s.to_owned(), None => fs::read_to_string(&sidecar).map_err(|e| format!("{}: {e}; provide --sha256", sidecar.display()))? };
    let expected = text.split_whitespace().next().ok_or("empty checksum")?;
    if expected.len() != 64 || !expected.bytes().all(|c| c.is_ascii_hexdigit()) { return Err("invalid SHA-256 checksum".into()); }
    let actual = digest(bundle)?;
    if !actual.eq_ignore_ascii_case(expected) { return Err("bundle SHA-256 mismatch; nothing was installed".into()); }
    Ok(actual)
}

/// Only regular inventoried files are accepted. No archive links or special files
/// can escape staging; duplicate and case-colliding paths are rejected everywhere.
fn unpack(bundle: &Path, destination: Option<&Path>) -> Result<Value> {
    let input = fs::File::open(bundle).map_err(err)?;
    let mut archive = tar::Archive::new(GzDecoder::new(input));
    let mut manifest: Option<Value> = None; let mut seen = BTreeSet::new(); let mut folded = BTreeSet::new();
    let mut total = 0u64;
    for next in archive.entries().map_err(err)? {
        let mut entry = next.map_err(err)?; let path = entry.path().map_err(err)?.into_owned();
        if !safe_path(&path) || !entry.header().entry_type().is_file() { return Err(format!("unsupported archive entry: {}", path.display())); }
        let name = path.to_str().ok_or("non-UTF-8 archive path")?.replace('\\', "/");
        if !seen.insert(name.clone()) || !folded.insert(name.to_lowercase()) { return Err(format!("duplicate archive path: {name}")); }
        let size = entry.size(); total = total.checked_add(size).ok_or("archive size overflow")?;
        if total > 8 * 1024 * 1024 * 1024 || seen.len() > 100_000 { return Err("distribution exceeds installation limits".into()); }
        if name == MANIFEST {
            if seen.len() != 1 || size > 32 * 1024 * 1024 { return Err("distribution manifest must be the first entry".into()); }
            let mut bytes = Vec::new(); entry.read_to_end(&mut bytes).map_err(err)?;
            let value: Value = serde_json::from_slice(&bytes).map_err(err)?;
            validate_manifest(&value)?;
            if let Some(to) = destination { fs::write(to.join(MANIFEST), &bytes).map_err(err)?; }
            manifest = Some(value); continue;
        }
        let value = manifest.as_ref().ok_or("distribution manifest must be the first entry")?;
        let record = value["files"].get(&name).ok_or_else(|| format!("uninventoried file: {name}"))?;
        if record["size"].as_u64() != Some(size) { return Err(format!("incorrect file length: {name}")); }
        let mut hash = Sha256::new(); let mut buf = [0u8; 65536];
        let mut output = if let Some(to) = destination {
            let p = to.join(&path); fs::create_dir_all(p.parent().ok_or("invalid archive parent")?).map_err(err)?;
            Some(fs::OpenOptions::new().write(true).create_new(true).open(p).map_err(err)?)
        } else { None };
        loop { let n = entry.read(&mut buf).map_err(err)?; if n == 0 { break; }
            hash.update(&buf[..n]); if let Some(out) = &mut output { out.write_all(&buf[..n]).map_err(err)?; }
        }
        if record["sha256"].as_str() != Some(hex(hash.finalize()).as_str()) { return Err(format!("incorrect file checksum: {name}")); }
        #[cfg(unix)]
        if let Some(to) = destination {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(to.join(path), fs::Permissions::from_mode(if record["executable"].as_bool() == Some(true) { 0o755 } else { 0o644 })).map_err(err)?;
        }
    }
    let value = manifest.ok_or("missing distribution manifest")?;
    let files = value["files"].as_object().ok_or("invalid file inventory")?;
    if seen.len() != files.len() + 1 { return Err("distribution is missing inventoried files".into()); }
    Ok(value)
}
fn validate_manifest(v: &Value) -> Result<()> {
    if v["schema"] != 1 || v["product"] != "Thor" { return Err("not a supported Thor distribution".into()); }
    if field(v,"target")? != target() { return Err(format!("this bundle targets {}; this computer is {}", field(v,"target")?,target())); }
    if !component(field(v,"version")?) { return Err("invalid release version".into()); }
    let files = v["files"].as_object().ok_or("missing file inventory")?;
    let suffix = env::consts::EXE_SUFFIX;
    for required in [format!("thor{suffix}"),format!("thor-install{suffix}"),format!("odin/odin{suffix}"),format!("tls/bin/tls{suffix}"),
                     "frontend/main.odin".into(), "runtime/basic/basic.thor".into(), "templates/main.thor".into(),
                     "editors/thor-language.vsix".into(), "install/profiles/Thor.code-profile".into()] {
        if !files.contains_key(&required) { return Err(format!("distribution is missing {required}")); }
    }
    Ok(())
}

struct RemoveDir(PathBuf);
impl Drop for RemoveDir { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }
fn unique() -> String { format!("{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos()) }
fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    fs::create_dir_all(path.parent().ok_or("invalid output parent")?).map_err(err)?;
    let temp = path.with_file_name(format!(".thor-write-{}",unique()));
    fs::write(&temp, bytes).map_err(err)?;
    let result = fs::rename(&temp,path).map_err(err);
    let _ = fs::remove_file(temp); result
}
fn command(mut command: Command) -> Result<()> {
    let label = format!("{command:?}");
    let status = command.status().map_err(|e| format!("{label}: {e}"))?;
    if !status.success() { return Err(format!("command failed ({status}): {label}")); }
    Ok(())
}
fn health(root: &Path) -> Result<()> {
    let odin = root.join(format!("odin/odin{}",env::consts::EXE_SUFFIX));
    let mut cmd = Command::new(&odin); cmd.arg("version").env_remove("ODIN_ROOT"); command(cmd)?;
    let temp = root.join(format!(".health-{}",unique())); fs::create_dir(&temp).map_err(err)?; let _guard = RemoveDir(temp.clone());
    fs::write(temp.join("main.thor"), include_str!("../smoke.thor")).map_err(err)?;
    let mut cmd = Command::new(root.join(format!("thor{}",env::consts::EXE_SUFFIX)));
    cmd.args(["run",".","-thread-count:2"]).current_dir(&temp)
        .env_remove("THOR_ROOT").env_remove("THOR_ODIN").env_remove("ODIN_ROOT").env_remove("THOR_FRONTEND_ODIN").env_remove("THOR_CACHE_DIR");
    command(cmd).map_err(|e| format!("installation build/run check failed. Check platform prerequisites (macOS developer tools; Linux clang; Windows MSVC + SDK). {e}"))
}
fn registered() -> Result<Value> {
    let value = read_json(&registry()?)?;
    if value["schema"] != 1 || value["managed_by"] != "thor-install" { return Err("unrecognized installation record".into()); }
    Ok(value)
}
fn run(o: Options) -> Result<()> {
    if o.doctor {
        let record = registered()?; let root = PathBuf::from(field(&record,"root")?);
        println!("Thor {}: {}",field(&record,"version")?,root.display()); return health(&root);
    }
    if o.bundle.is_none() && (o.vscode || o.path) {
        let record=registered()?;
        if let Some(prefix)=&o.prefix {
            if absolute(prefix)?!=PathBuf::from(field(&record,"prefix")?) {return Err("changing --prefix requires --bundle".into());}
        }
        let root=PathBuf::from(field(&record,"root")?);
        if o.dry {println!("Dry run: use installed Thor at {}; no settings changed.",root.display());return Ok(());}
        if o.path {add_path(Path::new(field(&record,"bin")?))?;}
        if o.vscode {vscode::setup(&o,&vscode::find_code(&o)?,&root)?;}
        return Ok(());
    }
    let bundle = absolute(o.bundle.as_deref().ok_or("provide --bundle FILE, or use the repository's ./thor-install bootstrap")?)?;
    check_checksum(&bundle,o.checksum.as_deref())?;
    let manifest = unpack(&bundle,None)?;
    let prefix = absolute(&o.prefix.clone().unwrap_or(data_home()?))?;
    // Odin rejects these characters in -out paths; parser caches live here too.
    let illegal="\"'`\\!$%^&*()=[]{};:#|,<>?";
    if prefix.to_string_lossy().chars().any(|c| illegal.contains(c) && !(cfg!(windows) && matches!(c,'\\'|':'))) {
        return Err("Odin cannot build inside this installation path (quotes or shell punctuation). Choose --prefix with a supported path; spaces and Unicode are supported.".into());
    }
    let code = if o.vscode { Some(vscode::find_code(&o)?) } else { None };
    println!("Install Thor {} ({}) into {}",field(&manifest,"version")?,field(&manifest,"target")?,prefix.display());
    if o.vscode { println!("VS Code profile: {}",o.profile.as_deref().unwrap_or("Default (settings preserved)")); }
    if o.dry { println!("Dry run: bundle verified; no files or settings changed."); return Ok(()); }
    if prefix.exists() && !prefix.join(MARKER).exists() && fs::read_dir(&prefix).map_err(err)?.next().is_some() {
        return Err(format!("{} is not an installer-owned directory; choose an empty --prefix",prefix.display()));
    }
    fs::create_dir_all(&prefix).map_err(err)?;
    let lock = prefix.join(".install-lock"); fs::create_dir(&lock).map_err(|e| format!("could not lock {}: {e}; if an installer was interrupted, remove this directory before retrying",lock.display()))?;
    let _lock = RemoveDir(lock);
    if prefix.join(MARKER).exists() && read_json(&prefix.join(MARKER))?["managed_by"] != "thor-install" { return Err("unrecognized installation owner".into()); }
    atomic_write(&prefix.join(MARKER),b"{\"managed_by\":\"thor-install\",\"schema\":1}\n")?;
    let versions = prefix.join("versions"); fs::create_dir_all(&versions).map_err(err)?;
    let manifest_hash = hex(Sha256::digest(serde_json::to_vec(&manifest).map_err(err)?));
    let root = versions.join(format!("{}-{}",field(&manifest,"version")?,&manifest_hash[..12]));
    if root.exists() {
        if read_json(&root.join(MANIFEST))? != manifest { return Err("existing version has a different manifest; it was preserved".into()); }
        for (name, record) in manifest["files"].as_object().ok_or("missing inventory")? {
            if digest(&root.join(name))? != field(record,"sha256")? { return Err(format!("installed file was modified: {name}; it was preserved")); }
        }
    } else {
        let stage = versions.join(format!(".stage-{}",unique())); fs::create_dir(&stage).map_err(err)?; let _stage = RemoveDir(stage.clone());
        if unpack(&bundle,Some(&stage))? != manifest { return Err("distribution changed during installation; nothing activated".into()); }
        fs::rename(&stage,&root).map_err(err)?;
    }
    health(&root)?;
    activate(&prefix,&root,&manifest)?;
    if o.path { add_path(&prefix.join("bin"))?; }
    if let Some(code) = code { vscode::setup(&o,&code,&root)?; }
    retire_versions(&prefix,&root);
    println!("Thor is ready: {}",prefix.join("bin").display());
    if !env::split_paths(&env::var_os("PATH").unwrap_or_default()).any(|p| p == prefix.join("bin")) {
        println!("Reload your shell profile or sign out and back in after PATH setup, or invoke {} directly.",root.join(format!("thor{}",env::consts::EXE_SUFFIX)).display());
        if !o.path { println!("Use --path to add the command directory to your user PATH."); }
    }
    Ok(())
}

// Keep the active toolchain and one rollback. Only an unmodified, fully
// inventoried distribution is disposable; user additions/edits are preserved.
fn retire_versions(prefix: &Path, active: &Path) {
    let versions=prefix.join("versions");
    if versions.is_symlink() { return; }
    let previous=read_json(&prefix.join("previous-installation.json")).ok()
        .and_then(|record|record["root"].as_str().map(PathBuf::from));
    let Ok(entries)=fs::read_dir(&versions) else {return;};
    for entry in entries.flatten() {
        let path=entry.path();
        if path==active || previous.as_ref()==Some(&path) || !entry.file_type().is_ok_and(|t|t.is_dir()) {continue;}
        let Ok(manifest)=read_json(&path.join(MANIFEST)) else {continue;};
        if validate_manifest(&manifest).is_err() {continue;}
        let hash=hex(Sha256::digest(serde_json::to_vec(&manifest).unwrap_or_default()));
        let expected=format!("{}-{}",manifest["version"].as_str().unwrap_or(""),&hash[..12]);
        if entry.file_name().to_str()!=Some(&expected) {continue;}
        let Some(files)=manifest["files"].as_object() else {continue;};
        if !files.keys().all(|name|safe_path(Path::new(name)) && path.join(name).is_file()) {continue;}
        fn unchanged(root:&Path,directory:&Path,files:&serde_json::Map<String,Value>)->bool {
            let Ok(entries)=fs::read_dir(directory) else {return false;};
            for entry in entries {
                let Ok(entry)=entry else {return false;};
                let Ok(kind)=entry.file_type() else {return false;};
                let path=entry.path();let Ok(relative)=path.strip_prefix(root) else {return false;};
                if relative==Path::new(".thor-cache") && kind.is_dir() {
                    if !generated_cache(&path) {return false;}continue;
                }
                if relative==Path::new("lib") && kind.is_dir() {
                    if !generated_views(root,&path) {return false;}continue;
                }
                if kind.is_dir() {if !unchanged(root,&path,files){return false;}}
                else if kind.is_file() {
                    let name=relative.to_string_lossy().replace('\\',"/");
                    if name==MANIFEST {continue;}
                    let Some(record)=files.get(&name) else {return false;};
                    if digest(&path).ok().as_deref()!=record["sha256"].as_str(){return false;}
                } else {return false;}
            }
            true
        }
        if unchanged(&path,&path,files) {
            if let Err(error)=fs::remove_dir_all(&path) {eprintln!("Preserved old toolchain {}: {error}",path.display());}
        } else {eprintln!("Preserved modified old toolchain {}",path.display());}
    }
}

fn hex_name(name:&str,len:usize)->bool {name.len()==len && name.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b))}
fn generated_cache(directory:&Path)->bool {
    let Ok(entries)=fs::read_dir(directory) else {return false;};
    for entry in entries {
        let Ok(entry)=entry else {return false;};
        let name=entry.file_name();let Some(name)=name.to_str() else {return false;};
        let Ok(kind)=entry.file_type() else {return false;};
        if kind.is_dir() {
            if !(matches!(name,"sdk-parser"|"rust-parse-v1"|"rust-syntax-v1"|"parse-v1")||hex_name(name,64)) || !generated_cache(&entry.path()) {return false;}
        } else if kind.is_file() {
            let frontend=name.strip_prefix("frontend-").map(|s|s.strip_suffix(".exe").unwrap_or(s)).is_some_and(|s|hex_name(s,20));
            let record=name.strip_suffix(".json").or_else(||name.strip_suffix(".bin")).is_some_and(|s|hex_name(s,64));
            if !frontend && !record {return false;}
        } else {return false;}
    }
    true
}
fn generated_views(root:&Path,directory:&Path)->bool {
    let Ok(entries)=fs::read_dir(directory) else {return false;};
    for entry in entries {
        let Ok(entry)=entry else {return false;};
        let path=entry.path();let name=entry.file_name();let Some(name)=name.to_str() else {return false;};
        if !entry.file_type().is_ok_and(|k|k.is_dir()) || !name.strip_prefix("runtime-rust-").is_some_and(|s|hex_name(s,24)) {return false;}
        let Ok(manifest)=read_json(&path.join("thor-manifest.json")) else {return false;};
        let Some(hash)=manifest["shared_runtime"].as_str().filter(|s|hex_name(s,64)) else {return false;};
        if manifest["engine"]!="rust" || name!=format!("runtime-rust-{}",&hash[..24]) {return false;}
        fn members(root:&Path,view:&Path,directory:&Path)->bool {
            let Ok(entries)=fs::read_dir(directory) else {return false;};
            for entry in entries {
        let Ok(entry)=entry else {return false;};
                let path=entry.path();let Ok(relative)=path.strip_prefix(view) else {return false;};
                let Ok(kind)=entry.file_type() else {return false;};
                if kind.is_dir() {if !members(root,view,&path){return false;}}
                else if relative==Path::new("thor-manifest.json") && kind.is_file() {continue;}
                else {
                    let source=root.join("runtime").join(relative);
                    if kind.is_symlink() {
                        if fs::canonicalize(&path).ok()!=fs::canonicalize(&source).ok() || !source.exists() {return false;}
                    } else if kind.is_file() && path.extension().is_some_and(|e|e=="odin") {
                        let Ok(original)=fs::read(source) else {return false;};
                        let expected=[b"#+vet !tabs\n".as_slice(),original.as_slice()].concat();
                        if fs::read(path).ok().as_deref()!=Some(expected.as_slice()){return false;}
                    } else {return false;}
                }
            }true
        }
        if !members(root,&path,&path) {return false;}
    }true
}

fn activate(prefix: &Path, root: &Path, manifest: &Value) -> Result<()> {
    let record_path = registry()?;
    if record_path.exists() { let _ = registered()?; }
    // Establish write access before changing active commands. A read-only or
    // misconfigured config location must not leave a new CLI with an old record.
    let probe=record_path.with_file_name(format!(".activation-probe-{}",unique()));
    atomic_write(&probe,b"thor-install")?;fs::remove_file(&probe).map_err(err)?;
    let bin = prefix.join("bin"); fs::create_dir_all(&bin).map_err(err)?;
    // Preflight *all* command destinations before publishing any of them.
    for name in ["thor","thor-install","odin"] {
        let dest = bin.join(format!("{name}{}",if cfg!(windows) {".cmd"} else {""}));
        if fs::symlink_metadata(&dest).is_ok() {
            #[cfg(unix)]
            if !fs::read_link(&dest).is_ok_and(|p| p.starts_with(prefix.join("versions"))) { return Err(format!("unmanaged command preserved: {}",dest.display())); }
            #[cfg(windows)]
            if !fs::read_to_string(&dest).unwrap_or_default().starts_with("@rem Managed by thor-install\r\n") { return Err(format!("unmanaged command preserved: {}",dest.display())); }
        }
    }
    let record = json!({"schema":1,"managed_by":"thor-install","version":field(manifest,"version")?,"root":root,"prefix":prefix,"bin":bin});
    // Retain the previous activation, allowing users to reactivate its bundle.
    if record_path.exists() && read_json(&record_path)?["root"].as_str()!=root.to_str() {
        atomic_write(&prefix.join("previous-installation.json"),&fs::read(&record_path).map_err(err)?)?;
    }
    for name in ["thor","thor-install","odin"] {
        let target=if name=="odin" {root.join(format!("odin/odin{}",env::consts::EXE_SUFFIX))}else{root.join(format!("{name}{}",env::consts::EXE_SUFFIX))};
        #[cfg(unix)] {
            let temp = bin.join(format!(".link-{}",unique()));
            std::os::unix::fs::symlink(&target,&temp).map_err(err)?;
            fs::rename(&temp,bin.join(name)).map_err(err)?;
        }
        #[cfg(windows)] {
            let text = format!("@rem Managed by thor-install\r\n@echo off\r\nsetlocal DisableDelayedExpansion\r\n\"{}\" %*\r\n",target.to_string_lossy().replace('%',"%%"));
            atomic_write(&bin.join(format!("{name}.cmd")),text.as_bytes())?;
        }
    }
    atomic_write(&record_path,&serde_json::to_vec_pretty(&record).map_err(err)?)
}
fn shell_quote(p: &Path) -> String { format!("'{}'",p.to_string_lossy().replace('\'',"'\\''")) }
fn add_path(bin: &Path) -> Result<()> {
    if bin.to_string_lossy().contains(if cfg!(windows) {';'}else{':'}) {
        return Err(format!("{} contains a PATH separator; use the absolute compiler path instead",bin.display()));
    }
    #[cfg(unix)] {
        let shell = env::var("SHELL").unwrap_or_default();
        if shell.ends_with("fish") { return Err(format!("PATH was not edited. In fish, run: fish_add_path {}",shell_quote(bin))); }
        let file = home()?.join(if shell.ends_with("zsh") {".zprofile"} else {".profile"});
        let start = "# >>> thor-install PATH >>>"; let end = "# <<< thor-install PATH <<<";
        let text = match fs::read_to_string(&file) { Ok(s) => s, Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(), Err(e) => return Err(err(e)) };
        let block = format!("{start}\nexport PATH={}:\"$PATH\"\n{end}",shell_quote(bin));
        if text.contains(start) {
            if !text.contains(&block) { return Err(format!("existing managed PATH block preserved in {}; update it manually for this prefix",file.display())); }
        } else {
            if file.exists() { let backup=file.with_file_name(format!("{}.thor-backup-{}",file.file_name().unwrap().to_string_lossy(),unique())); fs::copy(&file,backup).map_err(err)?; }
            let mut output=fs::OpenOptions::new().create(true).append(true).open(&file).map_err(err)?;
            writeln!(output,"\n{block}\n").map_err(err)?;
        }
        println!("PATH configured in {} (existing content retained).",file.display());
    }
    #[cfg(windows)] {
        let value=bin.to_string_lossy().replace('\'',"''");
        let script=format!("$p=[Environment]::GetEnvironmentVariable('Path','User'); $b='{value}'; if (($p -split ';') -notcontains $b) {{ [Environment]::SetEnvironmentVariable('Path',($b+';'+$p),'User') }}");
        let mut cmd=Command::new("powershell.exe");cmd.args(["-NoProfile","-NonInteractive","-Command",&script]);command(cmd)?;
    }
    Ok(())
}
