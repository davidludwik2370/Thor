//! Native sequence bounds, preserving storage identity and once-only operands.
use super::*;

impl Lower<'_, '_> {
    pub(super) fn checking_state(&self, id: NodeId, key: &str) -> i64 {
        for site in std::iter::once(id).chain(self.source.ancestors(id)) {
            let node = self.source.node(site);
            let state = node.data[key].as_i64().unwrap_or_default();
            if state != 0 { return state; }
            if node.kind == "Procedure" {
                return self.source.role(site, "type")
                    .and_then(|typ| self.source.node(typ).data[key].as_i64())
                    .unwrap_or_default();
            }
        }
        0
    }
    /// Reference Expressions.reference_capture follows projected storage owners.
    pub(super) fn reference_capture(&mut self,index:usize)->bool{
        let mut index=index;let mut seen=HashSet::new();
        while seen.insert(index){
            let symbol=self.resolver.symbols[index].clone();
            if let Some(projection)=symbol.projection{
                if self.resolver.projection_indirect(&projection){return true;}
                let Some(owner)=self.resolver.symbol(projection.owner)else{return false;};index=owner;continue;
            }
            let source=&self.resolver.sources[symbol.reference.file];
            return match source.node(symbol.reference.node).kind.as_str(){
                "Field"=>symbol.result,
                "Range"|"TypeSwitch"=>source.node(symbol.name_node).kind=="Unary"&&source.node(symbol.name_node).op=="&",
                _=>true,
            };
        }
        false
    }
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
        let typ = self.typ(id);
        if let Some(symbol) = self.resolver.symbol(self.reference(id)).filter(|_|
            matches!(&typ, Type::Scalar {name,..} if matches!(name.as_str(), "int" | "i8" | "i16" | "i32" | "u8" | "u16" | "u32"))) {
            let symbol = self.resolver.symbols[symbol].clone();
            let source = &self.resolver.sources[symbol.reference.file];
            if source.node(symbol.reference.node).kind == "Range" && symbol.index == 0
                && source.node(symbol.name_node).kind == "Ident" {
                if let Some(interval) = source.role(symbol.reference.node, "expr") {
                    if source.node(interval).kind == "Binary" && source.node(interval).op == "..<" {
                        if let Some(start) = source.role(interval, "left") {
                            if self.resolver.constant_int(Ref {file:symbol.reference.file,node:start})
                                .is_some_and(|start| start >= 0) {
                                return false;
                            }
                        }
                    }
                }
            }
        }
        match typ {
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
    pub(super) fn native_type(&mut self, id: NodeId) -> Result<Code> {
        let value = self.query_value(id)?;
        let type_of = self.native("type_of");
        self.default = true;
        Ok(Code::concat([
            self.g(format!("{type_of}({}default_value(", self.prefix), id),
            value,
            self.g("))", id),
        ]))
    }
    pub(super) fn sequence_trait(&mut self, id: NodeId) -> Result<String> {
        let typ = self.typ(id);
        let mut base = &typ;
        while let Type::Nominal(_, inner) = base { base = inner; }
        match base {
            Type::Slice(_) | Type::Dynamic(_) | Type::Fixed { .. } | Type::String { .. }
            | Type::Array { length: Some(_), .. } => return Ok("true".into()),
            // An array whose length is a type may be enum indexed. Odin's
            // native trait remains authoritative for this unresolved shape.
            _ => {}
        }
        let typ = self.native_type(id)?.finish().text;
        self.meta = true;
        let m = format!("{}meta", self.prefix);
        Ok(format!("(({m}.type_is_array({typ}) && !{m}.type_is_enumerated_array({typ})) || {m}.type_is_slice({typ}) || {m}.type_is_dynamic_array({typ}) || {m}.type_is_fixed_capacity_dynamic_array({typ}) || {m}.type_is_string({typ}))"))
    }
    pub(super) fn write_access(&mut self, id: NodeId) -> bool {
        if self.address_nodes.contains(&id) {
            return true;
        }
        for parent in self.source.ancestors(id) {
            if self.address_nodes.contains(&parent) {
                return true;
            }
            if matches!(self.source.node(parent).kind.as_str(), "Index" | "Slice" | "Deref") {
                if let Some(base) = self.source.role(parent, "expr") {
                    let typ = self.typ(base);
                    let mut shape = &typ;
                    while let Type::Nominal(_, inner) | Type::Soa(inner) = shape { shape = inner; }
                    if matches!(shape, Type::Slice(_) | Type::Dynamic(_) | Type::Pointer(_)) {
                        // Writing through a copied reference header leaves its
                        // containing value unchanged.
                        return false;
                    }
                }
            }
            let node = self.source.node(parent);
            match node.kind.as_str() {
                "Assignment" => {
                    if let Some(switch)=self.source.node(parent).parent.filter(|owner|self.source.node(*owner).kind=="TypeSwitch"){
                        return self.source.group(switch,"names").iter().any(|name|self.source.node(*name).kind=="Unary"&&self.source.node(*name).op=="&");
                    }
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
    pub(super) fn render_address(&mut self, id: NodeId) -> Result<Code> {
        let inserted = self.address_nodes.insert(id);
        let code = self.render(id, None, false);
        if inserted {
            self.address_nodes.remove(&id);
        }
        code
    }
    pub(super) fn addressable(&mut self, id: NodeId) -> Result<String> {
        let node = self.source.node(id);
        if node.kind == "Paren" {
            return self.addressable(self.role(id, "expr")?);
        }
        if matches!(node.kind.as_str(), "Ident" | "Selector") {
            if let Some(symbol) = self.resolver.symbol(self.reference(id)) {
                let constant=self.resolver.symbols[symbol].constant;
                return Ok((!constant&&self.reference_capture(symbol)).to_string());
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
        if matches!(typ, Type::Slice(_)|Type::Dynamic(_)) && node.kind == "Index" {
            return Ok("true".into());
        }
        if !typ.is_unknown() {
            return Ok(
                if node.kind == "Selector" || matches!(typ, Type::Array { .. }|Type::Fixed {..}) {
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
            "Return" => {
                self.source.group(parent, "values").len() == 1
                    && self
                        .source
                        .ancestors(parent)
                        .find(|owner| self.source.node(*owner).kind == "Procedure")
                        .and_then(|owner| self.source.role(owner, "type"))
                        .is_some_and(|signature| {
                            self.resolver
                                .fields(self.reference(signature), "results")
                                .len()
                                > 1
                        })
            }
            _ => false,
        }
    }
    pub(super) fn index_uses_type_operand(&self, id: NodeId) -> bool {
        matches!(self.source.node(id).kind.as_str(), "Index" | "Slice")
            && self.source.role(id, "expr").is_some_and(|base| {
                self.resolver.known_type_operand(self.reference(base))
            })
    }
    pub(super) fn access(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        let base = self.role(id, "expr")?;
        if self.source.ancestors(id).take_while(|parent| {
            matches!(self.source.node(*parent).kind.as_str(), "Paren" | "TypeAssertion" | "Unary")
        }).any(|parent| self.source.node(parent).kind == "TypeAssertion"
            && self.source.node(parent).parent.is_some_and(|owner| self.source.node(owner).kind == "Unary" && self.source.node(owner).op == "&")) {
            return Ok(None);
        }
        if self.index_uses_type_operand(id) {
            return Ok(None);
        }
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
        if index && bounds[0].is_some_and(|mut key| {
            while self.source.node(key).kind == "Paren" {
                let Some(inner) = self.source.role(key, "expr") else { break; };
                key = inner;
            }
            self.source.node(key).kind == "Compound" && self.source.role(key, "type").is_none()
        }) {
            // Untyped composite keys obtain their type from the native map.
            return Ok(None);
        }
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
                        let len = self.native("len");
                        let i128 = self.native("i128");
                        code.extend([
                            self.g(format!("({i128}({len}({base_text}))+{i128}("), *bound),
                            value.clone(),
                            self.g(format!(") when {i128}("), *bound),
                            value.clone(),
                            self.g(format!(")<0 else {i128}("), *bound),
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
        if index {
            if let Some(symbol) = self.resolver.symbol(self.reference(base)) {
                let symbol = &self.resolver.symbols[symbol];
                let source = &self.resolver.sources[symbol.reference.file];
                let by_reference_row = source.node(symbol.reference.node).kind == "Range"
                    && symbol.index == 0 && source.node(symbol.name_node).kind == "Unary"
                    && source.node(symbol.name_node).op == "&";
                let mut shape = &typ;
                while let Type::Nominal(_, inner) = shape {shape = inner;}
                if let Type::Array {length:Some(length), ..} = shape {
                    if by_reference_row {
                        // Native SOA array rows use a proxy lvalue. Keep its
                        // index syntax intact instead of reborrowing the row
                        // inside a generated procedure.
                        let length = *length;
                        let original = self.render(base, None, false)?;
                        let bound = bounds[0].unwrap();
                        let value = self.render(bound, None, false)?;
                        let sequence = self.module("sequence");
                        return Ok(Some(Code::concat([
                            original,
                            self.g(format!("[{sequence}.normalize({length},"), id),
                            value,
                            self.g(format!(",{},checked={})]",self.source_location(bound),
                                self.checking_state(id,"bounds_check") != -1),id),
                        ])));
                    }
                }
            }
        }
        let pointer = index && self.write_access(id);
        let module = self.module("sequence");
        let original = self.query_value(base)?;
        let type_of = self.native("type_of");
        let result = if pointer {
            // Infer the element type without taking an address in an
            // unevaluated witness. Nested polymorphic accesses can otherwise
            // require their pointer type before Odin has inferred it.
            Code::concat([
                self.g(format!("^{type_of}({module}.element("), id),
                original,
                self.g("))", id),
            ])
        } else if index {
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
                self.g(")[:0]))", id),
            ])
        };
        let p = format!("{}access_{}_", self.prefix, self.source.node(id).span.start);
        let (name, mut args) = self.closed(
            id,
            if pointer { "address" } else { "access" },
            Some(""),
            |this| {
                let addr=this.addressable(base)?;
                let typ = this.typ(base);
                let mut shape = &typ;
                while let Type::Nominal(_, inner) = shape { shape = inner; }
                let known_array = match shape {
                    Type::Array { .. } | Type::Fixed { .. } => Some(true),
                    Type::Slice(_) | Type::Dynamic(_) | Type::String { .. } => Some(false),
                    _ => None,
                };
                let native_array = match shape {
                    Type::Array { .. } => Some(true),
                    Type::Fixed { .. } | Type::Slice(_) | Type::Dynamic(_) | Type::String { .. } => Some(false),
                    _ => None,
                };
                let source_type = if known_array.is_none() { Some(this.native_type(base)?.finish().text) } else { None };
                let meta=format!("{}meta",this.source.prefix);
                let array = if let Some(value) = known_array { value.to_string() } else {
                    this.meta = true;
                    let typ = source_type.as_ref().unwrap();
                    format!("({meta}.type_is_array({typ}) || {meta}.type_is_enumerated_array({typ}) || {meta}.type_is_fixed_capacity_dynamic_array({typ}))")
                };
                let trait_=this.sequence_trait(base)?;
                let len=this.native("len");
                let readonly_array_capture = index && !pointer
                    && this.source.node(base).kind == "Ident"
                    && this.resolver.symbol(this.reference(base))
                        .is_some_and(|symbol| !this.reference_capture(symbol));
                let native_array = if let Some(value) = native_array { value.to_string() } else {
                    let typ = source_type.as_ref().unwrap();
                    format!("({meta}.type_is_array({typ}) || {meta}.type_is_enumerated_array({typ}))")
                };
                let borrowed_open = this.g(format!("when {native_array} {{"), id);
                let borrowed_else = this.g("} else {", id);
                let borrowed_close = this.g("}", id);
                let mut branch = |byref: bool, reuse_array: bool| -> Result<Code> {
                    let original = if byref{this.render_address(base)?}else{this.render(base,None,false)?};
                    let value = format!("{p}base");
                    let v = if reuse_array {
                        format!("({})", original.clone().finish().text)
                    } else {
                        format!("{value}{}", if byref { "^" } else { "" })
                    };
                    let mut code = if reuse_array { vec![] } else { vec![
                        this.g(
                            format!("{value} := {}", if byref { "&(" } else { "" }),
                            base,
                        ),
                        original,
                        this.g(if byref { ")\n" } else { "\n" }, base),
                    ] };
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
                                format!("{module}.normalize({len}({v}),{p}bound_{i},{},checked={})",this.source_location(*bound),this.checking_state(id,"bounds_check") != -1),
                                this.source.node(*bound).span,
                                "sequence-bound",
                            ));
                            native.push(this.g(format!("{p}bound_{i}"), *bound));
                        }
                    }
                    let op = if pointer { "&" } else { "" };
                    if trait_ == "true" || trait_ == "false" {
                        code.extend([
                            this.g(format!("return {op}({v})["), id),
                            if trait_ == "true" { Code::concat(normalized) } else { Code::concat(native) },
                            this.g("]", id),
                        ]);
                    } else { code.extend([
                        this.g(format!("when {trait_} {{return {op}({v})["), id),
                        Code::concat(normalized),
                        this.g(format!("]}} else {{return {op}({v})["), id),
                        Code::concat(native),
                        this.g("]}", id),
                    ]); }
                    Ok(Code::concat(code))
                };
                let value = branch(false, false)?;
                // A readonly capture already provides native array storage.
                // Copying it into another local adds work and changes Odin's
                // aliasing through large immutable value parameters. Slice
                // headers and side-effectful bases still need a saved value.
                let value = if readonly_array_capture && native_array != "false" {
                    let borrowed = branch(false, true)?;
                    if native_array == "true" { borrowed } else { Code::concat([
                        borrowed_open, borrowed, borrowed_else, value, borrowed_close,
                    ]) }
                } else { value };
                if addr == "false" || array == "false" {
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
                let referenced = branch(true, false)?;
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
                if array == "true" { return Ok(referenced); }
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
        let indices = lhs.iter().flat_map(|lhs| self.assignment_indices(*lhs)).collect::<Vec<_>>();
        let mut generic = vec![];
        for index in indices {
            let base = self.role(index, "expr")?;
            let key = self.role(index, "index")?;
            if self.typ(base).is_unknown() && self.needs_bound(key)
                && self.source.node(key).kind != "Compound"
            {
                generic.push(index);
            }
        }
        if generic.is_empty() {
            return Ok(None);
        }
        let post = self.source.node(id).parent.is_some_and(|parent| {
            self.source.node(parent).kind == "For" && self.source.role(parent, "post") == Some(id)
        });
        if !post {
            return self.inline_assignment(id, constant).map(Some);
        }
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
    fn assignment_indices(&self, mut target: NodeId) -> Vec<NodeId> {
        let mut indices = vec![];
        loop {
            match self.source.node(target).kind.as_str() {
                "Index" => indices.push(target),
                "Selector" | "Deref" | "Paren" | "TypeAssertion" => {}
                _ => break,
            }
            let Some(base) = self.source.role(target, "expr") else {
                break;
            };
            target = base;
        }
        indices.reverse();
        indices
    }
    fn assignment_reference(&mut self, id: NodeId) -> bool {
        let node = self.source.node(id);
        if let Some(symbol) = self.resolver.symbol(self.reference(id)) {
            return !self.resolver.symbols[symbol].constant && self.reference_capture(symbol);
        }
        match node.kind.as_str() {
            "Ident" => self
                .resolver
                .symbol(self.reference(id))
                .is_some_and(|symbol| {
                    !self.resolver.symbols[symbol].constant && self.reference_capture(symbol)
                }),
            "Deref" => true,
            "Index" => self.switch_replacements.contains_key(&id),
            "Implicit" if self.source.text(id) == "context" => true,
            "Paren" => self
                .source
                .role(id, "expr")
                .is_some_and(|base| self.assignment_reference(base)),
            "Selector" => self.source.role(id, "expr").is_some_and(|base| {
                let typ = self.typ(base);
                let mut shape = &typ;
                while let Type::Nominal(_, inner) = shape {
                    shape = inner;
                }
                matches!(shape, Type::Pointer(_)) || self.assignment_reference(base)
            }),
            _ => false,
        }
    }
    fn inline_assignment(&mut self, id: NodeId, constant: bool) -> Result<Code> {
        if let Some(block) = self.source.node(id).parent.filter(|parent| {
            self.source.node(*parent).kind == "Block"
                && self.source.raw.as_bytes()[self.source.node(*parent).span.start] != b'{'
        }) {
            self.convert_do(block);
        }
        let module = self.module("sequence");
        let mut parts = vec![self.g("{\n", id)];
        let mut replacements = vec![];
        for lhs in self.source.group(id, "lhs").to_vec() {
            for index in self.assignment_indices(lhs) {
                if self.switch_replacements.contains_key(&index) {
                    continue;
                }
                let base = self.role(index, "expr")?;
                let key = self.role(index, "index")?;
                let name = self.temporary_name("assignment_base");
                let mut reference = self.assignment_reference(base);
                let writable = self.addressable(base)?;
                let typ = self.native_type(base)?;
                let mut owner = base;
                while matches!(self.source.node(owner).kind.as_str(), "Selector" | "Paren") {
                    owner = self.role(owner, "expr")?;
                }
                let mut capture = None;
                if !reference && owner != base {
                    let namespace = self.source.node(owner).kind == "Ident"
                        && !self
                            .resolver
                            .namespace_imports(self.file, self.source.text(owner))
                            .is_empty();
                    if !namespace {
                        let owner_name = self.temporary_name("assignment_storage");
                        parts.extend([
                            self.g(format!("{owner_name} := "), owner),
                            self.render(owner, None, constant)?,
                            self.g("\n", owner),
                        ]);
                        capture = Some(
                            self.switch_replacements
                                .insert(owner, self.g(owner_name, owner)),
                        );
                        reference = true;
                    }
                }
                let original = self.render(base, None, constant);
                if let Some(previous) = capture {
                    if let Some(previous) = previous {
                        self.switch_replacements.insert(owner, previous);
                    } else {
                        self.switch_replacements.remove(&owner);
                    }
                }
                let original = original?;
                parts.extend([
                    self.g(format!("{module}.validate_assignment_base("), index),
                    typ,
                    self.g(
                        format!(
                            ",{writable})\n{name} := {}",
                            if reference {
                                format!("{module}.capture_reference(&(")
                            } else {
                                String::new()
                            }
                        ),
                        index,
                    ),
                    original,
                    self.g(if reference { "))\n" } else { "\n" }, index),
                ]);
                let key_name = self.temporary_name("assignment_index");
                let implicit = self.source.node(key).kind == "Compound"
                    && self.source.role(key, "type").is_none();
                if implicit {
                    let type_of = self.native("type_of");
                    parts.extend([
                        self.g(
                            format!("{key_name}: {type_of}({module}.map_key({name})) = "),
                            key,
                        ),
                        self.render(key, None, constant)?,
                        self.g("\n", key),
                    ]);
                } else {
                    parts.extend([
                        self.g(
                            format!("{key_name} := {module}.assignment_bound({name},"),
                            key,
                        ),
                        self.render(key, None, constant)?,
                        self.g(
                            format!(
                                ",{},checked={})\n",
                                self.source_location(key),
                                self.checking_state(id, "bounds_check") != -1
                            ),
                            key,
                        ),
                    ]);
                }
                let replacement = self.g(format!("{name}[{key_name}]"), index);
                replacements.push((index, self.switch_replacements.insert(index, replacement)));
            }
            let mut target = lhs;
            while self.source.node(target).kind == "Paren" {
                target = self.role(target, "expr")?;
            }
            let kind = self.source.node(target).kind.as_str();
            let owner = (kind == "Selector")
                .then(|| self.source.role(target, "expr"))
                .flatten();
            let namespace = owner.is_some_and(|base| {
                self.source.node(base).kind == "Ident"
                    && self.resolver.symbol(self.reference(base)).is_none()
                    && !self
                        .resolver
                        .namespace_imports(self.file, self.source.text(base))
                        .is_empty()
            });
            if let Some(owner) =
                owner.filter(|_| !namespace && !self.switch_replacements.contains_key(&lhs))
            {
                let name = self.temporary_name("assignment_owner");
                let typ = self.native_type(owner)?;
                let writable = self.addressable(owner)?;
                let reference = self.assignment_reference(owner);
                parts.extend([
                    self.g(format!("{module}.validate_assignment_field("), lhs),
                    typ,
                    self.g(
                        format!(
                            ",{writable})\n{name} := {}",
                            if reference {
                                format!("{module}.capture_field_reference(&(")
                            } else {
                                String::new()
                            }
                        ),
                        lhs,
                    ),
                    self.render(owner, None, constant)?,
                    self.g(if reference { "))\n" } else { "\n" }, lhs),
                ]);
                let field = self.source.text(self.role(target, "field")?);
                replacements.push((
                    lhs,
                    self.switch_replacements
                        .insert(lhs, self.g(format!("{name}.{field}"), lhs)),
                ));
            } else if !matches!(kind, "Ident" | "Index")
                && !self.switch_replacements.contains_key(&lhs)
            {
                let name = self.temporary_name("assignment_target");
                parts.extend([
                    self.g(format!("{name} := &("), lhs),
                    self.render(lhs, None, constant)?,
                    self.g(")\n", lhs),
                ]);
                replacements.push((
                    lhs,
                    self.switch_replacements
                        .insert(lhs, self.g(format!("{name}^"), lhs)),
                ));
            }
        }
        self.assigning.insert(id);
        let body = self.render(id, None, constant);
        self.assigning.remove(&id);
        for (index, old) in replacements.into_iter().rev() {
            if let Some(old) = old {
                self.switch_replacements.insert(index, old);
            } else {
                self.switch_replacements.remove(&index);
            }
        }
        parts.extend([body?, self.g("\n}", id)]);
        Ok(Code::concat(parts))
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
