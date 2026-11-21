//! Reviewed in-place SDK algorithms. Callback effects remain ordinary checked
//! calls; algorithms with allocation/growth are intentionally absent.
use super::*;
fn identity(r: &Resolver<'_>, callee: Ref) -> Option<String> {
    if let Some(i) = r.symbol(callee) {
        let symbol = &r.symbols[i];
        if symbol.constant {
            if let Some(node) = symbol.value {
                let source = &r.sources[symbol.reference.file];
                if source.node(node).kind == "Procedure" {
                    let root = source.info["odin_root"].as_str()?;
                    if source.path.parent()
                        == Some(std::path::Path::new(root).join("core/slice").as_path())
                    {
                        return source
                            .group(symbol.reference.node, "names")
                            .first()
                            .map(|node| source.text(*node).into());
                    }
                    return None;
                }
            }
        }
    }
    r.standard_library(callee, "core:slice")
        .then(|| r.library_alias_member(callee, "core:slice"))
        .flatten()
}
// Only lengths fixed by types or explicit slice bounds are used here. Mutable
// slice initializers never establish a lasting length fact.
fn static_length(r: &mut Resolver<'_>, at: Ref, bindings: &Substitutions) -> Option<i128> {
    let typ = r.specialized_type(at, bindings);
    if let Type::Array { length, .. } | Type::Vector { length, .. } = indexed_type(&typ) {
        return *length;
    }
    let source = &r.sources[at.file];
    let node = source.node(at.node);
    if node.kind == "Compound" && source.group(at.node, "elems").is_empty() {
        return Some(0);
    }
    if node.kind == "Slice" {
        let low = source
            .role(at.node, "low")
            .map(|node| r.context_integer(Ref { node, ..at }, bindings))
            .unwrap_or(Some(0))?;
        let high = source
            .role(at.node, "high")
            .map(|node| r.context_integer(Ref { node, ..at }, bindings))
            .unwrap_or_else(|| {
                let node = source.role(at.node, "expr")?;
                let typ = r.specialized_type(Ref { node, ..at }, bindings);
                match indexed_type(&typ) {
                    Type::Array { length, .. } | Type::Vector { length, .. } => *length,
                    _ => None,
                }
            })?;
        return high.checked_sub(low).filter(|len| *len >= 0);
    }
    None
}
impl Check<'_, '_> {
    pub(super) fn borrowed_builtin(&mut self, w: &Work, at: Ref, name: &str) -> Result<bool> {
        let fixed_only = [
            "append",
            "non_zero_append",
            "append_nothing",
            "resize",
            "non_zero_resize",
            "inject_at",
            "assign_at",
        ];
        let removal = [
            "pop",
            "pop_safe",
            "pop_front",
            "pop_front_safe",
            "ordered_remove",
            "unordered_remove",
        ];
        if name != "clear" && !fixed_only.contains(&name) && !removal.contains(&name) {
            return Ok(false);
        }
        let Some(node) = self.r.sources[at.file]
            .group(at.node, "args")
            .first()
            .copied()
        else {
            return Ok(false);
        };
        let node = self.r.sources[at.file].role(node, "value").unwrap_or(node);
        let typ = self.r.specialized_type(Ref { node, ..at }, &w.bindings);
        let storage = indexed_type(&typ);
        let allowed = matches!(storage, Type::Fixed { .. })
            || !fixed_only.contains(&name) && matches!(storage, Type::Dynamic(_))
            || name == "clear" && matches!(storage, Type::Map { .. });
        if !allowed {
            return Ok(false);
        }
        let elements = match storage {
            Type::Map { key, value } => vec![key.as_ref(), value.as_ref()],
            _ => storage.element().into_iter().collect(),
        };
        if elements
            .iter()
            .any(|typ| self.value_storage(typ, &mut HashSet::new(), true))
        {
            return Err(self.error(
                w,
                at,
                "collection copies or cleanup may allocate for the element type",
            ));
        }
        self.io
            .verify_sdk(self.r, self.overlays)
            .map_err(|reason| self.error(w, at, &reason))?;
        Ok(true)
    }

