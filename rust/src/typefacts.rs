//! Contextual type facts for instantiated analyses. Final overload/type checking
//! stays with Odin; these facts preserve nominal record and scalar identity.
use super::*;
use std::collections::BTreeMap;
pub type Substitutions = BTreeMap<String, Type>;
impl Resolver<'_> {
    pub fn record_fields(&mut self, typ: &Type) -> Vec<(String, Type)> {
        let (owner, bindings) = match typ {
            Type::Anonymous(fields) => return fields.clone(),
            Type::Struct(owner) => (*owner, Substitutions::new()),
            Type::Record(owner, bindings) => (*owner, bindings.iter().cloned().collect()),
            Type::Nominal(_, base) | Type::Pointer(base) => return self.record_fields(base),
            _ => return vec![],
        };
        let s = &self.sources[owner.file];
        let Some(fields) = s.role(owner.node, "fields") else {
            return vec![];
        };
        let mut result = vec![];
        for field in &s.node(fields).children {
            if s.node(*field).kind != "Field" {
                continue;
            }
            let ty = s
                .role(*field, "type")
                .map(|node| {
                    self.specialized_type(
                        Ref {
                            file: owner.file,
                            node,
                        },
                        &bindings,
                    )
                })
                .unwrap_or(Type::Unknown);
            for name in s.group(*field, "names") {
                result.push((s.text(*name).into(), ty.clone()));
            }
        }
        result
    }
    pub fn member_type(&mut self, typ: &Type, name: &str) -> Type {
        match typ {
            Type::Nominal(_, base) | Type::Pointer(base) => self.member_type(base, name),
            Type::Array { elem, length }
                if !name.is_empty() && name.chars().all(|c| "xyzwrgba".contains(c)) =>
            {
                let positions = name
                    .chars()
                    .map(|c| "xyzw".find(c).or_else(|| "rgba".find(c)).unwrap_or(4))
                    .collect::<Vec<_>>();
                if length.is_some_and(|length| {
                    positions.iter().any(|position| *position as i128 >= length)
                }) {
                    return Type::Unknown;
                }
                if name.len() == 1 {
                    *elem.clone()
                } else {
                    Type::Array {
                        elem: elem.clone(),
                        length: Some(name.len() as i128),
                    }
                }
            }
            Type::Soa(elem) => Type::Slice(Box::new(self.member_type(elem, name))),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .record_fields(typ)
                .into_iter()
                .find(|(field, _)| field == name)
                .map(|(_, typ)| typ)
                .unwrap_or(Type::Unknown),
            Type::Enum(owner) => Type::Enum(*owner),
            _ => Type::Unknown,
        }
    }
    pub fn specialized_type(&mut self, r: Ref, bindings: &Substitutions) -> Type {
        self.specialize(r, bindings, &mut HashSet::new())
    }
    fn specialize(&mut self, r: Ref, bindings: &Substitutions, seen: &mut HashSet<Ref>) -> Type {
        if !seen.insert(r) {
            return Type::Unknown;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let child = |role: &str| s.role(r.node, role).map(|node| Ref { file: r.file, node });
        let text = if n.kind == "PolyType" {
            s.names.get(n.name)
        } else {
            s.text(r.node).trim_start_matches('$')
        };
        if matches!(n.kind.as_str(), "Ident" | "PolyType") {
            if let Some(typ) = bindings.get(text) {
                return typ.clone();
            }
            if let Some(symbol) = self.symbol(r) {
                let symbol = self.symbols[symbol].clone();
                if let Some(node) = symbol.annotation.or(symbol.value) {
                    return self.specialize(
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
        let mut subtype = |role: &str| {
            child(role)
                .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                .unwrap_or(Type::Unknown)
        };
        match n.kind.as_str() {
            "Paren" => subtype("expr"),
            "WhenExpr" => {
                if let Some(cond) = child("cond").and_then(|r| self.condition(r)) {
                    return child(if cond { "x" } else { "y" })
                        .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                        .unwrap_or(Type::Unknown);
                }
                let a = child("x")
                    .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                    .unwrap_or(Type::Unknown);
                let b = child("y")
                    .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                    .unwrap_or(Type::Unknown);
                if a == b {
                    a
                } else {
                    self.type_of(r)
                }
            }
            "Compound" if child("type").is_some() => subtype("type"),
            "Selector" => {
                if let Some(primitive) = self.primitive(r) {
                    return primitive;
                }
                if let Some(symbol) = self.symbol(r) {
                    let symbol = self.symbols[symbol].clone();
                    if let Some(node) = symbol.annotation.or(symbol.value) {
                        return self.specialize(
                            Ref {
                                file: symbol.reference.file,
                                node,
                            },
                            bindings,
                            seen,
                        );
                    }
                }
                let typ = child("expr")
                    .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                    .unwrap_or(Type::Unknown);
                let name = child("field")
                    .map(|r| self.sources[r.file].text(r.node))
                    .unwrap_or("");
                self.member_type(&typ, name)
            }
            "Index" | "Deref" => subtype("expr").element().cloned().unwrap_or(Type::Unknown),
            "ArrayType" => {
                let elem = Box::new(subtype("elem"));
                if let Some(len) = child("len") {
                    Type::Array {
                        elem,
                        length: self.context_integer(len, bindings),
                    }
                } else if n.op == "soa" {
                    Type::Soa(elem)
                } else {
                    Type::Slice(elem)
                }
            }
            "DynamicArrayType" => {
                let elem = Box::new(subtype("elem"));
                if let Some(capacity) = child("capacity") {
                    Type::Fixed {
                        elem,
                        length: self.context_integer(capacity, bindings),
                    }
                } else {
                    Type::Dynamic(elem)
                }
            }
            "PointerType" | "MultiPointerType" => Type::Pointer(Box::new(subtype("elem"))),
            "Ellipsis" => Type::Slice(Box::new(subtype("expr"))),
            "MapType" => {
                let key = Box::new(subtype("key"));
                let value = Box::new(subtype("value"));
                Type::Map { key, value }
            }
            "SetType" => Type::Set(Box::new(
                s.group(r.node, "args")
                    .first()
                    .copied()
                    .map(|node| {
                        self.specialize(Ref { file: r.file, node }, bindings, &mut seen.clone())
                    })
                    .unwrap_or(Type::Unknown),
            )),
            "DistinctType" => Type::Nominal(r, Box::new(subtype("type"))),
            "StructType" => {
                let params = s.role(r.node, "params");
                let names = params
                    .into_iter()
                    .flat_map(|params| s.node(params).children.iter())
                    .flat_map(|field| s.group(*field, "names"))
                    .map(|name| s.text(*name).trim_start_matches('$').to_string())
                    .collect::<Vec<_>>();
                if names.is_empty() {
                    Type::Struct(r)
                } else {
                    Type::Record(
                        r,
                        names
                            .into_iter()
                            .map(|name| {
                                let typ = bindings.get(&name).cloned().unwrap_or(Type::Generic);
                                (name, typ)
                            })
                            .collect(),
                    )
                }
            }
            "Call" => {
                let Some(callee) = child("callee") else {
                    return Type::Unknown;
                };
                let base = self.specialize(callee, bindings, &mut seen.clone());
                let owner = match &base {
                    Type::Struct(owner) | Type::Record(owner, _) => Some(*owner),
                    _ => None,
                };
                if let Some(owner) = owner {
                    let params = self.fields(owner, "params");
                    if !params.is_empty() {
                        let args = s.group(r.node, "args");
                        return Type::Record(
                            owner,
                            params
                                .into_iter()
                                .enumerate()
                                .map(|(i, (name, annotation))| {
                                    let argument = args
                                        .iter()
                                        .copied()
                                        .find(|arg| {
                                            s.node(*arg).kind == "FieldValue"
                                                && s.role(*arg, "field")
                                                    .is_some_and(|field| s.text(field) == name)
                                        })
                                        .or_else(|| args.get(i).copied());
                                    let typ = argument
                                        .map(|node| {
                                            let node = s.role(node, "value").unwrap_or(node);
                                            let r = Ref { file: r.file, node };
                                            let type_param = annotation.is_some_and(|a| {
                                                self.sources[a.file].node(a.node).kind
                                                    == "TypeidType"
                                            });
                                            if type_param {
                                                self.specialize(r, bindings, &mut seen.clone())
                                            } else {
                                                let expected = annotation.map(|a| {
                                                    self.specialize(a, bindings, &mut seen.clone())
                                                });
                                                self.constant_value(r, bindings, expected.as_ref())
                                                    .map(Type::Constant)
                                                    .unwrap_or(Type::Unknown)
                                            }
                                        })
                                        .unwrap_or(Type::Unknown);
                                    (name, typ)
                                })
                                .collect(),
                        );
                    }
                }
                if !matches!(base, Type::Procedure(_) | Type::Unknown | Type::Generic) {
                    base
                } else {
                    self.type_of(r)
                }
            }
            "Binary" if n.op == "/" => subtype("right"),
            _ => self.type_of(r),
        }
    }
    pub fn parameter_bindings(&mut self, signature: Ref, args: &[Type]) -> Substitutions {
        let mut bindings = Substitutions::new();
        for ((name, annotation), actual) in self.fields(signature, "params").into_iter().zip(args) {
            if let Some(annotation) = annotation {
                let s = &self.sources[annotation.file];
                if s.node(annotation.node).kind == "TypeidType" {
                    bindings.insert(name.trim_start_matches('$').into(), actual.clone());
                }
                self.bind_shape(annotation, actual, &mut bindings, &mut HashSet::new());
            }
        }
        bindings
    }
    pub fn shape_matches(
        &mut self,
        annotation: Ref,
        actual: &Type,
        bindings: &Substitutions,
    ) -> bool {
        if actual.is_unknown() {
            return true;
        }
        let s = &self.sources[annotation.file];
        let n = s.node(annotation.node);
        let actual = if let Type::Nominal(_, base) = actual {
            base.as_ref()
        } else {
            actual
        };
        match n.kind.as_str() {
            "PolyType" => s
                .role(annotation.node, "specialization")
                .is_none_or(|node| {
                    self.shape_matches(
                        Ref {
                            file: annotation.file,
                            node,
                        },
                        actual,
                        bindings,
                    )
                }),
            "ArrayType" => {
                if s.role(annotation.node, "len").is_some() {
                    matches!(actual, Type::Array { .. })
                } else {
                    matches!(actual, Type::Slice(_) | Type::Soa(_))
                }
            }
            "DynamicArrayType" => {
                if s.role(annotation.node, "capacity").is_some() {
                    matches!(actual, Type::Fixed { .. })
                } else {
                    matches!(actual, Type::Dynamic(_))
                }
            }
            "PointerType" | "MultiPointerType" => matches!(actual, Type::Pointer(_)),
            "MapType" => matches!(actual, Type::Map { .. }),
            "Ellipsis" => true,
            "ProcedureType" => matches!(actual, Type::Procedure(_)),
            _ => {
                let formal = self.specialized_type(annotation, bindings);
                if formal.is_unknown() {
                    return true;
                }
                match (formal, actual) {
                    (
                        Type::Scalar { name, .. },
                        Type::Scalar {
                            name: other,
                            untyped,
                        },
                    ) => name == *other || *untyped || name == "any",
                    (
                        Type::String { name, .. },
                        Type::String {
                            name: other,
                            untyped,
                        },
                    ) => name == *other || *untyped,
                    (Type::Struct(a), Type::Struct(b)) => a == *b,
                    (Type::Record(a, _), Type::Record(b, _)) => a == *b,
                    _ => true,
                }
            }
        }
    }
    fn bind_shape(
        &self,
        annotation: Ref,
        actual: &Type,
        bindings: &mut Substitutions,
        seen: &mut HashSet<Ref>,
    ) {
        if !seen.insert(annotation) {
            return;
        }
        let s = &self.sources[annotation.file];
        let n = s.node(annotation.node);
        let child = |role: &str| {
            s.role(annotation.node, role).map(|node| Ref {
                file: annotation.file,
                node,
            })
        };
        match n.kind.as_str() {
            "PolyType" => {
                bindings.insert(s.names.get(n.name).into(), actual.clone());
                if let Some(specialization) = child("specialization") {
                    self.bind_shape(specialization, actual, bindings, seen);
                }
            }
            "Binary" if n.op == "/" => {
                for role in ["left", "right"] {
                    if let Some(child) = child(role) {
                        self.bind_shape(child, actual, bindings, &mut seen.clone());
                    }
                }
            }
            "ArrayType" | "DynamicArrayType" | "PointerType" | "MultiPointerType" | "Ellipsis" => {
                if let Type::Array {
                    length: Some(length),
                    ..
                }
                | Type::Fixed {
                    length: Some(length),
                    ..
                } = actual
                {
                    if let Some(count) = child(if n.kind == "ArrayType" {
                        "len"
                    } else {
                        "capacity"
                    }) {
                        if s.node(count.node).kind == "PolyType" {
                            bindings.insert(
                                s.names.get(s.node(count.node).name).into(),
                                Type::Constant(Constant::Integer(*length)),
                            );
                        }
                    }
                }
                if let (Some(elem), Some(actual)) = (
                    child(if n.kind == "Ellipsis" { "expr" } else { "elem" }),
                    actual.element(),
                ) {
                    self.bind_shape(elem, actual, bindings, seen);
                }
            }
            "MapType" => {
                if let Type::Map { key, value } = actual {
                    if let Some(k) = child("key") {
                        self.bind_shape(k, key, bindings, &mut seen.clone());
                    }
                    if let Some(v) = child("value") {
                        self.bind_shape(v, value, bindings, seen);
                    }
                }
            }
            "Call" => {
                if let Type::Record(_, arguments) = actual {
                    for (arg, (_, typ)) in s.group(annotation.node, "args").iter().zip(arguments) {
                        self.bind_shape(
                            Ref {
                                file: annotation.file,
                                node: *arg,
                            },
                            typ,
                            bindings,
                            &mut seen.clone(),
                        );
                    }
                }
            }
            _ => {}
        }
    }
    pub fn candidates(&mut self, callee: Ref) -> Vec<Ref> {
        self.candidate_inner(callee, &mut HashSet::new())
    }
    fn candidate_inner(&mut self, callee: Ref, seen: &mut HashSet<Ref>) -> Vec<Ref> {
        if !seen.insert(callee) {
            return vec![];
        }
        let s = &self.sources[callee.file];
        let n = s.node(callee.node);
        if n.kind == "ProcedureType" {
            return vec![callee];
        }
        if n.kind == "Procedure" {
            return vec![callee];
        }
        if n.kind == "Paren" {
            return s
                .role(callee.node, "expr")
                .map(|node| {
                    self.candidate_inner(
                        Ref {
                            file: callee.file,
                            node,
                        },
                        seen,
                    )
                })
                .unwrap_or_default();
        }
        if n.kind == "ProcedureGroup" {
            let mut result = vec![];
            for child in &n.children {
                result.extend(self.candidate_inner(
                    Ref {
                        file: callee.file,
                        node: *child,
                    },
                    &mut seen.clone(),
                ));
            }
            result.sort_by_key(|r| (r.file, r.node));
            result.dedup();
            return result;
        }
        if let Some(symbol) = self.symbol(callee) {
            let symbol = self.symbols[symbol].clone();
            if let Some(node) = symbol.annotation.or(symbol.value) {
                return self.candidate_inner(
                    Ref {
                        file: symbol.reference.file,
                        node,
                    },
                    seen,
                );
            }
        }
        vec![]
    }
}
