//! Opt-in convention findings and compiler-validated source edits.
use crate::{
    ast::{NodeId, Source, Span},
    diagnostic::{Error, Result},
    project::Project,
    semantics::{Ref, Resolver, Type},
};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
    process::Command,
};
#[path = "vet_unused.rs"]
mod unused;

pub const CHECKS: &[&str] = &["immutable", "pure", "unused", "semicolon"];
#[derive(Clone, Debug)]
pub struct Fix {
    file: usize,
    span: Span,
    replacement: String,
    rule: &'static str,
    names: String,
    highlight: Span,
}
fn position(source: &Source, span: Span) -> Value {
    let (line, column) = source.position(span.start);
    let (end_line, end_column) = source.position(span.end);
    json!({"file":source.path,"offset":span.start,"end_offset":span.end,"line":line,"column":column,"end_line":end_line,"end_column":end_column})
}
impl Fix {
    fn message(&self) -> String {
        if self.rule == "immutable" {
            format!("Declare '{}' immutable with ':>'.", self.names)
        } else if self.rule == "semicolon" {
            "Unnecessary semicolon.".into()
        } else if self.rule == "iterator" {
            format!("'{}' declared but not used", self.names)
        } else if self.rule == "import" {
            format!("Unused import '{}'.", self.names)
        } else {
            format!("Procedure '{}' can be declared with 'fn'.", self.names)
        }
    }
    fn diagnostic(&self, sources: &[Source]) -> Value {
        let source = &sources[self.file];
        let hash = source
            .raw
            .as_bytes()
            .iter()
            .fold(14695981039346656037u64, |h, b| {
                (h ^ u64::from(*b)).wrapping_mul(1099511628211)
            });
        json!({"type":"warning","stage":"vet","code":match self.rule {"immutable"=>"thor/prefer-immutable", "semicolon"=>"thor/unnecessary-semicolon", "import"=>"thor/unused-import", "iterator"=>"thor/unused", _=>"thor/prefer-fn"},
            "pos":position(source,self.highlight),"msgs":[self.message()],
            "fix":{"pos":position(source,self.span),"text":self.replacement,"snapshot":format!("{hash:016x}")}})
    }
}
fn binding(resolver: &Resolver<'_>, mut at: Ref) -> Option<Ref> {
    let source = &resolver.sources[at.file];
    while matches!(
        source.node(at.node).kind.as_str(),
        "Selector" | "Index" | "Slice" | "Deref" | "Paren"
    ) {
        at.node = source.role(at.node, "expr")?;
    }
    if source.node(at.node).kind != "Ident" {
        return None;
    }
    resolver
        .symbol(at)
        .map(|symbol| resolver.symbols[symbol].reference)
}
fn possible(typ: &Type, resolver: &mut Resolver<'_>, seen: &mut HashSet<Type>) -> bool {
    if !seen.insert(typ.clone()) {
        return true;
    }
    match typ {
        Type::Pointer(_)
        | Type::Dynamic(_)
        | Type::Fixed { .. }
        | Type::Map { .. }
        | Type::Set(_)
        | Type::Procedure(_) => false,
        Type::Scalar { name, .. } | Type::String { name, .. }
            if [
                "rawptr",
                "any",
                "typeid",
                "cstring",
                "cstring16",
                "string16",
            ]
            .contains(&name.as_str()) =>
        {
            false
        }
        Type::Nominal(_, base)
        | Type::Slice(base)
        | Type::Array { elem: base, .. }
        | Type::Soa(base) => possible(base, resolver, seen),
        Type::Struct(owner) | Type::Record(owner, _) => {
            let source = &resolver.sources[owner.file];
            if source.node(owner.node).op == "raw_union" {
                return false;
            }
            resolver
                .record_fields(typ)
                .iter()
                .all(|(_, typ)| possible(typ, resolver, &mut seen.clone()))
        }
        Type::Anonymous(fields) => fields
            .iter()
            .all(|(_, typ)| possible(typ, resolver, &mut seen.clone())),
        Type::Union(..) => resolver
            .union_variants(typ)
            .iter()
            .all(|typ| possible(typ, resolver, &mut seen.clone())),
        _ => true,
    }
}
fn owned(typ: &Type, resolver: &mut Resolver<'_>, seen: &mut HashSet<Type>) -> bool {
    if !seen.insert(typ.clone()) {
        return false;
    }
    match typ {
        Type::Unknown
        | Type::Generic
        | Type::BigInteger
        | Type::BigRational
        | Type::String { .. }
        | Type::Slice(_)
        | Type::Soa(_)
        | Type::Dynamic(_)
        | Type::Map { .. }
        | Type::Set(_) => true,
        Type::Nominal(_, base)
        | Type::Array { elem: base, .. }
        | Type::Fixed { elem: base, .. } => owned(base, resolver, seen),
        Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => resolver
            .record_fields(typ)
            .iter()
            .any(|(_, typ)| owned(typ, resolver, &mut seen.clone())),
        Type::Union(..) => resolver
            .union_variants(typ)
            .iter()
            .any(|typ| owned(typ, resolver, &mut seen.clone())),
        _ => false,
    }
}
fn field_types(resolver: &mut Resolver<'_>, signature: Ref, role: &str) -> Vec<Type> {
    let source = &resolver.sources[signature.file];
    let fields = source
        .role(signature.node, role)
        .map(|id| source.node(id).children.clone())
        .unwrap_or_default();
    fields
        .into_iter()
        .filter(|id| source.node(*id).kind == "Field")
        .map(|field| {
            if let Some(node) = source.role(field, "type") {
                resolver.specialized_type(
                    Ref {
                        file: signature.file,
                        node,
                    },
                    &Default::default(),
                )
            } else if let Some(node) = source.role(field, "default") {
                resolver.type_of(Ref {
                    file: signature.file,
                    node,
                })
            } else {
                Type::Unknown
            }
        })
        .collect()
}
fn reference_output(typ: &Type, resolver: &mut Resolver<'_>, seen: &mut HashSet<Type>) -> bool {
    if !seen.insert(typ.clone()) { return false; }
    match typ {
        Type::Nominal(_, base) => reference_output(base, resolver, seen),
        Type::Pointer(base) | Type::Slice(base) | Type::Dynamic(base) | Type::Soa(base)
        | Type::Set(base) | Type::Map { value: base, .. } =>
            owned(base, resolver, &mut HashSet::new())
                || !possible(base, resolver, &mut HashSet::new()),
        Type::Array { elem, .. } | Type::Fixed { elem, .. } => reference_output(elem, resolver, seen),
        Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => resolver.record_fields(typ)
            .iter().any(|(_, field)| reference_output(field, resolver, seen)),
        Type::Union(..) => resolver.union_variants(typ)
            .iter().any(|variant| reference_output(variant, resolver, seen)),
        Type::Unknown | Type::Generic => true,
        _ => false,
    }
}
// A correction lives in source shared by future targets and #config values.
// Current-target validation cannot establish every inactive implementation.
#[derive(Default)]
struct PortableBodies {
    known: std::collections::HashMap<(Ref, bool), bool>,
    contracts: std::collections::HashMap<Ref, bool>,
}
impl PortableBodies {
    fn check(&mut self, resolver: &mut Resolver<'_>, root: Ref, argument_contracts: bool) -> bool {
        if let Some(result) = self.known.get(&(root, argument_contracts)) { return *result; }
        let mut pending = vec![root];
        let mut seen = HashSet::new();
        let mut callers = std::collections::HashMap::<Ref, Vec<Ref>>::new();
        let mut rejected = HashSet::new();
        while let Some(procedure) = pending.pop() {
            if let Some(portable) = self.known.get(&(procedure, argument_contracts)) {
                if !portable { rejected.insert(procedure); }
                continue;
            }
            if !seen.insert(procedure) { continue; }
            crate::profile::count("vet.portability_body_scans", 1);
            let source = &resolver.sources[procedure.file];
            let target_specific = source.info["target_specific"] == true
                || source.info["file_tags"].as_array().into_iter().flatten()
                    .filter_map(Value::as_str).any(|tag| tag.starts_with("#+build"));
            if target_specific {
                rejected.insert(procedure);
                continue;
            }
            for node in source.descendants(procedure.node) {
                if matches!(source.node(node).kind.as_str(), "When" | "WhenExpr" | "CompileSwitch" | "CompileSwitchExpr") {
                    rejected.insert(procedure);
                    break;
                }
                if source.node(node).kind == "Call" {
                    if let Some(callee) = source.role(node, "callee") {
                        let callee = Ref { file: procedure.file, node: callee };
                        // Immutable corrections use the same reviewed argument
                        // contracts as ordinary checking. Purity still follows
                        // every call because a reader can have I/O effects.
                        if argument_contracts && self.contracted(resolver, callee) {
                            crate::profile::count("vet.contract_calls_skipped", 1);
                            continue;
                        }
                        for target in resolver.candidates(callee) {
                            callers.entry(target).or_default().push(procedure);
                            pending.push(target);
                        }
                    }
                }
            }
        }
        // Propagate through the entire discovered graph before caching any
        // successful result. A cycle can reach a nonportable implementation.
        let mut pending = rejected.iter().copied().collect::<Vec<_>>();
        while let Some(procedure) = pending.pop() {
            for caller in callers.get(&procedure).into_iter().flatten() {
                if rejected.insert(*caller) { pending.push(*caller); }
            }
        }
        for procedure in seen {
            self.known.insert((procedure, argument_contracts), !rejected.contains(&procedure));
        }
        self.known[&(root, argument_contracts)]
    }
    fn contracted(&mut self, resolver: &Resolver<'_>, callee: Ref) -> bool {
        *self.contracts.entry(callee).or_insert_with(|| {
            ["core:fmt", "thor:basic", "thor:graph", "core:slice"].iter().any(|path| {
                crate::evaluation::library_member(resolver, callee, path).is_some_and(|name|
                    crate::privileges::reviewed_library_contract(resolver, callee, path, &name))
            })
        })
    }
}

