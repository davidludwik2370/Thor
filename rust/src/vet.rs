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
        json!({"type":"warning","stage":"vet","code":if self.rule=="immutable"{"thor/prefer-immutable"}else{"thor/prefer-fn"},
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
        Type::Union(owner) => resolver
            .union_variants(*owner)
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
        | Type::Dynamic(_)
        | Type::Map { .. }
        | Type::Set(_) => true,
        Type::Nominal(_, base)
        | Type::Array { elem: base, .. }
        | Type::Fixed { elem: base, .. }
        | Type::Soa(base) => owned(base, resolver, seen),
        Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => resolver
            .record_fields(typ)
            .iter()
            .any(|(_, typ)| owned(typ, resolver, &mut seen.clone())),
        Type::Union(owner) => resolver
            .union_variants(*owner)
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
pub fn candidates(project: &Project, checks: &[String]) -> Vec<Fix> {
    let root = if project.options.entry.is_file() {
        project.options.entry.parent().unwrap()
    } else {
        &project.options.entry
    };
    let mut resolver = Resolver::with_flags(&project.sources, &project.options.flags);
    resolver.evaluated = project.evaluated.clone();
    resolver.prepare_contexts();
    crate::lower::prime_logical_constants(&mut resolver);
    let mut result = vec![];
    for (file, source) in project.sources.iter().enumerate() {
        if !source.thor || source.excluded || !source.path.starts_with(root) {
            continue;
        }
        let mut written = BTreeSet::new();
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
                }
            }
        }
        for (i, node) in source.nodes.iter().enumerate() {
            let id = NodeId(i);
            let at = Ref { file, node: id };
            if node.kind != "Declaration"
                || !source.group(id, "attributes").is_empty()
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
                if let Some(span) = node
                    .spans
                    .get("separator")
                    .filter(|span| source.slice(**span) == "=")
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
            // The existing checker rejects direct I/O and transitive effects;
            // full native checking below remains authoritative for every fix.
            let purity = crate::purity::Purity::analyze_from(&mut resolver, &[procedure]);
            if purity.faults.values().any(|faults| !faults.is_empty()) || !purity.gaps.is_empty() {
                continue;
            }
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
struct Validator<'a> {
    project: &'a Project,
    directory: &'a Path,
    compiler: PathBuf,
}
impl Validator<'_> {
    fn check(&self, fixes: &[Fix]) -> Result<bool> {
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
            .arg("check")
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
        command.args(self.project.options.flags.iter().filter(|flag| {
            !flag.starts_with("-out:")
                && !flag.starts_with("-build-mode:")
                && ![
                    "-warnings-as-errors",
                    "-ignore-warnings",
                    "-keep-executable",
                ]
                .contains(&flag.as_str())
        }));
        if self.project.frontend.test {
            command.arg("-build-mode:test");
        }
        let result = command.output()?;
        if !result.status.success() {
            let packet: Value = serde_json::from_slice(&result.stderr)
                .map_err(|_| Error::new("Thor vet could not validate its proposed corrections."))?;
            if let Some(failure) = packet["errors"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|error| error["stage"] == "toolchain")
            {
                return Err(Error::new(format!(
                    "Thor vet validation failed in the native toolchain. {}",
                    failure["msgs"][0].as_str().unwrap_or_default()
                )));
            }
        }
        Ok(result.status.success())
    }
    fn select(&self, proposed: &[Fix], accepted: &mut Vec<Fix>) -> Result<()> {
        if proposed.is_empty() {
            return Ok(());
        }
        let mut combined = accepted.clone();
        combined.extend_from_slice(proposed);
        if self.check(&combined)? {
            accepted.extend_from_slice(proposed);
        } else if proposed.len() > 1 {
            let middle = proposed.len() / 2;
            self.select(&proposed[..middle], accepted)?;
            self.select(&proposed[middle..], accepted)?;
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
) -> Result<Vec<Value>> {
    let proposed = candidates(project, checks);
    let mut accepted = vec![];
    if !proposed.is_empty() {
        let scratch = Scratch::new()?;
        Validator {
            project,
            directory: &scratch.0,
            compiler: std::env::current_exe()?,
        }
        .select(&proposed, &mut accepted)?;
    }
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
            apply(&accepted, &project.sources)?;
            println!("Applied {} corrections.", accepted.len());
            return Ok(vec![]);
        }
    }
    Ok(accepted
        .iter()
        .map(|fix| fix.diagnostic(&project.sources))
        .collect())
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
