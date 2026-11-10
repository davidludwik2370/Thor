//! Local application package policy, matching publishing.py.
use super::{icons, policy};
use crate::diagnostic::{Error, Result};
use image::RgbaImage;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
#[derive(Clone, Default, Debug)]
pub struct Options {
    pub name: Option<String>,
    pub identifier: Option<String>,
    pub version: Option<String>,
    pub icon: Option<String>,
    pub app_type: Option<String>,
    pub resources: Vec<String>,
    pub libraries: Vec<String>,
    pub directory: Option<PathBuf>,
}
pub struct Publisher {
    pub root: PathBuf,
    pub system: String,
    pub arch: String,
    pub name: String,
    pub executable_name: String,
    pub identifier: String,
    pub version: String,
    pub app_type: String,
    pub output: PathBuf,
    pub icon: Option<PathBuf>,
    pub image: Option<RgbaImage>,
    pub resources: BTreeMap<PathBuf, PathBuf>,
    pub directories: BTreeSet<PathBuf>,
    pub libraries: Vec<PathBuf>,
    pub linked_file: Option<PathBuf>,
    pub warnings: Vec<String>,
}
fn default_identifier(name: &str) -> String {
    let mut result = String::new();
    let mut invalid = false;
    for c in name.to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' {
            result.push(c);
            invalid = false;
        } else if !invalid {
            result.push('-');
            invalid = true;
        }
    }
    let result = result.trim_matches('-');
    format!(
        "local.thor.{}",
        if result.is_empty() { "app" } else { result }
    )
}
fn valid_identifier(value: &str) -> bool {
    let mut count = 0;
    let valid = value.split('.').all(|part| {
        count += 1;
        !part.is_empty() && part.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
    });
    valid && count >= 2
}
fn valid_version(value: &str) -> bool {
    let parts = value.split('.').collect::<Vec<_>>();
    !parts.is_empty()
        && parts.len() <= 3
        && parts.iter().all(|part| {
            let mut value = 0u32;
            let count = part.chars().count();
            count > 0
                && count <= 5
                && part.chars().all(|c| {
                    decimal(c).is_some_and(|digit| {
                        value = value * 10 + digit;
                        value <= 65535
                    })
                })
        })
}
impl Publisher {
    pub fn prepare(
        entry: &Path,
        flags: &[String],
        options: &Options,
        system: &str,
        arch: &str,
    ) -> Result<Self> {
        if !matches!(system, "darwin" | "windows" | "linux") {
            return Err(Error::new(
                "Publishing currently supports macOS, Windows and Linux hosts.",
            ));
        }
        let root = if entry.is_file() {
            entry.parent().unwrap().to_path_buf()
        } else {
            entry.to_path_buf()
        };
        if !root.is_dir() {
            return Err(Error::new(format!(
                "Project does not exist: {}",
                root.display()
            )));
        }
        let root = fs::canonicalize(root)?;
        let mut arch = arch.to_string();
        if let Some(target) = flags.iter().find_map(|flag| flag.strip_prefix("-target:")) {
            let parts = target.split('_').collect::<Vec<_>>();
            if parts.len() != 2 || parts[0] != system {
                return Err(Error::new("Publish on the target operating system; cross-OS publishing is not yet supported."));
            }
            arch = parts[1].into();
        }
        if flags.iter().any(|flag| {
            flag.starts_with("-build-mode:") && flag != "-build-mode:exe"
                || flag == "-no-entry-point"
        }) {
            return Err(Error::new(
                "thor publish requires an executable build with an entry point.",
            ));
        }
        if system == "windows" && flags.iter().any(|flag| flag.starts_with("-resource:")) {
            return Err(Error::new("thor publish manages Windows icon/version resources; custom -resource is not supported here."));
        }
        let config = policy::read_config(&root)?;
        let executable =
            if let Some(path) = flags.iter().find_map(|flag| flag.strip_prefix("-out:")) {
                policy::absolute(Path::new(path))?
            } else {
                let cwd = std::env::current_dir()?;
                cwd.join(
                    cwd.file_name()
                        .filter(|name| !name.is_empty())
                        .unwrap_or_else(|| std::ffi::OsStr::new("program")),
                )
            };
        let mut executable_name = executable
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        if system == "windows" && !executable_name.to_lowercase().ends_with(".exe") {
            executable_name.push_str(".exe");
        }
        policy::validate_name(&executable_name, "Executable name")?;
        let default_name = if executable_name.to_lowercase().ends_with(".exe") {
            &executable_name[..executable_name.len() - 4]
        } else {
            &executable_name
        };
        let name = policy::validate_name(
            options
                .name
                .as_deref()
                .or_else(|| config.text.get("name").map(String::as_str))
                .unwrap_or(default_name),
            "Application name",
        )?;
        let identifier = options
            .identifier
            .clone()
            .or_else(|| config.text.get("id").cloned())
            .unwrap_or_else(|| default_identifier(&name));
        if !valid_identifier(&identifier) {
            return Err(Error::new(
                "Application ID must be a reverse-domain identifier such as com.example.myapp.",
            ));
        }
        let version = options
            .version
            .clone()
            .or_else(|| config.text.get("version").cloned())
            .unwrap_or_else(|| "0.1.0".into());
        if !valid_version(&version) {
            return Err(Error::new("Application version must have one to three numeric components, each at most 65535."));
        }
        let app_type = options
            .app_type
            .clone()
            .or_else(|| config.text.get("type").cloned())
            .unwrap_or_else(|| "gui".into());
        if !matches!(app_type.as_str(), "gui" | "console") {
            return Err(Error::new("Application type must be gui or console."));
        }
        let output_dir = policy::absolute(
            options
                .directory
                .as_deref()
                .unwrap_or_else(|| executable.parent().unwrap()),
        )?;
        let suffix = if system == "darwin" {
            ".app".into()
        } else {
            format!(
                "-{system}-{arch}{}",
                if system == "windows" {
                    ".zip"
                } else {
                    ".tar.gz"
                }
            )
        };
        let output = output_dir.join(format!("{name}{suffix}"));
        let selected = options
            .icon
            .as_deref()
            .or_else(|| config.text.get("icon").map(String::as_str));
        let icon = if let Some(selected) = selected {
            Some(root.join(policy::relative_path(selected, "Icon")?))
        } else {
            let existing = [root.join("assets/icon.png"), root.join("assets/icon.ico")]
                .into_iter()
                .filter(|path| path.exists())
                .collect::<Vec<_>>();
            if existing.len() > 1 {
                return Err(Error::new("Both assets/icon.png and assets/icon.ico exist; choose one with --app-icon or [publish].icon."));
            }
            existing.into_iter().next()
        };
        if let Some(icon) = &icon {
            if !icon.is_file() || !fs::canonicalize(icon)?.starts_with(&root) {
                return Err(Error::new(format!(
                    "Icon must be an existing file inside the project: {}",
                    icon.display()
                )));
            }
        }
        let mut warnings = vec![];
        let image = icon
            .as_ref()
            .map(|path| icons::normalize(path, &mut warnings))
            .transpose()?;
        let mut entries = config.resources;
        entries.extend(options.resources.iter().cloned());
        let assets = root.join("assets");
        if assets.exists() || assets.is_symlink() {
            if !assets.is_dir() {
                return Err(Error::new("The automatic assets path must be a directory."));
            }
            entries.insert(0, "assets".into());
        }
        let resources = policy::collect_resources(&root, &entries)?;
        for path in &resources.directories {
            if output.starts_with(fs::canonicalize(root.join(path))?) {
                return Err(Error::new(
                    "Publish output must be outside the included resource directories.",
                ));
            }
        }
        if output == root
            || resources
                .files
                .values()
                .any(|path| fs::canonicalize(path).is_ok_and(|path| path == output))
        {
            return Err(Error::new("Publish output would replace a project input."));
        }
        let mut libraries = vec![];
        for value in config.libraries.iter().chain(&options.libraries) {
            let path = Path::new(value);
            let path = policy::absolute(&if path.is_absolute() {
                path.into()
            } else {
                root.join(path)
            })?;
            if !path.exists()
                || !(path.is_file()
                    || system == "darwin"
                        && path
                            .extension()
                            .is_some_and(|extension| extension == "framework"))
            {
                return Err(Error::new(format!(
                    "Declared library does not exist or is unsupported: {value}"
                )));
            }
            libraries.push(path);
        }
        let publisher = Self {
            root,
            system: system.into(),
            arch,
            name,
            executable_name,
            identifier,
            version,
            app_type,
            output,
            icon,
            image,
            resources: resources.files,
            directories: resources.directories,
            libraries,
            linked_file: None,
            warnings,
        };
        publisher.check_output()?;
        Ok(publisher)
    }
}

