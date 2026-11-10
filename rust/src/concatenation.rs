//! Constant array and slice concatenation uses Odin's native intrinsic.
use super::*;

fn unparen(source: &Source, mut id: NodeId) -> NodeId {
    while source.node(id).kind == "Paren" {
        let Some(inner) = source.role(id, "expr") else {
            break;
        };
        id = inner;
    }
    id
}

fn inline(source: &Source, id: NodeId) -> bool {
    let node = source.node(unparen(source, id));
    node.kind == "Compound"
        && node
            .spans
            .get("open")
            .is_some_and(|span| source.slice(*span) == "[")
}

fn operands(source: &Source, id: NodeId) -> Vec<NodeId> {
    let mut pending = vec![id];
    let mut values = Vec::new();
    while let Some(value) = pending.pop() {
        let inner = unparen(source, value);
        if source.node(inner).kind == "Binary" && source.node(inner).op == "++" {
            pending.extend(source.role(inner, "right"));
            pending.extend(source.role(inner, "left"));
        } else {
            values.push(value);
        }
    }
    values
}

fn base(mut typ: &Type) -> &Type {
    while let Type::Nominal(_, inner) = typ {
        typ = inner;
    }
    typ
}

pub(crate) fn result_type(
    source: &Source,
    id: NodeId,
    mut resolve: impl FnMut(NodeId) -> Type,
) -> Type {
    let values = operands(source, id)
        .into_iter()
        .map(|id| (id, resolve(id)))
        .collect::<Vec<_>>();
    let sequence = |typ: &Type| matches!(base(typ), Type::Array { .. } | Type::Slice(_));
    let known = values
        .iter()
        .find(|(id, typ)| !inline(source, *id) && sequence(typ))
        .or_else(|| {
            values.iter().find(|(_, typ)| {
                sequence(typ) && typ.element().is_some_and(|elem| !elem.is_unknown())
            })
        })
        .or_else(|| values.iter().find(|(_, typ)| sequence(typ)));
    let Some((_, typ)) = known else {
        return Type::Unknown;
    };
    if let Type::Array { elem, .. } = base(typ) {
        let length = values
            .iter()
            .try_fold(0i128, |count, (_, typ)| match base(typ) {
                Type::Array {
                    length: Some(length),
                    ..
                } => count.checked_add(*length),
                _ => None,
            });
        Type::Array {
            elem: elem.clone(),
            length,
        }
    } else {
        typ.clone()
    }
}

impl Lower<'_, '_> {
    pub(super) fn concatenate(&mut self, id: NodeId, expected: Option<Hint>) -> Result<Code> {
        let source = self.source;
        let values = operands(source, id);
        let types = values
            .iter()
            .map(|&value| self.typ(value))
            .collect::<Vec<_>>();
        let mut arrays = None;
        for (&value, typ) in values.iter().zip(&types) {
            if self.resolver.runtime_operand(self.reference(value)) {
                return Ok(self.error_token(value,"Array concatenation ('++') requires compile-time constant operands; runtime variables and immutable bindings do not qualify."));
            }
            let is_array = match base(typ) {
                Type::Array { .. } => true,
                Type::Slice(_) => false,
                Type::Unknown | Type::Generic => continue,
                _ => return Ok(self.error_token(value,"Array concatenation ('++') requires fixed arrays or constant slices; use + for constant strings.")),
            };
            if arrays.is_some_and(|previous| previous != is_array) {
                return Ok(self.error_token(value,"Array concatenation ('++') cannot mix fixed arrays and slices; convert the operands to the same container kind explicitly."));
            }
            arrays = Some(is_array);
        }
        self.meta = true;
        let mut hint = expected
            .filter(|hint| {
                hint.semantic
                    .as_ref()
                    .is_none_or(|typ| matches!(base(typ), Type::Array { .. } | Type::Slice(_)))
            })
            .map(|hint| Hint {
                constant_literal: true,
                semantic: hint
                    .semantic
                    .as_ref()
                    .and_then(|typ| typ.element().cloned()),
                code: Code::concat([
                    self.g(format!("{}meta.type_elem_type(", self.prefix), id),
                    hint.code,
                    self.g(")", id),
                ]),
                element_only: true,
                plain_array: false,
            });
        let mut rendered_anchor = None;
        if hint.is_none() {
            let anchor = values
                .iter()
                .zip(&types)
                .find(|(value, typ)| {
                    !inline(source, **value)
                        && matches!(base(typ), Type::Array { .. } | Type::Slice(_))
                })
                .or_else(|| {
                    values.iter().zip(&types).find(|(value, _)| {
                        inline(source, **value)
                            && !source.group(unparen(source, **value), "elems").is_empty()
                    })
                });
            if let Some((&anchor, typ)) = anchor {
                let value = self.render(anchor, None, true)?;
                let type_of = self.native("type_of");
                let element = if inline(source, anchor) {
                    let first = source.group(unparen(source, anchor), "elems")[0];
                    let code = self.render(first, None, true)?;
                    self.default = true;
                    Code::concat([
                        self.g(format!("{type_of}({}default_value(", self.prefix), id),
                        code,
                        self.g("))", id),
                    ])
                } else {
                    Code::concat([
                        self.g(format!("{}meta.type_elem_type({type_of}(", self.prefix), id),
                        value.clone(),
                        self.g("))", id),
                    ])
                };
                hint = Some(Hint {
                    constant_literal: true,
                    semantic: typ.element().cloned(),
                    code: element,
                    element_only: true,
                    plain_array: false,
                });
                rendered_anchor = Some((anchor, value));
            }
        }
        let span = source.node(id).span;
        let mut parts = vec![self.gen(
            format!("{}meta.concatenate(", self.prefix),
            span,
            "concatenation",
        )];
        for comment in &source.comments {
            if span.contains(*comment)
                && !values
                    .iter()
                    .any(|value| source.node(*value).span.contains(*comment))
            {
                parts.push(self.copy(*comment));
                parts.push(self.g("\n", id));
            }
        }
        for (index, value) in values.into_iter().enumerate() {
            if index > 0 {
                parts.push(self.g(", ", id));
            }
            let code = match &rendered_anchor {
                Some((anchor, rendered)) if *anchor == value => rendered.clone(),
                _ => self.render(
                    value,
                    if inline(source, value) {
                        hint.clone()
                    } else {
                        None
                    },
                    true,
                )?,
            };
            parts.push(code);
        }
        parts.push(self.g(")", id));
        Ok(Code::concat(parts).expansion(&source.path, span))
    }
}
