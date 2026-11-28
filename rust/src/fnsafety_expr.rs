use super::*;

struct Transfer {
    at: Ref,
    slot: usize,
    pure: bool,
    owners: BTreeMap<Origin, Value>,
}

impl Check<'_, '_> {
    pub(super) fn zero(&mut self, typ: &Type, depth: usize) -> Value {
        if depth > 24 {
            return Value {
                unknown: true,
                ..Value::default()
            };
        }
        match typ.storage_shape() {
            Type::Function(..) | Type::Procedure(_) => Value {
                callable: NIL,
                ..Value::default()
            },
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => Value {
                fields: self
                    .r
                    .record_fields(typ)
                    .into_iter()
                    .filter(|(n, _)| n != "_")
                    .map(|(name, typ)| (name, self.zero(&typ, depth + 1)))
                    .collect(),
                ..Value::default()
            },
            _ => Value::default(),
        }
    }
    pub(super) fn input(&mut self, typ: &Type, index: usize, depth: usize) -> Value {
        if depth > 24 {
            return Value {
                unknown: true,
                ..Value::default()
            };
        }
        match typ.storage_shape() {
            Type::Function(..) | Type::Procedure(_) => Value {
                callable: NON_NIL,
                ..Value::default()
            },
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => Value {
                fields: self
                    .r
                    .record_fields(typ)
                    .into_iter()
                    .filter(|(n, _)| n != "_")
                    .map(|(name, typ)| (name, self.input(&typ, index, depth + 1)))
                    .collect(),
                ..Value::default()
            },
            _ if self.reference_type(typ, 0) => Value {
                refs: BTreeSet::from([Origin::Input(index)]),
                ..Value::default()
            },
            _ => Value::default(),
        }
    }
    pub(super) fn expr(
        &mut self,
        state: &mut State,
        frame: &mut Frame,
        at: Ref,
        depth: usize,
    ) -> Value {
        if state.stopped {
            return Value::default();
        }
        if !self.step(at, frame, state, depth) {
            return Value {
                unknown: true,
                ..Value::default()
            };
        }
        let kind = self.kind(at).to_string();
        match kind.as_str() {
            "Ident" => {
                if self.r.native_builtin(at, "nil") {
                    return Value {
                        callable: NIL,
                        ..Value::default()
                    };
                }
                if let Some(projection) = self
                    .r
                    .symbol(at)
                    .and_then(|i| self.r.symbols[i].projection.clone())
                {
                    let owner = self.expr(state, frame, projection.owner, depth + 1);
                    self.valid(
                        state,
                        &Value {
                            refs: owner.refs.clone(),
                            ..Value::default()
                        },
                        at,
                        frame,
                    );
                    if let Some(value) = owner.fields.get(&projection.member) {
                        return value.clone();
                    }
                    self.gap(at, "using projection has no resolved local field state");
                    return Value {
                        unknown: true,
                        ..Value::default()
                    };
                }
                if let Some(value) = state.env.get(&self.symbol(at)) {
                    return value.clone();
                }
                let targets = self
                    .r
                    .candidates(at)
                    .into_iter()
                    .filter(|p| self.kind(*p) == "Procedure")
                    .collect::<BTreeSet<_>>();
                if !targets.is_empty() {
                    return Value {
                        targets,
                        callable: NON_NIL,
                        ..Value::default()
                    };
                }
                let typ = self.typ(at, frame);
                if self.reference_type(&typ, 0) {
                    Value {
                        refs: BTreeSet::from([Origin::Static]),
                        ..Value::default()
                    }
                } else {
                    Value::default()
                }
            }
            "Literal" => {
                if self.text(at).starts_with(['"', '`']) {
                    Value {
                        refs: BTreeSet::from([Origin::Static]),
                        ..Value::default()
                    }
                } else {
                    Value::default()
                }
            }
            "Procedure" => Value {
                targets: BTreeSet::from([at]),
                callable: NON_NIL,
                ..Value::default()
            },
            "Call" => self
                .call(state, frame, at, depth + 1)
                .into_iter()
                .next()
                .unwrap_or_default(),
            "Selector" => {
                let Some(base) = self.role(at, "expr") else {
                    self.gap(at, "selector base is unresolved");
                    return Value::default();
                };
                let value = self.expr(state, frame, base, depth + 1);
                self.valid(
                    state,
                    &Value {
                        refs: value.refs.clone(),
                        ..Value::default()
                    },
                    at,
                    frame,
                );
                let name = self
                    .role(at, "field")
                    .map(|r| self.text(r).to_string())
                    .unwrap_or_default();
                if let Some(field) = value.fields.get(&name) {
                    return field.clone();
                }
                let targets = self
                    .r
                    .candidates(at)
                    .into_iter()
                    .filter(|p| self.kind(*p) == "Procedure")
                    .collect::<BTreeSet<_>>();
                if !targets.is_empty() {
                    return Value {
                        targets,
                        callable: NON_NIL,
                        ..Value::default()
                    };
                }
                let typ = self.typ(at, frame);
                if self.reference_type(&typ, 0) || typ.pure_callable() {
                    self.gap(at, "reference field has no resolved local ownership state");
                    Value {
                        unknown: true,
                        ..value
                    }
                } else {
                    Value::default()
                }
            }
            "Index" | "MatrixIndex" | "Slice" | "Deref" => {
                if kind != "Deref" {
                    let mut enabled = !self.r.flags.iter().any(|flag| flag == "-no-bounds-check");
                    let source = &self.r.sources[at.file];
                    for node in std::iter::once(at.node).chain(source.ancestors(at.node)) {
                        match source.node(node).data["bounds_check"].as_i64() {
                            Some(-1) => {
                                enabled = false;
                                break;
                            }
                            Some(1) => {
                                enabled = true;
                                break;
                            }
                            _ => {}
                        }
                        if source.node(node).kind == "Procedure" {
                            if let Some(state) = source
                                .role(node, "type")
                                .and_then(|node| source.node(node).data["bounds_check"].as_i64())
                            {
                                if state != 0 {
                                    enabled = state == 1;
                                }
                            }
                            break;
                        }
                    }
                    if !enabled {
                        self.gap(
                            at,
                            "bounds checks are disabled and a static bounds proof is unavailable",
                        );
                    }
                }
                let Some(base) = self.role(at, "expr") else {
                    self.gap(at, "storage base is unresolved");
                    return Value::default();
                };
                let mut value = self.expr(state, frame, base, depth + 1);
                for role in ["index", "low", "high", "max"] {
                    if let Some(e) = self.role(at, role) {
                        self.expr(state, frame, e, depth + 1);
                    }
                }
                self.valid(state, &value, at, frame);
                if kind == "Slice" {
                    if value.refs.is_empty() {
                        let origin = Origin::Stack(self.symbol(base));
                        if state.life.get(&origin).is_some_and(|life| life & DEAD != 0) {
                            self.gap(at, "repeated stack storage needs generation tracking");
                        }
                        state.life.insert(origin.clone(), LIVE);
                        value.refs.insert(origin);
                    }
                    // A full [:] view is releasable only when it preserves an
                    // allocated owner's address; a rebased view never is.
                    value.view |= self.role(at, "low").is_some_and(|low| {
                        self.r.constant_value(low, &frame.bindings, None)
                            != Some(Constant::Integer(0))
                    });
                    return value;
                }
                if let Some(item) = value.fields.get("[]") {
                    return item.clone();
                }
                let typ = self.typ(at, frame);
                if self.reference_type(&typ, 0) || typ.pure_callable() {
                    value
                } else {
                    Value::default()
                }
            }
            "Compound" => {
                let typ = self.typ(at, frame);
                let fields = self.r.record_fields(&typ);
                let mut value = self.zero(&typ, 0);
                for (i, item) in self.group(at, "elems").into_iter().enumerate() {
                    let (name, expr) = if self.kind(item) == "FieldValue" {
                        (
                            self.role(item, "field").map(|f| self.text(f).to_string()),
                            self.role(item, "value").unwrap_or(item),
                        )
                    } else {
                        (fields.get(i).map(|(name, _)| name.clone()), item)
                    };
                    let item = self.expr(state, frame, expr, depth + 1);
                    let name = name.unwrap_or_else(|| "[]".into());
                    if name == "[]" {
                        value.fields.entry(name).or_default().join(&item);
                    } else if name != "_" {
                        value.fields.insert(name, item);
                    }
                }
                value
            }
            "Ternary" | "WhenExpr" => {
                if let Some(condition) = self.role(at, "cond") {
                    self.expr(state, frame, condition, depth + 1);
                    if let Some(selected) = self.r.context_condition(condition, &frame.bindings) {
                        if let Some(e) = self.role(at, if selected { "x" } else { "y" }) {
                            return self.expr(state, frame, e, depth + 1);
                        }
                    }
                }
                if kind == "WhenExpr" {
                    self.gap(
                        at,
                        "compile-time conditional expression is not yet selected",
                    );
                    frame.uncertain += 1;
                }
                let mut other = state.clone();
                let mut value = self
                    .role(at, "x")
                    .map(|r| self.expr(state, frame, r, depth + 1))
                    .unwrap_or_default();
                if let Some(r) = self.role(at, "y") {
                    value.join(&self.expr(&mut other, frame, r, depth + 1));
                }
                if kind == "WhenExpr" {
                    frame.uncertain -= 1;
                    value.unknown = true;
                }
                state.join(&other);
                value
            }
            "Paren" | "Run" | "Runtime" | "AutoCast" | "OrPanic" | "FieldValue" => self
                .role(
                    at,
                    if kind == "FieldValue" {
                        "value"
                    } else {
                        "expr"
                    },
                )
                .map(|e| self.expr(state, frame, e, depth + 1))
                .unwrap_or_default(),
            "Unary" => {
                let Some(expr) = self.role(at, "expr") else {
                    return Value::default();
                };
                let value = self.expr(state, frame, expr, depth + 1);
                if self.r.sources[at.file].node(at.node).op == "&" {
                    self.gap(
                        at,
                        "local address and indirect stores require a borrow summary",
                    );
                    value
                } else {
                    self.valid(state, &value, at, frame);
                    Value::default()
                }
            }
            "Binary" => {
                let op = self.r.sources[at.file].node(at.node).op.clone();
                if matches!(op.as_str(), "&&" | "||") {
                    if let (Some(left), Some(right)) =
                        (self.role(at, "left"), self.role(at, "right"))
                    {
                        self.expr(state, frame, left, depth + 1);
                        let yes = op == "&&";
                        if self.r.context_condition(left, &frame.bindings) != Some(!yes) {
                            let mut branch = state.clone();
                            if self.refine(&mut branch, left, yes) {
                                self.expr(&mut branch, frame, right, depth + 1);
                                state.join(&branch);
                            }
                        }
                    }
                    return Value::default();
                }
                let mut value = Value::default();
                for role in ["left", "right"] {
                    if let Some(e) = self.role(at, role) {
                        let v = self.expr(state, frame, e, depth + 1);
                        self.valid(state, &v, at, frame);
                        value.join(&v);
                    }
                }
                let typ = self.typ(at, frame);
                if self.reference_type(&typ, 0) {
                    self.gap(at, "reference-valued arithmetic needs an ownership model");
                    value.unknown = true;
                    value
                } else {
                    Value::default()
                }
            }
            "Directive" | "Implicit" | "ImplicitSelector" | "TypeidType" | "PolyType"
            | "ArrayType" | "SliceType" | "DynamicArrayType" | "StructType" | "ProcedureType"
            | "PointerType" | "MapType" | "SetType" => Value::default(),
            _ => {
                self.gap(
                    at,
                    &format!("{kind} expressions are outside the initial flow model"),
                );
                Value {
                    unknown: true,
                    ..Value::default()
                }
            }
        }
    }
    pub(super) fn values(
        &mut self,
        state: &mut State,
        frame: &mut Frame,
        expr: Ref,
        depth: usize,
    ) -> Vec<Value> {
        if self.kind(expr) == "Call" {
            self.call(state, frame, expr, depth + 1)
        } else {
            vec![self.expr(state, frame, expr, depth + 1)]
        }
    }
    fn call(&mut self, state: &mut State, frame: &mut Frame, at: Ref, depth: usize) -> Vec<Value> {
        if !self.step(at, frame, state, depth) {
            return vec![Value {
                unknown: true,
                ..Value::default()
            }];
        }
        let Some(callee) = self.role(at, "callee") else {
            return vec![];
        };
        let builtin = crate::purity::builtin_call(self.r, at);
        if builtin.as_deref() == Some("assert")
            && self.r.flags.iter().any(|f| f == "-disable-assert")
        {
            return vec![];
        }
        let args = self.group(at, "args");
        let mut arguments = BTreeMap::new();
        let mut inputs = Vec::new();
        for arg in &args {
            let value = self.expr(state, frame, *arg, depth + 1);
            arguments.insert(self.role(*arg, "value").unwrap_or(*arg), value.clone());
            inputs.push(value);
        }
        let library = ["core:slice", "core:strings", "core:mem"]
            .into_iter()
            .find_map(|lib| {
                crate::evaluation::library_member(self.r, callee, lib)
                    .filter(|_| crate::privileges::verified_library(self.r, callee, lib))
                    .map(|name| (lib, name))
            });
        let name = builtin
            .as_deref()
            .or_else(|| library.as_ref().map(|(_, name)| name.as_str()))
            .unwrap_or("");
        match name {
            "panic" | "unreachable" => {
                state.stopped = true;
                return vec![];
            }
            "len" | "cap" | "size_of" | "align_of" | "type_of" | "typeid_of" | "offset_of" => {
                return vec![Value::default()]
            }
            "make" | "new" | "new_clone" | "clone" => {
                for input in &inputs {
                    self.valid(state, input, at, frame);
                }
                // The reviewed default slice constructor has no backing
                // allocation for a literal zero length. Repeated nil cleanup
                // must not be mistaken for a double free.
                if name == "make" && args.len() == 2 {
                    let typ = self.typ(at, frame);
                    if matches!(typ.storage_shape(), Type::Slice(_))
                        && self.r.constant_value(
                            self.role(args[1], "value").unwrap_or(args[1]),
                            &frame.bindings,
                            None,
                        ) == Some(Constant::Integer(0))
                    {
                        return vec![Value::default()];
                    }
                }
                let mut value = self.fresh(state, at, 0);
                if name == "clone" {
                    if let Some(input) = inputs.first() {
                        value.fields = input.fields.clone();
                    }
                }
                return vec![value];
            }
            "delete" | "free" => {
                if let Some(value) = inputs.first() {
                    self.release(state, value, at, frame);
                }
                return vec![];
            }
            "assert" | "min" | "max" | "abs" | "clamp" | "copy" => {
                for input in &inputs {
                    self.valid(state, input, at, frame);
                }
                let typ = self.typ(at, frame);
                return vec![if self.reference_type(&typ, 0) {
                    let mut value = Value::default();
                    for v in &inputs {
                        value.join(v);
                    }
                    value
                } else {
                    Value::default()
                }];
            }
            "append" | "resize" | "reserve" => {
                self.gap(at, "growth/relocation needs a verified live-borrow summary");
                for input in &inputs {
                    self.valid(state, input, at, frame);
                }
                return vec![Value::default()];
            }
            _ => {}
        }
        if self.r.denotes_type(callee) {
            return vec![inputs.into_iter().next().unwrap_or_default()];
        }
        let callable = self.expr(state, frame, callee, depth + 1);
        if callable.callable & NIL != 0 {
            self.issue(
                at,
                None,
                frame,
                "callback may be nil at this call; initialize it or guard it against nil",
            );
        }
        let mut plans = self.r.call_plans(at, &frame.bindings);
        if !callable.targets.is_empty() {
            plans = self.r.call_plans_for_targets(
                at,
                &frame.bindings,
                &callable.targets.iter().copied().collect::<Vec<_>>(),
            );
        }
        plans.retain(|plan| self.kind(plan.procedure) == "Procedure");
        let mut output: Vec<Value> = vec![];
        if !plans.is_empty() {
            let original = state.clone();
            let mut joined = None;
            if plans.len() > 8 {
                self.gap(at, "more than eight callback alternatives");
                return vec![Value {
                    unknown: true,
                    ..Value::default()
                }];
            }
            for plan in plans {
                let summary = self.procedure(plan.procedure, plan.bindings.clone(), depth + 1);
                let mut branch = original.clone();
                let mut actuals = vec![];
                for actual in &plan.arguments {
                    let mut value = Value::default();
                    for actual in actual {
                        let v = arguments
                            .get(actual)
                            .cloned()
                            .unwrap_or_else(|| self.expr(&mut branch, frame, *actual, depth + 1));
                        value.join(&v);
                    }
                    actuals.push(value);
                }
                for index in &summary.reads {
                    if let Some(value) = actuals.get(*index) {
                        let mut origins = BTreeSet::new();
                        value.origins(&mut origins);
                        if value.refs.is_empty() && !origins.is_empty() {
                            self.gap(at, "callee reads aggregate references without a field-sensitive summary");
                        } else {
                            self.valid(&branch, value, at, frame);
                        }
                    }
                }
                for (index, life) in &summary.input_life {
                    if life & DEAD != 0 {
                        if let Some(value) = actuals.get(*index) {
                            if value.refs.is_empty() && !value.fields.is_empty() {
                                self.gap(at, "callee releases a nested reference without a field-sensitive summary");
                            } else {
                                self.release(&mut branch, value, at, frame);
                            }
                        }
                    }
                }
                let mut transfer = Transfer {
                    at,
                    slot: 0,
                    pure: self.r.sources[plan.procedure.file]
                        .node(plan.procedure.node)
                        .data["pure"]
                        == true,
                    owners: BTreeMap::new(),
                };
                output.resize(output.len().max(summary.results.len()), Value::default());
                for (out, result) in output.iter_mut().zip(&summary.results) {
                    out.join(&self.instantiate(&mut branch, result, &actuals, &mut transfer, 0));
                }
                if !summary.complete {
                    self.gap(at, "callee has unresolved safety obligations");
                }
                branch.stopped = summary.terminates;
                if let Some(joined) = &mut joined {
                    State::join(joined, &branch);
                } else {
                    joined = Some(branch);
                }
            }
            if let Some(joined) = joined {
                *state = joined;
            }
            return output;
        }
        let typ = self.typ(callee, frame);
        if let Type::Function(signature, _) = typ.storage_shape() {
            if callable.callable == 0 {
                self.gap(at, "callback initialization is unknown");
            }
            for input in &inputs {
                self.valid(state, input, at, frame);
            }
            let results = self.r.fields(*signature, "results");
            return results
                .iter()
                .enumerate()
                .map(|(i, _)| {
                    let typ = self.r.specialized_value_result(
                        at,
                        i,
                        &frame.bindings,
                        &mut HashSet::new(),
                    );
                    if typ.pure_callable() {
                        self.gap(
                            at,
                            "an opaque callback result needs a non-nullness contract",
                        );
                        return Value {
                            callable: NIL | NON_NIL,
                            ..Value::default()
                        };
                    }
                    if self.reference_type(&typ, 0) {
                        if matches!(typ.storage_shape(), Type::Slice(elem) | Type::Dynamic(elem) if self.reference_type(elem, 0)) {
                            self.gap(at, "an opaque nested result needs a deep ownership shape");
                            return Value { unknown: true, ..Value::default() };
                        }
                        if matches!(
                            typ.storage_shape(),
                            Type::Slice(_) | Type::Dynamic(_) | Type::String { .. }
                        ) {
                            self.fresh(state, at, i)
                        } else {
                            self.gap(
                                at,
                                "an opaque aggregate result needs a deep ownership shape",
                            );
                            Value {
                                unknown: true,
                                ..Value::default()
                            }
                        }
                    } else {
                        Value::default()
                    }
                })
                .collect();
        }
        self.gap(at, "call target has no available safety summary");
        for input in &inputs {
            self.valid(state, input, at, frame);
        }
        vec![Value {
            unknown: true,
            ..Value::default()
        }]
    }
    fn instantiate(
        &mut self,
        state: &mut State,
        value: &Value,
        inputs: &[Value],
        transfer: &mut Transfer,
        depth: usize,
    ) -> Value {
        if depth > 24 {
            self.gap(
                transfer.at,
                "result shape is deeper than the prototype limit",
            );
            return Value {
                unknown: true,
                ..Value::default()
            };
        }
        let mut out = Value {
            refs: BTreeSet::new(),
            fields: BTreeMap::new(),
            ..value.clone()
        };
        for origin in &value.refs {
            match origin {
                Origin::Input(i) => {
                    if let Some(value) = inputs.get(*i) {
                        out.join(value);
                    }
                }
                Origin::Owned(..) | Origin::Static
                    if transfer.pure || matches!(origin, Origin::Owned(..)) =>
                {
                    let value = if !transfer.pure {
                        transfer.owners.get(origin).cloned()
                    } else {
                        None
                    };
                    let value = value.unwrap_or_else(|| {
                        let value = self.fresh(state, transfer.at, transfer.slot);
                        transfer.slot += 1;
                        if !transfer.pure {
                            transfer.owners.insert(origin.clone(), value.clone());
                        }
                        value
                    });
                    out.join(&value);
                    if transfer.pure {
                        out.view = false;
                    }
                }
                origin => {
                    out.refs.insert(origin.clone());
                }
            }
        }
        for (name, value) in &value.fields {
            out.fields.insert(
                name.clone(),
                self.instantiate(state, value, inputs, transfer, depth + 1),
            );
        }
        out
    }
}
