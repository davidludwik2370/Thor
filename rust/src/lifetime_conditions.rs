//! Small relational facts for optional native resources. No path enumeration or
//! solver: predicates inspect already evaluated bindings and never replay calls.
use super::*;
impl Value {
    pub(super) fn join_file_choices(
        &mut self,
        other: &Self,
        left: &BTreeSet<Id>,
        right: &BTreeSet<Id>,
    ) {
        if left.is_empty()
            && right.is_empty()
            && self.absent_when.is_empty()
            && other.absent_when.is_empty()
        {
            self.join(other);
            return;
        }
        let mut choices = BTreeMap::<Place, BTreeSet<Id>>::new();
        let exact = (!self.unknown || self.choice)
            && (!other.unknown || other.choice)
            && !self.may_nil
            && !other.may_nil;
        if exact && self.refs.len() + other.refs.len() <= 8 {
            for (v, absent) in [(&*self, left), (other, right)] {
                for p in &v.refs {
                    let mut ids = absent.clone();
                    ids.extend(v.absent_when.get(p).into_iter().flatten());
                    choices
                        .entry(p.clone())
                        .and_modify(|old| old.retain(|id| ids.contains(id)))
                        .or_insert(ids);
                }
            }
        }
        self.join(other);
        self.absent_when = choices;
    }
}
impl Analysis<'_, '_> {
    pub(super) fn normalize_failed_allocations(&self,state:&mut State,returned:&[Value],fresh:usize){
        if !state.cells.range(fresh..).any(|(id,c)|c.life==DEAD&&matches!(self.objects[*id].storage,Storage::Heap(_))){return;}
        // Every live surviving slot, export and result protects its aliases.
        // Only fresh, private allocations cleaned before returning nil become
        // absent on that alternative. Caller-owned freed storage is never folded.
        let mut roots=returned.iter().cloned().chain(state.cells.values().filter(|c|c.life&LIVE!=0).map(|c|c.value.clone()))
            .chain(state.exports.iter().copied().map(|id|Value::reference(Place::root(id)))).collect::<Vec<_>>();
        fn allocator(a:&Allocator,roots:&mut Vec<Value>){if let Allocator::Region(p)=a{roots.push(Value::reference((**p).clone()));}}
        fn metadata(v:&Value,roots:&mut Vec<Value>){if let Some(a)=&v.allocator{allocator(a,roots);}for item in v.fields.values(){metadata(item,roots);}}
        let mut extra=vec![];for v in &roots{metadata(v,&mut extra);}roots.extend(extra);
        allocator(&state.allocator,&mut roots);allocator(&state.temp_allocator,&mut roots);
        for (id,c) in &state.cells{if c.life&LIVE!=0{if let Storage::Heap(a)=&self.objects[*id].storage{allocator(a,&mut roots);}}}
        for pending in state.cleanups.values().flatten(){
            allocator(&pending.allocator,&mut roots);allocator(&pending.temp_allocator,&mut roots);
            match &pending.action{Cleanup::Owner(p)=>roots.push(Value::reference(p.clone())),Cleanup::Procedure{inputs,..}=>roots.extend(inputs.iter().cloned()),Cleanup::Region(id)=>roots.push(Value::reference(Place::root(*id))),_=>{}}
        }
        for pool in state.pools.values(){for task in pool.tasks.values(){roots.push(task.data.clone());allocator(&task.allocator,&mut roots);allocator(&task.temp_allocator,&mut roots);}}
        for worker in state.workers.values(){if worker.phase&2!=0{roots.push(worker.task.data.clone());allocator(&worker.task.allocator,&mut roots);allocator(&worker.task.temp_allocator,&mut roots);}}
        let visible=state.reachable(roots);
        for(id,c)in state.cells.range_mut(fresh..){
            if c.life==DEAD&&!visible.contains(id)&&matches!(self.objects[*id].storage,Storage::Heap(_)){c.life=ABSENT;}
        }
    }
    fn predicate_value(&self, state: &State, f: &Frame, at: Ref) -> Option<(Option<Place>, Value)> {
        if self.kind(at) == "Paren" {
            return self.predicate_value(state, f, self.role(at, "expr")?);
        }
        let symbol = self.symbol(at)?;
        let id = f
            .env
            .get(&symbol)
            .copied()
            .or_else(|| self.ids.get(&(0, symbol, 0)).copied())?;
        let p = Place::root(id);
        Some((Some(p.clone()), state.read(&p)))
    }
    pub(super) fn refine_condition(
        &mut self,
        state: &mut State,
        f: &Frame,
        at: Ref,
        yes: bool,
        depth: usize,
    ) -> bool {
        // Scheduling-time scalar values are not an asynchronous ownership
        // contract. Check both runtime branches of a queued callback.
        if self.async_callback {
            return true;
        }
        if depth >= 8 {
            return true;
        }
        let node = &self.r.sources[at.file].node(at.node);
        if node.kind == "Paren" {
            return self
                .role(at, "expr")
                .is_none_or(|p| self.refine_condition(state, f, p, yes, depth + 1));
        }
        if node.kind == "Unary" && node.op == "!" {
            return self
                .role(at, "expr")
                .is_none_or(|p| self.refine_condition(state, f, p, !yes, depth + 1));
        }
        if node.kind != "Binary" {
            return self
                .predicate_value(state, f, at)
                .and_then(|(_, v)| v.truth)
                .is_none_or(|b| b == yes);
        }
        let op = node.op.clone();
        let Some(left) = self.role(at, "left") else {
            return true;
        };
        let Some(right) = self.role(at, "right") else {
            return true;
        };
        if op == "&&" && yes || op == "||" && !yes {
            return self.refine_condition(state, f, left, yes, depth + 1)
                && self.refine_condition(state, f, right, yes, depth + 1);
        }
        if !matches!(op.as_str(), "==" | "!=") {
            return true;
        }
        let equal = yes == (op == "==");
        for (value, other) in [(left, right), (right, left)] {
            let Some((slot, mut v)) = self.predicate_value(state, f, value) else {
                continue;
            };
            if self.r.native_builtin(other, "nil") {
                if matches!(base(&self.typ(value, f)), Type::Function(..) | Type::Procedure(_)) {
                    if !equal && v.nil_procedure && v.procedures.is_empty() && !v.unknown
                        || equal && !v.nil_procedure && !v.procedures.is_empty() && !v.unknown { return false; }
                    v.nil_procedure = equal;
                    if equal { v.procedures.clear(); v.unknown = false; }
                    if let Some(slot) = &slot { state.write(slot, &v, false); }
                }
                if let Some(id) = v.file_status.filter(|_| !v.unknown) {
                    if let Some(c) = state.cells.get_mut(&id) {
                        if equal {
                            c.life &= !ABSENT;
                        } else {
                            c.life = ABSENT;
                        }
                    }
                    // Success establishes a non-nil resource across its aliases.
                    if equal {
                        for c in state.cells.values_mut() {
                            Self::file_non_nil(&mut c.value, id);
                        }
                    }
                    crate::profile::count("lifetime.resource_refinements", 1);
                }
                if v.may_nil && !v.unknown && v.refs.len() == 1 {
                    let pointer = v.refs.first().unwrap();
                    if pointer.path.is_empty() && !pointer.interior && pointer.start == Some(0) {
                        if let Some(cell) = state.cells.get_mut(&pointer.id)
                            .filter(|cell| cell.life == (LIVE | ABSENT) && !cell.uncertain) {
                            cell.life = if equal { ABSENT } else { LIVE };
                            if equal { v.refs.clear(); }
                            v.may_nil = equal;
                            if let Some(slot) = &slot { state.write(slot, &v, false); }
                        }
                    }
                }
                continue;
            }
            let Some((_, against)) = self.predicate_value(state, f, other) else {
                continue;
            };
            if against.unknown
                || against.may_nil
                || against.refs.len() != 1
                || !against.fields.is_empty()
            {
                continue;
            }
            let target = against.refs.first().unwrap();
            // Native file identities are distinct allocations; arbitrary caller
            // pointers can alias and must not be separated by abstract IDs.
            if self.objects[target.id].resource.is_none()
                || v.refs.is_empty()
                || v.unknown && !v.choice
                || v.refs.iter().any(|p| self.objects[p.id].resource.is_none())
            {
                continue;
            }
            v.refs.retain(|p| (*p == *target) == equal);
            if v.refs.is_empty() && (equal || !v.may_nil) {
                return false;
            }
            if !v.may_nil && !v.refs.is_empty() {
                let mut absent = None::<BTreeSet<Id>>;
                for p in &v.refs {
                    let ids = v.absent_when.get(p).cloned().unwrap_or_default();
                    if let Some(old) = &mut absent {
                        old.retain(|id| ids.contains(id));
                    } else {
                        absent = Some(ids);
                    }
                }
                for id in absent.into_iter().flatten() {
                    if let Some(c) = state.cells.get_mut(&id) {
                        if c.life & DEAD == 0 {
                            c.life = ABSENT;
                        }
                    }
                }
            }
            v.unknown = v.refs.len() > 1 || v.may_nil;
            v.choice = v.unknown;
            if !v.may_nil && v.refs.len() == 1 {
                let p = v.refs.first().unwrap();
                if let Some(c) = state.cells.get_mut(&p.id) {
                    c.life &= !ABSENT;
                }
            }
            if let Some(slot) = slot {
                state.write(&slot, &v, false);
            }
            crate::profile::count("lifetime.resource_refinements", 1);
        }
        true
    }
    fn file_non_nil(value: &mut Value, id: Id) {
        if value.refs.len() == 1 && value.refs.first().is_some_and(|p| p.id == id) && !value.unknown
        {
            value.may_nil = false;
        }
        for v in value.fields.values_mut() {
            Self::file_non_nil(v, id);
        }
    }
}
