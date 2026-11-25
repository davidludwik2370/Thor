//! Adapt structured VS Code tasks while retaining their other settings.
use super::{Contents, Entry, Settings};
use crate::diagnostic::Result;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

struct ShellWord {
    start: usize,
    end: usize,
    value: String,
    separator: bool,
}

fn shell_words(text: &str) -> Option<Vec<ShellWord>> {
    if text.contains(['\n', '\r', '`']) || text.contains("$(") {
        return None;
    }
    let raw = text.as_bytes();
    let mut cursor = 0;
    let mut words = vec![];
    while cursor < raw.len() {
        if raw[cursor].is_ascii_whitespace() {
            cursor += 1;
            continue;
        }
        if raw[cursor] == b'#' {
            return None;
        }
        let start = cursor;
        if b";&|()<>".contains(&raw[cursor]) {
            cursor += 1;
            if cursor < raw.len() && raw[cursor] == raw[start] {
                cursor += 1;
            }
            words.push(ShellWord {
                start,
                end: cursor,
                value: text[start..cursor].into(),
                separator: true,
            });
            continue;
        }
        let mut value: Vec<u8> = vec![];
        let mut quote = None;
        while cursor < raw.len() {
            let byte = raw[cursor];
            if quote.is_none() && (byte.is_ascii_whitespace() || b";&|()<>".contains(&byte)) {
                break;
            }
            let drive_path=value.windows(2).enumerate().any(|(i,pair)|
                pair[0].is_ascii_alphabetic() && pair[1]==b':'
                    && (i==0 || matches!(value[i-1],b':'|b'='))
                    && (i+2==value.len() || matches!(value[i+2],b'\\'|b'/')));
            let unc_path=cfg!(windows) && (value.starts_with(b"\\\\")
                || value.is_empty() && raw.get(cursor+1)==Some(&b'\\'));
            if byte == b'\\' && quote != Some(b'\'') && !drive_path && !unc_path {
                cursor += 1;
                if cursor == raw.len() {
                    return None;
                }
                value.push(raw[cursor]);
            } else if quote == Some(byte) {
                quote = None;
            } else if quote.is_none() && matches!(byte, b'\'' | b'"') {
                quote = Some(byte);
            } else {
                value.push(byte);
            }
            cursor += 1;
        }
        if quote.is_some() {
            return None;
        }
        words.push(ShellWord {
            start,
            end: cursor,
            value: String::from_utf8(value).ok()?,
            separator: false,
        });
    }
    Some(words)
}

fn native_command(command: &str, settings: &Settings) -> bool {
    command == "odin"
        || command == settings.odin
        || command.rsplit(['/', '\\']).next()
            .is_some_and(|name| name == "odin" || name.eq_ignore_ascii_case("odin.exe"))
}

