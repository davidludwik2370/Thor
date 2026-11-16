//! Python Resolver.lookup's using projections, represented by symbol IDs.
//! Existing source nodes and type algorithms are retained. Aliases refer to
//! their containing storage instead of becoming independent local values.
use super::*;

#[derive(Clone, Debug)]
pub struct Projection {
    pub owner: Ref,
    pub member: String,
}
#[derive(Clone)]
struct UsingOwner {
    owner: Ref,
    declaration: Ref,
    scope: NodeId,
}
impl Resolver<'_> {
    pub(super) fn prepare_projections(&mut self) {
        let _phase = crate::profile::span("analysis.using_projections");
        let mut using: HashMap<(usize, NodeId), Vec<UsingOwner>> = HashMap::new();
        for (file, s) in self.sources.iter().enumerate() {
            for node in &self.index.promotions[file] {
                let r = Ref { file, node: *node };
                let n = s.node(*node);
                let owners = if n.kind == "Using" {
                    s.group(r.node, "values").to_vec()
                } else if n.data["using_field"] == true
                    && matches!(n.kind.as_str(), "Declaration" | "Field")
                {
                    if n.kind == "Field"
                        && s.ancestors(r.node)
                            .find(|node| {
                                matches!(
                                    s.node(*node).kind.as_str(),
                                    "ProcedureType" | "StructType"
                                )
                            })
                            .is_none_or(|node| s.node(node).kind != "ProcedureType")
                    {
                        continue;
                    }
                    s.group(r.node, "names").to_vec()
                } else {
                    continue;
                };
                for node in owners {
                    let owner = Ref { file, node };
                    let scope = if n.kind == "Field" {
                        self.symbol(owner)
                            .map(|id| self.symbols[id].scope)
                            .unwrap_or_else(|| super::scope(s, r.node))
                    } else {
                        super::scope(s, r.node)
                    };
                    using.entry((file, scope)).or_default().push(UsingOwner {
                        owner,
                        declaration: r,
                        scope,
                    });
                }
            }
        }
        if using.is_empty() {
            return;
        }
        // A using declaration in an unrelated SDK file cannot affect this
        // reference. Group package owners once, preserving source-file order.
        let package_using = self
            .index
            .members
            .iter()
            .map(|members| {
                members
                    .iter()
                    .flat_map(|file| {
                        using
                            .get(&(*file, NodeId(0)))
                            .into_iter()
                            .flatten()
                            .cloned()
                    })
                    .collect::<Vec<_>>()
            })
            .collect::<Vec<_>>();
        let using_files = using.keys().map(|(file, _)| *file).collect::<HashSet<_>>();
        let references = self
            .index
            .expression_names
            .iter()
            .enumerate()
            .filter(|(file, _)| using_files.contains(file)
                || !package_using[self.index.package_for[*file]].is_empty())
            .flat_map(|(file, nodes)| nodes.iter().map(move |node| Ref { file, node: *node }))
            .filter_map(|reference| {
                crate::profile::count("projection.scope_queries", 1);
                let visible = self.index.scopes(reference)
                    .any(|scope| {
                        if scope == NodeId(0) {
                            !package_using[self.index.package_for[reference.file]].is_empty()
                        } else {
                            using.contains_key(&(reference.file, scope))
                        }
                    });
                visible.then(|| (reference, self.index.scopes(reference).collect::<Vec<_>>()))
            })
            .collect::<Vec<_>>();
        let mut projected: HashMap<(Ref, String), usize> = HashMap::new();
        // A later `using inner` can refer to an earlier promoted record field.
        // Resolve those aliases with the same field/type lookup until stable.
        loop {
            let mut changed = false;
            for (reference, scopes) in &references {
                crate::profile::count("projection.reference_visits", 1);
                if self.using_symbols.contains_key(reference) {
                    continue;
                }
                let s = &self.sources[reference.file];
                let name = s.text(reference.node).to_string();
                let direct = self.lookup(*reference, &name);
                'scopes: for scope in scopes.iter().copied() {
                    if let Some(id) = direct {
                        let symbol = &self.symbols[id];
                        if symbol.scope == scope
                            && (symbol.reference.file == reference.file
                                || scope == NodeId(0)
                                    && self.index.package_for[symbol.reference.file]
                                        == self.index.package_for[reference.file])
                        {
                            break;
                        }
                    }
                    let owners = if scope == NodeId(0) {
                        package_using[self.index.package_for[reference.file]].clone()
                    } else {
                        using
                            .get(&(reference.file, scope))
                            .cloned()
                            .unwrap_or_default()
                    };
                    for entry in owners {
                        let owner_source = &self.sources[entry.owner.file];
                        let declaration = owner_source.node(entry.declaration.node);
                        if scope != NodeId(0) && entry.declaration.file == reference.file
                            && (declaration.kind == "Using" || declaration.kind == "Declaration" && declaration.mutable)
                            && declaration.span.end > s.node(reference.node).span.start {
                            continue;
                        }
                        if !self.active_definition(entry.declaration) {
                            continue;
                        }
                        if owner_source.node(entry.owner.node).kind == "Ident"
                            && self
                                .lookup(entry.owner, owner_source.text(entry.owner.node))
                                .is_none()
                        {
                            let imported = self.index.imports[entry.owner.file]
                                .get(owner_source.text(entry.owner.node))
                                .and_then(|paths| paths.first());
                            if let Some(symbol) = imported
                                .and_then(|path| self.package_symbol(entry.owner, path, &name))
                            {
                                self.using_symbols.insert(*reference, symbol);
                                changed = true;
                                break 'scopes;
                            }
                        }
                        let typ = self.type_of(entry.owner);
                        if self.member_path(&typ, &name).is_none() {
                            continue;
                        }
                        if self.symbol(entry.owner).is_none() {
                            continue;
                        }
                        let key = (entry.owner, name.clone());
                        let symbol = if let Some(symbol) = projected.get(&key) {
                            *symbol
                        } else {
                            let symbol = self.symbols.len();
                            Arc::make_mut(&mut self.symbols).push(Symbol {
                                name: name.clone(),
                                reference: entry.declaration,
                                name_node: entry.owner.node,
                                scope: entry.scope,
                                annotation: None,
                                value: None,
                                index: 0,
                                immutable: false,
                                constant: false,
                                parameter: false,
                                result: false,
                                projection: Some(Projection {
                                    owner: entry.owner,
                                    member: name.clone(),
                                }),
                            });
                            projected.insert(key, symbol);
                            symbol
                        };
                        self.using_symbols.insert(*reference, symbol);
                        changed = true;
                        break 'scopes;
                    }
                }
            }
            if !changed {
                break;
            }
            self.invalidate_queries();
        }
    }
    pub fn projection(&self, r: Ref) -> Option<Projection> {
        self.symbol(r)
            .and_then(|id| self.symbols[id].projection.clone())
    }
    pub fn projected_type(&mut self, projection: &Projection, bindings: &Substitutions) -> Type {
        let typ = self.specialized_type(projection.owner, bindings);
        self.member_type(&typ, &projection.member)
    }
    pub fn projection_indirect(&mut self, projection: &Projection) -> bool {
        let mut typ = self.type_of(projection.owner);
        let Some(path) = self.member_path(&typ, &projection.member) else {
            return false;
        };
        for field in path {
            while let Type::Nominal(_, base) = typ {
                typ = *base;
            }
            if matches!(typ, Type::Pointer(_)) {
                return true;
            }
            typ = self.member_type(&typ, &field);
        }
        false
    }
}
