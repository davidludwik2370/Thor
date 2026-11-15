//! Source binding uses, independent of generated helpers and ownership cleanup.
use super::{position, Project};
use crate::{
    ast::{NodeId, Source},
    semantics::{Ref, Resolver, Type},
};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};

fn binding(resolver: &Resolver<'_>, mut symbol: usize) -> usize {
    while let Some(projection) = &resolver.symbols[symbol].projection {
        let Some(owner) = resolver.symbol(projection.owner) else {
            break;
        };
        if owner == symbol {
            break;
        }
        symbol = owner;
    }
    symbol
}

fn names(source: &Source) -> HashSet<NodeId> {
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

fn direct_name(source: &Source, mut id: NodeId) -> Option<NodeId> {
    while source.node(id).kind == "Paren" {
        id = source.role(id, "expr")?;
    }
    (source.node(id).kind == "Ident").then_some(id)
}

fn explicit_discard(source: &Source, mut id: NodeId) -> bool {
    while let Some(parent) = source.node(id).parent {
        if source.node(parent).kind == "Paren" {
            id = parent;
            continue;
        }
        if source.node(parent).kind != "Assignment" || source.node(parent).op != "=" {
            return false;
        }
        let targets = source.group(parent, "lhs");
        let values = source.group(parent, "rhs");
        return targets.len() == values.len()
            && values
                .iter()
                .position(|value| *value == id)
                .is_some_and(|index| {
                    direct_name(source, targets[index]).is_some_and(|name| source.text(name) == "_")
                });
    }
    false
}

// Only recognize value-only self-updates. Calls, address-taking, indexing and
// other unknown operations remain reads: they may observe or escape a value.
fn simple_reads(source: &Source, root: NodeId) -> Option<Vec<NodeId>> {
    let mut reads = vec![];
    let mut pending = vec![root];
    while let Some(id) = pending.pop() {
        let node = source.node(id);
        match node.kind.as_str() {
            "Ident" => reads.push(id),
            "Literal" | "Paren" | "Binary" => {}
            "Unary" if matches!(node.op.as_str(), "+" | "-" | "!" | "~") => {}
            _ => return None,
        }
        pending.extend(node.children.iter().copied());
    }
    Some(reads)
}

fn write_only(resolver: &Resolver<'_>, file: usize) -> HashSet<NodeId> {
    let source = &resolver.sources[file];
    let mut writes = HashSet::new();
    for (i, node) in source.nodes.iter().enumerate() {
        let id = NodeId(i);
        let (targets, values) = if node.kind == "Assignment" && node.op != "in" {
            (source.group(id, "lhs").to_vec(), source.group(id, "rhs"))
        } else if node.kind == "IncDec" {
            (source.role(id, "expr").into_iter().collect(), &[][..])
        } else {
            continue;
        };
        for (index, target) in targets.iter().enumerate() {
            let Some(name) = direct_name(source, *target) else {
                continue;
            };
            let Some(symbol) = resolver.symbol(Ref { file, node: name }) else {
                continue;
            };
            // A bare `using` field writes through its owner, not to the owner
            // binding itself. Pointer/slice/field targets are likewise reads.
            let target = &resolver.symbols[symbol];
            let owner = &resolver.sources[target.reference.file];
            if target.projection.is_some()
                || matches!(
                    owner.node(target.reference.node).kind.as_str(),
                    "Range" | "TypeSwitch"
                ) && owner.text(target.name_node).starts_with('&')
            {
                continue;
            }
            writes.insert(name);
            if targets.len() == values.len() {
                if let Some(reads) = simple_reads(source, values[index]) {
                    writes.extend(
                        reads.into_iter().filter(|node| {
                            resolver.symbol(Ref { file, node: *node }) == Some(symbol)
                        }),
                    );
                }
            }
        }
    }
    writes
}

fn field_label(resolver: &mut Resolver<'_>, at: Ref) -> bool {
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
        "ImplicitSelector" | "EnumType" | "PolyType" => true,
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

#[derive(Default)]
pub(super) struct Analysis {
    pub(super) findings: Vec<Value>,
    pub(super) immutable_exemptions: Vec<Ref>,
}

pub(super) fn analyze(project: &Project) -> Analysis {
    let root = if project.options.entry.is_file() {
        project.options.entry.parent().unwrap()
    } else {
        &project.options.entry
    };
    let mut resolver = project.resolver();
    resolver.set_evaluations(project.evaluated.clone());
    resolver.prepare_contexts();
    let mut candidates = HashMap::new();
    for (index, symbol) in resolver.symbols.iter().enumerate() {
        let source = &project.sources[symbol.reference.file];
        if !source.thor
            || source.excluded
            || !source.path.starts_with(root)
            || symbol.result
            || symbol.projection.is_some()
        {
            continue;
        }
        let local = match source.node(symbol.reference.node).kind.as_str() {
            "Declaration" | "Range" | "TypeSwitch" => source
                .ancestors(symbol.reference.node)
                .find(|id| {
                    matches!(
                        source.node(*id).kind.as_str(),
                        "Procedure" | "StructType" | "File"
                    )
                })
                .is_some_and(|id| source.node(id).kind == "Procedure"),
            "Field" => {
                symbol.parameter
                    && source.node(symbol.scope).kind == "Procedure"
                    && source.role(symbol.scope, "body").is_some()
            }
            _ => false,
        };
        if local {
            candidates.insert((symbol.reference, symbol.name.clone()), index);
        }
    }
    let mut used = HashSet::new();
    let mut observed = HashSet::new();
    let mut certain_names = HashSet::new();
    for (file, source) in project.sources.iter().enumerate() {
        if source.excluded || !source.thor || !source.path.starts_with(root) {
            continue;
        }
        let names = names(source);
        let writes = write_only(&resolver, file);
        let mut pending = vec![(NodeId(0), true)];
        while let Some((id, certain)) = pending.pop() {
            let node = source.node(id);
            if certain && names.contains(&id) {
                certain_names.insert(Ref { file, node: id });
            }
            if matches!(node.kind.as_str(), "When" | "WhenExpr") {
                if let Some(cond) = source.role(id, "cond") {
                    pending.push((cond, certain));
                    let selected =
                        resolver.context_condition(Ref { file, node: cond }, &Default::default());
                    let arms = if node.kind == "When" {
                        ["body", "else"]
                    } else {
                        ["x", "y"]
                    };
                    for (index, role) in arms.into_iter().enumerate() {
                        if selected.is_none_or(|value| value == (index == 0)) {
                            if let Some(arm) = source.role(id, role) {
                                pending.push((arm, certain && selected.is_some()));
                            }
                        }
                    }
                    continue;
                }
            }
            // Unknown compile-time selections can depend on generic/native
            // facts. Count potential uses but do not warn inside their arms.
            let certain =
                certain && !matches!(node.kind.as_str(), "CompileSwitch" | "CompileSwitchExpr");
            if node.kind == "Ident"
                && !names.contains(&id)
                && !writes.contains(&id)
                && !field_label(&mut resolver, Ref { file, node: id })
            {
                if let Some(symbol) = resolver.symbol(Ref { file, node: id }) {
                    let owner = &resolver.symbols[binding(&resolver, symbol)];
                    if let Some(index) = candidates.get(&(owner.reference, owner.name.clone())) {
                        used.insert(*index);
                        if resolver.symbols[symbol].projection.is_some()
                            || !explicit_discard(source, id)
                        {
                            observed.insert(*index);
                        }
                    }
                }
            }
            pending.extend(node.children.iter().map(|child| (*child, certain)));
        }
    }
    let mut analysis = Analysis::default();
    for index in candidates.values() {
        let symbol = &resolver.symbols[*index];
        let at = Ref {
            file: symbol.reference.file,
            node: symbol.name_node,
        };
        if !certain_names.contains(&at) {
            continue;
        }
        // Unused names and bindings used only in direct discards receive
        // no immutable advice. Any ordinary use makes them eligible again.
        if !observed.contains(index) {
            analysis.immutable_exemptions.push(at);
        }
        if used.contains(index) {
            continue;
        }
        let source = &project.sources[at.file];
        let mut span = source.node(at.node).span;
        // Reference loop bindings include '&' in their binding node.
        span.start += source.slice(span).len() - symbol.name.len();
        analysis.findings.push(json!({"type":"warning", "stage":"vet", "code":"thor/unused",
            "pos":position(source, span), "msgs":[format!("'{}' declared but not used", symbol.name)]}));
    }
    analysis.findings.sort_by_key(|finding| {
        (
            finding["pos"]["file"].as_str().unwrap().to_string(),
            finding["pos"]["offset"].as_u64().unwrap(),
        )
    });
    analysis
}
