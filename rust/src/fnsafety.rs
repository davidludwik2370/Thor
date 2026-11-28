//! Experimental, bounded fn liveness dataflow. This is not a safety certificate.
//! Summaries are local to this resolved command, never trusted across revisions.
use crate::{
    ast::NodeId,
    diagnostic::{Error, Result},
    semantics::{Constant, Ref, Resolver, Substitutions, Type},
};
use serde_json::{json, Value as Json};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

#[path = "fnsafety_expr.rs"]
mod expr;
#[path = "fnsafety_flow.rs"]
mod flow;

#[derive(Clone, Copy, Default, PartialEq, Eq)]
pub enum Mode {
    #[default]
    Off,
    Prototype,
    Strict,
}
impl Mode {
    pub fn parse(text: &str) -> Result<Self> {
        match text {
            "off" => Ok(Self::Off),
            "prototype" => Ok(Self::Prototype),
            "strict" => Ok(Self::Strict),
            _ => Err(Error::usage(
                "--fn-safety: choose off, prototype, or strict",
            )),
        }
    }
    pub fn enabled(self) -> bool {
        self != Self::Off
    }
}

const LIVE: u8 = 1;
const DEAD: u8 = 2;
const ABSENT: u8 = 4;
const NON_NIL: u8 = 1;
const NIL: u8 = 2;
const MAX_DEPTH: usize = 64;
const MAX_FACTS: usize = 512;
const MAX_STEPS: usize = 32_768;

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum Origin {
    Input(usize),
    Owned(Ref, usize),
    Stack(Ref),
    Static,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
struct Value {
    refs: BTreeSet<Origin>,
    fields: BTreeMap<String, Value>,
    targets: BTreeSet<Ref>,
    callable: u8,
    unknown: bool,
    view: bool,
}
impl Value {
    fn join(&mut self, other: &Self) {
        self.refs.extend(other.refs.iter().cloned());
        self.targets.extend(&other.targets);
        self.callable |= other.callable;
        self.unknown |= other.unknown;
        self.view |= other.view;
        for (name, value) in &other.fields {
            self.fields.entry(name.clone()).or_default().join(value);
        }
    }
    fn origins(&self, into: &mut BTreeSet<Origin>) {
        into.extend(self.refs.iter().cloned());
        for value in self.fields.values() {
            value.origins(into);
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
struct State {
    env: BTreeMap<Ref, Value>,
    life: BTreeMap<Origin, u8>,
    deferred: Vec<(Ref, Value)>,
    stopped: bool,
}
impl State {
    fn join(&mut self, other: &Self) {
        self.stopped &= other.stopped;
        for (key, value) in &other.env {
            self.env.entry(*key).or_default().join(value);
        }
        let keys = self
            .life
            .keys()
            .chain(other.life.keys())
            .cloned()
            .collect::<BTreeSet<_>>();
        for key in keys {
            let value = self.life.get(&key).copied().unwrap_or(ABSENT)
                | other.life.get(&key).copied().unwrap_or(ABSENT);
            self.life.insert(key, value);
        }
        // Conditional defers cannot be represented as unconditional cleanup.
        for entry in &other.deferred {
            if !self.deferred.contains(entry) {
                self.deferred.push(entry.clone());
            }
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Control {
    Next,
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
#[derive(Clone, Default)]
struct Summary {
    results: Vec<Value>,
    input_life: BTreeMap<usize, u8>,
    complete: bool,
    reads: BTreeSet<usize>,
    terminates: bool,
}
#[derive(Clone)]
struct Frame {
    procedure: Ref,
    bindings: Substitutions,
    steps: usize,
    uncertain: usize,
    reads: BTreeSet<usize>,
}

struct Check<'a, 'r> {
    r: &'r mut Resolver<'a>,
    summaries: HashMap<(Ref, Substitutions), Summary>,
    active: HashSet<(Ref, Substitutions)>,
    issues: BTreeMap<(Ref, bool, String), Json>,
    gaps: BTreeSet<(Ref, String)>,
    roots: BTreeSet<Ref>,
    type_shapes: HashMap<Type, bool>,
    generation: usize,
}

pub(crate) fn analyze(r: &mut Resolver<'_>, mode: Mode) -> Result<Vec<Json>> {
    let _phase = crate::profile::span("analysis.fn_safety");
    let roots = r
        .sources
        .iter()
        .enumerate()
        .filter(|(_, s)| s.thor && !s.excluded && !s.shared_native)
        .flat_map(|(file, s)| {
            s.nodes
                .iter()
                .enumerate()
                .filter(|(_, n)| n.kind == "Procedure" && n.data["pure"] == true)
                .map(move |(i, _)| Ref {
                    file,
                    node: NodeId(i),
                })
        })
        .collect::<BTreeSet<_>>();
    crate::profile::count("fn_safety.roots", roots.len() as u64);
    if roots.is_empty() {
        return Ok(vec![]);
    }
    let generic = roots
        .iter()
        .filter(|root| !crate::specialization::parameters(**root, r).is_empty())
        .copied()
        .collect::<BTreeSet<_>>();
    let mut entries = roots
        .iter()
        .filter(|root| !generic.contains(root))
        .map(|root| (*root, Substitutions::new()))
        .collect::<Vec<_>>();
    entries.extend(
        r.callback_instances
            .iter()
            .cloned()
            .filter(|(root, _)| roots.contains(root)),
    );
    // Generic bodies are checked in their concrete call contexts, including
    // calls from ordinary proc code and calls beneath #run.
    let calls = r
        .sources
        .iter()
        .enumerate()
        .filter(|(_, s)| s.thor && !s.excluded && !s.shared_native)
        .flat_map(|(file, s)| {
            s.nodes
                .iter()
                .enumerate()
                .filter(|(_, n)| n.kind == "Call")
                .map(move |(i, _)| Ref {
                    file,
                    node: NodeId(i),
                })
        })
        .collect::<Vec<_>>();
    for call in calls {
        let Some(callee) = r.sources[call.file]
            .role(call.node, "callee")
            .map(|node| Ref { node, ..call })
        else {
            continue;
        };
        if !r
            .candidates(callee)
            .iter()
            .any(|target| generic.contains(target))
        {
            continue;
        }
        for plan in r.call_plans(call, &Substitutions::new()) {
            if roots.contains(&plan.procedure) {
                entries.push((plan.procedure, plan.bindings));
            }
        }
    }
    entries.sort_by_cached_key(|(at, bindings)| {
        (*at, serde_json::to_string(bindings).unwrap_or_default())
    });
    entries.dedup();
    let mut check = Check {
        r,
        summaries: HashMap::new(),
        active: HashSet::new(),
        issues: BTreeMap::new(),
        gaps: BTreeSet::new(),
        roots,
        type_shapes: HashMap::new(),
        generation: 0,
    };
    for (root, bindings) in entries {
        check.procedure(root, bindings, 0);
    }
    for root in check.roots.clone() {
        if !check
            .summaries
            .keys()
            .any(|(procedure, _)| *procedure == root)
            && check.branch_status(root, &Substitutions::new()) != Some(false)
        {
            check.gap(
                root,
                "generic fn has no concrete specialization in this build",
            );
        }
    }
    crate::profile::count("fn_safety.functions", check.summaries.len() as u64);
    crate::profile::count(
        "fn_safety.complete_summaries",
        check
            .summaries
            .values()
            .filter(|summary| summary.complete)
            .count() as u64,
    );
    crate::profile::count("fn_safety.unverified", check.gaps.len() as u64);
    let mut diagnostics = check.issues.into_values().collect::<Vec<_>>();
    for (at, message) in check.gaps {
        let source = &check.r.sources[at.file];
        let mut error = Error::at(source, source.node(at.node).span,
            format!("fn safety prototype cannot verify: {message}. This is an incomplete check, not a memory-safety certificate."));
        error.diagnostics[0]["type"] = json!(if mode == Mode::Strict {
            "error"
        } else {
            "info"
        });
        error.diagnostics[0]["code"] = json!("thor/fn-safety-unverified");
        diagnostics.extend(error.diagnostics);
    }
    if diagnostics.iter().any(|d| d["type"] == "error") {
        let mut error = Error::new("fn safety prototype rejected this program");
        error.diagnostics = diagnostics;
        return Err(error);
    }
    Ok(diagnostics)
}

impl Check<'_, '_> {
    fn role(&self, at: Ref, role: &str) -> Option<Ref> {
        self.r.sources[at.file]
            .role(at.node, role)
            .map(|node| Ref { node, ..at })
    }
    fn group(&self, at: Ref, role: &str) -> Vec<Ref> {
        self.r.sources[at.file]
            .group(at.node, role)
            .iter()
            .map(|node| Ref { node: *node, ..at })
            .collect()
    }
    fn kind(&self, at: Ref) -> &str {
        self.r.sources[at.file].node(at.node).kind.as_str()
    }
    fn text(&self, at: Ref) -> &str {
        self.r.sources[at.file].text(at.node)
    }
    fn symbol(&self, at: Ref) -> Ref {
        self.r
            .symbol(at)
            .map(|i| Ref {
                file: self.r.symbols[i].reference.file,
                node: self.r.symbols[i].name_node,
            })
            .unwrap_or(at)
    }
    fn binding_path(&self, mut at: Ref) -> Option<(Ref, Vec<String>)> {
        let mut path = vec![];
        for _ in 0..MAX_DEPTH {
            if self.kind(at) == "Selector" {
                path.push(self.text(self.role(at, "field")?).to_string());
                at = self.role(at, "expr")?;
            } else if let Some(projection) = self
                .r
                .symbol(at)
                .and_then(|i| self.r.symbols[i].projection.as_ref())
            {
                path.push(projection.member.clone());
                at = projection.owner;
            } else {
                path.reverse();
                return Some((self.symbol(at), path));
            }
        }
        None
    }
    fn typ(&mut self, at: Ref, frame: &Frame) -> Type {
        self.r.specialized_type(at, &frame.bindings)
    }
    fn gap(&mut self, at: Ref, message: &str) {
        self.generation += 1;
        self.gaps.insert((at, message.into()));
    }
    fn issue(&mut self, at: Ref, origin: Option<&Origin>, frame: &Frame, message: &str) {
        if frame.uncertain != 0 {
            self.gap(at, message);
            return;
        }
        self.generation += 1;
        let source = &self.r.sources[at.file];
        let mut error = Error::at(
            source,
            source.node(at.node).span,
            format!("fn safety: {message}"),
        );
        error.diagnostics[0]["code"] = json!("thor/fn-safety");
        if let Some(Origin::Owned(origin, _) | Origin::Stack(origin)) = origin {
            error.diagnostics[0]["related"] = json!([crate::purediagnostics::note(
                self.r.sources,
                *origin,
                "This storage originated here."
            )]);
        }
        self.issues
            .insert((at, false, message.into()), error.diagnostics.remove(0));
    }
    fn step(&mut self, at: Ref, frame: &mut Frame, state: &State, depth: usize) -> bool {
        frame.steps += 1;
        if frame.steps > MAX_STEPS || state.life.len() > MAX_FACTS || depth > MAX_DEPTH {
            self.gap(
                frame.procedure,
                "the bounded flow-analysis budget was reached",
            );
            false
        } else {
            crate::profile::count("fn_safety.nodes", 1);
            let _ = at;
            true
        }
    }
    fn reference_type(&mut self, typ: &Type, depth: usize) -> bool {
        if let Some(value) = self.type_shapes.get(typ) {
            return *value;
        }
        if depth > 24 {
            return true;
        }
        let result = match typ.storage_shape() {
            Type::Scalar { .. }
            | Type::Enum(_)
            | Type::Constant(_)
            | Type::Function(..)
            | Type::Procedure(_) => false,
            Type::Array { elem, .. }
            | Type::Fixed { elem, .. }
            | Type::Vector { elem, .. }
            | Type::Matrix { elem, .. } => self.reference_type(elem, depth + 1),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(typ)
                .into_iter()
                .any(|(name, typ)| name != "_" && self.reference_type(&typ, depth + 1)),
            _ => true,
        };
        self.type_shapes.insert(typ.clone(), result);
        result
    }
    fn valid(&mut self, state: &State, value: &Value, at: Ref, frame: &mut Frame) {
        if state.stopped {
            return;
        }
        if value.unknown {
            self.gap(
                at,
                "reference provenance was lost by an unsupported operation",
            );
            return;
        }
        let mut origins = BTreeSet::new();
        value.origins(&mut origins);
        for origin in origins {
            if let Origin::Input(index) = &origin {
                frame.reads.insert(*index);
            }
            if state.life.get(&origin).is_some_and(|life| life & DEAD != 0) {
                if value.refs.len() > 1
                    && state.life.get(&origin).is_some_and(|life| life & LIVE != 0)
                {
                    self.gap(
                        at,
                        "branch-dependent owner selection requires correlated liveness facts",
                    );
                } else {
                    self.issue(
                        at,
                        Some(&origin),
                        frame,
                        "storage was released on a reachable path before this use",
                    );
                }
            }
        }
    }
    fn release(&mut self, state: &mut State, value: &Value, at: Ref, frame: &mut Frame) {
        self.valid(state, value, at, frame);
        if value.refs.is_empty() && !value.fields.is_empty() {
            let mut origins = BTreeSet::new();
            value.origins(&mut origins);
            if !origins.is_empty() {
                self.gap(
                    at,
                    "cleanup through a nested result needs an exact owner path",
                );
            }
        }
        if value.view {
            self.issue(
                at,
                value.refs.first(),
                frame,
                "a borrowed or interior view cannot be released as its original owner",
            );
        }
        for origin in &value.refs {
            if matches!(origin, Origin::Stack(_) | Origin::Static) {
                self.issue(
                    at,
                    Some(origin),
                    frame,
                    "cleanup requires an allocated owner",
                );
            }
            state.life.insert(origin.clone(), DEAD);
        }
    }
    fn fresh(&mut self, state: &mut State, at: Ref, slot: usize) -> Value {
        let origin = Origin::Owned(at, slot);
        if state.life.contains_key(&origin) {
            self.gap(
                at,
                "repeated allocation at one loop/call site needs generation tracking",
            );
        }
        state.life.insert(origin.clone(), LIVE);
        Value {
            refs: BTreeSet::from([origin]),
            ..Value::default()
        }
    }
}
