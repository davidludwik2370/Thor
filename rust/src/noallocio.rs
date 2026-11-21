//! Source-checked effect adapters for SDK I/O. These never alter emitted code.
//! Opaque writers, mutable formatter registries, and changed SDK implementations
//! cannot acquire allocation freedom merely by having a familiar name.
use super::*;
use crate::semantics::Constant;
use std::{collections::BTreeMap, path::Path, sync::OnceLock};

#[derive(Default)]
pub(super) struct State {
    ready: bool,
    sdk_verified: std::rc::Rc<std::cell::Cell<bool>>,
    snapshot_checked: std::cell::Cell<bool>,
    inspected: std::collections::BTreeSet<std::path::PathBuf>,
}
#[derive(serde::Deserialize)]
struct Profiles {
    sdk: BTreeMap<String, String>,
    thor: BTreeMap<String, String>,
}
fn profiles() -> &'static Profiles {
    static DATA: OnceLock<Profiles> = OnceLock::new();
    DATA.get_or_init(|| {
        serde_json::from_str(include_str!("../data/noalloc-io-sdk.json"))
            .expect("valid reviewed I/O source manifest")
    })
}
fn identity(r: &Resolver<'_>, at: Ref) -> Option<(String, String)> {
    if r.sources[at.file].node(at.node).kind != "Procedure" {
        if let Some(i) = r.symbol(at) {
            let symbol = &r.symbols[i];
            if let Some(node) = symbol.value {
                if r.sources[symbol.reference.file].node(node).kind == "Procedure" {
                    return identity(
                        r,
                        Ref {
                            file: symbol.reference.file,
                            node,
                        },
                    );
                }
            }
        }
        for package in ["core:os", "core:io", "core:bufio", "core:fmt"] {
            if r.standard_library(at, package) {
                if let Some(name) = r.library_alias_member(at, package) {
                    return Some((package.into(), name));
                }
            }
        }
    }
    let target = if r.sources[at.file].node(at.node).kind == "Procedure" {
        Some(at)
    } else {
        r.symbol(at).and_then(|i| {
            let symbol = &r.symbols[i];
            symbol.value.map(|node| Ref {
                node,
                file: symbol.reference.file,
            })
        })
    }?;
    let s = &r.sources[target.file];
    let decl = s.node(target.node).parent?;
    let name = s.group(decl, "names").first().map(|node| s.text(*node))?;
    let runtime = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()?
        .join("runtime");
    if s.path == runtime.join("basic/basic.thor") && name == "print" {
        return Some(("thor:basic".into(), name.into()));
    }
    if let Some(root) = s.info["odin_root"].as_str() {
        for package in ["os", "io", "bufio", "fmt"] {
            if s.path.parent() == Some(Path::new(root).join("core").join(package).as_path()) {
                return Some((format!("core:{package}"), name.into()));
            }
        }
    }
    None
}
fn child(r: &Resolver<'_>, at: Ref, name: &str) -> Option<Ref> {
    r.sources[at.file]
        .role(at.node, name)
        .map(|node| Ref { node, ..at })
}
fn actuals(r: &Resolver<'_>, at: Ref) -> Vec<Ref> {
    r.sources[at.file]
        .group(at.node, "args")
        .iter()
        .map(|node| Ref {
            node: r.sources[at.file].role(*node, "value").unwrap_or(*node),
            ..at
        })
        .collect()
}
pub(super) fn value(
    flow: &Flow,
    r: &mut Resolver<'_>,
    at: Ref,
    bindings: &Substitutions,
    _depth: usize,
) -> Option<Fact> {
    let s = &r.sources[at.file];
    if matches!(s.node(at.node).kind.as_str(), "Ident" | "Selector") {
        let name = s
            .role(at.node, "field")
            .map(|node| s.text(node))
            .unwrap_or_else(|| s.text(at.node));
        if matches!(name, "stdin" | "stdout" | "stderr") {
            if let Some(name) = r.library_alias_member(at, "core:os") {
                if let Some(i) = ["stdin", "stdout", "stderr"]
                    .iter()
                    .position(|n| *n == name)
                    .filter(|_| r.standard_library(at, "core:os"))
                {
                    return Some(Fact::StandardFile(i as u8));
                }
            }
        }
    }
    if s.node(at.node).kind != "Call" {
        return None;
    }
    let callee = child(r, at, "callee")?;
    let (package, name) = identity(r, callee)?;
    let arg = actuals(r, at).first().copied()?;
    let fact = flow.fact_at(r, arg, bindings, _depth + 1);
    match (package.as_str(), name.as_str()) {
        ("core:os", "to_stream" | "to_reader" | "to_writer" | "to_flusher") => {
            Some(Fact::Stream(Box::new(fact)))
        }
        ("core:io", "to_reader" | "to_writer" | "to_flusher") => Some(fact),
        (
            "core:bufio",
            "writer_to_writer" | "writer_to_stream" | "reader_to_reader" | "reader_to_stream",
        ) => Some(Fact::Stream(Box::new(flow.deref(&fact)))),
        _ => None,
    }
}
fn source_proof(r: &Resolver<'_>, disk: bool) -> std::result::Result<(), String> {
    let data = profiles();
    let runtime = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("runtime");
    let root = r
        .sources
        .iter()
        .find_map(|s| s.info["odin_root"].as_str())
        .map(Path::new)
        .ok_or("cannot prove allocation freedom: missing native SDK root")?;
    // Hash the reviewed source closure without parsing unrelated SDK packages.
    // Also inspect additions: an added init procedure can change global hooks.
    let mut paths = vec![];
    let mut dirs = if disk {
        vec![root.join("base"), root.join("core")]
    } else {
        vec![]
    };
    let mut seen = std::collections::HashSet::new();
    while let Some(dir) = dirs.pop() {
        let canonical = std::fs::canonicalize(&dir)
            .map_err(|e| format!("cannot inspect I/O SDK {}: {e}", dir.display()))?;
        if !seen.insert(canonical) {
            continue;
        }
        let entries = std::fs::read_dir(&dir)
            .map_err(|e| format!("cannot inspect I/O SDK {}: {e}", dir.display()))?;
        for entry in entries {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            if path.is_dir() {
                dirs.push(path);
            } else if path.extension().is_some_and(|e| e == "odin")
                && !path
                    .file_stem()
                    .is_some_and(|n| n.to_string_lossy().ends_with("_test"))
            {
                paths.push(path);
            }
        }
    }
    if disk {
        paths.extend(data.thor.keys().map(|name| runtime.join(name)));
    } else {
        paths.extend(
            r.sources
                .iter()
                .filter(|s| {
                    s.path.starts_with(root.join("base"))
                        || s.path.starts_with(root.join("core"))
                        || matches!(
                            s.path.strip_prefix(&runtime).ok().and_then(|p| p.to_str()),
                            Some("basic/basic.thor" | "format/format.odin")
                        )
                })
                .filter(|s| {
                    !s.path
                        .file_stem()
                        .is_some_and(|n| n.to_string_lossy().ends_with("_test"))
                })
                .map(|s| s.path.clone()),
        );
    }
    let overlays = r
        .sources
        .iter()
        .map(|s| (&s.path, s.raw.as_bytes()))
        .collect::<std::collections::HashMap<_, _>>();
    for path in paths {
        let expected = if let Ok(relative) = path.strip_prefix(&runtime) {
            data.thor
                .get(&relative.to_string_lossy().replace('\\', "/"))
        } else {
            data.sdk.get(
                &path
                    .strip_prefix(root)
                    .unwrap()
                    .to_string_lossy()
                    .replace('\\', "/"),
            )
        };
        let owned;
        let bytes = if let Some(bytes) = overlays.get(&path) {
            *bytes
        } else {
            owned = std::fs::read(&path)
                .map_err(|e| format!("cannot inspect I/O SDK {}: {e}", path.display()))?;
            owned.as_slice()
        };
        if expected.is_none_or(|expected| *expected != crate::digest::sha256(bytes)) {
            return Err(format!("cannot prove allocation freedom: SDK I/O effect profile does not match {}; the changed implementation requires review",path.display()));
        }
    }
    // A collection override must never inherit the native summary.
    for source in r.sources.iter().filter(|s| !s.excluded) {
        for collection in ["core", "base"] {
            if source
                .collection_roots
                .get(collection)
                .is_some_and(|path| path != &root.join(collection))
            {
                return Err("cannot prove allocation freedom: SDK collection overrides require separate I/O review".into());
            }
        }
    }
    Ok(())
}

