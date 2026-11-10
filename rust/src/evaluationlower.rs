//! Render an isolated evaluation entry without reparsing or changing lexical
//! identities. Declarations retain their original scopes and source origins.
use super::*;
impl Lower<'_, '_> {
    pub(super) fn evaluation_main(&mut self, request: crate::evaluation::Request) -> Result<Code> {
        let view = self.evaluation.take();
        let checked = std::mem::replace(&mut self.checked_mode, true);
        let result = (|| {
            let id = request.expression;
            let expression = if let crate::evaluation::Mode::Case(index) = request.mode {
                self.evaluation_case_condition(id, index)?
            } else {
                self.render(id, None, false)?
            };
            let eval = self.module("eval");
            let pure = self.module("pure");
            let value = format!("{}evaluation_result", self.prefix);
            if !request.prelude.is_empty() {
                self.meta = true;
            }
            let scratch = format!("{}evaluation_scratch", self.prefix);
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
                self.g(format!("\n{eval}.emit({value})\n"), id),
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
                    self.g("{\n", scope),
                    Code::concat(declarations),
                    inner,
                    self.g("\n}\n", scope),
                ]);
            }
            Ok(Code::concat([
                self.g(format!("\nmain :: proc() {{\n{scratch}:{pure}.Scratch\n{pure}.scratch_init(&{scratch})\ndefer {pure}.scratch_destroy(&{scratch})\ncontext.allocator={pure}.scratch_allocator(&{scratch})\ncontext.temp_allocator=context.allocator\n"), id),
                self.g(request.prelude, id),
                inner,
                self.g("\n}\n", id),
            ]))
        })();
        self.checked_mode = checked;
        self.evaluation = view;
        result
    }
}
