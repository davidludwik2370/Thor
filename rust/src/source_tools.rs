//! Source-facing native tools must operate on original files, never lowered helpers.
use crate::{
    ast::{NodeId, Source, Span},
    diagnostic::{Error, Result},
    project::Project,
};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::PathBuf,
    process::{Command, Stdio},
};

fn signature(source: &Source, declaration: NodeId, short: bool) -> String {
    let mut end = source.node(declaration).span.end;
    if let Some(value) = source.group(declaration, "values").first() {
        if source.node(*value).kind == "Procedure" {
            if let Some(body) = source.role(*value, "body") {
                end = source.node(body).span.start;
            }
        } else if short
            && matches!(
                source.node(*value).kind.as_str(),
                "StructType" | "UnionType" | "EnumType"
            )
        {
            let raw = source.text(*value);
            if let Some(open) = raw.find('{') {
                end = source.node(*value).span.start + open;
            }
        }
    }
    source.raw[source.node(declaration).span.start..end]
        .trim_end_matches([';', ' ', '\t', '\r', '\n'])
        .to_string()
}

/// Native documentation selects the checked, active, public declarations and
/// their order. Restore original signatures (fn, :>, #mut, etc.) and filenames.
pub(crate) fn documentation(project: &Project, native: &str) -> String {
    let short = project.options.flags.iter().any(|flag| flag == "-short");
    let mut declarations: BTreeMap<(String, String), Vec<String>> = BTreeMap::new();
    let mut basenames = BTreeMap::new();
    for (path, (file, _)) in &project.outputs {
        let source = &project.sources[*file];
        basenames.insert(
            path.file_name().unwrap().to_string_lossy().to_string(),
            source
                .path
                .file_name()
                .unwrap()
                .to_string_lossy()
                .to_string(),
        );
        let package = source.names.get(source.node(NodeId(0)).name).to_string();
        for (index, node) in source.nodes.iter().enumerate() {
            let declaration = NodeId(index);
            if node.kind != "Declaration"
                || source.ancestors(declaration).any(|parent| {
                    matches!(
                        source.node(parent).kind.as_str(),
                        "Procedure" | "StructType" | "UnionType"
                    )
                })
            {
                continue;
            }
            for name in source.group(declaration, "names") {
                declarations
                    .entry((package.clone(), source.text(*name).into()))
                    .or_default()
                    .push(signature(source, declaration, short));
            }
        }
    }
    let mut text = String::new();
    let mut package = String::new();
    let mut body = false;
    for line in native.lines() {
        if let Some(name) = line.strip_prefix("package ") {
            package = name.trim().to_string();
        }
        if body {
            if line.starts_with("\t\t}") {
                body = false;
            }
            continue;
        }
        if line.starts_with("\t\t") && !line.starts_with("\t\t\t") {
            let item = line.trim_start();
            if let Some((name, _)) = item.split_once(" :") {
                if let Some(values) = declarations
                    .get(&(package.clone(), name.into()))
                    .filter(|values| values.len() == 1)
                {
                    for row in values[0].lines() {
                        text.push_str("\t\t");
                        text.push_str(row);
                        text.push('\n');
                    }
                    body = line.trim_end().ends_with('{');
                    continue;
                }
            }
        }
        let row = basenames
            .get(line.trim())
            .map(String::as_str)
            .unwrap_or(line.trim());
        if basenames.contains_key(line.trim()) {
            text.push_str("\t\t");
            text.push_str(row);
        } else {
            text.push_str(line);
        }
        text.push('\n');
    }
    project.remap_text(&text)
}

pub(crate) fn semicolon_edits(
    project: &Project,
    thor_only: bool,
) -> Result<Vec<(usize, Span, String)>> {
    let root = if project.options.entry.is_file() {
        project.options.entry.parent().unwrap()
    } else {
        &project.options.entry
    };
    let mut edits = vec![];
    for (file, source) in project.sources.iter().enumerate() {
        if !source.path.starts_with(root)
            || source.excluded
            || source.shared_native
            || thor_only && !source.thor
            || !source.raw.contains(';')
        {
            continue;
        }
        let mut command = Command::new(&project.frontend.executable);
        command
            .arg(&source.path)
            .args(["--stdin", "--source-tools"]);
        if !source.thor {
            command.arg("--odin");
        }
        if project.frontend.test {
            command.arg("--test");
        }
        command.arg(format!("--project-name={}", project.frontend.project_name));
        if let Some((os, arch)) = &project.frontend.target {
            command
                .arg(format!("--target-os={os}"))
                .arg(format!("--target-arch={arch}"));
        }
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        child
            .stdin
            .take()
            .ok_or_else(|| Error::new("source-tool input unavailable"))?
            .write_all(source.raw.as_bytes())?;
        let result = child.wait_with_output()?;
        if !result.status.success() {
            return Err(Error::new(format!(
                "Could not inspect semicolons in {}",
                source.path.display()
            )));
        }
        let info: Value = serde_json::from_slice(&result.stdout)?;
        for value in info["optional_semicolons"].as_array().into_iter().flatten() {
            let Some(span) = Span::from_wire(value) else {
                continue;
            };
            edits.push((file, span, String::new()));
        }
    }
    edits.sort_by_key(|(file, span, _)| (*file, span.start));
    edits.dedup_by_key(|(file, span, _)| (*file, span.start));
    Ok(edits)
}

