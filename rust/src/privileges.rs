//! Slice permissions are properties of reference paths, not of scalar copies.
//! Procedure summaries describe parameter-relative effects and returned aliases.
//! An arena preserves pointer cycles without Rust ownership cycles.
use crate::{
    ast::NodeId,
    semantics::{Ref, Resolver, Substitutions, Type, SharedType, TypeInput},
};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
#[path = "privilegesummaries.rs"]
mod cached;
pub(crate) use cached::SummaryCache;
#[path = "privilegequeries.rs"]
mod queries;
#[path = "privilegecow.rs"]
mod cow;
use cow::Shared;
#[path = "privilegework.rs"]
mod work;
type V = usize;
type C = usize;
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
enum Step {
    Field(String),
    Item,
    Deref,
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct Path {
    // Location tracking also applies to writable arguments. Qualification is
    // enabled only when a caller supplies borrowed data, or checks a contract.
    qualified: bool,
    index: usize,
    steps: Shared<Vec<Step>>,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
enum Aspect {
    Data,
    Storage,
    Inline,
    All,
    // Resolve a particular subtree against the caller's argument graph.
    References,
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct Origin {
    path: Path,
    aspect: Aspect,
    seed: Option<Ref>,
}
type Origins = Shared<BTreeSet<Origin>>;

// A whole-parameter reference covers its descendant data paths. Keep storage
// requirements and concrete readonly seeds, which have independent meanings.
fn compact_origins(origins: &mut Origins) -> bool {
    if origins.len() < 8 { return false; }
    let whole = origins.iter().filter(|origin| origin.seed.is_none()
        && origin.aspect == Aspect::All && origin.path.steps.is_empty())
        .map(|origin| (origin.path.qualified, origin.path.index)).collect::<HashSet<_>>();
    let references = origins.iter().filter(|origin| origin.seed.is_none()
        && origin.aspect == Aspect::References).map(|origin| origin.path.clone())
        .collect::<HashSet<_>>();
    if whole.is_empty() && references.is_empty() { return false; }
    let before = origins.len();
    origins.retain(|origin| {
        if origin.seed.is_some() || !matches!(origin.aspect, Aspect::Data | Aspect::References) {
            return true;
        }
        if whole.contains(&(origin.path.qualified, origin.path.index)) { return false; }
        let mut path = origin.path.clone();
        if origin.aspect == Aspect::Data && references.contains(&path) { return false; }
        while path.steps.pop().is_some() {
            if references.contains(&path) { return false; }
        }
        true
    });
    origins.len() != before
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct Value {
    typ: SharedType,
    data: Origins,
    storage: Origins,
    fields: Shared<BTreeMap<String, V>>,
    element: Option<V>,
    target: Option<V>,
    base: Option<V>,
    path: Option<Path>,
    locations: Shared<BTreeSet<Path>>,
}
impl Value {
    fn new(typ: SharedType) -> Self {
        Self {
            typ,
            data: Origins::new(),
            storage: Origins::new(),
            fields: Shared::new(),
            element: None,
            target: None,
            base: None,
            path: None,
            locations: Shared::new(),
        }
    }
}
#[derive(Clone, Debug)]
pub struct Issue {
    pub operation: Ref,
    pub seed: Ref,
    pub message: String,
    pub related: Vec<Ref>,
}
#[derive(Clone, Default)]
pub struct Facts {
    pub issues: HashMap<Ref, Issue>,
    pub mutating: BTreeSet<Ref>,
    pub verified_parameters: BTreeSet<Ref>,
    pub readonly: BTreeSet<Ref>,
    pub invalid: HashMap<Ref, String>,
    pub pure_faults: BTreeMap<Ref, Vec<Issue>>,
    pub unresolved: BTreeMap<Ref, Ref>,
    pub editor_info: Vec<serde_json::Value>,
}
#[derive(Clone, PartialEq, Eq)]
struct Effect {
    operation: Ref,
    unresolved: Option<Ref>,
    deleting: bool,
    reason: String,
    related: Vec<Ref>,
    changes_storage: bool,
}
impl Effect {
    fn covers(&self, other: &Self) -> bool {
        (!other.changes_storage || self.changes_storage)
            && (!self.deleting || other.deleting)
            && (self.unresolved.is_none() || self.unresolved == other.unresolved)
    }
}
#[derive(Clone, PartialEq, Eq)]
struct GraphState {
    roots: Vec<(usize, V)>,
    results: Vec<V>,
    stores: Vec<(Path, V)>,
    values: Vec<GraphValue>,
    effects: Vec<(Origin, Effect)>,
}
#[derive(Clone, PartialEq, Eq)]
struct GraphValue {
    typ: SharedType,
    data: Origins,
    storage: Origins,
    locations: Shared<BTreeSet<Path>>,
    path: Option<Path>,
    fields: Vec<(String, V)>,
    children: [Option<V>; 3],
}
struct Context {
    proc: Option<Ref>,
    checked: bool,
    callers: BTreeSet<C>,
    bindings: Substitutions,
    env: HashMap<usize, V>,
    effects: Shared<BTreeMap<Origin, Effect>>,
    stores: BTreeMap<Path, V>,
    results: Vec<V>,
    expressions: HashMap<Ref, Vec<V>>,
    optional: HashSet<Ref>,
    active: bool,
    controls: Vec<Ref>,
    named: Vec<Option<usize>>,
    callees: BTreeSet<C>,
    complete: bool,
    cacheable: bool,
    event_start: usize,
    event_end: usize,
    cached_events: Option<std::sync::Arc<[cached::FactEvent]>>,
    revision: u64,
}
impl Context {
    fn new(proc: Option<Ref>, bindings: Substitutions) -> Self {
        Self {
            proc,
            checked: false,
            callers: BTreeSet::new(),
            bindings,
            env: HashMap::new(),
            effects: Shared::new(),
            stores: BTreeMap::new(),
            results: vec![],
            expressions: HashMap::new(),
            optional: HashSet::new(),
            active: false,
            controls: vec![],
            named: vec![],
            callees: BTreeSet::new(), complete: false, cacheable: true,
            event_start: 0, event_end: 0,
            cached_events: None,
            revision: 0,
        }
    }
}
pub struct Privileges<'a, 'r> {
    r: &'r mut Resolver<'a>,
    values: work::Values,
    contexts: Vec<Context>,
    summaries: HashMap<SummaryKey, C>,
    recursive_pending: BTreeSet<C>,
    globals: HashMap<usize, V>,
    global_active: HashSet<usize>,
    explicit: HashSet<Ref>,
    explicit_ro: HashSet<Ref>,
    reader_targets: HashSet<Ref>,
    facts: Facts,
    changed: bool,
    depth: usize,
    flags: Vec<String>,
    pure_depth: usize,
    uncertain: Option<Ref>,
    deleting: bool,
    discovering: bool,
    opaque_readonly: bool,
    library_names: std::cell::RefCell<HashMap<Ref, Option<(String, String)>>>,
    reuse: Option<SummaryCache>,
    fact_events: Vec<cached::FactEvent>,
    type_properties: HashMap<(crate::semantics::TypeId, queries::Property), bool>,
    view_sources: HashMap<Ref, bool>,
    view_bodies: HashMap<Ref, (bool, Vec<Ref>)>,
    global_view_names: HashSet<String>,
    work: HashMap<(C, Ref), work::Dependencies>,
    work_weight: usize,
}
#[derive(Clone, PartialEq, Eq, Hash)]
struct SummaryKey {
    procedure: Ref,
    bindings: Vec<(String, Type)>,
    contract: bool,
    checked: bool,
    opaque_readonly: bool,
}
impl<'a, 'r> Privileges<'a, 'r> {
    fn new(r: &'r mut Resolver<'a>, flags: &[String], discovering: bool) -> Self {
        let reader_targets=crate::caller_metadata::reader_targets(r);
        let global_view_names = r.symbols.iter().filter(|symbol| {
            symbol.immutable && r.sources[symbol.reference.file].node(symbol.scope).kind == "File"
        }).map(|symbol| symbol.name.clone()).collect();
        Self {
            r,
            values: work::Values::default(),
            contexts: vec![],
            summaries: HashMap::new(),
            recursive_pending: BTreeSet::new(),
            globals: HashMap::new(),
            global_active: HashSet::new(),
            explicit: HashSet::new(),
            explicit_ro: HashSet::new(),
            reader_targets,
            facts: Facts::default(),
            changed: false,
            depth: 0,
            flags: flags.to_vec(),
            pure_depth: 0,
            uncertain: None,
            deleting: false,
            discovering,
            opaque_readonly: true,
            library_names: std::cell::RefCell::new(HashMap::new()),
            reuse: None, fact_events: vec![],
            type_properties: HashMap::new(),
            view_sources: HashMap::new(),
            view_bodies: HashMap::new(),
            global_view_names,
            work: HashMap::new(), work_weight: 0,
        }
    }
    pub fn analyze(r: &'r mut Resolver<'a>, flags: &[String]) -> Facts {
        Self::analyze_editor(r, flags, false)
    }
    pub fn analyze_editor(r: &'r mut Resolver<'a>, flags: &[String], editor: bool) -> Facts {
        Self::analyze_project(r, flags, editor, None)
    }
    pub(crate) fn analyze_project(
        r: &'r mut Resolver<'a>,
        flags: &[String],
        editor: bool,
        evaluation: Option<&crate::evaluation::Plan>,
    ) -> Facts {
        Self::analyze_with_dependencies(r, flags, editor, evaluation, false, None)
    }
    pub(crate) fn discover(
        r: &'r mut Resolver<'a>, flags: &[String], editor: bool,
        evaluation: Option<&crate::evaluation::Plan>,
    ) -> Facts {
        Self::analyze_with_dependencies(r, flags, editor, evaluation, true, None)
    }
    pub(crate) fn discover_cached(r: &'r mut Resolver<'a>, flags: &[String], editor: bool,
        cache: &mut SummaryCache) -> Facts {
        Self::analyze_with_dependencies(r, flags, editor, None, true, Some(cache))
    }
    fn analyze_with_dependencies(
        r: &'r mut Resolver<'a>, flags: &[String], editor: bool,
        evaluation: Option<&crate::evaluation::Plan>, discovering: bool,
        mut cache: Option<&mut SummaryCache>,
    ) -> Facts {
        let _phase = crate::profile::span("analysis.slice_permissions");
        let enabled = editor
            || r.sources.iter().any(|s| {
                s.nodes
                    .iter()
                    .any(|n| n.immutable || n.data["pure"] == true)
                    || s.info["ro_parameters"].as_array().is_some_and(|x| !x.is_empty())
                    || s.info["mut_parameters"]
                        .as_array()
                        .is_some_and(|x| !x.is_empty())
            });
        if !enabled {
            return Facts::default();
        }
        let previous_queries = r.begin_symbol_queries();
        let mut this = Self::new(r, flags, discovering);
        this.reuse = cache.as_deref().cloned();
        this.annotations();
        this.opaque_readonly = this.has_readonly_views();
        let procedures = this
            .r
            .sources
            .iter()
            .enumerate()
            .flat_map(|(file, s)| {
                s.nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, n)| n.kind == "Procedure")
                    .map(move |(i, _)| Ref {
                        file,
                        node: NodeId(i),
                    })
            })
            .collect::<Vec<_>>();
        for proc in procedures {
            if discovering && this.r.has_demanded_packages() {
                crate::profile::count("permissions.dependency_retries", 1);
                if let Some(cache) = cache.as_deref_mut() { this.harvest(cache); }
                this.r.end_symbol_queries(previous_queries);
                return Facts::default();
            }
            let source = this.s(proc);
            if evaluation.is_some_and(|plan| {
                !plan.emits(source, proc.node)
                    && !plan.requests.iter().any(|request| {
                        request.file == proc.file && source.is_descendant(proc.node, request.node)
                    })
            }) {
                continue;
            }
            if !this.r.active_definition(proc) {
                continue;
            }
            if this.s(proc).shared_native {
                continue;
            }
            let toolkit = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .join("runtime");
            if this.s(proc).path.starts_with(&toolkit) && !this.reader_targets.contains(&proc)
                && !this
                    .s(proc)
                    .descendants(proc.node)
                    .any(|id| this.s(proc).node(id).immutable)
            {
                continue;
            }
            let generic = this.role(proc, "type").is_some_and(|sig| {
                this.s(sig)
                    .descendants(sig.node)
                    .any(|id| this.s(sig).node(id).kind == "PolyType")
            });
            if editor || !generic || this.reader_targets.contains(&proc) || this.s(proc).node(proc.node).data["pure"] == true {
                crate::profile::count("permissions.root_candidates", 1);
                if !editor && !this.needs_root(proc) {
                    crate::profile::count("permissions.roots_skipped", 1);
                    continue;
                }
                this.pure_depth = usize::from(this.s(proc).node(proc.node).data["pure"] == true);
                this.summary(proc, Substitutions::new(), None, editor || this.reader_targets.contains(&proc));
                this.pure_depth = 0;
                if discovering && this.r.has_demanded_packages() {
                    crate::profile::event("permissions.incomplete", || serde_json::json!({
                        "file":this.s(proc).path,"offset":this.s(proc).node(proc.node).span.start,
                        "packages":this.r.demanded_packages()
                    }));
                }
            }
        }
        if discovering && this.r.has_demanded_packages() {
            crate::profile::count("permissions.dependency_retries", 1);
            if let Some(cache) = cache { this.harvest(cache); }
            this.r.end_symbol_queries(previous_queries);
            return Facts::default();
        }
        // Only incomplete recursive callees can invalidate an already
        // completed caller. Match the reference work queue instead of
        // repeatedly walking every unrelated SDK body.
        let mut visits = HashMap::<C, usize>::new();
        while let Some(c) = this.recursive_pending.pop_first() {
            let count = visits.entry(c).or_default();
            if *count >= 24 {
                continue;
            }
            *count += 1;
            let Some(proc) = this.contexts[c].proc else {
                continue;
            };
            let Some(body) = this.role(proc, "body") else {
                continue;
            };
            let before = this.state(c);
            this.pure_depth = usize::from(this.contexts[c].checked);
            this.body_pass(body, c, true);
            this.pure_depth = 0;
            if !this.state_matches(c, &before) {
                this.contexts[c].revision += 1;
                if visits[&c] == 24 {
                    this.conservative_summary(
                        c,
                        "verify a complex recursive slice reference graph",
                    );
                }
                this.recursive_pending.insert(c);
                this.recursive_pending
                    .extend(this.contexts[c].callers.iter().copied());
            }
        }
        for c in 0..this.contexts.len() {
            let Some(proc) = this.contexts[c].proc else {
                continue;
            };
            let sig = this.role(proc, "type").unwrap_or(proc);
            if this.contexts[c].complete {
                this.facts.verified_parameters.extend(this.params(sig).into_iter().map(|(name,_)|name));
            }
            for origin in this.contexts[c].effects.keys() {
                if let Some((name, _)) = this.params(sig).get(origin.path.index) {
                    this.facts.mutating.insert(*name);
                }
            }
            if this.s(proc).node(proc.node).data["pure"] != true {
                continue;
            }
            let sig = this.role(proc, "type").unwrap_or(proc);
            let params = this.params(sig);
            for (origin, effect) in this.contexts[c].effects.clone() {
                if origin.seed.is_some() || !effect.changes_storage {
                    continue;
                }
                if let Some(conditional) = effect.unresolved {
                    this.facts.unresolved.insert(effect.operation, conditional);
                    continue;
                }
                if let Some((name, field)) = params.get(origin.path.index) {
                    let bindings = this.contexts[c].bindings.clone();
                    let typ = this
                        .role(*field, "type")
                        .or_else(|| this.role(*field, "default"))
                        .map(|annotation| this.r.specialized_type(annotation, &bindings))
                        .unwrap_or(Type::Unknown);
                    if this.type_property(&typ, queries::Property::Unresolved) {
                        // A generic value copy may be inline storage or a
                        // borrowed header. Until native conditional ownership
                        // guards are emitted, this is a migration gap, not proof
                        // that a valid array copy mutates its input.
                        this.facts.unresolved.insert(effect.operation, *name);
                        continue;
                    }
                    this.facts.pure_faults.entry(proc).or_default().push(Issue {
                        operation: effect.operation,
                        seed: *name,
                        message:
                            "Pure code cannot mutate or delete storage borrowed from an input."
                                .into(),
                        related: effect.related,
                    });
                }
            }
        }
        if editor {
            this.facts.editor_info = this.editor_info();
        }
        this.r.end_symbol_queries(previous_queries);
        this.facts
    }
    fn editor_info(&mut self) -> Vec<serde_json::Value> {
        use serde_json::json;
        let mut parameters = self
            .explicit
            .iter()
            .map(|reference| (*reference, true))
            .collect::<BTreeMap<_, _>>();
        let mut files = self
            .explicit
            .iter()
            .map(|r| r.file)
            .collect::<BTreeSet<_>>();
        for c in 0..self.contexts.len() {
            let Some(proc) = self.contexts[c].proc else {
                continue;
            };
            files.insert(proc.file);
            let signature = self.role(proc, "type").unwrap_or(proc);
            let params = self.params(signature);
            let bindings = self.contexts[c].bindings.clone();
            let mut ready = true;
            for (name, field) in &params {
                let typ = self
                    .r
                    .symbol(*name)
                    .and_then(|symbol| self.contexts[c].env.get(&symbol))
                    .map(|v| self.values[*v].typ.owned())
                    .unwrap_or(Type::Unknown);
                if self.type_property(&typ, queries::Property::Unresolved) {
                    ready = false;
                }
                if self
                    .group(*field, "names")
                    .iter()
                    .any(|n| self.kind(*n) == "PolyType")
                {
                    let key = self.text(*name).trim_start_matches('$').to_string();
                    if bindings
                        .get(&key)
                        .is_none_or(|typ| matches!(typ, Type::Unknown | Type::Generic))
                    {
                        ready = false;
                    }
                }
            }
            let mut views = Vec::new();
            for (name, field) in &params {
                let typ = self
                    .role(*field, "type")
                    .map(|r| self.specialized_type(r, &bindings))
                    .or_else(|| {
                        self.r
                            .symbol(*name)
                            .and_then(|symbol| self.contexts[c].env.get(&symbol))
                            .map(|v| self.values[*v].typ.owned())
                    })
                    .unwrap_or(Type::Unknown);
                let value = self.value(typ);
                views.push(self.freeze(value, *name));
            }
            for (origin, effect) in self.contexts[c].effects.clone() {
                let Some((name, field)) = params.get(origin.path.index) else {
                    continue;
                };
                let typ = self
                    .role(*field, "type")
                    .map(|r| self.specialized_type(r, &bindings))
                    .unwrap_or(Type::Unknown);
                if !matches!(base_type(&typ), Type::Slice(_) | Type::Soa(_)) {
                    continue;
                }
                if !ready {
                    if !origin.path.steps.is_empty() || origin.aspect != Aspect::Data {
                        continue;
                    }
                    let operation = effect.operation;
                    let direct_write = effect.reason == "modify slice elements"
                        && self.kind(operation) == "Index"
                        && self.role(operation, "expr").is_some_and(|expr| {
                            matches!(base_type(&self.typ(expr, c)), Type::Slice(_) | Type::Soa(_))
                        });
                    let direct_alias = effect.reason.starts_with("create a writable slice alias")
                        && matches!(base_type(&self.typ(operation, c)), Type::Slice(_) | Type::Soa(_));
                    if !direct_write && !direct_alias {
                        continue;
                    }
                }
                if self.resolve(&origin, &views).is_empty() {
                    continue;
                }
                parameters.insert(*name, self.explicit.contains(name));
            }
        }
        self.r.sources.iter().enumerate().filter(|(file,source)| source.thor || files.contains(file))
            .map(|(file, source)| {
                let hash=source.raw.as_bytes().iter().fold(14695981039346656037u64,|h,b|(h^u64::from(*b)).wrapping_mul(1099511628211));
                let rows=parameters.iter().filter(|(r,_)|r.file==file).map(|(r,explicit)| {
                    let node=source.node(r.node);json!({"file":source.path,"offset":node.span.start,"end":node.span.end,"name":source.text(r.node),"explicit":explicit})
                }).collect::<Vec<_>>();
                let readonly=self.facts.readonly.iter().filter(|r|r.file==file).map(|r|source.node(r.node).span.start).collect::<BTreeSet<_>>();
                json!({"file":source.path,"source_hash":hash,"parameters":rows,"readonly_access":readonly})
            }).collect()
    }
    fn s(&self, r: Ref) -> &'a crate::ast::Source {
        &self.r.sources[r.file]
    }
    fn register(v: V, ids: &mut HashMap<V, V>, queue: &mut Vec<V>) -> V {
        if let Some(id) = ids.get(&v) { return *id; }
        let id = queue.len();
        queue.push(v);
        ids.insert(v, id);
        id
    }
    fn state_roots(&self, c: C) -> (GraphState, HashMap<V, V>, Vec<V>) {
        let context = &self.contexts[c];
        let mut ids = HashMap::new();
        let mut queue = vec![];
        let mut env = context
            .env
            .iter()
            .map(|(symbol, v)| (*symbol, *v))
            .collect::<Vec<_>>();
        env.sort_unstable_by_key(|(symbol, _)| *symbol);
        let roots = env
            .into_iter()
            .map(|(symbol, v)| (symbol, Self::register(v, &mut ids, &mut queue)))
            .collect();
        let results = context
            .results
            .iter()
            .map(|v| Self::register(*v, &mut ids, &mut queue))
            .collect();
        let stores = context
            .stores
            .iter()
            .map(|(path, v)| (path.clone(), Self::register(*v, &mut ids, &mut queue)))
            .collect();
        let state = GraphState {
            roots, results, stores, values: vec![],
            effects: context.effects.iter().map(|(origin, effect)| (origin.clone(), effect.clone())).collect(),
        };
        (state, ids, queue)
    }
    fn state(&self, c: C) -> GraphState {
        let _phase = crate::profile::span("permissions.snapshot");
        crate::profile::count("permissions.graph_snapshots", 1);
        let (mut state, mut ids, mut queue) = self.state_roots(c);
        let mut values = vec![];
        let mut cursor = 0;
        while cursor < queue.len() {
            let v = &self.values[queue[cursor]];
            cursor += 1;
            let fields = v
                .fields
                .iter()
                .map(|(name, v)| (name.clone(), Self::register(*v, &mut ids, &mut queue)))
                .collect();
            let children =
                [v.element, v.target, v.base].map(|v| v.map(|v| Self::register(v, &mut ids, &mut queue)));
            values.push(GraphValue {
                typ: v.typ.clone(),
                data: v.data.clone(),
                storage: v.storage.clone(),
                locations: v.locations.clone(),
                path: v.path.clone(),
                fields,
                children,
            });
        }
        state.values = values;
        state
    }
    /// Compare the current canonical graph directly with the saved snapshot.
    /// Equality remains exact; no hashes or mutation counters stand in for it.
    fn state_matches(&self, c: C, expected: &GraphState) -> bool {
        let _phase = crate::profile::span("permissions.compare");
        crate::profile::count("permissions.graph_comparisons", 1);
        let (state, mut ids, mut queue) = self.state_roots(c);
        if state.roots != expected.roots || state.results != expected.results
            || state.stores != expected.stores || state.effects != expected.effects {
            return false;
        }
        let mut cursor = 0;
        while cursor < queue.len() {
            let Some(old) = expected.values.get(cursor) else { return false; };
            let value = &self.values[queue[cursor]];
            cursor += 1;
            if value.typ != old.typ || value.data != old.data || value.storage != old.storage
                || value.locations != old.locations || value.path != old.path
                || value.fields.len() != old.fields.len() {
                return false;
            }
            for ((name, child), (old_name, old_child)) in value.fields.iter().zip(&old.fields) {
                if name != old_name || Self::register(*child, &mut ids, &mut queue) != *old_child {
                    return false;
                }
            }
            let children = [value.element, value.target, value.base]
                .map(|child| child.map(|child| Self::register(child, &mut ids, &mut queue)));
            if children != old.children { return false; }
        }
        cursor == expected.values.len()
    }
    fn role(&self, r: Ref, role: &str) -> Option<Ref> {
        self.s(r)
            .role(r.node, role)
            .map(|node| Ref { file: r.file, node })
    }
    fn group(&self, r: Ref, role: &str) -> Vec<Ref> {
        self.s(r)
            .group(r.node, role)
            .iter()
            .map(|node| Ref {
                file: r.file,
                node: *node,
            })
            .collect()
    }
    fn children(&self, r: Ref) -> Vec<Ref> {
        self.s(r)
            .node(r.node)
            .children
            .iter()
            .map(|node| Ref {
                file: r.file,
                node: *node,
            })
            .collect()
    }
    fn kind(&self, r: Ref) -> &str {
        self.s(r).node(r.node).kind.as_str()
    }
    fn text(&self, r: Ref) -> String {
        self.s(r).text(r.node).to_string()
    }
    fn op(&self, r: Ref) -> String {
        self.s(r).node(r.node).op.clone()
    }
    fn value(&mut self, typ: impl Into<TypeInput>) -> V {
        crate::profile::count("permissions.values", 1);
        let id = self.values.len();
        self.values.push(Value::new(self.r.shared_type(typ)));
        id
    }
    fn has_slice_data(&mut self, typ: &Type) -> bool {
        reference_data(typ) && (!matches!(base_type(typ), Type::Union(..))
            || self.type_property(typ, queries::Property::Slice))
    }
    fn fresh(&mut self, typ: Type, path: Option<Path>) -> V {
        let data = self.has_slice_data(&typ)
            || self.pure_depth > 0 && matches!(base_type(&typ), Type::Union(..));
        let id = self.value(typ);
        self.values[id].path = path.clone();
        if let Some(mut path) = path {
            if path.qualified && path.steps.len() > 16 {
                // Match Python's finite symbolic-path domain across calls.
                // A widened origin conservatively covers the whole parameter.
                path.steps = Shared::new();
                self.values[id].data.insert(Origin {
                    path,
                    aspect: Aspect::All,
                    seed: None,
                });
                crate::profile::count("permissions.widened_paths", 1);
                return id;
            }
            self.values[id].locations.insert(path.clone());
            if path.qualified
                && (data || self.pure_depth > 0
                    && matches!(base_type(&self.values[id].typ), Type::String { .. }))
            {
                self.values[id].data.insert(Origin {
                    path,
                    aspect: Aspect::Data,
                    seed: None,
                });
            }
        }
        id
    }
    fn qualify(&mut self, v: V, origins: Origins) -> V {
        if origins.is_empty() {
            return v;
        }
        let id = self.value(self.values[v].typ.clone());
        self.values[id] = self.values[v].clone();
        self.values[id].base = Some(v);
        self.values[id].storage.extend(origins.iter().cloned());
        compact_origins(&mut self.values[id].storage);
        if reference_data(&self.values[v].typ) {
            self.values[id].data.extend(origins.iter().cloned());
            compact_origins(&mut self.values[id].data);
        }
        id
    }
    fn merge(&mut self, dst: V, src: V) -> bool {
        self.merge_seen(dst, src, &mut HashSet::new())
    }
    fn merge_seen(&mut self, dst: V, src: V, seen: &mut HashSet<(V, V)>) -> bool {
        if dst == src || self.values[dst] == self.values[src] {
            crate::profile::count("permissions.noop_merges", 1);
            return false;
        }
        if !seen.insert((dst, src)) {
            return false;
        }
        let incoming = self.values[src].clone();
        let mut change = false;
        if self.values[dst].typ.is_unknown() && !incoming.typ.is_unknown() {
            self.values[dst].typ = incoming.typ.clone();
            change = true;
        }
        for (source, storage) in [(incoming.data, false), (incoming.storage, true)] {
            let set = if storage {
                &mut self.values[dst].storage
            } else {
                &mut self.values[dst].data
            };
            let n = set.len();
            set.extend(source.iter().cloned());
            change |= compact_origins(set);
            change |= n != set.len();
        }
        let n = self.values[dst].locations.len();
        self.values[dst].locations.extend(incoming.locations.iter().cloned());
        change |= n != self.values[dst].locations.len();
        for (name, v) in incoming.fields.iter() {
            if let Some(existing) = self.values[dst].fields.get(name).copied() {
                change |= self.merge_seen(existing, *v, seen)
            } else {
                self.values[dst].fields.insert(name.clone(), *v);
                change = true;
            }
        }
        for (child, target) in [(incoming.element, false), (incoming.target, true)] {
            if let Some(v) = child {
                let old = if target {
                    self.values[dst].target
                } else {
                    self.values[dst].element
                };
                if let Some(old) = old {
                    change |= self.merge_seen(old, v, seen)
                } else {
                    if target {
                        self.values[dst].target = Some(v)
                    } else {
                        self.values[dst].element = Some(v)
                    };
                    change = true
                }
            }
        }
        self.changed |= change;
        change
    }
    fn member(&mut self, v: V, name: &str) -> V {
        if matches!(base_type(&self.values[v].typ), Type::Pointer(_)) {
            let target = self.deref(v);
            return self.member(target, name);
        }
        if let Some(path) = self.member_path(&self.values[v].typ.clone(), name) {
            if path.len() > 1 {
                let mut child = v;
                for field in path {
                    child = self.member(child, &field);
                }
                return child;
            }
        }
        if matches!(base_type(&self.values[v].typ), Type::Array { .. })
            && name.chars().all(|x| "xyzwrgba".contains(x))
        {
            let item = self.item(v);
            if name.len() == 1 {
                return item;
            }
            let id = self.value(Type::Array {
                elem: Box::new(self.values[item].typ.owned()),
                length: Some(name.len() as i128),
            });
            self.values[id].element = Some(item);
            self.values[id].storage = self.values[v].storage.clone();
            return id;
        }
        if let Some(base) = self.values[v].base {
            let child = self.member(base, name);
            return self.qualify(child, self.values[v].storage.clone());
        }
        if matches!(base_type(&self.values[v].typ), Type::Soa(_)) {
            let child = if let Some(child) = self.values[v].fields.get(name).copied() {
                child
            } else {
                let typ = self.member_type(&self.values[v].typ.clone(), name);
                let path = self.values[v].path.clone().map(|mut p| {
                    p.steps.push(Step::Field(name.into()));
                    p
                });
                let child = self.fresh(typ, path);
                self.values[v].fields.insert(name.into(), child);
                child
            };
            let origins = self.values[v].data.iter().map(|origin| {
                let mut origin = origin.clone();
                if origin.seed.is_none() && origin.aspect == Aspect::Data {
                    origin.path.steps.push(Step::Field(name.into()));
                    origin.aspect = Aspect::Storage;
                }
                origin
            }).collect();
            return self.qualify(child, origins);
        }
        if let Some(child) = self.values[v].fields.get(name) {
            return *child;
        }
        let typ = self.member_type(&self.values[v].typ.clone(), name);
        let path = self.values[v].path.clone().map(|mut p| {
            p.steps.push(Step::Field(name.into()));
            p
        });
        let child = self.fresh(typ, path);
        self.values[child].storage = self.values[v].storage.clone();
        self.values[v].fields.insert(name.into(), child);
        child
    }
    fn item(&mut self, v: V) -> V {
        if let Some(base) = self.values[v].base {
            let child = self.item(base);
            return self.qualify(child, self.values[v].storage.clone());
        }
        if let Type::Soa(elem) = base_type(&self.values[v].typ).clone() {
            let result = if let Some(result) = self.values[v].element {
                result
            } else {
                let result = self.value(*elem.clone());
                for (name, _) in self.record_fields(&elem).iter() {
                    let column = self.member(v, name);
                    let item = self.item(column);
                    self.values[result].fields.insert(name.clone(), item);
                }
                self.values[v].element = Some(result);
                result
            };
            return self.qualify(result, self.values[v].data.clone());
        }
        let data = reference_data(&self.values[v].typ);
        let child = if let Some(child) = self.values[v].element {
            child
        } else {
            let typ = self.values[v]
                .typ
                .element()
                .cloned()
                .unwrap_or(Type::Unknown);
            let path = self.values[v].path.clone().map(|mut p| {
                p.steps.push(Step::Item);
                p
            });
            let child = self.fresh(typ, path);
            self.values[child].storage = if data {
                self.values[v].data.clone()
            } else {
                self.values[v].storage.clone()
            };
            self.values[v].element = Some(child);
            child
        };
        let origins = if data {
            self.values[v].data.clone()
        } else {
            self.values[v].storage.clone()
        };
        self.qualify(child, origins)
    }
    fn deref(&mut self, v: V) -> V {
        if let Some(target) = self.values[v].target {
            return target;
        }
        let path = self.values[v].path.clone().map(|mut p| {
            p.steps.push(Step::Deref);
            p
        });
        let id = self.fresh(
            self.values[v]
                .typ
                .element()
                .cloned()
                .unwrap_or(Type::Unknown),
            path.clone(),
        );
        if let Some(path) = path.filter(|path| path.qualified) {
            self.values[id].storage.insert(Origin {
                path,
                aspect: Aspect::Storage,
                seed: None,
            });
        }
        self.values[v].target = Some(id);
        id
    }
    fn copied(&mut self, v: V) -> V {
        self.copy_seen(v, &mut HashMap::new())
    }
    fn snapshot(&mut self, v: V, seen: &mut HashMap<V, V>) -> V {
        if let Some(result) = seen.get(&v) { return *result; }
        let old = self.values[v].clone();
        let result = self.value(old.typ.clone());
        seen.insert(v, result);
        self.values[result] = old.clone();
        self.values[result].fields.clear();
        for (name, child) in old.fields.iter() {
            let child = self.snapshot(*child, seen);
            self.values[result].fields.insert(name.clone(), child);
        }
        self.values[result].element = old.element.map(|child| self.snapshot(child, seen));
        self.values[result].target = old.target.map(|child| self.snapshot(child, seen));
        self.values[result].base = old.base.map(|child| self.snapshot(child, seen));
        result
    }
    fn copy_seen(&mut self, v: V, seen: &mut HashMap<V, V>) -> V {
        let _phase = seen.is_empty().then(||crate::profile::span("permissions.copy"));
        crate::profile::count("permissions.copy_visits", 1);
        if let Some(id) = seen.get(&v) {
            return *id;
        }
        let old = self.values[v].clone();
        let id = self.value(old.typ.clone());
        seen.insert(v, id);
        if reference_data(&old.typ)
            || self.pure_depth > 0 && matches!(base_type(&old.typ), Type::String { .. })
        {
            self.values[id].data = old.data;
        }
        if references_type(&old.typ) {
            self.values[id].path = old.path;
        }
        match base_type(&old.typ) {
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                let fields = self.record_fields(&old.typ);
                crate::profile::count("permissions.copy_fields", fields.len() as u64);
                for (name, _) in fields.iter() {
                    let child = self.member(v, name);
                    let copy = self.copy_seen(child, seen);
                    self.values[id].fields.insert(name.clone(), copy);
                }
            }
            Type::Array { .. } | Type::Fixed { .. } => {
                let child = self.item(v);
                let copy = self.copy_seen(child, seen);
                self.values[id].element = Some(copy);
            }
            Type::Slice(_) => {
                let child = self.item(v);
                self.values[id].element = Some(child);
            }
            Type::Soa(elem) => {
                for (name, _) in self.record_fields(elem).iter() {
                    let column = self.member(v, name);
                    self.values[id].fields.insert(name.clone(), column);
                }
            }
            _ => {
                self.values[id].fields = old.fields;
                self.values[id].element = old.element;
                self.values[id].target = old.target;
            }
        }
        id
    }
    fn freeze(&mut self, v: V, seed: Ref) -> V {
        let v = self.copied(v);
        self.freeze_seen(v, seed, &mut HashSet::new());
        v
    }
    fn freeze_seen(&mut self, v: V, seed: Ref, seen: &mut HashSet<V>) {
        if !seen.insert(v) {
            return;
        }
        let origin = Origin {
            path: Path {
                qualified: true,
                index: 0,
                steps: Shared::new(),
            },
            aspect: Aspect::Data,
            seed: Some(seed),
        };
        if self.has_slice_data(&self.values[v].typ.owned()) {
            self.values[v].data.insert(origin.clone());
            self.values[v].storage.insert(origin.clone());
        }
        match base_type(&self.values[v].typ).clone() {
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                self.values[v].storage.insert(Origin {
                    aspect: Aspect::Inline,
                    ..origin
                });
                let fields = self.record_fields(&self.values[v].typ.clone());
                for (name, _) in fields.iter() {
                    let child = self.member(v, name);
                    self.freeze_seen(child, seed, seen);
                    self.values[v].fields.insert(name.clone(), child);
                }
            }
            Type::Array { .. } | Type::Fixed { .. } => {
                self.values[v].storage.insert(Origin {
                    aspect: Aspect::Inline,
                    ..origin
                });
                let child = self.item(v);
                self.freeze_seen(child, seed, seen);
                self.values[v].element = Some(child);
            }
            _ => {}
        }
    }
    fn refs(&mut self, v: V) -> Origins {
        let _phase = crate::profile::span("permissions.references");
        self.refs_seen(v, &mut HashSet::new(), &mut HashSet::new(), 0)
    }
    fn refs_seen(
        &mut self,
        v: V,
        seen: &mut HashSet<V>,
        types: &mut HashSet<(crate::semantics::TypeId, usize)>,
        depth: usize,
    ) -> Origins {
        let value = &self.values[v];
        if value.path.as_ref().is_none_or(|path| !path.qualified)
            && value.data.is_empty()
            && value.storage.is_empty()
            && value.fields.is_empty()
            && value.element.is_none()
            && value.target.is_none()
            && value.base.is_none()
        {
            crate::profile::count("permissions.origin_free_leaves", 1);
            return Origins::new();
        }
        if !seen.insert(v) {
            return Origins::new();
        }
        let mut result = self.values[v].data.clone();
        // SOA columns are native multipointers. Their storage qualifiers
        // describe the referenced column memory retained by a header copy.
        if matches!(base_type(&self.values[v].typ), Type::Soa(_)) {
            for column in self.values[v].fields.values() {
                result.extend(self.values[*column].storage.iter().cloned());
            }
        }
        if result.iter().any(|origin| origin.aspect == Aspect::All) {
            compact_origins(&mut result);
            return result;
        }
        let shape = self.values[v]
            .path
            .as_ref()
            .filter(|path| path.qualified)
            .map(|path| (self.values[v].typ.id(), path.index));
        let recursive = shape.is_some_and(|shape| !types.insert(shape));
        if (depth > 16 && shape.is_some()) || recursive {
            if let Some(mut path) = self.values[v].path.clone() {
                path.steps = Shared::new();
                result.insert(Origin {
                    path,
                    aspect: Aspect::All,
                    seed: None,
                });
            }
            if !recursive {
                if let Some(shape) = shape {
                    types.remove(&shape);
                }
            }
            if recursive {
                crate::profile::count("permissions.recursive_reference_paths", 1);
            }
            return result;
        }
        // Keep unopened parameter subtrees symbolic. Existing children still
        // carry assignments and seeds introduced while checking this body.
        let mut covered = None;
        if self.values[v].path.as_ref().is_some_and(|path| path.qualified)
            && self.type_property(&self.values[v].typ.owned(), queries::Property::Slice)
        {
            let path = self.values[v].path.clone().unwrap();
            result.insert(Origin {
                path: path.clone(),
                aspect: Aspect::References,
                seed: None,
            });
            covered = Some(path);
            crate::profile::count("permissions.deferred_reference_paths", 1);
        }
        let mut children = self.values[v].fields.values().copied().collect::<Vec<_>>();
        children.extend(self.values[v].element);
        children.extend(self.values[v].target);
        for child in children {
            let references = self.refs_seen(child, seen, types, depth + 1);
            result.extend(
                references
                    .iter()
                    .filter(|origin| {
                        !covered.as_ref().is_some_and(|path| {
                            origin.seed.is_none()
                                && matches!(origin.aspect, Aspect::Data | Aspect::References)
                                && origin.path.index == path.index
                                && origin.path.steps.starts_with(&path.steps)
                        })
                    })
                    .cloned(),
            );
        }
        if let Some(shape) = shape {
            types.remove(&shape);
        }
        compact_origins(&mut result);
        result
    }
    fn contains_slice(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(typ.clone()) {
            return false;
        }
        if seen.len() > 24 {
            return true;
        }
        match base_type(typ) {
            Type::Slice(_) | Type::Soa(_) | Type::Unknown | Type::Generic => true,
            Type::Pointer(elem)
            | Type::Array { elem, .. }
            | Type::Fixed { elem, .. }
            | Type::Dynamic(elem)
            | Type::Set(elem)
            | Type::Map { value: elem, .. } => self.contains_slice(elem, seen),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self.record_fields(typ)
                .iter()
                .any(|(_, typ)| self.contains_slice(typ, seen)),
            Type::Union(..) => self.r.union_variants(typ).iter()
                .any(|variant| self.contains_slice(variant, seen)),
            _ => false,
        }
    }
    fn unresolved_value_type(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(typ.clone()) {
            return false;
        }
        match typ {
            Type::Unknown | Type::Generic => true,
            Type::Nominal(_, base)
            | Type::Pointer(base)
            | Type::Slice(base)
            | Type::Dynamic(base)
            | Type::Soa(base)
            | Type::Array { elem: base, .. }
            | Type::Fixed { elem: base, .. } => self.unresolved_value_type(base, seen),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self.record_fields(typ)
                .iter()
                .any(|(_, typ)| self.unresolved_value_type(typ, seen)),
            Type::Union(..) => self
                .r
                .union_variants(typ)
                .iter()
                .any(|typ| self.unresolved_value_type(typ, seen)),
            _ => false,
        }
    }
    fn require(&mut self, origins: Origins, operation: Ref, c: C, reason: &str, related: Vec<Ref>) {
        let changes_storage = ![
            "create a writable slice alias",
            "assign a writable slice alias",
            "take a writable address",
            "obtain a writable pointer",
            "satisfy explicit #mut",
            "pass a read-only slice to #mut parameter",
        ]
        .iter()
        .any(|prefix| reason.starts_with(prefix));
        self.require_effect(origins, operation, c, reason, related, changes_storage);
    }
    fn require_effect(
        &mut self,
        origins: Origins,
        operation: Ref,
        c: C,
        reason: &str,
        related: Vec<Ref>,
        changes_storage: bool,
    ) {
        let unresolved = self.uncertain.or_else(|| {
            self.contexts[c]
                .controls
                .iter()
                .rev()
                .copied()
                .find(|control| self.kind(*control) == "When")
                .and_then(|control| self.role(control, "cond"))
        });
        for origin in origins {
            if let Some(seed) = origin.seed {
                if origin.aspect == Aspect::Inline {
                    continue;
                }
                // A concrete read-only seed is checked at its original native
                // statement. Its surrounding when/marker chooses whether that
                // statement is active; it does not require branch prediction.
                let name = self.text(seed);
                let message = format!("Cannot {reason}: read-only slice access from '{name}'.");
                self.fact_event(cached::FactEvent::Issue(std::sync::Arc::new(Issue {
                    operation,
                    seed,
                    message,
                    related: related.clone(),
                })));
            } else {
                let incoming = Effect {
                    operation,
                    unresolved,
                    deleting: self.deleting,
                    reason: reason.into(),
                    related: related.clone(),
                    changes_storage,
                };
                let effects = &mut self.contexts[c].effects;
                if matches!(origin.aspect, Aspect::Data | Aspect::References) {
                    let all = Origin { path: Path { qualified: true, index: origin.path.index, steps: Shared::new() },
                        aspect: Aspect::All, seed: None };
                    if effects.get(&all).is_some_and(|effect| effect.covers(&incoming)) {
                        continue;
                    }
                }
                if !effects.contains_key(&origin)
                    || effects.get(&origin).is_some_and(|old| {
                        (!old.changes_storage && incoming.changes_storage)
                            || (old.deleting && !incoming.deleting && incoming.changes_storage)
                    })
                {
                    if origin.aspect == Aspect::All {
                        effects.retain(|previous, effect| previous.path.index != origin.path.index
                            || !matches!(previous.aspect, Aspect::Data | Aspect::References)
                            || !incoming.covers(effect));
                    }
                    effects.insert(origin, incoming);
                    self.changed = true;
                }
            }
        }
    }
    fn follow(&mut self, mut v: V, steps: &[Step]) -> V {
        for step in steps {
            v = match step {
                Step::Field(name) => self.member(v, name),
                Step::Item => self.item(v),
                Step::Deref => self.deref(v),
            }
        }
        v
    }
    fn resolve(&mut self, origin: &Origin, args: &[V]) -> Origins {
        if origin.seed.is_some() {
            return [origin.clone()].into();
        }
        let Some(v) = args.get(origin.path.index) else {
            return Origins::new();
        };
        if origin.aspect == Aspect::All {
            return self.refs(*v);
        }
        // Native SOA columns are multipointers, while a freshly zipped
        // column can still carry its source slice's provenance. Both shapes
        // describe the same referenced element storage across header copies.
        let column = origin.path.steps.split_last().is_some_and(|(last, prefix)| {
            matches!(last, Step::Field(_)) && {
                let parent = self.follow(*v, prefix);
                matches!(base_type(&self.values[parent].typ), Type::Soa(_))
            }
        });
        let v = self.follow(*v, &origin.path.steps);
        if origin.aspect == Aspect::References {
            return self.refs(v);
        }
        if origin.aspect == Aspect::Data {
            self.values[v].data.clone()
        } else {
            let mut storage = self.values[v].storage.clone();
            if column { storage.extend(self.values[v].data.iter().cloned()); }
            storage
        }
    }
    fn instantiate(&mut self, v: V, args: &[V], seen: &mut HashMap<V, V>, depth: usize) -> V {
        // Argument facts are read-only during one graph reconstruction. Share
        // path prefixes here, then discard them before a caller applies stores.
        self.instantiate_resolved(v, args, seen, &mut HashMap::new(), &mut HashMap::new(), depth)
    }
    fn resolve_path(&mut self, path: &Path, args: &[V], paths: &mut HashMap<Path, V>) -> Option<V> {
        if let Some(value) = paths.get(path) { return Some(*value); }
        let mut value = *args.get(path.index)?;
        let mut prefix = Path { steps: Shared::new(), ..path.clone() };
        for step in &path.steps {
            prefix.steps.push(step.clone());
            if let Some(existing) = paths.get(&prefix) {
                value = *existing;
            } else {
                value = self.follow(value, std::slice::from_ref(step));
                paths.insert(prefix.clone(), value);
            }
        }
        paths.insert(path.clone(), value);
        Some(value)
    }
    fn resolve_once(&mut self, origin: &Origin, args: &[V],
        origins: &mut HashMap<Origin, Origins>, paths: &mut HashMap<Path, V>) -> Origins {
        if let Some(value) = origins.get(origin) { return value.clone(); }
        let result = if origin.seed.is_some() || origin.aspect == Aspect::All {
            self.resolve(origin, args)
        } else if let Some(value) = self.resolve_path(&origin.path, args, paths) {
            match origin.aspect {
                Aspect::References => self.refs(value),
                Aspect::Data => self.values[value].data.clone(),
                _ => {
                    let mut storage = self.values[value].storage.clone();
                    if let Some((Step::Field(_), prefix)) = origin.path.steps.split_last() {
                        let parent = self.resolve_path(&Path { steps: prefix.to_vec().into(), ..origin.path.clone() }, args, paths);
                        if parent.is_some_and(|parent| matches!(base_type(&self.values[parent].typ), Type::Soa(_))) {
                            storage.extend(self.values[value].data.iter().cloned());
                        }
                    }
                    storage
                }
            }
        } else { Origins::new() };
        origins.insert(origin.clone(), result.clone());
        result
    }
    fn instantiate_resolved(&mut self, v: V, args: &[V], seen: &mut HashMap<V, V>,
        origins: &mut HashMap<Origin, Origins>, paths: &mut HashMap<Path, V>, depth: usize) -> V {
        let _phase = (depth == 0).then(||crate::profile::span("permissions.instantiate"));
        if let Some(id) = seen.get(&v) {
            return *id;
        }
        let old = self.values[v].clone();
        if let Some(path) = &old.path {
            if let Some(actual) = self.resolve_path(path, args, paths) {
                // A symbolic path may have been narrowed by a type switch.
                // Reconstruct that projection against the caller's union.
                let typ = old.typ.owned();
                let id = if !typ.is_unknown() && typ != self.values[actual].typ.owned() {
                    self.narrow(actual, typ)
                } else {
                    self.copied(actual)
                };
                seen.insert(v, id);
                for origin in &old.data {
                    let resolved = self.resolve_once(origin, args, origins, paths);
                    self.values[id].data.extend(resolved.iter().cloned());
                }
                compact_origins(&mut self.values[id].data);
                let mut storage = Origins::new();
                for origin in &old.storage {
                    storage.extend(self.resolve_once(origin, args, origins, paths));
                }
                let id = self.qualify(id, storage);
                seen.insert(v, id);
                return id;
            }
        }
        let id = self.value(old.typ);
        seen.insert(v, id);
        for origin in old.data.iter() {
            let resolved = self.resolve_once(origin, args, origins, paths);
            self.values[id].data.extend(resolved.iter().cloned());
        }
        for origin in old.storage.iter() {
            let resolved = self.resolve_once(origin, args, origins, paths);
            self.values[id].storage.extend(resolved);
        }
        compact_origins(&mut self.values[id].data);
        compact_origins(&mut self.values[id].storage);
        if depth > 16 {
            return id;
        }
        for (name, child) in old.fields.iter() {
            let child = self.instantiate_resolved(*child, args, seen, origins, paths, depth + 1);
            self.values[id].fields.insert(name.clone(), child);
        }
        if let Some(child) = old.element {
            let child = self.instantiate_resolved(child, args, seen, origins, paths, depth + 1);
            self.values[id].element = Some(child);
        }
        if let Some(child) = old.target {
            let child = self.instantiate_resolved(child, args, seen, origins, paths, depth + 1);
            self.values[id].target = Some(child);
        }
        id
    }
    fn params(&self, sig: Ref) -> Vec<(Ref, Ref)> {
        let mut result = vec![];
        if let Some(params) = self.role(sig, "params") {
            for field in self.children(params) {
                if self.kind(field) == "Field" {
                    for name in self.group(field, "names") {
                        result.push((name, field));
                    }
                }
            }
        }
        result
    }
    fn annotations(&mut self) {
        let sources = self.r.sources;
        for (file, s) in sources.iter().enumerate() {
            for (readonly, row) in [false, true].into_iter().flat_map(|ro| s.info[if ro {"ro_parameters"} else {"mut_parameters"}].as_array().into_iter().flatten().map(move |row| (ro, row))) {
                let offset = row[0].as_u64().unwrap_or(u64::MAX) as usize;
                let field = s
                    .nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, n)| {
                        n.kind == "Field" && n.span.start <= offset && offset < n.span.end
                    })
                    .min_by_key(|(_, n)| n.span.len())
                    .map(|(i, _)| NodeId(i));
                if let Some(field) = field {
                    if let Some(name) = s
                        .group(field, "names")
                        .iter()
                        .find(|name| s.node(**name).span.start == offset)
                    {
                        let reference = Ref { file, node: *name };
                        if readonly { self.explicit_ro.insert(reference); } else { self.explicit.insert(reference); }
                        let valid = s.ancestors(field).any(|ancestor| {
                            s.node(ancestor).kind == "ProcedureType"
                                && s.node(field).parent == s.role(ancestor, "params")
                        });
                        let typ = s
                            .role(field, "type")
                            .map(|node| {
                                self.specialized_type(Ref { file, node }, &Substitutions::new())
                            })
                            .unwrap_or(Type::Unknown);
                        if !valid
                            || !matches!(
                                base_type(&typ),
                                Type::Slice(_) | Type::Soa(_) | Type::Unknown | Type::Generic
                            )
                        {
                            self.facts
                                .invalid
                                .insert(reference, if readonly {"#ro requires a slice parameter."} else {"#mut requires a slice parameter."}.into());
                        }
                    }
                }
            }
        }
    }
    fn identity_result(&mut self, procedure: Ref, index: usize) -> Option<usize> {
        let signature = self.role(procedure, "type").unwrap_or(procedure);
        let parameters = self.params(signature).iter()
            .map(|(name, _)| self.r.symbol(*name)).collect::<Vec<_>>();
        let returns = self.s(procedure).descendants(procedure.node)
            .filter(|node| self.s(procedure).node(*node).kind == "Return")
            .filter(|node| self.s(procedure).ancestors(*node)
                .find(|ancestor| self.s(procedure).node(*ancestor).kind == "Procedure")
                == Some(procedure.node))
            .map(|node| Ref { file: procedure.file, node }).collect::<Vec<_>>();
        let mut parameter = None;
        for returned in returns {
            let values = self.group(returned, "values");
            let mut value = *values.get(index)?;
            while self.kind(value) == "Paren" { value = self.role(value, "expr")?; }
            if self.s(value).text(value.node) == "nil" && self.r.symbol(value).is_none() {
                continue;
            }
            if self.kind(value) != "Ident" { return None; }
            let symbol = self.r.symbol(value)?;
            let candidate = parameters.iter().position(|item| *item == Some(symbol))?;
            if parameter.is_some_and(|old| old != candidate) { return None; }
            parameter = Some(candidate);
        }
        parameter
    }
    fn summary(
        &mut self,
        proc: Ref,
        bindings: Substitutions,
        caller: Option<C>,
        contract: bool,
    ) -> C {
        let key = SummaryKey {
            procedure: proc,
            contract,
            bindings: bindings
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            checked: self.pure_depth > 0,
            opaque_readonly: self.opaque_readonly,
        };
        if let Some(c) = self.summaries.get(&key).copied() {
            if let Some(caller) = caller {
                self.contexts[caller].callees.insert(c);
                self.contexts[c].callers.insert(caller);
                if self.contexts[c].active {
                    self.contexts[c].cacheable = false;
                    self.contexts[caller].cacheable = false;
                    self.recursive_pending.insert(caller);
                }
            }
            return c;
        }
        if self.uncertain.is_none() && !self.deleting {
            if let Some(c) = self.restore_summary(&key, caller) { return c; }
        }
        let c = self.contexts.len();
        crate::profile::count("permissions.summaries_built", 1);
        self.contexts
            .push(Context::new(Some(proc), bindings.clone()));
        self.contexts[c].checked = self.pure_depth > 0;
        self.contexts[c].cacheable = self.uncertain.is_none() && !self.deleting;
        self.contexts[c].event_start = self.fact_events.len();
        if let Some(caller) = caller {
            self.contexts[caller].callees.insert(c);
            self.contexts[c].callers.insert(caller);
        }
        self.summaries.insert(key, c);
        let sig = self.role(proc, "type").unwrap_or(proc);
        for (index, (name, field)) in self.params(sig).into_iter().enumerate() {
            let typ = self
                .role(field, "type")
                .map(|r| self.specialized_type(r, &bindings))
                .unwrap_or_else(|| {
                    self.role(field, "default")
                        .map(|default| self.typ(default, c).defaulted())
                        .unwrap_or(Type::Unknown)
                });
            let v = self.fresh(
                typ,
                Some(Path {
                    qualified: contract,
                    index,
                    steps: Shared::new(),
                }),
            );
            let v = if self.explicit_ro.contains(&name) { self.freeze(v, name) } else { v };
            if let Some(symbol) = self.r.symbol(name) {
                self.contexts[c].env.insert(symbol, v);
            }
            if self.explicit.contains(&name) {
                self.require(
                    [Origin {
                        path: Path {
                            qualified: true,
                            index,
                            steps: Shared::new(),
                        },
                        aspect: Aspect::Data,
                        seed: None,
                    }]
                    .into(),
                    name,
                    c,
                    "satisfy explicit #mut",
                    vec![],
                );
            }
        }
        if let Some(results) = self.role(sig, "results") {
            for field in self.children(results) {
                if self.kind(field) != "Field" {
                    continue;
                }
                let typ = self
                    .role(field, "type")
                    .map(|r| self.specialized_type(r, &bindings))
                    .unwrap_or(Type::Unknown);
                let names = self.group(field, "names");
                for name in if names.is_empty() {
                    vec![None]
                } else {
                    names.into_iter().map(Some).collect()
                } {
                    let v = if name.is_none() && references_type(&typ) {
                        // This proof is independent of recursive summary order.
                        // Every return names the same immutable parameter or nil.
                        let index = self.contexts[c].results.len();
                        let input = self.identity_result(proc, index)
                            .and_then(|index| self.params(sig).get(index).copied())
                            .and_then(|(name, _)| self.r.symbol(name))
                            .and_then(|symbol| self.contexts[c].env.get(&symbol).copied());
                        input.map(|value| self.copied(value))
                            .unwrap_or_else(|| self.value(typ.clone()))
                    } else { self.value(typ.clone()) };
                    self.values[v].typ = self.r.shared_type(typ.clone());
                    let symbol = name.and_then(|name| self.r.symbol(name));
                    self.contexts[c].results.push(v);
                    self.contexts[c].named.push(symbol);
                    if let Some(symbol) = symbol {
                        self.contexts[c].env.insert(symbol, v);
                    }
                }
            }
        }
        self.contexts[c].active = true;
        self.depth += 1;
        if self.depth < 40 {
            if let Some(body) = self.role(proc, "body") {
                for pass in 0..24 {
                    crate::profile::count("permissions.body_passes", 1);
                    let before = self.state(c);
                    self.body_pass(body, c, pass >= 2);
                    if self.state_matches(c, &before) {
                        break;
                    }
                    if pass == 23 {
                        self.conservative_summary(c, "verify a complex slice reference graph");
                    }
                }
            }
        } else {
            self.contexts[c].cacheable = false;
            for (index, (name, _)) in self.params(sig).into_iter().enumerate() {
                if self.explicit_ro.contains(&name) { continue; }
                self.require(
                    [Origin {
                        path: Path {
                            qualified: true,
                            index,
                            steps: Shared::new(),
                        },
                        aspect: Aspect::All,
                        seed: None,
                    }]
                    .into(),
                    name,
                    c,
                    "verify a recursive slice call",
                    vec![],
                );
            }
        }
        self.depth -= 1;
        self.contexts[c].active = false;

        for origin in self.contexts[c].effects.keys() {
            if let Some((name, _)) = self.params(sig).get(origin.path.index) {
                self.facts.mutating.insert(*name);
            }
        }
        self.contexts[c].complete = !self.r.has_demanded_packages();
        self.contexts[c].revision += 1;
        self.contexts[c].event_end = self.fact_events.len();
        c
    }
    fn conservative_summary(&mut self, c: C, reason: &str) {
        self.contexts[c].cacheable = false;
        let Some(proc) = self.contexts[c].proc else {
            return;
        };
        let sig = self.role(proc, "type").unwrap_or(proc);
        for (index, (name, _)) in self.params(sig).into_iter().enumerate() {
            self.require(
                [Origin {
                    path: Path {
                        qualified: true,
                        index,
                        steps: Shared::new(),
                    },
                    aspect: Aspect::All,
                    seed: None,
                }]
                .into(),
                name,
                c,
                reason,
                vec![],
            );
        }
    }
    // Share fixed-input metadata with the resolver's other analyses and emitter.
    fn specialized_type(&mut self, reference: Ref, bindings: &Substitutions) -> Type {
        self.r.specialized_type(reference, bindings)
    }
    fn record_fields(&mut self, typ: &Type) -> crate::semantics::RecordFields {
        self.r.shared_record_fields(typ)
    }
    fn member_path(&mut self, typ: &Type, name: &str) -> Option<Vec<String>> {
        self.r.member_path(typ, name)
    }
    fn member_type(&mut self, typ: &Type, name: &str) -> Type {
        self.r.member_type(typ, name)
    }
    fn typ(&mut self, r: Ref, c: C) -> Type {
        let _phase = crate::profile::span("permissions.type_queries");
        if let Some(symbol) = self.r.symbol(r) {
            if let Some(v) = self.contexts[c]
                .env
                .get(&symbol)
                .or(self.globals.get(&symbol))
            {
                return self.values[*v].typ.owned();
            }
        }
        match self.kind(r) {
            "Selector" if self.r.symbol(r).is_none() => {
                if let (Some(base), Some(field)) = (self.role(r, "expr"), self.role(r, "field")) {
                    let typ = self.typ(base, c);
                    return self.member_type(&typ, &self.text(field));
                }
            }
            "Index" | "MatrixIndex" | "Deref" => {
                if let Some(base) = self.role(r, "expr") {
                    return self
                        .typ(base, c)
                        .element()
                        .cloned()
                        .unwrap_or(Type::Unknown);
                }
            }
            "Compound" => {
                if let Some(annotation) = self.role(r, "type") {
                    return self.specialized_type(annotation, &self.contexts[c].bindings.clone());
                }
                if self
                    .s(r)
                    .node(r.node)
                    .spans
                    .get("open")
                    .is_some_and(|open| self.s(r).slice(*open) == "{")
                {
                    if let Some(parent) = self.s(r).node(r.node).parent {
                        let mut parent = Ref {
                            file: r.file,
                            node: parent,
                        };
                        let mut child = r;
                        while self.kind(parent) == "Paren" {
                            child = parent;
                            let Some(node) = self.s(parent).node(parent.node).parent else { break; };
                            parent.node = node;
                        }
                        if self.kind(parent) == "Return" {
                            let index = self.group(parent, "values").iter().position(|value| *value == child);
                            if let (Some(index), Some(procedure)) = (index, self.contexts[c].proc) {
                                let signature = self.role(procedure, "type").unwrap_or(procedure);
                                if let Some((_, Some(annotation))) = self.r.fields(signature, "results").get(index) {
                                    return self.specialized_type(*annotation, &self.contexts[c].bindings.clone());
                                }
                            }
                        }
                        let mut field = None;
                        if self.kind(parent) == "FieldValue" {
                            field = self.role(parent, "field").map(|r| self.text(r));
                            if let Some(node) = self.s(parent).node(parent.node).parent {
                                parent.node = node;
                            }
                        }
                        if self.kind(parent) == "Compound" {
                            let typ = self.typ(parent, c);
                            if let Some(field) = field {
                                return self.member_type(&typ, &field);
                            }
                            if matches!(base_type(&typ), Type::Struct(_) | Type::Record(..)) {
                                let index = self
                                    .group(parent, "elems")
                                    .iter()
                                    .position(|child| child.node == r.node)
                                    .unwrap_or(0);
                                return self.record_fields(&typ)
                                    .get(index)
                                    .map(|(_, typ)| typ.clone())
                                    .unwrap_or(Type::Unknown);
                            }
                            return typ.element().cloned().unwrap_or(Type::Unknown);
                        }
                        if self.kind(parent) == "Declaration" {
                            if let Some(annotation) = self.role(parent, "type") {
                                return self.specialized_type(
                                    annotation,
                                    &self.contexts[c].bindings.clone(),
                                );
                            }
                        }
                    }
                }
            }
            _ => {}
        }
        let bindings = self.contexts[c].bindings.clone();
        self.specialized_type(r, &bindings)
    }
    fn expression_results(&mut self, r: Ref, c: C, count: usize) -> Vec<V> {
        let first = self.eval(r, c);
        let values = self.contexts[c]
            .expressions
            .get(&r)
            .cloned()
            .unwrap_or_else(|| vec![first]);
        if count == 1 && self.contexts[c].optional.contains(&r) {
            values.into_iter().take(1).collect()
        } else {
            values
        }
    }
    fn record(&mut self, r: Ref, c: C, values: Vec<V>, optional: bool) -> V {
        let first = values
            .first()
            .copied()
            .unwrap_or_else(|| self.value(Type::Unknown));
        self.contexts[c].expressions.insert(r, values);
        if optional {
            self.contexts[c].optional.insert(r);
        }
        first
    }
    fn eval_role(&mut self, r: Ref, role: &str, c: C) -> V {
        if let Some(child) = self.role(r, role) {
            self.eval(child, c)
        } else {
            self.value(Type::Unknown)
        }
    }
    fn eval(&mut self, r: Ref, c: C) -> V {
        if self.reuse.is_some() { self.observe_cache_input(r, c); }
        let typ = self.typ(r, c);
        let kind = self.kind(r).to_string();
        if kind == "Call" {
            return self.call(r, c);
        }
        if let Some(projection) = self.r.projection(r) {
            let owner = self.eval(projection.owner, c);
            return self.member(owner, &projection.member);
        }
        if kind == "Ident" || (kind == "Selector" && self.r.symbol(r).is_some()) {
            if let Some(symbol) = self.r.symbol(r) {
                if let Some(v) = self.contexts[c]
                    .env
                    .get(&symbol)
                    .or(self.globals.get(&symbol))
                {
                    return *v;
                }
                let declaration = self.r.symbols[symbol].reference;
                if self.kind(declaration) == "Declaration" && self.global_active.insert(symbol) {
                    let global = self.contexts.len();
                    self.contexts.push(Context::new(None, Substitutions::new()));
                    self.statement(declaration, global);
                    self.globals.extend(self.contexts[global].env.clone());
                    self.global_active.remove(&symbol);
                    if let Some(v) = self.globals.get(&symbol) {
                        return *v;
                    }
                }
            }
            return self.value(typ);
        }
        match kind.as_str() {
            "Paren" | "Runtime" | "OrReturn" | "OrBranch" | "OrPanic" | "AutoCast" => {
                let Some(inner) = self.role(r, "expr") else {
                    return self.value(typ);
                };
                let first = self.eval(inner, c);
                if let Some(mut values) = self.contexts[c].expressions.get(&inner).cloned() {
                    let unwrap = matches!(kind.as_str(), "OrReturn" | "OrBranch" | "OrPanic");
                    if unwrap {
                        values.pop();
                    }
                    let optional = !unwrap && self.contexts[c].optional.contains(&inner);
                    return self.record(r, c, values, optional);
                }
                first
            }
            "FieldValue" => self.eval_role(r, "value", c),
            "Selector" => {
                let v = self.eval_role(r, "expr", c);
                let field = self
                    .role(r, "field")
                    .map(|r| self.text(r))
                    .unwrap_or_default();
                let v = self.member(v, &field);
                if self.values[v].storage.iter().any(|o| o.seed.is_some()) {
                    if let Some(field) = self.role(r, "field") {
                        self.fact_event(cached::FactEvent::Readonly(field));
                    }
                }
                v
            }
            "Index" | "MatrixIndex" => {
                self.eval_role(r, "index", c);
                self.eval_role(r,"row",c);self.eval_role(r,"column",c);
                let v = self.eval_role(r, "expr", c);
                let result = self.item(v);
                if matches!(base_type(&self.values[v].typ), Type::Map { .. }) {
                    let ok = self.value(Type::scalar("bool"));
                    self.record(r, c, vec![result, ok], true)
                } else {
                    result
                }
            }
            "Deref" => {
                let v = self.eval_role(r, "expr", c);
                self.deref(v)
            }
            "TypeAssertion" => {
                let original = self.eval_role(r, "expr", c);
                let result = self.narrow(original, typ);
                let ok = self.value(Type::scalar("bool"));
                self.record(r, c, vec![result, ok], true)
            }
            "Slice" => {
                let mut v = self.eval_role(r, "expr", c);
                while matches!(base_type(&self.values[v].typ), Type::Pointer(_)) {
                    v = self.deref(v);
                }
                for role in ["low", "high"] {
                    self.eval_role(r, role, c);
                }
                if matches!(base_type(&self.values[v].typ), Type::Soa(_)) {
                    let result = self.copied(v);
                    self.values[result].storage.clear();
                    return result;
                }
                let elem = self.item(v);
                let result = self.value(if typ.is_unknown() {
                    Type::Slice(Box::new(self.values[elem].typ.owned()))
                } else {
                    typ
                });
                self.values[result].element = Some(elem);
                self.values[result].data = self.values[v].data.clone();
                let storage = self.values[v]
                    .storage
                    .iter()
                    .cloned()
                    .map(|mut o| {
                        if o.aspect == Aspect::Inline {
                            o.aspect = Aspect::Data
                        }
                        o
                    })
                    .collect::<Vec<_>>();
                self.values[result].data.extend(storage);
                result
            }
            "Unary" => {
                let v = self.eval_role(r, "expr", c);
                match self.op(r).as_str() {
                    "**" => v,
                    "&" => {
                        let header = self.role(r, "expr").is_some_and(|expr| {
                            self.r
                                .symbol(expr)
                                .is_some_and(|symbol| self.r.symbols[symbol].immutable)
                        });
                        if !header {
                            self.require(
                                self.values[v].storage.clone(),
                                r,
                                c,
                                "take a writable address of a read-only slice element",
                                vec![],
                            );
                        }
                        let id = self.value(Type::Pointer(Box::new(self.values[v].typ.owned())));
                        self.values[id].target = Some(v);
                        id
                    }
                    _ => self.value(typ),
                }
            }
            "Cast" => {
                let v = self.eval_role(r, "expr", c);
                if self.op(r) == "transmute" {
                    let refs = self.refs(v);
                    self.require(
                        refs,
                        r,
                        c,
                        "erase read-only slice permissions with transmute",
                        vec![],
                    );
                }
                let id = self.copied(v);
                self.values[id].typ = self.r.shared_type(typ.clone());
                if matches!(base_type(&typ), Type::Slice(_)) {
                    let origins = self.values[v]
                        .storage
                        .iter()
                        .cloned()
                        .map(|mut origin| {
                            if origin.aspect == Aspect::Inline {
                                origin.aspect = Aspect::Data
                            }
                            origin
                        })
                        .collect::<Vec<_>>();
                    self.values[id].data.extend(origins.iter().cloned());
                }
                id
            }
            "Procedure" | "ProcedureType" => self.value(typ),
            "Compound" => {
                let id = self.value(typ.clone());
                let fields = self.record_fields(&typ);
                for (index, element) in self.group(r, "elems").into_iter().enumerate() {
                    if matches!(base_type(&typ), Type::Map { .. })
                        && self.kind(element) == "FieldValue"
                    {
                        if let Some(key) = self.role(element, "field") {
                            let key_value = self.eval(key, c);
                            let refs = self.refs(key_value);
                            self.require(
                                refs,
                                key,
                                c,
                                "preserve read-only slice references in a map key",
                                vec![],
                            );
                        }
                    }
                    let incoming = self.eval(element, c);
                    let incoming = self.copied(incoming);
                    let name = if self.kind(element) == "FieldValue"
                        && !matches!(
                            base_type(&typ),
                            Type::Map { .. } | Type::Array { .. } | Type::Fixed { .. }
                        ) {
                        self.role(element, "field").map(|f| self.text(f))
                    } else {
                        fields.get(index).map(|(name, _)| name.clone())
                    };
                    let expected = if let Some(name) = &name {
                        fields
                            .iter()
                            .find(|(field, _)| field == name)
                            .map(|(_, typ)| typ)
                    } else {
                        typ.element()
                    };
                    if let Some(expected) = expected.filter(|t| !t.is_unknown()) {
                        self.values[incoming].typ = self.r.shared_type(expected.clone());
                    }
                    if expected.is_some_and(opaque) || matches!(base_type(&typ), Type::Set(_)) {
                        let refs = self.refs(incoming);
                        self.require(
                            refs,
                            element,
                            c,
                            "preserve read-only slice permissions in an opaque element or key",
                            vec![],
                        );
                    }
                    if let Some(name) = name {
                        self.values[id].fields.insert(name, incoming);
                    } else if let Some(old) = self.values[id].element {
                        self.merge(old, incoming);
                    } else {
                        self.values[id].element = Some(incoming);
                    }
                }
                id
            }
            "OrElse" => {
                let Some(x) = self.role(r, "x") else {
                    return self.value(typ);
                };
                let first = self.eval(x, c);
                let mut values = self.contexts[c]
                    .expressions
                    .get(&x)
                    .cloned()
                    .map(|mut x| {
                        x.pop();
                        x
                    })
                    .unwrap_or_else(|| vec![first]);
                if let Some(y) = self.role(r, "y") {
                    let other = self.expression_results(y, c, values.len());
                    for (index, v) in other.into_iter().enumerate() {
                        if let Some(old) = values.get(index) {
                            self.merge(*old, v);
                        } else {
                            values.push(v)
                        }
                    }
                }
                self.record(r, c, values, false)
            }
            "Ternary" | "WhenExpr" => {
                self.eval_role(r, "cond", c);
                if kind == "WhenExpr" {
                    if let Some(value) = self
                        .role(r, "cond")
                        .and_then(|cond| self.condition(cond, c))
                    {
                        return self.eval_role(r, if value { "x" } else { "y" }, c);
                    }
                }
                let x = self.eval_role(r, "x", c);
                let y = self.eval_role(r, "y", c);
                let result = self.copied(x);
                self.merge(result, y);
                result
            }
            "SwitchResults" | "FallbackList" => {
                let values = self
                    .group(r, "args")
                    .into_iter()
                    .map(|arg| self.eval(arg, c))
                    .collect();
                self.record(r, c, values, false)
            }
            "Comprehension" => {
                if let Some(loop_) = self.role(r, "loop") {
                    self.bind_loop(loop_, c);
                }
                self.eval_role(r, "cond", c);
                if let Some(key) = self.role(r, "key") {
                    let key_value = self.eval(key, c);
                    let refs = self.refs(key_value);
                    self.require(refs, key, c, "preserve read-only slice references in a map key", vec![]);
                }
                let output = self.eval_role(r, "output", c);
                let output = if self.s(r).node(r.node).data["spread"] == true {
                    self.item(output)
                } else {
                    output
                };
                if matches!(base_type(&typ), Type::Set(_)) {
                    if let Some(site) = self.role(r, "output") {
                        let refs = self.refs(output);
                        self.require(refs, site, c, "preserve read-only slice references in a Set key", vec![]);
                    }
                }
                let result = self.value(typ);
                let output = self.copied(output);
                self.values[result].element = Some(output);
                result
            }
            _ => {
                for child in self.children(r) {
                    if !matches!(
                        self.kind(child),
                        "Procedure" | "ProcedureType" | "ArrayType" | "StructType"
                    ) {
                        self.eval(child, c);
                    }
                }
                self.value(typ)
            }
        }
    }
    fn bind_loop(&mut self, r: Ref, c: C) {
        let v = self.eval_role(r, "expr", c);
        for (index, name) in self.group(r, "names").into_iter().enumerate() {
            let incoming = if index == 0
                && !matches!(base_type(&self.values[v].typ), Type::Map { .. })
                || index == 1 && matches!(base_type(&self.values[v].typ), Type::Map { .. })
            {
                self.item(v)
            } else {
                let typ = self.r.type_of(name);
                self.value(typ)
            };
            let incoming = if self.kind(name) == "Unary" && self.op(name) == "&" {
                if let Some(site) = self.role(r, "expr") {
                    self.require(
                        self.values[v].data.clone(),
                        site,
                        c,
                        "iterate over a read-only slice by writable reference",
                        vec![],
                    );
                }
                let id = self.value(Type::Pointer(Box::new(self.values[incoming].typ.owned())));
                self.values[id].target = Some(incoming);
                id
            } else {
                self.copied(incoming)
            };
            if let Some(symbol) = self.r.lookup(name, self.text(name).trim_start_matches('&')) {
                self.bind(symbol, incoming, c);
            }
        }
    }
    fn bind(&mut self, symbol: usize, v: V, c: C) {
        if let Some(old) = self.contexts[c].env.get(&symbol).copied() {
            self.merge(old, v);
        } else {
            self.contexts[c].env.insert(symbol, v);
            self.changed = true;
        }
    }
    fn statement(&mut self, r: Ref, c: C) {
        if self.discovering && self.r.has_demanded_packages() {
            return;
        }
        match self.kind(r).to_string().as_str() {
            "Procedure" | "ProcedureType" | "StructType" | "EnumType" | "UnionType" => {}
            "Declaration" => {
                let names = self.group(r, "names");
                let rhs = self.group(r, "values");
                if rhs.iter().any(|v| {
                    matches!(
                        self.kind(*v),
                        "Procedure" | "ProcedureType" | "StructType" | "EnumType" | "UnionType"
                    )
                }) {
                    return;
                }
                let values = if rhs.len() == 1 {
                    self.expression_results(rhs[0], c, names.len())
                } else {
                    rhs.iter().map(|v| self.eval(*v, c)).collect()
                };
                for (index, name) in names.into_iter().enumerate() {
                    let v = values
                        .get(index)
                        .copied()
                        .unwrap_or_else(|| self.value(Type::Unknown));
                    let mut v = self.copied(v);
                    if let Some(annotation) = self.role(r, "type") {
                        let bindings = self.contexts[c].bindings.clone();
                        let typ = self.specialized_type(annotation, &bindings);
                        self.values[v].typ = self.r.shared_type(typ);
                    }
                    if self.values[v].typ.is_unknown() {
                        // Recursive/discovery summaries can still contain an
                        // unknown result after its declaration has been typed.
                        let inferred = self.specialized_type(name, &self.contexts[c].bindings.clone());
                        if !inferred.is_unknown() {
                            v = self.narrow(v, inferred);
                        }
                    }
                    if self.s(r).node(r.node).immutable {
                        v = self.freeze(v, name)
                    } else if self.s(r).node(r.node).mutable {
                        if writable_alias(&self.values[v].typ) {
                            self.require(
                                self.values[v].data.clone(),
                                name,
                                c,
                                "create a writable slice alias",
                                vec![],
                            );
                        }
                        if opaque(&self.values[v].typ) {
                            let refs = self.refs(v);
                            self.require(
                                refs,
                                name,
                                c,
                                "erase read-only slice permissions in an opaque variable",
                                vec![],
                            );
                        }
                    }
                    if let Some(symbol) = self.r.symbol(name) {
                        self.bind(symbol, v, c);
                    }
                }
            }
            "Assignment" => {
                let lhs = self.group(r, "lhs");
                let rhs = self.group(r, "rhs");
                let targets = lhs.iter().map(|v| self.eval(*v, c)).collect::<Vec<_>>();
                let values = if rhs.len() == 1 {
                    self.expression_results(rhs[0], c, lhs.len())
                } else {
                    rhs.iter().map(|v| self.eval(*v, c)).collect()
                };
                for (index, (site, target)) in lhs.into_iter().zip(targets).enumerate() {
                    if self.text(site) == "_" {
                        continue;
                    }
                    let v = values
                        .get(index)
                        .copied()
                        .unwrap_or_else(|| self.value(Type::Unknown));
                    let v = self.copied(v);
                    if self.kind(site) == "Index" {
                        if let Some(container) = self.role(site, "expr") {
                            if matches!(base_type(&self.typ(container, c)), Type::Map { .. }) {
                                if let Some(key) = self.role(site, "index") {
                                    let key_value = self.eval(key, c);
                                    let refs = self.refs(key_value);
                                    self.require(
                                        refs,
                                        key,
                                        c,
                                        "preserve read-only slice references in a map key",
                                        vec![],
                                    );
                                }
                            }
                        }
                    }
                    if matches!(base_type(&self.values[target].typ), Type::Set(_))
                        && matches!(self.op(r).as_str(), "+=" | "-=")
                    {
                        let refs = self.refs(v);
                        self.require(
                            refs,
                            site,
                            c,
                            "preserve read-only slice references in a Set key",
                            vec![],
                        );
                    }
                    let named = self
                        .r
                        .symbol(site)
                        .is_some_and(|symbol| self.contexts[c].named.contains(&Some(symbol)));
                    if writable_alias(&self.values[target].typ) && !named {
                        self.require(
                            self.values[v].data.clone(),
                            site,
                            c,
                            "assign a writable slice alias",
                            vec![],
                        );
                    }
                    if self
                        .r
                        .symbol(site)
                        .is_some_and(|symbol| self.r.symbols[symbol].scope == NodeId(0))
                    {
                        let refs = self.refs(v);
                        self.require(
                            refs,
                            site,
                            c,
                            "store read-only slice references in global storage",
                            vec![],
                        );
                    }
                    self.write(target, v, c, site, self.op(r) == "=");
                }
            }
            "IncDec" => {
                let v = self.eval_role(r, "expr", c);
                self.require(
                    self.values[v].storage.clone(),
                    r,
                    c,
                    "modify slice elements",
                    vec![],
                );
            }
            "Return" => {
                let args = self.group(r, "values");
                let values = if args.is_empty() {
                    self.contexts[c]
                        .named
                        .iter()
                        .filter_map(|s| s.and_then(|s| self.contexts[c].env.get(&s).copied()))
                        .collect::<Vec<_>>()
                } else if args.len() == 1 {
                    self.expression_results(args[0], c, self.contexts[c].results.len())
                } else {
                    args.into_iter().map(|r| self.eval(r, c)).collect()
                };
                // Preserve storage shared with output parameters. Independent
                // return facts still detach from transient local rebindings.
                let mut snapshots = HashMap::new();
                let mut pending = self.contexts[c].stores.values().copied().collect::<Vec<_>>();
                while let Some(value) = pending.pop() {
                    if snapshots.insert(value, value).is_some() { continue; }
                    let value = &self.values[value];
                    pending.extend(value.fields.values().copied());
                    pending.extend([value.element, value.target, value.base].into_iter().flatten());
                }
                let mut copies = HashMap::new();
                for (index, v) in values.into_iter().enumerate() {
                    let v = self.copy_seen(v, &mut copies);
                    let v = self.snapshot(v, &mut snapshots);
                    if let Some(old) = self.contexts[c].results.get(index).copied() {
                        self.merge(old, v);
                    } else {
                        self.contexts[c].results.push(v);
                        self.changed = true;
                    }
                }
            }
            "When" | "If" => {
                if let Some(init) = self.role(r, "init") {
                    self.statement(init, c);
                }
                if let Some(cond) = self.role(r, "cond") {
                    self.eval(cond, c);
                    if self.kind(r) == "When" {
                        if let Some(value) = self.condition(cond, c) {
                            if let Some(body) = self.role(r, if value { "body" } else { "else" }) {
                                self.statement(body, c);
                            }
                            return;
                        }
                        self.contexts[c].cacheable = false;
                    }
                }
                self.contexts[c].controls.push(r);
                for role in ["body", "else"] {
                    if let Some(body) = self.role(r, role) {
                        self.statement(body, c);
                    }
                }
                self.contexts[c].controls.pop();
                if self.kind(r) == "If" {
                    let left = self
                        .role(r, "body")
                        .and_then(|body| self.terminal_fresh(body));
                    let right = self
                        .role(r, "else")
                        .and_then(|body| self.terminal_fresh(body));
                    if let (Some((lhs, rhs)), Some((other, _))) = (left, right) {
                        if self.text(lhs) == self.text(other) && self.local_header(lhs, c) {
                            let target = self.eval(lhs, c);
                            let value = self.eval(rhs, c);
                            let value = self.copied(value);
                            self.write(target, value, c, lhs, true);
                        }
                    }
                }
            }
            "Range" => {
                self.bind_loop(r, c);
                self.contexts[c].controls.push(r);
                if let Some(body) = self.role(r, "body") {
                    self.statement(body, c);
                }
                self.contexts[c].controls.pop();
            }
            "TypeSwitch" => {
                let original = self.eval_role(r, "expr", c);
                let names = self.group(r, "names");
                if let Some(body) = self.role(r, "body") {
                    for case in self.children(body) {
                        if self.kind(case) != "Case" {
                            continue;
                        }
                        let variants = self.group(case, "types");
                        let typ = if variants.len() == 1 {
                            self.specialized_type(variants[0], &self.contexts[c].bindings.clone())
                        } else {
                            self.values[original].typ.owned()
                        };
                        for name in &names {
                            let mut incoming = self.narrow(original, typ.clone());
                            if self.kind(*name) == "Unary" && self.op(*name) == "&" {
                                self.require(
                                    self.values[original].storage.clone(),
                                    *name,
                                    c,
                                    "take a writable reference to a read-only union element",
                                    vec![],
                                );
                                let pointer = self.value(Type::Pointer(Box::new(typ.clone())));
                                self.values[pointer].target = Some(incoming);
                                incoming = pointer;
                            }
                            if let Some(symbol) = self
                                .r
                                .lookup(*name, self.text(*name).trim_start_matches('&'))
                            {
                                self.contexts[c].env.insert(symbol, incoming);
                            }
                        }
                        for statement in self.group(case, "body") {
                            self.statement(statement, c);
                        }
                    }
                }
            }
            "Call" | "Binary" | "Unary" | "Comprehension" => {
                self.eval(r, c);
            }
            "For" | "Switch" | "Defer" => {
                self.contexts[c].controls.push(r);
                for child in self.children(r) {
                    self.statement(child, c);
                }
                self.contexts[c].controls.pop();
            }
            _ => {
                for child in self.children(r) {
                    self.statement(child, c);
                }
            }
        }
    }
    fn narrow(&mut self, original: V, typ: Type) -> V {
        let result = self.copied(original);
        self.values[result].typ = self.r.shared_type(typ.clone());
        if matches!(base_type(&typ), Type::Struct(_) | Type::Record(..) | Type::Anonymous(_)) {
            let fields = self.record_fields(&typ);
            self.values[result].fields.retain(|name, _| fields.iter().any(|(field, _)| field == name));
            self.values[result].element = None;
            self.values[result].target = None;
        }
        if !reference_data(&typ) {
            self.values[result].data.clear();
        }
        if matches!(
            base_type(&typ),
            Type::Scalar { .. } | Type::Enum(_) | Type::String { .. }
        ) {
            self.values[result].path = None;
            self.values[result].fields.clear();
            self.values[result].element = None;
            self.values[result].target = None;
        }
        if matches!(base_type(&typ), Type::Struct(_) | Type::Record(..)
            | Type::Anonymous(_) | Type::Array { .. } | Type::Fixed { .. }) {
            return self.qualify(result, self.values[original].data.clone());
        }
        result
    }
    fn local_header(&self, r: Ref, c: C) -> bool {
        if let Some(projection) = self.r.projection(r) {
            return self.local_header(projection.owner, c);
        }
        if self.kind(r) == "Ident" {
            return self.r.symbol(r).is_some_and(|symbol| {
                self.r.symbols[symbol].scope != NodeId(0)
                    && self.kind(self.r.symbols[symbol].reference) == "Declaration"
                    || self.contexts[c].named.contains(&Some(symbol))
            });
        }
        if self.kind(r) == "Selector" {
            return self
                .role(r, "expr")
                .is_some_and(|base| self.local_header(base, c));
        }
        false
    }
    fn header_root(&self, r: Ref) -> Option<usize> {
        if let Some(projection) = self.r.projection(r) {
            return self.header_root(projection.owner);
        }
        if self.kind(r) == "Ident" {
            return self.r.symbol(r);
        }
        if self.kind(r) == "Selector" {
            return self.role(r, "expr").and_then(|r| self.header_root(r));
        }
        None
    }
    fn terminal_fresh(&self, r: Ref) -> Option<(Ref, Ref)> {
        if self.kind(r) == "Block" {
            return self
                .children(r)
                .last()
                .and_then(|r| self.terminal_fresh(*r));
        }
        if self.kind(r) != "Assignment" || self.op(r) != "=" {
            return None;
        }
        let lhs = self.group(r, "lhs");
        let rhs = self.group(r, "rhs");
        if lhs.len() != 1 || rhs.len() != 1 {
            return None;
        }
        let fresh = self.kind(rhs[0]) == "Comprehension"
            || self.role(rhs[0], "callee").is_some_and(|callee| {
                self.builtin_name(callee, &mut HashSet::new())
                    .is_some_and(|name| matches!(name.as_str(), "make" | "new"))
                    || self.library(callee) == Some(("core:slice".into(), "clone".into()))
                        && self.verified_library(callee, "core:slice")
            });
        fresh.then_some((lhs[0], rhs[0]))
    }
    fn write(&mut self, target: V, v: V, c: C, site: Ref, strong: bool) {
        self.require(
            self.values[target].storage.clone(),
            site,
            c,
            "modify slice elements",
            vec![],
        );
        let independent = self.header_root(site).is_some_and(|symbol| {
            self.contexts[c].controls.iter().all(|control| {
                self.s(self.r.symbols[symbol].reference)
                    .ancestors(self.r.symbols[symbol].reference.node)
                    .any(|a| a == control.node)
            })
        });
        if strong
            && self.values[target].storage.is_empty()
            && self.values[target].locations.is_empty()
            && self.local_header(site, c)
            && independent
        {
            let copy = self.copied(v);
            let storage = self.values[target].storage.clone();
            self.values[target] = self.values[copy].clone();
            self.values[target].storage = storage;
        } else {
            let dst = self.values[target].base.unwrap_or(target);
            // A value assignment keeps the destination's writable location.
            let incoming = self.copied(v);
            self.merge(dst, incoming);
        }
        self.record_stores(target, v, c);
    }
    fn record_stores(&mut self, target: V, v: V, c: C) {
        if self.values[target].locations.is_empty() { return; }
        let typ = self.values[v].typ.owned();
        if !self.type_property(&typ, queries::Property::Slice) { return; }
        for path in self.values[target].locations.clone() {
            // Descendant writes have separate stores. Replaying an unchanged
            // argument projection would expand recursive paths indefinitely.
            let source = &self.values[v];
            if source.path.as_ref() == Some(&path)
                && source.data.is_empty() && source.storage.is_empty() {
                crate::profile::count("permissions.identity_stores_skipped", 1);
                continue;
            }
            let v = self.copied(v);
            if let Some(old) = self.contexts[c].stores.get(&path).copied() {
                self.merge(old, v);
            } else {
                self.contexts[c].stores.insert(path, v);
                self.changed = true;
            }
        }
    }
    fn call(&mut self, r: Ref, c: C) -> V {
        if crate::resulttypes::is_query(self.r,r) {
            let typ=self.typ(r,c);
            return self.value(typ);
        }
        let _phase = crate::profile::span("permissions.calls");
        let Some(callee) = self.role(r, "callee") else {
            return self.value(Type::Unknown);
        };
        let args = self.group(r, "args");
        let values = args
            .iter()
            .map(|arg| self.eval(*arg, c))
            .collect::<Vec<_>>();
        let typ = self.typ(r, c);
        let name = self
            .builtin_name(callee, &mut HashSet::new())
            .unwrap_or_else(|| self.text(callee));
        let library = self.library(callee);
        let builtin = self.builtin_name(callee, &mut HashSet::new()).is_some();
        if library
            .as_ref()
            .is_some_and(|(path, name)| path == "thor:sets" && name == "insert")
        {
            if let Some(key) = values.get(1) {
                let refs = self.refs(*key);
                self.require(
                    refs,
                    args[1],
                    c,
                    "preserve read-only slice references in a Set key",
                    vec![],
                );
            }
        }
        if builtin && name == "delete" && self.pure_depth > 0 {
            if let Some(value) = values.first() {
                let origins = self
                    .refs(*value)
                    .into_iter()
                    .filter(|origin| origin.seed.is_none())
                    .collect();
                let previous = self.deleting;
                self.deleting = true;
                self.require(origins, r, c, "delete borrowed storage", vec![]);
                self.deleting = previous;
            }
        }
        if builtin && READERS.contains(&name.as_str()) {
            let first = self.value(typ);
            let status = match name.as_str() {
                "make" | "new" => Some(Type::scalar("Allocator_Error")),
                "min" | "max" if crate::extremaplan::plan(self.r, r)
                    .is_some_and(|plan| plan.optional) => Some(Type::scalar("bool")),
                _ => None,
            };
            if let Some(status) = status {
                let status = self.value(status);
                return self.record(r, c, vec![first, status], true);
            }
            return first;
        }
        if let Some((path, name)) = &library {
            let reviewed = reviewed_library_contract(self.r, callee, path, name);
            if path == "core:fmt" && FMT_READERS.contains(&name.as_str()) && reviewed
            {
                return self.value(typ);
            }
            let reviewed_basic = path == "thor:basic" && reviewed;
            let graph_clone = name == "deepclone" && (reviewed_basic
                || path == "thor:graph" && reviewed);
            if graph_clone || reviewed_basic && ["try_hash", "_string"].contains(&name.as_str()) {
                let first = self.value(typ);
                let result = self.r.result_type(r, 1, &mut HashSet::new());
                // The verified status types are reference-free. Preserve the
                // resolved enum identity when available.
                let status = self.value(if result.is_unknown() {
                    Type::scalar(if name == "_string" { "Allocator_Error" } else { "Error" })
                } else {
                    result
                });
                return self.record(r, c, vec![first, status], name == "_string");
            }
            if reviewed_basic && ["print", "hash"].contains(&name.as_str()) {
                return self.value(typ);
            }
            if path == "core:fmt"
                && reviewed
                && ["bprint", "bprintln", "bprintf", "bprintfln"].contains(&name.as_str())
            {
                let buffer = args
                    .iter()
                    .position(|arg| {
                        self.kind(*arg) == "FieldValue"
                            && self
                                .role(*arg, "field")
                                .is_some_and(|f| self.text(f) == "buf")
                    })
                    .unwrap_or(0);
                let buffer_value =
                    if args.len() == 1 && self.kind(args[0]) == "Unary" && self.op(args[0]) == "**"
                    {
                        self.expanded_arguments(r, c, &args, &values)
                            .first()
                            .copied()
                    } else {
                        values.get(buffer).map(|v| (args[buffer], *v))
                    };
                if let Some((site, value)) = buffer_value {
                    self.require(
                        self.values[value].data.clone(),
                        site,
                        c,
                        "modify formatting buffer",
                        vec![],
                    );
                }
                return self.value(Type::String {
                    name: "string".into(),
                    untyped: false,
                });
            }
            if path == "core:slice"
                && reviewed
                && matches!(name.as_str(), "clone" | "clone_to_dynamic")
                && !values.is_empty()
            {
                let result = self.value(typ);
                let index = args
                    .iter()
                    .position(|arg| {
                        self.kind(*arg) == "FieldValue"
                            && self
                                .role(*arg, "field")
                                .is_some_and(|r| self.text(r) == "a")
                    })
                    .unwrap_or(0);
                let elem = self.item(values[index]);
                let elem = self.copied(elem);
                self.values[result].element = Some(elem);
                return result;
            }
        }
        if builtin {
            match name.as_str() {
                "soa_zip" => {
                    let mut fields = vec![];
                    let mut columns = BTreeMap::new();
                    for (index, (arg, value)) in args.iter().zip(&values).enumerate() {
                        let name = self.role(*arg, "field")
                            .map(|field| self.text(field)).unwrap_or_else(|| format!("_{index}"));
                        let typ = self.values[*value]
                            .typ
                            .element()
                            .cloned()
                            .unwrap_or(Type::Unknown);
                        fields.push((name.clone(), typ));
                        columns.insert(name, *value);
                        if self.values[*value].data.iter().any(|origin| origin.seed.is_some()) {
                            if let Some(field) = self.role(*arg, "field") {
                                self.fact_event(cached::FactEvent::Readonly(field));
                            }
                        }
                    }
                    let result = self.value(Type::Soa(Box::new(Type::Anonymous(fields))));
                    self.values[result].fields = columns.into();
                    return result;
                }
                "soa_unzip" if !values.is_empty() => {
                    let typ = self.values[values[0]]
                        .typ
                        .element()
                        .cloned()
                        .unwrap_or(Type::Unknown);
                    let names = self.record_fields(&typ)
                        .iter()
                        .map(|(name, _)| name.clone())
                        .collect::<Vec<_>>();
                    let values = names
                        .into_iter()
                        .map(|name| {
                            let column = self.member(values[0], &name);
                            self.copied(column)
                        })
                        .collect();
                    return self.record(r, c, values, false);
                }
                "new_clone" if !values.is_empty() => {
                    let index = args
                        .iter()
                        .position(|arg| {
                            self.kind(*arg) == "FieldValue"
                                && self
                                    .role(*arg, "field")
                                    .is_some_and(|r| self.text(r) == "data")
                        })
                        .unwrap_or(0);
                    let copy = self.copied(values[index]);
                    let result = self.value(Type::Pointer(Box::new(self.values[copy].typ.owned())));
                    self.values[result].target = Some(copy);
                    let error = self.value(Type::scalar("Allocator_Error"));
                    return self.record(r, c, vec![result, error], true);
                }
                "swizzle" if !values.is_empty() => {
                    let item = self.item(values[0]);
                    let item = self.copied(item);
                    let result = self.value(typ);
                    self.values[result].element = Some(item);
                    return result;
                }
                "expand_values" if !values.is_empty() => {
                    let fields = self.record_fields(&self.values[values[0]].typ.clone());
                    let expanded = if !fields.is_empty() {
                        fields
                            .iter()
                            .map(|(name, _)| self.member(values[0], name))
                            .collect::<Vec<_>>()
                    } else if let Type::Array {
                        length: Some(length),
                        ..
                    } = base_type(&self.values[values[0]].typ)
                    {
                        let length = *length;
                        let item = self.item(values[0]);
                        vec![item; usize::try_from(length).unwrap_or(0).min(4096)]
                    } else {
                        vec![]
                    };
                    let results = expanded.into_iter().map(|v| self.copied(v)).collect();
                    return self.record(r, c, results, false);
                }
                "copy" if values.len() > 1 => {
                    let position = |name: &str, fallback| {
                        args.iter()
                            .position(|arg| {
                                self.kind(*arg) == "FieldValue"
                                    && self
                                        .role(*arg, "field")
                                        .is_some_and(|r| self.text(r) == name)
                            })
                            .unwrap_or(fallback)
                    };
                    let dst = values[position("dst", 0)];
                    let src = values[position("src", 1)];
                    self.require(
                        self.values[dst].data.clone(),
                        r,
                        c,
                        "modify destination slice elements",
                        vec![],
                    );
                    let dst = self.item(dst);
                    let src = self.item(src);
                    let src = self.copied(src);
                    self.write(dst, src, c, r, false);
                    return self.value(typ);
                }
                "raw_data" if !values.is_empty() => {
                    self.require(
                        self.values[values[0]].data.clone(),
                        r,
                        c,
                        "obtain a writable pointer into a read-only slice",
                        vec![],
                    );
                    let item = self.item(values[0]);
                    let id = self.value(Type::Pointer(Box::new(self.values[item].typ.owned())));
                    self.values[id].target = Some(item);
                    return id;
                }
                "append" | "clear" | "pop" | "pop_safe" | "pop_front" | "pop_front_safe"
                    if !values.is_empty() =>
                {
                    let target =
                        if matches!(base_type(&self.values[values[0]].typ), Type::Pointer(_)) {
                            self.deref(values[0])
                        } else {
                            values[0]
                        };
                    self.require(
                        self.values[target].data.clone(),
                        r,
                        c,
                        "modify slice storage",
                        vec![],
                    );
                    if name == "append" {
                        for (index, v) in values[1..].iter().enumerate() {
                            let expanded = self.kind(args[index + 1]) == "Unary"
                                && self.op(args[index + 1]) == "**"
                                || self.s(r).node(r.node).spans.get("ellipsis").is_some_and(
                                    |span| {
                                        self.s(r).node(args[index + 1].node).span.start >= span.end
                                    },
                                );
                            let v = if expanded { self.item(*v) } else { *v };
                            let target = self.item(target);
                            let v = self.copied(v);
                            self.write(target, v, c, r, false);
                        }
                    }
                    return if name.starts_with("pop") {
                        let item = self.item(target);
                        self.copied(item)
                    } else {
                        self.value(typ)
                    };
                }
                _ => {}
            }
        }
        let indirect = self.r.symbol(callee).is_some_and(|symbol| {
            let s = &self.r.symbols[symbol];
            s.parameter
                || s.result
                || self.s(s.reference).node(s.reference.node).mutable && !s.immutable
        });
        let mut candidates = if indirect {
            vec![]
        } else {
            self.r.candidates(callee)
        };
        let plans = { let _phase=crate::profile::span("permissions.call_plans"); if indirect {
            vec![]
        } else {
            self.r.call_plans(r, &self.contexts[c].bindings.clone())
        }};
        if !indirect && !plans.is_empty() {
            candidates.retain(|candidate| plans.iter().any(|plan| plan.procedure == *candidate));
        }
        if candidates.is_empty() {
            // Conversions keep slice provenance. Opaque calls must promise
            // nothing about write access or returned storage.
            if self.denotes_type(callee) {
                let v = values
                    .first()
                    .copied()
                    .unwrap_or_else(|| self.value(typ.clone()));
                let id = self.copied(v);
                self.values[id].typ = self.r.shared_type(typ.clone());
                if matches!(base_type(&typ), Type::Slice(_)) {
                    let storage = self.values[v]
                        .storage
                        .iter()
                        .cloned()
                        .map(|mut origin| {
                            if origin.aspect == Aspect::Inline {
                                origin.aspect = Aspect::Data
                            }
                            origin
                        })
                        .collect::<Vec<_>>();
                    self.values[id].data.extend(storage);
                }
                // A checked generic conversion also receives an exact native
                // target proof. Keep borrowed provenance until that proof and
                // result promotion; do not treat a captured type as a callback.
                let checked_generic =
                    self.pure_depth > 0 && matches!(base_type(&typ), Type::Unknown | Type::Generic);
                if matches!(base_type(&typ), Type::Pointer(_)) || opaque(&typ) && !checked_generic {
                    let refs = self.refs(v);
                    self.require(
                        refs,
                        r,
                        c,
                        "erase read-only slice permissions in a conversion",
                        vec![],
                    );
                }
                return id;
            }
            let contract = self.r.signature(callee).map(|signature| self.params(signature)).unwrap_or_default();
            for (index,(arg, v)) in self.expanded_arguments(r, c, &args, &values).into_iter().enumerate() {
                let parameter = if self.kind(arg)=="FieldValue" {
                    self.role(arg,"field").and_then(|label| contract.iter().find(|(name,_)|self.text(*name)==self.text(label)))
                }else {contract.get(index).or_else(||contract.last().filter(|(_,field)|self.s(*field).node(field.node).data["variadic"]==true))};
                if parameter.is_some_and(|(name,_)|self.explicit_ro.contains(name)) {continue;}
                let refs = self.refs(v);
                self.require(
                    refs,
                    arg,
                    c,
                    "verify an indirect or foreign call with a read-only slice",
                    vec![],
                );
            }
            return self.opaque_result(typ, r, &mut HashMap::new());
        }
        let mut results = vec![];
        for candidate in candidates {
            let sig = if self.kind(candidate) == "Procedure" {
                self.role(candidate, "type").unwrap_or(candidate)
            } else {
                candidate
            };
            let params = self.params(sig);
            let mut bound = vec![];
            let mut sites = vec![];
            let expanded = self.expanded_arguments(r, c, &args, &values);
            let mut positional = 0;
            for (name, field) in &params {
                let key = self.text(*name).trim_start_matches('$').to_string();
                let named = args.iter().position(|arg| {
                    self.kind(*arg) == "FieldValue"
                        && self
                            .role(*arg, "field")
                            .is_some_and(|f| self.text(f) == key)
                });
                let selected = if let Some(index) = named {
                    Some((args[index], values[index]))
                } else {
                    let value = expanded.get(positional).copied();
                    positional += 1;
                    value
                };
                let mut value = selected.map(|(_, v)| v).unwrap_or_else(|| {
                    self.role(*field, "default")
                        .map(|default| self.eval(default, c))
                        .unwrap_or_else(|| self.value(Type::Unknown))
                });
                if self.s(*field).node(field.node).data["variadic"] == true {
                    let spread = self.s(r).node(r.node).spans.get("ellipsis").copied();
                    if spread.is_none() {
                        let slice =
                            self.value(Type::Slice(Box::new(self.values[value].typ.owned())));
                        self.values[slice].element = Some(value);
                        for (_, other) in expanded.iter().skip(positional) {
                            let child = self.copied(*other);
                            self.merge(value, child);
                        }
                        value = slice;
                    }
                }
                bound.push(value);
                sites.push(selected.map(|(site, _)| site).unwrap_or(r));
            }
            let mut substitutions = self.contexts[c].bindings.clone();
            let types = bound
                .iter()
                .map(|v| self.values[*v].typ.owned())
                .collect::<Vec<_>>();
            substitutions.extend(self.r.parameter_bindings(sig, &types));
            substitutions.extend(self.r.constant_parameter_bindings(
                sig,
                &sites,
                &self.contexts[c].bindings,
            ));
            if let Some(plan) = plans.iter().find(|plan| plan.procedure == candidate) {
                // Value flow can know a constructed SOA row more precisely
                // than syntax alone. Keep that information when a shared call
                // plan has no concrete binding; resolved type/value arguments
                // still come from the common planner.
                for (name, typ) in &plan.bindings {
                    if typ.resolved() || !substitutions.get(name).is_some_and(Type::resolved) {
                        substitutions.insert(name.clone(), typ.clone());
                    }
                }
            }
            if params
                .iter()
                .zip(&bound)
                .zip(&sites)
                .any(|(((_, field), v), site)| {
                    self.role(*field, "type").is_some_and(|annotation| {
                        if self.kind(annotation) == "TypeidType" {
                            return false;
                        }
                        let actual_site = self.role(*site, "value").unwrap_or(*site);
                        let actual = if self.denotes_type(actual_site) {
                            Type::scalar("typeid")
                        } else {
                            self.values[*v].typ.owned()
                        };
                        !self.r.shape_matches(annotation, &actual, &substitutions)
                    })
                })
            {
                continue;
            }
            for (index, (name, field)) in params.iter().enumerate() {
                let v = bound[index];
                if self.explicit_ro.contains(name) && !matches!(base_type(&self.values[v].typ), Type::Slice(_) | Type::Soa(_) | Type::Unknown | Type::Generic) {
                    self.fact_event(cached::FactEvent::Invalid(r, std::sync::Arc::new("#ro requires a slice parameter in this specialization.".into())));
                }
                if self.explicit.contains(name) {
                    if !matches!(
                        base_type(&self.values[v].typ),
                        Type::Slice(_) | Type::Soa(_) | Type::Unknown | Type::Generic
                    ) {
                        self.fact_event(cached::FactEvent::Invalid(r,
                            std::sync::Arc::new("#mut requires a slice parameter in this specialization.".into())));
                    }
                    self.require(
                        self.values[v].data.clone(),
                        sites[index],
                        c,
                        &format!(
                            "pass a read-only slice to #mut parameter '{}'",
                            self.text(*name)
                        ),
                        vec![*name],
                    );
                }
                let formal = self
                    .role(*field, "type")
                    .map(|r| self.specialized_type(r, &substitutions))
                    .unwrap_or(Type::Unknown);
                let opaque_formal = if self.s(*field).node(field.node).data["variadic"] == true {
                    formal.element().unwrap_or(&formal)
                } else {
                    &formal
                };
                if opaque(opaque_formal) {
                    let refs = self.refs(v);
                    self.require(
                        refs,
                        sites[index],
                        c,
                        "preserve read-only slice permissions through an opaque parameter",
                        vec![*name],
                    );
                }
            }
            if self.kind(candidate) != "Procedure" || self.role(candidate, "body").is_none() {
                for v in bound {
                    let refs = self.refs(v);
                    self.require(
                        refs,
                        r,
                        c,
                        "verify a foreign or bodyless slice call",
                        vec![],
                    );
                }
                for (index, (_, annotation)) in
                    self.r.fields(sig, "results").into_iter().enumerate()
                {
                    let typ = annotation
                        .map(|r| self.specialized_type(r, &substitutions))
                        .unwrap_or(Type::Unknown);
                    let output = self.opaque_result(typ, r, &mut HashMap::new());
                    if let Some(old) = results.get(index).copied() {
                        self.merge(old, output);
                    } else {
                        results.push(output)
                    }
                }
                continue;
            }
            let has_references = bound.iter().any(|value| !self.refs(*value).is_empty());
            let result_fields = self.r.fields(sig, "results");
            let result_references = result_fields.iter().any(|(_, annotation)| {
                annotation.is_none_or(|r| {
                    let typ = self.specialized_type(r, &substitutions);
                    self.type_property(&typ, queries::Property::Slice)
                })
            });
            // As in the reference analyzer, body inspection is needed only
            // when provenance can cross this call, or a generic body has
            // immutable declarations whose permissions need specialization.
            // Purity is checked separately; it must not force this permission
            // pass to traverse scalar-only standard-library implementations.
            let local_checks = self
                .s(sig)
                .descendants(sig.node)
                .any(|id| self.s(sig).node(id).kind == "PolyType")
                && self
                    .s(candidate)
                    .descendants(candidate.node)
                    .any(|id| self.s(candidate).node(id).immutable);
            let inspect_result = result_references && (self.opaque_readonly
                || self.pure_depth > 0 || self.s(candidate).node(candidate.node).data["pure"] == true
                || result_fields.iter().any(|(_, annotation)| annotation.is_some_and(|node| {
                    let typ = self.specialized_type(node, &substitutions);
                    self.type_property(&typ, queries::Property::NestedSlice)
                })));
            let output_view = !has_references && !inspect_result && !local_checks
                && bound.iter().any(|value| {
                    let typ = self.values[*value].typ.owned();
                    self.type_property(&typ, queries::Property::Slice)
                }) && self.can_introduce_view(candidate);
            // A callee can introduce a read-only view through writable output
            // storage even when its arguments initially carry no qualifiers.
            if !has_references && !output_view && !inspect_result && !local_checks {
                for (index, (_, annotation)) in result_fields.into_iter().enumerate() {
                    let typ = annotation
                        .map(|r| self.specialized_type(r, &substitutions))
                        .unwrap_or(Type::Unknown);
                    let result = self.value(typ);
                    if let Some(old) = results.get(index).copied() {
                        self.merge(old, result);
                    } else {
                        results.push(result)
                    }
                }
                continue;
            }
            let old_depth = self.pure_depth;
            if self.s(candidate).node(candidate.node).data["pure"] == true {
                self.pure_depth += 1;
            }
            let contract = has_references || self.pure_depth > 0;
            let summary = self.summary(candidate, substitutions, Some(c), contract);
            self.note_callee(summary);
            self.pure_depth = old_depth;
            let effects = self.contexts[summary].effects.clone();
            for (origin, effect) in effects {
                let mut origins = self.resolve(&origin, &bound);
                if effect.deleting {
                    origins.retain(|origin| origin.seed.is_none());
                }
                let site = sites.get(origin.path.index).copied().unwrap_or(r);
                let mut related = vec![effect.operation];
                related.extend(effect.related);
                let previous_uncertainty = self.uncertain;
                self.uncertain = effect.unresolved.or(previous_uncertainty);
                let previous_deleting = self.deleting;
                self.deleting = effect.deleting;
                self.require_effect(
                    origins,
                    site,
                    c,
                    &format!(
                        "pass a read-only slice to '{}': it may {}",
                        self.text(callee),
                        effect.reason
                    ),
                    related,
                    effect.changes_storage,
                );
                self.uncertain = previous_uncertainty;
                self.deleting = previous_deleting;
            }
            let stores = self.contexts[summary].stores.clone();
            let mut instances = HashMap::new();

            for (path, stored) in stores {
                if let Some(out) = bound.get(path.index) {
                    let out = self.follow(*out, &path.steps);
                    let incoming = self.instantiate(stored, &bound, &mut instances, 0);
                    let target = self.values[out].base.unwrap_or(out);
                    if self.merge(target, incoming) {
                        self.record_stores(out, incoming, c);
                    }
                }
            }
            let outputs = self.contexts[summary].results.clone();

            for (index, output) in outputs.into_iter().enumerate() {
                let output = if self.s(candidate).node(candidate.node).data["pure"] == true
                    || old_depth > 0
                {
                    self.value(self.values[output].typ.clone())
                } else {
                    self.instantiate(output, &bound, &mut instances, 0)
                };
                if let Some(old) = results.get(index).copied() {
                    self.merge(old, output);
                } else {
                    results.push(output)
                }
            }


        }
        if results.is_empty() {
            self.value(typ)
        } else {
            let optional = candidates_optional(self.r, callee);
            self.record(r, c, results, optional)
        }
    }
    fn expanded_arguments(&mut self, r: Ref, c: C, args: &[Ref], values: &[V]) -> Vec<(Ref, V)> {
        let mut result = vec![];
        if self
            .role(r, "callee")
            .is_some_and(|callee| self.op(callee) == "->")
        {
            if let Some(receiver) = self
                .role(r, "callee")
                .and_then(|callee| self.role(callee, "expr"))
            {
                let v = self.eval(receiver, c);
                result.push((receiver, v));
            }
        }
        for (arg, v) in args.iter().copied().zip(values.iter().copied()) {
            if self.kind(arg) == "FieldValue" {
                continue;
            }
            if self.kind(arg) == "Unary" && self.op(arg) == "**" {
                let fields = self.record_fields(&self.values[v].typ.clone());
                if !fields.is_empty() {
                    for (name, _) in fields.iter() {
                        let child = self.member(v, name);
                        result.push((arg, child));
                    }
                } else if let Type::Array {
                    length: Some(length),
                    ..
                } = base_type(&self.values[v].typ)
                {
                    let length = *length;
                    let child = self.item(v);
                    for _ in 0..length.min(4096) {
                        result.push((arg, child));
                    }
                } else {
                    result.push((arg, v));
                }
            } else if args.len() == 1 {
                if let Some(values) = self.contexts[c].expressions.get(&arg) {
                    for v in values {
                        result.push((arg, *v));
                    }
                } else {
                    result.push((arg, v));
                }
            } else {
                result.push((arg, v));
            }
        }
        result
    }
    fn opaque_result(&mut self, typ: Type, seed: Ref, seen: &mut HashMap<Type, V>) -> V {
        // An erased call can hide existing Thor views. When the project has
        // none, retain ordinary Odin callback/foreign return permissions.
        if !self.opaque_readonly { return self.value(typ); }
        if let Some(v) = seen.get(&typ) {
            return *v;
        }
        let id = self.value(typ.clone());
        seen.insert(typ.clone(), id);
        if matches!(base_type(&typ), Type::Slice(_) | Type::Union(..))
            && self.has_slice_data(&typ) {
            self.values[id].data.insert(Origin {
                path: Path {
                    qualified: true,
                    index: 0,
                    steps: Shared::new(),
                },
                aspect: Aspect::Data,
                seed: Some(seed),
            });
        }
        match base_type(&typ) {
            Type::Pointer(elem) => {
                let child = self.opaque_result(*elem.clone(), seed, seen);
                self.values[id].target = Some(child);
            }
            Type::Array { elem, .. }
            | Type::Fixed { elem, .. }
            | Type::Slice(elem)
            | Type::Dynamic(elem)
            | Type::Map { value: elem, .. }
            | Type::Set(elem) => {
                let child = self.opaque_result(*elem.clone(), seed, seen);
                self.values[id].element = Some(child);
            }
            Type::Struct(_) | Type::Record(..) => {
                for (name, typ) in self.record_fields(&typ).iter() {
                    let child = self.opaque_result(typ.clone(), seed, seen);
                    self.values[id].fields.insert(name.clone(), child);
                }
            }
            _ => {}
        }
        id
    }
    fn builtin_name(&self, callee: Ref, seen: &mut HashSet<Ref>) -> Option<String> {
        let _phase = seen.is_empty().then(||crate::profile::span("permissions.builtin_names"));
        if !seen.insert(callee) {
            return None;
        }
        if self.kind(callee) == "Paren" {
            return self
                .role(callee, "expr")
                .and_then(|r| self.builtin_name(r, seen));
        }
        if let Some(symbol) = self.r.symbol(callee) {
            let symbol = &self.r.symbols[symbol];
            if symbol.constant && !symbol.immutable {
                if let Some(node) = symbol.value {
                    return self.builtin_name(
                        Ref {
                            file: symbol.reference.file,
                            node,
                        },
                        seen,
                    );
                }
            }
            return None;
        }
        let text = self.text(callee);
        if self.r.native_builtin(callee, &text) {
            return Some(text);
        }
        if let Some((path, name)) = self.library(callee) {
            if path == "base:builtin"
                && !self
                    .s(callee)
                    .collection_roots
                    .get("base")
                    .is_some_and(|root| {
                        self.s(callee).info["odin_root"]
                            .as_str()
                            .is_none_or(|odin| root != &std::path::Path::new(odin).join("base"))
                    })
            {
                return Some(name);
            }
        }
        None
    }
    fn denotes_type(&mut self, r: Ref) -> bool {
        self.r.denotes_type(r)
    }
    fn verified_library(&self, reference: Ref, path: &str) -> bool {
        verified_library(self.r, reference, path)
    }
    fn library(&self, callee: Ref) -> Option<(String, String)> {
        if let Some(cached) = self.library_names.borrow().get(&callee) {
            return cached.clone();
        }
        let library = self.library_uncached(callee);
        self.library_names.borrow_mut().insert(callee, library.clone());
        library
    }
    fn library_uncached(&self, callee: Ref) -> Option<(String, String)> {
        for path in ["core:fmt", "thor:basic", "thor:graph", "core:slice"] {
            if let Some(name) = crate::evaluation::library_member(self.r, callee, path) {
                return Some((path.into(), name));
            }
        }
        if self.kind(callee) == "Selector" {
            let owner = self.role(callee, "expr")?;
            let field = self.role(callee, "field")?;
            if self.r.lookup(owner, &self.text(owner)).is_some() {
                return None;
            }
            if let Some(path) = self.r.namespace_imports(callee.file, &self.text(owner)).first() {
                return Some((path.clone(), self.text(field)));
            }
        }
        if self.kind(callee) == "Ident" && self.r.lookup(callee, &self.text(callee)).is_none() {
            for selection in &self.s(callee).selections {
                if selection.names.contains(&self.text(callee)) {
                    return Some((
                        crate::lower::unquote(&selection.path).ok()?,
                        self.text(callee),
                    ));
                }
            }
        }
        if self.kind(callee) == "Ident" {
            if let Some(symbol) = self.r.symbol(callee) {
                let target = &self.r.sources[self.r.symbols[symbol].reference.file];
                if target.path.parent() != self.s(callee).path.parent() {
                    for import in &self.s(callee).nodes {
                        if import.kind != "Import" {
                            continue;
                        }
                        let path = crate::lower::unquote(&import.op).ok()?;
                        let directory = if let Some((collection, suffix)) = path.split_once(':') {
                            self.s(callee)
                                .collection_roots
                                .get(collection)
                                .map(|root| root.join(suffix))
                        } else {
                            self.s(callee)
                                .path
                                .parent()
                                .map(|parent| parent.join(&path))
                        };
                        if directory
                            .and_then(|p| crate::project::canonicalize(p).ok())
                            .as_deref()
                            == target.path.parent()
                        {
                            return Some((path, self.text(callee)));
                        }
                    }
                }
            }
        }
        None
    }
    fn condition(&mut self, r: Ref, c: C) -> Option<bool> {
        if let Some(value) = self
            .r
            .context_condition(r, &self.contexts[c].bindings.clone())
        {
            return Some(value);
        }
        match self.kind(r).to_string().as_str() {
            "Call" => {
                let callee = self.role(r, "callee")?;
                let args = self.group(r, "args");
                if self.kind(callee) == "Directive"
                    && self.text(callee) == "#config"
                    && args.len() == 2
                {
                    let key = self.text(args[0]);
                    let prefix = format!("-define:{}=", key.trim_matches('"'));
                    if let Some(value) = self
                        .flags
                        .iter()
                        .find_map(|flag| flag.strip_prefix(&prefix))
                    {
                        return match value {
                            "true" => Some(true),
                            "false" => Some(false),
                            _ => None,
                        };
                    }
                    return self.condition(args[1], c);
                }
                if let Some((path, name)) = self.library(callee) {
                    if path == "base:intrinsics" && name.starts_with("type_is_") && !args.is_empty()
                    {
                        let typ = self.typ(args[0], c);
                        if typ.is_unknown() {
                            return None;
                        }
                        return Some(match name.trim_start_matches("type_is_") {
                            "slice" => matches!(base_type(&typ), Type::Slice(_)),
                            "array" => matches!(base_type(&typ), Type::Array { .. }),
                            "dynamic_array" => matches!(base_type(&typ), Type::Dynamic(_)),
                            "pointer" => matches!(base_type(&typ), Type::Pointer(_)),
                            "struct" => {
                                matches!(base_type(&typ), Type::Struct(_) | Type::Record(..))
                            }
                            "map" => matches!(base_type(&typ), Type::Map { .. }),
                            "union" => matches!(base_type(&typ), Type::Union(..)),
                            "enum" => matches!(base_type(&typ), Type::Enum(_)),
                            "string" => matches!(base_type(&typ), Type::String { .. }),
                            "integer" => {
                                matches!(base_type(&typ),Type::Scalar{name,..}if !name.starts_with('f')&&typ.numeric())
                            }
                            "float" => {
                                matches!(base_type(&typ),Type::Scalar{name,..}if name.starts_with('f')&&typ.numeric())
                            }
                            _ => return None,
                        });
                    }
                }
                None
            }
            "Paren" => self.role(r, "expr").and_then(|r| self.condition(r, c)),
            "Ident" => match self.text(r).as_str() {
                "true" => Some(true),
                "false" => Some(false),
                _ => None,
            },
            "Unary" if self.op(r) == "!" => self
                .role(r, "expr")
                .and_then(|r| self.condition(r, c))
                .map(|x| !x),
            "Binary" => {
                let left = self.role(r, "left")?;
                let right = self.role(r, "right")?;
                match self.op(r).as_str() {
                    "&&" | "||" => {
                        let a = self.condition(left, c);
                        let b = self.condition(right, c);
                        if self.op(r) == "&&" {
                            if a == Some(false) || b == Some(false) {
                                Some(false)
                            } else if a == Some(true) && b == Some(true) {
                                Some(true)
                            } else {
                                None
                            }
                        } else if a == Some(true) || b == Some(true) {
                            Some(true)
                        } else if a == Some(false) && b == Some(false) {
                            Some(false)
                        } else {
                            None
                        }
                    }
                    "==" | "!=" => {
                        let bindings = self.contexts[c].bindings.clone();
                        let a = self.specialized_type(left, &bindings);
                        let b = self.specialized_type(right, &bindings);
                        if a.resolved()
                            && b.resolved()
                            && (self.denotes_type(left) || bindings.contains_key(&self.text(left)))
                            && (self.denotes_type(right)
                                || bindings.contains_key(&self.text(right)))
                        {
                            Some((a == b) == (self.op(r) == "=="))
                        } else {
                            let a = self.r.context_integer(left, &bindings)?;
                            let b = self.r.context_integer(right, &bindings)?;
                            Some((a == b) == (self.op(r) == "=="))
                        }
                    }
                    _ => None,
                }
            }
            _ => None,
        }
    }
}
fn base_type(typ: &Type) -> &Type {
    if let Type::Nominal(_, base) = typ {
        base_type(base)
    } else {
        typ
    }
}
// Union payload provenance survives copies, but copying a union value is not
// itself a writable slice alias. Narrowing checks the selected payload later.
fn writable_alias(typ: &Type) -> bool {
    matches!(
        base_type(typ),
        Type::Slice(_) | Type::Soa(_) | Type::Unknown | Type::Generic
    )
}
fn reference_data(typ: &Type) -> bool {
    matches!(
        base_type(typ),
        Type::Slice(_) | Type::Soa(_) | Type::Unknown | Type::Generic | Type::Union(..)
    )
}
fn references_type(typ: &Type) -> bool {
    matches!(
        base_type(typ),
        Type::Slice(_)
            | Type::Soa(_)
            | Type::Dynamic(_)
            | Type::Map { .. }
            | Type::Set(_)
            | Type::Pointer(_)
            | Type::Unknown
            | Type::Generic
            | Type::Union(..)
    )
}
fn opaque(typ: &Type) -> bool {
    matches!(base_type(typ),Type::Scalar{name,..}if matches!(name.as_str(),"any"|"rawptr"))
}
fn candidates_optional(r: &mut Resolver<'_>, callee: Ref) -> bool {
    r.candidates(callee).into_iter().any(|candidate| {
        let s = &r.sources[candidate.file];
        let sig = if s.node(candidate.node).kind == "Procedure" {
            s.role(candidate.node, "type").unwrap_or(candidate.node)
        } else {
            candidate.node
        };
        let data = &s.node(sig).data;
        data["optional_ok"] == true || data["optional_allocator_error"] == true
    })
}
const READERS: &[&str] = &[
    "len",
    "cap",
    "size_of",
    "align_of",
    "offset_of",
    "offset_of_by_string",
    "type_of",
    "typeid_of",
    "type_info_of",
    "assert",
    "panic",
    "unreachable",
    "min",
    "max",
    "abs",
    "clamp",
    "delete",
    "make",
    "new",
    "free",
    "reserve",
    "resize",
    "complex",
    "quaternion",
    "real",
    "imag",
    "jmag",
    "kmag",
    "conj",
];
// These argument contracts are also used by vet. A checked correction still
// validates writable buffers, but need not inspect platform-specific internals
// behind a reviewed contract to establish that contract on other targets.
pub(crate) fn reviewed_library_contract(
    resolver: &Resolver<'_>, callee: Ref, path: &str, name: &str,
) -> bool {
    let reviewed = match path {
        "core:fmt" => FMT_READERS.contains(&name)
            || ["bprint", "bprintln", "bprintf", "bprintfln"].contains(&name),
        "thor:basic" => ["print", "hash", "try_hash", "_string", "deepclone"].contains(&name),
        "thor:graph" => name == "deepclone",
        "core:slice" => ["clone", "clone_to_dynamic"].contains(&name),
        _ => false,
    };
    reviewed && verified_library(resolver, callee, path)
}

