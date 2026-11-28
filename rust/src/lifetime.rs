//! Opt-in lifetime/cleanup proof. No lowering, instrumentation, or source edits.
//!
//! Storage is an arena graph: copying an inline value copies its reference
//! edges, not the objects those edges address. Object identity survives stores
//! through outputs, containers, aliases and calls. Joins retain alternatives;
//! uncertainty is never silently upgraded to a proof. Work and graph budgets
//! produce explicit unverified diagnostics rather than truncating a proof.
use crate::{
    ast::{NodeId, Span},
    project::Project,
    semantics::{Constant, Ref, Resolver, Substitutions, Type},
};
use serde_json::{json, Value as Json};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::sync::Arc;
#[path = "lifetime_contracts.rs"]
mod contracts;
#[path = "lifetime_foreign.rs"]
mod foreign;
#[path = "lifetime_loops.rs"]
mod loops;
#[path = "lifetime_models.rs"]
mod models;
#[path = "lifetime_pure_callbacks.rs"]
mod pure_callbacks;
#[path = "lifetime_recursive.rs"]
mod recursive;
#[path = "lifetime_resources.rs"]
mod resources;
#[path = "lifetime_owned.rs"]
mod owned;
#[path = "lifetime_summary.rs"]
mod summaries;
#[path = "lifetime_scratch.rs"]
mod scratch;
#[path = "lifetime_conditions.rs"]
mod conditions;
#[path = "lifetime_threads.rs"]
mod threads;
#[path = "lifetime_allocators.rs"]
mod allocators;
#[path = "lifetime_buffers.rs"]
mod buffers;
#[path = "lifetime_projections.rs"]
mod projections;
#[path = "lifetime_pairs.rs"]
mod pairs;
#[path = "lifetime_demand.rs"]
mod demand;
#[path = "lifetime_memory.rs"]
mod memory;
#[path = "lifetime_workers.rs"]
mod workers;

const LIVE: u8 = 1;
const DEAD: u8 = 2;
const ABSENT: u8 = 4;
const MAX_STEPS: usize = 4_096;
const MAX_PROJECT_STEPS: usize = 65_536;
const MAX_OBJECTS: usize = 512;
const MAX_CALLS: usize = 64;
const MAX_DEPTH: usize = 32;
const MAX_LOOP: usize = 4;
const MAX_VALUE_DEPTH: usize = 32;

