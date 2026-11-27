//! Reference Borrowing.prepare/pass_/store/finish. This builds interprocedural
//! symbolic stores and requirements; call-profile lowering is connected separately.
use crate::{
    borroweval::{Environment, Evaluator},
    borrowflow::{Arena, Flow, Id, Kind},
    permissions::{Access, Permission, MAX_PATH, READONLY},
    purity,
    semantics::{Ref, Resolver, Type},
};
use std::collections::{BTreeMap, BTreeSet, HashMap};

#[derive(Clone)]
pub struct Store {
    pub target: Access,
    pub value: Id,
}
#[derive(Default)]
pub struct Model {
    pub arena: Arena,
    pub environments: BTreeMap<Ref, Environment>,
    pub nodes: BTreeMap<Ref, Vec<Ref>>,
    pub targets: BTreeMap<Ref, BTreeSet<Ref>>,
    pub stores: BTreeMap<Ref, BTreeMap<Access, Store>>,
    pub requirements: BTreeMap<Ref, BTreeSet<Access>>,
    pub checks: BTreeMap<Ref, Permission>,
    pub escapes: BTreeMap<Ref, String>,
    pub mutates: BTreeSet<Ref>,
    pub converged: bool,
    instances: HashMap<(Ref, Ref), HashMap<Id, Id>>,
}
#[derive(Clone, Default)]
pub struct Emission {
    pub callbacks: BTreeSet<Ref>,
    pub procedures: BTreeSet<Ref>,
    pub entries: BTreeSet<Ref>,
    pub checks: BTreeMap<Ref, Permission>,
    pub profiles: BTreeMap<Ref, Vec<(Access, Permission)>>,
    pub mutates: BTreeSet<Ref>,
    pub escapes: BTreeMap<Ref, String>,
    pub contracts: BTreeMap<Ref, String>,
}
impl Emission {
    pub fn covers(&self, resolver: &Resolver<'_>, site: Ref) -> bool {
        resolver.sources[site.file]
            .ancestors(site.node)
            .find(|id| resolver.sources[site.file].node(*id).kind == "Procedure")
            .is_some_and(|node| {
                self.procedures.contains(&Ref {
                    file: site.file,
                    node,
                })
            })
    }
}
fn kind(typ: &Type) -> Kind {
    match typ {
        Type::Nominal(_, inner) => kind(inner),
        Type::Pointer(_) => Kind::Pointer,
        Type::Array { .. } => Kind::Array,
        Type::Fixed { .. } => Kind::Fixed,
        Type::Generic => Kind::Generic,
        Type::Unknown => Kind::Unknown,
        _ => Kind::Other,
    }
}
fn walk(resolver: &Resolver<'_>, node: Ref, nodes: &mut Vec<Ref>) {
    nodes.push(node);
    for id in &resolver.sources[node.file].node(node.node).children {
        if resolver.sources[node.file].node(*id).kind != "Procedure" {
            walk(
                resolver,
                Ref {
                    file: node.file,
                    node: *id,
                },
                nodes,
            );
        }
    }
}
fn store(
    proc: Ref,
    target: Id,
    value: Id,
    node: Ref,
    arena: &mut Arena,
    stores: &mut BTreeMap<Ref, BTreeMap<Access, Store>>,
    escapes: &mut BTreeMap<Ref, String>,
) -> bool {
    let mut changed = false;
    for location in arena.values[target].locations.clone() {
        if location.procedure != proc || location.path.len() > MAX_PATH {
            continue;
        }
        if arena.stack_owners(value).contains(&proc) {
            escapes.insert(node,"Pure helpers cannot store a reference to their local stack storage through an input; allocate an owned buffer or copy the value instead.".into());
            continue;
        }
        let entries = stores.entry(proc).or_default();
        let destination = if let Some(entry) = entries.get(&location) {
            entry.value
        } else {
            let value = arena.add(Flow::default());
            entries.insert(
                location.clone(),
                Store {
                    target: location,
                    value,
                },
            );
            changed = true;
            value
        };
        changed |= arena.merge(destination, value);
    }
    changed
}
impl Model {
    /// Connect only the reference domains already supported by native emission.
    /// Other paths retain their prior explicit migration checks.
    pub fn emission(&mut self, resolver: &mut Resolver<'_>, purity: &purity::Facts) -> Emission {
        if !self.converged {
            // Python installs a contract on every procedure prepared in this
            // graph. Keep it inside the native checked body: unused generic
            // helpers and ordinary proc calls must remain lazy/unchanged.
            return Emission {
                entries: purity.entry_roots.clone(),
                procedures: self.nodes.keys().copied().collect(),
                contracts: self.nodes.keys().copied().map(|proc| (proc,
                    "Pure storage provenance did not converge; simplify the reference graph.".into())).collect(),
                profiles: purity.targets.keys().copied().map(|call| (call, vec![])).collect(),
                ..Emission::default()
            };
        }
        let mut result = Emission {
            entries: purity.entry_roots.clone(),
            callbacks: purity.parallel_callbacks.clone(),
            ..Emission::default()
        };
        for (proc, nodes) in &self.nodes {
            let mut eval = Evaluator {
                resolver,
                arena: &mut self.arena,
            };
            let supported = nodes.iter().all(|node| {
                if purity.conditional.contains_key(node) {
                    return true;
                }
                if eval.queried(*node) {
                    return true;
                }
                if eval.kind(*node) != "Call" {
                    return true;
                }
                eval.builtin(*node).is_some()
                    || eval.approved(*node) && !crate::parallelcompile::is_call(eval.resolver,*node)
                    || eval
                        .role(*node, "callee")
                        .is_some_and(|callee| eval.resolver.denotes_type(callee) || matches!(eval.resolver.type_of(callee),Type::Function(..)|Type::Generic))
                    || self
                        .targets
                        .get(node)
                        .is_some_and(|targets| !targets.is_empty())
            });
            if supported {
                result.procedures.insert(*proc);
            }
        }
        loop {
            let rejected = result
                .procedures
                .iter()
                .filter(|proc| {
                    self.nodes[proc].iter().any(|node| {
                        purity.targets.get(node).is_some_and(|targets| {
                            targets
                                .iter()
                                .any(|target| !result.procedures.contains(target))
                        })
                    })
                })
                .copied()
                .collect::<Vec<_>>();
            if rejected.is_empty() {
                break;
            }
            for proc in rejected {
                result.procedures.remove(&proc);
            }
        }
        for proc in &result.procedures {
            let env = self.environments.get_mut(proc).unwrap();
            let mut eval = Evaluator {
                resolver,
                arena: &mut self.arena,
            };
            for node in &self.nodes[proc] {
                if let Some(check) = self.checks.get(node) {
                    result.checks.insert(*node, check.clone());
                }
                if let Some(message) = self.escapes.get(node) {
                    result.escapes.insert(*node, message.clone());
                }
                if let Some(targets) = purity.targets.get(node) {
                    let mut profile = BTreeMap::new();
                    for target in targets {
                        let bindings = eval.bindings(*node, *target, env);
                        for access in &self.requirements[target] {
                            profile.insert(access.clone(), eval.resolve_access(&bindings, access));
                        }
                    }
                    result.profiles.insert(*node, profile.into_iter().collect());
                }
            }
        }
        result.mutates = self
            .mutates
            .intersection(&result.procedures)
            .copied()
            .collect();
        result
    }
    pub fn prepare(resolver: &mut Resolver<'_>, purity: &purity::Facts) -> Self {
        let _phase = crate::profile::span("analysis.borrow_model");
        let mut model = Self::default();
        let mut queue = purity.checked.iter().copied().collect::<Vec<_>>();
        while let Some(proc) = queue.pop() {
            if model.nodes.contains_key(&proc) {
                continue;
            }
            let mut eval = Evaluator {
                resolver,
                arena: &mut model.arena,
            };
            let proc = &proc;
            let Some(body) = eval.role(*proc, "body") else {
                continue;
            };
            let internal = eval.resolver.sources[proc.file].node(proc.node).data["pure"] != true
                && !purity.entry_roots.contains(proc);
            let mut env = Environment::default();
            for (index, (name, field)) in eval.parameters(*proc).into_iter().enumerate() {
                if let Some(symbol) = eval.resolver.symbol(name) {
                    let access = Access {
                        procedure: *proc,
                        index,
                        path: vec![],
                        aspect: crate::permissions::Aspect::Borrowed,
                    };
                    let borrowed = if internal {
                        Permission::Access(access.clone())
                    } else {
                        READONLY
                    };
                    let typ = eval
                        .role(field, "type")
                        .map(|node| eval.resolver.type_of(node))
                        .unwrap_or(Type::Unknown);
                    let locations = if internal {
                        [access].into()
                    } else {
                        BTreeSet::new()
                    };
                    let value = eval.arena.add(Flow {
                        borrowed,
                        location: READONLY,
                        locations,
                        owner: Some(*proc),
                        kind: kind(&typ),
                        typ,
                        ..Flow::default()
                    });
                    env.symbols.insert(symbol, value);
                }
            }
            let mut nodes = vec![];
            if purity.conditional.contains_key(&body) {
                nodes.push(body);
            } else {
                walk(eval.resolver, body, &mut nodes);
            }
            for (_, field) in eval.parameters(*proc) {
                if let Some(default) = eval.role(field, "default") {
                    if !nodes.contains(&default) {
                        walk(eval.resolver, default, &mut nodes);
                    }
                }
            }
            model.environments.insert(*proc, env);
            for node in &nodes {
                if eval.kind(*node) != "Call"
                    || eval.queried(*node)
                    || eval.builtin(*node).is_some()
                    || eval.approved(*node) && !crate::parallelcompile::is_call(eval.resolver,*node)
                    || eval.io_call(*node)
                    || eval
                        .role(*node, "callee")
                        .is_some_and(|callee| eval.resolver.denotes_type(callee) || matches!(eval.resolver.type_of(callee),Type::Function(..)|Type::Generic))
                {
                    continue;
                }
                let Some(callee) = eval.role(*node, "callee") else {
                    continue;
                };
                let parallel=crate::parallelcompile::is_call(eval.resolver,*node);
                let plans = if parallel {vec![]}else{eval.resolver.call_plans(*node, &Default::default())};
                let targets = if plans.is_empty() {
                    if parallel {vec![]}else{eval.resolver.candidates(callee)}
                } else {
                    plans.into_iter().map(|plan| plan.procedure).collect()
                };
                let mut targets = targets
                    .into_iter()
                    .filter(|target| {
                        eval.kind(*target) == "Procedure" && eval.role(*target, "body").is_some()
                    })
                    .collect::<BTreeSet<_>>();
                targets.extend(purity.targets.get(node).into_iter().flatten().copied());
                queue.extend(targets.iter().copied());
                model.targets.insert(*node, targets);
            }
            model.nodes.insert(*proc, nodes);
            model.stores.insert(*proc, BTreeMap::new());
            model.requirements.insert(*proc, BTreeSet::new());
        }
        let limit = (model.nodes.values().map(Vec::len).sum::<usize>() + 1).max(4);
        for _ in 0..limit {
            let mut changed = false;
            for proc in model.nodes.keys().copied().collect::<Vec<_>>() {
                changed |= model.pass(proc, resolver);
            }
            if !changed {
                model.converged = true;
                break;
            }
        }
        if model.converged {
            model.finish(resolver);
        }
        model
    }
    fn pass(&mut self, proc: Ref, resolver: &mut Resolver<'_>) -> bool {
        let mut changed = false;
        let mut env = self.environments.remove(&proc).unwrap_or_default();
        let mut eval = Evaluator {
            resolver,
            arena: &mut self.arena,
        };
        for node in self.nodes[&proc].clone() {
            if eval.queried(node) {
                continue;
            }
            let source = &eval.resolver.sources[node.file];
            let node_kind = source.node(node.node).kind.clone();
            let op = source.node(node.node).op.clone();
            if node_kind == "IncDec" || node_kind == "Assignment" && op != "in" {
                let targets = if node_kind == "Assignment" {
                    eval.group(node, "lhs")
                } else {
                    eval.role(node, "expr").into_iter().collect()
                };
                for target in targets {
                    let value = eval.flow(target, &mut env);
                    if !eval.arena.values[value].locations.is_empty() {
                        changed |= self.mutates.insert(proc);
                    }
                }
            }
            let (names, values) = if node_kind == "Declaration" {
                (eval.group(node, "names"), eval.group(node, "values"))
            } else if node_kind == "Assignment" && op == "=" {
                (eval.group(node, "lhs"), eval.group(node, "rhs"))
            } else {
                (vec![], vec![])
            };
            if node_kind == "Declaration" && values.is_empty() {
                for name in &names {
                    if let Some(symbol) = eval.resolver.symbol(*name) {
                        if let std::collections::hash_map::Entry::Vacant(slot) =
                            env.symbols.entry(symbol)
                        {
                            let typ = eval
                                .role(node, "type")
                                .map(|typ| eval.resolver.type_of(typ))
                                .unwrap_or(Type::Unknown);
                            let value = eval.arena.add(Flow {
                                owner: Some(proc),
                                kind: kind(&typ),
                                ..Flow::default()
                            });
                            slot.insert(value);
                            changed = true;
                        }
                    }
                }
            }
            let switch_tuple = values.first().is_some_and(|value| {
                let mut value = *value;
                while eval.kind(value) == "Paren" {
                    let Some(child) = eval.role(value, "expr") else {
                        break;
                    };
                    value = child;
                }
                matches!(eval.kind(value), "SwitchExpr" | "CompileSwitchExpr")
            });
            for (index, lhs) in names.into_iter().enumerate() {
                if eval.text(lhs) == "_" {
                    continue;
                }
                let rhs = if values.len() == 1 && switch_tuple {
                    values.first()
                } else {
                    values.get(index)
                };
                let Some(rhs) = rhs.copied() else {
                    continue;
                };
                let value = if values.len() == 1 {
                    eval.result_flow(rhs, index, &mut env)
                } else {
                    eval.flow(rhs, &mut env)
                };
                let expected=eval.resolver.type_of(lhs);
                let value = eval.copied_as(value, rhs,Some(expected));
                if node_kind == "Declaration" {
                    if let Some(symbol) = eval.resolver.symbol(lhs) {
                        if let Some(existing) = env.symbols.get(&symbol).copied() {
                            changed |= eval.arena.merge(existing, value);
                        } else {
                            let typ = eval.resolver.type_of(lhs);
                            let value = eval.arena.header_copy(
                                value,
                                typ,
                                Some(rhs),
                                Some(proc),
                                eval.resolver,
                            );
                            env.symbols.insert(symbol, value);
                            changed = true;
                        }
                    }
                } else {
                    let target = eval.flow(lhs, &mut env);
                    changed |= eval.arena.write(target, value);
                    changed |= store(
                        proc,
                        target,
                        value,
                        node,
                        eval.arena,
                        &mut self.stores,
                        &mut self.escapes,
                    );
                }
            }
            if node_kind == "Call" {
                let builtin = eval.builtin(node);
                if builtin.as_deref().is_some_and(|name| {
                    [
                        "append",
                        "clear",
                        "pop",
                        "pop_safe",
                        "pop_front",
                        "pop_front_safe",
                    ]
                    .contains(&name)
                }) {
                    if let Some(target) = eval.argument(node, "array", 0) {
                        let container = eval.flow(target, &mut env);
                        let container = eval.arena.deref(container, eval.resolver);
                        if !eval.arena.values[container].locations.is_empty() {
                            changed |= self.mutates.insert(proc);
                        }
                        if builtin.as_deref() == Some("append") {
                            let ellipsis = eval.resolver.sources[node.file]
                                .node(node.node)
                                .spans
                                .get("ellipsis")
                                .map(|span| span.end);
                            for argument in eval.group(node, "args") {
                                let value = if eval.kind(argument) == "FieldValue" {
                                    eval.role(argument, "value").unwrap_or(argument)
                                } else {
                                    argument
                                };
                                let label = eval
                                    .role(argument, "field")
                                    .map(|field| eval.text(field).to_string());
                                if value == target
                                    || label
                                        .as_deref()
                                        .is_some_and(|name| ["loc", "allocator"].contains(&name))
                                {
                                    continue;
                                }
                                let spread = eval.kind(value) == "Unary"
                                    && eval.resolver.sources[value.file].node(value.node).op
                                        == "**";
                                let base = if spread {
                                    eval.role(value, "expr").unwrap_or(value)
                                } else {
                                    value
                                };
                                let flow = eval.flow(base, &mut env);
                                let typ = eval.resolver.type_of(base);
                                let expanded = spread
                                    || ellipsis.is_some_and(|offset| {
                                        eval.resolver.sources[value.file]
                                            .node(value.node)
                                            .span
                                            .start
                                            >= offset
                                    })
                                    || label.as_deref() == Some("args");
                                let fields = eval.resolver.record_fields(&typ);
                                let incoming = if expanded && !fields.is_empty() {
                                    fields
                                        .into_iter()
                                        .map(|(name, typ)| {
                                            (eval.arena.member(flow, &name, eval.resolver), typ)
                                        })
                                        .collect::<Vec<_>>()
                                } else if expanded && typ.is_unknown() {
                                    let mut names = BTreeSet::new();
                                    for source in eval.resolver.sources {
                                        for record in &source.nodes {
                                            if record.kind == "StructType" {
                                                if let Some(fields) = record.roles.get("fields") {
                                                    for field in &source.node(*fields).children {
                                                        if source.node(*field).kind == "Field" {
                                                            names.extend(
                                                                source
                                                                    .group(*field, "names")
                                                                    .iter()
                                                                    .map(|name| {
                                                                        source
                                                                            .text(*name)
                                                                            .to_string()
                                                                    }),
                                                            );
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    let mut values = vec![(eval.arena.item(flow), typ.clone())];
                                    values.extend(names.into_iter().map(|name| {
                                        (eval.arena.member(flow, &name, eval.resolver), typ.clone())
                                    }));
                                    values
                                } else if expanded {
                                    vec![(
                                        eval.arena.item(flow),
                                        typ.element().cloned().unwrap_or(typ.clone()),
                                    )]
                                } else {
                                    vec![(eval.copied(flow, value), typ)]
                                };
                                for (incoming, element_type) in incoming {
                                    let incoming = eval.arena.header_copy(
                                        incoming,
                                        element_type,
                                        Some(value),
                                        None,
                                        eval.resolver,
                                    );
                                    let element = eval.arena.item(container);
                                    changed |= eval.arena.write(element, incoming);
                                    changed |= store(
                                        proc,
                                        element,
                                        incoming,
                                        node,
                                        eval.arena,
                                        &mut self.stores,
                                        &mut self.escapes,
                                    );
                                }
                            }
                        }
                    }
                }
                if builtin
                    .as_deref()
                    .is_some_and(|name| name == "copy" || name == "delete")
                {
                    let target = if builtin.as_deref() == Some("copy") {
                        eval.argument(node, "dst", 0)
                    } else {
                        eval.argument(node, "array", 0)
                            .or_else(|| eval.argument(node, "str", 0))
                            .or_else(|| eval.argument(node, "m", 0))
                    };
                    if let Some(target) = target {
                        let value = eval.flow(target, &mut env);
                        if !eval.arena.values[value].origins.is_empty() {
                            changed |= self.mutates.insert(proc);
                        }
                    }
                }
                if builtin.as_deref() == Some("copy") {
                    if let (Some(dst), Some(src)) =
                        (eval.argument(node, "dst", 0), eval.argument(node, "src", 1))
                    {
                        let target = eval.flow(dst, &mut env);
                        let target = eval.arena.item(target);
                        let value = eval.flow(src, &mut env);
                        let value = eval.arena.item(value);
                        changed |= eval.arena.write(target, value);
                        changed |= store(
                            proc,
                            target,
                            value,
                            node,
                            eval.arena,
                            &mut self.stores,
                            &mut self.escapes,
                        );
                    }
                } else if let Some(targets) = self.targets.get(&node) {
                    for target in targets {
                        let Some(stores) = self
                            .stores
                            .get(target)
                            .map(|stores| stores.values().cloned().collect::<Vec<_>>())
                        else {
                            continue;
                        };
                        if self.mutates.contains(target) {
                            changed |= self.mutates.insert(proc);
                        }
                        let bindings = eval.bindings(node, *target, &mut env);
                        let cache = self.instances.entry((node, *target)).or_default();
                        for stored in stores {
                            let destination = eval.project_access(&bindings, &stored.target);
                            let (incoming, instance_changed) =
                                eval.instantiate(stored.value, &bindings, cache);
                            changed |= instance_changed;
                            changed |= eval.arena.write(destination, incoming);
                            changed |= store(
                                proc,
                                destination,
                                incoming,
                                node,
                                eval.arena,
                                &mut self.stores,
                                &mut self.escapes,
                            );
                        }
                    }
                }
            }
            if matches!(node_kind.as_str(), "Range" | "TypeSwitch") {
                if let (Some(name), Some(expr)) = (
                    eval.group(node, "names").first().copied(),
                    eval.role(node, "expr"),
                ) {
                    let mut value = eval.flow(expr, &mut env);
                    if node_kind == "Range" {
                        value = eval.arena.item(value);
                    }
                    let reference = eval.kind(name) == "Unary"
                        && eval.resolver.sources[name.file].node(name.node).op == "&";
                    let symbol = eval.resolver.symbol(name).or_else(|| {
                        eval.resolver
                            .lookup(name, eval.text(name).trim_start_matches('&'))
                    });
                    if let Some(symbol) = symbol {
                        if let Some(existing) = env.symbols.get(&symbol).copied() {
                            changed |= eval.arena.merge(existing, value);
                        } else {
                            if !reference {
                                let typ = eval.resolver.type_of(name);
                                value = eval.arena.header_copy(
                                    value,
                                    typ,
                                    Some(name),
                                    None,
                                    eval.resolver,
                                );
                                eval.arena.values[value].location = READONLY;
                            }
                            env.symbols.insert(symbol, value);
                            changed = true;
                        }
                    }
                }
            }
        }
        self.environments.insert(proc, env);
        changed
    }
    fn finish(&mut self, resolver: &mut Resolver<'_>) {
        for (proc, nodes) in &self.nodes {
            let env = self.environments.get_mut(proc).unwrap();
            let mut eval = Evaluator {
                resolver,
                arena: &mut self.arena,
            };
            for node in nodes {
                if eval.queried(*node) {
                    continue;
                }
                let source = &eval.resolver.sources[node.file];
                let n = source.node(node.node);
                let targets = if n.kind == "Assignment" && n.op != "in" {
                    eval.group(*node, "lhs")
                } else if n.kind == "IncDec" {
                    eval.role(*node, "expr").into_iter().collect()
                } else {
                    vec![]
                };
                let mut permission = crate::permissions::OWNED;
                for target in targets {
                    permission = permission.join(&eval.borrowed_write(target, env));
                }
                if eval
                    .builtin(*node)
                    .as_deref()
                    .is_some_and(|name| name == "copy" || name == "delete")
                {
                    let copy = eval.builtin(*node).as_deref() == Some("copy");
                    let target = if copy {
                        eval.argument(*node, "dst", 0)
                    } else {
                        eval.argument(*node, "array", 0)
                            .or_else(|| eval.argument(*node, "str", 0))
                            .or_else(|| eval.argument(*node, "m", 0))
                    };
                    if let Some(target) = target {
                        permission = permission.join(&eval.borrowed_consumption(target, env));
                    }
                }
                if !permission.is_owned() {
                    self.requirements
                        .get_mut(proc)
                        .unwrap()
                        .extend(permission.accesses());
                    self.checks.insert(*node, permission);
                }
            }
        }
        for _ in 0..(self.nodes.len() * 25 + 1).max(64) {
            let mut changed = false;
            for (proc, nodes) in &self.nodes {
                let env = self.environments.get_mut(proc).unwrap();
                let mut eval = Evaluator {
                    resolver,
                    arena: &mut self.arena,
                };
                for node in nodes {
                    if eval.queried(*node) {
                        continue;
                    }
                    let Some(targets) = self.targets.get(node) else {
                        continue;
                    };
                    for target in targets {
                        let Some(requirements) = self.requirements.get(target).cloned() else {
                            continue;
                        };
                        let bindings = eval.bindings(*node, *target, env);
                        for access in requirements {
                            for needed in eval.resolve_access(&bindings, &access).accesses() {
                                if needed.procedure == *proc && needed.path.len() <= MAX_PATH {
                                    changed |=
                                        self.requirements.get_mut(proc).unwrap().insert(needed);
                                }
                            }
                        }
                    }
                }
            }
            if !changed {
                return;
            }
        }
        self.converged = false;
    }
}