fn verified_library(resolver: &Resolver<'_>, reference: Ref, path: &str) -> bool {
    let _phase = crate::profile::span("permissions.library_verification");
    let Some((collection, suffix)) = path.split_once(':') else { return false; };
    if collection == "thor" {
        let expected = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent().unwrap().join("runtime").join(suffix);
        return resolver.sources[reference.file].collection_roots.get(collection)
            .map(|root| root.join(suffix))
            .is_some_and(|actual| match (
                resolver.canonical_path(&actual), resolver.canonical_path(&expected),
            ) {
                (Some(actual), Some(expected)) => actual == expected,
                _ => false,
            });
    }
    resolver.standard_library(reference, path)
}

const FMT_READERS: &[&str] = &[
    "print",
    "println",
    "printf",
    "printfln",
    "eprint",
    "eprintln",
    "eprintf",
    "eprintfln",
    "aprint",
    "aprintln",
    "aprintf",
    "aprintfln",
    "tprint",
    "tprintln",
    "tprintf",
    "tprintfln",
];

#[cfg(test)]
mod graph_tests {
    use crate::ast::Source;
    use super::*;

    #[test]
    fn writable_parameters_keep_output_locations_without_borrowed_origins() {
        let mut resolver = Resolver::new(&[]);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        let typ = Type::Pointer(Box::new(Type::Slice(Box::new(Type::scalar("int")))));
        let parameter = analysis.fresh(typ.clone(), Some(Path {
            qualified: false, index: 0, steps: Shared::new(),
        }));
        let target = analysis.deref(parameter);
        let references = analysis.refs(parameter);
        if !references.is_empty() || !analysis.values[target].storage.is_empty()
            || analysis.values[target].locations.len() != 1 {
            panic!("writable parameter lost its output location or gained a borrow");
        }
        let seed = Ref { file: 0, node: NodeId(0) };
        let readonly = analysis.freeze(target, seed);
        let actual = analysis.value(typ);
        let result = analysis.instantiate(readonly, &[actual], &mut HashMap::new(), 0);
        let references = analysis.refs(result);
        if !references.iter().any(|origin| origin.seed == Some(seed)) {
            panic!("callee-created readonly view lost its qualifier");
        }
        let references = analysis.refs(actual);
        if !references.is_empty() {
            panic!("returned view changed the caller's original access");
        }
    }

