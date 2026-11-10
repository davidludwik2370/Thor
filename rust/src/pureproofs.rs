//! Unevaluated native type proofs preserve constants and operand lifetimes.
//! Captured type parameters must obey the same contracts as resolved types.
use super::*;
use crate::semantics::Substitutions;
impl Lower<'_, '_> {
    pub(super) fn pure_auto_cast(
        &mut self,
        id: NodeId,
        mut expected: Option<Hint>,
        constant: bool,
    ) -> Result<Code> {
        let value = self.role(id, "expr")?;
        if self.queried_pure_expression(id) {
            let expression = self.render(value, None, constant)?;
            return self.splice(id, vec![(self.source.node(value).span, expression)], vec![]);
        }
        let mut parent = self.source.node(id).parent;
        while parent.is_some_and(|node| self.source.node(node).kind == "Paren") {
            parent = self.source.node(parent.unwrap()).parent;
        }
        let source_typ = self.typ(value);
        let source_shape = self.pure_conversion_shape(&source_typ, &mut HashSet::new());
        let mut forced = source_shape == Some(true);
        let call = parent
            .and_then(|p| {
                if self.source.node(p).kind == "FieldValue" {
                    self.source.node(p).parent
                } else {
                    Some(p)
                }
            })
            .filter(|p| self.source.node(*p).kind == "Call");
        if let Some(call) = call {
            let callee = self.role(call, "callee")?;
            if !self
                .resolver
                .native_builtin(self.reference(callee), "append")
            {
                let args = self
                    .source
                    .group(call, "args")
                    .iter()
                    .map(|node| self.reference(*node))
                    .collect::<Vec<_>>();
                let mut shapes = vec![];
                let mut indices = vec![];
                for candidate in self.resolver.candidates(self.reference(callee)) {
                    let owner = &self.resolver.sources[candidate.file];
                    let signature = if owner.node(candidate.node).kind == "Procedure" {
                        Ref {
                            file: candidate.file,
                            node: owner.role(candidate.node, "type").unwrap_or(candidate.node),
                        }
                    } else {
                        candidate
                    };
                    let Some(groups) = self.resolver.arrange_arguments(signature, &args) else {
                        continue;
                    };
                    let Some(index) = groups.iter().position(|group| {
                        group.iter().any(|arg| {
                            arg.file == self.reference(id).file
                                && self
                                    .source
                                    .node(arg.node)
                                    .span
                                    .contains(self.source.node(id).span)
                        })
                    }) else {
                        continue;
                    };
                    let fields = self.resolver.fields(signature, "params");
                    let typ = fields[index]
                        .1
                        .map(|annotation| {
                            self.resolver
                                .specialized_type(annotation, &Substitutions::new())
                        })
                        .unwrap_or(Type::Unknown);
                    shapes.push(self.pure_conversion_shape(&typ, &mut HashSet::new()));
                    let owner = &self.resolver.sources[signature.file];
                    let names = owner
                        .role(signature.node, "params")
                        .map(|params| {
                            owner
                                .node(params)
                                .children
                                .iter()
                                .flat_map(|field| owner.group(*field, "names"))
                                .copied()
                                .collect::<Vec<_>>()
                        })
                        .unwrap_or_default();
                    let runtime = names[..index]
                        .iter()
                        .filter(|name| owner.node(**name).kind != "PolyType")
                        .count();
                    let variadic = fields[index].1.is_some_and(|annotation| {
                        self.resolver.sources[annotation.file]
                            .node(annotation.node)
                            .kind
                            == "Ellipsis"
                    }) && !self.source.node(call).spans.contains_key("ellipsis");
                    indices.push(if owner.node(names[index]).kind == "PolyType" {
                        None
                    } else {
                        Some((runtime, variadic))
                    });
                }
                if !shapes.is_empty() && shapes.iter().all(|shape| *shape == Some(false)) {
                    expected = None;
                    forced = true;
                } else if !indices.is_empty()
                    && indices[0].is_some()
                    && indices.iter().all(|index| *index == indices[0])
                {
                    self.querying += 1;
                    let original = self.render(call, None, false);
                    self.querying -= 1;
                    let original = original?;
                    self.meta = true;
                    let query = self.native("type_of");
                    let (index, variadic) = indices[0].unwrap();
                    let mut code = Code::concat([
                        self.g(
                            format!(
                                "{}meta.type_proc_parameter_type({query}({}meta.procedure_of(",
                                self.prefix, self.prefix
                            ),
                            id,
                        ),
                        original,
                        self.g(format!(")),{index})"), id),
                    ]);
                    if variadic {
                        code = Code::concat([
                            self.g(format!("{}meta.type_elem_type(", self.prefix), id),
                            code,
                            self.g(")", id),
                        ]);
                    }
                    expected = Some(Hint {
                        semantic: None,
                        code,
                        element_only: false,
                        plain_array: false,
                    });
                } else {
                    expected = None;
                }
            }
        }

        if expected.is_none() && !forced {
            if let Some(parent) = parent {
                let node = self.source.node(parent);
                if node.kind == "Binary" {
                    if ["==", "!=", "<", "<=", ">", ">="].contains(&node.op.as_str()) {
                        let left = self.role(parent, "left")?;
                        let other = if self
                            .source
                            .node(left)
                            .span
                            .contains(self.source.node(id).span)
                        {
                            self.role(parent, "right")?
                        } else {
                            left
                        };
                        let expression = self.render(other, None, constant)?;
                        let query = self.native("type_of");
                        expected = Some(Hint {
                            semantic: None,
                            code: Code::concat([
                                self.g(format!("{query}("), id),
                                expression,
                                self.g(")", id),
                            ]),
                            element_only: false,
                            plain_array: false,
                        });
                    } else if [
                        "+", "-", "*", "/", "%", "%%", "&", "|", "~", "<<", ">>", "..", "..<",
                        "..=",
                    ]
                    .contains(&node.op.as_str())
                    {
                        forced = true;
                    }
                } else if ["Index", "Slice", "Range", "Unary", "AutoCast", "Cast"]
                    .contains(&node.kind.as_str())
                {
                    forced = true;
                } else if ["If", "When", "Ternary", "WhenExpr"].contains(&node.kind.as_str())
                    && self.source.role(parent, "cond").is_some_and(|cond| {
                        self.source
                            .node(cond)
                            .span
                            .contains(self.source.node(id).span)
                    })
                {
                    expected = Some(Hint {
                        semantic: None,
                        code: self.g("bool", id),
                        element_only: false,
                        plain_array: false,
                    });
                } else if ["FieldValue", "Compound"].contains(&node.kind.as_str()) {
                    let compound = if node.kind == "FieldValue" {
                        self.source.node(parent).parent
                    } else {
                        Some(parent)
                    };
                    if let Some(compound) =
                        compound.filter(|node| self.source.node(*node).kind == "Compound")
                    {
                        if let Some(annotation) = self.source.role(compound, "type") {
                            let typ = self.typ(annotation);
                            let field = if node.kind == "FieldValue" {
                                self.source
                                    .role(parent, "field")
                                    .map(|field| self.source.text(field).to_string())
                            } else {
                                let fields = self.resolver.record_fields(&typ);
                                self.source
                                    .group(compound, "elems")
                                    .iter()
                                    .position(|value| *value == id)
                                    .and_then(|i| fields.get(i).map(|(name, _)| name.clone()))
                            };
                            let typ_code = self.render(annotation, None, true)?;
                            self.meta = true;
                            if let Some(field) = field {
                                expected = Some(Hint {
                                    semantic: None,
                                    code: Code::concat([
                                        self.g(format!("{}meta.type_field_type(", self.prefix), id),
                                        typ_code,
                                        self.g(format!(",{})", json!(field)), id),
                                    ]),
                                    element_only: false,
                                    plain_array: false,
                                });
                            } else if matches!(
                                typ,
                                Type::Array { .. }
                                    | Type::Slice(_)
                                    | Type::Dynamic(_)
                                    | Type::Fixed { .. }
                            ) {
                                expected = Some(Hint {
                                    semantic: None,
                                    code: Code::concat([
                                        self.g(format!("{}meta.type_elem_type(", self.prefix), id),
                                        typ_code,
                                        self.g(")", id),
                                    ]),
                                    element_only: false,
                                    plain_array: false,
                                });
                            }
                        }
                    }
                } else if node.kind == "Call"
                    && self.source.role(parent, "callee").is_some_and(|callee| {
                        self.resolver
                            .native_builtin(self.reference(callee), "append")
                    })
                    && self.source.group(parent, "args").iter().skip(1).any(|arg| {
                        self.source
                            .node(*arg)
                            .span
                            .contains(self.source.node(id).span)
                    })
                {
                    let destination = self.source.group(parent, "args")[0];
                    let expression = self.render(destination, None, false)?;
                    let query = self.native("type_of");
                    self.meta = true;
                    expected = Some(Hint {
                        semantic: None,
                        code: Code::concat([
                            self.g(format!("{}meta.type_elem_type({query}((", self.prefix), id),
                            expression,
                            self.g(")^))", id),
                        ]),
                        element_only: false,
                        plain_array: false,
                    });
                }
            }
        }
        if expected.is_none() && !forced {
            return Ok(self.error_token(id, "Pure auto_cast requires a resolved target type; use an explicit numeric conversion in this context."));
        }
        let expression = self.render(value, None, constant)?;
        let proof = if let Some(hint) = expected {
            Some(self.pure_conversion_proof(id, hint.code, Some(value))?)
        } else if source_shape != Some(false) {
            let name = format!("{}pure_auto_cast_operand", self.prefix);
            let key = (NodeId(0), self.prefix.clone(), name.clone());
            if !self.helpers.contains_key(&key) {
                let validator = format!("{}_mode", self.pure_validator(id));
                let code=format!("\n@(private=\"file\")\n{name}::proc \"contextless\"($S:typeid,$Site:string)->S{{{validator}(S,true,true,false,0,Site,2);return {{}}}}\n");
                self.helpers.insert(key, self.g(code, id));
            }
            self.default = true;
            let query = self.native("type_of");
            let owner = self
                .source
                .ancestors(id)
                .find(|node| self.source.node(*node).kind == "Procedure")
                .unwrap_or(id);
            let token = self.pure_type_site(id, owner, "auto_cast operand");
            Some(Code::concat([
                self.g(
                    format!("{query}({name}({query}({}default_value(", self.prefix),
                    id,
                ),
                expression.clone(),
                self.g(format!(")),{token}))"), id),
            ]))
        } else {
            None
        };
        if let Some(proof) = proof {
            let size = self.native("size_of");
            return Ok(Code::concat([
                self.g("auto_cast ((", id),
                expression.clone(),
                self.g(format!(") when {size}("), id),
                proof,
                self.g(")>=0 else (", id),
                expression,
                self.g("))", id),
            ]));
        }
        Ok(Code::concat([self.g("auto_cast ", id), expression]))
    }
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
        let Some(_target) = target else {
            return Ok(None);
        };
        if self.queried_pure_expression(id) {
            return Ok(None);
        }
        if self.resolver.denotes_type(self.reference(id)) {
            return Ok(None);
        }
        if self.big_constructor_type(id).is_some(){return Ok(None);}
        let target_type = self.typ(_target);
        let operand = if node.kind == "Cast" {
            self.source.role(id, "expr")
        } else {
            self.source.group(id, "args").first().copied()
        };
        let source_safe = if let Some(operand) = operand {
            let typ = self.typ(operand);
            self.pure_conversion_shape(&typ, &mut HashSet::new()) == Some(false)
        } else {
            true
        };
        if source_safe
            && self.pure_conversion_shape(&target_type, &mut HashSet::new()) == Some(false)
        {
            return Ok(None);
        }
        self.pure_conversion_busy.insert(id);
        let expression = self.render(id, expected, constant);
        self.pure_conversion_busy.remove(&id);
        let expression = expression?;
        let operand = if node.kind == "Cast" {
            self.source.role(id, "expr")
        } else {
            self.source.group(id, "args").first().copied()
        };
        let query = self.native("type_of");
        let target = Code::concat([
            self.g(format!("{query}("), id),
            expression.clone(),
            self.g(")", id),
        ]);
        let proof = self.pure_conversion_proof(id, target, operand)?;
        Ok(Some(Code::concat([
            self.g("(", id),
            proof,
            self.g(")(", id),
            expression,
            self.g(")", id),
        ])))
    }
    pub(super) fn pure_transmute(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        if self.queried_pure_expression(id) {
            return Ok(None);
        }
        let target = self.role(id, "type")?;
        let value = self.role(id, "expr")?;
        let target_type = self.typ(target);
        let value_type = self.typ(value);
        if [target_type, value_type]
            .iter()
            .all(|typ| crate::purity::Purity::scalar_transmute(typ).is_some())
        {
            return Ok(None);
        }
        self.pure_conversion_busy.insert(id);
        let result = (|| {
            let (name, mut args) = self.closed(id, "pure_transmute", Some(""), |this| {
                this.meta = true;
                let meta = format!("{}meta", this.prefix);
                let target = this.render(target, None, true)?;
                let query = this.native("type_of");
                let source = this.query_value(value)?;
                let scalar = |typ: &str| ["numeric", "boolean", "enum", "bit_set"].iter()
                    .map(|kind| format!("{meta}.type_is_{kind}({typ})")).collect::<Vec<_>>().join(" || ");
                let condition = format!("({}) && ({})", scalar(&target.finish().text), scalar(&format!("{query}({})", source.finish().text)));
                let expression = this.render(id, None, constant)?;
                let token = this.error_token(id, "Pure transmute requires scalar values; pointer and aggregate representations are not eligible.").finish().text;
                this.pure_error_sites.insert(token.clone(), this.reference(id));
                Ok(Code::concat([
                    this.g(format!("when {condition} {{return "), id),
                    expression,
                    this.g(format!("}} else {{#panic({})}}", json!(token)), id),
                ]))
            })?;
            args.push(self.render(target, None, true)?);
            Ok(Some(self.helper_call(id, &name, args)))
        })();
        self.pure_conversion_busy.remove(&id);
        result
    }
    fn pure_conversion_shape(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> Option<bool> {
        if !seen.insert(typ.clone()) {
            return None;
        }
        let children = match typ {
            Type::Unknown | Type::Generic => return None,
            Type::Pointer(_) | Type::Procedure(_) | Type::Map { .. } | Type::Set(_) => {
                return Some(true)
            }
            Type::Scalar { name, .. } | Type::String { name, .. }
                if [
                    "rawptr",
                    "any",
                    "typeid",
                    "cstring",
                    "cstring16",
                    "string16",
                ]
                .contains(&name.as_str()) =>
            {
                return Some(true)
            }
            Type::Nominal(_, base) => vec![*base.clone()],
            Type::Array { elem, .. }
            | Type::Slice(elem)
            | Type::Dynamic(elem)
            | Type::Fixed { elem, .. }
            | Type::Soa(elem) => vec![*elem.clone()],
            Type::Struct(owner) | Type::Record(owner, _)
                if self.resolver.sources[owner.file].node(owner.node).op == "raw_union" =>
            {
                return Some(true)
            }
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .resolver
                .record_fields(typ)
                .into_iter()
                .map(|(_, typ)| typ)
                .collect(),
            Type::Union(owner) => self.resolver.union_variants(*owner),
            _ => return Some(false),
        };
        let shapes = children
            .iter()
            .map(|typ| self.pure_conversion_shape(typ, &mut seen.clone()))
            .collect::<Vec<_>>();
        if shapes.contains(&Some(true)) {
            Some(true)
        } else if shapes.contains(&None) {
            None
        } else {
            Some(false)
        }
    }
    fn pure_conversion_proof(
        &mut self,
        id: NodeId,
        target: Code,
        operand: Option<NodeId>,
    ) -> Result<Code> {
        let check = operand
            .filter(|operand| {
                let node = self.source.node(*operand);
                node.kind != "ImplicitSelector"
                    && self.source.text(*operand) != "nil"
                    && !(node.kind == "Compound" && self.source.role(*operand, "type").is_none())
            })
            .filter(|operand| {
                let typ = self.typ(*operand);
                self.pure_conversion_shape(&typ, &mut HashSet::new()) != Some(false)
            });
        let name = format!(
            "{}pure_conversion_type{}",
            self.prefix,
            if check.is_some() { "_source" } else { "" }
        );
        let key = (NodeId(0), self.prefix.clone(), name.clone());
        if !self.helpers.contains_key(&key) {
            let validator = format!("{}_mode", self.pure_validator(id));
            let source = if check.is_some() { ",$S:typeid" } else { "" };
            let checks = if check.is_some() {
                format!(";{validator}(S,true,true,false,0,Site,2)")
            } else {
                String::new()
            };
            let code=format!("\n@(private=\"file\")\n{name}::proc \"contextless\"($T:typeid,$Site:string{source})->T{{{validator}(T,true,true,false,0,Site,1){checks};return {{}}}}\n");
            self.helpers.insert(key, self.g(code, id));
        }
        let owner = self
            .source
            .ancestors(id)
            .find(|node| self.source.node(*node).kind == "Procedure")
            .unwrap_or(id);
        let token = self.pure_type_site(id, owner, "conversion target and operand");
        let query = self.native("type_of");
        let mut code = vec![
            self.g(format!("{query}({name}("), id),
            target,
            self.g(format!(",{token}"), id),
        ];
        if let Some(operand) = check {
            self.default = true;
            let expression = self.render(operand, None, false)?;
            code.extend([
                self.g(format!(",{query}({}default_value(", self.prefix), id),
                expression,
                self.g("))", id),
            ]);
        }
        code.push(self.g("))", id));
        Ok(Code::concat(code))
    }
    pub(super) fn queried_pure_expression(&mut self, id: NodeId) -> bool {
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
                && crate::purity::builtin_call(
                    self.resolver,
                    Ref {
                        file: self.file,
                        node: ancestor,
                    },
                )
                .is_some_and(|name| {
                    ["type_of", "size_of", "align_of", "offset_of"].contains(&name.as_str())
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
                } else if crate::purity::builtin_call(self.resolver, self.reference(id))
                    .is_some_and(|name| ["min", "max", "clamp"].contains(&name.as_str()))
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
