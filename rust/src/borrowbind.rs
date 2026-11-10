//! Reference Borrowing.bindings/project/resolve/instantiate operations.
use crate::{
    borroweval::{Environment, Evaluator},
    borrowflow::{Flow, Id},
    permissions::{Access, Aspect, Permission, Step, READONLY},
    semantics::{Ref, Type},
};
use std::collections::HashMap;

impl Evaluator<'_, '_, '_> {
    pub fn parameters(&self, procedure: Ref) -> Vec<(Ref, Ref)> {
        self.role(procedure, "type")
            .and_then(|signature| self.role(signature, "params"))
            .into_iter()
            .flat_map(|params| {
                self.resolver.sources[params.file]
                    .node(params.node)
                    .children
                    .iter()
                    .map(move |node| Ref {
                        file: params.file,
                        node: *node,
                    })
            })
            .filter(|field| self.kind(*field) == "Field")
            .flat_map(|field| {
                self.group(field, "names")
                    .into_iter()
                    .map(move |name| (name, field))
            })
            .collect()
    }
    pub fn bindings(&mut self, call: Ref, callee: Ref, env: &mut Environment) -> Vec<Id> {
        let mut positional = vec![];
        let mut named = HashMap::new();
        if let Some(receiver) = self.role(call, "callee") {
            if self.kind(receiver) == "Selector"
                && self.resolver.sources[receiver.file].node(receiver.node).op == "->"
            {
                positional.extend(self.role(receiver, "expr"));
            }
        }
        for arg in self.group(call, "args") {
            if self.kind(arg) == "FieldValue" {
                if let (Some(field), Some(value)) =
                    (self.role(arg, "field"), self.role(arg, "value"))
                {
                    named.insert(self.text(field).to_string(), value);
                }
            } else {
                positional.push(arg);
            }
        }
        let ellipsis = self.resolver.sources[call.file]
            .node(call.node)
            .spans
            .get("ellipsis")
            .map(|span| span.end);
        let mut position = 0;
        let mut bound = vec![];
        for (name, field) in self.parameters(callee) {
            let key = self.text(name).trim_start_matches('$').to_string();
            if self.resolver.sources[field.file].node(field.node).data["variadic"] == true {
                let mut values = positional[position..].to_vec();
                position = positional.len();
                if let Some(value) = named.get(&key) {
                    values = vec![*value];
                }
                let flow = if values.len() == 1
                    && ellipsis.is_some_and(|offset| {
                        self.resolver.sources[values[0].file]
                            .node(values[0].node)
                            .span
                            .start
                            >= offset
                    }) {
                    let value = self.flow(values[0], env);
                    let typ = self.resolver.type_of(values[0]);
                    self.arena
                        .header_copy(value, typ, Some(values[0]), None, self.resolver)
                } else {
                    let mut elements = vec![];
                    for value in values {
                        if self.kind(value) == "Unary"
                            && self.resolver.sources[value.file].node(value.node).op == "**"
                        {
                            if let Some(base) = self.role(value, "expr") {
                                let typ = self.resolver.type_of(base);
                                let value = self.flow(base, env);
                                let value = self.arena.item(value);
                                elements.push(self.arena.header_copy(
                                    value,
                                    typ.element().cloned().unwrap_or(Type::Unknown),
                                    Some(base),
                                    None,
                                    self.resolver,
                                ));
                            }
                        } else {
                            let flow = self.flow(value, env);
                            let flow = self.copied(flow, value);
                            let typ = self.resolver.type_of(value);
                            elements.push(self.arena.header_copy(
                                flow,
                                typ,
                                Some(value),
                                None,
                                self.resolver,
                            ));
                        }
                    }
                    let element = if elements.is_empty() {
                        None
                    } else {
                        Some(self.arena.joined(&elements, self.resolver))
                    };
                    self.arena.add(Flow {
                        element,
                        ..Flow::default()
                    })
                };
                self.arena.values[flow].location = READONLY;
                bound.push(flow);
                continue;
            }
            let value = named
                .get(&key)
                .copied()
                .or_else(|| {
                    let value = positional.get(position).copied();
                    if value.is_some() {
                        position += 1;
                    }
                    value
                })
                .or_else(|| self.role(field, "default"));
            let (flow, typ) = if let Some(value) = value {
                (self.flow(value, env), self.resolver.type_of(value))
            } else {
                (
                    self.arena.add(Flow::default()),
                    self.role(field, "type")
                        .map(|typ| self.resolver.type_of(typ))
                        .unwrap_or(Type::Unknown),
                )
            };
            let flow = self
                .arena
                .header_copy(flow, typ, value, None, self.resolver);
            self.arena.values[flow].location = READONLY;
            bound.push(flow);
        }
        bound
    }
    pub fn project_access(&mut self, bindings: &[Id], access: &Access) -> Id {
        let mut value = bindings.get(access.index).copied().unwrap_or_else(|| {
            self.arena.add(Flow {
                borrowed: READONLY,
                ..Flow::default()
            })
        });
        for step in &access.path {
            value = match step {
                Step::Field(name) => self.arena.member(value, name, self.resolver),
                Step::Item => self.arena.item(value),
                Step::Deref => self.arena.deref(value, self.resolver),
            };
        }
        value
    }
    pub fn resolve_access(&mut self, bindings: &[Id], access: &Access) -> Permission {
        let value = self.project_access(bindings, access);
        match access.aspect {
            Aspect::Location => self.arena.values[value].location.clone(),
            Aspect::Borrowed => self.arena.values[value].borrowed.clone(),
        }
    }
    pub fn instantiate(
        &mut self,
        value: Id,
        bindings: &[Id],
        cache: &mut HashMap<Id, Id>,
    ) -> (Id, bool) {
        let mut changed = false;
        let result =
            self.instantiate_seen(value, bindings, cache, &mut HashMap::new(), &mut changed);
        (result, changed)
    }
    fn instantiate_seen(
        &mut self,
        value: Id,
        bindings: &[Id],
        cache: &mut HashMap<Id, Id>,
        seen: &mut HashMap<Id, Id>,
        changed: &mut bool,
    ) -> Id {
        if let Some(value) = seen.get(&value) {
            return *value;
        }
        let flow = self.arena.values[value].clone();
        let result = if let Some(result) = cache.get(&value) {
            *result
        } else {
            let result = self.arena.add(Flow::default());
            cache.insert(value, result);
            *changed = true;
            result
        };
        seen.insert(value, result);
        let borrowed = flow
            .borrowed
            .substitute(&mut |access| self.resolve_access(bindings, access));
        let incoming = self.arena.add(Flow {
            borrowed,
            numeric_copy: flow.numeric_copy,
            stack: flow.stack,
            ..Flow::default()
        });
        *changed |= self.arena.merge(result, incoming);
        let location = flow
            .location
            .substitute(&mut |access| self.resolve_access(bindings, access));
        let location = self.arena.values[result].location.join(&location);
        *changed |= location != self.arena.values[result].location;
        self.arena.values[result].location = location;
        self.arena.values[result].owner = flow.owner;
        self.arena.values[result].kind = flow.kind;
        for (name, child) in flow.fields {
            let child = self.instantiate_seen(child, bindings, cache, seen, changed);
            self.arena.values[result].fields.insert(name, child);
        }
        if let Some(child) = flow.element {
            let child = self.instantiate_seen(child, bindings, cache, seen, changed);
            self.arena.values[result].element = Some(child);
        }
        let originals = flow
            .origins
            .iter()
            .map(|access| self.project_access(bindings, access))
            .collect::<Vec<_>>();
        let mut targets = originals
            .iter()
            .filter_map(|original| self.arena.values[*original].target)
            .collect::<Vec<_>>();
        if targets.is_empty() {
            if let Some(target) = flow.target {
                targets.push(self.instantiate_seen(target, bindings, cache, seen, changed));
            }
        }
        for target in targets {
            let incoming = self.arena.add(Flow {
                target: Some(target),
                ..Flow::default()
            });
            *changed |= self.arena.merge(result, incoming);
        }
        for original in originals {
            let flow = self.arena.values[original].clone();
            let incoming = self.arena.add(Flow {
                origins: flow.origins,
                stack: flow.stack,
                ..Flow::default()
            });
            *changed |= self.arena.merge(result, incoming);
            if self.arena.values[result].element.is_none() {
                self.arena.values[result].element = flow.element;
            }
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        ast::NodeId,
        borrowflow::{Arena, Kind},
        permissions::OWNED,
        semantics::Resolver,
    };
    fn access() -> Access {
        Access {
            procedure: Ref {
                file: 0,
                node: NodeId(1),
            },
            index: 0,
            path: vec![],
            aspect: Aspect::Borrowed,
        }
    }
    #[test]
    fn instantiation_preserves_the_callers_actual_pointer_target_and_converges() {
        let mut resolver = Resolver::new(&[]);
        let mut arena = Arena::default();
        let target = arena.add(Flow::default());
        let actual = arena.add(Flow {
            target: Some(target),
            kind: Kind::Pointer,
            location: READONLY,
            ..Flow::default()
        });
        let detached = arena.add(Flow::default());
        let symbolic = arena.add(Flow {
            borrowed: Permission::Access(access()),
            target: Some(detached),
            origins: [access()].into(),
            ..Flow::default()
        });
        let mut eval = Evaluator {
            resolver: &mut resolver,
            arena: &mut arena,
        };
        let mut cache = HashMap::new();
        let (value, changed) = eval.instantiate(symbolic, &[actual], &mut cache);
        assert!(changed);
        assert_eq!(eval.arena.values[value].target, Some(target));
        assert_eq!(eval.arena.values[value].borrowed, OWNED);
        let (second, changed) = eval.instantiate(symbolic, &[actual], &mut cache);
        assert_eq!(second, value);
        assert!(!changed);
    }
    #[test]
    fn cyclic_instantiation_substitutes_parameter_permissions_without_copying_targets() {
        let mut resolver = Resolver::new(&[]);
        let mut arena = Arena::default();
        let actual = arena.add(Flow {
            borrowed: READONLY,
            ..Flow::default()
        });
        let symbolic = arena.add(Flow {
            borrowed: Permission::Access(access()),
            ..Flow::default()
        });
        arena.values[symbolic]
            .fields
            .insert("self".into(), symbolic);
        let mut eval = Evaluator {
            resolver: &mut resolver,
            arena: &mut arena,
        };
        let mut cache = HashMap::new();
        let (value, changed) = eval.instantiate(symbolic, &[actual], &mut cache);
        assert!(changed);
        assert_eq!(eval.arena.values[value].borrowed, READONLY);
        let child = eval.arena.values[value].fields.get("self").copied();
        assert_eq!(child, Some(value));
        let (_, changed) = eval.instantiate(symbolic, &[actual], &mut cache);
        assert!(!changed);
    }
}
