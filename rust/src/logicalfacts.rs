//! Reference activation and operator routing shared by lowering and scheduling.
use super::*;
impl Resolver<'_> {
    pub(crate) fn logical_type_position(&self, r: Ref) -> bool {
        let source = &self.sources[r.file];
        let node = source.node(r.node);
        if node.kind != "Ident" { return false; }
        !node.parent.is_some_and(|parent| {
            let owner = source.node(parent);
            matches!(owner.kind.as_str(), "EnumType" | "ImplicitSelector" | "File")
                || matches!(owner.kind.as_str(), "Selector" | "FieldValue")
                    && source.role(parent, "field") == Some(r.node)
                || owner.kind == "Field" && source.group(parent, "names").contains(&r.node)
        })
    }
    pub(crate) fn logical_enabled(&self, file: usize) -> bool {
        self.sources[file].thor
            && *self.logical_activation.get_or_init(|| {
                let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap()
                    .join("runtime");
                self.sources.iter().enumerate().any(|(file, source)| {
                    let internal = ["pure", "eval", "format"]
                        .iter()
                        .any(|name| source.path.parent() == Some(root.join(name).as_path()));
                    !internal
                        && source.nodes.iter().enumerate().any(|(index, node)| {
                            node.kind == "Import"
                                && crate::lower::unquote(&node.op).ok().as_deref()
                                    == Some("thor:big")
                                || source.thor
                                    && node.kind == "Ident"
                                    && matches!(source.text(NodeId(index)), "Integer" | "Rational")
                                    && self.logical_type_position(Ref { file, node: NodeId(index) })
                                    && self
                                        .symbol(Ref {
                                            file,
                                            node: NodeId(index),
                                        })
                                        .is_none()
                        })
                })
            })
    }
    pub(crate) fn logical_operator(&mut self, r: Ref, expected: Option<&Type>) -> bool {
        let source = &self.sources[r.file];
        let node = source.node(r.node);
        let args = match node.kind.as_str() {
            "Unary" if matches!(node.op.as_str(), "+" | "-" | "~") => {
                source.role(r.node, "expr").into_iter().collect::<Vec<_>>()
            }
            "Binary"
                if matches!(
                    node.op.as_str(),
                    "+" | "-"
                        | "*"
                        | "/"
                        | "%"
                        | "%%"
                        | "**"
                        | "&"
                        | "|"
                        | "~"
                        | "&~"
                        | "<<"
                        | ">>"
                        | "=="
                        | "!="
                        | "<"
                        | "<="
                        | ">"
                        | ">="
                ) =>
            {
                [source.role(r.node, "left"), source.role(r.node, "right")]
                    .into_iter()
                    .flatten()
                    .collect()
            }
            _ => return false,
        }
        .into_iter()
        .map(|node| Ref { file: r.file, node })
        .collect::<Vec<_>>();
        if args.iter().any(|arg| self.denotes_type(*arg))
            || args
                .iter()
                .any(|arg| matches!(self.type_of(*arg),Type::Scalar{name,..} if name=="typeid"))
        {
            return false;
        }
        if args
            .iter()
            .any(|arg| self.type_of(*arg).big_name().is_some())
        {
            return true;
        }
        if args.iter().any(|arg| {
            self.type_of(*arg).is_unknown()
                && source.node(arg.node).kind != "ImplicitSelector"
                && !matches!(source.text(arg.node), "nil" | "---")
        }) {
            return true;
        }
        expected.is_some_and(|typ| typ.big_name().is_some() || matches!(typ, Type::Generic))
            && args.into_iter().all(|arg| self.untyped_operand(arg))
    }
}
impl Resolver<'_> {
    pub(crate) fn logical_numeric_result(&mut self, r: Ref) -> Option<Type> {
        let source = &self.sources[r.file];
        if !source.thor || source.node(r.node).kind != "Call" {
            return None;
        }
        let callee = Ref {
            file: r.file,
            node: source.role(r.node, "callee")?,
        };
        if crate::extremaplan::name(self, callee, &mut HashSet::new()).is_none()
            && !["abs", "clamp"]
                .iter()
                .any(|name| self.native_builtin(callee, name))
        {
            return None;
        }
        let mut candidates = vec![];
        if let Some(plan) = crate::extremaplan::plan(self, r) {
            for (kind, node) in plan.parts {
                let typ = self.type_of(Ref { file: r.file, node });
                candidates.push(if kind == crate::extremaplan::Part::Scalar {
                    typ
                } else {
                    typ.element().cloned().unwrap_or(Type::Unknown)
                });
            }
        } else {
            for node in source.group(r.node, "args") {
                let at = Ref {
                    file: r.file,
                    node: *node,
                };
                if !self.denotes_type(at) {
                    candidates.push(self.type_of(at));
                }
            }
        }
        candidates
            .iter()
            .find(|typ| typ.big_name().is_some())
            .or_else(|| candidates.iter().find(|typ| typ.is_unknown()))
            .cloned()
    }
}

