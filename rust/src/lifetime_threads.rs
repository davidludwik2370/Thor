//! Reviewed core:thread pool lifetimes, bounded to eight queued call sites.
//! Safe callback bodies may only change inline values. Reference/cleanup
//! effects, custom hooks, copied pool headers and unknown lifecycle stay opaque.
use super::*;
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Task {
    pub at: Ref,
    pub callback: Value,
    pub data: Value,
    pub allocator: Allocator,
    pub safe: bool,
    pub inputs:Option<Vec<Value>>,
    pub bindings:Substitutions,
    pub temp_allocator:Allocator,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Pool {
    pub phase: u8,
    pub opaque: bool,
    pub backing: Value,
    pub tasks: BTreeMap<Ref, Task>,
}
impl Pool {
    pub fn join(&mut self, other: &Self) {
        self.phase |= other.phase;
        self.opaque |= other.opaque;
        self.backing.join(&other.backing);
        for (at, task) in &other.tasks {
            self.tasks
                .entry(*at)
                .and_modify(|t| {
                    t.data.join(&task.data);
                    t.callback.join(&task.callback);
                    t.safe &= task.safe;
                })
                .or_insert_with(|| task.clone());
        }
        if self.tasks.len() > 8 {
            self.opaque = true;
        }
    }
}
impl Analysis<'_, '_> {
    fn empty_task_data_safe(&mut self, callback: &Value) -> bool {
        if callback.procedures.len() != 1 {
            return false;
        }
        let proc = *callback.procedures.first().unwrap();
        if let Some(answer) = self.summaries.empty_tasks.get(&proc) {
            return *answer;
        }
        let answer = self
            .role(proc, "body")
            .zip(self.role(proc, "type"))
            .is_some_and(|(body, sig)| {
                let Some(params) = self.role(sig, "params") else {
                    return false;
                };
                let names = self.r.sources[params.file]
                    .node(params.node)
                    .children
                    .iter()
                    .flat_map(|n| self.group(Ref { node: *n, ..params }, "names"))
                    .collect::<Vec<_>>();
                let symbols = names
                    .into_iter()
                    .filter_map(|n| self.symbol(n))
                    .collect::<HashSet<_>>();
                let nodes = self.r.sources[body.file]
                    .descendants(body.node)
                    .take(1025)
                    .collect::<Vec<_>>();
                nodes.len() <= 1024
                    && nodes.into_iter().all(|node| {
                        let at = Ref { node, ..body };
                        if self.kind(at) != "Ident"
                            || !self.symbol(at).is_some_and(|s| symbols.contains(&s))
                        {
                            return true;
                        }
                        let s = &self.r.sources[body.file];
                        s.node(node).parent.is_some_and(|p| {
                            s.node(p).kind == "Selector"
                                && s.role(p, "expr") == Some(node)
                                && s.role(p, "field").is_some_and(|field| {
                                    matches!(s.text(field), "user_index" | "allocator")
                                })
                        })
                    })
            });
        self.summaries.empty_tasks.insert(proc, answer);
        answer
    }
    pub(super) fn task_effects(&mut self, state: &State, task: &Task) -> Option<State> {
        if task.callback.procedures.len() != 1 {
            return None;
        }
        let proc = *task.callback.procedures.first()?;
        if self.role(proc, "body").is_none()
            || self.r.sources[proc.file].shared_native
            || self.active.iter().any(|(p, _)| *p == proc)
            || self.active.len() >= MAX_DEPTH
        {
            return None;
        }
        let input = Value {
            fields: BTreeMap::from([
                (Key::Field("data".into()), task.data.clone()),
                (Key::Field("procedure".into()), task.callback.clone()),
                (
                    Key::Field("allocator".into()),
                    Value {
                        allocator: Some(task.allocator.clone()),
                        ..Value::default()
                    },
                ),
            ]),
            ..Value::default()
        };
        let old_allocator = state.allocator.clone();
        let old_temp_allocator=state.temp_allocator.clone();
        let mut before = state.clone();
        before.allocator = task.allocator.clone();
        before.temp_allocator=task.temp_allocator.clone();
        self.active.push((proc, task.at));
        let previous = self.async_callback;
        self.async_callback = true;
        let exits = self.procedure(before, proc, task.inputs.clone().unwrap_or_else(||vec![input]), task.bindings.clone());
        self.async_callback = previous;
        self.active.pop();
        let mut result = None::<State>;
        for mut exit in exits {
            if exit.control == Control::Stop {
                continue;
            }
            exit.state.allocator = old_allocator.clone();
            exit.state.temp_allocator=old_temp_allocator.clone();
            if let Some(old) = &mut result {
                old.merge(&exit.state);
            } else {
                result = Some(exit.state);
            }
        }
        result
    }
    pub(super) fn task_inline_only(&self, before: &State, after: &State) -> bool {
        before.cells.iter().all(|(id, c)| {
            after.cells.get(id).is_some_and(|n| {
                c.life == n.life
                    && (!c.value.has_refs() && !n.value.has_refs() || c.value == n.value)
            })
        }) && after.cells.iter().all(|(id, c)| {
            before.cells.contains_key(id)
                || c.life & LIVE == 0
                || !c.value.has_refs() && matches!(self.objects[*id].storage, Storage::Static)
        }) && before.pools == after.pools && before.workers==after.workers
    }
    pub(super) fn pool_model(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        args: &[Ref],
        vs: &[Value],
        name: &str,
    ) -> Option<Vec<Value>> {
        if !matches!(
            name,
            "pool_init" | "pool_start" | "pool_add_task" | "pool_finish" | "pool_destroy"
        ) {
            return None;
        }
        let i = self.argument(args, "pool", 0)?;
        let target = &vs[i];
        self.direct(state, target, at);
        if target.unknown || target.refs.len() != 1 {
            return None;
        }
        let place = target.refs.first()?.clone();
        if name == "pool_init" {
            if state.pools.get(&place).is_some_and(|p| p.phase != 8) {
                self.report(
                    at,
                    Some(place.id),
                    false,
                    "leak",
                    "thread pool is reinitialized before its previous resources are destroyed",
                );
                return None;
            }
            let allocator = self
                .argument(args, "allocator", 1)
                .and_then(|i| vs[i].allocator.clone())
                .unwrap_or(Allocator::Unknown);
            let backing = self.allocate(state, f, at, allocator, Value::default());
            for p in &backing.refs {
                self.objects[p.id].resource = Some(Resource::Pool);
            }
            // Hooks may retain user data or mutate shared ownership.
            let opaque = args.iter().enumerate().any(|(j, a)| {
                j >= 3
                    || self.argument_name(*a).is_some_and(|n| {
                        matches!(
                            self.text(n),
                            "init_proc" | "init_data" | "fini_proc" | "fini_data"
                        )
                    })
            });
            if opaque {
                self.unknown_call(
                    state,
                    at,
                    vs,
                    "custom thread-pool hooks require manual ownership review",
                );
            }
            state.write(
                &place.child(Key::Field("<pool-owned>".into())),
                &backing,
                false,
            );
            state.pools.insert(
                place,
                Pool {
                    phase: 1,
                    opaque,
                    backing,
                    tasks: BTreeMap::new(),
                },
            );
            crate::profile::count("lifetime.pool_contracts", 1);
            return Some(vec![]);
        }
        let mut pool = state.pools.get(&place)?.clone();
        if pool.phase == 8 {
            self.report(
                at,
                Some(place.id),
                true,
                "lifetime",
                "thread pool is used after destruction",
            );
            return Some(vec![]);
        }
        if pool.opaque {
            return None;
        }
        match name {
            "pool_start" => {
                if pool.phase != 1 {
                    return None;
                }
                pool.phase = 2;
            }
            "pool_add_task" => {
                if pool.phase != 2 || pool.tasks.len() >= 8 {
                    return None;
                }
                let callback = vs.get(self.argument(args, "procedure", 2)?)?.clone();
                let data = vs.get(self.argument(args, "data", 3)?)?.clone();
                let allocator = self
                    .argument(args, "allocator", 1)
                    .and_then(|i| vs[i].allocator.clone())
                    .unwrap_or(Allocator::Unknown);
                let mut task = Task {
                    at,
                    callback,
                    data,
                    allocator,
                    safe: false,
                    inputs:None,
                    bindings:Substitutions::new(),
                    temp_allocator:Allocator::Temporary(place.id+1),
                };
                if let Some(after) = self.task_effects(state, &task) {
                    task.safe = self.task_inline_only(state, &after)
                        && !task.data.unknown
                        && task.data.refs.iter().all(|p| {
                            let v = state.read(p);
                            !v.has_refs() && !v.unknown
                        })
                        && (!task.data.refs.is_empty()
                            || self.empty_task_data_safe(&task.callback));
                }
                if !task.safe {
                    self.unknown_call(
                        state,
                        at,
                        &[task.data.clone()],
                        "asynchronous callback reference/cleanup effects cannot be verified",
                    );
                    self.report(
                        at,
                        None,
                        false,
                        "lifetime",
                        "asynchronous callback shared/global ownership effects require review",
                    );
                }
                let borrowed = state.reachable([task.data.clone()]);
                for (place, bytes) in &mut state.buffers {
                    if borrowed.contains(&place.id) {
                        bytes.forget();
                    }
                }
                pool.tasks
                    .entry(at)
                    .and_modify(|old| {
                        old.data.join(&task.data);
                        old.callback.join(&task.callback);
                        old.safe &= task.safe && old.callback.procedures.len() == 1;
                    })
                    .or_insert(task);
            }
            "pool_finish" => {
                if pool.phase != 2 {
                    return None;
                }
                for task in pool.tasks.values() {
                    if task.safe {
                        if let Some(after) = self.task_effects(state, task) {
                            if self.task_inline_only(state, &after) {
                                *state = after;
                            } else {
                                self.unknown_call(
                                    state,
                                    at,
                                    &[task.data.clone()],
                                    "queued callback ownership changed before completion",
                                );
                            }
                        } else {
                            self.unknown_call(
                                state,
                                at,
                                &[task.data.clone()],
                                "queued callback could not be verified at completion",
                            );
                        }
                    } else {
                        self.valid(state, &task.data, at);
                    }
                }
                pool.tasks.clear();
                pool.phase = 4;
            }
            "pool_destroy" => {
                if pool.phase == 2 || !pool.tasks.is_empty() {
                    self.report(
                        at,
                        Some(place.id),
                        pool.phase == 2,
                        "lifetime",
                        "thread pool must finish its workers before destruction",
                    );
                }
                self.release_as(state, &pool.backing, at, Some(Resource::Pool));
                pool.phase = 8;
                pool.tasks.clear();
                state.write(
                    &place.child(Key::Field("<pool-owned>".into())),
                    &Value::default(),
                    false,
                );
            }
            _ => return None,
        }
        state.pools.insert(place, pool);
        crate::profile::count("lifetime.pool_contracts", 1);
        Some(vec![])
    }
    pub(super) fn pending_pool_scope(&mut self, state: &State, f: &Frame, scope: Ref) {
        for pool in state.pools.values() {
            if pool.tasks.is_empty() {
                continue;
            }
            for task in pool.tasks.values() {
                for id in state.reachable([task.data.clone()]) {
                    if matches!(self.objects[id].storage,Storage::Stack{frame,scope:s}if frame==f.id&&s==scope)
                    {
                        self.report(task.at,Some(id),!pool.opaque&&pool.phase==2,"lifetime","queued task may outlive its borrowed stack storage; finish the pool before this scope exits");
                    }
                }
            }
        }
    }
}
