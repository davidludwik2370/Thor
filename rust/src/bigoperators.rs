//! Numeric operations retain Odin's native specialization as the other branch.
use super::*;
impl Lower<'_, '_> {
    fn big_operands(&self, id: NodeId) -> Vec<NodeId> {
        if self.source.node(id).kind == "Binary" {
            ["left", "right"]
                .iter()
                .filter_map(|role| self.source.role(id, role))
                .collect()
        } else {
            self.source.role(id, "expr").into_iter().collect()
        }
    }
    fn big_chosen(&mut self, id: NodeId, hint: Option<&Hint>) -> Result<Code> {
        if let Some(hint) = hint.filter(|hint| {
            hint.semantic
                .as_ref()
                .is_some_and(|typ| typ.big_name().is_some() || matches!(typ, Type::Generic))
        }) {
            return Ok(hint.code.clone());
        }
        let values = self.big_operands(id);
        for logical in [true, false] {
            for value in &values {
                let typ = self.typ(*value);
                if if logical {
                    typ.big_name().is_some()
                } else {
                    typ.is_unknown()
                } {
                    return self.native_type(*value);
                }
            }
        }
        self.native_type(values[0])
    }
    pub(super) fn big_operator(
        &mut self,
        id: NodeId,
        hint: Option<Hint>,
        constant: bool,
    ) -> Result<Code> {
        let values = self.big_operands(id);
        let node = self.source.node(id);
        let op = node.op.clone();
        if constant {
            return Ok(self.error_token(id,"Compile-time Integer/Rational operators require matching, compile-time-known numeric operands; use fn/#run for computation through procedures."));
        }
        let rational = values
            .iter()
            .any(|v| matches!(self.typ(*v), Type::BigRational));
        if rational && matches!(op.as_str(), "&" | "|" | "~" | "&~" | "<<" | ">>") {
            return Ok(
                self.error_token(id, "Rational does not support bitwise operators or shifts.")
            );
        }
        if values.len() == 2 {
            let right = values[1];
            let number = self.resolver.constant_int(self.reference(right));
            if matches!(op.as_str(), "<<" | ">>") && number.is_some_and(|n| n < 0) {
                return Ok(self.error_token(right, "Integer shift counts must be nonnegative."));
            }
            if op == "**"
                && number.is_some_and(|n| n < 0)
                && values
                    .iter()
                    .any(|v| matches!(self.typ(*v), Type::BigInteger))
            {
                return Ok(self.error_token(right,"Integer powers require a nonnegative exponent; use Rational for negative powers."));
            }
            if matches!(op.as_str(), "/" | "%" | "%%") && self.big_statically_zero(right) {
                return Ok(self.error_token(right, "A big-number divisor must not be zero."));
            }
        }
        let comparison = matches!(op.as_str(), "==" | "!=" | "<" | "<=" | ">" | ">=");
        let module = self.module("big");
        let chosen = self.big_chosen(id, hint.as_ref())?;
        let (name, mut inputs) = self.closed(
            id,
            "big_operator",
            if comparison { Some("bool") } else { Some("") },
            |this| {
                let typ = if comparison {
                    this.big_chosen(id, hint.as_ref())?
                } else {
                    this.g(format!("{}Result", this.prefix), id)
                };
                let typ_text = typ.clone().finish().text;
                let mut code = vec![this.g(
                    format!(
                        "when {typ_text}=={module}.Integer || {typ_text}=={module}.Rational {{\n"
                    ),
                    id,
                )];
                let mut names = vec![];
                for (index, value) in values.iter().enumerate() {
                    let name = format!(
                        "{}big_operand_{}_{}",
                        this.prefix,
                        this.source.node(id).span.start,
                        index
                    );
                    let expected = Hint {
                        code: typ.clone(),
                        semantic: Some(Type::Generic),
                        element_only: false,
                        plain_array: false,
                    };
                    code.extend([
                        this.g(format!("{name}: {typ_text} = "), *value),
                        this.big_owned(*value, Some(expected), false)?,
                        this.g(
                            format!("\ndefer {module}.compiler_destroy(&{name})\n"),
                            *value,
                        ),
                    ]);
                    names.push(name);
                }
                let method = if comparison {
                    "compare"
                } else if values.len() == 2 {
                    "binary"
                } else {
                    "unary"
                };
                names.push(json!(op).to_string());
                code.push(this.g(
                    format!(
                        "return {module}.{method}({})\n}} else {{return ",
                        names.join(",")
                    ),
                    id,
                ));
                this.logical.busy.insert(id);
                let native = this.render(id, hint.clone(), false);
                this.logical.busy.remove(&id);
                code.extend([native?, this.g("}", id)]);
                Ok(Code::concat(code))
            },
        )?;
        if !comparison {
            inputs.push(chosen);
        }
        Ok(self.helper_call(id, &name, inputs))
    }
    pub(super) fn big_update_assignment(&mut self, id: NodeId) -> Result<Option<Code>> {
        let lhs = self.source.group(id, "lhs").to_vec();
        let rhs = self.source.group(id, "rhs").to_vec();
        let op = self.source.node(id).op.trim_end_matches('=').to_string();
        if lhs.len() != 1
            || rhs.len() != 1
            || !matches!(
                op.as_str(),
                "+" | "-" | "*" | "/" | "%" | "%%" | "**" | "&" | "|" | "~" | "&~" | "<<" | ">>"
            )
        {
            return Ok(None);
        }
        let typ = self.typ(lhs[0]);
        if typ.big_name().is_none() && !typ.is_unknown() {
            return Ok(None);
        }
        self.big_statement(id,|this|{
            let module=this.module("big");let mut code=vec![];let targets=this.big_capture_targets(id,&lhs,&mut code)?;let target=&targets[0];let typ=this.native_type(lhs[0])?;let text=typ.clone().finish().text;
            let old=format!("{}big_update_old_{}",this.prefix,this.source.node(id).span.start);let right=format!("{old}_rhs");let result=format!("{old}_result");
            code.push(this.g(format!("when {text}=={module}.Integer || {text}=={module}.Rational {{\n{old}:={module}.compiler_clone({target})\ndefer {module}.compiler_destroy(&{old})\n{right}:{text}="),id));
            let hint=Hint{code:typ,semantic:Some(Type::Generic),element_only:false,plain_array:false};
            code.extend([this.big_owned(rhs[0],Some(hint),false)?,this.g(format!("\ndefer {module}.compiler_destroy(&{right})\n{result}:={module}.binary({old},{right},{})\n",json!(op)),id)]);
            this.big_commit(id,&targets,&[result],&mut code);
            this.logical.busy.insert(id);let native=this.render(id,None,false);this.logical.busy.remove(&id);
            code.extend([this.g("} else {",id),native?,this.g("}",id)]);Ok(Code::concat(code))
        }).map(Some)
    }
}
