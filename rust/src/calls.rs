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

impl Resolver<'_> {
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
        let key = (
            call,
            context
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
        );
        if !self.planning.insert(key.clone()) {
            return vec![];
        }
        let plans = self.plan_calls(call, context);
        self.planning.remove(&key);
        plans
    }

    fn plan_calls(&mut self, call: Ref, context: &Substitutions) -> Vec<CallPlan> {
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
        let mut plans = vec![];
        for procedure in self.candidates(Ref {
            file: call.file,
            node: callee,
        }) {
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
            let Some(arguments) = self.arrange_arguments(signature, &args) else {
                continue;
            };
            let fields = self.fields(signature, "params");
            let types = arguments
                .iter()
                .zip(&fields)
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
                                self.specialized_type(*arg, context)
                            }
                        })
                        .unwrap_or(Type::Unknown)
                })
                .collect::<Vec<_>>();
            let mut bindings = context.clone();
            bindings.extend(self.parameter_bindings(signature, &types));
            let sites = arguments
                .iter()
                .map(|group| group.first().copied().unwrap_or(call))
                .collect::<Vec<_>>();
            bindings.extend(self.constant_parameter_bindings(signature, &sites, context));
            let mut score = 0;
            let mut valid = true;
            for ((_, annotation), group) in fields.iter().zip(&arguments) {
                if let Some(annotation) = annotation {
                    let formal = &self.sources[annotation.file];
                    let variadic = formal.node(annotation.node).kind == "Ellipsis";
                    let annotation =
                        if variadic && !source.node(call.node).spans.contains_key("ellipsis") {
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
                        let type_operand = self.denotes_type(*arg);
                        if self.sources[annotation.file].node(annotation.node).kind == "TypeidType"
                        {
                            score += if type_operand { 5 } else { 0 };
                            continue;
                        }
                        let typ = if type_operand {
                            Type::Scalar {
                                name: "typeid".into(),
                                untyped: false,
                            }
                        } else {
                            self.specialized_type(*arg, context)
                        };
                        if let Some(amount) = self.shape_score(annotation, &typ, &bindings) {
                            score += amount;
                        } else if !self.contextual_cast_argument(*arg) {
                            valid = false;
                            break;
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
        let best = plans.iter().map(|(score, _)| *score).max();
        plans
            .into_iter()
            .filter(|(score, _)| Some(*score) == best)
            .map(|(_, plan)| plan)
            .collect()
    }

    /// Arguments in declaration order, including defaults and variadic groups.
    /// No argument expressions are executed while considering an overload.
    pub fn arrange_arguments(&self, signature: Ref, args: &[Ref]) -> Option<Vec<Vec<Ref>>> {
        let source = &self.sources[signature.file];
        let params = source.role(signature.node, "params")?;
        let fields = source
            .node(params)
            .children
            .iter()
            .filter(|node| source.node(**node).kind == "Field")
            .flat_map(|field| {
                source
                    .group(*field, "names")
                    .iter()
                    .map(move |name| (*name, *field))
            })
            .collect::<Vec<_>>();
        let mut bound = vec![vec![]; fields.len()];
        let mut positional = 0;
        let mut named = false;
        for arg in args {
            let owner = &self.sources[arg.file];
            let node = owner.node(arg.node);
            if node.kind == "FieldValue" {
                named = true;
                let name = owner.text(owner.role(arg.node, "field")?);
                let index = fields.iter().position(|(parameter, _)| {
                    source.text(*parameter).trim_start_matches('$') == name
                })?;
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
                if source.node(fields[positional].1).data["variadic"] != true {
                    positional += 1;
                }
            }
        }
        for (index, (_, field)) in fields.iter().enumerate() {
            if bound[index].is_empty() {
                if let Some(default) = source.role(*field, "default") {
                    bound[index].push(Ref {
                        file: signature.file,
                        node: default,
                    });
                } else if source.node(*field).data["variadic"] != true {
                    return None;
                }
            }
        }
        Some(bound)
    }

    pub fn specialized_result(&mut self, call: Ref, index: usize, context: &Substitutions) -> Type {
        let mut result = None;
        for plan in self.call_plans(call, context) {
            let typ = self
                .fields(plan.signature, "results")
                .get(index)
                .and_then(|(_, annotation)| *annotation)
                .map(|annotation| self.specialized_type(annotation, &plan.bindings))
                .unwrap_or(Type::Unknown);
            if result.as_ref().is_some_and(|previous| previous != &typ) {
                return Type::Unknown;
            }
            result = Some(typ);
        }
        result.unwrap_or(Type::Unknown)
    }
}
