use super::*;
impl Lower<'_, '_> {
    // Infer individual types from a multiple-valued expression without
    // evaluating it or requiring procedure_of to accept a non-call operand.
    pub(super) fn multi_value_types(&mut self, id: NodeId, count: usize) -> Result<Vec<Code>> {
        self.meta = true;
        self.querying += 1;
        let expression = self.render(id, None, false);
        self.querying -= 1;
        let expression = expression?;
        let name = format!("{}result_shape_{}_{}", self.prefix, id.0, count);
        let params = (0..count)
            .map(|i| format!("_:$T{i}"))
            .collect::<Vec<_>>()
            .join(",");
        let fields = (0..count)
            .map(|i| format!("v{i}:T{i}"))
            .collect::<Vec<_>>()
            .join(",");
        let helper = self.g(
            format!(
                "\n{name}::proc \"contextless\"({params})->struct{{{fields}}}{{return {{}}}}\n"
            ),
            id,
        );
        self.helpers
            .entry((self.helper_scope(id), self.prefix.clone(), name.clone()))
            .or_insert(helper);
        let type_of = self.native("type_of");
        Ok((0..count)
            .map(|i| {
                Code::concat([
                    self.g(
                        format!("{}meta.type_field_type({type_of}({name}(", self.prefix),
                        id,
                    ),
                    expression.clone(),
                    self.g(format!(")),\"v{i}\")"), id),
                ])
            })
            .collect())
    }

    pub(super) fn fallback_list(&mut self, id: NodeId, constant: bool) -> Result<Code> {
        let values = self.source.group(id, "args").to_vec();
        if values.len() < 2 {
            return Err(Error::at(
                self.source,
                self.source.node(id).span,
                "An or_else fallback list requires at least two values.",
            ));
        }
        let owner =
            crate::fallbacks::owner(self.resolver, self.reference(id)).ok_or_else(|| {
                Error::at(
                    self.source,
                    self.source.node(id).span,
                    "A fallback list must be the right-hand operand of or_else.",
                )
            })?;
        let left = self.source.role(owner.node, "x").unwrap();
        if let Some(actual) = crate::fallbacks::optional_count(self.resolver, self.reference(left))
            .and_then(|n| n.checked_sub(1))
        {
            if actual != values.len() {
                return Err(Error::at(self.source,self.source.node(id).span,format!("or_else fallback list has {} values; the operation has {actual} successful results (excluding its status).",values.len())));
            }
        }
        let targets = crate::fallbacks::targets(self.resolver, owner, values.len());
        for (i, value) in values.iter().enumerate() {
            let blank = crate::fallbacks::discarded(self.resolver, self.reference(*value));
            let target_blank =
                targets[i].is_some_and(|target| crate::fallbacks::discarded(self.resolver, target));
            if blank != target_blank {
                let message = if blank {
                    format!("Fallback position {} uses '_', but its result has no matching discarded assignment target.",i+1)
                } else {
                    format!("Fallback position {} is discarded; use '_' to match its assignment target.",i+1)
                };
                return Err(Error::at(
                    self.source,
                    self.source.node(*value).span,
                    message,
                ));
            }
        }
        let name = format!("{}fallback_shape_{}", self.prefix, id.0);
        let params = (0..values.len())
            .map(|i| format!("_:$T{i}"))
            .chain(["_:$Status".into()])
            .collect::<Vec<_>>()
            .join(",");
        let fields = (0..values.len())
            .map(|i| format!("v{i}:T{i}"))
            .collect::<Vec<_>>()
            .join(",");
        let helper = self
            .g(
                format!(
                    "\n{name}::proc \"contextless\"({params})->struct{{{fields}}}{{return {{}}}}\n"
                ),
                id,
            )
            .region(
                &self.source.path,
                self.source.node(id).span,
                "fallback-type",
            );
        self.helpers
            .entry((self.helper_scope(id), self.prefix.clone(), name.clone()))
            .or_insert(helper);
        self.querying += 1;
        let witness = self.render(left, None, false);
        self.querying -= 1;
        let witness = witness?;
        let type_of = self.native("type_of");
        let expand = self.native("expand_values");
        let typ = Code::concat([
            self.g(format!("{type_of}({name}("), id),
            witness,
            self.g("))", id),
        ]);
        let mut code = vec![
            self.g(format!("{expand}("), id),
            typ.clone(),
            self.g("{", id),
        ];
        for (i, value) in values.iter().enumerate() {
            if crate::fallbacks::discarded(self.resolver, self.reference(*value)) {
                continue;
            }
            self.meta = true;
            let hint = Hint {
                code: Code::concat([
                    self.g(format!("{}meta.type_field_type(", self.prefix), id),
                    typ.clone(),
                    self.g(format!(",\"v{i}\")"), id),
                ]),
                semantic: Some(self.resolver.result_type(
                    self.reference(left),
                    i,
                    &mut HashSet::new(),
                )),
                constant_literal: false,
                element_only: false,
                plain_array: false,
            };
            code.extend([
                self.g(format!("v{i}="), *value),
                self.render(*value, Some(hint), constant)?,
                self.g(",", *value),
            ]);
        }
        code.push(self.g("})", id));
        Ok(Code::concat(code))
    }
}
