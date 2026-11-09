//! Native sequence bounds, preserving storage identity and once-only operands.
use super::*;

impl Lower<'_, '_> {
    fn needs_bound(&mut self, mut id: NodeId) -> bool {
        while self.source.node(id).kind == "Paren" {
            let Some(expr) = self.source.role(id, "expr") else {
                break;
            };
            id = expr;
        }
        if let Some(value) = self.resolver.constant_int(self.reference(id)) {
            return value < 0;
        }
        if self.source.node(id).kind == "ImplicitSelector" {
            return false;
        }
        match self.typ(id) {
            Type::Enum(_) => false,
            Type::Scalar { name, .. } if name.starts_with('u') && !name.starts_with("u128") => {
                false
            }
            _ => true,
        }
    }
    pub(super) fn query_value(&mut self, id: NodeId) -> Result<Code> {
        self.querying += 1;
        let code = self.render(id, None, false);
        self.querying -= 1;
        code
    }
    fn native_type(&mut self, id: NodeId) -> Result<Code> {
        let value = self.query_value(id)?;
        let type_of = self.native("type_of");
        self.default = true;
        Ok(Code::concat([
            self.g(format!("{type_of}({}default_value(", self.prefix), id),
            value,
            self.g("))", id),
        ]))
    }
    fn sequence_trait(&mut self, id: NodeId) -> Result<String> {
        let typ = self.native_type(id)?.finish().text;
        self.meta = true;
        let m = format!("{}meta", self.prefix);
        Ok(format!("(({m}.type_is_array({typ}) && !{m}.type_is_enumerated_array({typ})) || {m}.type_is_slice({typ}) || {m}.type_is_dynamic_array({typ}) || {m}.type_is_fixed_capacity_dynamic_array({typ}) || {m}.type_is_string({typ}))"))
    }
    fn write_access(&self, id: NodeId) -> bool {
        if self.address_nodes.contains(&id) {
            return true;
        }
        for parent in self.source.ancestors(id) {
            if self.address_nodes.contains(&parent) {
                return true;
            }
            let node = self.source.node(parent);
            match node.kind.as_str() {
                "Assignment" => {
                    return self.source.group(parent, "lhs").iter().any(|lhs| {
                        self.source
                            .node(*lhs)
                            .span
                            .contains(self.source.node(id).span)
                    })
                }
                "Unary" if node.op == "&" => return true,
                "Range" => {
                    return self.source.group(parent, "names").iter().any(|name| {
                        self.source.node(*name).kind == "Unary" && self.source.node(*name).op == "&"
                    })
                }
                "Slice" => return true,
                "Paren" | "Selector" | "Index" | "Deref" => {}
                _ => return false,
            }
        }
        false
    }
    fn render_address(&mut self, id: NodeId) -> Result<Code> {
        let inserted = self.address_nodes.insert(id);
        let code = self.render(id, None, false);
        if inserted {
            self.address_nodes.remove(&id);
        }
        code
    }
    fn addressable(&mut self, id: NodeId) -> Result<String> {
        let node = self.source.node(id);
        if node.kind == "Paren" {
            return self.addressable(self.role(id, "expr")?);
        }
        if matches!(node.kind.as_str(), "Ident" | "Selector") {
            if let Some(symbol) = self.resolver.symbol(self.reference(id)) {
                let symbol = &self.resolver.symbols[symbol];
                return Ok((!symbol.constant && (!symbol.parameter || symbol.result)).to_string());
            }
        }
        if node.kind == "Ident" {
            return Ok("false".into());
        }
        if node.kind == "Deref" {
            return Ok("true".into());
        }
        if !matches!(node.kind.as_str(), "Selector" | "Index") {
            return Ok("false".into());
        }
        let base = self.role(id, "expr")?;
        let typ = self.typ(base);
        let inherited = self.addressable(base)?;
        if matches!(typ, Type::Pointer(_)) {
            return Ok("true".into());
        }
        if matches!(typ, Type::Slice(_)) && node.kind == "Index" {
            return Ok("true".into());
        }
        if !typ.is_unknown() && !matches!(typ, Type::Dynamic(_)) {
            return Ok(
                if node.kind == "Selector" || matches!(typ, Type::Array { .. }) {
                    inherited
                } else {
                    "false".into()
                },
            );
        }
        let t = self.native_type(base)?.finish().text;
        self.meta = true;
        let m = format!("{}meta", self.prefix);
        if node.kind == "Selector" {
            return Ok(format!("({m}.type_is_pointer({t}) || ({inherited}))"));
        }
        Ok(format!("({m}.type_is_slice({t}) || {m}.type_is_dynamic_array({t}) || {m}.type_is_pointer({t}) || {m}.type_is_multi_pointer({t}) || (({m}.type_is_array({t}) || {m}.type_is_fixed_capacity_dynamic_array({t})) && ({inherited})))"))
    }
    fn optional_access(&self, mut id: NodeId) -> bool {
        while let Some(parent) = self
            .source
            .node(id)
            .parent
            .filter(|parent| self.source.node(*parent).kind == "Paren")
        {
            id = parent;
        }
        let Some(parent) = self.source.node(id).parent else {
            return false;
        };
        match self.source.node(parent).kind.as_str() {
            "OrElse" | "OrReturn" | "OrBranch" | "OrPanic" => true,
            "Declaration" => {
                self.source.group(parent, "names").len() > 1
                    && self.source.group(parent, "values").len() == 1
            }
            "Assignment" => {
                self.source.group(parent, "lhs").len() > 1
                    && self.source.group(parent, "rhs").len() == 1
            }
            _ => false,
        }
    }
    pub(super) fn access(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        let base = self.role(id, "expr")?;
        let typ = self.typ(base);
        if self.native_access.contains(&id)
            || matches!(typ, Type::Map { .. } | Type::Enum(_) | Type::Pointer(_))
        {
            return Ok(None);
        }
        let index = self.source.node(id).kind == "Index";
        if !typ.is_sequence() && !typ.is_unknown() {
            return Ok(None);
        }
        if index && self.optional_access(id) && !self.write_access(id) {
            return Ok(None);
        }
        let bounds = if index {
            vec![self.source.role(id, "index")]
        } else {
            vec![self.source.role(id, "low"), self.source.role(id, "high")]
        };
        if !bounds
            .iter()
            .flatten()
            .any(|bound| self.needs_bound(*bound))
        {
            return Ok(None);
        }
        if let Type::Array {
            length: Some(length),
            ..
        } = typ
        {
            if index {
                if let Some(value) = self
                    .resolver
                    .constant_int(self.reference(bounds[0].unwrap()))
                {
                    let normalized = if value < 0 { length + value } else { value };
                    if normalized < 0 {
                        return Ok(Some(self.error_token(bounds[0].unwrap(),&format!("Negative sequence index {value} is out of bounds for length {length}."))));
                    }
                    let original = self.render(base, None, constant)?;
                    return Ok(Some(
                        Code::concat([
                            self.g("(", id),
                            original,
                            self.g(format!(")[{normalized}]"), id),
                        ])
                        .expansion(&self.source.path, self.source.node(id).span),
                    ));
                }
            }
        }
        if constant {
            let base_code = self.render(base, None, true)?;
            let base_text = base_code.clone().finish().text;
            let mut code = vec![self.g("(", id), base_code, self.g(")[", id)];
            for (i, bound) in bounds.iter().enumerate() {
                if i > 0 {
                    code.push(self.g(":", id));
                }
                if let Some(bound) = bound {
                    let value = self.render(*bound, None, true)?;
                    let typ = self.typ(*bound);
                    if self.needs_bound(*bound)
                        && !matches!(typ,Type::Scalar{name,..} if name.starts_with('u'))
                    {
                        code.extend([
                            self.g(format!("(i128(len({base_text}))+i128("), *bound),
                            value.clone(),
                            self.g(") when i128(", *bound),
                            value.clone(),
                            self.g(")<0 else i128(", *bound),
                            value,
                            self.g("))", *bound),
                        ]);
                    } else {
                        code.push(value);
                    }
                }
            }
            code.push(self.g("]", id));
            return Ok(Some(Code::concat(code)));
        }
        let pointer = index && self.write_access(id);
        let module = self.module("sequence");
        let original = self.query_value(base)?;
        let type_of = self.native("type_of");
        let result = if index {
            Code::concat([
                self.g(format!("{type_of}({module}.element("), id),
                original,
                self.g("))", id),
            ])
        } else {
            self.default = true;
            Code::concat([
                self.g(format!("{type_of}({}default_value((", self.prefix), id),
                original,
                self.g(")[:]))", id),
            ])
        };
        let p = format!("{}access_{}_", self.prefix, self.source.node(id).span.start);
        let (name, mut args) = self.closed(
            id,
            if pointer { "address" } else { "access" },
            Some(if pointer { "^" } else { "" }),
            |this| {
                let addr=this.addressable(base)?;
                let source_type=this.native_type(base)?.finish().text;
                this.meta=true;let meta=format!("{}meta",this.source.prefix);
                let array=format!("({meta}.type_is_array({source_type}) || {meta}.type_is_enumerated_array({source_type}) || {meta}.type_is_fixed_capacity_dynamic_array({source_type}))");
                let trait_=this.sequence_trait(base)?;
                let mut branch = |byref: bool| -> Result<Code> {
                    let original = if byref{this.render_address(base)?}else{this.render(base,None,false)?};
                    let value = format!("{p}base");
                    let v = format!("{value}{}", if byref { "^" } else { "" });
                    let mut code = vec![
                        this.g(
                            format!("{value} := {}", if byref { "&(" } else { "" }),
                            base,
                        ),
                        original,
                        this.g(if byref { ")\n" } else { "\n" }, base),
                    ];
                    for (i, bound) in bounds.iter().enumerate() {
                        if let Some(bound) = bound {
                            let rendered = this.render(*bound, None, false)?;
                            code.extend([
                                this.g(format!("{p}bound_{i} := "), *bound),
                                rendered,
                                this.g("\n", *bound),
                            ]);
                        }
                    }
                    let mut normalized = vec![];
                    let mut native = vec![];
                    for (i, bound) in bounds.iter().enumerate() {
                        if i > 0 {
                            normalized.push(this.g(":", id));
                            native.push(this.g(":", id));
                        }
                        if let Some(bound) = bound {
                            normalized.push(this.gen(
                                format!("{module}.normalize(len({v}),{p}bound_{i},{})",this.source_location(*bound)),
                                this.source.node(*bound).span,
                                "sequence-bound",
                            ));
                            native.push(this.g(format!("{p}bound_{i}"), *bound));
                        }
                    }
                    let op = if pointer { "&" } else { "" };
                    code.extend([
                        this.g(format!("when {trait_} {{return {op}({v})["), id),
                        Code::concat(normalized),
                        this.g(format!("]}} else {{return {op}({v})["), id),
                        Code::concat(native),
                        this.g("]}", id),
                    ]);
                    Ok(Code::concat(code))
                };
                let value = branch(false)?;
                if addr == "false" {
                    if pointer {
                        let error = this.error_token(
                            id,
                            "Cannot take a mutable address of an element in a temporary array.",
                        );
                        return Ok(Code::concat([
                            this.g(format!("when {array} {{ _ = "), id),
                            error,
                            this.g("} else {", id),
                            value,
                            this.g("}", id),
                        ]));
                    }
                    return Ok(value);
                }
                let referenced = branch(true)?;
                let referenced = if addr == "true" {
                    referenced
                } else {
                    let fallback = if pointer {
                        let token = this
                            .error_token(
                                id,
                                "Cannot take a mutable address of an element in a temporary array.",
                            )
                            .finish()
                            .text;
                        this.g(format!("#panic({})", json!(token)), id)
                    } else {
                        value.clone()
                    };
                    Code::concat([
                        this.g(format!("when {addr} {{"), id),
                        referenced,
                        this.g("} else {", id),
                        fallback,
                        this.g("}", id),
                    ])
                };
                Ok(Code::concat([
                    this.g(format!("when {array} {{"), id),
                    referenced,
                    this.g("} else {", id),
                    value,
                    this.g("}", id),
                ]))
            },
        )?;
        args.push(result);
        let call = self.helper_call(id, &name, args);
        Ok(Some(if pointer {
            Code::concat([self.g("(", id), call, self.g(")^", id)])
        } else {
            call
        }))
    }

