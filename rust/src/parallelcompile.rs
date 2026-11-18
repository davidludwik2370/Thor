//! Reviewed scheduler boundary. Only the installed pmap implementation receives
//! this contract; the selected user callback is still checked transitively.
use crate::semantics::{Ref, Resolver, Substitutions};

/// Remove only the compiler-owned argument from native pmap diagnostics. The
/// SDK location scopes this cleanup so an ordinary user parameter keeps its name.
pub(crate) fn visible_diagnostics(messages: &mut Vec<serde_json::Value>) {
    if !messages
        .iter()
        .filter_map(serde_json::Value::as_str)
        .any(|text| text.replace('\\', "/").contains("/parallel/pmap.thor"))
    {
        return;
    }
    for message in messages.iter_mut() {
        if let Some(text) = message.as_str() {
            let mut text = text.to_string();
            for suffix in [": bool", "=true", "=false"] {
                text = text.replace(&format!(", $__thor_pmap_spread{suffix}"), "");
            }
            text = text
                .lines()
                .filter(|line| !line.trim_start().starts_with("• __thor_pmap_spread ="))
                .collect::<Vec<_>>()
                .join("\n");
            *message = serde_json::Value::String(text);
        }
    }
    messages.retain(|message| !message.as_str().is_some_and(|text| text.trim().is_empty()));
}

pub(crate) fn has_mode_parameter(r: &Resolver<'_>, signature: Ref) -> bool {
    let source = &r.sources[signature.file];
    source
        .path
        .file_name()
        .is_some_and(|name| name == "pmap.thor")
        && source
            .collection_roots
            .get("thor")
            .is_some_and(|root| source.path.parent() == Some(root.join("parallel").as_path()))
}

pub(crate) fn is_call(r: &Resolver<'_>, call: Ref) -> bool {
    let source = &r.sources[call.file];
    source.node(call.node).kind == "Call"
        && source.role(call.node, "callee").is_some_and(|node| {
            crate::evaluation::library_member(
                r,
                Ref {
                    file: call.file,
                    node,
                },
                "thor:parallel",
            )
            .as_deref()
                == Some("pmap")
        })
}

pub(crate) struct Callback {
    pub argument: Ref,
    pub procedure: Ref,
    pub bindings: Substitutions,
    pub defaults: Vec<Ref>,
    pub returns_values: bool,
}

pub(crate) fn callback(r: &mut Resolver<'_>, call: Ref, outer: &Substitutions) -> Option<Callback> {
    if !is_call(r, call) {
        return None;
    }
    let callee = Ref {
        file: call.file,
        node: r.sources[call.file].role(call.node, "callee")?,
    };
    for candidate in r.candidates(callee) {
        let signature = Ref {
            node: r.sources[candidate.file]
                .role(candidate.node, "type")
                .unwrap_or(candidate.node),
            ..candidate
        };
        for query in crate::resulttypes::default_queries(r, signature) {
            let (mut bindings, argument, _) =
                crate::resulttypes::bind_default(r, call, signature, &query)?;
            bindings.extend(outer.clone());
            // Mutable procedure values cannot inherit an initializer's effects.
            if r.symbol(argument).is_some_and(|index| {
                let symbol = &r.symbols[index];
                !symbol.constant || symbol.immutable || symbol.parameter || symbol.result
            }) {
                return None;
            }
            let result =
                crate::resulttypes::resolve_default(r, call, query.call, &bindings, argument).0?;
            let owner = &r.sources[result.signature.file];
            let procedure = owner
                .node(result.signature.node)
                .parent
                .filter(|node| owner.node(*node).kind == "Procedure")?;
            let types = result.arguments;
            let mut position = 0;
            let mut defaults = vec![];
            let owner = &r.sources[result.signature.file];
            if let Some(params) = owner.role(result.signature.node, "params") {
                for field in &owner.node(params).children {
                    let count = owner.group(*field, "names").len().max(1);
                    if position + count > types.len() {
                        if let Some(node) = owner.role(*field, "default") {
                            defaults.push(Ref {
                                file: result.signature.file,
                                node,
                            });
                        }
                    }
                    position += count;
                }
            }
            let bindings = r.parameter_bindings(result.signature, &types);
            return Some(Callback {
                argument,
                procedure: Ref {
                    node: procedure,
                    ..result.signature
                },
                bindings,
                defaults,
                returns_values: !result.fields.is_empty(),
            });
        }
    }
    None
}

pub(crate) fn explicit_allocator(r: &Resolver<'_>, call: Ref, returns_values: bool) -> bool {
    let source = &r.sources[call.file];
    let mut positional = 0;
    source.group(call.node, "args").iter().any(|arg| {
        if source.node(*arg).kind == "FieldValue" {
            source
                .role(*arg, "field")
                .is_some_and(|field| matches!(source.text(field), "allocator" | "worker_allocator"))
        } else {
            positional += 1;
            positional >= if returns_values { 5 } else { 4 }
        }
    })
}
