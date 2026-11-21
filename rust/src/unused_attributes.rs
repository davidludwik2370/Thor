//! Explicitly unavailable declarations. No source scan or resolver work unless
//! a Thor file actually contains the attribute.
use crate::{
    ast::{NodeId, Source},
    diagnostic::{Error, Result},
    semantics::{Ref, Resolver},
    source_uses,
};
use serde_json::json;
use std::collections::HashSet;

pub(crate) fn elements(source: &Source, attribute: NodeId) -> Vec<NodeId> {
    source
        .group(attribute, "elems")
        .iter()
        .copied()
        .filter(|item| source.text(source.role(*item, "field").unwrap_or(*item)) == "unused")
        .collect()
}

pub(crate) fn marked(source: &Source, declaration: NodeId) -> bool {
    source.thor
        && source
            .group(declaration, "attributes")
            .iter()
            .any(|attribute| !elements(source, *attribute).is_empty())
}

pub(crate) fn only_unused_attributes(source: &Source, declaration: NodeId) -> bool {
    source.thor
        && source
            .group(declaration, "attributes")
            .iter()
            .all(|attribute| {
                source.group(*attribute, "elems").len() == elements(source, *attribute).len() + crate::vet_attributes::elements(source,*attribute).len() + crate::noalloc::elements(source,*attribute).len()
            })
}

pub(crate) fn present(sources: &[Source]) -> bool {
    sources.iter().any(|source| {
        !source.excluded
            && source.thor
            && source.raw.contains("unused")
            && source.nodes.iter().enumerate().any(|(index, node)| {
                node.kind == "Attribute" && !elements(source, NodeId(index)).is_empty()
            })
    })
}

pub(crate) fn validate(resolver: &mut Resolver<'_>, final_check: bool) -> Result<()> {
    let mut marked = HashSet::new();
    let mut spellings = HashSet::new();
    for (file, source) in resolver.sources.iter().enumerate() {
        if source.excluded || !source.thor || !source.raw.contains("unused") {
            continue;
        }
        for (index, node) in source.nodes.iter().enumerate() {
            let id = NodeId(index);
            if node.kind == "Declaration" && self::marked(source, id) {
                marked.insert(Ref { file, node: id });
                spellings.extend(
                    source
                        .group(id, "names")
                        .iter()
                        .map(|name| source.text(*name).to_string()),
                );
            }
        }
    }
    if !present(resolver.sources) {
        return Ok(());
    }
    let queries = resolver.begin_symbol_queries();
    let result = check(resolver, &marked, &spellings, final_check);
    resolver.end_symbol_queries(queries);
    result
}

fn check(
    resolver: &mut Resolver<'_>,
    marked: &HashSet<Ref>,
    spellings: &HashSet<String>,
    final_check: bool,
) -> Result<()> {
    for (file, source) in resolver.sources.iter().enumerate() {
        if source.excluded || source.shared_native {
            continue;
        }
        let names = source_uses::names(source);
        let mut pending = vec![(NodeId(0), true)];
        while let Some((id, certain)) = pending.pop() {
            let node = source.node(id);
            let at = Ref { file, node: id };
            if source.thor
                && node.kind == "Attribute"
                && !elements(source, id).is_empty()
                && node
                    .parent
                    .is_none_or(|parent| source.node(parent).kind != "Declaration")
                && (certain || final_check)
            {
                let mut error = Error::at(
                    source,
                    source.node(elements(source, id)[0]).span,
                    "@(unused) applies to variable, constant, type, and procedure declarations.",
                );
                error.diagnostics[0]["code"] = json!("thor/unused-attribute");
                return Err(error);
            }
            if marked.contains(&at) && (certain || final_check) {
                let attrs = source
                    .group(id, "attributes")
                    .iter()
                    .flat_map(|attribute| elements(source, *attribute))
                    .collect::<Vec<_>>();
                let problem = if attrs.len() > 1 {
                    Some("Duplicate @(unused) attribute.")
                } else if source.node(attrs[0]).kind != "Ident" {
                    Some("@(unused) takes no value; use the bare attribute.")
                } else if source
                    .group(id, "names")
                    .iter()
                    .all(|name| source.text(*name) == "_")
                {
                    Some("@(unused) requires a named declaration.")
                } else {
                    let entry = source.names.get(source.node(NodeId(0)).name) == "main"
                        && source
                            .group(id, "names")
                            .iter()
                            .any(|name| source.text(*name) == "main")
                        && !source
                            .ancestors(id)
                            .any(|owner| source.node(owner).kind == "Procedure");
                    let automatic = resolver.attributes(at).iter().any(|(name, value)| {
                        matches!(
                            name.as_str(),
                            "init" | "fini" | "test" | "export" | "require"
                        ) && value.is_none_or(|value| {
                            resolver
                                .context_condition(value, &Default::default())
                                .unwrap_or(final_check)
                        })
                    });
                    (entry || automatic).then_some("@(unused) cannot mark an entry point or an automatically used declaration.")
                };
                if let Some(message) = problem {
                    let mut error = Error::at(source, source.node(attrs[0]).span, message);
                    error.diagnostics[0]["code"] = json!("thor/unused-attribute");
                    return Err(error);
                }
            }
            if matches!(node.kind.as_str(), "When" | "WhenExpr") {
                if let Some(cond) = source.role(id, "cond") {
                    let selected =
                        resolver.context_condition(Ref { file, node: cond }, &Default::default());
                    let roles = if node.kind == "When" {
                        ["body", "else"]
                    } else {
                        ["x", "y"]
                    };
                    for (index, role) in roles.into_iter().enumerate().rev() {
                        if selected.is_none_or(|value| value == (index == 0)) {
                            pending.extend(
                                source
                                    .role(id, role)
                                    .map(|node| (node, certain && selected.is_some())),
                            );
                        }
                    }
                    pending.push((cond, certain));
                    continue;
                }
            }
            if (certain || final_check)
                && matches!(node.kind.as_str(), "Ident" | "Selector")
                && spellings.contains(source.text(source.role(id, "field").unwrap_or(id)))
                && (node.kind == "Selector"
                    || node.kind == "Ident"
                        && !names.contains(&id)
                        && !source_uses::field_label(resolver, at))
            {
                if let Some(symbol) = resolver.symbol(at).map(|index| &resolver.symbols[index]) {
                    if marked.contains(&symbol.reference) {
                        let span = source.node(source.role(id, "field").unwrap_or(id)).span;
                        let mut error = Error::at(source, span,
                            format!("Cannot use '{}': it is marked @(unused). Remove the attribute before using it.", symbol.name));
                        error.diagnostics[0]["code"] = json!("thor/unused-reference");
                        let owner = &resolver.sources[symbol.reference.file];
                        let declaration = Error::at(
                            owner,
                            owner.node(symbol.name_node).span,
                            "Declaration marked @(unused) here.",
                        );
                        error.diagnostics[0]["related"] = json!([{"pos":declaration.diagnostics[0]["pos"],"message":"Declaration marked @(unused) here."}]);
                        return Err(error);
                    }
                }
            }
            pending.extend(node.children.iter().rev().map(|node| (*node, certain)));
        }
    }
    Ok(())
}
