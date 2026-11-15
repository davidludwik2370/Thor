//! Type witnesses keep private and generic parameter names in their own scope.
use super::*;
use crate::semantics::Constant;
impl Lower<'_, '_> {
    pub(super) fn prepare_contexts(&mut self) -> Result<()> {
        let witnesses = self
            .resolver
            .parameter_witnesses
            .values()
            .filter(|w| {
                w.annotation.file == self.file
                    && self.evaluation.as_ref().is_none_or(|view| {
                        view.retains(self.source, w.declaration.node)
                    })
            })
            .cloned()
            .collect::<Vec<_>>();
        for witness in witnesses {
            let name = self.witness_name(witness.annotation);
            let generic = if witness.used.is_empty() {
                String::new()
            } else {
                format!(
                    "({})",
                    witness
                        .used
                        .iter()
                        .map(|(name, kind)| format!("${name}:{kind}"))
                        .collect::<Vec<_>>()
                        .join(",")
                )
            };
            let annotation = self.context_template(witness.annotation.node)?;
            let code = Code::concat([
                self.g(
                    format!("\n{name} :: struct{generic} {{value:"),
                    witness.annotation.node,
                ),
                annotation,
                self.g("}\n", witness.annotation.node),
            ]);
            self.declaration_helpers
                .entry(witness.declaration.node)
                .or_default()
                .push(code);
        }
        Ok(())
    }
    fn witness_name(&self, r: Ref) -> String {
        format!(
            "{}parameter_{}",
            self.resolver.sources[r.file].prefix, r.node.0
        )
    }
    fn context_template(&mut self, id: NodeId) -> Result<Code> {
        let node = self.source.node(id);
        if node.kind == "PolyType" {
            return if let Some(typ) = self.source.role(id, "specialization") {
                self.context_template(typ)
            } else {
                Ok(self.g(self.source.names.get(node.name), id))
            };
        }
        let mut replacements = vec![];
        for child in &node.children {
            replacements.push((
                self.source.node(*child).span,
                self.context_template(*child)?,
            ));
        }
        self.splice(id, replacements, vec![])
    }
    fn context_package(&mut self, owner: Ref) -> Option<String> {
        let target = &self.resolver.sources[owner.file];
        if target.path.parent() == self.source.path.parent() {
            return Some(String::new());
        }
        for import in self
            .source
            .nodes
            .iter()
            .filter(|node| node.kind == "Import")
        {
            let path = unquote(&import.op).ok()?;
            let directory = if let Some((collection, suffix)) = path.split_once(':') {
                self.source.collection_roots.get(collection)?.join(suffix)
            } else {
                self.source.path.parent()?.join(&path)
            };
            if crate::project::canonicalize(directory).ok().as_deref() != target.path.parent() {
                continue;
            }
            // A separate alias also works for blank imports and local shadowing.
            let alias = format!("{}context_package_{}", self.prefix, owner.file);
            self.modules.insert(alias.clone(), path);
            return Some(format!("{alias}."));
        }
        // A re-export such as basic.to_set can resolve to a declaration in
        // another Thor runtime package without a direct source import.
        let runtime = self.source.collection_roots.get("thor")?;
        let directory = target.path.parent()?.strip_prefix(runtime).ok()?;
        let path = format!("thor:{}", directory.to_str()?.replace('\\', "/"));
        let alias = format!("{}context_package_{}", self.prefix, owner.file);
        self.modules.insert(alias.clone(), path);
        Some(format!("{alias}."))
    }
    fn context_type(&mut self, typ: &Type) -> Option<String> {
        Some(match typ {
            Type::BigInteger | Type::BigRational => format!("{}.{}",self.module("big"),typ.big_name()?),
            Type::Scalar { name, .. } | Type::String { name, .. } => self.native(name),
            Type::Array {
                elem,
                length: Some(length),
            } => format!("[{length}]{}", self.context_type(elem)?),
            Type::Slice(elem) => format!("[]{}", self.context_type(elem)?),
            Type::Dynamic(elem) => format!("[dynamic]{}", self.context_type(elem)?),
            Type::Pointer(elem) => format!("^{}", self.context_type(elem)?),
            Type::Fixed {
                elem,
                length: Some(length),
            } => format!("[dynamic;{length}]{}", self.context_type(elem)?),
            Type::Constant(Constant::Integer(value)) => value.to_string(),
            Type::Constant(Constant::Boolean(value)) => value.to_string(),
            Type::Constant(Constant::Text(value)) => json!(value).to_string(),
            Type::Constant(Constant::Enum(owner, value)) => {
                format!("{}({value})", self.context_type(&Type::Enum(*owner))?)
            }
            Type::Enum(owner)
            | Type::Struct(owner)
            | Type::Union(owner, _)
            | Type::Nominal(owner, _) => {
                let source = &self.resolver.sources[owner.file];
                let declaration = source
                    .ancestors(owner.node)
                    .find(|node| source.node(*node).kind == "Declaration")?;
                if owner.file != self.file && self.resolver.file_private_declaration(Ref {file:owner.file,node:declaration}) {
                    return None;
                }
                let name = source
                    .text(*source.group(declaration, "names").first()?)
                    .to_string();
                let mut name = format!("{}{name}", self.context_package(*owner)?);
                if let Type::Union(_, bindings) = typ {
                    if let Some(params) = source.role(owner.node, "params") {
                        let parameters = source.node(params).children.iter()
                            .flat_map(|field| source.group(*field, "names"))
                            .map(|node| source.text(*node).trim_start_matches('$').to_string())
                            .collect::<Vec<_>>();
                        let arguments = parameters.iter().map(|parameter| {
                            let typ = bindings.iter().find(|(name, _)| name == parameter)?.1.clone();
                            self.context_type(&typ)
                        }).collect::<Option<Vec<_>>>()?;
                        name.push('(');
                        name.push_str(&arguments.join(","));
                        name.push(')');
                    }
                }
                name
            }
            _ => return None,
        })
    }
    pub(super) fn contextual_hint(&mut self, id: NodeId) -> Option<Hint> {
        let hint = self
            .resolver
            .context_hints
            .get(&self.reference(id))?
            .clone();
        let name = self.witness_name(hint.annotation);
        let package = self.context_package(hint.annotation)?;
        let mut arguments = vec![];
        for (typ, witness) in hint.values.iter().zip(&hint.witnesses) {
            let code = if let Some((argument, type_argument, depth)) = witness {
                self.querying += 1;
                let rendered = self.render(argument.node, None, false);
                self.querying -= 1;
                let mut code = rendered.ok()?;
                if !type_argument {
                    let query = self.native("type_of");
                    code = Code::concat([self.g(format!("{query}("), id), code, self.g(")", id)]);
                }
                if *depth > 0 {
                    self.meta = true;
                }
                for _ in 0..*depth {
                    code = Code::concat([
                        self.g(format!("{}meta.type_elem_type(", self.prefix), id),
                        code,
                        self.g(")", id),
                    ]);
                }
                code
            } else {
                let code = self.context_type(typ)?;
                self.g(code, id)
            };
            arguments.push(code);
        }
        let type_of = self.native("type_of");
        let mut code = vec![self.g(format!("{type_of}({package}{name}"), id)];
        if !arguments.is_empty() {
            code.push(self.g("(", id));
            for (i, arg) in arguments.into_iter().enumerate() {
                if i > 0 {
                    code.push(self.g(",", id));
                }
                code.push(arg);
            }
            code.push(self.g(")", id));
        }
        code.push(self.g("{}.value)", id));
        let bindings = self.resolver.parameter_witnesses.get(&hint.annotation)
            .map(|witness|witness.used.iter().map(|(name,_)|name.clone()).zip(hint.values.clone()).collect())
            .unwrap_or_default();
        let semantic = Some(self.resolver.specialized_type(hint.annotation,&bindings));
        Some(Hint {constant_literal:false,
            semantic,
            code: Code::concat(code),
            element_only: false,
            plain_array: hint.plain_array,
        })
    }
}