    pub(super) fn access_assignment(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        if self.assigning.contains(&id) {
            return Ok(None);
        }
        let lhs = self.source.group(id, "lhs").to_vec();
        let indices = lhs
            .iter()
            .flat_map(|lhs| std::iter::once(*lhs).chain(self.source.descendants(*lhs)))
            .filter(|child| self.source.node(*child).kind == "Index")
            .collect::<Vec<_>>();
        let mut generic = vec![];
        for index in indices {
            let base = self.role(index, "expr")?;
            if self.typ(base).is_unknown() {
                generic.push(index);
            }
        }
        if generic.is_empty() {
            return Ok(None);
        }
        let post = self.source.node(id).parent.is_some_and(|parent| {
            self.source.node(parent).kind == "For" && self.source.role(parent, "post") == Some(id)
        });
        let (name, args) = self.closed(
            id,
            "assignment",
            if post { Some("bool") } else { None },
            |this| {
                this.assigning.insert(id);
                let body = this.assignment_branches(id, &generic, 0, constant);
                this.assigning.remove(&id);
                let body = body?;
                Ok(if post {
                    Code::concat([body, this.g("\nreturn true", id)])
                } else {
                    body
                })
            },
        )?;
        let call = self.helper_call(id, &name, args);
        Ok(Some(if post {
            Code::concat([self.g("_ = ", id), call])
        } else {
            call
        }))
    }
    fn assignment_branches(
        &mut self,
        id: NodeId,
        indices: &[NodeId],
        position: usize,
        constant: bool,
    ) -> Result<Code> {
        if position == indices.len() {
            return self.render(id, None, constant);
        }
        let index = indices[position];
        let base = self.role(index, "expr")?;
        let condition = self.sequence_trait(base)?;
        let sequence = self.assignment_branches(id, indices, position + 1, constant)?;
        self.native_access.insert(index);
        let native = self.assignment_branches(id, indices, position + 1, constant);
        self.native_access.remove(&index);
        Ok(Code::concat([
            self.g(format!("when {condition} {{\n"), index),
            sequence,
            self.g("\n} else {\n", index),
            native?,
            self.g("\n}", index),
        ]))
    }
}
