//! Checked pure callback effects and bounded owned-result shapes.
use super::*;

#[derive(Clone)]
pub(super) enum ResultShape {
    Inline,
    Buffer,
    Fields(Vec<(Key, Arc<ResultShape>)>),
}

impl Analysis<'_, '_> {
    fn callback_result_shape(
        &mut self,
        typ: &Type,
        active: &mut HashSet<Type>,
        depth: usize,
    ) -> Option<Arc<ResultShape>> {
        if let Some(shape) = self.summaries.callback_results.get(typ) {
            return shape.clone();
        }
        if depth > MAX_VALUE_DEPTH || !active.insert(typ.clone()) {
            return None;
        }
        let shape = if self.inline_type(typ, 0) || typ.pure_callable() {
            Some(ResultShape::Inline)
        } else {
            match base(typ) {
                Type::String { name, .. } if name == "string" => Some(ResultShape::Buffer),
                Type::Slice(elem) | Type::Dynamic(elem)
                    if self.inline_type(elem, 0) || elem.pure_callable() =>
                {
                    Some(ResultShape::Buffer)
                }
                Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                    let fields = self.r.record_fields(typ);
                    if fields.len() > 64 {
                        None
                    } else {
                        fields
                            .into_iter()
                            .filter(|(name, _)| name != "_")
                            .map(|(name, typ)| {
                                self.callback_result_shape(&typ, active, depth + 1)
                                    .map(|shape| (Key::Field(name), shape))
                            })
                            .collect::<Option<Vec<_>>>()
                            .map(ResultShape::Fields)
                    }
                }
                Type::Array {
                    elem,
                    length: Some(length),
                } if (0..=16).contains(length) => self
                    .callback_result_shape(elem, active, depth + 1)
                    .map(|shape| {
                        ResultShape::Fields(
                            (0..*length)
                                .map(|index| (Key::Item(Some(index)), shape.clone()))
                                .collect(),
                        )
                    }),
                _ => None,
            }
        }
        .map(Arc::new);
        active.remove(typ);
        self.summaries
            .callback_results
            .insert(typ.clone(), shape.clone());
        shape
    }

    fn callback_result_value(
        &mut self,
        state: &mut State,
        f: &Frame,
        at: Ref,
        shape: &ResultShape,
    ) -> Value {
        if !self.budget(at) {
            return Value {
                unknown: true,
                ..Value::default()
            };
        }
        match shape {
            ResultShape::Inline => Value::default(),
            ResultShape::Buffer => {
                let mut value =
                    self.allocate(state, f, at, state.allocator.clone(), Value::default());
                // An opaque result can be nil/empty. Its potential allocation
                // still carries cleanup responsibility when it exists.
                for pointer in &value.refs {
                    state.cells.get_mut(&pointer.id).unwrap().life |= ABSENT;
                }
                value.may_nil = true;
                value
            }
            ResultShape::Fields(fields) => Value {
                fields: fields
                    .iter()
                    .map(|(key, shape)| {
                        (key.clone(), self.callback_result_value(state, f, at, shape))
                    })
                    .collect(),
                ..Value::default()
            },
        }
    }

    pub(super) fn pure_callback(
        &mut self,
        state: &mut State,
        f: &Frame,
        at: Ref,
        args: &[Ref],
        values: &[Value],
        callable_type: &Type,
    ) -> Option<Vec<Value>> {
        let Type::Function(signature, _) = base(callable_type) else {
            return None;
        };
        // Purity rules forbid retaining, modifying or releasing borrowed input.
        // Reading that input still requires its backing storage to be live.
        for (arg, value) in args.iter().zip(values) {
            let actual = self.role(*arg, "value").unwrap_or(*arg);
            let typ = self.typ(actual, f);
            self.consume(state, value, at, &typ, 0);
            if value.unknown
                && !value.caller_tail
                && !value.has_refs()
                && !self.inline_type(&typ, 0)
                && !typ.pure_callable()
            {
                self.report(
                    at,
                    None,
                    false,
                    "lifetime",
                    "pure callback input has unverified reference provenance",
                );
            }
        }
        let results = self.r.fields(*signature, "results");
        let mut output = Vec::with_capacity(results.len());
        for index in 0..results.len() {
            let typ = self
                .r
                .specialized_value_result(at, index, &f.bindings, &mut HashSet::new());
            let shape = self.callback_result_shape(&typ, &mut HashSet::new(), 0);
            if let Some(shape) = shape {
                output.push(self.callback_result_value(state, f, at, &shape));
            } else {
                self.report(
                    at,
                    None,
                    false,
                    "lifetime",
                    "pure callback result has an unsupported or unresolved ownership shape",
                );
                self.report(
                    at,
                    None,
                    false,
                    "leak",
                    "cleanup of this pure callback result cannot be verified from its type",
                );
                output.push(Value {
                    unknown: true,
                    ..Value::default()
                });
            }
        }
        crate::profile::count("lifetime.pure_callback_contracts", 1);
        Some(output)
    }
}