impl Resolver<'_> {
    pub(crate) fn logical_constructor(&mut self, r: Ref) -> Option<Type> {
        let source = &self.sources[r.file];
        if source.node(r.node).kind != "Call" {
            return None;
        }
        let callee = Ref {
            file: r.file,
            node: source.role(r.node, "callee")?,
        };
        if !self.denotes_type(callee) {
            return None;
        }
        let typ = self.type_of(callee);
        typ.big_name()?;
        Some(typ)
    }
    pub(crate) fn logical_literal(&self, r: Ref, seen: &mut HashSet<Ref>) -> Option<String> {
        if !seen.insert(r) {
            return None;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        match n.kind.as_str() {
            "Paren" => self.logical_literal(
                Ref {
                    file: r.file,
                    node: s.role(r.node, "expr")?,
                },
                seen,
            ),
            "Literal" if matches!(n.op.as_str(), "integer" | "float") => {
                Some(s.text(r.node).into())
            }
            "Unary" if matches!(n.op.as_str(), "+" | "-") => Some(format!(
                "{}{}",
                n.op,
                self.logical_literal(
                    Ref {
                        file: r.file,
                        node: s.role(r.node, "expr")?
                    },
                    seen
                )?
            )),
            "Ident" => {
                let symbol = &self.symbols[self.symbol(r)?];
                let owner = &self.sources[symbol.reference.file];
                if !symbol.constant
                    || symbol.annotation.is_some()
                    || owner.node(symbol.reference.node).kind != "Declaration"
                {
                    return None;
                }
                self.logical_literal(
                    Ref {
                        file: symbol.reference.file,
                        node: symbol.value?,
                    },
                    seen,
                )
            }
            _ => None,
        }
    }
    pub(crate) fn logical_dynamic_property(&mut self, r: Ref) -> bool {
        let source = &self.sources[r.file];
        if source.node(r.node).kind != "Selector" {
            return false;
        }
        let Some(field) = source.role(r.node, "field") else {
            return false;
        };
        if !matches!(source.text(field), "numerator" | "denominator") {
            return false;
        }
        let Some(base) = source.role(r.node, "expr") else {
            return false;
        };
        let at = Ref {
            file: r.file,
            node: base,
        };
        if self.denotes_type(at) {
            return false;
        }
        if source.node(base).kind == "Ident" && self.symbol(at).is_none() {
            let namespace = source.text(base);
            if source.nodes.iter().any(|node| {
                if node.kind != "Import" {
                    return false;
                }
                let name = source.names.get(node.name);
                if !name.is_empty() {
                    return name == namespace;
                }
                crate::lower::unquote(&node.op).ok().is_some_and(|path| {
                    path.rsplit('/').next().unwrap_or(&path).rsplit(':').next() == Some(namespace)
                })
            }) {
                return false;
            }
        }
        let mut typ = self.type_of(at);
        while let Type::Pointer(inner) = typ {
            typ = *inner;
        }
        typ.is_unknown()
    }
    pub(crate) fn logical_fresh(&mut self, mut r: Ref) -> bool {
        while self.sources[r.file].node(r.node).kind == "Paren" {
            let Some(expr) = self.sources[r.file].role(r.node, "expr") else {
                break;
            };
            r.node = expr;
        }
        let source = &self.sources[r.file];
        let node = source.node(r.node);
        if node.kind == "Literal" || self.logical_literal(r, &mut HashSet::new()).is_some() {
            return false;
        }
        let typ = self.type_of(r);
        match node.kind.as_str() {
            "Binary" | "Unary" if self.logical_operator(r, None) => {
                !matches!(node.op.as_str(), "==" | "!=" | "<" | "<=" | ">" | ">=")
            }
            "Selector" => {
                let known = source
                    .role(r.node, "field")
                    .is_some_and(|field| matches!(source.text(field), "numerator" | "denominator"))
                    && source.role(r.node, "expr").is_some_and(|expr| {
                        let mut typ = self.type_of(Ref {
                            file: r.file,
                            node: expr,
                        });
                        while let Type::Pointer(inner) = typ {
                            typ = *inner;
                        }
                        matches!(typ, Type::BigRational)
                    });
                known || self.logical_dynamic_property(r)
            }
            "Compound" => typ.big_name().is_some(),
            "OrElse" | "OrReturn" | "OrBranch" | "OrPanic" | "Run" => {
                typ.big_name().is_some() || matches!(typ, Type::Generic)
            }
            "Call" if !self.denotes_type(r) => {
                if typ.big_name().is_some() || self.logical_numeric_result(r).is_some() {
                    return true;
                }
                let Some(callee) = source.role(r.node, "callee") else {
                    return false;
                };
                self.signature(Ref {
                    file: r.file,
                    node: callee,
                })
                .is_some_and(|signature| !self.fields(signature, "results").is_empty())
                    && typ.is_unknown()
            }
            _ => false,
        }
    }
    pub(crate) fn logical_generic_reference(
        &mut self,
        mut r: Ref,
        seen: &mut HashSet<Ref>,
    ) -> bool {
        while self.sources[r.file].node(r.node).kind == "Paren" {
            let Some(expr) = self.sources[r.file].role(r.node, "expr") else {
                break;
            };
            r.node = expr;
        }
        if !seen.insert(r) {
            return false;
        }
        if matches!(self.type_of(r), Type::Generic) {
            return true;
        }
        let source = &self.sources[r.file];
        let node = source.node(r.node);
        if matches!(node.kind.as_str(), "Selector" | "Index" | "Deref") {
            return source.role(r.node, "expr").is_some_and(|expr| {
                self.logical_generic_reference(
                    Ref {
                        file: r.file,
                        node: expr,
                    },
                    seen,
                )
            });
        }
        if node.kind == "Ident" {
            if let Some(symbol) = self.symbol(r).map(|index| self.symbols[index].clone()) {
                if symbol.reference.file == r.file {
                    return symbol.value.is_some_and(|value| {
                        self.logical_generic_reference(
                            Ref {
                                file: r.file,
                                node: value,
                            },
                            seen,
                        )
                    });
                }
            }
        }
        false
    }
}