    #[test]
    fn instantiated_union_projections_keep_the_variant_and_shared_slice() {
        let mut resolver = Resolver::new(&[]);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        let site = Ref { file: 0, node: NodeId(0) };
        let slice = Type::Slice(Box::new(Type::scalar("int")));
        let variant = Type::Anonymous(vec![("items".into(), slice.clone())]);
        let actual = analysis.value(Type::Union(site, vec![]));
        let items = analysis.value(slice);
        let readonly = analysis.freeze(items, site);
        analysis.values[actual].fields.insert("items".into(), readonly);
        analysis.values[actual].fields.insert("other_variant_cycle".into(), actual);
        let symbolic = analysis.fresh(variant.clone(), Some(Path {
            qualified: false, index: 0, steps: Shared::new(),
        }));
        let result = analysis.instantiate(symbolic, &[actual], &mut HashMap::new(), 0);
        if analysis.values[result].typ.owned() != variant
            || analysis.values[result].fields.len() != 1 {
            panic!("union projection reconstructed unrelated variants");
        }
        let child = analysis.member(result, "items");
        let origins = analysis.refs(child);
        if !origins.iter().any(|origin| origin.seed == Some(site)) {
            panic!("union projection lost its shared slice permission");
        }
        if !analysis.values[actual].fields.contains_key("other_variant_cycle") {
            panic!("narrowing changed the caller's original union");
        }
    }

