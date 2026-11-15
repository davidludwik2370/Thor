//! Inspect and relocate only native files copied into publication staging.
use crate::diagnostic::{Error, Result};
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs,
    path::{Path, PathBuf},
    process::Command,
};
pub fn tool(args: &[String]) -> Result<String> {
    let result = Command::new(&args[0])
        .args(&args[1..])
        .output()
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                Error::new(format!("Publishing requires {} on PATH.", args[0]))
            } else {
                Error::new(error.to_string())
            }
        })?;
    if !result.status.success() {
        let detail = if result.stderr.is_empty() {
            &result.stdout
        } else {
            &result.stderr
        };
        return Err(Error::new(format!(
            "Publish tool {} failed: {}",
            args[0],
            String::from_utf8_lossy(detail).trim()
        )));
    }
    Ok(String::from_utf8_lossy(&result.stdout).into_owned())
}
macro_rules! run {($($value:expr),+ $(,)?)=>{tool(&[$(format!("{}",$value)),+])};}
pub fn macho(path: &Path) -> Result<bool> {
    use std::io::Read;
    let mut bytes = [0; 4];
    let mut file = fs::File::open(path)?;
    if file.read(&mut bytes)? != 4 {
        return Ok(false);
    }
    Ok(matches!(
        bytes,
        [0xfe, 0xed, 0xfa, 0xce]
            | [0xce, 0xfa, 0xed, 0xfe]
            | [0xfe, 0xed, 0xfa, 0xcf]
            | [0xcf, 0xfa, 0xed, 0xfe]
            | [0xca, 0xfe, 0xba, 0xbe]
            | [0xbe, 0xba, 0xfe, 0xca]
            | [0xca, 0xfe, 0xba, 0xbf]
            | [0xbf, 0xba, 0xfe, 0xca]
    ))
}
pub fn walk(root: &Path) -> Result<Vec<PathBuf>> {
    fn visit(root: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
        let mut entries = fs::read_dir(root)?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<std::io::Result<Vec<_>>>()?;
        entries.sort();
        for path in entries {
            out.push(path.clone());
            if fs::symlink_metadata(&path)?.is_dir() {
                visit(&path, out)?;
            }
        }
        Ok(())
    }
    let mut result = vec![];
    visit(root, &mut result)?;
    result.sort();
    Ok(result)
}
pub fn sign_macos(root: &Path) -> Result<()> {
    let paths = walk(root)?;
    let mut seen = BTreeSet::new();
    for path in &paths {
        if path.is_file() {
            let original = crate::project::canonicalize(path)?;
            if !seen.contains(&original) && macho(path)? {
                seen.insert(original);
                run!["codesign", "--force", "--sign", "-", path.display()]?;
            }
        }
    }
    let mut frameworks = paths
        .into_iter()
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "framework")
                && path.is_dir()
        })
        .collect::<Vec<_>>();
    frameworks.sort_by_key(|path| std::cmp::Reverse(path.components().count()));
    for path in frameworks {
        run!["codesign", "--force", "--sign", "-", path.display()]?;
    }
    run!["codesign", "--force", "--sign", "-", root.display()]?;
    run!["codesign", "--verify", "--deep", "--strict", root.display()]?;
    Ok(())
}
pub fn pe_imports(path: &Path) -> Result<Vec<String>> {
    fn parse(data: &[u8]) -> std::result::Result<Vec<String>, String> {
        fn u16_at(data: &[u8], offset: usize) -> std::result::Result<u16, String> {
            Ok(u16::from_le_bytes(
                data.get(offset..offset + 2)
                    .ok_or("truncated executable header")?
                    .try_into()
                    .unwrap(),
            ))
        }
        fn u32_at(data: &[u8], offset: usize) -> std::result::Result<u32, String> {
            Ok(u32::from_le_bytes(
                data.get(offset..offset + 4)
                    .ok_or("truncated executable header")?
                    .try_into()
                    .unwrap(),
            ))
        }
        fn u64_at(data: &[u8], offset: usize) -> std::result::Result<u64, String> {
            Ok(u64::from_le_bytes(
                data.get(offset..offset + 8)
                    .ok_or("truncated executable header")?
                    .try_into()
                    .unwrap(),
            ))
        }
        if data.get(..2) != Some(b"MZ") {
            return Err("missing DOS header".into());
        }
        let pe = u32_at(data, 0x3c)? as usize;
        if data.get(pe..pe + 4) != Some(b"PE\0\0") {
            return Err("missing PE header".into());
        }
        let sections = u16_at(data, pe + 6)? as usize;
        let optional_size = u16_at(data, pe + 20)? as usize;
        let optional = pe + 24;
        let magic = u16_at(data, optional)?;
        if !matches!(magic, 0x10b | 0x20b) {
            return Err("unsupported PE optional header".into());
        }
        let directories = optional + if magic == 0x10b { 96 } else { 112 };
        let count = u32_at(data, directories - 4)?;
        let base = if magic == 0x10b {
            u64::from(u32_at(data, optional + 28)?)
        } else {
            u64_at(data, optional + 24)?
        };
        let table = optional + optional_size;
        let mut rows = vec![];
        for index in 0..sections {
            let start = table + index * 40;
            let _ = data
                .get(start..start + 24)
                .ok_or("truncated section table")?;
            rows.push((
                u64::from(u32_at(data, start + 8)?),
                u64::from(u32_at(data, start + 12)?),
                u64::from(u32_at(data, start + 16)?),
                u64::from(u32_at(data, start + 20)?),
            ));
        }
        let offset = |rva: u64| -> std::result::Result<usize, String> {
            for (virtual_size, address, size, raw) in &rows {
                if *address <= rva && rva < address + (*size).max(*virtual_size) {
                    let position = raw + rva - address;
                    if position >= data.len() as u64 || rva - address >= *size {
                        break;
                    }
                    return Ok(position as usize);
                }
            }
            Err("import address is outside file sections".into())
        };
        let name = |rva: u64| -> std::result::Result<String, String> {
            let start = offset(rva)?;
            let end = data[start..]
                .iter()
                .position(|byte| *byte == 0)
                .map(|end| start + end)
                .ok_or("unterminated DLL import name")?;
            let bytes = &data[start..end];
            if bytes.is_empty()
                || bytes
                    .iter()
                    .any(|byte| !byte.is_ascii() || b"/\\:".contains(byte))
            {
                return Err("invalid DLL import name".into());
            }
            Ok(std::str::from_utf8(bytes)
                .map_err(|_| "invalid DLL import name")?
                .into())
        };
        let mut result = vec![];
        let mut seen = BTreeSet::new();
        for (index, width) in [(1, 20), (13, 32)] {
            if count <= index {
                continue;
            }
            let rva = u64::from(u32_at(data, directories + index as usize * 8)?);
            let size = u32_at(data, directories + index as usize * 8 + 4)? as usize;
            if rva == 0 {
                continue;
            }
            let start = offset(rva)?;
            for position in (start..data.len().min(start + size)).step_by(width) {
                let record = data
                    .get(position..position + width)
                    .ok_or("truncated import table")?;
                if record.iter().all(|byte| *byte == 0) {
                    break;
                }
                let rva = if index == 1 {
                    u64::from(u32_at(record, 12)?)
                } else {
                    let attrs = u32_at(record, 0)?;
                    let value = u64::from(u32_at(record, 4)?);
                    if attrs & 1 == 0 {
                        value
                            .checked_sub(base)
                            .ok_or("invalid delay import address")?
                    } else {
                        value
                    }
                };
                let value = name(rva)?;
                if seen.insert(value.clone()) {
                    result.push(value);
                }
            }
        }
        Ok(result)
    }
    parse(&fs::read(path)?).map_err(|error| {
        Error::new(format!(
            "Cannot inspect Windows executable {}: {error}",
            path.file_name().unwrap_or_default().to_string_lossy()
        ))
    })
}
fn path_name(path: &Path) -> String {
    path.file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned()
}
fn relative(to: &Path, from: &Path) -> PathBuf {
    let to = to.components().collect::<Vec<_>>();
    let from = from.components().collect::<Vec<_>>();
    let common = to.iter().zip(&from).take_while(|(a, b)| a == b).count();
    let mut result = PathBuf::new();
    for _ in common..from.len() {
        result.push("..");
    }
    for part in &to[common..] {
        result.push(part.as_os_str());
    }
    if result.as_os_str().is_empty() {
        result.push(".");
    }
    result
}
const WINDOWS_SYSTEM:&str="kernel32 user32 gdi32 gdi32full advapi32 shell32 ole32 oleaut32 uuid comdlg32 comctl32 ws2_32 winmm imm32 version shlwapi setupapi cfgmgr32 bcrypt crypt32 ncrypt secur32 iphlpapi dnsapi psapi dbghelp ntdll msvcrt ucrtbase opengl32 d3d11 d3d12 dxgi dwmapi shcore powrprof winhttp wininet hid winspool uxtheme normaliz msimg32 rpcrt4 avrt dinput8 dsound xinput1_4 xinput9_1_0 mfplat mfreadwrite mfuuid strmiids propsys wtsapi32 netapi32 userenv usp10 wintrust mswsock msasn1 kernelbase dcomp windowscodecs winusb win32u dhcpcsvc cryptbase profapi ktmw32 cabinet winbio winfax urlmon combase wldap32 activeds authz cryptui winhttp wininet winmm";
pub fn windows(
    executable: &Path,
    binary: &Path,
    destination: &Path,
    project: &Path,
    declared: &[PathBuf],
    hints: &[PathBuf],
) -> Result<Vec<String>> {
    let mut dirs = vec![
        executable.parent().unwrap().to_path_buf(),
        project.into(),
        project.join("bin"),
        project.join("lib"),
        project.join("libs"),
    ];
    dirs.extend(
        declared
            .iter()
            .chain(hints)
            .filter_map(|path| path.parent().map(Path::to_path_buf)),
    );
    dirs.extend(
        std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
            .filter(|path| !path.as_os_str().is_empty()),
    );
    let system_root =
        PathBuf::from(std::env::var_os("SystemRoot").unwrap_or_else(|| r"C:\Windows".into()));
    let system_root = crate::project::canonicalize(&system_root).unwrap_or_else(|_| {
        if system_root.is_absolute() {
            system_root.clone()
        } else {
            std::env::current_dir()
                .unwrap_or_default()
                .join(&system_root)
        }
    });
    let mut queue = VecDeque::from([(crate::project::canonicalize(executable)?, binary.to_path_buf())]);
    let mut owners = BTreeMap::new();
    fn register(
        source: PathBuf,
        destination: &Path,
        owners: &mut BTreeMap<String, PathBuf>,
        dirs: &mut Vec<PathBuf>,
        queue: &mut VecDeque<(PathBuf, PathBuf)>,
    ) -> Result<()> {
        if !source.is_file() {
            return Err(Error::new(format!(
                "Declared Windows library must be a DLL file: {}",
                source.display()
            )));
        }
        let name = path_name(&source);
        let key = name.to_lowercase();
        if let Some(owner) = owners.get(&key) {
            if owner != &source {
                return Err(Error::new(format!(
                    "Two different DLLs have the same packaged name: {name}"
                )));
            }
            return Ok(());
        }
        let target = destination.join(&name);
        if target.exists() {
            return Err(Error::new(format!(
                "DLL conflicts with a packaged file: {name}"
            )));
        }
        super::policy::copy_file(&source, &target)?;
        owners.insert(key, source.clone());
        dirs.push(source.parent().unwrap().into());
        queue.push_back((source, target));
        Ok(())
    }
    for source in declared {
        register(
            crate::project::canonicalize(source)?,
            destination,
            &mut owners,
            &mut dirs,
            &mut queue,
        )?;
    }
    while let Some((source, _)) = queue.pop_front() {
        for name in pe_imports(&source)? {
            let lower = name.to_lowercase();
            if lower.starts_with("api-ms-win-")
                || lower.starts_with("ext-ms-win-")
                || WINDOWS_SYSTEM
                    .split_whitespace()
                    .any(|value| value == lower.strip_suffix(".dll").unwrap_or(&lower))
            {
                continue;
            }
            let mut found = None;
            for directory in &dirs {
                if !directory.is_dir() {
                    continue;
                }
                for entry in fs::read_dir(directory)? {
                    let path = entry?.path();
                    if path.is_file() && path_name(&path).to_lowercase() == lower {
                        found = Some(crate::project::canonicalize(path)?);
                        break;
                    }
                }
                if found.is_some() {
                    break;
                }
            }
            let Some(dependency) = found else {
                if system_root.join("System32").join(&name).exists() {
                    continue;
                }
                return Err(Error::new(format!("Cannot locate DLL {name:?} required by {}; declare it with --app-library or [publish].libraries.",path_name(&source))));
            };
            if dependency.starts_with(&system_root) {
                continue;
            }
            register(dependency, destination, &mut owners, &mut dirs, &mut queue)?;
        }
    }
    let mut names = owners
        .values()
        .map(|path| path_name(path))
        .collect::<Vec<_>>();
    names.sort();
    Ok(names)
}
fn linux_system(name: &str) -> bool {
    if name.starts_with("ld-linux")
        || name.starts_with("ld-musl")
        || name == "linux-vdso.so"
        || name.starts_with("linux-vdso.so.")
    {
        return true;
    }
    ["c", "m", "dl", "pthread", "rt", "resolv", "util", "anl"]
        .iter()
        .any(|library| {
            let base = format!("lib{library}.so");
            name == base || name.starts_with(&(base + "."))
        })
}
pub fn linux(
    executable: &Path,
    binary: &Path,
    destination: &Path,
    project: &Path,
    declared: &[PathBuf],
    hints: &[PathBuf],
) -> Result<Vec<String>> {
    let inspector = ["readelf", "llvm-readelf"]
        .iter()
        .find_map(|name| {
            std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
                .map(|path| path.join(name))
                .find(|path| is_executable(path))
        })
        .ok_or_else(|| {
            Error::new("Linux publishing requires readelf (binutils) or llvm-readelf.")
        })?;
    let mut queue = VecDeque::from([(crate::project::canonicalize(executable)?, binary.to_path_buf())]);
    let mut owners = BTreeMap::new();
    let mut patched = BTreeSet::new();
    fn register(
        source: &Path,
        destination: &Path,
        owners: &mut BTreeMap<String, PathBuf>,
        queue: &mut VecDeque<(PathBuf, PathBuf)>,
    ) -> Result<PathBuf> {
        if !source.is_file() {
            return Err(Error::new(format!(
                "Declared Linux library must be a file: {}",
                source.display()
            )));
        }
        let source = crate::project::canonicalize(source)?;
        let name = path_name(&source);
        let target = destination.join(&name);
        if let Some(owner) = owners.get(&name) {
            if owner != &source {
                return Err(Error::new(format!(
                    "Two different libraries have the same packaged name: {name}"
                )));
            }
            return Ok(target);
        }
        fs::create_dir_all(destination)?;
        super::policy::copy_file(&source, &target)?;
        owners.insert(name, source.clone());
        queue.push_back((source, target.clone()));
        Ok(target)
    }
    for source in declared {
        register(source, destination, &mut owners, &mut queue)?;
    }
    while let Some((source, target)) = queue.pop_front() {
        let dynamic = run![inspector.display(), "-d", source.display()]?;
        let needed = dynamic
            .lines()
            .filter(|line| line.contains("(NEEDED)"))
            .filter_map(|line| {
                line.split_once('[')
                    .and_then(|(_, tail)| tail.split_once(']').map(|(name, _)| name.to_string()))
            })
            .filter(|name| !linux_system(&path_name(Path::new(name))))
            .collect::<Vec<_>>();
        if needed.is_empty() {
            continue;
        }
        let mut resolved = None;
        for dependency in needed {
            let mut candidates = vec![];
            if Path::new(&dependency).is_absolute() {
                candidates.push(PathBuf::from(&dependency));
            }
            candidates.extend(
                declared
                    .iter()
                    .filter(|path| path_name(path) == dependency)
                    .cloned(),
            );
            candidates.extend([
                source.parent().unwrap().join(&dependency),
                project.join(&dependency),
                project.join("lib").join(&dependency),
                project.join("libs").join(&dependency),
            ]);
            candidates.extend(
                hints
                    .iter()
                    .chain(declared)
                    .filter_map(|path| path.parent().map(|parent| parent.join(&dependency))),
            );
            let original = if let Some(path) = candidates.into_iter().find(|path| path.is_file()) {
                Some(path)
            } else {
                if resolved.is_none() {
                    let listing = run!["ldd", source.display()]?;
                    let mut paths = BTreeMap::new();
                    for line in listing.lines() {
                        if let Some((name, tail)) = line.trim().split_once("=>") {
                            let tail = tail.trim();
                            if tail.starts_with('/') {
                                if let Some((path, _)) = tail.split_once(" (") {
                                    paths.insert(
                                        name.trim().to_string(),
                                        PathBuf::from(path.trim()),
                                    );
                                }
                            }
                        }
                    }
                    resolved = Some(paths);
                }
                resolved
                    .as_ref()
                    .and_then(|paths| paths.get(&dependency))
                    .filter(|path| path.is_file())
                    .cloned()
            };
            let Some(original) = original else {
                return Err(Error::new(format!("Cannot locate dependency {dependency:?} required by {}; declare it with --app-library or [publish].libraries.",path_name(&source))));
            };
            let relocated = register(&original, destination, &mut owners, &mut queue)?;
            if dependency != path_name(&relocated) {
                run![
                    "patchelf",
                    "--replace-needed",
                    dependency,
                    path_name(&relocated),
                    target.display()
                ]?;
            }
        }
        let rpath = if target.parent() == Some(destination) {
            "$ORIGIN".into()
        } else {
            format!(
                "$ORIGIN/{}",
                relative(destination, target.parent().unwrap()).display()
            )
        };
        run!["patchelf", "--set-rpath", rpath, target.display()]?;
        patched.insert(target);
    }
    if !owners.is_empty() && !patched.contains(binary) {
        run![
            "patchelf",
            "--set-rpath",
            format!(
                "$ORIGIN/{}",
                relative(destination, binary.parent().unwrap()).display()
            ),
            binary.display()
        ]?;
    }
    Ok(owners.into_keys().collect())
}
fn writable(path: &Path, directory: bool) -> Result<()> {
    let mut mode = fs::metadata(path)?.permissions();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        mode.set_mode(mode.mode() | if directory { 0o300 } else { 0o200 });
    }
    #[cfg(not(unix))]
    {
        let _ = directory;
        mode.set_readonly(false);
    }
    fs::set_permissions(path, mode)?;
    Ok(())
}
fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    fs::create_dir(destination)?;
    let mut children = fs::read_dir(source)?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<std::io::Result<Vec<_>>>()?;
    children.sort();
    for child in children {
        let target = destination.join(child.file_name().unwrap());
        let kind = fs::symlink_metadata(&child)?;
        if kind.is_symlink() {
            let link = fs::read_link(&child)?;
            #[cfg(unix)]
            std::os::unix::fs::symlink(link, target)?;
            #[cfg(windows)]
            if child.is_dir() {
                std::os::windows::fs::symlink_dir(link, target)?;
            } else {
                std::os::windows::fs::symlink_file(link, target)?;
            }
        } else if kind.is_dir() {
            copy_tree(&child, &target)?;
        } else {
            super::policy::copy_file(&child, &target)?;
        }
    }
    let metadata = fs::metadata(source)?;
    fs::set_permissions(destination, metadata.permissions())?;
    filetime::set_file_times(
        destination,
        filetime::FileTime::from_last_access_time(&metadata),
        filetime::FileTime::from_last_modification_time(&metadata),
    )?;
    Ok(())
}
fn rpaths(text: &str) -> Vec<String> {
    let mut current = false;
    let mut result = vec![];
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with("cmd ") {
            current = line == "cmd LC_RPATH";
        } else if current {
            if let Some(value) = line
                .strip_prefix("path ")
                .and_then(|value| value.split_once(" (offset").map(|(value, _)| value))
            {
                result.push(value.into());
                current = false;
            }
        }
    }
    result
}
struct Mac<'a> {
    executable: &'a Path,
    destination: &'a Path,
    mapped: BTreeMap<PathBuf, PathBuf>,
    order: Vec<PathBuf>,
    owners: BTreeMap<String, PathBuf>,
    queue: VecDeque<(PathBuf, PathBuf, Vec<PathBuf>)>,
}
impl Mac<'_> {
    fn register(&mut self, path: &Path, inherited: Vec<PathBuf>) -> Result<PathBuf> {
        let path = crate::project::canonicalize(path)?;
        if let Some(target) = self.mapped.get(&path) {
            return Ok(target.clone());
        }
        let framework = path.ancestors().skip(1).find(|parent| {
            parent
                .extension()
                .is_some_and(|extension| extension == "framework")
        });
        let (owner, source, relative) = if let Some(framework) = framework {
            let owner = path_name(framework);
            let relative = PathBuf::from(&owner).join(path.strip_prefix(framework).unwrap());
            (owner, framework.to_path_buf(), relative)
        } else {
            let owner = path_name(&path);
            (owner.clone(), path.clone(), PathBuf::from(owner))
        };
        if self.owners.get(&owner).is_some_and(|old| old != &source) {
            return Err(Error::new(format!(
                "Two different dependencies would have the same packaged name: {owner}"
            )));
        }
        if !self.owners.contains_key(&owner) {
            fs::create_dir_all(self.destination)?;
            let target = self.destination.join(&owner);
            if target.exists() {
                return Err(Error::new(format!(
                    "Dependency conflicts with a packaged file: {owner}"
                )));
            }
            if framework.is_some() {
                copy_tree(&source, &target)?;
                let root = crate::project::canonicalize(&target)?;
                for link in walk(&target)? {
                    let kind = fs::symlink_metadata(&link)?;
                    if kind.is_symlink() {
                        if !crate::project::canonicalize(&link)?.starts_with(&root) {
                            return Err(Error::new(format!(
                                "Framework has an external symlink: {owner}/{}",
                                link.strip_prefix(&target).unwrap().display()
                            )));
                        }
                    } else if kind.is_dir() {
                        writable(&link, true)?;
                    } else if kind.is_file() && macho(&link)? {
                        writable(&link, false)?;
                    }
                }
                writable(&target, true)?;
            } else {
                super::policy::copy_file(&path, &target)?;
                writable(&target, false)?;
            }
            self.owners.insert(owner, source);
        }
        let target = self.destination.join(relative);
        self.order.push(target.clone());
        self.mapped.insert(path.clone(), target.clone());
        self.queue.push_back((path, target.clone(), inherited));
        Ok(target)
    }
    fn expand(&self, value: &str, source: &Path) -> PathBuf {
        if let Some(value) = value.strip_prefix("@loader_path") {
            source.parent().unwrap().join(value.trim_start_matches('/'))
        } else if let Some(value) = value.strip_prefix("@executable_path") {
            self.executable
                .parent()
                .unwrap()
                .join(value.trim_start_matches('/'))
        } else {
            value.into()
        }
    }
}
pub fn macos(
    executable: &Path,
    binary: &Path,
    destination: &Path,
    project: &Path,
    declared: &[PathBuf],
    hints: &[PathBuf],
) -> Result<Vec<String>> {
    let original = crate::project::canonicalize(executable)?;
    let mut mac = Mac {
        executable,
        destination,
        mapped: BTreeMap::from([(original.clone(), binary.to_path_buf())]),
        order: vec![binary.to_path_buf()],
        owners: BTreeMap::new(),
        queue: VecDeque::from([(original.clone(), binary.to_path_buf(), vec![])]),
    };
    for path in declared {
        let path = if path.is_dir() {
            let name = path.file_stem().unwrap_or_default();
            [path.join(name), path.join("Versions/Current").join(name)]
                .into_iter()
                .find(|path| path.is_file())
                .ok_or_else(|| {
                    Error::new("Cannot find the executable in the declared framework.")
                })?
        } else {
            path.clone()
        };
        if !macho(&path)? {
            return Err(Error::new(format!(
                "Declared library is not Mach-O: {}",
                path_name(&path)
            )));
        }
        mac.register(&path, vec![])?;
    }
    while let Some((source, target, inherited)) = mac.queue.pop_front() {
        let listing = run!["otool", "-L", source.display()]?;
        let mut seen = BTreeSet::new();
        let dependencies = listing
            .lines()
            .filter(|line| line.starts_with(['\t', ' ']))
            .map(|line| {
                line.trim()
                    .split(" (compatibility version")
                    .next()
                    .unwrap_or_default()
                    .to_string()
            })
            .filter(|line| seen.insert(line.clone()))
            .collect::<Vec<_>>();
        let commands = run!["otool", "-l", source.display()]?;
        let mut paths = rpaths(&commands)
            .into_iter()
            .map(|path| mac.expand(&path, &source))
            .collect::<Vec<_>>();
        paths.extend(inherited);
        let identifier = if source != original {
            let identities = run!["otool", "-D", source.display()]?;
            identities
                .lines()
                .skip(1)
                .find(|line| !line.is_empty() && !line.ends_with(':'))
                .map(str::trim)
                .unwrap_or_default()
                .to_string()
        } else {
            String::new()
        };
        for dependency in dependencies {
            if dependency == identifier
                || dependency.starts_with("/usr/lib/")
                || dependency.starts_with("/System/Library/")
            {
                continue;
            }
            let mut candidates = if let Some(suffix) = dependency.strip_prefix("@rpath/") {
                let mut values = paths
                    .iter()
                    .map(|path| path.join(suffix))
                    .collect::<Vec<_>>();
                values.extend(
                    declared
                        .iter()
                        .filter_map(|path| path.parent().map(|parent| parent.join(suffix))),
                );
                values.extend(
                    hints
                        .iter()
                        .filter(|path| path_name(path) == path_name(Path::new(suffix)))
                        .cloned(),
                );
                values.extend(
                    hints
                        .iter()
                        .filter_map(|path| path.parent().map(|parent| parent.join(suffix))),
                );
                values.extend([project.join(suffix), source.parent().unwrap().join(suffix)]);
                values
            } else {
                let mut values = vec![mac.expand(&dependency, &source)];
                if dependency.starts_with("@executable_path/")
                    || dependency.starts_with("@loader_path/")
                {
                    values.push(project.join(dependency.split_once('/').unwrap().1));
                }
                values.extend(
                    hints
                        .iter()
                        .filter(|path| path_name(path) == path_name(Path::new(&dependency)))
                        .cloned(),
                );
                values
            };
            let resolved = candidates
                .drain(..)
                .find(|path| path.is_file())
                .map(crate::project::canonicalize)
                .transpose()?;
            let Some(resolved) = resolved else {
                return Err(Error::new(format!("Cannot locate dependency {dependency:?} required by {}; declare it with --app-library or [publish].libraries.",path_name(&source))));
            };
            let text = resolved.to_string_lossy();
            if text.starts_with("/usr/lib/") || text.starts_with("/System/Library/") {
                continue;
            }
            let relocated = mac.register(&resolved, paths.clone())?;
            let replacement = format!(
                "@loader_path/{}",
                relative(&relocated, target.parent().unwrap())
                    .to_string_lossy()
                    .replace('\\', "/")
            );
            run![
                "install_name_tool",
                "-change",
                dependency,
                replacement,
                target.display()
            ]?;
        }
        if !identifier.is_empty() {
            run![
                "install_name_tool",
                "-id",
                format!(
                    "@rpath/{}",
                    target
                        .strip_prefix(destination)
                        .unwrap()
                        .to_string_lossy()
                        .replace('\\', "/")
                ),
                target.display()
            ]?;
        }
    }
    if !mac.owners.is_empty() {
        for target in &mac.order {
            let rpath = format!(
                "@loader_path/{}",
                relative(destination, target.parent().unwrap())
                    .to_string_lossy()
                    .replace('\\', "/")
            );
            let commands = run!["otool", "-l", target.display()]?;
            if !rpaths(&commands).contains(&rpath) {
                run!["install_name_tool", "-add_rpath", rpath, target.display()]?;
            }
        }
    }
    let mut libraries = mac
        .mapped
        .values()
        .filter(|target| target.as_path() != binary)
        .map(|target| {
            target
                .strip_prefix(destination)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/")
        })
        .collect::<Vec<_>>();
    libraries.sort();
    Ok(libraries)
}
pub fn bundle(
    system: &str,
    executable: &Path,
    binary: &Path,
    destination: &Path,
    project: &Path,
    declared: &[PathBuf],
    hints: &[PathBuf],
) -> Result<Vec<String>> {
    match system {
        "darwin" => macos(executable, binary, destination, project, declared, hints),
        "windows" => windows(executable, binary, destination, project, declared, hints),
        "linux" => linux(executable, binary, destination, project, declared, hints),
        _ => Err(Error::new(
            "Publishing currently supports macOS, Windows and Linux hosts.",
        )),
    }
}

fn is_executable(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::metadata(path).is_ok_and(|metadata| metadata.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        true
    }
}
