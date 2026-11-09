//! Lift inline generic defaults beside their owning declaration. Odin can
//! specialize a named generic callback against the explicit parameter type.
use super::*;
impl Lower<'_, '_> {
    pub(super) fn prepare_defaults(&mut self) -> Result<()> {
        let s = self.source;
        for (i, node) in s.nodes.iter().enumerate() {
            let procedure = NodeId(i);
            if node.kind != "Procedure" {
                continue;
            }
            let Some(field) = node.parent.filter(|parent| {
                s.node(*parent).kind == "Field" && s.role(*parent, "default") == Some(procedure)
            }) else {
                continue;
            };
            let Some(host) = s
                .ancestors(field)
                .find(|parent| s.node(*parent).kind == "Procedure")
            else {
                continue;
            };
            let Some(declaration) = s
                .node(host)
                .parent
                .filter(|parent| s.node(*parent).kind == "Declaration")
            else {
                continue;
            };
            let signature = self.role(procedure, "type")?;
            let Some(params) = s.role(signature, "params") else {
                continue;
            };
            let mut replacements = vec![];
            let mut inferred = HashSet::new();
            for parameter in &s.node(params).children {
                let Some(annotation) = s.role(*parameter, "type") else {
                    continue;
                };
                for id in std::iter::once(annotation).chain(s.descendants(annotation)) {
                    if s.node(id).kind != "Ident" {
                        continue;
                    }
                    let Some(symbol) = self.resolver.symbol(self.reference(id)) else {
                        continue;
                    };
                    let symbol = &self.resolver.symbols[symbol];
                    if symbol.scope == host && s.node(symbol.reference.node).kind == "PolyType" {
                        let text = s.text(id).to_string();
                        if inferred.insert(text.clone()) {
                            replacements.push((id, self.g(format!("${text}"), id)));
                        }
                    }
                }
            }
            if replacements.is_empty() {
                continue;
            }
            let mut invalid = None;
            for id in s.descendants(procedure) {
                if s.node(id).kind != "Ident" {
                    continue;
                }
                if let Some(symbol) = self.resolver.symbol(self.reference(id)) {
                    let symbol = &self.resolver.symbols[symbol];
                    if symbol.scope == host && !inferred.contains(&symbol.name) {
                        invalid = Some(id);
                        break;
                    }
                }
            }
            if let Some(id) = invalid {
                let code=self.error_token(id,"Inline default callbacks cannot capture enclosing parameters or uninferred generic types. Pass the value as an argument or use a named generic default.");
                self.switch_replacements.insert(procedure, code);
                continue;
            }
            let name = format!("{}default_{}", s.prefix, s.node(procedure).span.start);
            let mut field_code = vec![];
            let names = s.group(field, "names");
            if let Some(last) = names.last() {
                field_code
                    .push(self.copy(Span::new(s.node(field).span.start, s.node(*last).span.end)));
            }
            field_code.push(self.g(": ", field));
            field_code.push(self.copy(s.node(signature).span));
            field_code.push(self.g(format!(" = {name}"), field));
            self.switch_replacements
                .insert(field, Code::concat(field_code));
            let mut previous = vec![];
            for (id, code) in replacements {
                previous.push((id, self.switch_replacements.insert(id, code)));
            }
            let body = self.render(procedure, None, false);
            for (id, code) in previous {
                if let Some(code) = code {
                    self.switch_replacements.insert(id, code);
                } else {
                    self.switch_replacements.remove(&id);
                }
            }
            let body = body?;
            let helper = Code::concat([
                self.g(format!("\n@(private=\"file\")\n{name} :: "), procedure),
                body,
                self.g("\n", procedure),
            ]);
            self.declaration_helpers
                .entry(declaration)
                .or_default()
                .push(helper);
        }
        Ok(())
    }
}
