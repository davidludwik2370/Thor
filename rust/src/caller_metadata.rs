//! Automatic caller metadata is part of a procedure's source-level contract.
//! Native defaults carry strings/locations without allocating at runtime.
use crate::{
    ast::{NodeId, Source},
    diagnostic::{Error, Result},
    semantics::{Ref, Resolver, Substitutions, Type},
};

pub(crate) fn params(source: &Source, signature: NodeId) -> Vec<(NodeId, NodeId)> {
    source
        .role(signature, "params")
        .into_iter()
        .flat_map(|list| source.node(list).children.iter())
        .filter(|field| source.node(**field).kind == "Field")
        .flat_map(|field| {
            source
                .group(*field, "names")
                .iter()
                .map(move |name| (*name, *field))
        })
        .collect()
}
pub(crate) fn automatic(source: &Source, field: NodeId) -> bool {
    source.node(field).data["automatic"] == true
}
fn descriptor(source: &Source, field: NodeId, fields: &[(NodeId, NodeId)]) -> Option<String> {
    if !automatic(source, field) {
        return None;
    }
    let default = source.role(field, "default")?;
    if source.text(default) == "#caller_location" {
        return Some("location".into());
    }
    if source.text(default) == "#caller_expression" {
        return Some("call".into());
    }
    let args = source.group(default, "args");
    if source
        .role(default, "callee")
        .is_some_and(|callee| source.text(callee) == "#caller_expression")
        && args.len() == 1
        && source.node(args[0]).kind == "Ident"
    {
        let index = fields.iter().position(|(name, _)| {
            source.text(*name).trim_start_matches('$') == source.text(args[0])
        })?;
        return Some(format!("argument:{index}"));
    }
    None
}
fn contract(source: &Source, signature: NodeId) -> Vec<Option<String>> {
    let fields = params(source, signature);
    fields
        .iter()
        .map(|(_, field)| descriptor(source, *field, &fields))
        .collect()
}
fn procedure(typ: &Type) -> Option<Ref> {
    match typ {
        Type::Procedure(r) => Some(*r),
        Type::Nominal(_, inner) => procedure(inner),
        _ => None,
    }
}
fn is_ro(source: &Source, name: NodeId) -> bool {
    source.info["ro_parameters"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|row| row[0].as_u64() == Some(source.node(name).span.start as u64))
}
fn conversion(
    resolver: &mut Resolver<'_>,
    expected: &Type,
    actual: Ref,
    bindings: &Substitutions,
    permissions: Option<&crate::privileges::Facts>,
) -> Result<()> {
    if matches!(
        expected,
        Type::Scalar { .. }
            | Type::String { .. }
            | Type::Enum(_)
            | Type::Constant(_)
            | Type::BigInteger
            | Type::BigRational
            | Type::Unknown
            | Type::Generic
    ) {
        return Ok(());
    }
    let source = &resolver.sources[actual.file];
    if matches!(
        source.node(actual.node).kind.as_str(),
        "Paren" | "AutoCast" | "Runtime"
    ) {
        if let Some(node) = source.role(actual.node, "expr") {
            return conversion(
                resolver,
                expected,
                Ref { node, ..actual },
                bindings,
                permissions,
            );
        }
    }
    // A literal's contextual type already names the destination callback type.
    // Check its actual elements before that contextual conversion hides them.
    if source.node(actual.node).kind == "Compound" {
        let elems = source.group(actual.node, "elems").to_vec();
        let fields = resolver.record_fields(expected);
        let mut base = expected;
        while let Type::Nominal(_, inner) = base {
            base = inner;
        }
        for (index, item) in elems.into_iter().enumerate() {
            let source = &resolver.sources[actual.file];
            let value = source.role(item, "value").unwrap_or(item);
            let label = source
                .role(item, "field")
                .map(|id| source.text(id).to_string());
            let typ = if let Type::Map { key, value: typ } = base {
                if let Some(key_node) = source.role(item, "field") {
                    conversion(
                        resolver,
                        key,
                        Ref {
                            node: key_node,
                            ..actual
                        },
                        bindings,
                        permissions,
                    )?;
                }
                Some(typ.as_ref().clone())
            } else if let Some(elem) = base.element() {
                Some(elem.clone())
            } else {
                label
                    .and_then(|name| fields.iter().find(|(key, _)| key == &name))
                    .or_else(|| fields.get(index))
                    .map(|(_, typ)| typ.clone())
            };
            if let Some(typ) = typ {
                conversion(
                    resolver,
                    &typ,
                    Ref {
                        node: value,
                        ..actual
                    },
                    bindings,
                    permissions,
                )?;
            }
        }
    }
    let actual_type = resolver.specialized_type(actual, bindings);
    compare_types(
        resolver,
        expected,
        &actual_type,
        actual,
        permissions,
        true,
        &mut std::collections::HashSet::new(),
    )
}
fn compare_types(
    resolver: &mut Resolver<'_>,
    expected: &Type,
    actual_type: &Type,
    actual: Ref,
    permissions: Option<&crate::privileges::Facts>,
    direct: bool,
    seen: &mut std::collections::HashSet<(Type, Type)>,
) -> Result<()> {
    if !seen.insert((expected.clone(), actual_type.clone())) {
        return Ok(());
    }
    if let Type::Nominal(_, inner) = expected {
        return compare_types(
            resolver,
            inner,
            actual_type,
            actual,
            permissions,
            direct,
            seen,
        );
    }
    if let Type::Nominal(_, inner) = actual_type {
        return compare_types(resolver, expected, inner, actual, permissions, direct, seen);
    }
    if let (Some(expected), Some(actual_signature)) = (procedure(expected), procedure(actual_type))
    {
        let expected_source = &resolver.sources[expected.file];
        let actual_source = &resolver.sources[actual_signature.file];
        let expected_contract = contract(expected_source, expected.node);
        let actual_contract = contract(actual_source, actual_signature.node);
        if (expected_contract.iter().any(Option::is_some)
            || actual_contract.iter().any(Option::is_some))
            && expected_contract != actual_contract
        {
            return Err(Error::at(&resolver.sources[actual.file],resolver.sources[actual.file].node(actual.node).span,
                "Incompatible automatic caller metadata parameters in procedure conversion. Preserve each :> metadata parameter and its capture target in the callback type."));
        }
        if let Some(facts) = permissions {
            for ((expected_name, _), (actual_name, _)) in params(expected_source, expected.node)
                .iter()
                .zip(params(actual_source, actual_signature.node))
            {
                if !is_ro(expected_source, *expected_name) || is_ro(actual_source, actual_name) {
                    continue;
                }
                let inspectable = actual_source
                    .node(actual_signature.node)
                    .parent
                    .is_some_and(|p| {
                        actual_source.node(p).kind == "Procedure"
                            && actual_source.role(p, "body").is_some()
                    });
                if !direct
                    || !inspectable
                    || !fixed_procedure_value(
                        resolver,
                        actual,
                        &mut std::collections::HashSet::new(),
                    )
                    || !facts.verified_parameters.contains(&Ref {
                        file: actual_signature.file,
                        node: actual_name,
                    })
                    || facts.mutating.contains(&Ref {
                        file: actual_signature.file,
                        node: actual_name,
                    })
                {
                    return Err(Error::at(&resolver.sources[actual.file],resolver.sources[actual.file].node(actual.node).span,
                        "A #ro callback parameter requires a procedure that preserves read-only slice access; this procedure may mutate or expose that input."));
                }
            }
        }
        // Callback inputs/results can themselves contain procedure values.
        let expected_fields = resolver
            .fields(expected, "params")
            .into_iter()
            .chain(resolver.fields(expected, "results"))
            .collect::<Vec<_>>();
        let actual_fields = resolver
            .fields(actual_signature, "params")
            .into_iter()
            .chain(resolver.fields(actual_signature, "results"))
            .collect::<Vec<_>>();
        for ((_, left), (_, right)) in expected_fields.into_iter().zip(actual_fields) {
            if let (Some(left), Some(right)) = (left, right) {
                let a = resolver.specialized_type(left, &Substitutions::new());
                let b = resolver.specialized_type(right, &Substitutions::new());
                compare_types(resolver, &a, &b, actual, permissions, false, seen)?;
            }
        }
    } else if let (Type::Map { key: a, value: b }, Type::Map { key: c, value: d }) =
        (expected, actual_type)
    {
        compare_types(resolver, a, c, actual, permissions, false, seen)?;
        compare_types(resolver, b, d, actual, permissions, false, seen)?;
    } else if let (Some(a), Some(b)) = (expected.element(), actual_type.element()) {
        compare_types(resolver, a, b, actual, permissions, false, seen)?;
    } else if expected.record_like() && actual_type.record_like() {
        let a = resolver.record_fields(expected);
        let b = resolver.record_fields(actual_type);
        for ((_, a), (_, b)) in a.iter().zip(&b) {
            compare_types(resolver, a, b, actual, permissions, false, seen)?;
        }
    }
    Ok(())
}
fn active(resolver: &mut Resolver<'_>, at: Ref, bindings: &Substitutions) -> bool {
    let source = &resolver.sources[at.file];
    let conditions = source
        .ancestors(at.node)
        .filter(|id| source.node(*id).kind == "When")
        .collect::<Vec<_>>();
    for owner in conditions {
        let Some(cond) = source.role(owner, "cond") else {
            continue;
        };
        let Some(selected) = resolver.context_condition(Ref { node: cond, ..at }, bindings) else {
            continue;
        };
        let branch = source.role(owner, if selected { "body" } else { "else" });
        if !branch
            .is_some_and(|branch| source.node(branch).span.contains(source.node(at.node).span))
        {
            return false;
        }
    }
    true
}
pub(crate) fn validate(resolver: &mut Resolver<'_>) -> Result<()> {
    validate_with_permissions(resolver, None)
}
pub(crate) fn validate_with_permissions(
    resolver: &mut Resolver<'_>,
    permissions: Option<&crate::privileges::Facts>,
) -> Result<()> {
    let enabled = resolver.sources.iter().any(|s| {
        s.thor
            && !s.excluded
            && (s.info["auto_parameters"]
                .as_array()
                .is_some_and(|p| !p.is_empty())
                || permissions.is_some()
                    && s.info["ro_parameters"]
                        .as_array()
                        .is_some_and(|p| !p.is_empty()))
    });
    if !enabled {
        return Ok(());
    }
    for file in 0..resolver.sources.len() {
        let source = &resolver.sources[file];
        if source.excluded {
            continue;
        }
        for (i, node) in source
            .nodes
            .iter()
            .enumerate()
            .filter(|(_, n)| n.kind == "ProcedureType")
        {
            let signature = NodeId(i);
            if !resolver.active_definition(Ref {
                file,
                node: signature,
            }) {
                continue;
            }
            let fields = params(source, signature);
            let mut seen_auto = false;
            for (index, (name, field)) in fields.iter().enumerate() {
                if !automatic(source, *field) {
                    if seen_auto {
                        return Err(Error::at(source,source.node(*name).span,"Automatic :> caller metadata parameters must follow ordinary input parameters."));
                    }
                    continue;
                }
                seen_auto = true;
                let problem = if source.text(*name) == "_" {
                    Some("Automatic caller metadata requires a named parameter.")
                } else if descriptor(source, *field, &fields).is_none() {
                    Some("A :> parameter requires #caller_expression, #caller_expression(earlier_parameter), or #caller_location.")
                } else {
                    let default = source.role(*field, "default").unwrap();
                    let args = source.group(default, "args");
                    if let Some(target) = args.first() {
                        let earlier = fields[..index].iter().any(|(n, f)| {
                            source.text(*n).trim_start_matches('$') == source.text(*target)
                                && !automatic(source, *f)
                        });
                        if !earlier {
                            Some("#caller_expression in a :> parameter must name an earlier ordinary input parameter.")
                        } else {
                            None
                        }
                    } else {
                        None
                    }
                };
                if let Some(problem) = problem {
                    return Err(Error::at(source, source.node(*field).span, problem));
                }
            }
            if !seen_auto {
                continue;
            }
            if !matches!(
                node.data["calling_convention"].as_str().unwrap_or(""),
                "" | "odin" | "contextless"
            ) || source
                .ancestors(signature)
                .any(|id| source.node(id).kind == "ForeignBlock")
            {
                return Err(Error::at(source,node.span,"Automatic :> caller metadata parameters cannot be used with a foreign calling convention or foreign declaration."));
            }
            if let Some(proc) = node.parent.filter(|p| source.node(*p).kind == "Procedure") {
                if let Some(decl) = source
                    .node(proc)
                    .parent
                    .filter(|p| source.node(*p).kind == "Declaration")
                {
                    let auto_entry = source
                        .group(decl, "names")
                        .iter()
                        .any(|name| source.text(*name) == "main")
                        && source.names.get(source.node(NodeId(0)).name) == "main";
                    let retained = source
                        .group(decl, "attributes")
                        .iter()
                        .flat_map(|a| source.group(*a, "elems"))
                        .any(|item| {
                            let key = source.text(source.role(*item, "field").unwrap_or(*item));
                            matches!(key, "export" | "init" | "fini" | "test")
                                && source
                                    .role(*item, "value")
                                    .is_none_or(|value| source.text(value) != "false")
                        });
                    if auto_entry || retained {
                        return Err(Error::at(source,node.span,"Automatic :> caller metadata parameters cannot be used on exported or automatically invoked entry points."));
                    }
                }
            }
        }
    }
    // Ordinary source is visited once. Only specializations carrying a callback
    // contract need another pass; SDK templates otherwise retain their fast path.
    let mut pending = vec![];
    for (file, source) in resolver.sources.iter().enumerate() {
        if !source.excluded && !source.shared_native {
            pending.push((file, None, Substitutions::new()));
        }
    }
    let mut visited = std::collections::HashSet::new();
    while let Some((file, body, bindings)) = pending.pop() {
        if !visited.insert((file, body, bindings.clone())) {
            continue;
        }
        let nodes = if let Some(body) = body {
            resolver.sources[file].descendants(body).collect::<Vec<_>>()
        } else {
            (0..resolver.sources[file].nodes.len())
                .map(NodeId)
                .collect()
        };
        let source = &resolver.sources[file];
        let generic_bodies = source
            .nodes
            .iter()
            .enumerate()
            .filter(|(_, n)| n.kind == "Procedure")
            .filter_map(|(i, _)| {
                let signature = source.role(NodeId(i), "type")?;
                source
                    .descendants(signature)
                    .any(|id| source.node(id).kind == "PolyType")
                    .then(|| source.role(NodeId(i), "body"))
                    .flatten()
                    .map(|body| (NodeId(i), body))
            })
            .collect::<std::collections::HashMap<_, _>>();
        for node in nodes {
            let at = Ref { file, node };
            let source = &resolver.sources[file];
            if source
                .ancestors(node)
                .find_map(|owner| {
                    generic_bodies
                        .get(&owner)
                        .copied()
                        .filter(|scope| source.node(*scope).span.contains(source.node(node).span))
                })
                .is_some_and(|scope| Some(scope) != body)
            {
                continue;
            }
            if !active(resolver, at, &bindings) {
                continue;
            }
            let kind = resolver.sources[file].node(node).kind.as_str().to_string();
            if kind == "Call" {
                let Some(callee) = resolver.sources[file].role(node, "callee") else {
                    continue;
                };
                let args = resolver.sources[file].group(node, "args").to_vec();
                if resolver.denotes_type(Ref { file, node: callee }) {
                    let expected = resolver.specialized_type(Ref { file, node: callee }, &bindings);
                    if args.len() == 1 {
                        conversion(
                            resolver,
                            &expected,
                            Ref {
                                file,
                                node: args[0],
                            },
                            &bindings,
                            permissions,
                        )?;
                    }
                    continue;
                }
                let plans = resolver.call_plans(at, &bindings);
                for plan in &plans {
                    if !plan.bindings.is_empty() {
                        if let Some(body) =
                            resolver.sources[plan.procedure.file].role(plan.procedure.node, "body")
                        {
                            if !resolver.sources[plan.procedure.file].shared_native {
                                pending.push((
                                    plan.procedure.file,
                                    Some(body),
                                    plan.bindings.clone(),
                                ));
                            }
                        }
                    }
                }
                let typ = resolver.specialized_type(Ref { file, node: callee }, &bindings);
                let signature = if plans.len() == 1 {
                    Some(plans[0].signature)
                } else {
                    procedure(&typ)
                };
                let Some(sig) = signature else {
                    continue;
                };
                let bound = if plans.len() == 1 {
                    &plans[0].bindings
                } else {
                    &bindings
                };
                let fields = params(&resolver.sources[sig.file], sig.node);
                let source = &resolver.sources[file];
                let receiver =
                    source.node(callee).kind == "Selector" && source.node(callee).op == "->";
                let mut index = usize::from(receiver);
                for arg in args {
                    let source = &resolver.sources[file];
                    let position = if source.node(arg).kind == "FieldValue" {
                        let label = source.role(arg, "field").unwrap();
                        fields.iter().position(|(name, _)| {
                            resolver.sources[sig.file]
                                .text(*name)
                                .trim_start_matches('$')
                                == source.text(label)
                        })
                    } else {
                        let position = Some(index);
                        if !fields.get(index).is_some_and(|(_, field)| {
                            resolver.sources[sig.file].node(*field).data["variadic"] == true
                        }) {
                            index += 1;
                        }
                        position
                    };
                    if let Some((name, field)) = position.and_then(|index| fields.get(index)) {
                        if automatic(&resolver.sources[sig.file], *field) {
                            return Err(Error::at(source,source.node(arg).span,format!("Caller metadata parameter '{}' is automatic (:>) and cannot be supplied explicitly.",resolver.sources[sig.file].text(*name))));
                        }
                        let value = source.role(arg, "value").unwrap_or(arg);
                        if let Some(annotation) = resolver.sources[sig.file].role(*field, "type") {
                            let expected = resolver.specialized_type(
                                Ref {
                                    file: sig.file,
                                    node: annotation,
                                },
                                bound,
                            );
                            conversion(
                                resolver,
                                &expected,
                                Ref { file, node: value },
                                &bindings,
                                permissions,
                            )?;
                        }
                    }
                }
            } else if matches!(kind.as_str(), "Declaration" | "Compound" | "Cast") {
                let annotation = resolver.sources[file].role(node, "type");
                if let Some(annotation) = annotation {
                    let expected = resolver.specialized_type(
                        Ref {
                            file,
                            node: annotation,
                        },
                        &bindings,
                    );
                    let values = if kind == "Declaration" {
                        resolver.sources[file].group(node, "values").to_vec()
                    } else if kind == "Compound" {
                        vec![node]
                    } else {
                        resolver.sources[file]
                            .role(node, "expr")
                            .into_iter()
                            .collect()
                    };
                    for value in values {
                        conversion(
                            resolver,
                            &expected,
                            Ref { file, node: value },
                            &bindings,
                            permissions,
                        )?;
                    }
                }
            } else if kind == "Assignment" {
                let source = &resolver.sources[file];
                let pairs = source
                    .group(node, "lhs")
                    .iter()
                    .copied()
                    .zip(source.group(node, "rhs").iter().copied())
                    .collect::<Vec<_>>();
                for (lhs, rhs) in pairs {
                    let expected = resolver.specialized_type(Ref { file, node: lhs }, &bindings);
                    conversion(
                        resolver,
                        &expected,
                        Ref { file, node: rhs },
                        &bindings,
                        permissions,
                    )?;
                }
            } else if kind == "Return" {
                let source = &resolver.sources[file];
                let owner = source
                    .ancestors(node)
                    .find(|id| source.node(*id).kind == "Procedure");
                let Some(signature) = owner.and_then(|p| source.role(p, "type")) else {
                    continue;
                };
                let values = source.group(node, "values").to_vec();
                let results = resolver.fields(
                    Ref {
                        file,
                        node: signature,
                    },
                    "results",
                );
                for (value, (_, annotation)) in values.into_iter().zip(results) {
                    if let Some(annotation) = annotation {
                        let expected = resolver.specialized_type(annotation, &bindings);
                        conversion(
                            resolver,
                            &expected,
                            Ref { file, node: value },
                            &bindings,
                            permissions,
                        )?;
                    }
                }
            }
        }
    }
    Ok(())
}

