//! Copy an Odin working tree and validate a mechanical Thor conversion.
use crate::{
    diagnostic::{Error, Result},
    frontend::{bootstrap, Frontend},
    project::absolute,
};
use std::{
    collections::BTreeMap,
    env, fs,
    io::Write,
    path::{Path, PathBuf},
};

#[path = "migration_command.rs"]
mod commands;
#[path = "migration_config.rs"]
mod config;
#[path = "migration_plan.rs"]
mod plan;
#[path = "migration_validation.rs"]
mod validation;

pub(super) struct Settings {
    source: PathBuf,
    source_argument: PathBuf,
    destination: PathBuf,
    odin: String,
    flags: Vec<String>,
    dry_run: bool,
    commands: Vec<commands::Invocation>,
    command_odin: Option<String>,
    cwd: PathBuf,
}

struct Request {
    source: Option<PathBuf>,
    output: Option<PathBuf>,
    odin: Option<String>,
    flags: Vec<String>,
    dry_run: bool,
    commands: Vec<commands::Invocation>,
}

#[derive(Clone)]
pub(super) enum Contents {
    File(Vec<u8>),
    Directory,
    Link(PathBuf),
    SourceLink(PathBuf, Vec<u8>),
    Asset(u64, Option<std::time::SystemTime>),
}
#[derive(Clone)]
pub(super) struct Entry {
    relative: PathBuf,
    contents: Contents,
    permissions: fs::Permissions,
}

struct WorkDirectory(PathBuf);
impl Drop for WorkDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn request(args: &[String]) -> Result<Request> {
    let mut source = None;
    let mut destination = None;
    let mut odin = None;
    let mut flags = vec![];
    let mut dry_run = false;
    let mut commands = vec![];
    let mut arguments = args.iter();
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--output-dir" => {
                destination =
                    Some(PathBuf::from(arguments.next().ok_or_else(|| {
                        Error::usage("--output-dir requires a path")
                    })?))
            }
            "--odin" => {
                odin = Some(
                    arguments
                        .next()
                        .ok_or_else(|| Error::usage("--odin requires a compiler path"))?
                        .clone(),
                )
            }
            "--command" => {
                commands.push(commands::parse(arguments.next().ok_or_else(|| {
                    Error::usage("--command requires an Odin invocation")
                })?)?)
            }
            "--dry-run" => dry_run = true,
            _ if argument.starts_with("--output-dir=") => destination = Some(argument[13..].into()),
            _ if argument.starts_with("--odin=") => odin = Some(argument[7..].into()),
            _ if argument.starts_with("--command=") => {
                commands.push(commands::parse(&argument[10..])?)
            }
            _ if argument.starts_with("--") => {
                return Err(Error::usage(format!(
                    "unknown migration option '{argument}'"
                )))
            }
            _ if argument.starts_with('-') => flags.push(argument.clone()),
            _ if source.is_none() => source = Some(PathBuf::from(argument)),
            _ => {
                return Err(Error::usage(format!(
                    "unexpected migration argument '{argument}'"
                )))
            }
        }
    }
    if source.is_none() && commands.is_empty() {
        return Err(Error::usage(
            "thor migrate requires an Odin project directory or --command",
        ));
    }
    if source.is_none() && (destination.is_some() || !flags.is_empty()) {
        return Err(Error::usage("--output-dir and migration validation flags require an explicit project directory; put compiler arguments inside --command for translation"));
    }
    Ok(Request {
        source,
        output: destination,
        odin,
        flags,
        dry_run,
        commands,
    })
}

