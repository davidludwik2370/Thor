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
    fn expression_name(&self, r: Ref) -> bool {
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        if n.kind != "Ident" {
            return false;
        }
        if let Some(parent) = n.parent {
            let p = s.node(parent);
            if matches!(
                p.kind.as_str(),
                "Declaration" | "Field" | "Range" | "TypeSwitch"
            ) && s.group(parent, "names").contains(&r.node)
            {
                return false;
            }
            if matches!(p.kind.as_str(), "Selector" | "FieldValue")
                && s.role(parent, "field") == Some(r.node)
            {
                return false;
            }
            if matches!(
                p.kind.as_str(),
                "ImplicitSelector" | "EnumType" | "PolyType"
            ) {
                return false;
            }
        }
        true
    }
    pub(super) fn prepare_projections(&mut self) {
        let mut using: HashMap<(usize, NodeId), Vec<UsingOwner>> = HashMap::new();
        let mut references = vec![];
        for (file, s) in self.sources.iter().enumerate() {
            for (index, n) in s.nodes.iter().enumerate() {
                let r = Ref {
                    file,
                    node: NodeId(index),
                };
                if self.expression_name(r) {
                    references.push(r);
                }
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
        let mut projected: HashMap<(Ref, String), usize> = HashMap::new();
        // A later `using inner` can refer to an earlier promoted record field.
        // Resolve those aliases with the same field/type lookup until stable.
        loop {
            let mut changed = false;
            for reference in &references {
                if self.using_symbols.contains_key(reference) {
                    continue;
                }
                let s = &self.sources[reference.file];
                let name = s.text(reference.node).to_string();
                let direct = self.lookup(*reference, &name);
                let scopes = s
                    .ancestors(reference.node)
                    .filter(|node| is_scope(s, *node) || s.node(*node).kind == "ProcedureType")
                    .chain(std::iter::once(NodeId(0)))
                    .collect::<Vec<_>>();
                'scopes: for scope in scopes {
                    if let Some(id) = direct {
                        let symbol = &self.symbols[id];
                        if symbol.scope == scope
                            && (symbol.reference.file == reference.file
                                || scope == NodeId(0)
                                    && self.sources[symbol.reference.file].path.parent()
                                        == s.path.parent())
                        {
                            break;
                        }
                    }
                    let owners = if scope == NodeId(0) {
                        self.sources
                            .iter()
                            .enumerate()
                            .filter(|(_, unit)| unit.path.parent() == s.path.parent())
                            .flat_map(|(file, _)| {
                                using.get(&(file, NodeId(0))).into_iter().flatten()
                            })
                            .cloned()
                            .collect::<Vec<_>>()
                    } else {
                        using
                            .get(&(reference.file, scope))
                            .cloned()
                            .unwrap_or_default()
                    };
                    for entry in owners {
                        let owner_source = &self.sources[entry.owner.file];
                        if !self.active_definition(entry.declaration) {
                            continue;
                        }
                        if owner_source.node(entry.owner.node).kind == "Ident"
                            && self
                                .lookup(entry.owner, owner_source.text(entry.owner.node))
                                .is_none()
                        {
                            let imported = owner_source
                                .nodes
                                .iter()
                                .filter(|n| n.kind == "Import")
                                .find_map(|n| {
                                    let path = crate::lower::unquote(&n.op).ok()?;
                                    let explicit = owner_source.names.get(n.name);
                                    let namespace = if explicit.is_empty() {
                                        path.rsplit(['/', ':']).next().unwrap_or("")
                                    } else {
                                        explicit
                                    };
                                    (namespace == owner_source.text(entry.owner.node))
                                        .then_some(path)
                                });
                            if let Some(symbol) = imported
                                .and_then(|path| self.package_symbol(owner_source, &path, &name))
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
                            self.symbols.push(Symbol {
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
            self.types.clear();
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
