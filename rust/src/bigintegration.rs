//! Numeric builtins, borrowed membership and generic rational components.
use super::*;
use crate::extremaplan::Part;
impl Lower<'_, '_> {
    pub(super) fn big_dynamic_property(&mut self, id: NodeId) -> bool {
        if self.source.node(id).kind != "Selector" {
            return false;
        }
        let Some(field) = self.source.role(id, "field") else {
            return false;
        };
        if !matches!(self.source.text(field), "numerator" | "denominator") {
            return false;
        }
        let Some(base) = self.source.role(id, "expr") else {
            return false;
        };
        if self.resolver.denotes_type(self.reference(base)) {
            return false;
        }
        if self.source.node(base).kind == "Ident"
            && self.resolver.symbol(self.reference(base)).is_none()
        {
            let namespace = self.source.text(base);
            if self.source.nodes.iter().any(|node| {
                if node.kind != "Import" {
                    return false;
                }
                let name = self.source.names.get(node.name);
                if !name.is_empty() {
                    return name == namespace;
                }
                unquote(&node.op).ok().is_some_and(|path| {
                    path.rsplit('/').next().unwrap_or(&path).rsplit(':').next() == Some(namespace)
                })
            }) {
                return false;
            }
        }
        let mut typ = self.typ(base);
        while let Type::Pointer(inner) = typ {
            typ = *inner;
        }
        typ.is_unknown()
    }
    fn big_integrated_builtin(&self, id: NodeId) -> Option<String> {
        if self.source.node(id).kind != "Call" {
            return None;
        }
        let callee = self.source.role(id, "callee")?;
        crate::extremaplan::name(self.resolver, self.reference(callee), &mut HashSet::new())
            .or_else(|| {
                ["abs", "clamp"]
                    .iter()
                    .find(|name| self.resolver.native_builtin(self.reference(callee), name))
                    .map(|name| (*name).to_string())
            })
    }
    pub(super) fn big_integration(
        &mut self,
        id: NodeId,
        hint: Option<Hint>,
        constant: bool,
    ) -> Result<Option<Code>> {
        if self.logical.integration_busy.contains(&id) {
            return Ok(None);
        }
        if matches!(self.source.node(id).kind.as_str(), "Assignment" | "Unary") {
            let node = self.source.node(id);
            let targets = if node.kind == "Assignment" {
                self.source.group(id, "lhs").to_vec()
            } else if node.op == "&" {
                self.source.role(id, "expr").into_iter().collect()
            } else {
                vec![]
            };
            let mut properties = vec![];
            for target in targets {
                for node in std::iter::once(target).chain(self.source.descendants(target)) {
                    if self.big_dynamic_property(node) {
                        properties.push(node);
                    }
                }
            }
            if let Some(first) = properties.first().copied() {
                let module = self.module("big");
                let mut predicates = vec![];
                for property in properties {
                    let base = self.role(property, "expr")?;
                    let value = self.query_value(base)?.finish().text;
                    predicates.push(format!("(type_of({value})=={module}.Rational || type_of({value})==^{module}.Rational)"));
                }
                let predicate = predicates.join(" || ");
                let error=self.error_token(first,"Rational numerator and denominator properties are read-only; use big.set_fraction to change both together.");
                self.logical.integration_busy.insert(id);
                let native = self.render(id, hint, constant);
                self.logical.integration_busy.remove(&id);
                return Ok(Some(if node.kind == "Assignment" {
                    Code::concat([
                        self.g(format!("when {predicate}{{_="), id),
                        error,
                        self.g("}else{", id),
                        native?,
                        self.g("}", id),
                    ])
                } else {
                    Code::concat([
                        self.g("(", id),
                        error,
                        self.g(format!(" when {predicate} else "), id),
                        native?,
                        self.g(")", id),
                    ])
                }));
            }
        }
        if self.big_dynamic_property(id) && !self.write_access(id) && !constant {
            let base = self.role(id, "expr")?;
            let field = self.source.text(self.role(id, "field")?).to_string();
            let value = self.query_value(base)?.finish().text;
            let module = self.module("big");
            let result=self.g(format!("type_of(({module}.Integer{{}} when type_of({value})=={module}.Rational || type_of({value})==^{module}.Rational else ({value}).{field}))"),id);
            if self.temporary_producer(base) {
                let (name, mut args) = self.closed(id, "big_component", Some(""), |this| {
                    let mut code = vec![];
                    let value = this.temporary_evaluate(base, &mut code, None)?;
                    let out = format!(
                        "{}big_component_{}",
                        this.prefix,
                        this.source.node(id).span.start
                    );
                    code.push(this.g(
                        format!(
                            "{out}:={module}.compiler_{field}({},{}Result)\n",
                            value.value, this.prefix
                        ),
                        id,
                    ));
                    this.temporary_release(&value, &mut code, false)?;
                    code.push(this.g(format!("return {out}"), id));
                    Ok(Code::concat(code))
                })?;
                args.push(result);
                return Ok(Some(self.helper_call(id, &name, args)));
            }
            return Ok(Some(Code::concat([
                self.g(format!("{module}.compiler_{field}("), id),
                self.render(base, None, false)?,
                self.g(",", id),
                result,
                self.g(")", id),
            ])));
        }
        if self.source.node(id).kind == "Binary"
            && matches!(self.source.node(id).op.as_str(), "in" | "not_in")
        {
            return self.big_membership(id, constant);
        }
        let Some(name) = self.big_integrated_builtin(id) else {
            return Ok(None);
        };
        let Some(target) = self.resolver.logical_numeric_result(self.reference(id)) else {
            return Ok(None);
        };
        if constant {
            return Ok(target.big_name().map(|_| {
                self.error_token(
                    id,
                    "A numeric builtin constant requires compile-time-known matching inputs.",
                )
            }));
        }
        self.big_numeric_builtin(id, hint, &name, target).map(Some)
    }
    fn big_numeric_builtin(
        &mut self,
        id: NodeId,
        hint: Option<Hint>,
        name: &str,
        target: Type,
    ) -> Result<Code> {
        let plan = crate::extremaplan::plan(self.resolver, self.reference(id));
        let optional = plan.as_ref().is_some_and(|plan| plan.optional);
        let parts = plan.map(|plan| plan.parts).unwrap_or_else(|| {
            self.source
                .group(id, "args")
                .iter()
                .map(|value| (Part::Scalar, *value))
                .collect()
        });
        if parts.is_empty()
            || parts
                .iter()
                .any(|(_, value)| self.source.node(*value).kind == "FieldValue")
            || name == "abs" && parts.len() != 1
            || name == "clamp" && parts.len() != 3
        {
            self.logical.integration_busy.insert(id);
            let code = self.render(id, hint, false);
            self.logical.integration_busy.remove(&id);
            return code;
        }
        let witness = parts
            .iter()
            .find(|(kind, value)| {
                let typ = self.typ(*value);
                let typ = if *kind == Part::Scalar {
                    typ
                } else {
                    typ.element().cloned().unwrap_or(Type::Unknown)
                };
                typ.big_name().is_some() || typ.is_unknown()
            })
            .copied()
            .unwrap_or(parts[0]);
        let chosen = if parts.iter().any(|(kind, _)| *kind != Part::Scalar) {
            let value = parts
                .iter()
                .find(|(kind, _)| *kind != Part::Scalar)
                .unwrap()
                .1;
            let sequence = self.module("sequence");
            let query = self.query_value(value)?;
            Code::concat([
                self.g(format!("type_of({sequence}.element("), value),
                query,
                self.g("))", value),
            ])
        } else {
            self.native_type(witness.1)?
        };
        let module = self.module("big");
        let typ = format!("{}Result{}", self.prefix, if optional { "_0" } else { "" });
        let (helper,mut args)=self.closed(id,if optional{"extrema_optional"}else{"big_builtin"},Some(if optional{"tuple:2"}else{""}),|this|{
            let mut code=vec![this.g(format!("when {typ}=={module}.Integer || {typ}=={module}.Rational{{\n"),id)];let mut values=vec![];
            for(index,(kind,value))in parts.iter().enumerate(){
                let name=format!("{}big_builtin_{}_{}",this.prefix,this.source.node(id).span.start,index);
                if *kind==Part::Scalar{
                    let hint=Hint{constant_literal:false,code:this.g(&typ,*value),semantic:Some(if target.big_name().is_some(){target.clone()}else{Type::Generic}),element_only:false,plain_array:false};
                    code.extend([this.g(format!("{name}:{typ}="),*value),this.big_owned(*value,Some(hint),false)?,this.g(format!("\ndefer {module}.compiler_destroy(&{name})\n"),*value)]);
                }else{
                    if *kind==Part::Array&&matches!(this.typ(*value),Type::Array{length:Some(0),..}){return Ok(this.error_token(*value,"An empty fixed array cannot be expanded into min/max."));}
                    code.extend([this.g(format!("{name}:="),*value),this.render(*value,None,false)?,this.g("\n",*value)]);
                    if this.is_array(*value)||this.source.node(*value).kind=="Compound"{code.push(this.g(format!("defer for item in {name}{{{module}.compiler_destroy_value(item)}}\n"),*value));}
                }
                values.push(name);
            }
            if matches!(name,"abs"|"clamp"){code.push(this.g(format!("return {module}.compiler_{name}({})\n",values.join(",")),id));}
            else{
                let value=format!("{}big_extreme_{}",this.prefix,this.source.node(id).span.start);let ok=format!("{value}_ok");code.push(this.g(format!("{value}:{typ}\n{ok}:=false\n"),id));
                for((kind,_),arg)in parts.iter().zip(&values){let candidate=if *kind==Part::Scalar{arg.as_str()}else{"item"};let step=format!("if !{ok} || {module}.compare({candidate},{value},{}){{{module}.compiler_destroy(&{value});{value}={module}.compiler_clone({candidate});{ok}=true}}",json!(if name=="min"{"<"}else{">"}));code.push(this.g(if *kind==Part::Scalar{format!("{step}\n")}else{format!("for item in {arg}{{{step}}}\n")},id));}
                code.push(this.g(format!("return {value}{}\n",if optional{format!(",{ok}")}else{String::new()}),id));
            }
            this.logical.busy.insert(id);let native=this.render(id,hint.clone(),false);this.logical.busy.remove(&id);
            code.extend([this.g("}else{return ",id),native?,this.g("}",id)]);Ok(Code::concat(code))
        })?;
        args.push(chosen);
        if optional {
            args.push(self.g("bool", id));
        }
        Ok(self.helper_call(id, &helper, args))
    }
    fn big_membership(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        let left = self.role(id, "left")?;
        let right = self.role(id, "right")?;
        let sequence = self.unparen(right);
        let inverse = self.source.node(id).op == "not_in";
        let container = self.typ(sequence);
        if !self.is_array(sequence) {
            if constant
                || !matches!(
                    container,
                    Type::Array { .. } | Type::Slice(_) | Type::Dynamic(_) | Type::Fixed { .. }
                )
            {
                return Ok(None);
            }
            let Some(element) = container
                .element()
                .cloned()
                .filter(|typ| typ.big_name().is_some())
            else {
                return Ok(None);
            };
            let module = self.module("big");
            let hint = Hint {constant_literal:false,
                code: self.g(format!("{module}.{}", element.big_name().unwrap()), id),
                semantic: Some(element),
                element_only: false,
                plain_array: false,
            };
            let(name,args)=self.closed(id,"big_membership",Some("bool"),|this|{
                let needle=format!("{}big_needle_{}",this.prefix,this.source.node(id).span.start);let values=format!("{}big_values_{}",this.prefix,this.source.node(id).span.start);
                Ok(Code::concat([this.g(format!("{needle}:="),left),this.big_owned(left,Some(hint),false)?,this.g(format!("\ndefer {module}.compiler_destroy(&{needle})\n{values}:="),right),this.render(right,None,false)?,this.g(format!("\nfor item in {values}{{if {module}.equal({needle},item){{return {}}}}}\nreturn {inverse}",!inverse),id)]))
            })?;
            return Ok(Some(self.helper_call(id, &name, args)));
        }
        let elements = self.source.group(sequence, "elems").to_vec();
        let target = std::iter::once(left)
            .chain(elements.iter().copied())
            .find(|node| self.typ(*node).big_name().is_some());
        let Some(target) = target else {
            return Ok(None);
        };
        if constant {
            return self.big_constant_render(id, None);
        }
        let module = self.module("big");
        let semantic = self.typ(target);
        let (name, args) = self.closed(id, "big_membership", Some("bool"), |this| {
            let typ = this.native_type(target)?;
            let typ_text = typ.clone().finish().text;
            let hint = Hint {constant_literal:false,
                code: typ,
                semantic: Some(semantic),
                element_only: false,
                plain_array: false,
            };
            let needle = format!(
                "{}big_needle_{}",
                this.prefix,
                this.source.node(id).span.start
            );
            let mut code = vec![
                this.g(format!("{needle}:{typ_text}="), left),
                this.big_owned(left, Some(hint.clone()), false)?,
                this.g(
                    format!("\ndefer {module}.compiler_destroy(&{needle})\n"),
                    left,
                ),
            ];
            let mut names = vec![];
            for (index, value) in elements.iter().enumerate() {
                let name = format!(
                    "{}big_member_{}_{}",
                    this.prefix,
                    this.source.node(id).span.start,
                    index
                );
                code.extend([
                    this.g(format!("{name}:{typ_text}="), *value),
                    this.big_owned(*value, Some(hint.clone()), false)?,
                    this.g(
                        format!("\ndefer {module}.compiler_destroy(&{name})\n"),
                        *value,
                    ),
                ]);
                names.push(name);
            }
            let condition = if names.is_empty() {
                "false".to_string()
            } else {
                names
                    .iter()
                    .map(|name| format!("{module}.equal({needle},{name})"))
                    .collect::<Vec<_>>()
                    .join(" || ")
            };
            code.push(this.g(
                if inverse {
                    format!("return !({condition})")
                } else {
                    format!("return {condition}")
                },
                id,
            ));
            Ok(Code::concat(code))
        })?;
        Ok(Some(self.helper_call(id, &name, args)))
    }
}
