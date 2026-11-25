//! Preserve native policy checks on original source, including migrated code.
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
    !project.options.native_policy_probe
        && !matches!(project.options.command.as_str(), "transpile" | "ast")
        && (project.options.odin_compatible
            && crate::native_compat::source_policies(&project.options.flags)
            || project.options.flags.iter().any(|flag| {
                crate::cli::native_source_check(
                    flag.trim_start_matches('-')
                        .split(':')
                        .next()
                        .unwrap_or_default(),
                )
            })
            || project.sources.iter().any(|source| {
                source.thor
                    && !source.excluded
                    && (source_tags_requested(source)
                        || project.options.odin_compatible
                            && (source.raw.contains("// thor-migrate #+vet")
                                || source.raw.contains("// Odin vet ")))
            }))
        && project
            .sources
            .iter()
            .any(|source| source.thor && !source.excluded)
}

/// Native tags have their own inventory (including tag-only policies). Let Odin
/// validate names and syntax; a negation must not bypass the checking view.
fn source_tags_requested(source: &Source) -> bool {
    source.info["file_tags"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .any(|tag| {
            let mut words = tag.trim_start_matches("#+").split_whitespace();
            if words.next() != Some("vet") {
                return false;
            }
            let names = words
                .flat_map(|w| w.split(','))
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>();
            names.is_empty()
                || names.iter().any(|name| {
                    !matches!(
                        *name,
                        "no_alloc"
                            | "semicolon"
                            | "!semicolon"
                            | "when-shadowing"
                            | "!when-shadowing"
                    )
                })
        })
}

pub(crate) fn tag_spans(source: &Source) -> Vec<Span> {
    let mut spans = vec![];
    let mut cursor = 0;
    for tag in source.info["file_tags"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        if tag.trim_start_matches("#+").split_whitespace().next() != Some("vet") {
            continue;
        }
        if let Some((start, _)) = source.raw.match_indices(tag).find(|(start, _)| {
            *start >= cursor
                && !source
                    .comments
                    .iter()
                    .any(|span| span.start <= *start && *start < span.end)
        }) {
            cursor = start + tag.len();
            spans.push(Span::new(start, cursor));
        }
    }
    spans
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

fn using_feature(source: &Source) -> bool {
    let configured = source.info["file_tags"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .any(|tag| {
            tag.contains("feature")
                && tag
                    .split_whitespace()
                    .any(|word| matches!(word, "using-stmt" | "!using-stmt"))
        });
    !configured
        && source.nodes.iter().any(|node| {
            node.kind == "Using"
                || node.kind == "Field"
                    && node.data["using_field"] == true
                    && node.parent.is_some_and(|p| {
                        source.node(p).kind == "Fields"
                            && source.node(p).parent.is_some_and(|p| {
                                source.node(p).kind == "ProcedureType"
                                    && source.role(p, "params") == node.parent
                            })
                    })
        })
}
fn advanced_source(source: &Source) -> bool {
    source.thor
        && !source.excluded
        && (using_feature(source)
            || source.info["features"]
                .as_array()
                .into_iter()
                .flatten()
                .any(|f| f["kind"] != "PureFunction")
            || source.nodes.iter().enumerate().any(|(i, n)| {
                n.kind == "CaseComparison" || n.kind == "Attribute"
                    && (!crate::unused_attributes::elements(source, NodeId(i)).is_empty()
                        || !crate::vet_attributes::elements(source, NodeId(i)).is_empty())
            }))
}
fn advanced(project: &Project) -> bool {
    project.sources.iter().any(advanced_source)
}
fn native_tag(source: &Source) -> bool {
    source.info["file_tags"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .any(|tag| {
            let mut words = tag.trim_start_matches("#+").split_whitespace();
            words.next() == Some("vet") && {
                let names = words
                    .flat_map(|w| w.split(','))
                    .filter(|n| !n.is_empty())
                    .collect::<Vec<_>>();
                names.is_empty() || names.iter().any(|n| *n != "no_alloc")
            }
        })
}
fn selected_package(project: &Project, source: &Source, prefix: &str) -> bool {
    let package = source.names.get(source.node(NodeId(0)).name);
    project
        .options
        .flags
        .iter()
        .find_map(|f| f.strip_prefix(prefix))
        .is_none_or(|names| names.split(',').any(|name| name == package))
}

fn compiler_source(project: &Project, source: &Source) -> bool {
    let entry = if project.file_entry {
        project.options.entry.parent().unwrap()
    } else {
        &project.options.entry
    };
    source
        .path
        .starts_with(project.options.root.join("runtime"))
        && source.path.parent() != Some(entry)
}

/// Header tags inherit command policies before the package declaration, just
/// as Odin does. Any explicit vet tag therefore overrides the package filter.
fn using_policy(project: &Project, source: &Source, name: &str) -> bool {
    let cli = project
        .options
        .flags
        .iter()
        .any(|flag| flag == &format!("-vet-{name}") || name == "using-stmt" && flag == "-vet");
    let mut enabled =
        cli && (native_tag(source) || selected_package(project, source, "-vet-packages:"));
    for tag in source.info["file_tags"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        let mut words = tag.trim_start_matches("#+").split_whitespace();
        if words.next() != Some("vet") {
            continue;
        }
        let words = words
            .flat_map(|w| w.split(','))
            .filter(|w| !w.is_empty())
            .collect::<Vec<_>>();
        if words.is_empty() && name == "using-stmt" {
            enabled = true;
        }
        for word in words {
            if word == name {
                enabled = true;
            } else if word.strip_prefix('!') == Some(name) {
                enabled = false;
            }
        }
    }
    enabled
}
fn style_options(project: &Project, source: &Source) -> u8 {
    let package = source.names.get(source.node(NodeId(0)).name);
    let selected = |prefix: &str| {
        project
            .options
            .flags
            .iter()
            .find_map(|f| f.strip_prefix(prefix))
            .is_none_or(|names| names.split(',').any(|name| name == package))
    };
    let mut style = 0;
    if project.options.flags.iter().any(|f| f == "-vet-style") {
        style |= 1;
    }
    if project.options.flags.iter().any(|f| f == "-vet-tabs") {
        style |= 4;
    }
    let tags = source.info["file_tags"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|t| t.trim_start_matches("#+").split_whitespace().next() == Some("vet"))
        .collect::<Vec<_>>();
    if tags.is_empty() && !selected("-vet-packages:") {
        style = 0;
    }
    for tag in tags {
        for word in tag
            .trim_start_matches("#+")
            .split_whitespace()
            .skip(1)
            .flat_map(|w| w.split(','))
        {
            match word {
                "style" => style |= 1,
                "!style" => style &= !1,
                "tabs" => style |= 4,
                "!tabs" => style &= !4,
                _ => {}
            }
        }
    }
    if project.options.flags.iter().any(|f| f == "-strict-style")
        && selected("-strict-style-packages:")
    {
        style |= 2;
    }
    style
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn projection<'a>(
    project: &'a Project,
    file: usize,
    checking_resolver: &mut Option<crate::semantics::Resolver<'a>>,
) -> Result<Emitted> {
    let source = &project.sources[file];
    let contextual = checking_resolver.as_ref().is_some_and(|resolver| {
        resolver
            .parameter_witnesses
            .values()
            .any(|w| w.annotation.file == file)
    });
    if advanced_source(source) || compiler_source(project, source) || contextual {
        let resolver = checking_resolver.get_or_insert_with(|| {
            let mut resolver = project.resolver();
            resolver.prepare_contexts();
            resolver.set_evaluations(project.evaluated.clone());
            resolver
        });
        let mut lower = crate::lower::Lower::new(file, resolver)?;
        lower.apply_evaluation_results(
            project
                .evaluated
                .iter()
                .filter(|(r, _)| r.file == file)
                .map(|(r, v)| (r.node, v.clone()))
                .collect(),
        );
        lower.apply_specialized_results(
            project
                .specialized
                .iter()
                .filter(|(r, _)| r.file == file)
                .map(|(r, v)| (r.node, v.clone()))
                .collect(),
            project.evaluation_scheduled,
        );
        lower.apply_evaluation_skips(
            project
                .evaluation_skips
                .iter()
                .filter(|(r, _)| r.file == file)
                .map(|(r, v)| (r.node, *v))
                .collect(),
        );
        let view = lower.native_checking_view()?;
        // Tabs are checked on original statements, not generated indentation.
        // Compiler implementation files never inherit a user's native policy.
        let off = if compiler_source(project, source)
            || !native_tag(source) && !selected_package(project, source, "-vet-packages:")
        {
            "#+vet !unused !unused-procedures !shadowing !using-stmt !using-param !style !semicolon !deprecated !cast !tabs !explicit-allocators !when-shadowing !nil-deref !uninitialized !atomic-access\n"
        } else {
            "#+vet !tabs\n"
        };
        let name = source
            .node(NodeId(0))
            .spans
            .get("package")
            .map(|s| s.start)
            .unwrap_or(0);
        let at = source.raw[..name]
            .rmatch_indices("package")
            .find(|(at, _)| {
                !source
                    .comments
                    .iter()
                    .any(|c| c.start <= *at && *at < c.end)
            })
            .map(|(at, _)| at)
            .unwrap_or(0);
        let veto_using = !compiler_source(project, source)
            && source.nodes.iter().any(|n| {
                n.kind == "Using" && using_policy(project, source, "using-stmt")
                    || n.kind == "Field"
                        && n.data["using_field"] == true
                        && using_policy(project, source, "using-param")
                        && n.parent.is_some_and(|p| {
                            source.node(p).kind == "Fields"
                                && source.node(p).parent.is_some_and(|p| {
                                    source.node(p).kind == "ProcedureType"
                                        && source.role(p, "params") == n.parent
                                })
                        })
            });
        let off = if using_feature(source) && !veto_using {
            format!("{off}#+feature using-stmt\n")
        } else {
            off.to_owned()
        };
        // Locate the native package token in the emitted header; source maps
        // are composed rather than discarded when inserting the policy tag.
        let offset = view
            .segments
            .iter()
            .find_map(|segment| {
                let o = &segment.origin;
                (o.exact && o.span.start <= at && at < o.span.end)
                    .then_some(segment.start + at.saturating_sub(o.span.start))
            })
            .unwrap_or(0);
        return view
            .rewrite(&[crate::emit::MappedEdit {
                span: Span::new(offset, offset),
                text: off.into(),
                origin: crate::emit::Origin {
                    file: source.path.to_string_lossy().into_owned(),
                    span: Span::new(at, at),
                    kind: "native-check-support".into(),
                    exact: false,
                },
            }])
            .map_err(Error::new);
    }
    let mut edits = source
        .edits
        .iter()
        .map(|edit| (edit.span, edit.text.clone()))
        .collect::<Vec<_>>();
    edits.extend(crate::noalloc::tag_edits(source));
    for (i, node) in source.nodes.iter().enumerate() {
        let id = NodeId(i);
        if source.thor && node.kind == "Attribute" {
            let removed = crate::noalloc::elements(source, id);
            if removed.is_empty() {
                continue;
            }
            let kept = source
                .group(id, "elems")
                .iter()
                .filter(|item| !removed.contains(item))
                .map(|item| source.text(*item))
                .collect::<Vec<_>>();
            edits.push((
                node.span,
                if kept.is_empty() {
                    String::new()
                } else {
                    format!("@({})", kept.join(", "))
                },
            ));
        }
    }
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
    if let Some(position) = value.get("pos") {
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
            // Odin embeds the earlier declaration line in its shadowing text.
            // Compose that secondary location through the checking source map too.
            if let Some(messages) = value.get_mut("msgs").and_then(Value::as_array_mut) {
                for message in messages {
                    let Some(text) = message.as_str() else {
                        continue;
                    };
                    let marker = "shadows declaration at line ";
                    let Some((prefix, tail)) = text.split_once(marker) else {
                        continue;
                    };
                    let digits = tail.bytes().take_while(u8::is_ascii_digit).count();
                    let Some(line) = tail[..digits]
                        .parse::<usize>()
                        .ok()
                        .filter(|line| *line > 0)
                    else {
                        continue;
                    };
                    let start = view
                        .text
                        .split_inclusive('\n')
                        .take(line - 1)
                        .map(str::len)
                        .sum::<usize>();
                    let end = start
                        + view.text[start..]
                            .find('\n')
                            .unwrap_or(view.text.len() - start);
                    if let Some(origin) = view.segments.iter().find(|segment| {
                        segment.start < end && segment.end > start && segment.origin.exact
                    }) {
                        let at = origin.origin.span.start + start.saturating_sub(origin.start);
                        let original = source.raw[..at].bytes().filter(|c| *c == b'\n').count() + 1;
                        *message =
                            Value::String(format!("{prefix}{marker}{original}{}", &tail[digits..]));
                    }
                }
            }
            if let Some(origin) = view.lookup(offset, length) {
                value["pos"] =
                    Error::at(source, origin.span, "").diagnostics.remove(0)["pos"].clone();
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

/// A type witness needs its signature, not a second execution of user code in
/// a captured helper. Stub compiler-owned witness bodies before native checks.
/// Explicit user calls/reads/writes live in the source-mapped inline view.
fn prepare_witnesses(probe: &mut Project, project: &Project, views: &BTreeMap<PathBuf, Emitted>) {
    use crate::ast::Edit;
    for source in &mut probe.sources {
        let Some(original) = project.sources.iter().find(|s| s.path == source.path) else {
            continue;
        };
        if !views.contains_key(&source.path) {
            continue;
        }
        let mut bodies = vec![];
        let mut edits = vec![];
        for (i, node) in source.nodes.iter().enumerate() {
            if node.kind != "Declaration" {
                continue;
            }
            let id = NodeId(i);
            let names = source.group(id, "names");
            if names.is_empty()
                || !source.text(names[0]).starts_with(&original.prefix)
                || source
                    .text(names[0])
                    .starts_with(&format!("{}vet_validate_", original.prefix))
            {
                continue;
            }
            for value in source.group(id, "values") {
                if source.node(*value).kind != "Procedure" {
                    continue;
                }
                let (Some(body), Some(signature)) =
                    (source.role(*value, "body"), source.role(*value, "type"))
                else {
                    continue;
                };
                let mut text = String::from("{\n");
                if let Some(params) = source.role(signature, "params") {
                    for field in source
                        .node(params)
                        .children
                        .iter()
                        .filter(|f| source.node(**f).kind == "Field")
                    {
                        for name in source.group(*field, "names") {
                            if source.text(*name) != "_" {
                                text.push_str(&format!(
                                    "_ = {}\n",
                                    source.text(*name).trim_start_matches('$')
                                ));
                            }
                        }
                    }
                }
                if source.node(signature).data["diverging"] == true {
                    text.push_str("unreachable()\n");
                } else {
                    let count = source
                        .role(signature, "results")
                        .map(|r| {
                            source
                                .node(r)
                                .children
                                .iter()
                                .filter(|f| source.node(**f).kind == "Field")
                                .map(|f| source.group(*f, "names").len().max(1))
                                .sum::<usize>()
                        })
                        .unwrap_or(0);
                    text.push_str("return ");
                    text.push_str(&vec!["{}"; count].join(","));
                    text.push('\n');
                }
                text.push('}');
                bodies.push(source.node(body).span);
                edits.push(Edit {
                    span: source.node(body).span,
                    text,
                });
                edits.push(Edit {
                    span: Span::new(node.span.start, node.span.start),
                    text: "@(require)\n".into(),
                });
            }
        }
        for (i, node) in source.nodes.iter().enumerate() {
            if node.kind != "Import" {
                continue;
            }
            let alias = source.names.get(node.name);
            if !alias.starts_with(&original.prefix) {
                continue;
            }
            let used = source.nodes.iter().enumerate().any(|(j, n)| {
                j != i
                    && n.kind == "Ident"
                    && source.names.get(n.name) == alias
                    && !node.span.contains(n.span)
                    && !bodies.iter().any(|b| b.contains(n.span))
            });
            if !used {
                if let Some(offset) = source.slice(node.span).find(alias) {
                    edits.push(Edit {
                        span: Span::new(
                            node.span.start + offset,
                            node.span.start + offset + alias.len(),
                        ),
                        text: "_".into(),
                    });
                }
            }
        }
        source.edits.extend(edits);
    }
}

pub(super) fn validate(project: &Project) -> Result<(i32, Vec<Value>)> {
    if !active(project) {
        return Ok((0, vec![]));
    }
    let _phase = crate::profile::span("backend.native_source_checks");
    if advanced(project) {
        for source in project
            .sources
            .iter()
            .filter(|s| s.thor && !s.excluded && !compiler_source(project, s))
        {
            let style = style_options(project, source);
            if style != 0 {
                project.frontend.parse_native_style(source, style)?;
            }
        }
    }
    let scratch = Scratch::new()?;
    let mut options = project.options.clone();
    options.native_policy_probe = true;
    options.native_policy_only = false;
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
        let output = crate::native_compat::artifact_path(&scratch.0,"artifact",&options.flags);
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
    let mut checking_resolver = None;
    for (file, source) in project
        .sources
        .iter()
        .enumerate()
        .filter(|(_, source)| source.thor && !source.excluded)
    {
        let view = projection(project, file, &mut checking_resolver)?;
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
    prepare_witnesses(&mut probe, project, &views);
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
