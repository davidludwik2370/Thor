//! Alpha-renaming of two exclusive outer buffers at a loop back-edge. The
//! program still swaps real headers; this only canonicalizes analysis identities.
use super::*;
impl Analysis<'_, '_> {
    fn swapped_headers(&mut self, at: Ref, f: &Frame) -> Option<(Ref, Ref)> {
        let key = (at, f.bindings.clone());
        if let Some(v) = self.summaries.header_pairs.get(&key) {
            return *v;
        }
        self.summaries.header_pairs.insert(key.clone(), None);
        let body = self.role(at, "body")?;
        let nodes = self.r.sources[body.file].node(body.node).children.clone();
        if nodes.len() < 2 {
            return None;
        }
        let tail = Ref {
            node: *nodes.last()?,
            ..body
        };
        let resize = self.role(tail, "expr").unwrap_or(tail);
        let callee = self.role(resize, "callee")?;
        if self.call_model(resize, callee).builtin.as_deref() != Some("resize") {
            return None;
        }
        crate::profile::count("lifetime.header_pair_candidate", 1);
        let args = self.group(resize, "args");
        if args.len() != 2
            || self.r.constant_value(args[1], &f.bindings, None) != Some(Constant::Integer(0))
        {
            return None;
        }
        let resized = self.role(args[0], "expr").filter(|_| {
            self.kind(args[0]) == "Unary"
                && self.r.sources[args[0].file].node(args[0].node).op == "&"
        })?;
        let swap_stmt = Ref {
            node: nodes[nodes.len() - 2],
            ..body
        };
        let swap = self.role(swap_stmt, "expr").unwrap_or(swap_stmt);
        let actual = self.group(swap, "args");
        if actual.len() != 2 {
            return None;
        }
        let mut names = vec![];
        for a in &actual {
            if self.kind(*a) != "Unary" || self.r.sources[a.file].node(a.node).op != "&" {
                return None;
            }
            let n = self.role(*a, "expr")?;
            if self.kind(n) != "Ident" {
                return None;
            }
            names.push(self.symbol(n)?);
        }
        if names[0] == names[1] || self.symbol(resized) != Some(names[1]) {
            return None;
        }
        // Verify the selected procedure body. A helper called `swap` has no
        // privilege by name, and extra writes/deferred work reject the proof.
        let plans = self.r.call_plans(swap, &f.bindings);
        if plans.len() != 1 {
            return None;
        }
        let proc = plans[0].procedure;
        let sig = self.role(proc, "type")?;
        let params = self.role(sig, "params")?;
        let ps = self.r.sources[params.file]
            .node(params.node)
            .children
            .clone()
            .into_iter()
            .flat_map(|node| self.group(Ref { node, ..params }, "names"))
            .collect::<Vec<_>>();
        if ps.len() != 2 {
            return None;
        }
        let bs = self.role(proc, "body")?;
        let statements = self.r.sources[bs.file].node(bs.node).children.clone();
        if statements.len() != 1 {
            return None;
        }
        let assignment = Ref {
            node: statements[0],
            ..bs
        };
        if self.kind(assignment) != "Assignment"
            || self.r.sources[bs.file].node(assignment.node).op != "="
        {
            return None;
        }
        let lhs = self.group(assignment, "lhs");
        let rhs = self.group(assignment, "rhs");
        if lhs.len() != 2 || rhs.len() != 2 {
            return None;
        }
        for (expr, param) in [
            (lhs[0], ps[0]),
            (lhs[1], ps[1]),
            (rhs[0], ps[1]),
            (rhs[1], ps[0]),
        ] {
            if self.kind(expr) != "Deref"
                || self.role(expr, "expr").and_then(|n| self.symbol(n)) != self.symbol(param)
            {
                return None;
            }
        }
        let pair = (names[0], names[1]);
        self.summaries.header_pairs.insert(key, Some(pair));
        crate::profile::count("lifetime.header_pair_verified", 1);
        Some(pair)
    }
    pub(super) fn normalize_header_pair(
        &mut self,
        entry: &State,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
    ) {
        let Some((a, b)) = self.swapped_headers(at, f) else {
            return;
        };
        let Some(ha) = f.env.get(&a).copied() else {
            return;
        };
        let Some(hb) = f.env.get(&b).copied() else {
            return;
        };
        for h in [ha, hb] {
            if !matches!(self.objects[h].storage,Storage::Stack{frame,..}if frame==f.id) {
                return;
            }
        }
        let exact = |s: &State, h| {
            let v = s.read(&Place::root(h));
            if !v.unknown && v.refs.len() == 1 {
                v.refs
                    .first()
                    .filter(|p| p.path.is_empty() && p.start == Some(0) && !p.interior)
                    .map(|p| p.id)
            } else {
                None
            }
        };
        let (Some(x), Some(y), Some(cx), Some(cy)) = (
            exact(entry, ha),
            exact(entry, hb),
            exact(state, ha),
            exact(state, hb),
        ) else {
            return;
        };
        if x == y || cx != y || cy != x {
            return;
        }
        if self.objects[x].resource.is_some() || self.objects[y].resource.is_some() {
            return;
        }
        if !matches!((&self.objects[x].storage,&self.objects[y].storage),(Storage::Heap(a),Storage::Heap(b))if a==b)
        {
            return;
        }
        // Branches can change scalar contents without changing buffer lifetime.
        // Opaque effects invalidate the value itself and still reject this proof.
        if [x, y].iter().any(|id| {
            state
                .cells
                .get(id)
                .is_none_or(|c| c.life != LIVE || c.value.unknown)
        }) {
            return;
        }
        let targets = BTreeSet::from([ha, hb, x, y]);
        let mut roots = state
            .cells
            .iter()
            .filter(|(id, c)| **id != ha && **id != hb && c.life & LIVE != 0)
            .map(|(_, c)| c.value.clone())
            .collect::<Vec<_>>();
        roots.extend(f.keys.values().cloned());
        for id in &state.exports {
            roots.push(Value::reference(Place::root(*id)));
        }
        fn allocator(a: &Allocator, roots: &mut Vec<Value>) {
            if let Allocator::Region(p) = a {
                roots.push(Value::reference((**p).clone()));
            }
        }
        fn metadata(v: &Value, roots: &mut Vec<Value>) {
            if let Some(a) = &v.allocator {
                allocator(a, roots);
            }
            for v in v.fields.values() {
                metadata(v, roots);
            }
        }
        allocator(&state.allocator, &mut roots);
        allocator(&state.temp_allocator, &mut roots);
        for c in state.cells.values() {
            metadata(&c.value, &mut roots);
        }
        for cleanup in state.cleanups.values().flatten() {
            allocator(&cleanup.allocator, &mut roots);
            allocator(&cleanup.temp_allocator, &mut roots);
            match &cleanup.action {
                Cleanup::Owner(p) if p.id == ha || p.id == hb => {}
                Cleanup::Owner(p) => roots.push(Value::reference(p.clone())),
                Cleanup::Procedure { inputs, .. } => roots.extend(inputs.iter().cloned()),
                Cleanup::Region(id) => roots.push(Value::reference(Place::root(*id))),
                Cleanup::Statement(stmt) => {
                    let uses = self.r.sources[stmt.file]
                        .descendants(stmt.node)
                        .any(|node| {
                            self.symbol(Ref { node, ..*stmt })
                                .is_some_and(|s| s == a || s == b)
                        });
                    if uses {
                        let call = self.role(*stmt, "expr").unwrap_or(*stmt);
                        let Some(callee) = self.role(call, "callee") else {
                            return;
                        };
                        let args = self.group(call, "args");
                        if self.call_model(call, callee).builtin.as_deref() != Some("delete")
                            || args.len() != 1
                            || !self.symbol(args[0]).is_some_and(|s| s == a || s == b)
                        {
                            return;
                        }
                    }
                }
            }
        }
        for pool in state.pools.values() {
            roots.push(pool.backing.clone());
            for t in pool.tasks.values() {
                roots.push(t.data.clone());
                allocator(&t.allocator, &mut roots);
            }
        }
        for worker in state.workers.values().filter(|w|w.phase&2!=0) {
            roots.push(worker.task.data.clone());
            allocator(&worker.task.allocator, &mut roots);
            allocator(&worker.task.temp_allocator, &mut roots);
        }
        if state.reachable(roots).iter().any(|id| targets.contains(id)) {
            return;
        }
        fn place(p: &mut Place, x: Id, y: Id) {
            if p.id == x {
                p.id = y
            } else if p.id == y {
                p.id = x
            }
        }
        fn alloc(a: &mut Allocator, x: Id, y: Id) {
            if let Allocator::Region(p) = a {
                place(Arc::make_mut(p), x, y)
            }
        }
        fn value(v: &mut Value, x: Id, y: Id) {
            v.refs = std::mem::take(&mut v.refs)
                .into_iter()
                .map(|mut p| {
                    place(&mut p, x, y);
                    p
                })
                .collect();
            if let Some(a) = &mut v.allocator {
                alloc(a, x, y);
            }
            for v in v.fields.values_mut() {
                value(v, x, y);
            }
            v.absent_when.clear();
        }
        state.cells = std::mem::take(&mut state.cells)
            .into_iter()
            .map(|(id, mut c)| {
                value(&mut c.value, x, y);
                (
                    if id == x {
                        y
                    } else if id == y {
                        x
                    } else {
                        id
                    },
                    c,
                )
            })
            .collect();
        state.buffers = std::mem::take(&mut state.buffers)
            .into_iter()
            .map(|(mut p, b)| {
                place(&mut p, x, y);
                (p, b)
            })
            .collect();
        state.regions = std::mem::take(&mut state.regions)
            .into_iter()
            .map(|(mut p, b)| {
                place(&mut p, x, y);
                (p, b)
            })
            .collect();
        for places in f.range_places.values_mut() {
            for p in places {
                place(p, x, y);
            }
        }
        crate::profile::count("lifetime.header_pair_normalized", 1);
    }
}
