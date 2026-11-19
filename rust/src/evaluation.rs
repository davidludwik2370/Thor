//! Compile-time helper dependency extraction. Keep reference decision order;
//! helper compilation/scheduling is connected separately from this graph pass.
use crate::{
    ast::{NodeId, Span},
    borroweval::Evaluator as FlowEvaluator,
    borrowflow::Arena,
    diagnostic::{Error, Result},
    lower::unquote,
    semantics::{scope, Ref, Resolver, Type},
    specialization::TypeCode,
};
use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    path::PathBuf,
};
#[path = "evaluationschedule.rs"]
mod schedule;
pub use schedule::Scheduler;
#[path = "evaluationbridge.rs"]
mod bridge;
#[path = "evaluationeligibility.rs"]
mod eligibility;
pub(crate) use eligibility::{approved_format, approved_library, explicit_allocator, library_member, library_origin, primitive};
use eligibility::source_location_dependent;
/// Borrowed evaluator state used by one isolated helper. Native helper creation
/// snapshots the selected inputs; the scheduler retains its own registries.
pub struct Context<'a> {
    pub batch: &'a [Ref],
    pub results: &'a BTreeMap<Ref, serde_json::Value>,
    pub skips: &'a BTreeMap<Ref, bool>,
    pub cases: &'a crate::specialization::Cases,
    pub bindings: &'a crate::specialization::Bindings,
    pub frames: &'a [(Ref, crate::specialization::Bindings)],
    pub cache: Option<&'a std::cell::RefCell<crate::evaluationcache::EvaluationCache>>,
    pub cache_misses: &'a BTreeSet<String>,
}
static EMPTY_RESULTS: BTreeMap<Ref, serde_json::Value> = BTreeMap::new();
static EMPTY_SKIPS: BTreeMap<Ref, bool> = BTreeMap::new();
static EMPTY_CASES: crate::specialization::Cases = BTreeMap::new();
static EMPTY_BINDINGS: crate::specialization::Bindings = BTreeMap::new();
static EMPTY_CACHE_MISSES: BTreeSet<String> = BTreeSet::new();
impl Default for Context<'_> {
    fn default() -> Self {
        Self {
            batch: &[],
            results: &EMPTY_RESULTS,
            skips: &EMPTY_SKIPS,
            cases: &EMPTY_CASES,
            bindings: &EMPTY_BINDINGS,
            frames: &[],
            cache: None,
            cache_misses: &EMPTY_CACHE_MISSES,
        }
    }
}

