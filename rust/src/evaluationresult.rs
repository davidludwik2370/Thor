//! Contents-only reconstruction retains native result-type checking. Runtime
//! owned values are materialized independently; constants never own buffers.
use super::*;
impl Lower<'_, '_> {
    pub(crate) fn apply_type_probes(
        &mut self,
        probes: BTreeMap<NodeId, Vec<String>>,
        imports: BTreeMap<String, String>,
    ) {
        self.modules.extend(imports);
        for (scope, codes) in probes {
            self.meta = true;
            for (index, code) in codes.into_iter().enumerate() {
                let name = format!("{}type_probe_{index}", self.prefix);
                self.helpers.insert(
                    (scope, self.prefix.clone(), name),
                    self.g(format!("\n{code}\n"), scope),
                );
            }
        }
    }

    pub fn apply_specialized_results(
        &mut self,
        cases: BTreeMap<NodeId, BTreeMap<String, crate::specialization::EvaluatedCase>>,
        scheduled: bool,
    ) {
        self.specialized.extend(cases);
        self.evaluation_scheduled = scheduled;
    }
    pub(super) fn generic_evaluation(&self, id: NodeId) -> bool {
        self.source.ancestors(id).any(|owner| {
            self.source.node(owner).kind == "Procedure"
                && !crate::specialization::parameters(self.reference(owner), self.resolver)
                    .is_empty()
        })
    }
    pub(super) fn unbound_evaluation(&mut self, id: NodeId) -> Code {
        self.error_token(id, "Compile-time evaluation requires resolved generic types and constant parameters at this specialization.")
    }
    pub(super) fn specialized_result(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        constant: bool,
    ) -> Result<Code> {
        let cases = self.specialized.get(&id).cloned().unwrap_or_default();
        let mut code = if self.source.node(id).kind == "Run" || constant {
            self.unbound_evaluation(id)
        } else {
            self.render(id, expected, constant)?
        };
        for case in cases.values().rev() {
            let mut context =
                crate::specialization::TypeCode::new(self.file, Some(self.reference(id)));
            let predicate = crate::specialization::Specializations::predicate(
                &case.bindings,
                &mut context,
                self.resolver,
            )
            .map_err(|error| {
                Error::at(self.source, self.source.node(id).span, error.to_string())
            })?;
            for (alias, directory) in context.imports {
                self.modules.insert(
                    alias,
                    crate::evaluation::dependency_path(self.source, &directory),
                );
            }
            self.meta = true;
            let value = if let Some(unchecked) = case.skipped {
                self.evaluation_skipped(id, unchecked)?
            } else {
                self.evaluation_result(id, &case.value, constant)?
            };
            code = Code::concat([
                self.g("(", id),
                value,
                self.g(format!(" when ({predicate}) else "), id),
                code,
                self.g(")", id),
            ]);
        }
        Ok(code)
    }

