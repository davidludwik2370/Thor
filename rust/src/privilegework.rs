//! Reuse only statement transfers already observed to leave their inputs and
//! outputs unchanged. The existing canonical graph comparison still decides
//! convergence, including recursion, aliases and newly allocated graph nodes.
use super::*;
use std::{
    cell::RefCell,
    ops::{Index, IndexMut},
};
const LIMIT: usize = 4096;
const CACHE_LIMIT: usize = 100_000;

#[derive(Default)]
pub(super) struct Values {
    items: Vec<Value>,
    trace: RefCell<Option<Trace>>,
}
struct Trace {
    start: usize,
    values: HashMap<V, Value>,
    callees: BTreeMap<C, u64>,
    overflow: bool,
}
impl Values {
    pub fn len(&self) -> usize {
        self.items.len()
    }
    pub fn push(&mut self, value: Value) {
        self.items.push(value);
    }
    fn tracing(&self) -> bool {
        self.trace.borrow().is_some()
    }
    fn start(&self) {
        *self.trace.borrow_mut() = Some(Trace {
            start: self.len(),
            values: HashMap::new(),
            callees: BTreeMap::new(),
            overflow: false,
        });
    }
    fn finish(&self) -> Trace {
        self.trace
            .borrow_mut()
            .take()
            .expect("active permission trace")
    }
    fn observe(&self, id: V) {
        let mut active = self.trace.borrow_mut();
        let Some(trace) = active.as_mut() else {
            return;
        };
        if trace.overflow || id >= trace.start || trace.values.contains_key(&id) {
            return;
        }
        if trace.values.len() >= LIMIT {
            trace.overflow = true;
            trace.values.clear();
            return;
        }
        trace.values.insert(id, self.items[id].clone());
    }
    fn callee(&self, c: C, context: &Context) {
        let mut active = self.trace.borrow_mut();
        let Some(trace) = active.as_mut() else {
            return;
        };
        if trace.overflow {
            return;
        }
        if context.active || !context.complete || trace.callees.len() >= LIMIT {
            trace.overflow = true;
            return;
        }
        trace.callees.insert(c, context.revision);
    }
}
impl Index<V> for Values {
    type Output = Value;
    fn index(&self, id: V) -> &Value {
        self.observe(id);
        &self.items[id]
    }
}
impl IndexMut<V> for Values {
    fn index_mut(&mut self, id: V) -> &mut Value {
        self.observe(id);
        &mut self.items[id]
    }
}

