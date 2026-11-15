//! Preserve native policy checks across migration's syntax-only Thor edits.
//!
//! Check a source-preserving native view before compiling the regular Thor
//! lowering. Native checks observe the original lexical scopes and expressions,
//! while ordinary Thor validation still enforces fn and immutable-view rules.
use super::*;
use crate::ast::NodeId;
use crate::emit::Code;
use std::sync::atomic::{AtomicU64, Ordering};

pub(super) fn checking_flag(flag: &str) -> bool {
    flag.starts_with("-vet") || flag.starts_with("-strict-style")
}

pub(super) fn active(project: &Project) -> bool {
    project.options.odin_compatible
        && !project.options.native_policy_probe
        && (crate::native_compat::source_policies(&project.options.flags)
            || project.sources.iter().any(|source| {
                source.raw.contains("// thor-migrate #+vet") || source.raw.contains("// Odin vet ")
            }))
        && project
            .sources
            .iter()
            .any(|source| source.thor && !source.excluded)
}

struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Result<Self> {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let path = env::temp_dir().join(format!(
            "thor-native-check-{}-{stamp}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path)?;
        Ok(Self(path))
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn projection(source: &Source) -> Result<Emitted> {
    for feature in source.info["features"].as_array().into_iter().flatten() {
        if feature["kind"] == "PureFunction" {
            continue;
        }
        let start = feature["start"].as_u64().unwrap_or_default() as usize;
        let end = feature["end"].as_u64().unwrap_or(start as u64) as usize;
        return Err(Error::at(source, Span::new(start, end), format!(
            "Native source checks cannot yet project the Thor feature '{}'. The migration checking view supports fn, immutable declarations and #mut parameters.",
            feature["kind"].as_str().unwrap_or("expression")
        )));
    }
    let mut edits = source
        .edits
        .iter()
        .map(|edit| (edit.span, edit.text.clone()))
        .collect::<Vec<_>>();
    let package = source
        .node(NodeId(0))
        .spans
        .get("package")
        .map(|span| span.start)
        .unwrap_or(source.raw.len());
    for comment in source.info["comments"].as_array().into_iter().flatten() {
        let Some(span) = Span::from_wire(comment) else {
            continue;
        };
        if span.end <= package && source.slice(span).starts_with("// thor-migrate #+vet") {
            edits.push((
                Span::new(span.start, span.start + "// thor-migrate ".len()),
                String::new(),
            ));
        } else if span.end <= package && source.slice(span).starts_with("// Odin vet ") {
            edits.push((
                Span::new(span.start, span.start + "// Odin vet".len()),
                "#+vet".into(),
            ));
        }
    }
    edits.sort_by_key(|(span, _)| (span.start, span.end));
    let mut cursor = 0;
    let mut parts = vec![];
    for (span, text) in edits {
        if span.start < cursor {
            return Err(Error::at(
                source,
                span,
                "Overlapping native source-check projections.",
            ));
        }
        parts.push(Code::source(
            source.slice(Span::new(cursor, span.start)),
            &source.path,
            Span::new(cursor, span.start),
        ));
        parts.push(Code::generated(
            text,
            &source.path,
            span,
            "native-policy-syntax",
        ));
        cursor = span.end;
    }
    parts.push(Code::source(
        source.slice(Span::new(cursor, source.raw.len())),
        &source.path,
        Span::new(cursor, source.raw.len()),
    ));
    Ok(Code::concat(parts).finish())
}

fn remap(value: &mut Value, project: &Project, views: &BTreeMap<PathBuf, Emitted>) {
    if let Some(position) = value.get_mut("pos") {
        let path = position["file"].as_str().map(PathBuf::from);
        if let Some((source, view)) = path.as_ref().and_then(|path| {
            Some((
                project.sources.iter().find(|source| source.path == *path)?,
                views.get(path)?,
            ))
        }) {
            let offset = position["offset"].as_u64().unwrap_or_default() as usize;
            let length = position["end_offset"]
                .as_u64()
                .map(|end| end as usize)
                .unwrap_or(offset + 1)
                .saturating_sub(offset)
                .max(1);
            if let Some(origin) = view.lookup(offset, length) {
                *position = Error::at(source, origin.span, "").diagnostics.remove(0)["pos"].clone();
            }
        }
    }
    match value {
        Value::Array(values) => {
            for value in values {
                remap(value, project, views);
            }
        }
        Value::Object(fields) => {
            for (name, value) in fields {
                if name != "pos" {
                    remap(value, project, views);
                }
            }
        }
        _ => {}
    }
}

pub(super) fn validate(project: &Project) -> Result<(i32, Vec<Value>)> {
    if !active(project) {
        return Ok((0, vec![]));
    }
    let _phase = crate::profile::span("backend.native_source_checks");
    let scratch = Scratch::new()?;
    let mut options = project.options.clone();
    options.native_policy_probe = true;
    options.vet = None;
    options.vet_apply = false;
    options.vet_ask = false;
    options.raw_diagnostics = false;
    options.json = true;
    options.editor_info = false;
    options.tracking = "off".into();
    options.no_sanitize = true;
    options.clear_cache = false;
    options.cache_report = false;
    options.output = Some(scratch.0.join("generated"));
    options.cache = project.frontend.cache_dir.clone();
    options.command = if project.frontend.test
        || matches!(
            project.options.command.as_str(),
            "build" | "run" | "test" | "publish"
        ) {
        "build".into()
    } else {
        "check".into()
    };
    options.flags.retain(|flag| {
        !flag.starts_with("-out:")
            && !flag.starts_with("-show-")
            && !flag.starts_with("-export-")
            && !matches!(
                flag.as_str(),
                "-keep-executable"
                    | "-keep-temp-files"
                    | "-print-linker-flags"
                    | "-build-diagnostics"
            )
    });
    if project.frontend.test
        && !options
            .flags
            .iter()
            .any(|flag| flag.starts_with("-build-mode:"))
    {
        options.flags.push("-build-mode:test".into());
    }
    if options.command == "build" {
        let output = scratch
            .0
            .join(format!("artifact{}", env::consts::EXE_SUFFIX));
        if options
            .flags
            .iter()
            .any(|flag| matches!(flag.as_str(), "-build-mode:llvm" | "-build-mode:llvm-ir"))
        {
            fs::create_dir(&output)?;
        }
        options.flags.push(format!("-out:{}", output.display()));
    }
    let mut views = BTreeMap::new();
    for source in project
        .sources
        .iter()
        .filter(|source| source.thor && !source.excluded)
    {
        let view = projection(source)?;
        options
            .overlays
            .insert(source.path.clone(), view.text.clone());
        views.insert(source.path.clone(), view);
    }
    let mut probe = Project::new(options).map_err(|mut error| {
        for diagnostic in &mut error.diagnostics {
            remap(diagnostic, project, &views);
        }
        error
    })?;
    // Keep the source-check program free of Thor's instrumentation helpers.
    // Explicit native sanitizer flags still reach the native compiler.
    probe.memory = crate::memory::Memory::default();
    probe.collected_diagnostics = Some(Default::default());
    let target = probe.lower()?;
    let status = probe.compile(&target)?;
    let mut diagnostics = probe.collected_diagnostics.take().unwrap().into_inner();
    for diagnostic in &mut diagnostics {
        remap(diagnostic, project, &views);
    }
    probe.release();
    Ok((status, diagnostics))
}