#[derive(Clone, Default)]
pub struct FileView {
    pub keep: BTreeSet<NodeId>,
    pub values: BTreeMap<NodeId, String>,
    pub aliases: BTreeSet<String>,
    pub imports: BTreeMap<String, String>,
    pub bridges: Vec<Bridge>,
    pub request: Option<Request>,
    pub entry: Option<EntryRequest>,
}
#[derive(Clone)]
pub struct Bridge {
    pub scope: NodeId,
    pub origin: NodeId,
    pub name: String,
    pub code: String,
}
#[derive(Clone)]
pub struct Request {
    pub expression: NodeId,
    pub batch: Vec<NodeId>,
    pub locals: BTreeMap<NodeId, Vec<NodeId>>,
    pub mode: Mode,
    pub prelude: String,
    pub bridge: Option<RequestBridge>,
}
#[derive(Clone)]
pub struct RequestBridge {
    pub name: String,
    pub parameters: String,
    pub type_names: Vec<String>,
}
#[derive(Clone)]
pub struct EntryRequest {
    pub origin: NodeId,
    pub locals: BTreeMap<NodeId, Vec<NodeId>>,
    pub prelude: String,
    pub call: String,
}
#[derive(Clone, Copy, Default)]
pub enum Mode {
    #[default]
    Value,
    Condition,
    Case(usize),
}
impl FileView {
    pub fn retains(&self, source: &crate::ast::Source, node: NodeId) -> bool {
        node == NodeId(0)
            || source.role(NodeId(0), "package") == Some(node)
            || self.keep.iter().any(|kept| {
                *kept == node
                    || source.is_descendant(node, *kept)
                    || source.is_descendant(*kept, node)
            })
    }
}
#[derive(Clone, Default)]
pub struct Plan {
    pub files: BTreeMap<PathBuf, FileView>,
    pub roots: Vec<Ref>,
    pub entries: BTreeSet<Ref>,
    pub requests: Vec<Ref>,
    pub bindings: crate::specialization::Bindings,
    pub entry_file: usize,
}
impl Plan {
    pub(crate) fn emits(&self, source: &crate::ast::Source, node: NodeId) -> bool {
        let Some(view) = self.files.get(&source.path) else {
            return false;
        };
        view.retains(source, node)
            || view
                .request
                .as_ref()
                .map(|request| &request.locals)
                .into_iter()
                .chain(view.entry.as_ref().map(|entry| &entry.locals))
                .flat_map(|scopes| scopes.values())
                .flatten()
                .any(|declaration| {
                    source
                        .node(*declaration)
                        .span
                        .contains(source.node(node).span)
                })
    }
    pub(crate) fn emits_type(&self, typ: &Type, sources: &[crate::ast::Source]) -> bool {
        let present = |owner: Ref| {
            let source = &sources[owner.file];
            if compiler_support(source) {
                return true;
            }
            let node = source
                .node(owner.node)
                .parent
                .filter(|node| source.node(*node).kind == "Declaration")
                .unwrap_or(owner.node);
            self.emits(source, node)
        };
        match typ {
            Type::Nominal(owner, inner) => present(*owner) && self.emits_type(inner, sources),
            Type::Struct(owner) | Type::Enum(owner) => present(*owner),
            Type::Record(owner, bindings) | Type::Union(owner, bindings) => {
                present(*owner)
                    && bindings
                        .iter()
                        .all(|(_, typ)| self.emits_type(typ, sources))
            }
            Type::Array { elem, .. }
            | Type::Fixed { elem, .. }
            | Type::Pointer(elem)
            | Type::Slice(elem)
            | Type::Dynamic(elem) => self.emits_type(elem, sources),
            _ => true,
        }
    }
    /// Isolate one request using original node identities. Local declarations
    /// from the surrounding runtime body are reintroduced by the helper main.
    pub fn build(r: &mut Resolver<'_>, expression: Ref) -> Result<Self> {
        Self::with_results(r, expression, &BTreeMap::new())
    }
    pub fn with_results(
        r: &mut Resolver<'_>,
        expression: Ref,
        results: &BTreeMap<Ref, serde_json::Value>,
    ) -> Result<Self> {
        Self::with_mode(r, expression, results, &BTreeMap::new(), Mode::Value)
    }
    pub(crate) fn with_mode(
        r: &mut Resolver<'_>,
        expression: Ref,
        results: &BTreeMap<Ref, serde_json::Value>,
        skips: &BTreeMap<Ref, bool>,
        mode: Mode,
    ) -> Result<Self> {
        Self::with_context(
            r,
            expression,
            &Context {
                results,
                skips,
                ..Context::default()
            },
            mode,
        )
    }
    pub(crate) fn with_context(
        r: &mut Resolver<'_>,
        expression: Ref,
        context: &Context<'_>,
        mode: Mode,
    ) -> Result<Self> {
        let results = context.results;
        let skips = context.skips;
        let roots = if context.batch.is_empty() {
            request_roots(&r.sources[expression.file], expression, mode)
        } else {
            context.batch.to_vec()
        };
        let definitions = context
            .bindings
            .values()
            .filter_map(|binding| Extraction::type_definition(&binding.typ))
            .filter_map(|owner| {
                r.sources[owner.file]
                    .node(owner.node)
                    .parent
                    .filter(|node| r.sources[owner.file].node(*node).kind == "Declaration")
                    .map(|node| Ref {
                        file: owner.file,
                        node,
                    })
            });
        let graph = Extraction::build(
            r,
            &roots.iter().copied().chain(definitions).collect::<Vec<_>>(),
        )?;
        if let Some(nested) = graph.nested_runs.iter().find(|node| {
            !results.contains_key(node)
                && !skips.contains_key(node)
                && !context.cases.contains_key(node)
        }) {
            let source = &r.sources[nested.file];
            return Err(Error::unsupported(
                source,
                source.node(nested.node).span,
                "nested compile-time evaluation scheduling",
            ));
        }
        let mut plan = Self {
            bindings: context.bindings.clone(),
            ..Self::default()
        };
        for source in r.sources {
            plan.files.insert(source.path.clone(), FileView::default());
        }
        let mut locals: BTreeMap<usize, BTreeMap<NodeId, Vec<NodeId>>> = BTreeMap::new();
        for kept in &graph.keep {
            let source = &r.sources[kept.file];
            let owner = source
                .ancestors(kept.node)
                .find(|id| source.node(*id).kind == "Procedure")
                .map(|node| Ref {
                    file: kept.file,
                    node,
                });
            if owner.is_none_or(|owner| graph.procedures.contains(&owner)) {
                plan.files
                    .get_mut(&source.path)
                    .unwrap()
                    .keep
                    .insert(kept.node);
            } else if source.node(kept.node).kind == "Declaration" {
                locals
                    .entry(kept.file)
                    .or_default()
                    .entry(scope(source, kept.node))
                    .or_default()
                    .push(kept.node);
            }
        }
        for (file, scopes) in &mut locals {
            for declarations in scopes.values_mut() {
                declarations.sort_by_key(|id| r.sources[*file].node(*id).span.start);
            }
        }
        for import in &graph.imports {
            plan.files
                .get_mut(&r.sources[import.file].path)
                .unwrap()
                .keep
                .insert(import.node);
        }
        for (file, alias) in &graph.aliases {
            plan.files
                .get_mut(&r.sources[*file].path)
                .unwrap()
                .aliases
                .insert(alias.clone());
        }
        for (query, code) in &graph.queries {
            plan.files
                .get_mut(&r.sources[query.file].path)
                .unwrap()
                .values
                .insert(query.node, code.clone());
        }
        for ((file, alias), directory) in &graph.helper_imports {
            let source = &r.sources[*file];
            let path = dependency_path(source, directory);
            plan.files
                .get_mut(&source.path)
                .unwrap()
                .imports
                .insert(alias.clone(), path);
        }
        for (callee, target) in &graph.calls {
            let caller = &r.sources[callee.file];
            let code = match target {
                CallTarget::Builtin(name) if name.starts_with('#') => name.clone(),
                CallTarget::Builtin(name) => {
                    let alias = format!("{}evaluation_builtin", caller.prefix);
                    plan.files
                        .get_mut(&caller.path)
                        .unwrap()
                        .imports
                        .insert(alias.clone(), "base:builtin".into());
                    format!("{alias}.{name}")
                }
                CallTarget::Procedure(target) => {
                    let source = &r.sources[target.file];
                    let declaration = source
                        .ancestors(target.node)
                        .find(|id| source.node(*id).kind == "Declaration")
                        .ok_or_else(|| {
                            Error::at(
                                source,
                                source.node(target.node).span,
                                "Compile-time execution requires a named direct procedure call.",
                            )
                        })?;
                    let name = source
                        .group(declaration, "names")
                        .first()
                        .map(|id| source.text(*id))
                        .ok_or_else(|| Error::new("evaluation target omitted its name"))?;
                    if source.path.parent() == caller.path.parent() {
                        name.into()
                    } else {
                        let bridge = format!(
                            "{}run_target_{}",
                            source.prefix,
                            source.node(declaration).span.start
                        );
                        let view = plan.files.get_mut(&source.path).unwrap();
                        if !view.bridges.iter().any(|item| item.name == bridge) {
                            view.bridges.push(Bridge {
                                scope: scope(source, declaration),
                                origin: declaration,
                                name: bridge.clone(),
                                code: format!("\n{bridge} :: proc{{{name}}}\n"),
                            });
                        }
                        let alias =
                            format!("{}evaluation_dependency_{}", caller.prefix, target.file);
                        let path = dependency_path(caller, source.path.parent().unwrap());
                        plan.files
                            .get_mut(&caller.path)
                            .unwrap()
                            .imports
                            .insert(alias.clone(), path);
                        format!("{alias}.{bridge}")
                    }
                }
            };
            plan.files
                .get_mut(&caller.path)
                .unwrap()
                .values
                .insert(callee.node, code);
        }
        for (callee, (path, name)) in &graph.external {
            let source = &r.sources[callee.file];
            let alias = format!(
                "{}original_library_{}",
                source.prefix,
                source.node(callee.node).span.start
            );
            let view = plan.files.get_mut(&source.path).unwrap();
            view.imports.insert(alias.clone(), path.clone());
            view.values.insert(callee.node, format!("{alias}.{name}"));
        }
        plan.configure_request(r, expression, context, mode, locals)?;
        plan.requests = roots;
        Ok(plan)
    }
}
pub(crate) fn request_roots(source: &crate::ast::Source, expression: Ref, mode: Mode) -> Vec<Ref> {
    if let Mode::Case(index) = mode {
        let case = &source.node(expression.node).data["cases"][index];
        let mut roots = case["matches"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Span::from_wire)
            .filter_map(|span| source.at(span, expression.node))
            .map(|node| Ref {
                file: expression.file,
                node,
            })
            .collect::<Vec<_>>();
        roots.extend(source.role(expression.node, "selector").map(|node| Ref {
            file: expression.file,
            node,
        }));
        roots
    } else {
        let mut roots = vec![expression];
        if source.node(expression.node).kind=="Binary" && source.node(expression.node).op=="**" {
            if let Some(node)=source.value_annotation(expression.node) {roots.push(Ref{node,..expression});}
        }
        if source.node(expression.node).kind == "Comprehension" {
            if let Some(node) = source.comprehension_annotation(expression.node) {
                roots.push(Ref { node, ..expression });
            }
        }
        roots
    }
}
pub(crate) fn dependency_path(source: &crate::ast::Source, directory: &std::path::Path) -> String {
    if let Some((_, path)) = source
        .collection_roots
        .iter()
        .filter_map(|(name, root)| {
            directory.strip_prefix(root).ok().map(|suffix| {
                (
                    root.components().count(),
                    format!("{name}:{}", suffix.to_string_lossy().replace('\\', "/")),
                )
            })
        })
        .max_by_key(|(depth, _)| *depth)
    {
        return path;
    }
    let caller = source
        .path
        .parent()
        .unwrap()
        .components()
        .collect::<Vec<_>>();
    let target = directory.components().collect::<Vec<_>>();
    let common = caller
        .iter()
        .zip(&target)
        .take_while(|(a, b)| a == b)
        .count();
    let mut relative = PathBuf::new();
    for _ in common..caller.len() {
        relative.push("..");
    }
    for part in &target[common..] {
        relative.push(part.as_os_str());
    }
    if relative.as_os_str().is_empty() {
        ".".into()
    } else {
        relative.to_string_lossy().replace('\\', "/")
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CallTarget {
    Builtin(String),
    Procedure(Ref),
}
#[derive(Default)]
pub struct Closure {
    pub keep: BTreeSet<Ref>,
    pub imports: BTreeSet<Ref>,
    pub aliases: BTreeSet<(usize, String)>,
    pub procedures: BTreeSet<Ref>,
    pub queries: BTreeMap<Ref, String>,
    pub calls: BTreeMap<Ref, CallTarget>,
    pub external: BTreeMap<Ref, (String, String)>,
    pub helper_imports: BTreeMap<(usize, String), PathBuf>,
    pub nested_runs: BTreeSet<Ref>,
}
pub struct Extraction<'a, 'r> {
    r: &'r mut Resolver<'a>,
    graph: Closure,
    visited: HashSet<Ref>,
}
impl<'a, 'r> Extraction<'a, 'r> {
    pub fn build(r: &'r mut Resolver<'a>, roots: &[Ref]) -> Result<Closure> {
        let mut this = Self {
            r,
            graph: Closure::default(),
            visited: HashSet::new(),
        };
        for root in roots {
            if this.kind(*root) == "Declaration" {
                this.declaration(*root)?;
            } else {
                this.visit(*root)?;
            }
        }
        for (file, name) in this.graph.aliases.clone() {
            if let Some(import) = this.selection_import(file, &name) {
                this.graph.imports.insert(import);
            }
        }
        Ok(this.graph)
    }
    fn kind(&self, node: Ref) -> &str {
        &self.r.sources[node.file].node(node.node).kind
    }
    fn role(&self, node: Ref, name: &str) -> Option<Ref> {
        self.r.sources[node.file]
            .role(node.node, name)
            .map(|node_id| Ref {
                file: node.file,
                node: node_id,
            })
    }
    fn children(&self, node: Ref) -> Vec<Ref> {
        self.r.sources[node.file]
            .node(node.node)
            .children
            .iter()
            .map(|node_id| Ref {
                file: node.file,
                node: *node_id,
            })
            .collect()
    }
    fn group(&self, node: Ref, name: &str) -> Vec<Ref> {
        self.r.sources[node.file]
            .group(node.node, name)
            .iter()
            .map(|node_id| Ref {
                file: node.file,
                node: *node_id,
            })
            .collect()
    }
    fn owner(&self, node: Ref) -> Option<Ref> {
        self.r.sources[node.file]
            .ancestors(node.node)
            .find(|node_id| self.r.sources[node.file].node(*node_id).kind == "Procedure")
            .map(|node_id| Ref {
                file: node.file,
                node: node_id,
            })
    }
    fn builtin(&mut self, node: Ref) -> Option<String> {
        crate::purity::builtin_call(self.r, node)
    }
    fn io_call(&mut self, node: Ref) -> bool {
        FlowEvaluator {
            resolver: self.r,
            arena: &mut Arena::default(),
        }
        .io_call(node)
    }
    fn import_alias(&self, import: Ref) -> Option<String> {
        let source = &self.r.sources[import.file];
        let node = source.node(import.node);
        let name = source.names.get(node.name);
        if !name.is_empty() {
            return Some(name.into());
        }
        unquote(&node.op)
            .ok()?
            .rsplit(['/', ':'])
            .next()
            .map(str::to_string)
    }
    fn selection_import(&self, file: usize, name: &str) -> Option<Ref> {
        let source = &self.r.sources[file];
        let selected = source.selections.iter().find(|selection| {
            if selection.names.iter().any(|item| item == name) {
                return true;
            }
            if !selection.wildcard {
                return false;
            }
            let Ok(path) = unquote(&selection.path) else {
                return false;
            };
            let directory = if let Some((collection, suffix)) = path.split_once(':') {
                source
                    .collection_roots
                    .get(collection)
                    .map(|root| root.join(suffix))
            } else {
                source.path.parent().map(|root| root.join(path))
            };
            let directory = directory.and_then(|path| crate::project::canonicalize(path).ok());
            self.r.symbols.iter().any(|symbol| {
                symbol.name == name
                    && symbol.scope == NodeId(0)
                    && self.r.sources[symbol.reference.file].path.parent() == directory.as_deref()
            })
        })?;
        source
            .nodes
            .iter()
            .enumerate()
            .rev()
            .find(|(_, node)| {
                node.kind == "Import"
                    && node.span.start <= selected.start
                    && node.op == selected.path
            })
            .map(|(index, _)| Ref {
                file,
                node: NodeId(index),
            })
    }
    fn query(&mut self, node: Ref) -> Option<(String, Type)> {
        if self.kind(node) != "Call" {
            return None;
        }
        let result_query=crate::resulttypes::is_query(self.r,node);
        let builtin = if result_query {"type_of".to_string()} else {self.builtin(node)?};
        if !["type_of", "size_of", "align_of", "offset_of"].contains(&builtin.as_str()) {
            return None;
        }
        let args = self.group(node, "args");
        if args.len() != if builtin == "offset_of" { 2 } else { 1 } {
            return None;
        }
        let typ = self.r.specialized_type(if result_query{node}else{args[0]}, &Default::default());
        if typ.is_unknown() {
            return None;
        }
        let mut context = TypeCode::new(node.file, Some(node));
        context.allow_imports = result_query;
        let mut code = context.render(&typ, self.r).ok()?;
        for (alias, directory) in context.imports {
            self.graph
                .helper_imports
                .insert((node.file, alias), directory);
        }
        if builtin != "type_of" {
            code = format!(
                "{builtin}({code}{})",
                args.iter()
                    .skip(1)
                    .map(|arg| format!(",{}", self.r.sources[arg.file].text(arg.node)))
                    .collect::<String>()
            );
        }
        Some((code, typ))
    }
    fn type_definition(typ: &Type) -> Option<Ref> {
        match typ {
            Type::Nominal(owner, _)
            | Type::Struct(owner)
            | Type::Record(owner, _)
            | Type::Enum(owner)
            | Type::Union(owner, _) => Some(*owner),
            Type::Array { elem, .. }
            | Type::Slice(elem)
            | Type::Pointer(elem)
            | Type::Dynamic(elem) => Self::type_definition(elem),
            _ => None,
        }
    }
    fn query_definitions(typ:&Type,definitions:&mut Vec<Ref>) {
        if let Some(owner)=Self::type_definition(typ) {definitions.push(owner);}
        match typ {
            Type::Anonymous(fields)=>for (_,typ) in fields {Self::query_definitions(typ,definitions)},
            Type::Array{elem,..}|Type::Slice(elem)|Type::Dynamic(elem)|Type::Pointer(elem)|Type::Fixed{elem,..}=>Self::query_definitions(elem,definitions),
            _=>{},
        }
    }
    fn declaration(&mut self, node: Ref) -> Result<()> {
        let source = &self.r.sources[node.file];
        if compiler_support(source) {
            return Ok(());
        }
        let data = source.node(node.node);
        if data.kind == "Declaration"
            && (data.mutable || data.immutable)
            && scope(source, node.node) == NodeId(0)
        {
            return Ok(());
        }
        if !self.visited.insert(node) {
            return Ok(());
        }
        self.graph.keep.insert(node);
        for id in source.ancestors(node.node).collect::<Vec<_>>() {
            let ancestor = Ref {
                file: node.file,
                node: id,
            };
            match self.kind(ancestor) {
                "When" => {
                    if let Some(cond) = self.role(ancestor, "cond") {
                        self.graph.keep.insert(cond);
                        self.visit(cond)?;
                    }
                }
                "ForeignBlock" => self.graph.keep.extend(self.group(ancestor, "attributes")),
                "CompileSwitch" | "CompileSwitchExpr" => {
                    if let Some(selector) = self.role(ancestor, "selector") {
                        self.graph.keep.insert(selector);
                        self.visit(selector)?;
                    }
                    let source = &self.r.sources[node.file];
                    let matches = source.node(id).data["cases"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .flat_map(|case| case["matches"].as_array().into_iter().flatten())
                        .filter_map(Span::from_wire)
                        .filter_map(|span| source.at(span, id))
                        .map(|node_id| Ref {
                            file: node.file,
                            node: node_id,
                        })
                        .collect::<Vec<_>>();
                    for matched in matches {
                        self.graph.keep.insert(matched);
                        self.visit(matched)?;
                    }
                }
                _ => {}
            }
        }
        self.visit(node)
    }
    fn conditional_alternatives(&self, symbol: usize) -> Vec<Ref> {
        let symbol = &self.r.symbols[symbol];
        self.r
            .symbols
            .iter()
            .filter(|candidate| {
                candidate.name == symbol.name
                    && candidate.reference != symbol.reference
                    && ((candidate.reference.file == symbol.reference.file
                        && candidate.scope == symbol.scope)
                        || candidate.scope == NodeId(0)
                            && symbol.scope == NodeId(0)
                            && self.r.sources[candidate.reference.file].path.parent()
                                == self.r.sources[symbol.reference.file].path.parent())
            })
            .filter(|candidate| {
                self.r.sources[candidate.reference.file]
                    .ancestors(candidate.reference.node)
                    .any(|node| {
                        ["When", "CompileSwitch"].contains(
                            &self.r.sources[candidate.reference.file]
                                .node(node)
                                .kind
                                .as_str(),
                        )
                    })
            })
            .map(|candidate| candidate.reference)
            .collect()
    }
    fn reference(&self, node: Ref) -> bool {
        let source = &self.r.sources[node.file];
        let Some(parent) = source.node(node.node).parent else {
            return true;
        };
        !(["Declaration", "Field", "Range"].contains(&source.node(parent).kind.as_str())
            && source.group(parent, "names").contains(&node.node)
            || ["Selector", "FieldValue"].contains(&source.node(parent).kind.as_str())
                && source.role(parent, "field") == Some(node.node))
    }
    fn contract_body(&mut self, proc: Ref) -> bool {
        if let Some(declaration) = self.r.sources[proc.file].node(proc.node).parent {
            if self
                .r
                .attributes(Ref {
                    file: proc.file,
                    node: declaration,
                })
                .iter()
                .any(|(name, _)| name.starts_with("deferred_"))
            {
                return true;
            }
        }
        let Some(signature) = self.role(proc, "type") else {
            return false;
        };
        let Some(params) = self.role(signature, "params") else {
            return false;
        };
        self.children(params).into_iter().any(|field| {
            self.r.sources[field.file].node(field.node).data["using_field"] == true
                && self.role(field, "type").is_some_and(|annotation| {
                    self.r
                        .specialized_type(annotation, &Default::default())
                        .is_unknown()
                })
        })
    }
    fn visit(&mut self, node: Ref) -> Result<()> {
        if let Some((code, typ)) = self.query(node) {
            self.graph.queries.insert(node, code);
            let mut definitions=vec![];
            Self::query_definitions(&typ,&mut definitions);
            definitions.sort();definitions.dedup();
            for owner in definitions {
                if let Some(declaration) = self.r.sources[owner.file]
                    .node(owner.node)
                    .parent
                    .filter(|parent| self.r.sources[owner.file].node(*parent).kind == "Declaration")
                {
                    self.declaration(Ref {
                        file: owner.file,
                        node: declaration,
                    })?;
                } else if self.kind(owner).ends_with("Type") {
                    self.visit(owner)?;
                }
            }
            return Ok(());
        }
        if self.kind(node) == "Run" {
            self.graph.nested_runs.insert(node);
            if let Some(expr) = self.role(node, "expr") {
                self.visit(expr)?;
            }
            return Ok(());
        }
        if self.kind(node) == "Call" {
            let callee = self
                .role(node, "callee")
                .ok_or_else(|| Error::new("evaluation call omitted callee"))?;
            if self.io_call(node) {
                for arg in self.group(node, "args") {
                    self.visit(arg)?;
                }
                return Ok(());
            }
            if let Some(message) = self.r.private_call_reason(callee) {
                return Err(Error::at(
                    &self.r.sources[callee.file],
                    self.r.sources[callee.file].node(callee.node).span,
                    message,
                ));
            }
            if let Some(name) = self.builtin(node) {
                self.graph.calls.insert(callee, CallTarget::Builtin(name));
                for arg in self.group(node, "args") {
                    self.visit(arg)?;
                }
                return Ok(());
            }
            if let Some(library) = approved_library(self.r, callee) {
                if crate::parallelcompile::is_call(self.r,node) {
                    // The scheduler is a reviewed operation. Keep its ordinary
                    // Thor facade for native lowering without treating its
                    // thread/allocator implementation as user pure code.
                    let directory=std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("runtime/parallel");
                    for (file,source) in self.r.sources.iter().enumerate() {
                        if source.thor && source.path.parent()==Some(directory.as_path()) {
                            self.graph.keep.insert(Ref{file,node:NodeId(0)});
                        }
                    }
                }
                self.graph.external.insert(callee, library);
                for arg in self.group(node, "args") {
                    self.visit(arg)?;
                }
                return Ok(());
            }
            let plans = self.r.call_plans(node, &Default::default());
            if let [plan] = plans.as_slice() {
                let target = plan.procedure;
                if self.kind(target) == "Procedure" && self.role(target, "body").is_some() {
                    self.graph
                        .calls
                        .insert(callee, CallTarget::Procedure(target));
                    self.graph.procedures.insert(target);
                    if let Some(declaration) = self.r.sources[target.file].node(target.node).parent
                    {
                        self.declaration(Ref {
                            file: target.file,
                            node: declaration,
                        })?;
                    }
                    if let Some(symbol) = self.r.symbol(callee) {
                        for alternative in self.conditional_alternatives(symbol) {
                            self.declaration(alternative)?;
                        }
                    }
                    for arg in self.group(node, "args") {
                        self.visit(arg)?;
                    }
                    return Ok(());
                }
            }
        }
        if self.kind(node) == "Procedure" && self.role(node, "body").is_some() {
            self.graph.procedures.insert(node);
            if self.contract_body(node) {
                for child in self.children(node) {
                    if Some(child) != self.role(node, "body") {
                        self.visit(child)?;
                    }
                }
                return Ok(());
            }
        }
        if self.kind(node) == "Ident" && self.reference(node) {
            let source = &self.r.sources[node.file];
            let name = source.text(node.node);
            let local = self.r.lookup(node, name).is_some_and(|index| {
                self.r.sources[self.r.symbols[index].reference.file]
                    .path
                    .parent()
                    == source.path.parent()
            });
            if !local && self.selection_import(node.file, name).is_some() {
                self.graph.aliases.insert((node.file, name.into()));
            }
            if self.r.lookup(node, name).is_none() {
                for (index, _) in source
                    .nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, import)| import.kind == "Import")
                {
                    let import = Ref {
                        file: node.file,
                        node: NodeId(index),
                    };
                    if self.import_alias(import).as_deref() == Some(name) {
                        self.graph.imports.insert(import);
                    }
                }
            }
            if let Some(symbol) = self.r.symbol(node) {
                let declaration = self.r.symbols[symbol].reference;
                if declaration != node
                    && !self.r.sources[node.file]
                        .ancestors(node.node)
                        .any(|parent| {
                            declaration
                                == Ref {
                                    file: node.file,
                                    node: parent,
                                }
                        })
                {
                    if self
                        .owner(declaration)
                        .is_none_or(|owner| !self.graph.procedures.contains(&owner))
                    {
                        self.declaration(declaration)?;
                    }
                    for alternative in self.conditional_alternatives(symbol) {
                        self.declaration(alternative)?;
                    }
                }
            }
        }
        if self.kind(node) == "Selector" {
            if let Some(symbol) = self.r.symbol(node) {
                self.declaration(self.r.symbols[symbol].reference)?;
                for alternative in self.conditional_alternatives(symbol) {
                    self.declaration(alternative)?;
                }
            }
        }
        if self.kind(node) == "Call" {
            if let Some(callee) = self
                .role(node, "callee")
                .filter(|callee| !self.r.denotes_type(*callee))
            {
                for target in self.r.candidates(callee) {
                    if self.kind(target) == "Procedure" && self.role(target, "body").is_some() {
                        self.graph.procedures.insert(target);
                        if let Some(declaration) =
                            self.r.sources[target.file].node(target.node).parent
                        {
                            if self.r.sources[target.file].node(declaration).kind == "Declaration" {
                                self.declaration(Ref {
                                    file: target.file,
                                    node: declaration,
                                })?;
                            }
                        }
                    }
                }
            }
        }
        for child in self.children(node) {
            self.visit(child)?;
        }
        Ok(())
    }
}
pub(crate) fn compiler_support(source: &crate::ast::Source) -> bool {
    source.info["odin_root"].as_str().is_some_and(|root| {
        ["runtime", "builtin", "intrinsics"].iter().any(|package| {
            source
                .path
                .starts_with(std::path::Path::new(root).join("base").join(package))
        })
    })
}
