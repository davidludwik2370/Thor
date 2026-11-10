//! Optional and multiple numeric results retain the caller's control flow.
use super::*;
impl Lower<'_, '_> {
    pub(super) fn big_numeric_results(&mut self, id: NodeId, count: usize) -> bool {
        (0..count).any(|index| {
            let typ = self
                .resolver
                .result_type(self.reference(id), index, &mut HashSet::new());
            typ.big_name().is_some() || matches!(typ, Type::Generic)
        })
    }
    pub(super) fn big_result_types(&mut self, id: NodeId, count: usize) -> Result<Vec<Code>> {
        self.meta = true;
        let code = self.query_value(id)?;
        Ok((0..count)
            .map(|index| {
                Code::concat([
                    self.g(
                        format!(
                            "{}meta.type_proc_return_type(type_of({}meta.procedure_of(",
                            self.prefix, self.prefix
                        ),
                        id,
                    ),
                    code.clone(),
                    self.g(format!(")),{index})"), id),
                ])
            })
            .collect())
    }
    pub(super) fn big_multi_declaration(&mut self, id: NodeId) -> Result<Option<Code>> {
        let names = self.source.group(id, "names").to_vec();
        let values = self.source.group(id, "values").to_vec();
        if values.len() != 1 || names.len() < 2 || !self.big_numeric_results(values[0], names.len())
        {
            return Ok(None);
        }
        let global = !self
            .source
            .ancestors(id)
            .any(|a| self.source.node(a).kind == "Procedure");
        if !global && !names.iter().any(|name| self.source.text(*name) == "_") {
            return Ok(None);
        }
        let types = self.big_result_types(values[0], names.len())?;
        let module = self.module("big");
        let flavor = if global {
            "big_global_results"
        } else {
            "big_discard_results"
        };
        let result = if global {
            String::new()
        } else {
            format!("tuple:{}", names.len())
        };
        let (helper, mut inputs) = self.closed(id, flavor, Some(&result), |this| {
            let locals = (0..names.len())
                .map(|index| {
                    format!(
                        "{}big_multi_result_{}_{}",
                        this.prefix,
                        this.source.node(id).span.start,
                        index
                    )
                })
                .collect::<Vec<_>>();
            let mut code = vec![];
            if global {
                let runtime = format!("{}big_context", this.prefix);
                this.modules.insert(runtime.clone(), "base:runtime".into());
                code.push(this.g(format!("context={runtime}.default_context()\n"), id));
                this.logical.globalizing += 1;
            }
            let expression = this.render(values[0], None, false);
            if global {
                this.logical.globalizing -= 1;
            }
            code.extend([
                this.g(format!("{}:=", locals.join(",")), id),
                expression?,
                this.g("\n", id),
            ]);
            for (name, local) in names.iter().zip(&locals) {
                if this.source.text(*name) == "_" {
                    code.push(this.g(format!("{module}.compiler_destroy(&{local})\n"), *name));
                }
            }
            code.push(this.g(
                format!(
                    "return {}{}{}",
                    if global {
                        format!("{}Result{{", this.prefix)
                    } else {
                        String::new()
                    },
                    locals.join(","),
                    if global { "}" } else { "" }
                ),
                id,
            ));
            Ok(Code::concat(code))
        })?;
        let replacement = if global {
            let storage = format!(
                "{}big_globals_{}",
                self.prefix,
                self.source.node(id).span.start
            );
            let fields = types.into_iter().enumerate().flat_map(|(index, typ)| {
                [self.g(format!("value_{index}:"), id), typ, self.g(",", id)]
            });
            inputs.push(Code::concat([
                self.g("struct{", id),
                Code::concat(fields),
                self.g("}", id),
            ]));
            let call = self.helper_call(id, &helper, inputs);
            self.helpers.insert(
                (NodeId(0), self.prefix.clone(), storage.clone()),
                Code::concat([
                    self.g(format!("\n@(private=\"file\")\n{storage}:="), id),
                    call,
                    self.g("\n", id),
                ]),
            );
            self.g(
                (0..names.len())
                    .map(|index| format!("{storage}.value_{index}"))
                    .collect::<Vec<_>>()
                    .join(","),
                id,
            )
        } else {
            inputs.extend(types);
            self.helper_call(id, &helper, inputs)
        };
        self.logical.busy.insert(id);
        let old = self.logical.replacements.insert(values[0], replacement);
        let code = self.render(id, None, false);
        if let Some(old) = old {
            self.logical.replacements.insert(values[0], old);
        } else {
            self.logical.replacements.remove(&values[0]);
        }
        self.logical.busy.remove(&id);
        code.map(Some)
    }
    pub(super) fn big_discard_call(&mut self, id: NodeId) -> Result<Option<Code>> {
        if self.temporary_discarding.contains(&id)
            || !self
                .source
                .node(id)
                .parent
                .is_some_and(|p| self.source.node(p).kind == "ExpressionStatement")
        {
            return Ok(None);
        }
        let callee = self.role(id, "callee")?;
        let mut count = self
            .resolver
            .signature(self.reference(callee))
            .map_or(0, |signature| {
                self.resolver.fields(signature, "results").len()
            });
        if self.big_constructor_type(id).is_some()
            && self.source.group(id, "args").iter().any(|arg| {
                self.typ(self.source.role(*arg, "value").unwrap_or(*arg))
                    .is_string()
            })
        {
            count = 2;
        }
        if count <= 1 || !self.big_numeric_results(id, count) {
            return Ok(None);
        }
        self.temporary_discarding.insert(id);
        let module = self.module("big");
        let built = self.closed(id, "big_discard_call", None, |this| {
            let names = (0..count)
                .map(|index| {
                    format!(
                        "{}big_unused_{}_{}",
                        this.prefix,
                        this.source.node(id).span.start,
                        index
                    )
                })
                .collect::<Vec<_>>();
            let mut code = vec![
                this.g(format!("{}:=", names.join(",")), id),
                this.render(id, None, false)?,
                this.g("\n", id),
            ];
            for name in names {
                code.push(this.g(format!("{module}.compiler_destroy(&{name})\n"), id));
            }
            Ok(Code::concat(code))
        });
        self.temporary_discarding.remove(&id);
        let (name, inputs) = built?;
        Ok(Some(self.helper_call(id, &name, inputs)))
    }
    pub(super) fn big_append(&mut self, id: NodeId) -> Result<Option<Code>> {
        let args = self.source.group(id, "args").to_vec();
        if args.len() < 2 || self.source.role(id, "ellipsis").is_some() {
            return Ok(None);
        }
        let Type::Pointer(inner) = self.typ(args[0]) else {
            return Ok(None);
        };
        let Type::Dynamic(element) = *inner else {
            return Ok(None);
        };
        if element.big_name().is_none() {
            return Ok(None);
        }
        let (name, inputs) = self.closed(id, "big_append", Some("int"), |this| {
            let pointer = format!(
                "{}big_append_{}",
                this.prefix,
                this.source.node(id).span.start
            );
            let mut code = vec![
                this.g(format!("{pointer}:="), id),
                this.render(args[0], None, false)?,
                this.g("\n", id),
            ];
            let mut actual = vec![pointer.clone()];
            for (index, value) in args[1..].iter().enumerate() {
                let name = format!("{pointer}_value_{index}");
                let typ = format!("type_of({pointer}^[0])");
                let hint = Hint {
                    code: this.g(&typ, *value),
                    semantic: Some(*element.clone()),
                    element_only: false,
                    plain_array: false,
                };
                code.extend([
                    this.g(format!("{name}:{typ}="), *value),
                    this.big_owned(*value, Some(hint), false)?,
                    this.g("\n", *value),
                ]);
                actual.push(name);
            }
            code.push(this.g(format!("return append({})", actual.join(",")), id));
            Ok(Code::concat(code))
        })?;
        Ok(Some(self.helper_call(id, &name, inputs)))
    }
    pub(super) fn big_optional(&mut self, id: NodeId) -> Result<Option<Code>> {
        let kind = self.typ(id);
        if kind.big_name().is_none() && !matches!(kind, Type::Generic) {
            return Ok(None);
        }
        let else_ = self.source.node(id).kind == "OrElse";
        let primary = self.unparen(self.role(id, if else_ { "x" } else { "expr" })?);
        let types = if self.source.node(primary).kind == "Call" {
            self.big_result_types(primary, 2)?
        } else {
            vec![self.native_type(primary)?, self.g("bool", id)]
        };
        let module = self.module("big");
        let fresh = self.big_fresh(primary);
        let discarded = self.source.node(id).parent.is_some_and(|parent| {
            let node = self.source.node(parent);
            node.kind == "ExpressionStatement"
                || matches!(node.kind.as_str(), "Assignment" | "Declaration") && {
                    let names = self.source.group(
                        parent,
                        if node.kind == "Assignment" {
                            "lhs"
                        } else {
                            "names"
                        },
                    );
                    names.len() == 1 && self.source.text(names[0]) == "_"
                }
        });
        let (name, mut inputs) = self.closed(
            id,
            if else_ {
                "big_optional_else"
            } else {
                "big_optional_branch"
            },
            Some(if else_ { "" } else { "tuple:2" }),
            |this| {
                let value = format!(
                    "{}big_optional_{}",
                    this.prefix,
                    this.source.node(id).span.start
                );
                let status = format!("{value}_ok");
                let success = format!("{value}_success");
                let mut code = vec![
                    this.g(format!("{value},{status}:="), id),
                    this.render(primary, None, false)?,
                    this.g(
                        format!("\n{success}:={module}.compiler_optional_ok({status})\n"),
                        id,
                    ),
                ];
                if else_ {
                    code.push(this.g(
                        format!("if {success} {{return {module}.compiler_own({value},{fresh})}}\n"),
                        id,
                    ));
                    if fresh {
                        code.push(this.g(format!("{module}.compiler_destroy(&{value})\n"), id));
                    }
                    let fallback = this.role(id, "y")?;
                    if this.diverging(fallback) {
                        code.push(this.render(fallback, None, false)?);
                    } else {
                        let hint = Hint {
                            code: this.g(format!("{}Result", this.prefix), id),
                            semantic: Some(kind.clone()),
                            element_only: false,
                            plain_array: false,
                        };
                        code.extend([
                            this.g("return ", fallback),
                            this.big_owned(fallback, Some(hint), false)?,
                        ]);
                    }
                } else {
                    if discarded {
                        code.push(this.g(
                            if fresh {
                                format!("{module}.compiler_destroy(&{value})\n")
                            } else {
                                format!("{value}=type_of({value}){{}}\n")
                            },
                            id,
                        ));
                    } else if fresh {
                        code.push(this.g(
                            format!("if !{success} {{{module}.compiler_destroy(&{value})}}\n"),
                            id,
                        ));
                    } else {
                        code.push(this.g(
                            format!("if {success} {{{value}={module}.compiler_clone({value})}}\n"),
                            id,
                        ));
                    }
                    code.push(this.g(format!("return {value},{status}"), id));
                }
                Ok(Code::concat(code))
            },
        )?;
        if else_ {
            inputs.push(types[0].clone());
            return Ok(Some(self.helper_call(id, &name, inputs)));
        }
        inputs.extend(types);
        let replacement = self.helper_call(id, &name, inputs);
        self.splice(
            id,
            vec![(self.source.node(primary).span, replacement)],
            vec![],
        )
        .map(Some)
    }
}
