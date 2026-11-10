//! Switch values retain native multiple results, enum coverage, and assignment
//! evaluation order. Constant switches select native when branches lazily.
use super::*;

#[derive(Clone)]
struct Case {
    matches: Vec<NodeId>,
    result: NodeId,
}
impl Lower<'_, '_> {
    fn switch_hint_semantic(
        &mut self,
        id: NodeId,
        index: usize,
        hints: &[Option<Hint>],
    ) -> Option<Type> {
        hints.get(index).and_then(|hint|hint.as_ref()).and_then(|hint|hint.semantic.clone())
            .or_else(||Some(self.resolver.result_type(self.reference(id),index,&mut HashSet::new())))
    }
    pub(super) fn temporary_switch<F>(&mut self, id: NodeId, mut arm: F) -> Result<Code>
    where F: FnMut(&mut Self, NodeId) -> Result<Code> {
        if let Some(problem) = self.switch_problem(id, 1)? { return Ok(problem); }
        let cases = self.cases(id)?;
        if self.source.node(id).kind == "SwitchExpr" {
            let mut branches = HashMap::new();
            for case in cases { branches.insert(case.result, arm(self, case.result)?); }
            return self.native_switch(id, &[None], Some(&[]), Some(&branches));
        }
        let validation = self.switch_validation(id)?;
        let mut code = vec![self.g("when ", id), validation, self.g(" {\n", id)];
        for (index, case) in cases.iter().filter(|case|!case.matches.is_empty())
            .chain(cases.iter().filter(|case|case.matches.is_empty())).enumerate() {
            let condition = self.case_condition(id, case)?;
            let branch = arm(self, case.result)?;
            code.extend([self.g(if index==0 {"when "} else {"else when "},id), condition,
                self.g(" {\n", id), branch, self.g("}\n", id)]);
        }
        code.push(self.g("}\n", id));
        Ok(Code::concat(code))
    }
    pub(super) fn unparen(&self, mut id: NodeId) -> NodeId {
        while self.source.node(id).kind == "Paren" {
            let Some(child) = self.source.role(id, "expr") else {
                break;
            };
            id = child;
        }
        id
    }
    fn cases(&self, id: NodeId) -> Result<Vec<Case>> {
        self.source.node(id).data["cases"]
            .as_array()
            .ok_or_else(|| Error::new("switch cases omitted"))?
            .iter()
            .map(|case| {
                let at = |value: &serde_json::Value| {
                    Span::from_wire(value)
                        .and_then(|span| self.source.at(span, id))
                        .ok_or_else(|| Error::new("invalid switch operand span"))
                };
                Ok(Case {
                    matches: case["matches"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .map(at)
                        .collect::<Result<Vec<_>>>()?,
                    result: if self.source.node(id).kind == "CompileSwitch" {
                        id
                    } else {
                        at(&case["result"])?
                    },
                })
            })
            .collect()
    }
    pub(super) fn diverging(&mut self, id: NodeId) -> bool {
        let id = self.unparen(id);
        if self.source.node(id).kind != "Call" {
            return false;
        }
        let Some(callee) = self.source.role(id, "callee") else {
            return false;
        };
        if let Some(signature) = self.resolver.signature(self.reference(callee)) {
            return self.resolver.sources[signature.file]
                .node(signature.node)
                .data["diverging"]
                == true;
        }
        ["panic", "panic_contextless", "unreachable"]
            .iter()
            .any(|name| self.resolver.native_builtin(self.reference(callee), name))
    }
    fn arm_count(&mut self, id: NodeId) -> Result<usize> {
        let id = self.unparen(id);
        Ok(match self.source.node(id).kind.as_str() {
            "SwitchBreak" => 0,
            "SwitchResults" => self.source.group(id, "args").len(),
            "SwitchExpr" | "CompileSwitchExpr" => self.switch_count(id)?,
            "Call" => {
                if self.diverging(id) {
                    0
                } else {
                    let callee = self.role(id, "callee")?;
                    self.resolver
                        .signature(self.reference(callee))
                        .map(|sig| self.resolver.fields(sig, "results").len())
                        .unwrap_or(1)
                }
            }
            _ => 1,
        })
    }
    fn switch_count(&mut self, id: NodeId) -> Result<usize> {
        let mut count = 1;
        for case in self.cases(id)? {
            count = count.max(self.arm_count(case.result)?);
        }
        Ok(count)
    }
    fn switch_problem(&mut self, id: NodeId, count: usize) -> Result<Option<Code>> {
        let cases = self.cases(id)?;
        let defaults = cases.iter().filter(|case| case.matches.is_empty()).count();
        let problem = if defaults > 1 {
            Some("A switch may have only one default arm.")
        } else if defaults == 0 {
            if self.source.node(id).data["partial"] == true {
                Some("A #partial switch expression requires a default arm.")
            } else if self.source.node(id).kind == "CompileSwitchExpr" {
                Some("A switch expression requires a default arm.")
            } else if let Some(selector) = self.source.role(id, "selector") {
                if matches!(
                    self.typ(selector),
                    Type::Enum(_) | Type::Generic | Type::Unknown
                ) {
                    None
                } else {
                    Some("A switch expression requires a default arm.")
                }
            } else {
                Some("A switch expression requires a default arm.")
            }
        } else {
            None
        };
        if let Some(problem) = problem {
            return Ok(Some(self.error_token(id, problem)));
        }
        if self.source.node(id).kind == "SwitchExpr" {
            for case in &cases {
                if self.source.node(case.result).kind == "SwitchBreak"
                    || self.diverging(case.result)
                {
                    continue;
                }
                let arm = self.unparen(case.result);
                if self.source.node(arm).kind == "Call" {
                    let callee = self.role(arm, "callee")?;
                    if self.resolver.signature(self.reference(callee)).is_none() {
                        continue;
                    }
                }
                let actual = self.arm_count(case.result)?;
                if actual != count {
                    return Ok(Some(self.error_token(case.result,&format!("Every switch value arm must return {count} results; this arm returns {actual}."))));
                }
            }
        } else if let Some(problem) = self.compile_case_problem(id)? {
            return Ok(Some(self.error_token(id, &problem)));
        }
        Ok(None)
    }
    fn compile_case_problem(&mut self, id: NodeId) -> Result<Option<String>> {
        if let Some(problem)=self.big_compile_case_problem(id){return Ok(Some(problem));}
        let mut keys = HashSet::new();
        let mut intervals: Vec<(i128, i128)> = vec![];
        for case in self.cases(id)? {
            for operand in case.matches {
                let node = self.source.node(operand);
                let number = self.resolver.constant_int(self.reference(operand));
                let key = number.map(|n| format!("int:{n}")).or_else(|| {
                    (node.kind == "Literal" && node.op == "string"
                        || matches!(self.source.text(operand), "true" | "false")
                        || node.kind == "ImplicitSelector")
                        .then(|| self.source.text(operand).to_string())
                });
                if let Some(key) = key {
                    if !keys.insert(key) {
                        return Ok(Some("Duplicate constant switch case.".into()));
                    }
                }
                let mut range = number.map(|n| (n, n));
                if node.kind == "Binary" && matches!(node.op.as_str(), ".." | "..<" | "..=") {
                    let a = self.role(operand, "left")?;
                    let b = self.role(operand, "right")?;
                    if let (Some(a), Some(b)) = (
                        self.resolver.constant_int(self.reference(a)),
                        self.resolver.constant_int(self.reference(b)),
                    ) {
                        let b = if node.op == "..=" {
                            Some(b)
                        } else {
                            b.checked_sub(1)
                        };
                        if b.is_none_or(|b| b < a) {
                            return Ok(Some(
                                "A switch case range must not be empty or reversed.".into(),
                            ));
                        }
                        range = Some((a, b.unwrap()));
                    }
                }
                if let Some((a, b)) = range {
                    if intervals
                        .iter()
                        .any(|(low, high)| a.max(*low) <= b.min(*high))
                    {
                        return Ok(Some(
                            "Duplicate or overlapping constant switch cases.".into(),
                        ));
                    }
                    intervals.push((a, b));
                }
            }
        }
        Ok(None)
    }
    fn switch_constant(&self, id: NodeId, seen: &mut HashSet<NodeId>) -> bool {
        if !seen.insert(id) {
            return false;
        }
        let s = self.source;
        let n = s.node(id);
        if matches!(n.kind.as_str(), "Literal" | "ImplicitSelector")
            || matches!(s.text(id), "true" | "false")
            || self.resolver.denotes_type(self.reference(id))
        {
            return true;
        }
        if let Some(symbol) = self.resolver.symbol(self.reference(id)) {
            let symbol = &self.resolver.symbols[symbol];
            if symbol.reference.file != self.file {
                return false;
            }
            if s.node(symbol.reference.node).kind == "PolyType" {
                return true;
            }
            if symbol.constant {
                return symbol
                    .value
                    .is_some_and(|value| self.switch_constant(value, seen));
            }
            return false;
        }
        match n.kind.as_str() {
            "Paren" | "Unary" => s
                .role(id, "expr")
                .is_some_and(|child| self.switch_constant(child, seen)),
            "Binary" => ["left", "right"].iter().all(|role| {
                s.role(id, role)
                    .is_some_and(|child| self.switch_constant(child, &mut seen.clone()))
            }),
            "Call" => s.role(id, "callee").is_some_and(|callee| {
                (matches!(
                    s.text(callee),
                    "#config"
                        | "len"
                        | "size_of"
                        | "align_of"
                        | "type_of"
                        | "max"
                        | "min"
                        | "abs"
                        | "clamp"
                ) && self
                    .resolver
                    .native_builtin(self.reference(callee), s.text(callee))
                    || self.resolver.denotes_type(self.reference(callee)))
                    && s.group(id, "args")
                        .iter()
                        .skip(usize::from(s.text(callee) == "#config"))
                        .all(|arg| self.switch_constant(*arg, &mut seen.clone()))
            }),
            _ => false,
        }
    }
    fn switch_validation(&mut self, id: NodeId) -> Result<Code> {
        if self.source.role(id,"selector").is_some_and(|selector|self.typ(selector).big_name().is_some()){return Ok(self.g("true",id));}
        let mut operands = vec![];
        for case in self.cases(id)? {
            for operand in case.matches {
                if self.switch_constant(operand, &mut HashSet::new()) {
                    operands.push(operand);
                }
            }
        }
        if operands.is_empty() {
            return Ok(self.g("true", id));
        }
        let name = format!(
            "{}validate_switch_{}",
            self.prefix,
            self.source.node(id).span.start
        );
        let selector = format!("{}selector", self.prefix);
        let owner = self.helper_scope(id);
        let mut declaration=vec![self.g(format!("\n{}{name} :: proc \"contextless\" (${selector}: ${}Selector) -> bool {{\nswitch {selector} {{\n",if owner.0==0{"@(private=\"file\")\n"}else{""},self.prefix),id)];
        for operand in operands {
            declaration.extend([
                self.g("case ", operand),
                self.render(operand, None, true)?,
                self.g(":\n", operand),
            ]);
        }
        declaration.push(self.g("case:\n}\nreturn true\n}\n", id));
        self.helpers.insert(
            (owner, self.prefix.clone(), name.clone()),
            Code::concat(declaration),
        );
        let value = if let Some(selector) = self.source.role(id, "selector") {
            self.render(selector, None, true)?
        } else {
            self.g("true", id)
        };
        let type_of = self.native("type_of");
        Ok(Code::concat([
            self.g(format!("({type_of}({name}("), id),
            value,
            self.g(")) == bool)", id),
        ]))
    }
    pub(super) fn compile_switch(&mut self, id: NodeId) -> Result<Code> {
        let cases = self.cases(id)?;
        if cases.iter().filter(|case| case.matches.is_empty()).count() > 1 {
            return Ok(self.error_token(id, "A switch may have only one default arm."));
        }
        if let Some(problem) = self.compile_case_problem(id)? {
            return Ok(self.error_token(id, &problem));
        }
        let validation = self.switch_validation(id)?;
        let mut code = vec![self.g("when ", id), validation, self.g(" {\n", id)];
        let rows = self.source.node(id).data["cases"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        for (i, (case, row)) in cases
            .iter()
            .zip(rows.iter())
            .filter(|(case, _)| !case.matches.is_empty())
            .chain(
                cases
                    .iter()
                    .zip(rows.iter())
                    .filter(|(case, _)| case.matches.is_empty()),
            )
            .enumerate()
        {
            let condition = self.case_condition(id, case)?;
            code.extend([
                self.g(if i == 0 { "when " } else { "else when " }, id),
                condition,
                self.g(" {\n", id),
            ]);
            for span in row["statements"].as_array().into_iter().flatten() {
                if let Some(statement) =
                    Span::from_wire(span).and_then(|span| self.source.at(span, id))
                {
                    code.push(self.render(statement, None, false)?);
                    code.push(self.g("\n", statement));
                }
            }
            code.push(self.g("}\n", id));
        }
        code.push(self.g("}", id));
        Ok(Code::concat(code))
    }
    fn case_condition(&mut self, id: NodeId, case: &Case) -> Result<Code> {
        if let Some(selected)=self.big_compile_case(id,&case.matches){return Ok(self.g(if selected{"true"}else{"false"},id));}
        if case.matches.is_empty() {
            return Ok(self.g("true", id));
        }
        let selector = self.source.role(id, "selector");
        let mut values = vec![];
        for operand in &case.matches {
            let value = if let Some(selector) = selector {
                let node = self.source.node(*operand);
                let selector_code = self.render(selector, None, true)?;
                if node.kind == "Binary" && matches!(node.op.as_str(), ".." | "..<" | "..=") {
                    let low = self.render(self.role(*operand, "left")?, None, true)?;
                    let high = self.render(self.role(*operand, "right")?, None, true)?;
                    Code::concat([
                        self.g("((", *operand),
                        selector_code.clone(),
                        self.g(") >= (", *operand),
                        low,
                        self.g(") && (", *operand),
                        selector_code,
                        self.g(if node.op == "..=" { ") <= (" } else { ") < (" }, *operand),
                        high,
                        self.g("))", *operand),
                    ])
                } else {
                    let value = self.render(*operand, None, true)?;
                    Code::concat([
                        self.g("((", *operand),
                        selector_code,
                        self.g(") == (", *operand),
                        value,
                        self.g("))", *operand),
                    ])
                }
            } else {
                self.render(*operand, None, true)?
            };
            values.push(value);
        }
        let mut result = self.g("false", id);
        for value in values.into_iter().rev() {
            result = Code::concat([
                self.g("(true when (", id),
                value,
                self.g(") else ", id),
                result,
                self.g(")", id),
            ]);
        }
        Ok(result)
    }
    pub(super) fn evaluation_case_condition(&mut self, id: NodeId, index: usize) -> Result<Code> {
        let cases = self.cases(id)?;
        self.case_condition(
            id,
            cases
                .get(index)
                .ok_or_else(|| Error::new("invalid evaluation switch case"))?,
        )
    }
    fn switch_witness(&mut self, arm: NodeId, index: usize) -> Result<Option<Code>> {
        if self.source.node(arm).kind == "SwitchBreak" || self.diverging(arm) {
            return Ok(None);
        }
        let arm = self.unparen(arm);
        if self.source.node(arm).kind == "SwitchResults" {
            if let Some(value) = self.source.group(arm, "args").get(index) {
                return Ok(Some(self.query_value_switch(*value)?));
            }
            return Ok(None);
        }
        if matches!(
            self.source.node(arm).kind.as_str(),
            "SwitchExpr" | "CompileSwitchExpr"
        ) {
            let count = self.switch_count(arm)?;
            let types = self.switch_types(arm, &vec![None; count])?;
            return Ok(types
                .get(index)
                .cloned()
                .map(|code| Code::concat([self.g("(", arm), code, self.g("){}", arm)])));
        }
        if self.source.node(arm).kind == "Call" {
            let code = self.query_value_switch(arm)?;
            self.meta = true;
            let type_of = self.native("type_of");
            return Ok(Some(Code::concat([
                self.g(
                    format!(
                        "({}meta.type_proc_return_type({type_of}({}meta.procedure_of(",
                        self.prefix, self.prefix
                    ),
                    arm,
                ),
                code,
                self.g(format!(")),{index})){{}}"), arm),
            ])));
        }
        if index == 0 {
            Ok(Some(self.query_value_switch(arm)?))
        } else {
            Ok(None)
        }
    }
    fn query_value_switch(&mut self, id: NodeId) -> Result<Code> {
        self.querying += 1;
        let code = self.render(id, None, false);
        self.querying -= 1;
        code
    }
    fn switch_types(&mut self, id: NodeId, hints: &[Option<Hint>]) -> Result<Vec<Code>> {
        let cases = self.cases(id)?;
        let compile = self.source.node(id).kind == "CompileSwitchExpr";
        let mut types = vec![];
        for (index, hint) in hints.iter().enumerate() {
            if let Some(hint) = hint {
                types.push(hint.code.clone());
                continue;
            }
            let mut values = vec![];
            for case in &cases {
                if let Some(code) = self.switch_witness(case.result, index)? {
                    values.push((case, code));
                }
            }
            if values.is_empty() {
                types.push(self.error_token(id,"A switch expression with only nonreturning/break arms requires expected result types."));
                continue;
            }
            let mut query = values.last().unwrap().1.clone();
            if compile {
                if let Some((_, code)) = values.iter().find(|(case, _)| case.matches.is_empty()) {
                    query = code.clone();
                }
                for (case, code) in values
                    .into_iter()
                    .rev()
                    .filter(|(case, _)| !case.matches.is_empty())
                {
                    let cond = self.case_condition(id, case)?;
                    query = Code::concat([
                        self.g("(", id),
                        code,
                        self.g(" when ", id),
                        cond,
                        self.g(" else ", id),
                        query,
                        self.g(")", id),
                    ]);
                }
            } else {
                for (_, code) in values[..values.len() - 1].iter().rev() {
                    query = Code::concat([
                        self.g("(false ? (", id),
                        code.clone(),
                        self.g(") : (", id),
                        query,
                        self.g("))", id),
                    ]);
                }
            }
            self.default = true;
            let type_of = self.native("type_of");
            types.push(Code::concat([
                self.g(format!("{type_of}({}default_value(", self.prefix), id),
                query,
                self.g("))", id),
            ]));
        }
        Ok(types)
    }
    fn switch_arm(&mut self, arm: NodeId, hints: &[Option<Hint>], constant: bool) -> Result<Code> {
        let arm = self.unparen(arm);
        if matches!(
            self.source.node(arm).kind.as_str(),
            "SwitchExpr" | "CompileSwitchExpr"
        ) {
            return self.switch_expression(arm, hints.to_vec(), constant);
        }
        if self.source.node(arm).kind == "SwitchResults" {
            let values = self.source.group(arm, "args").to_vec();
            let mut code = vec![];
            for (i, value) in values.iter().enumerate() {
                if i > 0 {
                    code.push(self.g(", ", arm));
                }
                code.push(self.render(*value, hints.get(i).cloned().flatten(), constant)?);
            }
            Ok(Code::concat(code))
        } else {
            self.render(arm, hints.first().cloned().flatten(), constant)
        }
    }
    fn compile_arm(&mut self, arm: NodeId, hints: &[Option<Hint>], constant: bool) -> Result<Code> {
        if self.diverging(arm) {
            if hints.iter().any(Option::is_none) {
                return Ok(self.error_token(
                    arm,
                    "A selected nonreturning arm requires expected result types.",
                ));
            }
            let result = format!("tuple:{}", hints.len());
            let (name, mut args) = self.closed(arm, "switch_panic", Some(&result), |this| {
                this.render(arm, None, false)
            })?;
            args.extend(hints.iter().flatten().map(|hint| hint.code.clone()));
            return Ok(self.helper_call(arm, &name, args));
        }
        let actual = self.arm_count(arm)?;
        let known = self.source.node(self.unparen(arm)).kind != "Call"
            || self
                .source
                .role(self.unparen(arm), "callee")
                .is_some_and(|callee| self.resolver.signature(self.reference(callee)).is_some());
        if known && actual != hints.len() {
            let error = self.error_token(
                arm,
                &format!(
                    "Every switch value arm must return {} results; this arm returns {actual}.",
                    hints.len()
                ),
            );
            let mut values = vec![];
            for i in 0..hints.len() {
                if i > 0 {
                    values.push(self.g(", ", arm));
                }
                values.push(error.clone());
            }
            return Ok(Code::concat(values));
        }
        self.switch_arm(arm, hints, constant)
    }
    fn switch_consume(
        &mut self,
        id: NodeId,
        arm: NodeId,
        hints: &[Option<Hint>],
        targets: Option<&[Code]>,
    ) -> Result<Code> {
        let s = self.source;
        let mut root = id;
        while let Some(parent) = s
            .node(root)
            .parent
            .filter(|parent| s.node(*parent).kind == "Paren")
        {
            root = parent;
        }
        let blanks = if let Some(targets) = targets {
            targets
                .iter()
                .map(|code| code.clone().finish().text == "_")
                .collect::<Vec<_>>()
        } else if let Some(parent) = s.node(root).parent {
            let names = if s.node(parent).kind == "Declaration" {
                s.group(parent, "names")
            } else if s.node(parent).kind == "Assignment" {
                s.group(parent, "lhs")
            } else {
                &[]
            };
            names
                .iter()
                .map(|name| s.text(*name) == "_")
                .collect::<Vec<_>>()
        } else {
            vec![]
        };
        let values = if s.node(arm).kind == "SwitchResults" {
            s.group(arm, "args").to_vec()
        } else {
            vec![arm]
        };
        let cleanup = values.len() == hints.len()
            && values
                .iter()
                .enumerate()
                .any(|(i, value)| blanks.get(i) == Some(&true) && self.anonymous_string(*value));
        let mut code = vec![];
        let mut results = vec![];
        if cleanup {
            for (i, value) in values.iter().enumerate() {
                let name = format!("{}switch_value_{i}", self.prefix);
                let expression = self.render(*value, hints[i].clone(), false)?;
                code.push(self.g(name.clone(), *value));
                if let Some(hint) = &hints[i] {
                    code.extend([
                        self.g(": ", *value),
                        hint.code.clone(),
                        self.g(" = ", *value),
                    ]);
                } else {
                    code.push(self.g(" := ", *value));
                }
                code.extend([expression, self.g("\n", *value)]);
                results.push(self.g(name, *value));
            }
            for (i, value) in values.iter().enumerate() {
                if blanks.get(i) == Some(&true) && self.anonymous_string(*value) {
                    let delete = self.native("delete");
                    code.extend([
                        self.g(format!("{delete}("), *value),
                        results[i].clone(),
                        self.g(")\n", *value),
                    ]);
                }
            }
        }
        if let Some(targets) = targets {
            for (i, target) in targets.iter().enumerate() {
                if i > 0 {
                    code.push(self.g(", ", arm));
                }
                code.push(target.clone());
            }
            code.push(self.g(" = ", arm));
        } else {
            code.push(self.g("return ", arm));
        }
        if cleanup {
            for (i, result) in results.into_iter().enumerate() {
                if i > 0 {
                    code.push(self.g(", ", arm));
                }
                code.push(result);
            }
        } else {
            code.push(self.switch_arm(arm, hints, false)?);
        }
        Ok(Code::concat(code))
    }
    fn native_switch(
        &mut self,
        id: NodeId,
        hints: &[Option<Hint>],
        targets: Option<&[Code]>,
        branches: Option<&HashMap<NodeId, Code>>,
    ) -> Result<Code> {
        let cases = self.cases(id)?;
        let selector = self.source.role(id, "selector");
        let missing = !cases.iter().any(|case| case.matches.is_empty());
        let mut code = vec![];
        let selector_code = if let Some(selector) = selector {
            self.render(selector, None, false)?
        } else {
            self.g("", id)
        };
        if missing {
            self.meta = true;
            let type_of = self.native("type_of");
            code.extend([
                self.g(
                    format!("when {}meta.type_is_enum({type_of}(", self.prefix),
                    id,
                ),
                selector_code.clone(),
                self.g(")) {\n", id),
            ]);
            if targets.is_some() {
                code.push(self.g(format!("{}matched := false\n", self.prefix), id));
            }
        }
        code.push(self.g(
            if self.source.node(id).data["partial"] == true {
                "#partial switch "
            } else {
                "switch "
            },
            id,
        ));
        code.extend([selector_code, self.g(" {\n", id)]);
        for case in cases {
            code.push(self.g("case ", case.result));
            for (i, operand) in case.matches.iter().enumerate() {
                if i > 0 {
                    code.push(self.g(", ", *operand));
                }
                code.push(self.render(*operand, None, false)?);
            }
            code.push(self.g(": ", case.result));
            if missing && targets.is_some() {
                code.push(self.g(format!("{}matched=true; ", self.prefix), case.result));
            }
            if self.source.node(case.result).kind == "SwitchBreak" {
                continue;
            }
            if let Some(branch) = branches.and_then(|branches| branches.get(&case.result)) {
                code.push(branch.clone());
            } else if self.diverging(case.result) {
                code.push(self.render(case.result, None, false)?);
            } else {
                code.push(self.switch_consume(id, case.result, hints, targets)?);
            }
            code.push(self.g("\n", case.result));
        }
        code.push(self.g("}\n", id));
        if missing {
            let panic = self.native(if self.has_context(id) {
                "panic"
            } else {
                "panic_contextless"
            });
            if targets.is_some() {
                code.push(self.g(format!("if !{}matched {{", self.prefix), id));
            }
            code.push(self.g(
                format!("{panic}(\"Unmatched enum value in switch expression\")"),
                id,
            ));
            if targets.is_some() {
                code.push(self.g("}", id));
            }
            code.push(self.g("\n} else {#panic(\"A switch expression requires a default arm unless its selector is an enum\")}\n",id));
        }
        Ok(Code::concat(code))
    }
    pub(super) fn switch_expression(
        &mut self,
        id: NodeId,
        mut hints: Vec<Option<Hint>>,
        constant: bool,
    ) -> Result<Code> {
        let count = self.switch_count(id)?;
        if hints.len() == 1 && count > 1 && hints[0].is_none() {
            hints = vec![None; count];
        }
        if let Some(problem) = self.switch_problem(id, hints.len())? {
            return Ok(problem);
        }
        let cases = self.cases(id)?;
        if cases
            .iter()
            .any(|case| self.source.node(case.result).kind == "SwitchBreak")
        {
            return Ok(self.error_token(id,"break in a switch expression is allowed only as the entire right-hand side of an ordinary = assignment."));
        }
        if self.source.node(id).kind == "CompileSwitchExpr" {
            if hints.len() > 1 {
                let types = self.switch_types(id, &hints)?;
                let generic = (0..hints.len())
                    .map(|i| {
                        Some(Hint {
                            semantic: self.switch_hint_semantic(id,i,&hints),
                            code: self.g(format!("{}Result_{i}", self.prefix), id),
                            element_only: false,
                            plain_array: false,
                        })
                    })
                    .collect::<Vec<_>>();
                let result = format!("tuple:{}", hints.len());
                let (name, mut args) =
                    self.closed(id, "compile_switch", Some(&result), |this| {
                        this.compile_switch_returns(id, &generic)
                    })?;
                args.extend(types);
                return Ok(self.helper_call(id, &name, args));
            }
            let default = cases
                .iter()
                .find(|case| case.matches.is_empty())
                .ok_or_else(|| Error::new("switch default omitted"))?;
            let mut value = self.compile_arm(default.result, &hints, constant)?;
            for case in cases.iter().rev().filter(|case| !case.matches.is_empty()) {
                let arm = self.compile_arm(case.result, &hints, constant)?;
                let cond = self.case_condition(id, case)?;
                value = Code::concat([
                    self.g("(", id),
                    arm,
                    self.g(" when ", id),
                    cond,
                    self.g(" else ", id),
                    value,
                    self.g(")", id),
                ]);
            }
            let validation = self.switch_validation(id)?;
            let invalid = self.error_token(id, "Invalid compile-time switch cases.");
            return Ok(Code::concat([
                self.g("(", id),
                value,
                self.g(" when ", id),
                validation,
                self.g(" else ", id),
                invalid,
                self.g(")", id),
            ]));
        }
        if constant {
            return Err(self.unsupported(id, "runtime switch expression in constant context"));
        }
        let types = self.switch_types(id, &hints)?;
        let generic = (0..hints.len())
            .map(|i| {
                Some(Hint {
                    semantic: self.switch_hint_semantic(id,i,&hints),
                    code: self.g(format!("{}Result_{i}", self.prefix), id),
                    element_only: false,
                    plain_array: false,
                })
            })
            .collect::<Vec<_>>();
        let flavor = format!("tuple:{}", hints.len());
        let (name, mut args) = self.closed(id, "switch", Some(&flavor), |this| {
            this.native_switch(id, &generic, None, None)
        })?;
        args.extend(types);
        Ok(self.helper_call(id, &name, args))
    }
    fn compile_switch_returns(&mut self, id: NodeId, hints: &[Option<Hint>]) -> Result<Code> {
        let cases = self.cases(id)?;
        let validation = self.switch_validation(id)?;
        let mut code = vec![self.g("when ", id), validation, self.g(" {\n", id)];
        for (i, case) in cases
            .iter()
            .filter(|case| !case.matches.is_empty())
            .chain(cases.iter().filter(|case| case.matches.is_empty()))
            .enumerate()
        {
            let condition = self.case_condition(id, case)?;
            code.extend([
                self.g(if i == 0 { "when " } else { "else when " }, id),
                condition,
                self.g(" {\n", id),
            ]);
            if !self.diverging(case.result) {
                code.push(self.g("return ", case.result));
            }
            code.push(self.compile_arm(case.result, hints, false)?);
            code.push(self.g("\n}\n", id));
        }
        code.push(self.g("}\n", id));
        Ok(Code::concat(code))
    }
    fn compile_switch_statement(
        &mut self,
        owner: NodeId,
        root: NodeId,
        id: NodeId,
        hints: &[Option<Hint>],
        constant: bool,
    ) -> Result<Code> {
        let cases = self.cases(id)?;
        let validation = self.switch_validation(id)?;
        let mut code = vec![self.g("when ", id), validation, self.g(" {\n", id)];
        self.switch_owners.insert(owner);
        let body = (|| -> Result<Code> {
            for (i, case) in cases
                .iter()
                .filter(|case| !case.matches.is_empty())
                .chain(cases.iter().filter(|case| case.matches.is_empty()))
                .enumerate()
            {
                let condition = self.case_condition(id, case)?;
                code.extend([
                    self.g(if i == 0 { "when " } else { "else when " }, id),
                    condition,
                    self.g(" {\n", id),
                ]);
                if self.source.node(case.result).kind != "SwitchBreak" {
                    if self.diverging(case.result) && self.source.node(owner).kind != "Declaration"
                    {
                        code.push(self.render(case.result, None, false)?);
                    } else {
                        let value = self.compile_arm(
                            case.result,
                            hints,
                            constant
                                || self.source.node(owner).kind == "Declaration"
                                    && !self.source.node(owner).mutable,
                        )?;
                        self.switch_replacements.insert(root, value);
                        let declaration = self.render(owner, None, constant);
                        self.switch_replacements.remove(&root);
                        code.push(declaration?);
                    }
                }
                code.push(self.g("\n}\n", id));
            }
            code.push(self.g("}\n", id));
            Ok(Code::concat(code))
        })();
        self.switch_owners.remove(&owner);
        body
    }
    fn statement_hints(&mut self, id: NodeId) -> Result<Vec<Option<Hint>>> {
        let s = self.source;
        let hints = match s.node(id).kind.as_str() {
            "Declaration" => {
                let hint = if let Some(annotation) = s.role(id, "type") {
                    Some(Hint {
                        semantic: Some(self.typ(annotation)),
                        code: self.render(annotation, None, false)?,
                        element_only: false,
                        plain_array: s.node(annotation).kind == "ArrayType",
                    })
                } else {
                    None
                };
                vec![hint; s.group(id, "names").len()]
            }
            "Assignment" => {
                let mut hints = vec![];
                for lhs in s.group(id, "lhs") {
                    if s.text(*lhs) == "_" {
                        hints.push(None);
                    } else {
                        let type_of = self.native("type_of");
                        let value = self.query_value_switch(*lhs)?;
                        hints.push(Some(Hint {
                            semantic: Some(self.typ(*lhs)),
                            code: Code::concat([
                                self.g(format!("{type_of}("), *lhs),
                                value,
                                self.g(")", *lhs),
                            ]),
                            element_only: false,
                            plain_array: false,
                        }));
                    }
                }
                hints
            }
            "Return" => {
                let owner = s
                    .ancestors(id)
                    .find(|node| s.node(*node).kind == "Procedure");
                let mut hints = vec![];
                if let Some(signature) = owner.and_then(|owner| s.role(owner, "type")) {
                    for (_, annotation) in
                        self.resolver.fields(self.reference(signature), "results")
                    {
                        let hint = if let Some(annotation) =
                            annotation.filter(|annotation| annotation.file == self.file)
                        {
                            Some(Hint {
                                semantic: Some(self.typ(annotation.node)),
                                code: self.render(annotation.node, None, false)?,
                                element_only: false,
                                plain_array: false,
                            })
                        } else {
                            None
                        };
                        hints.push(hint);
                    }
                }
                hints
            }
            _ => vec![],
        };
        Ok(hints)
    }
    fn global_switch_binding(
        &mut self,
        owner: NodeId,
        root: NodeId,
        id: NodeId,
        hints: &[Option<Hint>],
    ) -> Result<Code> {
        let count = hints.len();
        let types = self.switch_types(id, hints)?;
        let mut record = vec![self.g("struct{", id)];
        for (i, typ) in types.into_iter().enumerate() {
            if i > 0 {
                record.push(self.g(", ", id));
            }
            record.extend([self.g(format!("value_{i}: "), id), typ]);
        }
        record.push(self.g("}", id));
        let (name, mut args) = self.closed(owner, "switch_global", Some(""), |this| {
            let type_of = this.native("type_of");
            let hints = (0..hints.len())
                .map(|i| {
                    Some(Hint {
                        semantic: this.switch_hint_semantic(id,i,hints),
                        code: this.g(
                            format!("{type_of}({}Result{{}}.value_{i})", this.source.prefix),
                            id,
                        ),
                        element_only: false,
                        plain_array: false,
                    })
                })
                .collect::<Vec<_>>();
            let expression = this.switch_expression(id, hints, false)?;
            let names = (0..count)
                .map(|i| format!("{}value_{i}", this.source.prefix))
                .collect::<Vec<_>>();
            Ok(Code::concat([
                this.g(format!("{} := ", names.join(", ")), id),
                expression,
                this.g(
                    format!(
                        "\nreturn {}Result{{{}}}",
                        this.source.prefix,
                        names.join(", ")
                    ),
                    id,
                ),
            ]))
        })?;
        args.push(Code::concat(record));
        let call = self.helper_call(owner, &name, args);
        let temporary = format!(
            "{}global_switch_{}",
            self.prefix,
            self.source.node(owner).span.start
        );
        let mut replacement = vec![];
        for i in 0..hints.len() {
            if i > 0 {
                replacement.push(self.g(", ", id));
            }
            replacement.push(self.g(format!("{temporary}.value_{i}"), id));
        }
        self.helpers.insert(
            (NodeId(0), self.prefix.clone(), temporary.clone()),
            Code::concat([
                self.g(format!("\n@(private=\"file\")\n{temporary} := "), id),
                call,
                self.g("\n", id),
            ]),
        );
        self.switch_owners.insert(owner);
        self.switch_replacements
            .insert(root, Code::concat(replacement));
        let code = self.render(owner, None, false);
        self.switch_owners.remove(&owner);
        self.switch_replacements.remove(&root);
        code
    }
    pub(super) fn switch_statement(
        &mut self,
        owner: NodeId,
        constant: bool,
    ) -> Result<Option<Code>> {
        if self.switch_owners.contains(&owner) {
            return Ok(None);
        }
        let s = self.source;
        let role = match s.node(owner).kind.as_str() {
            "Declaration" => "values",
            "Assignment" => "rhs",
            "Return" => "values",
            _ => return Ok(None),
        };
        let values = s.group(owner, role);
        if values.len() != 1 {
            return Ok(None);
        }
        let root = values[0];
        let id = self.unparen(root);
        if !matches!(s.node(id).kind.as_str(), "SwitchExpr" | "CompileSwitchExpr") {
            return Ok(None);
        }
        let hints = self.statement_hints(owner)?;
        let cases = self.cases(id)?;
        let skip = cases
            .iter()
            .any(|case| s.node(case.result).kind == "SwitchBreak");
        if skip && (s.node(owner).kind != "Assignment" || s.node(owner).op != "=") {
            return Ok(Some(self.error_token(id,"break in a switch expression requires an ordinary = assignment to existing destinations.")));
        }
        if let Some(problem) = self.switch_problem(id, hints.len())? {
            let global=s.node(owner).kind=="Declaration"&&!s.ancestors(owner).any(|ancestor|s.node(ancestor).kind=="Procedure");
            return Ok(Some(if global{self.g(format!("#panic({})",json!(problem.finish().text)),id)}else{problem}));
        }
        if s.node(id).kind == "CompileSwitchExpr" && (hints.len() > 1 || skip) {
            let header = s.node(owner).parent.is_some_and(|parent| {
                matches!(
                    s.node(parent).kind.as_str(),
                    "For" | "If" | "Switch" | "Range"
                ) && (s.role(parent, "init") == Some(owner)
                    || s.role(parent, "post") == Some(owner))
            });
            if !header {
                return self
                    .compile_switch_statement(owner, root, id, &hints, constant)
                    .map(Some);
            }
        }
        if !skip {
            if s.node(owner).kind == "Declaration"
                && s.node(owner).mutable
                && hints.len() > 1
                && !s
                    .ancestors(owner)
                    .any(|parent| s.node(parent).kind == "Procedure")
            {
                return self
                    .global_switch_binding(owner, root, id, &hints)
                    .map(Some);
            }
            let value = self.switch_expression(
                id,
                hints,
                constant || s.node(owner).kind == "Declaration" && !s.node(owner).mutable,
            )?;
            self.switch_replacements.insert(root, value);
            self.switch_owners.insert(owner);
            let code = self.render(owner, None, constant);
            self.switch_owners.remove(&owner);
            self.switch_replacements.remove(&root);
            return code.map(Some);
        }
        let post = s.node(owner).parent.is_some_and(|parent| {
            s.node(parent).kind == "For" && s.role(parent, "post") == Some(owner)
        });
        let lhs = s.group(owner, "lhs").to_vec();
        let (name, args) = self.closed(
            owner,
            "switch_assignment",
            if post { Some("bool") } else { None },
            |this| {
                let mut code = vec![];
                let mut targets = vec![];
                let mut local_hints = vec![];
                for (i, lhs) in lhs.iter().enumerate() {
                    if s.text(*lhs) == "_" {
                        targets.push(this.g("_", *lhs));
                        local_hints.push(None);
                        continue;
                    }
                    let name = format!("{}switch_lhs_{i}", this.prefix);
                    let target = this.unparen(*lhs);
                    let value = if s.node(target).kind == "Index"
                        && matches!(this.typ(this.role(target, "expr")?), Type::Map { .. })
                    {
                        let base = this.render(this.role(target, "expr")?, None, false)?;
                        let key = this.render(this.role(target, "index")?, None, false)?;
                        code.extend([
                            this.g(format!("{name} := &("), *lhs),
                            base,
                            this.g(")\n", *lhs),
                            this.g(format!("{name}_key := "), *lhs),
                            key,
                            this.g("\n", *lhs),
                        ]);
                        this.g(format!("{name}^[{name}_key]"), *lhs)
                    } else {
                        let value = this.render(*lhs, None, false)?;
                        code.extend([
                            this.g(format!("{name} := &("), *lhs),
                            value,
                            this.g(")\n", *lhs),
                        ]);
                        this.g(format!("{name}^"), *lhs)
                    };
                    let type_of = this.native("type_of");
                    local_hints.push(Some(Hint {
                        semantic: Some(this.typ(*lhs)),
                        code: Code::concat([
                            this.g(format!("{type_of}("), *lhs),
                            value.clone(),
                            this.g(")", *lhs),
                        ]),
                        element_only: false,
                        plain_array: false,
                    }));
                    targets.push(value);
                }
                code.push(this.native_switch(id, &local_hints, Some(&targets), None)?);
                if post {
                    code.push(this.g("\nreturn true", owner));
                }
                Ok(Code::concat(code))
            },
        )?;
        let call = self.helper_call(owner, &name, args);
        Ok(Some(if post {
            Code::concat([self.g("_ = ", owner), call])
        } else {
            call
        }))
    }
}
