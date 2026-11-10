//! Source-preserving native emitter with explicit guards for known migration
//! gaps. Differential acceptance, not compilation alone, establishes parity.
use crate::{
    ast::{NodeId, Source, Span},
    diagnostic::{Error, Result},
    emit::{Code, Emitted},
    semantics::{Ref, Resolver, Type},
};
use serde_json::json;
use std::collections::{BTreeMap, HashMap, HashSet};
#[path = "bigconstants.rs"]
mod bigconstants;
#[path = "bignumbers.rs"]
mod bignumbers;
#[path = "bigownership.rs"]
mod bigownership;
#[path = "bigoperators.rs"]
mod bigoperators;
#[path = "access.rs"]
mod access;
#[path = "comprehensions.rs"]
mod comprehensions;
#[path = "contextlower.rs"]
mod contextlower;
#[path = "defaults.rs"]
mod defaults;
#[path = "evaluationlower.rs"]
mod evaluationlower;
#[path = "evaluationresult.rs"]
mod evaluationresult;
#[path = "extrema.rs"]
mod extrema;
#[path = "ownershiplower.rs"]
mod ownershiplower;
#[path = "purelower.rs"]
mod purelower;
#[path = "pureproofs.rs"]
mod pureproofs;
#[path = "pureschema.rs"]
mod pureschema;
#[path = "purevariants.rs"]
mod purevariants;
#[path = "repetition.rs"]
mod repetition;
#[path = "sets.rs"]
mod sets;
#[path = "storage.rs"]
mod storage;
#[path = "switches.rs"]
mod switches;
#[path = "typedloops.rs"]
mod typedloops;
#[path = "temporaries.rs"]
mod temporaries;

