//! Reference activation and operator routing shared by lowering and scheduling.
use super::*;
impl Resolver<'_> {
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
