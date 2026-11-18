//! Result records are derived from resolved calls, never runtime type metadata.
use crate::semantics::{Ref, Resolver, Substitutions, Type};
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug)]
pub(crate) struct Results {
    pub signature: Ref,
    pub fields: Vec<(String, Type)>,
}

#[derive(Clone, Debug)]
pub(crate) struct DefaultQuery {
    pub name: String,
    pub query: Ref,
    pub call: Ref,
    pub callback: usize,
}

pub(crate) fn is_query(resolver: &Resolver<'_>, node: Ref) -> bool {
    let source = &resolver.sources[node.file];
    source.node(node.node).kind == "Call"
        && source.role(node.node, "callee").is_some_and(|callee| {
            intrinsic_symbol(
                resolver,
                Ref {
                    file: node.file,
                    node: callee,
                },
                &mut HashSet::new(),
            )
        })
}

fn intrinsic_symbol(resolver: &Resolver<'_>, reference: Ref, seen: &mut HashSet<Ref>) -> bool {
    if !seen.insert(reference) {
        return false;
    }
    let source = &resolver.sources[reference.file];
    if source.node(reference.node).kind == "Paren" {
        return source
            .role(reference.node, "expr")
            .is_some_and(|node| intrinsic_symbol(resolver, Ref { node, ..reference }, seen));
    }
    let Some(index) = resolver.symbol(reference) else {
        return false;
    };
    let symbol = &resolver.symbols[index];
    if !symbol.constant || symbol.immutable {
        return false;
    }
    let owner = &resolver.sources[symbol.reference.file];
    if symbol.name == "result_type_of"
        && source
            .collection_roots
            .get("thor")
            .is_some_and(|root| owner.path.parent() == Some(root.join("intrinsics").as_path()))
    {
        return true;
    }
    symbol.value.is_some_and(|node| {
        matches!(
            owner.node(node).kind.as_str(),
            "Ident" | "Selector" | "Paren"
        ) && intrinsic_symbol(
            resolver,
            Ref {
                file: symbol.reference.file,
                node,
            },
            seen,
        )
    })
}

pub(crate) fn expression(resolver: &Resolver<'_>, query: Ref) -> Option<Ref> {
    let source = &resolver.sources[query.file];
    let args = source.group(query.node, "args");
    if args.len() != 1 {
        return None;
    }
    let mut node = args[0];
    while source.node(node).kind == "Paren" {
        node = source.role(node, "expr")?;
    }
    (source.node(node).kind == "Call").then_some(Ref {
        file: query.file,
        node,
    })
}

pub(crate) fn default_queries(resolver: &Resolver<'_>, signature: Ref) -> Vec<DefaultQuery> {
    let source = &resolver.sources[signature.file];
    let Some(params) = source.role(signature.node, "params") else {
        return vec![];
    };
    let fields = resolver.fields(signature, "params");
    source
        .node(params)
        .children
        .iter()
        .filter_map(|field| {
            let query = Ref {
                file: signature.file,
                node: source.role(*field, "default")?,
            };
            if !is_query(resolver, query) {
                return None;
            }
            let call = expression(resolver, query)?;
            let callee = Ref {
                file: call.file,
                node: source.role(call.node, "callee")?,
            };
            let symbol = resolver.symbol(callee)?;
            let parameter = &resolver.symbols[symbol];
            let callback = fields
                .iter()
                .position(|(name, _)| *name == parameter.name)?;
            let name = source
                .group(*field, "names")
                .first()
                .map(|name| source.text(*name).trim_start_matches('$').to_string())?;
            Some(DefaultQuery {
                name,
                query,
                call,
                callback,
            })
        })
        .collect()
}

