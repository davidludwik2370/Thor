//! Lower result type queries and ordinary generic procedures that use a query
//! as a type-parameter default. The runtime procedure keeps its normal body.
use super::*;
use crate::resulttypes::{self, Results};
use crate::semantics::Substitutions;

impl Lower<'_, '_> {
    fn result_literal(&mut self, callback: Ref) -> Result<Option<Code>> {
        if callback.file != self.file {
            return Ok(None);
        }
        let node = self.unparen(callback.node);
        if self.source.node(node).kind != "Procedure" {
            return Ok(None);
        }
        let checked_callback = self.borrowing.callbacks.contains(&self.reference(node))
            && self.source.node(node).data["pure"] != true;
        if checked_callback {
            self.emitting_callbacks.insert(node);
            self.prepare_pure(self.reference(node), true)?;
        }
        let name = format!("{}result_callback_{}", self.prefix, node.0);
        let body = self.render(node, None, false);
        if checked_callback {
            self.emitting_callbacks.remove(&node);
        }
        let body = body?;
        let declaration = Code::concat([
            self.g(format!("\n{name} :: "), node),
            body,
            self.g("\n", node),
        ]);
        self.helpers.insert(
            (
                self.helper_scope(callback.node),
                self.prefix.clone(),
                name.clone(),
            ),
            declaration,
        );
        Ok(Some(self.g(name, callback.node)))
    }
    fn result_error(&self, id: NodeId, message: &str) -> Error {
        Error::at(self.source, self.source.node(id).span, message)
    }
    fn result_type_code(&mut self, id: NodeId, call: Code, results: &Results) -> Result<Code> {
        if results.fields.is_empty() {
            return Err(self.result_error(
                id,
                "result_type_of: the call has no return values and therefore no result type.",
            ));
        }
        if !resulttypes::unique_names(results) {
            return Err(self.result_error(id,"result_type_of: result field names collide. Name the results explicitly or supply an explicit result_type."));
        }
        self.meta = true;
        let type_of = self.native("type_of");
        let signature = Code::concat([
            self.g(format!("{type_of}({}meta.procedure_of(", self.prefix), id),
            call,
            self.g("))", id),
        ]);
        let mut fields = vec![];
        for (index, (name, _)) in results.fields.iter().enumerate() {
            let typ = Code::concat([
                self.g(format!("{}meta.type_proc_return_type(", self.prefix), id),
                signature.clone(),
                self.g(format!(",{index})"), id),
            ]);
            if results.fields.len() == 1 {
                return Ok(typ);
            }
            fields.extend([self.g(format!("{name}: "), id), typ, self.g(",", id)]);
        }
        Ok(Code::concat([
            self.g("struct {", id),
            Code::concat(fields),
            self.g("}", id),
        ]))
    }

    pub(super) fn result_query(&mut self, id: NodeId) -> Result<Option<Code>> {
        let query = self.reference(id);
        if !resulttypes::is_query(self.resolver, query) {
            return Ok(None);
        }
        let Some(call) = resulttypes::expression(self.resolver, query) else {
            return Err(self.result_error(id,"result_type_of requires exactly one call expression, for example result_type_of(calculate(**Job{}))."));
        };
        let results = resulttypes::resolve(self.resolver, call, &Substitutions::new(), None);
        let Some(results) = results else {
            return Err(self.result_error(id,"result_type_of cannot resolve the callback's result signature from these argument types."));
        };
        let callee = self.resolver.sources[call.file]
            .role(call.node, "callee")
            .map(|node| Ref {
                file: call.file,
                node,
            });
        let replacement = if let Some(callee) = callee {
            self.result_literal(callee)?
        } else {
            None
        };
        let saved = if let (Some(callee), Some(code)) = (callee, replacement) {
            Some((
                callee.node,
                self.switch_replacements.insert(callee.node, code),
            ))
        } else {
            None
        };
        self.querying += 1;
        let owner = &self.resolver.sources[call.file];
        let args = owner.group(call.node, "args");
        let empty_spread = args.len() == 1
            && owner.node(args[0]).kind == "Unary"
            && owner.node(args[0]).op == "**"
            && resulttypes::argument_types(self.resolver, call, &Substitutions::new()).is_empty();
        let call = if empty_spread {
            self.render(owner.role(call.node, "callee").unwrap(), None, false)
                .map(|code| Code::concat([code, self.g("()", id)]))
        } else {
            self.render(call.node, None, false)
        };
        self.querying -= 1;
        if let Some((node, old)) = saved {
            if let Some(old) = old {
                self.switch_replacements.insert(node, old);
            } else {
                self.switch_replacements.remove(&node);
            }
        }
        let code = self.result_type_code(id, call?, &results)?;
        Ok(Some(
            code.expansion(&self.source.path, self.source.node(id).span),
        ))
    }

