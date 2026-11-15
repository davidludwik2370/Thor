//! Selected native branches share contextual type and constant substitutions.
use super::*;
// Literal coercibility is an expression property, not part of native typeid
// identity. A generic inferred from `3` still has type `int`.
fn type_identity(typ: Type) -> Type {
    match typ {
        Type::Scalar { name, .. } => Type::Scalar {
            name,
            untyped: false,
        },
        Type::String { name, .. } => Type::String {
            name,
            untyped: false,
        },
        Type::Array { elem, length } => Type::Array {
            elem: Box::new(type_identity(*elem)),
            length,
        },
        Type::Fixed { elem, length } => Type::Fixed {
            elem: Box::new(type_identity(*elem)),
            length,
        },
        Type::Slice(elem) => Type::Slice(Box::new(type_identity(*elem))),
        Type::Dynamic(elem) => Type::Dynamic(Box::new(type_identity(*elem))),
        Type::Pointer(elem) => Type::Pointer(Box::new(type_identity(*elem))),
        Type::Nominal(owner, elem) => Type::Nominal(owner, Box::new(type_identity(*elem))),
        Type::Record(owner, args) => Type::Record(
            owner,
            args.into_iter()
                .map(|(name, typ)| (name, type_identity(typ)))
                .collect(),
        ),
        other => other,
    }
}
impl Resolver<'_> {
    pub fn context_condition(&mut self, r: Ref, bindings: &Substitutions) -> Option<bool> {
        self.context_condition_seen(r, bindings, &mut HashSet::new())
    }
    fn context_condition_seen(
        &mut self,
        r: Ref,
        bindings: &Substitutions,
        seen: &mut HashSet<Ref>,
    ) -> Option<bool> {
        if !seen.insert(r) {
            self.incomplete_plans.set(self.incomplete_plans.get().wrapping_add(1));
            return None;
        }
        if let Some(Constant::Boolean(value)) = self.constant_value(r, bindings, None) {
            return Some(value);
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let child = |name| s.role(r.node, name).map(|node| Ref { file: r.file, node });
        match n.kind.as_str() {
            "Ident" | "Selector" => {
                if let Some(symbol) = self.symbol(r) {
                    let symbol = self.symbols[symbol].clone();
                    if symbol.constant {
                        if let Some(node) = symbol.value {
                            return self.context_condition_seen(
                                Ref {
                                    file: symbol.reference.file,
                                    node,
                                },
                                bindings,
                                seen,
                            );
                        }
                    }
                }
            }
            "Paren" => return self.context_condition_seen(child("expr")?, bindings, seen),
            "Unary" if n.op == "!" => {
                return self
                    .context_condition_seen(child("expr")?, bindings, seen)
                    .map(|v| !v)
            }
            "Binary" => {
                let (left, right) = (child("left")?, child("right")?);
                let op = n.op.clone();
                if op == "&&" || op == "||" {
                    let a = self.context_condition_seen(left, bindings, &mut seen.clone());
                    let b = self.context_condition_seen(right, bindings, &mut seen.clone());
                    return match (op.as_str(), a, b) {
                        ("&&", Some(false), _) | ("&&", _, Some(false)) => Some(false),
                        ("&&", Some(true), Some(true)) => Some(true),
                        ("||", Some(true), _) | ("||", _, Some(true)) => Some(true),
                        ("||", Some(false), Some(false)) => Some(false),
                        _ => None,
                    };
                }
                if (op == "==" || op == "!=")
                    && (self.denotes_type(left) || self.bound_type(left, bindings).is_some())
                    && (self.denotes_type(right) || self.bound_type(right, bindings).is_some())
                {
                    let a = self.specialized_type(left, bindings);
                    let b = self.specialized_type(right, bindings);
                    if a.resolved() && b.resolved() {
                        return Some((type_identity(a) == type_identity(b)) == (op == "=="));
                    }
                }
            }
            "Call" => {
                let callee = child("callee")?;
                let args = s.group(r.node, "args");
                if args.len() == 1 {
                    if let Some(name) = self.library_member(callee, "base:intrinsics") {
                        let arg = Ref {
                            file: r.file,
                            node: args[0],
                        };
                        let typ = self.specialized_type(arg, bindings);
                        if !typ.is_unknown() {
                            let mut base = &typ;
                            while let Type::Nominal(_, inner) = base {
                                base = inner;
                            }
                            return Some(match name.as_str() {
                                "type_is_slice" => matches!(base, Type::Slice(_)),
                                "type_is_array" => matches!(base, Type::Array { .. }),
                                "type_is_dynamic_array" => matches!(base, Type::Dynamic(_)),
                                "type_is_pointer" => matches!(base, Type::Pointer(_)),
                                "type_is_struct" => {
                                    matches!(base, Type::Struct(_) | Type::Record(..))
                                }
                                "type_is_map" => matches!(base, Type::Map { .. }),
                                "type_is_union" => matches!(base, Type::Union(..)),
                                "type_is_enum" => matches!(base, Type::Enum(_)),
                                "type_is_string" => matches!(base, Type::String { .. }),
                                "type_is_integer" => {
                                    matches!(base,Type::Scalar{name,..}if !name.starts_with('f')&&base.numeric())
                                }
                                "type_is_float" => {
                                    matches!(base,Type::Scalar{name,..}if name.starts_with('f')&&base.numeric())
                                }
                                _ => return None,
                            });
                        }
                    }
                }
            }
            _ => {}
        }
        self.condition(r)
    }
    /// A recognized library symbol must come from the actual SDK collection.
    pub fn library_member(&self, r: Ref, path: &str) -> Option<String> {
        if !self.standard_library(r, path) {
            return None;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        if n.kind == "Selector" {
            let base = s.role(r.node, "expr")?;
            if s.node(base).kind != "Ident"
                || self
                    .lookup(
                        Ref {
                            file: r.file,
                            node: base,
                        },
                        s.text(base),
                    )
                    .is_some()
            {
                return None;
            }
            let field = s.role(r.node, "field")?;
            if self.namespace_imports(r.file, s.text(base)).iter().any(|import| import == path) {
                return Some(s.text(field).into());
            }
        }
        if n.kind == "Ident"
            && self.lookup(r, s.text(r.node)).is_none()
            && s.selections.iter().any(|selection| {
                crate::lower::unquote(&selection.path).ok().as_deref() == Some(path)
                    && (selection.wildcard
                        || selection.names.iter().any(|name| name == s.text(r.node)))
            })
        {
            return Some(s.text(r.node).into());
        }
        None
    }
    /// Follow constant aliases and namespace imports without trusting a local
    /// procedure merely because it has an SDK procedure's name.
    pub fn library_alias_member(&self, r: Ref, path: &str) -> Option<String> {
        self.library_alias_seen(r, path, &mut std::collections::HashSet::new())
    }
    fn library_alias_seen(
        &self,
        r: Ref,
        path: &str,
        seen: &mut std::collections::HashSet<Ref>,
    ) -> Option<String> {
        if !seen.insert(r) {
            return None;
        }
        if let Some(name) = self.library_member(r, path) {
            return Some(name);
        }
        let source = &self.sources[r.file];
        if source.node(r.node).kind == "Paren" {
            return source
                .role(r.node, "expr")
                .and_then(|node| self.library_alias_seen(Ref { file: r.file, node }, path, seen));
        }
        let symbol = &self.symbols[self.symbol(r)?];
        if !symbol.constant || symbol.immutable {
            return None;
        }
        let owner = &self.sources[symbol.reference.file];
        if self.standard_library(r, path) {
            let (collection, suffix) = path.split_once(':')?;
            let expected = std::path::Path::new(source.info["odin_root"].as_str()?)
                .join(collection)
                .join(suffix);
            if owner.path.parent() == Some(expected.as_path()) {
                return Some(symbol.name.clone());
            }
        }
        let value = Ref {
            file: symbol.reference.file,
            node: symbol.value?,
        };
        matches!(
            owner.node(value.node).kind.as_str(),
            "Ident" | "Selector" | "Paren"
        )
        .then(|| self.library_alias_seen(value, path, seen))
        .flatten()
    }
}
