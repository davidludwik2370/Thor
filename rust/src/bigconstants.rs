//! Host-side source constants. Unknown expressions stay with native evaluation.
use super::*;
use serde_json::Value;
#[derive(Debug)]
enum Failure {
    Unknown,
    Invalid(String),
}
impl From<String> for Failure {
    fn from(value: String) -> Self {
        Self::Invalid(value)
    }
}
fn kind_of(typ: &Type) -> Option<&'static str> {
    match typ {
        Type::BigInteger => Some("big_integer"),
        Type::BigRational => Some("big_rational"),
        _ => None,
    }
}
type FoldResult = std::result::Result<Value, Failure>;
impl Lower<'_, '_> {
    fn constant_child(&self, r: Ref, role: &str) -> std::result::Result<Ref, Failure> {
        self.resolver.sources[r.file]
            .role(r.node, role)
            .map(|node| Ref { file: r.file, node })
            .ok_or(Failure::Unknown)
    }
    fn big_constant_value(
        &mut self,
        r: Ref,
        kind: Option<&'static str>,
        seen: &mut HashSet<Ref>,
    ) -> FoldResult {
        if !seen.insert(r) {
            return Err(Failure::Unknown);
        }
        let result = self.big_constant_inner(r, kind, seen);
        seen.remove(&r);
        result
    }
    fn big_constant_inner(
        &mut self,
        r: Ref,
        kind: Option<&'static str>,
        seen: &mut HashSet<Ref>,
    ) -> FoldResult {
        if let Some(value) = self.logical.constants.get(&r).or_else(||self.resolver.evaluated.get(&r)) {
            return Ok(value.clone());
        }
        let source = &self.resolver.sources[r.file];
        let node = source.node(r.node);
        if node.kind == "Paren" {
            return self.big_constant_value(self.constant_child(r, "expr")?, kind, seen);
        }
        if matches!(source.text(r.node), "true" | "false") && self.resolver.symbol(r).is_none() {
            return Ok(json!({"kind":"bool","value":source.text(r.node)=="true"}));
        }
        match node.kind.as_str() {
            "Literal" if matches!(node.op.as_str(), "integer" | "float") => {
                let number = crate::bigvalues::literal(source.text(r.node))?;
                Ok(crate::bigvalues::packet(
                    kind.unwrap_or("big_rational"),
                    &number,
                )?)
            }
            "Ident" | "Selector" => {
                if node.kind == "Selector" {
                    let base = self.constant_child(r, "expr")?;
                    let field = source
                        .text(source.role(r.node, "field").ok_or(Failure::Unknown)?)
                        .to_string();
                    if matches!(self.resolver.type_of(base), Type::BigRational)
                        && matches!(field.as_str(), "numerator" | "denominator")
                    {
                        let value = self.big_constant_value(base, None, seen)?;
                        if value["kind"] != "big_rational" {
                            return Err(Failure::Unknown);
                        }
                        let number = crate::bigvalues::number(&value)?;
                        return Ok(crate::bigvalues::packet(
                            "big_integer",
                            &crate::bigvalues::Fraction::integer(if field == "numerator" {
                                number.numerator().clone()
                            } else {
                                number.denominator().clone()
                            }),
                        )?);
                    }
                }
                let symbol = self
                    .resolver
                    .symbol(r)
                    .map(|index| self.resolver.symbols[index].clone())
                    .ok_or(Failure::Unknown)?;
                let owner = &self.resolver.sources[symbol.reference.file];
                if !symbol.constant || owner.node(symbol.reference.node).kind != "Declaration" {
                    return Err(Failure::Unknown);
                }
                let annotation = if let Some(annotation) = symbol.annotation {
                    Some(
                        kind_of(&self.resolver.type_of(Ref {
                            file: symbol.reference.file,
                            node: annotation,
                        }))
                        .ok_or(Failure::Unknown)?,
                    )
                } else {
                    None
                };
                self.big_constant_value(
                    Ref {
                        file: symbol.reference.file,
                        node: symbol.value.ok_or(Failure::Unknown)?,
                    },
                    annotation.or(kind),
                    seen,
                )
            }
            "Call" => {
                let callee = self.constant_child(r, "callee")?;
                let target = self.resolver.type_of(callee);
                let constructor = self
                    .resolver
                    .denotes_type(callee)
                    .then(|| kind_of(&target))
                    .flatten();
                let Some(target) = constructor else {
                    return self.big_constant_builtin(r, kind, seen);
                };
                let args = source.group(r.node, "args").to_vec();
                let mut named = HashMap::new();
                let mut positional = vec![];
                for arg in &args {
                    if source.node(*arg).kind == "FieldValue" {
                        let name = source
                            .text(source.role(*arg, "field").ok_or(Failure::Unknown)?)
                            .to_string();
                        let value = source.role(*arg, "value").ok_or(Failure::Unknown)?;
                        if named.insert(name, value).is_some() {
                            return Err(Failure::Invalid(
                                "Repeated big-number constructor argument.".into(),
                            ));
                        }
                    } else {
                        positional.push(*arg);
                    }
                }
                if named
                    .keys()
                    .any(|name| !matches!(name.as_str(), "value" | "numerator" | "denominator"))
                {
                    return Err(Failure::Unknown);
                }
                if target == "big_integer" || args.len() == 1 {
                    positional.extend(named.get("value"));
                    if positional.len() != 1 {
                        return Err(Failure::Invalid("A numeric constructor requires one value or a numerator/denominator pair.".into()));
                    }
                    let value = self.big_constant_value(
                        Ref {
                            file: r.file,
                            node: positional[0],
                        },
                        Some(target),
                        seen,
                    )?;
                    if target == "big_integer" && value["kind"] == "big_rational" {
                        return Err(Failure::Invalid("Integer/Rational constructors require exact integer inputs or text; convert Rational values explicitly first.".into()));
                    }
                    return Ok(crate::bigvalues::packet(
                        target,
                        &crate::bigvalues::number(&value)?,
                    )?);
                }
                if args.len() != 2 {
                    return Err(Failure::Invalid(
                        "Rational requires a numerator and denominator.".into(),
                    ));
                }
                let mut values = vec![];
                for (index, name) in ["numerator", "denominator"].iter().enumerate() {
                    let node = named
                        .get(*name)
                        .copied()
                        .or_else(|| positional.get(index).copied())
                        .ok_or_else(|| {
                            Failure::Invalid(
                                "Rational requires a numerator and denominator.".into(),
                            )
                        })?;
                    let value = self.big_constant_value(
                        Ref { file: r.file, node },
                        Some("big_integer"),
                        seen,
                    )?;
                    if value["kind"] != "big_integer" {
                        return Err(Failure::Invalid("Rational numerator and denominator require Integer or native integer inputs.".into()));
                    }
                    values.push(crate::bigvalues::number(&value)?);
                }
                if values[1].numerator() == &num_bigint::BigInt::from(0) {
                    return Err(Failure::Invalid(
                        "A Rational denominator must not be zero.".into(),
                    ));
                }
                let number = crate::bigvalues::Fraction::new(
                    values[0].numerator().clone(),
                    values[1].numerator().clone(),
                )?;
                Ok(crate::bigvalues::packet("big_rational", &number)?)
            }
            "Unary" => {
                let value = self.big_constant_value(self.constant_child(r, "expr")?, kind, seen)?;
                if node.op == "~" && value["kind"] != "big_integer" {
                    return Err(Failure::Unknown);
                }
                Ok(crate::bigconstantvalue::unary(&value, &node.op)?)
            }
            "Binary" => {
                let left = self.constant_child(r, "left")?;
                let right = self.constant_child(r, "right")?;
                if matches!(node.op.as_str(), "in" | "not_in") {
                    return self.big_constant_membership(r, left, right, seen);
                }
                let shapes = [self.resolver.type_of(left), self.resolver.type_of(right)];
                let chosen = kind
                    .or_else(|| shapes.iter().find_map(kind_of))
                    .ok_or(Failure::Unknown)?;
                for (value, shape) in [left, right].into_iter().zip(&shapes) {
                    if let Some(kind) = kind_of(shape) {
                        if kind != chosen {
                            return Err(Failure::Invalid("Integer/Rational operands must have matching types; use an explicit conversion.".into()));
                        }
                    } else if !self.resolver.untyped_operand(value) {
                        return Err(Failure::Unknown);
                    }
                }
                let a = self.big_constant_value(left, Some(chosen), seen)?;
                let b = self.big_constant_value(right, Some(chosen), seen)?;
                Ok(crate::bigconstantvalue::binary(&a, &b, &node.op)?)
            }
            "CompileSwitchExpr" => {
                if let Some(problem) = self.constant_case_problem(r, seen)? {
                    return Err(Failure::Invalid(problem));
                }
                let mut default = None;
                for case in node.data["cases"].as_array().ok_or(Failure::Unknown)? {
                    let matches = case["matches"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .map(|span| self.constant_at(r, span))
                        .collect::<std::result::Result<Vec<_>, _>>()?;
                    if matches.is_empty() {
                        default = Some(case);
                        continue;
                    }
                    let selected =
                        if let Some(selected) = self.constant_case_value(r, &matches, seen)? {
                            selected
                        } else {
                            if source.role(r.node, "selector").is_some() {
                                return Err(Failure::Unknown);
                            }
                            let mut matched = false;
                            for value in matches {
                                let value = self.big_constant_value(value, None, seen)?;
                                if value == json!({"kind":"bool","value":true}) {
                                    matched = true;
                                    break;
                                }
                            }
                            matched
                        };
                    if selected {
                        return self.big_constant_value(
                            self.constant_at(r, &case["result"])?,
                            kind,
                            seen,
                        );
                    }
                }
                if let Some(case) = default {
                    return self.big_constant_value(
                        self.constant_at(r, &case["result"])?,
                        kind,
                        seen,
                    );
                }
                Err(Failure::Unknown)
            }
            "Ternary" | "WhenExpr" => {
                let cond = self.big_constant_value(self.constant_child(r, "cond")?, None, seen)?;
                if cond["kind"] != "bool" {
                    return Err(Failure::Unknown);
                }
                self.big_constant_value(
                    self.constant_child(r, if cond["value"] == true { "x" } else { "y" })?,
                    kind,
                    seen,
                )
            }
            _ => Err(Failure::Unknown),
        }
    }
}
impl Lower<'_, '_> {
    pub(super) fn big_constant_view(&mut self, id: NodeId, value: &Value) -> Result<Code> {
        let module = self.module("big");
        let mut backing = crate::bigstatic::Backing::new(&self.prefix, &module);
        let view = backing
            .view(value)
            .map_err(|error| Error::at(self.source, self.source.node(id).span, error))?;
        for (name, text) in backing.declarations {
            let key = (NodeId(0), self.prefix.clone(), name);
            if !self.helpers.contains_key(&key) {
                self.helpers.insert(key, self.g(text, id));
            }
        }
        Ok(self.g(view, id))
    }
    pub(super) fn big_constant_render(
        &mut self,
        id: NodeId,
        hint: Option<&Hint>,
    ) -> Result<Option<Code>> {
        let kind = hint.and_then(|h| h.semantic.as_ref()).and_then(kind_of);
        let typ = self.typ(id);
        let node = self.source.node(id);
        let mut concrete = typ.big_name().is_some();
        if node.kind == "Call" {
            let callee = self.role(id, "callee")?;
            if crate::extremaplan::name(self.resolver, self.reference(callee), &mut HashSet::new())
                .is_some()
                || ["abs", "clamp"]
                    .iter()
                    .any(|name| self.resolver.native_builtin(self.reference(callee), name))
            {
                concrete |= self
                    .source
                    .group(id, "args")
                    .iter()
                    .any(|value| self.typ(*value).big_name().is_some());
            }
        }
        if node.kind == "Binary" {
            concrete |= ["left", "right"]
                .iter()
                .filter_map(|role| self.source.role(id, role))
                .any(|value| self.typ(value).big_name().is_some());
        }
        if node.kind == "CompileSwitchExpr" {
            let values = node.data["cases"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|case| {
                    Span::from_wire(&case["result"]).and_then(|span| self.source.at(span, id))
                })
                .collect::<Vec<_>>();
            concrete |= values
                .into_iter()
                .any(|value| self.typ(value).big_name().is_some());
        }
        if kind.is_none() && !concrete {
            return Ok(None);
        }
        let value = match self.big_constant_value(self.reference(id), kind, &mut HashSet::new()) {
            Ok(value) => value,
            Err(Failure::Unknown) => return Ok(None),
            Err(Failure::Invalid(message)) => return Ok(Some(self.error_token(id, &message))),
        };
        self.logical
            .constants
            .insert(self.reference(id), value.clone());
        self.resolver
            .remember_logical_constant(self.reference(id), value.clone());
        if value["kind"] == "bool" {
            return Ok(Some(self.g(
                if value["value"] == true {
                    "true"
                } else {
                    "false"
                },
                id,
            )));
        }
        self.big_constant_view(id, &value).map(Some)
    }
    pub(super) fn big_constant_readonly(&mut self, id: NodeId) -> bool {
        let mut root = id;
        loop {
            if let Some(symbol) = self
                .resolver
                .symbol(self.reference(root))
                .map(|index| self.resolver.symbols[index].clone())
            {
                if self.resolver.sources[symbol.reference.file]
                    .node(symbol.reference.node)
                    .kind
                    == "Declaration"
                    && symbol.constant
                    && self.typ(root).big_name().is_some()
                {
                    return true;
                }
            }
            if !matches!(
                self.source.node(root).kind.as_str(),
                "Selector" | "Index" | "Paren"
            ) {
                return false;
            }
            let Some(base) = self.source.role(root, "expr") else {
                return false;
            };
            root = base;
        }
    }
    pub(super) fn big_constant_declaration(&mut self, id: NodeId) -> Result<Option<Code>> {
        let node = self.source.node(id);
        if self.logical.constant_busy.contains(&id) || node.kind != "Declaration" || node.mutable {
            return Ok(None);
        }
        let hint = if let Some(annotation) = self.source.role(id, "type") {
            let semantic = self.typ(annotation);
            if semantic.big_name().is_some() {
                Some(Hint {
                    code: self.render(annotation, None, false)?,
                    semantic: Some(semantic),
                    element_only: false,
                    plain_array: false,
                })
            } else {
                None
            }
        } else {
            None
        };
        let values = self.source.group(id, "values").to_vec();
        for value in &values {
            self.big_constant_render(*value, hint.as_ref())?;
        }
        if !values.iter().any(|value| {
            self.logical
                .constants
                .get(&self.reference(*value))
                .is_some_and(crate::bigvalues::contains)
        }) {
            return Ok(None);
        }
        let Some(separator) = self.source.node(id).spans.get("separator").copied() else {
            return Ok(Some(self.error_token(
                id,
                "The frontend did not provide this numeric constant declaration separator.",
            )));
        };
        self.logical.constant_busy.insert(id);
        let edit = self.edits.len();
        self.edits.push((separator, self.g("=", id)));
        let code = self.render(id, None, false);
        self.edits.remove(edit);
        self.logical.constant_busy.remove(&id);
        code.map(Some)
    }
}
impl Lower<'_, '_> {
    fn constant_at(&self, r: Ref, span: &Value) -> std::result::Result<Ref, Failure> {
        Span::from_wire(span)
            .and_then(|span| self.resolver.sources[r.file].at(span, r.node))
            .map(|node| Ref { file: r.file, node })
            .ok_or(Failure::Unknown)
    }
    fn constant_compare(a: &Value, b: &Value, op: &str) -> std::result::Result<bool, Failure> {
        Ok(crate::bigconstantvalue::binary(a, b, op)?["value"] == true)
    }
    fn constant_case_value(
        &mut self,
        r: Ref,
        matches: &[Ref],
        seen: &mut HashSet<Ref>,
    ) -> std::result::Result<Option<bool>, Failure> {
        let source = &self.resolver.sources[r.file];
        let Some(selector) = source
            .role(r.node, "selector")
            .map(|node| Ref { file: r.file, node })
        else {
            return Ok(None);
        };
        let Some(kind) = kind_of(&self.resolver.type_of(selector)) else {
            return Ok(None);
        };
        let selected = self.big_constant_value(selector, None, seen)?;
        for value in matches {
            let n = source.node(value.node);
            if n.kind == "Binary" && matches!(n.op.as_str(), ".." | "..<" | "..=") {
                let low = self.big_constant_value(
                    self.constant_child(*value, "left")?,
                    Some(kind),
                    seen,
                )?;
                let high = self.big_constant_value(
                    self.constant_child(*value, "right")?,
                    Some(kind),
                    seen,
                )?;
                if Self::constant_compare(&low, &selected, "<=")?
                    && Self::constant_compare(
                        &selected,
                        &high,
                        if n.op == "..=" { "<=" } else { "<" },
                    )?
                {
                    return Ok(Some(true));
                }
            } else {
                let value = self.big_constant_value(*value, Some(kind), seen)?;
                if Self::constant_compare(&selected, &value, "==")? {
                    return Ok(Some(true));
                }
            }
        }
        Ok(Some(matches.is_empty()))
    }
    fn constant_case_problem(
        &mut self,
        r: Ref,
        seen: &mut HashSet<Ref>,
    ) -> std::result::Result<Option<String>, Failure> {
        let source = &self.resolver.sources[r.file];
        let node = source.node(r.node);
        let Some(selector) = source
            .role(r.node, "selector")
            .map(|node| Ref { file: r.file, node })
        else {
            return Ok(None);
        };
        let Some(kind) = kind_of(&self.resolver.type_of(selector)) else {
            return Ok(None);
        };
        let mut intervals: Vec<(Value, Value, bool)> = vec![];
        for case in node.data["cases"].as_array().ok_or(Failure::Unknown)? {
            for span in case["matches"].as_array().into_iter().flatten() {
                let value = self.constant_at(r, span)?;
                let n = source.node(value.node);
                let bounds: std::result::Result<(Value, Value, bool), Failure> = (|| {
                    if n.kind == "Binary" && matches!(n.op.as_str(), ".." | "..<" | "..=") {
                        let low = self.big_constant_value(
                            self.constant_child(value, "left")?,
                            Some(kind),
                            seen,
                        )?;
                        let high = self.big_constant_value(
                            self.constant_child(value, "right")?,
                            Some(kind),
                            seen,
                        )?;
                        Ok((low, high, n.op == "..="))
                    } else {
                        let low = self.big_constant_value(value, Some(kind), seen)?;
                        Ok((low.clone(), low, true))
                    }
                })(
                );
                let Ok((low, high, closed)) = bounds else {
                    continue;
                };
                if Self::constant_compare(&low, &high, ">")?
                    || Self::constant_compare(&low, &high, "==")? && !closed
                {
                    return Ok(Some(
                        "A switch case range must not be empty or reversed.".into(),
                    ));
                }
                for (a, b, inclusive) in &intervals {
                    let lower = if Self::constant_compare(&low, a, ">")? {
                        &low
                    } else {
                        a
                    };
                    let upper = if Self::constant_compare(&high, b, "<")? {
                        &high
                    } else {
                        b
                    };
                    if Self::constant_compare(lower, upper, "<")?
                        || Self::constant_compare(lower, upper, "==")?
                            && (Self::constant_compare(lower, &high, "<")? || closed)
                            && (Self::constant_compare(lower, b, "<")? || *inclusive)
                    {
                        return Ok(Some(
                            "Duplicate or overlapping constant switch cases.".into(),
                        ));
                    }
                }
                intervals.push((low, high, closed));
            }
        }
        Ok(None)
    }
    pub(super) fn big_compile_case_problem(&mut self, id: NodeId) -> Option<String> {
        self.constant_case_problem(self.reference(id), &mut HashSet::new())
            .ok()
            .flatten()
    }
    pub(super) fn big_compile_case(&mut self, id: NodeId, matches: &[NodeId]) -> Option<bool> {
        let matches = matches
            .iter()
            .map(|id| self.reference(*id))
            .collect::<Vec<_>>();
        self.constant_case_value(self.reference(id), &matches, &mut HashSet::new())
            .ok()
            .flatten()
    }
}
impl Lower<'_, '_> {
    fn big_constant_builtin(
        &mut self,
        r: Ref,
        kind: Option<&'static str>,
        seen: &mut HashSet<Ref>,
    ) -> FoldResult {
        let callee = self.constant_child(r, "callee")?;
        let source = &self.resolver.sources[r.file];
        let name = crate::extremaplan::name(self.resolver, callee, &mut HashSet::new())
            .or_else(|| {
                ["abs", "clamp"]
                    .iter()
                    .find(|name| self.resolver.native_builtin(callee, name))
                    .map(|name| (*name).to_string())
            })
            .ok_or(Failure::Unknown)?;
        let args = source.group(r.node, "args").to_vec();
        let mut chosen = kind;
        for value in &args {
            chosen = chosen.or_else(|| {
                kind_of(&self.resolver.type_of(Ref {
                    file: r.file,
                    node: *value,
                }))
            });
        }
        let chosen = chosen.ok_or(Failure::Unknown)?;
        let mut values = vec![];
        for node in args {
            let at = Ref { file: r.file, node };
            let shape = self.resolver.type_of(at);
            if let Some(kind) = kind_of(&shape) {
                if kind != chosen {
                    return Err(Failure::Invalid(
                        "Integer/Rational operands must have matching types.".into(),
                    ));
                }
            } else if !self.resolver.untyped_operand(at) {
                return Err(Failure::Unknown);
            }
            values.push(self.big_constant_value(at, Some(chosen), seen)?);
        }
        match name.as_str() {
            "abs" if values.len() == 1 => {
                let number = crate::bigvalues::number(&values[0])?;
                if number.numerator() < &num_bigint::BigInt::from(0) {
                    Ok(crate::bigconstantvalue::unary(&values[0], "-")?)
                } else {
                    Ok(values.remove(0))
                }
            }
            "min" | "max" if !values.is_empty() => {
                let mut result = values.remove(0);
                for value in values {
                    if Self::constant_compare(
                        &value,
                        &result,
                        if name == "min" { "<" } else { ">" },
                    )? {
                        result = value;
                    }
                }
                Ok(result)
            }
            "clamp" if values.len() == 3 => {
                let mut result = values[0].clone();
                if Self::constant_compare(&result, &values[1], "<")? {
                    result = values[1].clone();
                }
                if Self::constant_compare(&result, &values[2], ">")? {
                    result = values[2].clone();
                }
                Ok(result)
            }
            _ => Err(Failure::Unknown),
        }
    }
}
impl Lower<'_, '_> {
    fn big_constant_membership(
        &mut self,
        r: Ref,
        left: Ref,
        mut right: Ref,
        seen: &mut HashSet<Ref>,
    ) -> FoldResult {
        let source = &self.resolver.sources[r.file];
        while source.node(right.node).kind == "Paren" {
            right = self.constant_child(right, "expr")?;
        }
        if source.node(right.node).kind != "Compound"
            || source
                .node(right.node)
                .spans
                .get("open")
                .is_none_or(|span| source.slice(*span) != "[")
        {
            return Err(Failure::Unknown);
        }
        let elems = source.group(right.node, "elems").to_vec();
        let mut target = kind_of(&self.resolver.type_of(left));
        for value in &elems {
            target = target.or_else(|| {
                kind_of(&self.resolver.type_of(Ref {
                    file: r.file,
                    node: *value,
                }))
            });
        }
        let kind = target.ok_or(Failure::Unknown)?;
        let result: std::result::Result<bool, Failure> = (|| {
            let value = self.big_constant_value(left, Some(kind), seen)?;
            let needle = crate::bigvalues::number(&value)?;
            let mut found = false;
            for node in elems {
                let value =
                    self.big_constant_value(Ref { file: r.file, node }, Some(kind), seen)?;
                if crate::bigvalues::number(&value)? == needle {
                    found = true;
                    break;
                }
            }
            Ok(found)
        })();
        let found = match result {
            Ok(found) => found,
            Err(_) => {
                return Err(Failure::Invalid(
                    "Compile-time numeric membership requires matching known values.".into(),
                ))
            }
        };
        Ok(json!({"kind":"bool","value":if source.node(r.node).op=="in"{found}else{!found}}))
    }
}