type Id = usize;
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
enum Key {
    Field(String),
    Item(Option<i128>),
    Range(i128, Option<i128>),
    // Byte offsets from reviewed raw-pointer operations. Ordinary Item offsets
    // remain element indices, including slices of types wider than a byte.
    Byte(Option<i128>),
    // Current element width of a reviewed pointer/slice reinterpretation.
    View(i128),
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct Place {
    id: Id,
    path: Vec<Key>,
    interior: bool,
    start: Option<i128>,
    end: Option<i128>,
}
impl Place {
    fn root(id: Id) -> Self {
        Self {
            id,
            path: vec![],
            interior: false,
            start: Some(0),
            end: None,
        }
    }
    fn child(&self, key: Key) -> Self {
        let mut p = self.clone();
        let key = match key {
            key @ (Key::Byte(_)|Key::View(_)) if p.start!=Some(0)=>{
                p.path.push(Key::Item(p.start));
                key
            },
            Key::Item(Some(index)) => Key::Item(p.start.and_then(|start| start.checked_add(index))),
            Key::Item(None) if p.start != Some(0) || p.end.is_some() => {
                Key::Range(p.start.unwrap_or(0), p.end)
            }
            key => key,
        };
        p.path.push(key);
        p.start = Some(0);
        p.end = None;
        p
    }
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
enum Allocator {
    Heap,
    Region(Arc<Place>),
    Temporary(usize),
    Unknown,
}
#[derive(Clone, Default, Debug, PartialEq, Eq)]
struct Value {
    refs: BTreeSet<Place>,
    fields: BTreeMap<Key, Value>,
    procedures: BTreeSet<Ref>,
    allocator: Option<Allocator>,
    unknown: bool,
    // Only parameter expansion can introduce a symbolic borrowed tail. Opaque
    // results and mutated caller state must never inherit that precondition.
    caller_tail: bool,
    // Bounded predicates, never ownership edges. Only joins of closed choices
    // may be narrowed; opaque mutation discards the predicate.
    truth: Option<bool>,
    file_status: Option<Id>,
    choice: bool,
    may_nil: bool,
    nil_procedure: bool,
    absent_when:BTreeMap<Place,BTreeSet<Id>>,
    byte:Option<u8>,
    number:Option<memory::Number>,
    bytes_len:Option<i128>,
}
impl Value {
    fn reference(place: Place) -> Self {
        Self {
            refs: BTreeSet::from([place]),
            ..Self::default()
        }
    }
    fn merge(&mut self, v: &Self) {
        if *self == Self::default() {
            *self = v.clone();
            return;
        }
        self.absent_when.clear();
        self.caller_tail &= v.caller_tail;
        if self.byte!=v.byte{self.byte=None;}
        if self.number!=v.number{self.number=None;}
        if self.bytes_len!=v.bytes_len{self.bytes_len=None;}
        self.choice = (!self.unknown || self.choice) && (!v.unknown || v.choice)
            && (self.choice || v.choice || self.refs != v.refs);
        self.may_nil |= v.may_nil;
        self.nil_procedure |= v.nil_procedure;
        if self.truth != v.truth {self.truth=None;}
        if self.file_status != v.file_status {self.file_status=None;}
        self.unknown |= self.refs != v.refs && (!self.refs.is_empty() || !v.refs.is_empty());
        self.refs.extend(v.refs.iter().cloned());
        self.procedures.extend(v.procedures.iter().copied());
        self.unknown |= v.unknown;
        if self.allocator != v.allocator {
            self.allocator = Some(Allocator::Unknown);
        }
        for (k, v) in &v.fields {
            self.fields.entry(k.clone()).or_default().merge(v);
        }
    }
    fn child(&self, key: &Key) -> Self {
        let mut v = self.fields.get(key).cloned().unwrap_or_default();
        if matches!(key,Key::Byte(_)|Key::View(_)) {
            // Reinterpreting bytes cannot erase encoded addresses. Their exact
            // field correspondence is unknown, so retain all possible edges.
            for item in self.fields.values(){v.merge(item);}
            v.refs.extend(self.refs.iter().cloned());
            if v.has_refs(){v.invalidate();}
        }
        if matches!(key, Key::Item(_)) {
            if let Some(other) = self.fields.get(&Key::Item(None)) {
                v.merge(other);
            }
            if *key == Key::Item(None) {
                for (k, other) in &self.fields {
                    if matches!(k, Key::Item(_)) {
                        v.merge(other);
                    }
                }
            }
        }
        if let Key::Range(low, high) = key {
            for (k, item) in &self.fields {
                match k {
                    Key::Item(Some(index))
                        if index >= low && high.is_none_or(|end| *index < end) =>
                    {
                        v.merge(item)
                    }
                    Key::Item(None) => {
                        v.merge(item);
                        v.invalidate();
                    }
                    _ => {}
                }
            }
        }
        if let Key::Item(index) = key {
            for (k, item) in &self.fields {
                if let Key::Range(low, high) = k {
                    if index.is_none_or(|index| index >= *low && high.is_none_or(|end| index < end))
                    {
                        v.merge(item);
                        v.invalidate();
                    }
                }
            }
        }
        v.unknown |= self.unknown;
        v.caller_tail |= self.caller_tail;
        v
    }
    // Unlike merge's accumulator bottom, an empty value on a control-flow
    // branch means nil/zero. Do not lose that alternative when it is first.
    fn join(&mut self, v: &Self) {
        self.absent_when.clear();
        self.caller_tail &= v.caller_tail;
        if self.byte!=v.byte{self.byte=None;}
        if self.number!=v.number{self.number=None;}
        if self.bytes_len!=v.bytes_len{self.bytes_len=None;}
        self.choice = (!self.unknown || self.choice) && (!v.unknown || v.choice)
            && (self.choice || v.choice || self.refs != v.refs);
        self.may_nil |= v.may_nil || self.refs.is_empty()!=v.refs.is_empty();
        self.nil_procedure |= v.nil_procedure;
        if self.truth != v.truth {self.truth=None;}
        if self.file_status != v.file_status {self.file_status=None;}
        self.unknown |=
            v.unknown || self.refs != v.refs && (!self.refs.is_empty() || !v.refs.is_empty());
        self.refs.extend(v.refs.iter().cloned());
        self.procedures.extend(v.procedures.iter().copied());
        if self.allocator != v.allocator {
            self.allocator = Some(Allocator::Unknown);
        }
        for (key, item) in &mut self.fields {
            item.join(v.fields.get(key).unwrap_or(&Value::default()));
        }
        for (key, item) in &v.fields {
            if !self.fields.contains_key(key) {
                let mut value = Value::default();
                value.join(item);
                self.fields.insert(key.clone(), value);
            }
        }
    }
    fn read(&self, path: &[Key]) -> Self {
        match path.split_first() {
            Some((k, tail)) => self.child(k).read(tail),
            None => self.clone(),
        }
    }
    fn write(&mut self, path: &[Key], v: &Self, weak: bool) {
        match path.split_first() {
            Some((k, tail)) => self.fields.entry(k.clone()).or_default().write(
                tail,
                v,
                weak || matches!(k, Key::Item(None) | Key::Range(..)),
            ),
            None => {
                if weak {
                    self.merge(v)
                } else {
                    *self = v.clone()
                }
            }
        }
    }
    fn roots(&self, out: &mut BTreeSet<Id>) {
        out.extend(self.refs.iter().map(|p| p.id));
        for v in self.fields.values() {
            v.roots(out);
        }
    }
    fn has_refs(&self) -> bool {
        !self.refs.is_empty() || self.fields.values().any(Self::has_refs)
    }
    fn references(&self, id: Id) -> bool {
        self.refs.iter().any(|p| p.id == id) || self.fields.values().any(|v| v.references(id))
    }
    fn invalidate(&mut self) {self.unknown=true;self.choice=false;self.file_status=None;self.truth=None;self.byte=None;self.number=None;self.bytes_len=None;self.absent_when.clear();}
    fn forget_scalar_facts(&mut self){
        self.truth=None;self.byte=None;self.number=None;self.bytes_len=None;self.file_status=None;self.choice=false;self.absent_when.clear();
        self.fields.retain(|_,v|{v.forget_scalar_facts();*v!=Value::default()});
    }
}
#[derive(Clone, Debug)]
enum Storage {
    Stack { frame: usize, scope: Ref },
    Static,
    Caller,
    Heap(Allocator),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Resource { File, StandardFile, Image, Pool, Thread }
#[derive(Clone, Debug)]
struct Object {
    resource: Option<Resource>,
    origin: Ref,
    owner: Option<Ref>,
    storage: Storage,
    // Once exposed, a stack address may also be held by an expression or
    // deferred capture outside State.cells. Keep its tombstone permanently.
    addressed: bool,
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct Cell {
    value: Value,
    life: u8,
    uncertain: bool,
    released: Option<Ref>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct State {
    cells: BTreeMap<Id, Cell>,
    globals: BTreeSet<Id>,
    exports: BTreeSet<Id>,
    allocator: Allocator,
    temp_allocator:Allocator,
    cleanups: BTreeMap<(usize, Ref), Vec<Pending>>,
    terminated: bool,
    pools: BTreeMap<Place,threads::Pool>,
    file_origins:BTreeSet<Id>,
    buffers:BTreeMap<Place,buffers::Bytes>,
    regions:BTreeMap<Place,memory::Region>,
    workers:BTreeMap<Place,workers::Worker>,
}
impl Default for State {
    fn default() -> Self {
        Self {
            cells: BTreeMap::new(),
            globals: BTreeSet::new(),
            exports: BTreeSet::new(),
            allocator: Allocator::Unknown,
            temp_allocator:Allocator::Temporary(0),
            cleanups: BTreeMap::new(),
            terminated: false,
            pools:BTreeMap::new(),
            file_origins:BTreeSet::new(),
            buffers:BTreeMap::new(),
            regions:BTreeMap::new(),
            workers:BTreeMap::new(),
        }
    }
}
impl State {
    fn merge(&mut self, s: &Self) {
        self.regions.retain(|p,b|s.regions.get(p).is_some_and(|other|b.join(other)));
        for (p,worker) in &mut self.workers{if let Some(other)=s.workers.get(p){worker.join(other)}else{worker.opaque=true;}}
        for (p,worker) in &s.workers{if !self.workers.contains_key(p){let mut w=worker.clone();w.opaque=true;self.workers.insert(p.clone(),w);}}
        self.buffers.retain(|p,b|s.buffers.get(p).is_some_and(|other|b.join(other)));
        let files=self.file_origins.union(&s.file_origins).copied().take(8).collect::<Vec<_>>();
        let absent_left=files.iter().filter(|id|self.cells.get(id).is_none_or(|c|c.life==ABSENT)).copied().collect::<BTreeSet<_>>();
        let absent_right=files.iter().filter(|id|s.cells.get(id).is_none_or(|c|c.life==ABSENT)).copied().collect::<BTreeSet<_>>();
        self.file_origins.extend(&s.file_origins);
        for (place,pool) in &mut self.pools {
            if let Some(other)=s.pools.get(place){pool.join(other);}else{pool.opaque=true;}
        }
        for (place,pool) in &s.pools {if !self.pools.contains_key(place){let mut p=pool.clone();p.opaque=true;self.pools.insert(place.clone(),p);}}
        for (id, c) in &mut self.cells {
            if let Some(other) = s.cells.get(id) {
                c.uncertain |= c.value != other.value;
                c.value.join_file_choices(&other.value,&absent_left,&absent_right);
                c.life |= other.life;
                c.uncertain |= other.uncertain;
                if c.released != other.released {
                    c.released = None;
                }
            } else {
                c.life |= ABSENT;
            }
        }
        for (id, c) in &s.cells {
            if !self.cells.contains_key(id) {
                let mut c = c.clone();
                c.life |= ABSENT;
                self.cells.insert(*id, c);
            }
        }
        self.globals.extend(&s.globals);
        self.exports.extend(&s.exports);
        self.terminated &= s.terminated;
        if self.allocator != s.allocator {
            self.allocator = Allocator::Unknown;
        }
        if self.temp_allocator!=s.temp_allocator{self.temp_allocator=Allocator::Unknown;}
        let keys = self
            .cleanups
            .keys()
            .chain(s.cleanups.keys())
            .copied()
            .collect::<BTreeSet<_>>();
        for key in keys {
            let left = self.cleanups.get(&key).cloned().unwrap_or_default();
            let right = s.cleanups.get(&key).cloned().unwrap_or_default();
            let mut all = left.clone();
            for item in &mut all {
                if !right
                    .iter()
                    .any(|r| r.action == item.action && r.allocator==item.allocator && !r.conditional)
                {
                    item.conditional = true;
                }
            }
            for item in right {
                if !all.iter().any(|r| r.action == item.action && r.allocator==item.allocator) {
                    all.push(Pending {
                        action: item.action,
                        conditional: true,
                        allocator:item.allocator,
                        temp_allocator:item.temp_allocator,
                    });
                }
            }
            self.cleanups.insert(key, all);
        }
    }
    fn read(&self, p: &Place) -> Value {
        let Some(c) = self.cells.get(&p.id) else {
            return Value {
                unknown: true,
                ..Value::default()
            };
        };
        let mut value = c.value.read(&p.path);
        if p.start == Some(0) && p.end.is_none() {
            return value;
        }
        let mut fields = BTreeMap::new();
        for (key, mut item) in value.fields {
            match (key, p.start) {
                (Key::Item(Some(index)), Some(start)) => {
                    if index >= start && p.end.is_none_or(|end| index < end) {
                        fields.insert(Key::Item(Some(index - start)), item);
                    }
                }
                (Key::Item(None), _) => {
                    item.invalidate();
                    fields.insert(Key::Item(None), item);
                }
                (key, _) => {
                    fields.insert(key, item);
                }
            }
        }
        value.fields = fields;
        value.unknown |= p.start.is_none();
        value
    }
    fn write(&mut self, p: &Place, v: &Value, weak: bool) {
        self.write_bytes(p,v.byte,weak);
        for (place,pool) in &mut self.pools {if place.id==p.id
            && (place.path.starts_with(&p.path)||p.path.starts_with(&place.path)){pool.opaque=true;}}
        for (place,worker) in &mut self.workers{if place.id==p.id&&worker.phase&2!=0{worker.opaque=true;worker.task.data.merge(v);}}
        if let Some(c) = self.cells.get_mut(&p.id) {
            if weak{let mut value=v.clone();value.forget_scalar_facts();c.value.forget_scalar_facts();c.value.write(&p.path,&value,true);}
            else{c.value.write(&p.path, v, false);}
        }
    }
    fn graph(
        &self,
        values: impl IntoIterator<Item = Value>,
        certain: bool,
        ownership: bool,
    ) -> BTreeSet<Id> {
        fn enqueue(v: &Value, todo: &mut BTreeSet<Place>, certain: bool) {
            if certain && v.unknown {
                return;
            }
            todo.extend(v.refs.iter().cloned());
            for item in v.fields.values() {
                enqueue(item, todo, certain);
            }
        }
        let mut todo = BTreeSet::new();
        for v in values {
            enqueue(&v, &mut todo, certain);
        }
        let mut seen = BTreeSet::new();
        let mut result = BTreeSet::new();
        while let Some(p) = todo.pop_first() {
            if !seen.insert(p.clone()) {
                continue;
            }
            if !ownership || (!p.interior && p.path.iter().all(|k|matches!(k,Key::View(_))) && p.start == Some(0)) {
                result.insert(p.id);
            }
            if certain && self.cells.get(&p.id).is_some_and(|c| c.uncertain) {
                continue;
            }
            enqueue(&self.read(&p), &mut todo, certain);
        }
        result
    }
    fn certainly_reachable(&self, values: impl IntoIterator<Item = Value>) -> BTreeSet<Id> {
        self.graph(values, true, true)
    }
    fn reachable(&self, values: impl IntoIterator<Item = Value>) -> BTreeSet<Id> {
        self.graph(values, false, false)
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct Pending {
    action: Cleanup,
    conditional: bool,
    allocator:Allocator,
    temp_allocator:Allocator,
}
#[derive(Clone, Debug, PartialEq, Eq)]
enum Cleanup {
    Statement(Ref),
    Owner(Place),
    Region(Id),
    Procedure {
        procedure: Ref,
        inputs: Vec<Value>,
        call: Ref,
    },
}
#[derive(Clone)]
struct Frame {
    id: usize,
    procedure: Ref,
    env: HashMap<Ref, Id>,
    // A reference range binding denotes the element's lvalue, not a copied
    // pointer variable. Reads, writes and address-taking use the same places.
    range_places: HashMap<Ref, Vec<Place>>,
    scope: Ref,
    bindings: Substitutions,
    keys: HashMap<Ref, Value>,
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum Control {
    Normal,
    Return,
    Break,
    Continue,
    Stop,
}
#[derive(Clone)]
struct Exit {
    state: State,
    control: Control,
    values: Vec<Value>,
    at: Ref,
}
fn joined(xs: Vec<Exit>) -> Vec<Exit> {
    let mut result: Vec<Exit> = vec![];
    for x in xs {
        // Preserve a small number of owned-reference return alternatives until
        // the callee boundary can relate a released failure allocation to nil.
        // Scalar-only branching retains the existing compact join.
        if x.control==Control::Return && result.iter().filter(|r|r.control==Control::Return).count()<4
            && (x.values.iter().any(Value::has_refs)||result.iter().any(|r|r.control==Control::Return&&r.values.iter().any(Value::has_refs))){result.push(x);continue;}
        if let Some(r) = result.iter_mut().find(|r| r.control == x.control) {
            r.state.merge(&x.state);
            r.values
                .resize(r.values.len().max(x.values.len()), Value::default());
            for (i, v) in x.values.iter().enumerate() {
                let different = r.values[i] != *v;
                r.values[i].merge(v);
                r.values[i].unknown |= different;
            }
        } else {
            result.push(x);
        }
    }
    result
}
struct Finding {
    at: Ref,
    origin: Option<Id>,
    definite: bool,
    rule: &'static str,
    message: String,
    chain: Vec<Ref>,
}
#[derive(Clone)]
struct CallModel {
    builtin: Option<String>,
    cloning: bool,
    library: Option<(&'static str, String)>,
}
struct Analysis<'a, 'r> {
    r: &'r mut Resolver<'a>,
    objects: Vec<Object>,
    ids: HashMap<(usize, Ref, u8), Id>,
    findings: Vec<Finding>,
    active: Vec<(Ref, Ref)>,
    steps: usize,
    frames: usize,
    exhausted: bool,
    expression_depth: usize,
    lifetime: bool,
    leak: bool,
    static_values: &'r HashSet<Ref>,
    temporaries: &'r HashMap<Ref, Vec<Ref>>,
    models: &'r mut HashMap<Ref, CallModel>,
    // Type queries repeat across loop and caller frames. Check the exact
    // specialization cache before doing resolver work, including concrete types.
    refined_types: HashMap<Ref, HashMap<Substitutions, Type>>,
    scalar_facts: &'r mut HashMap<Ref, HashMap<Substitutions, (Option<Option<u8>>, Option<bool>,Option<i128>)>>,
    inline_types: &'r mut HashMap<Type, bool>,
    summaries: &'r mut summaries::Cache,
    custom_formatting: bool,
    custom_io: bool,
    custom_random: bool,
    static_slots: HashMap<(Ref, Substitutions), Id>,
    custom_foreign_callbacks: bool,
    foreign_title_slot: Option<Id>,
    raylib_six: bool,
    async_callback:bool,
    demand_count:usize,
}
fn base(t: &Type) -> &Type {
    t.storage_shape()
}
fn reference(t: &Type) -> bool {
    matches!(
        base(t),
        Type::Pointer(_)
            | Type::Slice(_)
            | Type::Dynamic(_)
            | Type::Map { .. }
            | Type::Set(_)
            | Type::String { .. }
            | Type::Soa(_)
            | Type::BigInteger
            | Type::BigRational
    ) || matches!(base(t),Type::Scalar{name,..} if matches!(name.as_str(),"rawptr"|"any"|"cstring"|"cstring16"))
}
fn generic(t: &Type) -> bool {
    match t {
        Type::Generic => true,
        Type::Pointer(t)
        | Type::Slice(t)
        | Type::Dynamic(t)
        | Type::Set(t)
        | Type::SoaStorage(t)
        | Type::Soa(t)
        | Type::Nominal(_, t)
        | Type::Array { elem: t, .. }
        | Type::Fixed { elem: t, .. }
        | Type::Vector { elem: t, .. }
        | Type::Matrix { elem: t, .. } => generic(t),
        Type::Map { key, value } => generic(key) || generic(value),
        Type::Record(_, fields) | Type::Anonymous(fields) | Type::Union(_, fields) => {
            fields.iter().any(|(_, t)| generic(t))
        }
        _ => false,
    }
}
impl Analysis<'_, '_> {
    fn kind(&self, at: Ref) -> &str {
        self.r.sources[at.file].node(at.node).kind.as_str()
    }
    fn text(&self, at: Ref) -> &str {
        self.r.sources[at.file].text(at.node)
    }
    fn role(&self, at: Ref, name: &str) -> Option<Ref> {
        self.r.sources[at.file]
            .role(at.node, name)
            .map(|node| Ref { node, ..at })
    }
    fn group(&self, at: Ref, name: &str) -> Vec<Ref> {
        self.r.sources[at.file]
            .group(at.node, name)
            .iter()
            .map(|node| Ref { node: *node, ..at })
            .collect()
    }
    fn symbol(&self, at: Ref) -> Option<Ref> {
        self.r.symbol(at).map(|i| Ref {
            file: self.r.symbols[i].reference.file,
            node: self.r.symbols[i].name_node,
        })
    }
    fn typ(&mut self, at: Ref, f: &Frame) -> Type {
        if let Some(typ) = self
            .refined_types
            .get(&at)
            .and_then(|types| types.get(&f.bindings))
        {
            return typ.clone();
        }
        let typ = self.r.specialized_type(at, &f.bindings);
        let typ = if crate::resulttypes::concrete(&typ){typ}else{self.refined_type(at, f, 0)};
        self.refined_types
            .entry(at)
            .or_default()
            .insert(f.bindings.clone(), typ.clone());
        typ
    }
    fn inline_type(&mut self, typ: &Type, depth: usize) -> bool {
        if depth > 16 {
            return false;
        }
        if let Some(answer) = self.inline_types.get(typ) {
            return *answer;
        }
        let answer = match base(typ) {
            Type::Scalar { name, .. } => {
                !matches!(name.as_str(), "rawptr" | "any" | "cstring" | "cstring16")
            }
            Type::Enum(_)
            | Type::Constant(Constant::Integer(_) | Constant::Boolean(_) | Constant::Enum(..)) => {
                true
            }
            Type::Array { elem, .. }
            | Type::Fixed { elem, .. }
            | Type::Vector { elem, .. }
            | Type::Matrix { elem, .. } => self.inline_type(elem, depth + 1),
            Type::Struct(owner) | Type::Record(owner, _)
                if self.r.sources[owner.file].node(owner.node).op == "raw_union" =>
            {
                false
            }
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(typ)
                .iter()
                .all(|(_, t)| self.inline_type(t, depth + 1)),
            _ => false,
        };
        self.inline_types.insert(typ.clone(), answer);
        answer
    }
    fn refined_type(&mut self, at: Ref, f: &Frame, depth: usize) -> Type {
        let typ = self.r.specialized_type(at, &f.bindings);
        if crate::resulttypes::concrete(&typ) || depth >= 8 {
            return typ;
        }
        if matches!(self.kind(at),"Index"|"Deref"|"Selector"){
            if let Some(expr)=self.role(at,"expr"){
                let owner=self.refined_type(expr,f,depth+1);
                if self.kind(at)=="Selector"{
                    let owner=if let Type::Pointer(t)=base(&owner){(**t).clone()}else{owner};
                    if let Some(field)=self.role(at,"field"){
                        if let Some((_,t))=self.r.record_fields(&owner).into_iter().find(|(name,_)|name==self.text(field)){return t;}
                    }
                }else if let Some(t)=owner.element(){return t.clone();}
            }
        }
        if self.kind(at) == "Call" && crate::parallelcompile::is_call(self.r, at) {
            if let Some(results) = crate::resulttypes::resolve(self.r, at, &f.bindings, None) {
                return results
                    .fields
                    .first()
                    .map(|(_, t)| t.clone())
                    .unwrap_or(Type::Unknown);
            }
        }
        if self.kind(at) == "Call" {
            if let Some(signature) = self.r.call_signature(at) {
                let results = self.r.fields(signature, "results");
                if results.len() == 1 {
                    if let Some(annotation) = results[0].1 {
                        let result = self.r.specialized_type(annotation, &f.bindings);
                        if crate::resulttypes::concrete(&result) {
                            return result;
                        }
                    }
                }
            }
        }
        if self.kind(at) == "Ident" {
            if let Some(value) = self.r.symbol(at).and_then(|i| {
                let symbol = &self.r.symbols[i];
                symbol.value.map(|node| Ref {
                    file: symbol.reference.file,
                    node,
                })
            }) {
                return self.refined_type(value, f, depth + 1);
            }
        }
        if self.kind(at) == "Compound" {
            let mut child = at;
            let mut parent = self.r.sources[at.file]
                .node(at.node)
                .parent
                .map(|node| Ref { node, ..at });
            let mut field = None;
            if let Some(p) = parent.filter(|p| self.kind(*p) == "FieldValue") {
                field = self.role(p, "field");
                child = p;
                parent = self.r.sources[p.file]
                    .node(p.node)
                    .parent
                    .map(|node| Ref { node, ..p });
            }
            if let Some(parent) = parent {
                if self.kind(parent) == "Compound" {
                    let owner = self.refined_type(parent, f, depth + 1);
                    if let Some(elem) = owner.element() {
                        return elem.clone();
                    }
                    let fields = self.r.record_fields(&owner);
                    let index = field
                        .and_then(|field| fields.iter().position(|(n, _)| n == self.text(field)))
                        .or_else(|| self.group(parent, "elems").iter().position(|e| *e == child));
                    if let Some((_, t)) = index.and_then(|i| fields.get(i)) {
                        return t.clone();
                    }
                } else if self.kind(parent)=="Call" {
                    let plans=self.r.call_plans(parent,&f.bindings);
                    if plans.len()==1 {
                        let plan=&plans[0];
                        if let Some(index)=plan.arguments.iter().position(|g|g.contains(&child)||g.contains(&at)) {
                            if let Some(sig)=self.role(plan.procedure,"type") {
                                if let Some(annotation)=self.r.fields(sig,"params").get(index).and_then(|(_,t)|*t){
                                    return self.r.specialized_type(annotation,&plan.bindings);
                                }
                            }
                        }
                    }
                } else if self.kind(parent) == "Return" {
                    if let Some(index) = self.group(parent, "values").iter().position(|value| *value == child) {
                        if let Some(annotation) = self.role(f.procedure, "type")
                            .and_then(|signature| self.r.fields(signature, "results").get(index).and_then(|(_, typ)| *typ)) {
                            return self.r.specialized_type(annotation, &f.bindings);
                        }
                    }
                } else if self.kind(parent) == "Declaration" {
                    if let Some(annotation) = self.role(parent, "type") {
                        return self.r.specialized_type(annotation, &f.bindings);
                    }
                }
            }
        }
        typ
    }
    fn argument_name(&self, arg: Ref) -> Option<Ref> {
        (self.kind(arg) == "FieldValue")
            .then(|| self.role(arg, "field"))
            .flatten()
    }
    fn argument(&self, args: &[Ref], name: &str, position: usize) -> Option<usize> {
        args.iter()
            .position(|arg| {
                self.argument_name(*arg)
                    .is_some_and(|field| self.text(field) == name)
            })
            .or_else(|| {
                args.iter()
                    .enumerate()
                    .filter(|(_, arg)| self.kind(**arg)!="FieldValue")
                    .nth(position)
                    .map(|(i, _)| i)
            })
    }
    fn items(&mut self, state: &State, value: &Value, at: Ref) -> Value {
        let mut item = value.child(&Key::Item(None));
        for p in &value.refs {
            item.merge(&self.read(state, p, at).child(&Key::Item(None)));
        }
        item
    }
    fn parallel_call(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        args: &[Ref],
        values: &[Value],
        depth: usize,
    ) -> Vec<Value> {
        let Some(jobs) = self.argument(args, "jobs", 0) else {
            return vec![];
        };
        self.direct(state, &values[jobs], at);
        let job = self.items(state, &values[jobs], at);
        let ty = self.typ(self.role(args[jobs], "value").unwrap_or(args[jobs]), f);
        let element = match base(&ty) {
            Type::Slice(t)
            | Type::Dynamic(t)
            | Type::Array { elem: t, .. }
            | Type::Fixed { elem: t, .. } => *t.clone(),
            _ => Type::Unknown,
        };
        let output = self.typ(at, f);
        let mut returns = matches!(base(&output), Type::Slice(_));
        let mut row = Value::default();
        let before = state.clone();
        if let Some(callback) = crate::parallelcompile::callback(self.r, at, &f.bindings) {
            returns = callback.returns_values;
            if self.active.iter().any(|(p, _)| *p == callback.procedure)
                || self.active.len() >= MAX_DEPTH
            {
                row = self.unknown_call(
                    state,
                    at,
                    &[job.clone()],
                    "recursive pmap callback requires a lifetime summary",
                );
            } else {
                let sig = self
                    .role(callback.procedure, "type")
                    .unwrap_or(callback.procedure);
                let params = self.r.fields(sig, "params");
                let mut inputs = if crate::resulttypes::signature_spreads(self.r, sig) {
                    self.r
                        .record_fields(&element)
                        .into_iter()
                        .map(|(name, _)| job.child(&Key::Field(name)))
                        .collect::<Vec<_>>()
                } else if params.is_empty() {
                    vec![]
                } else {
                    vec![job.clone()]
                };
                for default in &callback.defaults {
                    if inputs.len() < params.len() {
                        inputs.push(self.value(state, f, *default, depth + 1));
                    }
                }
                let caller_allocator = state.allocator.clone();
                state.allocator = self
                    .argument(args, "worker_allocator", if returns { 5 } else { 4 })
                    .and_then(|i| values[i].allocator.clone())
                    .unwrap_or(Allocator::Heap);
                self.active.push((callback.procedure, at));
                let previous = self.async_callback;
                self.async_callback = true;
                let exits =
                    self.procedure(state.clone(), callback.procedure, inputs, callback.bindings);
                self.async_callback = previous;
                self.active.pop();
                let mut after: Option<State> = None;
                let mut produced: Vec<Value> = vec![];
                for exit in exits {
                    if exit.control == Control::Stop {
                        continue;
                    }
                    produced.resize(produced.len().max(exit.values.len()), Value::default());
                    for (dest, source) in produced.iter_mut().zip(exit.values) {
                        dest.merge(&source);
                    }
                    if let Some(after) = &mut after {
                        after.merge(&exit.state)
                    } else {
                        after = Some(exit.state)
                    }
                }
                if let Some(mut after) = after {
                    after.allocator = caller_allocator;
                    let stable = before
                        .cells
                        .iter()
                        .filter(|(_, c)| c.life & LIVE != 0)
                        .all(|(id, c)| after.cells.get(id).is_some_and(|n| {
                            // Inline scalar writes do not change ownership. Do
                            // retain lifetime, uncertainty and reference facts.
                            let mut before = c.clone();
                            let mut after = n.clone();
                            before.value.forget_scalar_facts();
                            after.value.forget_scalar_facts();
                            before == after
                        }));
                    let new_global_effect = after.globals.difference(&before.globals).any(|id| {
                        after.cells.get(id).is_some_and(|c| {
                            after
                                .reachable([c.value.clone()])
                                .iter()
                                .any(|id| !matches!(self.objects[*id].storage, Storage::Static))
                        })
                    });
                    let keys = match base(&output) {
                        Type::Slice(elem) => self.r.record_fields(elem),
                        _ => vec![],
                    };
                    row = if produced.len() == 1 {
                        produced.pop().unwrap()
                    } else {
                        Value {
                            fields: produced
                                .into_iter()
                                .enumerate()
                                .map(|(i, v)| {
                                    (
                                        Key::Field(
                                            keys.get(i)
                                                .map(|(n, _)| n.clone())
                                                .unwrap_or_else(|| format!("v{i}")),
                                        ),
                                        v,
                                    )
                                })
                                .collect(),
                            ..Value::default()
                        }
                    };
                    if !stable || new_global_effect {
                        self.unknown_call(&mut after,at,&[job.clone(),row.clone()],"pmap callback changes shared reference state; repeated parallel effects cannot be verified");
                    }
                    // Empty jobs do not execute the callback. Keep that path
                    // without treating independent output storage as a job alias.
                    after.merge(&before);
                    *state = after;
                } else {
                    *state = before.clone();
                }
            }
        } else {
            row = self.unknown_call(
                state,
                at,
                &[job],
                "pmap callback implementation cannot be resolved",
            );
            self.report(
                at,
                None,
                false,
                "leak",
                "pmap callback allocation effects require manual review",
            );
        }
        if !returns {
            return vec![];
        }
        let allocator = self
            .argument(args, "allocator", 4)
            .and_then(|i| values[i].allocator.clone())
            .unwrap_or_else(|| before.allocator.clone());
        let contents = Value {
            fields: BTreeMap::from([(Key::Item(None), row)]),
            ..Value::default()
        };
        let out = self.allocate(state, f, at, allocator, contents);
        for p in &out.refs {
            if let Some(c) = state.cells.get_mut(&p.id) {
                c.life |= ABSENT;
            }
        }
        vec![out]
    }
    fn report(
        &mut self,
        at: Ref,
        id: Option<Id>,
        definite: bool,
        rule: &'static str,
        message: impl Into<String>,
    ) {
        if rule == "lifetime" && !self.lifetime || rule == "leak" && !self.leak {
            return;
        }
        self.findings.push(Finding {
            at,
            origin: id,
            definite,
            rule,
            message: message.into(),
            chain: self.active.iter().map(|(_, call)| *call).collect(),
        });
    }
    fn budget(&mut self, at: Ref) -> bool {
        self.steps += 1;
        if self.steps > MAX_STEPS || self.objects.len() > MAX_OBJECTS || self.frames > MAX_CALLS {
            if !self.exhausted {
                self.report(
                    at,
                    None,
                    false,
                    "lifetime",
                    "analysis budget reached; remaining reference effects cannot be verified",
                );
                self.report(
                    at,
                    None,
                    false,
                    "leak",
                    "analysis budget reached; remaining cleanup obligations cannot be verified",
                );
            }
            self.exhausted = true;
            false
        } else {
            true
        }
    }
    fn object(
        &mut self,
        state: &mut State,
        frame: usize,
        at: Ref,
        tag: u8,
        storage: Storage,
        value: Value,
    ) -> Id {
        let id = *self.ids.entry((frame, at, tag)).or_insert_with(|| {
            let id = self.objects.len();
            self.objects.push(Object {
                    resource: None,
                origin: at,
                owner: None,
                storage,
                addressed: matches!(tag, 2 | 6),
            });
            id
        });
        state.cells.insert(
            id,
            Cell {
                value,
                life: LIVE,
                uncertain: false,
                released: None,
            },
        );
        id
    }
    fn read(&mut self, state: &State, p: &Place, at: Ref) -> Value {
        if let Some(c) = state.cells.get(&p.id) {
            if c.life & DEAD != 0 {
                let definite = c.life == DEAD && !c.uncertain;
                self.report(at,Some(p.id),definite,"lifetime",if definite{"use of storage after its lifetime ended"}else{"storage may have been released on another path; this access cannot be verified"});
            }
        }
        state.read(p)
    }
    fn read_many(&mut self, state: &State, ps: &[Place], at: Ref, unknown: bool) -> Value {
        let mut v = Value::default();
        for p in ps {
            if let Some(c) = state.cells.get(&p.id) {
                if c.life & DEAD != 0 {
                    let definite = c.life == DEAD && !c.uncertain && ps.len() == 1 && !unknown;
                    self.report(
                        at,
                        Some(p.id),
                        definite,
                        "lifetime",
                        if definite {
                            "use of storage after its lifetime ended"
                        } else {
                            "storage may have been released; selected reference cannot be verified"
                        },
                    );
                }
            }
            v.merge(&state.read(p));
        }
        v
    }
    fn direct(&mut self, state: &State, v: &Value, at: Ref) {
        let ps = v.refs.iter().cloned().collect::<Vec<_>>();
        let _ = self.read_many(state, &ps, at, v.unknown);
    }
    fn valid(&mut self, state: &State, v: &Value, at: Ref) {
        let ids = state.reachable([v.clone()]);
        let certain = state.graph([v.clone()], true, false);
        for id in ids {
            if let Some(c) = state.cells.get(&id) {
                if c.life & DEAD != 0 {
                    let definite = c.life == DEAD
                        && !c.uncertain
                        && !v.unknown
                        && v.refs.len() <= 1
                        && certain.contains(&id);
                    self.report(
                        at,
                        Some(id),
                        definite,
                        "lifetime",
                        if definite {
                            "reference reaches storage whose lifetime has ended"
                        } else {
                            "reference may reach released storage; lifetime cannot be verified"
                        },
                    );
                }
            }
        }
    }
    fn release(&mut self, state: &mut State, v: &Value, at: Ref) {
        self.release_as(state, v, at, None)
    }
    fn release_as(&mut self, state: &mut State, v: &Value, at: Ref, resource: Option<Resource>) {
        self.check_worker_release(state,v,at);
        if v.unknown {
            self.report(
                at,
                None,
                false,
                "lifetime",
                "cleanup target has unknown provenance; validity and ownership cannot be verified",
            );
        }
        for p in &v.refs {
            if self.objects[p.id].resource.is_some() && self.objects[p.id].resource != resource {
                let definite=!v.unknown && v.refs.len()==1
                    && state.cells.get(&p.id).is_some_and(|c|c.life==LIVE && !c.uncertain);
                self.report(at, Some(p.id), definite, "lifetime",
                    "native resource requires its matching close/unload operation");
                continue;
            }
            let offset = p
                .path
                .iter()
                .any(|k| matches!(k,Key::Item(Some(n))|Key::Byte(Some(n)) if *n!=0));
            if p.interior || offset {
                self.report(at,Some(p.id),!v.unknown&&v.refs.len()==1,"lifetime","cleanup requires the original allocation, not an interior pointer or rebased view");
                continue;
            }
            if p.path
                .iter()
                .any(|k| matches!(k, Key::Field(_) | Key::Item(None) | Key::Range(..) | Key::Byte(None)))
            {
                self.report(at,Some(p.id),false,"lifetime","cleanup address may be an interior view; original allocation cannot be verified");
            }
            let Some(c) = state.cells.get_mut(&p.id) else {
                continue;
            };
            if c.life & DEAD != 0 {
                let definite = c.life == DEAD && !c.uncertain && v.refs.len() == 1 && !v.unknown;
                self.report(
                    at,
                    Some(p.id),
                    definite,
                    "lifetime",
                    if definite {
                        "storage is released more than once (double free)"
                    } else {
                        "cleanup may release storage more than once"
                    },
                );
            }
            if matches!(
                self.objects[p.id].storage,
                Storage::Stack { .. } | Storage::Static
            ) {
                self.report(
                    at,
                    Some(p.id),
                    v.refs.len() == 1 && !v.unknown,
                    "lifetime",
                    "cleanup attempts to free borrowed stack or static storage",
                );
                continue;
            }
            if v.refs.len() == 1 && !v.unknown {
                c.life = (c.life & ABSENT) | DEAD;
                // An exact explicit cleanup establishes the end of this
                // lifetime even if an earlier opaque call had unknown effects.
                c.uncertain = false;
            } else {
                c.life |= DEAD;
            }
            c.released = Some(at);
        }
    }
    fn end_region(&mut self, state: &mut State, region: &Place, at: Ref, definite: bool) {
        if !state.workers.is_empty(){
            let affected=Value{refs:state.cells.keys().filter(|id|matches!(&self.objects[**id].storage,Storage::Heap(Allocator::Region(p))if p.id==region.id&&p.path==region.path)).map(|id|Place::root(*id)).collect(),..Value::default()};
            self.check_worker_release(state,&affected,at);
        }
        // Reset/destroy invalidates every remaining allocation. Individually
        // released members are already gone; destroying their arena is not a
        // second free of those members.
        for (id, c) in &mut state.cells {
            if let Storage::Heap(Allocator::Region(owner)) = &self.objects[*id].storage {
                let overlap = owner.id == region.id
                    && owner.path.len() == region.path.len()
                    && owner.path.iter().zip(&region.path).all(|(a, b)| {
                        a == b
                            || matches!(a, Key::Item(_) | Key::Range(..))
                                && matches!(b, Key::Item(_) | Key::Range(..))
                                && (matches!(a, Key::Item(None) | Key::Range(..))
                                    || matches!(b, Key::Item(None) | Key::Range(..)))
                    });
                if !overlap {
                    continue;
                }
                let exact = owner.as_ref() == region
                    && owner
                        .path
                        .iter()
                        .all(|key| !matches!(key, Key::Item(None) | Key::Range(..)));
                if definite && exact {
                    c.life = (c.life & ABSENT) | DEAD;
                    c.uncertain = false;
                } else {
                    c.life |= DEAD;
                }
                c.released = Some(at);
            }
        }
    }
    fn consume(&mut self, state: &State, v: &Value, at: Ref, typ: &Type, depth: usize) {
        if depth > MAX_VALUE_DEPTH {
            self.report(
                at,
                v.refs.first().map(|p| p.id),
                false,
                "lifetime",
                "formatting graph exceeds the reference inspection budget",
            );
            return;
        }
        if Self::cstring_type(typ){self.check_termination(state,v,at);}
        match base(typ) {
            Type::Pointer(inner)
                if matches!(
                    base(inner),
                    Type::Scalar { .. } | Type::Enum(_) | Type::Procedure(_) | Type::Function(_, _)
                ) => {}
            Type::Pointer(_) => {
                if state
                    .reachable([v.clone()])
                    .iter()
                    .any(|id| state.cells.get(id).is_some_and(|c| c.life & DEAD != 0))
                {
                    self.report(at,v.refs.first().map(|p|p.id),false,"lifetime","formatting may dereference a pointer depending on its verb; reference access cannot be verified");
                }
            }
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                for (name, t) in self.r.record_fields(typ) {
                    self.consume(state, &v.child(&Key::Field(name)), at, &t, depth + 1);
                }
            }
            Type::Slice(elem)
            | Type::Dynamic(elem)
            | Type::Array { elem, .. }
            | Type::Fixed { elem, .. } => {
                self.direct(state, v, at);
                if let Some(bytes)=v.bytes_len{self.memory_read(state,v,bytes,at);}
                for item in v.fields.values() {
                    self.consume(state, item, at, elem, depth + 1);
                }
                for p in &v.refs {
                    if let Some(c) = state.cells.get(&p.id) {
                        for item in c.value.read(&p.path).fields.values() {
                            self.consume(state, item, at, elem, depth + 1);
                        }
                    }
                }
            }
            _ => self.valid(state, v, at),
        }
    }
    fn unknown_call(&mut self, state: &mut State, at: Ref, args: &[Value], why: &str) -> Value {
        let ids = state.reachable(args.iter().cloned());
        if args.iter().any(|v| v.unknown) && ids.is_empty() {
            self.report(
                at,
                None,
                false,
                "lifetime",
                format!("{why}; input reference provenance cannot be verified"),
            );
        }
        for id in ids {
            Self::forget_memory(state,id);
            for (place,bytes)in &mut state.buffers{if place.id==id{bytes.forget();}}
            // Foreign/unknown code can retain data even when it cannot mutate it.
            self.report(
                at,
                Some(id),
                false,
                "lifetime",
                format!(
                    "{why}; retention, release, and lifetime of this reference cannot be verified"
                ),
            );
            if let Some(c) = state.cells.get_mut(&id) {
                c.uncertain = true;
                c.value.invalidate();
                c.value.caller_tail = false;
            }
        }
        let mut v = Value {
            unknown: true,
            ..Value::default()
        };
        for a in args {
            v.merge(a);
        }
        v
    }
    fn allocate(
        &mut self,
        state: &mut State,
        f: &Frame,
        at: Ref,
        allocator: Allocator,
        value: Value,
    ) -> Value {
        if let Allocator::Region(place) = &allocator {
            let _ = self.read(state, place, at);
        }
        let key = (f.id, at, 1);
        if let Some(id) = self.ids.get(&key).copied() {
            let observed = state
                .cells
                .iter()
                .any(|(owner,c)| c.life & LIVE != 0 && (c.value.references(id)
                    ||matches!(&self.objects[*owner].storage,Storage::Heap(Allocator::Region(p))if p.id==id)))
                || matches!(&state.allocator,Allocator::Region(p)if p.id==id)
                || matches!(&state.temp_allocator,Allocator::Region(p)if p.id==id)
                || f.range_places.values().flatten().any(|p|p.id==id)
                || f.keys.values().any(|v|v.references(id))
                || state.pools.values().any(|pool|pool.tasks.values().any(|task|task.data.references(id)))
                || state.workers.values().any(|worker|worker.phase&2!=0&&worker.task.data.references(id))
                || state.cleanups.values().flatten().any(|p| match &p.action {
                    Cleanup::Owner(slot) => state.read(slot).references(id),
                    Cleanup::Procedure { inputs, .. } => inputs.iter().any(|v| v.references(id)),
                    _ => false,
                });
            if state.cells.get(&id).is_some_and(|c| c.life & LIVE != 0) || observed {
                if observed&&state.cells.get(&id).is_some_and(|c|c.life&DEAD!=0){crate::profile::count("lifetime.retained_generations",1);}
                // Reexecuting a site does not revive its old allocation. Keep
                // the older generation for aliases and cleanup obligations.
                self.ids.remove(&key);
            }
        }
        let id = self.object(state, f.id, at, 1, Storage::Heap(allocator), value);
        Value::reference(Place::root(id))
    }
    fn member_places(&mut self, state: &mut State, f: &mut Frame, at: Ref) -> Vec<Place> {
        if self.expression_depth >= MAX_VALUE_DEPTH || !self.budget(at) {
            self.report(
                at,
                None,
                false,
                "lifetime",
                "reference expression exceeds the analysis depth budget",
            );
            return vec![];
        }
        self.expression_depth += 1;
        let places = self.places(state, f, at);
        self.expression_depth -= 1;
        places
    }
    fn places(&mut self, state: &mut State, f: &mut Frame, at: Ref) -> Vec<Place> {
        match self.kind(at).to_owned().as_str() {
            "Ident" | "Selector" if self.kind(at)=="Ident" || self.role(at,"expr").is_some_and(|expr|
                self.kind(expr)=="Ident" && self.r.symbol(expr).is_none()
                    && !self.r.namespace_imports(expr.file,self.text(expr)).is_empty())
                && self.r.symbol(at).is_some_and(|i| !self.r.symbols[i].constant || self.r.symbols[i].immutable) => {
                if let Some(projection) = self.r.projection(at) {
                    let owner = projection.owner;
                    let ps = if matches!(base(&self.typ(owner, f)), Type::Pointer(_)) {
                        let value = self.value(state, f, owner, 0);
                        self.direct(state, &value, at);
                        if value.unknown && value.refs.is_empty() {
                            self.report(
                                at,
                                None,
                                false,
                                "lifetime",
                                "using target is beyond the tracked reference provenance",
                            );
                        }
                        value.refs.into_iter().collect()
                    } else {
                        self.member_places(state, f, owner)
                    };
                    return ps
                        .into_iter()
                        .map(|p| p.child(Key::Field(projection.member.clone())))
                        .collect();
                }
                let Some(symbol) = self.symbol(at) else {
                    return vec![];
                };
                if let Some(places) = f.range_places.get(&symbol) {
                    return places.clone();
                }
                if let Some(id) = f.env.get(&symbol) {
                    return vec![Place::root(*id)];
                }
                if let Some(id) = self
                    .static_slots
                    .get(&(symbol, f.bindings.clone()))
                    .filter(|id| state.cells.contains_key(id))
                {
                    return vec![Place::root(*id)];
                }
                let global = self.r.symbol(at).is_some_and(|i| {
                    !self.r.sources[self.r.symbols[i].reference.file]
                        .ancestors(self.r.symbols[i].name_node)
                        .any(|n| {
                            self.r.sources[self.r.symbols[i].reference.file]
                                .node(n)
                                .kind
                                == "Procedure"
                        })
                });
                if global {
                    let id = if let Some(id) = self.ids.get(&(0, symbol, 0)).copied() {
                        id
                    } else {
                        let id =
                            self.object(state, 0, symbol, 0, Storage::Static, Value::default());
                        state.globals.insert(id);
                        if let Some(value) = self.native_global(state, symbol, at) {
                            state.write(&Place::root(id), &value, false);
                        } else if let Some(value) = self
                            .r
                            .symbol(at)
                            .and_then(|i| self.r.symbols[i].value)
                            .map(|node| Ref {
                                node,
                                file: symbol.file,
                            })
                        {
                            if !self.r.denotes_type(value) && self.kind(value) != "Procedure" {
                                let mut global = f.clone();
                                global.id = 0;
                                global.env.clear();
                                global.range_places.clear();
                                let value = self.value(state, &mut global, value, 0);
                                state.write(&Place::root(id), &value, false);
                                let literals = state.reachable([value]);
                                for literal in literals {
                                    if matches!(
                                        self.objects[literal].storage,
                                        Storage::Stack { frame: 0, .. }
                                    ) {
                                        self.objects[literal].storage = Storage::Static;
                                    }
                                }
                            }
                        }
                        id
                    };
                    state.globals.insert(id);
                    return vec![Place::root(id)];
                }
                vec![]
            }
            "Paren" => self
                .role(at, "expr")
                .map(|a| self.member_places(state, f, a))
                .unwrap_or_default(),
            "Deref" => {
                let v = self
                    .role(at, "expr")
                    .map(|a| self.value(state, f, a, 0))
                    .unwrap_or_default();
                self.direct(state, &v, at);
                if v.unknown && v.refs.is_empty() {
                    self.report(
                        at,
                        None,
                        false,
                        "lifetime",
                        "pointer target is beyond the abstract parameter tracking depth",
                    );
                }
                v.refs.into_iter().collect()
            }
            "Selector" => {
                let Some(expr) = self.role(at, "expr") else {
                    return vec![];
                };
                let name = self
                    .role(at, "field")
                    .map(|a| self.text(a).to_owned())
                    .unwrap_or_default();
                let ps = if matches!(base(&self.typ(expr, f)), Type::Pointer(_)) {
                    self.value(state, f, expr, 0).refs.into_iter().collect()
                } else {
                    self.member_places(state, f, expr)
                };
                ps.into_iter()
                    .map(|p| p.child(Key::Field(name.clone())))
                    .collect()
            }
            "Index" | "MatrixIndex" => {
                let Some(expr) = self.role(at, "expr") else {
                    return vec![];
                };
                if let Some(index) = self.role(at, "index") {
                    let value = self.value(state, f, index, 0);
                    f.keys.insert(index, value);
                }
                let key = self.index(at, f);
                let ps = if matches!(
                    base(&self.typ(expr, f)),
                    Type::Array { .. }
                        | Type::Fixed { .. }
                        | Type::Matrix { .. }
                        | Type::Vector { .. }
                ) {
                    self.member_places(state, f, expr)
                } else {
                    let v = self.value(state, f, expr, 0);
                    self.direct(state, &v, at);
                    if let Key::Item(Some(index))=key {if index>=0 && Self::byte_element(&self.typ(at,f)){
                        if let Some(count)=index.checked_add(1){self.check_bytes(state,&v,count,at,true);}
                    }}
                    if v.unknown && v.refs.is_empty() {
                        self.report(
                            at,
                            None,
                            false,
                            "lifetime",
                            "indexed storage is beyond the tracked reference provenance",
                        );
                    }
                    v.refs.into_iter().collect()
                };
                let typ=self.typ(at,f);
                let stride=self.r.simple_size(&typ);
                let ps=ps.into_iter().map(|p| {
                    let raw=p.path.iter().any(|k|matches!(k,Key::View(_)|Key::Byte(_)))||Self::has_raw_region(state,&p);
                    if raw {if self.kind(at)=="MatrixIndex"{return p.child(Key::Byte(None));}if let Key::Item(index)=key{return p.child(Key::Byte(index.zip(stride).and_then(|(a,b)|a.checked_mul(b))));}}
                    p.child(key.clone())
                }).collect::<Vec<_>>();
                ps
            }
            _ => vec![],
        }
    }
    fn index(&self, at: Ref, f: &Frame) -> Key {
        let index = self.role(at, "index").or_else(|| self.role(at, "x"));
        Key::Item(
            index.and_then(|a| match self.r.constant_value(a, &f.bindings, None) {
                Some(Constant::Integer(n)) => Some(n),
                _ => None,
            }),
        )
    }
    fn end_temporaries(&mut self, state: &mut State, f: &Frame, at: Ref) {
        if let Some(targets) = self.temporaries.get(&at) {
            for target in targets {
                if let Some(id) = self.ids.get(&(f.id, *target, 1)).copied() {
                    if state.cells.contains_key(&id) {
                        self.release(state, &Value::reference(Place::root(id)), at);
                    }
                }
            }
        }
    }
    fn value(&mut self, state: &mut State, f: &mut Frame, at: Ref, depth: usize) -> Value {
        if self.expression_depth >= MAX_VALUE_DEPTH {
            self.report(
                at,
                None,
                false,
                "lifetime",
                "value expression exceeds the analysis depth budget",
            );
            self.report(
                at,
                None,
                false,
                "leak",
                "value ownership expression exceeds the analysis depth budget",
            );
            return Value {
                unknown: true,
                ..Value::default()
            };
        }
        self.expression_depth += 1;
        let mut v = self.expression(state, f, at, depth);
        self.expression_depth -= 1;
        let typ = self.typ(at, f);
        self.expand_caller_tail(state,f,at,&typ,&mut v);
        // These are resolver constants, independent of mutable graph state.
        // Cache by exact specialization; never cache caller/storage values.
        let facts = if let Some(facts) = self.scalar_facts.get(&at).and_then(|m|m.get(&f.bindings)) {
            *facts
        } else {
            let facts = match self.r.constant_value(at,&f.bindings,None) {
                Some(Constant::Integer(n)) => (Some(u8::try_from(n).ok()),None,Some(n)),
                Some(Constant::Boolean(b)) => (None,Some(b),None),
                _ => (None,None,None),
            };
            self.scalar_facts.entry(at).or_default().insert(f.bindings.clone(),facts);
            facts
        };
        if let Some(byte)=facts.0{v.byte=byte;}
        if facts.1.is_some(){v.truth=facts.1;}
        if let Some(n)=facts.2{if v.number.is_none(){v.number=Some(memory::Number{value:Some(n),wrapped:false});}}
        self.fit_number(&mut v,&typ);
        // Unknown scalar contents are not unknown pointer provenance. Preserve
        // explicit address/integer casts, whose tracked reference edges matter.
        if v.unknown && !v.has_refs() && self.inline_type(&typ, 0) {
            v.unknown = false;
            v.caller_tail = false;
        }
        if matches!(base(&typ),Type::Struct(owner)|Type::Record(owner,_) if self.r.sources[owner.file].node(owner.node).op=="raw_union")
        {
            self.report(
                at,
                v.refs.first().map(|p| p.id),
                false,
                "lifetime",
                "raw union field overlap prevents a reference-provenance proof",
            );
            v.invalidate();
        }
        self.end_temporaries(state, f, at);
        v
    }
    fn expression(&mut self, state: &mut State, f: &mut Frame, at: Ref, depth: usize) -> Value {
        if depth > MAX_VALUE_DEPTH || !self.budget(at) {
            return Value {
                unknown: true,
                ..Value::default()
            };
        }
        let kind = self.kind(at).to_owned();
        if self.text(at)=="context"{return Value{fields:BTreeMap::from([
            (Key::Field("allocator".into()),Self::allocator_value(&state.allocator)),
            (Key::Field("temp_allocator".into()),Self::allocator_value(&state.temp_allocator)),
        ]),..Value::default()};}
        match kind.as_str() {
            "Ident" => {
                if self.r.native_builtin(at, "nil") { return Value { nil_procedure: true, ..Value::default() }; }
                let ps = self.member_places(state, f, at);
                let mut v = self.read_many(state, &ps, at, false);
                if v == Value::default() {
                    v.procedures.extend(self.r.candidates(at));
                }
                v
            }
            "Literal" => {
                if self.text(at).starts_with(['"', '`']) {
                    let id = self.object(state, 0, at, 3, Storage::Static, Value::default());
                    if let Some(Constant::Text(text))=self.r.constant_value(at,&f.bindings,None){
                        let bytes=text.as_bytes();let mut b=buffers::Bytes::new(bytes.len() as i128+1,Some(0));
                        if bytes.len()<=64{for(i,x)in bytes.iter().enumerate(){b.writes.insert(i as i128,Some(*x));}}
                        else{b.default=None;b.writes.insert(bytes.len()as i128,Some(0));}
                        state.buffers.insert(Place::root(id),b);
                    }
                    Value::reference(Place::root(id))
                } else {
                    Value::default()
                }
            }
            "Implicit" | "ImplicitSelector" | "SliceType" | "ArrayType" | "DynamicArrayType"
            | "PointerType" | "MultiPointerType" | "MapType" | "SetType" | "StructType"
            | "UnionType" | "ProcedureType" | "TypeidType" => Value::default(),
            "Directive" => {
                if reference(&self.typ(at, f)) {
                    let id = self.object(state, 0, at, 3, Storage::Static, Value::default());
                    Value::reference(Place::root(id))
                } else {
                    Value::default()
                }
            }
            "Procedure" => Value {
                procedures: BTreeSet::from([at]),
                ..Value::default()
            },
            "Paren" | "Runtime" | "Run" | "OrPanic" | "TypeAssertion" | "AutoCast" => self
                .role(at, "expr")
                .map(|e| self.value(state, f, e, depth + 1))
                .unwrap_or_default(),
            "OrReturn" | "OrBranch" => {
                let value = self
                    .role(at, "expr")
                    .map(|expr| self.value(state, f, expr, depth + 1))
                    .unwrap_or_default();
                self.optional_exit(state, f, at);
                value
            }
            "Cast" => {
                let v = self
                    .role(at, "expr")
                    .map(|e| self.value(state, f, e, depth + 1))
                    .unwrap_or_default();
                if !v.refs.is_empty() {
                    self.report(at,v.refs.first().map(|p|p.id),false,"lifetime","explicit cast can hide storage provenance; lifetime cannot be fully verified");
                }
                v
            }
            "FieldValue" => self
                .role(at, "value")
                .map(|e| self.value(state, f, e, depth + 1))
                .unwrap_or_default(),
            "Unary" => {
                let Some(expr) = self.role(at, "expr") else {
                    return Value::default();
                };
                if self.r.sources[at.file].node(at.node).op == "&" {
                    let places = self.member_places(state, f, expr);
                    for p in &places {
                        self.objects[p.id].addressed = true;
                    }
                    Value {
                        refs: places.into_iter().collect(),
                        ..Value::default()
                    }
                } else {
                    let value = self.value(state, f, expr, depth + 1);
                    self.number_unary(&self.r.sources[at.file].node(at.node).op,&value)
                }
            }
            "Deref" | "Selector" | "Index" | "MatrixIndex" => {
                if self.text(at) == "context.allocator" {
                    return Self::allocator_value(&state.allocator);
                }
                if self.text(at)=="context.temp_allocator"{return Self::allocator_value(&state.temp_allocator);}
                let ps = self.member_places(state, f, at);
                let located = !ps.is_empty();
                let typ=self.typ(at,f);
                if matches!(kind.as_str(),"Deref"|"Index"|"Selector"){self.memory_access(state,&ps,&typ,at,true);}
                let mut v = self.read_many(state, &ps, at, false);
                if reference(&typ)&&!v.has_refs()&&ps.iter().any(|p|Self::has_raw_region(state,p)){
                    v.unknown=true;
                    self.report(at,None,false,"lifetime","reference loaded from raw bytes has unverified provenance");
                }
                if !located {
                    if let Some(expr) = self.role(at, "expr") {
                        let parent = self.value(state, f, expr, depth + 1);
                        if kind == "Selector" {
                            let name = self
                                .role(at, "field")
                                .map(|field| self.text(field).to_owned())
                                .unwrap_or_default();
                            v = parent.child(&Key::Field(name));
                        } else if kind == "Index" || kind == "MatrixIndex" {
                            if matches!(base(&self.typ(expr, f)), Type::Soa(_)) {
                                let key = self.index(at, f);
                                for (field, column) in &parent.fields {
                                    let mut item = column.child(&key);
                                    for p in &column.refs {
                                        item.merge(&self.read(state, &p.child(key.clone()), at));
                                    }
                                    v.fields.insert(field.clone(), item);
                                }
                            } else {
                                v = parent.child(&self.index(at, f));
                            }
                            for p in &parent.refs {
                                v.merge(&self.read(state, p, at).child(&self.index(at, f)));
                            }
                        } else {
                            for p in &parent.refs {
                                v.merge(&self.read(state, p, at));
                            }
                        }
                    }
                }
                if v == Value::default() {
                    v.procedures.extend(self.r.candidates(at));
                }
                v
            }
            "Slice" => {
                let Some(expr) = self.role(at, "expr") else {
                    return Value::default();
                };
                let mut v = if matches!(
                    base(&self.typ(expr, f)),
                    Type::Array { .. } | Type::Fixed { .. }
                ) {
                    Value {
                        refs: self.member_places(state, f, expr).into_iter().collect(),
                        ..Value::default()
                    }
                } else {
                    self.value(state, f, expr, depth + 1)
                };
                for p in &v.refs {
                    self.objects[p.id].addressed = true;
                }
                // Bounds are ordinary expressions: calls in them can release
                // or retain the very storage this new view references.
                for role in ["low", "high"] {
                    if let Some(bound) = self.role(at, role) {
                        let _ = self.value(state, f, bound, depth + 1);
                    }
                }
                let low = self
                    .role(at, "low")
                    .map(|low| self.r.constant_value(low, &f.bindings, None))
                    .unwrap_or(Some(Constant::Integer(0)));
                let high = self
                    .role(at, "high")
                    .map(|high| self.r.constant_value(high, &f.bindings, None));
                v.refs = v
                    .refs
                    .into_iter()
                    .map(|mut p| {
                        let old = p.start;
                        p.start = match (old, &low) {
                            (Some(start), Some(Constant::Integer(low))) => start.checked_add(*low),
                            _ => None,
                        };
                        if let Some(high) = &high {
                            p.end = match (old, high) {
                                (Some(start), Some(Constant::Integer(high))) => start
                                    .checked_add(*high)
                                    .map(|end| p.end.map_or(end, |previous| previous.min(end))),
                                _ => None,
                            };
                        }
                        p.interior |= p.start.is_some_and(|start| start != 0);
                        if p.start.is_none() || high.as_ref().is_some_and(Option::is_none) {
                            v.unknown=true;v.choice=false;v.file_status=None;v.truth=None;
                        }
                        p
                    })
                    .collect();
                v
            }
            "Compound" => {
                let t = self.typ(at, f);
                let fields = self.r.record_fields(&t);
                let mut v = Value::default();
                let elements=self.group(at,"elems");
                // Native empty slice literals are nil headers, without a
                // backing stack allocation. Do not erase provenance from a
                // zero-length view of existing storage instead.
                if elements.is_empty()&&matches!(base(&t),Type::Slice(_)) {
                    v.may_nil=true;
                    v.bytes_len=Some(0);
                    return v;
                }
                for (i, e) in elements.into_iter().enumerate() {
                    let key = if let Some(field) = self.role(e, "field") {
                        if self.kind(field) == "Ident" {
                            Key::Field(self.text(field).to_owned())
                        } else {
                            Key::Item(match self.r.constant_value(field, &f.bindings, None) {
                                Some(Constant::Integer(n)) => Some(n),
                                _ => None,
                            })
                        }
                    } else if let Some((name, _)) = fields.get(i) {
                        Key::Field(name.clone())
                    } else {
                        Key::Item(Some(i as i128))
                    };
                    let item = self.value(state, f, e, depth + 1);
                    v.fields.entry(key).or_default().merge(&item);
                }
                if matches!(base(&t), Type::Slice(_)) {
                    let storage = if self.static_values.contains(&at) || f.id == 0 {
                        Storage::Static
                    } else {
                        Storage::Stack {
                            frame: f.id,
                            scope: f.scope,
                        }
                    };
                    let contents=v.clone();
                    let id = self.object(state, f.id, at, 2, storage, v);
                    if matches!(base(&t),Type::Slice(elem)if Self::byte_element(elem)){
                        let len=contents.fields.keys().filter_map(|k|if let Key::Item(Some(i))=k{Some(*i+1)}else{None}).max().unwrap_or(0);
                        self.literal_buffer(state,Place::root(id),len,&contents);
                    }
                    Value::reference(Place::root(id))
                } else if matches!(base(&t), Type::Dynamic(_) | Type::Map { .. } | Type::Set(_)) {
                    self.allocate(state, f, at, state.allocator.clone(), v)
                } else {
                    v
                }
            }
            "OrElse" => self
                .results(state, f, at, 1)
                .into_iter()
                .next()
                .unwrap_or_default(),
            "Ternary" | "WhenExpr" => {
                let condition = self.role(at, "cond");
                if let Some(c) = condition {
                    let _ = self.value(state, f, c, depth + 1);
                }
                let known =
                    condition.and_then(|c| match self.r.constant_value(c, &f.bindings, None) {
                        Some(Constant::Boolean(b)) => Some(b),
                        _ => None,
                    });
                let mut result = Value::default();
                let before = state.clone();
                let mut branches = vec![];
                for (i, role) in ["x", "y"].iter().enumerate() {
                    if known.is_some_and(|b| b != (i == 0)) {
                        continue;
                    }
                    if let Some(e) = self.role(at, role) {
                        let mut branch = before.clone();
                        let v = self.value(&mut branch, f, e, depth + 1);
                        result.merge(&v);
                        branches.push(branch);
                    }
                }
                if let Some(mut branch) = branches.pop() {
                    for s in branches {
                        branch.merge(&s);
                    }
                    *state = branch;
                }
                result
            }
            "Call" => self
                .call(state, f, at, depth + 1)
                .into_iter()
                .next()
                .unwrap_or_default(),
            "Binary" => {
                let left = self.role(at, "left");
                let lv=left.map(|left|self.value(state,f,left,depth+1)).unwrap_or_default();
                let op = self.r.sources[at.file].node(at.node).op.clone();
                let short = matches!(op.as_str(), "&&" | "||");
                let execute =
                    left.and_then(
                        |left| match self.r.constant_value(left, &f.bindings, None) {
                            Some(Constant::Boolean(b)) if short => Some(b == (op == "&&")),
                            _ => None,
                        },
                    );
                if execute == Some(false) || state.terminated {
                    return Value::default();
                }
                let mut rv=Value::default();
                if let Some(right) = self.role(at, "right") {
                    let before = (short && execute.is_none()).then(|| state.clone());
                    rv = self.value(state, f, right, depth + 1);
                    if let Some(before) = before {
                        if state.terminated {
                            *state = before;
                        } else {
                            state.merge(&before);
                        }
                    }
                }
                let mut result=self.number_binary(&op,&lv,&rv);
                if matches!(op.as_str(),"+"|"-"|"*"|"/"|"%"|"<<"|">>"|"&"|"|"|"~")&&(lv.has_refs()||rv.has_refs()){
                    result.merge(&lv);result.merge(&rv);result.invalidate();
                }
                result
            }
            "Interpolation" => {
                for child in self.r.sources[at.file].node(at.node).children.to_vec() {
                    let arg = Ref { node: child, ..at };
                    let value = self.value(state, f, arg, depth + 1);
                    let typ = self.typ(arg, f);
                    self.consume(state, &value, arg, &typ, 0);
                }
                // Default f-strings carry compiler cleanup at their statement;
                // owned f! strings are caller-managed allocations.
                self.allocate(state, f, at, state.allocator.clone(), Value::default())
            }
            "Comprehension" => {
                if let (Some(loop_), Some(expr)) = (self.role(at, "loop"), self.role(at, "expr")) {
                    self.iteration(state, f, loop_, expr);
                }
                let item = self
                    .role(at, "output")
                    .map(|e| self.value(state, f, e, depth + 1))
                    .unwrap_or_default();
                let mut contents = Value::default();
                contents.fields.insert(Key::Item(None), item);
                if matches!(
                    base(&self.typ(at, f)),
                    Type::Array { .. } | Type::Fixed { .. }
                ) {
                    contents
                } else {
                    self.allocate(state, f, at, state.allocator.clone(), contents)
                }
            }
            "SwitchExpr" | "CompileSwitchExpr" => {
                let cases = self.r.sources[at.file].node(at.node).data["cases"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default();
                let prefixed = self.r.sources[at.file].descendants(at.node).any(|node| {
                    self.r.sources[at.file].node(node).kind == "Case"
                        && self.r.sources[at.file].group(node, "body").len() > 1
                });
                if prefixed {
                    self.report(at,None,false,"lifetime","switch expression has statement-prefix effects requiring manual lifetime review");
                    self.report(at,None,false,"leak","switch expression has statement-prefix effects requiring manual cleanup review");
                    for c in state.cells.values_mut() {
                        c.uncertain = true;
                    }
                }
                let mut v = Value::default();
                let before = state.clone();
                let mut branches = vec![];
                for case in cases {
                    if let Some(node) = Span::from_wire(&case["result"])
                        .and_then(|span| self.r.sources[at.file].at(span, at.node))
                    {
                        let mut branch = before.clone();
                        let item = self.value(&mut branch, f, Ref { node, ..at }, depth + 1);
                        v.merge(&item);
                        branches.push(branch);
                    }
                }
                if let Some(mut branch) = branches.pop() {
                    for b in branches {
                        branch.merge(&b);
                    }
                    *state = branch;
                }
                v
            }
            "SwitchResults" | "FallbackList" => {
                let mut v = Value::default();
                for e in self.group(at, "args") {
                    v.merge(&self.value(state, f, e, depth + 1));
                }
                v
            }
            "Unsafe" => {
                self.report(
                    at,
                    None,
                    false,
                    "lifetime",
                    "unsafe expression or assembly can hide reference effects",
                );
                self.report(
                    at,
                    None,
                    false,
                    "leak",
                    "unsafe expression or assembly can hide ownership effects",
                );
                Value {
                    unknown: true,
                    ..Value::default()
                }
            }
            _ => {
                // Type/declaration syntax has no runtime reference effect. An
                // unmodelled expression with reference results is unverified.
                let typ = self.typ(at, f);
                if reference(&typ) {
                    self.report(
                        at,
                        None,
                        false,
                        "lifetime",
                        "expression has unmodelled reference semantics",
                    );
                    Value {
                        unknown: true,
                        ..Value::default()
                    }
                } else {
                    Value::default()
                }
            }
        }
    }
    fn call_model(&mut self, at: Ref, callee: Ref) -> CallModel {
        if let Some(model) = self.models.get(&at) {
            model.clone()
        } else {
            let (builtin, cloning) = {
                let mut arena = crate::borrowflow::Arena::default();
                let eval = crate::borroweval::Evaluator {
                    resolver: self.r,
                    arena: &mut arena,
                };
                (eval.builtin(at), eval.clone_library(at).is_some())
            };
            let mut library = None;
            for path in [
                "base:intrinsics",
                "core:mem",
                "core:math",
                "base:runtime",
                "core:strings",
                "core:slice",
                "core:fmt",
                "core:os",
                "core:mem/virtual",
                "thor:parallel",
                "thor:basic",
                "thor:sets",
                "thor:big",
                "core:sort",
                "core:unicode/utf8",
                "core:encoding/endian",
                "core:strconv",
                "core:math/rand",
                "core:thread",
                "vendor:raylib",
            ] {
                if let Some(name) = crate::evaluation::library_member(self.r, callee, path) {
                    if crate::privileges::verified_library(self.r, callee, path) {
                        library = Some((path, name));
                        break;
                    }
                }
            }
            let cloning = cloning
                && library.as_ref().is_some_and(|(path, name)| {
                    matches!(*path, "core:slice" | "core:strings") && name == "clone"
                });
            let model = CallModel {
                builtin,
                cloning,
                library,
            };
            self.models.insert(at, model.clone());
            model
        }
    }
    fn call(&mut self, state: &mut State, f: &mut Frame, at: Ref, depth: usize) -> Vec<Value> {
        let Some(callee) = self.role(at, "callee") else {
            return vec![];
        };
        let CallModel { builtin, cloning, library } = self.call_model(at, callee);
        if builtin.as_deref() == Some("assert")
            && self.r.flags.iter().any(|flag| flag == "-disable-assert")
        {
            return vec![];
        }
        if library
            .as_ref()
            .is_some_and(|(path, name)| *path == "core:fmt" && name == "assertf")
            && self.r.flags.iter().any(|flag| flag == "-disable-assert")
        {
            return vec![];
        }
        let args = self.group(at, "args");
        let mut evaluated = HashMap::new();
        let mut vs = vec![];
        for arg in &args {
            let actual = self.role(*arg, "value").unwrap_or(*arg);
            let v = if self.r.denotes_type(actual) {
                Value::default()
            } else {
                self.value(state, f, actual, depth + 1)
            };
            evaluated.insert(actual, v.clone());
            evaluated.insert(*arg, v.clone());
            vs.push(v);
        }
        if self.r.denotes_type(callee) {
            let t = self.typ(at, f);
            if matches!(base(&t),Type::String{name,..}if name=="string") && args.first().is_some_and(|a|Self::cstring_type(&self.typ(*a,f))){
                self.direct(state,&vs[0],at);self.check_termination(state,&vs[0],at);
            }
            if t.big_name().is_some() {
                self.report(
                    at,
                    None,
                    false,
                    "leak",
                    "arbitrary-precision storage ownership requires manual cleanup review",
                );
                return vec![Value {
                    unknown: true,
                    ..Value::default()
                }];
            }
            let mut v = vs.first().cloned().unwrap_or_default();
            if let Type::Pointer(elem)=base(&t){if let Some(width)=self.r.simple_size(elem){
                let source=args.first().map(|a|self.typ(self.role(*a,"value").unwrap_or(*a),f));
                let same=source.as_ref().is_some_and(|t|matches!(base(t),Type::Pointer(elem)if self.r.simple_size(elem)==Some(width)));
                if !same{v.refs=v.refs.into_iter().map(|p|p.child(Key::View(width))).collect();}
            }}
            if reference(&t)
                || matches!(
                    base(&t),
                    Type::Struct(_)
                        | Type::Record(..)
                        | Type::Anonymous(_)
                        | Type::Array { .. }
                        | Type::Fixed { .. }
                        | Type::Union(..)
                )
            {
                return vec![v];
            }
            if !v.refs.is_empty() {
                self.unknown_call(
                    state,
                    at,
                    &[v.clone()],
                    "conversion hides reference provenance in a scalar",
                );
                return vec![v];
            }
            return vec![Value{number:v.number,..Value::default()}];
        }
        let name = builtin
            .as_deref()
            .or_else(|| library.as_ref().map(|(_, name)| name.as_str()))
            .unwrap_or("");
        let lib = library.as_ref().map(|(path, _)| *path).unwrap_or("");
        if let Some(result)=self.memory_call(state,f,at,&args,&vs,builtin.as_deref(),lib,name){return result;}
        if let Some(result)=self.buffer_call(state,f,at,&args,&vs,builtin.as_deref(),lib,name){return result;}
        if let Some(result) = self.file_model(state, f, at, &args, &vs, lib, name) {
            return result;
        }
        if lib=="core:thread" {
            if let Some(result)=self.worker_model(state,f,at,&args,&vs,name){return result;}
            if let Some(result)=self.pool_model(state,f,at,&args,&vs,name){return result;}
        }
        if let Some(result) = self.foreign_model(state, f, at, &args, &vs, lib, name) {
            return result;
        }
        if lib == "thor:parallel" && name == "pmap" {
            return self.parallel_call(state, f, at, &args, &vs, depth);
        }
        if lib == "core:slice" && name == "sort_by" {
            if let (Some(data), Some(less)) = (
                self.argument(&args, "data", 0),
                self.argument(&args, "less", 1),
            ) {
                self.direct(state, &vs[data], at);
                let item = self.items(state, &vs[data], at);
                for p in &vs[data].refs {
                    state.write(&p.child(Key::Item(None)), &item, true);
                }
                let targets = &vs[less].procedures;
                if targets.len() == 1 {
                    let procedure = *targets.first().unwrap();
                    if self.role(procedure, "body").is_some()
                        && !self.r.sources[procedure.file].shared_native
                        && !self.active.iter().any(|(p, _)| *p == procedure)
                        && self.active.len() < MAX_DEPTH
                    {
                        let signature = self.role(procedure, "type").unwrap_or(procedure);
                        let data_type =
                            self.typ(self.role(args[data], "value").unwrap_or(args[data]), f);
                        let element = data_type.element().cloned().unwrap_or(Type::Unknown);
                        let bindings = self
                            .r
                            .parameter_bindings(signature, &[element.clone(), element]);
                        let before = state.clone();
                        self.active.push((procedure, at));
                        let exits = self.procedure(
                            state.clone(),
                            procedure,
                            vec![item.clone(), item.clone()],
                            bindings,
                        );
                        self.active.pop();
                        for exit in exits.into_iter().filter(|e| e.control != Control::Stop) {
                            if before
                                .cells
                                .iter()
                                .filter(|(_, c)| c.life & LIVE != 0)
                                .any(|(id, c)| exit.state.cells.get(id) != Some(c))
                            {
                                self.unknown_call(state, at, &[item.clone()], "sort comparator changes shared references; repeated effects require review");
                            }
                            state.merge(&exit.state);
                        }
                        return vec![];
                    }
                }
                self.unknown_call(
                    state,
                    at,
                    &[item],
                    "sort comparator implementation cannot be verified",
                );
                return vec![];
            }
        }
        if lib == "thor:basic" && name == "sum" {
            if let Some(i) = self.argument(&args, "vals", 0) {
                let actual = self.role(args[i], "value").unwrap_or(args[i]);
                let typ = self.typ(actual, f);
                if matches!(base(&typ),Type::Slice(elem)|Type::Dynamic(elem)|Type::Fixed{elem,..}|Type::Array{elem,..} if elem.numeric())
                {
                    self.direct(state, &vs[i], at);
                    return vec![Value::default()];
                }
            }
        }
        if lib == "core:strings" && matches!(name, "fields_iterator" | "split_iterator") {
            if let Some(i) = self.argument(&args, "s", 0) {
                let mut result = Value::default();
                for p in &vs[i].refs {
                    let mut remaining = self.read(state, p, at);
                    self.direct(state, &remaining, at);
                    result.merge(&remaining);
                    for source in remaining.refs.iter().cloned().collect::<Vec<_>>() {
                        remaining.refs.remove(&source);
                        let mut source = source;
                        source.start = None;
                        remaining.refs.insert(source);
                    }
                    remaining.invalidate();
                    state.write(p, &remaining, vs[i].refs.len() != 1);
                }
                for (j, value) in vs.iter().enumerate() {
                    if j != i {
                        self.direct(state, value, at);
                    }
                }
                result.invalidate();
                return vec![result, Value::default()];
            }
        }
        if lib == "core:strings" && matches!(name, "builder_from_bytes" | "builder_from_slice") {
            if let Some(i) = self.argument(&args, "backing", 0) {
                self.direct(state, &vs[i], at);
                return vec![Value {
                    fields: BTreeMap::from([
                        (Key::Field("buf".into()), vs[i].clone()),
                        (Key::Field("<fixed-builder>".into()), Value::default()),
                    ]),
                    ..Value::default()
                }];
            }
        }
        if lib == "core:strings"
            && matches!(
                name,
                "write_string"
                    | "write_byte"
                    | "write_rune"
                    | "to_cstring"
                    | "to_string"
                    | "unsafe_to_cstring"
            )
            || lib == "core:fmt"
                && matches!(name, "sbprint" | "sbprintln" | "sbprintf" | "sbprintfln")
        {
            if let Some(i) = self.argument(&args, if lib == "core:fmt" { "buf" } else { "b" }, 0) {
                let mut builder = if vs[i].fields.contains_key(&Key::Field("buf".into())) {
                    vs[i].clone()
                } else {
                    Value::default()
                };
                if builder == Value::default() {
                    for p in &vs[i].refs {
                        builder.merge(&self.read(state, p, at));
                    }
                }
                if builder
                    .fields
                    .contains_key(&Key::Field("<fixed-builder>".into()))
                {
                    let data = builder.child(&Key::Field("buf".into()));
                    self.direct(state, &data, at);
                    if lib=="core:fmt" || name.starts_with("write_"){Self::forget_buffer_contents(state,&data);}
                    if lib == "core:fmt" && self.custom_formatting {
                        self.unknown_call(
                            state,
                            at,
                            &vs,
                            "custom formatters may retain or release fixed-buffer builder inputs",
                        );
                    } else {
                        for (j, (arg, value)) in args.iter().zip(&vs).enumerate() {
                            if j == i
                                || self
                                    .argument_name(*arg)
                                    .is_some_and(|field| self.text(field) == "loc")
                            {
                                continue;
                            }
                            let typ = self.typ(self.role(*arg, "value").unwrap_or(*arg), f);
                            self.consume(state, value, at, &typ, 0);
                        }
                    }
                    return if lib == "core:fmt"
                        || matches!(name, "to_cstring" | "to_string" | "unsafe_to_cstring")
                    {
                        vec![data, Value::default()]
                    } else {
                        vec![Value::default(), Value::default()]
                    };
                }
            }
        }
        if let Some(result) = self.builder_model(state, f, at, &args, &vs, lib, name) {
            return result;
        }
        if let Some(result) = self.scalar_library_model(state, f, at, &args, &vs, lib, name) {
            return result;
        }
        if matches!(name, "panic" | "unreachable") && builtin.is_some() {
            state.terminated = true;
            return vec![];
        }
        if matches!(name, "assert" | "assert_msg") && builtin.is_some() {
            if args.first().is_some_and(|arg| {
                self.r.constant_value(*arg, &f.bindings, None) == Some(Constant::Boolean(false))
            }) {
                state.terminated = true;
            }
            return vec![];
        }
        if lib == "core:math" && matches!(name, "abs" | "min" | "max" | "clamp") {
            return vec![Value::default()];
        }
        if name == "heap_allocator" && lib == "base:runtime" {
            return vec![Value {
                allocator: Some(Allocator::Heap),
                ..Value::default()
            }];
        }
        if lib == "thor:basic" && name == "scoped_arena" {
            let region = self.object(
                state,
                f.id,
                at,
                6,
                Storage::Stack {
                    frame: f.id,
                    scope: f.scope,
                },
                Value::default(),
            );
            state
                .cleanups
                .entry((f.id, f.scope))
                .or_default()
                .push(Pending {
                    action: Cleanup::Region(region),
                    conditional: false,
                    allocator:state.allocator.clone(),
                    temp_allocator:state.temp_allocator.clone(),
                });
            return vec![Value {
                allocator: Some(Allocator::Region(Arc::new(Place::root(region)))),
                refs: BTreeSet::from([Place::root(region)]),
                ..Value::default()
            }];
        }
        if name == "arena_allocator" && matches!(lib, "core:mem" | "core:mem/virtual") {
            let allocator = vs
                .first()
                .and_then(|v| v.refs.first())
                .map(|p| Allocator::Region(Arc::new(p.clone())))
                .unwrap_or(Allocator::Unknown);
            return vec![Value {
                allocator: Some(allocator),
                refs: vs.first().map(|v| v.refs.clone()).unwrap_or_default(),
                ..Value::default()
            }];
        }
        if (name == "arena_destroy" || name == "arena_free_all")
            && matches!(lib, "core:mem" | "core:mem/virtual")
        {
            if let Some(v) = vs.first() {
                self.direct(state, v, at);
                let definite = v.refs.len() == 1 && !v.unknown;
                for p in &v.refs {
                    self.end_region(state, p, at, definite);
                    if name == "arena_destroy" {
                        let mut backing =
                            state.read(&p.child(Key::Field("<arena-backing>".into())));
                        backing.unknown |= !definite;
                        self.release(state, &backing, at);
                    }
                }
            }
            return vec![];
        }
        if matches!(
            name,
            "arena_init" | "arena_init_growing" | "arena_init_static"
        ) && matches!(lib, "core:mem" | "core:mem/virtual")
        {
            if let Some(arena) = vs.first() {
                self.direct(state, arena, at);
            }
            // The arena's backing allocation is owned by its explicit destroy.
            // Track it as an ordinary heap object behind the arena record.
            if name == "arena_init_static" {
                self.unknown_call(
                    state,
                    at,
                    &vs,
                    "static arena shares caller-provided backing storage",
                );
                return vec![Value::default()];
            }
            let backing = self.allocate(state, f, at, state.allocator.clone(), Value::default());
            if let Some(v) = vs.first() {
                for p in &v.refs {
                    state.write(
                        &p.child(Key::Field("<arena-backing>".into())),
                        &backing,
                        false,
                    );
                }
            }
            return vec![Value::default()];
        }
        if (builtin.is_some() && matches!(name, "make" | "new" | "new_clone" | "clone"))
            || cloning
            || matches!(
                (lib, name),
                ("core:strings", "clone" | "clone_to_cstring" | "concatenate")
                    | (
                        "core:fmt",
                        "aprint"
                            | "aprintln"
                            | "aprintf"
                            | "aprintfln"
                            | "caprint"
                            | "caprintf"
                            | "caprintfln"
                    )
                    | ("thor:basic", "_string")
                    | ("core:unicode/utf8", "runes_to_string")
            )
        {
            let mut contents = Value::default();
            if lib == "core:fmt"
                || lib == "thor:basic" && name == "_string"
                || lib == "core:unicode/utf8"
            {
                if lib == "core:fmt" && self.custom_formatting {
                    self.unknown_call(state,at,&vs,"custom formatters are registered; formatter retention/cleanup effects cannot be verified");
                } else {
                    for (arg, value) in args.iter().zip(&vs) {
                        if self
                            .argument_name(*arg)
                            .is_some_and(|field| matches!(self.text(field), "allocator" | "loc"))
                        {
                            continue;
                        }
                        let typ = self.typ(self.role(*arg, "value").unwrap_or(*arg), f);
                        self.consume(state, value, at, &typ, 0);
                    }
                }
            }
            if name == "make"
                && matches!(
                    base(&self.typ(at, f)),
                    Type::Fixed { .. } | Type::Array { .. }
                )
            {
                return vec![contents];
            }
            if cloning || matches!(name, "clone" | "new_clone") {
                if let Some(v) = vs.first() {
                    for p in &v.refs {
                        contents.merge(&self.read(state, p, at));
                    }
                    contents.fields.extend(v.fields.clone());
                }
            }
            let allocator_arg = crate::scoped::allocator_argument(self.r, at);
            let allocator = allocator_arg
                .and_then(|a| {
                    evaluated
                        .get(&a)
                        .cloned()
                        .or_else(|| Some(self.value(state, f, a, depth + 1)))
                })
                .and_then(|v| v.allocator)
                .unwrap_or_else(|| state.allocator.clone());
            let empty = if name == "make" {
                let t = self.typ(at, f);
                let sizes = args
                    .iter()
                    .skip(1)
                    .filter(|a| self.kind(**a) != "FieldValue")
                    .filter_map(|a| match self.r.constant_value(*a, &f.bindings, None) {
                        Some(Constant::Integer(n)) => Some(n),
                        _ => None,
                    })
                    .collect::<Vec<_>>();
                matches!(base(&t), Type::Slice(_) | Type::Dynamic(_))
                    && !sizes.is_empty()
                    && sizes.iter().all(|n| *n == 0)
                    || matches!(base(&t), Type::Dynamic(_)) && args.len() == 1
            } else {
                false
            };
            let mut v = if empty {
                Value::default()
            } else {
                self.allocate(state, f, at, allocator, contents)
            };
            if name == "make" {
                let t = self.typ(at, f);
                let uncertain_size = matches!(base(&t), Type::Slice(_) | Type::Dynamic(_))
                    && args.iter().skip(1).any(|a| {
                        self.kind(*a) != "FieldValue"
                            && self.r.constant_value(*a, &f.bindings, None).is_none()
                    });
                if uncertain_size {
                    for p in &v.refs {
                        if let Some(c) = state.cells.get_mut(&p.id) {
                            c.life |= ABSENT;
                        }
                    }
                }
            }
            self.allocation_buffer(state,f,at,&v,&args,name);
            self.allocation_region(state,f,at,&v,&args,name);
            if name=="make"{let typ=self.typ(at,f);if let Type::Slice(elem)|Type::Dynamic(elem)=base(&typ){
                v.bytes_len=self.argument(&args,"len",1).and_then(|i|vs[i].number.and_then(|n|n.value)).zip(self.r.simple_size(elem)).and_then(|(a,b)|a.checked_mul(b));
            }}
            return vec![v, Value::default()];
        }
        if lib == "core:strings" && matches!(name, "fields" | "split" | "split_n" | "split_lines") {
            // These APIs allocate the outer header array, while each string is
            // a borrowed view of s. The delimiter is read, never retained.
            for (arg, value) in args.iter().zip(&vs) {
                if self
                    .argument_name(*arg)
                    .is_some_and(|field| matches!(self.text(field), "allocator" | "loc"))
                {
                    continue;
                }
                self.direct(state, value, at);
            }
            let source = args
                .iter()
                .position(|arg| {
                    self.argument_name(*arg)
                        .is_some_and(|field| self.text(field) == "s")
                })
                .or_else(|| {
                    args.iter()
                        .position(|arg| self.argument_name(*arg).is_none())
                });
            let mut part = source.and_then(|i| vs.get(i)).cloned().unwrap_or_default();
            // Segment bounds are runtime data. Preserve storage identity but
            // never claim that a segment is an owning, unre-based allocation.
            part.refs = part
                .refs
                .into_iter()
                .map(|mut p| {
                    p.start = None;
                    p
                })
                .collect();
            part.invalidate();
            let allocator_arg = crate::scoped::allocator_argument(self.r, at);
            let allocator = allocator_arg
                .and_then(|a| {
                    evaluated
                        .get(&a)
                        .cloned()
                        .or_else(|| Some(self.value(state, f, a, depth + 1)))
                })
                .and_then(|v| v.allocator)
                .unwrap_or_else(|| state.allocator.clone());
            let contents = Value {
                fields: BTreeMap::from([(Key::Item(None), part)]),
                ..Value::default()
            };
            let value = self.allocate(state, f, at, allocator, contents);
            for p in &value.refs {
                if let Some(c) = state.cells.get_mut(&p.id) {
                    c.life |= ABSENT;
                }
            }
            return vec![value, Value::default()];
        }
        if builtin.is_some() && matches!(name, "delete" | "free" | "delete_slice" | "delete_string" | "delete_cstring" | "delete_dynamic_array" | "delete_map" | "delete_soa_slice" | "delete_soa_dynamic_array")
            || matches!(
                (lib, name),
                ("core:mem", "free" | "free_with_size" | "free_bytes" | "delete" | "delete_slice" | "delete_string" | "delete_cstring" | "delete_dynamic_array" | "delete_map" | "delete_soa_slice" | "delete_soa_dynamic_array")
                    | ("base:runtime", "mem_free" | "mem_free_with_size")
                    | ("thor:sets", "destroy")
                    | ("thor:big", "destroy")
            )
        {
            let target=self.cleanup_target(&args);
            if let Some(v) = vs.get(target) {
                if matches!(lib, "thor:sets" | "thor:big") {
                    for p in &v.refs {
                        let header = state.read(p);
                        self.release(state, &header, at);
                    }
                } else {
                    let typ=self.typ(self.role(args[target],"value").unwrap_or(args[target]),f);
                    let embedded=matches!(name,"delete_dynamic_array"|"delete_soa_dynamic_array"|"delete_map")
                        || name=="delete" && Self::stored_allocator(&typ);
                    let position=if matches!(name,"free_with_size"|"mem_free_with_size"){2}else{1};
                    let allocator=self.argument(&args,"allocator",position).map(|i|vs[i].allocator.clone().unwrap_or(Allocator::Unknown)).unwrap_or_else(||state.allocator.clone());
                    if embedded || self.check_allocator(state,v,&allocator,at){self.release(state, v, at);}
                }
            }
            return vec![];
        }
        if (builtin.is_some() && matches!(name, "append" | "append_elem"))
            || lib == "thor:basic" && matches!(name, "append" | "append_elem")
        {
            if let Some(target) = vs.first() {
                let fixed=args.first().is_some_and(|arg|matches!(base(&self.typ(*arg,f)),Type::Pointer(inner) if matches!(base(inner),Type::Fixed{..}|Type::Array{..})));
                for p in &target.refs {
                    if fixed {
                        self.read(state, p, at);
                        for arg in vs.iter().skip(1) {
                            let item = if self.r.sources[at.file]
                                .node(at.node)
                                .spans
                                .contains_key("ellipsis")
                            {
                                self.items(state, arg, at)
                            } else {
                                arg.clone()
                            };
                            state.write(&p.child(Key::Item(None)), &item, true);
                        }
                        continue;
                    }
                    let mut collection = self.read(state, p, at);
                    if !fixed{Self::forget_buffer_extent(state,&collection);}
                    if collection.refs.is_empty() {
                        collection =
                            self.allocate(state, f, at, state.allocator.clone(), Value::default());
                        state.write(p, &collection, false);
                    }
                    for destination in &collection.refs {
                        let aliased = state.cells.iter().any(|(id, c)| {
                            *id != p.id
                                && c.life & LIVE != 0
                                && c.value.refs.iter().any(|view| view.id == destination.id)
                        });
                        if aliased {
                            self.report(at,Some(destination.id),false,"lifetime","append may relocate backing storage while an independent view exists");
                            if let Some(c) = state.cells.get_mut(&destination.id) {
                                c.uncertain = true;
                            }
                        }
                        for arg in vs.iter().skip(1) {
                            let mut item = arg.clone();
                            if self.r.sources[at.file]
                                .node(at.node)
                                .spans
                                .contains_key("ellipsis")
                            {
                                item = self.items(state, arg, at);
                            }
                            state.write(&destination.child(Key::Item(None)), &item, true);
                        }
                    }
                }
            }
            return vec![Value::default()];
        }
        if builtin.is_some() && name == "copy" {
            if let (Some(dest), Some(source)) = (vs.first(), vs.get(1)) {
                self.direct(state, source, at);
                self.direct(state, dest, at);
                let item = self.items(state, source, at);
                for p in &dest.refs {
                    state.write(&p.child(Key::Item(None)), &item, true);
                }
            }
            return vec![Value::default()];
        }
        if builtin.is_some() && name == "soa_zip" {
            let mut v = Value::default();
            for (i, arg) in args.iter().enumerate() {
                let key = self
                    .argument_name(*arg)
                    .map(|field| Key::Field(self.text(field).to_owned()))
                    .unwrap_or(Key::Item(Some(i as i128)));
                v.fields.insert(key, vs[i].clone());
            }
            return vec![v];
        }
        if matches!(
            name,
            "len"
                | "cap"
                | "size_of"
                | "align_of"
                | "offset_of"
                | "type_of"
                | "typeid_of"
                | "type_info_of"
                | "min"
                | "max"
                | "abs"
                | "clamp"
        ) && builtin.is_some()
        {
            return vec![Value::default()];
        }
        if builtin.is_some()
            && matches!(
                name,
                "clear"
                    | "reserve"
                    | "resize"
                    | "pop"
                    | "ordered_remove"
                    | "unordered_remove"
                    | "remove_range"
            )
        {
            // Native resize-to-zero only changes the header length. It cannot
            // grow, relocate or touch the existing backing storage.
            if name=="resize" && self.argument(&args,"len",1).or_else(||self.argument(&args,"length",1)).is_some_and(|i|
                self.r.constant_value(self.role(args[i],"value").unwrap_or(args[i]),&f.bindings,None)==Some(Constant::Integer(0))) {
                if let Some(target)=vs.first(){self.direct(state,target,at);}return vec![Value::default()];
            }
            if matches!(name,"reserve"|"resize"){if let Some(target)=vs.first(){for p in &target.refs{let value=state.read(p);Self::forget_buffer_extent(state,&value);}}}
            // These operations do not retain references externally, but changes
            // to capacity may relocate outstanding views. Mark that uncertainty.
            if name == "clear" {
                if let Some(target) = vs.first() {
                    for p in &target.refs {
                        let header = state.read(p);
                        let destinations = if header.refs.is_empty() {
                            vec![p.clone()]
                        } else {
                            header.refs.into_iter().collect()
                        };
                        for destination in destinations {
                            if let Some(c) = state.cells.get_mut(&destination.id) {
                                let mut contents = c.value.read(&destination.path);
                                contents.fields.retain(|k, _| {
                                    !matches!(k, Key::Item(_) | Key::Range(..))
                                        && !matches!(k,Key::Field(name) if name=="<keys>")
                                });
                                c.value.write(&destination.path, &contents, false);
                            }
                        }
                    }
                }
                return vec![];
            }
            if name == "pop" {
                let mut v = Value::default();
                if let Some(a) = vs.first() {
                    for p in &a.refs {
                        let header = self.read(state, p, at);
                        v.merge(&header.child(&Key::Item(None)));
                        for data in &header.refs {
                            v.merge(&self.read(state, data, at).child(&Key::Item(None)));
                        }
                    }
                }
                // Pop transfers an element, not the backing allocation. Keep
                // the summarized remainder as an overapproximation; every
                // nested allocation still has its own cleanup obligation, so
                // discarding the result cannot silently discharge a leak.
                return vec![v];
            }
            if name == "remove_range" {
                if let Some(target) = vs.first() {
                    self.direct(state, target, at);
                    for p in &target.refs {
                        let header = self.read(state, p, at);
                        let destinations = if header.refs.is_empty() {
                            vec![p.clone()]
                        } else {
                            header.refs.into_iter().collect()
                        };
                        for destination in destinations {
                            // Removal does not free the backing buffer. A
                            // summarized removed owner must not count as a
                            // certain ownership transfer through the remainder.
                            let mut contents = self.read(state, &destination, at);
                            for (key, item) in &mut contents.fields {
                                if matches!(key, Key::Item(_) | Key::Range(..)) && item.has_refs() {
                                    item.invalidate();
                                }
                            }
                            state.write(&destination, &contents, false);
                        }
                    }
                }
                return vec![];
            }
            if matches!(name, "reserve" | "resize") {
                if let Some(v) = vs.first() {
                    let ids = state.reachable([v.clone()]);
                    for id in ids {
                        let standalone =
                            self.r.sources[at.file]
                                .node(at.node)
                                .parent
                                .is_some_and(|p| {
                                    self.r.sources[at.file].node(p).kind == "ExpressionStatement"
                                });
                        let headers = vs
                            .first()
                            .map(|v| v.refs.iter().map(|p| p.id).collect::<BTreeSet<_>>())
                            .unwrap_or_default();
                        let alias=state.cells.iter().any(|(owner,c)| !headers.contains(owner) && c.life&LIVE!=0 && c.value.references(id))
                            || state.cleanups.values().flatten().any(|pending|matches!(&pending.action,Cleanup::Procedure{inputs,..} if inputs.iter().any(|v|v.references(id))));
                        if matches!(self.objects[id].storage, Storage::Heap(_))
                            && (alias
                                || !standalone
                                || !self.active.is_empty()
                                || state.cells[&id].uncertain)
                        {
                            self.report(at,Some(id),false,"lifetime","capacity change may invalidate existing views; relocation cannot be verified");
                        }
                    }
                }
            }
            return vec![Value::default()];
        }
        if matches!(contracts::effect(lib, name), Some(contracts::Effect::Format)) {
            if name == "assertf"
                && args.first().is_some_and(|arg| {
                    matches!(
                        self.r.constant_value(*arg, &f.bindings, None),
                        Some(Constant::Boolean(true))
                    )
                })
            {
                return vec![];
            }
            if self.custom_formatting || self.custom_io && !name.starts_with('b') {
                if state.reachable(vs.iter().cloned()).is_empty() {
                    self.report(at,None,false,"lifetime","custom formatting or I/O callbacks have unverified global reference effects");
                }
                self.unknown_call(state,at,&vs,"custom formatting or I/O callbacks may retain or release references");
            } else {
                for (i, v) in vs.iter().enumerate() {
                    let typ = args
                        .get(i)
                        .map(|a| self.typ(self.role(*a, "value").unwrap_or(*a), f))
                        .unwrap_or(Type::Unknown);
                    self.consume(state, v, at, &typ, 0);
                }
            }
            if name.starts_with('b') {
                let buffer=self.argument(&args,"buf",0).and_then(|i|vs.get(i)).cloned().unwrap_or_default();
                // Output bytes can overwrite encoded addresses. Their former
                // edges are no longer a verified ownership-transfer path.
                for pointer in &buffer.refs {
                    if let Some(cell)=state.cells.get_mut(&pointer.id) {
                        if cell.value.has_refs() {cell.uncertain=true;}
                    }
                }
                return vec![buffer];
            }
            return vec![Value::default()];
        }
        if matches!(contracts::effect(lib, name), Some(contracts::Effect::Endian))
        {
            for value in &vs {
                self.direct(state, value, at);
            }
            let width=if name.contains("16") {2} else if name.contains("32") {4} else {8};
            let buffer=self.argument(&args,"b",0).and_then(|i|vs.get(i));
            if name.starts_with("unchecked_put_") {
                let mut encoded=self.argument(&args,"v",1).and_then(|i|vs.get(i)).cloned().unwrap_or_default();
                encoded.byte=None;
                if let Some(buffer)=buffer {
                    for pointer in &buffer.refs {
                        for index in 0..width {
                            state.write(&pointer.child(Key::Item(Some(index))),&encoded,buffer.unknown || buffer.refs.len()!=1);
                        }
                    }
                }
                return vec![];
            }
            let mut decoded=Value::default();
            if let Some(buffer)=buffer {
                for pointer in &buffer.refs {
                    for index in 0..width {
                        decoded.merge(&state.read(&pointer.child(Key::Item(Some(index)))));
                    }
                }
            }
            return vec![decoded];
        }
        if lib == "core:os" && matches!(name, "read" | "write" | "read_at" | "write_at") {
            if name.starts_with("read"){if let Some(i)=self.argument(&args,"p",1).or_else(||self.argument(&args,"buf",1)){
                Self::forget_buffer_contents(state,&vs[i]);
                self.memory_written(state,&vs[i],vs[i].bytes_len);
            }}
            if name.starts_with("write"){if let Some(i)=self.argument(&args,"p",1).or_else(||self.argument(&args,"buf",1)){
                if let Some(n)=vs[i].bytes_len{self.memory_read(state,&vs[i],n,at);}
            }}
            for v in &vs {
                self.direct(state, v, at);
            }
            return vec![Value::default(), Value::default()];
        }
        // Resolve visible concrete callbacks as well as aliases/overloads. Call
        // arguments are evaluated once, before binding defaults and parameters.
        let mut plans = self.r.call_plans(at, &f.bindings);
        let callable = self.value(state, f, callee, depth + 1);
        if callable.nil_procedure {
            let definite = callable.procedures.is_empty() && !callable.unknown;
            self.report(at, None, definite, "lifetime", if definite { "call through a nil procedure value" } else { "runtime procedure target may be nil" });
            if definite { state.terminated = true; return vec![]; }
        }
        let closed_targets = !callable.unknown || callable.choice;
        let mut targets = if closed_targets { callable.procedures } else { BTreeSet::new() };
        // A constant declaration keeps its identity when the value interpreter
        // runs out of work. Invalidation of runtime storage has no such proof.
        if plans.len() == 1
            && (crate::parallelcompile::known_callback(self.r, callee)
                || closed_targets && targets.is_empty())
        {
            targets.clear();
            targets.insert(plans[0].procedure);
        } else if targets.len() == 1 {
            let target = *targets.first().unwrap();
            if self.kind(target) == "Procedure" && !plans.iter().any(|p| p.procedure == target) {
                plans = self.r.call_plans_for_targets(at, &f.bindings, &[target]);
            }
        }
        if targets.len() == 1 {
            if let Some(values) = self.invoke_procedure(state, f, at, *targets.first().unwrap(), &plans, &args, &evaluated, &vs, depth) {
                return values;
            }
        } else if (2..=8).contains(&targets.len()) && targets.iter().all(|target| {
            !self.r.sources[target.file].shared_native && self.role(*target, "body").is_some()
        }) {
            // The callback expression and actual arguments were evaluated once.
            // Follow each bounded runtime alternative with the same input state.
            let before = state.clone();
            let mut after = None;
            let mut results = vec![];
            for target in &targets {
                let mut branch = before.clone();
                let target_plans = self.r.call_plans_for_targets(at, &f.bindings, &[*target]);
                let values = self.invoke_procedure(&mut branch, f, at, *target, &target_plans, &args, &evaluated, &vs, depth)
                    .expect("inspectable callback target");
                if branch.terminated { continue; }
                results.resize(results.len().max(values.len()), Value::default());
                for (result, value) in results.iter_mut().zip(values) { result.merge(&value); }
                if let Some(after) = &mut after { State::merge(after, &branch); }
                else { after = Some(branch); }
            }
            if let Some(after) = after {
                let callable_type = self.typ(callee, f);
                if callable_type.pure_callable() && results.iter().any(Value::has_refs) {
                    // The inspected alternatives all obey the same fresh-result
                    // contract. Use one result graph without losing branch-body
                    // diagnostics or inventing aliases between exclusive owners.
                    *state = before;
                    if let Some(values) = self.pure_callback(state, f, at, &args, &vs, &callable_type) { return values; }
                }
                *state = after;
            }
            else { state.terminated = true; }
            crate::profile::count("lifetime.callback_alternatives", targets.len() as u64);
            return results;
        }
        let callable_type = self.typ(callee, f);
        if let Some(values) = self.pure_callback(state, f, at, &args, &vs, &callable_type) { return values; }
        // A checked fn callback cannot affect external mutable reference state.
        // Its body and any argument/result storage still get their own checks.
        if matches!(base(&callable_type), Type::Procedure(_))
            && !crate::parallelcompile::known_callback(self.r, callee)
            && state.reachable(vs.iter().cloned()).is_empty()
            && !vs.iter().any(|v| v.unknown)
        {
            self.report(at,None,false,"lifetime","runtime procedure target is unresolved; effects on global reference state cannot be verified");
        }
        let v = self.unknown_call(
            state,
            at,
            &vs,
            "call has no reviewed lifetime/ownership model or inspectable implementation",
        );
        let t = self.typ(at, f);
        let has_result = self
            .r
            .call_signature(at)
            .map(|sig| !self.r.fields(sig, "results").is_empty())
            .unwrap_or(reference(&t) || t.is_unknown());
        if has_result && (reference(&t) || t.is_unknown()) {
            self.report(
                at,
                None,
                false,
                "lifetime",
                "reference result has unverified provenance and lifetime",
            );
            self.report(
                at,
                None,
                false,
                "leak",
                "ownership and cleanup responsibility for this reference result cannot be verified",
            );
            vec![v]
        } else {
            vec![Value::default()]
        }
    }
    fn invoke_procedure(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        procedure: Ref,
        plans: &[crate::semantics::CallPlan],
        args: &[Ref],
        evaluated: &HashMap<Ref, Value>,
        vs: &[Value],
        depth: usize,
    ) -> Option<Vec<Value>> {
        let source = &self.r.sources[procedure.file];
        if !source.shared_native && self.role(procedure, "body").is_some() {
            if self.active.len() >= MAX_DEPTH
                || self.active.iter().any(|(p, _)| *p == procedure)
            {
                let v = self.unknown_call(
                    state,
                    at,
                    &vs,
                    "recursive call requires a lifetime summary that did not converge",
                );
                return Some(vec![v]);
            }
            let signature = self.role(procedure, "type").unwrap();
            let fields = self.r.fields(signature, "params");
            let mut inputs = vec![];
            for (i, (name, _)) in fields.iter().enumerate() {
                let mut v = Value::default();
                if let Some(plan) = plans.iter().find(|p| p.procedure == procedure) {
                    for actual in plan.arguments.get(i).into_iter().flatten() {
                        let value = if let Some(v) = evaluated.get(actual) {
                            v.clone()
                        } else {
                            self.value(state, f, *actual, depth + 1)
                        };
                        v.merge(&value);
                    }
                } else {
                    let arg = args
                        .iter()
                        .position(|a| {
                            self.argument_name(*a)
                                .is_some_and(|field| self.text(field) == name)
                        })
                        .unwrap_or(i);
                    if let Some(value) = vs.get(arg) {
                        v = value.clone();
                    }
                }
                inputs.push(v);
            }
            let bindings = plans
                .iter()
                .find(|p| p.procedure == procedure)
                .map(|p| p.bindings.clone())
                .unwrap_or_default();
            let before_allocator = state.allocator.clone();
            let before_temp_allocator=state.temp_allocator.clone();
            let fresh_start = self.objects.len();
            self.active.push((procedure, at));
            let captured_inputs = inputs.clone();
            let results = self.procedure(state.clone(), procedure, inputs, bindings);
            self.active.pop();
            let mut result = vec![];
            let mut after = None;
            for exit in results {
                if exit.control == Control::Stop {
                    continue;
                }
                let mut s = exit.state;
                self.normalize_failed_allocations(&mut s,&exit.values,fresh_start);
                s.allocator = before_allocator.clone();
                s.temp_allocator=before_temp_allocator.clone();
                if let Some(after) = &mut after {
                    State::merge(after, &s);
                } else {
                    after = Some(s);
                }
                result.resize(result.len().max(exit.values.len()), Value::default());
                for (i, v) in exit.values.iter().enumerate() {
                    result[i].merge(v);
                }
            }
            if let Some(after) = after {
                *state = after;
            } else {
                state.terminated = true;
            }
            let declaration = self.r.sources[procedure.file]
                .node(procedure.node)
                .parent
                .map(|node| Ref { node, ..procedure })
                .unwrap_or(procedure);
            for (name, effect) in self.r.attributes(declaration) {
                if !matches!(
                    name.as_str(),
                    "deferred_none" | "deferred_in" | "deferred_out" | "deferred_in_out"
                ) {
                    continue;
                }
                let mut values = vec![];
                if matches!(name.as_str(), "deferred_in" | "deferred_in_out") {
                    values.extend(captured_inputs.iter().cloned());
                }
                if matches!(name.as_str(), "deferred_out" | "deferred_in_out") {
                    values.extend(result.iter().cloned());
                }
                let targets = effect
                    .map(|effect| self.r.candidates(effect))
                    .unwrap_or_default();
                if targets.len() == 1
                    && self.role(targets[0], "body").is_some()
                    && !self.r.sources[targets[0].file].shared_native
                {
                    state
                        .cleanups
                        .entry((f.id, f.scope))
                        .or_default()
                        .push(Pending {
                            action: Cleanup::Procedure {
                                procedure: targets[0],
                                inputs: values,
                                call: at,
                            },
                            conditional: false,
                            allocator:state.allocator.clone(),
                            temp_allocator:state.temp_allocator.clone(),
                        });
                } else {
                    self.unknown_call(
                        state,
                        at,
                        &values,
                        "native deferred cleanup callback cannot be inspected",
                    );
                }
            }
            for id in state.reachable(result.iter().cloned()) {
                if id >= fresh_start && matches!(self.objects[id].storage, Storage::Heap(_)) {
                    self.objects[id].owner = None;
                }
            }
            return Some(result);
        }
        None
    }
    fn optional_exit(&mut self, state: &mut State, f: &Frame, at: Ref) {
        let mut protected = BTreeSet::new();
        for ((frame, _), pending) in &state.cleanups {
            if *frame != f.id {
                continue;
            }
            for item in pending.iter().filter(|item| !item.conditional) {
                match &item.action {
                    Cleanup::Owner(place) => state.read(place).roots(&mut protected),
                    Cleanup::Region(region) => {
                        for id in state.cells.keys() {
                            if matches!(&self.objects[*id].storage,Storage::Heap(Allocator::Region(owner)) if **owner==Place::root(*region))
                            {
                                protected.insert(*id);
                            }
                        }
                    }
                    Cleanup::Statement(stmt) => {
                        let call = self.role(*stmt, "expr").unwrap_or(*stmt);
                        if self.kind(call) == "Call" {
                            let mut arena = crate::borrowflow::Arena::default();
                            let builtin = crate::borroweval::Evaluator {
                                resolver: self.r,
                                arena: &mut arena,
                            }
                            .builtin(call);
                            if builtin.as_deref() == Some("delete") {
                                if let Some(arg) = self
                                    .group(call, "args")
                                    .first()
                                    .and_then(|arg| self.symbol(*arg))
                                    .and_then(|symbol| f.env.get(&symbol))
                                {
                                    state.read(&Place::root(*arg)).roots(&mut protected);
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        let mut external = vec![];
        for id in state.globals.iter().chain(&state.exports) {
            if let Some(c) = state.cells.get(id) {
                external.push(c.value.clone());
            }
        }
        let exposed = state.reachable(external);
        for id in state.cells.keys().copied().collect::<Vec<_>>() {
            if !matches!(self.objects[id].storage, Storage::Heap(_))
                || state.cells[&id].life & LIVE == 0
            {
                continue;
            }
            if !protected.contains(&id) {
                self.report(
                    at,
                    Some(id),
                    false,
                    "leak",
                    "optional early exit may bypass later cleanup or ownership transfer",
                );
            }
            if exposed.contains(&id) {
                self.report(at,Some(id),false,"lifetime","optional early exit has externally retained references; failure-path lifetime cannot be verified");
            }
        }
    }
    fn results(&mut self, state: &mut State, f: &mut Frame, at: Ref, count: usize) -> Vec<Value> {
        match self.kind(at) {
            "Call" => {
                let v = self.call(state, f, at, 0);
                self.end_temporaries(state, f, at);
                return v;
            }
            "OrReturn" | "OrBranch" => {
                if let Some(expr) = self.role(at, "expr") {
                    let values = self.results(state, f, expr, count);
                    self.optional_exit(state, f, at);
                    return values;
                }
            }
            "Paren" | "Runtime" | "Run" | "OrPanic" => {
                if let Some(expr) = self.role(at, "expr") {
                    return self.results(state, f, expr, count);
                }
            }
            "FallbackList" | "SwitchResults" => {
                return self
                    .group(at, "args")
                    .into_iter()
                    .map(|arg| self.value(state, f, arg, 0))
                    .collect()
            }
            "OrElse" => {
                // The producer executes before either outcome. Only the
                // fallback is conditional; its branch starts after production.
                let mut v = self
                    .role(at, "x")
                    .map(|x| self.results(state, f, x, count))
                    .unwrap_or_default();
                v.truncate(count);
                v.resize(count, Value::default());
                if !state.terminated {
                    if let Some(fallback) = self.role(at, "y") {
                        let mut branch = state.clone();
                        let mut alternatives = self.results(&mut branch, f, fallback, count);
                        alternatives.resize(count, Value::default());
                        if !branch.terminated {
                            for (value, alternative) in v.iter_mut().zip(alternatives) {
                                let different = *value != alternative;
                                value.merge(&alternative);
                                value.unknown |= different;
                            }
                            state.merge(&branch);
                        }
                    }
                }
                return v;
            }
            _ => {}
        }
        vec![self.value(state, f, at, 0)]
    }
    fn bind(&mut self, state: &mut State, f: &mut Frame, name: Ref, v: Value) {
        if self.text(name) == "_" {
            return;
        }
        let symbol = self.symbol(name).unwrap_or(name);
        let declaration = self.r.sources[name.file]
            .ancestors(name.node)
            .find(|n| self.r.sources[name.file].node(*n).kind == "Declaration");
        let is_static = declaration.is_some_and(|node| {
            self.r
                .attributes(Ref { node, ..name })
                .iter()
                .any(|(name, _)| name == "static")
        });
        let storage = if is_static {
            Storage::Static
        } else {
            Storage::Stack {
                frame: f.id,
                scope: f.scope,
            }
        };
        let id = if is_static {
            let key = (symbol, f.bindings.clone());
            let id = if let Some(id) = self.static_slots.get(&key) {
                *id
            } else {
                let id = self.objects.len();
                self.objects.push(Object {
                    resource: None,
                    origin: name,
                    owner: Some(name),
                    storage: Storage::Static,
                    addressed: false,
                });
                self.static_slots.insert(key, id);
                id
            };
            state.cells.entry(id).or_insert(Cell {
                value: v,
                life: LIVE,
                uncertain: false,
                released: None,
            });
            state.globals.insert(id);
            id
        } else {
            self.object(state, f.id, name, 0, storage, v)
        };
        f.env.insert(symbol, id);
        self.bind_buffer(state,f,name,Place::root(id),declaration);
        self.bind_region(state,f,name,Place::root(id));
    }
    fn iteration(&mut self, state: &mut State, f: &mut Frame, at: Ref, expr: Ref) {
        let iterator = self.iterator_call(expr);
        let mut input = self.value(state, f, expr, 0);
        if input.refs.is_empty()
            && self
                .group(at, "names")
                .iter()
                .any(|name| self.kind(*name) == "Unary")
            && matches!(
                base(&self.typ(expr, f)),
                Type::Array { .. } | Type::Fixed { .. }
            )
        {
            input.refs.extend(self.member_places(state, f, expr));
        }
        if self
            .group(at, "names")
            .iter()
            .any(|name| self.kind(*name) == "Unary")
        {
            for p in &input.refs {
                self.objects[p.id].addressed = true;
            }
        }
        self.valid(state, &input, at);
        let mut item = if iterator {
            input.clone()
        } else {
            self.items(state, &input, at)
        };
        if !iterator && matches!(base(&self.typ(expr, f)), Type::Soa(_)) {
            for (key, column) in &input.fields {
                let mut field = column.child(&Key::Item(None));
                for p in &column.refs {
                    field.merge(&self.read(state, p, at).child(&Key::Item(None)));
                }
                item.fields.insert(key.clone(), field);
            }
        }
        let map = !iterator && matches!(base(&self.typ(expr, f)), Type::Map { .. });
        let mut map_keys = Value::default();
        if map {
            for p in &input.refs {
                map_keys.merge(&state.read(p).child(&Key::Field("<keys>".into())));
            }
        }
        for (i, mut name) in self.group(at, "names").into_iter().enumerate() {
            let address = self.kind(name) == "Unary";
            if address {
                name = self.role(name, "expr").unwrap_or(name);
            }
            let value = if map {
                if i == 0 {
                    map_keys.clone()
                } else {
                    item.clone()
                }
            } else if i == 0 {
                if address {
                    Value {
                        refs: input
                            .refs
                            .iter()
                            .map(|p| p.child(Key::Item(None)))
                            .collect(),
                        ..Value::default()
                    }
                } else {
                    item.clone()
                }
            } else {
                Value::default()
            };
            if address {
                let symbol = self.symbol(name).unwrap_or(name);
                f.range_places
                    .insert(symbol, value.refs.into_iter().collect());
            } else {
                self.bind(state, f, name, value);
            }
        }
    }
    fn iterator_call(&mut self, expr: Ref) -> bool {
        if self.kind(expr) != "Call" {
            return false;
        }
        self.r.call_signature(expr).is_some_and(|sig| {
            let fields = self.r.fields(sig, "results");
            fields.len() == 2
                && fields[1].1.is_some_and(
                    |ty| matches!(base(&self.r.type_of(ty)),Type::Scalar{name,..} if name=="bool"),
                )
        })
    }
    fn statement(&mut self, mut state: State, f: &mut Frame, at: Ref) -> Vec<Exit> {
        let normal = |state: State| {
            let control = if state.terminated {
                Control::Stop
            } else {
                Control::Normal
            };
            vec![Exit {
                state,
                control,
                values: vec![],
                at,
            }]
        };
        if !self.budget(at) {
            return normal(state);
        }
        match self.kind(at).to_owned().as_str() {
            "Block" | "Case" => {
                let old_scope = f.scope;
                // Native `when` braces select declarations in the surrounding
                // scope; they do not introduce a runtime defer/storage scope.
                let transparent=self.kind(at)=="Block"&&self.r.sources[at.file].node(at.node).parent.is_some_and(|p|self.r.sources[at.file].node(p).kind=="When");
                let initial_allocator = state.allocator.clone();
                let initial_temp_allocator=state.temp_allocator.clone();
                if !transparent {f.scope = at;}
                let mut exits = normal(state);
                let children = if self.kind(at) == "Case" {
                    self.group(at, "body")
                } else {
                    self.r.sources[at.file]
                        .node(at.node)
                        .children
                        .iter()
                        .copied()
                        .map(|node| Ref { node, ..at })
                        .collect()
                };
                for child in children {
                    if self.kind(child) == "Ident" {
                        continue;
                    }
                    let mut next = vec![];
                    for exit in exits {
                        if exit.control != Control::Normal {
                            next.push(exit);
                        } else {
                            next.extend(self.statement(exit.state, f, child));
                        }
                    }
                    exits = joined(next);
                }
                if transparent {return exits;}
                // Odin defers evaluate their expressions when the scope ends,
                // in reverse order, against the final binding values.
                for exit in &mut exits {
                    if exit.control == Control::Stop {
                        continue;
                    }
                    let cleanups = exit.state.cleanups.remove(&(f.id, at)).unwrap_or_default();
                    for cleanup in cleanups.iter().rev() {
                        let before = cleanup.conditional.then(|| exit.state.clone());
                        let current_allocator=exit.state.allocator.clone();
                        let current_temp_allocator=exit.state.temp_allocator.clone();
                        exit.state.allocator=cleanup.allocator.clone();
                        exit.state.temp_allocator=cleanup.temp_allocator.clone();
                        match &cleanup.action {
                            Cleanup::Owner(slot) => {
                                let value = exit.state.read(slot);
                                let origin=self.objects[slot.id].origin;let typ=self.typ(origin,f);
                                let embedded=Self::stored_allocator(&typ);
                                let allocator=exit.state.allocator.clone();
                                if embedded || self.check_allocator(&exit.state,&value,&allocator,at){self.release(&mut exit.state, &value, at);}
                            }
                            Cleanup::Procedure {
                                procedure,
                                inputs,
                                call,
                            } => {
                                self.active.push((*procedure, *call));
                                let effects = self.procedure(
                                    exit.state.clone(),
                                    *procedure,
                                    inputs.clone(),
                                    Substitutions::new(),
                                );
                                self.active.pop();
                                let mut after = None;
                                for effect in effects {
                                    if effect.control != Control::Stop {
                                        if let Some(s) = &mut after {
                                            State::merge(s, &effect.state);
                                        } else {
                                            after = Some(effect.state);
                                        }
                                    }
                                }
                                if let Some(after) = after {
                                    exit.state = after;
                                } else {
                                    exit.control = Control::Stop;
                                }
                            }
                            Cleanup::Region(region) => {
                                self.end_region(&mut exit.state, &Place::root(*region), at, true);
                            }
                            Cleanup::Statement(stmt) => {
                                let effects = self.statement(exit.state.clone(), f, *stmt);
                                if let Some(mut effects) = effects.into_iter().next() {
                                    std::mem::swap(&mut exit.state, &mut effects.state);
                                }
                            }
                        }
                        if let Some(before) = before {
                            exit.state.merge(&before);
                        }
                        exit.state.allocator=current_allocator;
                        exit.state.temp_allocator=current_temp_allocator;
                    }
                    self.pending_pool_scope(&exit.state,f,at);
                    self.pending_worker_scope(&exit.state,f,at);
                    let ids=exit.state.cells.keys().copied().filter(|id|matches!(self.objects[*id].storage,Storage::Stack{frame,scope} if frame==f.id&&scope==at)).collect::<Vec<_>>();
                    for id in ids {
                        if let Some(c) = exit.state.cells.get_mut(&id) {
                            c.life = DEAD;
                            c.released = Some(at);
                        }
                    }
                }
                for exit in &mut exits {
                    exit.state.allocator = initial_allocator.clone();
                    exit.state.temp_allocator=initial_temp_allocator.clone();
                }
                f.scope = old_scope;
                exits
            }
            "Declaration" => {
                let names = self.group(at, "names");
                let expressions = self.group(at, "values");
                let uninitialized = expressions.is_empty();
                let mut values = if expressions.len() == 1 {
                    self.results(&mut state, f, expressions[0], names.len())
                } else {
                    expressions
                        .into_iter()
                        .map(|e| self.value(&mut state, f, e, 0))
                        .collect()
                };
                values.resize(names.len(), Value::default());
                let owners = crate::scoped::names(&self.r.sources[at.file], at.node);
                for (name, mut v) in names.into_iter().zip(values) {
                    if uninitialized && matches!(base(&self.typ(name, f)), Type::Function(..) | Type::Procedure(_)) { v.nil_procedure = true; }
                    for id in state.reachable([v.clone()]) {
                        if let Some(object) = self.objects.get_mut(id) {
                            if object.owner.is_none() && matches!(object.storage, Storage::Heap(_))
                            {
                                object.owner = Some(name);
                            }
                        }
                    }
                    self.bind(&mut state, f, name, v);
                    if owners.contains(&name.node) {
                        if let Some(id) = self.symbol(name).and_then(|s| f.env.get(&s).copied()) {
                            state
                                .cleanups
                                .entry((f.id, f.scope))
                                .or_default()
                                .push(Pending {
                                    action: Cleanup::Owner(Place::root(id)),
                                    conditional: false,
                                    allocator:state.allocator.clone(),
                                    temp_allocator:state.temp_allocator.clone(),
                                });
                        }
                    }
                }
                normal(state)
            }
            "Assignment" => {
                let lhs = self.group(at, "lhs");
                let rhs = self.group(at, "rhs");
                let destinations = lhs
                    .iter()
                    .map(|target| self.member_places(&mut state, f, *target))
                    .collect::<Vec<_>>();
                let mut values = if rhs.len() == 1 {
                    self.results(&mut state, f, rhs[0], lhs.len())
                } else {
                    rhs.into_iter()
                        .map(|e| self.value(&mut state, f, e, 0))
                        .collect()
                };
                values.resize(lhs.len(), Value::default());
                for ((target, mut v), places) in lhs.into_iter().zip(values).zip(destinations) {
                    let typ=self.typ(target,f);
                    let bytes=self.r.simple_size(&typ);
                    self.memory_access(&state,&places,&typ,target,self.r.sources[at.file].node(at.node).op!="=");
                    self.memory_written(&mut state,&Value{refs:places.iter().cloned().collect(),..Value::default()},bytes);
                    if self.r.sources[at.file].node(at.node).op!="="{
                        let op=self.r.sources[at.file].node(at.node).op.trim_end_matches('=').to_string();
                        v.number=if places.len()==1{self.number_binary(&op,&state.read(&places[0]),&v).number}else{None};
                        self.fit_number(&mut v,&typ);
                        v.byte=None;v.truth=None;v.file_status=None;
                    }
                    if places.len()==1 && self.kind(target)=="Ident" {
                        self.replace_exclusive_header(&mut state, f, target, &places[0], &mut v);
                    }
                    if self.text(target) == "context.allocator" {
                        state.allocator = v.allocator.clone().unwrap_or(Allocator::Unknown);
                        continue;
                    }
                    if self.text(target)=="context.temp_allocator"{state.temp_allocator=v.allocator.clone().unwrap_or(Allocator::Unknown);continue;}
                    if self.text(target)=="context"{
                        state.allocator=v.child(&Key::Field("allocator".into())).allocator.unwrap_or(Allocator::Unknown);
                        state.temp_allocator=v.child(&Key::Field("temp_allocator".into())).allocator.unwrap_or(Allocator::Unknown);
                        continue;
                    }
                    if self.kind(target) == "Index" {
                        if let (Some(expr), Some(index)) =
                            (self.role(target, "expr"), self.role(target, "index"))
                        {
                            if matches!(base(&self.typ(expr, f)), Type::Map { .. }) {
                                if let Some(key) = f.keys.get(&index).cloned() {
                                    for place in &places {
                                        let mut storage = place.clone();
                                        storage.path.pop();
                                        state.write(
                                            &storage.child(Key::Field("<keys>".into())),
                                            &key,
                                            true,
                                        );
                                    }
                                }
                            }
                        }
                    }
                    if places.is_empty() && self.text(target) != "_" {
                        if let Some(origin) = state.reachable([v.clone()]).first().copied() {
                            self.report(target,Some(origin),false,"lifetime","store target has unresolved provenance; reference retention cannot be verified");
                        }
                    }
                    let weak = places.len() > 1;
                    for p in places {
                        let _ = self.read(&state, &p, target);
                        state.write(&p, &v, weak);
                    }
                }
                normal(state)
            }
            "Return" => {
                let values = self.group(at, "values");
                let mut result = vec![];
                if values.is_empty() {
                    if let Some(signature) = self.role(f.procedure, "type") {
                        if let Some(fields) = self.role(signature, "results") {
                            for field in self.r.sources[fields.file]
                                .node(fields.node)
                                .children
                                .to_vec()
                            {
                                for name in self.group(
                                    Ref {
                                        node: field,
                                        ..fields
                                    },
                                    "names",
                                ) {
                                    result.push(self.value(&mut state, f, name, 0));
                                }
                            }
                        }
                    }
                } else if values.len() == 1 {
                    let count = self
                        .role(f.procedure, "type")
                        .map(|sig| self.r.fields(sig, "results").len())
                        .unwrap_or(1);
                    result = self.results(&mut state, f, values[0], count);
                } else {
                    for v in values {
                        result.push(self.value(&mut state, f, v, 0));
                    }
                }
                vec![Exit {
                    state,
                    control: Control::Return,
                    values: result,
                    at: self.group(at, "values").first().copied().unwrap_or(at),
                }]
            }
            "Defer" => {
                if let Some(stmt) = self.role(at, "stmt") {
                    state
                        .cleanups
                        .entry((f.id, f.scope))
                        .or_default()
                        .push(Pending {
                            action: Cleanup::Statement(stmt),
                            conditional: false,
                            allocator:state.allocator.clone(),
                            temp_allocator:state.temp_allocator.clone(),
                        });
                }
                normal(state)
            }
            "If" | "When" => {
                if let Some(init) = self.role(at, "init") {
                    if let Some(e) = self.statement(state.clone(), f, init).into_iter().next() {
                        state = e.state;
                    }
                }
                let cond = self.role(at, "cond");
                if let Some(cond) = cond {
                    let _ = self.value(&mut state, f, cond, 0);
                }
                let known = cond.and_then(|c| {
                    self.r.condition(c).or_else(|| {
                        match self.r.constant_value(c, &f.bindings, None) {
                            Some(Constant::Boolean(b)) => Some(b),
                            _ => None,
                        }
                    })
                });
                let before = f.clone();
                let mut results = vec![];
                for (yes, role) in [(true, "body"), (false, "else")] {
                    if known.is_some_and(|b| b != yes) {
                        continue;
                    }
                    let mut frame = before.clone();
                    let mut branch=state.clone();
                    if cond.is_some_and(|cond|!self.refine_condition(&mut branch,&frame,cond,yes,0)) {continue;}
                    if let Some(body) = self.role(at, role) {
                        results.extend(self.statement(branch, &mut frame, body));
                    } else {
                        results.extend(normal(branch));
                    }
                    // Block-local names do not escape, but control-flow init
                    // bindings do. IDs are stable at each lexical name.
                    f.env.extend(frame.env);
                }
                joined(results)
            }
            "Range" | "For" => {
                if let Some(init) = self.role(at, "init") {
                    if let Some(e) = self.statement(state.clone(), f, init).into_iter().next() {
                        state = e.state;
                    }
                }
                let iterator = self
                    .role(at, "expr")
                    .filter(|expr| self.iterator_call(*expr));
                if let Some(expr) = self.role(at, "expr").filter(|_| iterator.is_none()) {
                    self.iteration(&mut state, f, at, expr);
                }
                self.discard_unaddressed_locals(&mut state, None);
                let entry = state.clone();
                let mut back = state;
                let mut outward = vec![];
                let mut stable = false;
                for _ in 0..MAX_LOOP {
                    let old = back.clone();
                    if let Some(expr) = iterator {
                        self.iteration(&mut back, f, at, expr);
                    }
                    if let Some(cond) = self.role(at, "cond") {
                        let _ = self.value(&mut back, f, cond, 0);
                        if self.r.constant_value(cond, &f.bindings, None)
                            == Some(Constant::Boolean(false))
                        {
                            stable = true;
                            break;
                        }
                    }
                    let exits = if let Some(body) = self.role(at, "body") {
                        self.statement(back.clone(), f, body)
                    } else {
                        normal(back.clone())
                    };
                    let mut continuing = None;
                    for mut e in exits {
                        match e.control {
                            Control::Break => {
                                e.control = Control::Normal;
                                outward.push(e);
                            }
                            Control::Return | Control::Stop => outward.push(e),
                            _ => {
                                if let Some(post) = self.role(at, "post") {
                                    if let Some(e2) =
                                        self.statement(e.state.clone(), f, post).into_iter().next()
                                    {
                                        e.state = e2.state;
                                    }
                                }
                                if let Some(s) = &mut continuing {
                                    State::merge(s, &e.state);
                                } else {
                                    continuing = Some(e.state);
                                }
                            }
                        }
                    }
                    if let Some(mut continuing) = continuing {
                        self.normalize_header_pair(&entry,&mut continuing,f,at);
                        continuing.merge(&entry);
                        self.discard_unaddressed_locals(&mut continuing, None);
                        self.discard_loop_locals(&mut continuing,f);
                        back = continuing;
                    } else {
                        stable = true;
                        break;
                    }
                    if back == old {
                        stable = true;
                        break;
                    }
                }
                if !stable {
                    let mut v = Value::default();
                    for c in back.cells.values() {
                        v.merge(&c.value);
                    }
                    self.unknown_call(
                        &mut back,
                        at,
                        &[v],
                        "loop reference graph did not converge within the analysis budget",
                    );
                }
                back.merge(&entry);
                outward.extend(normal(back));
                joined(outward)
            }
            "Switch" | "CompileSwitch" | "TypeSwitch" => {
                if let Some(init) = self.role(at, "init") {
                    if let Some(e) = self.statement(state.clone(), f, init).into_iter().next() {
                        state = e.state;
                    }
                }
                let switched = self
                    .role(at, "expr")
                    .map(|expr| self.value(&mut state, f, expr, 0));
                let cases = self.r.sources[at.file]
                    .descendants(at.node)
                    .filter(|n| {
                        self.r.sources[at.file].node(*n).kind == "Case"
                            && self.r.sources[at.file].ancestors(*n).find(|p| {
                                matches!(
                                    self.r.sources[at.file].node(*p).kind.as_str(),
                                    "Switch" | "CompileSwitch" | "TypeSwitch"
                                )
                            }) == Some(at.node)
                    })
                    .collect::<Vec<_>>();
                let variant = if self.kind(at) == "TypeSwitch" {
                    switched
                } else {
                    None
                };
                let mut results = vec![];
                let mut has_default = false;
                for node in cases {
                    let case = Ref { node, ..at };
                    has_default |= self.group(case, "types").is_empty();
                    let mut branch = f.clone();
                    let mut branch_state = state.clone();
                    if let Some(value) = &variant {
                        for name in self.group(at, "names") {
                            self.bind(&mut branch_state, &mut branch, name, value.clone());
                        }
                    }
                    results.extend(self.statement(branch_state, &mut branch, case));
                    f.env.extend(branch.env);
                }
                if !has_default {
                    results.extend(normal(state));
                }
                for e in &mut results {
                    if e.control == Control::Break {
                        e.control = Control::Normal;
                    }
                }
                joined(results)
            }
            "Procedure" | "ProcedureType" | "Attribute" | "Field" | "Using" => normal(state),
            "Other"
                if self.text(at).trim().starts_with("goto")
                    || self.text(at).trim().starts_with("fallthrough") =>
            {
                self.report(
                    at,
                    None,
                    false,
                    "lifetime",
                    "unstructured control flow requires manual lifetime review",
                );
                self.report(
                    at,
                    None,
                    false,
                    "leak",
                    "unstructured control flow requires manual cleanup review",
                );
                for c in state.cells.values_mut() {
                    c.uncertain = true;
                }
                normal(state)
            }
            "Other" if self.text(at).trim().starts_with("break") => vec![Exit {
                state,
                control: Control::Break,
                values: vec![],
                at,
            }],
            "Other" if self.text(at).trim().starts_with("continue") => vec![Exit {
                state,
                control: Control::Continue,
                values: vec![],
                at,
            }],
            "Other" => {
                let mut exits = normal(state);
                for node in self.r.sources[at.file].node(at.node).children.to_vec() {
                    let mut next = vec![];
                    for e in exits {
                        if e.control == Control::Normal {
                            next.extend(self.statement(e.state, f, Ref { node, ..at }));
                        } else {
                            next.push(e);
                        }
                    }
                    exits = joined(next);
                }
                exits
            }
            "ExpressionStatement" => {
                let expr = self.role(at, "expr").or_else(|| {
                    self.r.sources[at.file]
                        .node(at.node)
                        .children
                        .first()
                        .map(|node| Ref { node: *node, ..at })
                });
                if let Some(expr) = expr {
                    let _ = self.value(&mut state, f, expr, 0);
                    if self.kind(expr) == "Call"
                        && self.r.call_signature(expr).is_some_and(|sig| {
                            self.r.sources[sig.file].node(sig.node).data["diverging"] == true
                        })
                    {
                        return vec![Exit {
                            state,
                            control: Control::Stop,
                            values: vec![],
                            at,
                        }];
                    }
                }
                normal(state)
            }
            _ => {
                let _ = self.value(&mut state, f, at, 0);
                normal(state)
            }
        }
    }
    fn promote(
        &mut self,
        state: &mut State,
        value: &Value,
        allocator: &Allocator,
        at: Ref,
        seen: &mut HashMap<Id, Id>,
    ) -> Value {
        self.promote_seen(state, value, allocator, at, seen, 0)
    }
    fn promote_seen(
        &mut self,
        state: &mut State,
        value: &Value,
        allocator: &Allocator,
        at: Ref,
        seen: &mut HashMap<Id, Id>,
        depth: usize,
    ) -> Value {
        // No graph edges means no promotion work, even for deeply nested
        // inline records. Keep any existing uncertainty; only skip the walk.
        if !value.has_refs() {
            crate::profile::count("lifetime.inline_promotions", 1);
            return value.clone();
        }
        if depth > MAX_VALUE_DEPTH || !self.budget(at) {
            self.report(
                at,
                None,
                false,
                "lifetime",
                "pure result graph exceeds the promotion analysis budget",
            );
            self.report(
                at,
                None,
                false,
                "leak",
                "pure result ownership exceeds the promotion analysis budget",
            );
            let mut value = value.clone();
            value.invalidate();
            return value;
        }
        let mut v = value.clone();
        v.refs.clear();
        v.fields.clear();
        self.direct(state, value, at);
        for (key, item) in &value.fields {
            if matches!(key, Key::Field(name) if name == "_") { continue; }
            v.fields.insert(
                key.clone(),
                self.promote_seen(state, item, allocator, at, seen, depth + 1),
            );
        }
        for p in &value.refs {
            let id = if let Some(id) = seen.get(&p.id) {
                *id
            } else {
                let id = self.objects.len();
                seen.insert(p.id, id);
                self.objects.push(Object {
                    resource: None,
                    origin: at,
                    owner: None,
                    storage: Storage::Heap(allocator.clone()),
                    addressed: false,
                });
                state.cells.insert(
                    id,
                    Cell {
                        value: Value::default(),
                        life: LIVE | state.cells.get(&p.id).map(|cell| cell.life & ABSENT).unwrap_or(0),
                        uncertain: false,
                        released: None,
                    },
                );
                let contents = state.read(p);
                let cloned = self.promote_seen(state, &contents, allocator, at, seen, depth + 1);
                if let Some(c) = state.cells.get_mut(&id) {
                    c.value = cloned;
                }
                seen.remove(&p.id);
                id
            };
            v.refs.insert(Place::root(id));
        }
        v
    }
    fn procedure(
        &mut self,
        mut state: State,
        proc: Ref,
        inputs: Vec<Value>,
        bindings: Substitutions,
    ) -> Vec<Exit> {
        if let Some(summary) = self.neutral_summary(proc, &bindings) {
            let mut global_ok = true;
            for global in &summary.globals {
                let id = if let Some(id) = self.ids.get(&(0, *global, 0)) {
                    *id
                } else {
                    let id =
                        self.object(&mut state, 0, *global, 0, Storage::Static, Value::default());
                    state.globals.insert(id);
                    id
                };
                if state
                    .cells
                    .get(&id)
                    .is_none_or(|c| c.value.has_refs() || c.value.unknown)
                {
                    global_ok = false;
                    break;
                }
            }
            let mut graph_reads=vec![None;inputs.len()];
            let mut projection_reads=vec![None;inputs.len()];
            if global_ok
                && inputs.iter().enumerate().all(|(i, v)| {
                    if (summary.reads.get(i)==Some(&true)||summary.writes.get(i)==Some(&true))&&self.raw_summary_input(&state,v){return false;}
                    if summary.projected.get(i)==Some(&true) {
                        projection_reads[i]=self.projected_reads(&state,v,&summary.projections[i]);
                        return projection_reads[i].is_some();
                    }
                    if summary.borrowed.get(i) != Some(&true) {
                        return !v.has_refs();
                    }
                    if let Some(graph)=summary.graphs.get(i).and_then(Option::as_ref) {
                        graph_reads[i]=self.graph_reads(&state,v,graph);
                        return graph_reads[i].is_some();
                    }
                    // Integer fields can still carry tracked address casts.
                    // A reader's inline element type does not erase those
                    // edges. Header-only summaries do not read the elements.
                    summary.reads.get(i) != Some(&true)
                        || (!v.fields.values().any(Value::has_refs)
                            && v.refs.iter().all(|p| !state.read(p).has_refs()))
                })
            {
                crate::profile::count("lifetime.summary_applied", 1);
                if summary.writes.iter().any(|v| *v) {
                    crate::profile::count("lifetime.writer_summary_applied", 1);
                }
                let at = self.active.last().map(|(_, call)| *call).unwrap_or(proc);
                for (i, value) in inputs.iter().enumerate() {
                    if let Some(reads)=projection_reads[i].take(){
                        for read in reads{self.direct(&state,&read,at);}
                        crate::profile::count("lifetime.projection_summary_applied",1);
                    }
                    if summary.bytes.get(i).copied().unwrap_or(0)>0{self.check_bytes(&state,value,summary.bytes[i],at,true);}
                    if summary.nuls.get(i)==Some(&true){self.check_termination(&state,value,at);}
                    if summary.writes.get(i)==Some(&true){Self::forget_buffer_contents(&mut state,value);
                        for p in &value.refs{if let Some(c)=state.cells.get_mut(&p.id){c.value.forget_scalar_facts();}}
                    }
                    if summary.reads.get(i) == Some(&true) {
                        if let Some(reads)=graph_reads[i].take() {
                            for read in reads {self.direct(&state,&read,at);
                                if summary.writes.get(i)==Some(&true){Self::forget_buffer_contents(&mut state,&read);
                                    for p in &read.refs{if let Some(c)=state.cells.get_mut(&p.id){c.value.forget_scalar_facts();}}
                                }
                            }
                            crate::profile::count("lifetime.graph_summary_applied",1);
                            continue;
                        }
                        self.direct(&state, value, at);
                        if value.unknown && value.refs.is_empty() {
                            self.report(
                                at,
                                None,
                                false,
                                "lifetime",
                                "reader input has unverified reference provenance",
                            );
                        }
                    }
                }
                for global in &summary.global_writes{if let Some(id)=self.ids.get(&(0,*global,0)){if let Some(c)=state.cells.get_mut(id){c.value.forget_scalar_facts();}}}
                return vec![Exit {
                    state,
                    control: Control::Return,
                    values: vec![Value::default(); summary.results],
                    at,
                }];
            }
        }
        // Purely inline helpers cannot leave borrowed inputs in their dead
        // slots. Avoid a graph scan for every scalar/fixed-value math call.
        let reference_inputs = inputs.iter().any(Value::has_refs);
        self.frames += 1;
        let id = self.frames;
        let pure = self.r.sources[proc.file].node(proc.node).data["pure"] == true;
        let outer_allocator = state.allocator.clone();
        let scratch = if pure {
            let scratch = self.object(
                &mut state,
                id,
                proc,
                9,
                Storage::Stack {
                    frame: id,
                    scope: proc,
                },
                Value::default(),
            );
            state.allocator = Allocator::Region(Arc::new(Place::root(scratch)));
            Some(scratch)
        } else {
            None
        };
        let mut f = Frame {
            id,
            procedure: proc,
            env: HashMap::new(),
            range_places: HashMap::new(),
            scope: proc,
            bindings,
            keys: HashMap::new(),
        };
        if let Some(signature) = self.role(proc, "type") {
            let mut index = 0;
            for role in ["params", "results"] {
                if let Some(fields) = self.role(signature, role) {
                    for field in self.r.sources[fields.file]
                        .node(fields.node)
                        .children
                        .to_vec()
                    {
                        for name in self.group(
                            Ref {
                                node: field,
                                ..fields
                            },
                            "names",
                        ) {
                            let v = if role == "params" {
                                let v = inputs.get(index).cloned().unwrap_or_default();
                                index += 1;
                                v
                            } else {
                                Value::default()
                            };
                            self.bind(&mut state, &mut f, name, v);
                        }
                    }
                }
            }
        }
        let mut exits = if let Some(body) = self.role(proc, "body") {
            self.statement(state, &mut f, body)
        } else {
            vec![]
        };
        let owning = self.owning_results(proc, &f.bindings);
        for e in &mut exits {
            if let Some(scratch) = scratch {
                if e.control != Control::Stop {
                    let mut promoted = vec![];
                    let mut seen = HashMap::new();
                    for value in &e.values {
                        promoted.push(self.promote(
                            &mut e.state,
                            value,
                            &outer_allocator,
                            e.at,
                            &mut seen,
                        ));
                    }
                    e.values = promoted;
                    for (id, cell) in &mut e.state.cells {
                        if matches!(&self.objects[*id].storage,Storage::Heap(Allocator::Region(region)) if **region==Place::root(scratch))
                        {
                            cell.life = DEAD;
                            cell.released = Some(proc);
                        }
                    }
                }
                e.state.allocator = outer_allocator.clone();
            }
            for (i, value) in e.values.iter_mut().enumerate() {
                if owning.get(i) == Some(&true)
                    && value.refs.iter().all(|p| {
                        p.path.is_empty()
                            && !p.interior
                            && p.start == Some(0)
                            && matches!(self.objects[p.id].storage, Storage::Heap(_))
                    })
                {
                    // A monotonic header is exported whenever its allocation
                    // exists. An empty/no-allocation branch is not lost ownership.
                    value.unknown = false;
                }
            }
            if e.control == Control::Normal {
                e.control = Control::Return;
            }
            for (id, c) in &mut e.state.cells {
                if matches!(self.objects[*id].storage,Storage::Stack{frame,..} if frame==f.id) {
                    c.life = DEAD;
                    c.released = Some(proc);
                }
            }
            // The owning-header proof permits its local address only at
            // builtin append. Those header addresses cannot survive the call;
            // do not keep a new dead header tombstone on every loop visit.
            for (index, proved) in owning.iter().enumerate() {
                if !proved {continue;}
                let return_at=if self.kind(e.at)=="Return" {Some(e.at)} else {
                    self.r.sources[e.at.file].ancestors(e.at.node).find(|n|self.r.sources[e.at.file].node(*n).kind=="Return")
                        .map(|node|Ref{node,..e.at})
                };
                let values=return_at.map(|at|self.group(at,"values")).unwrap_or_default();
                let name=values.get(index).copied().or_else(|| {
                    let sig=self.role(proc,"type")?;
                    let results=self.role(sig,"results")?;
                    self.r.sources[results.file].node(results.node).children.iter()
                        .flat_map(|n|self.group(Ref{node:*n,..results},"names")).nth(index)
                });
                if let Some(id)=name.and_then(|name|self.symbol(name)).and_then(|s|f.env.get(&s).copied()) {
                    if matches!(self.objects[id].storage,Storage::Stack{frame,..} if frame==f.id) {
                        self.objects[id].addressed=false;
                    }
                }
            }
            for v in &e.values {
                self.valid(&e.state, v, e.at);
            }
            if reference_inputs || e.values.iter().any(Value::has_refs) {
                self.discard_unaddressed_locals(&mut e.state, Some(f.id));
            }
        }
        exits
    }
    fn discard_unaddressed_locals(&self, state: &mut State, frame: Option<usize>) {
        // Unexposed expired slots cannot be observed through any alias. Heap
        // obligations and every address-taken tombstone stay, including values
        // temporarily held while evaluating another call argument.
        state.cells.retain(|id,c| c.life & LIVE != 0 || self.objects[*id].addressed
            || !matches!(self.objects[*id].storage,Storage::Stack{frame:owner,..} if frame.is_none_or(|frame|frame==owner)));
    }
    fn parameter(&mut self, state: &mut State, at: Ref, t: &Type, depth: usize) -> Value {
        if depth > 3 || !self.budget(at) {
            if self.inline_type(t, 0) {
                return Value::default();
            }
            if matches!(
                base(t),
                Type::Scalar { .. } | Type::Enum(_) | Type::Constant(_) | Type::Procedure(_) | Type::Function(_, _)
            ) {
                return Value::default();
            }
            return Value {
                unknown: true,
                caller_tail: true,
                ..Value::default()
            };
        }
        match base(t) {
            Type::Pointer(elem) | Type::Slice(elem) | Type::Dynamic(elem) => {
                let mut content = self.parameter(state, at, elem, depth + 1);
                if !matches!(base(t), Type::Pointer(_)) {
                    content = Value {
                        fields: BTreeMap::from([(Key::Item(None), content)]),
                        ..Value::default()
                    };
                }
                let id = self.objects.len();
                self.objects.push(Object {
                    resource: None,
                    origin: at,
                    owner: None,
                    storage: Storage::Caller,
                    addressed: false,
                });
                state.cells.insert(
                    id,
                    Cell {
                        value: content,
                        life: LIVE,
                        uncertain: false,
                        released: None,
                    },
                );
                state.exports.insert(id);
                Value::reference(Place::root(id))
            }
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                let mut v = Value::default();
                for (name, t) in self.r.record_fields(t) {
                    v.fields
                        .insert(Key::Field(name), self.parameter(state, at, &t, depth));
                }
                v
            }
            Type::Array { elem, .. } | Type::Fixed { elem, .. } => Value {
                fields: BTreeMap::from([(
                    Key::Item(None),
                    self.parameter(state, at, elem, depth),
                )]),
                ..Value::default()
            },
            Type::String { .. } | Type::Map { .. } | Type::Set(_) | Type::Soa(_) => {
                let id = self.objects.len();
                self.objects.push(Object {
                    resource: None,
                    origin: at,
                    owner: None,
                    storage: Storage::Caller,
                    addressed: false,
                });
                state.cells.insert(
                    id,
                    Cell {
                        value: Value::default(),
                        life: LIVE,
                        uncertain: false,
                        released: None,
                    },
                );
                state.exports.insert(id);
                Value::reference(Place::root(id))
            }
            Type::Unknown | Type::Generic => Value {
                unknown: true,
                ..Value::default()
            },
            _ => Value::default(),
        }
    }
    fn finish(&mut self, _proc: Ref, exit: Exit) {
        if exit.control == Control::Stop {
            return;
        }
        if self.exhausted {
            // A budget diagnostic already describes the proof boundary. Do
            // not invent downstream leaks from cleanups we did not execute.
            return;
        }
        let mut exports = exit.values.clone();
        for id in exit.state.globals.iter().chain(&exit.state.exports) {
            // Contents of a destroyed caller-owned container are no longer
            // exported. A surviving alias/return/global still checks the dead
            // container itself through its own live root.
            if let Some(c) = exit.state.cells.get(id).filter(|c|c.life&LIVE!=0) {
                exports.push(c.value.clone());
            }
        }
        for v in &exports {
            self.valid(&exit.state, v, exit.at);
        }
        let certain = exit.state.certainly_reachable(exports.clone());
        let reachable = exit.state.reachable(exports);
        let allocated = exit
            .state
            .cells
            .iter()
            .filter_map(|(id, c)| {
                matches!(self.objects[*id].storage, Storage::Heap(_)).then_some((*id, c.clone()))
            })
            .collect::<Vec<_>>();
        for (id, c) in allocated {
            if c.life & LIVE == 0 || certain.contains(&id) {
                continue;
            }
            if reachable.contains(&id) {
                self.report(self.objects[id].origin,Some(id),false,"leak","allocation may escape on some paths; cleanup or ownership transfer is not verified on every path");
                continue;
            }
            let Storage::Heap(allocator) = &self.objects[id].storage else {
                continue;
            };
            // The native per-thread temporary allocator is reclaimed by the
            // runtime. Explicit resets still end the individual generations.
            if matches!(allocator,Allocator::Temporary(_)){continue;}
            let definite = matches!(allocator, Allocator::Heap)
                && c.life == LIVE
                && !c.uncertain
                && !self.exhausted;
            self.report(self.objects[id].origin,Some(id),definite,"leak",if definite{
                "heap allocation has no cleanup and no ownership path leaving this procedure"
            }else if c.uncertain{
                "allocation is not visibly released; an unverified call may retain or release it"
            }else if matches!(allocator,Allocator::Unknown){
                "allocation has no visible cleanup or ownership transfer; allocator lifetime is unknown (it may be arena-managed)"
            }else{
                "allocation cleanup is conditional or region-managed; release on every path cannot be verified"
            });
        }
    }
}
fn pos(r: &Resolver<'_>, at: Ref) -> Json {
    let s = &r.sources[at.file];
    let span = s.node(at.node).span;
    let (line, column) = s.position(span.start);
    let (end_line, end_column) = s.position(span.end);
    json!({"file":s.path,"offset":span.start,"end_offset":span.end,"line":line,"column":column,"end_line":end_line,"end_column":end_column})
}
pub(crate) fn analyze(project: &Project, checks: &[String]) -> Vec<Json> {
    let _phase = crate::profile::span("vet.lifetime");
    let root = if project.options.entry.is_file() {
        project.options.entry.parent().unwrap()
    } else {
        &project.options.entry
    };
    let mut resolver = project.resolver();
    resolver.set_evaluations(project.evaluated.clone());
    let roots = project
        .sources
        .iter()
        .enumerate()
        .filter(|(_, s)| s.thor && !s.excluded && s.path.starts_with(root))
        .flat_map(|(file, s)| {
            s.nodes.iter().enumerate().filter_map(move |(i, n)| {
                (n.kind == "Procedure").then_some(Ref {
                    file,
                    node: NodeId(i),
                })
            })
        })
        .collect::<Vec<_>>();
    let mut static_values = HashSet::new();
    let mut temporaries = HashMap::new();
    for file in project
        .sources
        .iter()
        .enumerate()
        .filter_map(|(i, s)| (s.thor && !s.excluded && !s.shared_native).then_some(i))
    {
        let (values, plans) = crate::lower::Lower::lifetime_metadata(file, &mut resolver);
        static_values.extend(values.into_iter().map(|node| Ref { file, node }));
        for (i, n) in project.sources[file]
            .nodes
            .iter()
            .enumerate()
            .filter(|(_, n)| n.kind == "Declaration")
        {
            let declaration = Ref {
                file,
                node: NodeId(i),
            };
            if resolver
                .attributes(declaration)
                .iter()
                .any(|(name, _)| name == "static")
            {
                static_values.extend(
                    project.sources[file]
                        .group(declaration.node, "values")
                        .iter()
                        .map(|node| Ref { file, node: *node }),
                );
            }
            let _ = n;
        }
        temporaries.extend(plans.into_iter().map(|(node, targets)| {
            (
                Ref { file, node },
                targets
                    .into_iter()
                    .map(|node| Ref { file, node })
                    .collect::<Vec<_>>(),
            )
        }));
    }
    let custom_formatting = project
        .sources
        .iter()
        .enumerate()
        .filter(|(_, s)| !s.excluded && !s.shared_native)
        .any(|(file, s)| {
            s.nodes.iter().enumerate().any(|(i, n)| {
                if n.kind != "Call" {
                    return false;
                }
                s.role(NodeId(i), "callee").is_some_and(|node| {
                    crate::evaluation::library_member(&resolver, Ref { file, node }, "core:fmt")
                        .is_some_and(|name| {
                            matches!(
                                name.as_str(),
                                "register_user_formatter" | "set_user_formatters"
                            )
                        })
                })
            })
        });
    let lifetime = checks.iter().any(|s| s == "lifetime");
    let custom_io=project.sources.iter().enumerate().filter(|(_,s)|!s.excluded && !s.shared_native).any(|(file,s)| {
        s.nodes.iter().enumerate().any(|(i,n)| {
            let at=Ref{file,node:NodeId(i)};
            if !resolver.active_definition(at) {return false;}
            let targets=if n.kind=="Assignment" {s.group(at.node,"lhs").to_vec()}
                else if n.kind=="Unary" && n.op=="&" {s.role(at.node,"expr").into_iter().collect()}
                else {return false;};
            targets.into_iter().any(|target| {
                std::iter::once(target).chain(s.descendants(target)).any(|node| {
                    let item=Ref{node,..at};
                    if s.node(node).kind=="Selector" {
                        if s.role(node,"field").is_some_and(|field|matches!(s.text(field),"stream"|"impl"))
                            && s.role(node,"expr").is_some_and(|expr|{let typ=resolver.type_of(Ref{node:expr,..at});resources::file_type(&resolver,&typ)}) {return true;}
                    }
                    if s.node(node).kind=="Ident" && matches!(s.text(node),"stdin"|"stdout"|"stderr")
                        || s.node(node).kind=="Selector" && s.role(node,"field").is_some_and(|field|matches!(s.text(field),"stdin"|"stdout"|"stderr")) {
                        if let Some(index)=resolver.symbol(item) {
                            let symbol=&resolver.symbols[index];let owner=&resolver.sources[symbol.reference.file];
                            if matches!(symbol.name.as_str(),"stdin"|"stdout"|"stderr")
                                && owner.info["odin_root"].as_str().is_some_and(|root|owner.path==std::path::Path::new(root).join("core/os/file.odin")) {return true;}
                        }
                    }
                    s.node(node).kind=="Deref" && {let typ=resolver.type_of(item);resources::file_type(&resolver,&typ)}
                })
            })
        })
    });
    // Treat ambient RNG identities as unknown if user code can replace or
    // expose the actual context. Ordinary allocator changes are independent.
    let custom_random = project
        .sources
        .iter()
        .enumerate()
        .filter(|(_, s)| !s.shared_native && !s.excluded)
        .any(|(file, s)| {
            s.nodes.iter().enumerate().any(|(i, n)| {
                if !matches!(n.kind.as_str(), "Ident" | "Implicit")
                    || s.text(NodeId(i)) != "context"
                    || resolver
                        .symbol(Ref {
                            file,
                            node: NodeId(i),
                        })
                        .is_some()
                    || !resolver.active_definition(Ref {
                        file,
                        node: NodeId(i),
                    })
                {
                    return false;
                }
                let mut at = NodeId(i);
                if let Some(parent) = s.node(at).parent.filter(|p| s.node(*p).kind == "Selector") {
                    if s.role(parent, "field")
                        .is_some_and(|field| s.text(field) != "random_generator")
                    {
                        return false;
                    }
                    at = parent;
                }
                s.ancestors(at).any(|p| {
                    if s.node(p).kind == "Assignment"
                        && s.group(p, "lhs") == [at]
                        && s.node(at).kind == "Selector"
                        && s.role(at, "field")
                            .is_some_and(|field| s.text(field) == "random_generator")
                    {
                        let rhs = s.group(p, "rhs");
                        if rhs.len() == 1
                            && s.node(rhs[0]).kind == "Compound"
                            && s.group(rhs[0], "elems").is_empty()
                        {
                            return false;
                        }
                    }
                    s.node(p).kind == "Using"
                        || s.node(p).kind == "Unary" && s.node(p).op == "&"
                        || s.node(p).kind == "Assignment"
                            && s.group(p, "lhs")
                                .iter()
                                .any(|lhs| s.node(*lhs).span.contains(s.node(at).span))
                })
            })
        });
    let leak = checks.iter().any(|s| s == "leak");
    let custom_foreign_callbacks = project
        .sources
        .iter()
        .enumerate()
        .filter(|(_, s)| !s.shared_native && !s.excluded)
        .any(|(file, s)| {
            s.nodes.iter().enumerate().any(|(i, n)| {
                n.kind == "Call"
                    && resolver.active_definition(Ref {
                        file,
                        node: NodeId(i),
                    })
                    && !s
                        .group(NodeId(i), "args")
                        .first()
                        .is_some_and(|arg| s.text(s.role(*arg, "value").unwrap_or(*arg)) == "nil")
                    && s.role(NodeId(i), "callee").is_some_and(|node| {
                        crate::evaluation::library_member(
                            &resolver,
                            Ref { file, node },
                            "vendor:raylib",
                        )
                        .is_some_and(|name| name.starts_with("Set") && name.ends_with("Callback"))
                    })
            })
        });
    let raylib_six = project.sources.iter().any(|s| {
        s.info["odin_root"].as_str().is_some_and(|root| {
            s.path.parent() == Some(std::path::Path::new(root).join("vendor/raylib").as_path())
        }) && s.nodes.iter().enumerate().any(|(i, n)| {
            n.kind == "Declaration"
                && s.group(NodeId(i), "names")
                    .iter()
                    .any(|name| s.text(*name) == "VERSION_MAJOR")
                && s.group(NodeId(i), "values")
                    .first()
                    .is_some_and(|value| s.text(*value) == "6")
        })
    });
    let mut findings = vec![];
    let mut seen = BTreeSet::new();
    let mut models = HashMap::new();
    let mut inline_types = HashMap::new();
    let mut scalar_facts = HashMap::new();
    let mut summaries = summaries::Cache::default();
    let mut remaining = MAX_PROJECT_STEPS;
    for proc in roots {
        if remaining == 0 {
            for (enabled, rule) in [(lifetime, "lifetime"), (leak, "leak")] {
                if enabled {
                    findings.push(json!({"type":"warning","stage":"vet","code":format!("thor/{rule}-unknown"),"certainty":"unverified","pos":pos(&resolver,proc),"msgs":[format!("Unverified {rule}: project analysis budget reached; remaining procedure bodies require manual review.")]}));
                }
            }
            break;
        }
        if !resolver.active_definition(proc) {
            continue;
        }
        // Like Odin, check polymorphic bodies at concrete call sites. An
        // unbound T is not an external opaque pointer contract to diagnose.
        let signature = resolver.sources[proc.file]
            .role(proc.node, "type")
            .map(|node| Ref { node, ..proc });
        if signature.is_some_and(|signature| {
            ["params", "results"].into_iter().any(|role| {
                resolver
                    .fields(signature, role)
                    .into_iter()
                    .any(|(_, t)| t.is_some_and(|t| generic(&resolver.type_of(t))))
            })
        }) {
            continue;
        }
        let mut a = Analysis {
            r: &mut resolver,
            models: &mut models,
            refined_types: HashMap::new(),
            scalar_facts: &mut scalar_facts,
            inline_types: &mut inline_types,
            summaries: &mut summaries,
            custom_formatting,
            custom_io,
            custom_random,
            static_slots: HashMap::new(),
            custom_foreign_callbacks,
            foreign_title_slot: None,
            raylib_six,
            async_callback:false,
            demand_count:0,
            static_values: &static_values,
            temporaries: &temporaries,
            objects: vec![],
            ids: HashMap::new(),
            findings: vec![],
            active: vec![],
            steps: 0,
            frames: 0,
            exhausted: false,
            expression_depth: 0,
            lifetime,
            leak,
        };
        let mut state = State::default();
        let mut inputs = vec![];
        if let Some(sig) = a.role(proc, "type") {
            for (name, t) in a.r.fields(sig, "params") {
                let at = a.r.sources[sig.file]
                    .descendants(sig.node)
                    .find(|node| {
                        a.r.sources[sig.file].node(*node).kind == "Ident"
                            && a.r.sources[sig.file].text(*node) == name
                    })
                    .map(|node| Ref { node, ..sig })
                    .unwrap_or(sig);
                let t = t.map(|t| a.r.type_of(t)).unwrap_or(Type::Unknown);
                inputs.push(a.parameter(&mut state, at, &t, 0));
            }
        }
        let exits = a.procedure(state, proc, inputs, Substitutions::new());
        for e in joined(exits) {
            a.finish(proc, e);
        }
        remaining = remaining.saturating_sub(a.steps);
        crate::profile::count("vet.lifetime_steps", a.steps as u64);
        crate::profile::count("vet.lifetime_objects", a.objects.len() as u64);
        for finding in a.findings {
            let origin = finding.origin.and_then(|id| a.objects.get(id));
            let rule = if finding.definite {
                finding.rule.to_owned()
            } else {
                format!("{}_unknown", finding.rule)
            };
            // Suppression follows the allocation/reference's declaration, not
            // the location of an incidental alias use or a callee declaration.
            if !finding.definite {
                let owner = origin
                    .and_then(|o| o.owner)
                    .or_else(|| origin.map(|o| o.origin))
                    .unwrap_or(finding.at);
                if crate::vet_attributes::ignored(
                    a.r,
                    owner.file,
                    a.r.sources[owner.file].node(owner.node).span,
                    &rule,
                ) {
                    continue;
                }
                if origin.is_none() {
                    let procedure = a.r.sources[finding.at.file]
                        .ancestors(finding.at.node)
                        .find(|node| a.r.sources[finding.at.file].node(*node).kind == "Procedure");
                    if let Some(procedure) = procedure {
                        if let Some(declaration) =
                            a.r.sources[finding.at.file].node(procedure).parent
                        {
                            if crate::vet_attributes::rules(a.r, finding.at.file, declaration)
                                .is_ok_and(|rules| rules.contains(&rule))
                            {
                                continue;
                            }
                        }
                    }
                }
            }
            let origin_at = origin.map(|o| o.origin);
            if !seen.insert((finding.at, origin_at, rule.clone())) {
                continue;
            }
            let mut related = vec![];
            if let Some(o) = origin {
                related.push(json!({"pos":pos(a.r,o.owner.unwrap_or(o.origin)),"message":"Storage originates here."}));
            }
            for call in finding.chain {
                if call != finding.at {
                    related.push(json!({"pos":pos(a.r,call),"message":"Reference effects followed through this call."}));
                }
            }
            findings.push(json!({"type":"warning","stage":"vet","code":format!("thor/{}",rule.replace('_',"-")),"certainty":if finding.definite{"definite"}else{"unverified"},"pos":pos(a.r,finding.at),"msgs":[format!("{} {}: {}.",if finding.definite{"Definite"}else{"Unverified"},finding.rule,finding.message)],"related":related}));
        }
    }
    findings.sort_by_key(|f| {
        (
            f["pos"]["file"].as_str().unwrap_or("").to_owned(),
            f["pos"]["offset"].as_u64(),
            f["code"].as_str().unwrap_or("").to_owned(),
        )
    });
    findings
}

#[cfg(test)]
mod tests {
    use super::*;
    fn at(n: usize) -> Ref {
        Ref {
            file: 0,
            node: NodeId(n),
        }
    }
    fn cell(value: Value) -> Cell {
        Cell {
            value,
            life: LIVE,
            uncertain: false,
            released: None,
        }
    }

    #[test]
    fn cyclic_reference_graphs_terminate_and_preserve_allocation_identity() {
        let mut s = State::default();
        s.cells.insert(0, cell(Value::reference(Place::root(1))));
        s.cells.insert(1, cell(Value::reference(Place::root(0))));
        let reachable = s.reachable([Value::reference(Place::root(0))]);
        let expected = BTreeSet::from([0, 1]);
        assert_eq!(reachable, expected);
    }
    #[test]
    fn branch_join_keeps_release_alternatives_and_is_idempotent() {
        let mut s = State::default();
        s.cells.insert(0, cell(Value::default()));
        let mut released = s.clone();
        released.cells.get_mut(&0).unwrap().life = DEAD;
        s.merge(&released);
        let again = s.clone();
        s.merge(&again);
        assert_eq!(s, again);
        assert_eq!(s.cells[&0].life, LIVE | DEAD);
    }
    #[test]
    fn conditional_export_is_possible_without_becoming_a_guaranteed_transfer() {
        let live = Value::reference(Place::root(0));
        let mut maybe = live.clone();
        maybe.merge(&Value::default());
        let s = State::default();
        let possible = s.reachable([maybe.clone()]);
        let certain = s.certainly_reachable([maybe]);
        let expected = BTreeSet::from([0]);
        assert_eq!(possible, expected);
        assert!(certain.is_empty());
    }
    #[test]
    fn copied_inline_records_share_only_their_explicit_reference_fields() {
        let mut record = Value::default();
        record
            .fields
            .insert(Key::Field("data".into()), Value::reference(Place::root(7)));
        let mut copy = record.clone();
        copy.fields
            .insert(Key::Field("other".into()), Value::reference(Place::root(8)));
        let mut original = BTreeSet::new();
        record.roots(&mut original);
        let mut copied = BTreeSet::new();
        copy.roots(&mut copied);
        let expected = BTreeSet::from([7]);
        assert_eq!(original, expected);
        let expected = BTreeSet::from([7, 8]);
        assert_eq!(copied, expected);
    }
    #[test]
    fn conditional_defer_is_not_upgraded_to_unconditional_cleanup() {
        let mut s = State::default();
        s.cleanups.insert(
            (1, at(0)),
            vec![Pending {
                action: Cleanup::Owner(Place::root(0)),
                conditional: false,
                allocator: Allocator::Unknown,
                temp_allocator: Allocator::Temporary(0),
            }],
        );
        s.merge(&State::default());
        assert!(s.cleanups[&(1, at(0))][0].conditional);
    }
    #[test]
    fn precise_element_replacement_does_not_modify_a_different_element() {
        let mut array = Value::default();
        array.write(
            &[Key::Item(Some(0))],
            &Value::reference(Place::root(1)),
            false,
        );
        array.write(
            &[Key::Item(Some(1))],
            &Value::reference(Place::root(2)),
            false,
        );
        array.write(&[Key::Item(Some(0))], &Value::default(), false);
        let first = array.read(&[Key::Item(Some(0))]);
        let second = array.read(&[Key::Item(Some(1))]);
        assert!(first.refs.is_empty());
        let expected = BTreeSet::from([Place::root(2)]);
        assert_eq!(second.refs, expected);
    }
}
