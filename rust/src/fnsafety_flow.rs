use super::*;

fn joined(exits: Vec<Exit>) -> Vec<Exit> {
    let mut result: Vec<Exit> = vec![];
    for exit in exits {
        if let Some(existing) = result.iter_mut().find(|e| e.control == exit.control) {
            existing.state.join(&exit.state);
            existing.values.resize(
                existing.values.len().max(exit.values.len()),
                Value::default(),
            );
            for (to, from) in existing.values.iter_mut().zip(&exit.values) {
                to.join(from);
            }
        } else {
            result.push(exit);
        }
    }
    result
}

impl Check<'_, '_> {
    pub(super) fn branch_status(
        &mut self,
        procedure: Ref,
        bindings: &Substitutions,
    ) -> Option<bool> {
        let ancestors = self.r.sources[procedure.file]
            .ancestors(procedure.node)
            .collect::<Vec<_>>();
        let mut known = true;
        for ancestor in ancestors {
            let at = Ref {
                node: ancestor,
                ..procedure
            };
            if self.kind(at) != "When" {
                continue;
            }
            let selected = self
                .role(at, "cond")
                .and_then(|cond| self.r.context_condition(cond, bindings));
            let in_body = self.role(at, "body").is_some_and(|body| {
                self.r.sources[procedure.file]
                    .node(body.node)
                    .span
                    .contains(self.r.sources[procedure.file].node(procedure.node).span)
            });
            if selected.is_some_and(|yes| yes != in_body) {
                return Some(false);
            }
            known &= selected.is_some();
        }
        known.then_some(true)
    }
    pub(super) fn procedure(
        &mut self,
        procedure: Ref,
        bindings: Substitutions,
        depth: usize,
    ) -> Summary {
        let key = (procedure, bindings.clone());
        if let Some(summary) = self.summaries.get(&key) {
            crate::profile::count("fn_safety.summary_hits", 1);
            return summary.clone();
        }
        if depth > MAX_DEPTH || !self.active.insert(key.clone()) {
            self.gap(procedure, "recursive summary requires a fixed-point proof");
            return Summary::default();
        }
        let before = self.generation;
        let mut frame = Frame {
            procedure,
            bindings,
            steps: 0,
            uncertain: 0,
            reads: BTreeSet::new(),
        };
        match self.branch_status(procedure, &frame.bindings) {
            Some(false) => {
                self.active.remove(&key);
                let summary = Summary {
                    complete: true,
                    ..Summary::default()
                };
                self.summaries.insert(key, summary.clone());
                crate::profile::count("fn_safety.inactive", 1);
                return summary;
            }
            None => {
                self.gap(
                    procedure,
                    "enclosing compile-time branch is not yet selected",
                );
                frame.uncertain += 1;
            }
            Some(true) => {}
        }
        let mut state = State::default();
        let Some(signature) = self.role(procedure, "type") else {
            self.active.remove(&key);
            self.gap(procedure, "callee signature is unavailable");
            return Summary::default();
        };
        let fields = self.r.fields(signature, "params");
        let mut parameters = vec![];
        if let Some(params) = self.role(signature, "params") {
            let children = self.r.sources[params.file]
                .node(params.node)
                .children
                .iter()
                .copied()
                .collect::<Vec<_>>();
            for field in children {
                parameters.extend(self.group(
                    Ref {
                        node: field,
                        ..params
                    },
                    "names",
                ));
            }
        }
        for (i, name) in parameters.iter().enumerate() {
            let typ = fields
                .get(i)
                .and_then(|(_, typ)| *typ)
                .map(|at| self.r.specialized_type(at, &frame.bindings))
                .unwrap_or(Type::Unknown);
            let value = self.input(&typ, i, 0);
            state.life.insert(Origin::Input(i), LIVE);
            state.env.insert(self.symbol(*name), value);
        }
        if let Some(results) = self.role(signature, "results") {
            let children = self.r.sources[results.file]
                .node(results.node)
                .children
                .iter()
                .copied()
                .collect::<Vec<_>>();
            for field in children {
                for name in self.group(
                    Ref {
                        node: field,
                        ..results
                    },
                    "names",
                ) {
                    let typ = self.typ(name, &frame);
                    let value = self.zero(&typ, 0);
                    state.env.insert(self.symbol(name), value);
                }
            }
        }
        let pure = self.r.sources[procedure.file].node(procedure.node).data["pure"] == true;
        let exits = if let Some(body) = self.role(procedure, "body") {
            if self.r.sources[procedure.file].shared_native {
                self.gap(
                    procedure,
                    "native callee implementation has no reviewed fast safety contract",
                );
                vec![]
            } else {
                self.statement(state, &mut frame, body, pure, depth + 1)
            }
        } else {
            self.gap(procedure, "callee implementation is unavailable");
            vec![]
        };
        let mut summary = Summary {
            terminates: !exits.is_empty() && exits.iter().all(|e| e.control == Control::Stop),
            ..Summary::default()
        };
        for exit in exits {
            if exit.control == Control::Stop {
                continue;
            }
            for value in &exit.values {
                self.valid(&exit.state, value, exit.at, &mut frame);
            }
            summary.results.resize(
                summary.results.len().max(exit.values.len()),
                Value::default(),
            );
            for (out, value) in summary.results.iter_mut().zip(exit.values) {
                out.join(&value);
            }
            for (i, _) in parameters.iter().enumerate() {
                summary
                    .input_life
                    .entry(i)
                    .and_modify(|life| {
                        *life |= exit
                            .state
                            .life
                            .get(&Origin::Input(i))
                            .copied()
                            .unwrap_or(LIVE)
                    })
                    .or_insert(
                        exit.state
                            .life
                            .get(&Origin::Input(i))
                            .copied()
                            .unwrap_or(LIVE),
                    );
            }
        }
        summary.reads = frame.reads;
        summary.complete = before == self.generation;
        self.active.remove(&key);
        self.summaries.insert(key, summary.clone());
        summary
    }
    fn assign(
        &mut self,
        state: &mut State,
        frame: &mut Frame,
        target: Ref,
        value: Value,
        depth: usize,
    ) {
        match self.kind(target) {
            "Ident" => {
                if self.text(target) != "_" {
                    if let Some((root, path)) = self
                        .binding_path(target)
                        .filter(|(_, path)| !path.is_empty())
                    {
                        let mut entry = state.env.get_mut(&root);
                        for field in &path {
                            entry =
                                entry.map(|entry| entry.fields.entry(field.clone()).or_default());
                        }
                        if let Some(entry) = entry {
                            *entry = value;
                        } else {
                            self.gap(target, "using assignment has no resolved local owner");
                        }
                    } else {
                        state.env.insert(self.symbol(target), value);
                    }
                }
            }
            "Selector" => {
                if let (Some(base), Some(field)) =
                    (self.role(target, "expr"), self.role(target, "field"))
                {
                    let mut old = self.expr(state, frame, base, depth + 1);
                    self.valid(
                        state,
                        &Value {
                            refs: old.refs.clone(),
                            ..Value::default()
                        },
                        target,
                        frame,
                    );
                    if !old.refs.is_empty() {
                        self.gap(
                            target,
                            "stores through a shared reference require a heap-field summary",
                        );
                    }
                    old.fields.insert(self.text(field).to_string(), value);
                    self.assign(state, frame, base, old, depth + 1);
                }
            }
            "Index" | "Deref" => {
                self.expr(state, frame, target, depth + 1);
                if !value.refs.is_empty() || !value.fields.is_empty() || value.callable != 0 {
                    self.gap(target, "reference/callback stores through indexed or indirect aliases need a heap summary");
                }
            }
            _ => self.gap(
                target,
                "assignment target is outside the initial flow model",
            ),
        }
    }
    pub(super) fn refine(&mut self, state: &mut State, condition: Ref, yes: bool) -> bool {
        if self.kind(condition) != "Binary" {
            return true;
        }
        let op = self.r.sources[condition.file]
            .node(condition.node)
            .op
            .clone();
        if !matches!(op.as_str(), "==" | "!=") {
            return true;
        }
        if let (Some(left), Some(right)) =
            (self.role(condition, "left"), self.role(condition, "right"))
        {
            for (value, other) in [(left, right), (right, left)] {
                if self.r.native_builtin(other, "nil") {
                    let Some((key, path)) = self.binding_path(value) else {
                        return true;
                    };
                    let mut entry = state.env.get_mut(&key);
                    for field in &path {
                        entry = entry.and_then(|v| v.fields.get_mut(field));
                    }
                    if let Some(v) = entry {
                        if v.callable != 0 {
                            let desired = if yes == (op == "==") { NIL } else { NON_NIL };
                            if v.callable & desired == 0 {
                                return false;
                            }
                            v.callable = desired;
                        }
                    }
                }
            }
        }
        true
    }
    pub(super) fn statement(
        &mut self,
        mut state: State,
        frame: &mut Frame,
        at: Ref,
        tail: bool,
        depth: usize,
    ) -> Vec<Exit> {
        let normal = |state| {
            vec![Exit {
                state,
                control: Control::Next,
                values: vec![],
                at,
            }]
        };
        if state.stopped {
            return vec![Exit {
                state,
                control: Control::Stop,
                values: vec![],
                at,
            }];
        }
        if !self.step(at, frame, &state, depth) {
            return normal(state);
        }
        let kind = self.kind(at).to_string();
        match kind.as_str() {
            "Block" | "Case" => {
                let start = state.deferred.len();
                let children = if kind == "Case" {
                    self.group(at, "body")
                } else {
                    self.r.sources[at.file]
                        .node(at.node)
                        .children
                        .iter()
                        .map(|node| Ref { node: *node, ..at })
                        .collect()
                };
                let mut exits = normal(state);
                for (i, child) in children.iter().enumerate() {
                    let mut next = vec![];
                    for exit in exits {
                        if exit.control != Control::Next {
                            next.push(exit);
                        } else {
                            next.extend(self.statement(
                                exit.state,
                                frame,
                                *child,
                                tail && i + 1 == children.len(),
                                depth + 1,
                            ));
                        }
                    }
                    exits = joined(next);
                }
                for exit in &mut exits {
                    if exit.control == Control::Stop {
                        continue;
                    }
                    while exit.state.deferred.len() > start {
                        let (cleanup, _) = exit.state.deferred.pop().unwrap();
                        if self.kind(cleanup) == "Ident" {
                            let value = exit
                                .state
                                .env
                                .get(&self.symbol(cleanup))
                                .cloned()
                                .unwrap_or_default();
                            self.release(&mut exit.state, &value, cleanup, frame);
                        } else {
                            let actions = self.statement(
                                exit.state.clone(),
                                frame,
                                cleanup,
                                false,
                                depth + 1,
                            );
                            if let Some(action) = actions.first() {
                                exit.state = action.state.clone();
                            }
                        }
                    }
                    let locals = exit
                        .state
                        .life
                        .keys()
                        .filter(|origin| {
                            if let Origin::Stack(origin) = origin {
                                let source = &self.r.sources[origin.file];
                                source
                                    .ancestors(origin.node)
                                    .find(|node| source.node(*node).kind == "Block")
                                    == Some(at.node)
                                    && origin.file == at.file
                            } else {
                                false
                            }
                        })
                        .cloned()
                        .collect::<Vec<_>>();
                    for local in locals {
                        exit.state.life.insert(local, DEAD);
                    }
                }
                exits
            }
            "Declaration" => {
                let names = self.group(at, "names");
                let expressions = self.group(at, "values");
                let mut values = vec![];
                for expression in expressions {
                    values.extend(self.values(&mut state, frame, expression, depth + 1));
                }
                let scoped = crate::scoped::names(&self.r.sources[at.file], at.node);
                for (i, name) in names.iter().enumerate() {
                    let typ = self.typ(*name, frame);
                    let value = values.get(i).cloned().unwrap_or_else(|| self.zero(&typ, 0));
                    state.env.insert(self.symbol(*name), value);
                    if scoped.contains(&name.node) {
                        state.deferred.push((*name, Value::default()));
                    }
                }
                normal(state)
            }
            "Assignment" => {
                let mut values = vec![];
                for expression in self.group(at, "rhs") {
                    values.extend(self.values(&mut state, frame, expression, depth + 1));
                }
                let compound = self.r.sources[at.file].node(at.node).op != "=";
                for (target, value) in self.group(at, "lhs").into_iter().zip(values) {
                    if compound {
                        let old = self.expr(&mut state, frame, target, depth + 1);
                        self.valid(&state, &old, at, frame);
                    }
                    self.assign(&mut state, frame, target, value, depth + 1);
                }
                normal(state)
            }
            "Return" | "ExpressionStatement" => {
                let returning = kind == "Return" || tail;
                let expressions = if kind == "Return" {
                    self.group(at, "values")
                } else {
                    self.role(at, "expr").into_iter().collect()
                };
                let mut values = vec![];
                for expression in expressions {
                    values.extend(self.values(&mut state, frame, expression, depth + 1));
                }
                if kind == "Return" && values.is_empty() {
                    if let Some(signature) = self
                        .role(frame.procedure, "type")
                        .and_then(|s| self.role(s, "results"))
                    {
                        let children = self.r.sources[signature.file]
                            .node(signature.node)
                            .children
                            .iter()
                            .copied()
                            .collect::<Vec<_>>();
                        for field in children {
                            for name in self.group(
                                Ref {
                                    node: field,
                                    ..signature
                                },
                                "names",
                            ) {
                                values.push(
                                    state
                                        .env
                                        .get(&self.symbol(name))
                                        .cloned()
                                        .unwrap_or_default(),
                                );
                            }
                        }
                    }
                }
                if returning {
                    if frame.uncertain != 0 {
                        for value in &mut values {
                            value.unknown = true;
                        }
                    }
                    for value in &values {
                        self.valid(&state, value, at, frame);
                    }
                }
                let control = if state.stopped {
                    Control::Stop
                } else if returning {
                    Control::Return
                } else {
                    Control::Next
                };
                vec![Exit {
                    state,
                    control,
                    values: if returning { values } else { vec![] },
                    at,
                }]
            }
            "Defer" => {
                if let Some(stmt) = self.role(at, "stmt") {
                    state.deferred.push((stmt, Value::default()));
                }
                normal(state)
            }
            "If" | "When" => {
                if let Some(init) = self.role(at, "init") {
                    if let Some(exit) = self
                        .statement(state.clone(), frame, init, false, depth + 1)
                        .first()
                    {
                        state = exit.state.clone();
                    }
                }
                let condition = self.role(at, "cond");
                if let Some(condition) = condition {
                    self.expr(&mut state, frame, condition, depth + 1);
                }
                let selected =
                    condition.and_then(|cond| self.r.context_condition(cond, &frame.bindings));
                if kind == "When" && selected.is_none() {
                    self.gap(at, "compile-time branch is not yet selected");
                    frame.uncertain += 1;
                }
                let mut exits = vec![];
                for (yes, role) in [(true, "body"), (false, "else")] {
                    if selected.is_some_and(|selected| selected != yes) {
                        continue;
                    }
                    let mut branch = state.clone();
                    if condition.is_some_and(|condition| !self.refine(&mut branch, condition, yes))
                    {
                        continue;
                    }
                    if let Some(body) = self.role(at, role) {
                        exits.extend(self.statement(branch, frame, body, tail, depth + 1));
                    } else {
                        exits.extend(normal(branch));
                    }
                }
                if kind == "When" && selected.is_none() {
                    frame.uncertain -= 1;
                }
                if let Some(first) = exits.first() {
                    if exits
                        .iter()
                        .any(|exit| exit.state.deferred != first.state.deferred)
                    {
                        self.gap(
                            at,
                            "conditional defer registration needs path-dependent cleanup facts",
                        );
                    }
                }
                joined(exits)
            }
            "For" | "Range" => {
                // The range source is evaluated once below, including its
                // helper effects. Replaying it would invent repeated cleanup.
                for role in ["init", "cond"] {
                    if let Some(expr) = self.role(at, role) {
                        if self.kind(expr) == "Declaration" {
                            if let Some(exit) = self
                                .statement(state.clone(), frame, expr, false, depth + 1)
                                .first()
                            {
                                state = exit.state.clone();
                            }
                        } else {
                            self.expr(&mut state, frame, expr, depth + 1);
                        }
                    }
                }
                if let Some(expr) = self.role(at, "expr") {
                    let value = self.expr(&mut state, frame, expr, depth + 1);
                    self.valid(&state, &value, at, frame);
                    let item = value.fields.get("[]").cloned().unwrap_or(value);
                    for (i, name) in self.group(at, "names").into_iter().enumerate() {
                        state.env.insert(
                            self.symbol(name),
                            if i == 0 {
                                item.clone()
                            } else {
                                Value::default()
                            },
                        );
                    }
                }
                let initial = state.clone();
                let mut back = state;
                let mut outward = vec![];
                let mut stable = false;
                for _ in 0..4 {
                    let old = back.clone();
                    if let Some(body) = self.role(at, "body") {
                        for exit in self.statement(back.clone(), frame, body, false, depth + 1) {
                            match exit.control {
                                Control::Next | Control::Continue => back.join(&exit.state),
                                Control::Break => outward.extend(normal(exit.state)),
                                _ => outward.push(exit),
                            }
                        }
                    }
                    if let Some(post) = self.role(at, "post") {
                        if let Some(exit) = self
                            .statement(back.clone(), frame, post, false, depth + 1)
                            .first()
                        {
                            back.join(&exit.state);
                        }
                    }
                    back.join(&initial);
                    if back == old {
                        stable = true;
                        break;
                    }
                }
                if !stable {
                    self.gap(at, "loop ownership state did not converge in four passes");
                }
                outward.extend(normal(back));
                joined(outward)
            }
            "Switch" | "CompileSwitch" | "TypeSwitch" => {
                // Case selection and fallthrough need their own CFG rules.
                // Leave the whole operation explicitly unverified; executing
                // all arms in one abstract state would invent false defects.
                self.gap(
                    at,
                    "switch path coverage and case correlations are not yet certified",
                );
                normal(state)
            }
            "Branch" => vec![Exit {
                state,
                values: vec![],
                at,
                control: match self.r.sources[at.file].node(at.node).op.as_str() {
                    "break" => Control::Break,
                    "continue" => Control::Continue,
                    _ => {
                        self.gap(at, "branch transfer is unresolved");
                        Control::Stop
                    }
                },
            }],
            "Other"
                if matches!(
                    self.text(at).trim().trim_end_matches(';').trim(),
                    "break" | "continue"
                ) =>
            {
                let control = if self.text(at).trim().starts_with("break") {
                    Control::Break
                } else {
                    Control::Continue
                };
                vec![Exit {
                    state,
                    control,
                    values: vec![],
                    at,
                }]
            }
            "Empty" | "Using" => normal(state),
            _ => {
                self.gap(
                    at,
                    &format!("{kind} statements are outside the initial flow model"),
                );
                normal(state)
            }
        }
    }
}