#[derive(Clone, Debug)]
pub struct Hint {
    pub code: Code,
    pub semantic: Option<Type>,
    pub element_only: bool,
    pub plain_array: bool,
}
pub struct Lower<'a, 'r> {
    pub source: &'a Source,
    pub file: usize,
    pub resolver: &'r mut Resolver<'a>,
    edits: Vec<(Span, Code)>,
    selected: HashMap<String, String>,
    modules: BTreeMap<String, String>,
    helpers: BTreeMap<(NodeId, String, String), Code>,
    prefix: String,
    checked_mode: bool,
    checked_targets: BTreeMap<NodeId, Vec<Ref>>,
    emitting_variants: HashSet<NodeId>,
    mutating_params: HashSet<NodeId>,
    captures: Vec<HashMap<usize, Code>>,
    fstring_plans: HashMap<NodeId, HashSet<NodeId>>,
    fstring_frames: Vec<(String, HashSet<NodeId>)>,
    fstring_busy: HashSet<NodeId>,
    querying: usize,
    logical: bignumbers::State,
    temporary_serial: usize,
    temporary_discarding: HashSet<NodeId>,
    default: bool,
    meta: bool,
    builtin: bool,
    pub errors: HashMap<String, String>,
    pub type_sites: HashMap<String, (Ref, String)>,
    pub pure_error_sites: HashMap<String, Ref>,
    blocked: HashMap<NodeId, String>,
    storage_guards: BTreeMap<NodeId, Vec<storage::Guard>>,
    storage_block_guards: BTreeMap<NodeId, Vec<storage::Guard>>,
    assigning: HashSet<NodeId>,
    native_access: HashSet<NodeId>,
    address_nodes: HashSet<NodeId>,
    switch_owners: HashSet<NodeId>,
    switch_replacements: HashMap<NodeId, Code>,
    body_prologues: BTreeMap<NodeId, Vec<Code>>,
    import_paths: BTreeMap<String, String>,
    declaration_helpers: BTreeMap<NodeId, Vec<Code>>,
    verified_pure: HashSet<NodeId>,
    pure_owned: HashSet<NodeId>,
    pure_busy: HashSet<NodeId>,
    pure_conversion_busy: HashSet<NodeId>,
    pure_operand_busy: HashSet<NodeId>,
    pure_panic_busy: HashSet<NodeId>,
    pure_defaults: HashSet<NodeId>,
    pure_local_busy: HashSet<NodeId>,
    pure_ignored: HashMap<NodeId, String>,
    pure_conditional: HashMap<NodeId, String>,
    borrowing: crate::borrowprogram::Emission,
    borrow_marker: String,
    ownership_wrapping: HashSet<NodeId>,
    evaluation: Option<crate::evaluation::FileView>,
    evaluation_mode: bool,
    evaluated: BTreeMap<NodeId, serde_json::Value>,
    specialized: BTreeMap<NodeId, BTreeMap<String, crate::specialization::EvaluatedCase>>,
    evaluation_scheduled: bool,
    evaluated_busy: HashSet<NodeId>,
    evaluation_skips: BTreeMap<NodeId, bool>,
    evaluation_queries: HashSet<NodeId>,
    evaluation_witnesses: BTreeMap<NodeId, String>,
}
impl<'a, 'r> Lower<'a, 'r> {
    pub fn new(file: usize, resolver: &'r mut Resolver<'a>) -> Result<Self> {
        Self::new_with_view(file, resolver, None)
    }
    pub(crate) fn new_with_view(
        file: usize,
        resolver: &'r mut Resolver<'a>,
        evaluation: Option<crate::evaluation::FileView>,
    ) -> Result<Self> {
        let source = &resolver.sources[file];
        let borrow_marker = format!(
            "{}checked_call",
            resolver.sources.first().unwrap_or(source).prefix
        );
        let mut this = Self {
            source,
            file,
            resolver,
            edits: vec![],
            selected: HashMap::new(),
            modules: BTreeMap::new(),
            helpers: BTreeMap::new(),
            prefix: source.prefix.clone(),
            checked_mode: false,
            checked_targets: BTreeMap::new(),
            emitting_variants: HashSet::new(),
            mutating_params: HashSet::new(),
            captures: vec![],
            fstring_plans: HashMap::new(),
            fstring_frames: vec![],
            fstring_busy: HashSet::new(),
            querying: 0,
            logical: bignumbers::State::default(),
            temporary_serial: 0,
            temporary_discarding: HashSet::new(),
            default: false,
            meta: false,
            builtin: false,
            errors: HashMap::new(),
            type_sites: HashMap::new(),
            pure_error_sites: HashMap::new(),
            blocked: HashMap::new(),
            storage_guards: BTreeMap::new(),
            storage_block_guards: BTreeMap::new(),
            assigning: HashSet::new(),
            native_access: HashSet::new(),
            address_nodes: HashSet::new(),
            switch_owners: HashSet::new(),
            switch_replacements: HashMap::new(),
            body_prologues: BTreeMap::new(),
            import_paths: BTreeMap::new(),
            declaration_helpers: BTreeMap::new(),
            verified_pure: HashSet::new(),
            pure_owned: HashSet::new(),
            pure_busy: HashSet::new(),
            pure_conversion_busy: HashSet::new(),
            pure_operand_busy: HashSet::new(),
            pure_panic_busy: HashSet::new(),
            pure_defaults: HashSet::new(),
            pure_local_busy: HashSet::new(),
            pure_ignored: HashMap::new(),
            pure_conditional: HashMap::new(),
            borrowing: crate::borrowprogram::Emission::default(),
            borrow_marker,
            ownership_wrapping: HashSet::new(),
            evaluation_mode: evaluation.is_some(),
            evaluation,
            evaluated: BTreeMap::new(),
            specialized: BTreeMap::new(),
            evaluation_scheduled: false,
            evaluated_busy: HashSet::new(),
            evaluation_skips: BTreeMap::new(),
            evaluation_queries: HashSet::new(),
            evaluation_witnesses: BTreeMap::new(),
        };
        for edit in &source.edits {
            this.edits
                .push((edit.span, this.gen(edit.text.clone(), edit.span, "syntax")));
        }
        this.imports()?;
        this.resources()?;
        this.prepare_contexts()?;
        this.discover_fstrings();
        if this.source.thor {
            this.prepare_defaults()?;
        }
        this.storage()?;
        if let Some(view) = this.evaluation.clone() {
            this.modules.extend(view.imports);
            for (node, code) in view.values {
                this.switch_replacements.insert(node, this.g(code, node));
            }
            for bridge in view.bridges {
                this.helpers.insert(
                    (bridge.scope, this.prefix.clone(), bridge.name),
                    this.g(bridge.code, bridge.origin),
                );
            }
        }
        Ok(this)
    }
    pub fn instrument_memory(
        &mut self,
        memory: &crate::memory::Memory,
        collection: &str,
        map: &str,
        entry: bool,
    ) {
        if !memory.enabled() {
            return;
        }
        for (i, node) in self.source.nodes.iter().enumerate() {
            let id = NodeId(i);
            if self
                .evaluation
                .as_ref()
                .is_some_and(|view| !view.retains(self.source, id))
            {
                continue;
            }
            if node.kind != "Procedure" {
                continue;
            }
            let Some(body) = self.source.role(id, "body").filter(|body| {
                self.source.raw.as_bytes()[self.source.node(*body).span.start] == b'{'
            }) else {
                continue;
            };
            let declaration = node
                .parent
                .filter(|decl| self.source.node(*decl).kind == "Declaration");
            let attributes = declaration
                .into_iter()
                .flat_map(|decl| self.source.group(decl, "attributes"))
                .map(|attr| self.source.text(*attr))
                .collect::<Vec<_>>();
            if attributes.iter().any(|attr| attr.contains("naked")) {
                continue;
            }
            let main = entry
                && !self
                    .source
                    .ancestors(id)
                    .any(|parent| self.source.node(parent).kind == "Procedure")
                && declaration.is_some_and(|decl| {
                    self.source.group(decl, "names").len() == 1
                        && self.source.text(self.source.group(decl, "names")[0]) == "main"
                });
            if main && memory.tracking != "off" {
                let alias = format!("{}memory_start", self.prefix);
                self.modules.insert(
                    alias.clone(),
                    format!(
                        "{collection}:{}",
                        if memory.tracking == "trace" {
                            "debug_memory_trace"
                        } else {
                            "debug_memory"
                        }
                    ),
                );
                let prologue = self.gen(
                    format!("\ncontext.allocator={alias}.begin({map})\n"),
                    self.source.node(body).span,
                    "memory-debug",
                );
                self.body_prologues.entry(body).or_default().push(prologue);
            }
            if memory.address
                && !attributes
                    .iter()
                    .any(|attr| attr.contains("no_sanitize_address"))
            {
                self.meta = true;
                let prologue = self.gen(
                    format!(
                        "\n{{_debug_entry:u64=0;_={}meta.volatile_load(&_debug_entry)}}\n",
                        self.prefix
                    ),
                    self.source.node(body).span,
                    "memory-debug",
                );
                self.body_prologues.entry(body).or_default().push(prologue);
            }
        }
    }
    pub fn apply_privileges(&mut self, facts: &crate::privileges::Facts) {
        self.mutating_params.extend(
            facts
                .mutating
                .iter()
                .filter(|r| r.file == self.file)
                .map(|r| r.node),
        );
        for (reference, issue) in &facts.issues {
            if reference.file == self.file {
                self.permission_error(reference.node, &issue.message);
            }
        }
        for (reference, message) in &facts.invalid {
            if reference.file == self.file {
                self.permission_error(reference.node, message);
            }
        }
    }
    pub fn remap_imports(&mut self, paths: BTreeMap<String, String>) {
        for (i, node) in self.source.nodes.iter().enumerate() {
            if node.kind != "Import" {
                continue;
            }
            let Ok(path) = unquote(&node.op) else {
                continue;
            };
            if let (Some(mapped), Some(span)) = (paths.get(&path), node.spans.get("path")) {
                self.edits
                    .push((*span, self.gen(json!(mapped).to_string(), *span, "import")));
            }
            let _ = i;
        }
        self.import_paths = paths;
    }
    fn permission_error(&mut self, origin: NodeId, message: &str) {
        let mut target = origin;
        if self.source.node(target).kind == "FieldValue" {
            target = self.source.role(target, "value").unwrap_or(target);
        }
        if let Some(parent) = self.source.node(origin).parent {
            if self.source.group(parent, "names").contains(&origin) {
                if self.source.node(parent).kind == "Declaration" {
                    target = self
                        .source
                        .group(parent, "values")
                        .first()
                        .copied()
                        .unwrap_or(origin);
                } else if self.source.node(parent).kind == "Field" {
                    target = self.source.role(parent, "type").unwrap_or(origin);
                }
            }
        }
        // Keep direct immutable-storage errors ahead of dependent slice/data
        // permission errors, following the reference check order. In
        // particular, assigning a slice header is a binding write.
        if self.blocked.keys().any(|id| {
            self.source
                .node(target)
                .span
                .contains(self.source.node(*id).span)
        }) {
            return;
        }
        let code = self.error_token(origin, message);
        self.blocked.remove(&target);
        if self.source.thor {
            self.switch_replacements.insert(target, code);
        } else {
            self.edits.push((self.source.node(target).span, code));
        }
    }
    fn gen(&self, text: impl Into<String>, span: Span, kind: &str) -> Code {
        Code::generated(text, &self.source.path, span, kind)
    }
    fn g(&self, text: impl Into<String>, id: NodeId) -> Code {
        self.gen(text, self.source.node(id).span, "generated")
    }
    fn copy(&self, span: Span) -> Code {
        Code::source(self.source.slice(span), &self.source.path, span)
    }
    fn reference(&self, id: NodeId) -> Ref {
        Ref {
            file: self.file,
            node: id,
        }
    }
    fn typ(&mut self, id: NodeId) -> Type {
        self.resolver.type_of(self.reference(id))
    }
    fn role(&self, id: NodeId, name: &str) -> Result<NodeId> {
        self.source.role(id, name).ok_or_else(|| {
            Error::new(format!(
                "parser omitted {name} role for {}",
                self.source.node(id).kind
            ))
        })
    }
    fn module(&mut self, name: &str) -> String {
        let alias = format!("{}{name}", self.prefix);
        self.modules.insert(alias.clone(), format!("thor:{name}"));
        alias
    }
    fn native(&mut self, name: &str) -> String {
        self.builtin = true;
        format!("{}builtin.{name}", self.prefix)
    }
    fn unsupported(&self, id: NodeId, name: &str) -> Error {
        Error::unsupported(self.source, self.source.node(id).span, name)
    }
    fn error_token(&mut self, id: NodeId, message: &str) -> Code {
        let token = format!(
            "{}expression_error_{}_{}_end",
            self.source.prefix,
            self.source.node(id).span.start,
            self.errors.len()
        );
        self.errors.insert(token.clone(), message.into());
        self.g(token, id)
    }
    pub fn finish(&mut self) -> Result<(Emitted, HashMap<String, String>)> {
        let request = self
            .evaluation
            .as_ref()
            .and_then(|view| view.request.clone());
        let entry = self.evaluation.as_ref().and_then(|view| view.entry.clone());
        let mut main = vec![];
        if let Some(request) = request {
            main.push(self.evaluation_main(request)?);
        }
        if let Some(entry) = entry {
            main.push(self.evaluation_entry(entry)?);
        }
        if self.source.excluded {
            return Ok((
                self.copy(Span::new(0, self.source.raw.len())).finish(),
                std::mem::take(&mut self.errors),
            ));
        }
        if !self.source.thor {
            let storage = self.native_storage()?;
            let mut replacements = storage.replacements;
            if let Some(view) = &self.evaluation {
                for (id, value) in &view.values {
                    if view.retains(self.source, *id) {
                        replacements.push((self.source.node(*id).span, self.g(value, *id)));
                    }
                }
                for (index, node) in self.source.nodes.iter().enumerate() {
                    let id = NodeId(index);
                    if !view.retains(self.source, id)
                        && node
                            .parent
                            .is_some_and(|parent| view.retains(self.source, parent))
                    {
                        replacements.push((node.span, self.g("", id)));
                    }
                }
            }
            let mut inserts: Vec<_> = self
                .body_prologues
                .iter()
                .map(|(body, parts)| {
                    (
                        self.source.node(*body).span.start + 1,
                        Code::concat(parts.iter().cloned()),
                    )
                })
                .collect();
            inserts.extend(storage.inserts);
            for (declaration, parts) in &self.declaration_helpers {
                inserts.push((
                    self.source.node(*declaration).span.end,
                    Code::concat(parts.iter().cloned()),
                ));
            }
            let mut code = vec![self.splice(NodeId(0), replacements, inserts)?];
            code.extend(main);
            code.extend(
                self.helpers
                    .iter()
                    .filter(|((owner, _, _), _)| *owner == NodeId(0))
                    .map(|(_, code)| code.clone()),
            );
            for (alias, path) in &self.modules {
                code.push(self.gen(
                    format!(
                        "\nimport {alias} {}\n",
                        json!(self.import_paths.get(path).unwrap_or(path))
                    ),
                    Span::new(0, 0),
                    "memory-debug",
                ));
            }
            if self.meta {
                code.push(self.gen(
                    format!("\nimport {}meta \"base:intrinsics\"\n", self.prefix),
                    Span::new(0, 0),
                    "memory-debug",
                ));
            }
            if self.builtin {
                code.push(self.gen(
                    format!("\nimport {}builtin \"base:builtin\"\n", self.prefix),
                    Span::new(0, 0),
                    "support",
                ));
            }
            return Ok((
                Code::concat(code).finish(),
                std::mem::take(&mut self.errors),
            ));
        }
        self.preflight()?;
        let mut code = vec![self.render(NodeId(0), None, false)?];
        code.extend(main);
        let helpers = self
            .helpers
            .range(
                (NodeId(0), String::new(), String::new())
                    ..(NodeId(1), String::new(), String::new()),
            )
            .map(|(_, code)| code.clone())
            .collect::<Vec<_>>();
        code.extend(helpers);
        if self.default {
            let name = format!("{}default_value", self.prefix);
            code.push(self.gen(format!("\n@(private=\"file\")\n{name} :: proc \"contextless\" (value: $T) -> T {{ return value }}\n"),Span::new(0,0),"support"));
        }
        if self.meta {
            code.push(self.gen(
                format!("\nimport {}meta \"base:intrinsics\"\n", self.prefix),
                Span::new(0, 0),
                "support",
            ));
        }
        if self.builtin {
            code.push(self.gen(
                format!("\nimport {}builtin \"base:builtin\"\n", self.prefix),
                Span::new(0, 0),
                "support",
            ));
        }
        for (alias, path) in &self.modules {
            code.push(self.gen(
                format!(
                    "\nimport {alias} {}\n",
                    json!(self.import_paths.get(path).unwrap_or(path))
                ),
                Span::new(0, 0),
                "support",
            ));
        }
        Ok((
            Code::concat(code).finish(),
            std::mem::take(&mut self.errors),
        ))
    }
    fn preflight(&mut self) -> Result<()> {
        for (i, node) in self.source.nodes.iter().enumerate() {
            let id = NodeId(i);
            if self
                .evaluation
                .as_ref()
                .is_some_and(|view| !view.retains(self.source, id))
            {
                continue;
            }
            if self
                .resolver
                .conditional_immutables
                .contains(&self.reference(id))
            {
                return Err(self.unsupported(id, "conditional immutable binding selection"));
            }
            if node.data["pure"] == true && !self.verified_pure.contains(&id) {
                return Err(self.unsupported(id, "pure-function verification and execution"));
            }
            let evaluated = self.evaluated.contains_key(&id)
                || self.evaluation_skips.contains_key(&id)
                || self.specialized.contains_key(&id)
                || self.evaluation_scheduled && self.generic_evaluation(id);
            if node.kind == "Run" && !evaluated {
                return Err(self.unsupported(id, &node.kind));
            }
        }
        Ok(())
    }
    fn imports(&mut self) -> Result<()> {
        let s = self.source;
        for selection in &s.selections {
            let path = unquote(&selection.path)?;
            let mut names = if selection.wildcard {
                let directory = if let Some((collection, suffix)) = path.split_once(':') {
                    s.collection_roots
                        .get(collection)
                        .map(|root| root.join(suffix))
                } else {
                    s.path.parent().map(|parent| parent.join(&path))
                };
                let directory = directory.and_then(|path| std::fs::canonicalize(path).ok());
                let mut names = std::collections::BTreeSet::new();
                for symbol in &self.resolver.symbols {
                    if symbol.scope != NodeId(0) {
                        continue;
                    }
                    let source = &self.resolver.sources[symbol.reference.file];
                    if source.path.parent() != directory.as_deref()
                        || source.info["private"] == true
                    {
                        continue;
                    }
                    let decl = symbol.reference.node;
                    if source
                        .group(decl, "attributes")
                        .iter()
                        .any(|attribute| source.text(*attribute).contains("private"))
                    {
                        continue;
                    }
                    if source.ancestors(decl).any(|ancestor| {
                        source.node(ancestor).kind == "ForeignBlock"
                            && source
                                .group(ancestor, "attributes")
                                .iter()
                                .any(|attr| source.text(*attr).contains("private"))
                    }) {
                        continue;
                    }
                    names.insert(symbol.name.clone());
                }
                names.into_iter().collect::<Vec<_>>()
            } else {
                selection.names.clone()
            };
            if let Some(view) = &self.evaluation {
                names.retain(|name| view.aliases.contains(name));
            }
            let mut namespace = if selection.package_name.is_empty() {
                path.rsplit(['/', ':']).next().unwrap_or("").to_string()
            } else {
                selection.package_name.clone()
            };
            if namespace == "_" {
                namespace = format!("{}import_{}", self.prefix, selection.start);
                let import = s.nodes.iter().enumerate().rev().find(|(_, n)| {
                    n.kind == "Import" && n.span.end <= selection.start && n.op == selection.path
                });
                if let Some((_, node)) = import {
                    if let Some(span) = node.spans.get("alias") {
                        self.edits
                            .push((*span, self.gen(namespace.clone(), *span, "import")));
                    }
                }
            }
            let alias = format!("{}import_{}", self.prefix, self.modules.len());
            self.modules.insert(alias.clone(), path);
            for (i, name) in names.iter().enumerate() {
                let start = selection
                    .positions
                    .get(i)
                    .and_then(|v| v["offset"].as_u64())
                    .unwrap_or(selection.start as u64) as usize;
                let span = Span::new(start, start + name.len());
                if self
                    .selected
                    .insert(name.clone(), format!("{alias}.{name}"))
                    .is_some()
                {
                    return Err(Error::at(s, span, "duplicate name in selective import"));
                }
                // Resolve even unused selected names without making them package
                // exports. The init-local aliases retain native privacy checks.
                let owner = NodeId(0);
                let name_key = format!("validate_import_{start}");
                let body=format!("\n@(private=\"file\", init)\n{}{name_key} :: proc \"contextless\" () {{ checked :: {alias}.{name} }}\n",self.prefix);
                self.helpers.insert(
                    (owner, self.prefix.clone(), name_key),
                    self.gen(body, span, "import"),
                );
                if let Some(symbol) = self.resolver.lookup(
                    Ref {
                        file: self.file,
                        node: NodeId(0),
                    },
                    name,
                ) {
                    let declaration = self.resolver.symbols[symbol].reference;
                    if declaration.file == self.file {
                        return Err(Error::at(
                            s,
                            s.node(self.resolver.symbols[symbol].name_node).span,
                            format!("declaration conflicts with selective import: {name}"),
                        ));
                    }
                }
            }
            let mut comments = String::from("\n");
            for span in &s.comments {
                if selection.start <= span.start && span.end <= selection.end {
                    comments.push_str(s.slice(*span));
                    comments.push('\n');
                }
            }
            let span = Span::new(selection.start, selection.end);
            self.edits.push((span, self.gen(comments, span, "syntax")));
            let _ = namespace;
        }
        Ok(())
    }
    fn resources(&mut self) -> Result<()> {
        for (i, node) in self.source.nodes.iter().enumerate() {
            let id = NodeId(i);
            let paths = if node.kind == "ForeignImport" {
                self.source.group(id, "paths").to_vec()
            } else if node.kind == "Call"
                && self.source.role(id, "callee").is_some_and(|callee| {
                    matches!(
                        self.source.text(callee),
                        "#load" | "#load_hash" | "#load_directory" | "#exists"
                    )
                })
            {
                self.source
                    .group(id, "args")
                    .first()
                    .copied()
                    .into_iter()
                    .collect()
            } else {
                vec![]
            };
            for literal in paths {
                if self.source.node(literal).kind != "Literal" {
                    continue;
                }
                let path = unquote(self.source.text(literal))?;
                let path = if let Some((collection, suffix)) = path.split_once(':') {
                    self.source
                        .collection_roots
                        .get(collection)
                        .map(|base| base.join(suffix))
                } else if std::path::Path::new(&path).is_absolute() {
                    None
                } else {
                    Some(
                        self.source
                            .path
                            .parent()
                            .ok_or_else(|| Error::new("source has no parent"))?
                            .join(path),
                    )
                };
                if let Some(path) = path {
                    let path = crate::project::absolute(&path)?;
                    let span = self.source.node(literal).span;
                    self.edits.push((
                        span,
                        self.gen(json!(path.to_string_lossy()).to_string(), span, "resource"),
                    ));
                }
            }
        }
        Ok(())
    }
    fn splice(
        &self,
        id: NodeId,
        mut replacements: Vec<(Span, Code)>,
        inserts: Vec<(usize, Code)>,
    ) -> Result<Code> {
        let span = self.source.node(id).span;
        let mut chosen: Vec<(Span, Code)> = vec![];
        replacements.sort_by_key(|(span, _)| (span.start, std::cmp::Reverse(span.end)));
        for (child, code) in replacements {
            if span.contains(child)
                && !child.is_empty()
                && !chosen.iter().any(|(outer, _)| outer.contains(child))
            {
                chosen.push((child, code));
            }
        }
        for (edit, code) in &self.edits {
            if span.contains(*edit)
                && !edit.is_empty()
                && !chosen.iter().any(|(outer, _)| outer.contains(*edit))
            {
                chosen.push((*edit, code.clone()));
            }
        }
        for (offset, code) in inserts {
            if span.start <= offset
                && offset <= span.end
                && !chosen
                    .iter()
                    .any(|(child, _)| child.start < offset && offset < child.end)
            {
                chosen.push((Span::new(offset, offset), code));
            }
        }
        chosen.sort_by_key(|(span, _)| (span.start, span.end));
        let mut cursor = span.start;
        let mut parts = vec![];
        for (child, code) in chosen {
            if child.start < cursor {
                return Err(Error::new(format!(
                    "overlapping source transformations in {}",
                    self.source.path.display()
                )));
            }
            parts.push(self.copy(Span::new(cursor, child.start)));
            parts.push(code);
            cursor = child.end;
        }
        parts.push(self.copy(Span::new(cursor, span.end)));
        Ok(Code::concat(parts))
    }
    pub fn render(&mut self, id: NodeId, expected: Option<Hint>, constant: bool) -> Result<Code> {
        if self
            .evaluation
            .as_ref()
            .is_some_and(|view| !view.retains(self.source, id))
        {
            return Ok(self.g("", id));
        }
        let old = self.checked_mode;
        if self.source.node(id).kind == "Procedure" {
            self.checked_mode =
                self.source.node(id).data["pure"] == true || self.emitting_variants.contains(&id);
        }
        let result = self.render_inner(id, expected, constant);
        self.checked_mode = old;
        let code = result?;
        if self.querying == 0 {
            if let Some(guards) = self.storage_guards.get(&id).cloned() {
                let guards = guards
                    .into_iter()
                    .map(|guard| self.render_storage_guard(guard))
                    .collect::<Result<Vec<_>>>()?;
                return Ok(Code::concat(
                    guards.into_iter().chain(std::iter::once(code)),
                ));
            }
        }
        Ok(code)
    }