    pub(super) fn result_query_field(&mut self, id: NodeId) -> Result<Option<Code>> {
        let Some(query) = self.source.role(id, "default") else {
            return Ok(None);
        };
        if !resulttypes::is_query(self.resolver, self.reference(query)) {
            return Ok(None);
        }
        let Some(name) = self.source.group(id, "names").first().copied() else {
            return Ok(None);
        };
        // A dependent type default is supplied at its callers, where the
        // callback's overload/generic specialization and result names are known.
        if !self
            .source
            .ancestors(id)
            .any(|node| self.source.node(node).kind == "ProcedureType")
        {
            return Ok(None);
        }
        let typ = self.source.role(id, "type");
        if !typ.is_some_and(|typ| self.source.node(typ).kind == "TypeidType") {
            return Err(self.result_error(id,"A result_type_of parameter default requires an explicit '$name: typeid' parameter."));
        }
        Ok(Some(Code::concat([
            self.render(name, None, false)?,
            self.g(": typeid", id),
        ])))
    }

    fn query_witness(
        &mut self,
        site: NodeId,
        signature: Ref,
        query: &resulttypes::DefaultQuery,
        parameters: &HashMap<String, Ref>,
        bindings: &Substitutions,
        literal: &Option<Code>,
        mapping: Option<(bool, usize)>,
    ) -> Result<Code> {
        if let Some((spread, arguments)) = mapping {
            let callback = parameters
                .get(&self.resolver.fields(signature, "params")[query.callback].0)
                .copied()
                .unwrap();
            let jobs = parameters.get("jobs").copied().unwrap();
            self.querying += 1;
            let callback_code = if let Some(code) = literal {
                Ok(code.clone())
            } else {
                self.render(callback.node, None, false)
            };
            let jobs_code = self.render(jobs.node, None, false);
            self.querying -= 1;
            let mut code = vec![callback_code?, self.g("(", site)];
            if !spread || arguments > 0 {
                let type_of = self.native("type_of");
                let jobs_code = jobs_code?;
                if spread {
                    // Odin can select a one-argument overload before expanding
                    // `**`. Explicit field witnesses keep overload arity visible.
                    self.meta = true;
                    let typ = self.resolver.type_of(jobs);
                    let fields = self
                        .resolver
                        .record_fields(typ.element().unwrap_or(&Type::Unknown));
                    for (i, (name, _)) in fields.iter().enumerate() {
                        if i > 0 {
                            code.push(self.g(",", site));
                        }
                        code.extend([
                            self.g(
                                format!("{}meta.type_field_type({type_of}((", self.prefix),
                                site,
                            ),
                            jobs_code.clone(),
                            self.g(format!(")[0]),{}){{}}", serde_json::json!(name)), site),
                        ]);
                    }
                } else {
                    code.extend([
                        self.g(format!("{type_of}(("), site),
                        jobs_code,
                        self.g(")[0]){}", site),
                    ]);
                }
            }
            code.push(self.g(")", site));
            return Ok(Code::concat(code));
        }
        let owner = &self.resolver.sources[query.call.file];
        let span = owner.node(query.call.node).span;
        let args = owner.group(query.call.node, "args");
        if args.len() == 1
            && owner.node(args[0]).kind == "Unary"
            && owner.node(args[0]).op == "**"
            && resulttypes::argument_types(self.resolver, query.call, bindings).is_empty()
        {
            if let Some(callback) = parameters
                .get(&self.resolver.fields(signature, "params")[query.callback].0)
                .filter(|arg| arg.file == self.file)
            {
                return Ok(Code::concat([
                    if let Some(code) = literal {
                        code.clone()
                    } else {
                        self.render(callback.node, None, false)?
                    },
                    self.g("()", site),
                ]));
            }
        }
        let params = self.resolver.fields(signature, "params");
        let mut replacements = vec![];
        // Substitute types through native type_of witnesses. No job or argument
        // expression is evaluated by a type query, including empty job slices.
        for (name, annotation) in &params {
            let Some(argument) = parameters
                .get(name)
                .filter(|arg| arg.file == self.file)
                .copied()
            else {
                continue;
            };
            if let Some(annotation) = annotation {
                let source = &self.resolver.sources[annotation.file];
                let kind = source.node(annotation.node).kind.as_str();
                if kind == "ArrayType" && source.role(annotation.node, "len").is_none() {
                    if let Some(elem) = source
                        .role(annotation.node, "elem")
                        .filter(|node| source.node(*node).kind == "PolyType")
                    {
                        let generic = source.text(elem).trim_start_matches('$').to_string();
                        let typeof_ = self.native("type_of");
                        let actual = self.render(argument.node, None, false)?;
                        let code = Code::concat([
                            self.g(format!("{typeof_}(("), site),
                            actual,
                            self.g(")[0])", site),
                        ]);
                        for node in owner.descendants(query.call.node) {
                            if owner.node(node).kind == "Ident" && owner.text(node) == generic {
                                replacements.push((owner.node(node).span, code.clone()));
                            }
                        }
                    }
                }
            }
            for node in owner.descendants(query.call.node) {
                if owner.node(node).kind == "Ident" && owner.text(node) == name {
                    replacements.push((
                        owner.node(node).span,
                        if name == &self.resolver.fields(signature, "params")[query.callback].0
                            && literal.is_some()
                        {
                            literal.clone().unwrap()
                        } else {
                            self.render(argument.node, None, false)?
                        },
                    ));
                }
            }
        }
        replacements.sort_by_key(|(span, _)| span.start);
        let mut cursor = span.start;
        let mut parts = vec![];
        for (at, code) in replacements {
            if at.start < cursor {
                continue;
            }
            parts.push(self.g(owner.slice(Span::new(cursor, at.start)), site));
            parts.push(code);
            cursor = at.end;
        }
        parts.push(self.g(owner.slice(Span::new(cursor, span.end)), site));
        Ok(Code::concat(parts))
    }