    #[test]
    fn resolution_reuses_projections_within_a_read_phase_and_refreshes_after_writes() {
        let mut resolver = Resolver::new(&[]);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        let site = Ref { file: 0, node: NodeId(0) };
        let mut typ = Type::scalar("int");
        for _ in 0..12 { typ = Type::Slice(Box::new(typ)); }
        let actual = analysis.value(typ);
        let readonly = analysis.freeze(actual, site);
        let mut paths = HashMap::new();
        let mut origins = HashMap::new();
        let start = analysis.values.len();
        for _ in 0..100 {
            for depth in 1..12 {
                let origin = Origin {
                    path: Path { qualified: true, index: 0, steps: vec![Step::Item; depth].into() },
                    aspect: Aspect::Data, seed: None,
                };
                let references = analysis.resolve_once(&origin, &[readonly], &mut origins, &mut paths);
                if !references.iter().any(|origin| origin.seed == Some(site)) {
                    panic!("cached projection lost its permission");
                }
            }
        }
        if analysis.values.len() - start > 32 {
            panic!("repeated resolution rebuilt a shared path");
        }
        let symbolic = analysis.fresh(Type::Slice(Box::new(Type::scalar("int"))), Some(Path {
            qualified: false, index: 0, steps: Shared::new(),
        }));
        let writable = analysis.value(Type::Slice(Box::new(Type::scalar("int"))));
        let first = analysis.instantiate(symbolic, &[writable], &mut HashMap::new(), 0);
        if !analysis.refs(first).is_empty() { panic!("writable input acquired a permission"); }
        let frozen = analysis.freeze(writable, site);
        analysis.merge(writable, frozen);
        let second = analysis.instantiate(symbolic, &[writable], &mut HashMap::new(), 0);
        if !analysis.refs(second).iter().any(|origin| origin.seed == Some(site)) {
            panic!("resolution cache survived an intervening write");
        }
    }