fn decimal(c: char) -> Option<u32> {
    // Decimal blocks in the frozen Python reference (Unicode 14).
    const STARTS: &[u32] = &[
        0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66,
        0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90,
        0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0,
        0xff10, 0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0,
        0x11650, 0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0, 0x16a60, 0x16ac0,
        0x16b50, 0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e950, 0x1fbf0,
    ];
    let code = c as u32;
    STARTS
        .iter()
        .find(|start| **start <= code && code < **start + 10)
        .map(|start| code - start)
}
impl Publisher {
    pub fn check_output(&self) -> Result<()> {
        if self.output.is_symlink() {
            return Err(Error::new("Publish output must not be a symlink."));
        }
        if !self.output.exists() {
            return Ok(());
        }
        let metadata = (|| -> Result<serde_json::Value> {
            use std::io::Read;
            let bytes = if self.system == "darwin" {
                fs::read(self.output.join("Contents/Resources/thor-publish.json"))?
            } else if self.system == "windows" {
                let mut archive = zip::ZipArchive::new(fs::File::open(&self.output)?)
                    .map_err(|error| Error::new(error.to_string()))?;
                let mut file = archive
                    .by_name(&format!("{}/resources/thor-publish.json", self.name))
                    .map_err(|error| Error::new(error.to_string()))?;
                let mut bytes = vec![];
                file.read_to_end(&mut bytes)?;
                bytes
            } else {
                let mut archive =
                    tar::Archive::new(flate2::read::GzDecoder::new(fs::File::open(&self.output)?));
                let mut found = None;
                for entry in archive.entries()? {
                    let mut entry = entry?;
                    if entry.path()?.to_string_lossy()
                        == format!("{}/resources/thor-publish.json", self.name)
                    {
                        let mut bytes = vec![];
                        entry.read_to_end(&mut bytes)?;
                        found = Some(bytes);
                        break;
                    }
                }
                found.ok_or_else(|| Error::new("package metadata missing"))?
            };
            serde_json::from_slice(&bytes).map_err(|error| Error::new(error.to_string()))
        })();
        if metadata.is_ok_and(|data| {
            data.get("generator").and_then(serde_json::Value::as_str) == Some("thor")
                && data.get("schema").is_some_and(|value| {
                    value.as_f64() == Some(1.0) || value.as_bool() == Some(true)
                })
        }) {
            return Ok(());
        }
        Err(Error::new(format!(
            "Refusing to replace output not managed by thor publish: {}",
            self.output.display()
        )))
    }
    pub fn native_flags(&mut self, flags: &[String], scratch: &Path) -> Result<Vec<String>> {
        let mut result = flags
            .iter()
            .filter(|flag| !flag.starts_with("-out:"))
            .cloned()
            .collect::<Vec<_>>();
        if !result.iter().any(|flag| flag.starts_with("-o:")) {
            result.push("-o:speed".into());
        }
        let exported = result
            .iter()
            .find_map(|flag| flag.strip_prefix("-export-linked-libs-file:"));
        self.linked_file = Some(if let Some(path) = exported {
            policy::absolute(Path::new(path))?
        } else {
            scratch.join("linked-libraries.txt")
        });
        if exported.is_none() {
            result.push(format!(
                "-export-linked-libs-file:{}",
                self.linked_file.as_ref().unwrap().display()
            ));
        }
        if self.system == "darwin" {
            let extra = result
                .iter()
                .filter_map(|flag| flag.strip_prefix("-extra-linker-flags:"))
                .collect::<Vec<_>>()
                .join(" ");
            result.retain(|flag| !flag.starts_with("-extra-linker-flags:"));
            result.push(format!(
                "-extra-linker-flags:{extra} -Wl,-headerpad_max_install_names"
            ));
        } else if self.system == "windows" {
            let expected = if self.app_type == "gui" {
                "windows"
            } else {
                "console"
            };
            let specified = result
                .iter()
                .filter_map(|flag| flag.strip_prefix("-subsystem:"))
                .collect::<Vec<_>>();
            if !specified.is_empty() && specified != [expected] {
                return Err(Error::new(
                    "-subsystem conflicts with --app-type; select gui or console with --app-type.",
                ));
            }
            if specified.is_empty() {
                result.push(format!("-subsystem:{expected}"));
            }
            let icon = scratch.join("app.ico");
            if let (Some(image), Some(source)) = (&self.image, &self.icon) {
                icons::write(image, source, &icon, "windows")?;
            }
            let rc = |value: &str| value.replace('\\', "\\\\").replace('"', "\\\"");
            let mut numbers = self
                .version
                .split('.')
                .map(str::to_string)
                .collect::<Vec<_>>();
            numbers.resize(4, "0".into());
            numbers.truncate(4);
            let mut content = "#pragma code_page(65001)\n".to_string();
            if self.image.is_some() {
                content.push_str(&format!("1 ICON \"{}\"\n", rc(&icon.to_string_lossy())));
            }
            content.push_str(&format!("1 VERSIONINFO\nFILEVERSION {}\nPRODUCTVERSION {}\nBEGIN\nBLOCK \"StringFileInfo\"\nBEGIN\nBLOCK \"040904b0\"\nBEGIN\n",numbers.join(","),numbers.join(",")));
            for (key, value) in [
                ("FileDescription", &self.name),
                ("ProductName", &self.name),
                ("FileVersion", &self.version),
                ("ProductVersion", &self.version),
            ] {
                content.push_str(&format!("VALUE \"{key}\", \"{}\\0\"\n", rc(value)));
            }
            content.push_str("END\nEND\nBLOCK \"VarFileInfo\"\nBEGIN\nVALUE \"Translation\", 0x409, 1200\nEND\nEND\n");
            let resource = scratch.join("app.rc");
            fs::write(&resource, content)?;
            result.push(format!("-resource:{}", resource.display()));
        }
        Ok(result)
    }
}