impl State {
    pub(super) fn verify_sdk(
        &self,
        r: &Resolver<'_>,
        overlays: &BTreeMap<std::path::PathBuf, String>,
    ) -> std::result::Result<(), String> {
        if self.snapshot_checked.get() {
            return Ok(());
        }
        let disk = !self.sdk_verified.get();
        source_proof(r, disk)?;
        let root = r
            .sources
            .iter()
            .find_map(|s| s.info["odin_root"].as_str())
            .ok_or("missing SDK root")?;
        for (path, raw) in overlays {
            if let Ok(relative) = path.strip_prefix(root) {
                let key = relative.to_string_lossy().replace('\\', "/");
                if profiles()
                    .sdk
                    .get(&key)
                    .is_none_or(|expected| *expected != crate::digest::sha256(raw.as_bytes()))
                {
                    return Err(format!("cannot prove allocation freedom: SDK effect profile does not match {}; the changed implementation requires review",path.display()));
                }
            }
        }
        if disk {
            self.sdk_verified.set(true);
            crate::profile::count("no_alloc.sdk_snapshots", 1);
        }
        self.snapshot_checked.set(true);
        Ok(())
    }
    pub(super) fn new(
        inspected: &std::collections::BTreeSet<std::path::PathBuf>,
        sdk_verified: std::rc::Rc<std::cell::Cell<bool>>,
    ) -> Self {
        Self {
            inspected: inspected.clone(),
            sdk_verified,
            ..Self::default()
        }
    }
    fn prepare(&mut self, r: &mut Resolver<'_>) -> std::result::Result<(), String> {
        if self.ready {
            return Ok(());
        }
        // Only opted-in, reachable I/O requests this closure. The normal parser
        // caches and target filtering are shared with other compiler analyses.
        for (file, s) in r.sources.iter().enumerate().filter(|(_, s)| !s.excluded) {
            for (i, n) in s
                .nodes
                .iter()
                .enumerate()
                .filter(|(_, n)| n.kind == "Import")
            {
                let at = Ref {
                    file,
                    node: NodeId(i),
                };
                if !r.active_definition(at) {
                    continue;
                }
                if let Ok(path) = crate::lower::unquote(&n.op) {
                    if !path.starts_with("core:") && !path.starts_with("base:") {
                        let _ = r.package_symbol(at, &path, "__thor_noalloc_io_graph");
                    }
                }
            }
        }
        if r.demanded_packages().iter().any(|path| {
            !self.inspected.contains(path)
                && !r
                    .sources
                    .iter()
                    .any(|s| s.path.parent() == Some(path.as_path()))
        }) {
            return Err(
                "cannot prove allocation freedom until I/O dependencies have been inspected".into(),
            );
        }
        let disk = !self.sdk_verified.get();
        source_proof(r, disk)?;
        if disk {
            self.sdk_verified.set(true);
            crate::profile::count("no_alloc.io_sdk_snapshots", 1);
        }
        let mut reachable = std::collections::HashSet::new();
        let mut queue = VecDeque::new();
        for (file, s) in r.sources.iter().enumerate().filter(|(_, s)| !s.excluded) {
            for (i, n) in s.nodes.iter().enumerate() {
                let at = Ref {
                    file,
                    node: NodeId(i),
                };
                if n.kind == "Procedure" {
                    let decl = n.parent.filter(|node| s.node(*node).kind == "Declaration");
                    let entry = decl.is_some_and(|decl| {
                        s.group(decl, "names")
                            .iter()
                            .any(|node| s.text(*node) == "main")
                            || r.attributes(Ref { node: decl, ..at })
                                .iter()
                                .any(|(name, _)| {
                                    matches!(name.as_str(), "init" | "test" | "export")
                                })
                            || s.group(decl, "attributes")
                                .iter()
                                .any(|attr| !super::elements(s, *attr).is_empty())
                    });
                    if entry || super::file_enabled(s) {
                        queue.push_back(at);
                    }
                } else if n.kind == "Declaration"
                    && n.mutable
                    && !s
                        .ancestors(at.node)
                        .any(|node| s.node(node).kind == "Procedure")
                {
                    queue.push_back(at);
                }
            }
        }
        while let Some(at) = queue.pop_front() {
            if reachable.contains(&at) {
                continue;
            }
            let s = &r.sources[at.file];
            if !r.active_definition(at) {
                continue;
            }
            if s.info["odin_root"].as_str().is_some_and(|root| {
                ["core/fmt", "core/os"]
                    .iter()
                    .any(|p| s.path.parent() == Some(Path::new(root).join(p).as_path()))
            }) {
                continue;
            }
            for node in std::iter::once(at.node).chain(s.descendants(at.node)) {
                let site = Ref { node, ..at };
                if !reachable.insert(site) {
                    continue;
                }
                let n = s.node(node);
                if n.kind == "Procedure" && node != at.node {
                    queue.push_back(site);
                }
                if matches!(n.kind.as_str(), "Ident" | "Selector")
                    && (n.kind != "Ident" || r.expression_reference(site))
                {
                    for target in r.candidates(site) {
                        if r.sources[target.file].node(target.node).kind == "Procedure" {
                            queue.push_back(target);
                        }
                    }
                }
            }
        }
        // Std streams and the formatter map are mutable public SDK state.
        // A write anywhere in the closed source graph invalidates the default
        // target proof, even if it occurs outside the annotated procedure.
        for (file, s) in r.sources.iter().enumerate().filter(|(_, s)| !s.excluded) {
            let sdk = s.info["odin_root"].as_str().map(Path::new);
            if sdk.is_some_and(|root| {
                s.path.parent() == Some(root.join("core/os").as_path())
                    || s.path.parent() == Some(root.join("core/fmt").as_path())
            }) {
                continue;
            }
            for (i, n) in s.nodes.iter().enumerate() {
                let at = Ref {
                    file,
                    node: NodeId(i),
                };
                if !reachable.contains(&at) || !r.active_definition(at) {
                    continue;
                }
                if n.kind == "Call" {
                    if let Some(callee) = child(r, at, "callee") {
                        if identity(r, callee).is_some_and(|(p, n)| {
                            p == "core:fmt"
                                && matches!(
                                    n.as_str(),
                                    "set_user_formatters" | "register_user_formatter"
                                )
                        }) {
                            return Err("cannot prove allocation freedom: the program configures a mutable user-formatter registry".into());
                        }
                    }
                }
                if matches!(n.kind.as_str(), "Ident" | "Selector")
                    && (n.kind != "Ident" || r.expression_reference(at))
                {
                    if identity(r, at).is_some_and(|(p, n)| {
                        p == "core:fmt"
                            && matches!(
                                n.as_str(),
                                "register_user_formatter" | "set_user_formatters"
                            )
                    }) {
                        return Err("cannot prove allocation freedom: the program configures a mutable user-formatter registry".into());
                    }
                    if r.library_alias_member(at, "core:fmt").as_deref() == Some("_user_formatters")
                    {
                        return Err("cannot prove allocation freedom: the user-formatter registry escapes SDK control".into());
                    }
                }
                if n.kind == "Cast" || n.kind == "AutoCast" || n.kind == "Unary" && n.op == "&" {
                    if child(r, at, "expr").is_some_and(|expr| protected_place(r, expr)) {
                        return Err("cannot prove allocation freedom: native file metadata escapes through an address or cast".into());
                    }
                }
                if n.kind == "Call" && child(r, at, "callee").is_some_and(|c| r.denotes_type(c)) {
                    if actuals(r, at).iter().any(|arg| protected_place(r, *arg)) {
                        return Err("cannot prove allocation freedom: native file metadata escapes through a conversion".into());
                    }
                }
                if n.kind == "Call" {
                    let native_file = actuals(r, at).iter().any(|arg| protected_place(r, *arg));
                    if native_file {
                        let callee = child(r, at, "callee").unwrap_or(at);
                        let reviewed = identity(r, callee).is_some_and(|(p, n)| {
                            p == "core:os"
                                && matches!(
                                    n.as_str(),
                                    "read"
                                        | "read_at"
                                        | "write"
                                        | "write_at"
                                        | "write_string"
                                        | "write_strings"
                                        | "flush"
                                        | "to_stream"
                                        | "to_reader"
                                        | "to_writer"
                                        | "to_flusher"
                                )
                                || p == "core:fmt"
                                    && matches!(
                                        n.as_str(),
                                        "fprint" | "fprintln" | "fprintf" | "fprintfln"
                                    )
                        });
                        let user = r.candidates(callee).iter().all(|target| {
                            let source = &r.sources[target.file];
                            source.role(target.node, "body").is_some()
                                && source.info["odin_root"]
                                    .as_str()
                                    .is_none_or(|root| !source.path.starts_with(root))
                        });
                        if !reviewed && (!user || r.candidates(callee).is_empty()) {
                            return Err("cannot prove allocation freedom: native file metadata escapes to an unverified call".into());
                        }
                    }
                }
                if n.kind == "Using" {
                    if s.group(at.node, "values")
                        .iter()
                        .any(|node| protected_place(r, Ref { node: *node, ..at }))
                    {
                        return Err("cannot prove allocation freedom: native file metadata escapes through a using projection".into());
                    }
                }
                if n.kind == "Assignment" {
                    for node in s.group(at.node, "lhs") {
                        let mut lhs = Ref { node: *node, ..at };
                        loop {
                            let typ = r.specialized_type(lhs, &Substitutions::new());
                            let source = &r.sources[lhs.file];
                            let unresolved = matches!(typ, Type::Unknown | Type::Generic)
                                && source.role(lhs.node, "field").is_some_and(|field| {
                                    matches!(source.text(field), "procedure" | "stream" | "impl")
                                });
                            if protected_file_type(r, &typ) || unresolved {
                                return Err("cannot prove allocation freedom: native file callback metadata can be changed through an alias".into());
                            }
                            if r.library_alias_member(lhs, "core:os").is_some_and(|n| {
                                matches!(n.as_str(), "stdin" | "stdout" | "stderr")
                            }) {
                                return Err("cannot prove allocation freedom: a standard stream can be replaced by the program".into());
                            }
                            if let Some(owner) = r
                                .symbol(lhs)
                                .and_then(|i| r.symbols[i].projection.as_ref().map(|p| p.owner))
                            {
                                let typ = r.specialized_type(owner, &Substitutions::new());
                                if protected_file_type(r, &typ) {
                                    return Err("cannot prove allocation freedom: native file callback metadata can be changed through a using alias".into());
                                }
                            }
                            let Some(base) = child(r, lhs, "expr") else {
                                break;
                            };
                            lhs = base;
                        }
                    }
                }
            }
        }
        self.ready = true;
        crate::profile::count("no_alloc.io_graphs", 1);
        Ok(())
    }
}

