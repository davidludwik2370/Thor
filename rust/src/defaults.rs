//! Lift generic defaults and explicitly supply omitted callbacks at their source
//! call sites. Keep lexical declarations and native conditional selection intact.
use super::*;
use serde_json::Value;
struct Plan {
    procedure: NodeId,
    field: NodeId,
    declaration: NodeId,
    name: String,
    replacements: Vec<(NodeId, String)>,
    error: Option<(NodeId, String)>,
}
impl Lower<'_, '_> {
    fn default_plan(&self, file: usize, procedure: NodeId) -> Option<Plan> {
        let s = &self.resolver.sources[file];
        if !s.thor || s.node(procedure).kind != "Procedure" {
            return None;
        }
        let mut value = procedure;
        while let Some(parent) = s.node(value).parent.filter(|parent| {
            s.node(*parent).kind == "Paren" && s.role(*parent, "expr") == Some(value)
        }) {
            value = parent;
        }
        let field = s.node(value).parent.filter(|parent| {
            s.node(*parent).kind == "Field" && s.role(*parent, "default") == Some(value)
        })?;
        let host_signature = s
            .ancestors(field)
            .find(|node| s.node(*node).kind == "ProcedureType")?;
        if s.node(field).parent != s.role(host_signature, "params") {
            return None;
        }
        let host = s
            .node(host_signature)
            .parent
            .filter(|parent| s.node(*parent).kind == "Procedure")?;
        let mut declaration = host;
        while let Some(parent) = s
            .node(declaration)
            .parent
            .filter(|parent| s.node(*parent).kind == "Paren")
        {
            declaration = parent;
        }
        let declaration = s
            .node(declaration)
            .parent
            .filter(|parent| s.node(*parent).kind == "Declaration")?;
        let signature = s.role(procedure, "type")?;
        fn dependencies(
            resolver: &Resolver<'_>,
            file: usize,
            id: NodeId,
            host: NodeId,
            procedure: NodeId,
            found: &mut bool,
        ) {
            let s = &resolver.sources[file];
            let n = s.node(id);
            if matches!(
                n.kind.as_str(),
                "Call" | "Selector" | "WhenExpr" | "Ternary" | "Binary"
            ) {
                return;
            }
            if n.kind == "PolyType" {
                *found = true;
            }
            if n.kind == "Ident"
                && resolver
                    .symbol(Ref { file, node: id })
                    .is_some_and(|symbol| {
                        let symbol = &resolver.symbols[symbol];
                        s.node(symbol.name_node).kind == "PolyType"
                            && [host, procedure].contains(&symbol.scope)
                    })
            {
                *found = true;
            }
            if n.kind == "Field" {
                if s.group(id, "names")
                    .iter()
                    .any(|name| s.node(*name).kind == "PolyType")
                {
                    *found = true;
                }
                if let Some(typ) = s.role(id, "type") {
                    dependencies(resolver, file, typ, host, procedure, found);
                }
            } else {
                for child in &n.children {
                    dependencies(resolver, file, *child, host, procedure, found);
                }
            }
        }
        let mut generic = false;
        dependencies(
            self.resolver,
            file,
            signature,
            host,
            procedure,
            &mut generic,
        );
        if !generic {
            return None;
        }
        let mut replacements = vec![];
        let mut inferred = HashSet::new();
        fn bind(
            resolver: &Resolver<'_>,
            file: usize,
            id: NodeId,
            host: NodeId,
            inferred: &mut HashSet<String>,
            replacements: &mut Vec<(NodeId, String)>,
        ) {
            let s = &resolver.sources[file];
            let n = s.node(id);
            if matches!(
                n.kind.as_str(),
                "Call" | "Selector" | "WhenExpr" | "Ternary" | "Binary" | "ProcedureType"
            ) {
                return;
            }
            if n.kind == "Ident" {
                if let Some(symbol) = resolver
                    .symbol(Ref { file, node: id })
                    .map(|symbol| &resolver.symbols[symbol])
                {
                    if symbol.reference.file == file
                        && symbol.scope == host
                        && s.node(symbol.name_node).kind == "PolyType"
                        && inferred.insert(symbol.name.clone())
                    {
                        replacements.push((id, format!("${}", s.text(id))));
                    }
                }
            }
            for child in &n.children {
                bind(resolver, file, *child, host, inferred, replacements);
            }
        }
        for param in s
            .role(signature, "params")
            .into_iter()
            .flat_map(|params| &s.node(params).children)
        {
            if let Some(typ) = s.role(*param, "type") {
                bind(
                    self.resolver,
                    file,
                    typ,
                    host,
                    &mut inferred,
                    &mut replacements,
                );
            }
        }
        let mut error = None;
        if s.role(field, "type").is_none()
            && s.descendants(signature)
                .any(|id| s.node(id).kind == "PolyType")
        {
            error=Some((signature,"An explicitly generic inline default needs an explicit callback parameter type. Use the enclosing element type in the inline signature, or annotate the callback parameter.".into()));
        } else {
            for id in s
                .descendants(procedure)
                .filter(|id| s.node(*id).kind == "Ident")
            {
                let Some(symbol) = self
                    .resolver
                    .symbol(Ref { file, node: id })
                    .map(|index| &self.resolver.symbols[index])
                else {
                    continue;
                };
                if symbol.reference.file != file || symbol.scope != host {
                    continue;
                }
                let poly = s.node(symbol.name_node).kind == "PolyType";
                if poly && inferred.contains(&symbol.name) {
                    continue;
                }
                error = Some((
                    if poly { signature } else { id },
                    if poly {
                        format!("Inline default callback depends on generic '{}', which cannot be inferred from its parameter types. Use a named generic default or pass an explicit callback.",symbol.name)
                    } else {
                        format!("Inline default callbacks cannot capture enclosing parameter '{}'. Pass the required value as a callback argument instead.",symbol.name)
                    },
                ));
                break;
            }
        }
        let scope = crate::semantics::scope(s, declaration);
        let key = json!([
            s.group(declaration, "names")
                .iter()
                .map(|id| s.text(*id))
                .collect::<Vec<_>>(),
            s.group(field, "names")
                .iter()
                .map(|id| s.text(*id))
                .collect::<Vec<_>>(),
            if scope.0 == 0 {
                Value::Null
            } else {
                json!([s.path, s.node(scope).span.start])
            }
        ]);
        let prefix = self
            .resolver
            .sources
            .iter()
            .filter(|other| other.path.parent() == s.path.parent())
            .map(|other| other.prefix.as_str())
            .min()
            .unwrap_or(&s.prefix);
        let mut name = format!(
            "{prefix}default_{}",
            &crate::digest::sha256(key.to_string().as_bytes())[..16]
        );
        while self
            .resolver
            .sources
            .iter()
            .any(|source| source.raw.contains(&name))
        {
            name.push('_');
        }
        Some(Plan {
            procedure,
            field,
            declaration,
            name,
            replacements,
            error,
        })
    }
    pub(super) fn prepare_defaults(&mut self) -> Result<()> {
        let procedures = self
            .source
            .nodes
            .iter()
            .enumerate()
            .filter(|(_, n)| n.kind == "Procedure")
            .map(|(i, _)| NodeId(i))
            .collect::<Vec<_>>();
        for procedure in procedures {
            let Some(plan) = self.default_plan(self.file, procedure) else {
                continue;
            };
            if let Some((site, message)) = plan.error {
                let code = self.error_token(site, &message);
                self.switch_replacements.insert(procedure, code);
                continue;
            }
            let signature = self.role(procedure, "type")?;
            let field_code = if let Some(annotation) = self.source.role(plan.field, "type") {
                self.copy(Span::new(
                    self.source.node(plan.field).span.start,
                    self.source.node(annotation).span.end,
                ))
            } else {
                let last = *self.source.group(plan.field, "names").last().unwrap();
                Code::concat([
                    self.copy(Span::new(
                        self.source.node(plan.field).span.start,
                        self.source.node(last).span.end,
                    )),
                    self.g(": ", plan.field),
                    self.render(signature, None, false)?,
                ])
            };
            self.switch_replacements.insert(plan.field, field_code);
            let mut previous = vec![];
            for (id, text) in plan.replacements {
                let code = self.g(text, id);
                previous.push((id, self.switch_replacements.insert(id, code)));
            }
            let body = self.render(plan.procedure, None, false);
            for (id, code) in previous {
                if let Some(code) = code {
                    self.switch_replacements.insert(id, code);
                } else {
                    self.switch_replacements.remove(&id);
                }
            }
            let helper = Code::concat([
                self.g(format!("\n{} :: ", plan.name), procedure),
                body?,
                self.g("\n", procedure),
            ]);
            self.declaration_helpers
                .entry(plan.declaration)
                .or_default()
                .push(helper);
        }
        Ok(())
    }
    pub(super) fn default_arguments(&mut self, call: NodeId) -> Result<Vec<(usize, Code)>> {
        let callee = self.role(call, "callee")?;
        let candidates = self.resolver.candidates(self.reference(callee));
        if !candidates.iter().any(|target| {
            let source = &self.resolver.sources[target.file];
            source.thor
                && source.node(target.node).kind == "Procedure"
                && source
                    .role(target.node, "type")
                    .and_then(|sig| source.role(sig, "params"))
                    .is_some_and(|params| {
                        source
                            .descendants(params)
                            .any(|node| source.node(node).kind == "Procedure")
                    })
        }) {
            return Ok(vec![]);
        }
        let Some(signature) = self.resolver.call_signature(self.reference(call)) else {
            return Ok(vec![]);
        };
        let source = &self.resolver.sources[signature.file];
        let Some(params) = source.role(signature.node, "params") else {
            return Ok(vec![]);
        };
        let procedures = source
            .descendants(params)
            .filter(|id| source.node(*id).kind == "Procedure")
            .collect::<Vec<_>>();
        let names = self
            .resolver
            .fields(signature, "params")
            .into_iter()
            .map(|(name, _)| name.trim_start_matches('$').to_string())
            .collect::<Vec<_>>();
        let callee = self.role(call, "callee")?;
        let receiver =
            self.source.node(callee).kind == "Selector" && self.source.node(callee).op == "->";
        let mut supplied = HashSet::new();
        let mut index = usize::from(receiver);
        if receiver {
            supplied.extend(names.first().cloned());
        }
        for arg in self.source.group(call, "args") {
            if self.source.node(*arg).kind == "FieldValue" {
                if let Some(field) = self.source.role(*arg, "field") {
                    supplied.insert(self.source.text(field).to_string());
                }
            } else {
                if let Some(name) = names.get(index) {
                    supplied.insert(name.clone());
                }
                index += 1;
            }
        }
        let mut added = vec![];
        for procedure in procedures {
            let Some(plan) = self.default_plan(signature.file, procedure) else {
                continue;
            };
            if plan.error.is_some() {
                continue;
            }
            let source = &self.resolver.sources[signature.file];
            if source.node(plan.field).parent != Some(params) {
                continue;
            }
            let fields = source
                .group(plan.field, "names")
                .iter()
                .map(|id| source.text(*id).trim_start_matches('$').to_string())
                .collect::<Vec<_>>();
            for name in fields {
                if supplied.contains(&name) {
                    continue;
                }
                let access = self.compiler_access(
                    Ref {
                        file: signature.file,
                        node: procedure,
                    },
                    &plan.name,
                    call,
                )?;
                added.push(Code::concat([self.g(format!("{name}="), call), access]));
            }
        }
        if added.is_empty() {
            return Ok(vec![]);
        }
        let args = self.source.group(call, "args");
        // A trailing comma/comment is kept outside the added named arguments.
        let position = args
            .last()
            .map(|id| self.source.node(*id).span.end)
            .unwrap_or(self.source.node(call).span.end - 1);
        let mut code = vec![self.g(if args.is_empty() { "" } else { ", " }, call)];
        for (index, arg) in added.into_iter().enumerate() {
            if index > 0 {
                code.push(self.g(", ", call));
            }
            code.push(arg);
        }
        Ok(vec![(position, Code::concat(code))])
    }
}