pub(crate) fn strip_semicolons(project: &Project) -> Result<String> {
    let edits = semicolon_edits(project, false)?;
    let mut originals = BTreeMap::<PathBuf, Vec<u8>>::new();
    for (file, _, _) in &edits {
        originals
            .entry(project.sources[*file].path.clone())
            .or_insert_with(|| project.sources[*file].raw.as_bytes().to_vec());
    }
    if project
        .options
        .flags
        .iter()
        .any(|flag| flag == "-keep-temp-files")
    {
        for (path, bytes) in &originals {
            let backup = PathBuf::from(format!("{}~backup.thor-temp", path.display()));
            let mut output = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&backup)?;
            output.write_all(bytes)?;
            fs::set_permissions(&backup, fs::metadata(path)?.permissions())?;
        }
    }
    crate::vet::apply_source_edits(&edits, &project.sources)?;
    Ok(format!(
        "Files stripped of unnecessary semicolons: {}\n",
        originals.len()
    ))
}

/// Remap reports written by the native compiler (JSON, CSV, or Make format).
pub(crate) fn remap_exports(project: &Project) -> Result<()> {
    for flag in &project.options.flags {
        let Some(path) = [
            "-export-dependencies-file:",
            "-export-defineables:",
            "-export-timings-file:",
            "-export-linked-libs-file:",
        ]
        .iter()
        .find_map(|prefix| flag.strip_prefix(prefix)) else {
            continue;
        };
        let path = PathBuf::from(path);
        if !path.is_file() {
            continue;
        }
        let text = fs::read_to_string(&path)?;
        let mapped = if let Ok(mut value) = serde_json::from_str::<Value>(&text) {
            fn visit(project: &Project, value: &mut Value) {
                match value {
                    Value::String(text) => *text = project.remap_text(text),
                    Value::Array(items) => {
                        for item in items {
                            visit(project, item);
                        }
                    }
                    Value::Object(items) => {
                        for item in items.values_mut() {
                            visit(project, item);
                        }
                    }
                    _ => {}
                }
            }
            visit(project, &mut value);
            serde_json::to_string_pretty(&value)? + "\n"
        } else {
            project.remap_text(&text)
        };
        crate::frontend::atomic_write(&path, mapped.as_bytes())?;
    }
    Ok(())
}