    #[test]
    fn origin_compaction_preserves_storage_seeds_and_uncovered_parameters() {
        let root = Path { qualified: true, index: 0, steps: Shared::new() };
        let whole = Origin { path: root.clone(), aspect: Aspect::All, seed: None };
        let storage = Origin { aspect: Aspect::Storage, ..whole.clone() };
        let seed = Origin { seed: Some(Ref { file: 0, node: NodeId(0) }),
            aspect: Aspect::Data, ..whole.clone() };
        let other = Origin { path: Path { index: 1, ..root.clone() },
            aspect: Aspect::Data, seed: None };
        let mut origins: Origins = [whole.clone(), storage.clone(), seed.clone(), other.clone()].into();
        for index in 0..12 {
            origins.insert(Origin { path: Path { steps: vec![Step::Field(format!("child_{index}"))].into(),
                ..root.clone() }, aspect: Aspect::Data, seed: None });
        }
        compact_origins(&mut origins);
        if origins != [whole, storage, seed, other].into() {
            panic!("whole-parameter compaction changed an independent requirement");
        }
        let parent = Origin { path: root.clone(), aspect: Aspect::References, seed: None };
        let mut origins: Origins = [parent.clone()].into();
        for index in 0..12 {
            origins.insert(Origin { path: Path { steps: vec![Step::Field(format!("child_{index}"))].into(),
                ..root.clone() }, aspect: if index % 2 == 0 { Aspect::Data } else { Aspect::References }, seed: None });
        }
        compact_origins(&mut origins);
        if origins != [parent].into() { panic!("subtree compaction retained covered data paths"); }
    }