fn settings(mut request: Request) -> Result<Settings> {
    let cwd = env::current_dir()?;
    let source_argument = commands::lexical(&cwd.join(request.source.unwrap()));
    let source = crate::project::canonicalize(&source_argument)?;
    if !source.is_dir() {
        return Err(Error::usage("thor migrate expects a project directory"));
    }
    let name = source
        .file_name()
        .ok_or_else(|| Error::usage("the filesystem root cannot be migrated"))?;
    let destination = request
        .output
        .unwrap_or_else(|| PathBuf::from("."))
        .join(name);
    let absolute = absolute(&destination)?;
    let mut parent = absolute.as_path();
    let mut tail = vec![];
    while !parent.exists() {
        tail.push(
            parent
                .file_name()
                .ok_or_else(|| Error::new("invalid migration destination"))?
                .to_os_string(),
        );
        parent = parent
            .parent()
            .ok_or_else(|| Error::new("invalid migration destination"))?;
    }
    let mut destination = crate::project::canonicalize(parent)?;
    for component in tail.into_iter().rev() {
        destination.push(component);
    }
    if fs::symlink_metadata(&destination).is_ok() {
        return Err(Error::new(format!(
            "migration destination already exists: {}; choose --output-dir",
            destination.display()
        )));
    }
    if destination.starts_with(&source) || source.starts_with(&destination) {
        return Err(Error::new("migration source and destination must not overlap; choose --output-dir outside the source tree"));
    }
    for flag in &request.flags {
        let name = flag.split(':').next().unwrap_or(flag);
        if matches!(
            name,
            "-out"
                | "-file"
                | "-build-mode"
                | "-export-dependencies"
                | "-export-dependencies-file"
                | "-doc-format"
                | "-keep-temp-files"
                | "-export-defineables"
                | "-export-timings"
                | "-export-timings-file"
                | "-export-linked-libs-file"
        ) {
            return Err(Error::usage(format!("{name} does not apply to migration validation; pass target, define, collection and checking flags")));
        }
    }
    if !request.commands.is_empty() {
        let shared = std::mem::take(&mut request.flags)
            .into_iter()
            .map(|flag| {
                if let Some((name, path)) = flag
                    .strip_prefix("-collection:")
                    .and_then(|value| value.split_once('='))
                {
                    format!(
                        "-collection:{name}={}",
                        commands::lexical(&source.join(path)).display()
                    )
                } else {
                    flag
                }
            })
            .collect::<Vec<_>>();
        for command in &mut request.commands {
            if let Some(index) = &mut command.source {
                *index += shared.len();
            }
            let mut arguments = shared.clone();
            arguments.append(&mut command.arguments);
            command.arguments = arguments;
        }
    }
    let odin = request
        .odin
        .clone()
        .or_else(|| {
            request
                .commands
                .first()
                .and_then(|command| command.odin.clone())
        })
        .or_else(|| env::var("THOR_ODIN").ok())
        .unwrap_or_else(|| "odin".into());
    let odin = crate::frontend::executable(&odin)?
        .to_string_lossy()
        .into_owned();
    Ok(Settings {
        source,
        source_argument,
        destination,
        odin,
        flags: request.flags,
        dry_run: request.dry_run,
        commands: request.commands,
        command_odin: request.odin,
        cwd,
    })
}

fn inventory(root: &Path, directory: &Path, entries: &mut Vec<Entry>) -> Result<()> {
    let mut paths = fs::read_dir(directory)?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<std::io::Result<Vec<_>>>()?;
    paths.sort();
    for path in paths {
        let name = path.file_name().unwrap().to_string_lossy();
        if matches!(
            name.as_ref(),
            ".git" | ".hg" | ".svn" | ".thor-cache" | ".thor-build" | ".thor-rust-build"
        ) {
            continue;
        }
        let metadata = fs::symlink_metadata(&path)?;
        let relative = path.strip_prefix(root).unwrap().to_path_buf();
        let contents = if metadata.is_symlink() {
            let target = fs::read_link(&path)?;
            if path.extension().is_some_and(|ext| ext == "odin") && path.is_file() {
                Contents::SourceLink(target, fs::read(&path)?)
            } else {
                Contents::Link(target)
            }
        } else if metadata.is_dir() {
            Contents::Directory
        } else if metadata.is_file() {
            if metadata.len() > 1024 * 1024
                && path.extension().is_none_or(|ext| ext != "odin")
                && !relative.starts_with(".vscode")
            {
                Contents::Asset(metadata.len(), metadata.modified().ok())
            } else {
                Contents::File(fs::read(&path)?)
            }
        } else {
            return Err(Error::new(format!(
                "unsupported special file in project: {}",
                path.display()
            )));
        };
        entries.push(Entry {
            relative,
            contents,
            permissions: metadata.permissions(),
        });
        if metadata.is_dir() {
            inventory(root, &path, entries)?;
        }
    }
    Ok(())
}

pub(super) fn converted(path: &Path) -> PathBuf {
    if path
        .extension()
        .is_some_and(|extension| extension == "odin")
    {
        path.with_extension("thor")
    } else {
        path.into()
    }
}

fn entry_path(entry: &Entry) -> PathBuf {
    if matches!(entry.contents, Contents::Directory) {
        entry.relative.clone()
    } else {
        converted(&entry.relative)
    }
}

fn relative_link(from: &Path, to: &Path) -> PathBuf {
    let from = from.components().collect::<Vec<_>>();
    let to = to.components().collect::<Vec<_>>();
    let common = from.iter().zip(&to).take_while(|(a, b)| a == b).count();
    let mut path = PathBuf::new();
    for _ in common..from.len() {
        path.push("..");
    }
    for component in &to[common..] {
        path.push(component.as_os_str());
    }
    if path.as_os_str().is_empty() {
        path.push(".");
    }
    path
}

