//! Classify source binding names and labels before resolving references.
use crate::{
    ast::{NodeId, Source},
    semantics::{Ref, Resolver, Type},
};
use std::collections::HashSet;

pub(crate) fn names(source: &Source) -> HashSet<NodeId> {
    let mut names = HashSet::new();
    let mut pending = vec![];
    for (i, _) in source.nodes.iter().enumerate() {
        let id = NodeId(i);
        pending.extend(source.group(id, "names").iter().copied());
        pending.extend(source.role(id, "label"));
    }
    while let Some(id) = pending.pop() {
        if names.insert(id) {
            pending.extend(source.node(id).children.iter().copied());
        }
    }
    names
}

pub(crate) fn field_label(resolver: &mut Resolver<'_>, at: Ref) -> bool {
    let source = &resolver.sources[at.file];
    let Some(parent) = source.node(at.node).parent else {
        return false;
    };
    if source.node(at.node).span.start < source.node(parent).span.start
        || source.node(parent).kind == "Other"
            && matches!(
                source.text(parent).split_whitespace().next(),
                Some("break" | "continue")
            )
    {
        return true;
    }
    match source.node(parent).kind.as_str() {
        "Selector" => source.role(parent, "field") == Some(at.node),
        "ImplicitSelector" | "EnumType" | "PolyType" | "Attribute" => true,
        "Call"
            if source
                .role(parent, "callee")
                .is_some_and(|callee| source.text(callee) == "#config")
                && source.group(parent, "args").first() == Some(&at.node) =>
        {
            true
        }
        "FieldValue" if source.role(parent, "field") == Some(at.node) => {
            let Some(owner) = source.node(parent).parent else {
                return true;
            };
            if source.node(owner).kind != "Compound" {
                return true;
            }
            let mut typ = resolver.type_of(Ref { node: owner, ..at });
            while let Type::Nominal(_, inner) = typ {
                typ = *inner;
            }
            matches!(typ, Type::Struct(_) | Type::Record(..) | Type::Anonymous(_))
        }
        _ => false,
    }
}