/// Caller defaults are supplied by the call site, not executed as a callee body.
pub(crate) fn in_default(source: &Source, node: NodeId) -> bool {
    source.ancestors(node).any(|field| {
        source.node(field).kind == "Field"
            && source.node(field).parent.is_some_and(|list| {
                source.node(list).parent.is_some_and(|sig| {
                    source.node(sig).kind == "ProcedureType"
                        && source.role(sig, "params") == Some(list)
                })
            })
            && source.role(field, "default").is_some_and(|default| {
                source.node(default).span.contains(source.node(node).span)
                    && (source.text(default) == "#caller_location"
                        || source.text(default).starts_with("#caller_expression"))
            })
    })
}

/// Only immutable, resolved procedure values can establish a reader proof for
/// an otherwise unannotated callback type. Mutable procedure variables cannot
/// inherit the effects of their initializer.
fn fixed_procedure_value(
    resolver: &Resolver<'_>,
    mut value: Ref,
    seen: &mut std::collections::HashSet<Ref>,
) -> bool {
    if !seen.insert(value) {
        return false;
    }
    let source = &resolver.sources[value.file];
    if matches!(source.node(value.node).kind.as_str(), "Paren" | "AutoCast") {
        return source
            .role(value.node, "expr")
            .is_some_and(|node| fixed_procedure_value(resolver, Ref { node, ..value }, seen));
    }
    if source.node(value.node).kind == "Procedure" {
        return true;
    }
    let Some(symbol) = resolver.symbol(value).map(|id| &resolver.symbols[id]) else {
        return false;
    };
    if symbol.parameter || symbol.result || !symbol.constant && !symbol.immutable {
        return false;
    }
    let Some(node) = symbol.value else {
        return false;
    };
    value = Ref {
        file: symbol.reference.file,
        node,
    };
    fixed_procedure_value(resolver, value, seen)
}