#[derive(Clone, PartialEq, Eq)]
struct Shape {
    env: HashMap<usize, V>,
    results: Vec<V>,
    stores: BTreeMap<Path, V>,
    effects: Shared<BTreeMap<Origin, Effect>>,
}
impl Shape {
    fn of(context: &Context) -> Self {
        Self {
            env: context.env.clone(),
            results: context.results.clone(),
            stores: context.stores.clone(),
            effects: context.effects.clone(),
        }
    }
    fn matches(&self, context: &Context) -> bool {
        self.env == context.env
            && self.results == context.results
            && self.stores == context.stores
            && self.effects == context.effects
    }
    fn len(&self) -> usize {
        self.env.len() + self.results.len() + self.stores.len() + self.effects.len()
    }
}
pub(super) struct Dependencies {
    values: HashMap<V, Value>,
    shape: Shape,
    globals: HashMap<usize, V>,
    callees: BTreeMap<C, u64>,
}
impl Dependencies {
    fn len(&self) -> usize {
        self.values.len() + self.shape.len() + self.globals.len() + self.callees.len()
    }
    fn valid(&self, analysis: &Privileges<'_, '_>, c: C) -> bool {
        self.shape.matches(&analysis.contexts[c])
            && self.globals == analysis.globals
            && self.callees.iter().all(|(c, revision)| {
                !analysis.contexts[*c].active
                    && analysis.contexts[*c].complete
                    && analysis.contexts[*c].revision == *revision
            })
            && self
                .values
                .iter()
                .all(|(v, before)| before == &analysis.values.items[*v])
    }
}
impl Privileges<'_, '_> {
    pub(super) fn note_callee(&self, c: C) {
        self.values.callee(c, &self.contexts[c]);
    }
    pub(super) fn body_pass(&mut self, body: Ref, c: C, incremental: bool) {
        // Most bodies stabilize in two passes. Do not trace those ordinary
        // passes: retain the cheap path and start only when replay is costly.
        if !incremental
            || self.values.tracing()
            || self.kind(body) != "Block"
            || self.uncertain.is_some()
            || self.deleting
        {
            self.statement(body, c);
            return;
        }
        for statement in self.children(body) {
            let key = (c, statement);
            if self
                .work
                .get(&key)
                .is_some_and(|cache| cache.valid(self, c))
            {
                crate::profile::count("permissions.statements_skipped", 1);
                continue;
            }
            if let Some(old) = self.work.remove(&key) {
                self.work_weight -= old.len();
            }
            let ctx = &self.contexts[c];
            if self.work_weight >= CACHE_LIMIT
                || ctx.env.len()
                    + ctx.results.len()
                    + ctx.stores.len()
                    + ctx.effects.len()
                    + self.globals.len()
                    > LIMIT
            {
                self.statement(statement, c);
                continue;
            }
            let shape = Shape::of(&self.contexts[c]);
            let globals = self.globals.clone();
            let contexts = self.contexts.len();
            self.values.start();
            self.statement(statement, c);
            let trace = self.values.finish();
            crate::profile::count("permissions.statements_traced", 1);
            // A transfer is reusable only after an execution that changed no
            // existing value, root/store/effect binding or global binding.
            // Fresh unreachable temporaries do not affect canonical equality.
            let dependencies = Dependencies {
                values: trace.values,
                shape,
                globals,
                callees: trace.callees,
            };
            if !trace.overflow
                && contexts == self.contexts.len()
                && dependencies.valid(self, c)
                && self.work_weight + dependencies.len() <= CACHE_LIMIT
            {
                self.work_weight += dependencies.len();
                self.work.insert(key, dependencies);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn traces_notice_every_old_read_and_write_but_do_not_retain_new_temporaries() {
        let resolver = Resolver::new(&[]);
        let typ = resolver.shared_type(Type::scalar("int"));
        let mut values = Values::default();
        values.push(Value::new(typ.clone()));
        values.start();
        let _ = values[0].typ.clone();
        values.push(Value::new(typ));
        values[1].base = Some(0);
        let trace = values.finish();
        if trace.values.len() != 1 || !trace.values.contains_key(&0) || trace.overflow {
            panic!("trace missed an input or retained new temporary");
        }
        values.start();
        values[0].base = Some(1);
        let changed = values.finish();
        if changed.values[&0] == values[0] {
            panic!("write did not capture the previous value");
        }
    }
    #[test]
    fn reuse_checks_roots_global_bindings_values_and_callee_revisions() {
        let mut resolver = Resolver::new(&[]);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        analysis
            .contexts
            .push(Context::new(None, Substitutions::new()));
        analysis
            .contexts
            .push(Context::new(None, Substitutions::new()));
        analysis.contexts[1].complete = true;
        analysis.contexts[1].revision = 1;
        let v = analysis.value(Type::scalar("int"));
        analysis.contexts[0].env.insert(7, v);
        let dependencies = Dependencies {
            values: [(v, analysis.values[v].clone())].into(),
            shape: Shape::of(&analysis.contexts[0]),
            globals: analysis.globals.clone(),
            callees: [(1, 1)].into(),
        };
        if !dependencies.valid(&analysis, 0) {
            panic!("unchanged dependency was invalid");
        }
        analysis.values[v].base = Some(v);
        if dependencies.valid(&analysis, 0) {
            panic!("value change ignored");
        }
        analysis.values[v].base = None;
        analysis.contexts[1].revision += 1;
        if dependencies.valid(&analysis, 0) {
            panic!("callee change ignored");
        }
        analysis.contexts[1].revision = 1;
        analysis.globals.insert(1, v);
        if dependencies.valid(&analysis, 0) {
            panic!("global shape change ignored");
        }
        analysis.globals.clear();
        analysis.contexts[0].env.insert(8, v);
        if dependencies.valid(&analysis, 0) {
            panic!("root shape change ignored");
        }
    }
}
