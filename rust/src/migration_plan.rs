//! Rename native identifier tokens using existing lexical and nominal type facts.
use super::{Contents, Entry, Settings};
use crate::{
    ast::{NodeId, Source, Span},
    diagnostic::{Error, Result},
    frontend::Frontend,
    lower::unquote,
    semantics::{Ref, Resolver, Type},
};
use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

const NAMES: [&str; 5] = ["fn", "set", "or_panic", "Integer", "Rational"];
type Edit = (Span, String);

fn parse(frontend: &Frontend, path: &Path, bytes: &[u8]) -> Result<Source> {
    let mut child = Command::new(&frontend.executable)
        .arg(path)
        .args(["--odin", "--migration", "--stdin"])
        .arg(format!("--odin-root={}", frontend.odin_root))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    child
        .stdin
        .take()
        .ok_or_else(|| Error::new("migration parser input unavailable"))?
        .write_all(bytes)?;
    let result = child.wait_with_output()?;
    if !result.status.success() {
        return Err(Error::new(format!(
            "Could not parse Odin source {}:\n{}{}",
            path.display(),
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        )));
    }
    Source::from_migration_bytes(
        path.into(),
        String::from_utf8(bytes.to_vec()).map_err(|_| Error::new("Odin source is not UTF-8"))?,
        &result.stdout,
    )
}

fn identifier_spans(source: &Source) -> Vec<Span> {
    if let Some(spans) = &source.migration_identifiers { return spans.to_vec(); }
    source.info["migration_identifiers"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Span::from_wire)
        .collect()
}

fn imports(source: &Source) -> Vec<(String, String)> {
    source
        .nodes
        .iter()
        .filter(|node| node.kind == "Import")
        .filter_map(|node| {
            let path = unquote(&node.op).ok()?;
            let alias = source.names.get(node.name);
            Some((
                if alias.is_empty() {
                    path.rsplit(['/', ':']).next().unwrap_or("").into()
                } else {
                    alias.into()
                },
                path,
            ))
        })
        .collect()
}

fn native_sdk_import(source: &Source, path: &str) -> bool {
    let Some((collection, _)) = path.split_once(':') else { return false; };
    if !matches!(collection, "base" | "core" | "vendor") { return false; }
    let Some(configured) = source.collection_roots.get(collection) else { return false; };
    let Some(odin_root) = source.info["odin_root"].as_str() else { return false; };
    let Ok(configured) = crate::project::canonicalize(configured) else { return false; };
    crate::project::canonicalize(Path::new(odin_root).join(collection))
        .is_ok_and(|native| configured == native)
}

fn native_sdk_reference(resolver: &Resolver<'_>, mut site: Ref) -> bool {
    let mut seen = HashSet::new();
    while seen.insert(site) {
        let source = &resolver.sources[site.file];
        if source.node(site.node).kind == "Selector" {
            if let Some(base) = source.role(site.node, "expr") {
                if source.node(base).kind == "Ident"
                    && resolver.lookup(site, source.text(base)).is_none() {
                    return imports(source).iter().any(|(alias, path)|
                        alias == source.text(base) && native_sdk_import(source, path));
                }
            }
        }
        if source.node(site.node).kind == "Paren" {
            if let Some(node) = source.role(site.node, "expr") {site.node = node;continue;}
        }
        let Some(symbol) = resolver.symbol(site).map(|index| &resolver.symbols[index]) else {break;};
        let Some(node) = symbol.value else {break;};
        site = Ref {file:symbol.reference.file,node};
    }
    false
}

fn record_owner(typ: &Type) -> Option<Ref> {
    match typ {
        Type::Struct(owner) | Type::Record(owner, _) | Type::Enum(owner) | Type::Union(owner, _) => {
            Some(*owner)
        }
        Type::Pointer(inner) | Type::Nominal(_, inner) | Type::Soa(inner) => record_owner(inner),
        _ => None,
    }
}

fn annotation_type(resolver: &mut Resolver<'_>, site: Ref, depth: usize) -> Type {
    if depth > 32 {
        return Type::Unknown;
    }
    let source = &resolver.sources[site.file];
    if source.node(site.node).kind == "BitSetType" {
        return source
            .role(site.node, "elem")
            .map(|node| resolver.type_of(Ref { node, ..site }))
            .unwrap_or(Type::Unknown);
    }
    let typ = resolver.type_of(site);
    if typ.is_unknown() {
        if let Some(symbol) = resolver.symbol(site) {
            let symbol = &resolver.symbols[symbol];
            if let Some(node) = symbol.value.or(symbol.annotation) {
                let next = Ref {
                    file: symbol.reference.file,
                    node,
                };
                if next != site {
                    return annotation_type(resolver, next, depth + 1);
                }
            }
        }
    }
    typ
}

