//! Early value lookup after dependency extraction, following prepared_key.
//! Interfaces retain all file syntax except unrelated procedure bodies.
use super::*;
use crate::{
    ast::Source,
    digest::sha256,
    evaluationcache::{canonical, EvaluationCache},
};
use serde_json::json;

pub(crate) fn support(source: &Source, root: &std::path::Path) -> bool {
    source.path.parent().is_some_and(|directory| {
        directory.parent() == Some(root.join("runtime").as_path())
            && directory.file_name().is_some_and(|name| {
                [
                    "eval",
                    "pure",
                    "format",
                    "numeric",
                    "sequence",
                    "sets",
                    "big",
                    "temporaries",
                    "optional",
                ]
                .iter()
                .any(|expected| name == *expected)
            })
    })
}
pub(crate) fn headers(project: &Project) -> Value {
    let mut result = vec![];
    for source in project
        .sources
        .iter()
        .filter(|source| !source.shared_native && !support(source, &project.options.root))
    {
        let mut spans = vec![];
        for (index, node) in source.nodes.iter().enumerate() {
            let id = NodeId(index);
            if node.kind != "Declaration" || source.node(scope(source, id)).kind != "File" {
                continue;
            }
            let values = source.group(id, "values");
            if values.len() == 1 && source.node(values[0]).kind == "Procedure" {
                if let Some(body) = source.role(values[0], "body") {
                    spans.push(source.node(body).span);
                }
            }
        }
        spans.sort_by_key(|span| span.start);
        let mut text = String::new();
        let mut cursor = 0;
        for span in spans {
            text.push_str(&source.raw[cursor..span.start]);
            text.push_str("{/* procedure body */}");
            cursor = span.end;
        }
        text.push_str(&source.raw[cursor..]);
        result.push(json!([source.path, source.excluded, text]));
    }
    Value::Array(result)
}
// These packets contain values rather than helper-local type identities.
// Generic frames, records/unions and opaque values keep the emitted-helper key.
fn prepared_value_type(typ: &Type) -> bool {
    match typ {
        Type::Scalar {name, ..} => !["any", "typeid", "rawptr", "cstring"].contains(&name.as_str()),
        Type::String {name, ..} => name == "string",
        Type::BigInteger | Type::BigRational => true,
        Type::Nominal(_, inner) | Type::Array {elem:inner, ..}
        | Type::Slice(inner) | Type::Dynamic(inner) => prepared_value_type(inner),
        _ => false,
    }
}
impl Scheduler<'_> {
    pub(super) fn prepared_key(
        &mut self,
        node: Ref,
        roots: &[Ref],
        graph: &Closure,
        mode: Mode,
    ) -> Option<String> {
        if !matches!(mode, Mode::Value)
            || !self.cache.borrow().enabled
            || !self
                .specializations
                .relevant(node, &self.resolver)
                .is_empty()
        {
            return None;
        }
        if !prepared_value_type(&self.resolver.type_of(node))
            || graph.procedures.iter().any(|procedure| {
                !crate::specialization::parameters(*procedure, &self.resolver).is_empty()
            })
        {
            return None;
        }
        // Directives can observe the original call/declaration location. Body
        // edits deliberately disappear from headers, so preserve the emitted
        // helper's validation for these calls instead of reusing a stale value.
        if roots.iter().chain(&graph.keep).any(|root| {
            let source = self.source(*root);
            std::iter::once(root.node).chain(source.descendants(root.node))
                .any(|node| source.node(node).kind == "Directive")
        }) {
            return None;
        }
        let mut dependencies = graph
            .keep
            .iter()
            .map(|node| {
                json!([
                    self.source(*node).path,
                    self.kind(*node),
                    self.source(*node).text(node.node),
                    self.group(*node, "attributes")
                        .iter()
                        .map(|attribute| self.source(*attribute).text(attribute.node))
                        .collect::<Vec<_>>()
                ])
            })
            .collect::<Vec<_>>();
        dependencies.sort_by_key(Value::to_string);
        let mut native = vec![];
        let mut support_sources = vec![];
        for source in &self.project.sources {
            let item = json!([source.path, sha256(source.raw.as_bytes())]);
            if source.shared_native {
                native.push(item);
            } else if support(source, &self.project.options.root) {
                support_sources.push(item);
            }
        }
        native.sort_by_key(Value::to_string);
        support_sources.sort_by_key(Value::to_string);
        let mut previous = self
            .results
            .iter()
            .filter(|(node, _)| {
                roots.iter().chain(&graph.keep).any(|root| {
                    root.file == node.file
                        && self
                            .source(*root)
                            .node(root.node)
                            .span
                            .contains(self.source(**node).node(node.node).span)
                })
            })
            .map(|(node, data)| {
                json!([
                    self.source(*node).path,
                    self.source(*node).text(node.node),
                    data
                ])
            })
            .collect::<Vec<_>>();
        previous.sort_by_key(Value::to_string);
        let mut queries = graph
            .queries
            .iter()
            .map(|(query, code)| json!([self.source(*query).text(query.node), code]))
            .collect::<Vec<_>>();
        queries.sort_by_key(Value::to_string);
        let source = self.source(node);
        let scopes = source
            .ancestors(node.node)
            .filter(|id| source.node(*id).kind == "Procedure")
            .filter_map(|id| source.role(id, "type"))
            .map(|id| source.text(id))
            .collect::<Vec<_>>();
        let resources = EvaluationCache::resources(&self.project.sources)?;
        let identity = self
            .cache
            .borrow_mut()
            .identity(&self.project.frontend.odin, &self.project.options.root)?;
        Some(sha256(&canonical(
            &json!({"schema":2,"identity":identity,"entry":self.project.options.entry,
            "flags":self.project.options.flags,"test":self.project.frontend.test,"expression":source.text(node.node),
            "headers":headers(self.project),"dependencies":dependencies,"native":native,"support":support_sources,
            "previous":previous,"queries":queries,"scopes":scopes,"resources":resources}),
        )))
    }
}
