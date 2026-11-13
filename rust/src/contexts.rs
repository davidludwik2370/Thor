//! Contextual literals use type witnesses emitted in the declaration's scope.
use super::*;
#[derive(Clone, Debug, PartialEq)]
pub struct ParameterWitness {
    pub declaration: Ref,
    pub annotation: Ref,
    pub used: Vec<(String, String)>,
}
#[derive(Clone, Debug, PartialEq)]
pub struct ContextHint {
    pub annotation: Ref,
    pub values: Vec<Type>,
    pub witnesses: Vec<Option<(Ref, bool, usize)>>,
    pub plain_array: bool,
}
impl Resolver<'_> {
    pub fn prepare_contexts(&mut self) {
        let _phase = crate::profile::span("analysis.contexts");
        if self.contexts_ready {
            crate::profile::count("resolver.context_reuses", 1);
            return;
        }
        let calls = self
            .sources
            .iter()
            .enumerate()
            .filter(|(_, s)| !s.shared_native)
            .flat_map(|(file, s)| {
                s.nodes
                    .iter()
                    .enumerate()
                    .filter(|(id, n)| n.kind == "Call" && s.group(NodeId(*id), "args").iter().any(|arg|
                        std::iter::once(*arg).chain(s.descendants(*arg)).any(|node|
                            s.node(node).kind == "Compound" && s.node(node).spans.get("open")
                                .is_some_and(|span| s.slice(*span) == "["))))
                    .map(move |(node, _)| Ref {
                        file,
                        node: NodeId(node),
                    })
            })
            .collect::<Vec<_>>();
        if calls.is_empty() {
            self.contexts_ready = !self.has_demanded_packages();
            return;
        }
        // Witnesses are prepared incrementally; cache only the completed view.
        self.invalidate_queries();
        let memoized = self.metadata.take().is_some();
        for call in calls {
            let s = &self.sources[call.file];
            let plans = self.call_plans(call, &Substitutions::new());
            if plans.len() != 1 {
                continue;
            }
            let plan = &plans[0];
            let owner = &self.sources[plan.procedure.file];
            if owner.shared_native && !owner.collection_roots.get("thor")
                .is_some_and(|runtime| owner.path.starts_with(runtime))
            {
                continue;
            }
            let Some(declaration) = owner
                .node(plan.procedure.node)
                .parent
                .filter(|node| owner.node(*node).kind == "Declaration")
            else {
                continue;
            };
            for ((_, annotation), args) in self
                .fields(plan.signature, "params")
                .into_iter()
                .zip(&plan.arguments)
            {
                let Some(annotation) = annotation else {
                    continue;
                };
                let source = &self.sources[annotation.file];
                if source.node(annotation.node).kind == "PolyType"
                    && source.role(annotation.node, "specialization").is_none()
                {
                    continue;
                }
                let mut generics = BTreeMap::new();
                for node in owner.descendants(plan.signature.node) {
                    if owner.node(node).kind != "PolyType" {
                        continue;
                    }
                    let name = owner.names.get(owner.node(node).name).to_string();
                    let parent = owner.node(node).parent;
                    let kind = if parent.is_some_and(|p| {
                        owner.node(p).kind == "Field" && owner.group(p, "names").contains(&node)
                    }) {
                        parent
                            .and_then(|p| owner.role(p, "type"))
                            .map(|typ| {
                                if owner.node(typ).kind == "TypeidType" {
                                    "typeid".to_string()
                                } else {
                                    owner.text(typ).to_string()
                                }
                            })
                            .unwrap_or("typeid".into())
                    } else if parent.is_some_and(|p| {
                        ["ArrayType", "DynamicArrayType"].contains(&owner.node(p).kind.as_str())
                            && (owner.role(p, "len") == Some(node)
                                || owner.role(p, "capacity") == Some(node))
                    }) {
                        "int".into()
                    } else {
                        "typeid".into()
                    };
                    generics.entry(name).or_insert(kind);
                }
                let mut used = BTreeSet::new();
                Self::context_identifiers(source, annotation.node, &mut used);
                let used = generics
                    .into_iter()
                    .filter(|(name, _)| used.contains(name))
                    .collect::<Vec<_>>();
                let values = used
                    .iter()
                    .map(|(name, _)| plan.bindings.get(name).cloned().unwrap_or(Type::Unknown))
                    .collect::<Vec<_>>();
                if values.iter().any(|typ| !typ.resolved()) {
                    continue;
                }
                let witnesses = values
                    .iter()
                    .map(|wanted| {
                        for ((_, annotation), group) in self
                            .fields(plan.signature, "params")
                            .into_iter()
                            .zip(&plan.arguments)
                        {
                            let type_argument = annotation.is_some_and(|r| {
                                self.sources[r.file].node(r.node).kind == "TypeidType"
                            });
                            for arg in group {
                                if arg.file != call.file {
                                    continue;
                                }
                                let mut typ = self.specialized_type(*arg, &Substitutions::new());
                                for depth in 0..16 {
                                    if typ == *wanted {
                                        return Some((*arg, type_argument, depth));
                                    }
                                    let Some(elem) = typ.element() else {
                                        break;
                                    };
                                    typ = elem.clone();
                                }
                            }
                        }
                        None
                    })
                    .collect::<Vec<_>>();
                for arg in args {
                    let s = &self.sources[arg.file];
                    if std::iter::once(arg.node)
                        .chain(s.descendants(arg.node))
                        .any(|node| {
                            s.node(node).kind == "Compound"
                                && s.node(node)
                                    .spans
                                    .get("open")
                                    .is_some_and(|span| s.slice(*span) == "[")
                        })
                    {
                        let plain_array = matches!(
                            self.specialized_type(annotation, &plan.bindings),
                            Type::Array { .. }
                        );
                        self.context_hints.insert(
                            *arg,
                            ContextHint {
                                annotation,
                                values: values.clone(),
                                witnesses: witnesses.clone(),
                                plain_array,
                            },
                        );
                        self.parameter_witnesses.insert(
                            annotation,
                            ParameterWitness {
                                declaration: Ref {
                                    file: plan.procedure.file,
                                    node: declaration,
                                },
                                annotation,
                                used: used.clone(),
                            },
                        );
                    }
                }
            }
        }
        self.types.clear();
        if memoized { self.metadata = Some(Metadata::default()); }
        self.contexts_ready = !self.has_demanded_packages();
    }
    fn context_identifiers(source: &Source, node: NodeId, used: &mut BTreeSet<String>) {
        let n = source.node(node);
        if n.kind == "PolyType" {
            if let Some(typ) = source.role(node, "specialization") {
                Self::context_identifiers(source, typ, used);
            } else {
                used.insert(source.names.get(n.name).into());
            }
            return;
        }
        if n.kind == "Ident" {
            used.insert(source.text(node).into());
        }
        for child in &n.children {
            Self::context_identifiers(source, *child, used);
        }
    }
}