fn context_type(resolver: &mut Resolver<'_>, site: Ref, depth: usize) -> Type {
    if depth > 32 {
        return Type::Unknown;
    }
    let source = &resolver.sources[site.file];
    let Some(parent) = source.node(site.node).parent else {
        return Type::Unknown;
    };
    let parent_ref = Ref {
        node: parent,
        ..site
    };
    let reference = |node| Ref { node, ..site };
    match source.node(parent).kind.as_str() {
        "Compound" => {
            let typ = source
                .role(parent, "type")
                .map(|node| annotation_type(resolver, reference(node), 0))
                .unwrap_or_else(|| context_type(resolver, parent_ref, depth + 1));
            match typ {
                Type::Array { elem, .. }
                | Type::Slice(elem)
                | Type::Dynamic(elem)
                | Type::Fixed { elem, .. } => *elem,
                other => other,
            }
        }
        "Declaration" | "Field" => source
            .role(parent, "type")
            .map(|node| annotation_type(resolver, reference(node), 0))
            .unwrap_or(Type::Unknown),
        "Assignment" => {
            let index = source
                .group(parent, "rhs")
                .iter()
                .position(|node| *node == site.node)
                .unwrap_or(0);
            source
                .group(parent, "lhs")
                .get(index)
                .map(|node| resolver.type_of(reference(*node)))
                .unwrap_or(Type::Unknown)
        }
        "Binary" => {
            let other = if source.role(parent, "left") == Some(site.node) {
                source.role(parent, "right")
            } else {
                source.role(parent, "left")
            };
            other
                .map(|node| resolver.type_of(reference(node)))
                .unwrap_or(Type::Unknown)
        }
        "Case" => {
            let switch = source
                .ancestors(parent)
                .find(|node| source.node(*node).kind == "Switch");
            switch
                .and_then(|node| source.role(node, "expr"))
                .map(|node| resolver.type_of(reference(node)))
                .unwrap_or(Type::Unknown)
        }
        "Return" => {
            let index = source
                .group(parent, "values")
                .iter()
                .position(|node| *node == site.node)
                .unwrap_or(0);
            let signature = source
                .ancestors(parent)
                .find(|node| source.node(*node).kind == "Procedure")
                .and_then(|node| source.role(node, "type"));
            signature
                .and_then(|node| {
                    resolver
                        .fields(reference(node), "results")
                        .get(index)
                        .and_then(|(_, typ)| *typ)
                })
                .map(|typ| annotation_type(resolver, typ, 0))
                .unwrap_or(Type::Unknown)
        }
        "Call" => {
            let index = source
                .group(parent, "args")
                .iter()
                .position(|node| *node == site.node)
                .unwrap_or(0);
            resolver
                .call_signature(parent_ref)
                .and_then(|signature| {
                    resolver
                        .fields(signature, "params")
                        .get(index)
                        .and_then(|(_, typ)| *typ)
                })
                .map(|typ| annotation_type(resolver, typ, 0))
                .unwrap_or(Type::Unknown)
        }
        "FieldValue" => {
            if let Some(container) = source
                .node(parent)
                .parent
                .filter(|node| source.node(*node).kind == "Compound")
            {
                let typ = source
                    .role(container, "type")
                    .map(|node| annotation_type(resolver, reference(node), 0))
                    .unwrap_or_else(|| context_type(resolver, reference(container), depth + 1));
                if let Type::Map { key, value } = typ {
                    return if source.role(parent, "field") == Some(site.node) {
                        *key
                    } else {
                        *value
                    };
                }
                let name = source
                    .role(parent, "field")
                    .map(|node| source.text(node))
                    .unwrap_or("");
                return resolver.member_type(&typ, name);
            }
            context_type(resolver, parent_ref, depth + 1)
        }
        "Paren" | "ImplicitSelector" | "Ternary" | "WhenExpr" => {
            context_type(resolver, parent_ref, depth + 1)
        }
        _ => Type::Unknown,
    }
}

fn metadata_key(source: &Source, node: NodeId) -> bool {
    if source
        .node(node)
        .parent
        .is_some_and(|parent| source.node(parent).kind == "Attribute")
    {
        return true;
    }
    let mut key = node;
    while let Some(parent) = source
        .node(key)
        .parent
        .filter(|parent| source.node(*parent).kind == "Paren")
    {
        key = parent;
    }
    if let Some(parent) = source.node(key).parent {
        if source.node(parent).kind == "Call"
            && source
                .role(parent, "callee")
                .is_some_and(|callee| source.text(callee) == "#config")
            && source.group(parent, "args").first() == Some(&key)
        {
            return true;
        }
    }
    let Some(parent) = source.node(node).parent else {
        return false;
    };
    source.role(parent, "field") == Some(node)
        && source
            .ancestors(node)
            .any(|owner| source.node(owner).kind == "Attribute")
}

