//! Summary-local storage cannot escape through an inline-only result or store.
//! Owned scratch requires immediate unconditional cleanup registration. All uses
//! still pass the ordinary non-retaining summary proof; no header reassignment,
//! arbitrary allocator, early unprotected exit, or pointer/integer cast is waived.
use super::*;

impl Analysis<'_, '_> {
    pub(super) fn scratch_place(&mut self, at: Ref, f: &Frame, proof: &summaries::Proof) -> bool {
        match self.kind(at) {
            "Ident" => {
                let Some(symbol) = self.symbol(at) else {
                    return false;
                };
                if proof.scratch.contains(&symbol) {
                    return true;
                }
                let local = self.r.sources[symbol.file]
                    .ancestors(symbol.node)
                    .find(|n| self.r.sources[symbol.file].node(*n).kind == "Procedure")
                    == Some(f.procedure.node)
                    && symbol.file == f.procedure.file;
                let typ = self.typ(at, f);
                local && self.inline_type(&typ, 0)
            }
            "Paren" | "Index" | "Selector" => self
                .role(at, "expr")
                .is_some_and(|expr| self.scratch_place(expr, f, proof)),
            _ => false,
        }
    }
    pub(super) fn register_summary_scratch(
        &mut self,
        at: Ref,
        f: &Frame,
        proof: &mut summaries::Proof,
    ) {
        let names = self.group(at, "names");
        let values = self.group(at, "values");
        if names.len() != 1 || values.len() != 1 {
            return;
        }
        let name = names[0];
        let value = values[0];
        let typ = self.typ(name, f);
        if !matches!(base(&typ),Type::Slice(t)|Type::Dynamic(t)|Type::Pointer(t) if self.inline_type(t,0))
            && !matches!(base(&typ), Type::String { .. })
        {
            return;
        }
        let borrowed = self.kind(value) == "Literal" && self.text(value).starts_with(['"', '`'])
            || self.kind(value) == "Slice"
                && self
                    .role(value, "expr")
                    .is_some_and(|p| self.scratch_place(p, f, proof))
            || self.kind(value) == "Unary"
                && self.r.sources[value.file].node(value.node).op == "&"
                && self
                    .role(value, "expr")
                    .is_some_and(|p| self.scratch_place(p, f, proof));
        if borrowed {
            proof.scratch.insert(self.symbol(name).unwrap_or(name));
            return;
        }
        let Some(callee) = self.role(value, "callee") else {
            return;
        };
        let model = self.call_model(value, callee);
        if !matches!(model.builtin.as_deref(), Some("make" | "new")) {
            return;
        }
        // Explicit allocator expressions can dispatch arbitrary callbacks.
        if self
            .group(value, "args")
            .iter()
            .any(|a| self.kind(*a) == "FieldValue")
        {
            return;
        }
        let scoped = crate::scoped::names(&self.r.sources[at.file], at.node).contains(&name.node);
        let cleanup = if scoped {
            None
        } else {
            let s = &self.r.sources[at.file];
            let Some(block) = s.node(at.node).parent else {
                return;
            };
            if s.node(block).kind != "Block" {
                return;
            }
            let Some(i) = s.node(block).children.iter().position(|n| *n == at.node) else {
                return;
            };
            let Some(next) = s.node(block).children.get(i + 1) else {
                return;
            };
            let next = Ref { node: *next, ..at };
            if self.kind(next) != "Defer" {
                return;
            }
            let Some(stmt) = self.role(next, "stmt") else {
                return;
            };
            let call = self.role(stmt, "expr").unwrap_or(stmt);
            let Some(callee) = self.role(call, "callee") else {
                return;
            };
            let model = self.call_model(call, callee);
            let args = self.group(call, "args");
            if !matches!(model.builtin.as_deref(), Some("free" | "delete"))
                || args.len() != 1
                || self.symbol(args[0]) != self.symbol(name)
            {
                return;
            }
            Some(next)
        };
        proof.scratch.insert(self.symbol(name).unwrap_or(name));
        proof.scratch_allocations.insert(value);
        if let Some(cleanup) = cleanup {
            proof.scratch_cleanups.insert(cleanup);
        }
        crate::profile::count("lifetime.summary_scratch", 1);
    }
}
