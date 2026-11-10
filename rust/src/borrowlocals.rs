//! First reference fixed-point driver: declared fn bodies without ordinary
//! checked-helper stores. Unsupported call effects remain with the existing
//! analysis until the reference binding/store propagation is connected.
use crate::{
    borroweval::{Environment, Evaluator},
    borrowflow::{Arena, Flow, Kind},
    permissions::{Permission, READONLY},
    purity,
    semantics::{Ref, Resolver, Type},
};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Default)]
pub struct Facts {
    pub covered: BTreeSet<Ref>,
    pub checks: BTreeMap<Ref, Permission>,
}
fn walk(resolver: &Resolver<'_>, node: Ref, nodes: &mut Vec<Ref>) {
    nodes.push(node);
    for child in &resolver.sources[node.file].node(node.node).children {
        if resolver.sources[node.file].node(*child).kind != "Procedure" {
            walk(
                resolver,
                Ref {
                    file: node.file,
                    node: *child,
                },
                nodes,
            );
        }
    }
}
fn kind(typ: &Type) -> Kind {
    match typ {
        Type::Nominal(_, inner) => kind(inner),
        Type::Unknown => Kind::Unknown,
        Type::Generic => Kind::Generic,
        Type::Pointer(_) => Kind::Pointer,
        Type::Array { .. } => Kind::Array,
        Type::Fixed { .. } => Kind::Fixed,
        _ => Kind::Other,
    }
}
impl Facts {
    pub fn analyze(resolver: &mut Resolver<'_>, purity: &purity::Facts) -> Self {
        let roots = purity
            .checked
            .iter()
            .copied()
            .filter(|root| resolver.sources[root.file].node(root.node).data["pure"] == true)
            .collect::<Vec<_>>();
        let mut facts = Self::default();
        for root in roots {
            let mut arena = Arena::default();
            let mut env = Environment::default();
            let mut eval = Evaluator {
                resolver,
                arena: &mut arena,
            };
            let Some(signature) = eval.role(root, "type") else {
                continue;
            };
            let Some(body) = eval.role(root, "body") else {
                continue;
            };
            let mut nodes = vec![];
            walk(eval.resolver, body, &mut nodes);
            if let Some(params) = eval.role(signature, "params") {
                for field in eval.resolver.sources[params.file]
                    .node(params.node)
                    .children
                    .clone()
                {
                    if let Some(default) = eval.role(
                        Ref {
                            file: root.file,
                            node: field,
                        },
                        "default",
                    ) {
                        walk(eval.resolver, default, &mut nodes);
                    }
                }
            }
            let mut covered = true;
            for node in &nodes {
                if eval.queried(*node) {
                    continue;
                }
                let source = &eval.resolver.sources[node.file];
                if matches!(source.node(node.node).kind.as_str(), "Range" | "TypeSwitch")
                    || source.node(node.node).kind == "Unary" && source.node(node.node).op == "**"
                {
                    covered = false;
                    break;
                }
                if source.node(node.node).kind != "Call" {
                    continue;
                }
                let builtin = eval.builtin(*node);
                let mutating = builtin.as_deref().is_some_and(|name| {
                    [
                        "append",
                        "clear",
                        "copy",
                        "pop",
                        "pop_safe",
                        "pop_front",
                        "pop_front_safe",
                    ]
                    .contains(&name)
                });
                let pure_call = purity.targets.get(node).is_some_and(|targets| {
                    !targets.is_empty()
                        && targets.iter().all(|target| {
                            eval.resolver.sources[target.file].node(target.node).data["pure"]
                                == true
                        })
                });
                let conversion = eval
                    .role(*node, "callee")
                    .is_some_and(|callee| eval.resolver.denotes_type(callee));
                if mutating || builtin.is_none() && !conversion && !pure_call {
                    covered = false;
                    break;
                }
            }
            if !covered {
                continue;
            }
            for field in eval
                .role(signature, "params")
                .into_iter()
                .flat_map(|params| {
                    eval.resolver.sources[params.file]
                        .node(params.node)
                        .children
                        .clone()
                })
                .collect::<Vec<_>>()
            {
                let field = Ref {
                    file: root.file,
                    node: field,
                };
                for name in eval.group(field, "names") {
                    if let Some(symbol) = eval.resolver.symbol(name) {
                        let typ = eval
                            .role(field, "type")
                            .map(|typ| eval.resolver.type_of(typ))
                            .unwrap_or(Type::Unknown);
                        let value = eval.arena.add(Flow {
                            borrowed: READONLY,
                            location: READONLY,
                            owner: Some(root),
                            kind: kind(&typ),
                            typ,
                            ..Flow::default()
                        });
                        env.symbols.insert(symbol, value);
                    }
                }
            }
            let mut converged = false;
            for _ in 0..(nodes.len() + 1).max(4) {
                let mut changed = false;
                for node in &nodes {
                    if eval.queried(*node) {
                        continue;
                    }
                    let source = &eval.resolver.sources[node.file];
                    let node_kind = source.node(node.node).kind.clone();
                    let assignment = node_kind == "Assignment" && source.node(node.node).op == "=";
                    let (names, values) = if node_kind == "Declaration" {
                        (eval.group(*node, "names"), eval.group(*node, "values"))
                    } else if assignment {
                        (eval.group(*node, "lhs"), eval.group(*node, "rhs"))
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
                                        .role(*node, "type")
                                        .map(|typ| eval.resolver.type_of(typ))
                                        .unwrap_or(Type::Unknown);
                                    let value = eval.arena.add(Flow {
                                        owner: Some(root),
                                        kind: kind(&typ),
                                        ..Flow::default()
                                    });
                                    slot.insert(value);
                                    changed = true;
                                }
                            }
                        }
                    }
                    for (index, lhs) in names.iter().enumerate() {
                        if eval.resolver.sources[lhs.file].text(lhs.node) == "_" {
                            continue;
                        }
                        let rhs = if values.len() == 1 {
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
                        let value = eval.copied(value, rhs);
                        if node_kind == "Declaration" {
                            if let Some(symbol) = eval.resolver.symbol(*lhs) {
                                if let Some(existing) = env.symbols.get(&symbol).copied() {
                                    changed |= eval.arena.merge(existing, value);
                                } else {
                                    let typ = eval.resolver.type_of(*lhs);
                                    let value = eval.arena.header_copy(
                                        value,
                                        typ,
                                        Some(rhs),
                                        Some(root),
                                        eval.resolver,
                                    );
                                    env.symbols.insert(symbol, value);
                                    changed = true;
                                }
                            }
                        } else {
                            let target = eval.flow(*lhs, &mut env);
                            changed |= eval.arena.write(target, value);
                        }
                    }
                    if matches!(node_kind.as_str(), "Range" | "TypeSwitch") {
                        if let Some(name) = eval.group(*node, "names").first().copied() {
                            if let Some(expr) = eval.role(*node, "expr") {
                                let mut value = eval.flow(expr, &mut env);
                                if node_kind == "Range" {
                                    value = eval.arena.item(value);
                                }
                                let reference = eval.resolver.sources[name.file]
                                    .node(name.node)
                                    .kind
                                    == "Unary"
                                    && eval.resolver.sources[name.file].node(name.node).op == "&";
                                if let Some(symbol) = eval.resolver.symbol(name) {
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
                }
                if !changed {
                    converged = true;
                    break;
                }
            }
            if !converged {
                continue;
            }
            for node in nodes {
                if eval.queried(node) {
                    continue;
                }
                let source = &eval.resolver.sources[node.file];
                let targets = match source.node(node.node).kind.as_str() {
                    "Assignment" if source.node(node.node).op != "in" => eval.group(node, "lhs"),
                    "IncDec" => eval.role(node, "expr").into_iter().collect(),
                    _ => vec![],
                };
                let mut checks = crate::permissions::OWNED;
                for lhs in targets {
                    checks = checks.join(&eval.storage(lhs, &mut env));
                }
                if eval.builtin(node).as_deref() == Some("delete") {
                    let target = eval
                        .argument(node, "array", 0)
                        .or_else(|| eval.argument(node, "str", 0));
                    if let Some(target) = target {
                        let value = eval.flow(target, &mut env);
                        checks = checks.join(&eval.arena.values[value].borrowed);
                    }
                }
                if !checks.is_owned() {
                    facts.checks.insert(node, checks);
                }
            }
            facts.covered.insert(root);
        }
        facts
    }
    pub fn covers(&self, resolver: &Resolver<'_>, site: Ref) -> bool {
        resolver.sources[site.file]
            .ancestors(site.node)
            .find(|node| resolver.sources[site.file].node(*node).kind == "Procedure")
            .is_some_and(|node| {
                self.covered.contains(&Ref {
                    file: site.file,
                    node,
                })
            })
    }
}