fn collect_readers(
    resolver: &mut Resolver<'_>,
    expected: &Type,
    actual: Ref,
    targets: &mut std::collections::HashSet<Ref>,
) {
    let source = &resolver.sources[actual.file];
    if matches!(source.node(actual.node).kind.as_str(), "Paren" | "AutoCast") {
        if let Some(node) = source.role(actual.node, "expr") {
            collect_readers(resolver, expected, Ref { node, ..actual }, targets);
        }
        return;
    }
    if let Some(signature) = procedure(expected) {
        let source = &resolver.sources[signature.file];
        if !params(source, signature.node)
            .iter()
            .any(|(name, _)| is_ro(source, *name))
            || !fixed_procedure_value(resolver, actual, &mut std::collections::HashSet::new())
        {
            return;
        }
        if let Some(signature) = resolver.signature(actual) {
            let source = &resolver.sources[signature.file];
            if let Some(body) = source.node(signature.node).parent.filter(|p| {
                source.node(*p).kind == "Procedure" && source.role(*p, "body").is_some()
            }) {
                targets.insert(Ref {
                    file: signature.file,
                    node: body,
                });
            }
        }
        return;
    }
    if resolver.sources[actual.file].node(actual.node).kind != "Compound" {
        return;
    }
    let fields = resolver.record_fields(expected);
    let elems = resolver.sources[actual.file]
        .group(actual.node, "elems")
        .to_vec();
    for (index, item) in elems.into_iter().enumerate() {
        let source = &resolver.sources[actual.file];
        let value = source.role(item, "value").unwrap_or(item);
        let label = source.role(item, "field").map(|node| source.text(node));
        let typ = expected.element().cloned().or_else(|| {
            label
                .and_then(|label| fields.iter().find(|(name, _)| name == label))
                .or_else(|| fields.get(index))
                .map(|(_, typ)| typ.clone())
        });
        if let Some(typ) = typ {
            collect_readers(
                resolver,
                &typ,
                Ref {
                    node: value,
                    ..actual
                },
                targets,
            );
        }
    }
}
pub(crate) fn reader_targets(resolver: &mut Resolver<'_>) -> std::collections::HashSet<Ref> {
    let mut targets = std::collections::HashSet::new();
    if !resolver.sources.iter().any(|source| {
        !source.excluded
            && source.info["ro_parameters"]
                .as_array()
                .is_some_and(|rows| !rows.is_empty())
    }) {
        return targets;
    }
    let bindings = Substitutions::new();
    for file in 0..resolver.sources.len() {
        if resolver.sources[file].excluded || resolver.sources[file].shared_native {
            continue;
        }
        for i in 0..resolver.sources[file].nodes.len() {
            let node = NodeId(i);
            let at = Ref { file, node };
            if !resolver.active_definition(at) {
                continue;
            }
            let kind = resolver.sources[file].node(node).kind.as_str().to_string();
            if matches!(kind.as_str(), "Declaration" | "Compound" | "Cast") {
                if let Some(annotation) = resolver.sources[file].role(node, "type") {
                    let typ = resolver.specialized_type(
                        Ref {
                            file,
                            node: annotation,
                        },
                        &bindings,
                    );
                    let values = if kind == "Declaration" {
                        resolver.sources[file].group(node, "values").to_vec()
                    } else if kind == "Compound" {
                        vec![node]
                    } else {
                        resolver.sources[file]
                            .role(node, "expr")
                            .into_iter()
                            .collect()
                    };
                    for value in values {
                        collect_readers(resolver, &typ, Ref { file, node: value }, &mut targets);
                    }
                }
            } else if kind == "Assignment" {
                let source = &resolver.sources[file];
                let pairs = source
                    .group(node, "lhs")
                    .iter()
                    .copied()
                    .zip(source.group(node, "rhs").iter().copied())
                    .collect::<Vec<_>>();
                for (lhs, rhs) in pairs {
                    let typ = resolver.type_of(Ref { file, node: lhs });
                    collect_readers(resolver, &typ, Ref { file, node: rhs }, &mut targets);
                }
            } else if kind == "Call" {
                let Some(callee) = resolver.sources[file].role(node, "callee") else {
                    continue;
                };
                let args = resolver.sources[file]
                    .group(node, "args")
                    .iter()
                    .map(|node| Ref { file, node: *node })
                    .collect::<Vec<_>>();
                if resolver.denotes_type(Ref { file, node: callee }) {
                    if args.len() == 1 {
                        let typ = resolver.type_of(Ref { file, node: callee });
                        collect_readers(resolver, &typ, args[0], &mut targets);
                    }
                } else if let Some(sig) = resolver.call_signature(at) {
                    if let Some(arguments) = resolver.arrange_arguments(sig, &args) {
                        for ((_, annotation), values) in
                            resolver.fields(sig, "params").into_iter().zip(arguments)
                        {
                            if let Some(annotation) = annotation {
                                let typ = resolver.type_of(annotation);
                                for value in values {
                                    collect_readers(resolver, &typ, value, &mut targets);
                                }
                            }
                        }
                    }
                }
            } else if kind == "Return" {
                let source = &resolver.sources[file];
                let signature = source
                    .ancestors(node)
                    .find(|id| source.node(*id).kind == "Procedure")
                    .and_then(|p| source.role(p, "type"));
                if let Some(signature) = signature {
                    let values = source.group(node, "values").to_vec();
                    for ((_, annotation), value) in resolver
                        .fields(
                            Ref {
                                file,
                                node: signature,
                            },
                            "results",
                        )
                        .into_iter()
                        .zip(values)
                    {
                        if let Some(annotation) = annotation {
                            let typ = resolver.type_of(annotation);
                            collect_readers(
                                resolver,
                                &typ,
                                Ref { file, node: value },
                                &mut targets,
                            );
                        }
                    }
                }
            }
        }
    }
    targets
}