fn shell_task(
    text: &str,
    settings: &Settings,
    entries: &[Entry],
) -> Option<(String, bool, String)> {
    let words = shell_words(text)?;
    let mut command = true;
    let mut native = false;
    let mut runtime = false;
    let mut compatibility = String::new();
    let mut debug = false;
    let mut edits = vec![];
    let mut output = "${workspaceFolder}/.thor-rust-build/${workspaceFolderBasename}".to_string();
    for (index, word) in words.iter().enumerate() {
        if word.separator {
            if native && !runtime {
                edits.push((word.start, word.start, format!(" {compatibility} ")));
            }
            command = matches!(word.value.as_str(), ";" | "&&" | "||" | "|" | "(" | ")");
            native = false;
            runtime = false;
            continue;
        }
        if command {
            if word.value.contains('=')
                || matches!(word.value.as_str(), "env" | "exec" | "command" | "time")
            {
                continue;
            }
            native = native_command(&word.value, settings)
                && words.get(index + 1).is_some_and(|next| {
                    matches!(
                        next.value.as_str(),
                        "build" | "run" | "test" | "check" | "doc" | "strip-semicolon"
                    )
                });
            if native {
                edits.push((word.start, word.end, "thor".to_string()));
                compatibility = "--odin-compatible".into();
                if word.value != "odin" {
                    compatibility.push_str(&format!(" --odin {}", &text[word.start..word.end]));
                }
            }
            command = false;
            continue;
        }
        if !native || runtime {
            continue;
        }
        if word.value == "--" {
            edits.push((word.start, word.start, format!("{compatibility} ")));
            runtime = true;
            continue;
        }
        debug |= word.value == "-debug";
        if word.value == "--output-dir" {
            if let Some(next) = words.get(index + 1) {
                output = next.value.clone();
            }
        } else if let Some(path) = word.value.strip_prefix("--output-dir=") {
            output = path.into();
        }
        if let Some(path) = source_path(&word.value, settings, entries) {
            if Path::new(&word.value).is_absolute() {
                edits.push((word.start, word.end, serde_json::to_string(&path).ok()?));
            } else if let Some(suffix) = text[word.start..word.end].rfind(".odin") {
                edits.push((
                    word.start + suffix,
                    word.start + suffix + 5,
                    ".thor".to_string(),
                ));
            }
        }
    }
    if native && !runtime {
        edits.push((text.len(), text.len(), format!(" {compatibility}")));
    }
    if edits.is_empty() {
        return None;
    }
    let mut result = text.to_string();
    for (start, end, replacement) in edits.into_iter().rev() {
        result.replace_range(start..end, &replacement);
    }
    Some((result, debug, format!("{output}/thor-manifest.json")))
}

fn source_path(value: &str, settings: &Settings, entries: &[Entry]) -> Option<String> {
    if let Ok(relative) = Path::new(value).strip_prefix(&settings.source) {
        if entries.iter().any(|entry| {
            entry.relative == relative
                && entry.relative.extension().is_some_and(|ext| ext == "odin")
        }) {
            return Some(format!(
                "${{workspaceFolder}}/{}",
                super::converted(relative).display()
            ));
        }
    }
    let candidate = value.strip_prefix("${workspaceFolder}/").unwrap_or(value);
    entries
        .iter()
        .find(|entry| {
            entry.relative == Path::new(candidate)
                && entry.relative.extension().is_some_and(|ext| ext == "odin")
        })
        .map(|_| {
            super::converted(Path::new(value))
                .to_string_lossy()
                .into_owned()
        })
}

fn load(entries: &[Entry], path: &str, messages: &mut Vec<String>) -> Option<Value> {
    let entry = entries
        .iter()
        .find(|entry| entry.relative == Path::new(path))?;
    let Contents::File(bytes) = &entry.contents else {
        return None;
    };
    match serde_json::from_slice(bytes) {
        Ok(document) => Some(document),
        Err(_) => {
            messages.push(format!("Review {path}: editor configuration contains JSON comments or syntax requiring manual conversion"));
            None
        }
    }
}