pub(super) fn rebased_flags(settings: &Settings, destination: &Path) -> Result<Vec<String>> {
    settings
        .flags
        .iter()
        .map(|flag| (flag, &settings.source))
        .chain(settings.commands.first().into_iter().flat_map(|command| {
            command
                .arguments
                .iter()
                .take_while(|arg| arg.as_str() != "--")
                .filter(|arg| {
                    arg.starts_with("-collection:")
                        || arg.starts_with("-define:")
                        || arg.starts_with("-target:")
                })
                .map(|flag| (flag, &settings.cwd))
        }))
        .map(|(flag, cwd)| {
            let Some(value) = flag.strip_prefix("-collection:") else {
                return Ok(flag.clone());
            };
            let (name, path) = value
                .split_once('=')
                .ok_or_else(|| Error::usage("-collection requires NAME=PATH"))?;
            let path = Path::new(path);
            let path = crate::project::canonicalize(if path.is_absolute() {
                path.to_path_buf()
            } else {
                cwd.join(path)
            })?;
            let path = path
                .strip_prefix(&settings.source)
                .map(|relative| destination.join(relative))
                .unwrap_or(path);
            Ok(format!("-collection:{name}={}", path.display()))
        })
        .collect()
}

fn write_tree(
    root: &Path,
    original: &Path,
    entries: &[Entry],
    replacements: &BTreeMap<PathBuf, Vec<u8>>,
) -> Result<()> {
    fs::create_dir(root)?;
    for entry in entries {
        let path = root.join(entry_path(entry));
        match &entry.contents {
            Contents::Directory => fs::create_dir(&path)?,
            Contents::File(bytes) | Contents::SourceLink(_, bytes) => {
                let bytes = replacements.get(&entry.relative).unwrap_or(bytes);
                let mut file = fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&path)?;
                file.write_all(bytes)?;
                let permissions = if matches!(entry.contents, Contents::SourceLink(..)) {
                    fs::metadata(original.join(&entry.relative))?.permissions()
                } else {
                    entry.permissions.clone()
                };
                fs::set_permissions(path, permissions)?;
            }
            Contents::Asset(..) => {
                let mut input = fs::File::open(original.join(&entry.relative))?;
                let mut output = fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&path)?;
                std::io::copy(&mut input, &mut output)?;
                fs::set_permissions(path, entry.permissions.clone())?;
            }
            Contents::Link(target) => {
                let resolved = original
                    .join(&entry.relative)
                    .parent()
                    .unwrap()
                    .join(target);
                let resolved = absolute(&resolved)?;
                let target = if let Ok(relative) = resolved.strip_prefix(original) {
                    let converted = if resolved.is_dir() {
                        relative.to_path_buf()
                    } else {
                        converted(relative)
                    };
                    relative_link(entry.relative.parent().unwrap(), &converted)
                } else {
                    resolved.clone()
                };
                #[cfg(unix)]
                std::os::unix::fs::symlink(target, path)?;
                #[cfg(windows)]
                {
                    if resolved.is_dir() {
                        std::os::windows::fs::symlink_dir(target, path)?;
                    } else {
                        std::os::windows::fs::symlink_file(target, path)?;
                    }
                }
            }
        }
    }
    Ok(())
}

