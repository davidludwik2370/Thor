//! Render an isolated evaluation entry without reparsing or changing lexical
//! identities. Declarations retain their original scopes and source origins.
use super::*;
impl Lower<'_, '_> {
    pub(super) fn evaluation_main(&mut self, request: crate::evaluation::Request) -> Result<Code> {
        let view = self.evaluation.take();
        let checked = std::mem::replace(&mut self.checked_mode, true);
        let result = (|| {
            let id = request.expression;
            let expression = if !request.batch.is_empty() {
                let first = self.render(request.batch[0], None, false)?;
                let type_of = self.native("type_of");
                let mut parts = vec![
                    self.g(format!("[{}]{type_of}(", request.batch.len()), id),
                    first.clone(), self.g("){", id), first,
                ];
                for node in request.batch.iter().skip(1) {
                    parts.push(self.g(",", *node));
                    parts.push(self.render(*node, None, false)?);
                }
                parts.push(self.g("}", id));
                Code::concat(parts)
            } else if let crate::evaluation::Mode::Case(index) = request.mode {
                self.evaluation_case_condition(id, index)?
            } else {
                self.render(id, None, matches!(request.mode,crate::evaluation::Mode::Condition))?
            };
            let eval = self.module("eval");
            let pure = self.module("pure");
            let value = format!("{}evaluation_result", self.prefix);
            if !request.prelude.is_empty() {
                self.meta = true;
            }
            let scratch = format!("{}evaluation_scratch", self.prefix);
            let emit = if let Some(bridge) = &request.bridge {
                self.meta = true;
                let names = format!("{}binding_names", self.prefix);
                format!(
                    "\n{names}:=[{}]string{{{}}}\n{eval}.emit_specialized({value},{names}[:])\n",
                    bridge.type_names.len(),
                    bridge
                        .type_names
                        .iter()
                        .map(|name| format!("{}meta.type_canonical_name({name})", self.prefix))
                        .collect::<Vec<_>>()
                        .join(",")
                )
            } else {
                format!("\n{eval}.emit({value})\n")
            };
            let mut inner = Code::concat([
                self.g(format!("\n{value} := "), id),
                if matches!(request.mode, crate::evaluation::Mode::Condition) {
                    Code::concat([
                        self.g("true when (", id),
                        expression,
                        self.g(") else false", id),
                    ])
                } else {
                    expression
                },
                self.g(emit, id),
            ]);
            for scope in self
                .source
                .ancestors(id)
                .filter(|scope| {
                    *scope != NodeId(0) && crate::semantics::is_scope(self.source, *scope)
                })
                .collect::<Vec<_>>()
            {
                let mut declarations = vec![];
                for declaration in request.locals.get(&scope).into_iter().flatten() {
                    declarations.push(self.render(*declaration, None, true)?);
                    declarations.push(self.g("\n", *declaration));
                }
                declarations.extend(self.helper_declarations(scope));
                inner = Code::concat([
                    self.gen("{\n", self.source.node(scope).span, "evaluation-scope"),
                    Code::concat(declarations),
                    inner,
                    self.g("\n}\n", scope),
                ]);
            }
            let heading = if let Some(bridge) = &request.bridge {
                format!("\n{} :: proc({}) {{\n", bridge.name, bridge.parameters)
            } else {
                format!("\nmain :: proc() {{\n{scratch}:{pure}.Scratch\n{pure}.scratch_init(&{scratch})\ndefer {pure}.scratch_destroy(&{scratch})\ncontext.allocator={pure}.scratch_allocator(&{scratch})\ncontext.temp_allocator=context.allocator\n")
            };
            Ok(Code::concat([
                self.g(heading, id),
                self.g(request.prelude, id),
                inner,
                self.g("\n}\n", id),
            ]))
        })();
        self.checked_mode = checked;
        self.evaluation = view;
        result
    }
    pub(super) fn evaluation_entry(
        &mut self,
        entry: crate::evaluation::EntryRequest,
    ) -> Result<Code> {
        let view = self.evaluation.take();
        let result = (|| {
            let id = entry.origin;
            self.meta = true;
            let mut inner = self.g(entry.call, id);
            for scope in self
                .source
                .ancestors(id)
                .filter(|scope| {
                    *scope != NodeId(0) && crate::semantics::is_scope(self.source, *scope)
                })
                .collect::<Vec<_>>()
            {
                let mut declarations = vec![];
                for declaration in entry.locals.get(&scope).into_iter().flatten() {
                    declarations.push(self.render(*declaration, None, true)?);
                    declarations.push(self.g("\n", *declaration));
                }
                declarations.extend(self.helper_declarations(scope));
                inner = Code::concat([
                    self.gen("{\n", self.source.node(scope).span, "evaluation-scope"),
                    Code::concat(declarations),
                    inner,
                    self.g("\n}\n", scope),
                ]);
            }
            let pure = self.module("pure");
            let scratch = format!("{}evaluation_scratch", self.prefix);
            Ok(Code::concat([
                self.g(format!("\nmain::proc(){{\n{scratch}:{pure}.Scratch\n{pure}.scratch_init(&{scratch})\ndefer {pure}.scratch_destroy(&{scratch})\ncontext.allocator={pure}.scratch_allocator(&{scratch})\ncontext.temp_allocator=context.allocator\n"),id),
                self.g(entry.prelude,id),inner,self.g("\n}\n",id),
            ]))
        })();
        self.evaluation = view;
        result
    }
}
