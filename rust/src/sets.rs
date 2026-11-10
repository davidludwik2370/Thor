//! Set syntax retains shallow ownership. Shared native procedures dispatch
//! generic operators after Odin specializes their operand types.
use super::*;

impl Lower<'_, '_> {
    pub(super) fn set_trait(&mut self, id: NodeId) -> Result<Code> {
        self.querying += 1;
        let rendered = self.render(id, None, false);
        self.querying -= 1;
        let rendered = rendered?.finish().text;
        let test = self.set_value_trait(&rendered);
        Ok(self.g(test, id))
    }
    pub(super) fn set_value_trait(&mut self, rendered: &str) -> String {
        self.default = true;
        self.meta = true;
        let type_of = self.native("type_of");
        let sets = self.module("sets");
        let m = format!("{}meta", self.prefix);
        let t = format!("{type_of}({}default_value({rendered}))", self.prefix);
        format!("((({t}=={sets}.Set({type_of}({sets}.key_witness(({rendered}).table)))) when {m}.type_is_map({type_of}(({rendered}).table)) else false) when {m}.type_has_field({t},\"table\") else false)")
    }
    pub(super) fn set_expression(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        let s = self.source;
        let node = s.node(id);
        if node.kind == "SetType" {
            let arg = *s
                .group(id, "args")
                .first()
                .ok_or_else(|| Error::new("set element type omitted"))?;
            let sets = self.module("sets");
            let element = self.render(arg, None, true)?;
            return Ok(Some(Code::concat([
                self.g(format!("{sets}.Set("), id),
                element,
                self.g(")", id),
            ])));
        }
        if node.kind == "Compound" {
            if let Some(annotation) = s.role(id, "type") {
                if matches!(self.typ(annotation), Type::Set(_)) {
                    let sets = self.module("sets");
                    let target = self.render(annotation, None, true)?;
                    let type_of = self.native("type_of");
                    let element = Code::concat([
                        self.g(format!("{type_of}({sets}.element_witness(("), id),
                        target,
                        self.g("){}))", id),
                    ]);
                    let hint = Hint {
                        semantic: self.typ(annotation).element().cloned(),
                        code: element.clone(),
                        element_only: false,
                        plain_array: false,
                    };
                    let values = s.group(id, "elems").to_vec();
                    let mut code = vec![
                        self.g(format!("{sets}.from_values("), id),
                        element.clone(),
                        self.g(format!(",[{}]", values.len()), id),
                        element,
                        self.g("{", id),
                    ];
                    for (i, value) in values.iter().enumerate() {
                        if s.node(*value).kind == "FieldValue" {
                            return Ok(Some(self.error_token(
                                *value,
                                "A Set literal contains element expressions, without field names.",
                            )));
                        }
                        if i > 0 {
                            code.push(self.g(", ", id));
                        }
                        code.push(self.render(*value, Some(hint.clone()), constant)?);
                    }
                    code.push(self.g("})", id));
                    return Ok(Some(Code::concat(code)));
                }
            }
        }
        if node.kind == "Binary" {
            let a = self.role(id, "left")?;
            let b = self.role(id, "right")?;
            let at = self.typ(a);
            let bt = self.typ(b);
            let op = match node.op.as_str() {
                "|" => Some(("union_of", "union")),
                "&" => Some(("intersection", "intersection")),
                "-" => Some(("difference", "difference")),
                "~" => Some(("symmetric_difference", "symmetric_difference")),
                "==" | "!=" => Some(("equal", "equal")),
                _ => None,
            };
            if let Some((named, generic_name)) = op {
                let set = matches!(at, Type::Set(_)) || matches!(bt, Type::Set(_));
                let generic = at.is_unknown()
                    && bt.is_unknown()
                    && !constant
                    && !self.resolver.denotes_type(self.reference(a))
                    && !self.resolver.denotes_type(self.reference(b))
                    && s.text(a) != "nil"
                    && s.text(b) != "nil";
                if set || generic {
                    let sets = self.module("sets");
                    let name = if set {
                        named.to_string()
                    } else {
                        format!("compiler_{generic_name}")
                    };
                    let left = self.render(a, None, constant)?;
                    let right = self.render(b, None, constant)?;
                    return Ok(Some(Code::concat([
                        self.g(
                            format!("{}({sets}.{name}(", if node.op == "!=" { "!" } else { "" }),
                            id,
                        ),
                        left,
                        self.g(", ", id),
                        right,
                        self.g("))", id),
                    ])));
                }
            }
        }
        if node.kind == "Assignment"
            && s.group(id, "lhs").len() == 1
            && s.group(id, "rhs").len() == 1
        {
            let left = s.group(id, "lhs")[0];
            let right = s.group(id, "rhs")[0];
            let typ = self.typ(left);
            let name = if matches!(typ, Type::Set(_)) {
                match node.op.as_str() {
                    "+=" => Some("insert"),
                    "-=" => Some("subtract_assign"),
                    "|=" => Some("union_assign"),
                    "&=" => Some("intersection_assign"),
                    "~=" => Some("symmetric_difference_assign"),
                    _ => None,
                }
                .map(str::to_string)
            } else if typ.is_unknown() {
                match node.op.as_str() {
                    "+=" => Some("add"),
                    "-=" => Some("sub"),
                    "|=" => Some("union"),
                    "&=" => Some("intersection"),
                    "~=" => Some("xor"),
                    _ => None,
                }
                .map(|name| format!("compiler_{name}_assign"))
            } else {
                None
            };
            if let Some(name) = name {
                let sets = self.module("sets");
                if let Some(code) = self.temporary_update(id, &format!("{sets}.{name}"))? { return Ok(Some(code)); }
                let post = s.node(id).parent.is_some_and(|parent| {
                    s.node(parent).kind == "For" && s.role(parent, "post") == Some(id)
                });
                let build = |this: &mut Self| -> Result<Code> {
                    let lhs = this.render(left, None, false)?;
                    let rhs = this.render(right, None, false)?;
                    Ok(Code::concat([
                        this.g(format!("{sets}.{name}(&("), left),
                        lhs,
                        this.g("), ", id),
                        rhs,
                        this.g(")", id),
                    ]))
                };
                if post {
                    let (name, args) = self.closed(id, "update", Some("bool"), |this| {
                        let code = build(this)?;
                        Ok(Code::concat([code, this.g("\nreturn true", id)]))
                    })?;
                    let call = self.helper_call(id, &name, args);
                    return Ok(Some(Code::concat([self.g("_ = ", id), call])));
                }
                return build(self).map(Some);
            }
        }
        if node.kind == "Call" && s.group(id, "args").len() == 1 {
            let callee = self.role(id, "callee")?;
            let name = s.text(callee);
            if self.resolver.native_builtin(self.reference(callee), name) {
                let arg = s.group(id, "args")[0];
                let typ = self.typ(arg);
                if name == "len"
                    && (matches!(typ, Type::Set(_)) || typ.is_unknown())
                    && !self.resolver.denotes_type(self.reference(arg))
                {
                    let sets = self.module("sets");
                    let value = self.render(arg, None, constant)?;
                    if matches!(typ, Type::Set(_)) {
                        return Ok(Some(Code::concat([
                            self.g(format!("{sets}.count("), id),
                            value,
                            self.g(")", id),
                        ])));
                    }
                    let trait_ = self.set_trait(arg)?;
                    let len = self.native("len");
                    return Ok(Some(Code::concat([
                        self.g(format!("({len}(("), id),
                        value.clone(),
                        self.g(").table) when ", id),
                        trait_,
                        self.g(format!(" else {len}("), id),
                        value,
                        self.g("))", id),
                    ])));
                }
                let target = match (name, &typ) {
                    ("delete", Type::Set(_)) => Some(("destroy", false)),
                    ("delete", Type::Generic | Type::Unknown) => Some(("compiler_delete", false)),
                    ("clear", Type::Set(_)) => Some(("clear_set", true)),
                    ("clear", Type::Pointer(elem)) if matches!(**elem, Type::Set(_)) => {
                        Some(("clear_set", false))
                    }
                    ("clear", Type::Pointer(elem)) if elem.is_unknown() => {
                        Some(("compiler_clear", false))
                    }
                    _ => None,
                };
                if let Some((name, byref)) = target {
                    let sets = self.module("sets");
                    let value = self.render(arg, None, false)?;
                    return Ok(Some(Code::concat([
                        self.g(
                            format!("{sets}.{name}({}", if byref { "&(" } else { "" }),
                            id,
                        ),
                        value,
                        self.g(if byref { "))" } else { ")" }, id),
                    ])));
                }
            }
        }
        if node.kind == "Range" {
            let expr = self.role(id, "expr")?;
            let typ = self.typ(expr);
            let generic = typ.is_unknown()
                && !self.resolver.denotes_type(self.reference(expr))
                && s.node(expr).kind != "Call"
                && !s
                    .group(id, "names")
                    .iter()
                    .any(|name| s.node(*name).kind == "Unary");
            if matches!(typ, Type::Set(_)) || generic {
                let sets = self.module("sets");
                let value = self.render(expr, None, false)?;
                let iterator = Code::concat([
                    self.g(format!("{sets}.entries("), expr),
                    value,
                    self.g(")", expr),
                ]);
                let mut replacements = vec![];
                for child in &node.children {
                    let code = if *child == expr {
                        iterator.clone()
                    } else {
                        self.render(*child, None, false)?
                    };
                    replacements.push((s.node(*child).span, code));
                }
                let selected = self.splice(id, replacements, vec![])?;
                if !generic {
                    return Ok(Some(selected));
                }
                let condition = self.set_trait(expr)?;
                let mut replacements = vec![];
                for child in &node.children {
                    replacements.push((s.node(*child).span, self.render(*child, None, false)?));
                }
                let native = self.splice(id, replacements, vec![])?;
                return Ok(Some(Code::concat([
                    self.g("{when ", id),
                    condition,
                    self.g("{\n", id),
                    selected,
                    self.g("\n} else {\n", id),
                    native,
                    self.g("\n}}", id),
                ])));
            }
        }
        Ok(None)
    }
}
