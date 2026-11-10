//! Lexical symbols and type shapes used by native lowering. Odin retains final
//! authority over exact types, overloads, constant evaluation and assignability.
use crate::ast::{NodeId, Source};
use serde::Serialize;
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
#[path = "calls.rs"]
mod calls;
#[path = "conditions.rs"]
mod conditions;
#[path = "constants.rs"]
mod constants;
#[path = "contexts.rs"]
mod contexts;
#[path = "projections.rs"]
mod projections;
pub use projections::Projection;
#[path = "typefacts.rs"]
mod typefacts;
pub use calls::CallPlan;
pub use contexts::{ContextHint, ParameterWitness};
pub use typefacts::Substitutions;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize)]
pub struct Ref {
    pub file: usize,
    pub node: NodeId,
}
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize)]
pub enum Constant {
    Integer(i128),
    Boolean(bool),
    Text(String),
    Enum(Ref, i128),
}
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize)]
pub enum Type {
    Constant(Constant),
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
    Anonymous(Vec<(String, Type)>),
    Soa(Box<Type>),
    Record(Ref, Vec<(String, Type)>),
    Nominal(Ref, Box<Type>),
    Fixed {
        elem: Box<Type>,
        length: Option<i128>,
    },
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
            Self::Soa(elem) => Some(elem),
            Self::Nominal(_, base) => base.element(),
            Self::Fixed { elem, .. } => Some(elem),
            Self::Map { value, .. } => Some(value),
            _ => None,
        }
    }
    pub fn is_sequence(&self) -> bool {
        if let Self::Nominal(_, base) = self {
            return base.is_sequence();
        }
        matches!(
            self,
            Self::Array { .. }
                | Self::Slice(_)
                | Self::Dynamic(_)
                | Self::Fixed { .. }
                | Self::String { .. }
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
        if let Self::Nominal(_, base) = self {
            return base.numeric();
        }
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
    pub projection: Option<Projection>,
}
pub struct Resolver<'a> {
    pub sources: &'a [Source],
    pub symbols: Vec<Symbol>,
    scopes: HashMap<(usize, NodeId, String), Vec<usize>>,
    types: HashMap<Ref, Type>,
    active: HashSet<Ref>,
    planning: HashSet<(Ref, Vec<(String, Type)>)>,
    conditions: RefCell<HashSet<Ref>>,
    pub context_hints: HashMap<Ref, ContextHint>,
    pub parameter_witnesses: BTreeMap<Ref, ParameterWitness>,
    pub conditional_immutables: HashSet<Ref>,
    pub flags: Vec<String>,
    pub evaluated: BTreeMap<Ref, serde_json::Value>,
    pub bound_types: BTreeMap<(Ref, String), Type>,
    pub bound_constants: BTreeMap<(Ref, String), (Type, serde_json::Value)>,
    using_symbols: HashMap<Ref, usize>,
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
    pub fn set_lexical_bindings(
        &mut self,
        types: BTreeMap<(Ref, String), Type>,
        constants: BTreeMap<(Ref, String), (Type, serde_json::Value)>,
    ) {
        if self.bound_types == types && self.bound_constants == constants {
            return;
        }
        self.bound_types = types;
        self.bound_constants = constants;
        self.types.clear();
        self.context_hints.clear();
        self.parameter_witnesses.clear();
        self.prepare_contexts();
    }
    pub fn remember_evaluation(&mut self, node: Ref, value: serde_json::Value) {
        self.evaluated.insert(node, value);
        self.types.clear();
    }
    pub fn new(sources: &'a [Source]) -> Self {
        Self::with_flags(sources, &[])
    }
    pub fn with_flags(sources: &'a [Source], flags: &[String]) -> Self {
        let mut this = Self {
            sources,
            symbols: vec![],
            scopes: HashMap::new(),
            types: HashMap::new(),
            active: HashSet::new(),
            planning: HashSet::new(),
            conditions: RefCell::new(HashSet::new()),
            context_hints: HashMap::new(),
            parameter_witnesses: BTreeMap::new(),
            conditional_immutables: HashSet::new(),
            flags: flags.to_vec(),
            evaluated: BTreeMap::new(),
            bound_types: BTreeMap::new(),
            bound_constants: BTreeMap::new(),
            using_symbols: HashMap::new(),
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
                        let Some(signature) = signature else {
                            continue;
                        };
                        if source.node(signature).kind == "StructType" {
                            if source.node(id).parent != source.role(signature, "params") {
                                continue;
                            }
                            owner = signature;
                            parameter = true;
                        } else {
                            owner = source
                                .node(signature)
                                .parent
                                .filter(|id| source.node(*id).kind == "Procedure")
                                .unwrap_or(signature);
                            parameter = source.node(id).parent == source.role(signature, "params");
                            result = !parameter;
                        }
                        source.group(id, "names").to_vec()
                    }
                    "Range" | "TypeSwitch" => {
                        owner = id;
                        source.group(id, "names").to_vec()
                    }
                    "PolyType" => {
                        if let Some(prototype) = source.ancestors(id).find(|id| {
                            ["ProcedureType", "StructType"]
                                .contains(&source.node(*id).kind.as_str())
                        }) {
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
                    let name = if source.names.get(source.node(name_node).name) == "_" {
                        "_".to_string()
                    } else if source.node(name_node).kind == "PolyType" {
                        source.names.get(source.node(name_node).name).to_string()
                    } else {
                        source
                            .text(name_node)
                            .trim_start_matches(['&', '$'])
                            .to_string()
                    };
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
                        projection: None,
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
                            ) && sources[s.reference.file]
                                .role(a, "cond")
                                .is_none_or(|node| {
                                    this.condition(Ref {
                                        file: s.reference.file,
                                        node,
                                    })
                                    .is_none()
                                })
                        })
                })
            {
                this.conditional_immutables
                    .extend(ids.iter().map(|id| this.symbols[*id].reference));
            }
        }
        this.prepare_projections();
        this
    }
    pub fn lookup(&self, reference: Ref, name: &str) -> Option<usize> {
        let source = &self.sources[reference.file];
        if source.text(reference.node) == name {
            if let Some(symbol) = self.using_symbols.get(&reference) {
                return Some(*symbol);
            }
        }
        for owner in std::iter::once(reference.node)
            .chain(source.ancestors(reference.node))
            .filter(|id| is_scope(source, *id))
        {
            if source.node(owner).kind == "Range"
                && ["expr", "init"].iter().any(|role| {
                    source.role(owner, role).is_some_and(|expr| {
                        source
                            .node(expr)
                            .span
                            .contains(source.node(reference.node).span)
                    })
                })
            {
                continue;
            }
            if let Some(ids) = self.scopes.get(&(reference.file, owner, name.to_string())) {
                let own_decl = source.ancestors(reference.node).find(|id| {
                    source.node(*id).kind == "Declaration"
                        && source.node(*id).mutable
                        && !source.group(*id, "names").contains(&reference.node)
                });
                let own_field = source.ancestors(reference.node).find(|id| {
                    source.node(*id).kind == "Field"
                        && ["type", "default"].iter().any(|role| {
                            source.role(*id, role).is_some_and(|part| {
                                source
                                    .node(part)
                                    .span
                                    .contains(source.node(reference.node).span)
                            })
                        })
                });
                if let Some(id) = ids.iter().find(|id| {
                    Some(self.symbols[**id].reference.node) != own_decl
                        && (owner == NodeId(0)
                            || source.node(self.symbols[**id].reference.node).kind != "Declaration"
                            || !source.node(self.symbols[**id].reference.node).mutable
                            || source.node(reference.node).span.start
                                >= source.node(self.symbols[**id].reference.node).span.end
                            || source
                                .group(self.symbols[**id].reference.node, "names")
                                .contains(&reference.node))
                        && !own_field.is_some_and(|field| {
                            source
                                .group(field, "names")
                                .contains(&self.symbols[**id].name_node)
                        })
                        && self.active_definition(self.symbols[**id].reference)
                }) {
                    return Some(*id);
                }
            }
        }
        for (file, other) in self.sources.iter().enumerate() {
            if other.path.parent() != source.path.parent() {
                continue;
            }
            if let Some(ids) = self.scopes.get(&(file, NodeId(0), name.to_string())) {
                if let Some(id) = ids
                    .iter()
                    .find(|id| self.active_definition(self.symbols[**id].reference))
                    .copied()
                {
                    return Some(id);
                }
            }
        }
        for (index, node) in source.nodes.iter().enumerate() {
            if node.kind != "Using" || node.span.start >= source.node(reference.node).span.start {
                continue;
            }
            let owner = scope(source, NodeId(index));
            if owner != NodeId(0)
                && !source
                    .node(owner)
                    .span
                    .contains(source.node(reference.node).span)
            {
                continue;
            }
            for value in source.group(NodeId(index), "values") {
                let alias = source.text(*value);
                for import in &source.nodes {
                    if import.kind != "Import" {
                        continue;
                    }
                    let Ok(path) = crate::lower::unquote(&import.op) else {
                        continue;
                    };
                    let explicit = source.names.get(import.name);
                    let namespace = if explicit.is_empty() {
                        path.rsplit(['/', ':']).next().unwrap_or("")
                    } else {
                        explicit
                    };
                    if namespace == alias {
                        if let Some(symbol) = self.package_symbol(source, &path, name) {
                            return Some(symbol);
                        }
                    }
                }
            }
        }
        None
    }
    pub fn symbol(&self, reference: Ref) -> Option<usize> {
        if let Some(symbol) = self.using_symbols.get(&reference) {
            return Some(*symbol);
        }
        let source = &self.sources[reference.file];
        match source.node(reference.node).kind.as_str() {
            "Ident" | "PolyType" => {
                let name = if source.node(reference.node).kind == "PolyType" {
                    source.names.get(source.node(reference.node).name)
                } else {
                    source.text(reference.node).trim_start_matches('$')
                };
                self.lookup(reference, name).or_else(|| {
                    source
                        .selections
                        .iter()
                        .filter(|s| s.wildcard || s.names.iter().any(|item| item == name))
                        .find_map(|selection| {
                            crate::lower::unquote(&selection.path)
                                .ok()
                                .and_then(|path| self.package_symbol(source, &path, name))
                        })
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
                .and_then(|symbols| {
                    symbols
                        .iter()
                        .find(|id| self.active_definition(self.symbols[**id].reference))
                })
                .copied()
        })
    }
    pub fn active_definition(&self, r: Ref) -> bool {
        let source = &self.sources[r.file];
        let span = source.node(r.node).span;
        for ancestor in source.ancestors(r.node) {
            if source.node(ancestor).kind != "When" {
                continue;
            }
            let Some(cond) = source.role(ancestor, "cond") else {
                continue;
            };
            if let Some(selected) = self.condition(Ref {
                file: r.file,
                node: cond,
            }) {
                let arm = source.role(ancestor, if selected { "body" } else { "else" });
                if !arm.is_some_and(|arm| source.node(arm).span.contains(span)) {
                    return false;
                }
            }
        }
        true
    }
    pub fn condition(&self, r: Ref) -> Option<bool> {
        if !self.conditions.borrow_mut().insert(r) {
            return None;
        }
        let value = self.condition_inner(r);
        self.conditions.borrow_mut().remove(&r);
        value
    }
    fn condition_inner(&self, r: Ref) -> Option<bool> {
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let role = |name| s.role(r.node, name).map(|node| Ref { file: r.file, node });
        match n.kind.as_str() {
            "Paren" => self.condition(role("expr")?),
            "Ident" => match s.text(r.node) {
                "true" => Some(true),
                "false" => Some(false),
                "ODIN_DEBUG" => Some(self.flags.iter().any(|f| f == "-debug")),
                _ => self.symbol(r).and_then(|id| {
                    let symbol = &self.symbols[id];
                    if symbol.constant {
                        symbol.value.and_then(|node| {
                            self.condition(Ref {
                                file: symbol.reference.file,
                                node,
                            })
                        })
                    } else {
                        None
                    }
                }),
            },
            "Unary" if n.op == "!" => self.condition(role("expr")?).map(|x| !x),
            "Call" => {
                let callee = role("callee")?;
                let args = s.group(r.node, "args");
                if s.text(callee.node) == "#config" && args.len() == 2 {
                    let key = s.text(args[0]).trim_matches('"');
                    let prefix = format!("-define:{key}=");
                    if let Some(value) = self
                        .flags
                        .iter()
                        .find_map(|flag| flag.strip_prefix(&prefix))
                    {
                        return match value {
                            "true" => Some(true),
                            "false" => Some(false),
                            _ => None,
                        };
                    }
                    return self.condition(Ref {
                        file: r.file,
                        node: args[1],
                    });
                }
                None
            }
            "Binary" => {
                let a = role("left")?;
                let b = role("right")?;
                if matches!(n.op.as_str(), "==" | "!=")
                    && s.node(a.node).kind == "Ident"
                    && s.node(b.node).kind == "ImplicitSelector"
                {
                    let target = self
                        .flags
                        .iter()
                        .find_map(|flag| flag.strip_prefix("-target:"))
                        .and_then(|target| target.split_once('_'));
                    let actual = match s.text(a.node) {
                        "ODIN_OS" => {
                            target
                                .map(|(os, _)| os)
                                .unwrap_or(if cfg!(target_os = "macos") {
                                    "darwin"
                                } else {
                                    std::env::consts::OS
                                })
                        }
                        "ODIN_ARCH" => {
                            target
                                .map(|(_, arch)| arch)
                                .unwrap_or(match std::env::consts::ARCH {
                                    "aarch64" => "arm64",
                                    "x86_64" => "amd64",
                                    other => other,
                                })
                        }
                        _ => "",
                    };
                    if !actual.is_empty() {
                        return Some(
                            actual.eq_ignore_ascii_case(s.text(b.node).trim_start_matches('.'))
                                == (n.op == "=="),
                        );
                    }
                }
                if matches!(n.op.as_str(), "&&" | "||") {
                    let a = self.condition(a);
                    let b = self.condition(b);
                    return if n.op == "&&" {
                        if a == Some(false) || b == Some(false) {
                            Some(false)
                        } else if a == Some(true) && b == Some(true) {
                            Some(true)
                        } else {
                            None
                        }
                    } else if a == Some(true) || b == Some(true) {
                        Some(true)
                    } else if a == Some(false) && b == Some(false) {
                        Some(false)
                    } else {
                        None
                    };
                }
                let a = self.constant_int(a)?;
                let b = self.constant_int(b)?;
                match n.op.as_str() {
                    "==" => Some(a == b),
                    "!=" => Some(a != b),
                    "<" => Some(a < b),
                    "<=" => Some(a <= b),
                    ">" => Some(a > b),
                    ">=" => Some(a >= b),
                    _ => None,
                }
            }
            _ => None,
        }
    }
    pub fn signature(&mut self, reference: Ref) -> Option<Ref> {
        match self.type_of(reference) {
            Type::Procedure(signature) => Some(signature),
            _ => None,
        }
    }
    pub fn primitive(&self, r: Ref) -> Option<Type> {
        let source = &self.sources[r.file];
        let name = if source.node(r.node).kind == "Ident" {
            if self.symbol(r).is_some() {
                return None;
            }
            source.text(r.node)
        } else if source.node(r.node).kind == "Selector" {
            let owner = source.role(r.node, "expr")?;
            let field = source.role(r.node, "field")?;
            if source.node(owner).kind != "Ident"
                || self
                    .lookup(
                        Ref {
                            file: r.file,
                            node: owner,
                        },
                        source.text(owner),
                    )
                    .is_some()
            {
                return None;
            }
            let native = source.nodes.iter().any(|node| {
                node.kind == "Import"
                    && crate::lower::unquote(&node.op).is_ok_and(|path| {
                        path == "base:builtin" && {
                            let alias = source.names.get(node.name);
                            source.text(owner) == if alias.is_empty() { "builtin" } else { alias }
                        }
                    })
            });
            if !native {
                return None;
            }
            if source.collection_roots.get("base").is_some_and(|root| {
                source.info["odin_root"]
                    .as_str()
                    .is_none_or(|odin| root != &std::path::Path::new(odin).join("base"))
            }) {
                return None;
            }
            source.text(field)
        } else {
            return None;
        };
        if matches!(name, "string" | "cstring" | "string16" | "cstring16") {
            return Some(Type::String {
                name: name.into(),
                untyped: false,
            });
        }
        if matches!(
            name,
            "bool"
                | "b8"
                | "b16"
                | "b32"
                | "b64"
                | "int"
                | "uint"
                | "uintptr"
                | "rawptr"
                | "any"
                | "rune"
                | "byte"
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
                | "complex32"
                | "complex64"
                | "complex128"
                | "quaternion64"
                | "quaternion128"
                | "quaternion256"
        ) {
            Some(Type::scalar(name))
        } else {
            None
        }
    }
    pub fn standard_library(&self, r: Ref, path: &str) -> bool {
        let Some((collection, suffix)) = path.split_once(':') else {
            return false;
        };
        if !["base", "core", "vendor"].contains(&collection) {
            return false;
        }
        let source = &self.sources[r.file];
        source.info["odin_root"].as_str().is_some_and(|root| {
            let expected = std::path::Path::new(root).join(collection).join(suffix);
            let actual = source
                .collection_roots
                .get(collection)
                .map(|root| root.join(suffix))
                .unwrap_or_else(|| expected.clone());
            match (
                std::fs::canonicalize(actual),
                std::fs::canonicalize(expected),
            ) {
                (Ok(actual), Ok(expected)) => actual == expected,
                _ => false,
            }
        })
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
                let name = if source.names.get(source.node(*name).name) == "_" {
                    "_"
                } else {
                    source.text(*name).trim_start_matches('$')
                };
                result.push((name.into(), annotation));
            }
        }
        result
    }
    pub fn attributes(&self, declaration: Ref) -> Vec<(String, Option<Ref>)> {
        let s = &self.sources[declaration.file];
        let mut result = vec![];
        for attribute in s.group(declaration.node, "attributes") {
            let items = s.group(*attribute, "elems");
            for item in items {
                let name = s.role(*item, "field").unwrap_or(*item);
                let value = s.role(*item, "value").map(|node| Ref {
                    file: declaration.file,
                    node,
                });
                result.push((s.text(name).into(), value));
            }
        }
        result
    }
    pub fn private_call_reason(&self, callee: Ref) -> Option<String> {
        let symbol = &self.symbols[self.symbol(callee)?];
        let owner = &self.sources[symbol.reference.file];
        let caller = &self.sources[callee.file];
        let mut visibility = None;
        for (name, value) in self.attributes(symbol.reference) {
            if name == "private" {
                visibility = Some(
                    value
                        .and_then(|r| crate::lower::unquote(self.sources[r.file].text(r.node)).ok())
                        .unwrap_or("package".into()),
                );
            }
        }
        if visibility.is_none() {
            for tag in owner.info["file_tags"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|tag| tag.as_str())
            {
                if tag.contains("private") {
                    visibility = Some(
                        if tag.split_whitespace().any(|word| word == "file") {
                            "file"
                        } else {
                            "package"
                        }
                        .into(),
                    );
                }
            }
        }
        match visibility.as_deref() {
            Some("file") if caller.path != owner.path => Some(format!(
                "Procedure '{}' is private to its file.",
                symbol.name
            )),
            Some("package") if caller.path.parent() != owner.path.parent() => Some(format!(
                "Procedure '{}' is private to its package.",
                symbol.name
            )),
            _ => None,
        }
    }
    pub fn type_of(&mut self, reference: Ref) -> Type {
        if (!self.bound_types.is_empty() || !self.bound_constants.is_empty())
            && matches!(
                self.sources[reference.file]
                    .node(reference.node)
                    .kind
                    .as_str(),
                "Ident" | "PolyType"
            )
        {
            if let Some(typ) = self.bound_type(reference, &Default::default()) {
                return typ.clone();
            }
        }
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
                    if let Some(projection) = sym.projection {
                        return self.projected_type(&projection, &Substitutions::new());
                    }
                    if let Some(annotation) = sym.annotation {
                        if self.compiled_type_parameter(
                            Ref {
                                file: sym.reference.file,
                                node: annotation,
                            },
                            &sym.name,
                        ) {
                            return Type::Generic;
                        }
                        return self.type_of(Ref {
                            file: sym.reference.file,
                            node: annotation,
                        });
                    }
                    if let Some(value) = sym.value {
                        let source = &self.sources[sym.reference.file];
                        let reference = Ref {
                            file: sym.reference.file,
                            node: value,
                        };
                        let typ = if source.group(sym.reference.node, "values").len() == 1
                            && source.group(sym.reference.node, "names").len() > 1
                        {
                            self.result_type(reference, sym.index, &mut HashSet::new())
                        } else {
                            self.type_of(reference)
                        };
                        return if sym.constant { typ } else { typ.defaulted() };
                    }
                    if s.node(sym.reference.node).kind == "Range" {
                        if let Some(expr) = s.role(sym.reference.node, "expr") {
                            let mut t = self.type_of(Ref {
                                file: r.file,
                                node: expr,
                            });
                            while let Type::Pointer(elem) = t {
                                t = *elem;
                            }
                            if let Type::Map { key, value } = t {
                                return if sym.index == 0 { *key } else { *value };
                            }
                            if sym.index == 1 {
                                return Type::scalar("int");
                            }
                            if matches!(t, Type::String { .. }) {
                                return Type::scalar("rune");
                            }
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
            "Paren" | "AutoCast" | "Unary" | "Run" => {
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
            "DynamicArrayType" => {
                let elem = Box::new(
                    child("elem")
                        .map(|r| self.type_of(r))
                        .unwrap_or(Type::Unknown),
                );
                if let Some(capacity) = child("capacity") {
                    Type::Fixed {
                        elem,
                        length: self.constant_int(capacity),
                    }
                } else {
                    Type::Dynamic(elem)
                }
            }
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
            "Comprehension" => {
                if let Some(annotation) = child("type") {
                    return self.type_of(annotation);
                }
                let output = child("output")
                    .map(|r| self.type_of(r))
                    .unwrap_or(Type::Unknown);
                let output = if n.data["spread"] == true {
                    output.element().cloned().unwrap_or(Type::Unknown)
                } else {
                    output.defaulted()
                };
                if let Some(values) = self
                    .evaluated
                    .get(&r)
                    .and_then(|value| value["values"].as_array())
                {
                    Type::Array {
                        elem: Box::new(output),
                        length: Some(values.len() as i128),
                    }
                } else {
                    Type::Slice(Box::new(output))
                }
            }
            "SetType" => Type::Set(Box::new(
                s.group(r.node, "args")
                    .first()
                    .copied()
                    .map(|node| self.type_of(Ref { file: r.file, node }))
                    .unwrap_or(Type::Unknown),
            )),
            "StructType" => Type::Struct(r),
            "UnionType" => Type::Union(r),
            "EnumType" => Type::Enum(r),
            "PolyType" => child("specialization")
                .map(|target| self.type_of(target))
                .unwrap_or(Type::Generic),
            "TypeidType" => Type::Scalar {
                name: "typeid".into(),
                untyped: false,
            },
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
            "SwitchExpr" | "CompileSwitchExpr" | "OrPanic" => {
                self.result_type(r, 0, &mut HashSet::new())
            }
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
                if builtin && matches!(name, "min" | "max") {
                    let args = s.group(r.node, "args");
                    let ellipsis = n.spans.get("ellipsis");
                    for arg in args {
                        let mut value = *arg;
                        while s.node(value).kind == "Paren" {
                            let Some(inner) = s.role(value, "expr") else {
                                break;
                            };
                            value = inner;
                        }
                        let spread =
                            ellipsis.is_some_and(|span| s.node(value).span.start >= span.end);
                        let array = s.node(value).kind == "Unary" && s.node(value).op == "**";
                        if spread || array {
                            let value = if array {
                                s.role(value, "expr").unwrap_or(value)
                            } else {
                                value
                            };
                            return self
                                .type_of(Ref {
                                    file: r.file,
                                    node: value,
                                })
                                .element()
                                .cloned()
                                .unwrap_or(Type::Unknown);
                        }
                    }
                    if let Some(arg) = args.first() {
                        return self.type_of(Ref {
                            file: r.file,
                            node: *arg,
                        });
                    }
                }
                let callee_type = self.type_of(callee);
                match callee_type {
                    Type::Procedure(signature) => {
                        let specialized = self.specialized_result(r, 0, &Substitutions::new());
                        if !specialized.is_unknown() {
                            return specialized;
                        }
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
                    Type::Struct(_) | Type::Record(..) => {
                        self.specialized_type(r, &Substitutions::new())
                    }
                    Type::Unknown => self.specialized_result(r, 0, &Substitutions::new()),
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
                let typ = self.type_of(base);
                self.member_type(&typ, field)
            }
            _ => Type::Unknown,
        }
    }
    fn result_type(&mut self, r: Ref, index: usize, seen: &mut HashSet<Ref>) -> Type {
        self.specialized_value_result(r, index, &Substitutions::new(), seen)
    }
    pub fn constant_int(&self, r: Ref) -> Option<i128> {
        self.integer(r, &mut HashSet::new())
    }
    fn integer(&self, r: Ref, seen: &mut HashSet<Ref>) -> Option<i128> {
        if let Some(symbol) = (!self.bound_constants.is_empty())
            .then(|| self.symbol(r))
            .flatten()
            .map(|index| &self.symbols[index])
        {
            if let Some((typ, value)) = self
                .bound_constants
                .get(&(symbol.reference, symbol.name.clone()))
                .filter(|(_, value)| value["kind"] == "integer")
            {
                if typ.native_float_constant() {
                    return None;
                }
                return value["value"].as_str()?.parse().ok();
            }
        }
        if let Some(value) = self
            .evaluated
            .get(&r)
            .filter(|value| value["kind"] == "integer")
        {
            return value["value"].as_str()?.parse().ok();
        }
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
            "Call" => {
                let callee = child("callee")?;
                let args = s.group(r.node, "args");
                if self.native_builtin(callee, "size_of") && args.len() == 1 {
                    let arg = Ref {
                        file: r.file,
                        node: args[0],
                    };
                    let typ = self.primitive(arg)?;
                    let pointer = self
                        .flags
                        .iter()
                        .find_map(|flag| flag.strip_prefix("-target:"))
                        .and_then(|target| target.split_once('_'))
                        .map(|(_, arch)| {
                            if matches!(arch, "i386" | "arm32" | "wasm32") {
                                4
                            } else {
                                8
                            }
                        })
                        .unwrap_or(std::mem::size_of::<usize>());
                    return match typ {
                        Type::Scalar { name, .. } => match name.as_str() {
                            "rawptr" | "int" | "uint" | "uintptr" => Some(pointer as i128),
                            "any" => Some((2 * pointer) as i128),
                            "rune" => Some(4),
                            "byte" | "bool" => Some(1),
                            other => other
                                .trim_start_matches(['i', 'u', 'f', 'b'])
                                .parse::<i128>()
                                .ok()
                                .map(|bits| bits / 8),
                        },
                        Type::String { name, .. } => {
                            Some((pointer * if name.starts_with('c') { 1 } else { 2 }) as i128)
                        }
                        _ => None,
                    };
                }
                None
            }
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
        if self.primitive(r).is_some() {
            return true;
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
        if node.kind == "WhenExpr" {
            if let Some(selected) = source
                .role(r.node, "cond")
                .and_then(|node| self.condition(Ref { file: r.file, node }))
            {
                return source
                    .role(r.node, if selected { "x" } else { "y" })
                    .is_some_and(|node| self.type_operand(Ref { file: r.file, node }, seen));
            }
            return ["x", "y"].iter().all(|role| {
                source.role(r.node, role).is_some_and(|node| {
                    self.type_operand(Ref { file: r.file, node }, &mut seen.clone())
                })
            });
        }
        if let Some(symbol) = self.symbol(r) {
            let symbol = &self.symbols[symbol];
            let s = &self.sources[symbol.reference.file];
            if self
                .bound_constants
                .contains_key(&(symbol.reference, symbol.name.clone()))
            {
                return false;
            }
            if s.node(symbol.reference.node).kind == "PolyType" {
                return !s.node(symbol.reference.node).parent.is_some_and(|parent| {
                    s.node(parent).kind == "ArrayType"
                        && s.role(parent, "len") == Some(symbol.reference.node)
                });
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
            return source.role(r.node, "callee").is_some_and(|node| {
                let callee = Ref { file: r.file, node };
                self.native_builtin(callee, "type_of")
                    || self.record_constructor(callee, &mut seen.clone())
            });
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
    fn record_constructor(&self, r: Ref, seen: &mut HashSet<Ref>) -> bool {
        if !seen.insert(r) {
            return false;
        }
        let s = &self.sources[r.file];
        if matches!(s.node(r.node).kind.as_str(), "StructType" | "UnionType") {
            return s
                .role(r.node, "params")
                .is_some_and(|params| !s.node(params).children.is_empty());
        }
        if s.node(r.node).kind == "Paren" {
            return s
                .role(r.node, "expr")
                .is_some_and(|node| self.record_constructor(Ref { file: r.file, node }, seen));
        }
        self.symbol(r).is_some_and(|index| {
            let symbol = &self.symbols[index];
            symbol.constant
                && symbol.value.is_some_and(|node| {
                    self.record_constructor(
                        Ref {
                            file: symbol.reference.file,
                            node,
                        },
                        seen,
                    )
                })
        })
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
