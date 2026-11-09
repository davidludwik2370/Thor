//! Lexical symbols and type shapes used by native lowering. Odin retains final
//! authority over exact types, overloads, constant evaluation and assignability.
use crate::ast::{NodeId, Source};
use serde::Serialize;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
pub struct Ref {
    pub file: usize,
    pub node: NodeId,
}
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize)]
pub enum Type {
    Unknown,
    Generic,
    Scalar {
        name: String,
        untyped: bool,
    },
    String {
        name: String,
        untyped: bool,
    },
    Array {
        elem: Box<Type>,
        length: Option<i128>,
    },
    Slice(Box<Type>),
    Dynamic(Box<Type>),
    Pointer(Box<Type>),
    Map {
        key: Box<Type>,
        value: Box<Type>,
    },
    Set(Box<Type>),
    Struct(Ref),
    Enum(Ref),
    Union(Ref),
    Procedure(Ref),
}
impl Type {
    pub fn scalar(name: &str) -> Self {
        Self::Scalar {
            name: name.into(),
            untyped: false,
        }
    }
    pub fn is_unknown(&self) -> bool {
        matches!(self, Self::Unknown | Self::Generic)
    }
    pub fn element(&self) -> Option<&Type> {
        match self {
            Self::Array { elem, .. }
            | Self::Slice(elem)
            | Self::Dynamic(elem)
            | Self::Pointer(elem)
            | Self::Set(elem) => Some(elem),
            Self::Map { value, .. } => Some(value),
            _ => None,
        }
    }
    pub fn is_sequence(&self) -> bool {
        matches!(
            self,
            Self::Array { .. } | Self::Slice(_) | Self::Dynamic(_) | Self::String { .. }
        )
    }
    pub fn defaulted(self) -> Self {
        match self {
            Self::Scalar { name, .. } => Self::Scalar {
                name,
                untyped: false,
            },
            Self::String { name, .. } => Self::String {
                name,
                untyped: false,
            },
            other => other,
        }
    }
    pub fn untyped(&self) -> bool {
        matches!(
            self,
            Self::Scalar { untyped: true, .. } | Self::String { untyped: true, .. }
        )
    }
    pub fn numeric(&self) -> bool {
        match self {
            Self::Scalar { name, .. } => {
                name == "int"
                    || name == "uint"
                    || name == "uintptr"
                    || name == "rune"
                    || name.starts_with(['i', 'u', 'f'])
                        && name.chars().nth(1).is_some_and(|c| c.is_ascii_digit())
            }
            _ => false,
        }
    }
}
#[derive(Clone, Debug)]
pub struct Symbol {
    pub name: String,
    pub reference: Ref,
    pub name_node: NodeId,
    pub scope: NodeId,
    pub annotation: Option<NodeId>,
    pub value: Option<NodeId>,
    pub index: usize,
    pub immutable: bool,
    pub constant: bool,
    pub parameter: bool,
    pub result: bool,
}
pub struct Resolver<'a> {
    pub sources: &'a [Source],
    pub symbols: Vec<Symbol>,
    scopes: HashMap<(usize, NodeId, String), Vec<usize>>,
    types: HashMap<Ref, Type>,
    active: HashSet<Ref>,
    pub conditional_immutables: HashSet<Ref>,
}
pub fn is_scope(source: &Source, id: NodeId) -> bool {
    let node = source.node(id);
    if node.kind == "Block"
        && node.parent.is_some_and(|parent| {
            matches!(
                source.node(parent).kind.as_str(),
                "When" | "CompileSwitch" | "ForeignBlock"
            )
        })
    {
        return false;
    }
    matches!(
        node.kind.as_str(),
        "Block"
            | "Procedure"
            | "ProcedureType"
            | "File"
            | "StructType"
            | "For"
            | "If"
            | "Range"
            | "Switch"
            | "TypeSwitch"
            | "Case"
    )
}
pub fn scope(source: &Source, id: NodeId) -> NodeId {
    source
        .ancestors(id)
        .find(|id| is_scope(source, *id))
        .unwrap_or(NodeId(0))
}
impl<'a> Resolver<'a> {
    pub fn new(sources: &'a [Source]) -> Self {
        let mut this = Self {
            sources,
            symbols: vec![],
            scopes: HashMap::new(),
            types: HashMap::new(),
            active: HashSet::new(),
            conditional_immutables: HashSet::new(),
        };
        for (file, source) in sources.iter().enumerate() {
            if source.excluded {
                continue;
            }
            for (i, node) in source.nodes.iter().enumerate() {
                let id = NodeId(i);
                let mut owner = scope(source, id);
                let mut parameter = false;
                let mut result = false;
                let names = match node.kind.as_str() {
                    "Declaration" => source.group(id, "names").to_vec(),
                    "Field" => {
                        let signature = source.ancestors(id).find(|id| {
                            matches!(
                                source.node(*id).kind.as_str(),
                                "ProcedureType" | "StructType"
                            )
                        });
                        let Some(signature) =
                            signature.filter(|id| source.node(*id).kind == "ProcedureType")
                        else {
                            continue;
                        };
                        owner = source
                            .node(signature)
                            .parent
                            .filter(|id| source.node(*id).kind == "Procedure")
                            .unwrap_or(signature);
                        parameter = source.node(id).parent == source.role(signature, "params");
                        result = !parameter;
                        source.group(id, "names").to_vec()
                    }
                    "Range" | "TypeSwitch" => {
                        owner = id;
                        source.group(id, "names").to_vec()
                    }
                    "PolyType" => {
                        if let Some(prototype) = source
                            .ancestors(id)
                            .find(|id| source.node(*id).kind == "ProcedureType")
                        {
                            owner = source
                                .node(prototype)
                                .parent
                                .filter(|id| source.node(*id).kind == "Procedure")
                                .unwrap_or(prototype);
                        }
                        vec![id]
                    }
                    _ => continue,
                };
                let values = source.group(id, "values");
                for (index, name_node) in names.into_iter().enumerate() {
                    let name = source
                        .text(name_node)
                        .trim_start_matches(['&', '$'])
                        .to_string();
                    if name == "_" {
                        continue;
                    }
                    let value = values.get(index).copied().or_else(|| {
                        if values.len() == 1 {
                            Some(values[0])
                        } else {
                            None
                        }
                    });
                    let annotation = if node.kind == "Range" {
                        source.role(
                            id,
                            if index == 0 {
                                "value_type"
                            } else {
                                "index_type"
                            },
                        )
                    } else {
                        source.role(id, "type")
                    };
                    let symbol = Symbol {
                        name: name.clone(),
                        reference: Ref { file, node: id },
                        name_node,
                        scope: owner,
                        annotation,
                        value,
                        index,
                        immutable: node.immutable,
                        constant: node.kind == "Declaration" && !node.mutable,
                        parameter,
                        result,
                    };
                    this.scopes
                        .entry((file, owner, name))
                        .or_default()
                        .push(this.symbols.len());
                    this.symbols.push(symbol);
                }
            }
        }
        let mut groups: HashMap<_, Vec<usize>> = HashMap::new();
        for (index, symbol) in this.symbols.iter().enumerate() {
            let source = &sources[symbol.reference.file];
            let key = if symbol.scope.0 == 0 {
                (
                    source.path.parent().unwrap_or(&source.path).to_path_buf(),
                    None,
                    symbol.name.clone(),
                )
            } else {
                (source.path.clone(), Some(symbol.scope), symbol.name.clone())
            };
            groups.entry(key).or_default().push(index);
        }
        for ids in groups.values() {
            if ids.len() > 1
                && ids.iter().any(|id| this.symbols[*id].immutable)
                && ids.iter().any(|id| {
                    let s = &this.symbols[*id];
                    sources[s.reference.file]
                        .ancestors(s.reference.node)
                        .any(|a| {
                            matches!(
                                sources[s.reference.file].node(a).kind.as_str(),
                                "When" | "CompileSwitch"
                            )
                        })
                })
            {
                this.conditional_immutables
                    .extend(ids.iter().map(|id| this.symbols[*id].reference));
            }
        }
        this
    }
    pub fn lookup(&self, reference: Ref, name: &str) -> Option<usize> {
        let source = &self.sources[reference.file];
        for owner in std::iter::once(reference.node)
            .chain(source.ancestors(reference.node))
            .filter(|id| is_scope(source, *id))
        {
            if let Some(ids) = self.scopes.get(&(reference.file, owner, name.to_string())) {
                let own_decl = source.ancestors(reference.node).find(|id| {
                    source.node(*id).kind == "Declaration"
                        && source.node(*id).mutable
                        && !source.group(*id, "names").contains(&reference.node)
                });
                if let Some(id) = ids
                    .iter()
                    .find(|id| Some(self.symbols[**id].reference.node) != own_decl)
                {
                    return Some(*id);
                }
            }
        }
        for (file, other) in self.sources.iter().enumerate() {
            if other.path.parent() != source.path.parent() {
                continue;
            }
            if let Some(ids) = self.scopes.get(&(file, NodeId(0), name.to_string())) {
                return ids.first().copied();
            }
        }
        None
    }
    pub fn symbol(&self, reference: Ref) -> Option<usize> {
        let source = &self.sources[reference.file];
        match source.node(reference.node).kind.as_str() {
            "Ident" | "PolyType" => {
                let name = source.text(reference.node).trim_start_matches('$');
                self.lookup(reference, name).or_else(|| {
                    source
                        .selections
                        .iter()
                        .find(|s| s.names.iter().any(|item| item == name))
                        .and_then(|selection| crate::lower::unquote(&selection.path).ok())
                        .and_then(|path| self.package_symbol(source, &path, name))
                })
            }
            "Selector" => {
                let base = source.role(reference.node, "expr")?;
                if source.node(base).kind != "Ident"
                    || self
                        .lookup(
                            Ref {
                                file: reference.file,
                                node: base,
                            },
                            source.text(base),
                        )
                        .is_some()
                {
                    return None;
                }
                let field = source.role(reference.node, "field")?;
                source
                    .nodes
                    .iter()
                    .filter(|node| node.kind == "Import")
                    .find_map(|node| {
                        let path = crate::lower::unquote(&node.op).ok()?;
                        let explicit = source.names.get(node.name);
                        let name = if explicit.is_empty() {
                            path.rsplit(['/', ':']).next().unwrap_or("")
                        } else {
                            explicit
                        };
                        if name == source.text(base) {
                            self.package_symbol(source, &path, source.text(field))
                        } else {
                            None
                        }
                    })
            }
            "Paren" => self.symbol(Ref {
                file: reference.file,
                node: source.role(reference.node, "expr")?,
            }),
            _ => None,
        }
    }
    fn package_symbol(&self, source: &Source, path: &str, name: &str) -> Option<usize> {
        let directory = if let Some((collection, suffix)) = path.split_once(':') {
            source.collection_roots.get(collection)?.join(suffix)
        } else {
            source.path.parent()?.join(path)
        };
        let directory = std::fs::canonicalize(directory).ok()?;
        self.sources.iter().enumerate().find_map(|(file, unit)| {
            if unit.path.parent() != Some(directory.as_path()) {
                return None;
            }
            self.scopes
                .get(&(file, NodeId(0), name.to_string()))
                .and_then(|symbols| symbols.first())
                .copied()
        })
    }
    pub fn signature(&mut self, reference: Ref) -> Option<Ref> {
        match self.type_of(reference) {
            Type::Procedure(signature) => Some(signature),
            _ => None,
        }
    }
    pub fn native_builtin(&self, reference: Ref, name: &str) -> bool {
        let source = &self.sources[reference.file];
        source.node(reference.node).kind == "Ident"
            && source.text(reference.node) == name
            && self.symbol(reference).is_none()
            && !source
                .selections
                .iter()
                .any(|selection| selection.names.iter().any(|selected| selected == name))
            && !source
                .nodes
                .iter()
                .filter(|node| node.kind == "Import")
                .any(|node| {
                    let explicit = source.names.get(node.name);
                    explicit == name
                        || explicit.is_empty()
                            && crate::lower::unquote(&node.op)
                                .ok()
                                .is_some_and(|path| path.rsplit(['/', ':']).next() == Some(name))
                })
    }
    pub fn fields(&self, reference: Ref, role: &str) -> Vec<(String, Option<Ref>)> {
        let source = &self.sources[reference.file];
        let Some(fields) = source.role(reference.node, role) else {
            return vec![];
        };
        let mut result = vec![];
        for field in &source.node(fields).children {
            if source.node(*field).kind != "Field" {
                continue;
            }
            let annotation = source.role(*field, "type").map(|node| Ref {
                file: reference.file,
                node,
            });
            let names = source.group(*field, "names");
            if names.is_empty() {
                result.push((String::new(), annotation));
            }
            for name in names {
                result.push((source.text(*name).into(), annotation));
            }
        }
        result
    }
    pub fn type_of(&mut self, reference: Ref) -> Type {
        if let Some(typ) = self.types.get(&reference) {
            return typ.clone();
        }
        if !self.active.insert(reference) {
            return Type::Unknown;
        }
        let typ = self.infer(reference);
        self.active.remove(&reference);
        if !typ.is_unknown() {
            self.types.insert(reference, typ.clone());
        }
        typ
    }
    fn infer(&mut self, r: Ref) -> Type {
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let child = |name: &str| s.role(r.node, name).map(|node| Ref { file: r.file, node });
        match n.kind.as_str() {
            "Interpolation" => Type::String {
                name: "string".into(),
                untyped: false,
            },
            "Literal" => match n.op.as_str() {
                "string" => Type::String {
                    name: "string".into(),
                    untyped: true,
                },
                "float" => Type::Scalar {
                    name: "f64".into(),
                    untyped: true,
                },
                "rune" => Type::Scalar {
                    name: "rune".into(),
                    untyped: true,
                },
                _ => Type::Scalar {
                    name: "int".into(),
                    untyped: true,
                },
            },
            "Ident" => {
                if let Some(id) = self.symbol(r) {
                    let sym = self.symbols[id].clone();
                    if let Some(annotation) = sym.annotation {
                        return self.type_of(Ref {
                            file: sym.reference.file,
                            node: annotation,
                        });
                    }
                    if let Some(value) = sym.value {
                        let typ = self.type_of(Ref {
                            file: sym.reference.file,
                            node: value,
                        });
                        return if sym.constant { typ } else { typ.defaulted() };
                    }
                    if s.node(sym.reference.node).kind == "Range" {
                        if sym.index == 1 {
                            return Type::scalar("int");
                        }
                        if let Some(expr) = s.role(sym.reference.node, "expr") {
                            let t = self.type_of(Ref {
                                file: r.file,
                                node: expr,
                            });
                            if let Some(elem) = t.element() {
                                return elem.clone();
                            }
                            return t.defaulted();
                        }
                    }
                }
                let text = s.text(r.node);
                if text == "true" || text == "false" {
                    return Type::Scalar {
                        name: "bool".into(),
                        untyped: true,
                    };
                }
                if text == "string" || text == "cstring" {
                    return Type::String {
                        name: text.into(),
                        untyped: false,
                    };
                }
                if matches!(
                    text,
                    "bool"
                        | "b8"
                        | "b16"
                        | "b32"
                        | "b64"
                        | "int"
                        | "uint"
                        | "uintptr"
                        | "rune"
                        | "rawptr"
                        | "typeid"
                        | "any"
                        | "byte"
                ) || text.starts_with(['i', 'u', 'f'])
                    && text.chars().nth(1).is_some_and(|c| c.is_ascii_digit())
                {
                    return Type::scalar(text);
                }
                Type::Unknown
            }
            "Paren" | "AutoCast" | "Unary" => {
                let typ = child("expr")
                    .map(|r| self.type_of(r))
                    .unwrap_or(Type::Unknown);
                if n.kind == "Unary" && n.op == "&" {
                    Type::Pointer(Box::new(typ))
                } else {
                    typ
                }
            }
            "Cast" | "TypeAssertion" => child("type")
                .map(|r| self.type_of(r))
                .unwrap_or(Type::Unknown),
            "Deref" | "Index" => child("expr")
                .map(|r| self.type_of(r))
                .and_then(|t| t.element().cloned())
                .unwrap_or(Type::Unknown),
            "Slice" => {
                let typ = child("expr")
                    .map(|r| self.type_of(r))
                    .unwrap_or(Type::Unknown);
                match typ {
                    Type::String { .. } => typ,
                    _ => Type::Slice(Box::new(typ.element().cloned().unwrap_or(Type::Unknown))),
                }
            }
            "ArrayType" => {
                let elem = Box::new(
                    child("elem")
                        .map(|r| self.type_of(r))
                        .unwrap_or(Type::Unknown),
                );
                if let Some(len) = child("len") {
                    Type::Array {
                        elem,
                        length: self.constant_int(len),
                    }
                } else {
                    Type::Slice(elem)
                }
            }
            "DynamicArrayType" => Type::Dynamic(Box::new(
                child("elem")
                    .map(|r| self.type_of(r))
                    .unwrap_or(Type::Unknown),
            )),
            "PointerType" => Type::Pointer(Box::new(
                child("elem")
                    .map(|r| self.type_of(r))
                    .unwrap_or(Type::Unknown),
            )),
            "MapType" => Type::Map {
                key: Box::new(
                    child("key")
                        .map(|r| self.type_of(r))
                        .unwrap_or(Type::Unknown),
                ),
                value: Box::new(
                    child("value")
                        .map(|r| self.type_of(r))
                        .unwrap_or(Type::Unknown),
                ),
            },
            "StructType" => Type::Struct(r),
            "UnionType" => Type::Union(r),
            "EnumType" => Type::Enum(r),
            "PolyType" => Type::Generic,
            "ProcedureType" => Type::Procedure(r),
            "Procedure" => child("type").map(Type::Procedure).unwrap_or(Type::Unknown),
            "Compound" => {
                if let Some(typ) = child("type") {
                    return self.type_of(typ);
                }
                if s.node(r.node)
                    .spans
                    .get("open")
                    .is_some_and(|span| s.slice(*span) == "[")
                {
                    let elems = s.group(r.node, "elems");
                    let typ = elems
                        .first()
                        .map(|node| {
                            self.type_of(Ref {
                                file: r.file,
                                node: *node,
                            })
                            .defaulted()
                        })
                        .unwrap_or(Type::Unknown);
                    return Type::Array {
                        elem: Box::new(typ),
                        length: Some(elems.len() as i128),
                    };
                }
                Type::Unknown
            }
            "Binary" => {
                if matches!(
                    n.op.as_str(),
                    "in" | "not_in" | "==" | "!=" | "<" | ">" | "<=" | ">=" | "&&" | "||"
                ) {
                    return Type::scalar("bool");
                }
                let a = child("left")
                    .map(|r| self.type_of(r))
                    .unwrap_or(Type::Unknown);
                let b = child("right")
                    .map(|r| self.type_of(r))
                    .unwrap_or(Type::Unknown);
                if n.op == "*" && matches!(b, Type::String { .. }) {
                    return b;
                }
                if a.untyped() && !b.untyped() {
                    b
                } else {
                    a
                }
            }
            "Ternary" | "WhenExpr" | "OrElse" => {
                let a = child("x").map(|r| self.type_of(r)).unwrap_or(Type::Unknown);
                let b = child("y").map(|r| self.type_of(r)).unwrap_or(Type::Unknown);
                if a.is_unknown() || a.untyped() && !b.untyped() {
                    b
                } else {
                    a
                }
            }
            "FieldValue" => child("value")
                .map(|r| self.type_of(r))
                .unwrap_or(Type::Unknown),
            "Call" => {
                let Some(callee) = child("callee") else {
                    return Type::Unknown;
                };
                let name = s.text(callee.node);
                let builtin = self.native_builtin(callee, name);
                if builtin && matches!(name, "make" | "new" | "new_clone") {
                    if let Some(arg) = s.group(r.node, "args").first() {
                        let typ = self.type_of(Ref {
                            file: r.file,
                            node: *arg,
                        });
                        return if name == "make" {
                            typ
                        } else {
                            Type::Pointer(Box::new(typ))
                        };
                    }
                }
                if builtin && name == "type_of" {
                    if let Some(arg) = s.group(r.node, "args").first() {
                        return self.type_of(Ref {
                            file: r.file,
                            node: *arg,
                        });
                    }
                }
                if builtin && matches!(name, "len" | "cap" | "size_of" | "align_of" | "offset_of") {
                    return Type::scalar("int");
                }
                let callee_type = self.type_of(callee);
                match callee_type {
                    Type::Procedure(signature) => {
                        let fields = self.fields(signature, "results");
                        if fields.len() == 1 {
                            fields[0]
                                .1
                                .map(|r| self.type_of(r))
                                .unwrap_or(Type::Unknown)
                        } else {
                            Type::Unknown
                        }
                    }
                    Type::Unknown => Type::Unknown,
                    other => other,
                }
            }
            "Selector" => {
                if let Some(symbol) = self.symbol(r) {
                    let symbol = self.symbols[symbol].clone();
                    if let Some(node) = symbol.annotation.or(symbol.value) {
                        return self.type_of(Ref {
                            file: symbol.reference.file,
                            node,
                        });
                    }
                }
                let Some(base) = child("expr") else {
                    return Type::Unknown;
                };
                let field = child("field").map(|r| s.text(r.node)).unwrap_or("");
                let mut typ = self.type_of(base);
                if let Type::Pointer(elem) = typ {
                    typ = *elem;
                }
                match typ {
                    Type::Struct(owner) => {
                        let source = &self.sources[owner.file];
                        if let Some(fields) = source.role(owner.node, "fields") {
                            for item in &source.node(fields).children {
                                if source
                                    .group(*item, "names")
                                    .iter()
                                    .any(|name| source.text(*name) == field)
                                {
                                    return source
                                        .role(*item, "type")
                                        .map(|node| {
                                            self.type_of(Ref {
                                                file: owner.file,
                                                node,
                                            })
                                        })
                                        .unwrap_or(Type::Unknown);
                                }
                            }
                        }
                        Type::Unknown
                    }
                    Type::Enum(owner) => Type::Enum(owner),
                    _ => Type::Unknown,
                }
            }
            _ => Type::Unknown,
        }
    }
    pub fn constant_int(&self, r: Ref) -> Option<i128> {
        self.integer(r, &mut HashSet::new())
    }
    fn integer(&self, r: Ref, seen: &mut HashSet<Ref>) -> Option<i128> {
        if !seen.insert(r) {
            return None;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let child = |name: &str| s.role(r.node, name).map(|node| Ref { file: r.file, node });
        match n.kind.as_str() {
            "Literal" => {
                let text = s.text(r.node).replace('_', "");
                for (prefix, radix) in [("0x", 16), ("0b", 2), ("0o", 8), ("0d", 10)] {
                    if let Some(value) = text.strip_prefix(prefix) {
                        return i128::from_str_radix(value, radix).ok();
                    }
                }
                text.parse().ok()
            }
            "Paren" => self.integer(child("expr")?, seen),
            "Unary" => {
                let value = self.integer(child("expr")?, seen)?;
                match n.op.as_str() {
                    "-" => value.checked_neg(),
                    "+" => Some(value),
                    _ => None,
                }
            }
            "Ident" => {
                let sym = &self.symbols[self.symbol(r)?];
                if sym.constant {
                    self.integer(
                        Ref {
                            file: sym.reference.file,
                            node: sym.value?,
                        },
                        seen,
                    )
                } else {
                    None
                }
            }
            "Binary" => {
                let a = self.integer(child("left")?, &mut seen.clone())?;
                let b = self.integer(child("right")?, &mut seen.clone())?;
                match n.op.as_str() {
                    "+" => a.checked_add(b),
                    "-" => a.checked_sub(b),
                    "*" => a.checked_mul(b),
                    _ => None,
                }
            }
            _ => None,
        }
    }
    pub fn runtime_operand(&self, r: Ref) -> bool {
        self.runtime(r, &mut HashSet::new())
    }
    pub fn denotes_type(&self, r: Ref) -> bool {
        self.type_operand(r, &mut HashSet::new())
    }
    fn type_operand(&self, r: Ref, seen: &mut HashSet<Ref>) -> bool {
        if !seen.insert(r) {
            return false;
        }
        let source = &self.sources[r.file];
        let node = source.node(r.node);
        if node.kind.ends_with("Type") {
            return true;
        }
        if node.kind == "Paren" {
            return source
                .role(r.node, "expr")
                .is_some_and(|node| self.type_operand(Ref { file: r.file, node }, seen));
        }
        if let Some(symbol) = self.symbol(r) {
            let symbol = &self.symbols[symbol];
            let s = &self.sources[symbol.reference.file];
            if s.node(symbol.reference.node).kind == "PolyType" {
                return true;
            }
            if symbol.parameter
                && symbol
                    .annotation
                    .is_some_and(|id| s.node(id).kind == "TypeidType")
            {
                return true;
            }
            return symbol.constant
                && symbol.value.is_some_and(|node| {
                    self.type_operand(
                        Ref {
                            file: symbol.reference.file,
                            node,
                        },
                        seen,
                    )
                });
        }
        if node.kind == "Call" {
            return source
                .role(r.node, "callee")
                .is_some_and(|node| self.native_builtin(Ref { file: r.file, node }, "type_of"));
        }
        let text = source.text(r.node);
        node.kind == "Ident"
            && (matches!(
                text,
                "bool"
                    | "b8"
                    | "b16"
                    | "b32"
                    | "b64"
                    | "int"
                    | "uint"
                    | "uintptr"
                    | "rune"
                    | "rawptr"
                    | "typeid"
                    | "any"
                    | "string"
                    | "cstring"
                    | "byte"
            ) || text.starts_with(['i', 'u', 'f'])
                && text.chars().nth(1).is_some_and(|c| c.is_ascii_digit()))
    }
    fn runtime(&self, r: Ref, seen: &mut HashSet<Ref>) -> bool {
        if !seen.insert(r) {
            return false;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        if n.kind == "Literal" || n.kind.ends_with("Type") {
            return false;
        }
        if let Some(id) = self.symbol(r) {
            let symbol = &self.symbols[id];
            if !symbol.constant {
                return true;
            }
        }
        if n.kind == "Call" {
            if let Some(callee) = s.role(r.node, "callee") {
                let callee = Ref {
                    file: r.file,
                    node: callee,
                };
                if !self.native_builtin(callee, s.text(callee.node)) && !self.denotes_type(callee) {
                    return true;
                }
            }
            let name = s.role(r.node, "callee").map(|id| s.text(id)).unwrap_or("");
            if !matches!(
                name,
                "len"
                    | "cap"
                    | "size_of"
                    | "align_of"
                    | "type_of"
                    | "min"
                    | "max"
                    | "abs"
                    | "clamp"
                    | "#config"
            ) && !matches!(
                name,
                "int"
                    | "uint"
                    | "i8"
                    | "i16"
                    | "i32"
                    | "i64"
                    | "i128"
                    | "u8"
                    | "u16"
                    | "u32"
                    | "u64"
                    | "u128"
                    | "f16"
                    | "f32"
                    | "f64"
                    | "string"
            ) {
                return true;
            }
        }
        n.children.iter().any(|node| {
            self.runtime(
                Ref {
                    file: r.file,
                    node: *node,
                },
                &mut seen.clone(),
            )
        })
    }
}
