//! Compile validation artifacts in scratch storage. Test runners are built only.
use super::{commands, Contents, Entry, Settings};
use crate::diagnostic::{Error, Result};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Output},
};

pub(super) struct Case {
    action: String,
    entry: PathBuf,
    flags: Vec<String>,
    cwd: PathBuf,
    odin: String,
}

fn test_case(settings: &Settings) -> Case {
    Case {
        action: "build".into(),
        entry: settings.source.clone(),
        flags: settings
            .flags
            .iter()
            .filter(|flag| !flag.starts_with("-build-mode:"))
            .cloned()
            .chain(["-build-mode:test".into()])
            .collect(),
        cwd: settings.source.clone(),
        odin: settings.odin.clone(),
    }
}

fn has_tests(entries: &[Entry]) -> bool {
    entries.iter().any(|entry| {
        if entry.relative.parent() != Some(Path::new("")) {
            return false;
        }
        if entry
            .relative
            .file_name()
            .is_some_and(|name| name.to_string_lossy().ends_with("_test.odin"))
        {
            return true;
        }
        match &entry.contents {
            Contents::File(bytes) | Contents::SourceLink(_, bytes) => {
                let text = String::from_utf8_lossy(bytes);
                text.contains("#+test")
                    || text.contains('@')
                        && crate::diagnosticcleanup::tokens(&text).contains(&"test")
            }
            _ => false,
        }
    })
}

impl Case {
    fn flags(&self, settings: &Settings, destination: &Path, output: &Path) -> Result<Vec<String>> {
        let mapping = commands::Mapping {
            source: settings.source.clone(),
            alias: Some(settings.source_argument.clone()),
            destination: destination.into(),
            cwd: self.cwd.clone(),
        };
        let mut flags = vec![];
        for flag in &self.flags {
            let flag = commands::path_flag(flag, Some(&mapping));
            let flag = if let Some(value) = flag.strip_prefix("-collection:") {
                let (name, path) = value
                    .split_once('=')
                    .ok_or_else(|| Error::usage("-collection requires NAME=PATH"))?;
                format!(
                    "-collection:{name}={}",
                    commands::lexical(&self.cwd.join(path)).display()
                )
            } else {
                flag
            };
            let name = flag.split(':').next().unwrap_or(&flag);
            if name == "-out" {
                continue;
            }
            if matches!(
                name,
                "-export-timings-file"
                    | "-export-dependencies-file"
                    | "-export-defineables"
                    | "-export-linked-libs-file"
            ) {
                flags.push(format!(
                    "{name}:{}",
                    output.join(name.trim_start_matches('-')).display()
                ));
            } else {
                flags.push(flag);
            }
        }
        if self.action == "build" {
            let artifact = output.join("artifact");
            if flags
                .iter()
                .any(|flag| matches!(flag.as_str(), "-build-mode:llvm" | "-build-mode:llvm-ir"))
            {
                fs::create_dir_all(&artifact)?;
            }
            flags.push(format!("-out:{}", artifact.display()));
        }
        Ok(flags)
    }

    fn native(&self, settings: &Settings, output: &Path) -> Result<Output> {
        fs::create_dir_all(output)?;
        Ok(Command::new(&self.odin)
            .arg(&self.action)
            .arg(&self.entry)
            .args(self.flags(settings, &settings.source, output)?)
            .current_dir(&self.cwd)
            .output()?)
    }

    fn thor(
        &self,
        settings: &Settings,
        stage: &Path,
        output: &Path,
        toolchain: &Path,
    ) -> Result<Output> {
        fs::create_dir_all(output)?;
        let original = fs::canonicalize(&self.entry).unwrap_or_else(|_| self.entry.clone());
        let entry = if let Ok(relative) = original.strip_prefix(&settings.source) {
            let entry = stage.join(relative);
            if self.entry.is_file() {
                super::converted(&entry)
            } else {
                entry
            }
        } else {
            self.entry.clone()
        };
        let flags = self.flags(settings, stage, output)?;
        let compatible =
            !settings.commands.is_empty() || crate::native_compat::source_policies(&self.flags);
        let mut command = Command::new(std::env::current_exe()?);
        command
            .arg(&self.action)
            .arg(entry)
            .args(flags)
            .args([
                "--output-dir",
                output.join("generated").to_str().unwrap(),
                "--cache-dir",
                output.join("cache").to_str().unwrap(),
            ])
            .arg("--odin")
            .arg(&self.odin)
            .env("THOR_ROOT", toolchain)
            .current_dir(&self.cwd);
        if compatible {
            command.arg("--odin-compatible");
        }
        Ok(command.output()?)
    }
}

