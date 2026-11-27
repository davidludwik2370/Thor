//! Constant callback identity uses a zero-sized native type witness.
use super::*;
impl Lower<'_, '_> {
    pub(super) fn callback_arguments(&mut self, id: NodeId) -> Result<Vec<(usize, Code)>> {
        if !self.resolver.pure_callbacks_present() {
            return Ok(vec![]);
        }
        let Some(signature) = self.resolver.call_signature(self.reference(id)) else {
            return Ok(vec![]);
        };
        if crate::purecallbacks::callback_slots(self.resolver, signature).is_empty() {
            return Ok(vec![]);
        }
        let plans = self
            .resolver
            .call_plans(self.reference(id), &Default::default());
        let [plan] = plans.as_slice() else {
            return Ok(vec![]);
        };
        let slots = crate::purecallbacks::callback_slots(self.resolver, plan.signature);
        let mut values = vec![];
        for (index, tag) in slots {
            let Some(arg) = plan
                .arguments
                .get(index)
                .and_then(|args| args.first())
                .copied()
            else {
                continue;
            };
            let value = self.resolver.sources[arg.file]
                .role(arg.node, "value")
                .map(|node| Ref { node, ..arg })
                .unwrap_or(arg);
            let targets = self.resolver.candidates(value);
            let identity = if let Some(forward) = self.resolver.symbol(value).and_then(|i| {
                crate::purecallbacks::callback_tag(self.resolver, &self.resolver.symbols[i])
            }) {
                forward
            } else if let [target] = targets.as_slice() {
                format!(
                    "struct{{callback_{}:[0]u8}}{{}}",
                    crate::purecallbacks::identity(self.resolver, *target)
                )
            } else {
                continue;
            };
            values.push(format!("{tag}={identity}"));
        }
        if values.is_empty() {
            return Ok(vec![]);
        }
        let args = self.source.group(id, "args");
        let offset = args
            .last()
            .map(|arg| self.source.node(*arg).span.end)
            .unwrap_or(self.source.node(id).span.end - 1);
        Ok(vec![(
            offset,
            self.g(
                format!(
                    "{}{}",
                    if args.is_empty() { "" } else { "," },
                    values.join(",")
                ),
                id,
            ),
        )])
    }
}