pub fn candidates(project: &Project, checks: &[String]) -> Vec<Fix> {
    if !checks.iter().any(|check| matches!(check.as_str(), "immutable" | "pure")) {
        return vec![];
    }
    let root = if project.options.entry.is_file() {
        project.options.entry.parent().unwrap()
    } else {
        &project.options.entry
    };
    let mut resolver = project.resolver();
    resolver.set_evaluations(project.evaluated.clone());
    resolver.prepare_contexts();
    crate::lower::prime_logical_constants(&mut resolver);
    let mut result = vec![];
    let mut portable = PortableBodies::default();
    for (file, source) in project.sources.iter().enumerate() {
        if !source.thor || source.excluded || !source.path.starts_with(root) {
            continue;
        }
        let mut written = BTreeSet::new();
        let mut mutated = BTreeSet::new();
        for (i, node) in source.nodes.iter().enumerate() {
            let id = NodeId(i);
            let targets = if node.kind == "Assignment" && node.op != "in" {
                source.group(id, "lhs").to_vec()
            } else if node.kind == "IncDec"
                || node.kind == "Unary" && node.op == "&"
                || node.kind == "Range"
                    && source
                        .group(id, "names")
                        .iter()
                        .any(|name| source.text(*name).starts_with('&'))
            {
                source.role(id, "expr").into_iter().collect()
            } else {
                vec![]
            };
            for node in targets {
                if let Some(declaration) = binding(&resolver, Ref { file, node }) {
                    written.insert(declaration);
                    if matches!(source.node(id).kind.as_str(), "Assignment" | "IncDec") {
                        mutated.insert(declaration);
                    }
                }
            }
        }
        for (i, node) in source.nodes.iter().enumerate() {
            let id = NodeId(i);
            let at = Ref { file, node: id };
            if node.kind != "Declaration"
            || !crate::unused_attributes::only_unused_attributes(source, id)
                || source.ancestors(id).any(|id| {
                    matches!(
                        source.node(id).kind.as_str(),
                        "When" | "CompileSwitch" | "CompileSwitchExpr"
                    )
                })
            {
                continue;
            }
            let names = source
                .group(id, "names")
                .iter()
                .filter(|name| source.text(**name) != "_")
                .copied()
                .collect::<Vec<_>>();
            if names.is_empty() {
                continue;
            }
            let label = names
                .iter()
                .map(|name| source.text(*name))
                .collect::<Vec<_>>()
                .join(", ");
            let highlight = Span::new(
                source.node(names[0]).span.start,
                source.node(*names.last().unwrap()).span.end,
            );
            let values = source.group(id, "values");
            if checks.iter().any(|check| check == "immutable")
                && node.mutable
                && !node.immutable
                && !values.is_empty()
                && !written.contains(&at)
                && source
                    .ancestors(id)
                    .any(|id| source.node(id).kind == "Procedure")
            {
                let owner = source.ancestors(id).find(|owner| source.node(*owner).kind == "Procedure").unwrap();
                let types = names.iter().map(|name| resolver.type_of(Ref {file,node:*name})).collect::<Vec<_>>();
                if types.iter().any(|typ| owned(typ, &mut resolver, &mut HashSet::new())
                    || !possible(typ, &mut resolver, &mut HashSet::new())) {
                    let owner = Ref {file,node:owner};
                    if !portable.check(&mut resolver, owner, true) { continue; }
                }
                // Returning a readonly alias changes an API used by callers
                // outside this entry point. Keep that ownership choice explicit.
                if types.iter().any(|typ| resolver.frozen_slice_storage(typ)) {
                    let signature = source.role(owner, "type").unwrap_or(owner);
                    let outputs = field_types(&mut resolver, Ref {file,node:signature}, "results");
                    if outputs.iter().any(|typ| owned(typ, &mut resolver, &mut HashSet::new())
                        || !possible(typ, &mut resolver, &mut HashSet::new())) { continue; }
                    // Output parameters can expose the same ownership contract
                    // as a returned alias to callers outside this entry point.
                    let inputs = field_types(&mut resolver, Ref {file,node:signature}, "params");
                    if inputs.iter().any(|typ| reference_output(typ, &mut resolver, &mut HashSet::new())) {
                        continue;
                    }
                }
                let generic_local = source.ancestors(id).filter(|owner| source.node(*owner).kind == "Procedure")
                    .any(|owner| !crate::specialization::parameters(Ref {file,node:owner}, &resolver).is_empty());
                // A check of this entry point cannot establish write access for
                // every future specialization of a shared generic body.
                let independent = !generic_local || names.iter().all(|name| {
                    let typ = resolver.type_of(Ref {file,node:*name});
                    !owned(&typ, &mut resolver, &mut HashSet::new())
                        && possible(&typ, &mut resolver, &mut HashSet::new())
                });
                if let Some(span) = node
                    .spans
                    .get("separator")
                    .filter(|span| independent && source.slice(**span) == "=")
                {
                    result.push(Fix {
                        file,
                        span: *span,
                        replacement: ">".into(),
                        rule: "immutable",
                        names: label.clone(),
                        highlight,
                    });
                }
            }
            if !checks.iter().any(|check| check == "pure")
                || values.len() != 1
                || names.len() != 1
                || source.text(names[0]) == "main"
            {
                continue;
            }
            let proc = values[0];
            let procedure = Ref { file, node: proc };
            if source.node(proc).kind != "Procedure"
                || source.node(proc).data["pure"] == true
                || source.role(proc, "body").is_none()
                || !crate::specialization::parameters(procedure, &resolver).is_empty()
            {
                continue;
            }
            let Some(signature) = source.role(proc, "type") else {
                continue;
            };
            let convention = source.node(signature).data["calling_convention"]
                .as_str()
                .unwrap_or_default();
            if !matches!(convention, "" | "odin") {
                continue;
            }
            let signature = Ref {
                file,
                node: signature,
            };
            let outputs = field_types(&mut resolver, signature, "results");
            if outputs
                .iter()
                .any(|typ| owned(typ, &mut resolver, &mut HashSet::new()))
            {
                continue;
            }
            let inputs = field_types(&mut resolver, signature, "params");
            if inputs
                .iter()
                .chain(&outputs)
                .any(|typ| !possible(typ, &mut resolver, &mut HashSet::new()))
            {
                continue;
            }
            let input_fields = source.role(signature.node, "params")
                .map(|params| source.node(params).children.as_slice()).unwrap_or_default();
            if input_fields.iter().filter(|field| source.node(**field).kind == "Field")
                .zip(&inputs).any(|(field,typ)| mutated.contains(&Ref {file,node:*field})
                    && owned(typ, &mut resolver, &mut HashSet::new())) {
                continue;
            }
            // The existing checker rejects direct I/O and transitive effects;
            // full native checking below remains authoritative for every fix.
            let purity = crate::purity::Purity::analyze_from(&mut resolver, &[procedure]);
            if purity.faults.values().any(|faults| !faults.is_empty()) || !purity.gaps.is_empty() {
                continue;
            }
            if !portable.check(&mut resolver, procedure, false) { continue; }
            let start = source.node(proc).span.start;
            if source.raw.get(start..start + 4) == Some("proc") {
                result.push(Fix {
                    file,
                    span: Span::new(start, start + 4),
                    replacement: "fn".into(),
                    rule: "pure",
                    names: label,
                    highlight,
                });
            }
        }
    }
    result.sort_by(|a, b| {
        (
            a.rule != "immutable",
            &project.sources[a.file].path,
            a.span.start,
        )
            .cmp(&(
                b.rule != "immutable",
                &project.sources[b.file].path,
                b.span.start,
            ))
    });
    result
}
fn revisions(fixes: &[Fix], sources: &[Source]) -> BTreeMap<PathBuf, Vec<u8>> {
    let mut groups = BTreeMap::<usize, Vec<&Fix>>::new();
    for fix in fixes {
        groups.entry(fix.file).or_default().push(fix);
    }
    groups
        .into_iter()
        .map(|(file, mut edits)| {
            let source = &sources[file];
            let mut raw = source.raw.as_bytes().to_vec();
            edits.sort_by_key(|fix| std::cmp::Reverse(fix.span.start));
            for fix in edits {
                raw.splice(
                    fix.span.start..fix.span.end,
                    fix.replacement.as_bytes().iter().copied(),
                );
            }
            (source.path.clone(), raw)
        })
        .collect()
}
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Result<Self> {
        for sequence in 0..100 {
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "thor-vet-{}-{stamp}-{sequence}",
                std::process::id()
            ));
            let mut builder = fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            match builder.create(&path) {
                Ok(()) => return Ok(Self(path)),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.into()),
            }
        }
        Err(Error::new("Could not create vet validation storage."))
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
enum Validation {
    Passed,
    Failed(Vec<Value>),
}
struct Validator<'a> {
    project: &'a Project,
    directory: &'a Path,
    compiler: PathBuf,
}
impl Validator<'_> {
    fn check(&self, fixes: &[Fix]) -> Result<Validation> {
        crate::profile::count("vet.validation_checks", 1);
        let mut overlays = self
            .project
            .options
            .overlays
            .iter()
            .map(|(path, text)| (path.clone(), text.as_bytes().to_vec()))
            .collect::<BTreeMap<_, _>>();
        overlays.extend(revisions(fixes, &self.project.sources));
        let mut replacements = BTreeMap::new();
        for (index, (path, raw)) in overlays.into_iter().enumerate() {
            let snapshot = self.directory.join(format!("source-{index}"));
            fs::write(&snapshot, raw)?;
            replacements.insert(path.to_string_lossy().into_owned(), snapshot);
        }
        let overlay = self.directory.join("overlay.json");
        fs::write(
            &overlay,
            serde_json::to_vec(&json!({"Replace":replacements}))?,
        )?;
        let mut command = Command::new(&self.compiler);
        command
            .env("THOR_ROOT", &self.project.options.root)
            .arg(if self.project.frontend.test { "build" } else { "check" })
            .arg(&self.project.options.entry)
            .arg("--odin")
            .arg(&self.project.options.odin)
            .arg("--overlay")
            .arg(overlay)
            .arg("--output-dir")
            .arg(self.directory.join("output"))
            .arg("--run-timeout")
            .arg(self.project.options.run_timeout.as_secs_f64().to_string())
            .arg("-json-errors");
        if let Some(cache) = self.project.options.cache.as_ref().or(self
            .project
            .frontend
            .cache_dir
            .as_ref())
        {
            command.arg("--cache-dir").arg(cache);
        }
        if self.project.options.no_cache {
            command.arg("--no-cache");
        }
        if self.project.options.odin_compatible {
            command.arg("--odin-compatible");
        }
        command.args(self.project.options.flags.iter().filter(|flag| {
            let name = flag.trim_start_matches('-').split(':').next().unwrap_or_default();
            let action = if self.project.frontend.test { "build" } else { "check" };
            let supported = crate::cli::catalog().native_flags.iter().find(|info| info.name == name)
                .is_none_or(|info| info.commands.iter().any(|command| command == action));
            !flag.starts_with("-out:")
                && supported
                && (crate::cli::helper_flag(flag)
                    || self.project.frontend.test && flag.as_str() == "-all-packages")
                && !flag.starts_with("-build-mode:")
                && flag.as_str() != "-keep-executable"
        }));
        if self.project.frontend.test {
            command.arg("-build-mode:test").arg(format!("-out:{}", self.directory.join("test-probe").display()));
        }
        let result = command.output()?;
        if !result.status.success() {
            let packet: Value = serde_json::from_slice(&result.stderr).map_err(|_| {
                Error::new(format!(
                    "Thor vet could not validate its proposed corrections (exit {}).\n{}{}",
                    result.status,
                    String::from_utf8_lossy(&result.stdout),
                    String::from_utf8_lossy(&result.stderr)
                ))
            })?;
            if let Some(failure) = packet["errors"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|error| error["stage"] == "toolchain"
                    && !crate::compiler_output::has_source_errors(&packet))
            {
                return Err(Error::new(format!(
                    "Thor vet validation failed in the native toolchain. {}",
                    failure["msgs"][0].as_str().unwrap_or_default()
                )));
            }
            return Ok(Validation::Failed(packet["errors"].as_array().cloned().unwrap_or_default()));
        }
        Ok(Validation::Passed)
    }
    fn blocked_immutable(&self, fix: &Fix, combined: &[Fix], errors: &[Value]) -> bool {
        if fix.rule != "immutable" { return false; }
        let source = &self.project.sources[fix.file];
        let delta = combined.iter().filter(|edit| edit.file == fix.file && edit.span.end <= fix.highlight.start)
            .map(|edit| edit.replacement.len() as isize - edit.span.len() as isize).sum::<isize>();
        let Some(start) = fix.highlight.start.checked_add_signed(delta) else { return false; };
        let Some(end) = fix.highlight.end.checked_add_signed(delta) else { return false; };
        errors.iter().filter(|error| error["type"] == "error")
            .flat_map(|error| error["related"].as_array().into_iter().flatten())
            .any(|related| related["message"] == "Read-only view declared here."
                && related["pos"]["file"].as_str().is_some_and(|path| Path::new(path) == source.path)
                && related["pos"]["offset"].as_u64().is_some_and(|offset| (start..end).contains(&(offset as usize))))
    }
    fn blocked_pure(&self, fix: &Fix, combined: &[Fix], errors: &[Value]) -> bool {
        if fix.rule != "pure" { return false; }
        let source = &self.project.sources[fix.file];
        let delta = combined.iter().filter(|edit| edit.file == fix.file && edit.span.end <= fix.span.start)
            .map(|edit| edit.replacement.len() as isize - edit.span.len() as isize).sum::<isize>();
        let Some(start) = fix.span.start.checked_add_signed(delta) else { return false; };
        let rooted = errors.iter().filter(|error| error["type"] == "error")
            .flat_map(|error| error["related"].as_array().into_iter().flatten())
            .any(|related| related["message"].as_str().is_some_and(|message|
                    message.starts_with("fn '") && message.contains("requires its entire call chain to be pure"))
                && related["pos"]["file"].as_str().is_some_and(|path| Path::new(path) == source.path)
                && related["pos"]["offset"].as_u64() == Some(start as u64));
        if rooted { return true; }
        let Some(procedure) = source.nodes.iter().find(|node| node.kind == "Procedure"
            && node.span.start == fix.span.start) else { return false; };
        let shifted = |offset: usize| offset.checked_add_signed(combined.iter()
            .filter(|edit| edit.file == fix.file && edit.span.end <= offset)
            .map(|edit| edit.replacement.len() as isize - edit.span.len() as isize).sum());
        let Some(end) = shifted(procedure.span.end) else { return false; };
        errors.iter().any(|error| error["type"] == "error"
            && error["msgs"].as_array().is_some_and(|messages| messages.iter()
                .any(|message| message.as_str().is_some_and(|message| message.starts_with("Pure "))))
            && error["pos"]["file"].as_str().is_some_and(|path| Path::new(path) == source.path)
            && error["pos"]["offset"].as_u64().is_some_and(|offset| {
                let offset = offset as usize;
                (start..end).contains(&offset) && !source.nodes.iter().any(|node|
                    node.kind == "Procedure" && node.span.start > procedure.span.start
                        && node.span.end <= procedure.span.end
                        && shifted(node.span.start).zip(shifted(node.span.end))
                            .is_some_and(|(start,end)| (start..end).contains(&offset)))
            }))
    }
    fn select(&self, proposed: &[Fix], accepted: &mut Vec<Fix>) -> Result<()> {
        if proposed.is_empty() {
            return Ok(());
        }
        let mut combined = accepted.clone();
        combined.extend_from_slice(proposed);
        match self.check(&combined)? {
            Validation::Passed => accepted.extend_from_slice(proposed),
            Validation::Failed(errors) => {
                // Readonly seeds and pure call-chain roots identify the exact
                // proposed declarations responsible for a failure. Reject those
                // declarations before retrying the remaining batch.
                let remaining = proposed.iter().filter(|fix| !self.blocked_immutable(fix, &combined, &errors)
                    && !self.blocked_pure(fix, &combined, &errors))
                    .cloned().collect::<Vec<_>>();
                if remaining.len() < proposed.len() {
                    crate::profile::count("vet.corrections_rejected", (proposed.len() - remaining.len()) as u64);
                    let readonly = proposed.iter().filter(|fix| fix.rule == "immutable").count()
                        - remaining.iter().filter(|fix| fix.rule == "immutable").count();
                    crate::profile::count("vet.readonly_corrections_rejected", readonly as u64);
                    crate::profile::count("vet.pure_corrections_rejected", (proposed.len() - remaining.len() - readonly) as u64);
                    self.select(&remaining, accepted)?;
                } else if proposed.len() > 1 {
                    let middle = proposed.len() / 2;
                    self.select(&proposed[..middle], accepted)?;
                    self.select(&proposed[middle..], accepted)?;
                }
            }
        }
        Ok(())
    }
}
struct Staged(Vec<PathBuf>);
impl Drop for Staged {
    fn drop(&mut self) {
        for path in &self.0 {
            let _ = fs::remove_file(path);
        }
    }
}
impl Staged {
    fn write(
        &mut self,
        parent: &Path,
        bytes: &[u8],
        permissions: fs::Permissions,
    ) -> Result<PathBuf> {
        for sequence in 0..100 {
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let path = parent.join(format!(
                ".thor-vet-{}-{stamp}-{sequence}",
                std::process::id()
            ));
            let file = fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(&path);
            match file {
                Ok(mut file) => {
                    self.0.push(path.clone());
                    file.set_permissions(permissions)?;
                    file.write_all(bytes)?;
                    return Ok(path);
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.into()),
            }
        }
        Err(Error::new("Could not stage a vet correction."))
    }
}
pub(crate) fn apply_source_edits(edits: &[(usize, Span, String)], sources: &[Source]) -> Result<()> {
    let fixes = edits.iter().map(|(file, span, replacement)| Fix {
        file: *file, span: *span, replacement: replacement.clone(),
        rule: "source-cleanup", names: String::new(), highlight: *span,
    }).collect::<Vec<_>>();
    apply(&fixes, sources)
}
fn apply(fixes: &[Fix], sources: &[Source]) -> Result<()> {
    apply_with(fixes, sources, |from, to| fs::rename(from, to))
}
fn apply_with<F>(fixes: &[Fix], sources: &[Source], mut replace: F) -> Result<()>
where
    F: FnMut(&Path, &Path) -> io::Result<()>,
{
    let updated = revisions(fixes, sources);
    let originals = fixes
        .iter()
        .map(|fix| {
            let s = &sources[fix.file];
            (s.path.clone(), s.raw.as_bytes().to_vec())
        })
        .collect::<BTreeMap<_, _>>();
    let mut temporary = Staged(vec![]);
    let mut staged = BTreeMap::new();
    let mut backups = BTreeMap::new();
    let mut applied = vec![];
    let result = (|| {
        for (path, raw) in &updated {
            if fs::read(path)? != originals[path] {
                return Err(Error::new(format!(
                    "Source changed during vetting at {}. Run thor vet again.",
                    path.display()
                )));
            }
            let mode = fs::metadata(path)?.permissions();
            let parent = path
                .parent()
                .ok_or_else(|| Error::new("Source has no parent directory."))?;
            staged.insert(path.clone(), temporary.write(parent, raw, mode.clone())?);
            backups.insert(
                path.clone(),
                temporary.write(parent, &originals[path], mode)?,
            );
        }
        for (path, file) in &staged {
            if fs::read(path)? != originals[path] {
                return Err(Error::new(format!(
                    "Source changed during vetting at {}. Run thor vet again.",
                    path.display()
                )));
            }
            replace(file, path)?;
            applied.push(path.clone());
        }
        Ok(())
    })();
    if result.is_err() {
        for path in applied.iter().rev() {
            if fs::read(path).ok().as_ref() == updated.get(path) {
                let _ = replace(&backups[path], path);
            }
        }
    }
    result
}
pub fn run(
    project: &Project,
    checks: &[String],
    apply_requested: bool,
    ask: bool,
    existing: &mut Vec<Value>,
) -> Result<Vec<Value>> {
    let usage = if checks
        .iter()
        .any(|check| matches!(check.as_str(), "unused" | "immutable"))
    {
        unused::analyze(project, checks.iter().any(|check| check == "unused"))
    } else {
        unused::Analysis::default()
    };
    let mut exempt_offsets = BTreeMap::<PathBuf, BTreeSet<usize>>::new();
    for at in usage.immutable_exemptions {
        let source = &project.sources[at.file];
        exempt_offsets
            .entry(source.path.clone())
            .or_default()
            .insert(source.node(at.node).span.start);
    }
    let proposed = usage.fixes.into_iter().chain(candidates(project, checks))
        .filter(|fix| {
            fix.rule != "immutable"
                || exempt_offsets
                    .get(&project.sources[fix.file].path)
                    .is_none_or(|offsets| {
                        offsets
                            .range(fix.highlight.start..fix.highlight.end)
                            .next()
                            .is_none()
                    })
        })
        .collect::<Vec<_>>();
    let mut findings = if checks.iter().any(|check| check == "unused") {
        usage.findings
    } else {
        vec![]
    };
    let unused = findings
        .iter()
        .map(|finding| {
            (
                finding["pos"]["file"].clone(),
                finding["pos"]["offset"].clone(),
                finding["msgs"].clone(),
            )
        })
        .collect::<Vec<_>>();
    existing.retain(|finding| {
        finding["type"] != "warning"
            || !unused.contains(&(
                finding["pos"]["file"].clone(),
                finding["pos"]["offset"].clone(),
                finding["msgs"].clone(),
            ))
    });
    let mut accepted = if checks.iter().any(|check| check == "semicolon") {
        crate::source_tools::semicolon_edits(project, true)?
            .into_iter()
            .map(|(file, span, replacement)| Fix {
                file, span, replacement, rule: "semicolon", names: String::new(), highlight: span,
            })
            .collect()
    } else {
        vec![]
    };
    let (covered_semicolons, independent): (Vec<_>, Vec<_>) = accepted.into_iter().partition(|fix|
        proposed.iter().any(|item| item.rule == "import" && item.file == fix.file && item.span.contains(fix.span)));
    accepted = independent;
    if !proposed.is_empty() {
        let scratch = Scratch::new()?;
        Validator {
            project,
            directory: &scratch.0,
            compiler: std::env::current_exe()?,
        }
        .select(&proposed, &mut accepted)?;
    }
    for fix in covered_semicolons {
        if !accepted.iter().any(|item| item.rule == "import" && item.file == fix.file && item.span.contains(fix.span)) {
            accepted.push(fix);
        }
    }
    let corrected_imports = accepted.iter().filter(|fix| fix.rule == "import")
        .map(|fix| (project.sources[fix.file].path.to_string_lossy().into_owned(), fix.highlight.start))
        .collect::<BTreeSet<_>>();
    findings.retain(|finding| finding["code"] != "thor/unused-import"
        || !corrected_imports.contains(&(finding["pos"]["file"].as_str().unwrap().into(),
            finding["pos"]["offset"].as_u64().unwrap() as usize)));
    if project.options.command == "vet" && (apply_requested || ask) && !accepted.is_empty() {
        for fix in &accepted {
            let source = &project.sources[fix.file];
            let (line, column) = source.position(fix.highlight.start);
            println!(
                "{}({line}:{column}): {}",
                source.path.display(),
                fix.message()
            );
        }
        let mut confirmed = apply_requested;
        if ask {
            print!("Apply these {} corrections? [y/N] ", accepted.len());
            io::stdout().flush()?;
            let mut answer = String::new();
            io::stdin().read_line(&mut answer)?;
            confirmed = matches!(answer.trim().to_lowercase().as_str(), "y" | "yes");
        }
        if confirmed {
            let corrected = accepted.iter().filter(|fix| fix.rule == "iterator")
                .map(|fix| (project.sources[fix.file].path.to_string_lossy().into_owned(), fix.highlight.start))
                .collect::<BTreeSet<_>>();
            findings.retain(|finding| finding["code"] != "thor/unused" || !corrected.contains(&(
                finding["pos"]["file"].as_str().unwrap().into(), finding["pos"]["offset"].as_u64().unwrap() as usize)));
            apply(&accepted, &project.sources)?;
            let updated = revisions(&accepted, &project.sources);
            for finding in &mut findings {
                let path = Path::new(finding["pos"]["file"].as_str().unwrap());
                let Some(raw) = updated.get(path) else {
                    continue;
                };
                let raw = std::str::from_utf8(raw).expect("vet edits preserve UTF-8");
                let fixes = accepted
                    .iter()
                    .filter(|fix| project.sources[fix.file].path == path)
                    .collect::<Vec<_>>();
                let pos = finding["pos"].as_object_mut().unwrap();
                for (key, line_key, column_key) in [
                    ("offset", "line", "column"),
                    ("end_offset", "end_line", "end_column"),
                ] {
                    let old = pos[key].as_u64().unwrap() as usize;
                    let delta = fixes
                        .iter()
                        .filter(|fix| fix.span.end <= old)
                        .map(|fix| fix.replacement.len() as isize - fix.span.len() as isize)
                        .sum::<isize>();
                    let offset = old
                        .checked_add_signed(delta)
                        .expect("edits preserve source positions");
                    let before = &raw[..offset];
                    let line = before.bytes().filter(|byte| *byte == b'\n').count() + 1;
                    let column = before.rsplit('\n').next().unwrap().encode_utf16().count() + 1;
                    pos.insert(key.into(), json!(offset));
                    pos.insert(line_key.into(), json!(line));
                    pos.insert(column_key.into(), json!(column));
                }
            }
            println!("Applied {} corrections.", accepted.len());
            return Ok(findings);
        }
    }
    // Enrich loop warnings in place so binding diagnostics retain source order.
    let iterator_fixes = accepted.iter().filter(|fix| fix.rule == "iterator")
        .map(|fix| ((project.sources[fix.file].path.to_string_lossy().into_owned(), fix.highlight.start), fix))
        .collect::<BTreeMap<(String, usize), _>>();
    for finding in &mut findings {
        if finding["code"] == "thor/unused" {
            if let Some(fix) = iterator_fixes.get(&(finding["pos"]["file"].as_str().unwrap().into(), finding["pos"]["offset"].as_u64().unwrap() as usize)) {
                *finding = fix.diagnostic(&project.sources);
            }
        }
    }
    findings.extend(accepted.iter().filter(|fix| fix.rule != "iterator").map(|fix| fix.diagnostic(&project.sources)));
    Ok(findings)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(path: PathBuf, raw: &str) -> Source {
        Source::from_frontend(
            path,
            raw.into(),
            json!({"nodes":[{"kind":"File","start":0,"end":raw.len(),"parent":-1}]}),
            true,
        )
        .unwrap()
    }
    fn fixes(sources: &[Source]) -> Vec<Fix> {
        sources
            .iter()
            .enumerate()
            .map(|(file, source)| {
                let start = source.raw.find(":=").unwrap() + 1;
                Fix {
                    file,
                    span: Span::new(start, start + 1),
                    replacement: ">".into(),
                    rule: "immutable",
                    names: "value".into(),
                    highlight: Span::new(start - 5, start),
                }
            })
            .collect()
    }
    #[test]
    fn source_updates_preserve_bytes_modes_and_cleanup() {
        let directory = Scratch::new().unwrap();
        let path = directory.0.join("main.thor");
        let raw = "// 😀\r\npackage main\r\nmain::proc(){value:=1;_=value}\r\n";
        fs::write(&path, raw).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o640)).unwrap();
        }
        let sources = vec![fixture(path.clone(), raw)];
        let changes = fixes(&sources);
        let result = apply(&changes, &sources);
        assert!(result.is_ok());
        let updated = fs::read_to_string(&path).unwrap();
        assert_eq!(updated, raw.replace(":=", ":>"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&path).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o640);
        }
        let files = fs::read_dir(&directory.0).unwrap().collect::<Vec<_>>();
        assert_eq!(files.len(), 1);
    }
    #[test]
    fn concurrent_changes_are_preserved_and_partial_replacements_roll_back() {
        let directory = Scratch::new().unwrap();
        let raw = "package main\nmain::proc(){value:=1;_=value}\n";
        let a = directory.0.join("a.thor");
        let b = directory.0.join("b.thor");
        fs::write(&a, raw).unwrap();
        fs::write(&b, raw).unwrap();
        let sources = vec![fixture(a.clone(), raw), fixture(b.clone(), raw)];
        let changes = fixes(&sources);
        let mut attempts = 0;
        let result = apply_with(&changes, &sources, |from, to| {
            attempts += 1;
            if attempts == 2 {
                Err(io::Error::other("replacement failure"))
            } else {
                fs::rename(from, to)
            }
        });
        assert!(result.is_err());
        let restored_a = fs::read_to_string(&a).unwrap();
        let restored_b = fs::read_to_string(&b).unwrap();
        assert_eq!(restored_a, raw);
        assert_eq!(restored_b, raw);
        fs::write(&b, "changed by another writer").unwrap();
        let concurrent = apply(&changes, &sources);
        assert!(concurrent.is_err());
        let preserved = fs::read_to_string(&b).unwrap();
        assert_eq!(preserved, "changed by another writer");
        let files = fs::read_dir(&directory.0).unwrap().collect::<Vec<_>>();
        assert_eq!(files.len(), 2);
    }
}
