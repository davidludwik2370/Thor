//! Shared call-site specialization. Analyses inspect only viable overloads;
//! unresolved ties remain ties so Odin retains authority over ambiguity.
use super::*;

#[derive(Clone, Debug)]
pub struct CallPlan {
    pub procedure: Ref,
    pub signature: Ref,
    pub arguments: Vec<Vec<Ref>>,
    pub bindings: Substitutions,
}
pub(crate) type ClosedCalls = HashMap<Ref, HashMap<Substitutions, Vec<CallPlan>>>;

impl Resolver<'_> {
    pub(crate) fn take_closed_calls(&mut self) -> ClosedCalls {
        self.metadata.as_mut().map(|metadata| std::mem::take(&mut metadata.calls)).unwrap_or_default()
    }
    pub(crate) fn adopt_closed_calls(&mut self, calls: ClosedCalls) {
        if let Some(metadata) = &mut self.metadata {
            for (site, contexts) in calls {
                metadata.calls.entry(site).or_default().extend(contexts);
            }
        }
    }
    fn spread_types(&mut self, arg: Ref, context: &Substitutions) -> Option<Vec<Type>> {
        let source = &self.sources[arg.file];
        if source.node(arg.node).kind != "Unary" || source.node(arg.node).op != "**" {
            return None;
        }
        let node = source.role(arg.node, "expr")?;
        let typ = self.specialized_type(
            Ref {
                file: arg.file,
                node,
            },
            context,
        );
        let mut base = &typ;
        while let Type::Nominal(_, inner) = base {
            base = inner;
        }
        if let Type::Array { elem, .. } = base {
            return Some(vec![*elem.clone()]);
        }
        let fields = self.record_fields(&typ);
        Some(if fields.is_empty() {
            vec![Type::Unknown]
        } else {
            fields.into_iter().map(|(_, typ)| typ).collect()
        })
    }
    fn packed_variadic(&self, call: Ref, arg: Ref) -> bool {
        let source = &self.sources[call.file];
        source
            .node(call.node)
            .spans
            .get("ellipsis")
            .is_some_and(|span| self.sources[arg.file].node(arg.node).span.start >= span.end)
            || source.group(call.node, "args").iter().any(|id| {
                source.node(*id).kind == "FieldValue" && source.role(*id, "value") == Some(arg.node)
            })
    }
    fn contextual_cast_argument(&self, mut actual: Ref) -> bool {
        while self.sources[actual.file].node(actual.node).kind == "Paren" {
            let Some(node) = self.sources[actual.file].role(actual.node, "expr") else {
                break;
            };
            actual = Ref {
                file: actual.file,
                node,
            };
        }
        self.sources[actual.file].node(actual.node).kind == "AutoCast"
    }
    pub fn call_plans(&mut self, call: Ref, context: &Substitutions) -> Vec<CallPlan> {
        let _phase = crate::profile::span("resolver.call_plans");
        if let Some(plans) = self.metadata.as_ref().and_then(|m|m.calls.get(&call)?.get(context))
            .or_else(||self.base_metadata.as_ref()?.calls.get(&call)?.get(context)) {
            crate::profile::count("resolver.call_plan_hits", 1);
            return plans.clone();
        }
        let tracking = self.track_import_demands.get();
        let local_key = (context.clone(), tracking);
        if let Some(plans) = self.metadata.as_ref().and_then(|m|m.local_calls.get(&call)?.get(&local_key))
            .or_else(||self.base_metadata.as_ref()?.local_calls.get(&call)?.get(&local_key)) {
            crate::profile::count("resolver.local_call_plan_hits", 1);
            return plans.clone();
        }
        let epoch = self.incomplete_plans.get();
        let key = (
            call,
            context
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
        );
        if !self.planning.insert(key.clone()) {
            self.incomplete_plans.set(self.incomplete_plans.get().wrapping_add(1));
            return vec![];
        }
        let plans = self.plan_calls(call, context);
        // A nested query can also finish independently of its caller. The
        // recursion epoch records any curtailed type/condition/call query;
        // requiring an empty inference stack discarded most complete plans.
        // Retain only a unique plan with fully known inputs/bindings. Partial,
        // ambiguous, recursively curtailed and unloaded-import plans take the
        // existing planner path on their next use.
        if plans.len() == 1 && !self.has_demanded_packages()
            && context.values().all(Type::resolved)
            && plans[0].bindings.values().all(Type::resolved)
            && plans[0].arguments.iter().flatten()
                .all(|arg|self.specialized_type(*arg,context).resolved())
            && epoch == self.incomplete_plans.get() && !self.has_demanded_packages() {
            if let Some(memo) = &mut self.metadata {
                memo.calls.entry(call).or_default().insert(context.clone(),plans.clone());
                crate::profile::count("resolver.call_plans_cached", 1);
            }
        } else if epoch == self.incomplete_plans.get() && !self.has_demanded_packages() {
            // Generic/ambiguous/empty answers are still deterministic for the
            // exact substitutions and fixed source graph. Never move them to
            // discovery's append-only cache; new declarations can refine them.
            // Include demand tracking because a miss must still record imports
            // when the permission-discovery pass enables that observation.
            if let Some(memo) = &mut self.metadata {
                if memo.local_call_count < 65_536 {
                    let previous = memo.local_calls.entry(call).or_default().insert(local_key, plans.clone());
                    if previous.is_none() { memo.local_call_count += 1; }
                    crate::profile::count("resolver.local_call_plans_cached", 1);
                }
            }
        }
        // Keep the recursion guard while proving the argument types complete.
        // That proof can revisit this call through cyclic constant arguments.
        self.planning.remove(&key);
        plans
    }

    /// Follow call-site overload selection before falling back to a declared
    /// procedure value. Ambiguous native calls retain Odin's own diagnostics.
    pub fn call_signature(&mut self, call: Ref) -> Option<Ref> {
        let plans = self.call_plans(call, &Substitutions::new());
        if plans.len() == 1 {
            return Some(plans[0].signature);
        }
        let callee = self.sources[call.file].role(call.node, "callee")?;
        self.signature(Ref {
            file: call.file,
            node: callee,
        })
    }

    fn plan_calls(&mut self, call: Ref, context: &Substitutions) -> Vec<CallPlan> {
        self.plan_target_calls(call, context, None)
    }

    /// Effect analysis can resolve a runtime procedure value to a known body.
    /// Use the ordinary argument arrangement and specialization rules for it.
    pub(crate) fn call_plans_for_targets(&mut self, call: Ref, context: &Substitutions, targets: &[Ref]) -> Vec<CallPlan> {
        self.plan_target_calls(call, context, Some(targets))
    }

    fn plan_target_calls(&mut self, call: Ref, context: &Substitutions, targets: Option<&[Ref]>) -> Vec<CallPlan> {
        let source = &self.sources[call.file];
        let Some(callee) = source.role(call.node, "callee") else {
            return vec![];
        };
        let args = source
            .group(call.node, "args")
            .iter()
            .map(|node| Ref {
                file: call.file,
                node: *node,
            })
            .collect::<Vec<_>>();
        let supplied = args
            .iter()
            .map(|arg| Ref {
                file: arg.file,
                node: source.role(arg.node, "value").unwrap_or(arg.node),
            })
            .collect::<HashSet<_>>();
        let argument_signature=if targets.is_some() {
            let mut typ=self.specialized_type(Ref{file:call.file,node:callee},context);
            while let Type::Nominal(_,inner)=typ {typ=*inner;}
            let Some(signature)=typ.signature() else{return vec![];};
            Some(signature)
        }else{None};
        let mut plans = vec![];
        let candidates = targets.map(|targets| targets.to_vec()).unwrap_or_else(|| self.candidates(Ref {
            file: call.file,
            node: callee,
        }));
        for procedure in candidates {
            let owner = &self.sources[procedure.file];
            let signature = if owner.node(procedure.node).kind == "Procedure" {
                owner
                    .role(procedure.node, "type")
                    .map(|node| Ref {
                        file: procedure.file,
                        node,
                    })
                    .unwrap_or(procedure)
            } else {
                procedure
            };
            // Defaults and named arguments belong to the callable's declared
            // type; parameter effects belong to the selected implementation.
            let Some(arguments) = self.arrange_arguments(argument_signature.unwrap_or(signature), &args) else {
                continue;
            };
            let layout = self.signature_layout(signature);
            let fields = &layout.params;
            let types = arguments
                .iter()
                .zip(fields)
                .map(|(group, (_, annotation))| {
                    group
                        .first()
                        .map(|arg| {
                            if !annotation.is_some_and(|annotation| {
                                self.sources[annotation.file].node(annotation.node).kind
                                    == "TypeidType"
                            }) && self.denotes_type(*arg)
                            {
                                Type::Scalar {
                                    name: "typeid".into(),
                                    untyped: false,
                                }
                            } else {
                                let typ = self.specialized_type(*arg, context);
                                if annotation.is_some_and(|annotation| {
                                    self.sources[annotation.file].node(annotation.node).kind
                                        == "Ellipsis"
                                }) {
                                    if let Some(types) = self.spread_types(*arg, context) {
                                        Type::Slice(Box::new(
                                            types.into_iter().next().unwrap_or(Type::Unknown),
                                        ))
                                    } else if self.packed_variadic(call, *arg) {
                                        typ
                                    } else {
                                        Type::Slice(Box::new(typ))
                                    }
                                } else {
                                    typ
                                }
                            }
                        })
                        .unwrap_or(Type::Unknown)
                })
                .collect::<Vec<_>>();
            let mut bindings = context.clone();
            if let Type::Function(_, captures) = self.specialized_type(Ref { node: callee, ..call }, context) { bindings.extend(captures); }
            bindings.extend(self.parameter_bindings(signature, &types));
            let mut result_default_valid=true;
            for query in crate::resulttypes::default_queries(self,signature) {
                let default_used=arguments.iter().flatten().any(|arg|*arg==query.query);
                if !default_used {continue;}
                if let Some(callback)=arguments.get(query.callback).and_then(|group|group.first()).copied() {
                    if let Some(results)=crate::resulttypes::resolve_default(self,call,query.call,&bindings,callback).0 {
                        if results.fields.is_empty() {result_default_valid=false;break;}
                        bindings.insert(query.name,crate::resulttypes::packed_type(&results));
                    }
                }
            }
            if !result_default_valid {continue;}
            let sites = arguments
                .iter()
                .map(|group| group.first().copied().unwrap_or(call))
                .collect::<Vec<_>>();
            bindings.extend(self.constant_parameter_bindings(signature, &sites, context));
            let mut score = 0;
            let mut valid = true;
            for (index, ((_, annotation), group)) in fields.iter().zip(&arguments).enumerate() {
                if let Some(annotation) = annotation {
                    let formal = &self.sources[annotation.file];
                    let variadic = formal.node(annotation.node).kind == "Ellipsis";
                    let element = if variadic {
                        formal
                            .role(annotation.node, "expr")
                            .map(|node| Ref {
                                file: annotation.file,
                                node,
                            })
                            .unwrap_or(*annotation)
                    } else {
                        *annotation
                    };
                    for arg in group.iter().filter(|arg| supplied.contains(arg)) {
                        let annotation = if variadic && !self.packed_variadic(call, *arg) {
                            element
                        } else {
                            *annotation
                        };
                        if self.sources[annotation.file].node(annotation.node).kind == "TypeidType"
                        {
                            score += if self.denotes_type(*arg) { 5 } else { 0 };
                            continue;
                        }
                        let typ = if !variadic {
                            // An ordinary parameter has one arranged argument.
                            // Its type was already resolved for binding above.
                            types[index].clone()
                        } else if self.denotes_type(*arg) {
                            Type::Scalar {
                                name: "typeid".into(),
                                untyped: false,
                            }
                        } else {
                            self.specialized_type(*arg, context)
                        };
                        let types = if variadic {
                            self.spread_types(*arg, context)
                                .unwrap_or_else(|| vec![typ])
                        } else {
                            vec![typ]
                        };
                        for typ in types {
                            if let Some(amount) = self.shape_score(annotation, &typ, &bindings) {
                                score += amount;
                            } else if !self.contextual_cast_argument(*arg) {
                                valid = false;
                                break;
                            }
                        }
                    }
                }
            }
            if valid {
                plans.push((
                    score,
                    CallPlan {
                        procedure,
                        signature,
                        arguments,
                        bindings,
                    },
                ));
            }
        }
        if plans.len() <= 1 {
            return plans.into_iter().map(|(_, plan)| plan).collect();
        }
        let best = plans.iter().map(|(score, _)| *score).max();
        // Mutually exclusive declarations are one native overload. A weaker
        // match in the active arm cannot be shadowed by its inactive sibling.
        let selected = plans
            .iter()
            .filter(|(score, _)| Some(*score) == best)
            .map(|(_, plan)| self.checked_procedure_identity(plan.procedure))
            .collect::<HashSet<_>>();
        plans
            .into_iter()
            .filter(|(score, plan)| {
                Some(*score) == best
                    || selected.contains(&self.checked_procedure_identity(plan.procedure))
            })
            .map(|(_, plan)| plan)
            .collect()
    }

    /// Arguments in declaration order, including defaults and variadic groups.
    /// No argument expressions are executed while considering an overload.
    pub fn arrange_arguments(&self, signature: Ref, args: &[Ref]) -> Option<Vec<Vec<Ref>>> {
        let layout = self.signature_layout(signature);
        let arguments = layout.arguments.as_ref()?;
        let fields = &arguments.fields;
        let mut bound = vec![vec![]; fields.len()];
        let mut positional = 0;
        let mut named = false;
        for arg in args {
            let owner = &self.sources[arg.file];
            let node = owner.node(arg.node);
            if node.kind == "FieldValue" {
                named = true;
                let name = owner.text(owner.role(arg.node, "field")?);
                let index = *arguments.positions.get(name)?;
                if !bound[index].is_empty() {
                    return None;
                }
                bound[index].push(Ref {
                    file: arg.file,
                    node: owner.role(arg.node, "value")?,
                });
            } else {
                if named || positional >= fields.len() {
                    return None;
                }
                bound[positional].push(*arg);
                if !fields[positional].variadic {
                    positional += 1;
                }
            }
        }
        for (index, field) in fields.iter().enumerate() {
            if bound[index].is_empty() {
                if let Some(default) = field.default {
                    bound[index].push(default);
                } else if !field.variadic && !(arguments.positions.get("__thor_pmap_spread")==Some(&index)
                    && crate::parallelcompile::has_mode_parameter(self,signature)) {
                    return None;
                }
            }
        }
        Some(bound)
    }

    pub fn specialized_result(&mut self, call: Ref, index: usize, context: &Substitutions) -> Type {
        if let Some(typ)=self.builtin_result_type(call,index,context){return typ;}
        let mut result = None;
        for plan in self.call_plans(call, context) {
            let layout = self.signature_layout(plan.signature);
            let typ = layout.results
                .get(index)
                .and_then(|(_, annotation)| *annotation)
                .map(|annotation| self.specialized_type(annotation, &plan.bindings))
                .unwrap_or(Type::Unknown);
            if result.as_ref().is_some_and(|previous| previous != &typ) {
                return Type::Unknown;
            }
            result = Some(typ);
        }
        result.unwrap_or_else(|| self.soa_zip_result(call, index, context).unwrap_or(Type::Unknown))
    }

    fn soa_zip_result(&mut self, call: Ref, index: usize, context: &Substitutions) -> Option<Type> {
        if index != 0 { return None; }
        let source = &self.sources[call.file];
        let callee = Ref { node: source.role(call.node, "callee")?, ..call };
        if !self.native_builtin_alias(callee, "soa_zip") { return None; }
        let arguments = source.group(call.node, "args").to_vec();
        if arguments.is_empty() { return None; }
        let mut fields = Vec::with_capacity(arguments.len());
        for (index, node) in arguments.into_iter().enumerate() {
            let name = source.role(node, "field")
                .map(|field| source.text(field).to_owned())
                .unwrap_or_else(|| format!("_{index}"));
            let value = Ref { node: source.role(node, "value").unwrap_or(node), ..call };
            let typ = self.specialized_type(value, context);
            let mut base = &typ;
            while let Type::Nominal(_, inner) = base { base = inner; }
            let Type::Slice(element) = base else { return None; };
            fields.push((name, *element.clone()));
        }
        Some(Type::Soa(Box::new(Type::Anonymous(fields))))
    }
}