fn unique_directory(parent: &Path, prefix: &str) -> Result<PathBuf> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    loop {
        let path = parent.join(format!(
            "{prefix}{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        match fs::create_dir(&path) {
            Ok(()) => return Ok(path),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        }
    }
}
fn executable_mode(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o755))?;
    }
    Ok(())
}
fn copy_debug_tree(source: &Path, destination: &Path) -> Result<()> {
    fs::create_dir(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let original = entry.path();
        let target = destination.join(entry.file_name());
        if original.is_dir() {
            copy_debug_tree(&original, &target)?;
        } else {
            policy::copy_file(&original, &target)?;
        }
    }
    fs::set_permissions(destination, fs::metadata(source)?.permissions())?;
    Ok(())
}
fn xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}
impl Publisher {
    pub fn package(&self, executable: &Path, scratch: &Path) -> Result<PathBuf> {
        let staging = unique_directory(scratch, "payload-")?;
        let root = staging.join(format!(
            "{}{}",
            self.name,
            if self.system == "darwin" { ".app" } else { "" }
        ));
        let (binary_dir, resource_dir, library_dir) = match self.system.as_str() {
            "darwin" => (
                root.join("Contents/MacOS"),
                root.join("Contents/Resources"),
                root.join("Contents/Frameworks"),
            ),
            "windows" => (root.clone(), root.join("resources"), root.clone()),
            _ => (root.join("bin"), root.join("resources"), root.join("lib")),
        };
        fs::create_dir_all(&binary_dir)?;
        fs::create_dir_all(&resource_dir)?;
        let binary = binary_dir.join(&self.executable_name);
        policy::copy_file(executable, &binary)?;
        let debug = PathBuf::from(format!("{}.dSYM", executable.display()));
        if debug.is_dir() {
            let directory = resource_dir.join("Debug");
            fs::create_dir(&directory)?;
            copy_debug_tree(&debug, &directory.join(debug.file_name().unwrap()))?;
        }
        for relative in &self.directories {
            fs::create_dir_all(resource_dir.join(relative))?;
        }
        for (relative, original) in &self.resources {
            let destination = resource_dir.join(relative);
            fs::create_dir_all(destination.parent().unwrap())?;
            policy::copy_file(original, &destination)?;
        }
        if resource_dir.join("thor-publish.json").exists() {
            return Err(Error::new(
                "Resource thor-publish.json conflicts with Thor's package metadata.",
            ));
        }
        let icon_name = match self.system.as_str() {
            "darwin" => "AppIcon.icns",
            "windows" => "AppIcon.ico",
            _ => "AppIcon.png",
        };
        if let (Some(image), Some(source)) = (&self.image, &self.icon) {
            let destination = resource_dir.join(icon_name);
            if destination.exists() {
                return Err(Error::new(format!(
                    "Resource {icon_name} conflicts with the packaged application icon."
                )));
            }
            icons::write(image, source, &destination, &self.system)?;
        }
        let mut hints = vec![];
        if let Some(file) = &self.linked_file {
            for line in fs::read_to_string(file)?.lines() {
                let fields = line.rsplitn(3, '\t').collect::<Vec<_>>();
                if fields.len() == 3 && fields[0] == "user" {
                    let path = Path::new(fields[2]);
                    let path = if path.is_absolute() {
                        path.into()
                    } else {
                        self.root.join(path)
                    };
                    if path.exists() {
                        hints.push(path);
                    }
                }
            }
        }
        let libraries = super::deps::bundle(
            &self.system,
            executable,
            &binary,
            &library_dir,
            &self.root,
            &self.libraries,
            &hints,
        )?;
        let metadata = serde_json::json!({"generator":"thor","schema":1,"name":self.name,"id":self.identifier,"version":self.version,"platform":self.system,"architecture":self.arch,"type":self.app_type,
            "resources":self.resources.keys().map(|path|path.to_string_lossy().replace('\\',"/")).collect::<Vec<_>>(),"libraries":libraries});
        fs::write(
            resource_dir.join("thor-publish.json"),
            format!(
                "{}\n",
                serde_json::to_string_pretty(&metadata)
                    .map_err(|error| Error::new(error.to_string()))?
            ),
        )?;
        if self.system == "darwin" {
            let mut bundle_executable = self.executable_name.clone();
            if self.app_type == "console" {
                let command = resource_dir.join("ThorConsole.command");
                if command.exists() || self.executable_name == "ThorConsole" {
                    return Err(Error::new(
                        "Executable name conflicts with Thor's console launcher.",
                    ));
                }
                fs::write(
                    &command,
                    format!(
                        "#!/bin/sh\nexec \"$(dirname \"$0\")/../MacOS/\"{} \"$@\"\n",
                        policy::shell_quote(&self.executable_name)
                    ),
                )?;
                executable_mode(&command)?;
                let launcher = binary_dir.join("ThorConsole");
                let source = scratch.join("console-launcher.c");
                fs::write(&source, CONSOLE_LAUNCHER)?;
                super::deps::tool(&[
                    "clang".into(),
                    "-arch".into(),
                    if self.arch == "amd64" {
                        "x86_64".into()
                    } else {
                        self.arch.clone()
                    },
                    source.to_string_lossy().into_owned(),
                    "-o".into(),
                    launcher.to_string_lossy().into_owned(),
                ])?;
                bundle_executable = "ThorConsole".into();
            }
            let mut info = BTreeMap::from([
                ("CFBundlePackageType", "APPL"),
                ("CFBundleInfoDictionaryVersion", "6.0"),
                ("CFBundleExecutable", &bundle_executable),
                ("CFBundleName", &self.name),
                ("CFBundleDisplayName", &self.name),
                ("CFBundleIdentifier", &self.identifier),
                ("CFBundleShortVersionString", &self.version),
                ("CFBundleVersion", &self.version),
            ]);
            if self.image.is_some() {
                info.insert("CFBundleIconFile", icon_name);
            }
            let mut plist="<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\">\n<dict>\n".to_string();
            for (key, value) in info {
                plist.push_str(&format!(
                    "\t<key>{key}</key>\n\t<string>{}</string>\n",
                    xml(value)
                ));
            }
            plist.push_str("\t<key>NSHighResolutionCapable</key>\n\t<true/>\n</dict>\n</plist>\n");
            fs::write(root.join("Contents/Info.plist"), plist)?;
            super::deps::sign_macos(&root)?;
        } else if self.system == "linux" {
            let launcher = root.join("AppRun");
            fs::write(
                &launcher,
                format!(
                    "#!/bin/sh\nexec \"$(dirname \"$0\")/bin/\"{} \"$@\"\n",
                    policy::shell_quote(&self.executable_name)
                ),
            )?;
            executable_mode(&launcher)?;
            let install = root.join("install-desktop.sh");
            fs::write(
                &install,
                self.desktop_installer(if self.image.is_some() { icon_name } else { "" }),
            )?;
            executable_mode(&install)?;
        }
        self.check_output()?;
        fs::create_dir_all(self.output.parent().unwrap())?;
        if self.system == "darwin" {
            let backup = scratch.join("previous.app");
            if self.output.exists() {
                fs::rename(&self.output, &backup)?;
            }
            if let Err(error) = fs::rename(&root, &self.output) {
                if backup.exists() {
                    fs::rename(&backup, &self.output)?;
                }
                return Err(error.into());
            }
        } else {
            let archive = scratch.join(if self.system == "windows" {
                "package.zip"
            } else {
                "package.tar.gz"
            });
            if self.system == "windows" {
                use std::io::{Read, Write};
                let mut stream = zip::ZipWriter::new(fs::File::create(&archive)?);
                for path in super::deps::walk(&root)? {
                    let relative = Path::new(&self.name)
                        .join(path.strip_prefix(&root).unwrap())
                        .to_string_lossy()
                        .replace('\\', "/");
                    let mut options = zip::write::SimpleFileOptions::default()
                        .compression_method(zip::CompressionMethod::Deflated);
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        options =
                            options.unix_permissions(fs::metadata(&path)?.permissions().mode());
                    }
                    if path.is_file() {
                        stream
                            .start_file(relative, options)
                            .map_err(|error| Error::new(error.to_string()))?;
                        let mut bytes = vec![];
                        fs::File::open(path)?.read_to_end(&mut bytes)?;
                        stream.write_all(&bytes)?;
                    } else if path.is_dir() {
                        stream
                            .add_directory(relative, options)
                            .map_err(|error| Error::new(error.to_string()))?;
                    }
                }
                stream
                    .finish()
                    .map_err(|error| Error::new(error.to_string()))?;
            } else {
                let gzip = flate2::write::GzEncoder::new(
                    fs::File::create(&archive)?,
                    flate2::Compression::default(),
                );
                let mut stream = tar::Builder::new(gzip);
                stream.append_dir(&self.name, &root)?;
                for path in super::deps::walk(&root)? {
                    let relative = Path::new(&self.name).join(path.strip_prefix(&root).unwrap());
                    if fs::symlink_metadata(&path)?.is_dir() {
                        stream.append_dir(relative, path)?;
                    } else {
                        stream.append_path_with_name(path, relative)?;
                    }
                }
                stream.into_inner()?.finish()?;
            }
            fs::rename(archive, &self.output)?;
        }
        Ok(self.output.clone())
    }
}

