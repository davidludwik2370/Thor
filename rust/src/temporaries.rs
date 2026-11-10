//! Reference ownership algebra for anonymous Thor producers. Ordinary call
//! results remain borrowed; only known consumers release their temporary inputs.
use super::*;

pub(super) struct Temporary {
    pub(super) value: String,
    pub(super) owned: String,
    origin: NodeId,
    dependencies: Vec<Temporary>,
}
impl Lower<'_, '_> {
    fn temporary_name(&mut self, label: &str) -> String {
        self.temporary_serial += 1;
        format!("{}owned_{label}_{}", self.prefix, self.temporary_serial)
    }
    fn temporary_math(&mut self, id: NodeId) -> bool {
        let node = self.source.node(id);
        if node.kind != "Binary" || !matches!(node.op.as_str(), "|" | "&" | "-" | "~") {
            return false;
        }
        let (Some(a), Some(b)) = (self.source.role(id, "left"), self.source.role(id, "right"))
        else {
            return false;
        };
        let at = self.typ(a);
        let bt = self.typ(b);
        matches!(at, Type::Set(_))
            || matches!(bt, Type::Set(_))
            || at.is_unknown()
                && bt.is_unknown()
                && self.source.text(a) != "nil"
                && self.source.text(b) != "nil"
                && !self.resolver.denotes_type(self.reference(a))
                && !self.resolver.denotes_type(self.reference(b))
    }
    pub(super) fn temporary_producer(&mut self, id: NodeId) -> bool {
        let node = self.source.node(id);
        match node.kind.as_str() {
            "Paren" => self
                .source
                .role(id, "expr")
                .is_some_and(|expr| self.temporary_producer(expr)),
            "Interpolation" => node.data["manual_lifetime"] != true,
            "Compound" => self
                .source
                .role(id, "type")
                .is_some_and(|typ| matches!(self.typ(typ), Type::Set(_))),
            "Binary" => self.temporary_math(id),
            "Ternary" | "WhenExpr" => ["x", "y"].iter().any(|key| {
                self.source
                    .role(id, key)
                    .is_some_and(|arm| self.temporary_producer(arm))
            }),
            "SwitchExpr" | "CompileSwitchExpr" => {
                let arms = node.data["cases"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|case| {
                        Span::from_wire(&case["result"]).and_then(|span| self.source.at(span, id))
                    })
                    .collect::<Vec<_>>();
                arms.into_iter().any(|arm| self.temporary_producer(arm))
            }
            _ => false,
        }
    }
    pub(super) fn temporary_view(&mut self, id: NodeId) -> bool {
        let node = self.source.node(id);
        if node.kind == "Paren" {
            return self
                .source
                .role(id, "expr")
                .is_some_and(|expr| self.temporary_view(expr));
        }
        matches!(node.kind.as_str(), "Slice" | "Selector")
            && self
                .source
                .role(id, "expr")
                .is_some_and(|base| self.temporary_producer(base) || self.temporary_view(base))
    }
    fn temporary_hint(&self, code: Code) -> Hint {
        Hint {
            code,
            element_only: false,
            plain_array: false,
        }
    }
    fn temporary_captured(&self, id: NodeId) -> bool {
        self.fstring_frames
            .iter()
            .any(|(_, targets)| targets.contains(&id))
    }
    pub(super) fn temporary_evaluate(
        &mut self,
        id: NodeId,
        code: &mut Vec<Code>,
        hint: Option<Hint>,
    ) -> Result<Temporary> {
        let node = self.source.node(id);
        if node.kind == "Paren" {
            return self.temporary_evaluate(self.role(id, "expr")?, code, hint);
        }
        if matches!(
            node.kind.as_str(),
            "Ternary" | "WhenExpr" | "SwitchExpr" | "CompileSwitchExpr"
        ) && self.temporary_producer(id)
        {
            return self.temporary_select(id, code, hint);
        }
        if self.temporary_view(id) {
            return self.temporary_evaluate_view(id, code);
        }
        let value = self.temporary_name("value");
        code.push(self.g(value.clone(), id));
        if let Some(hint) = &hint {
            code.extend([self.g(": ", id), hint.code.clone(), self.g(" = ", id)]);
        } else {
            code.push(self.g(" := ", id));
        }
        code.extend([self.render(id, hint, false)?, self.g("\n", id)]);
        let owned = if !self.temporary_producer(id) || self.temporary_captured(id) {
            "false".into()
        } else if node.kind == "Binary" && !matches!(self.typ(id), Type::Set(_)) {
            let typ = self.native_type(id)?.finish().text;
            let test = self.set_value_trait(&format!("({typ}){{}}"));
            format!("(true when {test} else false)")
        } else {
            "true".into()
        };
        Ok(Temporary {
            value,
            owned,
            origin: id,
            dependencies: vec![],
        })
    }
    pub(super) fn temporary_release(
        &mut self,
        temp: &Temporary,
        code: &mut Vec<Code>,
        deferred: bool,
    ) -> Result<()> {
        let mut cleanup = vec![];
        if temp.owned != "false" {
            let destroy = match self.typ(temp.origin) {
                Type::String { .. } => format!("{}({})", self.native("delete"), temp.value),
                Type::Set(_) => format!("{}.destroy({})", self.module("sets"), temp.value),
                _ => {
                    self.meta = true;
                    let typ = self.native_type(temp.origin)?.finish().text;
                    let sets = self.module("sets");
                    let test = self.set_value_trait(&format!("({typ}){{}}"));
                    {
                        let delete = self.native("delete");
                        format!("when {}meta.type_is_string({typ}) {{{delete}({})}} else when {test} {{{sets}.destroy({})}}",self.prefix,temp.value,temp.value)
                    }
                }
            };
            cleanup.push(self.g(
                if temp.owned == "true" {
                    destroy
                } else {
                    format!("if {} {{{destroy}}}", temp.owned)
                },
                temp.origin,
            ));
            cleanup.push(self.g("\n", temp.origin));
        }
        for dependency in temp.dependencies.iter().rev() {
            self.temporary_release(dependency, &mut cleanup, false)?;
        }
        if !cleanup.is_empty() {
            if deferred {
                code.push(self.g("defer {\n", temp.origin));
            }
            code.extend(cleanup);
            if deferred {
                code.push(self.g("}\n", temp.origin));
            }
        }
        Ok(())
    }
    fn temporary_arm(&mut self, arm: NodeId, hint: Hint, value: &str, owned: &str) -> Result<Code> {
        if self.diverging(arm) {
            return self.render(arm, None, false);
        }
        let mut code = vec![];
        let selected = self.temporary_evaluate(arm, &mut code, Some(hint))?;
        if !selected.dependencies.is_empty() {
            return Ok(self.error_token(
                arm,
                "A view into an anonymous allocation needs a named owner.",
            ));
        }
        code.push(self.g(
            format!(
                "{value} = {}\n{owned} = {}\n",
                selected.value, selected.owned
            ),
            arm,
        ));
        Ok(Code::concat(code))
    }
    fn temporary_select(
        &mut self,
        id: NodeId,
        code: &mut Vec<Code>,
        hint: Option<Hint>,
    ) -> Result<Temporary> {
        let hint = if let Some(hint) = hint {
            hint
        } else {
            let typ = self.native_type(id)?;
            self.temporary_hint(typ)
        };
        let value = self.temporary_name("selected");
        let owned = self.temporary_name("selected_flag");
        code.extend([
            self.g(format!("{value}: "), id),
            hint.code.clone(),
            self.g(format!("\n{owned}: bool\n"), id),
        ]);
        let node = self.source.node(id);
        if matches!(node.kind.as_str(), "Ternary" | "WhenExpr") {
            let constant = node.kind == "WhenExpr";
            let condition = self.render(self.role(id, "cond")?, None, constant)?;
            let yes = self.temporary_arm(self.role(id, "x")?, hint.clone(), &value, &owned)?;
            let no = self.temporary_arm(self.role(id, "y")?, hint, &value, &owned)?;
            code.extend([
                self.g(if constant { "when " } else { "if " }, id),
                condition,
                self.g(" {\n", id),
                yes,
                self.g("} else {\n", id),
                no,
                self.g("}\n", id),
            ]);
        } else {
            code.push(self.temporary_switch(id, |this, arm| {
                this.temporary_arm(arm, hint.clone(), &value, &owned)
            })?);
        }
        Ok(Temporary {
            value,
            owned,
            origin: id,
            dependencies: vec![],
        })
    }
    fn temporary_evaluate_view(&mut self, id: NodeId, code: &mut Vec<Code>) -> Result<Temporary> {
        let base = self.temporary_evaluate(self.role(id, "expr")?, code, None)?;
        let value = self.temporary_name("view");
        if self.source.node(id).kind == "Selector" {
            code.push(self.g(
                format!(
                    "{value} := ({}).{}\n",
                    base.value,
                    self.source.text(self.role(id, "field")?)
                ),
                id,
            ));
        } else {
            let sequence = self.module("sequence");
            let mut bounds = vec![];
            for key in ["low", "high"] {
                if let Some(bound) = self.source.role(id, key) {
                    let local = self.temporary_name(key);
                    code.extend([
                        self.g(format!("{local} := "), bound),
                        self.render(bound, None, false)?,
                        self.g("\n", bound),
                    ]);
                    bounds.push(format!(
                        "{sequence}.normalize({}({}),{local})",
                        self.native("len"),
                        base.value
                    ));
                } else {
                    bounds.push(String::new());
                }
            }
            code.push(self.g(
                format!("{value} := ({})[{}:{}]\n", base.value, bounds[0], bounds[1]),
                id,
            ));
        }
        Ok(Temporary {
            value,
            owned: "false".into(),
            origin: id,
            dependencies: vec![base],
        })
    }
    fn temporary_binary(&mut self, id: NodeId) -> Result<Option<Code>> {
        let op = self.source.node(id).op.clone();
        let left = self.role(id, "left")?;
        let right = self.role(id, "right")?;
        if self.resolver.denotes_type(self.reference(left))
            || self.resolver.denotes_type(self.reference(right))
        {
            return Ok(None);
        }
        let sets = self.temporary_math(id);
        if !sets
            && !(matches!(op.as_str(), "==" | "!=" | "<" | "<=" | ">" | ">=")
                && (self.temporary_producer(left) || self.temporary_producer(right)))
        {
            return Ok(None);
        }
        let result_type = if sets {
            self.native_type(left)?
        } else {
            self.g("bool", id)
        };
        let (name,mut args)=self.closed(id,"consume",Some(""),|this|{
            let mut code=vec![];
            let input=if sets {this.g(format!("{}Result",this.prefix),id)}else{this.native_type(left)?};
            let hint=this.temporary_hint(input);
            let mut a=this.temporary_evaluate(left,&mut code,Some(hint.clone()))?;
            let b=this.temporary_evaluate(right,&mut code,if matches!(this.source.node(right).kind.as_str(),"ImplicitSelector"|"Literal"){Some(hint)}else{None})?;
            let result=this.temporary_name("result");
            let known=matches!(this.typ(left),Type::Set(_))||matches!(this.typ(right),Type::Set(_));
            let module=this.module("sets");
            if sets {
                let (method,generic)=match op.as_str(){"|"=>("union_of","union"),"&"=>("intersection","intersection"),"-"=>("difference","difference"),_=>("symmetric_difference","symmetric_difference")};
                let method=if known{method.into()}else{format!("compiler_{generic}")};
                let call=format!("{module}.{method}({},{})",a.value,b.value);
                if op=="|" && a.owned!="false" {
                    let test=this.set_value_trait(&format!("({}Result){{}}",this.prefix));
                    code.push(this.g(format!("{result}: {}Result\nwhen {test} {{if {} {{{module}.union_assign(&{},{})\n{result}={}}} else {{{result}={call}}}}} else {{{result}={call}}}\n",this.prefix,a.owned,a.value,b.value,a.value),id));
                    a.owned="false".into();
                }else{code.push(this.g(format!("{result} := {call}\n"),id));}
            }else{
                let expr=if matches!(op.as_str(),"=="|"!=") {format!("{}{module}.{}({},{})",if op=="!="{"!"}else{""},if known{"equal"}else{"compiler_equal"},a.value,b.value)}else{format!("{} {op} {}",a.value,b.value)};
                code.push(this.g(format!("{result} := {expr}\n"),id));
            }
            this.temporary_release(&b,&mut code,false)?;
            this.temporary_release(&a,&mut code,false)?;
            code.push(this.g(format!("return {result}"),id));
            Ok(Code::concat(code))
        })?;
        args.push(result_type);
        Ok(Some(self.helper_call(id, &name, args)))
    }
    fn temporary_membership(&mut self, id: NodeId) -> Result<Option<Code>> {
        let left = self.role(id, "left")?;
        let right = self.role(id, "right")?;
        if !(self.temporary_producer(left)
            || self.temporary_producer(right)
            || self.temporary_view(right))
        {
            return Ok(None);
        }
        let (name,args)=self.closed(id,"owned_membership",Some("bool"),|this|{
            let mut code=vec![];
            let typ=this.native_type(right)?.finish().text;
            this.meta=true;
            let sets=this.module("sets");
            let zero=format!("({typ}){{}}");
            let test=this.set_value_trait(&zero);
            let elem=format!("#type (type_of({sets}.element_witness({zero})) when {test} else (type_of({sets}.key_witness({zero})) when {}meta.type_is_map({typ}) else {}meta.type_elem_type({typ})))",this.prefix,this.prefix);
            let hint=this.temporary_hint(this.g(elem,left));
            let a=this.temporary_evaluate(left,&mut code,Some(hint))?;
            let b=this.temporary_evaluate(right,&mut code,None)?;
            let result=this.temporary_name("membership");
            let contains=this.membership_helper(id);
            code.push(this.g(format!("{result} := {}{contains}(value={},values={})\n",if this.source.node(id).op=="not_in"{"!"}else{""},a.value,b.value),id));
            this.temporary_release(&b,&mut code,false)?;this.temporary_release(&a,&mut code,false)?;
            code.push(this.g(format!("return {result}"),id));Ok(Code::concat(code))
        })?;
        Ok(Some(self.helper_call(id, &name, args)))
    }
    fn temporary_query(&mut self, id: NodeId, arg: NodeId) -> Result<Code> {
        let (name, args) = self.closed(id, "owned_query", Some("int"), |this| {
            let mut code = vec![];
            let value = this.temporary_evaluate(arg, &mut code, None)?;
            let result = this.temporary_name("query");
            let typ = this.typ(arg);
            let expr = if matches!(typ, Type::Set(_)) {
                format!("{}.count({})", this.module("sets"), value.value)
            } else if typ.is_unknown() {
                let test = this.set_value_trait(&value.value);
                format!(
                    "(len(({}).table) when {test} else len({}))",
                    value.value, value.value
                )
            } else {
                format!("len({})", value.value)
            };
            code.push(this.g(format!("{result} := {expr}\n"), id));
            this.temporary_release(&value, &mut code, false)?;
            code.push(this.g(format!("return {result}"), id));
            Ok(Code::concat(code))
        })?;
        Ok(self.helper_call(id, &name, args))
    }
    fn temporary_discard(&mut self, id: NodeId) -> Result<Option<Code>> {
        if self.temporary_discarding.contains(&id) || !self.temporary_producer(id) {
            return Ok(None);
        }
        let Some(parent) = self.source.node(id).parent else {
            return Ok(None);
        };
        let node = self.source.node(parent);
        let statement = node.kind == "ExpressionStatement";
        let (names, values) = match node.kind.as_str() {
            "Assignment" => (
                self.source.group(parent, "lhs"),
                self.source.group(parent, "rhs"),
            ),
            "Declaration" if node.mutable => (
                self.source.group(parent, "names"),
                self.source.group(parent, "values"),
            ),
            _ => ([].as_slice(), [].as_slice()),
        };
        if !(statement
            || names.len() == values.len()
                && values
                    .iter()
                    .position(|value| *value == id)
                    .is_some_and(|i| self.source.text(names[i]) == "_"))
        {
            return Ok(None);
        }
        self.temporary_discarding.insert(id);
        let result_type = if statement {
            None
        } else {
            Some(self.native_type(id)?)
        };
        let built = self.closed(
            id,
            "discard",
            if statement { None } else { Some("") },
            |this| {
                let mut code = vec![];
                let hint = if statement {
                    None
                } else {
                    Some(this.temporary_hint(this.g(format!("{}Result", this.prefix), id)))
                };
                let value = this.temporary_evaluate(id, &mut code, hint)?;
                this.temporary_release(&value, &mut code, false)?;
                if !statement {
                    code.push(this.g(format!("return {}Result{{}}", this.prefix), id));
                }
                Ok(Code::concat(code))
            },
        );
        self.temporary_discarding.remove(&id);
        let (name, mut args) = built?;
        args.extend(result_type);
        Ok(Some(self.helper_call(id, &name, args)))
    }
    fn temporary_index(&mut self, id: NodeId) -> Result<Option<Code>> {
        let base = self.role(id, "expr")?;
        if !self.temporary_producer(base) && !self.temporary_view(base) {
            return Ok(None);
        }
        if self.write_access(id) {
            return Ok(Some(self.error_token(
                id,
                "An address or write into an anonymous allocation needs a named owner.",
            )));
        }
        let sequence = self.module("sequence");
        let typ = self.native_type(base)?;
        let result_type = Code::concat([
            self.g(format!("type_of({sequence}.element(("), id),
            typ,
            self.g("){}))", id),
        ]);
        let (name,mut args)=self.closed(id,"owned_index",Some(""),|this|{
            let mut code=vec![];
            let value=this.temporary_evaluate(base,&mut code,None)?;
            let index=this.temporary_name("index");
            code.extend([this.g(format!("{index} := "),id),this.render(this.role(id,"index")?,None,false)?,this.g("\n",id)]);
            let result=this.temporary_name("element");
            let test=this.sequence_trait(base)?;
            code.push(this.g(format!("{result}: {}Result\nwhen {test} {{{result}=({})[{sequence}.normalize(len({}),{index})]}} else {{{result}=({})[{index}]}}\n",this.prefix,value.value,value.value,value.value),id));
            this.temporary_release(&value,&mut code,false)?;
            code.push(this.g(format!("return {result}"),id));Ok(Code::concat(code))
        })?;
        args.push(result_type);
        Ok(Some(self.helper_call(id, &name, args)))
    }
    pub(super) fn temporary_update(&mut self, id: NodeId, function: &str) -> Result<Option<Code>> {
        let rhs = self.source.group(id, "rhs")[0];
        if self.source.node(id).op == "+=" || !self.temporary_producer(rhs) {
            return Ok(None);
        }
        let left = self.source.group(id, "lhs")[0];
        let post = self.source.node(id).parent.is_some_and(|parent| {
            self.source.node(parent).kind == "For" && self.source.role(parent, "post") == Some(id)
        });
        let (name, args) = self.closed(
            id,
            "owned_update",
            if post { Some("bool") } else { None },
            |this| {
                let mut code = vec![];
                let pointer = this.temporary_name("target");
                code.extend([
                    this.g(format!("{pointer} := &("), left),
                    this.render(left, None, false)?,
                    this.g(")\n", left),
                ]);
                let value = this.temporary_evaluate(rhs, &mut code, None)?;
                code.push(this.g(format!("{function}({pointer},{})\n", value.value), id));
                this.temporary_release(&value, &mut code, false)?;
                if post {
                    code.push(this.g("return true", id));
                }
                Ok(Code::concat(code))
            },
        )?;
        let call = self.helper_call(id, &name, args);
        Ok(Some(if post {
            Code::concat([self.g("_ = ", id), call])
        } else {
            call
        }))
    }
    fn temporary_iterate(&mut self, id: NodeId) -> Result<Option<Code>> {
        let expr = self.role(id, "expr")?;
        if !self.temporary_producer(expr) && !self.temporary_view(expr) {
            return Ok(None);
        }
        let mut code = vec![self.g("{\n", id)];
        let value = self.temporary_evaluate(expr, &mut code, None)?;
        self.temporary_release(&value, &mut code, true)?;
        let sets = self.module("sets");
        let known = matches!(self.typ(expr), Type::Set(_));
        let render_loop = |this: &mut Self, selected: bool| -> Result<Code> {
            let iterator = this.g(
                if selected {
                    format!("{sets}.entries({})", value.value)
                } else {
                    value.value.clone()
                },
                expr,
            );
            let mut replacements = vec![];
            for child in this.source.node(id).children.clone() {
                let rendered = if child == expr {
                    iterator.clone()
                } else {
                    this.render(child, None, false)?
                };
                replacements.push((this.source.node(child).span, rendered));
            }
            this.splice(id, replacements, vec![])
        };
        if known {
            code.push(render_loop(self, true)?);
        } else {
            let test = self.set_value_trait(&value.value);
            let selected = render_loop(self, true)?;
            let native = render_loop(self, false)?;
            code.extend([
                self.g(format!("{{when {test} {{\n"), id),
                selected,
                self.g("\n} else {\n", id),
                native,
                self.g("\n}}\n", id),
            ]);
        }
        code.push(self.g("\n}", id));
        Ok(Some(Code::concat(code)))
    }
    pub(super) fn temporary_expression(
        &mut self,
        id: NodeId,
        constant: bool,
    ) -> Result<Option<Code>> {
        if self.querying > 0 {
            return Ok(None);
        }
        if let Some(discard) = self.temporary_discard(id)? {
            return Ok(Some(discard));
        }
        let node = self.source.node(id);
        match node.kind.as_str(){
            "Unary" if node.op=="&" => {
                let expr=self.unparen(self.role(id,"expr")?);
                let base=if self.source.node(expr).kind=="Index"{self.role(expr,"expr")?}else{expr};
                if self.temporary_producer(base)||self.temporary_view(base){return Ok(Some(self.error_token(id,"An address into an anonymous allocation needs a named owner; bind the allocation before taking its address.")));}
            }
            "Slice"|"Selector" if self.temporary_view(id) => return Ok(Some(self.error_token(id,"A view into an anonymous allocation needs a named owner; bind the allocation before accessing its fields or slicing it."))),
            "Index" => return self.temporary_index(id),
            "Binary" if !constant => {
                if matches!(node.op.as_str(),"in"|"not_in"){return self.temporary_membership(id);}
                return self.temporary_binary(id);
            }
            "Range" => return self.temporary_iterate(id),
            "Call" => {
                let callee=self.role(id,"callee")?;
                let args=self.source.group(id,"args").to_vec();
                if self.resolver.native_builtin(self.reference(callee),"raw_data") && args.iter().any(|arg|self.temporary_producer(*arg)||self.temporary_view(*arg)){
                    return Ok(Some(self.error_token(id,"A pointer into an anonymous allocation needs a named owner; bind the allocation before taking raw_data.")));
                }
                if args.len()==1 && self.resolver.native_builtin(self.reference(callee),"len") && (self.temporary_producer(args[0])||self.temporary_view(args[0])) {
                    return self.temporary_query(id,args[0]).map(Some);
                }
            }
            _=>{}
        }
        Ok(None)
    }
}
