//! Checking-only expressions. User reads and writes stay in their enclosing
//! procedure; compiler type witnesses are unevaluated and separately mapped.
use super::*;

struct Expr {
    before: Vec<Code>,
    value: Code,
}
struct View<'l, 'a, 'r> {
    lower: &'l mut Lower<'a, 'r>,
    serial: usize,
    contexts: HashMap<NodeId, (Option<Hint>, bool)>,
}

impl Lower<'_, '_> {
    pub(crate) fn native_checking_view(&mut self) -> Result<Emitted> {
        // Formatting lifetimes and pure-call scratch are runtime lowering
        // details. The checking view retains the actual user call arguments.
        self.fstring_plans.clear();
        let mut view = View {
            lower: self,
            serial: 0,
            contexts: HashMap::new(),
        };
        let body = view.node(NodeId(0))?;
        let mut parts = vec![body];
        if view.lower.default {
            parts.push(view.synthetic(format!(
                "\n{}default_value::proc \"contextless\"(value:$T)->T{{return value}}\n",
                view.lower.prefix
            )));
        }
        if view.lower.meta {
            parts.push(view.synthetic(format!(
                "\nimport {}meta \"base:intrinsics\"\n",
                view.lower.prefix
            )));
        }
        if view.lower.builtin {
            parts.push(view.synthetic(format!(
                "\nimport {}builtin \"base:builtin\"\n",
                view.lower.prefix
            )));
        }
        for (alias, path) in &view.lower.modules {
            parts.push(view.synthetic(format!("\nimport {alias} {}\n", json!(path))));
        }
        Ok(Code::concat(parts).finish())
    }
}

