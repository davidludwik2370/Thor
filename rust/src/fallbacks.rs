//! Multi-value fallback syntax and its assignment-position contract.
use crate::semantics::{Ref, Resolver, Type};

pub(crate) fn unparen(r: &Resolver<'_>, mut at: Ref) -> Ref {
    while r.sources[at.file].node(at.node).kind == "Paren" {
        let Some(node) = r.sources[at.file].role(at.node, "expr") else {
            break;
        };
        at.node = node;
    }
    at
}
pub(crate) fn discarded(r: &Resolver<'_>, at: Ref) -> bool {
    let at = unparen(r, at);
    let s = &r.sources[at.file];
    s.node(at.node).kind == "Ident" && s.text(at.node) == "_"
}
pub(crate) fn optional_count(r: &mut Resolver<'_>, at: Ref) -> Option<usize> {
    let at = unparen(r, at);
    if matches!(
        r.sources[at.file].node(at.node).kind.as_str(),
        "Index" | "TypeAssertion"
    ) {
        Some(2)
    } else {
        count(r, at)
    }
}
pub(crate) fn count(r: &mut Resolver<'_>, at: Ref) -> Option<usize> {
    let at = unparen(r, at);
    let s = &r.sources[at.file];
    match s.node(at.node).kind.as_str() {
        "FallbackList" | "SwitchResults" => Some(s.group(at.node, "args").len()),
        "OrElse" | "OrPanic" | "OrReturn" | "OrBranch" => {
            let node = s.role(
                at.node,
                if s.node(at.node).kind == "OrElse" {
                    "x"
                } else {
                    "expr"
                },
            )?;
            optional_count(r, Ref { node, ..at })?.checked_sub(1)
        }
        "Call" => r
            .call_signature(at)
            .map(|sig| r.fields(sig, "results").len()),
        "Unary" if s.node(at.node).op == "**" => {
            let node = s.role(at.node, "expr")?;
            let typ = r.type_of(Ref { node, ..at });
            match typ {
                Type::Array {
                    length: Some(n), ..
                } => usize::try_from(n).ok(),
                _ => Some(r.record_fields(&typ).len()),
            }
        }
        "SwitchExpr" | "CompileSwitchExpr" => {
            let arms = s.node(at.node).data["cases"]
                .as_array()?
                .iter()
                .filter_map(|case| {
                    crate::ast::Span::from_wire(&case["result"])
                        .and_then(|span| s.at(span, at.node))
                })
                .collect::<Vec<_>>();
            arms.into_iter()
                .filter_map(|node| count(r, Ref { node, ..at }))
                .max()
        }
        _ => Some(1),
    }
}

// Only transparent expression wrappers preserve a result's assignment slot.
// A return, call argument or stored aggregate supplies no discarded target.
pub(crate) fn targets(r: &mut Resolver<'_>, mut at: Ref, n: usize) -> Vec<Option<Ref>> {
    let empty = vec![None; n];
    let mut offset = 0;
    loop {
        let s = &r.sources[at.file];
        let Some(parent) = s.node(at.node).parent else {
            return empty;
        };
        let node = s.node(parent);
        match node.kind.as_str() {
            "Paren" | "OrElse" | "OrPanic" | "OrReturn" | "OrBranch" => at.node = parent,
            "Ternary" | "WhenExpr"
                if s.role(parent, "x") == Some(at.node) || s.role(parent, "y") == Some(at.node) =>
            {
                at.node = parent
            }
            "SwitchExpr" | "CompileSwitchExpr" => {
                let is_arm = node.data["cases"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .any(|case| {
                        crate::ast::Span::from_wire(&case["result"])
                            .and_then(|span| s.at(span, parent))
                            == Some(at.node)
                    });
                if !is_arm {
                    return empty;
                };
                at.node = parent;
            }
            "Declaration" | "Assignment" | "SwitchResults" => {
                let role = match node.kind.as_str() {
                    "Declaration" => "values",
                    "Assignment" => "rhs",
                    _ => "args",
                };
                let values = s.group(parent, role).to_vec();
                let Some(index) = values.iter().position(|v| *v == at.node) else {
                    return empty;
                };
                let target_role = if node.kind == "Declaration" {
                    "names"
                } else {
                    "lhs"
                };
                let targets = s.group(parent, target_role).to_vec();
                let wrapper = node.kind == "SwitchResults";
                for value in &values[..index] {
                    let Some(size) = count(r, Ref { node: *value, ..at }) else {
                        return empty;
                    };
                    offset += size;
                }
                if wrapper {
                    at.node = parent;
                    continue;
                }
                return (0..n)
                    .map(|i| {
                        targets
                            .get(offset + i)
                            .map(|node| Ref { node: *node, ..at })
                    })
                    .collect();
            }
            _ => return empty,
        }
    }
}

pub(crate) fn owner(r: &Resolver<'_>, at: Ref) -> Option<Ref> {
    let mut child = at;
    while let Some(parent) = r.sources[child.file].node(child.node).parent {
        let s = &r.sources[child.file];
        if s.node(parent).kind == "Paren" {
            child.node = parent;
            continue;
        }
        return (s.node(parent).kind == "OrElse" && s.role(parent, "y") == Some(child.node))
            .then_some(Ref { node: parent, ..at });
    }
    None
}