    pub(super) fn result_default_call(
        &mut self,
        id: NodeId,
        constant: bool,
    ) -> Result<Option<Code>> {
        let callee = self.role(id, "callee")?;
        let candidates = self.resolver.candidates(self.reference(callee));
        for candidate in candidates {
            let owner = &self.resolver.sources[candidate.file];
            let signature = if owner.node(candidate.node).kind == "Procedure" {
                Ref {
                    file: candidate.file,
                    node: owner.role(candidate.node, "type").unwrap_or(candidate.node),
                }
            } else {
                candidate
            };
            let queries = resulttypes::default_queries(self.resolver, signature);
            let Some(query) = queries.first() else {
                continue;
            };
            let Some((bindings, callback, parameters)) =
                resulttypes::bind_default(self.resolver, self.reference(id), signature, query)
            else {
                continue;
            };
            let (resolved, ambiguous) = resulttypes::resolve_default(
                self.resolver,
                self.reference(id),
                query.call,
                &bindings,
                callback,
            );
            let Some(results) = resolved else {
                let message = if ambiguous {
                    "Ambiguous pmap callback: both whole-element and argument-bundle overloads match. Select a specific callback."
                } else if !crate::parallelcompile::is_call(self.resolver, self.reference(id)) {
                    "Cannot resolve the mapping callback from the job field types. Job fields must match callback parameters in order."
                } else {
                    "Cannot resolve the mapping callback: a single-parameter callback receives the whole slice element; multiple or variadic parameters require matching struct job fields in order."
                };
                return Err(self.result_error(id, message));
            };
            let literal = self.result_literal(callback)?;
            let witness = self.query_witness(
                id,
                signature,
                query,
                &parameters,
                &bindings,
                &literal,
                results
                    .spread
                    .map(|spread| (spread, results.arguments.len())),
            )?;
            let args = self.source.group(id, "args").to_vec();
            let names = self.resolver.fields(signature, "params");
            // The action overload omits the dependent result-type parameter.
            // Its third positional argument is workers, not result_type.
            let action = results.fields.is_empty()
                && crate::parallelcompile::is_call(self.resolver, self.reference(id));
            let query_position = names
                .iter()
                .position(|(name, _)| name == &query.name)
                .unwrap_or(names.len());
            let supplied = args.iter().enumerate().any(|(index, arg)| {
                if self.source.node(*arg).kind == "FieldValue" {
                    self.source
                        .role(*arg, "field")
                        .is_some_and(|field| self.source.text(field) == query.name)
                } else if !action {
                    names
                        .get(index)
                        .is_some_and(|(name, _)| name == &query.name)
                } else {
                    false
                }
            });
            if results.fields.is_empty() && supplied {
                return Err(self.result_error(
                    id,
                    "The callback has no return values; result_type cannot be supplied.",
                ));
            }
            if supplied && !results.fields.is_empty() {
                if let Some(argument) = parameters.get(&query.name) {
                    let explicit = self.resolver.type_of(*argument);
                    let fields = if results.fields.len() == 1 {
                        vec![(String::new(), explicit)]
                    } else {
                        self.resolver.record_fields(&explicit)
                    };
                    if fields.len() != results.fields.len() {
                        return Err(self.result_error(id,"result_type must match the callback's return count and field types, in order. Only field names may be overridden."));
                    }
                    if fields
                        .iter()
                        .zip(&results.fields)
                        .any(|((_, actual), (_, expected))| {
                            resulttypes::concrete(actual)
                                && resulttypes::concrete(expected)
                                && actual != expected
                        })
                    {
                        return Err(self.result_error(id,"result_type must match the callback's return count and field types, in order. Only field names may be overridden."));
                    }
                }
            }
            let callback_name = &names[query.callback].0;
            let callback_candidates = self.resolver.candidates(callback);
            let specialize = self.checked_targets.contains_key(&id)
                && crate::parallelcompile::is_call(self.resolver, self.reference(id))
                || callback_candidates.len() > 1
                || self.resolver.sources[results.signature.file]
                    .descendants(results.signature.node)
                    .any(|node| {
                        self.resolver.sources[results.signature.file]
                            .node(node)
                            .kind
                            == "PolyType"
                    })
                || callback_candidates.iter().any(|target| {
                    let source = &self.resolver.sources[target.file];
                    source
                        .descendants(target.node)
                        .any(|node| source.node(node).kind == "PolyType")
                });
            // Capture runtime arguments in their original order before arranging
            // the native call. Odin requires type parameters positionally; named
            // type arguments are otherwise ambiguous even on a direct call.
            let helper = format!("{}result_call_{}", self.prefix, id.0);
            let result_name = format!("{}Result", self.prefix);
            let mut helper_params = vec![];
            let mut call_args = vec![];
            let mut arranged = HashMap::new();
            let mut bound_values = HashMap::new();
            let mut explicit_type = None;
            let owner = &self.resolver.sources[signature.file];
            let constant_parameters = owner
                .role(signature.node, "params")
                .into_iter()
                .flat_map(|list| &owner.node(list).children)
                .flat_map(|field| owner.group(*field, "names"))
                .filter(|name| owner.node(**name).kind == "PolyType")
                .map(|name| owner.text(*name).trim_start_matches('$').to_string())
                .collect::<HashSet<_>>();
            for (index, arg) in args.iter().enumerate() {
                let named = self.source.node(*arg).kind == "FieldValue";
                let name = if named {
                    self.source
                        .role(*arg, "field")
                        .map(|field| self.source.text(field).to_string())
                        .unwrap_or_default()
                } else {
                    names
                        .get(index + usize::from(action && index >= query_position))
                        .map(|(name, _)| name.clone())
                        .unwrap_or_default()
                };
                let value = if named {
                    self.role(*arg, "value")?
                } else {
                    *arg
                };
                if name == "__thor_pmap_spread" && results.spread.is_some() {
                    return Err(self.result_error(
                        *arg,
                        "pmap's argument mode is inferred and cannot be supplied explicitly.",
                    ));
                }
                if name == query.name {
                    explicit_type = Some(self.render(value, None, true)?);
                    continue;
                }
                let argument = format!("{}arg_{}", self.prefix, helper_params.len());
                if name == *callback_name && specialize {
                    self.meta = true;
                    let callback_type = format!("{}Callback_{}", self.prefix, helper_params.len());
                    let signature = Code::concat([
                        self.g(format!("type_of({}meta.procedure_of(", self.prefix), id),
                        witness.clone(),
                        self.g("))", id),
                    ]);
                    helper_params.push(format!("${callback_type}: typeid"));
                    call_args.push(signature.clone());
                    helper_params.push(format!("{argument}: {callback_type}"));
                    bound_values.insert(
                        name.clone(),
                        Code::concat([self.g("cast(", id), signature, self.g(")nil", id)]),
                    );
                } else if constant_parameters.contains(&name) {
                    helper_params.push(format!(
                        "${argument}: ${}Arg_{}",
                        self.prefix,
                        helper_params.len()
                    ));
                    bound_values.insert(name.clone(), self.render(value, None, true)?);
                } else {
                    helper_params.push(format!(
                        "{argument}: ${}Arg_{}",
                        self.prefix,
                        helper_params.len()
                    ));
                }
                arranged.insert(name.clone(), argument);
                let checked_callback = if literal.is_none()
                    && name == *callback_name
                    && crate::parallelcompile::is_call(self.resolver, self.reference(id))
                {
                    self.checked_targets
                        .get(&id)
                        .and_then(|targets| targets.first())
                        .copied()
                        .filter(|target| {
                            self.resolver.sources[target.file].node(target.node).data["pure"]
                                != true
                        })
                } else {
                    None
                };
                call_args.push(if let Some(target) = checked_callback {
                    self.checked_callback_access(target, value)?
                } else if name == *callback_name && literal.is_some() {
                    literal.clone().unwrap()
                } else {
                    self.render(value, None, constant)?
                });
            }
            let mut native_args = vec![];
            let type_position = names
                .iter()
                .position(|(name, _)| name == &query.name)
                .unwrap_or(names.len());
            for (index, (name, _)) in names.iter().enumerate() {
                if name == "__thor_pmap_spread" && results.spread.is_some() {
                    continue;
                }
                if name == &query.name {
                    if !results.fields.is_empty() {
                        native_args.push(self.g(&result_name, id));
                    }
                } else if let Some(argument) = arranged.get(name) {
                    native_args.push(self.g(
                        if index < type_position {
                            argument.clone()
                        } else {
                            format!("{name}={argument}")
                        },
                        id,
                    ));
                }
            }
            if let Some(spread) = results.spread {
                native_args.push(self.g(format!("__thor_pmap_spread={spread}"), id));
            }
            if !results.fields.is_empty() {
                let typ = if let Some(explicit) = explicit_type {
                    explicit
                } else {
                    self.result_type_code(id, witness, &results)?
                };
                helper_params.insert(0, format!("${result_name}: typeid"));
                call_args.insert(0, typ);
            }
            let target = if self.checked_targets.contains_key(&id)
                && crate::parallelcompile::is_call(self.resolver, self.reference(id))
            {
                let module = format!("{}checked_parallel", self.prefix);
                self.modules.insert(module.clone(), "thor:parallel".into());
                self.g(format!("{module}.pmap_checked"), id)
            } else {
                self.render(callee, None, constant)?
            };
            let mut native = vec![target, self.g("(", id)];
            for (index, arg) in native_args.into_iter().enumerate() {
                if index > 0 {
                    native.push(self.g(",", id));
                }
                native.push(arg);
            }
            for (_, code) in self.caller_arguments_for_signature(id, signature)? {
                native.push(code);
            }
            native.push(self.g(")", id));
            let native_call = Code::concat(native);
            let mut checks = vec![];
            if self.checked_targets.contains_key(&id)
                && crate::parallelcompile::is_call(self.resolver, self.reference(id))
            {
                for (_, permission) in self
                    .borrowing
                    .profiles
                    .get(&self.reference(id))
                    .cloned()
                    .unwrap_or_default()
                {
                    let query = self.permission_query(&permission, id)?;
                    let error = self
                        .error_token(
                            id,
                            "Pure pmap cannot mutate or delete job storage borrowed from an input.",
                        )
                        .finish()
                        .text;
                    checks.push(self.g(
                        format!("when {query} {{#panic({})}}\n", serde_json::json!(error)),
                        id,
                    ));
                }
            }
            let return_count = if results.fields.is_empty() {
                0
            } else {
                self.resolver.fields(signature, "results").len()
            };
            let mut returns = vec![];
            if return_count > 0 {
                self.meta = true;
                // Query the fully bound call at the original site. Doing this
                // in a generic helper's result annotation would expose the
                // callee's unbound native polymorphic parameters to Odin.
                let mut bound_args = vec![];
                for (index, (name, _)) in names.iter().enumerate() {
                    if name == "__thor_pmap_spread" && results.spread.is_some() {
                        continue;
                    }
                    let code = if name == &query.name {
                        Some(call_args[0].clone())
                    } else if let Some(value) = bound_values.get(name) {
                        Some(value.clone())
                    } else if let Some(argument) = arranged.get(name) {
                        let at = helper_params
                            .iter()
                            .position(|param| param.starts_with(&format!("{argument}:")))
                            .unwrap();
                        Some(call_args[at].clone())
                    } else {
                        None
                    };
                    if let Some(code) = code {
                        if !bound_args.is_empty() {
                            bound_args.push(self.g(",", id));
                        }
                        if index > type_position {
                            bound_args.push(self.g(format!("{name}="), id));
                        }
                        bound_args.push(code);
                    }
                }
                if let Some(spread) = results.spread {
                    if !bound_args.is_empty() {
                        bound_args.push(self.g(",", id));
                    }
                    bound_args.push(self.g(format!("__thor_pmap_spread={spread}"), id));
                }
                let bound_call = Code::concat([
                    self.render(callee, None, constant)?,
                    self.g("(", id),
                    Code::concat(bound_args),
                    self.g(")", id),
                ]);
                returns.push(self.g(" -> (", id));
                for index in 0..return_count {
                    if index > 0 {
                        returns.push(self.g(",", id));
                    }
                    let output = format!("{}Output_{index}", self.prefix);
                    helper_params.push(format!("${output}: typeid"));
                    call_args.push(Code::concat([
                        self.g(
                            format!(
                                "{}meta.type_proc_return_type(type_of({}meta.procedure_of(",
                                self.prefix, self.prefix
                            ),
                            id,
                        ),
                        bound_call.clone(),
                        self.g(format!(")),{index})"), id),
                    ]));
                    returns.push(self.g(output, id));
                }
                returns.push(self.g(")", id));
            }
            let declaration = Code::concat([
                self.g(
                    format!("\n{helper} :: proc({})", helper_params.join(",")),
                    id,
                ),
                Code::concat(returns),
                self.g(" {", id),
                Code::concat(checks),
                self.g(if return_count == 0 { "" } else { "return " }, id),
                native_call,
                self.g("}\n", id),
            ]);
            self.helpers.insert(
                (self.helper_scope(id), self.prefix.clone(), helper.clone()),
                declaration,
            );
            return Ok(Some(self.helper_call(id, &helper, call_args)));
        }
        Ok(None)
    }
}