impl View<'_, '_, '_> {
    fn s(&self) -> &Source {
        self.lower.source
    }
    fn g(&self, text: impl Into<String>, id: NodeId) -> Code {
        self.lower.gen(text, self.s().node(id).span, "native-check")
    }
    fn synthetic(&self, text: impl Into<String>) -> Code {
        self.lower
            .gen(text, Span::new(0, 0), "native-check-support")
    }
    fn name(&mut self) -> String {
        self.serial += 1;
        format!("{}vet_{}", self.lower.prefix, self.serial)
    }
    fn copy(&self, id: NodeId) -> Code {
        self.lower.copy(self.s().node(id).span)
    }
    fn join(&self, id: NodeId, before: Vec<Code>, value: Code) -> Code {
        if before.is_empty() {
            return value;
        }
        let start = self.s().node(id).span.start;
        let column = self.s().raw[..start]
            .rsplit('\n')
            .next()
            .unwrap_or("")
            .chars()
            .count();
        Code::concat(
            before
                .into_iter()
                .chain([self.g(" ".repeat(column), id), value]),
        )
    }
    fn template(&self, id: NodeId, mut replacements: Vec<(Span, Code)>) -> Result<Code> {
        let span = self.s().node(id).span;
        for (edit, code) in &self.lower.edits {
            if span.contains(*edit) && !replacements.iter().any(|(at, _)| at.contains(*edit)) {
                replacements.push((*edit, code.clone()));
            }
        }
        replacements.sort_by_key(|(s, _)| (s.start, s.end));
        let mut cursor = span.start;
        let mut parts = vec![];
        for (at, code) in replacements {
            if at.start < cursor {
                continue;
            }
            parts.push(self.lower.copy(Span::new(cursor, at.start)));
            parts.push(code);
            cursor = at.end;
        }
        parts.push(self.lower.copy(Span::new(cursor, span.end)));
        Ok(Code::concat(parts))
    }
    fn expression_kind(&self, id: NodeId) -> bool {
        matches!(
            self.s().node(id).kind.as_str(),
            "Ident"
                | "Literal"
                | "Call"
                | "Binary"
                | "Unary"
                | "Deref"
                | "Selector"
                | "ImplicitSelector"
                | "Index"
                | "Slice"
                | "Paren"
                | "Cast"
                | "AutoCast"
                | "Compound"
                | "Interpolation"
                | "Comprehension"
                | "OrElse"
                | "OrPanic"
                | "OrReturn"
                | "OrBranch"
                | "TypeAssertion"
                | "Ternary"
                | "Run"
                | "Runtime"
                | "WhenExpr"
                | "SwitchExpr"
                | "CompileSwitchExpr"
                | "FallbackList"
                | "FieldValue"
        )
    }
    fn child_contexts(&mut self, id: NodeId) -> Result<()> {
        let (hint, constant) = self.contexts.get(&id).cloned().unwrap_or((None, false));
        let readonly =
            self.s().node(id).kind == "Declaration" && self.lower.readonly_data(id).is_some();
        let contexts = self
            .lower
            .child_contexts(id, hint.clone(), constant, false, readonly)?;
        self.contexts.extend(contexts);
        if matches!(
            self.s().node(id).kind.as_str(),
            "Ternary" | "WhenExpr" | "OrElse"
        ) {
            for role in ["x", "y"] {
                if let Some(child) = self.s().role(id, role) {
                    self.contexts.insert(child, (hint.clone(), constant));
                }
            }
        }
        Ok(())
    }
    fn hint(&self, id: NodeId) -> Option<Hint> {
        let mut hint = self.contexts.get(&id).and_then(|(hint, _)| hint.clone());
        if let Some(hint) = &mut hint {
            if matches!(hint.semantic, Some(Type::Array { .. })) {
                hint.plain_array = true;
            }
        }
        hint
    }
    fn node(&mut self, id: NodeId) -> Result<Code> {
        let kind = self.s().node(id).kind.as_str().to_owned();
        if self.expression_kind(id) {
            let e = self.expr(id)?;
            return Ok(self.join(id, e.before, e.value));
        }
        self.child_contexts(id)?;
        if matches!(kind.as_str(), "Attribute" | "SetType") {
            return self.lower.render(id, None, true);
        }
        if kind == "When" {
            if let Some(cond) = self.s().role(id, "cond") {
                if let Some(selected) = self.lower.resolver.condition(Ref {
                    file: self.lower.file,
                    node: cond,
                }) {
                    let inactive = self.s().role(id, if selected { "else" } else { "body" });
                    let mut edits = vec![];
                    for child in self.s().node(id).children.to_vec() {
                        let code = if Some(child) == inactive {
                            self.g("{}", child)
                        } else if child == cond {
                            self.lower.render(child, None, true)?
                        } else {
                            self.node(child)?
                        };
                        edits.push((self.s().node(child).span, code));
                    }
                    return self.template(id, edits);
                }
            }
        }
        if matches!(
            kind.as_str(),
            "If" | "For" | "Range" | "Switch" | "TypeSwitch" | "Defer"
        ) {
            return self.control(id);
        }
        let children = self.s().node(id).children.to_vec();
        let values = if matches!(kind.as_str(), "Declaration" | "Return") {
            self.s().group(id, "values").to_vec()
        } else if kind == "Assignment" {
            self.s().group(id, "rhs").to_vec()
        } else {
            vec![]
        };
        let mut addresses = vec![];
        let mut address_edits = vec![];
        if kind == "Assignment"
            && values.iter().any(|v| {
                self.s()
                    .descendants(*v)
                    .chain(std::iter::once(*v))
                    .any(|n| self.s().node(n).kind == "Comprehension")
            })
        {
            for lhs in self.s().group(id, "lhs").to_vec() {
                if self.s().node(lhs).kind == "Ident" {
                    continue;
                }
                let e = self.expr(lhs)?;
                addresses.extend(e.before);
                let name = self.name();
                if self.s().node(lhs).kind == "Deref" {
                    let pointer = self
                        .s()
                        .role(lhs, "expr")
                        .or_else(|| self.s().node(lhs).children.first().copied())
                        .unwrap();
                    let value = self.expr(pointer)?;
                    addresses.extend(value.before);
                    addresses.extend([
                        self.g(format!("{name} := "), pointer),
                        value.value,
                        self.g("\n", pointer),
                    ]);
                } else {
                    addresses.extend([
                        self.g(format!("{name} := &("), lhs),
                        e.value,
                        self.g(")\n", lhs),
                    ]);
                }
                address_edits.push((lhs, self.g(format!("{name}^"), lhs)));
            }
        }
        let (mut before, values) = self.ordered(values)?;
        addresses.append(&mut before);
        before = addresses;
        let mut edits = values
            .iter()
            .map(|(id, code)| (self.s().node(*id).span, code.clone()))
            .collect::<Vec<_>>();
        for child in children {
            if let Some((_, code)) = address_edits.iter().find(|(lhs, _)| *lhs == child) {
                edits.push((self.s().node(child).span, code.clone()));
                continue;
            }
            if values.iter().any(|(value, _)| *value == child) {
                continue;
            }
            let code = if self.expression_kind(child) {
                let e = self.expr(child)?;
                before.extend(e.before);
                e.value
            } else {
                self.node(child)?
            };
            edits.push((self.s().node(child).span, code));
        }
        if matches!(kind.as_str(), "Block" | "File" | "Case") {
            let helpers = self.lower.helper_declarations(id);
            if !helpers.is_empty() {
                let support = self.synthetic(Code::concat(helpers).finish().text);
                let offset = if kind == "File" {
                    self.s().node(id).span.end
                } else if kind == "Case" {
                    self.s()
                        .node(id)
                        .spans
                        .get("colon")
                        .map(|s| s.end)
                        .unwrap_or(self.s().node(id).span.start)
                } else {
                    self.s().node(id).span.start + 1
                };
                edits.push((Span::new(offset, offset), support));
            }
        }
        let mut code = self.template(id, edits)?;
        if kind == "Declaration" && crate::unused_attributes::marked(self.s(), id) {
            let n = self.s().node(id);
            let local = self
                .s()
                .ancestors(id)
                .any(|owner| self.s().node(owner).kind == "Procedure");
            if local || !n.mutable && !n.immutable {
                let mut parts = vec![code];
                for name in self.s().group(id, "names") {
                    // Count a marked value as used without introducing a
                    // read that would trip uninitialized/atomic-access vet.
                    let value = n.mutable || n.immutable;
                    parts.extend([
                        self.g(if value { "\n_ :: type_of(" } else { "\n_ :: " }, *name),
                        self.copy(*name),
                        self.g(if value { ")\n" } else { "\n" }, *name),
                    ]);
                }
                code = Code::concat(parts);
            }
        }
        if let Some(helpers) = self.lower.declaration_helpers.get(&id) {
            code = Code::concat(std::iter::once(code).chain(helpers.iter().cloned()));
        }
        Ok(self.join(id, before, code))
    }
    fn statements(&self, id: NodeId) -> bool {
        std::iter::once(id)
            .chain(self.s().descendants(id))
            .any(|n| {
                matches!(
                    self.s().node(n).kind.as_str(),
                    "Comprehension" | "SwitchExpr" | "CompileSwitchExpr"
                ) || self.lower.evaluated.contains_key(&n)
            })
    }
    fn iteration_input(&mut self, loop_: NodeId, input: NodeId) -> Result<Expr> {
        if let Some(annotation) = self.s().role(loop_, "value_type") {
            if let Some(interval) = self.lower.comp_interval(input) {
                let low = self.s().role(interval, "left").unwrap();
                let high = self.s().role(interval, "right").unwrap();
                let typ = self.copy(annotation);
                let (before, bounds) = self.ordered(vec![low, high])?;
                let value = Code::concat([
                    self.g("(", input),
                    typ.clone(),
                    self.g("(", low),
                    bounds[0].1.clone(),
                    self.g(format!("){}", self.s().node(interval).op), input),
                    typ,
                    self.g("(", high),
                    bounds[1].1.clone(),
                    self.g("))", input),
                ]);
                return Ok(Expr { before, value });
            }
        }
        let mut source = self.expr(input)?;
        if matches!(self.lower.typ(input), Type::Set(_)) {
            source.value =
                Code::concat([self.g("(", input), source.value, self.g(").table", input)]);
        }
        Ok(source)
    }
    fn selected_switch(
        &mut self,
        id: NodeId,
        selector: Option<NodeId>,
        cases: Vec<(Vec<NodeId>, Code)>,
    ) -> Result<Code> {
        let matched = self.name();
        let selector_name = self.name();
        let mut code = vec![];
        if let Some(selector) = selector {
            let e = self.expr(selector)?;
            code.extend(e.before);
            code.extend([
                self.g(format!("{selector_name} := "), selector),
                e.value,
                self.g("\n", selector),
            ]);
        } else {
            code.push(self.g(format!("{selector_name} := true\n"), id));
        }
        code.push(self.g(format!("{matched} := -1\n"), id));
        let mut fallback = None;
        for (index, (matches, _)) in cases.iter().enumerate() {
            if matches.is_empty() {
                fallback = Some(index);
            }
            for operand in matches {
                code.push(self.g(format!("if {matched}<0 {{\n"), *operand));
                let value = if let Some(interval) = self.lower.comp_interval(*operand) {
                    let low = self.s().role(interval, "left").unwrap();
                    let high = self.s().role(interval, "right").unwrap();
                    let (before, bounds) = self.ordered(vec![low, high])?;
                    code.extend(before);
                    Code::concat([
                        self.g(format!("{selector_name} >= ("), *operand),
                        bounds[0].1.clone(),
                        self.g(
                            format!(
                                ") && {selector_name} {} (",
                                if self.s().node(interval).op == "..=" {
                                    "<="
                                } else {
                                    "<"
                                }
                            ),
                            *operand,
                        ),
                        bounds[1].1.clone(),
                        self.g(")", *operand),
                    ])
                } else {
                    let e = self.expr(*operand)?;
                    code.extend(e.before);
                    Code::concat([
                        self.g(format!("{selector_name} == ("), *operand),
                        e.value,
                        self.g(")", *operand),
                    ])
                };
                code.extend([
                    self.g("if ", *operand),
                    value,
                    self.g(format!(" {{{matched}={index}}}\n}}\n"), *operand),
                ]);
            }
        }
        if let Some(index) = fallback {
            code.push(self.g(format!("if {matched}<0 {{{matched}={index}}}\n"), id));
        }
        if let Some(label) = self.s().role(id, "label") {
            code.extend([self.copy(label), self.g(": ", id)]);
        }
        code.push(self.g(format!("switch {matched} {{\n"), id));
        for (index, (_, body)) in cases.into_iter().enumerate() {
            code.extend([
                self.g(format!("case {index}:\n"), id),
                body,
                self.g("\n", id),
            ]);
        }
        code.push(self.g("}\n", id));
        Ok(Code::concat(code))
    }
    fn control(&mut self, id: NodeId) -> Result<Code> {
        let kind = self.s().node(id).kind.as_str().to_owned();
        if kind == "Defer" {
            let child = self
                .s()
                .node(id)
                .children
                .first()
                .copied()
                .ok_or_else(|| Error::new("defer body missing"))?;
            return Ok(Code::concat([
                self.g("defer {\n", id),
                self.node(child)?,
                self.g("\n}", id),
            ]));
        }
        let init = self.s().role(id, "init");
        let initial = if let Some(init) = init {
            Some(self.node(init)?)
        } else {
            None
        };
        if kind == "TypeSwitch" {
            let expression = self.s().role(id, "expr").unwrap();
            let e = self.expr(expression)?;
            let body = self.s().role(id, "body").unwrap();
            let body_code = self.node(body)?;
            // The binding is an AST assignment header, not a statement to
            // expand in place. Evaluate its source before entering the switch.
            let code = self.template(
                id,
                vec![
                    (self.s().node(expression).span, e.value),
                    (self.s().node(body).span, body_code),
                ],
            )?;
            return Ok(self.join(id, e.before, code));
        }
        if kind == "If" {
            let cond = self.s().role(id, "cond").unwrap();
            let e = self.expr(cond)?;
            let body = self.s().role(id, "body").unwrap();
            let body = self.node(body)?;
            let otherwise = if let Some(other) = self.s().role(id, "else") {
                Some(self.node(other)?)
            } else {
                None
            };
            if e.before.is_empty() && !init.is_some_and(|i| self.statements(i)) {
                let mut edits = vec![
                    (self.s().node(cond).span, e.value),
                    (self.s().node(self.s().role(id, "body").unwrap()).span, body),
                ];
                if let Some(init) = init {
                    edits.push((self.s().node(init).span, initial.unwrap()));
                }
                if let Some(other) = self.s().role(id, "else") {
                    edits.push((self.s().node(other).span, otherwise.unwrap()));
                }
                return self.template(id, edits);
            }
            let mut code = vec![self.g("{\n", id)];
            if let Some(initial) = initial {
                code.extend([initial, self.g("\n", id)]);
            }
            code.extend(e.before);
            code.extend([self.g("if ", id), e.value, body]);
            if let Some(other) = otherwise {
                code.extend([self.g(" else ", id), other]);
            }
            code.push(self.g("\n}", id));
            return Ok(Code::concat(code));
        }
        if kind == "For" {
            let cond = self.s().role(id, "cond");
            let post = self.s().role(id, "post");
            let body = self.s().role(id, "body").unwrap();
            let condition = if let Some(cond) = cond {
                self.expr(cond)?
            } else {
                Expr {
                    before: vec![],
                    value: self.g("true", id),
                }
            };
            // Native for headers accept ordinary single statements. A post
            // with a construction must run at the next iteration's entry so
            // every continue still executes it and every break skips it.
            let post_code = if let Some(post) = post {
                Some(self.node(post)?)
            } else {
                None
            };
            let body_code = self.node(body)?;
            if condition.before.is_empty()
                && !init.is_some_and(|i| self.statements(i))
                && !post.is_some_and(|p| self.statements(p))
            {
                let mut edits = vec![(self.s().node(body).span, body_code)];
                if let Some(cond) = cond {
                    edits.push((self.s().node(cond).span, condition.value));
                }
                if let Some(init) = init {
                    edits.push((self.s().node(init).span, initial.unwrap()));
                }
                if let Some(post) = post {
                    edits.push((self.s().node(post).span, post_code.unwrap()));
                }
                return self.template(id, edits);
            }
            let first = self.name();
            let mut code = vec![self.g("{\n", id)];
            if let Some(initial) = initial {
                code.extend([initial, self.g("\n", id)]);
            }
            if post_code.is_some() {
                code.push(self.g(format!("{first} := true\n"), id));
            }
            if let Some(label) = self.s().role(id, "label") {
                code.extend([self.copy(label), self.g(": ", id)]);
            }
            code.push(self.g("for {\n", id));
            if let Some(post) = post_code {
                code.extend([
                    self.g(format!("if {first} {{{first}=false}} else {{\n"), id),
                    post,
                    self.g("\n}\n", id),
                ]);
            }
            code.extend(condition.before);
            code.extend([
                self.g("if !(", id),
                condition.value,
                self.g(") {break}\n", id),
                body_code,
                self.g("\n}\n}", id),
            ]);
            return Ok(Code::concat(code));
        }
        if kind == "Switch" {
            let body = self.s().role(id, "body").unwrap();
            let case_ids = self.s().node(body).children.to_vec();
            let selector = self.s().role(id, "expr");
            if selector.is_some_and(|s| self.statements(s))
                || case_ids.iter().any(|c| {
                    self.s()
                        .group(*c, "types")
                        .iter()
                        .any(|m| self.statements(*m))
                })
            {
                let mut cases = vec![];
                for case in case_ids {
                    let matches = self.s().group(case, "types").to_vec();
                    let mut statements = vec![];
                    for statement in self.s().group(case, "body").to_vec() {
                        statements.extend([self.node(statement)?, self.g("\n", statement)]);
                    }
                    let helpers = self.lower.helper_declarations(case);
                    let support = self.synthetic(Code::concat(helpers).finish().text);
                    cases.push((
                        matches,
                        Code::concat(std::iter::once(support).chain(statements)),
                    ));
                }
                let mut code = vec![self.g("{\n", id)];
                if let Some(initial) = initial {
                    code.extend([initial, self.g("\n", id)]);
                }
                code.push(self.selected_switch(id, selector, cases)?);
                code.push(self.g("\n}", id));
                return Ok(Code::concat(code));
            }
        }
        if kind == "Range" {
            let input = self.s().role(id, "expr").unwrap();
            let source = self.iteration_input(id, input)?;
            let body_id = self.s().role(id, "body").unwrap();
            let body = self.node(body_id)?;
            if self.s().node(id).op == "unroll"
                || source.before.is_empty()
                    && !init.is_some_and(|i| self.statements(i))
                    && self.s().role(id, "value_type").is_none()
                    && self.s().role(id, "index_type").is_none()
                    && !matches!(self.lower.typ(input), Type::Set(_))
            {
                let mut edits = vec![
                    (self.s().node(input).span, source.value),
                    (self.s().node(body_id).span, body),
                ];
                if let Some(init) = init {
                    edits.push((self.s().node(init).span, initial.unwrap()));
                }
                let code = self.template(id, edits)?;
                return Ok(self.join(id, source.before, code));
            }
            let mut code = vec![];
            let scoped = init.is_some() || !source.before.is_empty();
            if scoped {
                code.push(self.g("{\n", id));
            }
            if let Some(initial) = initial {
                code.extend([initial, self.g("\n", id)]);
            }
            code.extend(source.before);
            if let Some(label) = self.s().role(id, "label") {
                code.extend([self.copy(label), self.g(": ", id)]);
            }
            code.push(self.g("for ", id));
            let index = self.s().role(id, "index_type");
            let native_index = self.name();
            for (i, binding) in self.s().group(id, "names").iter().enumerate() {
                if i > 0 {
                    code.push(self.g(",", id));
                }
                code.push(if i == 1 && index.is_some() {
                    self.g(native_index.clone(), *binding)
                } else {
                    self.copy(*binding)
                });
            }
            code.extend([self.g(" in ", id), source.value, self.g(" {\n", id)]);
            if let Some(annotation) = index {
                if let Some(binding) = self.s().group(id, "names").get(1).copied() {
                    code.extend([
                        self.copy(binding),
                        self.g(
                            if self.s().text(binding) == "_" {
                                " = "
                            } else {
                                " := "
                            },
                            binding,
                        ),
                        self.copy(annotation),
                        self.g(format!("({native_index})\n"), binding),
                    ]);
                }
            }
            code.extend([body, self.g("\n}", id)]);
            if scoped {
                code.push(self.g("\n}", id));
            }
            return Ok(Code::concat(code));
        }
        let mut edits = vec![];
        let mut before = vec![];
        for child in self.s().node(id).children.to_vec() {
            let code = if Some(child) == init {
                initial.clone().unwrap()
            } else if self.expression_kind(child) {
                let e = self.expr(child)?;
                before.extend(e.before);
                e.value
            } else {
                self.node(child)?
            };
            edits.push((self.s().node(child).span, code));
        }
        if let Some(init) = init.filter(|i| !before.is_empty() || self.statements(*i)) {
            let start = self.s().node(init).span.start;
            let end = self.s().node(init).span.end;
            let tail = &self.s().raw[end..self.s().node(id).span.end];
            let end = end + tail.find(';').map(|i| i + 1).unwrap_or(0);
            edits.retain(|(span, _)| !self.s().node(init).span.contains(*span));
            edits.push((Span::new(start, end), self.g("", init)));
            let body = self.template(id, edits)?;
            return Ok(Code::concat([
                self.g("{\n", id),
                initial.unwrap(),
                self.g("\n", id),
                Code::concat(before),
                body,
                self.g("\n}", id),
            ]));
        }
        let code = self.template(id, edits)?;
        Ok(self.join(id, before, code))
    }
    fn shape(&mut self, id: NodeId) -> Result<Code> {
        let hint = self.hint(id);
        let query = self.lower.render(id, hint, false)?.finish().text;
        Ok(self.lower.gen(
            format!("type_of({query})"),
            self.s().node(id).span,
            "native-check-shape",
        ))
    }
    fn ordered(&mut self, ids: Vec<NodeId>) -> Result<(Vec<Code>, Vec<(NodeId, Code)>)> {
        let mut before = vec![];
        let mut values: Vec<(NodeId, Code)> = vec![];
        let mut frozen = HashSet::new();
        for id in ids {
            let e = self.expr(id)?;
            if !e.before.is_empty() {
                for (previous, code) in &mut values {
                    if !self.stable(*previous) && frozen.insert(*previous) {
                        let name = self.name();
                        before.extend([
                            self.g(format!("{name} := "), *previous),
                            code.clone(),
                            self.g("\n", *previous),
                        ]);
                        *code = self.g(name, *previous);
                    }
                }
            }
            before.extend(e.before);
            values.push((id, e.value));
        }
        Ok((before, values))
    }
    fn plain(&mut self, id: NodeId, children: Vec<(NodeId, Code)>) -> Result<Code> {
        let mut saved = vec![];
        for (child, code) in children {
            saved.push((child, self.lower.switch_replacements.insert(child, code)));
        }
        let hint = self.hint(id);
        let constant = self
            .contexts
            .get(&id)
            .is_some_and(|(_, constant)| *constant);
        let result = self.lower.render(id, hint, constant);
        for (child, old) in saved {
            if let Some(old) = old {
                self.lower.switch_replacements.insert(child, old);
            } else {
                self.lower.switch_replacements.remove(&child);
            }
        }
        result
    }
    fn stable(&self, id: NodeId) -> bool {
        let n = self.s().node(id);
        if matches!(
            n.kind.as_str(),
            "Literal" | "ImplicitSelector" | "Procedure" | "ProcedureGroup"
        ) {
            return true;
        }
        if matches!(n.kind.as_str(), "Ident" | "Selector") {
            if n.kind == "Ident"
                && self.lower.resolver.native_builtin(
                    Ref {
                        file: self.lower.file,
                        node: id,
                    },
                    self.s().text(id),
                )
            {
                return true;
            }
            return self
                .lower
                .resolver
                .symbol(Ref {
                    file: self.lower.file,
                    node: id,
                })
                .is_some_and(|index| {
                    let s = &self.lower.resolver.symbols[index];
                    s.immutable || s.constant || s.parameter
                });
        }
        false
    }
    fn expr(&mut self, id: NodeId) -> Result<Expr> {
        self.child_contexts(id)?;
        let kind = self.s().node(id).kind.as_str().to_owned();
        if kind == "FieldValue" {
            let value = self.s().role(id, "value").unwrap();
            let e = self.expr(value)?;
            let code = self.template(id, vec![(self.s().node(value).span, e.value)])?;
            return Ok(Expr {
                before: e.before,
                value: code,
            });
        }
        if kind == "Call" {
            if let Some(callee) = self.s().role(id, "callee") {
                if [
                    "type_of",
                    "size_of",
                    "align_of",
                    "offset_of",
                    "offset_of_member",
                    "typeid_of",
                ]
                .iter()
                .any(|name| {
                    self.lower.resolver.native_builtin_alias(
                        Ref {
                            file: self.lower.file,
                            node: callee,
                        },
                        name,
                    )
                }) {
                    return Ok(Expr {
                        before: vec![],
                        value: self.lower.render(id, None, false)?,
                    });
                }
            }
        }
        if let Some(evaluated) = self.lower.evaluated.remove(&id) {
            let original = if kind == "Run" {
                let child = self
                    .s()
                    .role(id, "expr")
                    .or_else(|| self.s().node(id).children.first().copied())
                    .unwrap();
                self.expr(child)
            } else {
                self.expr(id)
            };
            self.lower.evaluated.insert(id, evaluated);
            let e = original?;
            let global = !self
                .s()
                .ancestors(id)
                .any(|a| self.s().node(a).kind == "Procedure");
            let mut before = vec![];
            if global {
                let serial = self.name();
                let name = format!("{}vet_validate_{serial}", self.lower.prefix);
                before.push(self.g(format!("@(require)\n{name} :: proc() {{\n"), id));
            } else {
                before.push(self.g("if false {\n", id));
            }
            before.extend(e.before);
            before.extend([self.g("_ = ", id), e.value, self.g("\n}\n", id)]);
            return Ok(Expr {
                before,
                value: self.lower.render(id, None, true)?,
            });
        }
        if matches!(kind.as_str(), "SwitchExpr" | "CompileSwitchExpr")
            && self.lower.switch_problem(id, 1)?.is_none()
        {
            let name = self.name();
            let shape = self.shape(id)?;
            let mut before = vec![self.g(format!("{name}: "), id), shape, self.g("\n", id)];
            let mut branches = HashMap::new();
            for case in self.lower.cases(id)? {
                let result = case.result;
                let e = self.expr(result)?;
                let mut parts = e.before;
                if self.lower.diverging(result) {
                    parts.extend([e.value, self.g("\n", result)]);
                } else {
                    parts.extend([
                        self.g(format!("{name} = "), result),
                        e.value,
                        self.g("\n", result),
                    ]);
                }
                branches.insert(result, Code::concat(parts));
            }
            let selector = self.s().role(id, "selector");
            let cases = self.lower.cases(id)?;
            if selector.is_some_and(|s| self.statements(s))
                || cases
                    .iter()
                    .any(|c| c.matches.iter().any(|m| self.statements(*m)))
            {
                before.push(
                    self.selected_switch(
                        id,
                        selector,
                        cases
                            .into_iter()
                            .map(|case| (case.matches, branches[&case.result].clone()))
                            .collect(),
                    )?,
                );
            } else {
                before.push(
                    self.lower
                        .temporary_switch(id, |_, arm| Ok(branches[&arm].clone()))?,
                );
            }
            before.push(self.g("\n", id));
            return Ok(Expr {
                before,
                value: self.g(name, id),
            });
        }
        if kind == "Comprehension" {
            return self.comprehension(id);
        }
        if kind == "Interpolation" {
            let (before, args) = self.ordered(self.s().group(id, "args").to_vec())?;
            let args = args.into_iter().map(|(_, v)| v).collect::<Vec<_>>();
            let name = format!("{}vet_string", self.lower.prefix);
            let helper = Code::generated(
                format!(
                    "\n{name}::proc \"contextless\"(args:..any)->string{{_ = args;return \"\"}}\n"
                ),
                &self.s().path,
                Span::new(0, 0),
                "native-check-support",
            );
            self.lower
                .helpers
                .entry((NodeId(0), self.lower.prefix.clone(), name.clone()))
                .or_insert(helper);
            let mut parts = vec![self.g(format!("{name}("), id)];
            for (i, arg) in args.into_iter().enumerate() {
                if i > 0 {
                    parts.push(self.g(",", id));
                }
                parts.push(arg);
            }
            parts.push(self.g(")", id));
            return Ok(Expr {
                before,
                value: Code::concat(parts),
            });
        }
        if kind == "Binary" && matches!(self.s().node(id).op.as_str(), "&&" | "||") {
            let left = self.s().role(id, "left").unwrap();
            let right = self.s().role(id, "right").unwrap();
            let a = self.expr(left)?;
            let b = self.expr(right)?;
            if !b.before.is_empty() {
                let name = self.name();
                let mut before = a.before;
                before.extend([
                    self.g(
                        format!(
                            "{name} := {}\nif {}(",
                            if self.s().node(id).op == "||" {
                                "true"
                            } else {
                                "false"
                            },
                            if self.s().node(id).op == "||" {
                                "!"
                            } else {
                                ""
                            }
                        ),
                        left,
                    ),
                    a.value,
                    self.g(") {\n", id),
                ]);
                before.extend(b.before);
                before.extend([
                    self.g(format!("{name} = "), right),
                    b.value,
                    self.g("\n}\n", id),
                ]);
                return Ok(Expr {
                    before,
                    value: self.g(name, id),
                });
            }
            return Ok(Expr {
                before: a.before,
                value: self.plain(id, vec![(left, a.value), (right, b.value)])?,
            });
        }
        if kind == "WhenExpr" {
            if let Some(cond) = self.s().role(id, "cond") {
                if let Some(selected) = self.lower.resolver.condition(Ref {
                    file: self.lower.file,
                    node: cond,
                }) {
                    if let Some(branch) = self.s().role(id, if selected { "x" } else { "y" }) {
                        return self.expr(branch);
                    }
                }
            }
        }
        if kind == "Ternary" {
            let c = self.s().role(id, "cond").unwrap();
            let x = self.s().role(id, "x").unwrap();
            let y = self.s().role(id, "y").unwrap();
            let cond = self.expr(c)?;
            let a = self.expr(x)?;
            let b = self.expr(y)?;
            if !a.before.is_empty() || !b.before.is_empty() {
                let name = self.name();
                let shape = self.shape(id)?;
                let mut before = cond.before;
                before.extend([
                    self.g(format!("{name}: "), id),
                    shape,
                    self.g("\nif ", c),
                    cond.value,
                    self.g(" {\n", id),
                ]);
                before.extend(a.before);
                before.extend([
                    self.g(format!("{name} = "), x),
                    a.value,
                    self.g("\n} else {\n", id),
                ]);
                before.extend(b.before);
                before.extend([
                    self.g(format!("{name} = "), y),
                    b.value,
                    self.g("\n}\n", id),
                ]);
                return Ok(Expr {
                    before,
                    value: self.g(name, id),
                });
            }
            return Ok(Expr {
                before: cond.before,
                value: self.plain(id, vec![(c, cond.value), (x, a.value), (y, b.value)])?,
            });
        }
        if kind == "OrElse" {
            let x = self.s().role(id, "x").unwrap();
            let y = self.s().role(id, "y").unwrap();
            let a = self.expr(x)?;
            let b = self.expr(y)?;
            if !b.before.is_empty() {
                let count = if self.s().node(y).kind == "FallbackList" {
                    self.s().group(y, "args").len()
                } else {
                    1
                };
                let prefix = self.name();
                let names = (0..count)
                    .map(|i| format!("{prefix}_v{i}"))
                    .collect::<Vec<_>>()
                    .join(",");
                let status = format!("{prefix}_status");
                self.lower.meta = true;
                let mut before = a.before;
                before.extend([self.g(format!("{names},{status} := "),x),a.value,self.g(format!("\nif !({status} when {}meta.type_is_boolean(type_of({status})) else ({status}==nil)) {{\n",self.lower.prefix),id)]);
                before.extend(b.before);
                before.extend([
                    self.g(format!("{names} = "), y),
                    b.value,
                    self.g("\n}\n", id),
                ]);
                let value = if count == 1 {
                    self.g(names, id)
                } else {
                    self.g(
                        format!(
                            "expand_values(struct{{{}}}{{{names}}})",
                            (0..count)
                                .map(|i| format!("v{i}:type_of({prefix}_v{i})"))
                                .collect::<Vec<_>>()
                                .join(",")
                        ),
                        id,
                    )
                };
                return Ok(Expr { before, value });
            }
            return Ok(Expr {
                before: a.before,
                value: self.plain(id, vec![(x, a.value), (y, b.value)])?,
            });
        }
        let mut before = vec![];
        let mut children: Vec<(NodeId, Code)> = vec![];
        let mut frozen = HashSet::new();
        for child in self.s().node(id).children.to_vec() {
            if !self.expression_kind(child) {
                children.push((child, self.node(child)?));
                continue;
            }
            let e = self.expr(child)?;
            if !e.before.is_empty() {
                for (previous, code) in &mut children {
                    if self.expression_kind(*previous)
                        && !self.stable(*previous)
                        && frozen.insert(*previous)
                    {
                        if self.s().node(*previous).kind == "FieldValue" {
                            let value = self.s().role(*previous, "value").unwrap();
                            let old = self.expr(value)?;
                            let name = self.name();
                            before.extend([
                                self.g(format!("{name} := "), value),
                                old.value,
                                self.g("\n", value),
                            ]);
                            *code = self.template(
                                *previous,
                                vec![(self.s().node(value).span, self.g(name, value))],
                            )?;
                            continue;
                        }
                        let name = self.name();
                        before.extend([
                            self.g(format!("{name} := "), *previous),
                            code.clone(),
                            self.g("\n", *previous),
                        ]);
                        *code = self.g(name, *previous);
                    }
                }
            }
            before.extend(e.before);
            children.push((child, e.value));
        }
        Ok(Expr {
            before,
            value: self.plain(id, children)?,
        })
    }
    fn comprehension(&mut self, id: NodeId) -> Result<Expr> {
        let input = self.s().role(id, "expr").unwrap();
        let output = self.s().role(id, "output").unwrap();
        let loop_ = self.s().role(id, "loop").unwrap();
        let name = self.name();
        let builder = format!("{name}_builder");
        let hint = self.hint(id);
        let target = self.lower.comp_target(id, hint)?;
        let target_type = self.lower.typ(id);
        let element = Code::concat([
            self.g(format!("{}meta.type_elem_type(", self.lower.prefix), id),
            target.clone(),
            self.g(")", id),
        ]);
        self.contexts.insert(
            output,
            (
                Some(Hint {
                    code: element,
                    semantic: target_type.element().cloned(),
                    constant_literal: false,
                    element_only: false,
                    plain_array: false,
                }),
                false,
            ),
        );
        self.lower.meta = true;
        let mut before = vec![
            self.g(format!("{name}: "), id),
            target.clone(),
            self.g("\n", id),
        ];
        let annotation = self.s().role(id, "type");
        let typ = self.lower.typ(id);
        let map = annotation.is_some_and(|a| self.s().node(a).kind == "MapType")
            || matches!(typ, Type::Map { .. });
        let set = annotation.is_some_and(|a| self.s().node(a).kind == "SetType")
            || matches!(typ, Type::Set(..));
        let dynamic = annotation.is_some_and(|a| self.s().node(a).kind == "DynamicArrayType")
            || matches!(typ, Type::Dynamic(..));
        if map {
            before.push(self.g(
                format!("{name} = make(type_of({name}), allocator=context.allocator)\n"),
                id,
            ));
        } else if set {
            before.push(self.g(
                format!(
                    "{name}.table = make(type_of({name}.table), allocator=context.allocator)\n"
                ),
                id,
            ));
        } else {
            before.extend([
                self.g(
                    format!(
                        "{builder}: [dynamic]{}meta.type_elem_type(",
                        self.lower.prefix
                    ),
                    id,
                ),
                target,
                self.g(")\n", id),
            ]);
        }
        if map {
            let module = self.lower.module("comprehension");
            for (part, member, semantic) in [
                (
                    self.s().role(id, "key"),
                    "first",
                    match &typ {
                        Type::Map { key, .. } => Some(*key.clone()),
                        _ => None,
                    },
                ),
                (
                    Some(output),
                    "second",
                    match &typ {
                        Type::Map { value, .. } => Some(*value.clone()),
                        _ => None,
                    },
                ),
            ] {
                if let Some(part) = part {
                    self.contexts.insert(
                        part,
                        (
                            Some(Hint {
                                code: self.g(format!("type_of({module}.{member}({name}))"), part),
                                semantic,
                                constant_literal: false,
                                element_only: false,
                                plain_array: false,
                            }),
                            false,
                        ),
                    );
                }
            }
        } else if set {
            let module = self.lower.module("sets");
            self.contexts.insert(
                output,
                (
                    Some(Hint {
                        code: self.g(format!("type_of({module}.element_witness({name}))"), output),
                        semantic: typ.element().cloned(),
                        constant_literal: false,
                        element_only: false,
                        plain_array: false,
                    }),
                    false,
                ),
            );
        }
        let source = self.iteration_input(loop_, input)?;
        before.extend(source.before);
        let mut body = vec![];
        let index = self.s().role(loop_, "index_type");
        let native_index = format!("{name}_index");
        if let Some(annotation) = index {
            if let Some(binding) = self.s().group(loop_, "names").get(1).copied() {
                body.extend([
                    self.copy(binding),
                    self.g(
                        if self.s().text(binding) == "_" {
                            " = "
                        } else {
                            " := "
                        },
                        binding,
                    ),
                    self.copy(annotation),
                    self.g(format!("({native_index})\n"), binding),
                ]);
            }
        }
        if let Some(cond) = self.s().role(id, "cond") {
            let e = self.expr(cond)?;
            body.extend(e.before);
            body.extend([self.g("if ", cond), e.value, self.g(" {\n", cond)]);
        }
        if let Some(key) = self.s().role(id, "key") {
            let k = self.expr(key)?;
            body.extend(k.before);
            body.extend([
                self.g(format!("{name}_key := "), key),
                k.value,
                self.g("\n", key),
            ]);
        }
        let value = self.expr(output)?;
        body.extend(value.before);
        if self.s().node(id).data["spread"] == true {
            body.extend([
                self.g(format!("{name}_part := "), output),
                value.value,
                self.g("\n", output),
            ]);
            if map {
                body.push(self.g(
                    format!("for {name}_k,{name}_v in {name}_part {{{name}[{name}_k]={name}_v}}\n"),
                    output,
                ));
            } else if set {
                let native = self.lower.typ(output);
                let part = if matches!(native, Type::Set(_)) {
                    format!("{name}_part.table")
                } else {
                    format!("{name}_part")
                };
                body.push(self.g(
                    format!("for {name}_item in {part} {{{name}.table[{name}_item]={{}}}}\n"),
                    output,
                ));
            } else {
                body.push(self.g(
                    format!("for {name}_item in {name}_part {{append(&{builder},{name}_item)}}\n"),
                    output,
                ));
            }
        } else if map {
            body.extend([
                self.g(format!("{name}[{name}_key] = "), output),
                value.value,
                self.g("\n", output),
            ]);
        } else if set {
            body.extend([
                self.g(format!("{name}.table["), output),
                value.value,
                self.g("] = {}\n", output),
            ]);
        } else {
            body.extend([
                self.g(format!("append(&{builder},"), output),
                value.value,
                self.g(")\n", output),
            ]);
        }
        if self.s().role(id, "cond").is_some() {
            body.push(self.g("}\n", id));
        }
        before.push(self.g("for ", id));
        for (i, binding) in self.s().group(loop_, "names").iter().enumerate() {
            if i > 0 {
                before.push(self.g(",", id));
            }
            before.push(if i == 1 && index.is_some() {
                self.g(native_index.clone(), *binding)
            } else {
                self.copy(*binding)
            });
        }
        before.extend([
            self.g(" in ", id),
            source.value,
            self.g(" {\n", id),
            Code::concat(body),
            self.g("}\n", id),
        ]);
        if !map && !set {
            before.push(self.g(
                format!("{name} = {builder}{}\n", if dynamic { "" } else { "[:]" }),
                id,
            ));
        }
        Ok(Expr {
            before,
            value: self.g(name, id),
        })
    }
}
