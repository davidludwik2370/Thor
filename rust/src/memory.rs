//! Debug instrumentation stays out of ordinary builds and compile-time helpers.
use crate::{
    ast::{NodeId, Source, Span},
    diagnostic::{Error, Result},
    digest::sha256,
    emit::{Emitted, Origin, Segment},
};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    env,
    path::{Path, PathBuf},
};
#[derive(Clone, Default)]
pub struct Memory {
    pub tracking: String,
    pub address: bool,
    pub extra: Vec<String>,
    pub symbolizer: String,
}
impl Memory {
    pub fn select(
        command: &str,
        flags: &[String],
        requested: &str,
        no_sanitize: bool,
    ) -> Result<Self> {
        if !matches!(requested, "auto" | "off" | "basic" | "trace") {
            return Err(Error::new(
                "--track-allocations must be auto, off, basic or trace",
            ));
        }
        let target = flags
            .iter()
            .find_map(|flag| flag.strip_prefix("-target:"))
            .unwrap_or("");
        let native =
            cfg!(target_os = "macos") && (target.is_empty() || target.starts_with("darwin_"));
        let debug = flags.iter().any(|flag| flag == "-debug");
        let test = command == "test" || flags.iter().any(|flag| flag == "-build-mode:test");
        let mode = flags
            .iter()
            .find_map(|flag| flag.strip_prefix("-build-mode:"))
            .unwrap_or("exe");
        let executable = matches!(command, "build" | "run" | "test")
            && matches!(mode, "exe" | "test")
            && !flags.iter().any(|flag| flag == "-no-entry-point");
        let tracking = if requested == "auto" {
            if native && debug && executable && !test {
                "basic"
            } else {
                "off"
            }
        } else {
            requested
        };
        if tracking != "off" && (!native || !executable || test) {
            return Err(Error::new("Automatic allocation tracking currently supports native macOS executables. Tests use Odin's per-test tracker; use --track-allocations=off here."));
        }
        if tracking == "trace" && !debug {
            return Err(Error::new(
                "--track-allocations=trace requires -debug for allocation call stacks",
            ));
        }
        let automatic = native
            && debug
            && executable
            && !no_sanitize
            && !flags.iter().any(|flag| flag.starts_with("-sanitize:"));
        let address = native
            && executable
            && (automatic || flags.iter().any(|flag| flag == "-sanitize:address"));
        let mut extra = vec![];
        if automatic {
            extra.push("-sanitize:address".into());
        }
        if address
            && !flags.iter().any(|flag| {
                matches!(
                    flag.as_str(),
                    "-use-single-module" | "-use-separate-modules"
                )
            })
        {
            extra.push("-use-single-module".into());
        }
        let symbolizer = if address {
            find_llvm().unwrap_or_default()
        } else {
            String::new()
        };
        Ok(Self {
            tracking: tracking.into(),
            address,
            extra,
            symbolizer,
        })
    }
    pub fn enabled(&self) -> bool {
        self.tracking != "off" || self.address
    }
    pub fn compact(
        &self,
        outputs: &BTreeMap<PathBuf, (usize, Emitted)>,
        sources: &[Source],
    ) -> Value {
        let mut files = vec![];
        let mut units = vec![];
        let mut aliases = BTreeMap::new();
        for (path, (file, emitted)) in outputs {
            let s = &sources[*file];
            let file_id = files
                .iter()
                .position(|value| value == &s.path.to_string_lossy().to_string())
                .unwrap_or_else(|| {
                    files.push(s.path.to_string_lossy().to_string());
                    files.len() - 1
                });
            let lines = line_starts(&emitted.text);
            let mut rows: Vec<Value> = vec![];
            for segment in &emitted.segments {
                if segment.origin.kind == "memory-debug" {
                    continue;
                }
                let mut cursor = segment.start;
                while cursor < segment.end {
                    let (line, column) = position(&emitted.text, &lines, cursor);
                    let stop = segment
                        .end
                        .min(lines.get(line).copied().unwrap_or(emitted.text.len()));
                    let content_end = if stop > 0 && emitted.text.as_bytes()[stop - 1] == b'\n' {
                        stop - 1
                    } else {
                        stop
                    };
                    let last = column
                        + emitted.text[cursor..content_end].chars().count()
                        + usize::from(stop > content_end);
                    let offset = if segment.origin.exact {
                        segment.origin.span.start + cursor - segment.start
                    } else {
                        segment.origin.span.start
                    };
                    let (original_line, original_column) = rune_position(s, offset);
                    let procedure = owner_name(s, offset);
                    rows.push(json!([
                        line,
                        column,
                        last.max(column + 1),
                        file_id,
                        original_line,
                        original_column,
                        segment.origin.exact,
                        procedure
                    ]));
                    cursor = stop;
                }
            }
            units.push(json!({"path":path,"rows":rows}));
            if s.path.extension().is_some_and(|ext| ext == "odin") {
                aliases.insert(
                    path.to_string_lossy().to_string(),
                    s.path.to_string_lossy().to_string(),
                );
            }
        }
        json!({"version":1,"files":files,"units":units,"aliases":aliases,"symbolizer":self.symbolizer})
    }
    pub fn finish(
        &self,
        outputs: &mut BTreeMap<PathBuf, (usize, Emitted)>,
        sources: &[Source],
        first: usize,
        collection: &str,
        root: &Path,
        map: &str,
    ) -> Result<()> {
        if !self.enabled() {
            return Ok(());
        }
        let data = self.compact(outputs, sources);
        let source = &sources[first];
        let prefix = format!("{}memory_", source.prefix);
        let memory = format!("{prefix}support");
        let runtime = format!("{prefix}runtime");
        let mut footer=format!("\nimport {memory} \"{collection}:debug_memory\"\nimport {runtime} \"base:runtime\"\n@(private=\"file\")\n{prefix}files :: []{memory}.File{{\n");
        let files = data["files"].as_array().cloned().unwrap_or_default();
        for unit in data["units"].as_array().into_iter().flatten() {
            footer.push_str(&format!("{{{}, []{memory}.Span{{\n", unit["path"]));
            for row in unit["rows"].as_array().into_iter().flatten() {
                let file = row[3].as_u64().unwrap_or(0) as usize;
                footer.push_str(&format!(
                    "{{{},{},{},{},{},{},{},{}}},\n",
                    row[0], row[1], row[2], files[file], row[4], row[5], row[6], row[7]
                ));
            }
            footer.push_str("}},\n");
        }
        footer.push_str("}\n");
        footer.push_str(&format!(
            "@(private=\"file\")\n{prefix}aliases :: []{memory}.Alias{{"
        ));
        for (path, original) in data["aliases"].as_object().into_iter().flatten() {
            footer.push_str(&format!("{{{},{original}}},", json!(path)));
        }
        footer.push_str("}\n");
        footer.push_str(&format!("@(private=\"package\",optimization_mode=\"none\")\n{map} :: #force_no_inline proc \"contextless\"(loc:{runtime}.Source_Code_Location)->{runtime}.Source_Code_Location {{return {memory}.map_location(loc,{prefix}files,{prefix}aliases)}}\n"));
        if self.address {
            let blob = encode(&data)?;
            footer.push_str(&format!(
                "@(export,link_section=\"__DATA,__thor_map\")\n{prefix}blob: [{}]u8={{",
                blob.len()
            ));
            for byte in blob {
                footer.push_str(&format!("{byte},"));
            }
            footer.push_str("}\n");
            let custom = sources.iter().any(|s| {
                s.nodes.iter().enumerate().any(|(i, n)| {
                    n.kind == "Declaration"
                        && (s
                            .group(NodeId(i), "names")
                            .iter()
                            .any(|name| s.text(*name) == "__asan_default_options")
                            || s.group(NodeId(i), "attributes").iter().any(|attribute| {
                                s.text(*attribute)
                                    .split_whitespace()
                                    .collect::<String>()
                                    .contains("link_name=\"__asan_default_options\"")
                            }))
                })
            });
            if !custom && !self.symbolizer.is_empty() {
                let libc = format!("{prefix}libc");
                let options = format!(
                    "external_symbolizer_path=\"{}\"",
                    root.join("llvm-symbolizer-thor").display()
                );
                footer.push_str(&format!("import {libc} \"core:c/libc\"\n@(export,no_sanitize_address,link_name=\"__asan_default_options\")\n{prefix}asan_options :: proc \"c\"()->cstring {{if {libc}.getenv(\"ASAN_SYMBOLIZER_PATH\")!=nil{{return \"\"}};return {}}}\n",json!(options)));
            }
        }
        if let Some((_, (_, emitted))) = outputs.iter_mut().find(|(_, (file, _))| *file == first) {
            let start = emitted.text.len();
            emitted.text.push_str(&footer);
            emitted.segments.push(Segment {
                start,
                end: emitted.text.len(),
                origin: Origin {
                    file: source.path.to_string_lossy().into_owned(),
                    span: Span::new(0, 0),
                    kind: "memory-debug".into(),
                    exact: false,
                },
            });
        }
        Ok(())
    }
}
pub fn rune_position(source: &Source, offset: usize) -> (usize, usize) {
    source.rune_position(offset)
}
fn line_starts(raw: &str) -> Vec<usize> {
    std::iter::once(0)
        .chain(
            raw.bytes()
                .enumerate()
                .filter(|(_, b)| *b == b'\n')
                .map(|(i, _)| i + 1),
        )
        .collect()
}
fn position(raw: &str, lines: &[usize], offset: usize) -> (usize, usize) {
    let row = lines
        .partition_point(|start| *start <= offset)
        .saturating_sub(1);
    (row + 1, raw[lines[row]..offset].chars().count() + 1)
}
fn owner_name(source: &Source, offset: usize) -> String {
    source
        .nodes
        .iter()
        .enumerate()
        .filter(|(_, n)| n.kind == "Procedure" && n.span.start <= offset && offset < n.span.end)
        .min_by_key(|(_, n)| n.span.len())
        .and_then(|(i, _)| source.node(NodeId(i)).parent)
        .and_then(|decl| source.group(decl, "names").first())
        .map(|name| source.text(*name).to_string())
        .unwrap_or_default()
}
fn find_llvm() -> Option<String> {
    let mut candidates = vec![];
    if let Ok(path) = env::var("THOR_LLVM_SYMBOLIZER") {
        candidates.push(PathBuf::from(path));
    }
    if let Some(paths) = env::var_os("PATH") {
        candidates.extend(env::split_paths(&paths).map(|p| p.join("llvm-symbolizer")));
    }
    candidates.extend([
        PathBuf::from("/opt/homebrew/opt/llvm/bin/llvm-symbolizer"),
        PathBuf::from("/usr/local/opt/llvm/bin/llvm-symbolizer"),
    ]);
    candidates
        .into_iter()
        .find(|path| {
            if !path.is_file() {
                return false;
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                path.metadata()
                    .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
            }
            #[cfg(not(unix))]
            {
                true
            }
        })
        .map(|p| p.to_string_lossy().into_owned())
}
fn encode(value: &Value) -> Result<Vec<u8>> {
    let raw = serde_json::to_vec(value)?;
    const MAX_PAYLOAD: usize = 32 * 1024 * 1024;
    let compressed_size = 6 + raw.len() + raw.len().div_ceil(65535) * 5;
    if compressed_size > MAX_PAYLOAD {
        return Err(Error::new(
            "Thor debug source map exceeds the 32 MiB payload limit",
        ));
    }
    let mut compressed = vec![0x78, 0x01];
    for (i, chunk) in raw.chunks(65535).enumerate() {
        compressed.push(u8::from((i + 1) * 65535 >= raw.len()));
        let n = chunk.len() as u16;
        compressed.extend(n.to_le_bytes());
        compressed.extend((!n).to_le_bytes());
        compressed.extend(chunk);
    }
    let (mut a, mut b) = (1u32, 0u32);
    for byte in raw {
        a = (a + u32::from(byte)) % 65521;
        b = (b + a) % 65521;
    }
    compressed.extend(((b << 16) | a).to_be_bytes());
    let digest = sha256(&compressed);
    let mut blob = b"THOR-MEM-MAP\0\x01".to_vec();
    blob.extend((compressed.len() as u64).to_le_bytes());
    for pair in digest.as_bytes().chunks(2) {
        let digit = |x: u8| if x <= b'9' { x - b'0' } else { x - b'a' + 10 };
        blob.push(digit(pair[0]) * 16 + digit(pair[1]));
    }
    blob.extend(compressed);
    Ok(blob)
}