    #[test]
    fn assigning_a_header_keeps_destination_locations_and_shared_referenced_data() {
        let text = "target";
        let source = Source::from_frontend("/header-write.thor".into(), text.into(),
            serde_json::json!({"nodes":[
                {"kind":"File","start":0,"end":text.len(),"parent":-1},
                {"kind":"Ident","start":0,"end":text.len(),"parent":0}
            ],"features":[],"edits":[],"selections":[]}), true).unwrap();
        let sources = [source];
        let mut resolver = Resolver::new(&sources);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        analysis.contexts.push(Context::new(None, Substitutions::new()));
        let left = Path { qualified:true, index:0, steps:Shared::new() };
        let right = Path { qualified:true, index:1, steps:Shared::new() };
        let typ = Type::Slice(Box::new(Type::scalar("int")));
        let target = analysis.fresh(typ.clone(), Some(left.clone()));
        let value = analysis.fresh(typ, Some(right.clone()));
        let element = analysis.item(value);
        analysis.write(target,value,0,Ref { file:0,node:NodeId(1) },false);
        if analysis.values[target].locations != [left.clone()].into()
            || analysis.contexts[0].stores.keys().cloned().collect::<Vec<_>>() != vec![left] {
            panic!("header assignment copied the source's writable location");
        }
        let original_data = analysis.values[element].base.unwrap_or(element);
        let copied_element = analysis.values[target].element.unwrap();
        let copied_data = analysis.values[copied_element].base.unwrap_or(copied_element);
        if copied_data != original_data
            || analysis.values[value].locations != [right.clone()].into()
            || !analysis.values[target].data.iter().any(|origin| origin.path==right) {
            panic!("header assignment lost shared data or source permissions");
        }
    }