fn declared_member(source: &Source, node: NodeId, parent: NodeId) -> bool {
    match source.node(parent).kind.as_str() {
        "EnumType" => source.group(parent, "members").contains(&node),
        "FieldValue" => source.role(parent, "field") == Some(node)
            && source.node(parent).parent.is_some_and(|owner| source.node(owner).kind == "EnumType"),
        "Field" if source.group(parent, "names").contains(&node) => {
            let owner = source.ancestors(parent).find(|owner| matches!(
                source.node(*owner).kind.as_str(), "BitFieldType" | "StructType" | "UnionType" | "ProcedureType"
            ));
            owner.is_some_and(|owner| source.node(owner).kind == "BitFieldType"
                || source.node(owner).kind == "StructType" && source.node(owner).spans.get("fields")
                    .is_some_and(|fields| fields.contains(source.node(node).span)))
        }
        _ => false,
    }
}

/// Odin derives RTTI from declaration spelling, including the parameter labels
/// of specialized records. Keep these names throughout the converted tree:
/// references in inactive branches and named arguments need the same spelling.
fn reflected_spellings(sources: &[Source]) -> HashSet<String> {
    let mut keep = HashSet::new();
    for source in sources {
        for (index, node) in source.nodes.iter().enumerate() {
            let id = NodeId(index);
            if node.kind == "PolyType" {
                let name = source.names.get(node.name);
                if NAMES.contains(&name) { keep.insert(name.to_owned()); }
            } else if node.kind == "Declaration" && !node.mutable {
                // A constant expression can denote a type through an alias,
                // specialization or conditional. No configuration is privileged.
                for (name, value) in source.group(id, "names").iter().zip(source.group(id, "values")) {
                    let name = source.text(*name);
                    if NAMES.contains(&name) && !matches!(source.node(*value).kind.as_str(), "Literal" | "Procedure") {
                        keep.insert(name.to_owned());
                    }
                }
            } else if matches!(node.kind.as_str(), "StructType" | "UnionType") {
                // Odin also accepts record parameters without a leading dollar.
                if let Some(params) = source.role(id, "params") {
                    for field in &source.node(params).children {
                        for name in source.group(*field, "names") {
                            let name = source.text(*name);
                            if NAMES.contains(&name) { keep.insert(name.to_owned()); }
                        }
                    }
                }
            }
        }
    }
    keep
}

fn ambiguous_native_operand(source: &Source, span: Span) -> bool {
    let Some(id) = source.at(span, NodeId(0)) else { return false; };
    let Some(parent) = source.node(id).parent else { return false; };
    match source.slice(span) {
        "fn" => source.node(parent).kind == "Call" && source.role(parent, "callee") == Some(id),
        "set" => matches!(source.node(parent).kind.as_str(), "Index" | "Slice" | "MatrixIndex")
            && source.role(parent, "expr") == Some(id),
        _ => false,
    }
}

fn rename_identifier(
    resolver: &mut Resolver<'_>,
    file: usize,
    span: Span,
    root: &Path,
) -> Result<bool> {
    let source = &resolver.sources[file];
    let Some(node) = source.at(span, NodeId(0)) else {
        return Ok(true);
    };
    let site = Ref { file, node };
    let parent = source.node(node).parent;
    if let Some(parent) = parent {
        // Only the declared member is metadata. Its type or explicit enum
        // value is an ordinary expression and follows the referenced binding.
        if declared_member(source, node, parent) {
            return Ok(false);
        }
        if matches!(
            source.node(parent).kind.as_str(),
            "Declaration" | "Field" | "Range" | "TypeSwitch"
        ) && source.group(parent, "names").contains(&node)
        {
            return Ok(true);
        }
        if source.node(parent).kind == "Selector" && source.role(parent, "field") == Some(node) {
            let selector = Ref {
                node: parent,
                ..site
            };
            if native_sdk_reference(resolver, selector) {
                // SDK collection imports retain the selected backend's ABI.
                return Ok(false);
            }
            if let Some(symbol) = resolver.symbol(selector) {
                return Ok(resolver.sources[resolver.symbols[symbol].reference.file]
                    .path
                    .starts_with(root));
            }
            if let Some(base) = source.role(parent, "expr") {
                let typ = resolver.type_of(Ref { node: base, ..site });
                if record_owner(&typ).is_some() {
                    return Ok(false);
                }
                if source.node(base).kind == "Ident"
                    && resolver.lookup(site, source.text(base)).is_none()
                {
                    if let Some((_, path)) = imports(source)
                        .iter()
                        .find(|(alias, _)| alias == source.text(base))
                    {
                        let directory = if let Some((collection, suffix)) = path.split_once(':') {
                            source
                                .collection_roots
                                .get(collection)
                                .map(|root| root.join(suffix))
                        } else {
                            Some(source.path.parent().unwrap().join(path))
                        };
                        if let Some(directory) = directory {
                            return Ok(crate::project::canonicalize(&directory)
                                .unwrap_or(directory)
                                .starts_with(root));
                        }
                    }
                }
                return Ok(false);
            }
        }
        if source.node(parent).kind == "ImplicitSelector" {
            return Ok(false);
        }
        if source.node(parent).kind == "FieldValue" && source.role(parent, "field") == Some(node) {
            if let Some(container) = source.node(parent).parent {
                if source.node(container).kind == "Call" {
                    if source.role(container, "callee").is_some_and(|node|
                        native_sdk_reference(resolver, Ref {file,node})) {
                        return Ok(false);
                    }
                    if let Some(signature) = resolver.call_signature(Ref {
                        node: container,
                        ..site
                    }) {
                        return Ok(resolver.sources[signature.file].path.starts_with(root));
                    }
                } else if source.node(container).kind == "Compound" {
                    let typ = source
                        .role(container, "type")
                        .map(|node| resolver.type_of(Ref { node, ..site }))
                        .unwrap_or_else(|| {
                            context_type(
                                resolver,
                                Ref {
                                    node: container,
                                    ..site
                                },
                                0,
                            )
                        });
                    if record_owner(&typ).is_some() {
                        return Ok(false);
                    }
                    // T{key=value} may be a record literal before T is known.
                    // An expression key requires a binding in the original Odin
                    // scope (e.g. a map key). An unbound identifier names a field.
                    if resolver.lookup(site, source.text(node)).is_none()
                        && resolver.symbol(site).is_none()
                    {
                        return Ok(false);
                    }
                }
            }
        }
    }
    if let Some(symbol) = resolver.symbol(site) {
        let symbol = resolver.symbols[symbol].clone();
        if symbol.projection.is_some() {
            return Ok(false);
        }
        return Ok(resolver.sources[symbol.reference.file]
            .path
            .starts_with(root));
    }
    Ok(true)
}

