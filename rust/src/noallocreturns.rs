//! Bounded return identities, independent of effect verification. Every real
//! call still queues its implementation in Check; this never exempts a body.
use super::*;

fn callable(r: &mut Resolver<'_>, typ: &Type, seen: &mut HashSet<Type>) -> bool {
    if seen.len() > 128 {
        return false;
    }
    if !seen.insert(typ.clone()) {
        return false;
    }
    match base_type(typ) {
        Type::Procedure(_) => true,
        Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => r
            .record_fields(typ)
            .iter()
            .any(|(_, t)| callable(r, t, seen)),
        _ => false,
    }
}
fn child(r: &Resolver<'_>, at: Ref, role: &str) -> Option<Ref> {
    r.sources[at.file]
        .role(at.node, role)
        .map(|node| Ref { node, ..at })
}
pub(super) fn value(
    flow: &Flow,
    r: &mut Resolver<'_>,
    at: Ref,
    bindings: &Substitutions,
    depth: usize,
) -> Fact {
    if depth > 20 {
        return Fact::Unknown;
    }
    let typ = r.specialized_type(at, bindings);
    if !callable(r, &typ, &mut HashSet::new()) {
        return Fact::Unknown;
    }
    let Some(callee) = child(r, at, "callee") else {
        return Fact::Unknown;
    };
    let plans = if crate::parallelcompile::known_callback(r, callee) {
        r.call_plans(at, bindings)
    } else if let Fact::Function(target) = flow.fact_at(r, callee, bindings, depth + 1) {
        r.call_plans_for_targets(at, bindings, &[target])
    } else {
        return Fact::Unknown;
    };
    let Some(plan) = plans.first().filter(|_| plans.len() == 1) else {
        return Fact::Unknown;
    };
    let signature = &r.sources[plan.signature.file];
    if crate::caller_metadata::params(signature, plan.signature.node)
        .iter()
        .any(|(_, field)| signature.node(*field).data["variadic"] == true)
    {
        return Fact::Unknown;
    }
    let source = &r.sources[plan.procedure.file];
    let Some(body) = source.role(plan.procedure.node, "body") else {
        return Fact::Unknown;
    };
    if source
        .descendants(body)
        .any(|node| source.node(node).kind == "Defer")
    {
        return Fact::Unknown;
    }
    let arguments = plan
        .arguments
        .iter()
        .map(|group| {
            group
                .first()
                .map(|arg| {
                    let value = flow.fact_at(r, *arg, &plan.bindings, depth + 1);
                    // Snapshot pointees against the caller, never retain caller-local keys
                    // in a summary cached for another invocation.
                    flow.detached_return_argument(&value)
                })
                .unwrap_or_default()
        })
        .collect::<Vec<_>>();
    let key = (plan.procedure, plan.bindings.clone(), arguments.clone());
    if let Some(value) = flow.returned.borrow().get(&key) {
        return value.clone();
    }
    if flow.returned.borrow().len() >= 512 {
        return Fact::Unknown;
    }
    flow.returned
        .borrow_mut()
        .insert(key.clone(), Fact::Unknown);
    let mut state = flow.clone();
    state.begin(r, plan.procedure, &arguments, &plan.bindings);
    let mut evaluator = Evaluate {
        r,
        bindings: &plan.bindings,
        flow: state,
        depth,
        budget: 2048,
        returns: vec![],
        procedure: plan.procedure,
    };
    evaluator.walk(Ref {
        node: body,
        ..plan.procedure
    });
    let value = if !evaluator.flow.terminated {
        Fact::Unknown
    } else {
        evaluator
            .returns
            .first()
            .filter(|first| evaluator.returns.iter().all(|v| v == *first))
            .cloned()
            .unwrap_or_default()
    };
    flow.returned.borrow_mut().insert(key, value.clone());
    value
}
struct Evaluate<'a, 'r, 'b> {
    r: &'r mut Resolver<'a>,
    bindings: &'b Substitutions,
    flow: Flow,
    depth: usize,
    budget: usize,
    returns: Vec<Fact>,
    procedure: Ref,
}
impl Evaluate<'_, '_, '_> {
    fn expression(&mut self, at: Ref) -> Fact {
        if self.budget == 0 || self.depth > 20 {
            self.flow.invalidate_all();
            return Fact::Unknown;
        }
        self.budget -= 1;
        self.depth += 1;
        let kind = self.r.sources[at.file].node(at.node).kind.clone();
        if kind == "Procedure" {
            self.depth -= 1;
            return self.flow.fact_at(self.r, at, self.bindings, self.depth + 1);
        }
        let children = self.r.sources[at.file].node(at.node).children.to_vec();
        for node in children {
            let here = Ref { node, ..at };
            if !self.r.denotes_type(here) {
                self.expression(here);
            }
        }
        let result = self.flow.fact_at(self.r, at, self.bindings, self.depth + 1);
        if kind == "Call" {
            let callee = child(self.r, at, "callee");
            let builtin = crate::borroweval::Evaluator {
                resolver: self.r,
                arena: &mut crate::borrowflow::Arena::default(),
            }
            .builtin(at);
            if !callee.is_some_and(|c| self.r.denotes_type(c)) && builtin.is_none() {
                for node in self.r.sources[at.file].group(at.node, "args").to_vec() {
                    let value = self.flow.fact_at(
                        self.r,
                        Ref { node, ..at },
                        self.bindings,
                        self.depth + 1,
                    );
                    self.flow.forget_addresses(&value);
                }
                self.flow.opaque_write();
            }
        }
        self.depth -= 1;
        result
    }
    fn walk(&mut self, at: Ref) {
        if self.flow.terminated {
            return;
        }
        if self.budget == 0 {
            self.returns.push(Fact::Unknown);
            self.flow.terminated = true;
            return;
        }
        self.budget -= 1;
        let source = &self.r.sources[at.file];
        let node = source.node(at.node).clone();
        match node.kind.as_str() {
            "Block" => {
                for node in node.children {
                    self.walk(Ref { node, ..at });
                }
            }
            "Procedure" | "Attribute" => {}
            "Declaration" => {
                if !node.mutable {
                    return;
                }
                let names = source.group(at.node, "names").to_vec();
                let values = source.group(at.node, "values").to_vec();
                let facts = values
                    .iter()
                    .map(|node| self.expression(Ref { node: *node, ..at }))
                    .collect::<Vec<_>>();
                if values.is_empty() {
                    self.flow.bind_declaration(self.r, at, self.bindings);
                } else {
                    for (i, node) in names.into_iter().enumerate() {
                        self.flow.assign(
                            self.r,
                            Ref { node, ..at },
                            facts.get(i).cloned().unwrap_or_default(),
                        );
                    }
                }
            }
            "Assignment" => {
                let lhs = source.group(at.node, "lhs").to_vec();
                let rhs = source.group(at.node, "rhs").to_vec();
                let values = rhs
                    .iter()
                    .map(|node| self.expression(Ref { node: *node, ..at }))
                    .collect::<Vec<_>>();
                for (i, lhs) in lhs.into_iter().enumerate() {
                    self.flow.assign(
                        self.r,
                        Ref { node: lhs, ..at },
                        if node.op == "=" {
                            values.get(i).cloned().unwrap_or_default()
                        } else {
                            Fact::Unknown
                        },
                    );
                }
            }
            "Return" => {
                let values = source.group(at.node, "values").to_vec();
                let value = if values.len() == 1 {
                    self.expression(Ref {
                        node: values[0],
                        ..at
                    })
                } else if values.is_empty() {
                    let source = &self.r.sources[self.procedure.file];
                    let names = source
                        .role(self.procedure.node, "type")
                        .and_then(|signature| source.role(signature, "results"))
                        .map(|node| {
                            source
                                .node(node)
                                .children
                                .iter()
                                .flat_map(|field| source.group(*field, "names").iter().copied())
                                .collect::<Vec<_>>()
                        })
                        .unwrap_or_default();
                    if names.len() == 1 {
                        self.flow.fact_at(
                            self.r,
                            Ref {
                                node: names[0],
                                ..self.procedure
                            },
                            self.bindings,
                            self.depth + 1,
                        )
                    } else {
                        Fact::Unknown
                    }
                } else {
                    Fact::Unknown
                };
                self.returns
                    .push(self.flow.detached_return_argument(&value));
                self.flow.terminated = true;
            }
            "If" | "When" => {
                if let Some(init) = child(self.r, at, "init") {
                    self.walk(init);
                }
                let cond = child(self.r, at, "cond");
                if let Some(cond) = cond {
                    self.expression(cond);
                }
                let branches = [child(self.r, at, "body"), child(self.r, at, "else")];
                if let Some(selected) =
                    cond.and_then(|cond| self.flow.condition(self.r, cond, self.bindings))
                {
                    if let Some(branch) = branches[usize::from(!selected)] {
                        self.walk(branch);
                    }
                } else {
                    let before = self.flow.clone();
                    if let Some(branch) = branches[0] {
                        self.walk(branch);
                    }
                    let left = self.flow.clone();
                    self.flow = before;
                    if let Some(branch) = branches[1] {
                        self.walk(branch);
                    }
                    self.flow.join(&left);
                }
            }
            "Call" => {
                self.expression(at);
            }
            // Loops, defers, switch fallthrough and opaque statements do not
            // establish a return identity. Normal effect checking handles them.
            _ => {
                self.returns.push(Fact::Unknown);
                self.flow.terminated = true;
            }
        }
    }
}