    fn render_inner(&mut self, id: NodeId, expected: Option<Hint>, constant: bool) -> Result<Code> {
        if let Some(message) = self.blocked.get(&id).cloned() {
            return Ok(self.error_token(id, &message));
        }
        if let Some(code) = self.logical.replacements.get(&id) {
            return Ok(code.clone());
        }
        if let Some(code) = self.switch_replacements.get(&id) {
            return Ok(code.clone());
        }
        if !self.evaluated_busy.contains(&id) {
            if self.specialized.contains_key(&id) {
                self.evaluated_busy.insert(id);
                let result = self.specialized_result(id, expected, constant);
                self.evaluated_busy.remove(&id);
                return result;
            }
            if self.evaluation_scheduled
                && self.source.node(id).kind == "Run"
                && self.generic_evaluation(id)
                && !self.evaluated.contains_key(&id)
                && !self.evaluation_skips.contains_key(&id)
            {
                return Ok(self.unbound_evaluation(id));
            }
            if let Some(unchecked) = self.evaluation_skips.get(&id).copied() {
                self.evaluated_busy.insert(id);
                let result = self.evaluation_skipped(id, unchecked);
                self.evaluated_busy.remove(&id);
                return result;
            }
            if let Some(value) = self.evaluated.get(&id).cloned() {
                self.evaluated_busy.insert(id);
                let result = self.evaluation_result(id, &value, constant);
                self.evaluated_busy.remove(&id);
                return result;
            }
        }
        if self.checked_mode {
            if let Some(name) = self.pure_ignored.get(&id) {
                return Ok(self.g(name, id));
            }
        }
        let node = self.source.node(id);
        let kind = node.kind.clone();
        if self.checked_mode
            && kind == "ExpressionStatement"
            && !self.pure_panic_busy.contains(&id)
            && node
                .parent
                .is_some_and(|parent| self.source.node(parent).kind == "Block")
            && self.source.role(id, "expr").is_some_and(|call| {
                self.source.node(call).kind == "Call"
                    && crate::purity::builtin_call(
                        self.resolver,
                        Ref {
                            file: self.file,
                            node: call,
                        },
                    )
                    .as_deref()
                        == Some("panic")
            })
        {
            self.pure_panic_busy.insert(id);
            if let Some(parent) = node.parent {
                self.convert_do(parent);
            }
            let statement = self.render_inner(id, expected, constant);
            self.pure_panic_busy.remove(&id);
            return Ok(Code::concat([self.g("{", id), statement?, self.g("}", id)]));
        }
        if self.checked_mode && self.querying == 0 {
            if kind == "Block" && self.pure_conditional.contains_key(&id) {
                if let Some(code) = self.ownership_body(id)? {
                    return Ok(code);
                }
            }
            if let Some(message) = self.pure_conditional.remove(&id) {
                let token = format!(
                    "{}conditional_pure_error_{}_end",
                    self.source.prefix, node.span.start
                );
                self.errors.insert(token.clone(), message.clone());
                let result = if kind == "Declaration" {
                    self.render_inner(id, expected, constant).map(|code| {
                        Code::concat([code, self.g(format!("\n#panic({})\n", json!(token)), id)])
                    })
                } else if kind == "Block" {
                    Ok(self.g(format!("{{#panic({})}}", json!(token)), id))
                } else {
                    Ok(self.g(token, id))
                };
                self.pure_conditional.insert(id, message);
                return result;
            }
        }
        if kind == "AutoCast" && self.checked_mode && self.querying == 0 {
            return self.pure_auto_cast(id, expected, constant);
        }

        if self.checked_mode && !self.pure_local_busy.contains(&id) {
            if let Some(code) = self.pure_local_type(id, expected.clone(), constant)? {
                return Ok(code);
            }
        }
        if self.checked_mode && self.querying == 0 && !self.pure_conversion_busy.contains(&id) {
            if kind == "Cast" && node.op == "transmute" {
                if let Some(code) = self.pure_transmute(id, constant)? {
                    return Ok(code);
                }
            }
            if let Some(code) = self.pure_conversion(id, expected.clone(), constant)? {
                return Ok(code);
            }
        }
        if self.checked_mode && self.querying == 0 && !self.pure_operand_busy.contains(&id) {
            if let Some(code) = self.pure_operands(id, expected.clone(), constant)? {
                return Ok(code);
            }
        }
        if kind == "Directive" && self.source.names.get(node.name) == "procedure" {
            if let Some(proc) = self
                .source
                .ancestors(id)
                .find(|node| self.source.node(*node).kind == "Procedure")
                .filter(|node| self.emitting_variants.contains(node))
            {
                return Ok(self.g(self.checked_procedure_literal(proc), id));
            }
        }
        if kind == "Block" {
            if let Some(code) = self.ownership_body(id)? {
                return Ok(code);
            }
            if self.checked_mode {
                if let Some((proc, message)) = node.parent.and_then(|proc| {
                    self.borrowing
                        .contracts
                        .get(&self.reference(proc))
                        .map(|message| (proc, message.clone()))
                }) {
                    let token = format!("{}pure_contract_{}_end", self.source.prefix, proc.0);
                    self.errors.insert(token.clone(), message);
                    self.pure_error_sites
                        .insert(token.clone(), self.reference(proc));
                    if self.source.raw.as_bytes()[node.span.start] != b'{' {
                        self.convert_do(id);
                    }
                    return Ok(self.g(format!("{{#panic({})}}", json!(token)), proc));
                }
            }
            if let Some(owner) = node
                .parent
                .filter(|owner| self.pure_owned.contains(owner) && !self.pure_busy.contains(owner))
            {
                if self.source.role(owner, "body") == Some(id) {
                    return self.pure_body(owner, id);
                }
            }
        }
        if kind == "ProcedureType" {
            if let Some(code) = self.ownership_signature(id)? {
                return Ok(code);
            }
        }
        if !constant && self.querying == 0 && !self.fstring_busy.contains(&id) {
            if let Some(targets) = self.fstring_plans.get(&id).cloned() {
                return self.fstring_expression(id, expected, targets);
            }
        }
        if let Some(code) = self.big_expression(id, expected.clone(), constant)? { return Ok(code); }
        if self.source.thor {
            if let Some(code) = self.temporary_expression(id, constant)? { return Ok(code); }
        }
        if matches!(kind.as_str(), "Declaration" | "Assignment" | "Return") {
            if let Some(code) = self.switch_statement(id, constant)? {
                return Ok(code);
            }
        }
        if matches!(kind.as_str(), "SwitchExpr" | "CompileSwitchExpr") {
            return self.switch_expression(id, vec![expected], constant);
        }
        if kind == "CompileSwitch" {
            return self.compile_switch(id);
        }
        if kind == "Comprehension" {
            return self.comprehension(id, expected, constant);
        }
        if kind == "Call" {
            if let Some(code) = self.extrema(id, constant)? {
                return Ok(code);
            }
        }
        if self.source.thor && kind == "Assignment" {
            if let Some(code) = self.access_assignment(id, constant)? {
                return Ok(code);
            }
        }
        if kind == "Range" {
            if let Some(code) = self.typed_loop(id)? {
                return Ok(code);
            }
        }
        if kind == "Procedure" && !self.fstring_frames.is_empty() {
            let frames = std::mem::take(&mut self.fstring_frames);
            let code = self.render(id, expected, constant);
            self.fstring_frames = frames;
            return code;
        }
        if kind == "Call" && self.checked_mode && self.querying == 0 {
            if let Some(code) = self.checked_call(id, constant)? {
                return Ok(code);
            }
        }
        if kind == "Interpolation" {
            let code = self.interpolation(id)?;
            if self.querying == 0 {
                if let Some((pointer, _)) = self
                    .fstring_frames
                    .iter()
                    .rev()
                    .find(|(_, targets)| targets.contains(&id))
                {
                    let pointer = pointer.clone();
                    let module = self.module("temporaries");
                    return Ok(Code::concat([
                        self.g(format!("{module}.capture({pointer},"), id),
                        code,
                        self.g(")", id),
                    ]));
                }
            }
            return Ok(code);
        }
        if kind == "OrElse" {
            if let Some(fallback) = self.source.role(id, "y") {
                let operand = self.unparen(fallback);
                if self.source.node(operand).kind == "Call" && !self.diverging(operand) {
                    if let Some(callee) = self.source.role(operand, "callee") {
                        if let Some(signature) = self.resolver.signature(self.reference(callee)) {
                            let source = &self.resolver.sources[signature.file];
                            let data = &source.node(signature.node).data;
                            if self.resolver.fields(signature, "results").len() == 2
                                && (data["optional_ok"] == true
                                    || data["optional_allocator_error"] == true)
                            {
                                let left =
                                    self.render(self.role(id, "x")?, expected.clone(), constant)?;
                                let right = self.render(fallback, expected, constant)?;
                                self.default = true;
                                return Ok(Code::concat([
                                    self.g("(", id),
                                    left,
                                    self.g(format!(" or_else {}default_value(", self.prefix), id),
                                    right,
                                    self.g("))", id),
                                ]));
                            }
                        }
                    }
                }
            }
        }
        if kind == "Ident" {
            if let Some(symbol) = self.resolver.symbol(self.reference(id)) {
                for captures in self.captures.iter().rev() {
                    if let Some(code) = captures.get(&symbol) {
                        return Ok(code.clone());
                    }
                }
            }
            if let Some(target) = self.selected.get(self.source.text(id)).filter(|_| {
                self.resolver
                    .lookup(self.reference(id), self.source.text(id))
                    .is_none()
            }) {
                let parent = node.parent;
                let binding = parent.is_some_and(|p| {
                    let parent = self.source.node(p);
                    matches!(
                        parent.kind.as_str(),
                        "ImplicitSelector" | "EnumType" | "PolyType"
                    ) || matches!(parent.kind.as_str(), "Selector" | "FieldValue")
                        && self.source.role(p, "field") == Some(id)
                        || matches!(parent.kind.as_str(), "Declaration" | "Field" | "Range")
                            && self.source.group(p, "names").contains(&id)
                });
                if !binding {
                    return Ok(self
                        .g(target.clone(), id)
                        .expansion(&self.source.path, node.span));
                }
            }
        }
        if let Some(code) = self.set_expression(id, constant)? {
            return Ok(code);
        }
        if kind == "Compound" && self.is_array(id) {
            return self.array(id, expected, None, constant);
        }
        if kind == "Binary" {
            match node.op.as_str() {
                "in" | "not_in" => return self.membership(id, constant),
                "++" => return self.concatenate(id, expected),
                "**" => return self.power(id, constant),
                "*" => {
                    let a = self.role(id, "left")?;
                    let b = self.role(id, "right")?;
                    if self.typ(a).is_string() || self.typ(b).is_string() {
                        return self.repeat(id, a, b);
                    }
                }
                _ => {}
            }
        }
        if kind == "OrPanic" {
            return self.or_panic(id);
        }
        if kind == "Call" {
            let callee = self.role(id, "callee")?;
            if self.source.node(callee).kind == "Selector" {
                let base = self.role(callee, "expr")?;
                let field = self.role(callee, "field")?;
                let namespace = self.source.text(base);
                let format_import = self.source.nodes.iter().any(|node| {
                    node.kind == "Import"
                        && unquote(&node.op).ok().as_deref() == Some("thor:format")
                        && (self.source.names.get(node.name) == namespace
                            || self.source.names.get(node.name).is_empty() && namespace == "format")
                });
                if format_import
                    && self.source.text(field) == "group_integer"
                    && self.resolver.symbol(self.reference(base)).is_none()
                {
                    if let Some(arg) = self.source.group(id, "args").first() {
                        if matches!(self.typ(*arg),Type::Scalar{name,..} if name.starts_with('f')) {
                            return Ok(
                                self.error_token(id, "Integer grouping requires an integer value.")
                            );
                        }
                    }
                }
            }
        }
        if self.source.thor && matches!(kind.as_str(), "Index" | "Slice") {
            if let Some(code) = self.access(id, constant)? {
                return Ok(code);
            }
        }
        let mut overrides: HashMap<NodeId, (Option<Hint>, bool)> = HashMap::new();
        match kind.as_str() {
            "Declaration" => {
                let hint = if let Some(annotation) = self.source.role(id, "type") {
                    Some(Hint {
                        semantic: Some(self.typ(annotation)),
                        code: self.render(annotation, None, false)?,
                        element_only: false,
                        plain_array: self.source.node(annotation).kind == "ArrayType",
                    })
                } else {
                    None
                };
                for value in self.source.group(id, "values") {
                    overrides.insert(*value, (hint.clone(), !node.mutable));
                }
            }
            "Assignment" => {
                for (lhs, rhs) in self
                    .source
                    .group(id, "lhs")
                    .iter()
                    .zip(self.source.group(id, "rhs"))
                {
                    let left = self.render(*lhs, None, false)?;
                    let type_of = self.native("type_of");
                    let hint = if self.source.text(*lhs) == "_" {
                        None
                    } else {
                        Some(Hint {
                            semantic: Some(self.typ(*lhs)),
                            code: Code::concat([
                                self.g(format!("{type_of}("), id),
                                left,
                                self.g(")", id),
                            ]),
                            element_only: false,
                            plain_array: false,
                        })
                    };
                    overrides.insert(*rhs, (hint, constant));
                }
            }
            "Field" => {
                if let (Some(annotation), Some(value)) = (
                    self.source.role(id, "type"),
                    self.source.role(id, "default"),
                ) {
                    let code = self.render(annotation, None, false)?;
                    overrides.insert(
                        value,
                        (
                            Some(Hint {
                                semantic: Some(self.typ(annotation)),
                                code,
                                element_only: false,
                                plain_array: self.source.node(annotation).kind == "ArrayType",
                            }),
                            constant,
                        ),
                    );
                }
            }
            "Procedure" => {
                for child in &node.children {
                    overrides.insert(*child, (None, false));
                }
                for clause in self.source.group(id, "where") {
                    overrides.insert(*clause, (None, true));
                }
            }
            "When" | "WhenExpr" => {
                if let Some(cond) = self.source.role(id, "cond") {
                    overrides.insert(cond, (None, true));
                }
            }
            "ArrayType" => {
                if let Some(len) = self.source.role(id, "len") {
                    overrides.insert(len, (None, true));
                }
            }
            "Paren" | "FieldValue" => {
                if let Some(expr) = self
                    .source
                    .role(id, if kind == "Paren" { "expr" } else { "value" })
                {
                    overrides.insert(expr, (expected.clone(), constant));
                }
            }
            "Return" => {
                if let Some(owner) = self
                    .source
                    .ancestors(id)
                    .find(|id| self.source.node(*id).kind == "Procedure")
                {
                    if let Some(signature) = self.source.role(owner, "type") {
                        let results = self.resolver.fields(self.reference(signature), "results");
                        for (value, (_, annotation)) in
                            self.source.group(id, "values").iter().zip(results)
                        {
                            if let Some(annotation) = annotation.filter(|r| r.file == self.file) {
                                let code = self.render(annotation.node, None, false)?;
                                overrides.insert(
                                    *value,
                                    (
                                        Some(Hint {
                                            semantic: Some(self.typ(annotation.node)),
                                            code,
                                            element_only: false,
                                            plain_array: self.source.node(annotation.node).kind
                                                == "ArrayType",
                                        }),
                                        constant,
                                    ),
                                );
                            }
                        }
                    }
                }
            }
            "Compound" if !self.is_array(id) => {
                let annotation=self.source.role(id,"type");
                let typ=annotation.map(|a|self.typ(a)).or_else(||expected.as_ref().and_then(|h|h.semantic.clone())).unwrap_or(Type::Unknown);
                let target=if let Some(a)=annotation{Some(self.render(a,None,true)?)}else{expected.as_ref().map(|h|h.code.clone())};
                let fields=self.resolver.record_fields(&typ);
                for (index,value) in self.source.group(id,"elems").iter().enumerate(){
                    let field=if self.source.node(*value).kind=="FieldValue"{self.source.role(*value,"field").map(|id|self.source.text(id).to_string())}else{fields.get(index).map(|(name,_)|name.clone())};
                    let map=matches!(typ,Type::Map{..});
                    let sequence=typ.is_sequence();
                    let semantic=if map||sequence{typ.element().cloned()}else if let Some(field)=&field{
                        fields.iter().find(|(name,_)|name==field).map(|(_,typ)|typ.clone()).or_else(||typ.is_unknown().then_some(Type::Generic))
                    }else{None};
                    let hint=if let Some(target)=target.as_ref().filter(|_|map||sequence||field.is_some()){
                        self.meta=true;
                        let query=if sequence{"type_elem_type"}else{"type_field_type"};
                        let mut code=vec![self.g(format!("#type {}meta.{query}(",self.prefix),id),target.clone()];
                        if !map&&!sequence{if let Some(field)=&field{code.push(self.g(format!(",{}",json!(field)),*value));}}
                        code.push(self.g(")",id));
                        let code=if map{let witness=self.module("comprehension");let type_of=self.native("type_of");Code::concat([self.g(format!("{type_of}({witness}.second(("),id),target.clone(),self.g("){}))",id)])}else{Code::concat(code)};
                        Some(Hint{code,semantic,element_only:false,plain_array:false})
                    }else{None};
                    overrides.insert(*value,(hint,constant));
                }
            }
            "Call" => {
                let callee = self.role(id, "callee")?;
                for arg in self.source.group(id, "args") {
                    let value = self.source.role(*arg, "value").unwrap_or(*arg);
                    if let Some(hint) = self.contextual_hint(value) {
                        overrides.insert(*arg, (Some(hint), constant));
                    }
                }
                if let Some(signature) = self
                    .resolver
                    .signature(self.reference(callee))
                    .filter(|_| self.source.node(callee).op != "->")
                {
                    let params = self.resolver.fields(signature, "params");
                    for (i, arg) in self.source.group(id, "args").iter().enumerate() {
                        if overrides.contains_key(arg) {
                            continue;
                        }
                        let index = if self.source.node(*arg).kind == "FieldValue" {
                            let name = self
                                .source
                                .role(*arg, "field")
                                .map(|id| self.source.text(id))
                                .unwrap_or("");
                            params.iter().position(|(p, _)| p == name).unwrap_or(i)
                        } else {
                            i
                        };
                        if let Some((_, Some(annotation))) = params.get(index) {
                            let unit = &self.resolver.sources[signature.file];
                            if unit
                                .descendants(signature.node)
                                .any(|node| unit.node(node).kind == "PolyType")
                            {
                                continue;
                            }
                            self.meta = true;
                            let target = self.render(callee, None, false)?;
                            let type_of = self.native("type_of");
                            let code = Code::concat([
                                self.g(
                                    format!(
                                        "#type {}meta.type_proc_parameter_type({type_of}(",
                                        self.prefix
                                    ),
                                    id,
                                ),
                                target,
                                self.g(format!("), {index})"), id),
                            ]);
                            overrides.insert(
                                *arg,
                                (
                                    Some(Hint {
                                        semantic: Some(self.resolver.specialized_type(*annotation, &Default::default())),
                                        code,
                                        element_only: false,
                                        plain_array: false,
                                    }),
                                    constant,
                                ),
                            );
                        }
                    }
                }
            }
            _ => {}
        }
        if kind == "Ternary" {
            let cond = self.role(id, "cond")?;
            let x = self.role(id, "x")?;
            let y = self.role(id, "y")?;
            let cond = self.render(cond, None, constant)?;
            let a = self.render(x, expected.clone(), constant)?;
            let b = self.render(y, expected.clone(), constant)?;
            return Ok(if constant {
                Code::concat([
                    self.g("(", id),
                    a,
                    self.g(" when (", id),
                    cond,
                    self.g(") else ", id),
                    b,
                    self.g(")", id),
                ])
            } else {
                Code::concat([
                    self.g("((", id),
                    cond,
                    self.g(") ? (", id),
                    a,
                    self.g(") : (", id),
                    b,
                    self.g("))", id),
                ])
            }
            .expansion(&self.source.path, node.span));
        }
        let mut replacements = vec![];
        for child in &node.children {
            let (hint, con) = overrides.get(child).cloned().unwrap_or((None, constant));
            replacements.push((
                self.source.node(*child).span,
                if self.big_owning_child(id,*child) {self.big_owned(*child,hint,con)?} else {self.render(*child, hint, con)?},
            ));
        }
        let mut inserts = vec![];
        if id.0 == 0 {
            let has_flag = self.source.info["file_tags"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|v| v.as_str())
                .any(|tag| {
                    tag.contains("feature")
                        && tag
                            .split_whitespace()
                            .any(|s| s == "using-stmt" || s == "!using-stmt")
                });
            if !has_flag {
                let origin = self.source.nodes.iter().find(|n| {
                    n.kind == "Using"
                        || n.kind == "Field"
                            && n.data["using_field"] == true
                            && n.parent.is_some_and(|p| {
                                self.source.node(p).kind == "Fields"
                                    && self.source.node(p).parent.is_some_and(|p| {
                                        self.source.node(p).kind == "ProcedureType"
                                            && self.source.role(p, "params") == n.parent
                                    })
                            })
                });
                if let Some(origin) = origin {
                    let newline = if self.source.raw.contains("\r\n") {
                        "\r\n"
                    } else {
                        "\n"
                    };
                    let offset = if self.source.raw.starts_with('\u{feff}') {
                        3
                    } else {
                        0
                    };
                    inserts.push((
                        offset,
                        self.gen(
                            format!("#+feature using-stmt{newline}"),
                            origin.span,
                            "support",
                        ),
                    ));
                }
            }
        }
        if kind == "Block" {
            let mut helpers = self.body_prologues.get(&id).cloned().unwrap_or_default();
            helpers.extend(
                self.storage_block_guards
                    .get(&id)
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .map(|guard| self.render_storage_guard(guard))
                    .collect::<Result<Vec<_>>>()?,
            );
            helpers.extend(self.helper_declarations(id));
            if !helpers.is_empty() {
                if self.source.raw.as_bytes()[node.span.start] == b'{' {
                    inserts.push((node.span.start + 1, Code::concat(helpers)));
                } else {
                    self.convert_do(id);
                    let code = self.splice(id, replacements, inserts)?;
                    return Ok(Code::concat([
                        self.g("{\n", id),
                        Code::concat(helpers),
                        code,
                        self.g("\n}", id),
                    ]));
                }
            }
        }
        let code = self.splice(id, replacements, inserts)?;
        if let Some(helpers) = self.declaration_helpers.get(&id) {
            Ok(Code::concat(
                std::iter::once(code).chain(helpers.iter().cloned()),
            ))
        } else {
            Ok(code)
        }
    }
    fn convert_do(&mut self, id: NodeId) {
        let mut cursor = self.source.node(id).span.start;
        loop {
            let previous = cursor;
            while cursor > 0 && self.source.raw.as_bytes()[cursor - 1].is_ascii_whitespace() {
                cursor -= 1;
            }
            if let Some(comment) = self.source.comments.iter().find(|span| span.end == cursor) {
                cursor = comment.start;
            }
            if previous == cursor {
                break;
            }
        }
        if cursor >= 2 && &self.source.raw.as_bytes()[cursor - 2..cursor] == b"do" {
            let token = Span::new(cursor - 2, cursor);
            self.edits
                .push((token, self.gen("", token, "expression-body")));
        }
    }
    fn is_array(&self, id: NodeId) -> bool {
        self.source.node(id).kind == "Compound"
            && self
                .source
                .node(id)
                .spans
                .get("open")
                .is_some_and(|span| self.source.slice(*span) == "[")
    }
    fn array(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        needle: Option<NodeId>,
        constant: bool,
    ) -> Result<Code> {
        let elems = self.source.group(id, "elems").to_vec();
        let mut first_rendered = None;
        let mut target = None;
        let element = if let Some(hint) = &expected {
            if hint.element_only {
                hint.code.clone()
            } else {
                self.meta = true;
                target = Some(hint.code.clone());
                Code::concat([
                    self.g(format!("{}meta.type_elem_type(", self.prefix), id),
                    hint.code.clone(),
                    self.g(")", id),
                ])
            }
        } else {
            let anchor = needle
                .filter(|needle| !self.resolver.untyped_operand(self.reference(*needle)))
                .or_else(|| elems.first().copied())
                .or(needle);
            if let Some(anchor) = anchor {
                let code = self.render(anchor, None, constant)?;
                if elems.first() == Some(&anchor) {
                    first_rendered = Some(code.clone());
                }
                self.default = true;
                let type_of = self.native("type_of");
                Code::concat([
                    self.g(format!("{type_of}({}default_value(", self.prefix), id),
                    code,
                    self.g("))", id),
                ])
            } else {
                return Ok(self.error_token(
                    id,
                    "An empty array literal needs an element type from its context.",
                ));
            }
        };
        let semantic = expected.as_ref().and_then(|hint| {
            if hint.element_only { hint.semantic.clone() }
            else { hint.semantic.as_ref().and_then(|typ|typ.element().cloned()) }
        }).or_else(|| {
            needle.filter(|needle| !self.resolver.untyped_operand(self.reference(*needle)))
                .or_else(||elems.first().copied()).or(needle).map(|anchor|self.typ(anchor).defaulted())
        });
        let hint = Hint {
            semantic,
            code: element.clone(),
            element_only: false,
            plain_array: false,
        };
        let mut values = vec![];
        for (i, value) in elems.iter().enumerate() {
            if i != 0 {
                values.push(self.g(", ", id));
            }
            values.push(if i == 0 && first_rendered.is_some() {
                if self.big_enabled(){self.big_owned(*value,Some(hint.clone()),constant)?}else{first_rendered.clone().unwrap()}
            } else {
                self.big_owned(*value, Some(hint.clone()), constant)?
            });
        }
        for span in &self.source.comments {
            if self.source.node(id).span.contains(*span)
                && !elems
                    .iter()
                    .any(|e| self.source.node(*e).span.contains(*span))
            {
                values.insert(
                    0,
                    self.gen(
                        format!("{}\n", self.source.slice(*span)),
                        *span,
                        "source-comment",
                    ),
                );
            }
        }
        let mut literal = Code::concat([
            self.g(format!("([{}]", elems.len()), id),
            element,
            self.g("{", id),
            Code::concat(values),
            self.g("})", id),
        ]);
        if let Some(target) = target {
            if !expected.as_ref().is_some_and(|hint| hint.plain_array) {
                literal = Code::concat([
                    self.g("cast(", id),
                    target,
                    self.g(")(", id),
                    literal,
                    self.g(")", id),
                ]);
            }
        }
        Ok(literal.expansion(&self.source.path, self.source.node(id).span))
    }
    fn membership_helper(&mut self, id: NodeId) -> String {
        let helper = format!("{}contains", self.prefix);
        let key = (NodeId(0), self.prefix.clone(), helper.clone());
        if !self.helpers.contains_key(&key) {
            let mut parts = String::new();
            let mut names = vec![];
            for (suffix, signature) in [
                ("array", "$C/[$N]$E"),
                ("slice", "$C/[]$E"),
                ("dynamic", "$C/[dynamic]$E"),
                ("fixed", "$C/[dynamic; $N]$E"),
            ] {
                let name = format!("{helper}_{suffix}");
                names.push(name.clone());
                parts.push_str(&format!("\n@(private=\"file\")\n{name} :: proc \"contextless\"(values: {signature}, value:E)->bool {{ for element in values {{if element==value {{return true}}}}; return false }}\n"));
            }
            for (suffix, signature, element) in [("map", "$C/map[$K]$V", "K"), ("bits", "$C/bit_set[$E]", "E")] {
                let name=format!("{helper}_{suffix}");
                names.push(name.clone());
                parts.push_str(&format!("\n@(private=\"file\")\n{name} :: proc \"contextless\"(values:{signature},value:{element})->bool{{return value in values}}\n"));
            }
            let sets=self.module("sets");
            let name=format!("{helper}_set");
            names.push(name.clone());
            parts.push_str(&format!("\n@(private=\"file\")\n{name} :: proc \"contextless\"(values:{sets}.Set($E),value:E)->bool{{return value in values.table}}\n"));
            parts.push_str(&format!(
                "\n@(private=\"file\")\n{helper} :: proc{{{}}}\n",
                names.join(", ")
            ));
            self.helpers.insert(key, self.g(parts, id));
        }
        helper
    }
    fn membership(&mut self, id: NodeId, constant: bool) -> Result<Code> {
        let left = self.role(id, "left")?;
        let right = self.role(id, "right")?;
        let mut sequence = right;
        while self.source.node(sequence).kind == "Paren" {
            sequence = self.role(sequence, "expr")?;
        }
        let op = self.source.node(id).op.clone();
        if self.source.node(sequence).kind == "Binary"
            && matches!(self.source.node(sequence).op.as_str(), ".." | "..<" | "..=")
        {
            return Err(self.unsupported(id, "range membership"));
        }
        let type_membership =
            self.resolver.denotes_type(self.reference(left)) && self.is_array(sequence);
        if (constant || type_membership) && self.is_array(sequence) {
            let elems = self.source.group(sequence, "elems").to_vec();
            let lhs = self.render(left, None, true)?;
            let rhs = if type_membership {
                self.g("", id)
            } else {
                self.array(sequence, None, Some(left), true)?
            };
            let mut parts = vec![self.g("(", id)];
            if elems.is_empty() {
                parts.extend([
                    self.g("((", id),
                    lhs.clone(),
                    self.g(")==(", id),
                    lhs.clone(),
                    self.g(
                        if op == "in" {
                            ")) && false"
                        } else {
                            ")) || true"
                        },
                        id,
                    ),
                ]);
            }
            for (i, elem) in elems.iter().enumerate() {
                if i > 0 {
                    parts.push(self.g(if op == "in" { " || " } else { " && " }, id));
                }
                if type_membership {
                    parts.extend([
                        self.g("((", id),
                        lhs.clone(),
                        self.g(if op == "in" { ") == (" } else { ") != (" }, id),
                        self.render(*elem, None, true)?,
                        self.g("))", id),
                    ]);
                } else {
                    parts.extend([
                        self.g("((", id),
                        lhs.clone(),
                        self.g(if op == "in" { ") == ((" } else { ") != ((" }, id),
                        rhs.clone(),
                        self.g(format!(")[{i}]))"), id),
                    ]);
                }
            }
            parts.push(self.g(")", id));
            return Ok(Code::concat(parts).expansion(&self.source.path, self.source.node(id).span));
        }
        let typ = self.typ(right);
        if matches!(typ, Type::Set(_)) {
            let sets = self.module("sets");
            let a = self.render(left, None, constant)?;
            let b = self.render(right, None, constant)?;
            return Ok(Code::concat([
                self.g(
                    format!("{}({sets}.contains(", if op == "not_in" { "!" } else { "" }),
                    id,
                ),
                b,
                self.g(", ", id),
                a,
                self.g("))", id),
            ]));
        }
        if matches!(typ, Type::Map { .. }) {
            let a = self.render(left, None, constant)?;
            let b = self.render(right, None, constant)?;
            return Ok(Code::concat([a, self.g(format!(" {op} "), id), b]));
        }
        if !typ.is_sequence() && !self.is_array(sequence) {
            return Err(self.unsupported(id, "generic/bit-set/set membership dispatch"));
        }
        let helper = self.membership_helper(id);
        let a = self.render(left, None, constant)?;
        let b = if self.is_array(sequence) {
            self.array(sequence, None, Some(left), constant)?
        } else {
            self.render(right, None, constant)?
        };
        Ok(Code::concat([
            self.g(
                format!(
                    "{}{}(value=(",
                    if op == "not_in" { "(!" } else { "" },
                    helper
                ),
                id,
            ),
            a,
            self.g("), values=(", id),
            b,
            self.g(if op == "not_in" { ")))" } else { "))" }, id),
        ])
        .expansion(&self.source.path, self.source.node(id).span))
    }
    fn concatenate(&mut self, id: NodeId, expected: Option<Hint>) -> Result<Code> {
        fn flatten(s: &Source, id: NodeId, out: &mut Vec<NodeId>) {
            let mut n = id;
            while s.node(n).kind == "Paren" {
                if let Some(inner) = s.role(n, "expr") {
                    n = inner;
                } else {
                    break;
                }
            }
            if s.node(n).kind == "Binary" && s.node(n).op == "++" {
                for role in ["left", "right"] {
                    if let Some(child) = s.role(n, role) {
                        flatten(s, child, out);
                    }
                }
            } else {
                out.push(id);
            }
        }
        let mut values = vec![];
        flatten(self.source, id, &mut values);
        let mut kind = None;
        for value in &values {
            if self.resolver.runtime_operand(self.reference(*value)) {
                return Ok(self.error_token(*value,"Array concatenation ('++') requires compile-time constant operands; runtime variables and immutable bindings do not qualify."));
            }
            let typ = self.typ(*value);
            let mut base = &typ;
            while let Type::Nominal(_, inner) = base {
                base = inner;
            }
            let current=match base{Type::Array{..}=>"array",Type::Slice(_)=>"slice",Type::Unknown|Type::Generic=>continue,_=>return Ok(self.error_token(*value,"Array concatenation ('++') requires fixed arrays or constant slices; use + for constant strings."))};
            if kind.is_some_and(|kind| kind != current) {
                return Ok(self.error_token(*value,"Array concatenation ('++') cannot mix fixed arrays and slices; convert the operands to the same container kind explicitly."));
            }
            kind = Some(current);
        }
        self.meta = true;
        let mut parts = vec![self.g(format!("{}meta.concatenate(", self.prefix), id)];
        let mut hint = expected.map(|hint| Hint {
            semantic: hint.semantic.as_ref().and_then(|typ|typ.element().cloned()),
            code: Code::concat([
                self.g(format!("{}meta.type_elem_type(", self.prefix), id),
                hint.code,
                self.g(")", id),
            ]),
            element_only: true,
            plain_array: false,
        });
        if hint.is_none() {
            let inner = |mut value| {
                while self.source.node(value).kind == "Paren" {
                    let Some(next) = self.source.role(value, "expr") else {
                        break;
                    };
                    value = next;
                }
                value
            };
            if let Some(anchor) = values
                .iter()
                .find(|id| {
                    !self.is_array(inner(**id))
                        && matches!(
                            self.typ(**id),
                            Type::Array { .. } | Type::Slice(_) | Type::Nominal(..)
                        )
                })
                .or_else(|| {
                    values.iter().find(|id| {
                        self.is_array(inner(**id))
                            && !self.source.group(inner(**id), "elems").is_empty()
                    })
                })
            {
                let anchor_inner = inner(*anchor);
                let code = if self.is_array(anchor_inner) {
                    self.source
                        .group(anchor_inner, "elems")
                        .first()
                        .map(|id| self.render(*id, None, true))
                        .transpose()?
                } else {
                    Some(self.render(*anchor, None, true)?)
                };
                if let Some(code) = code {
                    self.default = true;
                    let typeof_ = self.native("type_of");
                    let code = if self.is_array(anchor_inner) {
                        Code::concat([
                            self.g(format!("{typeof_}({}default_value(", self.prefix), id),
                            code,
                            self.g("))", id),
                        ])
                    } else {
                        Code::concat([
                            self.g(format!("{}meta.type_elem_type({typeof_}(", self.prefix), id),
                            code,
                            self.g("))", id),
                        ])
                    };
                    hint = Some(Hint {
                        semantic: None,
                        code,
                        element_only: true,
                        plain_array: false,
                    });
                }
            }
        }
        for (i, value) in values.iter().enumerate() {
            if i > 0 {
                parts.push(self.g(", ", id));
            }
            let mut inner = *value;
            while self.source.node(inner).kind == "Paren" {
                let Some(next) = self.source.role(inner, "expr") else {
                    break;
                };
                inner = next;
            }
            parts.push(self.render(
                *value,
                if self.is_array(inner) {
                    hint.clone()
                } else {
                    None
                },
                true,
            )?);
        }
        parts.push(self.g(")", id));
        Ok(Code::concat(parts).expansion(&self.source.path, self.source.node(id).span))
    }
    fn power(&mut self, id: NodeId, constant: bool) -> Result<Code> {
        if constant {
            return Err(self.unsupported(id, "compile-time numeric power"));
        }
        let left = self.role(id, "left")?;
        let right = self.role(id, "right")?;
        let a = self.render(left, None, false)?;
        let b = self.render(right, None, false)?;
        let module = self.module("numeric");
        let typeof_ = self.native("type_of");
        self.default = true;
        let typ = Code::concat([
            self.g(
                format!("{typeof_}({}default_value(true ? (", self.prefix),
                id,
            ),
            a.clone(),
            self.g(") : (", id),
            b.clone(),
            self.g(")))", id),
        ]);
        Ok(Code::concat([
            self.g(format!("{module}.power(("), id),
            typ.clone(),
            self.g(")(", id),
            a,
            self.g("), (", id),
            typ,
            self.g(")(", id),
            b,
            self.g("))", id),
        ])
        .expansion(&self.source.path, self.source.node(id).span))
    }
    fn has_context(&self, id: NodeId) -> bool {
        if self.logical.globalizing>0{return true;}
        let owner = self
            .source
            .ancestors(id)
            .find(|id| self.source.node(*id).kind == "Procedure");
        let Some(owner) = owner else {
            return true;
        };
        let convention = self
            .source
            .role(owner, "type")
            .and_then(|id| self.source.node(id).data["calling_convention"].as_str())
            .unwrap_or("");
        matches!(convention, "" | "odin")
    }
    fn source_location(&self, id: NodeId) -> String {
        let owner = self
            .source
            .ancestors(id)
            .find(|parent| self.source.node(*parent).kind == "Procedure");
        let name = owner
            .and_then(|owner| self.source.node(owner).parent)
            .and_then(|declaration| self.source.group(declaration, "names").first())
            .map(|name| self.source.text(*name))
            .unwrap_or(if owner.is_some() {
                "<anonymous>"
            } else {
                "<global>"
            });
        let offset = self.source.node(id).span.start;
        let (line, _) = self.source.position(offset);
        let column = self.source.raw[..offset]
            .rsplit('\n')
            .next()
            .unwrap_or("")
            .chars()
            .count()
            + 1;
        format!(
            "{{{},{line},{column},{}}}",
            json!(self.source.path.to_string_lossy()),
            json!(name)
        )
    }
    fn or_panic(&mut self, id: NodeId) -> Result<Code> {
        let mut primary = self.role(id, "expr")?;
        while self.source.node(primary).kind == "Paren" {
            primary = self.role(primary, "expr")?;
        }
        let module = self.module("optional");
        let owner = self
            .source
            .ancestors(id)
            .find(|id| self.source.node(*id).kind == "Procedure");
        let procedure = owner
            .and_then(|id| self.source.node(id).parent)
            .and_then(|id| self.source.group(id, "names").first())
            .map(|id| self.source.text(*id))
            .unwrap_or(if owner.is_some() {
                "<anonymous>"
            } else {
                "<global>"
            });
        let offset = self.source.node(id).span.start;
        let (line, _) = self.source.position(offset);
        // Source_Code_Location follows the reference's rune column; LSP/source
        // map positions separately use UTF-16 columns.
        let column = self.source.raw[..offset]
            .rsplit('\n')
            .next()
            .unwrap_or("")
            .chars()
            .count()
            + 1;
        let location = format!(
            "{{{},{line},{column},{}}}",
            json!(self.source.path.to_string_lossy()),
            json!(procedure)
        );
        let summary = json!(format!(
            "unexpected failure\n  expression: {}\n  procedure: {procedure}",
            self.source.text(primary)
        ))
        .to_string();
        let context = self.has_context(id);
        let fail = if context {
            "failure"
        } else {
            "failure_contextless"
        };
        let extra = if context { ",false" } else { "" };
        let mut count = 1;
        if self.source.node(primary).kind == "Call" {
            let callee = self.role(primary, "callee")?;
            if self.source.text(callee) == "#load" {
                let query = self.render(primary, None, false)?;
                return Ok(Code::concat([
                    self.g("(", id),
                    query,
                    self.g(
                        format!(" or_else {module}.{fail}(false,{summary},{location}{extra}))"),
                        id,
                    ),
                ]));
            }
            if let Some(signature) = self.resolver.signature(self.reference(callee)) {
                count = self
                    .resolver
                    .fields(signature, "results")
                    .len()
                    .saturating_sub(1);
            } else if self.big_constructor_type(primary).is_none() && self.extrema_name(callee, &mut HashSet::new()).is_none()
                && !matches!(self.source.text(callee), "make" | "new" | "new_clone")
            {
                return Err(self.unsupported(
                    id,
                    "optional-result signatures from imported/overloaded procedures",
                ));
            }
        }
        let mut queries = vec![];
        if count > 1 {
            self.meta = true;
            self.querying += 1;
            let rendered = self.render(primary, None, false);
            self.querying -= 1;
            let rendered = rendered?;
            let type_of = self.native("type_of");
            for index in 0..count {
                queries.push(Code::concat([
                    self.g(
                        format!(
                            "{}meta.type_proc_return_type({type_of}({}meta.procedure_of(",
                            self.prefix, self.prefix
                        ),
                        id,
                    ),
                    rendered.clone(),
                    self.g(format!(")),{index})"), id),
                ]));
            }
        } else if count == 1 {
            self.querying += 1;
            let code = self.render(primary, None, false);
            self.querying -= 1;
            let code = code?;
            let typeof_ = self.native("type_of");
            queries.push(Code::concat([
                self.g(format!("{typeof_}("), id),
                code,
                self.g(
                    format!(" or_else {module}.failure_contextless(false,\"\",{{}}))"),
                    id,
                ),
            ]));
        }
        let prefix = format!(
            "{}optional_{}_",
            self.prefix,
            self.source.node(id).span.start
        );
        let result = if count == 0 {
            None
        } else if count == 1 {
            Some(String::new())
        } else {
            Some(format!("tuple:{count}"))
        };
        let (name,mut args)=self.closed(id,"or_panic",result.as_deref(),|this|{
            let expression=this.render(primary,None,false)?;
            let names=(0..count).map(|index|format!("{prefix}value_{index}")).collect::<Vec<_>>();
            let values=if names.is_empty(){String::new()}else{format!("{}, ",names.join(", "))};
            let mut code=vec![this.g(format!("{values}{prefix}status := "),id),expression,this.g(format!("\nif !{module}.succeeded({prefix}status) {{ {module}.{fail}({prefix}status,{summary},{location}{extra}) }}\n"),id)];
            if !names.is_empty(){code.push(this.g(format!("return {}",names.join(", ")),id));}Ok(Code::concat(code))
        })?;
        args.extend(queries);
        Ok(self.helper_call(id, &name, args))
    }
    fn closed<F>(
        &mut self,
        id: NodeId,
        flavor: &str,
        result: Option<&str>,
        build: F,
    ) -> Result<(String, Vec<Code>)>
    where
        F: FnOnce(&mut Self) -> Result<Code>,
    {
        let node = self.source.node(id);
        let span = node.span;
        let owner = self.helper_scope(id);
        let variant = if self.querying > 0 {
            if self.fstring_frames.is_empty() { "_query" } else { "_query_temporary" }
        } else if self.fstring_frames.is_empty() {
            ""
        } else {
            "_temporary"
        };
        let name = format!(
            "{}{flavor}_{}_{}{variant}",
            self.prefix, span.start, span.end
        );
        let key = (owner, self.prefix.clone(), name.clone());
        let mut variables = vec![];
        let mut seen = HashSet::new();
        for child in self.source.descendants(id) {
            let node = self.source.node(child);
            if node.kind != "Ident" {
                continue;
            }
            if let Some(parent) = node.parent {
                let parent_node = self.source.node(parent);
                if matches!(parent_node.kind.as_str(), "Declaration" | "Field" | "Range")
                    && self.source.group(parent, "names").contains(&child)
                    || matches!(parent_node.kind.as_str(), "Selector" | "FieldValue")
                        && self.source.role(parent, "field") == Some(child)
                {
                    continue;
                }
            }
            let Some(symbol) = self.resolver.symbol(self.reference(child)) else {
                continue;
            };
            let sym = &self.resolver.symbols[symbol];
            let source = &self.resolver.sources[sym.reference.file];
            if sym.scope.0 == 0
                || sym.constant
                || source.node(sym.reference.node).kind == "PolyType"
                || span.contains(source.node(sym.reference.node).span)
            {
                continue;
            }
            if sym.parameter
                && source
                    .group(sym.reference.node, "names")
                    .iter()
                    .any(|name| source.node(*name).kind == "PolyType")
            {
                continue;
            }
            if seen.insert(symbol) {
                variables.push((symbol, child));
            }
        }
        let mut params = vec![];
        let mut args = vec![];
        let mut aliases = HashMap::new();
        for (i, (symbol, child)) in variables.iter().enumerate() {
            let sym = &self.resolver.symbols[*symbol];
            let declaration = self.source.node(sym.reference.node);
            let byref = if declaration.kind == "Field" {
                sym.result
            } else if matches!(declaration.kind.as_str(), "Range" | "TypeSwitch") {
                self.source.node(sym.name_node).kind == "Unary"
            } else {
                true
            };
            let parameter = format!("{}capture_{i}", self.prefix);
            params.push(format!(
                "{parameter}: {}${}Capture_{i}",
                if byref { "^" } else { "" },
                self.prefix
            ));
            let argument = self.render(*child, None, false)?;
            args.push(if byref {
                Code::concat([self.g("&(", id), argument, self.g(")", id)])
            } else {
                argument
            });
            aliases.insert(
                *symbol,
                self.g(
                    format!("({parameter}{})", if byref { "^" } else { "" }),
                    *child,
                ),
            );
        }
        if let Some((pointer, _)) = self.fstring_frames.last() {
            let pointer = pointer.clone();
            let module = self.module("temporaries");
            params.push(format!("{pointer}: ^{module}.Scope"));
            args.push(self.g(pointer, id));
        }
        let returns = if let Some(result) = result {
            if let Some(count) = result
                .strip_prefix("tuple:")
                .and_then(|n| n.parse::<usize>().ok())
            {
                let fixed_status = if flavor == "fstring_optional_ok" {
                    Some(self.native("bool"))
                } else if flavor == "fstring_optional_allocator_error" {
                    let alias = format!("{}fstring_runtime", self.prefix);
                    self.modules.insert(alias.clone(), "base:runtime".into());
                    Some(format!("{alias}.Allocator_Error"))
                } else {
                    None
                };
                let mut types = (0..count - usize::from(fixed_status.is_some()))
                    .map(|i| format!("{}Result_{i}", self.prefix))
                    .collect::<Vec<_>>();
                params.extend(types.iter().map(|name| format!("${name}: typeid")));
                types.extend(fixed_status);
                format!(" -> ({})", types.join(", "))
            } else if result.is_empty() || result == "^" {
                params.push(format!("${}Result: typeid", self.prefix));
                format!(" -> {result}{}Result", self.prefix)
            } else {
                format!(" -> {result}")
            }
        } else {
            String::new()
        };
        if params.is_empty() {
            params.push(format!("${}Site: bool", self.prefix));
            args.push(self.g("true", id));
        }
        if !self.helpers.contains_key(&key) {
            self.helpers.insert(key.clone(), self.g("", id));
            self.captures.push(aliases);
            let mut cleanup = None;
            for child in std::iter::once(id).chain(self.source.descendants(id)) {
                if self.source.node(child).kind != "Call" {
                    continue;
                }
                if child != id
                    && self
                        .source
                        .ancestors(child)
                        .take_while(|parent| *parent != id)
                        .any(|parent| self.source.node(parent).kind == "Procedure")
                {
                    continue;
                }
                if self
                    .source
                    .ancestors(child)
                    .take_while(|parent| *parent != id)
                    .any(|parent| {
                        self.source.node(parent).kind == "Call"
                            && self.source.role(parent, "callee").is_some_and(|callee| {
                                matches!(
                                    self.source.text(callee),
                                    "type_of"
                                        | "typeid_of"
                                        | "type_info_of"
                                        | "size_of"
                                        | "align_of"
                                        | "offset_of"
                                ) && self.resolver.symbol(self.reference(callee)).is_none()
                            })
                    })
                {
                    continue;
                }
                let callee = self.role(child, "callee")?;
                if let Some(signature) = self.resolver.signature(self.reference(callee)) {
                    let source = &self.resolver.sources[signature.file];
                    let declaration = source
                        .node(signature.node)
                        .parent
                        .filter(|parent| source.node(*parent).kind == "Procedure")
                        .and_then(|parent| source.node(parent).parent);
                    if let Some(declaration) = declaration {
                        let deferred = source
                            .group(declaration, "attributes")
                            .iter()
                            .flat_map(|attribute| source.group(*attribute, "elems"))
                            .any(|item| {
                                source.node(*item).kind == "FieldValue"
                                    && source.role(*item, "field").is_some_and(|field| {
                                        source.text(field).starts_with("deferred_")
                                    })
                            });
                        if deferred {
                            cleanup = Some(child);
                            break;
                        }
                    }
                }
            }
            let body = if let Some(cleanup) = cleanup {
                let token=self.error_token(cleanup,"A procedure with automatic scope cleanup must be called directly in its owning scope. Assign its result to a named value first, then use that value in this expression.").finish().text;
                Ok(self.g(format!("#panic({})", json!(token)), cleanup))
            } else {
                build(self)
            };
            self.captures.pop();
            let body = body?;
            let private = if owner.0 == 0 {
                "@(private=\"file\")\n"
            } else {
                ""
            };
            let global_switch = !self.evaluation_mode
                && (flavor.starts_with("switch")
                    || flavor.starts_with("compile_switch")
                    || flavor.starts_with("comprehension")
                    || flavor.starts_with("big_global"))
                && !self
                    .source
                    .ancestors(id)
                    .any(|parent| self.source.node(parent).kind == "Procedure");
            let convention = if self.has_context(id) && !global_switch {
                "odin"
            } else {
                "contextless"
            };
            let header = self.g(
                format!(
                    "\n{private}{name} :: proc \"{convention}\"({}){returns} {}{{\n",
                    params.join(", "),
                    if flavor == "extrema_optional" || flavor == "fstring_optional_ok" {
                        "#optional_ok "
                    } else if flavor == "fstring_optional_allocator_error" {
                        "#optional_allocator_error "
                    } else {
                        ""
                    }
                ),
                id,
            );
            self.helpers
                .insert(key, Code::concat([header, body, self.g("\n}\n", id)]));
        }
        Ok((name, args))
    }
    fn helper_call(&self, id: NodeId, name: &str, args: Vec<Code>) -> Code {
        let mut code = vec![self.g(format!("{name}("), id)];
        for (i, arg) in args.into_iter().enumerate() {
            if i > 0 {
                code.push(self.g(", ", id));
            }
            code.push(arg);
        }
        code.push(self.g(")", id));
        Code::concat(code).expansion(&self.source.path, self.source.node(id).span)
    }
    fn interpolation(&mut self, id: NodeId) -> Result<Code> {
        use crate::formatting::{parse, WIDTH_MARKER};
        let parts = self.source.node(id).data["parts"]
            .as_array()
            .cloned()
            .ok_or_else(|| Error::new("interpolation parts omitted"))?;
        let mut fields = self.source.group(id, "args").to_vec().into_iter();
        let mut parsed = vec![];
        let manual = self.source.node(id).data["manual_lifetime"] == true;
        let quote = self.source.raw.as_bytes()
            [self.source.node(id).span.start + if manual { 2 } else { 1 }]
            as char;
        for part in parts {
            if part["literal"] == true {
                let span = Span::new(
                    part["start"].as_u64().unwrap_or(0) as usize,
                    part["end"].as_u64().unwrap_or(0) as usize,
                );
                let literal = self
                    .source
                    .slice(span)
                    .replace("{{", "{")
                    .replace("}}", "}");
                parsed.push((None, Some(format!("{quote}{literal}{quote}")), None, vec![]));
                continue;
            }
            let field = fields
                .next()
                .ok_or_else(|| Error::new("interpolation field omitted"))?;
            let start = part["format_start"].as_i64().unwrap_or(-1);
            let end = part["format_end"].as_i64().unwrap_or(-1);
            let mut spec = if start >= 0 && end >= start {
                self.source
                    .slice(Span::new(start as usize, end as usize))
                    .to_string()
            } else {
                String::new()
            };
            let slots = part["format_exprs"].as_array().cloned().unwrap_or_default();
            let mut widths = vec![];
            for _ in &slots {
                widths.push(
                    fields
                        .next()
                        .ok_or_else(|| Error::new("interpolation width omitted"))?,
                );
            }
            for span in slots.iter().rev() {
                let span = Span::from_wire(span)
                    .ok_or_else(|| Error::new("invalid interpolation width span"))?;
                spec.replace_range(
                    span.start - 1 - start as usize..span.end + 1 - start as usize,
                    WIDTH_MARKER,
                );
            }
            let spec = parse(&spec).map_err(|error| {
                Error::at(
                    self.source,
                    if start < 0 {
                        self.source.node(field).span
                    } else {
                        Span::new(start as usize, end as usize)
                    },
                    error.message,
                )
            })?;
            parsed.push((Some(field), None, Some(spec), widths));
        }
        let formatting = self.module("format");
        let prefix = format!("{}format_{}_", self.prefix, self.source.node(id).span.start);
        let (name,args)=self.closed(id,"format",Some("string"),|this|{
            let builder=format!("{prefix}builder");
            let mut code=vec![this.g(format!("{builder}: {formatting}.Buffer = ---\n{formatting}.buffer_init(&{builder})\ndefer {formatting}.buffer_destroy(&{builder})\n"),id)];
            for (index,(field,literal,spec,widths)) in parsed.iter().enumerate(){
                if let Some(literal)=literal{code.push(this.g(format!("{formatting}.buffer_write_string(&{builder}, {literal})\n"),id));continue;}
                let field=field.ok_or_else(||Error::new("missing interpolation field"))?;
                let temporary=this.temporary_evaluate(field,&mut code,None)?;
                let mut value=temporary.value.clone();
                if !widths.is_empty() {
                    let snapshot=format!("{prefix}snapshot_{index}");
                    code.push(this.g(format!("{snapshot}:={formatting}.compiler_capture_width_value({value},{})\n",temporary.owned),field));
                    value=snapshot;
                }
                let width_name=format!("{prefix}width_{index}");
                for width in widths{
                    let evaluated=this.temporary_evaluate(*width,&mut code,None)?;
                    code.push(this.g(format!("{width_name} := {formatting}.normalize_width({})\n",evaluated.value),*width));
                    this.temporary_release(&evaluated,&mut code,false)?;
                }
                let options=spec.as_ref().ok_or_else(||Error::new("missing interpolation format"))?.options(&width_name);
                if this.checked_mode && this.querying==0 {
                    let query=this.native("type_of");
                    code.push(this.g(format!("{formatting}.validate_pure_value({query}({value}))\n"),field));
                }
                code.push(this.g(format!("{formatting}.write_spec(&{builder}.builder,{value},{options})\n"),field));
                if !widths.is_empty() {code.push(this.g(format!("{formatting}.compiler_release_width_value({value},{})\n",temporary.owned),field));}
                this.temporary_release(&temporary,&mut code,false)?;
            }
            code.push(this.g(format!("return {formatting}.buffer_owned_text(&{builder})"),id));Ok(Code::concat(code))
        })?;
        Ok(self.helper_call(id, &name, args))
    }
    fn anonymous_string(&self, mut id: NodeId) -> bool {
        while self.source.node(id).kind == "Paren" {
            if let Some(expr) = self.source.role(id, "expr") {
                id = expr;
            } else {
                break;
            }
        }
        self.source.node(id).kind == "Interpolation"
            && self.source.node(id).data["manual_lifetime"] != true
    }
    fn discover_fstrings(&mut self) {
        const EXPRESSIONS: &[&str] = &[
            "Call",
            "FieldValue",
            "Paren",
            "Interpolation",
            "Binary",
            "Unary",
            "Ternary",
            "WhenExpr",
            "Index",
            "Slice",
            "Selector",
            "Deref",
            "Compound",
            "SwitchExpr",
            "CompileSwitchExpr",
            "SwitchResults",
            "Cast",
            "AutoCast",
            "OrElse",
            "OrPanic",
        ];
        for (i, node) in self.source.nodes.iter().enumerate() {
            let id = NodeId(i);
            if node.kind != "Interpolation" || node.data["manual_lifetime"] == true {
                continue;
            }
            let mut argument = None;
            for parent in self.source.ancestors(id) {
                let p = self.source.node(parent);
                if p.kind == "Call" {
                    let callee = self.source.role(parent, "callee");
                    let query = callee.is_some_and(|callee| {
                        matches!(
                            self.source.text(callee),
                            "type_of"
                                | "typeid_of"
                                | "size_of"
                                | "align_of"
                                | "offset_of"
                                | "type_info_of"
                                | "#assert"
                                | "#config"
                                | "#defined"
                        ) && self.resolver.symbol(self.reference(callee)).is_none()
                    });
                    if query {
                        break;
                    }
                    if callee
                        .is_some_and(|callee| self.resolver.denotes_type(self.reference(callee)))
                        && self.big_constructor_type(parent).is_none()
                    {
                        continue;
                    }
                    if self
                        .source
                        .group(parent, "args")
                        .iter()
                        .any(|arg| self.source.node(*arg).span.contains(node.span))
                    {
                        argument = Some(parent);
                        break;
                    }
                }
                if matches!(
                    p.kind.as_str(),
                    "Interpolation"
                        | "Procedure"
                        | "Declaration"
                        | "Return"
                        | "Assignment"
                        | "Field"
                ) {
                    break;
                }
            }
            if let Some(call) = argument {
                let mut root = call;
                let switches = self
                    .source
                    .ancestors(call)
                    .filter(|parent| {
                        matches!(
                            self.source.node(*parent).kind.as_str(),
                            "SwitchExpr" | "CompileSwitchExpr"
                        )
                    })
                    .map(|parent| self.source.node(parent).span)
                    .collect::<Vec<_>>();
                for parent in self.source.ancestors(call) {
                    if EXPRESSIONS.contains(&self.source.node(parent).kind.as_str()) {
                        root = parent;
                    } else if matches!(
                        self.source.node(parent).kind.as_str(),
                        "Case" | "Block" | "ExpressionStatement"
                    ) && switches
                        .iter()
                        .any(|span| span.contains(self.source.node(parent).span))
                    {
                        continue;
                    } else {
                        break;
                    }
                }
                self.fstring_plans.entry(root).or_default().insert(id);
            }
        }
    }
    fn fstring_expression(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        targets: HashSet<NodeId>,
    ) -> Result<Code> {
        let statement = !self.temporary_discarding.contains(&id) && self
            .source
            .node(id)
            .parent
            .is_some_and(|p| self.source.node(p).kind == "ExpressionStatement");
        let mut signature = None;
        if !statement && self.source.node(id).kind == "Call" {
            let callee = self.role(id, "callee")?;
            let plans = self
                .resolver
                .call_plans(self.reference(id), &Default::default());
            signature = if let [plan] = plans.as_slice() {
                Some(plan.signature)
            } else {
                self.resolver.signature(self.reference(callee))
            };
        }
        let logical_optional=self.big_constructor_type(id).is_some()&&self.source.group(id,"args").iter().any(|arg|self.typ(self.source.role(*arg,"value").unwrap_or(*arg)).is_string());
        let count = if statement {
            0
        } else {
            signature
                .map(|signature| self.resolver.fields(signature, "results").len())
                .unwrap_or(if logical_optional{2}else{1})
        };
        let optional = signature
            .map(|signature| {
                &self.resolver.sources[signature.file]
                    .node(signature.node)
                    .data
            })
            .filter(|data| data["optional_ok"] == true || data["optional_allocator_error"] == true)
            .map(|data| {
                if data["optional_allocator_error"] == true {
                    "fstring_optional_allocator_error"
                } else {
                    "fstring_optional_ok"
                }
            }).or_else(||(!statement&&logical_optional).then_some("fstring_optional_ok"));
        self.fstring_busy.insert(id);
        self.querying += 1;
        let query = if count == 0 {
            Ok(vec![])
        } else if count > 1 {
            self.meta = true;
            let type_of = self.native("type_of");
            self.render(id, None, false).map(|code| {
                (0..count - usize::from(optional.is_some()))
                    .map(|index| {
                        Code::concat([
                            self.g(
                                format!(
                                    "{}meta.type_proc_return_type({type_of}({}meta.procedure_of(",
                                    self.prefix, self.prefix
                                ),
                                id,
                            ),
                            code.clone(),
                            self.g(format!(")),{index})"), id),
                        ])
                    })
                    .collect()
            })
        } else if let Some(hint) = expected {
            Ok(vec![hint.code])
        } else {
            self.default = true;
            let typeof_ = self.native("type_of");
            self.render(id, None, false).map(|code| {
                vec![Code::concat([
                    self.g(format!("{typeof_}({}default_value(", self.prefix), id),
                    code,
                    self.g("))", id),
                ])]
            })
        };
        self.querying -= 1;
        let query = match query {
            Ok(value) => value,
            Err(error) => {
                self.fstring_busy.remove(&id);
                return Err(error);
            }
        };
        let module = self.module("temporaries");
        let prefix = format!(
            "{}fstrings_{}_",
            self.prefix,
            self.source.node(id).span.start
        );
        let pointer = format!("{}fstring_scope", self.prefix);
        let result = if count == 0 {
            None
        } else if count == 1 {
            Some(String::new())
        } else {
            Some(format!("tuple:{count}"))
        };
        let (name,mut args)=self.closed(id,optional.unwrap_or("fstring_expression"),result.as_deref(),|this|{
            let code=this.g(format!("{prefix}entries:[{}]{module}.Entry\n{prefix}scope: {module}.Scope = {{entries={prefix}entries[:]}}\n{pointer} := &{prefix}scope\ndefer {module}.destroy({pointer})\n",targets.len()),id);
            this.fstring_frames.push((pointer.clone(),targets));let expression=this.render(id,None,false);this.fstring_frames.pop();
            Ok(Code::concat([code,this.g(if count==0{""}else{"return "},id),expression?]))
        })?;
        self.fstring_busy.remove(&id);
        args.extend(query);
        Ok(self.helper_call(id, &name, args))
    }
    fn helper_declarations(&self, owner: NodeId) -> Vec<Code> {
        self.helpers
            .range(
                (owner, self.prefix.clone(), String::new())
                    ..(owner, format!("{}\0", self.prefix), String::new()),
            )
            .map(|(_, code)| code.clone())
            .collect()
    }

    fn helper_scope(&self, id: NodeId) -> NodeId {
        self.source
            .ancestors(id)
            .find(|id| {
                self.source.node(*id).kind == "Block"
                    && !self
                        .source
                        .ancestors(*id)
                        .any(|parent| self.source.node(parent).kind == "Comprehension")
                    && self.source.node(*id).parent.is_none_or(|p| {
                        !matches!(self.source.node(p).kind.as_str(), "Switch" | "TypeSwitch")
                    })
            })
            .unwrap_or(NodeId(0))
    }
}
pub fn unquote(text: &str) -> Result<String> {
    if text.starts_with('`') && text.ends_with('`') {
        return Ok(text[1..text.len() - 1].into());
    }
    serde_json::from_str(text).map_err(|_| Error::new(format!("unsupported path literal: {text}")))
}