fn load_demands(
    sources: &mut Vec<Source>,
    frontend: &Frontend,
    roots: &BTreeMap<String, PathBuf>,
    packages: BTreeSet<PathBuf>,
) -> Result<bool> {
    let mut changed = false;
    for package in packages {
        if !package.is_dir() {
            continue;
        }
        let mut files = fs::read_dir(package)?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<std::io::Result<Vec<_>>>()?;
        files.sort();
        for path in files
            .into_iter()
            .filter(|path| path.extension().is_some_and(|ext| ext == "odin"))
        {
            let path = crate::project::canonicalize(path)?;
            if sources.iter().any(|source| source.path == path) {
                continue;
            }
            let mut source = parse(frontend, &path, &fs::read(&path)?)?;
            if source.excluded {
                continue;
            }
            source.collection_roots = roots.clone();
            source.shared_native = true;
            sources.push(source);
            changed = true;
        }
    }
    Ok(changed)
}

pub(super) fn create(
    settings: &Settings,
    frontend: &Frontend,
    entries: &[Entry],
) -> Result<(BTreeMap<PathBuf, Vec<u8>>, Vec<String>)> {
    let mut sources = vec![];
    let mut paths = vec![];
    let mut messages = vec![];
    for entry in entries {
        if entry.relative.extension().is_none_or(|ext| ext != "odin") {
            continue;
        }
        let bytes = match &entry.contents {
            Contents::File(bytes) | Contents::SourceLink(_, bytes) => bytes,
            _ => continue,
        };
        match parse(frontend, &settings.source.join(&entry.relative), bytes) {
            Ok(source) => { paths.push(entry.relative.clone()); sources.push(source); }
            Err(_) => messages.push(format!("Review {}: archived/inactive source could not be parsed and was copied with its original contents", entry.relative.display())),
        }
        if matches!(entry.contents, Contents::SourceLink(..)) {
            messages.push(format!(
                "Materialized source symlink {} as an independent source file",
                entry.relative.display()
            ));
        }
    }
    let owned_count = sources.len();
    let odin_root = sources
        .first()
        .and_then(|source| source.info["odin_root"].as_str())
        .ok_or_else(|| Error::new("parser omitted Odin root"))?.to_string();
    let mut roots = BTreeMap::new();
    for name in ["base", "core", "vendor"] {
        roots.insert(name.into(), Path::new(&odin_root).join(name));
    }
    for flag in super::rebased_flags(settings, &settings.source)? {
        if let Some(value) = flag.strip_prefix("-collection:") {
            let (name, path) = value.split_once('=').unwrap();
            roots.insert(name.into(), PathBuf::from(path));
        }
    }
    for source in &mut sources {
        source.collection_roots = roots.clone();
    }
    let flags = super::rebased_flags(settings, &settings.source)?;
    // Resolve only packages needed to identify a collision's declaration/type.
    let preserved = reflected_spellings(&sources);
    let (mut decisions, names) = loop {
        let mut occupied = HashSet::new();
        for source in &sources {
            occupied.extend(
                crate::diagnosticcleanup::tokens(&source.raw)
                    .into_iter()
                    .filter(|token| ["fn_odin", "set_odin", "or_panic_odin", "Integer_odin", "Rational_odin"]
                        .iter().any(|prefix| token.starts_with(prefix)))
                    .map(str::to_string),
            );
        }
        let mut names = BTreeMap::new();
        for name in NAMES {
            if preserved.contains(name) { continue; }
            let mut replacement = format!("{name}_odin");
            let mut index = 2;
            while occupied.contains(&replacement) {
                replacement = format!("{name}_odin_{index}");
                index += 1;
            }
            occupied.insert(replacement.clone());
            names.insert(name, replacement);
        }
        let mut resolver = Resolver::with_flags(&sources, &flags);
        resolver.track_import_demands(true);
        let mut decisions = vec![];
        for (file, source) in sources.iter().enumerate().take(owned_count) {
            let mut edits = vec![];
            for span in identifier_spans(source) {
                if source.node(NodeId(0)).spans.get("package") == Some(&span) {
                    continue;
                }
                let name = source.slice(span);
                if !NAMES.contains(&name)
                    || source.at(span, NodeId(0)).is_some_and(|node| metadata_key(source, node))
                {
                    continue;
                }
                if let Some(replacement) = names.get(name) {
                    if rename_identifier(&mut resolver, file, span, &settings.source)? {
                        edits.push((span, replacement.clone()));
                        continue;
                    }
                }
                if ambiguous_native_operand(source, span) {
                    edits.push((span, format!("({name})")));
                }
            }
            decisions.push(edits);
        }
        let missing = resolver.demanded_packages();
        drop(resolver);
        if !load_demands(&mut sources, frontend, &roots, missing)? {
            break (decisions, names);
        }
    };
    resource_edits(
        &sources,
        owned_count,
        &flags,
        settings,
        entries,
        &mut decisions,
        &mut messages,
    )?;
    let mut output = BTreeMap::new();
    let mut counts = BTreeMap::<String, usize>::new();
    for (file, mut edits) in decisions.into_iter().enumerate() {
        let source = &sources[file];
        for node in &source.nodes {
            if node.kind == "Import" {
                let path = unquote(&node.op)?;
                let alias = path.rsplit(['/', ':']).next().unwrap_or("");
                let mut replacement_alias = None;
                let native_sdk = native_sdk_import(source, &path);
                let target = if native_sdk { None } else if let Some((collection, suffix)) = path.split_once(':') {
                    source
                        .collection_roots
                        .get(collection)
                        .map(|root| root.join(suffix))
                } else {
                    None
                };
                if let Some(target) = target {
                    let lexical = super::commands::lexical(&target);
                    let target = if lexical.starts_with(&settings.source) {
                        lexical
                    } else {
                        crate::project::canonicalize(&target).unwrap_or(lexical)
                    };
                    if target.starts_with(&settings.source) {
                        let relative = super::relative_link(source.path.parent().unwrap(), &target);
                        let relative = relative.to_string_lossy();
                        if let Some(span) = node.spans.get("path") {
                            edits.push((*span, serde_json::to_string(relative.as_ref())?));
                        }
                        if relative.rsplit('/').next() != Some(alias) {
                            replacement_alias = Some(alias);
                        }
                    }
                }
                if source.names.get(node.name).is_empty() {
                    let replacement = names.get(alias).map(String::as_str).or(replacement_alias);
                    if let (Some(replacement), Some(span)) = (replacement, node.spans.get("path")) {
                        edits.push((Span::new(span.start, span.start), format!("{replacement} ")));
                    }
                }
            }
        }
        for (span, replacement) in &edits {
            if span.len() > 0 {
                let description = if replacement.starts_with('(') {
                    format!("Disambiguated '{}' with parentheses", source.slice(*span))
                } else {
                    format!("{} -> {replacement}", source.slice(*span))
                };
                *counts.entry(description).or_default() += 1;
            }
        }
        compatibility_edits(source, &names, &mut edits, &mut messages)?;
        edits.sort_by_key(|(span, _)| (span.start, span.end));
        edits.dedup();
        let mut result = Vec::new();
        let mut cursor = 0;
        for (span, replacement) in edits {
            if span.start < cursor {
                return Err(Error::new(format!(
                    "overlapping migration edits in {}",
                    source.path.display()
                )));
            }
            result.extend_from_slice(&source.raw.as_bytes()[cursor..span.start]);
            result.extend_from_slice(replacement.as_bytes());
            cursor = span.end;
        }
        result.extend_from_slice(&source.raw.as_bytes()[cursor..]);
        output.insert(paths[file].clone(), result);
    }
    for (rename, count) in counts {
        messages.push(format!("{rename} ({count} occurrences)"));
    }
    for flag in &settings.flags {
        if crate::cli::native_source_check(
            flag.trim_start_matches('-')
                .split(':')
                .next()
                .unwrap_or_default(),
        )
        {
            messages.push(format!("Native check {flag} is preserved through original-source validation in Thor."));
        }
    }
    Ok((output, messages))
}

