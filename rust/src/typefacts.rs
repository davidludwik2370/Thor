//! Contextual type facts for instantiated analyses. Final overload/type checking
//! stays with Odin; these facts preserve nominal record and scalar identity.
use super::*;
use std::collections::BTreeMap;
pub type Substitutions = BTreeMap<String, Type>;
impl Resolver<'_> {
    pub fn union_variants(&mut self, owner: Ref) -> Vec<Type> {
        self.sources[owner.file]
            .group(owner.node, "variants")
            .to_vec()
            .into_iter()
            .map(|node| {
                self.specialized_type(
                    Ref {
                        file: owner.file,
                        node,
                    },
                    &Substitutions::new(),
                )
            })
            .collect()
    }
    pub fn record_fields(&mut self, typ: &Type) -> Vec<(String, Type)> {
        self.shared_record_fields(typ).to_vec()
    }
    pub(crate) fn shared_record_fields(&mut self, typ: &Type) -> RecordFields {
        if let Some(fields) = self.metadata.as_ref().and_then(|memo| memo.fields.get(typ)) {
            crate::profile::count("resolver.field_hits", 1);
            return fields.clone();
        }
        let stable = self.active.is_empty() && self.planning.is_empty();
        let fields: RecordFields = self.record_fields_uncached(typ).into();
        if stable && !self.has_demanded_packages() {
            if let Some(memo) = &mut self.metadata { memo.fields.insert(typ.clone(), fields.clone()); }
        }
        fields
    }
    fn record_fields_uncached(&mut self, typ: &Type) -> Vec<(String, Type)> {
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
        if let Some(member) = self.metadata.as_ref().and_then(|memo| memo.members.get(typ).and_then(|members| members.get(name))) {
            crate::profile::count("resolver.member_hits", 1);
            return member.clone();
        }
        let stable = self.active.is_empty() && self.planning.is_empty();
        let member = self.member_type_uncached(typ, name);
        if stable && !self.has_demanded_packages() {
            if let Some(memo) = &mut self.metadata { memo.members.entry(typ.clone()).or_default().insert(name.to_owned(), member.clone()); }
        }
        member
    }
    fn member_type_uncached(&mut self, typ: &Type, name: &str) -> Type {
        match typ {
            Type::BigRational if matches!(name,"numerator"|"denominator") => Type::BigInteger,
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
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                let Some(path) = self.member_path(typ, name) else {
                    return Type::Unknown;
                };
                let mut typ = typ.clone();
                for component in path {
                    typ = self
                        .shared_record_fields(&typ)
                        .iter()
                        .find(|(field, _)| field == &component)
                        .map(|(_, typ)| typ.clone())
                        .unwrap_or(Type::Unknown);
                }
                typ
            }
            Type::Enum(owner) => Type::Enum(*owner),
            _ => Type::Unknown,
        }
    }
    /// Retain the actual storage path through promoted `using` fields.
    pub fn member_path(&mut self, typ: &Type, name: &str) -> Option<Vec<String>> {
        if let Some(path) = self.metadata.as_ref().and_then(|memo| memo.paths.get(typ).and_then(|paths| paths.get(name))) {
            crate::profile::count("resolver.path_hits", 1);
            return path.clone();
        }
        let stable = self.active.is_empty() && self.planning.is_empty();
        let path = self.member_path_seen(typ, name, &mut HashSet::new());
        if stable && !self.has_demanded_packages() {
            if let Some(memo) = &mut self.metadata { memo.paths.entry(typ.clone()).or_default().insert(name.to_owned(), path.clone()); }
        }
        path
    }
    fn member_path_seen(
        &mut self,
        typ: &Type,
        name: &str,
        seen: &mut HashSet<Ref>,
    ) -> Option<Vec<String>> {
        let owner = match typ {
            Type::Nominal(_, base) | Type::Pointer(base) => {
                return self.member_path_seen(base, name, seen)
            }
            Type::Anonymous(fields) => {
                return fields
                    .iter()
                    .any(|(field, _)| field == name)
                    .then(|| vec![name.into()])
            }
            Type::Struct(owner) | Type::Record(owner, _) => *owner,
            _ => return None,
        };
        if !seen.insert(owner) {
            return None;
        }
        let fields = self.shared_record_fields(typ);
        if fields.iter().any(|(field, _)| field == name) {
            return Some(vec![name.into()]);
        }
        let s = &self.sources[owner.file];
        let block = s.role(owner.node, "fields")?;
        let promoted = s
            .node(block)
            .children
            .iter()
            .filter(|field| s.node(**field).data["using_field"] == true)
            .flat_map(|field| s.group(*field, "names"))
            .map(|name| s.text(*name).to_string())
            .collect::<HashSet<_>>();
        let mut found = None;
        for (field, typ) in fields.iter() {
            if !promoted.contains(field) {
                continue;
            }
            if let Some(mut path) = self.member_path_seen(typ, name, &mut seen.clone()) {
                if found.is_some() {
                    return None;
                }
                path.insert(0, field.clone());
                found = Some(path);
            }
        }
        found
    }
    pub fn specialized_type(&mut self, r: Ref, bindings: &Substitutions) -> Type {
        if let Some(typ) = self.metadata.as_ref().and_then(|memo| memo.specialized.get(&r).and_then(|types| types.get(bindings))) {
            crate::profile::count("resolver.specialized_hits", 1);
            return typ.clone();
        }
        let stable = self.active.is_empty() && self.planning.is_empty();
        let typ = self.specialize(r, bindings, &mut HashSet::new());
        if stable && !self.has_demanded_packages() {
            if let Some(memo) = &mut self.metadata { memo.specialized.entry(r).or_default().insert(bindings.clone(), typ.clone()); }
        }
        typ
    }
    pub fn bound_type<'b>(&'b self, r: Ref, bindings: &'b Substitutions) -> Option<&'b Type> {
        if bindings.is_empty() && self.bound_types.is_empty() && self.bound_constants.is_empty() {
            return None;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let name = if n.kind == "PolyType" {
            s.names.get(n.name)
        } else {
            s.text(r.node).trim_start_matches('$')
        };
        if let Some(owner) = s
            .ancestors(r.node)
            .find(|node| s.node(*node).kind == "EnumType")
        {
            if let Some(typ) = bindings.get(&Self::enum_binding_key(
                Ref {
                    file: r.file,
                    node: owner,
                },
                name,
            )) {
                return Some(typ);
            }
        }
        let Some(index) = self.symbol(r) else {
            return bindings.get(name).filter(|_| n.kind == "PolyType");
        };
        let symbol = &self.symbols[index];
        let owner = &self.sources[symbol.reference.file];
        if owner.node(symbol.name_node).kind == "PolyType"
            || symbol.parameter && owner.node(symbol.scope).kind == "StructType"
        {
            if let Some(typ) = bindings.get(name) {
                return Some(typ);
            }
        }
        let key = (symbol.reference, symbol.name.clone());
        self.bound_types
            .get(&key)
            .or_else(|| self.bound_constants.get(&key).map(|(typ, _)| typ))
    }
    pub(super) fn enum_binding_key(owner: Ref, name: &str) -> String {
        format!("#enum:{}:{}:{name}", owner.file, owner.node.0)
    }
    pub fn compiled_type_parameter(&self, annotation: Ref, name: &str) -> bool {
        let s = &self.sources[annotation.file];
        s.node(annotation.node).kind == "TypeidType"
            && s.node(annotation.node).parent.is_some_and(|field| {
                let record = s
                    .node(field)
                    .parent
                    .and_then(|fields| s.node(fields).parent)
                    .is_some_and(|owner| {
                        s.node(owner).kind == "StructType"
                            && s.role(owner, "params") == s.node(field).parent
                    });
                s.group(field, "names").iter().any(|id| {
                    let poly = s.node(*id).kind == "PolyType";
                    (poly || record)
                        && (if poly {
                            s.names.get(s.node(*id).name)
                        } else {
                            s.text(*id)
                        }) == name.trim_start_matches('$')
                })
            })
    }
    fn specialize(&mut self, r: Ref, bindings: &Substitutions, seen: &mut HashSet<Ref>) -> Type {
        if let Some(typ)=self.logical_constant_type(r){return typ;}
        if !seen.insert(r) {
            return Type::Unknown;
        }
        if let Some(typ) = self.logical_type(r) { return typ; }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let child = |role: &str| s.role(r.node, role).map(|node| Ref { file: r.file, node });
        if matches!(n.kind.as_str(), "Ident" | "PolyType") {
            if let Some(typ) = self.bound_type(r, bindings).cloned() {
                if let Type::Constant(value) = typ {
                    if let Some(annotation) = self.symbol(r).and_then(|index| {
                        let symbol = &self.symbols[index];
                        symbol.annotation.map(|node| Ref {
                            file: symbol.reference.file,
                            node,
                        })
                    }) {
                        return self.specialize(annotation, bindings, seen);
                    }
                    return match value {
                        Constant::Integer(_) => Type::scalar("int"),
                        Constant::Boolean(_) => Type::scalar("bool"),
                        Constant::Text(_) => Type::String {
                            name: "string".into(),
                            untyped: false,
                        },
                        Constant::Enum(owner, _) => Type::Enum(owner),
                    };
                }
                return typ;
            }
            if n.kind == "PolyType" {
                return child("specialization")
                    .map(|target| self.specialize(target, bindings, seen))
                    .unwrap_or(Type::Generic);
            }
            if let Some(symbol) = self.symbol(r) {
                let symbol = self.symbols[symbol].clone();
                if let Some(projection) = symbol.projection {
                    return self.projected_type(&projection, bindings);
                }
                if self.sources[symbol.reference.file]
                    .node(symbol.reference.node)
                    .kind
                    == "PolyType"
                    || symbol.annotation.is_some_and(|annotation| {
                        self.compiled_type_parameter(
                            Ref {
                                file: symbol.reference.file,
                                node: annotation,
                            },
                            &symbol.name,
                        )
                    })
                {
                    return Type::Generic;
                }
                if symbol.annotation.is_none() {
                    let source = &self.sources[symbol.reference.file];
                    if source.group(symbol.reference.node, "names").len() > 1
                        && source.group(symbol.reference.node, "values").len() == 1
                    {
                        if let Some(node) = symbol.value {
                            let typ = self.specialized_value_result(
                                Ref {
                                    file: symbol.reference.file,
                                    node,
                                },
                                symbol.index,
                                bindings,
                                seen,
                            );
                            return if symbol.constant {
                                typ
                            } else {
                                typ.defaulted()
                            };
                        }
                    }
                }
                if let Some(node) = symbol.annotation.or(symbol.value) {
                    let typ = self.specialize(
                        Ref {
                            file: symbol.reference.file,
                            node,
                        },
                        bindings,
                        seen,
                    );
                    return if symbol.annotation.is_some() || symbol.constant { typ } else { typ.defaulted() };
                }
            }
        }
        let mut subtype = |role: &str| {
            child(role)
                .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                .unwrap_or(Type::Unknown)
        };
        match n.kind.as_str() {
            "Paren" | "Run" => subtype("expr"),
            "Binary" if n.op == "++" => {
                crate::lower::concatenation::result_type(s, r.node, |node| {
                    self.specialize(Ref { node, ..r }, bindings, &mut seen.clone())
                })
            }
            "WhenExpr" => {
                if let Some(cond) = child("cond").and_then(|r| self.context_condition(r, bindings))
                {
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
                let captures = s
                    .role(r.node, "fields")
                    .into_iter()
                    .flat_map(|fields| s.descendants(fields))
                    .filter(|node| s.node(*node).kind == "Ident")
                    .filter_map(|node| {
                        let name = s.text(node);
                        if names.iter().any(|parameter| parameter == name) {
                            return None;
                        }
                        let reference = Ref { file: r.file, node };
                        let typ = self
                            .constant_value(reference, bindings, None)
                            .map(Type::Constant)
                            .or_else(|| self.bound_type(reference, bindings).cloned())?;
                        let symbol = &self.symbols[self.symbol(reference)?];
                        let owner = &self.sources[symbol.reference.file];
                        (owner.node(symbol.name_node).kind == "PolyType"
                            && (symbol.reference.file != r.file
                                || !s
                                    .node(r.node)
                                    .span
                                    .contains(owner.node(symbol.reference.node).span)))
                        .then(|| (name.to_string(), typ.clone()))
                    })
                    .collect::<BTreeMap<_, _>>();
                if names.is_empty() && captures.is_empty() {
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
                            .chain(captures)
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
                        let mut arguments: Vec<_> = params
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
                                            self.sources[a.file].node(a.node).kind == "TypeidType"
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
                            .collect();
                        if let Type::Record(_, captured) = &base {
                            for (name, typ) in captured {
                                if !arguments.iter().any(|(parameter, _)| parameter == name) {
                                    arguments.push((name.clone(), typ.clone()));
                                }
                            }
                        }
                        return self.record_instance(owner, arguments);
                    }
                }
                if !matches!(base, Type::Procedure(_) | Type::Unknown | Type::Generic) {
                    base
                } else {
                    let typ = self.specialized_result(r, 0, bindings);
                    if typ.is_unknown() {
                        self.type_of(r)
                    } else {
                        typ
                    }
                }
            }
            "Binary" if n.op == "/" => subtype("right"),
            _ => self.type_of(r),
        }
    }
    /// Select a value from a multi-result expression without losing generic
    /// substitutions. Assignment slots are not all the first result's type.
    pub(super) fn specialized_value_result(
        &mut self,
        r: Ref,
        index: usize,
        bindings: &Substitutions,
        seen: &mut HashSet<Ref>,
    ) -> Type {
        if !seen.insert(r) {
            return Type::Unknown;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let child = |role: &str| s.role(r.node, role).map(|node| Ref { file: r.file, node });
        match n.kind.as_str() {
            "Paren" | "Run" | "OrPanic" | "OrReturn" | "OrBranch" => child("expr")
                .map(|r| self.specialized_value_result(r, index, bindings, seen))
                .unwrap_or(Type::Unknown),
            "OrElse" => child("x")
                .map(|r| self.specialized_value_result(r, index, bindings, seen))
                .unwrap_or(Type::Unknown),
            "SwitchResults" => s
                .group(r.node, "args")
                .get(index)
                .copied()
                .map(|node| self.specialized_type(Ref { file: r.file, node }, bindings))
                .unwrap_or(Type::Unknown),
            "SwitchExpr" | "CompileSwitchExpr" => {
                if n.kind == "CompileSwitchExpr" {
                    if let Some(arm) = self.selected_compile_switch_value(r, bindings) {
                        return self.specialized_value_result(arm, index, bindings, seen);
                    }
                }
                let arms = n.data["cases"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|case| {
                        crate::ast::Span::from_wire(&case["result"])
                            .and_then(|span| s.at(span, r.node))
                    })
                    .collect::<Vec<_>>();
                for node in arms {
                    let typ = self.specialized_value_result(
                        Ref { file: r.file, node },
                        index,
                        bindings,
                        &mut seen.clone(),
                    );
                    if !typ.is_unknown() {
                        return typ;
                    }
                }
                Type::Unknown
            }
            "Call" => {
                if let Some(typ) = self.logical_result(r,index) { return typ; }
                let typ = self.specialized_result(r, index, bindings);
                if !typ.is_unknown() {
                    return typ;
                }
                if let Some(signature) = child("callee").and_then(|r| self.signature(r)) {
                    if let Some((_, Some(annotation))) =
                        self.fields(signature, "results").get(index)
                    {
                        return self.specialized_type(*annotation, bindings);
                    }
                }
                if index == 0 {
                    self.type_of(r)
                } else {
                    Type::Unknown
                }
            }
            "Index" | "TypeAssertion" if index == 1 => Type::scalar("bool"),
            _ if index == 0 => self.specialized_type(r, bindings),
            _ => Type::Unknown,
        }
    }
    fn selected_compile_switch_value(&mut self, r: Ref, bindings: &Substitutions) -> Option<Ref> {
        let source = &self.sources[r.file];
        let cases = source.node(r.node).data["cases"].as_array()?;
        let reference = |span: &serde_json::Value| {
            crate::ast::Span::from_wire(span)
                .and_then(|span| source.at(span, r.node))
                .map(|node| Ref { node, ..r })
        };
        let selector = if let Some(node) = source.role(r.node, "selector") {
            let selector = Ref { node, ..r };
            let typ = self.specialized_type(selector, bindings);
            let value = self.constant_value(selector, bindings, Some(&typ))?;
            Some((value, typ))
        } else {
            None
        };
        let mut default = None;
        for case in cases {
            let operands = case["matches"].as_array()?;
            if operands.is_empty() {
                default = reference(&case["result"]);
                continue;
            }
            for operand in operands {
                let operand = reference(operand)?;
                let matches = if let Some((value, typ)) = &selector {
                    let ast = source.node(operand.node);
                    if ast.kind == "Binary" && matches!(ast.op.as_str(), ".." | "..<" | "..=") {
                        let integer = |role| {
                            let node = source.role(operand.node, role)?;
                            self.context_integer(Ref { node, ..r }, bindings)
                        };
                        let (low, high) = (integer("left")?, integer("right")?);
                        let Constant::Integer(value) = *value else { return None; };
                        value >= low && (value < high || value == high && ast.op == "..=")
                    } else {
                        self.constant_value(operand, bindings, Some(typ)).as_ref()? == value
                    }
                } else {
                    self.context_condition(operand, bindings)?
                };
                if matches {
                    return reference(&case["result"]);
                }
            }
        }
        default
    }
    fn record_instance(&self, owner: Ref, arguments: Vec<(String, Type)>) -> Type {
        let s = &self.sources[owner.file];
        let expected = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("runtime/sets/sets.odin");
        let genuine = matches!((std::fs::canonicalize(&s.path),std::fs::canonicalize(expected)),(Ok(actual),Ok(expected)) if actual==expected);
        let named_set = s.node(owner.node).parent.is_some_and(|declaration| {
            s.group(declaration, "names")
                .iter()
                .any(|name| s.text(*name) == "Set")
        });
        if genuine && named_set {
            Type::Set(Box::new(
                arguments
                    .into_iter()
                    .find(|(name, _)| name == "T")
                    .map(|(_, typ)| typ)
                    .unwrap_or(Type::Unknown),
            ))
        } else {
            Type::Record(owner, arguments)
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
        self.shape_score(annotation, actual, bindings).is_some()
    }
    pub fn shape_score(
        &mut self,
        annotation: Ref,
        actual: &Type,
        bindings: &Substitutions,
    ) -> Option<u32> {
        let s = &self.sources[annotation.file];
        let n = s.node(annotation.node);
        if actual.is_unknown() {
            return Some(0);
        }
        if n.kind == "PolyType" {
            return if let Some(node) = s.role(annotation.node, "specialization") {
                let actual = if let Type::Nominal(_, base) = actual {
                    base.as_ref()
                } else {
                    actual
                };
                self.shape_score(
                    Ref {
                        file: annotation.file,
                        node,
                    },
                    actual,
                    bindings,
                )
            } else {
                let bound = bindings.get(s.names.get(n.name));
                if bound.is_some_and(|bound| Self::type_score(bound, actual).is_none()) {
                    None
                } else {
                    Some(1)
                }
            };
        }
        let child = |name| {
            s.role(annotation.node, name).map(|node| Ref {
                file: annotation.file,
                node,
            })
        };
        if matches!(
            n.kind.as_str(),
            "ArrayType" | "DynamicArrayType" | "PointerType" | "MultiPointerType" | "Ellipsis"
        ) {
            let expected_kind = n.kind.as_str();
            let elem = match (expected_kind, actual) {
                ("ArrayType", Type::Array { elem, length }) if child("len").is_some() => {
                    let count = child("len").and_then(|r| self.context_integer(r, bindings));
                    if count.is_some() && length.is_some() && count != *length {
                        return None;
                    }
                    elem.as_ref()
                }
                ("ArrayType", Type::Slice(elem)) if child("len").is_none() && n.op != "soa" => {
                    elem.as_ref()
                }
                ("ArrayType", Type::Soa(elem)) if n.op == "soa" => elem.as_ref(),
                ("DynamicArrayType", Type::Dynamic(elem)) if child("capacity").is_none() => {
                    elem.as_ref()
                }
                ("DynamicArrayType", Type::Fixed { elem, length })
                    if child("capacity").is_some() =>
                {
                    let count = child("capacity").and_then(|r| self.context_integer(r, bindings));
                    if count.is_some() && length.is_some() && count != *length {
                        return None;
                    }
                    elem.as_ref()
                }
                ("PointerType" | "MultiPointerType", Type::Pointer(elem))
                | ("Ellipsis", Type::Slice(elem)) => elem.as_ref(),
                _ => return None,
            };
            return child(if expected_kind == "Ellipsis" {
                "expr"
            } else {
                "elem"
            })
            .and_then(|r| self.shape_score(r, elem, bindings))
            .map(|score| score + 3);
        }
        if n.kind == "Binary" && n.op == "/" {
            return s.role(annotation.node, "right").and_then(|node| {
                self.shape_score(
                    Ref {
                        file: annotation.file,
                        node,
                    },
                    actual,
                    bindings,
                )
            });
        }
        let expected = self.specialized_type(annotation, bindings);
        Self::type_score(&expected, actual)
    }
    fn type_score(expected: &Type, actual: &Type) -> Option<u32> {
        if expected.is_unknown() || actual.is_unknown() {
            return Some(0);
        }
        let canonical = |name: &str| match name {
            "byte" => "u8".to_string(),
            "rune" => "i32".to_string(),
            other => other.to_string(),
        };
        match (expected, actual) {
            (Type::Scalar { name, .. }, _) if name == "any" => Some(0),
            (
                Type::Scalar { name, .. },
                Type::Scalar {
                    name: other,
                    untyped,
                },
            ) => {
                if canonical(name) == canonical(other) {
                    Some(if *untyped { 2 } else { 5 })
                } else if *untyped && expected.numeric() && actual.numeric() {
                    Some(0)
                } else {
                    None
                }
            }
            (
                Type::String { name, .. },
                Type::String {
                    name: other,
                    untyped,
                },
            ) => {
                if name == other || *untyped {
                    Some(if *untyped { 2 } else { 5 })
                } else {
                    None
                }
            }
            (Type::Array { elem: a, length: n }, Type::Array { elem: b, length: m })
            | (Type::Fixed { elem: a, length: n }, Type::Fixed { elem: b, length: m }) => {
                if n.is_some() && m.is_some() && n != m {
                    return None;
                }
                Self::type_score(a, b).map(|score| score + 3)
            }
            (Type::Slice(a), Type::Slice(b))
            | (Type::Dynamic(a), Type::Dynamic(b))
            | (Type::Pointer(a), Type::Pointer(b))
            | (Type::Set(a), Type::Set(b))
            | (Type::Soa(a), Type::Soa(b)) => Self::type_score(a, b).map(|score| score + 3),
            (Type::Map { key: a, value: b }, Type::Map { key: c, value: d }) => {
                Some(Self::type_score(a, c)? + Self::type_score(b, d)? + 3)
            }
            (Type::Record(a, bindings), Type::Record(b, other)) if a == b => {
                let mut score = 5;
                for ((name, typ), (other_name, other_typ)) in bindings.iter().zip(other) {
                    if name != other_name {
                        return None;
                    }
                    score += Self::type_score(typ, other_typ)?;
                }
                Some(score)
            }
            (Type::Nominal(a, _), Type::Nominal(b, _)) if a == b => Some(5),
            (Type::Nominal(_, base), value) if value.untyped() => Self::type_score(base, value),
            (Type::Procedure(_), Type::Procedure(_)) => Some(1), // Odin checks complete calling signatures.
            (Type::Union(_), _) => Some(0), // Native union injection is checked by Odin.
            _ if expected == actual => Some(5),
            _ => None,
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
                let binding = bindings
                    .entry(s.names.get(n.name).into())
                    .or_insert(Type::Unknown);
                if binding.is_unknown() {
                    *binding = actual.clone();
                }
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
            "Ident" => {
                let name = s.text(annotation.node);
                let generic = self.symbol(annotation).is_some_and(|index| {
                    self.sources[self.symbols[index].reference.file]
                        .node(self.symbols[index].reference.node)
                        .kind
                        == "PolyType"
                });
                if generic {
                    let binding = bindings.entry(name.into()).or_insert(Type::Unknown);
                    // A typed argument can supply context for a preceding
                    // literal. Untyped defaults must not select an overload
                    // while the explicit polymorphic argument is unresolved.
                    if binding.is_unknown() && !actual.untyped() {
                        *binding = actual.clone();
                    }
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
                if let Type::Set(elem) = actual {
                    if let Some(arg) = s.group(annotation.node, "args").first() {
                        self.bind_shape(
                            Ref {
                                file: annotation.file,
                                node: *arg,
                            },
                            elem,
                            bindings,
                            &mut seen.clone(),
                        );
                    }
                }
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
    fn enclosing_when(&self, declaration: Ref) -> Option<NodeId> {
        let source = &self.sources[declaration.file];
        source
            .ancestors(declaration.node)
            .take_while(|node| !is_scope(source, *node))
            .filter(|node| source.node(*node).kind == "When")
            .last()
    }
    fn conditional_procedures(&self, symbol: &Symbol) -> Vec<Ref> {
        let Some(owner) = self.enclosing_when(symbol.reference) else {
            return vec![];
        };
        let file = symbol.reference.file;
        let source = &self.sources[file];
        self.scopes
            .get(&Ref { file, node: symbol.scope }).and_then(|names| names.get(&symbol.name))
            .into_iter()
            .flatten()
            .filter_map(|index| {
                let alternative = &self.symbols[*index];
                let mut value = alternative.value?;
                while source.node(value).kind == "Paren" {
                    value = source.role(value, "expr")?;
                }
                (source.node(value).kind == "Procedure"
                    && self.enclosing_when(alternative.reference) == Some(owner))
                    .then_some(Ref { file, node: value })
            })
            .collect()
    }
    pub(crate) fn checked_procedure_identity(&self, procedure: Ref) -> Ref {
        let source = &self.sources[procedure.file];
        let mut value = procedure.node;
        while let Some(parent) = source.node(value).parent {
            if source.node(parent).kind != "Paren" {
                break;
            }
            value = parent;
        }
        let Some(declaration) = source
            .node(value)
            .parent
            .filter(|parent| source.node(*parent).kind == "Declaration")
        else {
            return procedure;
        };
        let declaration = Ref { node: declaration, ..procedure };
        if self.enclosing_when(declaration).is_none() {
            return procedure;
        }
        let Some(symbol) = self
            .symbols
            .iter()
            .find(|symbol| symbol.reference == declaration && symbol.value == Some(value))
        else {
            return procedure;
        };
        self.conditional_procedures(symbol)
            .into_iter()
            .min()
            .unwrap_or(procedure)
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
            let conditional = self.conditional_procedures(&symbol);
            if conditional.len() > 1 {
                return conditional
                    .into_iter()
                    .filter(|candidate| self.active_definition(*candidate))
                    .collect();
            }
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
