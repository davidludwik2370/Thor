//! Source binding uses, independent of generated helpers and ownership cleanup.
use super::{position, Fix, Project};
use crate::{
    ast::{NodeId, Source, Span},
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
    pub(super) import_fixes: Vec<Fix>,
}

// Import removal must preserve uses in every target branch, not merely the
// active one. This scan considers original source references only, never the
// aliases and runtime adapters generated by lowering.
fn unused_imports(resolver: &mut Resolver<'_>, file: usize, names: &HashSet<NodeId>) -> Vec<Fix> {
    let source = &resolver.sources[file];
    let mut imports = vec![];
    for (i, node) in source.nodes.iter().enumerate() {
        if node.kind != "Import" {
            continue;
        }
        let Ok(path) = crate::lower::unquote(&node.op) else {
            continue;
        };
        let explicit = source.names.get(node.name);
        let namespace = if explicit.is_empty() {
            path.rsplit(['/', ':']).next().unwrap_or("")
        } else {
            explicit
        }
        .to_string();
        let selections = source
            .selections
            .iter()
            .filter(|selection| {
                selection.path == node.op
                    && (node.span.start <= selection.start && selection.start < node.span.end
                        || selection.start >= node.span.end
                            && !source.nodes.iter().any(|other| {
                                other.kind == "Import"
                                    && node.span.end <= other.span.start
                                    && other.span.start < selection.start
                            }))
            })
            .collect::<Vec<_>>();
        if namespace == "_" && selections.is_empty() {
            continue;
        }
        imports.push((NodeId(i), path, namespace, selections));
    }
    if imports.is_empty() {
        return vec![];
    }
    let mut used = HashSet::new();
    for (i, node) in source.nodes.iter().enumerate() {
        let id = NodeId(i);
        let at = Ref { file, node: id };
        if node.kind != "Ident" || names.contains(&id) || field_label(resolver, at) {
            continue;
        }
        let symbol = resolver.symbol(at);
        let name = source.text(id);
        for (index, (_, path, namespace, selections)) in imports.iter().enumerate() {
            if used.contains(&index) {
                continue;
            }
            if namespace != "_" && name == namespace && symbol.is_none() {
                used.insert(index);
                continue;
            }
            if !selections.iter().any(|selection| {
                selection.wildcard || selection.names.iter().any(|item| item == name)
            }) {
                continue;
            }
            let target = resolver.package_symbol(at, path, name);
            if target.is_some() && target == symbol
                || symbol.is_none()
                    && selections.iter().any(|selection| {
                        selection.names.iter().any(|item| item == name)
                            || selection.wildcard
                                && matches!(path.as_str(), "base:builtin" | "base:intrinsics")
                    })
            {
                used.insert(index);
            }
        }
    }
    let mut fixes = vec![];
    for (index, (id, path, _, selections)) in imports.into_iter().enumerate() {
        if used.contains(&index) {
            continue;
        }
        let node = source.node(id);
        let mut end = selections
            .iter()
            .map(|selection| selection.end)
            .fold(node.span.end, usize::max);
        let highlight = Span {
            start: node.span.start,
            end,
        };
        let comments = source
            .comments
            .iter()
            .filter(|span| node.span.start <= span.start && span.end <= end)
            .map(|span| source.slice(*span))
            .collect::<Vec<_>>();
        let newline = if source.raw.contains("\r\n") {
            "\r\n"
        } else {
            "\n"
        };
        let mut replacement = comments.join(newline);
        if comments
            .last()
            .is_some_and(|comment| comment.starts_with("//"))
        {
            replacement.push_str(newline);
        }
        let mut after = end;
        while matches!(source.raw.as_bytes().get(after), Some(b' ' | b'\t')) {
            after += 1;
        }
        if source.raw.as_bytes().get(after) == Some(&b';') {
            end = after + 1;
        }
        fixes.push(Fix {
            file,
            span: Span {
                start: node.span.start,
                end,
            },
            replacement,
            rule: "import",
            names: path,
            highlight,
        });
    }
    fixes
}

pub(super) fn analyze(project: &Project, include_unused: bool) -> Analysis {
    let root = if project.options.entry.is_file() {
        project.options.entry.parent().unwrap()
    } else {
        &project.options.entry
    };
    let mut resolver = project.resolver();
    resolver.set_evaluations(project.evaluated.clone());
    resolver.prepare_contexts();
    let queries = resolver.begin_symbol_queries();
    let mut candidates = HashMap::new();
    let mut types = HashSet::new();
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
        let datatype = include_unused
            && source.node(symbol.reference.node).kind == "Declaration"
            && symbol.constant
            && symbol.value.is_some_and(|node| {
                resolver.denotes_type(Ref {
                    file: symbol.reference.file,
                    node,
                })
            });
        if local || datatype {
            candidates.insert((symbol.reference, symbol.name.clone()), index);
            if datatype {
                types.insert(index);
            }
        }
    }
    let mut used = HashSet::new();
    let mut observed = HashSet::new();
    let mut certain_names = HashSet::new();
    let mut import_fixes = vec![];
    for (file, source) in project.sources.iter().enumerate() {
        if source.excluded || !source.path.starts_with(root) {
            continue;
        }
        let names = names(source);
        if include_unused && source.thor {
            import_fixes.extend(unused_imports(&mut resolver, file, &names));
        }
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
            if node.kind == "Selector"
                || node.kind == "Ident"
                    && !names.contains(&id)
                    && !writes.contains(&id)
                    && !field_label(&mut resolver, Ref { file, node: id })
            {
                if let Some(symbol) = resolver.symbol(Ref { file, node: id }) {
                    let owner = &resolver.symbols[binding(&resolver, symbol)];
                    if let Some(index) = candidates.get(&(owner.reference, owner.name.clone())) {
                        if types.contains(index)
                            && owner.reference.file == file
                            && source.node(owner.reference.node).span.contains(node.span)
                        {
                            continue; // A recursive type alone is not a use.
                        }
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
    let mut analysis = Analysis {
        import_fixes,
        ..Analysis::default()
    };
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
        if !observed.contains(index) && !types.contains(index) {
            analysis.immutable_exemptions.push(at);
        }
        if used.contains(index) {
            continue;
        }
        let source = &project.sources[at.file];
        let mut span = source.node(at.node).span;
        // Reference loop bindings include '&' in their binding node.
        span.start += source.slice(span).len() - symbol.name.len();
        let datatype = types.contains(index);
        let message = if datatype {
            format!("Type '{}' declared but not used", symbol.name)
        } else {
            format!("'{}' declared but not used", symbol.name)
        };
        analysis.findings.push(json!({"type":"warning", "stage":"vet", "code":if datatype {"thor/unused-type"} else {"thor/unused"},
            "pos":position(source, span), "msgs":[message]}));
    }
    for fix in &analysis.import_fixes {
        analysis.findings.push(
            json!({"type":"warning", "stage":"vet", "code":"thor/unused-import",
            "pos":position(&project.sources[fix.file], fix.highlight), "msgs":[fix.message()]}),
        );
    }
    analysis.findings.sort_by_key(|finding| {
        (
            finding["pos"]["file"].as_str().unwrap().to_string(),
            finding["pos"]["offset"].as_u64().unwrap(),
        )
    });
    resolver.end_symbol_queries(queries);
    analysis
}
