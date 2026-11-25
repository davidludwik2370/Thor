//! A bounded syntactic proof for monotonic owned dynamic-array returns. The
//! header starts empty, only append can take its address, and every return
//! exports that same header. This does not bless arbitrary escaping aliases.
use super::*;

impl Analysis<'_, '_> {
    pub(super) fn owning_results(&mut self, proc: Ref, bindings: &Substitutions) -> Vec<bool> {
        let key = (proc, bindings.clone());
        if let Some(result) = self.summaries.owners.get(&key) {
            return result.clone();
        }
        let result = self.prove_owning_results(proc, bindings);
        self.summaries.owners.insert(key, result.clone());
        result
    }
    fn prove_owning_results(&mut self, proc: Ref, bindings: &Substitutions) -> Vec<bool> {
        let Some(signature) = self.role(proc, "type") else {
            return vec![];
        };
        let fields = self.r.fields(signature, "results");
        let mut result = vec![false; fields.len()];
        if !fields.iter().any(|(_, t)| {
            t.is_some_and(|t| {
                matches!(
                    base(&self.r.specialized_type(t, bindings)),
                    Type::Dynamic(_)
                )
            })
        }) {
            return result;
        }
        let Some(body) = self.role(proc, "body") else {
            return result;
        };
        let nodes = self.r.sources[body.file]
            .descendants(body.node)
            .take(4097)
            .collect::<Vec<_>>();
        if nodes.len() > 4096 {
            return result;
        }
        let nodes = nodes
            .into_iter()
            .filter(|n| {
                self.r.sources[body.file]
                    .ancestors(*n)
                    .find(|p| self.r.sources[body.file].node(*p).kind == "Procedure")
                    == Some(proc.node)
            })
            .map(|node| Ref { node, ..body })
            .collect::<Vec<_>>();
        let named = self
            .role(signature, "results")
            .map(|list| {
                self.r.sources[list.file]
                    .node(list.node)
                    .children
                    .clone()
                    .into_iter()
                    .flat_map(|node| self.group(Ref { node, ..list }, "names"))
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        for (index, (_, annotation)) in fields.into_iter().enumerate() {
            let Some(annotation) = annotation else {
                continue;
            };
            let typ = self.r.specialized_type(annotation, bindings);
            let Type::Dynamic(element) = base(&typ) else {
                continue;
            };
            if !self.inline_type(element, 0) {
                continue;
            }
            let mut variable = None;
            let mut saw_return = false;
            let mut okay = true;
            for at in &nodes {
                if self.kind(*at) != "Return" {
                    continue;
                }
                saw_return = true;
                let values = self.group(*at, "values");
                let value = if values.is_empty() {
                    named.get(index).copied()
                } else {
                    values.get(index).copied()
                };
                let Some(value) = value.filter(|v| self.kind(*v) == "Ident") else {
                    okay = false;
                    break;
                };
                let Some(symbol) = self.symbol(value) else {
                    okay = false;
                    break;
                };
                if variable.is_some_and(|v| v != symbol) {
                    okay = false;
                    break;
                }
                variable = Some(symbol);
            }
            if !okay || !saw_return {
                continue;
            }
            let Some(variable) = variable else {
                continue;
            };
            let Some(symbol) = self.r.symbol(variable) else {
                continue;
            };
            let symbol = &self.r.symbols[symbol];
            if symbol.parameter || symbol.scope == NodeId(0) || symbol.value.is_some() {
                continue;
            }
            if self
                .r
                .attributes(symbol.reference)
                .iter()
                .any(|(name, _)| matches!(name.as_str(), "static" | "thread_local"))
            {
                continue;
            }
            for at in &nodes {
                if self.kind(*at) != "Ident" || self.symbol(*at) != Some(variable) {
                    continue;
                }
                let s = &self.r.sources[at.file];
                let Some(parent) = s.node(at.node).parent else {
                    okay = false;
                    break;
                };
                if s.node(parent).kind == "Field"
                    || s.node(parent).kind == "Declaration"
                        && s.group(parent, "names").contains(&at.node)
                {
                    continue;
                }
                if s.node(parent).kind == "Return" {
                    continue;
                }
                let mut cursor = at.node;
                let mut accepted = false;
                for _ in 0..32 {
                    let Some(parent) = s.node(cursor).parent else {
                        break;
                    };
                    let owner = Ref {
                        node: parent,
                        ..*at
                    };
                    match s.node(parent).kind.as_str() {
                        "Unary" if s.node(parent).op == "&" => {
                            let call = s.node(parent).parent.map(|node| Ref { node, ..*at });
                            if cursor != at.node
                                || !call.is_some_and(|call| {
                                    self.kind(call) == "Call"
                                        && self.group(call, "args").first() == Some(&owner)
                                })
                            {
                                break;
                            }
                            let call = call.unwrap();
                            let mut arena = crate::borrowflow::Arena::default();
                            let builtin = crate::borroweval::Evaluator {
                                resolver: self.r,
                                arena: &mut arena,
                            }
                            .builtin(call);
                            accepted = builtin
                                .as_deref()
                                .is_some_and(|name| matches!(name, "append" | "append_elem"));
                            break;
                        }
                        "Call" => {
                            let mut arena = crate::borrowflow::Arena::default();
                            let builtin = crate::borroweval::Evaluator {
                                resolver: self.r,
                                arena: &mut arena,
                            }
                            .builtin(owner);
                            accepted = builtin
                                .as_deref()
                                .is_some_and(|name| matches!(name, "len" | "cap"));
                            break;
                        }
                        "Range" => {
                            accepted = true;
                            break;
                        }
                        "Assignment" => {
                            let lhs = s.group(parent, "lhs");
                            accepted = cursor != at.node
                                && lhs
                                    .iter()
                                    .any(|lhs| s.node(*lhs).span.contains(s.node(at.node).span))
                                || lhs.len() == 1 && s.text(lhs[0]) == "_";
                            break;
                        }
                        "Index" | "Slice" | "Paren" | "Binary" | "FieldValue" => {
                            cursor = parent;
                        }
                        _ => break,
                    }
                }
                if !accepted {
                    okay = false;
                    break;
                }
            }
            result[index] = okay;
        }
        result
    }
}
