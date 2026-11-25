//! Mismatches require two proven, distinct native allocator identities. Dynamic
//! arrays/maps use their stored allocator, while pointer/slice/string cleanup
//! uses the selected argument or ambient allocator. Unknown callbacks stay unknown.
use super::*;
impl Analysis<'_, '_> {
    pub(super) fn allocator_value(allocator: &Allocator) -> Value {
        Value {
            allocator: Some(allocator.clone()),
            refs: if let Allocator::Region(p) = allocator {
                BTreeSet::from([(**p).clone()])
            } else {
                BTreeSet::new()
            },
            ..Value::default()
        }
    }
    pub(super) fn stored_allocator(t: &Type) -> bool {
        match base(t) {
            Type::Dynamic(_) | Type::Map { .. } | Type::Set(_) => true,
            Type::Soa(t) => Self::stored_allocator(t),
            _ => false,
        }
    }
    pub(super) fn cleanup_target(&self, args: &[Ref]) -> usize {
        args.iter()
            .position(|a| {
                self.kind(*a) == "FieldValue"
                    && self.role(*a, "field").is_some_and(|n| {
                        matches!(
                            self.text(n),
                            "ptr" | "array" | "str" | "p" | "m" | "data" | "bytes"
                        )
                    })
            })
            .or_else(|| args.iter().position(|a| self.argument_name(*a).is_none()))
            .unwrap_or(0)
    }
    pub(super) fn check_allocator(
        &mut self,
        state: &State,
        value: &Value,
        selected: &Allocator,
        at: Ref,
    ) -> bool {
        let mut okay = true;
        for p in &value.refs {
            let Storage::Heap(original) = self.objects[p.id].storage.clone() else {
                continue;
            };
            let different = match (&original, selected) {
                (Allocator::Temporary(a), Allocator::Temporary(b)) => a != b,
                (Allocator::Temporary(_), Allocator::Heap | Allocator::Region(_))
                | (Allocator::Heap | Allocator::Region(_), Allocator::Temporary(_)) => true,
                (Allocator::Heap, Allocator::Region(_))
                | (Allocator::Region(_), Allocator::Heap) => true,
                (Allocator::Region(a), Allocator::Region(b)) => {
                    a != b
                        && matches!(
                            self.objects[a.id].storage,
                            Storage::Stack { .. } | Storage::Static
                        )
                        && matches!(
                            self.objects[b.id].storage,
                            Storage::Stack { .. } | Storage::Static
                        )
                        && !a
                            .path
                            .iter()
                            .chain(&b.path)
                            .any(|k| matches!(k, Key::Item(None) | Key::Range(..)))
                }
                _ => false,
            };
            if different {
                let definite = !value.unknown
                    && value.refs.len() == 1
                    && state
                        .cells
                        .get(&p.id)
                        .is_some_and(|c| c.life == LIVE && !c.uncertain);
                self.report(
                    at,
                    Some(p.id),
                    definite,
                    "lifetime",
                    "cleanup allocator does not match the allocator that owns this storage",
                );
                okay = false;
            }
        }
        okay
    }
}