    #[test]
    fn identity_store_elision_retains_qualifier_and_alias_stores() {
        let mut resolver = Resolver::new(&[]);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        analysis.contexts.push(Context::new(None, Substitutions::new()));
        let typ = Type::Slice(Box::new(Type::scalar("int")));
        let path = Path { qualified: false, index: 0, steps: Shared::new() };
        let target = analysis.fresh(typ.clone(), Some(path.clone()));
        let identity = analysis.copied(target);
        analysis.record_stores(target, identity, 0);
        if !analysis.contexts[0].stores.is_empty() { panic!("identity created an output store"); }
        let other = analysis.fresh(typ, Some(Path { index: 1, ..path.clone() }));
        analysis.record_stores(target, other, 0);
        if !analysis.contexts[0].stores.contains_key(&path) { panic!("alias store was discarded"); }
        let seed = Ref { file: 0, node: NodeId(0) };
        let readonly = analysis.freeze(target, seed);
        analysis.record_stores(target, readonly, 0);
        let stored = analysis.contexts[0].stores[&path];
        if !analysis.refs(stored).iter().any(|origin| origin.seed == Some(seed)) {
            panic!("qualifier store was discarded");
        }
    }

    #[test]
    fn deep_symbolic_paths_widen_without_recording_unbounded_stores() {
        let mut resolver = Resolver::new(&[]);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        let path = Path { qualified: true, index: 3, steps: vec![Step::Deref; 17].into() };
        let value = analysis.fresh(Type::Slice(Box::new(Type::scalar("int"))), Some(path));
        if !analysis.values[value].locations.is_empty() { panic!("unbounded store path"); }
        let origins = &analysis.values[value].data;
        if origins.len() != 1 || !origins.iter().any(|origin| origin.aspect == Aspect::All
            && origin.path.index == 3 && origin.path.steps.is_empty()) {
            panic!("deep reference was not widened to its parameter");
        }
    }