/// Odin's documented 0.3.2 interchange layout uses absolute offsets. Append
/// replacement strings/entry arrays so every existing type/entity index stays
/// valid, then refresh its size and FNV checksum.
pub(crate) fn remap_document(project: &Project, output: &str) -> Result<()> {
    let filename = output
        .lines()
        .find_map(|line| line.strip_prefix("Wrote .odin-doc file to: "))
        .ok_or_else(|| {
            Error::new("The native compiler did not report a documentation export path")
        })?;
    let path = PathBuf::from(filename.trim());
    let mut data = fs::read(&path)?;
    fn word(data: &[u8], offset: usize) -> Result<usize> {
        let bytes = data
            .get(offset..offset + 4)
            .ok_or_else(|| Error::new("Invalid .odin-doc offset"))?;
        Ok(u32::from_le_bytes(bytes.try_into().unwrap()) as usize)
    }
    fn put(data: &mut [u8], offset: usize, value: usize) -> Result<()> {
        let value =
            u32::try_from(value).map_err(|_| Error::new("Documentation export exceeds 4 GiB"))?;
        data.get_mut(offset..offset + 4)
            .ok_or_else(|| Error::new("Invalid .odin-doc offset"))?
            .copy_from_slice(&value.to_le_bytes());
        Ok(())
    }
    fn array(data: &[u8], offset: usize, stride: usize) -> Result<Vec<usize>> {
        let (base, count) = (word(data, offset)?, word(data, offset + 4)?);
        if count > data.len() / stride || base > data.len().saturating_sub(count * stride) {
            return Err(Error::new("Invalid .odin-doc array"));
        }
        Ok((0..count).map(|index| base + index * stride).collect())
    }
    fn string(data: &[u8], offset: usize) -> Result<String> {
        let (base, count) = (word(data, offset)?, word(data, offset + 4)?);
        let text = data
            .get(
                base..base
                    .checked_add(count)
                    .ok_or_else(|| Error::new("Invalid .odin-doc string"))?,
            )
            .ok_or_else(|| Error::new("Invalid .odin-doc string"))?;
        Ok(std::str::from_utf8(text)
            .map_err(|_| Error::new("Invalid .odin-doc UTF-8"))?
            .to_string())
    }
    fn set_string(data: &mut Vec<u8>, offset: usize, text: &str) -> Result<()> {
        let start = data.len();
        data.extend_from_slice(text.as_bytes());
        data.push(0);
        put(data, offset, start)?;
        put(data, offset + 4, text.len())
    }
    if data.get(..8) != Some(b"odindoc\0")
        || data.get(12..15) != Some(&[0, 3, 2])
        || word(&data, 20)? != 60
        || word(&data, 16)? != data.len()
    {
        return Err(Error::new("Unsupported .odin-doc format from the selected toolchain; use thor doc for text documentation"));
    }
    let files = array(&data, 28, 12)?;
    let packages = array(&data, 36, 44)?;
    let entities = array(&data, 44, 112)?;
    let mut original_files = BTreeMap::new();
    for (index, file) in files.into_iter().enumerate() {
        let name = string(&data, file + 4)?;
        if let Some((source, emitted)) = project.outputs.get(&PathBuf::from(&name)) {
            original_files.insert(index, (*source, emitted));
        }
        let mapped = project.remap_text(&name);
        if mapped != name {
            set_string(&mut data, file + 4, &mapped)?;
        }
    }
    for entity in entities {
        let name = string(&data, entity + 32)?;
        let file = word(&data, entity + 16)?;
        let Some((source, emitted)) = original_files.get(&file) else {
            continue;
        };
        let Some(origin) = emitted.lookup(word(&data, entity + 28)?, 1) else {
            continue;
        };
        let source = &project.sources[*source];
        let (line, column) = source.position(origin.span.start);
        put(&mut data, entity + 20, line)?;
        put(&mut data, entity + 24, column)?;
        put(&mut data, entity + 28, origin.span.start)?;
        if let Some((declaration, _)) = source.nodes.iter().enumerate().find(|(index, node)| {
            node.kind == "Declaration"
                && node.span.contains(origin.span)
                && source
                    .group(NodeId(*index), "names")
                    .iter()
                    .any(|id| source.text(*id) == name)
        }) {
            let declaration = NodeId(declaration);
            let value = source.group(declaration, "values").first().copied();
            if let Some(value) = value {
                let end = source
                    .role(value, "body")
                    .filter(|_| source.node(value).kind == "Procedure")
                    .map_or(source.node(value).span.end, |body| {
                        source.node(body).span.start
                    });
                set_string(
                    &mut data,
                    entity + 44,
                    source.raw[source.node(value).span.start..end].trim(),
                )?;
            }
        }
    }
    for package in packages {
        let name = string(&data, package)?;
        let mapped = project.remap_text(&name);
        if mapped != name {
            set_string(&mut data, package, &mapped)?;
        }
        let entries = array(&data, package + 36, 12)?;
        let mut filtered = vec![];
        for entry in &entries {
            if !string(&data, *entry)?.starts_with("__thor_") {
                filtered.extend_from_slice(&data[*entry..*entry + 12]);
            }
        }
        if filtered.len() != entries.len() * 12 {
            while data.len() % 4 != 0 {
                data.push(0);
            }
            let base = data.len();
            data.extend_from_slice(&filtered);
            put(&mut data, package + 36, base)?;
            put(&mut data, package + 40, filtered.len() / 12)?;
        }
    }
    let length = data.len();
    put(&mut data, 16, length)?;
    let hash = data[60..].iter().fold(0x811c9dc5u32, |hash, byte| {
        (hash ^ u32::from(*byte)).wrapping_mul(0x01000193)
    });
    put(&mut data, 24, hash as usize)?;
    crate::frontend::atomic_write(&path, &data)
}