fn compatibility_edits(
    source: &Source,
    renames: &BTreeMap<&str, String>,
    edits: &mut Vec<Edit>,
    messages: &mut Vec<String>,
) -> Result<()> {
    if let Some(name) = source.node(NodeId(0)).spans.get("package").filter(|_| !source.has_odin_bounds_marker()) {
        edits.push((Span::new(name.end, name.end), format!(" {}", Source::ODIN_BOUNDS_MARKER)));
    }
    for span in source.info["migration_file_tags"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Span::from_wire)
    {
        if source.slice(span).starts_with("#+vet") {
            let start = span.start;
            edits.push((Span::new(start, start), "// thor-migrate ".into()));
            messages.push(format!(
                "{}: preserved native vet tag in a migration comment for translated source checks",
                source.path.display()
            ));
        }
    }
    for (index, node) in source.nodes.iter().enumerate() {
        let id = NodeId(index);
        if node.kind == "Declaration" && source.group(id, "names").iter()
            .any(|name| renames.contains_key(source.text(*name))) {
            preserve_link_names(source, id, edits, messages)?;
        }
    }
    Ok(())
}

fn resource_parts(
    resolver: &mut Resolver<'_>,
    site: Ref,
    owned: usize,
    seen: &mut HashSet<Ref>,
) -> Vec<Ref> {
    if !seen.insert(site) {
        return vec![];
    }
    let source = &resolver.sources[site.file];
    let node = source.node(site.node);
    if node.kind == "Literal" {
        return vec![site];
    }
    if node.kind == "Paren" {
        return source
            .role(site.node, "expr")
            .map(|node| resource_parts(resolver, Ref { node, ..site }, owned, seen))
            .unwrap_or_default();
    }
    if node.kind == "Binary" && node.op == "+" {
        return ["left", "right"]
            .into_iter()
            .filter_map(|role| source.role(site.node, role))
            .flat_map(|node| {
                resource_parts(resolver, Ref { node, ..site }, owned, &mut seen.clone())
            })
            .collect();
    }
    if let Some(symbol) = resolver.symbol(site) {
        let symbol = resolver.symbols[symbol].clone();
        if symbol.reference.file >= owned {
            return vec![];
        }
        if let Some(node) = symbol.value {
            return resource_parts(
                resolver,
                Ref {
                    file: symbol.reference.file,
                    node,
                },
                owned,
                seen,
            );
        }
        if symbol.parameter {
            let owner = &resolver.sources[symbol.reference.file];
            let signature = owner
                .ancestors(symbol.reference.node)
                .find(|node| owner.node(*node).kind == "ProcedureType");
            if let Some(node) = signature {
                let signature = Ref {
                    file: symbol.reference.file,
                    node,
                };
                let parameters = resolver.fields(signature, "params");
                if let Some(index) = parameters.iter().position(|(name, _)| {
                    name.trim_start_matches('$') == symbol.name.trim_start_matches('$')
                }) {
                    let calls = resolver
                        .sources
                        .iter()
                        .enumerate()
                        .take(owned)
                        .flat_map(|(file, source)| {
                            source
                                .nodes
                                .iter()
                                .enumerate()
                                .filter(|(_, node)| node.kind == "Call")
                                .map(move |(node, _)| Ref {
                                    file,
                                    node: NodeId(node),
                                })
                        })
                        .collect::<Vec<_>>();
                    let mut result = vec![];
                    for call in calls {
                        if resolver.call_signature(call) != Some(signature) {
                            continue;
                        }
                        let called = &resolver.sources[call.file];
                        let arguments = called.group(call.node, "args");
                        let argument = arguments
                            .iter()
                            .find(|arg| {
                                called.node(**arg).kind == "FieldValue"
                                    && called
                                        .role(**arg, "field")
                                        .is_some_and(|name| called.text(name) == symbol.name)
                            })
                            .copied()
                            .or_else(|| arguments.get(index).copied());
                        if let Some(argument) = argument {
                            let value = called.role(argument, "value").unwrap_or(argument);
                            result.extend(resource_parts(
                                resolver,
                                Ref {
                                    file: call.file,
                                    node: value,
                                },
                                owned,
                                &mut seen.clone(),
                            ));
                        }
                    }
                    return result;
                }
            }
        }
    }
    vec![]
}

