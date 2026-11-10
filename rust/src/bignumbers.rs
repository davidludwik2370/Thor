//! Nominal logical numbers and direct ownership; aggregates remain shallow.
use super::*;
#[derive(Default)]
pub(super) struct State {
    pub enabled: Option<bool>,
    pub busy: HashSet<NodeId>,
    pub integration_busy: HashSet<NodeId>,
    pub pure_storage_busy: HashSet<NodeId>,
    pub owning: HashSet<NodeId>,
    pub replacements: HashMap<NodeId, Code>,
    pub globalizing: usize,
    pub constants: HashMap<Ref, serde_json::Value>,
    pub constant_busy: HashSet<NodeId>,
}
impl Lower<'_, '_> {
    fn big_module(&mut self) -> String {
        self.module("big")
    }
    pub(super) fn big_enabled(&mut self) -> bool {
        if let Some(enabled) = self.logical.enabled {
            return enabled;
        }
        let enabled = self.resolver.logical_enabled(self.file);
        self.logical.enabled = Some(enabled);
        enabled
    }
    fn big_literal_at(&self, r: Ref, seen: &mut HashSet<Ref>) -> Option<String> {
        if !seen.insert(r) {
            return None;
        }
        let s = &self.resolver.sources[r.file];
        let n = s.node(r.node);
        match n.kind.as_str() {
            "Paren" => self.big_literal_at(
                Ref {
                    file: r.file,
                    node: s.role(r.node, "expr")?,
                },
                seen,
            ),
            "Literal" if matches!(n.op.as_str(), "integer" | "float") => {
                Some(s.text(r.node).into())
            }
            "Unary" if matches!(n.op.as_str(), "+" | "-") => Some(format!(
                "{}{}",
                n.op,
                self.big_literal_at(
                    Ref {
                        file: r.file,
                        node: s.role(r.node, "expr")?
                    },
                    seen
                )?
            )),
            "Ident" => {
                let symbol = &self.resolver.symbols[self.resolver.symbol(r)?];
                let owner = &self.resolver.sources[symbol.reference.file];
                if !symbol.constant
                    || symbol.annotation.is_some()
                    || owner.node(symbol.reference.node).kind != "Declaration"
                {
                    return None;
                }
                self.big_literal_at(
                    Ref {
                        file: symbol.reference.file,
                        node: symbol.value?,
                    },
                    seen,
                )
            }
            _ => None,
        }
    }
    pub(super) fn big_literal(&self, id: NodeId) -> Option<String> {
        self.big_literal_at(self.reference(id), &mut HashSet::new())
    }
    pub(super) fn big_constructor_type(&mut self,id:NodeId)->Option<Type>{self.resolver.logical_constructor(self.reference(id))}
    fn big_default_value(&self, id: NodeId) -> bool {
        for ancestor in self.source.ancestors(id) {
            let node = self.source.node(ancestor);
            if node.kind == "Procedure"
                && self.source.role(ancestor, "body").is_some_and(|body| {
                    self.source
                        .node(body)
                        .span
                        .contains(self.source.node(id).span)
                })
            {
                return false;
            }
            if node.kind == "Field"
                && self.source.role(ancestor, "default").is_some_and(|value| {
                    self.source
                        .node(value)
                        .span
                        .contains(self.source.node(id).span)
                })
            {
                return true;
            }
        }
        false
    }
    fn big_render_literal(
        &mut self,
        id: NodeId,
        typ: &Type,
        constant: bool,
    ) -> Result<Option<Code>> {
        let Some(text) = self.big_literal(id) else {
            return Ok(None);
        };
        if constant {
            return Ok(Some(self.error_token(
                id,
                "This Integer/Rational literal is not a valid exact compile-time number.",
            )));
        }
        if matches!(typ, Type::BigInteger)
            && crate::bigvalues::literal(&text)
                .is_ok_and(|number| number.denominator() != &num_bigint::BigInt::from(1))
        {
            return Ok(Some(self.error_token(
                id,
                "An Integer literal must represent a whole number exactly.",
            )));
        }
        let method = if matches!(typ, Type::BigInteger) {
            "literal_integer"
        } else {
            "literal_rational"
        };
        let module = self.big_module();
        Ok(Some(
            self.g(format!("{module}.{method}({})", json!(text)), id),
        ))
    }
    fn big_generic_literal(
        &mut self,
        id: NodeId,
        hint: Hint,
        constant: bool,
    ) -> Result<Option<Code>> {
        if constant || self.big_default_value(id) {
            return Ok(None);
        }
        let Some(text) = self.big_literal(id) else {
            return Ok(None);
        };
        let module = self.big_module();
        let (name,mut args)=self.closed(id,"big_literal",Some(""),|this|{
            let target=format!("{}Result",this.prefix);
            Ok(this.g(format!("when {target}=={module}.Integer {{return {module}.literal_integer({})}} else when {target}=={module}.Rational {{return {module}.literal_rational({})}} else {{value:{target}={text};return value}}",json!(text),json!(text)),id))
        })?;
        args.push(hint.code);
        Ok(Some(self.helper_call(id, &name, args)))
    }
    fn big_render_constructor(&mut self, id: NodeId, typ: Type, constant: bool) -> Result<Code> {
        if constant {
            return Ok(self.error_token(id,"A compile-time Integer/Rational constructor requires exact known numeric inputs; use #run for text parsing or procedure calls."));
        }
        let args = self.source.group(id, "args").to_vec();
        let values = args
            .iter()
            .filter_map(|arg| {
                if self.source.node(*arg).kind == "FieldValue" {
                    let field = self.source.role(*arg, "field")?;
                    if matches!(self.source.text(field), "allocator" | "loc") {
                        return None;
                    }
                    self.source.role(*arg, "value")
                } else {
                    Some(*arg)
                }
            })
            .collect::<Vec<_>>();
        if !(values.len() == 1 || matches!(typ, Type::BigRational) && values.len() == 2) {
            return Ok(self.error_token(id,"Integer expects one value; Rational expects one value or a numerator/denominator pair."));
        }
        let names = args
            .iter()
            .filter(|arg| self.source.node(**arg).kind == "FieldValue")
            .filter_map(|arg| self.source.role(*arg, "field"))
            .map(|name| self.source.text(name))
            .collect::<Vec<_>>();
        let allowed = if values.len() == 1 {
            ["allocator", "loc", "value", "text"].as_slice()
        } else {
            ["allocator", "loc", "numerator", "denominator"].as_slice()
        };
        let mut seen = HashSet::new();
        if names
            .iter()
            .any(|name| !seen.insert(*name) || !allowed.contains(name))
        {
            return Ok(self.error_token(
                id,
                "Invalid or repeated Integer/Rational constructor argument name.",
            ));
        }
        if values.len() == 1 && names.contains(&"text") && self.big_literal(values[0]).is_some() {
            return Ok(
                self.error_token(values[0], "The text constructor argument must be a string.")
            );
        }
        for value in &values {
            let shape = self.typ(*value);
            let integer = matches!(&shape,Type::Scalar{name,..} if name=="int"||name=="uint"||name=="uintptr"||name=="byte"||name=="rune"||name.starts_with(['i','u'])&&name[1..].bytes().all(|byte|byte.is_ascii_digit()));
            if self.big_literal(*value).is_none()
                && (matches!(shape, Type::Scalar { .. }) && !integer
                    || matches!(shape, Type::BigRational)
                        && (matches!(typ, Type::BigInteger) || values.len() > 1))
            {
                return Ok(self.error_token(*value,"Integer/Rational constructors require exact integer inputs or text; convert floating-point values explicitly first."));
            }
        }
        if values.len() == 1 && self.big_literal(values[0]).is_some() {
            let literal = self.big_render_literal(values[0], &typ, false)?.unwrap();
            if names
                .iter()
                .any(|name| matches!(*name, "allocator" | "loc"))
            {
                // Preserve the selected literal's diagnostics while adding the
                // explicit native allocator/location arguments.
                let mut text = literal.finish().text;
                text.pop();
                let mut code = vec![self.g(text, id)];
                for arg in args
                    .iter()
                    .filter(|arg| self.source.node(**arg).kind == "FieldValue")
                {
                    let field = self.role(*arg, "field")?;
                    if matches!(self.source.text(field), "allocator" | "loc") {
                        code.extend([self.g(",", id), self.render(*arg, None, false)?]);
                    }
                }
                code.push(self.g(")", id));
                return Ok(Code::concat(code));
            }
            return Ok(literal);
        }
        if values.len() == 1 && self.resolver.untyped_operand(self.reference(values[0])) {
            let module = self.big_module();
            let hint = Hint {
                code: self.g(format!("{module}.{}", typ.big_name().unwrap()), id),
                semantic: Some(typ.clone()),
                element_only: false,
                plain_array: false,
            };
            if self.big_handles(values[0], Some(&hint)) {
                return self.render(values[0], Some(hint), false);
            }
        }
        if matches!(typ, Type::BigRational) && values.len() == 2 {
            let denominator = args
                .iter()
                .find(|arg| {
                    self.source.node(**arg).kind == "FieldValue"
                        && self
                            .source
                            .role(**arg, "field")
                            .is_some_and(|field| self.source.text(field) == "denominator")
                })
                .and_then(|arg| self.source.role(*arg, "value"))
                .unwrap_or(values[1]);
            if self.big_statically_zero(denominator) {
                return Ok(
                    self.error_token(denominator, "A Rational denominator must not be zero.")
                );
            }
        }
        let count = if values.iter().any(|value| self.typ(*value).is_string()) {
            2
        } else {
            1
        };
        let owned = args
            .iter()
            .map(|arg| {
                let value = self.source.role(*arg, "value").unwrap_or(*arg);
                self.big_literal(value).is_some() || self.temporary_producer(value)
            })
            .collect::<Vec<_>>();
        let module = self.big_module();
        let method = if matches!(typ, Type::BigInteger) {
            "integer"
        } else {
            "rational"
        };
        let build = |this: &mut Self, release: bool| -> Result<Code> {
            let mut code = vec![];
            let mut actual = vec![];
            for (index, arg) in args.iter().enumerate() {
                let value = this.source.role(*arg, "value").unwrap_or(*arg);
                let expression = if this.big_literal(value).is_some() {
                    this.big_render_literal(value, &Type::BigInteger, false)?
                        .unwrap()
                } else {
                    this.render(value, None, false)?
                };
                let rendered = if release {
                    let name = format!(
                        "{}big_arg_{}_{}",
                        this.prefix,
                        this.source.node(id).span.start,
                        index
                    );
                    code.extend([
                        this.g(format!("{name}:="), value),
                        expression,
                        this.g("\n", value),
                    ]);
                    let captured = this
                        .fstring_frames
                        .iter()
                        .any(|(_, targets)| targets.contains(&value));
                    if owned[index] && !captured {
                        code.push(this.g(
                            format!("defer {module}.compiler_destroy_value({name})\n"),
                            value,
                        ));
                    }
                    this.g(name, value)
                } else {
                    expression
                };
                actual.push(if this.source.node(*arg).kind == "FieldValue" {
                    Code::concat([
                        this.copy(this.source.node(this.role(*arg, "field")?).span),
                        this.g("=", *arg),
                        rendered,
                    ])
                } else {
                    rendered
                });
            }
            code.push(this.g(
                format!("{}{module}.{method}(", if release { "return " } else { "" }),
                id,
            ));
            for (index, arg) in actual.into_iter().enumerate() {
                if index > 0 {
                    code.push(this.g(",", id));
                }
                code.push(arg);
            }
            code.push(this.g(")", id));
            Ok(Code::concat(code))
        };
        if !owned.iter().any(|owned| *owned) {
            return build(self, false);
        }
        let result = if count == 1 {
            format!("{module}.{}", typ.big_name().unwrap())
        } else {
            format!("({module}.{},bool)", typ.big_name().unwrap())
        };
        let (name, args) = self.closed(id, "big_constructor", Some(&result), |this| {
            build(this, true)
        })?;
        Ok(self.helper_call(id, &name, args))
    }
}
impl Lower<'_, '_> {
    pub(super) fn big_property(&mut self, id: NodeId) -> Option<String> {
        if self.source.node(id).kind != "Selector" {
            return None;
        }
        let base = self.source.role(id, "expr")?;
        let mut typ = self.typ(base);
        while let Type::Pointer(inner) = typ {
            typ = *inner;
        }
        let field = self.source.text(self.source.role(id, "field")?);
        (matches!(typ, Type::BigRational) && matches!(field, "numerator" | "denominator"))
            .then(|| field.into())
    }
    pub(super) fn big_handles(&mut self, id: NodeId, hint: Option<&Hint>) -> bool {
        self.resolver.logical_operator(
            self.reference(id),
            hint.and_then(|hint| hint.semantic.as_ref()),
        )
    }
    pub(super) fn big_fresh(&mut self, id: NodeId) -> bool {
        let id = self.unparen(id);
        let node = self.source.node(id);
        if node.kind == "Literal" || self.big_literal(id).is_some() {
            return false;
        }
        let typ = self.typ(id);
        match node.kind.as_str() {
            "Binary" | "Unary" if self.big_handles(id, None) => {
                !matches!(node.op.as_str(), "==" | "!=" | "<" | "<=" | ">" | ">=")
            }
            "Selector" => self.big_property(id).is_some() || self.big_dynamic_property(id),
            "Compound" => typ.big_name().is_some(),
            "OrElse" | "OrReturn" | "OrBranch" | "OrPanic" | "Run" => {
                typ.big_name().is_some() || matches!(typ, Type::Generic)
            }
            "Call" if !self.resolver.denotes_type(self.reference(id)) => {
                if typ.big_name().is_some() || self.resolver.logical_numeric_result(self.reference(id)).is_some() {
                    return true;
                }
                let Some(callee) = self.source.role(id, "callee") else {
                    return false;
                };
                let signature = self.resolver.signature(self.reference(callee));
                signature
                    .is_some_and(|signature| !self.resolver.fields(signature, "results").is_empty())
                    && typ.is_unknown()
            }
            _ => false,
        }
    }
    fn big_generic_reference(&mut self, id: NodeId, seen: &mut HashSet<NodeId>) -> bool {
        let id = self.unparen(id);
        if !seen.insert(id) {
            return false;
        }
        if matches!(self.typ(id), Type::Generic) {
            return true;
        }
        let node = self.source.node(id);
        if matches!(node.kind.as_str(), "Selector" | "Index" | "Deref") {
            return self
                .source
                .role(id, "expr")
                .is_some_and(|base| self.big_generic_reference(base, seen));
        }
        if node.kind == "Ident" {
            if let Some(symbol) = self
                .resolver
                .symbol(self.reference(id))
                .map(|index| self.resolver.symbols[index].clone())
            {
                if symbol.reference.file == self.file {
                    return symbol
                        .value
                        .is_some_and(|value| self.big_generic_reference(value, seen));
                }
            }
        }
        false
    }
    fn big_reference_copy(&mut self, id: NodeId, hint: Option<Hint>) -> Result<Code> {
        let target = if let Some(hint) = &hint {
            hint.code.clone()
        } else {
            self.native_type(id)?
        };
        let module = self.big_module();
        let (name,mut args)=self.closed(id,"big_reference_copy",Some(""),|this|{
            let code=this.render(id,hint.clone(),false)?;
            Ok(Code::concat([this.g(format!("when {}Result=={module}.Integer || {}Result=={module}.Rational {{return {module}.compiler_clone(",this.prefix,this.prefix),id),code.clone(),this.g(")} else {return ",id),code,this.g("}",id)]))
        })?;
        args.push(target);
        Ok(self.helper_call(id, &name, args))
    }
    pub(super) fn big_owned(
        &mut self,
        id: NodeId,
        hint: Option<Hint>,
        constant: bool,
    ) -> Result<Code> {
        if !self.big_enabled() || constant || self.logical.owning.contains(&id) {
            return self.render(id, hint, constant);
        }
        if self.source.node(id).kind == "FieldValue" {
            let value = self.role(id, "value")?;
            let rendered = self.big_owned(value, hint, constant)?;
            return self.splice(id, vec![(self.source.node(value).span, rendered)], vec![]);
        }
        let typ = self.typ(id);
        let wanted = hint
            .as_ref()
            .and_then(|hint| hint.semantic.as_ref())
            .filter(|typ| typ.big_name().is_some() || matches!(typ, Type::Generic))
            .cloned()
            .unwrap_or_else(|| typ.clone());
        if matches!(typ, Type::Unknown)
            && matches!(
                self.source.node(id).kind.as_str(),
                "Ident" | "Selector" | "Index" | "Deref" | "Paren"
            )
            && !self.big_fresh(id)
            && self.big_generic_reference(id, &mut HashSet::new())
        {
            return self.big_reference_copy(id, hint);
        }
        if wanted.big_name().is_none() && !matches!(wanted, Type::Generic)
            || self.resolver.denotes_type(self.reference(id))
            || matches!(self.source.text(id), "nil" | "---" | "_")
            || matches!(wanted, Type::Generic)
                && matches!(
                    self.source.node(id).kind.as_str(),
                    "ImplicitSelector" | "Compound"
                )
        {
            return self.render(id, hint, constant);
        }
        if wanted.big_name().is_some()
            && self.logical.globalizing == 0
            && !self
                .source
                .ancestors(id)
                .any(|a| self.source.node(a).kind == "Procedure")
            && self
                .source
                .node(id)
                .parent
                .is_some_and(|p| self.source.node(p).kind == "Declaration")
        {
            let module = self.big_module();
            let result = format!("{module}.{}", wanted.big_name().unwrap());
            let (name, args) = self.closed(id, "big_global", Some(&result), |this| {
                this.logical.globalizing += 1;
                let value = this.big_owned(id, hint.clone(), constant);
                this.logical.globalizing -= 1;
                let runtime = format!("{}big_context", this.prefix);
                this.modules.insert(runtime.clone(), "base:runtime".into());
                Ok(Code::concat([
                    this.g(format!("context={runtime}.default_context()\nreturn "), id),
                    value?,
                ]))
            })?;
            return Ok(self.helper_call(id, &name, args));
        }
        if matches!(
            self.source.node(id).kind.as_str(),
            "Ternary" | "WhenExpr" | "SwitchExpr" | "CompileSwitchExpr"
        ) {
            let target = if let Some(hint) = &hint {
                hint.code.clone()
            } else {
                self.native_type(id)?
            };
            let module = self.big_module();
            let semantic = Some(wanted.clone());
            let (name, mut args) = self.closed(id, "big_owned", Some(""), |this| {
                let hint = Hint {
                    code: this.g(format!("{}Result", this.prefix), id),
                    semantic,
                    element_only: false,
                    plain_array: false,
                };
                let mut code = vec![];
                let value = this.temporary_evaluate(id, &mut code, Some(hint))?;
                code.push(this.g(
                    format!(
                        "return {module}.compiler_own({},{})",
                        value.value, value.owned
                    ),
                    id,
                ));
                Ok(Code::concat(code))
            })?;
            args.push(target);
            return Ok(self.helper_call(id, &name, args));
        }
        self.logical.owning.insert(id);
        let rendered = self.render(id, hint.clone(), constant);
        self.logical.owning.remove(&id);
        let code = rendered?;
        if self.big_fresh(id)
            || self.big_handles(id, hint.as_ref())
            || self.big_literal(id).is_some()
                && hint
                    .as_ref()
                    .and_then(|hint| hint.semantic.as_ref())
                    .is_some_and(|typ| typ.big_name().is_some() || matches!(typ, Type::Generic))
        {
            return Ok(code);
        }
        let module = self.big_module();
        Ok(Code::concat([
            self.g(format!("{module}.compiler_clone("), id),
            code,
            self.g(")", id),
        ]))
    }
    pub(super) fn big_statement<F>(&mut self, id: NodeId, build: F) -> Result<Code>
    where
        F: FnOnce(&mut Self) -> Result<Code>,
    {
        let header = self.source.node(id).parent.is_some_and(|parent| {
            matches!(
                self.source.node(parent).kind.as_str(),
                "For" | "If" | "Switch" | "Range"
            )
        });
        if !header {
            return Ok(Code::concat([
                self.g("{\n", id),
                build(self)?,
                self.g("\n}", id),
            ]));
        }
        let post = self
            .source
            .node(id)
            .parent
            .is_some_and(|parent| self.source.role(parent, "post") == Some(id));
        let (name, args) = self.closed(
            id,
            "big_assign",
            if post { Some("bool") } else { None },
            |this| {
                let code = build(this)?;
                Ok(if post {
                    Code::concat([code, this.g("\nreturn true", id)])
                } else {
                    code
                })
            },
        )?;
        let call = self.helper_call(id, &name, args);
        Ok(if post {
            Code::concat([self.g("_ = ", id), call])
        } else {
            call
        })
    }
    pub(super) fn big_capture_targets(
        &mut self,
        id: NodeId,
        lhs: &[NodeId],
        code: &mut Vec<Code>,
    ) -> Result<Vec<String>> {
        let mut targets = vec![];
        for (index, target) in lhs.iter().enumerate() {
            if self.source.text(*target) == "_" {
                targets.push("_".into());
                continue;
            }
            let name = format!(
                "{}big_target_{}_{}",
                self.prefix,
                self.source.node(id).span.start,
                index
            );
            let root = self.unparen(*target);
            if self.source.node(root).kind == "Index"
                && matches!(self.typ(self.role(root, "expr")?), Type::Map { .. })
            {
                let base = self.role(root, "expr")?;
                let key = self.role(root, "index")?;
                code.extend([
                    self.g(format!("{name}:=&("), *target),
                    self.render_address(base)?,
                    self.g(")\n", *target),
                ]);
                let sets = self.module("sets");
                code.extend([
                    self.g(
                        format!("{name}_key:type_of({sets}.key_witness({name}^))="),
                        key,
                    ),
                    self.render(key, None, false)?,
                    self.g("\n", key),
                ]);
                targets.push(format!("{name}^[{name}_key]"));
            } else {
                code.extend([
                    self.g(format!("{name}:=&("), *target),
                    self.render_address(*target)?,
                    self.g(")\n", *target),
                ]);
                targets.push(format!("{name}^"));
            }
        }
        Ok(targets)
    }
    pub(super) fn big_commit(
        &mut self,
        id: NodeId,
        targets: &[String],
        values: &[String],
        code: &mut Vec<Code>,
    ) {
        let module = self.big_module();
        for (target, value) in targets.iter().zip(values) {
            code.push(self.g(if target=="_"{format!("{module}.compiler_destroy_value({value})\n")}
                else if target.ends_with(']'){format!("{value}_old:={target}\n{module}.compiler_destroy(&{value}_old)\n{target}={value}\n")}
                else{format!("{module}.compiler_destroy(&({target}))\n{target}={value}\n")},id));
        }
    }
    fn big_assignment(&mut self, id: NodeId) -> Result<Option<Code>> {
        let lhs = self.source.group(id, "lhs").to_vec();
        let rhs = self.source.group(id, "rhs").to_vec();
        if self.source.node(id).op != "=" {
            return Ok(None);
        }
        if rhs.len()==1 && matches!(self.source.node(self.unparen(rhs[0])).kind.as_str(),"SwitchExpr"|"CompileSwitchExpr") {
            if let Some(code)=self.switch_statement(id,false)? {return Ok(Some(code));}
        }
        let multiple =
            rhs.len() == 1 && lhs.len() > 1 && self.big_numeric_results(rhs[0], lhs.len());
        if !multiple
            && !lhs.iter().any(|value| {
                self.source.text(*value) != "_"
                    && (self.typ(*value).big_name().is_some() || self.typ(*value).is_unknown())
            })
        {
            return Ok(None);
        }
        let numeric = lhs
            .iter()
            .chain(rhs.iter())
            .any(|value| self.typ(*value).big_name().is_some())
            || lhs
                .iter()
                .any(|value| matches!(self.typ(*value), Type::Generic));
        if !multiple && !numeric {
            return Ok(None);
        }
        if rhs.len() != lhs.len() && rhs.len() != 1 {
            return Ok(None);
        }
        let code = self.big_statement(id, |this| {
            let mut code = vec![];
            let targets = this.big_capture_targets(id, &lhs, &mut code)?;
            let mut values = vec![];
            if rhs.len() == 1 && lhs.len() > 1 {
                values.extend((0..lhs.len()).map(|index| {
                    format!(
                        "{}big_multi_value_{}_{}",
                        this.prefix,
                        this.source.node(id).span.start,
                        index
                    )
                }));
                code.extend([
                    this.g(format!("{}:=", values.join(",")), id),
                    this.render(rhs[0], None, false)?,
                    this.g("\n", id),
                ]);
            } else {
                for (index, (value, target)) in rhs.iter().zip(&targets).enumerate() {
                    let name = format!(
                        "{}big_rhs_{}_{}",
                        this.prefix,
                        this.source.node(id).span.start,
                        index
                    );
                    let hint = (target != "_").then(|| Hint {
                        code: this.g(format!("type_of({target})"), *value),
                        semantic: Some(this.typ(lhs[index])),
                        element_only: false,
                        plain_array: false,
                    });
                    code.push(this.g(
                        if target == "_" {
                            format!("{name}:=")
                        } else {
                            format!("{name}:type_of({target})=")
                        },
                        *value,
                    ));
                    code.extend([this.big_owned(*value, hint, false)?, this.g("\n", *value)]);
                    values.push(name);
                }
            }
            this.big_commit(id, &targets, &values, &mut code);
            Ok(Code::concat(code))
        })?;
        Ok(Some(code))
    }
}
impl Lower<'_, '_> {
    fn big_call_hints(
        &mut self,
        id: NodeId,
        signature: Ref,
        native: bool,
    ) -> Result<Vec<Option<Hint>>> {
        let params = self.resolver.fields(signature, "params");
        let variadic = params.iter().position(|(_, annotation)| {
            annotation
                .is_some_and(|r| self.resolver.sources[r.file].node(r.node).kind == "Ellipsis")
        });
        let callee = self.role(id, "callee")?;
        let args = self.source.group(id, "args").to_vec();
        let plans = self
            .resolver
            .call_plans(self.reference(id), &Default::default());
        let bindings = if plans.len() == 1 {
            plans[0].bindings.clone()
        } else {
            Default::default()
        };
        let mut hints = vec![];
        for (position, arg) in args.iter().enumerate() {
            let value = self.source.role(*arg, "value").unwrap_or(*arg);
            let shifted = position + usize::from(self.source.node(callee).op == "->");
            let index = if self.source.node(*arg).kind == "FieldValue" {
                let name = self.source.text(self.role(*arg, "field")?);
                params
                    .iter()
                    .position(|(parameter, _)| parameter == name)
                    .unwrap_or(shifted)
            } else {
                variadic
                    .filter(|index| shifted >= *index)
                    .unwrap_or(shifted)
            };
            let expanded = self
                .source
                .node(id)
                .spans
                .get("ellipsis")
                .is_some_and(|span| self.source.node(*arg).span.start >= span.end)
                || self.source.node(*arg).kind == "Unary" && self.source.node(*arg).op == "**";
            let element = variadic == Some(index) && !expanded;
            let annotation = params
                .get(index)
                .and_then(|(_, annotation)| *annotation)
                .and_then(|annotation| {
                    if element {
                        let source = &self.resolver.sources[annotation.file];
                        source
                            .role(annotation.node, "expr")
                            .or_else(|| source.node(annotation.node).children.first().copied())
                            .map(|node| Ref {
                                file: annotation.file,
                                node,
                            })
                    } else {
                        Some(annotation)
                    }
                });
            let mut semantic =
                annotation.map(|annotation| self.resolver.specialized_type(annotation, &bindings));
            if element && matches!(semantic, Some(Type::Generic)) {
                if let Some(typ) = args
                    .iter()
                    .map(|value| self.typ(self.source.role(*value, "value").unwrap_or(*value)))
                    .find(|typ| typ.big_name().is_some())
                {
                    semantic = Some(typ);
                }
            }
            if let Some(name) = semantic.as_ref().and_then(Type::big_name) {
                let module = self.big_module();
                hints.push(Some(Hint {
                    code: self.g(format!("{module}.{name}"), value),
                    semantic,
                    element_only: false,
                    plain_array: false,
                }));
            } else if let Some(hint) = self.contextual_hint(value) {
                hints.push(Some(hint));
            } else if annotation.is_some() && !native {
                hints.push(Some(Hint {
                    code: self.g("", value),
                    semantic,
                    element_only: false,
                    plain_array: false,
                }));
            } else if annotation.is_some() {
                self.meta = true;
                self.querying += 1;
                self.logical.busy.insert(id);
                let call = self.render(id, None, false);
                self.logical.busy.remove(&id);
                self.querying -= 1;
                let runtime_index = params[..index]
                    .iter()
                    .filter(|(name, _)| !name.starts_with('$'))
                    .count();
                let mut code = vec![
                    self.g(
                        format!(
                            "#type {}",
                            if element {
                                format!("{}meta.type_elem_type(", self.prefix)
                            } else {
                                String::new()
                            }
                        ),
                        id,
                    ),
                    self.g(
                        format!(
                            "{}meta.type_proc_parameter_type(type_of({}meta.procedure_of(",
                            self.prefix, self.prefix
                        ),
                        id,
                    ),
                    call?,
                    self.g(
                        format!(")),{runtime_index}){}", if element { ")" } else { "" }),
                        id,
                    ),
                ];
                hints.push(Some(Hint {
                    code: Code::concat(std::mem::take(&mut code)),
                    semantic,
                    element_only: false,
                    plain_array: false,
                }));
            } else {
                hints.push(None);
            }
        }
        Ok(hints)
    }
    fn big_consume_call(&mut self, id: NodeId) -> Result<Option<Code>> {
        if self.querying > 0 {
            return Ok(None);
        }
        let callee = self.role(id, "callee")?;
        let excluded = [
            "delete",
            "type_of",
            "size_of",
            "align_of",
            "offset_of",
            "type_info_of",
            "raw_data",
            "new",
            "make",
            "append",
            "copy",
        ]
        .iter()
        .any(|name| self.resolver.native_builtin(self.reference(callee), name));
        if excluded {
            return Ok(None);
        }
        let Some(signature) = self.resolver.signature(self.reference(callee)) else {
            return Ok(None);
        };
        let args = self.source.group(id, "args").to_vec();
        let hints = self.big_call_hints(id, signature, false)?;
        let owns = args
            .iter()
            .zip(&hints)
            .map(|(arg, hint)| {
                let value = self.source.role(*arg, "value").unwrap_or(*arg);
                let shape = self.typ(value);
                (self.temporary_producer(value)
                    && (shape.big_name().is_some() || shape.is_unknown()))
                    || self.big_literal(value).is_some()
                        && hint
                            .as_ref()
                            .and_then(|hint| hint.semantic.as_ref())
                            .is_some_and(|typ| typ.big_name().is_some())
                    || self.big_handles(value, hint.as_ref())
            })
            .collect::<Vec<_>>();
        if !owns.iter().any(|own| *own) {
            return Ok(None);
        }
        let count = self.resolver.fields(signature, "results").len();
        let result = if count == 0 {
            None
        } else if count == 1 {
            Some(String::new())
        } else {
            Some(format!("tuple:{count}"))
        };
        let module = self.big_module();
        let params = self.resolver.fields(signature, "params");
        let (name, mut inputs) = self.closed(id, "big_call", result.as_deref(), |this| {
            let mut code = vec![];
            let mut replacements = vec![];
            let hints = this.big_call_hints(id, signature, true)?;
            for (index, ((arg, hint), own)) in args.iter().zip(&hints).zip(&owns).enumerate() {
                let value = this.source.role(*arg, "value").unwrap_or(*arg);
                if this.resolver.denotes_type(this.reference(value))
                    || params
                        .get(index)
                        .is_some_and(|(name, _)| name.starts_with('$'))
                {
                    continue;
                }
                let name = format!(
                    "{}big_input_{}_{}",
                    this.prefix,
                    this.source.node(id).span.start,
                    index
                );
                let expression = this.render(value, hint.clone(), false)?;
                code.push(this.g(name.clone(), value));
                if let Some(hint) = hint.as_ref().filter(|_| !*own) {
                    code.extend([this.g(": ", value), hint.code.clone(), this.g(" = ", value)]);
                } else {
                    code.push(this.g(" := ", value));
                }
                code.extend([expression, this.g("\n", value)]);
                if *own {
                    code.push(this.g(
                        format!("defer {module}.compiler_destroy_value({name})\n"),
                        value,
                    ));
                }
                let replacement = if this.source.node(*arg).kind == "FieldValue" {
                    Code::concat([
                        this.copy(this.source.node(this.role(*arg, "field")?).span),
                        this.g("=", *arg),
                        this.g(name, value),
                    ])
                } else {
                    this.g(name, value)
                };
                replacements.push((*arg, replacement));
            }
            this.logical.busy.insert(id);
            let previous = replacements
                .iter()
                .map(|(arg, replacement)| {
                    (
                        *arg,
                        this.logical.replacements.insert(*arg, replacement.clone()),
                    )
                })
                .collect::<Vec<_>>();
            let call = this.render(id, None, false);
            for (arg, previous) in previous {
                if let Some(previous) = previous {
                    this.logical.replacements.insert(arg, previous);
                } else {
                    this.logical.replacements.remove(&arg);
                }
            }
            this.logical.busy.remove(&id);
            if count > 0 {
                code.push(this.g("return ", id));
            }
            code.push(call?);
            Ok(Code::concat(code))
        })?;
        if count == 1 {
            inputs.push(self.native_type(id)?);
        } else if count > 1 {
            let call = self.query_value(id)?;
            self.meta = true;
            for index in 0..count {
                inputs.push(Code::concat([
                    self.g(
                        format!(
                            "{}meta.type_proc_return_type(type_of({}meta.procedure_of(",
                            self.prefix, self.prefix
                        ),
                        id,
                    ),
                    call.clone(),
                    self.g(format!(")),{index})"), id),
                ]));
            }
        }
        Ok(Some(self.helper_call(id, &name, inputs)))
    }
}
impl Lower<'_, '_> {
    pub(super) fn big_owning_child(&self, parent: NodeId, child: NodeId) -> bool {
        let n = self.source.node(parent);
        match n.kind.as_str() {
            "Declaration" => {
                n.mutable
                    && self.source.group(parent, "values").len()
                        == self.source.group(parent, "names").len()
                    && self.source.group(parent, "values").contains(&child)
            }
            "Return" => self.source.group(parent, "values").contains(&child),
            "Compound" => self.source.group(parent, "elems").contains(&child),
            _ => false,
        }
    }
    fn big_binding(&self, id: NodeId) -> bool {
        self.source.node(id).parent.is_some_and(|p| {
            let n = self.source.node(p);
            matches!(
                n.kind.as_str(),
                "Declaration" | "Field" | "Range" | "TypeSwitch"
            ) && self.source.group(p, "names").contains(&id)
                || matches!(n.kind.as_str(), "Selector" | "FieldValue")
                    && self.source.role(p, "field") == Some(id)
                || matches!(
                    n.kind.as_str(),
                    "PolyType" | "ImplicitSelector" | "EnumType"
                )
        })
    }
    pub(super) fn big_statically_zero(&mut self, id: NodeId) -> bool {
        if self.resolver.constant_int(self.reference(id)) == Some(0) {
            return true;
        }
        if let Some(text) = self.big_literal(id) {
            return crate::bigvalues::literal(&text)
                .is_ok_and(|n| n.numerator() == &num_bigint::BigInt::from(0));
        }
        let node = self.source.node(id);
        if node.kind == "Literal" && node.op == "string" {
            return unquote(self.source.text(id)).ok().is_some_and(|text| {
                let text = text.strip_prefix(['+', '-']).unwrap_or(&text);
                let text = if text.len() >= 2
                    && text.starts_with('0')
                    && matches!(
                        text.as_bytes()[1],
                        b'b' | b'B' | b'o' | b'O' | b'd' | b'D' | b'z' | b'Z' | b'x' | b'X'
                    ) {
                    &text[2..]
                } else {
                    text
                };
                let mut zero = false;
                let mut separator = false;
                for byte in text.bytes() {
                    if byte == b'0' {
                        zero = true;
                        separator = false;
                    } else if byte == b'_' && zero && !separator {
                        separator = true;
                    } else {
                        return false;
                    }
                }
                zero && !separator
            });
        }
        if matches!(self.big_constructor_type(id), Some(Type::BigInteger))
            && self.source.group(id, "args").len() == 1
        {
            return self.big_statically_zero(self.source.group(id, "args")[0]);
        }
        false
    }
    pub(super) fn big_expression(
        &mut self,
        id: NodeId,
        hint: Option<Hint>,
        constant: bool,
    ) -> Result<Option<Code>> {
        if !self.big_enabled() || self.logical.busy.contains(&id) {
            return Ok(None);
        }
        let node = self.source.node(id);
        if constant {
            if let Some(code) = self.big_constant_render(id, hint.as_ref())? {
                return Ok(Some(code));
            }
        }
        if node.kind == "Ident" && !self.big_binding(id) && self.big_constant_readonly(id) {
            if let Some(code) = self.big_constant_render(id, None)? {
                return Ok(Some(code));
            }
        }
        if let Some(code) = self.big_constant_declaration(id)? {
            return Ok(Some(code));
        }
        if matches!(node.kind.as_str(), "Assignment" | "IncDec")
            || node.kind == "Unary" && node.op == "&"
        {
            let targets = if node.kind == "Assignment" {
                self.source.group(id, "lhs").to_vec()
            } else {
                self.source.role(id, "expr").into_iter().collect()
            };
            for target in targets {
                if self.big_constant_readonly(target) {
                    return Ok(Some(self.error_token(target,"A compile-time Integer/Rational constant is read-only; copy it into a runtime binding before changing it or taking its address.")));
                }
            }
        }
        if let Some(code)=self.big_integration(id,hint.clone(),constant)? { return Ok(Some(code)); }
        if node.kind == "Declaration" && node.mutable {
            if let Some(code) = self.big_multi_declaration(id)? {
                return Ok(Some(code));
            }
        }
        if matches!(
            node.kind.as_str(),
            "OrElse" | "OrReturn" | "OrBranch"
        ) {
            if let Some(code) = self.big_optional(id)? {
                return Ok(Some(code));
            }
        }
        if node.kind == "Call" {
            if let Some(code) = self.big_discard_call(id)? {
                return Ok(Some(code));
            }
        }
        if self.querying == 0 {
            if let Some(code) = self.temporary_discard(id)? {
                return Ok(Some(code));
            }
        }
        if node.kind == "Ident"
            && !self.big_binding(id)
            && self.resolver.logical_type(self.reference(id)).is_some()
            && self.resolver.symbol(self.reference(id)).is_none()
        {
            let module = self.big_module();
            return Ok(Some(
                self.g(format!("{module}.{}", self.source.text(id)), id),
            ));
        }
        let expected = hint.as_ref().and_then(|hint| hint.semantic.as_ref());
        if let Some(typ) = expected.filter(|typ| typ.big_name().is_some()) {
            if let Some(code) = self.big_render_literal(id, typ, constant)? {
                return Ok(Some(code));
            }
        }
        if matches!(expected, Some(Type::Generic)) {
            if let Some(code) = self.big_generic_literal(id, hint.clone().unwrap(), constant)? {
                return Ok(Some(code));
            }
        }
        if let Some(typ) = self.big_constructor_type(id) {
            return self.big_render_constructor(id, typ, constant).map(Some);
        }
        if node.kind == "Selector" {
            if let Some(property) = self.big_property(id) {
                if self.write_access(id) {
                    return Ok(Some(self.error_token(id,"Rational numerator and denominator properties are read-only; use big.set_fraction to change both together.")));
                }
                let base = self.role(id, "expr")?;
                let module = self.big_module();
                if self.temporary_producer(base) {
                    let (name,args)=self.closed(id,"big_property",Some(&format!("{module}.Integer")),|this|{
                        let mut code=vec![];let value=this.temporary_evaluate(base,&mut code,None)?;
                        code.push(this.g(format!("defer {module}.compiler_destroy_value({})\nreturn {module}.{property}({})",value.value,value.value),id));
                        Ok(Code::concat(code))
                    })?;
                    return Ok(Some(self.helper_call(id, &name, args)));
                }
                let value = self.render(base, None, constant)?;
                let pointer = matches!(self.typ(base), Type::Pointer(_));
                return Ok(Some(Code::concat([
                    self.g(format!("{module}.{property}("), id),
                    value,
                    self.g(if pointer { "^)" } else { ")" }, id),
                ])));
            }
            let base = self.role(id, "expr")?;
            let mut typ = self.typ(base);
            while let Type::Pointer(inner) = typ {
                typ = *inner;
            }
            if typ.big_name().is_some()
                && self.source.text(self.role(id, "field")?).starts_with('_')
            {
                return Ok(Some(self.error_token(id,"Integer/Rational backing storage is private; use their constructors and read-only properties.")));
            }
        }
        if node.kind == "DistinctType" && self.typ(self.role(id, "type")?).big_name().is_some() {
            return Ok(Some(self.error_token(
                id,
                "Distinct Integer/Rational types are not supported yet; use a type alias.",
            )));
        }
        if matches!(node.kind.as_str(), "MapType" | "SetType") {
            let key = if node.kind == "MapType" {
                self.source.role(id, "key")
            } else {
                self.source.group(id, "args").first().copied()
            };
            if let Some(key) = key.filter(|key| self.typ(*key).big_name().is_some()) {
                return Ok(Some(self.error_token(
                    key,
                    "Integer/Rational map keys and set elements are not supported yet.",
                )));
            }
        }
        if matches!(node.kind.as_str(), "Binary" | "Unary") && self.big_handles(id, hint.as_ref()) {
            let values = if node.kind == "Binary" {
                vec![self.role(id, "left")?, self.role(id, "right")?]
            } else {
                vec![self.role(id, "expr")?]
            };
            let concrete = values.iter().any(|v| self.typ(*v).big_name().is_some());
            let requested = expected.is_some_and(|typ| typ.big_name().is_some());
            if !(self.big_default_value(id) && !concrete || constant && !concrete && !requested) {
                return self.big_operator(id, hint, constant).map(Some);
            }
        }
        if node.kind == "Assignment" {
            if node.op != "=" {
                return self.big_update_assignment(id);
            }
            return self.big_assignment(id);
        }
        if node.kind == "Call" {
            let callee = self.role(id, "callee")?;
            let args = self.source.group(id, "args").to_vec();
            if self
                .resolver
                .native_builtin(self.reference(callee), "append")
            {
                if let Some(code) = self.big_append(id)? {
                    return Ok(Some(code));
                }
            }
            if args.len() == 1
                && self
                    .resolver
                    .native_builtin(self.reference(callee), "delete")
            {
                let arg = args[0];
                if self.big_constant_readonly(arg) {
                    return Ok(Some(self.error_token(arg,"A compile-time Integer/Rational constant is read-only and cannot be deleted; delete an independent runtime copy instead.")));
                }
                let typ = self.typ(arg);
                if typ.big_name().is_some() || typ.is_unknown() {
                    let root = self.unparen(arg);
                    let mut base = root;
                    while matches!(
                        self.source.node(base).kind.as_str(),
                        "Selector" | "Index" | "Paren"
                    ) {
                        base = self.role(base, "expr")?;
                    }
                    let borrowed = self.resolver.symbol(self.reference(base)).is_some_and(|s| {
                        let r = self.resolver.symbols[s].reference;
                        matches!(
                            self.resolver.sources[r.file].node(r.node).kind.as_str(),
                            "Field" | "Range" | "TypeSwitch"
                        )
                    });
                    let addressable = self.addressable(arg)?;
                    let module = self.big_module();
                    if borrowed && addressable == "false" {
                        let message="This numeric value is borrowed; clone it before taking ownership or deleting it.";
                        if typ.big_name().is_some() {
                            return Ok(Some(self.error_token(arg, message)));
                        }
                        let (name,inputs)=self.closed(id,"big_borrowed_delete",None,|this|{
                            let value=this.render(arg,None,false)?;let text=value.clone().finish().text;let error=this.error_token(arg,message);let delete=this.native("delete");
                            Ok(Code::concat([this.g(format!("when type_of({text})=={module}.Integer || type_of({text})=={module}.Rational {{_="),arg),error,this.g("} else {",arg),this.g(format!("{delete}("),arg),value,this.g(")}",arg)]))
                        })?;
                        return Ok(Some(self.helper_call(id, &name, inputs)));
                    }
                    if self.source.node(root).kind == "Index"
                        && matches!(self.typ(self.role(root, "expr")?), Type::Map { .. })
                    {
                        let (name, inputs) = self.closed(id, "big_delete_map", None, |this| {
                            let mut code = vec![];
                            let targets = this.big_capture_targets(id, &[arg], &mut code)?;
                            let zero = format!(
                                "{}big_deleted_{}",
                                this.prefix,
                                this.source.node(id).span.start
                            );
                            code.push(this.g(format!("{zero}:type_of({})\n", targets[0]), id));
                            this.big_commit(id, &targets, &[zero], &mut code);
                            Ok(Code::concat(code))
                        })?;
                        return Ok(Some(self.helper_call(id, &name, inputs)));
                    }
                    let value = if addressable == "true" {
                        self.render_address(arg)?
                    } else {
                        self.render(arg, None, false)?
                    };
                    return Ok(Some(Code::concat([
                        self.g(
                            format!(
                                "{module}.{}",
                                if addressable == "true" {
                                    "compiler_delete(&("
                                } else {
                                    "compiler_destroy_value("
                                }
                            ),
                            id,
                        ),
                        value,
                        self.g(if addressable == "true" { "))" } else { ")" }, id),
                    ])));
                }
            }
            // Enter an f-string scope before snapshotting ordinary arguments.
            if self.querying == 0 && !self.fstring_busy.contains(&id) {
                if let Some(targets) = self.fstring_plans.get(&id).cloned() {
                    return self.fstring_expression(id, hint, targets).map(Some);
                }
            }
            return self.big_consume_call(id);
        }
        if node.kind == "Compound"
            && self.typ(id).big_name().is_some()
            && !self.source.group(id, "elems").is_empty()
        {
            return Ok(Some(self.error_token(
                id,
                "Construct Integer/Rational values with parentheses; backing fields are private.",
            )));
        }
        Ok(None)
    }
}
