//! Minimal project scaffolding, matching project_template.py.
use crate::diagnostic::Result;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    env, fs,
    io::Write,
    path::{Path, PathBuf},
    process::Command,
};

pub fn project_files(
    name: &str,
    starter: &[u8],
    root: &Path,
    odin: Option<&str>,
    gitignore: bool,
) -> BTreeMap<PathBuf, Vec<u8>> {
    let binary = if cfg!(windows) { "debug.exe" } else { "debug" };
    let generated = "${workspaceFolder}/.vscode/bin/generated";
    let mut args = vec![
        "build".to_string(),
        ".".into(),
        "-debug".into(),
        "-o:none".into(),
        "--output-dir".into(),
        generated.into(),
        format!("-out:${{workspaceFolder}}/.vscode/bin/{binary}"),
    ];
    if let Some(odin) = odin {
        args.extend(["--odin".into(), odin.into()]);
    }
    let tasks = json!({"version":"2.0.0","tasks":[{"label":"build-thor-debug","type":"process","command":root.join("thor"),"args":args,"options":{"cwd":"${workspaceFolder}"},"problemMatcher":[]}]});
    let launch = json!({"version":"0.2.0","configurations":[{"name":"Debug Thor (LLDB)","type":"thor-lldb","request":"launch","program":format!("${{workspaceFolder}}/.vscode/bin/{binary}"),"manifest":format!("{generated}/thor-manifest.json"),"cwd":"${workspaceFolder}","args":[],"preLaunchTask":"build-thor-debug"}]});
    let encode = |value: &Value| {
        let mut s = serde_json::to_string_pretty(value).expect("JSON values are serializable");
        s.push('\n');
        s.into_bytes()
    };
    let mut files = BTreeMap::from([
        (PathBuf::from(format!("{name}.thor")), starter.to_vec()),
        (PathBuf::from(".vscode/tasks.json"), encode(&tasks)),
        (PathBuf::from(".vscode/launch.json"), encode(&launch)),
    ]);
    if gitignore {
        files.insert(PathBuf::from(".gitignore"),format!(".vscode/bin/\n.thor-build/\n.thor-rust-build/\n.thor-cache/\n*.dSYM/\n/{name}\n/{name}.exe\n/{name}.app/\n").into_bytes());
    }
    files
}
fn expand(path: &str) -> PathBuf {
    if path == "~" {
        return env::var_os("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| path.into());
    }
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = env::var_os("HOME") {
            return PathBuf::from(home).join(rest);
        }
    }
    path.into()
}
fn lexical_absolute(path: &Path) -> PathBuf {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        env::current_dir().unwrap_or_default().join(path)
    };
    let mut result = PathBuf::new();
    for component in absolute.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                result.pop();
            }
            component => result.push(component.as_os_str()),
        }
    }
    result
}
fn executable(name: &str) -> Option<PathBuf> {
    fn eligible(path: &Path) -> bool {
        let Ok(metadata) = path.metadata() else {
            return false;
        };
        if !metadata.is_file() {
            return false;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o111 == 0 {
                return false;
            }
        }
        true
    }
    let path = Path::new(name);
    let directories = if path.is_absolute() || path.components().count() > 1 {
        vec![PathBuf::new()]
    } else {
        env::split_paths(&env::var_os("PATH").unwrap_or_default()).collect()
    };
    for directory in directories {
        let candidate = directory.join(name);
        if eligible(&candidate) {
            return Some(candidate);
        }
        #[cfg(windows)]
        for extension in env::var("PATHEXT")
            .unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".into())
            .split(';')
        {
            let candidate = directory.join(format!("{name}{extension}"));
            if eligible(&candidate) {
                return Some(candidate);
            }
        }
    }
    None
}
fn resolved(path: &Path) -> std::io::Result<PathBuf> {
    let path = lexical_absolute(path);
    let mut existing = path.as_path();
    let mut suffix = Vec::new();
    while !existing.exists() {
        let Some(name) = existing.file_name() else {
            return crate::project::canonicalize(path);
        };
        suffix.push(name.to_os_string());
        let Some(parent) = existing.parent() else {
            return crate::project::canonicalize(path);
        };
        existing = parent;
    }
    let mut result = crate::project::canonicalize(existing)?;
    result.extend(suffix.into_iter().rev());
    Ok(result)
}
fn open_project(project: &Path) {
    let mut command = if let Some(code) = executable("code") {
        let mut c = Command::new(code);
        c.arg("--new-window").arg(project).arg(project.join(format!(
            "{}.thor",
            project.file_name().unwrap().to_string_lossy()
        )));
        c
    } else if cfg!(target_os = "macos") {
        let mut c = Command::new("open");
        c.args(["-a", "Visual Studio Code"]).arg(project);
        c
    } else {
        println!("Open {} in VS Code to run or debug it.", project.display());
        return;
    };
    match command.status() {
        Ok(status) if status.success() => {}
        Ok(status) => eprintln!(
            "Project created, but VS Code exited with status {}.",
            status.code().unwrap_or(1)
        ),
        Err(error) => eprintln!("Project created, but VS Code could not open: {error}"),
    }
}
struct Config {
    name: String,
    parent: PathBuf,
    template: PathBuf,
    open: bool,
    gitignore: bool,
    odin: Option<String>,
}
fn parse(args: &[String], root: &Path) -> std::result::Result<Config, String> {
    let mut config = Config {
        name: String::new(),
        parent: env::current_dir().map_err(|e| e.to_string())?,
        template: root.join("templates/main.thor"),
        open: false,
        gitignore: false,
        odin: None,
    };
    let mut opening = None;
    let mut index = 0;
    while index < args.len() {
        let argument = &args[index];
        let (key, inline) = argument
            .split_once('=')
            .map_or((argument.as_str(), None), |(key, value)| (key, Some(value)));
        match key {
            "--parent" | "--template" | "--odin" => {
                let value = if let Some(value) = inline {
                    value.to_string()
                } else {
                    index += 1;
                    args.get(index)
                        .filter(|value| !value.starts_with("--"))
                        .ok_or_else(|| format!("argument {key}: expected one argument"))?
                        .clone()
                };
                match key {
                    "--parent" => config.parent = expand(&value),
                    "--template" => config.template = expand(&value),
                    _ => config.odin = Some(value),
                }
            }
            "--open" | "--no-open" if inline.is_none() => {
                let value = key == "--open";
                if opening.is_some_and(|old| old != value) {
                    return Err("argument --no-open: not allowed with argument --open".into());
                }
                opening = Some(value);
                config.open = value;
            }
            "--gitignore" if inline.is_none() => config.gitignore = true,
            _ if !argument.starts_with('-') && config.name.is_empty() => {
                config.name = argument.clone()
            }
            _ => return Err(format!("unrecognized arguments: {argument}")),
        }
        index += 1;
    }
    if config.name.is_empty() {
        return Err("the following arguments are required: name".into());
    }
    let mut bytes = config.name.bytes();
    let first = bytes.next().unwrap();
    if !(first.is_ascii_alphanumeric() || first == b'_')
        || !bytes.all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return Err(
            "project name must contain only letters, digits, underscores or hyphens".into(),
        );
    }
    Ok(config)
}
fn usage() {
    eprintln!("usage: thor new [--parent PARENT] [--open | --no-open] [--template TEMPLATE] [--gitignore] [--odin ODIN] name");
}
pub fn main(args: &[String], root: &Path) -> Result<i32> {
    let config = match parse(args, root) {
        Ok(config) => config,
        Err(error) => {
            usage();
            eprintln!("thor new: error: {error}");
            return Ok(2);
        }
    };
    let parent = resolved(&config.parent)?;
    let project = parent.join(&config.name);
    if fs::symlink_metadata(&project).is_ok() {
        usage();
        eprintln!(
            "thor new: error: project already exists: {}",
            project.display()
        );
        return Ok(2);
    }
    let starter = match fs::read(&config.template).and_then(|bytes| {
        std::str::from_utf8(&bytes)
            .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))?;
        Ok(bytes)
    }) {
        Ok(bytes) => bytes,
        Err(error) => {
            usage();
            eprintln!(
                "thor new: error: cannot read UTF-8 template {}: {error}",
                config.template.display()
            );
            return Ok(2);
        }
    };
    let odin = config.odin.as_ref().map(|value| {
        let value = executable(value).map_or_else(|| expand(value), |value| value);
        if value.is_absolute() || value.components().count() > 1 {
            lexical_absolute(&value).to_string_lossy().into_owned()
        } else {
            value.to_string_lossy().into_owned()
        }
    });
    let files = project_files(
        &config.name,
        &starter,
        root,
        odin.as_deref(),
        config.gitignore,
    );
    let create = (|| -> std::io::Result<()> {
        fs::create_dir_all(&parent)?;
        fs::create_dir(&project)?;
        for (name, bytes) in files {
            let path = project.join(name);
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(path)?;
            file.write_all(&bytes)?;
        }
        Ok(())
    })();
    if let Err(error) = create {
        eprintln!("thor new: {error}");
        return Ok(1);
    }
    println!(
        "Created {} with {}.thor and VS Code run/debug configuration.",
        project.display(),
        config.name
    );
    if let Some((_, suffix)) = config.name.rsplit_once('_') {
        if [
            "windows",
            "darwin",
            "linux",
            "freebsd",
            "openbsd",
            "netbsd",
            "dragonfly",
            "haiku",
            "wasi",
            "js",
            "freestanding",
            "essence",
            "orca",
            "386",
            "amd64",
            "arm32",
            "arm64",
            "wasm32",
            "wasm64",
            "wasm64p32",
            "riscv32",
            "riscv64",
            "test",
        ]
        .contains(&suffix.to_lowercase().as_str())
        {
            eprintln!("Warning: {}.thor retains Odin filename filtering; it may be excluded on other targets or outside test builds. Use a neutral project name for a portable starter.",config.name);
        }
    }
    if config.open {
        open_project(&project);
    }
    Ok(0)
}
