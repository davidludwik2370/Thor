//! Selected native branches share contextual type and constant substitutions.
use super::*;
// Literal coercibility is an expression property, not part of native typeid
// identity. A generic inferred from `3` still has type `int`.
fn type_identity(typ: Type) -> Type {
    match typ {
        Type::Scalar { name, .. } => Type::Scalar {
            name: match name.as_str() {
                "byte" => "u8".into(),
                _ => name,
            },
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
        Type::Matrix {
            elem,
            rows,
            columns,
            row_major,
        } => Type::Matrix {
            elem: Box::new(type_identity(*elem)),
            rows,
            columns,
            row_major,
        },
        Type::Vector { elem, length } => Type::Vector {
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
        Type::Map { key, value } => Type::Map {
            key: Box::new(type_identity(*key)), value: Box::new(type_identity(*value)),
        },
        Type::Set(elem) => Type::Set(Box::new(type_identity(*elem))),
        Type::Soa(elem) => Type::Soa(Box::new(type_identity(*elem))),
        Type::SoaStorage(seq) => Type::SoaStorage(Box::new(type_identity(*seq))),
        Type::Anonymous(fields) => Type::Anonymous(fields.into_iter()
            .map(|(name, typ)| (name, type_identity(typ))).collect()),
        Type::Union(owner, args) => Type::Union(owner, args.into_iter()
            .map(|(name, typ)| (name, type_identity(typ))).collect()),
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
    // Two inline record definitions with identical layouts can have the same
    // native typeid. A source offset only identifies a named declaration.
    fn declared_identity(&self, owner: Ref) -> bool {
        let source = &self.sources[owner.file];
        source.node(owner.node).parent.is_some_and(|id|
            source.node(id).kind == "Declaration" && source.group(id, "values").contains(&owner.node))
            && !source.ancestors(owner.node).any(|id| source.node(id).kind == "Procedure")
    }
    fn exact_identity(&self, typ: &Type) -> bool {
        match typ {
            // Signature references/effect qualifiers are not native typeids.
            // Distinct signatures can describe the same Odin procedure type, and
            // fn's pure qualifier is deliberately erased in native type queries.
            Type::Function(..) | Type::Procedure(_) => false,
            Type::Scalar { name, .. } => name != "bit_set",
            Type::Nominal(_, inner)
            | Type::Slice(inner)
            | Type::Dynamic(inner)
            | Type::SoaStorage(inner)
            | Type::Soa(inner)
            | Type::Set(inner) => self.exact_identity(inner),
            // Pointer erases the distinction between ^T and [^]T. An array
            // with no resolved length can also be an enum-indexed array.
            Type::Pointer(_) | Type::Array { length: None, .. } => false,
            Type::Array { elem, .. }
            | Type::Vector { elem, .. }
            | Type::Matrix { elem, .. }
            | Type::Fixed { elem, .. } => self.exact_identity(elem),
            Type::Map { key, value } => self.exact_identity(key) && self.exact_identity(value),
            Type::Record(owner, fields) | Type::Union(owner, fields) => {
                self.declared_identity(*owner) && fields.iter().all(|(_, typ)| self.exact_identity(typ))
            }
            Type::Struct(owner) | Type::Enum(owner) => self.declared_identity(*owner),
            Type::Anonymous(_) => false,
            _ => typ.resolved(),
        }
    }
    fn effect_qualified_type(
        &mut self,
        typ: &Type,
        bindings: &Substitutions,
        seen: &mut HashSet<Type>,
    ) -> bool {
        match typ {
            Type::Function(..) => return true,
            Type::Scalar { .. }
            | Type::String { .. }
            | Type::Enum(_)
            | Type::Unknown
            | Type::Generic
            | Type::Constant(_)
            | Type::BigInteger
            | Type::BigRational => return false,
            _ => {}
        }
        if !seen.insert(typ.clone()) {
            return false;
        }
        match typ {
            Type::Procedure(signature) => ["params", "results"]
                .into_iter()
                .flat_map(|role| self.fields(*signature, role))
                .collect::<Vec<_>>()
                .iter()
                .filter_map(|(_, annotation)| *annotation)
                .any(|annotation| {
                    let typ = self.specialized_type(annotation, bindings);
                    self.effect_qualified_type(&typ, bindings, seen)
                }),
            Type::Nominal(_, t)
            | Type::Pointer(t)
            | Type::Slice(t)
            | Type::Dynamic(t)
            | Type::SoaStorage(t)
            | Type::Soa(t)
            | Type::Set(t)
            | Type::Array { elem: t, .. }
            | Type::Fixed { elem: t, .. }
            | Type::Matrix { elem: t, .. }
            | Type::Vector { elem: t, .. } => self.effect_qualified_type(t, bindings, seen),
            Type::Map { key, value } => {
                self.effect_qualified_type(key, bindings, seen)
                    || self.effect_qualified_type(value, bindings, seen)
            }
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .record_fields(typ)
                .iter()
                .any(|(_, t)| self.effect_qualified_type(t, bindings, seen)),
            Type::Union(..) => self
                .union_variants(typ)
                .iter()
                .any(|t| self.effect_qualified_type(t, bindings, seen)),
            _ => false,
        }
    }
    /// Compare concrete procedure shapes after effect erasure. Their syntax
    /// references are not type identities. Unknown/defaulted/polymorphic native
    /// signatures stay unknown rather than selecting the wrong `when` arm.
    fn callable_identity(
        &mut self,
        typ: &Type,
        seen: &mut HashSet<Type>,
    ) -> Option<serde_json::Value> {
        if !seen.insert(typ.clone()) {
            return None;
        }
        let result = (|| {
            let key = match typ {
                Type::Function(signature, _) | Type::Procedure(signature) => {
                    let captures = match typ {
                        Type::Function(_, captures) => captures.iter().cloned().collect(),
                        _ => Substitutions::new(),
                    };
                    let source = &self.sources[signature.file];
                    if source.text(signature.node).contains(['#', '!'])
                        || source.text(signature.node).contains("..")
                        || source
                            .text(signature.node)
                            .split(|c: char| !c.is_alphanumeric() && c != '_')
                            .any(|word| word == "using")
                        || source.descendants(signature.node).any(|node| {
                            source.node(node).kind == "PolyType"
                                || source.node(node).kind == "Field"
                                    && source.role(node, "default").is_some()
                        })
                    {
                        return None;
                    }
                    let cc = source.node(signature.node).data["calling_convention"]
                        .as_str()
                        .filter(|s| !s.is_empty())
                        .unwrap_or("odin")
                        .to_string();
                    if !matches!(cc.as_str(), "odin" | "contextless") {
                        return None;
                    }
                    let mut roles = vec![];
                    for role in ["params", "results"] {
                        let mut types = vec![];
                        for (name, annotation) in self.fields(*signature, role) {
                            let field = self.specialized_type(annotation?, &captures);
                            types.push(serde_json::json!([
                                name,
                                self.callable_identity(&field, seen)?
                            ]));
                        }
                        roles.push(types);
                    }
                    serde_json::json!(["procedure", cc, roles])
                }
                Type::Pointer(_) => return None,
                Type::Slice(t) => serde_json::json!(["slice", self.callable_identity(t, seen)?]),
                Type::Dynamic(t) => {
                    serde_json::json!(["dynamic", self.callable_identity(t, seen)?])
                }
                Type::Array { elem, length } => serde_json::json!([
                    "array",
                    length.as_ref()?,
                    self.callable_identity(elem, seen)?
                ]),
                // Syntax references alone cannot identify anonymous bases or
                // caller-local/generic native nominal types precisely.
                Type::Nominal(..)
                | Type::Struct(_)
                | Type::Record(..)
                | Type::Union(..)
                | Type::Enum(_) => return None,
                Type::Scalar { .. }
                | Type::String { .. }
                | Type::BigInteger
                | Type::BigRational
                    if self.exact_identity(typ) =>
                {
                    serde_json::json!(format!("{:?}", type_identity(typ.clone())))
                }
                _ => return None,
            };
            Some(key)
        })();
        seen.remove(typ);
        result
    }
    pub(crate) fn native_boolean(&self, reference: Ref) -> Option<bool> {
        let source = &self.sources[reference.file];
        let name = source.text(reference.node);
        if !matches!(name, "ODIN_DEBUG" | "ODIN_VALGRIND_SUPPORT" | "ODIN_NO_CRT")
            || self.symbol(reference).is_some()
        {
            return None;
        }
        match name {
            "ODIN_NO_CRT" => Some(self.flags.iter().any(|flag| flag == "-no-crt")),
            "ODIN_DEBUG" => Some(self.flags.iter().any(|flag| flag == "-debug")),
            "ODIN_VALGRIND_SUPPORT" => {
                let target = self
                    .flags
                    .iter()
                    .find_map(|flag| flag.strip_prefix("-target:"))
                    .and_then(|target| target.split_once('_'));
                let host_arch = if cfg!(target_arch = "x86_64") {
                    "amd64"
                } else {
                    std::env::consts::ARCH
                };
                let (os, arch) = target.unwrap_or((std::env::consts::OS, host_arch));
                // Odin exposes Valgrind intrinsics on non-Windows amd64 targets.
                Some(os != "windows" && arch == "amd64")
            }
            _ => None,
        }
    }
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
            self.incomplete_plans
                .set(self.incomplete_plans.get().wrapping_add(1));
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
                    let erased=self.pure_callbacks_present() && (
                        self.effect_qualified_type(&a,bindings,&mut HashSet::new())
                        ||self.effect_qualified_type(&b,bindings,&mut HashSet::new()));
                    if !erased && a.resolved() && b.resolved() && self.exact_identity(&a) && self.exact_identity(&b) {
                        return Some((type_identity(a) == type_identity(b)) == (op == "=="));
                    }
                    if a.resolved() && b.resolved() {
                        if let (Some(a),Some(b))=(self.callable_identity(&a,&mut HashSet::new()),self.callable_identity(&b,&mut HashSet::new())) {
                            return Some((a==b)==(op=="=="));
                        }
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
                                "type_is_array" if matches!(base, Type::Array { length: None, .. }) => return None,
                                "type_is_array" => matches!(base, Type::Array { .. }),
                                "type_is_matrix" => matches!(base, Type::Matrix { .. }),
                                "type_is_matrix_row_major" => matches!(
                                    base,
                                    Type::Matrix {
                                        row_major: true,
                                        ..
                                    }
                                ),
                                "type_is_matrix_column_major" => matches!(
                                    base,
                                    Type::Matrix {
                                        row_major: false,
                                        ..
                                    }
                                ),
                                "type_is_simd_vector" => matches!(base, Type::Vector { .. }),
                                "type_is_dynamic_array" => matches!(base, Type::Dynamic(_)),
                                "type_is_pointer" | "type_is_multi_pointer" => {
                                    if matches!(base, Type::Pointer(_)) {
                                        self.multipointer(arg, 0)? == (name == "type_is_multi_pointer")
                                    } else {
                                        name == "type_is_pointer" && matches!(base, Type::Scalar{name,..} if name=="rawptr")
                                    }
                                }
                                "type_is_proc" => matches!(base, Type::Procedure(_) | Type::Function(..)),
                                "type_is_struct" => {
                                    match base {
                                        Type::Struct(owner) => self.sources[owner.file].node(owner.node).kind == "StructType",
                                        Type::Record(..) | Type::Anonymous(_) | Type::Set(_) | Type::Soa(_)
                                        | Type::SoaStorage(_) | Type::BigInteger | Type::BigRational => true,
                                        _ => false,
                                    }
                                }
                                "type_is_map" => matches!(base, Type::Map { .. }),
                                "type_is_union" => matches!(base, Type::Union(..)),
                                "type_is_enum" => matches!(base, Type::Enum(_)),
                                "type_is_string" => matches!(base, Type::String { .. }),
                                "type_is_boolean" => matches!(base,Type::Scalar{name,..} if matches!(name.as_str(),"bool"|"b8"|"b16"|"b32"|"b64")),
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
            if self
                .namespace_imports(r.file, s.text(base))
                .iter()
                .any(|import| import == path)
            {
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

#[cfg(test)]
mod native_boolean_tests {
    use super::*;
    #[test]
    fn valgrind_support_uses_the_selected_native_target() {
        let text = "ODIN_VALGRIND_SUPPORT";
        let source = Source::from_frontend(
            "/native-target.thor".into(),
            text.into(),
            serde_json::json!({"nodes":[
                {"kind":"File","start":0,"end":text.len(),"parent":-1},
                {"kind":"Ident","start":0,"end":text.len(),"parent":0}
            ],"features":[],"edits":[],"selections":[]}),
            true,
        )
        .unwrap();
        let sources = [source];
        let reference = Ref {
            file: 0,
            node: NodeId(1),
        };
        for (target, expected) in [
            ("linux_amd64", true),
            ("darwin_amd64", true),
            ("windows_amd64", false),
            ("linux_arm64", false),
            ("darwin_arm64", false),
            ("freestanding_amd64", true),
            ("js_wasm32", false),
            ("wasi_wasm32", false),
        ] {
            let mut resolver = Resolver::new(&sources);
            resolver.flags = vec![format!("-target:{target}")];
            let condition = resolver.condition(reference);
            let contextual = resolver.context_condition(reference, &Substitutions::new());
            let constant = resolver.constant_value(reference, &Substitutions::new(), None);
            if condition != Some(expected)
                || contextual != Some(expected)
                || !matches!(constant,Some(Constant::Boolean(value)) if value==expected)
            {
                panic!("native Boolean disagrees with selected target {target}");
            }
        }
    }
}
