//! Inspect native declarations required for call effects and constant typing.
use super::*;

impl Project {
    pub(super) fn discover_expression_facts(&mut self) -> Result<()> {
        let _phase = crate::profile::span("analysis.discover_expressions");
        use crate::{ast::NodeId, semantics::Ref};

        let operands = self
            .sources
            .iter()
            .enumerate()
            .flat_map(|(file, source)| {
                let namespaces = source.nodes.iter().filter(|node| node.kind == "Import")
                    .filter_map(|node| {
                        let explicit = source.names.get(node.name);
                        if !explicit.is_empty() {Some(explicit.to_string())} else {
                            unquote(&node.op).ok().and_then(|path| path.rsplit(['/',':']).next().map(str::to_string))
                        }
                    }).collect::<HashSet<_>>();
                source
                    .nodes
                    .iter()
                    .enumerate()
                    .filter_map(move |(index, node)| {
                        if !source.thor {
                            return None;
                        }
                        let id = NodeId(index);
                        if matches!(node.kind.as_str(), "Index" | "Slice") {
                            let base = source.role(id, "expr")?;
                            if source.node(base).kind == "Selector" && source.role(base, "expr")
                                .is_some_and(|owner| source.node(owner).kind == "Ident" && namespaces.contains(source.text(owner))) {
                                return Some(Ref {file,node:base});
                            }
                        }
                        if node.kind == "Range" {
                            let node = source.role(id, "expr")?;
                            return matches!(source.node(node).kind.as_str(), "Ident" | "Selector" | "Paren")
                                .then_some(Ref { file, node });
                        }
                        if node.kind == "Call" && source.text(source.role(id, "callee")?) == "len" {
                            let [node] = source.group(id, "args") else {
                                return None;
                            };
                            return matches!(source.node(*node).kind.as_str(), "Ident" | "Selector" | "Paren")
                                .then_some(Ref { file, node: *node });
                        }
                        if node.kind != "Binary" || !matches!(node.op.as_str(), "in" | "not_in") {
                            return None;
                        }
                        let mut container = source.role(id, "right")?;
                        while source.node(container).kind == "Paren" {
                            container = source.role(container, "expr")?;
                        }
                        let array = source.node(container);
                        if array.kind != "Compound"
                            || array
                                .spans
                                .get("open")
                                .is_none_or(|span| source.slice(*span) != "[")
                        {
                            return None;
                        }
                        source
                            .role(id, "left")
                            .map(|node| Ref { file, node })
                    })
            })
            .collect::<Vec<_>>();
        let logical = self.sources.iter().any(|source| {
            source.thor
                && source.nodes.iter().enumerate().any(|(index, node)| {
                    node.kind == "Ident"
                        && matches!(source.text(NodeId(index)), "Integer" | "Rational")
                        || node.kind == "Import"
                            && unquote(&node.op).ok().as_deref() == Some("thor:big")
                })
        });
        if operands.is_empty() && !logical {
            return Ok(());
        }
        let mut resolver = self.resolver();
        let mut packages = BTreeSet::new();
        let mut seen = HashSet::new();
        for operand in operands {
            Self::referenced_packages(&resolver, operand, &mut seen, &mut packages)?;
        }
        if logical {
            for (file, source) in resolver.sources.iter().enumerate() {
                if !resolver.logical_enabled(file) || source.shared_native {
                    continue;
                }
                for (index, call) in source.nodes.iter().enumerate() {
                    if call.kind != "Call" {
                        continue;
                    }
                    let id = NodeId(index);
                    let Some(callee) = source.role(id, "callee").map(|node| Ref { file, node })
                    else {
                        continue;
                    };
                    if source.group(id, "args").iter().any(|arg| {
                        let node = source.role(*arg, "value").unwrap_or(*arg);
                        resolver.type_of(Ref { file, node }).big_name().is_some()
                    }) && resolver.signature(callee).is_none()
                    {
                        Self::referenced_packages(&resolver, callee, &mut seen, &mut packages)?;
                    }
                }
            }
        }
        packages.retain(|directory| {
            !self
                .sources
                .iter()
                .any(|source| source.path.parent() == Some(directory))
        });
        crate::profile::destroy("analysis.resolver_teardown", resolver);
        if !packages.is_empty() {
            self.discover_type_facts_from(packages)?;
        }
        Ok(())
    }
    pub(super) fn discover_call_facts(&mut self) -> Result<()> {
        let _phase = crate::profile::span("analysis.discover_calls");
        if !self.sources.iter().any(|source| {
            !source.shared_native && (source.nodes.iter().any(|node| {
                node.immutable || node.data["pure"] == true
                    || matches!(node.kind.as_str(), "Run" | "Interpolation" | "OrPanic")
            }) || !source.selections.is_empty()
                || source.info["mut_parameters"].as_array().is_some_and(|rows| !rows.is_empty()))
        }) { return Ok(()); }
        let mut inspected = HashSet::new();
        let mut lookahead = super::dependencylookahead::Lookahead::new();
        let mut work = super::discoverywork::Work::default();
        loop {
            let mut resolver = self.resolver();
            resolver.begin_symbol_queries();
            work.update(&mut resolver, &self.options.root.join("runtime"));
            let types = work.types(&mut resolver);
            let record_packages = Self::discover_record_dependencies(&mut resolver, types);
            let mut roots = work.roots.clone();
            let requests = work.requests.clone();
            for request in &requests {
                let plans = resolver.call_plans(*request, &Default::default());
                if plans.is_empty() {
                    if let Some(callee) =
                        resolver.sources[request.file].role(request.node, "callee")
                    {
                        roots.extend(resolver.candidates(crate::semantics::Ref {
                            file: request.file,
                            node: callee,
                        }));
                    }
                } else {
                    roots.extend(plans.into_iter().map(|plan| plan.procedure));
                }
            }
            roots.retain(|root| resolver.sources[root.file].node(root.node).kind == "Procedure");
            let purity = crate::purity::Purity::analyze_from(&mut resolver, &roots);
            let mut packages = record_packages;
            for request in requests {
                if let Some(callee) = resolver.sources[request.file].role(request.node, "callee") {
                    Self::referenced_packages(
                        &resolver,
                        crate::semantics::Ref {
                            file: request.file,
                            node: callee,
                        },
                        &mut HashSet::new(),
                        &mut packages,
                    )?;
                }
            }
            for proc in &purity.checked {
                let source = &self.sources[proc.file];
                for node in source
                    .descendants(proc.node)
                    .filter(|id| source.node(*id).kind == "Call")
                {
                    if let Some(callee) = source.role(node, "callee") {
                        Self::referenced_packages(
                            &resolver,
                            crate::semantics::Ref {
                                file: proc.file,
                                node: callee,
                            },
                            &mut HashSet::new(),
                            &mut packages,
                        )?;
                    }
                }
            }
            packages.extend(resolver.demanded_packages());
            let needs_load = |directory: &PathBuf| {
                !inspected.contains(directory)
                    && !self
                        .sources
                        .iter()
                        .any(|source| source.path.parent() == Some(directory))
            };
            packages.retain(&needs_load);
            if packages.is_empty() {
                // Complete known signature/type dependencies before inspecting
                // slice effects. An incomplete graph would require a repeat.
                let tracking = resolver.track_import_demands(true);
                let _effects = self.permission_cache.borrow_mut().discover(
                    &mut resolver, &self.options.flags, self.options.editor_info,
                    self.evaluation.as_ref());
                resolver.track_import_demands(tracking);
                packages.extend(resolver.demanded_packages());
                packages.retain(needs_load);
            }
            if packages.is_empty() {
                self.retain_resolver(resolver);
                break;
            }
            inspected.extend(packages.iter().cloned());
            let mut demands = resolver.demanded_symbols();
            crate::profile::event("analysis.import_demands", || serde_json::json!(
                demands.iter().map(|d| (&self.sources[d.file].path,&d.path,&d.name)).collect::<Vec<_>>()));
            crate::profile::event("analysis.call_dependencies", || serde_json::json!({"packages":packages,"sources":self.sources.len()}));
            work.retain_calls(&mut resolver);
            crate::profile::destroy("analysis.resolver_teardown", resolver);
            self.discover_type_facts_from(packages)?;
            // Follow only declarations behind names this analysis requested.
            // Batch their transitive name dependencies before another graph.
            while !demands.is_empty() {
                let resolver = self.resolver();
                lookahead.expand(&resolver, &demands);
                let mut packages = resolver.demanded_packages();
                packages.retain(|directory| !inspected.contains(directory)
                    && !self.sources.iter().any(|source| source.path.parent() == Some(directory)));
                if packages.is_empty() { break; }
                inspected.extend(packages.iter().cloned());
                demands = resolver.demanded_symbols();
                crate::profile::count("analysis.dependency_lookahead_loads", 1);
                crate::profile::destroy("analysis.resolver_teardown", resolver);
                self.discover_type_facts_from(packages)?;
            }
        }
        Ok(())
    }
    /// Complete value shapes already requested by project expressions. Pointer
    /// targets are inspected on demand by the effect analysis, so a native
    /// window/context handle does not eagerly pull in its entire SDK graph.
    fn discover_record_dependencies(resolver: &mut Resolver<'_>, types: Vec<crate::semantics::Type>) -> BTreeSet<PathBuf> {
        use crate::semantics::{Ref, Type};
        let _phase = crate::profile::span("analysis.discover_record_shapes");
        let mut seen = HashSet::new();
        let mut pending = types;
        let mut expansions = HashMap::<Ref, usize>::new();
        let budget = resolver.sources.iter().map(|source| source.nodes.len()).sum::<usize>().max(1024);
        while let Some(typ) = pending.pop() {
            if seen.len() >= budget { break; }
            if !seen.insert(typ.clone()) { continue; }
            match typ {
                Type::Slice(elem) | Type::Dynamic(elem) | Type::Set(elem)
                | Type::Soa(elem) | Type::Nominal(_, elem) | Type::Array { elem, .. }
                | Type::Fixed { elem, .. } => pending.push(*elem),
                Type::Map { key, value } => pending.extend([*key, *value]),
                Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                    if let Type::Struct(owner) | Type::Record(owner, _) = &typ {
                        let count = expansions.entry(*owner).or_default();
                        if *count >= 32 { continue; }
                        *count += 1;
                    }
                    pending.extend(resolver.shared_record_fields(&typ).iter().map(|(_, typ)| typ.clone()));
                }
                Type::Union(..) => pending.extend(resolver.union_variants(&typ)),
                Type::Procedure(owner) => {
                    let source = &resolver.sources[owner.file];
                    let signature = if source.node(owner.node).kind == "Procedure" {
                        source.role(owner.node, "type").unwrap_or(owner.node)
                    } else { owner.node };
                    for role in ["params", "results"] {
                        if let Some(fields) = source.role(signature, role) {
                            for field in &source.node(fields).children {
                                if let Some(node) = source.role(*field, "type") {
                                    pending.push(resolver.type_of(Ref { file: owner.file, node }));
                                }
                            }
                        }
                    }
                }
                _ => {}
            }
        }
        crate::profile::count("analysis.record_shapes", seen.len() as u64);
        resolver.demanded_packages()
    }
    fn referenced_packages(
        resolver: &Resolver<'_>,
        callee: crate::semantics::Ref,
        seen: &mut HashSet<crate::semantics::Ref>,
        packages: &mut BTreeSet<PathBuf>,
    ) -> Result<()> {
        if !seen.insert(callee) {
            return Ok(());
        }
        let source = &resolver.sources[callee.file];
        let node = source.node(callee.node);
        if node.kind == "Paren" {
            if let Some(expr) = source.role(callee.node, "expr") {
                Self::referenced_packages(
                    resolver,
                    crate::semantics::Ref {
                        file: callee.file,
                        node: expr,
                    },
                    seen,
                    packages,
                )?;
            }
        }
        if matches!(
            node.kind.as_str(),
            "ProcedureGroup" | "Binary" | "Unary" | "Ternary" | "WhenExpr"
        ) {
            for child in &node.children {
                Self::referenced_packages(
                    resolver,
                    crate::semantics::Ref {
                        file: callee.file,
                        node: *child,
                    },
                    seen,
                    packages,
                )?;
            }
        }
        if node.kind == "Selector" {
            if let Some(base) = source
                .role(callee.node, "expr")
                .filter(|base| source.node(*base).kind == "Ident")
            {
                if resolver
                    .lookup(
                        crate::semantics::Ref {
                            file: callee.file,
                            node: base,
                        },
                        source.text(base),
                    )
                    .is_none()
                {
                    for import in source.nodes.iter().filter(|node| node.kind == "Import") {
                        let path = unquote(&import.op)?;
                        let explicit = source.names.get(import.name);
                        let alias = if explicit.is_empty() {
                            path.rsplit(['/', ':']).next().unwrap_or("")
                        } else {
                            explicit
                        };
                        if alias != source.text(base) {
                            continue;
                        }
                        if let Some(directory) = Self::referenced_directory(source, &path) {
                            packages.insert(directory);
                        }
                    }
                }
            }
        }
        if let Some(symbol) = resolver
            .symbol(callee)
            .map(|index| &resolver.symbols[index])
        {
            if symbol.constant && !symbol.immutable {
                if let Some(value) = symbol.value {
                    Self::referenced_packages(
                        resolver,
                        crate::semantics::Ref {
                            file: symbol.reference.file,
                            node: value,
                        },
                        seen,
                        packages,
                    )?;
                }
            }
        } else if node.kind == "Ident" {
            let name = source.text(callee.node);
            for selection in &source.selections {
                if selection.names.iter().any(|selected| selected == name) {
                    if let Some(directory) =
                        Self::referenced_directory(source, &unquote(&selection.path)?)
                    {
                        packages.insert(directory);
                    }
                }
            }
        }
        Ok(())
    }
    fn referenced_directory(source: &Source, path: &str) -> Option<PathBuf> {
        if matches!(path, "base:intrinsics" | "base:builtin") {
            return None;
        }
        let directory = if let Some((name, suffix)) = path.split_once(':') {
            source
                .collection_roots
                .get(name)
                .map(|root| root.join(suffix))
                .or_else(|| {
                    let root = source.info["odin_root"].as_str()?;
                    ["base", "core", "vendor"]
                        .contains(&name)
                        .then(|| Path::new(root).join(name).join(suffix))
                })?
        } else {
            source.path.parent()?.join(path)
        };
        crate::project::canonicalize(directory).ok()
    }
}
