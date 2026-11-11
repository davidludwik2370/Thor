//! Original call paths for diagnostics from checked procedure variants.
use crate::{
    ast::{NodeId, Source, Span},
    semantics::Ref,
};
use serde_json::{json, Value};

pub(crate) fn procedure_owner(sources: &[Source], site: Ref) -> Option<Ref> {
    let source = &sources[site.file];
    let node = std::iter::once(site.node)
        .chain(source.ancestors(site.node))
        .find(|id| source.node(*id).kind == "Procedure")?;
    Some(Ref {
        file: site.file,
        node,
    })
}

pub(crate) fn function_owner(sources: &[Source], site: Ref) -> Option<Ref> {
    let owner = procedure_owner(sources, site)?;
    (sources[owner.file].node(owner.node).data["pure"] == true).then_some(owner)
}

pub(crate) fn function_context<'a>(
    sources: &[Source],
    path: &'a [Ref],
) -> Option<(Ref, &'a [Ref])> {
    path.iter().enumerate().find_map(|(index, &site)| {
        function_owner(sources, site).map(|owner| (owner, &path[index..]))
    })
}

pub(crate) fn procedure_name(sources: &[Source], procedure: Ref) -> &str {
    let source = &sources[procedure.file];
    source
        .ancestors(procedure.node)
        .find(|id| source.node(*id).kind != "Paren")
        .filter(|id| source.node(*id).kind == "Declaration")
        .and_then(|id| source.group(id, "names").first())
        .map(|id| source.text(*id))
        .unwrap_or("anonymous fn")
}

fn callee(sources: &[Source], call: Ref) -> Option<&str> {
    let source = &sources[call.file];
    source.role(call.node, "callee").map(|id| source.text(id))
}

pub(crate) fn diagnostic_span(source: &Source, site: NodeId) -> Span {
    let node = source.node(site);
    let span = node.span;
    if span.is_empty()
        && node.kind == "TypeidType"
        && source.raw[span.start..].starts_with("typeid")
    {
        return Span::new(span.start, span.start + "typeid".len());
    }
    span
}

pub(crate) fn position(sources: &[Source], reference: Ref) -> Value {
    let source = &sources[reference.file];
    let span = diagnostic_span(source, reference.node);
    let (line, column) = source.position(span.start);
    let (end_line, end_column) = source.position(span.end);
    json!({"file":source.path,"offset":span.start,"end_offset":span.end,
           "line":line,"column":column,"end_line":end_line,"end_column":end_column})
}

pub(crate) fn note(sources: &[Source], site: Ref, message: &str) -> Value {
    json!({"pos": position(sources, site), "message": message})
}

fn covers(sources: &[Source], range: &Value, site: Ref) -> bool {
    let source = &sources[site.file];
    let offset = source.node(site.node).span.start as u64;
    range["file"]
        .as_str()
        .is_some_and(|file| source.path == std::path::Path::new(file))
        && range["offset"]
            .as_u64()
            .is_some_and(|start| start <= offset)
        && range["end_offset"].as_u64().is_some_and(|end| offset < end)
}

pub(crate) fn contextualize(
    sources: &[Source],
    error: &mut Value,
    origin: Ref,
    paths: &[(Ref, &[Ref])],
) {
    let mut notes = error["related"].as_array().cloned().unwrap_or_default();
    let mut chains = Vec::new();
    let mut primary = None;
    for &(root, path) in paths {
        let Some((&first, tail)) = path.split_first() else {
            continue;
        };
        let name = procedure_name(sources, root);
        let visible = |site: Ref| !sources[site.file].shared_native;
        let names = std::iter::once(name)
            .chain(
                path.iter()
                    .copied()
                    .filter(|site| visible(*site))
                    .filter_map(|site| callee(sources, site)),
            )
            .collect::<Vec<_>>();
        chains.push(names.join(" -> "));
        let calls = if paths.len() == 1 { tail } else { path };
        for &call in calls.iter().filter(|site| visible(**site)) {
            let Some(target) = callee(sources, call) else {
                continue;
            };
            let owner = procedure_owner(sources, call)
                .map(|site| procedure_name(sources, site))
                .unwrap_or("anonymous proc");
            notes.push(note(
                sources,
                call,
                &format!("'{owner}' calls '{target}' while being checked for fn '{name}'."),
            ));
        }
        if paths.len() == 1 {
            primary = Some((
                first,
                name,
                callee(sources, first).unwrap_or("this procedure"),
            ));
        }
        let signature = sources[root.file]
            .role(root.node, "type")
            .unwrap_or(root.node);
        notes.push(note(sources, Ref { node: signature, ..root },
            &format!("fn '{name}' requires its entire call chain to be pure. Ordinary proc callers do not impose this requirement.")));
    }
    if chains.is_empty() {
        return;
    }
    if let Some((first, name, target)) = primary {
        let message = error["msgs"][0]
            .as_str()
            .unwrap_or("Invalid pure operation")
            .trim_start_matches("Compile time panic: ");
        error["msgs"][0] = json!(format!(
            "Pure function '{name}' cannot call '{target}': {message}"
        ));
        error["pos"] = position(sources, first);
    }
    if !covers(sources, &error["pos"], origin)
        && !notes
            .iter()
            .any(|entry| covers(sources, &entry["pos"], origin))
    {
        let message = if sources[origin.file].shared_native {
            "Purity violation in standard-library implementation."
        } else {
            "Underlying purity violation in this checked implementation."
        };
        notes.push(note(sources, origin, message));
    }
    let mut unique = Vec::new();
    for entry in notes {
        if !unique.contains(&entry) {
            unique.push(entry);
        }
    }
    error["related"] = json!(unique);
    let label = if chains.len() == 1 {
        "Pure call chain"
    } else {
        "Pure call chains"
    };
    if let Some(messages) = error["msgs"].as_array_mut() {
        messages.push(json!(format!("{label}: {}.", chains.join("; "))));
    }
}