pub(crate) fn argument_types(
    resolver: &mut Resolver<'_>,
    call: Ref,
    bindings: &Substitutions,
) -> Vec<Type> {
    let source = &resolver.sources[call.file];
    let args = source.group(call.node, "args").to_vec();
    let mut types = vec![];
    for node in args {
        let source = &resolver.sources[call.file];
        let node = source.role(node, "value").unwrap_or(node);
        if source.node(node).kind == "Unary" && source.node(node).op == "**" {
            if let Some(expr) = source.role(node, "expr") {
                let typ = resolver.specialized_type(
                    Ref {
                        file: call.file,
                        node: expr,
                    },
                    bindings,
                );
                match typ {
                    Type::Array {
                        elem,
                        length: Some(count),
                    } if (0..=1024).contains(&count) => {
                        types.extend((0..count).map(|_| *elem.clone()))
                    }
                    _ => types.extend(resolver.record_fields(&typ).into_iter().map(|(_, typ)| typ)),
                }
            }
        } else {
            types.push(resolver.specialized_type(
                Ref {
                    file: call.file,
                    node,
                },
                bindings,
            ));
        }
    }
    types
}

/// Select using the call's inputs, including polymorphic specialization. Native
/// Odin remains responsible for checking the emitted call and its constraints.
pub(crate) fn resolve(
    resolver: &mut Resolver<'_>,
    call: Ref,
    bindings: &Substitutions,
    callback: Option<Ref>,
) -> Option<Results> {
    let callee = callback.or_else(|| {
        resolver.sources[call.file]
            .role(call.node, "callee")
            .map(|node| Ref {
                file: call.file,
                node,
            })
    })?;
    let types = argument_types(resolver, call, bindings);
    let mut candidates = resolver.candidates(callee);
    if candidates.is_empty() {
        if let Type::Procedure(signature) = resolver.specialized_type(callee, bindings) {
            candidates.push(signature);
        }
    }
    let single_candidate = candidates.len() == 1;
    let mut selected = vec![];
    for candidate in candidates {
        let source = &resolver.sources[candidate.file];
        let signature = if source.node(candidate.node).kind == "Procedure" {
            Ref {
                file: candidate.file,
                node: source.role(candidate.node, "type")?,
            }
        } else {
            candidate
        };
        let params = resolver.fields(signature, "params");
        let mut positional = types.clone();
        // Named arguments use the same signature arrangement as ordinary calls.
        if callback.is_none() {
            let args = resolver.sources[call.file]
                .group(call.node, "args")
                .iter()
                .map(|node| Ref {
                    file: call.file,
                    node: *node,
                })
                .collect::<Vec<_>>();
            if args
                .iter()
                .any(|arg| resolver.sources[arg.file].node(arg.node).kind == "FieldValue")
            {
                let Some(arranged) = resolver.arrange_arguments(signature, &args) else {
                    continue;
                };
                positional = arranged
                    .iter()
                    .map(|group| {
                        group
                            .first()
                            .map(|arg| resolver.specialized_type(*arg, bindings))
                            .unwrap_or(Type::Unknown)
                    })
                    .collect();
            }
        }
        let variadic = params
            .last()
            .and_then(|(_, annotation)| *annotation)
            .filter(|annotation| {
                resolver.sources[annotation.file].node(annotation.node).kind == "Ellipsis"
            });
        if let Some(annotation) = variadic {
            let start = params.len() - 1;
            if positional.len() >= start {
                let tail = positional.split_off(start);
                let expr = resolver.sources[annotation.file]
                    .role(annotation.node, "expr")
                    .map(|node| Ref {
                        file: annotation.file,
                        node,
                    });
                let elem = tail.first().cloned().unwrap_or_else(|| {
                    expr.map(|expr| resolver.specialized_type(expr, bindings))
                        .unwrap_or(Type::Unknown)
                });
                positional.push(Type::Slice(Box::new(elem)));
            }
        }
        if positional.len() > params.len() {
            continue;
        }
        let mut substitutions = bindings.clone();
        substitutions.extend(resolver.parameter_bindings(signature, &positional));
        let mut score = 0;
        let mut valid = true;
        for ((_, annotation), actual) in params.iter().zip(&positional) {
            if let Some(annotation) = annotation {
                if let Some(amount) = resolver.shape_score(*annotation, actual, &substitutions) {
                    score += amount;
                } else {
                    valid = false;
                    break;
                }
            }
        }
        // A unique declaration already determines the result names. Let Odin
        // check its exact call rules, including parameter directives such as
        // #any_int which are intentionally outside these structural facts.
        if !valid && !single_candidate {
            continue;
        }
        if positional.len() < params.len() {
            let source = &resolver.sources[signature.file];
            let Some(list) = source.role(signature.node, "params") else {
                continue;
            };
            let required = source
                .node(list)
                .children
                .iter()
                .filter(|field| {
                    source.role(**field, "default").is_none()
                        && source.node(**field).data["variadic"] != true
                })
                .map(|field| source.group(*field, "names").len().max(1))
                .sum::<usize>();
            if positional.len() < required {
                continue;
            }
        }
        let fields = resolver
            .fields(signature, "results")
            .into_iter()
            .enumerate()
            .map(|(index, (name, annotation))| {
                let name = if name.is_empty() || name == "_" {
                    format!("v{index}")
                } else {
                    name
                };
                let typ = annotation
                    .map(|annotation| resolver.specialized_type(annotation, &substitutions))
                    .unwrap_or(Type::Unknown);
                (name, typ)
            })
            .collect::<Vec<_>>();
        selected.push((score, Results { signature, fields }));
    }
    let best = selected.iter().map(|(score, _)| *score).max()?;
    let mut matching = selected
        .into_iter()
        .filter(|(score, _)| *score == best)
        .map(|(_, result)| result);
    let result = matching.next()?;
    if matching.any(|other| other.signature != result.signature) {
        return None;
    }
    Some(result)
}

