//! Per-declaration, source-level vet suppressions. Never suppress compiler errors.
use crate::{
    ast::{NodeId, Source, Span},
    diagnostic::{Error, Result},
    semantics::{Constant, Ref, Resolver, Substitutions},
};
use std::collections::HashSet;

pub(crate) fn elements(source: &Source, attribute: NodeId) -> Vec<NodeId> {
    source
        .group(attribute, "elems")
        .iter()
        .copied()
        .filter(|item| source.text(source.role(*item, "field").unwrap_or(*item)) == "vet_ignore")
        .collect()
}
pub(crate) fn rules(
    resolver: &Resolver<'_>,
    file: usize,
    declaration: NodeId,
) -> Result<HashSet<String>> {
    let source = &resolver.sources[file];
    let items = source
        .group(declaration, "attributes")
        .iter()
        .flat_map(|a| elements(source, *a))
        .collect::<Vec<_>>();
    if items.len() > 1 {
        return Err(Error::at(
            source,
            source.node(items[1]).span,
            "Duplicate @(vet_ignore) attribute.",
        ));
    }
    let mut result = HashSet::new();
    for item in items {
        let value = source.role(item, "value").and_then(|node| {
            resolver.constant_value(Ref { file, node }, &Substitutions::new(), None)
        });
        let Some(Constant::Text(text)) = value else {
            return Err(Error::at(
                source,
                source.node(item).span,
                "@(vet_ignore) requires a nonempty constant string of vet check names.",
            ));
        };
        if text.trim().is_empty() {
            return Err(Error::at(
                source,
                source.node(item).span,
                "@(vet_ignore) requires at least one vet check name.",
            ));
        }
        for name in text.split(',').map(str::trim) {
            let name = if name == "fn" { "pure" } else { name };
            if !crate::vet::CHECKS.contains(&name) {
                return Err(Error::at(source,source.node(item).span,
                format!("Unknown vet check '{name}' in @(vet_ignore). Expected immutable, pure (or fn), unused, or semicolon.")));
            }
            result.insert(name.to_string());
        }
    }
    Ok(result)
}
pub(crate) fn validate(resolver: &Resolver<'_>) -> Result<()> {
    for (file, source) in resolver
        .sources
        .iter()
        .enumerate()
        .filter(|(_, s)| s.thor && !s.excluded && s.raw.contains("vet_ignore"))
    {
        for (i, node) in source.nodes.iter().enumerate() {
            let id = NodeId(i);
            if node.kind == "Attribute" && !elements(source, id).is_empty() {
                let Some(owner) = node
                    .parent
                    .filter(|p| matches!(source.node(*p).kind.as_str(), "Declaration" | "Import"))
                else {
                    return Err(Error::at(
                        source,
                        node.span,
                        "@(vet_ignore) applies to a declaration or import.",
                    ));
                };
                rules(resolver, file, owner)?;
            }
        }
    }
    Ok(())
}
pub(crate) fn ignored(resolver: &Resolver<'_>, file: usize, span: Span, rule: &str) -> bool {
    let source = &resolver.sources[file];
    if !source.thor || !source.raw.contains("vet_ignore") {
        return false;
    }
    let rule = match rule {
        "iterator" | "import" => "unused",
        other => other,
    };
    let owner = source
        .nodes
        .iter()
        .enumerate()
        .filter(|(_, node)| matches!(node.kind.as_str(), "Declaration" | "Import"))
        .filter(|(_, node)| {
            node.span.contains(span)
                || rule == "semicolon"
                    && node.span.end <= span.start
                    && source
                        .raw
                        .get(node.span.end..span.start)
                        .is_some_and(|s| s.trim().is_empty() && !s.contains('\n'))
        })
        .min_by_key(|(_, node)| node.span.len())
        .map(|(i, _)| NodeId(i));
    let Some(owner) = owner else {
        return false;
    };
    // A procedure annotation describes the declaration, not findings in its body.
    if source.group(owner, "values").iter().any(|value| {
        source.node(*value).kind == "Procedure"
            && source
                .role(*value, "body")
                .is_some_and(|body| source.node(body).span.contains(span))
    }) {
        return false;
    }
    rules(resolver, file, owner).is_ok_and(|rules| rules.contains(rule))
}
