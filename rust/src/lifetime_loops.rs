//! Narrow allocation-site folding for exclusive, inline-element collection
//! headers. No old view may survive; dropped owners and opaque escapes never
//! receive this equivalence between a freed generation and its replacement.
use super::*;

impl Analysis<'_, '_> {
    pub(super) fn discard_loop_locals(&self, state: &mut State, f: &Frame) {
        let candidates = state
            .cells
            .iter()
            .filter_map(|(id, c)| {
                (c.life & LIVE == 0
                    && self.objects[*id].addressed
                    && matches!(self.objects[*id].storage,Storage::Stack{frame,..}if frame>=f.id))
                .then_some(*id)
            })
            .collect::<Vec<_>>();
        if candidates.is_empty() {
            return;
        }
        // At a completed loop back-edge there are no pending expressions in
        // this frame or its completed descendants. Frame IDs increase on entry,
        // so active caller-frame temporaries are strictly older and excluded.
        let mut roots = state
            .cells
            .values()
            .filter(|c| c.life & LIVE != 0)
            .map(|c| c.value.clone())
            .collect::<Vec<_>>();
        fn allocator_roots(v: &Value, roots: &mut Vec<Value>) {
            if let Some(Allocator::Region(p)) = &v.allocator {
                roots.push(Value::reference((**p).clone()));
            }
            for value in v.fields.values() {
                allocator_roots(value, roots);
            }
        }
        for cell in state.cells.values().filter(|c| c.life & LIVE != 0) {
            allocator_roots(&cell.value, &mut roots);
        }
        if let Allocator::Region(p) = &state.allocator {
            roots.push(Value::reference((**p).clone()));
        }
        if let Allocator::Region(p)=&state.temp_allocator{roots.push(Value::reference((**p).clone()));}
        for cleanup in state.cleanups.values().flatten() {
            if let Allocator::Region(p)=&cleanup.temp_allocator{roots.push(Value::reference((**p).clone()));}
            match &cleanup.action {
                Cleanup::Procedure { inputs, .. } => {
                    roots.extend(inputs.iter().cloned());
                    for value in inputs {
                        allocator_roots(value, &mut roots);
                    }
                }
                Cleanup::Owner(p) => roots.push(Value::reference(p.clone())),
                _ => {}
            }
        }
        for cleanup in state.cleanups.values().flatten() {
            if let Allocator::Region(p) = &cleanup.allocator {
                roots.push(Value::reference((**p).clone()));
            }
        }
        for pool in state.pools.values() {
            for task in pool.tasks.values() {
                roots.push(task.data.clone());
                allocator_roots(&task.data, &mut roots);
                if let Allocator::Region(p) = &task.allocator {
                    roots.push(Value::reference((**p).clone()));
                }
            }
        }
        for worker in state.workers.values().filter(|w|w.phase&2!=0){roots.push(worker.task.data.clone());allocator_roots(&worker.task.data,&mut roots);if let Allocator::Region(p)=&worker.task.allocator{roots.push(Value::reference((**p).clone()));}}
        for (id, cell) in &state.cells {
            if cell.life & LIVE != 0 {
                if let Storage::Heap(Allocator::Region(p)) = &self.objects[*id].storage {
                    roots.push(Value::reference((**p).clone()));
                }
            }
        }
        let protected = state.reachable(roots);
        for id in candidates {
            if !protected.contains(&id) && !state.exports.contains(&id) {
                state.cells.remove(&id);
                crate::profile::count("lifetime.loop_expired_slots", 1);
                state.buffers.retain(|p, _| p.id != id);
                state.regions.retain(|p, _| p.id != id);
                state.pools.retain(|p,pool|p.id!=id || pool.phase!=8);
            }
        }
    }
    fn fresh_header(&mut self, call: Ref, f: &Frame) -> bool {
        self.fresh_header_at(call, f, 0)
    }
    fn fresh_header_at(&mut self, call: Ref, f: &Frame, depth: usize) -> bool {
        if depth > 8 {
            return false;
        }
        let call = self
            .role(call, "expr")
            .filter(|_| matches!(self.kind(call), "Paren" | "OrPanic"))
            .unwrap_or(call);
        let Some(callee) = self.role(call, "callee") else {
            return false;
        };
        let model = self.call_model(call, callee);
        if model.builtin.as_deref() == Some("make") || model.cloning {
            return true;
        }
        let plans = self.r.call_plans(call, &f.bindings);
        if plans.len() != 1 {
            return false;
        }
        let plan = &plans[0];
        if self.owning_results(plan.procedure, &plan.bindings) == [true] {
            return true;
        }
        let key = (plan.procedure, plan.bindings.clone());
        if let Some(answer) = self.summaries.fresh_headers.get(&key) {
            return *answer;
        }
        // A recursive return alone is not a fresh allocation proof.
        self.summaries.fresh_headers.insert(key.clone(), false);
        let Some(body) = self.role(plan.procedure, "body") else {
            return false;
        };
        let nodes = self.r.sources[body.file]
            .descendants(body.node)
            .take(1025)
            .collect::<Vec<_>>();
        if nodes.len() > 1024 || self.summaries.exclusive_nodes + nodes.len() > 16384 {
            return false;
        }
        self.summaries.exclusive_nodes += nodes.len();
        let inner = Frame {
            procedure: plan.procedure,
            bindings: plan.bindings.clone(),
            ..f.clone()
        };
        let mut saw = false;
        let mut answer = true;
        for node in &nodes {
            let at = Ref {
                node: *node,
                ..body
            };
            if self.kind(at) != "Return" {
                continue;
            }
            saw = true;
            let values = self.group(at, "values");
            if values.len() != 1 || !self.fresh_header_at(values[0], &inner, depth + 1) {
                answer = false;
                break;
            }
        }
        answer &= saw;
        self.summaries.fresh_headers.insert(key, answer);
        answer
    }
    fn released_before(&mut self, assignment: Ref, variable: Ref, f: &Frame) -> bool {
        let s = &self.r.sources[assignment.file];
        let Some(block) = s
            .node(assignment.node)
            .parent
            .filter(|p| s.node(*p).kind == "Block")
        else {
            return false;
        };
        // A range binding into this same buffer must not be live at reset.
        if s.ancestors(assignment.node)
            .filter(|p| s.node(*p).kind == "Range")
            .any(|p| {
                s.role(p, "expr").is_some_and(|e| {
                    std::iter::once(e).chain(s.descendants(e)).any(|n| {
                        self.symbol(Ref {
                            node: n,
                            ..assignment
                        }) == Some(variable)
                    })
                })
            })
        {
            return false;
        }
        let statements = s.node(block).children.clone();
        let Some(index) = statements.iter().position(|p| *p == assignment.node) else {
            return false;
        };
        for node in statements[..index].iter().rev() {
            let item = Ref {
                node: *node,
                ..assignment
            };
            let uses = self.r.sources[item.file]
                .descendants(item.node)
                .any(|n| self.symbol(Ref { node: n, ..item }) == Some(variable));
            if !uses {
                continue;
            }
            let call = self.role(item, "expr").unwrap_or(item);
            let Some(callee) = self.role(call, "callee") else {
                return false;
            };
            return self.call_model(call, callee).builtin.as_deref() == Some("delete")
                && self.group(call, "args").len() == 1
                && self
                    .group(call, "args")
                    .first()
                    .is_some_and(|a| self.symbol(*a) == Some(variable))
                && self
                    .group(assignment, "rhs")
                    .first()
                    .is_some_and(|rhs| self.fresh_header(*rhs, f));
        }
        false
    }
    fn exclusive_header(&mut self, variable: Ref, f: &Frame) -> bool {
        let key = (variable, f.bindings.clone());
        if let Some(answer) = self.summaries.exclusive.get(&key) {
            return *answer;
        }
        let answer = self.prove_exclusive_header(variable, f);
        self.summaries.exclusive.insert(key, answer);
        answer
    }
    fn prove_exclusive_header(&mut self, variable: Ref, f: &Frame) -> bool {
        let Some(index) = self.r.symbol(variable) else {
            return false;
        };
        let symbol = self.r.symbols[index].clone();
        if symbol.parameter
            || symbol.result
            || symbol.scope == NodeId(0)
            || self
                .r
                .attributes(symbol.reference)
                .iter()
                .any(|(n, _)| matches!(n.as_str(), "static" | "thread_local"))
        {
            return false;
        }
        let typ = self.typ(variable, f);
        if !matches!(base(&typ),Type::Slice(t)|Type::Dynamic(t) if self.inline_type(t,0)) {
            return false;
        }
        let Some(body) = self.role(f.procedure, "body") else {
            return false;
        };
        let nodes = self.r.sources[body.file]
            .descendants(body.node)
            .take(4097)
            .collect::<Vec<_>>();
        if nodes.len() > 4096 || self.summaries.exclusive_nodes + nodes.len() > 16384 {
            return false;
        }
        self.summaries.exclusive_nodes += nodes.len();
        crate::profile::count("lifetime.exclusive_proof_nodes", nodes.len() as u64);
        let mut bindings = HashSet::new();
        for node in &nodes {
            let at = Ref {
                node: *node,
                ..body
            };
            if self.kind(at) == "Unsafe" {
                return false;
            }
            if self.kind(at) == "Range"
                && self
                    .role(at, "expr")
                    .is_some_and(|e| self.symbol(e) == Some(variable))
            {
                for name in self.group(at, "names") {
                    if self.kind(name) == "Unary" {
                        if let Some(binding) = self.role(name, "expr").and_then(|e| self.symbol(e))
                        {
                            bindings.insert(binding);
                        }
                    }
                }
            }
        }
        for node in nodes {
            let at = Ref { node, ..body };
            if self.kind(at) != "Ident" {
                continue;
            }
            let symbol = self.symbol(at);
            let projection = self.r.projection(at).map(|p| p.owner);
            let projected = projection.and_then(|p| self.symbol(p));
            if symbol.is_some_and(|s| bindings.contains(&s))
                || projected.is_some_and(|s| bindings.contains(&s))
            {
                let s = &self.r.sources[at.file];
                if s.node(at.node).parent.is_some_and(|p| {
                    s.node(p).kind == "Unary"
                        && s.node(p).parent.is_some_and(|r| {
                            s.node(r).kind == "Range" && s.group(r, "names").contains(&p)
                        })
                }) {
                    continue;
                }
                if self.r.sources[at.file].ancestors(at.node).any(|p| {
                    self.r.sources[at.file].node(p).kind == "Slice"
                        || self.r.sources[at.file].node(p).kind == "Unary"
                            && self.r.sources[at.file].node(p).op == "&"
                }) {
                    return false;
                }
                continue;
            }
            if symbol != Some(variable) {
                continue;
            }
            let s = &self.r.sources[at.file];
            if s.ancestors(at.node)
                .any(|n| s.node(n).kind == "Procedure" && n != f.procedure.node)
            {
                return false;
            }
            let Some(parent) = s.node(node).parent else {
                return false;
            };
            if s.node(parent).kind == "Declaration" && s.group(parent, "names").contains(&node) {
                continue;
            }
            let mut cursor = node;
            let mut element = false;
            let mut accepted = false;
            for _ in 0..32 {
                let Some(parent) = self.r.sources[at.file].node(cursor).parent else {
                    break;
                };
                let owner = Ref { node: parent, ..at };
                match self.kind(owner) {
                    "Paren" | "FieldValue" => cursor = parent,
                    "Slice" => {
                        element = false;
                        cursor = parent;
                    }
                    "Unary" if self.r.sources[at.file].node(parent).op == "&" => {
                        if cursor != node {
                            break;
                        }
                        cursor = parent;
                    }
                    "Index" => {
                        let t = self.typ(owner, f);
                        element = self.inline_type(&t, 0);
                        cursor = parent;
                    }
                    "Selector" if element => cursor = parent,
                    "Declaration" | "Return" if element => {
                        accepted = true;
                        break;
                    }
                    "Range" => {
                        accepted = true;
                        break;
                    }
                    "Assignment" => {
                        let lhs = self.group(owner, "lhs");
                        accepted = element
                            || lhs.iter().all(|v| self.text(*v) == "_")
                            || lhs.len() == 1
                                && self.symbol(lhs[0]) == Some(variable)
                                && self.released_before(owner, variable, f);
                        break;
                    }
                    "Binary" => {
                        accepted = true;
                        break;
                    }
                    "Call" => {
                        if element {
                            accepted = true;
                            break;
                        }
                        let Some(callee) = self.role(owner, "callee") else {
                            break;
                        };
                        let model = self.call_model(owner, callee);
                        accepted = model.builtin.as_deref().is_some_and(|b| {
                            matches!(
                                b,
                                "len"
                                    | "cap"
                                    | "delete"
                                    | "append"
                                    | "append_elem"
                                    | "clear"
                                    | "unordered_remove"
                                    | "ordered_remove"
                                    | "remove"
                            )
                        }) || model.library.as_ref().is_some_and(|(l, n)| {
                            matches!(contracts::effect(l, n), Some(contracts::Effect::Format))
                                && !self.custom_formatting
                        });
                        break;
                    }
                    _ => break,
                }
            }
            if !accepted {
                return false;
            }
        }
        true
    }
    pub(super) fn replace_exclusive_header(
        &mut self,
        state: &mut State,
        f: &Frame,
        at: Ref,
        slot: &Place,
        value: &mut Value,
    ) {
        let old = state.read(slot);
        if old.unknown || value.unknown || old.refs.len() != 1 || value.refs.len() != 1 {
            return;
        }
        let a = old.refs.first().unwrap();
        let b = value.refs.first().unwrap();
        if a.id == b.id || *a != Place::root(a.id) || *b != Place::root(b.id) {
            return;
        }
        let Some(before) = state.cells.get(&a.id) else {
            return;
        };
        let Some(after) = state.cells.get(&b.id).cloned() else {
            return;
        };
        if before.life & LIVE != 0
            || before.uncertain
            || after.life & DEAD != 0
            || after.uncertain
            || before.value.has_refs()
            || after.value.has_refs()
            || before.value.unknown
            || after.value.unknown
            || self.objects[a.id].resource.is_some()
            || self.objects[b.id].resource.is_some()
            || !matches!((&self.objects[a.id].storage,&self.objects[b.id].storage),(Storage::Heap(x),Storage::Heap(y)) if x==y)
        {
            return;
        }
        let Some(variable) = self.symbol(at) else {
            return;
        };
        if !self.exclusive_header(variable, f) {
            return;
        }
        // The syntactic proof excludes hidden aliases; call-time checks also
        // protect inherited caller state and unusual scalar address casts.
        if state.cells.iter().any(|(id, c)| {
            *id != slot.id
                && c.life & LIVE != 0
                && (c.value.references(a.id) || c.value.references(b.id))
        }) || state.cleanups.values().flatten().any(|p| {
            matches!(&p.action,Cleanup::Procedure{inputs,..}
                if inputs.iter().any(|v|v.references(a.id)||v.references(b.id)))
        }) || state.exports.contains(&a.id)
            || state.exports.contains(&b.id)
        {
            return;
        }
        state.cells.insert(a.id, after);
        state.cells.remove(&b.id);
        value.refs = BTreeSet::from([Place::root(a.id)]);
        crate::profile::count("lifetime.exclusive_replacements", 1);
    }
}
