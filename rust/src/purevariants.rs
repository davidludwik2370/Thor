//! Checked ordinary bodies retain their own scratch lifetime and result copying.
//! Original declarations and ordinary calls retain native shallow ownership.
use super::*;
impl Lower<'_, '_> {
    pub(super) fn checked_procedure_literal(&self, proc: NodeId) -> String {
        let name = self
            .source
            .ancestors(proc)
            .find(|node| self.source.node(*node).kind == "Declaration")
            .and_then(|node| self.source.group(node, "names").first())
            .map(|node| self.source.text(*node))
            .unwrap_or("anonymous procedure");
        json!(name).to_string()
    }
    fn checked_name(&self, target: Ref) -> String {
        format!(
            "{}checked_{}",
            self.resolver.sources[target.file].prefix, target.node.0
        )
    }
    fn checked_access(&mut self, target: Ref, site: NodeId) -> Result<Code> {
        let name = self.checked_name(target);
        let owner = &self.resolver.sources[target.file];
        if owner.path.parent() == self.source.path.parent() {
            return Ok(self.g(name, site));
        }
        for import in self
            .source
            .nodes
            .iter()
            .filter(|node| node.kind == "Import")
        {
            let path = unquote(&import.op)?;
            let directory = if let Some((collection, suffix)) = path.split_once(':') {
                self.source
                    .collection_roots
                    .get(collection)
                    .map(|root| root.join(suffix))
            } else {
                self.source.path.parent().map(|root| root.join(&path))
            };
            if directory
                .and_then(|p| std::fs::canonicalize(p).ok())
                .as_deref()
                == owner.path.parent()
            {
                let alias = format!("{}checked_package_{}", self.prefix, target.file);
                self.modules.insert(alias.clone(), path);
                return Ok(self.g(format!("{alias}.{name}"), site));
            }
        }
        // A public procedure alias can resolve to a body in a package not
        // directly imported by this file. Mirror PureCalls.access: retain the
        // original alias as the native access witness, and add a compiler-only
        // import for its checked implementation.
        let target_dir = owner
            .path
            .parent()
            .ok_or_else(|| Error::new("checked target has no package"))?;
        let path = self
            .source
            .collection_roots
            .iter()
            .filter_map(|(collection, root)| {
                target_dir.strip_prefix(root).ok().map(|suffix| {
                    (
                        root.components().count(),
                        format!(
                            "{collection}:{}",
                            suffix.to_string_lossy().replace('\\', "/")
                        ),
                    )
                })
            })
            .max_by_key(|(depth, _)| *depth)
            .map(|(_, path)| path)
            .unwrap_or_else(|| {
                let caller = self
                    .source
                    .path
                    .parent()
                    .unwrap()
                    .components()
                    .collect::<Vec<_>>();
                let target = target_dir.components().collect::<Vec<_>>();
                let common = caller
                    .iter()
                    .zip(&target)
                    .take_while(|(a, b)| a == b)
                    .count();
                let mut relative = std::path::PathBuf::new();
                for _ in common..caller.len() {
                    relative.push("..");
                }
                for part in &target[common..] {
                    relative.push(part.as_os_str());
                }
                relative.to_string_lossy().replace('\\', "/")
            });
        let alias = format!("{}checked_package_{}", self.prefix, target.file);
        self.modules.insert(alias.clone(), path);
        Ok(self.g(format!("{alias}.{name}"), site))
    }
    pub(super) fn prepare_checked_variants(&mut self, facts: &crate::purity::Facts) -> Result<()> {
        self.checked_targets = facts
            .targets
            .iter()
            .filter(|(call, _)| call.file == self.file)
            .map(|(call, targets)| (call.node, targets.iter().copied().collect()))
            .collect();
        let targets = facts
            .targets
            .values()
            .filter(|targets| {
                targets.iter().any(|target| {
                    self.resolver.sources[target.file].node(target.node).data["pure"] != true
                })
            })
            .flat_map(|targets| targets.iter())
            .copied()
            .filter(|target| target.file == self.file)
            .collect::<std::collections::BTreeSet<_>>();
        for target in targets {
            let mut parent = self.source.node(target.node).parent;
            while parent.is_some_and(|node| self.source.node(node).kind == "Paren") {
                parent = parent.and_then(|node| self.source.node(node).parent);
            }
            let Some(declaration) =
                parent.filter(|node| self.source.node(*node).kind == "Declaration")
            else {
                return Err(self.unsupported(target.node, "named checked procedure bodies"));
            };
            let body = self.role(target.node, "body")?;
            let prefix = format!("{}_", self.checked_name(target));
            let saved_prefix = std::mem::replace(&mut self.prefix, prefix);
            let old_prologue = self.body_prologues.remove(&body);
            let old_owned = self.pure_owned.contains(&target.node);
            let edit_count = self.edits.len();
            self.emitting_variants.insert(target.node);
            let result: Result<Code> = (|| {
                self.prepare_pure(target, true)?;
                let name = self.checked_name(target);
                let procedure = self.render(target.node, None, false)?;
                let mut attrs = vec![];
                for attribute in self.source.group(declaration, "attributes") {
                    for item in self.source.group(*attribute, "elems") {
                        let key = self.source.role(*item, "field").unwrap_or(*item);
                        if ["require_results", "deprecated"].contains(&self.source.text(key)) {
                            attrs.push(self.render(*item, None, true)?);
                        }
                    }
                }
                let mut annotation = vec![];
                if !attrs.is_empty() {
                    annotation.push(self.g("\n@(", target.node));
                    for (index, attr) in attrs.into_iter().enumerate() {
                        if index > 0 {
                            annotation.push(self.g(",", target.node));
                        }
                        annotation.push(attr);
                    }
                    annotation.push(self.g(")\n", target.node));
                }
                Ok(Code::concat([
                    Code::concat(annotation),
                    self.g(format!("\n{name} :: "), target.node),
                    procedure,
                    self.g("\n", target.node),
                ]))
            })();
            if self.meta {
                self.modules
                    .insert(format!("{}meta", self.prefix), "base:intrinsics".into());
            }
            if self.builtin {
                self.modules
                    .insert(format!("{}builtin", self.prefix), "base:builtin".into());
            }
            if self.default {
                let name = format!("{}default_value", self.prefix);
                let code=self.g(format!("\n@(private=\"file\")\n{name}::proc \"contextless\"(value:$T)->T{{return value}}\n"),target.node);
                self.helpers
                    .entry((NodeId(0), self.prefix.clone(), name))
                    .or_insert(code);
            }
            self.emitting_variants.remove(&target.node);
            // A checked body can convert a `do` block into braces. Its source
            // edits belong to the rendered variant, never the original proc.
            self.edits.truncate(edit_count);
            self.body_prologues.remove(&body);
            if let Some(prologue) = old_prologue {
                self.body_prologues.insert(body, prologue);
            }
            if !old_owned {
                self.pure_owned.remove(&target.node);
            }
            self.prefix = saved_prefix;
            let code = result?;
            self.declaration_helpers
                .entry(declaration)
                .or_default()
                .push(code);
        }
        Ok(())
    }
    pub(super) fn checked_call(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        let Some(targets) = self.checked_targets.get(&id).cloned() else {
            return Ok(None);
        };
        if !targets
            .iter()
            .any(|target| self.resolver.sources[target.file].node(target.node).data["pure"] != true)
        {
            return Ok(None);
        }
        let callee = self.role(id, "callee")?;
        let original = self.render(callee, None, false)?;
        let verify = format!("{}checked_access_{}", self.prefix, id.0);
        let owner = self.helper_scope(id);
        self.helpers.insert(
            (owner, self.prefix.clone(), verify.clone()),
            Code::concat([
                self.g(format!("\n{verify} :: "), callee),
                original,
                self.g("\n", callee),
            ]),
        );
        let mut names = vec![];
        for target in targets {
            names.push(self.checked_access(target, callee)?);
        }
        let target = if names.len() == 1 {
            names.remove(0)
        } else {
            let name = format!("{}checked_group_{}", self.prefix, id.0);
            let mut group = vec![self.g(format!("\n{name} :: proc{{"), id)];
            for (index, code) in names.into_iter().enumerate() {
                if index > 0 {
                    group.push(self.g(",", id));
                }
                group.push(code);
            }
            group.push(self.g("}\n", id));
            self.helpers.insert(
                (owner, self.prefix.clone(), name.clone()),
                Code::concat(group),
            );
            self.g(name, id)
        };
        let mut replacements = vec![(self.source.node(callee).span, target)];
        for argument in self.source.group(id, "args").to_vec() {
            replacements.push((self.source.node(argument).span, {
                let value = self.source.role(argument, "value").unwrap_or(argument);
                let hint = self.contextual_hint(value);
                self.render(argument, hint, constant)?
            }));
        }
        let mut inserts = vec![];
        if let Some(profile) = self.ownership_argument(id)? {
            let args = self.source.group(id, "args");
            let offset = args
                .last()
                .map(|arg| self.source.node(*arg).span.end)
                .unwrap_or(self.source.node(id).span.end - 1);
            let profile =
                Code::concat([self.g(if args.is_empty() { "" } else { "," }, id), profile]);
            inserts.push((offset, profile));
        }
        Ok(Some(self.splice(id, replacements, inserts)?))
    }
}