pub(super) fn prepare(
    settings: &Settings,
    entries: &[Entry],
    replacements: &mut BTreeMap<PathBuf, Vec<u8>>,
    messages: &mut Vec<String>,
) -> Result<()> {
    let mut debug_tasks = BTreeMap::new();
    if let Some(mut tasks) = load(entries, ".vscode/tasks.json", messages) {
        let mut changed = false;
        for task in tasks["tasks"].as_array_mut().into_iter().flatten() {
            if let Some(cwd) = task["options"]["cwd"].as_str() {
                if Path::new(cwd) == settings.source {
                    task["options"]["cwd"] = json!("${workspaceFolder}");
                    changed = true;
                } else if !matches!(cwd, "." | "${workspaceFolder}" | "${workspaceFolder}/") {
                    messages.push(
                        "Review .vscode/tasks.json: a task uses a custom working directory".into(),
                    );
                    continue;
                }
            }
            let command = task["command"].as_str().unwrap_or("");
            let native = native_command(command, settings);
            if !native {
                if task["type"] == "shell" {
                    if let Some((converted, debug, manifest)) =
                        shell_task(command, settings, entries)
                    {
                        task["command"] = json!(converted);
                        if debug {
                            if let Some(label) = task["label"].as_str() {
                                debug_tasks.insert(label.to_string(), manifest);
                            }
                        }
                        changed = true;
                        continue;
                    }
                }
                if command.contains("odin ") {
                    messages.push("Review .vscode/tasks.json: a shell command invokes Odin".into());
                }
                continue;
            }
            let native_executable = command.to_string();
            task["command"] = json!("thor");
            let label = task["label"].as_str().map(str::to_string);
            if let Some(arguments) = task["args"].as_array_mut() {
                let mut converted = vec![];
                let mut runtime = false;
                for argument in arguments.iter() {
                    let Some(text) = argument.as_str() else {
                        converted.push(argument.clone());
                        continue;
                    };
                    if text == "--" {
                        runtime = true;
                    }
                    if runtime {
                        converted.push(argument.clone());
                        continue;
                    }
                    converted.push(
                        source_path(text, settings, entries)
                            .map_or_else(|| argument.clone(), |path| json!(path)),
                    );
                }
                if converted
                    .first()
                    .and_then(Value::as_str)
                    .is_some_and(|action| {
                        matches!(
                            action,
                            "build" | "run" | "test" | "check" | "doc" | "strip-semicolon"
                        )
                    })
                {
                    let boundary = converted
                        .iter()
                        .position(|argument| argument == "--")
                        .unwrap_or(converted.len());
                    let mut compatibility = vec![json!("--odin-compatible")];
                    if native_executable != "odin" {
                        compatibility.extend([json!("--odin"), json!(native_executable)]);
                    }
                    converted.splice(boundary..boundary, compatibility);
                }
                *arguments = converted;
                if arguments
                    .iter()
                    .take_while(|value| *value != "--")
                    .any(|value| value == "-debug")
                {
                    let output = arguments
                        .windows(2)
                        .find(|pair| pair[0] == "--output-dir")
                        .and_then(|pair| pair[1].as_str())
                        .or_else(|| {
                            arguments
                                .iter()
                                .filter_map(Value::as_str)
                                .find_map(|arg| arg.strip_prefix("--output-dir="))
                        })
                        .unwrap_or(
                            "${workspaceFolder}/.thor-rust-build/${workspaceFolderBasename}",
                        );
                    if let Some(label) = &label {
                        debug_tasks.insert(label.clone(), format!("{output}/thor-manifest.json"));
                    }
                }
            }
            changed = true;
        }
        if changed {
            let mut bytes = serde_json::to_vec_pretty(&tasks)?;
            bytes.push(b'\n');
            replacements.insert(".vscode/tasks.json".into(), bytes);
            messages.push("Updated .vscode/tasks.json to invoke Thor".into());
        }
    }
    if let Some(mut launch) = load(entries, ".vscode/launch.json", messages) {
        let mut changed = false;
        for configuration in launch["configurations"]
            .as_array_mut()
            .into_iter()
            .flatten()
        {
            let label = configuration["preLaunchTask"].as_str().unwrap_or("");
            if let Some(manifest) = debug_tasks.get(label) {
                if configuration["type"] == "lldb" || configuration["type"] == "thor-lldb" {
                    configuration["type"] = json!("thor-lldb");
                    configuration["manifest"] = json!(manifest);
                    changed = true;
                }
            }
        }
        if changed {
            let mut bytes = serde_json::to_vec_pretty(&launch)?;
            bytes.push(b'\n');
            replacements.insert(".vscode/launch.json".into(), bytes);
            messages.push("Updated .vscode/launch.json with Thor debugger source maps".into());
        }
    }
    Ok(())
}