pub(crate) fn packed_type(results: &Results) -> Type {
    if results.fields.len() == 1 {
        results.fields[0].1.clone()
    } else if results.fields.is_empty() {
        Type::Unknown
    } else {
        Type::Anonymous(results.fields.clone())
    }
}

pub(crate) fn unique_names(results: &Results) -> bool {
    let mut names = HashSet::new();
    results.fields.iter().all(|(name, _)| names.insert(name))
}

pub(crate) fn concrete(typ: &Type) -> bool {
    match typ {
        // Procedure signatures have structural identity in Odin; distinct AST
        // references alone are insufficient to reject a result_type override.
        Type::Unknown | Type::Generic | Type::Procedure(_) => false,
        Type::Array { elem, .. }
        | Type::Slice(elem)
        | Type::Dynamic(elem)
        | Type::Pointer(elem)
        | Type::Fixed { elem, .. }
        | Type::Nominal(_, elem) => concrete(elem),
        Type::Anonymous(fields) | Type::Record(_, fields) => {
            fields.iter().all(|(_, typ)| concrete(typ))
        }
        _ => true,
    }
}

/// Parameter substitutions for a dependent type default. This also records the
/// actual callback declaration, preserving names through aliases and generics.
pub(crate) fn bind_default(
    resolver: &mut Resolver<'_>,
    site: Ref,
    signature: Ref,
    query: &DefaultQuery,
) -> Option<(Substitutions, Ref, HashMap<String, Ref>)> {
    let args = resolver.sources[site.file]
        .group(site.node, "args")
        .iter()
        .map(|node| Ref {
            file: site.file,
            node: *node,
        })
        .collect::<Vec<_>>();
    let arranged = resolver.arrange_arguments(signature, &args)?;
    let params = resolver.fields(signature, "params");
    let actual = arranged
        .iter()
        .map(|group| {
            group
                .first()
                .map(|arg| resolver.type_of(*arg))
                .unwrap_or(Type::Unknown)
        })
        .collect::<Vec<_>>();
    let bindings = resolver.parameter_bindings(signature, &actual);
    let callback = *arranged.get(query.callback)?.first()?;
    let parameters = params
        .into_iter()
        .zip(arranged)
        .filter_map(|((name, _), values)| Some((name, *values.first()?)))
        .collect();
    Some((bindings, callback, parameters))
}
