//! Constant, independently constructed slice literals may share read-only data.
//! A read-only view of existing storage must never be promoted this way.
use super::*;

fn base(mut typ: &Type) -> &Type {
    while let Type::Nominal(_, inner) = typ {
        typ = inner;
    }
    typ
}

fn atom(typ: &Type) -> bool {
    matches!(
        base(typ),
        Type::Scalar { .. } | Type::String { .. } | Type::Enum(_)
    )
}

impl Lower<'_, '_> {
    pub(super) fn readonly_data(&mut self, id: NodeId) -> Option<&'static str> {
        if self.has_scoped && !crate::scoped::names(self.source,id).is_empty() {return None;}
        let node = self.source.node(id);
        if !self.source.thor || !node.immutable || !self.source.group(id, "attributes").is_empty() {
            return None;
        }
        let parent = node.parent?;
        // Attributes are statements, not expressions or loop/if initializers.
        if !matches!(
            self.source.node(parent).kind.as_str(),
            "File" | "Block" | "Case"
        ) || self.source.node(parent).kind == "Block"
            && !self
                .source
                .slice(self.source.node(parent).span)
                .starts_with('{')
        {
            return None;
        }
        let values = self.source.group(id, "values").to_vec();
        if values.is_empty() || values.len() != self.source.group(id, "names").len() {
            return None;
        }
        // Layouts can expand exponentially through generic record fields. A
        // storage optimization must stay bounded and fall back without error.
        self.readonly_data_budget = self.source.nodes.len().saturating_mul(16).max(1024);
        let annotation = self.source.role(id, "type").map(|node| self.typ(node));
        for mut value in values {
            while self.source.node(value).kind == "Paren" {
                value = self.source.role(value, "expr")?;
            }
            if self.source.node(value).kind != "Compound" {
                return None;
            }
            let typ = annotation.clone().unwrap_or_else(|| self.typ(value));
            if !matches!(base(&typ), Type::Slice(_))
                || !self.readonly_constant(self.reference(value), &typ, &mut HashSet::new())
            {
                return None;
            }
        }
        Some(
            if self
                .source
                .ancestors(id)
                .any(|id| self.source.node(id).kind == "Procedure")
            {
                "@(static, rodata)\n"
            } else {
                "@(rodata)\n"
            },
        )
    }

    // Native values only: logical big numbers, dynamic containers, pointers,
    // unions, and unresolved generic layouts retain their existing lowering.
    fn readonly_shape(&mut self, typ: &Type, inline: bool, depth: usize) -> bool {
        if depth > 64 || self.readonly_data_budget == 0 {
            return false;
        }
        self.readonly_data_budget -= 1;
        match base(typ) {
            Type::Scalar { name, .. } => !matches!(name.as_str(), "any" | "rawptr" | "typeid"),
            Type::String { .. } | Type::Enum(_) => true,
            Type::Array { elem, length } => {
                length.is_some() && self.readonly_shape(elem, inline, depth + 1)
            }
            Type::Slice(elem) => !inline && self.readonly_shape(elem, false, depth + 1),
            Type::Struct(owner) | Type::Record(owner, _) => {
                let owner = *owner;
                if self.resolver.sources[owner.file].node(owner.node).op == "raw_union" {
                    return false;
                }
                let fields = self.resolver.record_fields(typ);
                if !fields
                    .iter()
                    .all(|(_, typ)| self.readonly_shape(typ, inline, depth + 1))
                {
                    return false;
                }
                let source = &self.resolver.sources[owner.file];
                let defaults: Vec<_> = source
                    .role(owner.node, "fields")
                    .into_iter()
                    .flat_map(|node| &source.node(node).children)
                    .filter_map(|field| {
                        source.role(*field, "default").map(|value| {
                            let name = source
                                .group(*field, "names")
                                .first()
                                .map(|name| source.text(*name));
                            (
                                value,
                                fields
                                    .iter()
                                    .find(|(field, _)| Some(field.as_str()) == name)
                                    .map(|(_, typ)| typ.clone()),
                            )
                        })
                    })
                    .collect();
                defaults.into_iter().all(|(node, typ)| {
                    typ.is_some_and(|typ| {
                        self.readonly_constant(
                            Ref {
                                file: owner.file,
                                node,
                            },
                            &typ,
                            &mut HashSet::new(),
                        )
                    })
                })
            }
            _ => false,
        }
    }

    fn readonly_constant(&mut self, at: Ref, expected: &Type, seen: &mut HashSet<Ref>) -> bool {
        if self.readonly_data_budget == 0 || !seen.insert(at) || seen.len() > 128 {
            return false;
        }
        self.readonly_data_budget -= 1;
        let source = &self.resolver.sources[at.file];
        let node = source.node(at.node);
        let child = |name| {
            source.role(at.node, name).map(|node| Ref {
                file: at.file,
                node,
            })
        };
        if node.kind == "Paren" {
            return child("expr")
                .is_some_and(|value| self.readonly_constant(value, expected, seen));
        }
        if !self.readonly_shape(expected, false, 0) {
            return false;
        }
        if self.resolver.evaluated.contains_key(&at) {
            // Runtime materialization of strings and aggregate results can
            // transfer owned allocations. Do not silently turn those into
            // borrowed static data, even though their contents are known.
            return matches!(base(expected), Type::Scalar { .. } | Type::Enum(_));
        }
        match node.kind.as_str() {
            "Literal" => {
                let typ = self.resolver.type_of(at);
                atom(expected) && atom(&typ) && expected.is_string() == typ.is_string()
            }
            "ImplicitSelector" => matches!(base(expected), Type::Enum(_)),
            "Compound" => {
                let fields = self.resolver.record_fields(expected);
                let element = expected.element().cloned();
                let elems = source.group(at.node, "elems").to_vec();
                elems.into_iter().enumerate().all(|(index, elem)| {
                    let value = source.role(elem, "value").unwrap_or(elem);
                    let field = source.role(elem, "field").map(|field| source.text(field));
                    let typ = element.as_ref().or_else(|| {
                        field
                            .and_then(|name| {
                                fields
                                    .iter()
                                    .find(|(field, _)| field == name)
                                    .map(|(_, typ)| typ)
                            })
                            .or_else(|| fields.get(index).map(|(_, typ)| typ))
                    });
                    typ.is_some_and(|typ| {
                        self.readonly_constant(
                            Ref {
                                file: at.file,
                                node: value,
                            },
                            typ,
                            &mut seen.clone(),
                        )
                    })
                })
            }
            "Unary" if matches!(node.op.as_str(), "+" | "-" | "~" | "!") => child("expr")
                .is_some_and(|value| {
                    atom(expected) && self.readonly_constant(value, expected, seen)
                }),
            "Binary"
                if matches!(
                    node.op.as_str(),
                    "+" | "-"
                        | "*"
                        | "/"
                        | "%"
                        | "%%"
                        | "<<"
                        | ">>"
                        | "&"
                        | "|"
                        | "~"
                        | "&~"
                        | "=="
                        | "!="
                        | "<"
                        | ">"
                        | "<="
                        | ">="
                        | "&&"
                        | "||"
                ) =>
            {
                ["left", "right"].iter().all(|name| {
                    child(name).is_some_and(|value| {
                        let typ = self.resolver.type_of(value);
                        atom(&typ) && self.readonly_constant(value, &typ, &mut seen.clone())
                    })
                })
            }
            "Ternary" | "WhenExpr" => ["cond", "x", "y"].iter().all(|name| {
                child(name).is_some_and(|value| {
                    let typ = if *name == "cond" {
                        Type::scalar("bool")
                    } else {
                        expected.clone()
                    };
                    self.readonly_constant(value, &typ, &mut seen.clone())
                })
            }),
            "Call" => {
                let cast =
                    child("callee").is_some_and(|callee| self.resolver.known_type_operand(callee));
                let builtin = crate::purity::builtin_call(self.resolver, at);
                if !atom(expected)
                    || !self.readonly_shape(expected, true, 0)
                    || !cast
                        && !matches!(
                            builtin.as_deref(),
                            Some(
                                "len"
                                    | "size_of"
                                    | "align_of"
                                    | "offset_of"
                                    | "min"
                                    | "max"
                                    | "abs"
                                    | "clamp"
                                    | "complex"
                                    | "quaternion"
                                    | "real"
                                    | "imag"
                                    | "jmag"
                                    | "kmag"
                                    | "conj"
                            )
                        )
                {
                    return false;
                }
                source.group(at.node, "args").iter().all(|node| {
                    let value = source.role(*node, "value").unwrap_or(*node);
                    let value = Ref {
                        file: at.file,
                        node: value,
                    };
                    let typ = self.resolver.type_of(value);
                    // Slice view conversions and len(slice_literal) remain
                    // runtime expressions in Odin, even with literal contents.
                    self.readonly_shape(&typ, true, 0)
                        && self.readonly_constant(value, &typ, &mut seen.clone())
                })
            }
            "Ident" | "Selector" | "Index" => {
                // A named slice constant may still have writable aliases. Only
                // inline values can be read from other declarations here.
                if !self.readonly_shape(expected, true, 0) {
                    return false;
                }
                if let Some(index) = self.resolver.symbol(at) {
                    let symbol = self.resolver.symbols[index].clone();
                    return symbol.constant
                        && !symbol.immutable
                        && symbol.value.is_some_and(|node| {
                            self.readonly_constant(
                                Ref {
                                    file: symbol.reference.file,
                                    node,
                                },
                                expected,
                                seen,
                            )
                        });
                }
                if node.kind == "Selector"
                    && child("expr").is_some_and(|expr| self.resolver.known_type_operand(expr))
                {
                    return matches!(base(expected), Type::Enum(_));
                }
                if matches!(node.kind.as_str(), "Selector" | "Index") {
                    // Array swizzles construct runtime values in Odin.
                    if node.kind == "Selector" && !atom(expected) {
                        return false;
                    }
                    return child("expr").is_some_and(|expr| {
                        let typ = self.resolver.type_of(expr);
                        self.readonly_shape(&typ, true, 0)
                            && self.readonly_constant(expr, &typ, &mut seen.clone())
                    }) && (node.kind != "Index"
                        || child("index").is_some_and(|index| {
                            self.readonly_constant(index, &Type::scalar("int"), &mut seen.clone())
                        }));
                }
                matches!(source.text(at.node), "true" | "false" | "nil")
            }
            _ => false,
        }
    }
}