pub fn main(args: &[String], toolchain: &Path) -> Result<i32> {
    let request = request(args)?;
    if request.source.is_none() {
        for command in &request.commands {
            println!("{}", command.translated(None, request.odin.as_deref()));
        }
        return Ok(0);
    }
    let settings = settings(request)?;
    let mapping = commands::Mapping {
        source: settings.source.clone(),
        alias: Some(settings.source_argument.clone()),
        destination: settings.destination.clone(),
        cwd: settings.cwd.clone(),
    };
    let translated = settings
        .commands
        .iter()
        .map(|command| command.translated(Some(&mapping), settings.command_odin.as_deref()))
        .collect::<Vec<_>>();
    let root_permissions = fs::metadata(&settings.source)?.permissions();
    let mut entries = vec![];
    inventory(&settings.source, &settings.source, &mut entries)?;
    let mut targets = BTreeMap::new();
    let mut source_count = 0;
    for entry in &entries {
        let target = entry_path(entry);
        if let Some(previous) = targets.insert(target.clone(), entry.relative.clone()) {
            return Err(Error::new(format!(
                "migration filename conflict between {} and {}",
                previous.display(),
                entry.relative.display()
            )));
        }
        if !matches!(entry.contents, Contents::Directory)
            && entry
                .relative
                .extension()
                .is_some_and(|extension| extension == "odin")
        {
            source_count += 1;
        }
    }
    if source_count == 0 {
        return Err(Error::new("project contains no .odin files"));
    }
    let unique = format!(
        "thor-migrate-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    );
    let temporary_parent = if settings.dry_run {
        env::temp_dir()
    } else {
        let parent = settings.destination.parent().unwrap();
        fs::create_dir_all(parent)?;
        parent.to_path_buf()
    };
    let work = WorkDirectory(temporary_parent.join(unique));
    fs::create_dir(&work.0)?;
    let cases = validation::original(&settings, &entries, &work.0)?;
    let (executable, odin, odin_root) = bootstrap(toolchain, &settings.odin)?;
    let analysis_flags = rebased_flags(&settings, &settings.source)?;
    let target = analysis_flags
        .iter()
        .find_map(|flag| flag.strip_prefix("-target:"))
        .and_then(|target| target.split_once('_'))
        .map(|(os, arch)| (os.into(), arch.into()));
    let frontend = Frontend {
        odin_syntax: false,
        executable,
        odin,
        odin_root,
        fingerprint: "migration".into(),
        cache_dir: None,
        normalized_cache: false,
        test: false,
        target,
        project_name: settings
            .source
            .file_name()
            .unwrap()
            .to_string_lossy()
            .into_owned(),
    };
    let (mut replacements, mut messages) = plan::create(&settings, &frontend, &entries)?;
    config::prepare(&settings, &entries, &mut replacements, &mut messages)?;
    if settings.source.file_name() != settings.destination.file_name() {
        messages.push("The destination folder has a different name. Its default executable and ODIN_BUILD_PROJECT_NAME will use the new name.".into());
    }
    for message in &messages {
        eprintln!("{message}");
    }
    for entry in entries
        .iter()
        .filter(|entry| entry_path(entry) != entry.relative)
    {
        eprintln!(
            "{} -> {}",
            entry.relative.display(),
            entry_path(entry).display()
        );
    }
    let stage = work.0.join(settings.destination.file_name().unwrap());
    write_tree(&stage, &settings.source, &entries, &replacements)?;
    validation::converted(&settings, &cases, &stage, &work.0, toolchain)?;
    // Compare saved bytes before publishing, including files that required no edits.
    let mut current = vec![];
    inventory(&settings.source, &settings.source, &mut current)?;
    if fs::metadata(&settings.source)?.permissions() != root_permissions
        || current.len() != entries.len()
        || current.iter().zip(&entries).any(|(a, b)| {
            a.relative != b.relative
                || a.permissions != b.permissions
                || !same_contents(&a.contents, &b.contents)
        })
    {
        return Err(Error::new(
            "source project changed during migration; run the command again",
        ));
    }
    if settings.dry_run {
        eprintln!(
            "Dry run passed Odin and Thor build validation. Destination would be {}",
            settings.destination.display()
        );
        for command in &translated {
            println!("{command}");
        }
        return Ok(0);
    }
    for entry in entries.iter().rev() {
        if matches!(entry.contents, Contents::Directory) {
            fs::set_permissions(stage.join(&entry.relative), entry.permissions.clone())?;
        }
    }
    fs::set_permissions(&stage, root_permissions)?;
    #[cfg(not(windows))]
    {
        // POSIX rename replaces only this empty reservation. A racing writer
        // that adds entries makes rename fail, preserving that writer's files.
        fs::create_dir(&settings.destination)
            .map_err(|error| Error::io("could not reserve migration destination", error))?;
        if let Err(error) = fs::rename(&stage, &settings.destination) {
            let _ = fs::remove_dir(&settings.destination);
            return Err(Error::io("could not publish migrated project", error));
        }
    }
    // Windows directory rename already refuses an existing destination.
    #[cfg(windows)]
    fs::rename(&stage, &settings.destination)?;
    eprintln!(
        "Migrated {source_count} Odin files to {}. Odin and Thor build validation passed.",
        settings.destination.display()
    );
    for command in &translated {
        println!("{command}");
    }
    Ok(0)
}

fn same_contents(left: &Contents, right: &Contents) -> bool {
    match (left, right) {
        (Contents::File(a), Contents::File(b)) => a == b,
        (Contents::Link(a), Contents::Link(b)) => a == b,
        (Contents::SourceLink(a, bytes_a), Contents::SourceLink(b, bytes_b)) => {
            a == b && bytes_a == bytes_b
        }
        (Contents::Asset(length_a, time_a), Contents::Asset(length_b, time_b)) => {
            length_a == length_b && time_a == time_b
        }
        (Contents::Directory, Contents::Directory) => true,
        _ => false,
    }
}