    #[test]
    fn whole_parameter_effects_cover_only_equally_strong_data_requirements() {
        let mut resolver = Resolver::new(&[]);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        analysis.contexts.push(Context::new(None, Substitutions::new()));
        let site = Ref { file: 0, node: NodeId(0) };
        let data = Origin { path: Path { qualified: true, index: 0, steps: vec![Step::Field("data".into())].into() },
            aspect: Aspect::Data, seed: None };
        let strong = Origin { path: Path { qualified: true, index: 0, steps: vec![Step::Field("strong".into())].into() },
            aspect: Aspect::Data, seed: None };
        let storage = Origin { aspect: Aspect::Storage, ..data.clone() };
        let all = Origin { path: Path { qualified: true, index: 0, steps: Shared::new() }, aspect: Aspect::All, seed: None };
        analysis.require_effect([data.clone()].into(), site, 0, "inspect", vec![], false);
        analysis.require_effect([strong.clone()].into(), site, 0, "modify", vec![], true);
        analysis.require_effect([storage.clone()].into(), site, 0, "address", vec![], false);
        analysis.require_effect([all.clone()].into(), site, 0, "inspect all", vec![], false);
        analysis.require_effect([data.clone()].into(), site, 0, "inspect again", vec![], false);
        let effects = &analysis.contexts[0].effects;
        if effects.len() != 3 || effects.contains_key(&data)
            || !effects.contains_key(&all) || !effects.contains_key(&strong)
            || !effects.contains_key(&storage) {
            panic!("effect coalescing lost an independent storage requirement");
        }
    }

    #[test]
    fn direct_graph_comparison_matches_full_snapshot_equality_with_cycles_and_aliases() {
        let mut resolver = Resolver::new(&[]);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        analysis.contexts.push(Context::new(None, Substitutions::new()));
        for n in 0..8 {
            let value = analysis.value(Type::scalar("int"));
            analysis.contexts[0].env.insert(n, value);
        }
        let site = Ref { file: 0, node: NodeId(0) };
        let origin = Origin { path: Path { qualified: true, index: 0, steps: Shared::new() }, aspect: Aspect::Data, seed: Some(site) };
        // A cycle and shared child make numeric arena IDs insufficient for equality.
        analysis.values[0].target = Some(0);
        analysis.values[1].fields.insert("shared".into(), 0);
        analysis.contexts[0].results.extend([0, 1]);
        for step in 0..120 {
            let before = analysis.state(0);
            let value = step % 8;
            match step % 12 {
                0 => { analysis.values[value].data.insert(origin.clone()); }
                1 => { analysis.values[value].storage.insert(origin.clone()); }
                2 => { analysis.values[value].locations.insert(origin.path.clone()); }
                3 => { analysis.values[value].path = Some(origin.path.clone()); }
                4 => { analysis.values[value].typ = analysis.r.shared_type(Type::scalar("i64")); }
                5 => { analysis.values[value].fields.insert(format!("field_{step}"), (value + 1) % 8); }
                6 => { analysis.values[value].element = Some((value + 2) % 8); }
                7 => { analysis.values[value].target = Some((value + 3) % 8); }
                8 => { analysis.values[value].base = Some((value + 4) % 8); }
                9 => { analysis.contexts[0].stores.insert(origin.path.clone(), value); }
                10 => { analysis.contexts[0].effects.insert(origin.clone(), Effect {
                    operation: site, unresolved: None, deleting: false,
                    reason: format!("effect_{step}"), related: vec![], changes_storage: true,
                }); }
                _ => { analysis.contexts[0].env.insert(20, value); }
            }
            let exact = analysis.state(0) == before;
            if analysis.state_matches(0, &before) != exact {
                panic!("graph comparison changed at mutation {step}");
            }
            let current = analysis.state(0);
            if !analysis.state_matches(0, &current) { panic!("unchanged graph failed comparison"); }
            // Unreachable allocations have no effect on a context's graph.
            analysis.value(Type::Unknown);
            if !analysis.state_matches(0, &current) { panic!("unreachable value changed graph"); }
        }
    }
}