fn lexical_path(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
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

fn resource_text(resolver: &Resolver<'_>, site: Ref, seen: &mut HashSet<Ref>) -> Option<String> {
    if !seen.insert(site) {
        return None;
    }
    if let Some(crate::semantics::Constant::Text(text)) =
        resolver.constant_value(site, &BTreeMap::new(), None)
    {
        return Some(text);
    }
    let source = &resolver.sources[site.file];
    let node = source.node(site.node);
    if node.kind == "Directive" && source.text(site.node) == "#directory" {
        return Some(format!("{}/", source.path.parent()?.display()));
    }
    if node.kind == "Literal" {
        return unquote(source.text(site.node)).ok();
    }
    if let Some(symbol) = resolver.symbol(site) {
        let symbol = &resolver.symbols[symbol];
        if let Some(node) = symbol.value {
            return resource_text(
                resolver,
                Ref {
                    file: symbol.reference.file,
                    node,
                },
                seen,
            );
        }
    }
    if node.kind == "Paren" {
        return resource_text(
            resolver,
            Ref {
                node: source.role(site.node, "expr")?,
                ..site
            },
            seen,
        );
    }
    if node.kind == "Binary" && node.op == "+" {
        let left = resource_text(
            resolver,
            Ref {
                node: source.role(site.node, "left")?,
                ..site
            },
            &mut seen.clone(),
        )?;
        let right = resource_text(
            resolver,
            Ref {
                node: source.role(site.node, "right")?,
                ..site
            },
            &mut seen.clone(),
        )?;
        return Some(left + &right);
    }
    None
}

fn resource_edits(
    sources: &[Source],
    owned: usize,
    flags: &[String],
    settings: &Settings,
    entries: &[Entry],
    edits: &mut [Vec<Edit>],
    messages: &mut Vec<String>,
) -> Result<()> {
    let mut resolver = Resolver::with_flags(sources, flags);
    for (file, source) in sources.iter().enumerate().take(owned) {
        for (index, node) in source.nodes.iter().enumerate() {
            let id = NodeId(index);
            if node.kind != "Call"
                || !source.role(id, "callee").is_some_and(|callee| {
                    matches!(source.text(callee), "#load" | "#load_hash" | "#exists")
                })
            {
                continue;
            }
            let Some(argument) = source.group(id, "args").first() else {
                continue;
            };
            for part in resource_parts(
                &mut resolver,
                Ref {
                    file,
                    node: *argument,
                },
                owned,
                &mut HashSet::new(),
            ) {
                let origin = &sources[part.file];
                let Ok(literal) = unquote(origin.text(part.node)) else {
                    continue;
                };
                if !literal.ends_with(".odin") {
                    continue;
                }
                let mut expression = part.node;
                while let Some(parent) = origin.node(expression).parent.filter(|parent| {
                    origin.node(*parent).kind == "Paren"
                        || origin.node(*parent).kind == "Binary" && origin.node(*parent).op == "+"
                }) {
                    expression = parent;
                }
                let expression = Ref {
                    node: expression,
                    ..part
                };
                if resource_parts(&mut resolver, expression, owned, &mut HashSet::new()).last()
                    != Some(&part)
                {
                    continue;
                }
                let Some(path) = resource_text(&resolver, expression, &mut HashSet::new()) else {
                    continue;
                };
                if !path.ends_with(".odin") {
                    continue;
                }
                let resolved = lexical_path(&source.path.parent().unwrap().join(&path));
                let entry = resolved
                    .strip_prefix(&settings.source)
                    .ok()
                    .and_then(|relative| entries.iter().find(|entry| entry.relative == relative));
                let Some(entry) = entry else {
                    continue;
                };
                let (span, value) = if Path::new(&path).is_absolute() {
                    let relative = super::relative_link(
                        origin
                            .path
                            .parent()
                            .unwrap()
                            .strip_prefix(&settings.source)
                            .unwrap(),
                        &super::converted(&entry.relative),
                    );
                    (
                        origin.node(expression.node).span,
                        format!(
                            "(#directory + {})",
                            serde_json::to_string(&relative.to_string_lossy())?
                        ),
                    )
                } else {
                    (
                        origin.node(part.node).span,
                        serde_json::to_string(&format!("{}.thor", &literal[..literal.len() - 5]))?,
                    )
                };
                edits[part.file].retain(|(edit, _)| !span.contains(*edit));
                edits[part.file].push((span, value));
                messages.push(format!(
                    "Updated source resource path {path} in {}",
                    origin.path.display()
                ));
            }
        }
    }
    Ok(())
}

fn rewritten(source: &Source, span: Span, edits: &[Edit]) -> String {
    let mut bytes = source.slice(span).to_string();
    let mut selected = edits
        .iter()
        .filter(|(edit, _)| span.contains(*edit))
        .collect::<Vec<_>>();
    selected.sort_by_key(|(edit, _)| std::cmp::Reverse(edit.start));
    for (edit, text) in selected {
        bytes.replace_range(edit.start - span.start..edit.end - span.start, text);
    }
    bytes
}

fn preserve_link_names(
    source: &Source,
    declaration: NodeId,
    edits: &mut Vec<Edit>,
    messages: &mut Vec<String>,
) -> Result<()> {
    let names = source.group(declaration, "names");
    let mut owners = source.ancestors(declaration).collect::<Vec<_>>();
    owners.reverse();
    owners.push(declaration);
    let mut foreign = false;
    let mut exported = false;
    let mut prefix = "\"\"".to_string();
    let mut suffix = prefix.clone();
    let mut explicit = false;
    let mut local_prefix = None;
    let mut local_suffix = None;
    for owner in owners {
        foreign |= source.node(owner).kind == "ForeignBlock";
        for attribute in source.group(owner, "attributes") {
            for element in source.group(*attribute, "elems") {
                let key = source.role(*element, "field").unwrap_or(*element);
                let value = source.role(*element, "value");
                match source.text(key) {
                    "export" => exported = true,
                    "link_name" => explicit = true,
                    "link_prefix" => {
                        if let Some(value) = value {
                            prefix = rewritten(source, source.node(value).span, edits);
                            if owner == declaration {
                                local_prefix = Some(source.node(value).span);
                            }
                        }
                    }
                    "link_suffix" => {
                        if let Some(value) = value {
                            suffix = rewritten(source, source.node(value).span, edits);
                            if owner == declaration {
                                local_suffix = Some(source.node(value).span);
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }
    if explicit || !foreign && !exported {
        return Ok(());
    }
    for span in [local_prefix, local_suffix].into_iter().flatten() {
        edits.retain(|(edit, _)| !span.contains(*edit));
        edits.push((span, "\"\"".into()));
    }
    let annotation = |name: NodeId| {
        format!(
            "@({}{}link_name=({prefix})+{}+({suffix})) ",
            if local_prefix.is_none() {
                "link_prefix=\"\", "
            } else {
                ""
            },
            if local_suffix.is_none() {
                "link_suffix=\"\", "
            } else {
                ""
            },
            serde_json::to_string(source.text(name)).unwrap()
        )
    };
    let span = source.node(declaration).span;
    if names.len() == 1 {
        edits.push((Span::new(span.start, span.start), annotation(names[0])));
    } else {
        let values = source.group(declaration, "values");
        if !values.is_empty() && values.len() != names.len() {
            return Err(Error::at(source, span, "Cannot preserve native link names because this declaration has mismatched names and initializers."));
        }
        let before = rewritten(
            source,
            Span::new(span.start, source.node(names[0]).span.start),
            edits,
        );
        let after = rewritten(
            source,
            Span::new(
                source.node(*names.last().unwrap()).span.end,
                values
                    .first()
                    .map(|value| source.node(*value).span.start)
                    .unwrap_or(span.end),
            ),
            edits,
        );
        let tail = values
            .last()
            .map(|value| {
                rewritten(
                    source,
                    Span::new(source.node(*value).span.end, span.end),
                    edits,
                )
            })
            .unwrap_or_default();
        let gap = |left: NodeId, right: NodeId| {
            let text = rewritten(
                source,
                Span::new(source.node(left).span.end, source.node(right).span.start),
                edits,
            );
            let separator = crate::diagnosticcleanup::tokens(&text)
                .into_iter()
                .find(|token| *token == ",")
                .map(|token| token.as_ptr() as usize - text.as_ptr() as usize);
            let mut text = text;
            if let Some(separator) = separator {
                text.replace_range(separator..separator + 1, "");
            }
            text
        };
        let replacement = names
            .iter()
            .enumerate()
            .map(|(index, name)| {
                let value = values
                    .get(index)
                    .map(|value| rewritten(source, source.node(*value).span, edits))
                    .unwrap_or_default();
                let name_gap = index
                    .checked_sub(1)
                    .map(|previous| gap(names[previous], *name))
                    .unwrap_or_default();
                let value_gap = index
                    .checked_sub(1)
                    .filter(|_| !values.is_empty())
                    .map(|previous| gap(values[previous], values[index]))
                    .unwrap_or_default();
                format!(
                    "{}{}{}{}{}{}{}{}",
                    name_gap,
                    annotation(*name),
                    before,
                    rewritten(source, source.node(*name).span, edits),
                    after,
                    value_gap,
                    value,
                    tail
                )
            })
            .collect::<Vec<_>>()
            .join("; ");
        edits.retain(|(edit, _)| !span.contains(*edit));
        edits.push((span, replacement));
    }
    messages.push(format!(
        "{}: preserved native link names for renamed foreign/exported declarations",
        source.path.display()
    ));
    Ok(())
}

#[cfg(test)]
mod sdk_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn native_sdk_collection_stays_external_inside_the_migration_tree() {
        let directory = std::env::temp_dir().join(format!("thor-sdk-ownership-{}",std::process::id()));
        fs::create_dir_all(directory.join("sdk/core")).unwrap();
        fs::create_dir_all(directory.join("custom/core")).unwrap();
        let sdk = directory.join("sdk");
        let mut source = Source::from_frontend(sdk.join("main.odin"), String::new(), json!({
            "odin_root":sdk, "nodes":[{"kind":"File","start":0,"end":0,"parent":-1,"children":[]}]
        }), false).unwrap();
        source.collection_roots.insert("core".into(),sdk.join("core"));
        let native = native_sdk_import(&source,"core:example");
        source.collection_roots.insert("core".into(),directory.join("custom/core"));
        let overridden = native_sdk_import(&source,"core:example");
        source.collection_roots.insert("custom".into(),sdk.join("core"));
        let custom = native_sdk_import(&source,"custom:example");
        fs::remove_dir_all(directory).unwrap();
        assert!(native);
        assert!(!overridden);
        assert!(!custom);
    }
}