    pub(super) fn collection_call(&mut self, w: &Work, at: Ref, callee: Ref) -> Result<bool> {
        let Some(name) = identity(self.r, callee) else {
            return Ok(false);
        };
        if ![
            "sort",
            "sort_by",
            "sort_by_with_data",
            "sort_by_cmp",
            "sort_by_cmp_with_data",
            "stable_sort",
            "stable_sort_by",
            "stable_sort_by_with_data",
            "stable_sort_by_cmp",
            "stable_sort_by_cmp_with_data",
            "sort_by_key",
            "reverse_sort_by_key",
            "sort_by_generic_cmp",
        ]
        .contains(&name.as_str())
        {
            return Ok(false);
        }
        let plans = self.r.call_plans(at, &w.bindings);
        let Some(plan) = plans.first().filter(|_| plans.len() == 1) else {
            return Ok(false);
        };
        self.io
            .verify_sdk(self.r, self.overlays)
            .map_err(|reason| self.error(w, at, &reason))?;
        let Some(data) = plan.arguments.first().and_then(|v| v.first()).copied() else {
            return Ok(false);
        };
        let typ = self.r.specialized_type(data, &plan.bindings);
        let Some(element) = typ.element().cloned() else {
            return Ok(false);
        };
        let known_short = static_length(self.r, data, &plan.bindings).is_some_and(|n| n < 2);
        if !known_short && self.value_storage(&element, &mut HashSet::new(), true) {
            return Err(self.error(
                w,
                at,
                "in-place collection elements have unproven copy/comparison effects",
            ));
        }
        if !known_short && name.contains("_by") {
            let Some(callback) = plan.arguments.get(1).and_then(|v| v.first()).copied() else {
                return Ok(false);
            };
            let Fact::Function(target) = self.flow.fact(self.r, callback, &plan.bindings) else {
                return Err(self.error(
                    w,
                    at,
                    "collection callback target is unknown and may allocate",
                ));
            };
            let source = &self.r.sources[target.file];
            let Some(signature) = source.role(target.node, "type") else {
                return Ok(false);
            };
            let key = name.ends_with("_by_key");
            let generic = name == "sort_by_generic_cmp";
            let mut types = if generic {
                vec![Type::scalar("rawptr"), Type::scalar("rawptr")]
            } else if key {
                vec![element.clone()]
            } else {
                vec![element.clone(), element.clone()]
            };
            let mut arguments = types.iter().cloned().map(Fact::Typed).collect::<Vec<_>>();
            if generic || name.ends_with("_with_data") {
                types.push(Type::scalar("rawptr"));
                // A comparator can run repeatedly. Pointee contents are not
                // assumed constant between invocations, even on the first call.
                let value = plan
                    .arguments
                    .get(2)
                    .and_then(|v| v.first())
                    .map(|arg| self.flow.fact(self.r, *arg, &plan.bindings))
                    .unwrap_or_default();
                arguments.push(if value == Fact::Nil {
                    Fact::Nil
                } else {
                    Fact::Unknown
                });
            }
            let mut bindings = w.bindings.clone();
            bindings.extend(self.r.parameter_bindings(
                Ref {
                    node: signature,
                    ..target
                },
                &types,
            ));
            let path = self.traces.len();
            self.traces.push((at, w.path));
            self.queue.push_back(Work {
                at: target,
                bindings,
                arguments,
                root: w.root,
                path: Some(path),
                procedure: true,
            });
        }
        for group in &plan.arguments {
            for arg in group {
                let value = self.flow.fact(self.r, *arg, &plan.bindings);
                self.flow.forget_addresses(&value);
            }
        }
        self.flow.opaque_write();
        crate::profile::count("no_alloc.collection_calls", 1);
        Ok(true)
    }
}
