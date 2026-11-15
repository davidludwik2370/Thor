//! Independent scalar evaluations share a bounded native build. Each value keeps
//! its own dependency key, and failures return to ordinary reference ordering.
use super::*;

const LIMIT: usize = 32;

impl Scheduler<'_> {
    fn batch_argument_known(&self, node: Ref, remaining: usize) -> bool {
        if remaining == 0 { return false; }
        if self.resolver.constant_value(node, &Substitutions::new(), None).is_some() {
            return true;
        }
        match self.kind(node) {
            "Literal" => true,
            "Paren" | "Unary" => self.role(node, "expr")
                .is_some_and(|value| self.batch_argument_known(value, remaining - 1)),
            "Binary" => ["left", "right"].iter().all(|role|
                self.role(node, role).is_some_and(|value| self.batch_argument_known(value, remaining - 1))),
            "Call" if self.role(node, "callee").is_some_and(|callee|
                self.resolver.primitive(callee).is_some()) => {
                let arguments = self.group(node, "args");
                arguments.len() == 1 && self.batch_argument_known(arguments[0], remaining - 1)
            }
            "Ident" | "Selector" => self.resolver.symbol(node).is_some_and(|index| {
                let symbol = &self.resolver.symbols[index];
                symbol.constant && !symbol.parameter && !symbol.immutable && symbol.value.is_some_and(|value|
                    self.batch_argument_known(Ref { file: symbol.reference.file, node: value }, remaining - 1))
            }),
            _ => false,
        }
    }

    fn batch_type(&mut self, node: Ref) -> Option<Type> {
        let source = self.source(node);
        if self.kind(node) != "Call" || self.value(node).is_some() || self.unbound_generic(node) {
            return None;
        }
        // Speculative lookahead never evaluates a branch or loop guard.
        if source.ancestors(node.node).any(|owner| {
            let node = source.node(owner);
            matches!(node.kind.as_str(), "If" | "Ternary" | "When" | "WhenExpr"
                | "For" | "Range" | "Switch" | "TypeSwitch" | "SwitchExpr"
                | "CompileSwitch" | "CompileSwitchExpr")
                || node.kind == "Binary" && matches!(node.op.as_str(), "&&" | "||")
        }) || !self.automatic(node) { return None; }
        let callee = self.role(node, "callee")?;
        if self.resolver.symbol(callee).is_some_and(|index| {
            let symbol = &self.resolver.symbols[index];
            !symbol.constant || symbol.immutable || symbol.parameter || symbol.result
        }) { return None; }
        let plans = self.resolver.call_plans(node, &Substitutions::new());
        let [plan] = plans.as_slice() else { return None; };
        if self.source(plan.procedure).node(plan.procedure.node).data["pure"] != true
            || self.source(plan.procedure).ancestors(plan.procedure.node)
                .any(|owner| self.source(plan.procedure).node(owner).kind == "Procedure")
            || !crate::specialization::parameters(plan.procedure, &self.resolver).is_empty()
            || self.resolver.fields(plan.signature, "results").len() != 1
        {
            return None;
        }
        // Include defaults and variadic packing in the readiness proof. The
        // caller must not retain allocated argument temporaries across calls.
        for arguments in &plan.arguments {
            if arguments.len() > 1 { return None; }
            for arg in arguments {
                if !self.batch_argument_known(*arg, 64) { return None; }
            }
        }
        let typ = self.resolver.type_of(node);
        let mut scalar = &typ;
        while let Type::Nominal(_, inner) = scalar { scalar = inner; }
        match scalar {
            Type::Scalar { name, .. } if matches!(name.as_str(),
                "bool" | "b8" | "b16" | "b32" | "b64" | "int" | "uint"
                | "rune" | "byte" | "i8" | "i16" | "i32" | "i64" | "i128"
                | "u8" | "u16" | "u32" | "u64" | "u128" | "f16" | "f32" | "f64") => Some(typ),
            _ => None,
        }
    }

    pub(super) fn batch(&mut self, first: Ref) -> Result<Option<Value>> {
        if self.batch_failed || !self.timeout.is_zero()
            || !self.specializations.bindings.is_empty() || !self.specializations.frames.is_empty()
        {
            return Ok(None);
        }
        let Some(typ) = self.batch_type(first) else { return Ok(None); };
        let source = self.source(first);
        let mut requests = vec![];
        let mut dependencies = vec![];
        for index in first.node.0..source.nodes.len() {
            let node = Ref { file: first.file, node: NodeId(index) };
            if self.batch_type(node).as_ref() != Some(&typ) { continue; }
            let roots = request_roots(source, node, Mode::Value);
            let Ok(graph) = Extraction::build(&mut self.resolver, &roots) else { continue; };
            if graph.keep.iter().any(|declaration| {
                let source = self.source(*declaration);
                source.node(declaration.node).kind == "Declaration"
                    && source.ancestors(declaration.node).any(|owner| source.node(owner).kind == "Procedure")
            }) { continue; }
            if self.required_evaluations(&graph).iter().any(|request| self.value(*request).is_none()) {
                continue;
            }
            let key = self.prepared_key(node, &roots, &graph, Mode::Value);
            if node != first && self.cache.borrow_mut().prepared(key.as_deref(), None).0.is_some() {
                continue;
            }
            requests.push(node);
            dependencies.push((graph, key));
            if requests.len() == LIMIT { break; }
        }
        if requests.len() < 2 || requests[0] != first { return Ok(None); }
        let skips = self.inactive.iter().map(|node| (*node, self.unchecked.contains(node))).collect();
        let executed = self.project.execute_evaluation_with_context(first, self.timeout, &Context {
            batch: &requests,
            results: &self.results,
            skips: &skips,
            cases: &self.cases,
            bindings: &self.specializations.bindings,
            frames: &self.specializations.frames,
            cache: Some(&self.cache),
            cache_misses: &BTreeSet::new(),
        }, Mode::Value);
        let executed = match executed {
            Ok(value) => value,
            Err(error) => {
                #[cfg(unix)]
                if crate::project::evaluation_cancelled() { return Err(error); }
                if error.message.contains("resource inputs changed during evaluation") {
                    return Err(error);
                }
                self.batch_failed = true;
                crate::profile::count("evaluation.batch_fallbacks", 1);
                return Ok(None);
            }
        };
        let values = executed.value["values"].as_array()
            .filter(|values| values.len() == requests.len());
        let Some(values) = values else {
            return Err(self.fail(first, "Invalid batched compile-time result."));
        };
        crate::profile::count("evaluation.batched_values", requests.len() as u64);
        // Publish only after the complete helper packet and resources have
        // passed the normal executor checks.
        for (index, ((node, (graph, prepared)), value)) in requests.iter()
            .zip(dependencies).zip(values).enumerate()
        {
            let target = executed.target.as_ref().map(|target| crate::digest::sha256(
                format!("batch-value-v1:{target}:{index}").as_bytes()));
            {
                let mut cache = self.cache.borrow_mut();
                cache.put(target.as_deref(), value);
                cache.remember(prepared.as_deref(), target.as_deref());
            }
            self.execution_records.insert(*node, scalar::ExecutionRecord {
                target, keep: graph.keep, procedures: graph.procedures,
            });
            if *node != first {
                self.results.insert(*node, value.clone());
                self.resolver.remember_evaluation(*node, value.clone());
            }
        }
        Ok(Some(values[0].clone()))
    }
}
