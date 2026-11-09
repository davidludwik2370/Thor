//! Inspect imports reached through checked bodies before assigning call effects.
use super::*;

impl Project {
    pub(super) fn discover_call_facts(&mut self) -> Result<()> {
        if !self
            .sources
            .iter()
            .any(|source| source.nodes.iter().any(|node| node.data["pure"] == true))
        {
            return Ok(());
        }
        let mut inspected = HashSet::new();
        loop {
            let mut resolver = Resolver::with_flags(&self.sources, &self.options.flags);
            let purity = crate::purity::Purity::analyze(&mut resolver);
            let mut packages = BTreeSet::new();
            for proc in &purity.checked {
                let source = &self.sources[proc.file];
                for node in source
                    .descendants(proc.node)
                    .filter(|id| source.node(*id).kind == "Call")
                {
                    if let Some(callee) = source.role(node, "callee") {
                        Self::callee_packages(
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
            packages.retain(|directory| {
                !inspected.contains(directory)
                    && !self
                        .sources
                        .iter()
                        .any(|source| source.path.parent() == Some(directory))
            });
            if packages.is_empty() {
                break;
            }
            inspected.extend(packages.iter().cloned());
            drop(resolver);
            self.discover_type_facts_from(packages)?;
        }
        Ok(())
    }
    fn callee_packages(
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
                Self::callee_packages(
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
        if node.kind == "ProcedureGroup" {
            for child in &node.children {
                Self::callee_packages(
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
                        if ["base:intrinsics", "base:builtin", "base:runtime"]
                            .contains(&path.as_str())
                        {
                            continue;
                        }
                        let explicit = source.names.get(import.name);
                        let alias = if explicit.is_empty() {
                            path.rsplit(['/', ':']).next().unwrap_or("")
                        } else {
                            explicit
                        };
                        if alias != source.text(base) {
                            continue;
                        }
                        let directory = if let Some((name, suffix)) = path.split_once(':') {
                            source
                                .collection_roots
                                .get(name)
                                .map(|root| root.join(suffix))
                        } else {
                            source.path.parent().map(|root| root.join(path))
                        };
                        if let Some(directory) =
                            directory.and_then(|directory| fs::canonicalize(directory).ok())
                        {
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
                    Self::callee_packages(
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
        }
        Ok(())
    }
}
