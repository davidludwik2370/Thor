//! Demand expansion applies only to synthetic borrowed parameter tails.
//! Opaque results, mutated tails and concrete released storage never acquire
//! borrowed preconditions. Queries do not replay index/call side effects.
use super::*;
impl Analysis<'_, '_> {
    fn tail_slots(&mut self, state: &State, f: &Frame, at: Ref, depth: usize) -> Vec<Place> {
        if depth >= 16 {
            return vec![];
        }
        match self.kind(at) {
            "Ident" => self
                .symbol(at)
                .and_then(|s| {
                    f.env
                        .get(&s)
                        .copied()
                        .or_else(|| self.ids.get(&(0, s, 0)).copied())
                })
                .map(Place::root)
                .into_iter()
                .collect(),
            "Paren" => self
                .role(at, "expr")
                .map(|n| self.tail_slots(state, f, n, depth + 1))
                .unwrap_or_default(),
            "Selector" | "Index" | "Deref" => {
                let Some(expr) = self.role(at, "expr") else {
                    return vec![];
                };
                let slots = self.tail_slots(state, f, expr, depth + 1);
                let typ = self.typ(expr, f);
                let pointer = matches!(base(&typ), Type::Pointer(_));
                let indirect = pointer
                    || self.kind(at) == "Index"
                        && !matches!(
                            base(&typ),
                            Type::Array { .. }
                                | Type::Fixed { .. }
                                | Type::Vector { .. }
                                | Type::Matrix { .. }
                        );
                let bases = if indirect {
                    slots
                        .iter()
                        .flat_map(|p| state.read(p).refs)
                        .collect::<Vec<_>>()
                } else {
                    slots
                };
                let key = if self.kind(at) == "Selector" {
                    self.role(at, "field")
                        .map(|n| Key::Field(self.text(n).to_owned()))
                } else if self.kind(at) == "Index" {
                    Some(self.index(at, f))
                } else {
                    None
                };
                bases
                    .into_iter()
                    .map(|p| {
                        if let Some(key) = &key {
                            p.child(key.clone())
                        } else {
                            p
                        }
                    })
                    .collect()
            }
            _ => vec![],
        }
    }
    pub(super) fn expand_caller_tail(
        &mut self,
        state: &mut State,
        f: &Frame,
        at: Ref,
        typ: &Type,
        v: &mut Value,
    ) {
        if !v.unknown
            || !v.caller_tail
            || v.has_refs()
            || !v.fields.is_empty()
            || self.demand_count >= 32
        {
            return;
        }
        let slots = self.tail_slots(state, f, at, 0);
        if slots.is_empty() || slots.len() > 4 {
            return;
        }
        if slots.iter().any(|p| {
            state.cells.get(&p.id).is_none_or(|c| c.life != LIVE) || {
                let v = state.read(p);
                !v.unknown || !v.caller_tail || v.has_refs() || !v.fields.is_empty()
            }
        }) {
            return;
        }
        if matches!(base(typ), Type::Unknown | Type::Generic) {
            return;
        }
        self.demand_count += 1;
        let expanded = self.parameter(state, at, typ, 0);
        if expanded.unknown && !expanded.has_refs() {
            return;
        }
        for p in slots {
            state.write(&p, &expanded, false);
        }
        *v = expanded;
        crate::profile::count("lifetime.demand_expansions", 1);
    }
}
