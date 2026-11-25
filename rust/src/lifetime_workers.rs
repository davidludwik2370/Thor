//! Native thread retention, separate from the existing pool queue but sharing
//! its callback interpreter and bounded effect proof. Completion is join/destroy,
//! never a mere is_done query or an unregister/terminate assumption.
use super::*;
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Worker {
    pub task: threads::Task,
    pub phase: u8,
    pub opaque: bool,
    pub detached: bool,
    pub direct: bool,
}
impl Worker {
    pub fn join(&mut self, other: &Self) {
        self.phase |= other.phase;
        self.opaque |=
            other.opaque || self.detached != other.detached || self.direct != other.direct;
        self.task.data.join(&other.task.data);
        self.task.callback.join(&other.task.callback);
        self.task.safe &= other.task.safe;
        if self.task.inputs != other.task.inputs || self.task.bindings != other.task.bindings {
            self.opaque = true;
        }
    }
}
impl Analysis<'_, '_> {
    fn finish_worker(&mut self, state: &mut State, place: &Place, at: Ref, destroy: bool) -> bool {
        let Some(mut worker) = state.workers.get(place).cloned() else {
            return false;
        };
        if worker.detached {
            self.report(
                at,
                Some(place.id),
                false,
                "lifetime",
                "self-cleaning thread handle cannot be joined or destroyed safely",
            );
            return true;
        }
        if worker.phase & 2 != 0 {
            self.valid(state, &worker.task.data, at);
            if worker.task.safe && !worker.opaque {
                if let Some(after) = self.task_effects(state, &worker.task) {
                    if self.task_inline_only(state, &after) {
                        *state = after;
                    } else {
                        self.unknown_call(
                            state,
                            at,
                            &[worker.task.data.clone()],
                            "native thread callback effects changed before completion",
                        );
                    }
                }
            }
        }
        worker.phase = if destroy { 8 } else { 4 };
        state.workers.insert(place.clone(), worker.clone());
        if destroy {
            self.release_as(
                state,
                &Value::reference(place.clone()),
                at,
                Some(Resource::Thread),
            );
        }
        state.workers.insert(place.clone(), worker);
        true
    }
    pub(super) fn worker_model(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        args: &[Ref],
        vs: &[Value],
        name: &str,
    ) -> Option<Vec<Value>> {
        let spawn = matches!(
            name,
            "create"
                | "create_and_start"
                | "create_and_start_with_data"
                | "create_and_start_with_poly_data"
                | "create_and_start_with_poly_data2"
                | "create_and_start_with_poly_data3"
                | "create_and_start_with_poly_data4"
                | "run"
                | "run_with_data"
                | "run_with_poly_data"
                | "run_with_poly_data2"
                | "run_with_poly_data3"
                | "run_with_poly_data4"
        );
        if spawn {
            if state.workers.len() >= 8 {
                return None;
            }
            // A caller-provided runtime.Context has arbitrary allocator hooks.
            if self
                .argument(args, "init_context", usize::MAX)
                .is_some_and(|i| {
                    !self
                        .r
                        .native_builtin(self.role(args[i], "value").unwrap_or(args[i]), "nil")
                })
            {
                return None;
            }
            let direct = name == "create";
            let count = if name.ends_with("data4") {
                4
            } else if name.ends_with("data3") {
                3
            } else if name.ends_with("data2") {
                2
            } else if name.ends_with("data") {
                1
            } else {
                0
            };
            let callback_i = self.argument(
                args,
                if direct { "procedure" } else { "fn" },
                if direct { 0 } else { count },
            )?;
            let callback = vs[callback_i].clone();
            let mut inputs = vec![];
            let mut types = vec![];
            for j in 0..count {
                let param = if count == 1 {
                    "data".into()
                } else {
                    format!("arg{}", j + 1)
                };
                let i = self.argument(args, &param, j)?;
                inputs.push(vs[i].clone());
                types.push(self.typ(self.role(args[i], "value").unwrap_or(args[i]), f));
            }
            let detached = name.starts_with("run")
                || self
                    .argument(args, "self_cleanup", count + 3)
                    .is_some_and(|i| {
                        self.r.constant_value(
                            self.role(args[i], "value").unwrap_or(args[i]),
                            &f.bindings,
                            None,
                        ) == Some(Constant::Boolean(true))
                    });
            // Unknown self_cleanup cannot grant join/destroy privileges.
            let unknown_detach = self
                .argument(args, "self_cleanup", count + 3)
                .is_some_and(|i| {
                    !matches!(
                        self.r.constant_value(
                            self.role(args[i], "value").unwrap_or(args[i]),
                            &f.bindings,
                            None
                        ),
                        Some(Constant::Boolean(_))
                    )
                });
            let mut handle = self.allocate(state, f, at, state.allocator.clone(), Value::default());
            handle.may_nil = true;
            let place = handle.refs.first()?.clone();
            self.objects[place.id].resource = Some(Resource::Thread);
            let mut data = Value::default();
            for v in &inputs {
                data.merge(v);
            }
            if direct {
                state.write(
                    &place.child(Key::Field("procedure".into())),
                    &callback,
                    false,
                );
            }else{
                // These are public Thread fields, and remain inspectable after
                // join. Keep payload provenance there as well as in the queue.
                state.write(&place.child(Key::Field("data".into())),&callback,false);
                if count>0{
                    let mut word=data.clone();
                    if name.contains("poly"){
                        fn edges(v:&Value,out:&mut BTreeSet<Place>){out.extend(v.refs.iter().cloned());for item in v.fields.values(){edges(item,out);}}
                        let mut refs=BTreeSet::new();edges(&word,&mut refs);
                        word=Value{refs,unknown:true,..Value::default()};
                    }
                    state.write(&place.child(Key::Field("user_args".into())).child(if name.contains("poly"){Key::Item(None)}else{Key::Item(Some(0))}),&word,false);
                }
            }
            let bindings = callback
                .procedures
                .first()
                .and_then(|p| self.role(*p, "type"))
                .map(|sig| self.r.parameter_bindings(sig, &types))
                .unwrap_or_default();
            let mut worker = Worker {
                task: threads::Task {
                    at,
                    callback,
                    data,
                    allocator: Allocator::Unknown,
                    safe: false,
                    inputs: Some(inputs),
                    bindings,
                    temp_allocator: Allocator::Temporary(place.id + 1),
                },
                phase: if direct { 1 } else { 2 },
                opaque: unknown_detach,
                detached,
                direct,
            };
            if !direct {
                self.verify_worker(state, &mut worker);
            }
            if detached {
                state.cells.get_mut(&place.id).unwrap().life = ABSENT;
            }
            state.workers.insert(place, worker);
            crate::profile::count("lifetime.worker_contracts", 1);
            return Some(if name.starts_with("run") {
                vec![]
            } else {
                vec![handle]
            });
        }
        if !matches!(
            name,
            "start" | "join" | "destroy" | "join_multiple" | "is_done"
        ) {
            return None;
        }
        let i = self.argument(
            args,
            if name == "join_multiple" {
                "threads"
            } else {
                "thread"
            },
            0,
        )?;
        let mut targets = vs[i].clone();
        if name == "join_multiple" {
            for v in vs.iter().skip(i + 1) {
                targets.merge(v);
            }
            let items = self.items(state, &targets, at);
            targets.refs.extend(items.refs);
        }
        if targets.unknown
            || targets.refs.is_empty()
            || targets.refs.iter().any(|p| !state.workers.contains_key(p))
        {
            return None;
        }
        for place in targets.refs.clone() {
            self.direct(state, &Value::reference(place.clone()), at);
            if name == "is_done" {
                continue;
            }
            if name == "start" {
                let mut worker = state.workers[&place].clone();
                if worker.phase != 1 || !worker.direct {
                    return None;
                }
                let header = state.read(&place);
                if header
                    .fields
                    .get(&Key::Field("init_context".into()))
                    .is_some_and(|v| *v != Value::default())
                {
                    return None;
                }
                worker.task.callback = header.child(&Key::Field("procedure".into()));
                worker.task.data = header;
                worker.task.inputs = Some(vec![Value::reference(place.clone())]);
                worker.phase = 2;
                self.verify_worker(state, &mut worker);
                state.workers.insert(place, worker);
            } else {
                self.finish_worker(state, &place, at, name == "destroy");
            }
        }
        Some(if name == "is_done" {
            vec![Value::default()]
        } else {
            vec![]
        })
    }
    fn verify_worker(&mut self, state: &mut State, worker: &mut Worker) {
        worker.task.safe = self
            .task_effects(state, &worker.task)
            .is_some_and(|after| self.task_inline_only(state, &after));
        if !worker.task.safe || worker.opaque {
            self.unknown_call(
                state,
                worker.task.at,
                &[worker.task.data.clone()],
                "native thread callback has unverified ownership effects",
            );
        }
    }
    pub(super) fn check_worker_release(&mut self, state: &State, value: &Value, at: Ref) {
        if self.async_callback {
            return;
        }
        for worker in state.workers.values() {
            if worker.phase & 2 == 0 {
                continue;
            }
            let refs = state.reachable([worker.task.data.clone()]);
            for p in &value.refs {
                if refs.contains(&p.id) {
                    self.report(at,Some(p.id),false,"lifetime","cleanup may invalidate data retained by a native thread; join or destroy the thread first");
                }
            }
        }
    }
    pub(super) fn pending_worker_scope(&mut self, state: &State, f: &Frame, scope: Ref) {
        for worker in state.workers.values() {
            if worker.phase & 2 == 0 {
                continue;
            }
            for id in state.reachable([worker.task.data.clone()]) {
                if matches!(self.objects[id].storage,Storage::Stack{frame,scope:s}if frame==f.id&&s==scope)
                {
                    self.report(worker.task.at,Some(id),false,"lifetime","native thread may outlive borrowed stack storage; join or destroy it before scope exit");
                }
            }
        }
    }
}
