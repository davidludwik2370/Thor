//! Slice permissions are properties of reference paths, not of scalar copies.
//! Procedure summaries describe parameter-relative effects and returned aliases.
//! An arena preserves pointer cycles without Rust ownership cycles.
use crate::{
    ast::NodeId,
    semantics::{Ref, Resolver, Substitutions, Type},
};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
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
    index: usize,
    steps: Vec<Step>,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
enum Aspect {
    Data,
    Storage,
    Inline,
    All,
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct Origin {
    path: Path,
    aspect: Aspect,
    seed: Option<Ref>,
}
type Origins = BTreeSet<Origin>;
#[derive(Clone, Debug)]
struct Value {
    typ: Type,
    data: Origins,
    storage: Origins,
    fields: BTreeMap<String, V>,
    element: Option<V>,
    target: Option<V>,
    base: Option<V>,
    path: Option<Path>,
    locations: BTreeSet<Path>,
}
impl Value {
    fn new(typ: Type) -> Self {
        Self {
            typ,
            data: Origins::new(),
            storage: Origins::new(),
            fields: BTreeMap::new(),
            element: None,
            target: None,
            base: None,
            path: None,
            locations: BTreeSet::new(),
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
#[derive(Default)]
pub struct Facts {
    pub issues: HashMap<Ref, Issue>,
    pub mutating: BTreeSet<Ref>,
    pub readonly: BTreeSet<Ref>,
    pub invalid: HashMap<Ref, String>,
    pub pure_faults: BTreeMap<Ref, Vec<Issue>>,
    pub unresolved: BTreeMap<Ref, Ref>,
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
#[derive(PartialEq, Eq)]
struct GraphState {
    roots: Vec<(usize, V)>,
    results: Vec<V>,
    stores: Vec<(Path, V)>,
    values: Vec<GraphValue>,
    effects: Vec<(Origin, Effect)>,
}
#[derive(PartialEq, Eq)]
struct GraphValue {
    typ: Type,
    data: Origins,
    storage: Origins,
    locations: BTreeSet<Path>,
    path: Option<Path>,
    fields: Vec<(String, V)>,
    children: [Option<V>; 3],
}
struct Context {
    proc: Option<Ref>,
    checked: bool,
    bindings: Substitutions,
    env: HashMap<usize, V>,
    effects: BTreeMap<Origin, Effect>,
    stores: BTreeMap<Path, V>,
    results: Vec<V>,
    expressions: HashMap<Ref, Vec<V>>,
    optional: HashSet<Ref>,
    active: bool,
    controls: Vec<Ref>,
    named: Vec<Option<usize>>,
}
impl Context {
    fn new(proc: Option<Ref>, bindings: Substitutions) -> Self {
        Self {
            proc,
            checked: false,
            bindings,
            env: HashMap::new(),
            effects: BTreeMap::new(),
            stores: BTreeMap::new(),
            results: vec![],
            expressions: HashMap::new(),
            optional: HashSet::new(),
            active: false,
            controls: vec![],
            named: vec![],
        }
    }
}
pub struct Privileges<'a, 'r> {
    r: &'r mut Resolver<'a>,
    values: Vec<Value>,
    contexts: Vec<Context>,
    summaries: HashMap<SummaryKey, C>,
    globals: HashMap<usize, V>,
    global_active: HashSet<usize>,
    explicit: HashSet<Ref>,
    facts: Facts,
    changed: bool,
    depth: usize,
    flags: Vec<String>,
    pure_depth: usize,
    uncertain: Option<Ref>,
    deleting: bool,
}
#[derive(PartialEq, Eq, Hash)]
struct SummaryKey {
    procedure: Ref,
    bindings: Vec<(String, Type)>,
    checked: bool,
}
impl<'a, 'r> Privileges<'a, 'r> {
    pub fn analyze(r: &'r mut Resolver<'a>, flags: &[String]) -> Facts {
        let enabled = r.sources.iter().any(|s| {
            s.nodes
                .iter()
                .any(|n| n.immutable || n.data["pure"] == true)
                || s.info["mut_parameters"]
                    .as_array()
                    .is_some_and(|x| !x.is_empty())
        });
        if !enabled {
            return Facts::default();
        }
        let mut this = Self {
            r,
            values: vec![],
            contexts: vec![],
            summaries: HashMap::new(),
            globals: HashMap::new(),
            global_active: HashSet::new(),
            explicit: HashSet::new(),
            facts: Facts::default(),
            changed: false,
            depth: 0,
            flags: flags.to_vec(),
            pure_depth: 0,
            uncertain: None,
            deleting: false,
        };
        this.annotations();
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
            if this.s(proc).path.starts_with(&toolkit)
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
            if !generic || this.s(proc).node(proc.node).data["pure"] == true {
                this.pure_depth = usize::from(this.s(proc).node(proc.node).data["pure"] == true);
                this.summary(proc, Substitutions::new());
                this.pure_depth = 0;
            }
        }
        // Recursive summaries form a finite monotone effect graph. Revisit the
        // actual roots after callees acquire effects on the first traversal.
        for _ in 0..24 {
            this.changed = false;
            let before = (0..this.contexts.len())
                .map(|c| this.state(c))
                .collect::<Vec<_>>();
            let contexts = (0..this.contexts.len())
                .filter(|c| this.contexts[*c].proc.is_some())
                .collect::<Vec<_>>();
            for c in contexts {
                let proc = this.contexts[c].proc.unwrap();
                if let Some(body) = this.role(proc, "body") {
                    this.pure_depth = usize::from(this.contexts[c].checked);
                    this.statement(body, c);
                    this.pure_depth = 0;
                }
            }
            let after = (0..this.contexts.len())
                .map(|c| this.state(c))
                .collect::<Vec<_>>();
            if before == after {
                break;
            }
        }
        for c in 0..this.contexts.len() {
            let Some(proc) = this.contexts[c].proc else {
                continue;
            };
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
                    if this.unresolved_value_type(&typ, &mut HashSet::new()) {
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
        this.facts
    }
    fn s(&self, r: Ref) -> &'a crate::ast::Source {
        &self.r.sources[r.file]
    }
    fn state(&self, c: C) -> GraphState {
        fn register(v: V, ids: &mut HashMap<V, V>, queue: &mut Vec<V>) -> V {
            if let Some(id) = ids.get(&v) {
                return *id;
            }
            let id = queue.len();
            queue.push(v);
            ids.insert(v, id);
            id
        }
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
            .map(|(symbol, v)| (symbol, register(v, &mut ids, &mut queue)))
            .collect();
        let results = context
            .results
            .iter()
            .map(|v| register(*v, &mut ids, &mut queue))
            .collect();
        let stores = context
            .stores
            .iter()
            .map(|(path, v)| (path.clone(), register(*v, &mut ids, &mut queue)))
            .collect();
        let mut values = vec![];
        let mut cursor = 0;
        while cursor < queue.len() {
            let v = &self.values[queue[cursor]];
            cursor += 1;
            let fields = v
                .fields
                .iter()
                .map(|(name, v)| (name.clone(), register(*v, &mut ids, &mut queue)))
                .collect();
            let children =
                [v.element, v.target, v.base].map(|v| v.map(|v| register(v, &mut ids, &mut queue)));
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
        GraphState {
            roots,
            results,
            stores,
            values,
            effects: context
                .effects
                .iter()
                .map(|(origin, effect)| (origin.clone(), effect.clone()))
                .collect(),
        }
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
    fn value(&mut self, typ: Type) -> V {
        let id = self.values.len();
        self.values.push(Value::new(typ));
        id
    }
    fn fresh(&mut self, typ: Type, path: Option<Path>) -> V {
        let id = self.value(typ);
        self.values[id].path = path.clone();
        if let Some(path) = path {
            self.values[id].locations.insert(path.clone());
            if reference_data(&self.values[id].typ)
                || self.pure_depth > 0
                    && matches!(base_type(&self.values[id].typ), Type::String { .. })
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
        self.values[id].storage.extend(origins.clone());
        if reference_data(&self.values[v].typ) {
            self.values[id].data.extend(origins);
        }
        id
    }
    fn merge(&mut self, dst: V, src: V) -> bool {
        self.merge_seen(dst, src, &mut HashSet::new())
    }
    fn merge_seen(&mut self, dst: V, src: V, seen: &mut HashSet<(V, V)>) -> bool {
        if dst == src || !seen.insert((dst, src)) {
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
            set.extend(source);
            change |= n != set.len();
        }
        let n = self.values[dst].locations.len();
        self.values[dst].locations.extend(incoming.locations);
        change |= n != self.values[dst].locations.len();
        for (name, v) in incoming.fields {
            if let Some(existing) = self.values[dst].fields.get(&name).copied() {
                change |= self.merge_seen(existing, v, seen)
            } else {
                self.values[dst].fields.insert(name, v);
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
        if let Some(path) = self.r.member_path(&self.values[v].typ.clone(), name) {
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
                elem: Box::new(self.values[item].typ.clone()),
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
                let typ = self.r.member_type(&self.values[v].typ.clone(), name);
                let path = self.values[v].path.clone().map(|mut p| {
                    p.steps.push(Step::Field(name.into()));
                    p
                });
                let child = self.fresh(typ, path);
                self.values[v].fields.insert(name.into(), child);
                child
            };
            return self.qualify(child, self.values[v].data.clone());
        }
        if let Some(child) = self.values[v].fields.get(name) {
            return *child;
        }
        let typ = self.r.member_type(&self.values[v].typ.clone(), name);
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
                for (name, _) in self.r.record_fields(&elem) {
                    let column = self.member(v, &name);
                    let item = self.item(column);
                    self.values[result].fields.insert(name, item);
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
        if let Some(path) = path {
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
        if let Some(result) = seen.get(&v) {
            return *result;
        }
        let old = self.values[v].clone();
        let result = self.value(old.typ.clone());
        seen.insert(v, result);
        self.values[result] = old.clone();
        self.values[result].fields.clear();
        for (name, child) in old.fields {
            let child = self.snapshot(child, seen);
            self.values[result].fields.insert(name, child);
        }
        self.values[result].element = old.element.map(|child| self.snapshot(child, seen));
        self.values[result].target = old.target.map(|child| self.snapshot(child, seen));
        self.values[result].base = old.base.map(|child| self.snapshot(child, seen));
        result
    }
    fn copy_seen(&mut self, v: V, seen: &mut HashMap<V, V>) -> V {
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
                let fields = self.r.record_fields(&old.typ);
                for (name, _) in fields {
                    let child = self.member(v, &name);
                    let copy = self.copy_seen(child, seen);
                    self.values[id].fields.insert(name, copy);
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
                index: 0,
                steps: vec![],
            },
            aspect: Aspect::Data,
            seed: Some(seed),
        };
        if reference_data(&self.values[v].typ) {
            self.values[v].data.insert(origin.clone());
            self.values[v].storage.insert(origin.clone());
        }
        match base_type(&self.values[v].typ).clone() {
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                self.values[v].storage.insert(Origin {
                    aspect: Aspect::Inline,
                    ..origin
                });
                let fields = self.r.record_fields(&self.values[v].typ.clone());
                for (name, _) in fields {
                    let child = self.member(v, &name);
                    self.freeze_seen(child, seed, seen);
                    self.values[v].fields.insert(name, child);
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
        self.refs_seen(v, &mut HashSet::new(), 0)
    }
    fn refs_seen(&mut self, v: V, seen: &mut HashSet<V>, depth: usize) -> Origins {
        if !seen.insert(v) {
            return Origins::new();
        }
        let mut result = self.values[v].data.clone();
        if depth > 16 {
            if let Some(path) = self.values[v].path.clone() {
                result.insert(Origin {
                    path,
                    aspect: Aspect::All,
                    seed: None,
                });
            }
            return result;
        }
        if self.values[v].path.is_some()
            && self.contains_slice(&self.values[v].typ.clone(), &mut HashSet::new())
        {
            match base_type(&self.values[v].typ).clone() {
                Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                    let fields = self.r.record_fields(&self.values[v].typ.clone());
                    for (name, _) in fields {
                        self.member(v, &name);
                    }
                }
                Type::Array { .. }
                | Type::Fixed { .. }
                | Type::Slice(_)
                | Type::Dynamic(_)
                | Type::Map { .. }
                | Type::Set(_) => {
                    self.item(v);
                }
                Type::Pointer(_) => {
                    self.deref(v);
                }
                Type::Soa(elem) => {
                    for (name, _) in self.r.record_fields(&elem) {
                        self.member(v, &name);
                    }
                }
                _ => {}
            }
        }
        let mut children = self.values[v].fields.values().copied().collect::<Vec<_>>();
        children.extend(self.values[v].element);
        children.extend(self.values[v].target);
        for child in children {
            result.extend(self.refs_seen(child, seen, depth + 1));
        }
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
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(typ)
                .into_iter()
                .any(|(_, typ)| self.contains_slice(&typ, seen)),
            Type::Union(owner) => {
                let variants = self.group(*owner, "variants");
                variants.into_iter().any(|r| {
                    let typ = self.r.specialized_type(r, &Substitutions::new());
                    self.contains_slice(&typ, seen)
                })
            }
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
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(typ)
                .iter()
                .any(|(_, typ)| self.unresolved_value_type(typ, seen)),
            Type::Union(owner) => self
                .r
                .union_variants(*owner)
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
                if let Some(conditional) = unresolved {
                    self.facts.unresolved.insert(operation, conditional);
                    continue;
                }
                let name = self.text(seed);
                let message = format!("Cannot {reason}: read-only slice access from '{name}'.");
                self.facts.issues.entry(operation).or_insert(Issue {
                    operation,
                    seed,
                    message,
                    related: related.clone(),
                });
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
                if !effects.contains_key(&origin)
                    || effects.get(&origin).is_some_and(|old| {
                        (!old.changes_storage && incoming.changes_storage)
                            || (old.deleting && !incoming.deleting && incoming.changes_storage)
                    })
                {
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
        let v = self.follow(*v, &origin.path.steps);
        if origin.aspect == Aspect::Data {
            self.values[v].data.clone()
        } else {
            self.values[v].storage.clone()
        }
    }
    fn instantiate(&mut self, v: V, args: &[V], seen: &mut HashMap<V, V>, depth: usize) -> V {
        if let Some(id) = seen.get(&v) {
            return *id;
        }
        let old = self.values[v].clone();
        if let Some(path) = &old.path {
            if let Some(actual) = args.get(path.index) {
                let actual = self.follow(*actual, &path.steps);
                let id = self.copied(actual);
                seen.insert(v, id);
                for origin in &old.data {
                    let origins = self.resolve(origin, args);
                    self.values[id].data.extend(origins);
                }
                return id;
            }
        }
        let id = self.value(old.typ);
        seen.insert(v, id);
        for origin in old.data {
            let origins = self.resolve(&origin, args);
            self.values[id].data.extend(origins);
        }
        for origin in old.storage {
            let origins = self.resolve(&origin, args);
            self.values[id].storage.extend(origins);
        }
        if depth > 16 {
            return id;
        }
        for (name, child) in old.fields {
            let child = self.instantiate(child, args, seen, depth + 1);
            self.values[id].fields.insert(name, child);
        }
        if let Some(child) = old.element {
            let child = self.instantiate(child, args, seen, depth + 1);
            self.values[id].element = Some(child);
        }
        if let Some(child) = old.target {
            let child = self.instantiate(child, args, seen, depth + 1);
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
        for (file, s) in self.r.sources.iter().enumerate() {
            for row in s.info["mut_parameters"].as_array().into_iter().flatten() {
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
                        self.explicit.insert(reference);
                        let valid = s.ancestors(field).any(|ancestor| {
                            s.node(ancestor).kind == "ProcedureType"
                                && s.node(field).parent == s.role(ancestor, "params")
                        });
                        let typ = s
                            .role(field, "type")
                            .map(|node| {
                                self.r
                                    .specialized_type(Ref { file, node }, &Substitutions::new())
                            })
                            .unwrap_or(Type::Unknown);
                        if !valid
                            || !matches!(
                                base_type(&typ),
                                Type::Slice(_) | Type::Unknown | Type::Generic
                            )
                        {
                            self.facts
                                .invalid
                                .insert(reference, "#mut requires a slice parameter.".into());
                        }
                    }
                }
            }
        }
    }
    fn summary(&mut self, proc: Ref, bindings: Substitutions) -> C {
        let key = SummaryKey {
            procedure: proc,
            bindings: bindings
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            checked: self.pure_depth > 0,
        };
        if let Some(c) = self.summaries.get(&key) {
            return *c;
        }
        let c = self.contexts.len();
        self.contexts
            .push(Context::new(Some(proc), bindings.clone()));
        self.contexts[c].checked = self.pure_depth > 0;
        self.summaries.insert(key, c);
        let sig = self.role(proc, "type").unwrap_or(proc);
        for (index, (name, field)) in self.params(sig).into_iter().enumerate() {
            let typ = self
                .role(field, "type")
                .map(|r| self.r.specialized_type(r, &bindings))
                .unwrap_or_else(|| {
                    self.role(field, "default")
                        .map(|default| self.typ(default, c).defaulted())
                        .unwrap_or(Type::Unknown)
                });
            let v = self.fresh(
                typ,
                Some(Path {
                    index,
                    steps: vec![],
                }),
            );
            if let Some(symbol) = self.r.symbol(name) {
                self.contexts[c].env.insert(symbol, v);
            }
            if self.explicit.contains(&name) {
                self.require(
                    [Origin {
                        path: Path {
                            index,
                            steps: vec![],
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
                    .map(|r| self.r.specialized_type(r, &bindings))
                    .unwrap_or(Type::Unknown);
                let names = self.group(field, "names");
                for name in if names.is_empty() {
                    vec![None]
                } else {
                    names.into_iter().map(Some).collect()
                } {
                    let v = self.value(typ.clone());
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
                self.statement(body, c);
            }
        } else {
            for (index, (name, _)) in self.params(sig).into_iter().enumerate() {
                self.require(
                    [Origin {
                        path: Path {
                            index,
                            steps: vec![],
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
        c
    }
    fn typ(&mut self, r: Ref, c: C) -> Type {
        if let Some(symbol) = self.r.symbol(r) {
            if let Some(v) = self.contexts[c]
                .env
                .get(&symbol)
                .or(self.globals.get(&symbol))
            {
                return self.values[*v].typ.clone();
            }
        }
        match self.kind(r) {
            "Selector" if self.r.symbol(r).is_none() => {
                if let (Some(base), Some(field)) = (self.role(r, "expr"), self.role(r, "field")) {
                    let typ = self.typ(base, c);
                    return self.r.member_type(&typ, &self.text(field));
                }
            }
            "Index" | "Deref" => {
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
                    return self
                        .r
                        .specialized_type(annotation, &self.contexts[c].bindings.clone());
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
                                return self.r.member_type(&typ, &field);
                            }
                            if matches!(base_type(&typ), Type::Struct(_) | Type::Record(..)) {
                                let index = self
                                    .group(parent, "elems")
                                    .iter()
                                    .position(|child| child.node == r.node)
                                    .unwrap_or(0);
                                return self
                                    .r
                                    .record_fields(&typ)
                                    .get(index)
                                    .map(|(_, typ)| typ.clone())
                                    .unwrap_or(Type::Unknown);
                            }
                            return typ.element().cloned().unwrap_or(Type::Unknown);
                        }
                        if self.kind(parent) == "Declaration" {
                            if let Some(annotation) = self.role(parent, "type") {
                                return self.r.specialized_type(
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
        self.r.specialized_type(r, &bindings)
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
        let typ = self.typ(r, c);
        let kind = self.kind(r).to_string();
        if kind == "Call" {
            return self.call(r, c);
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
            "Paren" | "OrReturn" | "OrBranch" | "OrPanic" | "AutoCast" => {
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
                        self.facts.readonly.insert(field);
                    }
                }
                v
            }
            "Index" => {
                self.eval_role(r, "index", c);
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
                    Type::Slice(Box::new(self.values[elem].typ.clone()))
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
                        let id = self.value(Type::Pointer(Box::new(self.values[v].typ.clone())));
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
                self.values[id].typ = typ.clone();
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
                    self.values[id].data.extend(origins);
                }
                id
            }
            "Compound" => {
                let id = self.value(typ.clone());
                let fields = self.r.record_fields(&typ);
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
                        self.values[incoming].typ = expected.clone();
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
            "SwitchResults" => {
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
                let output = self.eval_role(r, "output", c);
                let output = if self.s(r).node(r.node).data["spread"] == true {
                    self.item(output)
                } else {
                    output
                };
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
                let id = self.value(Type::Pointer(Box::new(self.values[incoming].typ.clone())));
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
                        self.values[v].typ = self.r.specialized_type(annotation, &bindings);
                    }
                    if self.s(r).node(r.node).immutable {
                        v = self.freeze(v, name)
                    } else if self.s(r).node(r.node).mutable {
                        if reference_data(&self.values[v].typ) {
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
                    if reference_data(&self.values[target].typ) && !named {
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
                let mut snapshots = HashMap::new();
                for (index, v) in values.into_iter().enumerate() {
                    let v = self.copied(v);
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
                            self.r
                                .specialized_type(variants[0], &self.contexts[c].bindings.clone())
                        } else {
                            self.values[original].typ.clone()
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
        self.values[result].typ = typ.clone();
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
        result
    }
    fn local_header(&self, r: Ref, c: C) -> bool {
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
            self.merge(dst, v);
        }
        for path in self.values[target].locations.clone() {
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
        if builtin && READERS.contains(&name.as_str())
            || library.as_ref().is_some_and(|(path, name)| {
                path == "core:fmt"
                    && self.verified_library(callee, path)
                    && FMT_READERS.contains(&name.as_str())
                    || path == "thor:basic"
                        && self.verified_library(callee, path)
                        && ["print", "hash", "try_hash", "_string", "deepclone"]
                            .contains(&name.as_str())
            })
        {
            return self.value(typ);
        }
        if let Some((path, name)) = &library {
            if path == "core:fmt"
                && self.verified_library(callee, path)
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
                && self.verified_library(callee, path)
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
                    for (arg, value) in args.iter().zip(&values) {
                        if let Some(field) = self.role(*arg, "field") {
                            let name = self.text(field);
                            let typ = self.values[*value]
                                .typ
                                .element()
                                .cloned()
                                .unwrap_or(Type::Unknown);
                            fields.push((name.clone(), typ));
                            columns.insert(name, *value);
                        }
                    }
                    let result = self.value(Type::Soa(Box::new(Type::Anonymous(fields))));
                    self.values[result].fields = columns;
                    return result;
                }
                "soa_unzip" if !values.is_empty() => {
                    let typ = self.values[values[0]]
                        .typ
                        .element()
                        .cloned()
                        .unwrap_or(Type::Unknown);
                    let names = self
                        .r
                        .record_fields(&typ)
                        .into_iter()
                        .map(|(name, _)| name)
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
                    let result = self.value(Type::Pointer(Box::new(self.values[copy].typ.clone())));
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
                    let fields = self.r.record_fields(&self.values[values[0]].typ.clone());
                    let expanded = if !fields.is_empty() {
                        fields
                            .into_iter()
                            .map(|(name, _)| self.member(values[0], &name))
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
                    let id = self.value(Type::Pointer(Box::new(self.values[item].typ.clone())));
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
        let plans = if indirect {
            vec![]
        } else {
            self.r.call_plans(r, &self.contexts[c].bindings.clone())
        };
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
                self.values[id].typ = typ.clone();
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
                if matches!(base_type(&typ), Type::Pointer(_)) || opaque(&typ) {
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
            for (arg, v) in self.expanded_arguments(r, c, &args, &values) {
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
                            self.value(Type::Slice(Box::new(self.values[value].typ.clone())));
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
                .map(|v| self.values[*v].typ.clone())
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
                            self.values[*v].typ.clone()
                        };
                        !self.r.shape_matches(annotation, &actual, &substitutions)
                    })
                })
            {
                continue;
            }
            for (index, (name, field)) in params.iter().enumerate() {
                let v = bound[index];
                if self.explicit.contains(name) {
                    if !matches!(
                        base_type(&self.values[v].typ),
                        Type::Slice(_) | Type::Unknown | Type::Generic
                    ) {
                        self.facts.invalid.insert(
                            r,
                            "#mut requires a slice parameter in this specialization.".into(),
                        );
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
                    .map(|r| self.r.specialized_type(r, &substitutions))
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
                        .map(|r| self.r.specialized_type(r, &substitutions))
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
                    let typ = self.r.specialized_type(r, &substitutions);
                    self.contains_slice(&typ, &mut HashSet::new())
                })
            });
            let local_checks = self
                .s(candidate)
                .descendants(candidate.node)
                .any(|id| self.s(candidate).node(id).immutable);
            if !has_references
                && !result_references
                && !local_checks
                && self.pure_depth == 0
                && self.s(candidate).node(candidate.node).data["pure"] != true
            {
                for (index, (_, annotation)) in result_fields.into_iter().enumerate() {
                    let typ = annotation
                        .map(|r| self.r.specialized_type(r, &substitutions))
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
            let summary = self.summary(candidate, substitutions);
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

            for (path, stored) in stores {
                if let Some(out) = bound.get(path.index) {
                    let out = self.follow(*out, &path.steps);
                    let incoming = self.instantiate(stored, &bound, &mut HashMap::new(), 0);
                    let target = self.values[out].base.unwrap_or(out);
                    self.merge(target, incoming);
                }
            }
            let outputs = self.contexts[summary].results.clone();

            let mut instances = HashMap::new();
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
                let fields = self.r.record_fields(&self.values[v].typ.clone());
                if !fields.is_empty() {
                    for (name, _) in fields {
                        let child = self.member(v, &name);
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
        if let Some(v) = seen.get(&typ) {
            return *v;
        }
        let id = self.value(typ.clone());
        seen.insert(typ.clone(), id);
        if matches!(base_type(&typ), Type::Slice(_) | Type::Union(_)) {
            self.values[id].data.insert(Origin {
                path: Path {
                    index: 0,
                    steps: vec![],
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
                for (name, typ) in self.r.record_fields(&typ) {
                    let child = self.opaque_result(typ, seed, seen);
                    self.values[id].fields.insert(name, child);
                }
            }
            _ => {}
        }
        id
    }
    fn builtin_name(&self, callee: Ref, seen: &mut HashSet<Ref>) -> Option<String> {
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
        if self.kind(r) == "Call" {
            if let Some(callee) = self.role(r, "callee") {
                let typ = self.r.specialized_type(callee, &Substitutions::new());
                return matches!(typ, Type::Struct(_) | Type::Record(..));
            }
        }
        if matches!(
            self.kind(r),
            "ArrayType" | "DynamicArrayType" | "PointerType" | "MapType" | "SetType" | "StructType"
        ) {
            return true;
        }
        if self.r.symbol(r).is_some_and(|symbol| {
            self.r.symbols[symbol].constant
                && self.r.symbols[symbol].value.is_some_and(|v| {
                    matches!(
                        self.r.sources[self.r.symbols[symbol].reference.file]
                            .node(v)
                            .kind
                            .as_str(),
                        "ArrayType"
                            | "DynamicArrayType"
                            | "PointerType"
                            | "MapType"
                            | "SetType"
                            | "StructType"
                            | "EnumType"
                    )
                })
        }) {
            return true;
        }
        self.r.symbol(r).is_none()
            && matches!(
                self.text(r).as_str(),
                "int"
                    | "uint"
                    | "i8"
                    | "i16"
                    | "i32"
                    | "i64"
                    | "i128"
                    | "u8"
                    | "u16"
                    | "u32"
                    | "u64"
                    | "u128"
                    | "f16"
                    | "f32"
                    | "f64"
                    | "bool"
                    | "rune"
                    | "byte"
                    | "string"
                    | "cstring"
                    | "rawptr"
                    | "any"
            )
    }
    fn verified_library(&self, reference: Ref, path: &str) -> bool {
        let Some((collection, suffix)) = path.split_once(':') else {
            return false;
        };
        if collection == "thor" {
            let expected = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .join("runtime")
                .join(suffix);
            return self
                .s(reference)
                .collection_roots
                .get(collection)
                .map(|root| root.join(suffix))
                .is_some_and(|actual| {
                    match (
                        std::fs::canonicalize(actual),
                        std::fs::canonicalize(expected),
                    ) {
                        (Ok(actual), Ok(expected)) => actual == expected,
                        _ => false,
                    }
                });
        }
        self.r.standard_library(reference, path)
    }
    fn library(&self, callee: Ref) -> Option<(String, String)> {
        if self.kind(callee) == "Selector" {
            let owner = self.role(callee, "expr")?;
            let field = self.role(callee, "field")?;
            if self.r.lookup(owner, &self.text(owner)).is_some() {
                return None;
            }
            for node in &self.s(callee).nodes {
                if node.kind != "Import" {
                    continue;
                }
                let path = crate::lower::unquote(&node.op).ok()?;
                let alias = self.s(callee).names.get(node.name);
                let alias = if alias.is_empty() {
                    path.rsplit(['/', ':']).next().unwrap_or("")
                } else {
                    alias
                };
                if alias == self.text(owner) {
                    return Some((path, self.text(field)));
                }
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
                            .and_then(|p| std::fs::canonicalize(p).ok())
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
                            "union" => matches!(base_type(&typ), Type::Union(_)),
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
                        let a = self.r.specialized_type(left, &bindings);
                        let b = self.r.specialized_type(right, &bindings);
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
fn reference_data(typ: &Type) -> bool {
    matches!(
        base_type(typ),
        Type::Slice(_) | Type::Soa(_) | Type::Unknown | Type::Generic | Type::Union(_)
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
            | Type::Union(_)
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