impl Check<'_, '_> {
    pub(super) fn io_call(&mut self, w: &Work, at: Ref, callee: Ref) -> Result<bool> {
        let target = self.flow.fact(self.r, callee, &w.bindings);
        let Some((package, name)) = identity(
            self.r,
            if let Fact::Function(target) = target {
                target
            } else {
                callee
            },
        ) else {
            return Ok(false);
        };
        let operation = match (package.as_str(), name.as_str()) {
            (
                "core:os",
                "read" | "read_at" | "write" | "write_at" | "write_string" | "write_strings"
                | "flush" | "to_stream" | "to_reader" | "to_writer" | "to_flusher",
            ) => true,
            (
                "core:io",
                "read" | "read_at" | "write" | "write_at" | "write_string" | "write_byte"
                | "write_rune" | "write_int" | "write_i64" | "flush" | "query" | "to_reader"
                | "to_writer" | "to_flusher",
            ) => true,
            (
                "core:bufio",
                "writer_init_with_buf"
                | "writer_to_stream"
                | "writer_to_writer"
                | "writer_write"
                | "writer_write_string"
                | "writer_write_byte"
                | "writer_write_rune"
                | "writer_flush"
                | "reader_init_with_buf"
                | "reader_to_stream"
                | "reader_read"
                | "reader_peek"
                | "reader_discard"
                | "reader_read_byte"
                | "reader_read_rune"
                | "reader_read_slice"
                | "reader_unread_byte"
                | "reader_unread_rune",
            ) => true,
            (
                "core:fmt",
                "print" | "println" | "printf" | "printfln" | "eprint" | "eprintln" | "eprintf"
                | "eprintfln" | "fprint" | "fprintln" | "fprintf" | "fprintfln" | "wprint"
                | "wprintln" | "wprintf" | "wprintfln",
            ) => true,
            ("thor:basic", "print") => true,
            _ => false,
        };
        if !operation {
            return Ok(false);
        }
        if let Err(reason) = self.io.prepare(self.r) {
            return Err(self.error(w, at, &reason));
        }
        let resolved = self.flow.fact(self.r, callee, &w.bindings);
        let mut plans = if !crate::parallelcompile::known_callback(self.r, callee) {
            if let Fact::Function(target) = resolved {
                self.r.call_plans_for_targets(at, &w.bindings, &[target])
            } else {
                self.r.call_plans(at, &w.bindings)
            }
        } else {
            self.r.call_plans(at, &w.bindings)
        };
        if plans.is_empty() {
            let targets =
                if let Fact::Function(target) = self.flow.fact(self.r, callee, &w.bindings) {
                    vec![target]
                } else {
                    self.r.candidates(callee)
                };
            if let Some(procedure) = targets.first().filter(|_| targets.len() == 1).copied() {
                let source = &self.r.sources[procedure.file];
                if source.node(procedure.node).kind == "Procedure" {
                    if let Some(node) = source.role(procedure.node, "type") {
                        let signature = Ref { node, ..procedure };
                        let arguments = self.r.sources[at.file]
                            .group(at.node, "args")
                            .iter()
                            .map(|node| Ref { node: *node, ..at })
                            .collect::<Vec<_>>();
                        let typ = self.r.specialized_type(callee, &w.bindings);
                        let declared = if let Type::Procedure(declared) = base_type(&typ) {
                            *declared
                        } else if crate::parallelcompile::known_callback(self.r, callee) {
                            signature
                        } else {
                            return Err(self.error(w,at,"cannot prove allocation freedom: callback argument layout is unresolved"));
                        };
                        if let Some(arguments) = self.r.arrange_arguments(declared, &arguments) {
                            plans.push(crate::semantics::CallPlan {
                                procedure,
                                signature,
                                arguments,
                                bindings: w.bindings.clone(),
                            });
                        }
                    }
                }
            }
        }
        let Some(plan) = plans.first().filter(|_| plans.len() == 1) else {
            return Err(self.error(
                w,
                at,
                "cannot prove allocation freedom: I/O signature or overload is unresolved",
            ));
        };
        let supplied = self.r.sources[at.file]
            .group(at.node, "args")
            .iter()
            .map(|node| {
                let source = &self.r.sources[at.file];
                Ref {
                    node: source.role(*node, "value").unwrap_or(*node),
                    ..at
                }
            })
            .collect::<HashSet<_>>();
        for arg in plan
            .arguments
            .iter()
            .flatten()
            .filter(|arg| !supplied.contains(arg))
        {
            self.walk(w, *arg)?;
        }
        // Normal call arrangement handles named arguments, defaults and packs.
        let mut facts = self.flow.arguments(self.r, plan);
        // Adapters need constants even when the source body delegates all work.
        for (i, refs) in plan.arguments.iter().enumerate() {
            if refs.len() == 1 && !matches!(facts[i], Fact::Pack(_)) {
                facts[i] = self.flow.fact(self.r, refs[0], &w.bindings);
            }
        }
        let names = crate::caller_metadata::params(
            &self.r.sources[plan.signature.file],
            plan.signature.node,
        )
        .iter()
        .map(|(node, _)| self.r.sources[plan.signature.file].text(*node).to_owned())
        .collect::<Vec<_>>();
        let by_name = |name: &str| {
            names
                .iter()
                .position(|n| n == name)
                .and_then(|i| facts.get(i))
                .cloned()
                .unwrap_or_default()
        };
        let get = |i: usize| facts.get(i).cloned().unwrap_or_default();
        if package == "core:os" {
            let file = get(0);
            if !name.starts_with("to_") {
                self.io_dispatch(
                    w,
                    at,
                    &file,
                    match name.as_str() {
                        "read" => "Read",
                        "read_at" => "Read_At",
                        "write_at" => "Write_At",
                        "flush" => "Flush",
                        _ => "Write",
                    },
                )?;
            }
        } else if package == "core:io" {
            if !name.starts_with("to_") {
                let mode = match name.as_str() {
                    "read" => "Read",
                    "read_at" => "Read_At",
                    "write_at" => "Write_At",
                    "flush" => "Flush",
                    "query" => "Query",
                    _ => "Write",
                };
                self.io_dispatch(w, at, &get(0), mode)?;
            }
        } else if package == "core:bufio" {
            if name == "writer_init_with_buf" || name == "reader_init_with_buf" {
                self.flow.through_pointer(
                    &get(0),
                    if name.starts_with("reader") {
                        Fact::BufferedReader(Box::new(get(1)))
                    } else {
                        Fact::Buffered(Box::new(get(1)))
                    },
                );
            } else if !name.starts_with("writer_to_") && !name.starts_with("reader_to_") {
                self.io_dispatch(
                    w,
                    at,
                    &self.flow.deref(&get(0)),
                    if name.starts_with("reader") {
                        "Read"
                    } else {
                        "Write"
                    },
                )?;
            }
        } else {
            let mut start = 0;
            let writer = if name.starts_with('f') || name.starts_with('w') {
                start = 1;
                get(0)
            } else {
                Fact::StandardFile(if name.starts_with('e') { 2 } else { 1 })
            };
            self.io_dispatch(w, at, &writer, "Write")?;
            if name.contains("printf") || name.ends_with("printfln") {
                start += 1;
            }
            if package == "thor:basic" {
                let Some(Fact::Pack(values)) = facts.get(start) else {
                    return Err(self.error(
                        w,
                        at,
                        "cannot prove allocation freedom: unknown print argument pack",
                    ));
                };
                if values.iter().any(|value| !print_value_known(value)) {
                    return Err(self.error(w,at,"cannot prove allocation freedom: basic.print may receive an owned logical number through any"));
                }
            }
            if !matches!(by_name("flush"), Fact::Constant(Constant::Boolean(false))) {
                self.io_dispatch(w, at, &writer, "Flush")?;
            }
        }

        // Arguments were checked normally before reaching an adapter. A native
        // operation cannot make an allocating argument or fallback disappear.
        if package == "core:bufio" {
            if !name.ends_with("init_with_buf") && !name.contains("_to_") {
                // Buffered byte/count updates preserve the stored writer. The
                // writer callback's data can still escape and clobber it.
                if let Fact::Buffered(inner) | Fact::BufferedReader(inner) =
                    self.flow.deref(&get(0))
                {
                    self.flow.forget_addresses(&inner);
                }
            }
        } else if !name.starts_with("to_") {
            for fact in facts.drain(..) {
                self.flow.forget_addresses(&fact);
            }
        }
        crate::profile::count("no_alloc.io_calls", 1);
        Ok(true)
    }
    fn io_dispatch(&mut self, w: &Work, at: Ref, value: &Fact, mode: &str) -> Result<()> {
        match self.flow.deref(value) {
            Fact::StandardFile(_)=>{
                let flags=&self.r.flags;
                if flags.iter().find_map(|f|f.strip_prefix("-target:")).map(|target|!target.starts_with("darwin_")&&!target.starts_with("linux_"))
                    .unwrap_or(!cfg!(any(target_os="macos",target_os="linux"))) {
                    return Err(self.error(w,at,"the target has no reviewed allocation-free native I/O boundary"));
                }
                Ok(())
            }
            Fact::Stream(inner)=>self.io_dispatch(w,at,&inner,mode),
            Fact::Buffered(inner)=>if matches!(mode,"Write"|"Flush"){self.io_dispatch(w,at,&inner,"Write")}else{Ok(())},
            Fact::BufferedReader(inner)=>if mode=="Read"{self.io_dispatch(w,at,&inner,"Read")}else{Ok(())},
            Fact::Record(fields)=>{
                let Some(Fact::Function(target))=fields.get("procedure")else{return Err(self.error(w,at,"cannot prove allocation freedom: stream callback target is unknown"))};
                let data=fields.get("data").cloned().unwrap_or_default();
                self.enqueue_io_callback(w,at,*target,vec![data,Fact::Atom(mode.into()),Fact::Unknown,Fact::Unknown,Fact::Unknown]);
                Ok(())
            }
            _=>Err(self.error(w,at,"cannot prove allocation freedom: stream callback target is unknown or can be replaced")),
        }
    }
    fn enqueue_io_callback(&mut self, w: &Work, site: Ref, target: Ref, arguments: Vec<Fact>) {
        let trace = self.traces.len();
        self.traces.push((site, w.path));
        self.queue.push_back(Work {
            at: target,
            bindings: w.bindings.clone(),
            root: w.root,
            path: Some(trace),
            procedure: true,
            arguments,
        });
    }
}
fn print_value_known(value: &Fact) -> bool {
    match value {
        Fact::Constant(_) => true,
        Fact::Typed(typ) => {
            !matches!(
                base_type(typ),
                Type::Unknown | Type::Generic | Type::BigInteger | Type::BigRational
            ) && !matches!(base_type(typ),Type::Scalar{name,..} if name=="any")
        }
        Fact::Pack(values) => values.iter().all(print_value_known),
        Fact::Record(_) => true,
        Fact::Spread(typ) => print_value_known(&Fact::Typed(typ.clone())),
        _ => false,
    }
}

