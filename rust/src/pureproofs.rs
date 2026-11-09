//! Unevaluated native type proofs preserve constants and operand lifetimes.
//! Captured type parameters must obey the same contracts as resolved types.
use super::*;
impl Lower<'_, '_> {
    pub(super) fn pure_local_type(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        constant: bool,
    ) -> Result<Option<Code>> {
        let Some(parent) = self.source.node(id).parent else {
            return Ok(None);
        };
        let owner = self.source.node(parent);
        if !(owner.kind == "Compound"
            || owner.kind == "Declaration" && (owner.mutable || owner.immutable))
            || self.source.role(parent, "type") != Some(id)
        {
            return Ok(None);
        }
        if self.queried_pure_expression(id) {
            return Ok(None);
        }
        let typ = self.typ(id);
        if !self.unresolved_pure_operand(&typ, &mut HashSet::new()) {
            return Ok(None);
        }
        self.pure_local_busy.insert(id);
        let typ = self.render(id, expected, constant);
        self.pure_local_busy.remove(&id);
        let typ = typ?;
        let name = format!("{}pure_local_type", self.prefix);
        let key = (NodeId(0), self.prefix.clone(), name.clone());
        if !self.helpers.contains_key(&key) {
            let validator = self.pure_validator(id);
            let code=format!("\n@(private=\"file\")\n{name}::proc \"contextless\"($T:typeid,$Site:string)->T{{{validator}(T,true,true,true,0,Site);return {{}}}}\n");
            self.helpers.insert(key, self.g(code, id));
        }
        let proc = self
            .source
            .ancestors(id)
            .find(|owner| self.source.node(*owner).kind == "Procedure")
            .unwrap_or(id);
        let token = self.pure_type_site(id, proc, "local storage type");
        let type_of = self.native("type_of");
        Ok(Some(Code::concat([
            self.g(format!("{type_of}({name}("), id),
            typ,
            self.g(format!(",{token}))"), id),
        ])))
    }
    pub(super) fn pure_conversion(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        constant: bool,
    ) -> Result<Option<Code>> {
        let node = self.source.node(id);
        let target = match node.kind.as_str() {
            "Call" => self
                .source
                .role(id, "callee")
                .filter(|target| self.resolver.denotes_type(self.reference(*target))),
            "Cast" if node.op != "transmute" => self.source.role(id, "type"),
            _ => None,
        };
        let Some(target) = target else {
            return Ok(None);
        };
        if self.queried_pure_expression(id) {
            return Ok(None);
        }
        let mut typ = self.typ(target);
        while let Type::Nominal(_, base) = typ {
            typ = *base;
        }
        if !matches!(typ, Type::Unknown | Type::Generic) {
            return Ok(None);
        }
        self.pure_conversion_busy.insert(id);
        let expression = self.render(id, expected, constant);
        self.pure_conversion_busy.remove(&id);
        let expression = expression?;
        let module = self.module("pure");
        let type_of = self.native("type_of");
        let owner = self
            .source
            .ancestors(id)
            .find(|node| self.source.node(*node).kind == "Procedure")
            .unwrap_or(id);
        let token = self.pure_type_site(id, owner, "conversion target");
        Ok(Some(Code::concat([
            self.g(
                format!("({type_of}({module}.conversion_type({type_of}("),
                id,
            ),
            expression.clone(),
            self.g(format!("),{token}))) ("), id),
            expression,
            self.g(")", id),
        ])))
    }
    fn queried_pure_expression(&self, id: NodeId) -> bool {
        if let Some(proc) = self
            .source
            .ancestors(id)
            .find(|owner| self.source.node(*owner).kind == "Procedure")
        {
            if self.source.node(proc).data["pure"] != true {
                if let Some(params) = self
                    .source
                    .role(proc, "type")
                    .and_then(|sig| self.source.role(sig, "params"))
                {
                    if self.source.node(params).children.iter().any(|field| {
                        !self.pure_defaults.contains(field)
                            && self.source.role(*field, "default").is_some_and(|value| {
                                self.source
                                    .node(value)
                                    .span
                                    .contains(self.source.node(id).span)
                            })
                    }) {
                        return true;
                    }
                }
            }
        }
        self.source.ancestors(id).any(|ancestor| {
            self.source.node(ancestor).kind == "Call"
                && self.source.role(ancestor, "callee").is_some_and(|callee| {
                    ["type_of", "size_of", "align_of", "offset_of"]
                        .iter()
                        .any(|name| self.resolver.native_builtin(self.reference(callee), name))
                })
        })
    }
    fn unresolved_pure_operand(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(typ.clone()) {
            return false;
        }
        match typ {
            Type::Unknown | Type::Generic | Type::Soa(_) => true,
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .resolver
                .record_fields(typ)
                .iter()
                .any(|(_, typ)| self.unresolved_pure_operand(typ, seen)),
            Type::Union(owner) => self
                .resolver
                .union_variants(*owner)
                .iter()
                .any(|typ| self.unresolved_pure_operand(typ, seen)),
            Type::Nominal(_, base)
            | Type::Pointer(base)
            | Type::Slice(base)
            | Type::Dynamic(base)
            | Type::Array { elem: base, .. }
            | Type::Fixed { elem: base, .. } => self.unresolved_pure_operand(base, seen),
            _ => false,
        }
    }
    pub(super) fn pure_operands(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        constant: bool,
    ) -> Result<Option<Code>> {
        if self.queried_pure_expression(id) {
            return Ok(None);
        }
        let node = self.source.node(id);
        let mut operands = vec![];
        let mut boolean = false;
        match node.kind.as_str() {
            "Binary"
                if ["==", "!=", "<", "<=", ">", ">=", "in", "not_in"]
                    .contains(&node.op.as_str()) =>
            {
                boolean = true;
                operands.push(self.role(id, "left")?);
                if !["in", "not_in"].contains(&node.op.as_str()) {
                    operands.push(self.role(id, "right")?);
                }
                if operands
                    .iter()
                    .any(|value| self.source.text(*value) == "nil")
                {
                    return Ok(None);
                }
            }
            "Cast" if node.op != "transmute" => operands.push(self.role(id, "expr")?),
            "Call" => {
                let callee = self.role(id, "callee")?;
                if self.resolver.denotes_type(self.reference(callee)) {
                    operands.extend(self.source.group(id, "args").first().copied());
                } else if ["min", "max", "clamp"]
                    .iter()
                    .any(|name| self.resolver.native_builtin(self.reference(callee), name))
                {
                    operands.extend(self.source.group(id, "args").iter().copied());
                }
            }
            _ => {
                if let Some(parent) = node.parent.filter(|parent| {
                    ["Switch", "SwitchExpr"].contains(&self.source.node(*parent).kind.as_str())
                }) {
                    let selector = self.source.role(parent, "selector").or_else(|| {
                        self.source
                            .node(parent)
                            .children
                            .iter()
                            .rev()
                            .copied()
                            .find(|child| {
                                !["Block", "Declaration"]
                                    .contains(&self.source.node(*child).kind.as_str())
                            })
                    });
                    if selector == Some(id) {
                        let observes = if self.source.node(parent).kind == "Switch" {
                            self.source
                                .node(parent)
                                .children
                                .iter()
                                .filter(|child| self.source.node(**child).kind == "Block")
                                .flat_map(|block| self.source.node(*block).children.iter())
                                .filter(|case| self.source.node(**case).kind == "Case")
                                .any(|case| {
                                    self.source
                                        .group(*case, "types")
                                        .iter()
                                        .any(|value| self.source.text(*value) != "nil")
                                })
                        } else {
                            self.source.node(parent).data["cases"]
                                .as_array()
                                .into_iter()
                                .flatten()
                                .flat_map(|case| case["matches"].as_array().into_iter().flatten())
                                .any(|span| {
                                    span.as_array()
                                        .filter(|a| a.len() == 2)
                                        .and_then(|a| {
                                            Some((a[0].as_u64()? as usize, a[1].as_u64()? as usize))
                                        })
                                        .and_then(|(a, b)| self.source.raw.get(a..b))
                                        .is_none_or(|value| value != "nil")
                                })
                        };
                        if observes {
                            operands.push(id);
                        }
                    }
                }
            }
        }
        let mut checks = vec![];
        for operand in operands {
            let operand = if self.source.node(operand).kind == "FieldValue" {
                self.role(operand, "value")?
            } else {
                operand
            };
            if self.resolver.denotes_type(self.reference(operand))
                || self.source.node(operand).kind == "ImplicitSelector"
            {
                continue;
            }
            let typ = self.typ(operand);
            if self.unresolved_pure_operand(&typ, &mut HashSet::new()) {
                checks.push(operand);
            }
        }
        if checks.is_empty() {
            return Ok(None);
        }
        self.pure_operand_busy.insert(id);
        let expression = self.render(id, expected, constant);
        let result = (|| {
            let expression = expression?;
            let name = format!("{}pure_pointer_free_type_{}", self.prefix, checks.len());
            let key = (NodeId(0), self.prefix.clone(), name.clone());
            if !self.helpers.contains_key(&key) {
                let validator = self.pure_validator(id);
                let params = (0..checks.len())
                    .map(|i| format!("$T{i}:typeid,$Site{i}:string"))
                    .collect::<Vec<_>>()
                    .join(",");
                let calls = (0..checks.len())
                    .map(|i| format!("{validator}(T{i},true,true,false,0,Site{i})"))
                    .collect::<Vec<_>>()
                    .join(";");
                let code=format!("\n@(private=\"file\")\n{name}::proc \"contextless\"($R:typeid,{params})->R{{{calls};return {{}}}}\n");
                self.helpers.insert(key, self.g(code, id));
            }
            let type_of = self.native("type_of");
            self.default = true;
            let default = format!("{}default_value", self.prefix);
            let mut args = vec![];
            let owner = self
                .source
                .ancestors(id)
                .find(|node| self.source.node(*node).kind == "Procedure")
                .unwrap_or(id);
            for operand in checks {
                let token = self.pure_type_site(
                    operand,
                    owner,
                    "pointer-free comparison/conversion operand",
                );
                args.push(self.g(format!(",{type_of}({default}("), operand));
                if operand == id {
                    args.push(expression.clone());
                } else {
                    args.push(self.render(operand, None, false)?);
                }
                args.push(self.g(format!(")),{token}"), operand));
            }
            let result_type = if boolean {
                self.g("bool", id)
            } else {
                Code::concat([
                    self.g(format!("{type_of}({default}("), id),
                    expression.clone(),
                    self.g("))", id),
                ])
            };
            Ok(Some(Code::concat([
                self.g(format!("({type_of}({name}("), id),
                result_type,
                Code::concat(args),
                self.g(")))(", id),
                expression,
                self.g(")", id),
            ])))
        })();
        self.pure_operand_busy.remove(&id);
        result
    }
}
