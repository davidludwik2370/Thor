//! Native command policies used by translated Odin invocations.
use crate::{cli, diagnostic::Result};
use std::{
    env,
    path::{Path, PathBuf},
};

pub(crate) fn source_policies(flags: &[String]) -> bool {
    flags.iter().any(|flag| {
        let name = flag
            .trim_start_matches('-')
            .split(':')
            .next()
            .unwrap_or_default();
        cli::native_check_rejection(name).is_some()
            || matches!(
                name,
                "bedrock"
                    | "no-rtti"
                    | "disable-init-fini"
                    | "disable-non-constant-globals"
                    | "disallow-do"
            )
    })
}

pub(crate) fn default_output(entry: &Path, file: bool, flags: &[String]) -> Result<PathBuf> {
    let cwd = env::current_dir()?;
    let source_name = if file {
        entry.file_stem()
    } else {
        entry.file_name()
    }
    .unwrap_or_default()
    .to_string_lossy();
    let name = source_name.trim();
    let target = flags
        .iter()
        .find_map(|flag| flag.strip_prefix("-target:"))
        .unwrap_or(env::consts::OS);
    let mode = flags
        .iter()
        .find_map(|flag| flag.strip_prefix("-build-mode:"))
        .unwrap_or("exe");
    let windows = target.starts_with("windows");
    let darwin = target.starts_with("darwin") || target == "macos";
    let extension = if target.contains("wasm") {
        "wasm"
    } else {
        match mode {
            "dll" | "dynamic" => {
                if windows {
                    "dll"
                } else if darwin {
                    "dylib"
                } else {
                    "so"
                }
            }
            "static" => {
                if windows {
                    "lib"
                } else {
                    "a"
                }
            }
            "obj" => {
                if windows {
                    "obj"
                } else {
                    "o"
                }
            }
            "assembly" => "S",
            _ if windows => "exe",
            _ if flags.iter().any(|flag| flag == "-subtarget:android") => "so",
            _ if cwd.join(source_name.as_ref()).is_dir()
                || file && entry.with_extension("").is_dir() =>
            {
                "bin"
            }
            _ => "",
        }
    };
    Ok(cwd.join(if extension.is_empty() {
        name.to_string()
    } else {
        format!("{name}.{extension}")
    }))
}