const CONSOLE_LAUNCHER: &str = r###"#include <mach-o/dyld.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
int main(void) {
    char executable[PATH_MAX], command[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) return 1;
    char *last = strrchr(executable, '/');
    if (!last) return 1;
    *last = 0;
    int n = snprintf(command, sizeof(command), "%s/../Resources/ThorConsole.command", executable);
    if (n < 0 || n >= sizeof(command)) return 1;
    execl("/usr/bin/open", "open", "-a", "Terminal", command, (char *)0);
    perror("Cannot open Terminal");
    return 1;
}
"###;

impl Publisher {
    pub fn desktop_installer(&self, icon_name: &str) -> String {
        let name = policy::shell_quote(&format!("Name={}", self.name.replace('\\', "\\\\")));
        let terminal = if self.app_type == "console" {
            "Terminal=true"
        } else {
            "Terminal=false"
        };
        let identifier = &self.identifier;
        let icon_line = if icon_name.is_empty() {
            ""
        } else {
            "  printf 'Icon=%s\\n' \"$escaped_icon\"\n"
        };
        format!(
            r###"#!/bin/sh
set -eu
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
desktop_dir="${{XDG_DATA_HOME:-$HOME/.local/share}}/applications"
mkdir -p "$desktop_dir"
escaped_exec=$(printf '%s' "$app_dir/AppRun" | sed 's/\\/\\\\\\\\/g; s/"/\\\\"/g; s/`/\\\\`/g; s/\$/\\\\$/g; s/%/%%/g')
escaped_icon=$(printf '%s' "$app_dir/resources/{icon_name}" | sed 's/\\/\\\\/g')
{{
  printf '%s\n' '[Desktop Entry]' 'Type=Application' {name}
  printf 'Exec="%s"\n' "$escaped_exec"
{icon_line}  printf '%s\n' {terminal}
}} > "$desktop_dir/{identifier}.desktop"
printf '%s\n' 'Desktop entry installed. Rerun this script after moving the application.'
"###
        )
    }
}