fn protected_file_type(r: &Resolver<'_>, typ: &Type) -> bool {
    match typ {
        Type::Pointer(inner) | Type::Nominal(_, inner) => protected_file_type(r, inner),
        Type::Struct(at) => {
            let s = &r.sources[at.file];
            s.path
                .file_name()
                .is_some_and(|n| n == "file.odin" || n == "file_stream.odin")
                && s.info["odin_root"].as_str().is_some_and(|root| {
                    s.path.parent() == Some(Path::new(root).join("core/os").as_path())
                })
                && s.node(at.node).parent.is_some_and(|decl| {
                    s.group(decl, "names")
                        .iter()
                        .any(|n| matches!(s.text(*n), "File" | "File_Stream"))
                })
        }
        _ => false,
    }
}

pub(super) fn descriptor_view(r: &Resolver<'_>, callee: Ref) -> bool {
    identity(r, callee).is_some_and(|(p, n)| {
        p == "core:bufio"
            && matches!(
                n.as_str(),
                "writer_to_writer" | "writer_to_stream" | "reader_to_stream"
            )
    })
}

fn protected_place(r: &mut Resolver<'_>, mut at: Ref) -> bool {
    // A field's value type alone cannot reveal that &procedure points into a
    // native file's callback table, especially after a using projection.
    for _ in 0..32 {
        if r.standard_library(at, "core:os")
            && r.library_alias_member(at, "core:os")
                .is_some_and(|name| matches!(name.as_str(), "stdin" | "stdout" | "stderr"))
        {
            return true;
        }
        let typ = r.specialized_type(at, &Substitutions::new());
        if protected_file_type(r, &typ) {
            return true;
        }
        let owner = r
            .symbol(at)
            .and_then(|i| r.symbols[i].projection.as_ref().map(|p| p.owner));
        let Some(next) = owner.or_else(|| child(r, at, "expr")) else {
            return false;
        };
        at = next;
    }
    true // An unresolved projection cycle must not acquire a trusted capability.
}
