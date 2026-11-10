//! Shared min/max identities and spread plans used by lowering and evaluation.
use crate::{
    ast::NodeId,
    lower::unquote,
    semantics::{Ref, Resolver},
};
use std::collections::HashSet;
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Part {
    Scalar,
    Array,
    Slice,
}
pub(crate) struct Plan {
    pub name: String,
    pub parts: Vec<(Part, NodeId)>,
    pub optional: bool,
}
pub(crate) fn plan(resolver: &Resolver<'_>, call: Ref) -> Option<Plan> {
    let source = &resolver.sources[call.file];
    if !source.thor || source.node(call.node).kind != "Call" {
        return None;
    }
    let ellipsis = source.node(call.node).spans.get("ellipsis").copied();
    let mut parts = vec![];
    for arg in source.group(call.node, "args") {
        let mut value = *arg;
        while source.node(value).kind == "Paren" {
            value = source.role(value, "expr")?;
        }
        if ellipsis.is_some_and(|span| source.node(*arg).span.start >= span.end) {
            parts.push((Part::Slice, *arg));
        } else if source.node(value).kind == "Unary" && source.node(value).op == "**" {
            parts.push((Part::Array, source.role(value, "expr")?));
        } else {
            parts.push((Part::Scalar, *arg));
        }
    }
    if parts.iter().all(|(kind, _)| *kind == Part::Scalar) {
        return None;
    }
    let callee = source.role(call.node, "callee")?;
    let name = name(
        resolver,
        Ref {
            file: call.file,
            node: callee,
        },
        &mut HashSet::new(),
    )?;
    Some(Plan {
        name,
        parts,
        optional: ellipsis.is_some(),
    })
}
pub(crate) fn name(resolver: &Resolver<'_>, r: Ref, seen: &mut HashSet<Ref>) -> Option<String> {
    if !seen.insert(r) {
        return None;
    }
    let source = &resolver.sources[r.file];
    if source.node(r.node).kind == "Ident" && resolver.lookup(r, source.text(r.node)).is_none() {
        for selection in &source.selections {
            let Ok(path) = unquote(&selection.path) else {
                continue;
            };
            if selection
                .names
                .iter()
                .any(|name| name == source.text(r.node))
                && matches!(path.as_str(), "core:math" | "base:builtin")
                && resolver.standard_library(r, &path)
                && matches!(source.text(r.node), "min" | "max")
            {
                return Some(source.text(r.node).into());
            }
        }
    }
    if source.node(r.node).kind == "Selector" {
        if let (Some(base), Some(field)) =
            (source.role(r.node, "expr"), source.role(r.node, "field"))
        {
            let member = source.text(field);
            if matches!(member, "min" | "max")
                && resolver
                    .lookup(
                        Ref {
                            file: r.file,
                            node: base,
                        },
                        source.text(base),
                    )
                    .is_none()
            {
                for import in &source.nodes {
                    if import.kind != "Import" {
                        continue;
                    }
                    let Ok(path) = unquote(&import.op) else {
                        continue;
                    };
                    let alias = source.names.get(import.name);
                    let alias = if alias.is_empty() {
                        path.rsplit(['/', ':']).next().unwrap_or("")
                    } else {
                        alias
                    };
                    if alias == source.text(base)
                        && matches!(path.as_str(), "core:math" | "base:builtin")
                        && resolver.standard_library(r, &path)
                    {
                        return Some(member.into());
                    }
                }
            }
        }
    }
    if source.node(r.node).kind == "Paren" {
        return source
            .role(r.node, "expr")
            .and_then(|node| name(resolver, Ref { file: r.file, node }, seen));
    }
    if let Some(symbol) = resolver.symbol(r) {
        let symbol = &resolver.symbols[symbol];
        if symbol.constant && !symbol.immutable {
            if let Some(node) = symbol.value {
                return name(
                    resolver,
                    Ref {
                        file: symbol.reference.file,
                        node,
                    },
                    seen,
                );
            }
        }
        return None;
    }
    if source.node(r.node).kind == "Ident"
        && matches!(source.text(r.node), "min" | "max")
        && resolver.native_builtin(r, source.text(r.node))
    {
        return Some(source.text(r.node).into());
    }
    if source.node(r.node).kind == "Selector" {
        let base = source.role(r.node, "expr")?;
        let field = source.role(r.node, "field")?;
        let member = source.text(field);
        if !matches!(member, "min" | "max")
            || resolver
                .lookup(
                    Ref {
                        file: r.file,
                        node: base,
                    },
                    source.text(base),
                )
                .is_some()
        {
            return None;
        }
        for import in &source.nodes {
            if import.kind != "Import" {
                continue;
            }
            let path = unquote(&import.op).ok()?;
            let alias = source.names.get(import.name);
            let alias = if alias.is_empty() {
                path.rsplit(['/', ':']).next().unwrap_or("")
            } else {
                alias
            };
            if alias == source.text(base)
                && matches!(path.as_str(), "core:math" | "base:builtin")
                && resolver.standard_library(r, &path)
            {
                return Some(member.into());
            }
        }
    }
    None
}