fn failure(label: &str, result: &Output) -> Error {
    Error::new(format!(
        "{label}:\n{}{}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    ))
}

pub(super) fn original(settings: &Settings, entries: &[Entry], work: &Path) -> Result<Vec<Case>> {
    if !settings.commands.is_empty() {
        let mut cases = vec![];
        for invocation in &settings.commands {
            if !matches!(
                invocation.action.as_str(),
                "build" | "run" | "test" | "check"
            ) {
                continue;
            }
            let Some(index) = invocation.source else {
                continue;
            };
            let mut flags = settings.flags.clone();
            flags.extend(
                invocation
                    .arguments
                    .iter()
                    .take_while(|arg| arg.as_str() != "--")
                    .filter(|arg| arg.starts_with('-') && !arg.starts_with("-out:"))
                    .cloned(),
            );
            let path = &invocation.arguments[index];
            let entry = if let Some(path) = commands::collection_entry(path, &flags, &settings.cwd)
            {
                path
            } else if path.contains(':') && !Path::new(path).is_absolute() {
                PathBuf::from(path)
            } else {
                commands::lexical(&settings.cwd.join(path))
            };
            if invocation.action == "test"
                && !flags.iter().any(|flag| flag.starts_with("-build-mode:"))
            {
                flags.push("-build-mode:test".into());
            }
            let case = Case {
                action: if invocation.action == "check" {
                    "check".into()
                } else {
                    "build".into()
                },
                entry,
                flags,
                cwd: settings.cwd.clone(),
                odin: settings
                    .command_odin
                    .clone()
                    .or_else(|| invocation.odin.clone())
                    .unwrap_or_else(|| settings.odin.clone()),
            };
            let result = case.native(settings, &work.join(format!("original-{}", cases.len())))?;
            if !result.status.success() {
                return Err(failure(
                    "Odin could not validate the original command",
                    &result,
                ));
            }
            cases.push(case);
        }
        if !cases.is_empty() {
            return Ok(cases);
        }
    }
    let case = Case {
        action: "build".into(),
        entry: settings.source.clone(),
        flags: settings.flags.clone(),
        cwd: settings.source.clone(),
        odin: settings.odin.clone(),
    };
    let result = case.native(settings, &work.join("original-build"))?;
    let tests = has_tests(entries);
    if !result.status.success() {
        let case = test_case(settings);
        let test = case.native(settings, &work.join("original-tests"))?;
        if test.status.success() {
            return Ok(vec![case]);
        }
        return Err(failure(
            "Odin could not check the original project",
            &result,
        ));
    }
    let mut cases = vec![case];
    if tests {
        let case = test_case(settings);
        let result = case.native(settings, &work.join("original-tests"))?;
        if result.status.success() {
            cases.push(case);
        } else {
            eprintln!("Original test code failed to compile. Migration will validate the executable build.\n{}", String::from_utf8_lossy(&result.stderr));
        }
    }
    Ok(cases)
}

pub(super) fn converted(
    settings: &Settings,
    cases: &[Case],
    stage: &Path,
    work: &Path,
    toolchain: &Path,
) -> Result<()> {
    for (index, case) in cases.iter().enumerate() {
        let result = case.thor(
            settings,
            stage,
            &work.join(format!("converted-{index}")),
            toolchain,
        )?;
        if !result.status.success() {
            return Err(failure("Converted project failed Thor validation", &result));
        }
    }
    Ok(())
}