    pub fn apply_evaluation_skips(&mut self, skips: BTreeMap<NodeId, bool>) {
        self.evaluation_skips.extend(skips);
    }
    pub(super) fn evaluation_skipped(&mut self, id: NodeId, unchecked: bool) -> Result<Code> {
        if unchecked {
            return Ok(self.g("0", id));
        }
        let call = if self.source.node(id).kind == "Run" {
            self.role(id, "expr")?
        } else {
            id
        };
        let typ = self.evaluation_result_type(call)?;
        Ok(Code::concat([self.g("(", id), typ, self.g("{})", id)]))
    }
    pub fn apply_evaluation_results(&mut self, results: BTreeMap<NodeId, serde_json::Value>) {
        self.evaluated.extend(results);
    }
    pub fn apply_evaluation_witnesses(&mut self, witnesses: BTreeMap<NodeId, String>) {
        self.evaluation_witnesses.extend(witnesses);
    }
    pub(super) fn evaluation_result(
        &mut self,
        id: NodeId,
        value: &serde_json::Value,
        constant: bool,
    ) -> Result<Code> {
        let call = if self.source.node(id).kind == "Run" {
            self.role(id, "expr")?
        } else {
            id
        };
        let untyped = self.source.node(id).kind == "Binary"
            && self.source.node(id).op == "**"
            && self.typ(id).untyped();
        let mut typ = if untyped && value["kind"] == "integer" {
            self.g("", id)
        } else {
            self.evaluation_result_type(call)?
        };
        self.meta = true;
        let meta = format!("{}meta", self.prefix);
        let mut value = value.clone();
        if self.source.node(id).kind == "Comprehension" {
            let count = value["values"]
                .as_array()
                .ok_or_else(|| {
                    Error::at(
                        self.source,
                        self.source.node(id).span,
                        "Compile-time comprehension did not produce a sequence.",
                    )
                })?
                .len();
            if crate::values::has_owned(&value, true)
                && value["values"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|value| crate::values::has_owned(value, true))
            {
                return Err(Error::at(self.source,self.source.node(id).span,"A compile-time comprehension constant cannot contain owned slices or dynamic arrays; flatten them or use a runtime binding."));
            }
            typ = Code::concat([
                self.g(format!("[{count}]{meta}.type_elem_type("), id),
                typ,
                self.g(")", id),
            ]);
            value["storage"] = json!("array");
            value["nil"] = json!(false);
        }
        if constant && crate::bigvalues::contains(&value) && !matches!(value["kind"].as_str(),Some("big_integer"|"big_rational")) {
            return Ok(self.error_token(id,if self.source.node(id).kind=="Comprehension"{"An Integer/Rational-containing comprehension needs a runtime binding; :: supports individual logical-number constants."}else{"An aggregate containing Integer/Rational results needs a runtime binding (:=); use :: for individual numeric constants."}));
        }
        let value = &value;
        let type_code = if untyped {
            String::new()
        } else {
            typ.finish().text
        };
        let mut literal = crate::values::expression_with_big(value, &type_code, &meta, &mut |packet,_|self.big_constant_view(id,packet).map(|code|code.finish().text).map_err(|error|crate::values::ProtocolError(error.to_string()))).map_err(|error| {
            Error::at(
                self.source,
                self.source.node(id).span,
                format!("Invalid compile-time result: {error}"),
            )
        })?;
        if untyped {
            let check = if value["kind"] == "integer" {
                let left = self.role(id, "left")?;
                let right = self.role(id, "right")?;
                let left = self.render(left, None, false)?.finish().text;
                let right = self.render(right, None, false)?.finish().text;
                let common = format!("(({left})+({right}))");
                self.default = true;
                let type_of = self.native("type_of");
                format!(
                    "{type_of}({}default_value({common}=={common}))==bool",
                    self.prefix
                )
            } else {
                let typ = typ.finish().text;
                format!("{typ}=={typ}")
            };
            literal = format!("({literal} when ({check}) else {literal})");
        }
        if !crate::values::has_owned(value, false) || constant {
            if constant
                && (crate::values::has_owned(value, true) || crate::values::has_dynamic(value))
            {
                let label=if self.source.node(id).kind=="Run"{"#run"}else{"fn"};
                let container=if crate::values::has_dynamic(value){"dynamic-array"}else{"slice"};
                return Ok(self.error_token(id,&format!("A {container}-containing {label} result needs a runtime binding (:=); use a pure consumer to compute a scalar constant.")));
            }
            return Ok(self.g(literal, id));
        }
        let module = self.module("pure");
        let prefix = format!(
            "{}materialized_{}_",
            self.prefix,
            self.source.node(id).span.start
        );
        let result = format!("{}Result", self.prefix);
        let body = crate::values::materialize_with_big(value, &result, &meta, &module, &prefix, &mut |packet,_|self.big_constant_view(id,packet).map(|code|code.finish().text).map_err(|error|crate::values::ProtocolError(error.to_string()))).map_err(
            |error| Error::at(self.source, self.source.node(id).span, error.to_string()),
        )?;
        let flavor = format!(
            "run_value_{}",
            &crate::digest::sha256(body.as_bytes())[..12]
        );
        let (name, mut arguments) =
            self.closed(id, &flavor, Some(""), |this| Ok(this.g(body, id)))?;
        arguments.push(typ);
        Ok(self.helper_call(id, &name, arguments))
    }
    fn evaluation_result_type(&mut self, call: NodeId) -> Result<Code> {
        if let Some(typ)=self.big_constructor_type(call){let module=self.module("big");return Ok(self.g(format!("{module}.{}",typ.big_name().unwrap()),call));}
        let witness = self.evaluation_witnesses.get(&call).cloned();
        if self.source.node(call).kind == "Comprehension" {
            return self.comp_target(call, None);
        }
        if let Some(plan) = crate::extremaplan::plan(self.resolver, self.reference(call)) {
            if let Some((_, witness)) = plan
                .parts
                .iter()
                .find(|(kind, _)| *kind != crate::extremaplan::Part::Scalar)
            {
                let sequence = self.module("sequence");
                let query = self.query_value(*witness)?;
                let type_of = self.native("type_of");
                return Ok(Code::concat([
                    self.g(format!("{type_of}({sequence}.element("), *witness),
                    query,
                    self.g("))", *witness),
                ]));
            }
        }
        let owner = self
            .source
            .ancestors(call)
            .take_while(|id| self.source.node(*id).kind != "Procedure")
            .find(|id| {
                self.source.node(*id).kind == "Declaration"
                    && !self.source.node(*id).mutable
                    && !self.source.node(*id).immutable
            })
            .filter(|id| {
                self.source.node(*id).parent.is_some_and(|parent| {
                    ["Block", "Case"].contains(&self.source.node(parent).kind.as_str())
                })
            });
        let mut saved = vec![];
        if let Some(owner) = owner.filter(|_| !self.evaluation_mode && witness.is_none()) {
            let graph = crate::evaluation::Extraction::build(
                self.resolver,
                &[Ref {
                    file: self.file,
                    node: call,
                }],
            )?;
            for ((file, alias), directory) in graph.helper_imports {
                if file == self.file {
                    self.modules.insert(
                        alias,
                        crate::evaluation::dependency_path(self.source, &directory),
                    );
                }
            }
            for (query, code) in graph.queries {
                if query.file != self.file || !self.source.is_descendant(query.node, call) {
                    continue;
                }
                let local = self.source.descendants(query.node).any(|node| {
                    self.resolver
                        .symbol(Ref {
                            file: self.file,
                            node,
                        })
                        .is_some_and(|index| self.resolver.symbols[index].scope != NodeId(0))
                });
                if !local {
                    continue;
                }
                if self.evaluation_queries.insert(query.node) {
                    self.querying += 1;
                    let original = self.render(query.node, None, false);
                    self.querying -= 1;
                    let type_of = self.native("type_of");
                    self.default = true;
                    let check = Code::concat([
                        self.g(
                            format!("\n_ = {type_of}({}default_value(", self.prefix),
                            query.node,
                        ),
                        original?,
                        self.g("))\n", query.node),
                    ]);
                    self.declaration_helpers
                        .entry(owner)
                        .or_default()
                        .push(check);
                }
                let previous = self
                    .switch_replacements
                    .insert(query.node, self.g(code, query.node));
                saved.push((query.node, previous));
            }
        }
        self.querying += 1;
        let expression = self.render(call, None, false);
        self.querying -= 1;
        for (query, previous) in saved {
            if let Some(previous) = previous {
                self.switch_replacements.insert(query, previous);
            } else {
                self.switch_replacements.remove(&query);
            }
        }
        let expression = expression?;
        let type_of = self.native("type_of");
        let plan = if self.source.node(call).kind == "Call" {
            self.resolver
                .call_plans(self.reference(call), &Default::default())
        } else {
            vec![]
        };
        if witness.as_deref() == Some("procedure") || witness.is_none() && plan.len() == 1 {
            self.meta = true;
            Ok(Code::concat([
                self.g(
                    format!(
                        "{}meta.type_proc_return_type({type_of}({}meta.procedure_of(",
                        self.prefix, self.prefix
                    ),
                    call,
                ),
                expression,
                self.g(")),0)", call),
            ]))
        } else {
            Ok(Code::concat([
                self.g(format!("{type_of}("), call),
                expression,
                self.g(")", call),
            ]))
        }
    }
}
