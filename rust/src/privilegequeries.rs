//! Permission roots and fixed-input type properties. Skipping a root does not
//! skip a callee summary requested by a checked caller or readonly argument.
use super::*;

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub(super) enum Property {
    Slice,
    Unresolved,
    Reference,
    NestedSlice,
}

impl Privileges<'_, '_> {
    pub(super) fn can_introduce_view(&mut self, procedure: Ref) -> bool {
        let _phase = crate::profile::span("permissions.view_sources");
        if let Some(value) = self.view_sources.get(&procedure) { return *value; }
        let mut pending = vec![procedure];
        let mut visited = HashSet::new();
        while let Some(current) = pending.pop() {
            if self.discovering && self.r.has_demanded_packages() { return false; }
            if !visited.insert(current) { continue; }
            if let Some(value) = self.view_sources.get(&current) {
                if *value { self.view_sources.insert(procedure, true); return true; }
                continue;
            }
            let (local, callees) = self.view_body(current);
            if local {
                self.view_sources.insert(procedure, true);
                return true;
            }
            pending.extend(callees);
        }
        // Every visited body's complete reachable closure was examined.
        // Cache negatives together so cycles cannot hide a later source.
        if !self.r.has_demanded_packages() {
            for current in visited { self.view_sources.insert(current, false); }
        }
        false
    }
    fn view_body(&mut self, current: Ref) -> (bool, Vec<Ref>) {
        if let Some(body) = self.view_bodies.get(&current) { return body.clone(); }
        crate::profile::count("permissions.view_bodies", 1);
        let mut local = false;
        let mut callees = Vec::new();
        let nodes = self.s(current).descendants(current.node).collect::<Vec<_>>();
        for node in nodes {
            let reference = Ref { file: current.file, node };
            let source = self.s(current);
            if source.ancestors(node).find(|id| source.node(*id).kind == "Procedure")
                .is_some_and(|id| id != current.node) { continue; }
            let mut names = if source.node(node).immutable {
                source.group(node, "names").iter().copied()
                    .map(|node| Ref { file: current.file, node }).collect::<Vec<_>>()
            } else { Vec::new() };
            if source.node(node).kind == "Ident"
                && self.global_view_names.contains(source.text(node)) {
                if let Some(symbol) = self.r.symbol(reference) {
                    let symbol = &self.r.symbols[symbol];
                    if symbol.immutable && self.r.sources[symbol.reference.file]
                        .node(symbol.scope).kind == "File" {
                        names.push(Ref { file: symbol.reference.file, node: symbol.name_node });
                    }
                }
            }
            for name in names {
                let typ = self.r.type_of(name);
                if self.r.frozen_slice_storage(&typ) {
                    local = true;
                    break;
                }
            }
            if local { break; }
            if self.kind(reference) != "Call" { continue; }
            let Some(callee) = self.role(reference, "callee") else { continue; };
            if self.builtin_name(callee, &mut HashSet::new()).is_some()
                || self.r.denotes_type(callee) { continue; }
            callees.extend(self.r.candidates(callee));
            if self.discovering && self.r.has_demanded_packages() { break; }
        }
        let body = (local, callees);
        if !self.r.has_demanded_packages() {
            self.view_bodies.insert(current, body.clone());
        }
        body
    }
    pub(super) fn has_readonly_views(&mut self) -> bool {
        let declarations = self.r.sources.iter().enumerate().flat_map(|(file, source)| {
            source.nodes.iter().enumerate().filter(|(_, node)| node.immutable)
                .flat_map(move |(index, _)| source.group(NodeId(index), "names").iter()
                    .filter(|node| source.text(**node) != "_")
                    .map(move |node| Ref { file, node: *node }))
        }).collect::<Vec<_>>();
        declarations.into_iter().any(|name| {
            // Odin procedure literals cannot capture caller locals. A local
            // view cannot hide in a callback's result unless its procedure can
            // return reference-bearing data. Writable escapes are checked at
            // the operation that attempts them.
            let source = self.s(name);
            if let Some(procedure) = source.ancestors(name.node)
                .find(|node| source.node(*node).kind == "Procedure") {
                let signature = source.role(procedure, "type").unwrap_or(procedure);
                let results = self.r.fields(Ref {file:name.file,node:signature}, "results");
                let can_escape = results.into_iter().any(|(_, annotation)| {
                    let typ = annotation.map(|node| self.r.type_of(node)).unwrap_or(Type::Unknown);
                    self.type_property(&typ, Property::Slice)
                });
                if !can_escape { return false; }
            }
            let typ = self.r.type_of(name);
            let sensitive = self.r.frozen_slice_storage(&typ);
            if sensitive {
                crate::profile::event("permissions.readonly_storage", || serde_json::json!({
                    "file": self.s(name).path, "offset": self.s(name).node(name.node).span.start,
                    "type": typ,
                }));
            }
            sensitive
        })
    }
    pub(super) fn needs_root(&mut self, proc: Ref) -> bool {
        let _phase = crate::profile::span("permissions.root_selection");
        let source = self.s(proc);
        if source.node(proc.node).data["pure"] == true {
            return true;
        }
        // Explicit contracts also feed checked-helper code generation, even
        // when no readonly caller happens to demand this procedure's summary.
        let signature = source
            .role(proc.node, "type")
            .map(|node| Ref {
                file: proc.file,
                node,
            })
            .unwrap_or(proc);
        if self
            .params(signature)
            .iter()
            .any(|(name, _)| self.explicit.contains(name))
        {
            crate::profile::count("permissions.explicit_roots", 1);
            return true;
        }
        for node in source.descendants(proc.node) {
            let reference = Ref {
                file: proc.file,
                node,
            };
            let n = source.node(node);
            if n.immutable || matches!(n.kind.as_str(), "Interpolation" | "Comprehension") {
                return true;
            }
            if n.kind == "Call" {
                let Some(callee) = self.role(reference, "callee") else {
                    return true;
                };
                if self.builtin_name(callee, &mut HashSet::new()).is_some()
                    || self.r.denotes_type(callee)
                {
                    continue;
                }
                let targets = self.r.candidates(callee);
                // Generic bodies can contain instantiation-dependent immutable
                // storage checks even when the result is void or scalar. An
                // overload set likewise needs the selected native call context.
                if targets.len() != 1 {
                    return true;
                }
                let target = targets[0];
                let target_source = self.s(target);
                let signature = target_source
                    .role(target.node, "type")
                    .unwrap_or(target.node);
                if target_source
                    .descendants(signature)
                    .any(|node| target_source.node(node).kind == "PolyType")
                {
                    return true;
                }
                // Omitted defaults are not descendants of the call. A
                // nonliteral default can bring readonly global data into an
                // otherwise reference-free void/scalar call.
                if target_source
                    .role(signature, "params")
                    .is_some_and(|params| {
                        target_source.node(params).children.iter().any(|field| {
                            target_source
                                .role(*field, "default")
                                .is_some_and(|value| target_source.node(value).kind != "Literal")
                        })
                    })
                {
                    return true;
                }
                let typ = self.r.type_of(reference);
                if !typ.is_unknown() {
                    if self.type_property(&typ, Property::Reference) {
                        return true;
                    }
                    continue;
                }
                // A resolved void call cannot return a readonly reference.
                // Generic/indirect/unknown results keep the ordinary path.
                let targets = self.r.candidates(callee);
                if targets.is_empty()
                    || targets.iter().any(|target| {
                        let s = self.s(*target);
                        let sig = s.role(target.node, "type").unwrap_or(target.node);
                        s.role(sig, "results")
                            .is_some_and(|results| !s.node(results).children.is_empty())
                    })
                {
                    return true;
                }
            }
            if !matches!(n.kind.as_str(), "Ident" | "Selector") {
                continue;
            }
            if n.parent
                .is_some_and(|parent| source.role(parent, "field") == Some(node))
            {
                continue; // selector/compound field spelling, not a value read
            }
            if let Some(id) = self.r.symbol(reference) {
                let symbol = &self.r.symbols[id];
                let declaration = symbol.reference;
                if declaration.file == proc.file
                    && source.is_descendant(declaration.node, proc.node)
                {
                    continue;
                }
                if symbol.constant
                    && symbol.value.is_some_and(|value| {
                        matches!(
                            self.r.sources[declaration.file].node(value).kind.as_str(),
                            "Literal"
                                | "Procedure"
                                | "ProcedureType"
                                | "StructType"
                                | "EnumType"
                                | "UnionType"
                        )
                    })
                {
                    continue;
                }
                // Globals, promoted fields and outer captures may introduce
                // readonly data even when the procedure has no local ':>'.
                return true;
            }
            let text = source.text(node);
            if matches!(text, "_" | "true" | "false" | "nil")
                || self.r.denotes_type(reference)
                || !self.r.namespace_imports(proc.file, text).is_empty()
                || self.builtin_name(reference, &mut HashSet::new()).is_some()
            {
                continue;
            }
            // Unresolved names may be unloaded imported values. Do not infer
            // that missing information proves the absence of readonly data.
            return true;
        }
        false
    }
    pub(super) fn type_property(&mut self, typ: &Type, property: Property) -> bool {
        let key = (self.r.type_id(typ), property);
        if let Some(value) = self.type_properties.get(&key) {
            crate::profile::count("permissions.type_property_hits", 1);
            return *value;
        }
        crate::profile::count("permissions.type_property_walks", 1);
        // Only cache a complete fresh-root query. A cycle/budget result reached
        // beneath another root is not a reusable property of that child type.
        let value = match property {
            Property::Slice => self.contains_slice(typ, &mut HashSet::new()),
            Property::Unresolved => self.unresolved_value_type(typ, &mut HashSet::new()),
            Property::Reference => self.root_reference_type(typ, &mut HashSet::new()),
            Property::NestedSlice => self.nested_slice_type(typ, &mut HashSet::new()),
        };
        if !self.r.has_demanded_packages() {
            self.type_properties.insert(key, value);
        }
        value
    }
    fn nested_slice_type(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(typ.clone()) { return false; }
        if seen.len() > 24 { return true; }
        match base_type(typ) {
            Type::Slice(elem) | Type::Dynamic(elem) | Type::Soa(elem) =>
                self.type_property(elem, Property::Slice),
            Type::Map { value, .. } => self.type_property(value, Property::Slice),
            Type::Array { elem, .. } | Type::Fixed { elem, .. } =>
                self.nested_slice_type(elem, seen),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self.record_fields(typ)
                .iter().any(|(_, field)| self.nested_slice_type(field, seen)),
            Type::Union(..) | Type::Unknown | Type::Generic => true,
            _ => false,
        }
    }
    fn root_reference_type(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(typ.clone()) {
            return false;
        }
        if seen.len() > 24 {
            return true;
        }
        match base_type(typ) {
            Type::Pointer(_)
            | Type::Slice(_)
            | Type::Dynamic(_)
            | Type::Map { .. }
            | Type::Set(_)
            | Type::Soa(_)
            | Type::Unknown
            | Type::Generic
            | Type::Union(..) => true,
            Type::Scalar { name, .. } if matches!(name.as_str(), "any" | "rawptr") => true,
            Type::Array { elem, .. } | Type::Fixed { elem, .. } => {
                self.root_reference_type(elem, seen)
            }
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .record_fields(typ)
                .iter()
                .any(|(_, typ)| self.root_reference_type(typ, seen)),
            _ => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_complete_root_properties_are_cached_and_type_shapes_stay_distinct() {
        let mut resolver = Resolver::new(&[]);
        let mut analysis = Privileges::new(&mut resolver, &[], false);
        let wide = Type::Anonymous(
            (0..40)
                .map(|i| (format!("f{i}"), Type::scalar(&format!("scalar{i}"))))
                .collect(),
        );
        let conservative = analysis.type_property(&wide, Property::Slice);
        let child = Type::scalar("scalar25");
        let exact = analysis.type_property(&child, Property::Slice);
        if !conservative || exact {
            panic!("budget-limited child result escaped its root");
        }
        for _ in 0..4 {
            for (typ, slice, unresolved, reference) in [
                (
                    Type::Slice(Box::new(Type::scalar("int"))),
                    true,
                    false,
                    true,
                ),
                (
                    Type::Array {
                        elem: Box::new(Type::scalar("int")),
                        length: Some(4),
                    },
                    false,
                    false,
                    false,
                ),
                (
                    Type::Pointer(Box::new(Type::scalar("int"))),
                    false,
                    false,
                    true,
                ),
                (
                    Type::Anonymous(vec![("x".into(), Type::Unknown)]),
                    true,
                    true,
                    true,
                ),
            ] {
                let actual = (
                    analysis.type_property(&typ, Property::Slice),
                    analysis.type_property(&typ, Property::Unresolved),
                    analysis.type_property(&typ, Property::Reference),
                );
                if actual != (slice, unresolved, reference) {
                    panic!("type property or cache key changed");
                }
            }
        }
        if analysis.type_properties.len() != 14 {
            panic!("completed properties were not reused");
        }
    }
}
