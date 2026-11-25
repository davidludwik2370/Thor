//! Lexical symbols and type shapes used by native lowering. Odin retains final
//! authority over exact types, overloads, constant evaluation and assignability.
use crate::ast::{NodeId, Source};
use serde::Serialize;
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::sync::Arc;
#[path = "nodetable.rs"]
mod nodetable;
use nodetable::NodeTable;
#[path = "logicalfacts.rs"]
mod logicalfacts;
#[path = "calls.rs"]
mod calls;
#[path = "signaturelayout.rs"]
mod signaturelayout;
#[path = "conditions.rs"]
mod conditions;
#[path = "constants.rs"]
mod constants;
#[path = "contexts.rs"]
mod contexts;
#[path = "projections.rs"]
mod projections;
#[path = "namescope.rs"]
mod namescope;
pub use projections::Projection;
#[path = "typefacts.rs"]
mod typefacts;
#[path = "builtintypes.rs"]
mod builtintypes;
#[path = "intrinsictypes.rs"]
mod intrinsictypes;
pub(crate) use intrinsictypes::{allocation_free_intrinsic,atomic_intrinsic};
pub use calls::CallPlan;
pub(crate) use calls::ClosedCalls;
pub use contexts::{ContextHint, ParameterWitness};
pub use typefacts::Substitutions;
#[path = "typepool.rs"]
mod typepool;
#[path = "resolverseed.rs"]
mod resolverseed;
pub(crate) use resolverseed::ResolverSeed;
pub(crate) use typepool::{SharedType, TypeId, TypeInput};

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
    BigInteger,
    BigRational,
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
    Matrix {elem:Box<Type>,rows:Option<i128>,columns:Option<i128>,row_major:bool},
    Vector {
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
    Union(Ref, Vec<(String, Type)>),
    Procedure(Ref),
}
impl Type {
    pub fn big_name(&self) -> Option<&'static str> {
        match self {
            Self::BigInteger => Some("Integer"),
            Self::BigRational => Some("Rational"),
            _ => None,
        }
    }
    pub(crate) fn record_like(&self) -> bool {
        match self {
            Self::Struct(_) | Self::Record(..) | Self::Anonymous(_) => true,
            Self::Nominal(_, inner) => inner.record_like(),
            _ => false,
        }
    }
    pub fn is_string(&self) -> bool {
        match self {
            Self::String { .. } => true,
            Self::Nominal(_, inner) => inner.is_string(),
            _ => false,
        }
    }
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
            | Self::Vector { elem, .. } | Self::Matrix {elem,..}
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
    pub fn indexed_element(&self) -> Option<&Type> {
        match self {
            // Pointer and multi-pointer shapes share this representation.
            // Let Odin resolve implicit collection dereferencing until the
            // representation can distinguish it from multi-pointer indexing.
            Self::Pointer(_) => None,
            Self::Nominal(_, base) => base.indexed_element(),
            _ => self.element(),
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
                    || name == "byte"
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
/// A name actually requested from an import not yet loaded by this resolver.
/// Stable source indices let declaration-only discovery replay it after append.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct ImportDemand {
    pub file: usize,
    pub path: String,
    pub name: String,
    pub owner: Option<Ref>,
}
type ScopeTable = NodeTable<HashMap<String, Vec<usize>>>;
/// Syntax-only state. Project snapshots may share this across flags and generic
/// frames; evaluated types, conditions and using projections stay per resolver.
#[derive(Clone)]
pub(crate) struct ResolverIndex {
    symbols: Arc<Vec<Symbol>>,
    scopes: Arc<ScopeTable>,
    names: Arc<namescope::NameIndex>,
    markers: Arc<BTreeMap<(Ref, String), String>>,
}
pub struct Resolver<'a> {
    pub sources: &'a [Source],
    pub symbols: Arc<Vec<Symbol>>,
    scopes: Arc<ScopeTable>,
    index: Arc<namescope::NameIndex>,
    base: Option<ResolverIndex>,
    import_packages: RefCell<HashMap<(usize, String), Option<usize>>>,
    canonical_paths: RefCell<HashMap<std::path::PathBuf, Option<std::path::PathBuf>>>,
    missing_packages: RefCell<BTreeSet<std::path::PathBuf>>,
    missing_symbols: RefCell<BTreeSet<ImportDemand>>,
    track_import_demands: Cell<bool>,
    types: NodeTable<Type>,
    base_types: Option<Arc<NodeTable<Type>>>,
    active: HashSet<Ref>,
    planning: HashSet<(Ref, Vec<(String, Type)>)>,
    incomplete_plans: Cell<u64>,
    conditions: RefCell<HashSet<Ref>>,
    pub context_hints: HashMap<Ref, ContextHint>,
    pub parameter_witnesses: BTreeMap<Ref, ParameterWitness>,
    pub immutable_markers: Arc<BTreeMap<(Ref,String),String>>,
    pub flags: Vec<String>,
    pub evaluated: BTreeMap<Ref, serde_json::Value>,
    pub logical_constants: BTreeMap<Ref,serde_json::Value>,
    logical_activation: std::cell::OnceCell<bool>,
    runtime_root: std::cell::OnceCell<std::path::PathBuf>,
    pub(crate) set_activation: std::cell::OnceCell<bool>,
    pub bound_types: BTreeMap<(Ref, String), Type>,
    pub bound_constants: BTreeMap<(Ref, String), (Type, serde_json::Value)>,
    using_symbols: HashMap<Ref, usize>,
    symbol_queries: RefCell<Option<HashMap<Ref, Option<usize>>>>,
    metadata: Option<Metadata>,
    base_metadata: Option<Arc<Metadata>>,
    base_symbol_queries: Option<Arc<HashMap<Ref, Option<usize>>>>,
    type_pool: RefCell<typepool::TypePool>,
    contexts_ready: bool,
}
/// Immutable answers shared by permission, purity and emission queries in the
/// same resolver. Binding/evaluation changes invalidate the entire table.
#[derive(Default)]
struct Metadata {
    calls: ClosedCalls,
    // Exact-input answers for this source graph only. Unlike ClosedCalls these
    // may contain unresolved overloads and never survive namespace extension.
    local_calls: HashMap<Ref, HashMap<(Substitutions, bool), Vec<CallPlan>>>,
    local_call_count: usize,
    specialized: HashMap<Ref, HashMap<Substitutions, Type>>,
    fields: HashMap<TypeId, RecordFields>,
    members: HashMap<TypeId, HashMap<String, Type>>,
    paths: HashMap<TypeId, HashMap<String, Option<Vec<String>>>>,
}
pub(crate) type RecordFields = Arc<[(String, Type)]>;
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
        self.invalidate_queries();
        self.context_hints.clear();
        self.parameter_witnesses.clear();
        self.prepare_contexts();
    }
    pub(crate) fn remember_logical_constant(&mut self,node:Ref,value:serde_json::Value){
        if self.logical_constants.get(&node) == Some(&value) { return; }
        self.logical_constants.insert(node,value);self.invalidate_queries();
    }
    pub(crate) fn logical_constant_type(&self,node:Ref)->Option<Type>{
        match self.logical_constants.get(&node).or_else(||self.evaluated.get(&node))?["kind"].as_str()?{
            "big_integer"=>Some(Type::BigInteger),"big_rational"=>Some(Type::BigRational),_=>None,
        }
    }
    pub fn remember_evaluation(&mut self, node: Ref, value: serde_json::Value) {
        if self.evaluated.get(&node) == Some(&value) { return; }
        self.evaluated.insert(node, value);
        self.invalidate_queries();
    }
    pub(crate) fn set_evaluations(&mut self, values: BTreeMap<Ref, serde_json::Value>) {
        if self.evaluated != values { self.evaluated = values; self.invalidate_queries(); }
    }
    /// Compiler-owned support collection remains distinct from user overrides.
    /// Cache the installation path per analysis; relocation never uses a build
    /// machine's source path and hot identity checks do no filesystem discovery.
    pub(crate) fn runtime_root(&self) -> &std::path::Path {
        self.runtime_root.get_or_init(|| {
            self.sources.iter().find_map(|source|
                source.collection_roots.get(&source.support_collection)).cloned()
                .unwrap_or_else(|| crate::cli::root().expect("compiler installation root").join("runtime"))
        })
    }
    pub fn new(sources: &'a [Source]) -> Self {
        Self::with_flags(sources, &[])
    }
    pub(crate) fn shared_type(&self, typ: impl Into<TypeInput>) -> SharedType {
        match typ.into() {
            TypeInput::Owned(typ) => self.type_pool.borrow_mut().intern(&typ),
            TypeInput::Shared(typ) => typ,
        }
    }
    pub(crate) fn type_id(&self, typ: &Type) -> TypeId {
        self.type_pool.borrow_mut().intern(typ).id()
    }
    pub fn with_flags(sources: &'a [Source], flags: &[String]) -> Self {
        Self::with_index(sources, flags, None)
    }
    pub(crate) fn syntax_index(&self) -> ResolverIndex {
        self.base.as_ref().expect("prepared syntax index").clone()
    }
    pub(crate) fn with_index(sources: &'a [Source], flags: &[String], base: Option<&ResolverIndex>) -> Self {
        let build_from = base.is_none().then_some(0);
        Self::from_index(sources, flags, base.cloned(), build_from)
    }
    pub(crate) fn extend_index(sources: &'a [Source], flags: &[String], base: ResolverIndex, start: usize) -> Self {
        Self::from_index(sources, flags, Some(base), Some(start))
    }
    fn from_index(sources: &'a [Source], flags: &[String], base: Option<ResolverIndex>, build_from: Option<usize>) -> Self {
        let _phase = crate::profile::span("analysis.resolver_setup");
        crate::profile::count("resolver.constructions", 1);
        let (symbols, mut scopes, mut index, markers) = match base {
            Some(base) => (base.symbols, base.scopes, base.names, base.markers),
            None => (Arc::default(), Arc::new(ScopeTable::new(sources.iter().map(|source| source.nodes.len()))),
                Arc::new(namescope::NameIndex::new(sources)), Arc::default()),
        };
        let symbol_start = symbols.len();
        if let Some(start) = build_from.filter(|start| *start > 0) {
            Arc::make_mut(&mut scopes).extend_files(sources[start..].iter().map(|source| source.nodes.len()));
            Arc::make_mut(&mut index).append(sources, start);
        }
        let mut this = Self {
            sources,
            symbols,
            scopes,
            index,
            base: None,
            import_packages: RefCell::new(HashMap::new()),
            canonical_paths: RefCell::new(HashMap::new()),
            missing_packages: RefCell::new(BTreeSet::new()),
            missing_symbols: RefCell::new(BTreeSet::new()),
            track_import_demands: Cell::new(false),
            types: NodeTable::new(sources.iter().map(|source| source.nodes.len())),
            base_types: None,
            active: HashSet::new(),
            planning: HashSet::new(),
            incomplete_plans: Cell::new(0),
            conditions: RefCell::new(HashSet::new()),
            context_hints: HashMap::new(),
            parameter_witnesses: BTreeMap::new(),
            immutable_markers: markers,
            flags: flags.to_vec(),
            evaluated: BTreeMap::new(),
            logical_constants: BTreeMap::new(),
            logical_activation: std::cell::OnceCell::new(),
            runtime_root: std::cell::OnceCell::new(),
            set_activation: std::cell::OnceCell::new(),
            bound_types: BTreeMap::new(),
            bound_constants: BTreeMap::new(),
            using_symbols: HashMap::new(),
            symbol_queries: RefCell::new(None),
            metadata: None,
            base_metadata: None,
            base_symbol_queries: None,
            type_pool: RefCell::new(typepool::TypePool::default()),
            contexts_ready: false,
        };
        if let Some(start) = build_from {
        if start == 0 {
            crate::profile::count("resolver.index_builds", 1);
        } else {
            crate::profile::count("resolver.index_reuses", 1);
            crate::profile::count("resolver.index_extensions", 1);
        }
        crate::profile::count("resolver.syntax_sources_indexed", (sources.len()-start) as u64);
        for (file, source) in sources.iter().enumerate().skip(start) {
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
                                "ProcedureType" | "StructType" | "BitFieldType"
                            )
                        });
                        let Some(signature) = signature else {
                            continue;
                        };
                        if matches!(source.node(signature).kind.as_str(), "StructType" | "BitFieldType") {
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
                    } else if let Some(operand) = source.role(name_node, "expr")
                        .filter(|operand| source.node(name_node).kind == "Unary"
                            && source.node(*operand).kind == "Ident")
                    {
                        source.text(operand).to_string()
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
                    }).or_else(|| (node.kind == "Field").then(|| source.role(id, "default")).flatten());
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
                    Arc::make_mut(&mut this.scopes)
                        .get_or_insert_with(Ref { file, node: owner }, HashMap::new)
                        .entry(name)
                        .or_default()
                        .push(this.symbols.len());
                    if owner == NodeId(0) {
                        let package = this.index.package_for[file];
                        Arc::make_mut(&mut this.index).package_symbols[package]
                            .entry(symbol.name.clone()).or_default().push(this.symbols.len());
                    }
                    Arc::make_mut(&mut this.symbols).push(symbol);
                }
            }
        }
        // Marker groups only matter when at least one binding is immutable.
        // Borrow paths and names instead of copying them for every SDK symbol.
        fn group_key<'s>(sources: &'s [Source], symbol: &'s Symbol) -> (&'s std::path::Path, Option<NodeId>, &'s str) {
            let source = &sources[symbol.reference.file];
            if symbol.scope.0 == 0 {
                (source.path.parent().unwrap_or(&source.path), None, symbol.name.as_str())
            } else {
                (source.path.as_path(), Some(symbol.scope), symbol.name.as_str())
            }
        }
        let mut groups: HashMap<_, Vec<usize>> = HashMap::new();
        for symbol in this.symbols.iter().skip(symbol_start).filter(|symbol| symbol.immutable) {
            groups.entry(group_key(sources, symbol)).or_default();
        }
        for (index, symbol) in this.symbols.iter().enumerate().skip(symbol_start) {
            if let Some(ids) = groups.get_mut(&group_key(sources, symbol)) {
                ids.push(index);
            }
        }
        for ids in groups.values(){
            if !ids.iter().any(|id|this.symbols[*id].immutable)||!ids.iter().any(|id|{
                let symbol=&this.symbols[*id];sources[symbol.reference.file].ancestors(symbol.reference.node)
                    .any(|node|matches!(sources[symbol.reference.file].node(node).kind.as_str(),"When"|"CompileSwitch"))
            }){continue;}
            let first=&this.symbols[ids[0]];let source=&sources[first.reference.file];
            let key=crate::digest::sha256(format!("{}{}",first.name,source.node(first.scope).span.start).as_bytes());
            let marker=format!("{}immutable_{}",source.prefix,&key[..12]);
            for id in ids{let symbol=&this.symbols[*id];Arc::make_mut(&mut this.immutable_markers).insert((symbol.reference,symbol.name.clone()),marker.clone());}
        }
        } else { crate::profile::count("resolver.index_reuses", 1); }
        this.base = Some(ResolverIndex {symbols:this.symbols.clone(), scopes:this.scopes.clone(),
            names:this.index.clone(), markers:this.immutable_markers.clone()});
        this.prepare_projections();
        this.metadata = Some(Metadata::default());
        this
    }
    fn invalidate_queries(&mut self) {
        self.contexts_ready = false;
        self.types.clear();
        self.base_types = None;
        self.base_metadata = None;
        self.base_symbol_queries = None;
        if let Some(metadata) = &mut self.metadata { *metadata = Metadata::default(); }
        if let Some(queries) = self.symbol_queries.get_mut() { queries.clear(); }
    }
    pub fn immutable_marker(&self,reference:Ref)->Option<(Ref,String)>{
        let symbol=&self.symbols[self.symbol(reference)?];
        self.immutable_markers.get(&(symbol.reference,symbol.name.clone())).map(|name|(symbol.reference,name.clone()))
    }
    pub fn lookup(&self, reference: Ref, name: &str) -> Option<usize> {
        crate::profile::count("lookup.calls", 1);
        let source = &self.sources[reference.file];
        if source.text(reference.node) == name {
            if let Some(symbol) = self.using_symbols.get(&reference) {
                return Some(*symbol);
            }
        }
        for owner in self.index.scopes(reference) {
            let range_header=source.node(owner).kind == "Range"
                && ["expr", "init"].iter().any(|role| {
                    source.role(owner, role).is_some_and(|expr| {
                        source
                            .node(expr)
                            .span
                            .contains(source.node(reference.node).span)
                    })
                });
            if let Some(ids) = self.scopes.get(&Ref { file: reference.file, node: owner }).and_then(|names| names.get(name)) {
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
                    !(range_header && self.symbols[**id].reference.node==owner)
                        && Some(self.symbols[**id].reference.node) != own_decl
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
        if let Some(ids) = self.index.package_symbols[self.index.package_for[reference.file]].get(name) {
            if let Some(id) = ids
                    .iter()
                    .find(|id| self.active_definition(self.symbols[**id].reference))
                    .copied()
            { return Some(id); }
        }
        for id in &self.index.using[reference.file] {
            crate::profile::count("lookup.using_candidates", 1);
            let node = source.node(*id);
            if node.span.start >= source.node(reference.node).span.start {
                continue;
            }
            let owner = self.index.scopes(Ref { file:reference.file, node:*id }).next().unwrap_or(NodeId(0));
            if owner != NodeId(0)
                && !source
                    .node(owner)
                    .span
                    .contains(source.node(reference.node).span)
            {
                continue;
            }
            for value in source.group(*id, "values") {
                let alias = source.text(*value);
                for path in self.index.imports[reference.file].get(alias).into_iter().flatten() {
                        if let Some(symbol) = self.package_symbol(reference, path, name) {
                            return Some(symbol);
                        }
                }
            }
        }
        None
    }
    // Nested fixed-input analyses share answers. Changes to bindings, context
    // witnesses or evaluated values clear them through invalidate_queries.
    pub(crate) fn begin_symbol_queries(&self) -> bool {
        let mut queries = self.symbol_queries.borrow_mut();
        let active = queries.is_some();
        if !active { *queries = Some(HashMap::new()); }
        active
    }
    pub(crate) fn end_symbol_queries(&self, previous: bool) {
        if !previous { self.symbol_queries.replace(None); }
    }
    pub fn symbol(&self, reference: Ref) -> Option<usize> {
        // A demanded import does not mutate this resolver's source graph.
        // Keep both positive and negative answers until this fixed-input pass
        // ends. Discovery creates a new resolver after loading dependencies;
        // the outstanding package set is never cleared by a cache hit.
        if let Some(value) = self.symbol_queries.borrow().as_ref()
            .and_then(|queries| queries.get(&reference)
                .or_else(|| self.base_symbol_queries.as_ref()?.get(&reference))).copied() {
            crate::profile::count("lookup.symbol_hits", 1);
            return value;
        }
        let value = self.symbol_uncached(reference);
        if let Some(queries) = self.symbol_queries.borrow_mut().as_mut() {
            queries.insert(reference, value);
        }
        value
    }
    fn symbol_uncached(&self, reference: Ref) -> Option<usize> {
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
                                .and_then(|path| self.package_symbol(reference, &path, name))
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
                self.index.imports[reference.file].get(source.text(base))
                    .into_iter().flatten().find_map(|path| {
                        self.package_symbol(reference, path, source.text(field))
                    })
            }
            "Paren" => self.symbol(Ref {
                file: reference.file,
                node: source.role(reference.node, "expr")?,
            }),
            _ => None,
        }
    }
    pub(crate) fn package_symbol(&self, site: Ref, path: &str, name: &str) -> Option<usize> {
        self.package_symbol_for(site.file, path, name, Some(site))
    }
    pub(crate) fn replay_import(&self, demand: &ImportDemand) -> Option<usize> {
        self.package_symbol_for(demand.file, &demand.path, &demand.name, demand.owner)
    }
    fn package_symbol_for(&self, file: usize, path: &str, name: &str, site: Option<Ref>) -> Option<usize> {
        let key = (file, path.to_string());
        let cached = self.import_packages.borrow().get(&key).copied();
        let package = if let Some(package) = cached { package } else {
        let source = &self.sources[file];
        let directory = if let Some((collection, suffix)) = path.split_once(':') {
            source.collection_roots.get(collection)?.join(suffix)
        } else {
            source.path.parent()?.join(path)
        };
        crate::profile::count("lookup.import_directory_resolutions", 1);
        let package = crate::project::canonicalize(directory).ok().and_then(|directory| {
            let package = self.index.packages.get(&directory).copied();
            if package.is_none() && !["base:builtin", "base:intrinsics"].contains(&path) {
                self.missing_packages.borrow_mut().insert(directory);
            }
            package
        });
        self.import_packages.borrow_mut().insert(key, package);
        package
        };
        let found=package.and_then(|package|self.index.package_symbols[package].get(name)
            .and_then(|symbols|symbols.iter().find(|id|self.active_definition(self.symbols[**id].reference))).copied());
        if found.is_none() {
            if self.track_import_demands.get() && !["base:builtin", "base:intrinsics"].contains(&path) {
                if let Some(package)=package {
                    if let Some((directory,_))=self.index.packages.iter().find(|(_,id)|**id==package){
                        self.missing_packages.borrow_mut().insert(directory.clone());
                    }
                }
                let source = &self.sources[file];
                let owner = site.and_then(|site| {
                    std::iter::once(site.node).chain(source.ancestors(site.node))
                        .find(|id| source.node(*id).kind == "Procedure")
                        .or_else(|| std::iter::once(site.node).chain(source.ancestors(site.node))
                            .find(|id| source.node(*id).kind == "Declaration"))
                        .map(|node| Ref {file,node})
                });
                self.missing_symbols.borrow_mut().insert(ImportDemand {
                    file, path: path.into(), name: name.into(), owner,
                });
            }
        }
        found
    }
    pub(crate) fn demanded_packages(&self) -> BTreeSet<std::path::PathBuf> {
        self.missing_packages.borrow().clone()
    }
    pub(crate) fn demanded_symbols(&self) -> Vec<ImportDemand> {
        self.missing_symbols.borrow().iter().cloned().collect()
    }
    pub(crate) fn track_import_demands(&self, enabled: bool) -> bool {
        self.track_import_demands.replace(enabled)
    }
    pub(crate) fn has_demanded_packages(&self) -> bool {
        !self.missing_packages.borrow().is_empty()
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
            self.incomplete_plans.set(self.incomplete_plans.get().wrapping_add(1));
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
                _ => self.native_boolean(r).or_else(|| self.symbol(r).and_then(|id| {
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
                })),
            },
            "Unary" if n.op == "!" => self.condition(role("expr")?).map(|x| !x),
            "Call" => {
                let callee = role("callee")?;
                if s.text(callee.node) != "#config" {
                    return None;
                }
                let [name, default] = s.group(r.node, "args") else {
                    return None;
                };
                if self.build_define(s.text(*name)).is_none() {
                    return self.condition(Ref { node: *default, ..r });
                }
                match self.constant_value(r, &Substitutions::new(), None) {
                    Some(Constant::Boolean(value)) => Some(value),
                    _ => None,
                }
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
                match self.constant_value(r, &Substitutions::new(), None) {
                    Some(Constant::Boolean(value)) => Some(value),
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
    pub(crate) fn logical_type(&self, r: Ref) -> Option<Type> {
        let source = &self.sources[r.file];
        let name = if let Some(index) = self.symbol(r) {
            let symbol = &self.symbols[index];
            if !matches!(symbol.name.as_str(), "Integer" | "Rational") {
                return None;
            }
            let owner = &self.sources[symbol.reference.file];
            let expected = self.runtime_root().join("big/big.odin");
            if owner.path != expected {
                return None;
            }
            symbol.name.as_str()
        } else if source.thor && self.logical_type_position(r) {
            source.text(r.node)
        } else {
            return None;
        };
        match name {
            "Integer" => Some(Type::BigInteger),
            "Rational" => Some(Type::BigRational),
            _ => None,
        }
    }
    pub(crate) fn logical_result(&mut self, r: Ref, index: usize) -> Option<Type> {
        if let Some(typ)=self.logical_numeric_result(r){
            if index==0{return Some(typ);}
            if index==1&&crate::extremaplan::plan(self,r).is_some_and(|plan|plan.optional){return Some(Type::scalar("bool"));}
        }
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
        if index == 0 {
            return Some(typ);
        }
        if index == 1 && source.group(r.node, "args").iter().any(|arg| {
            let node = source.role(*arg, "value").unwrap_or(*arg);
            self.type_of(Ref { file: r.file, node }).is_string()
        }) {
            return Some(Type::scalar("bool"));
        }
        None
    }
    pub fn primitive(&self, r: Ref) -> Option<Type> {
        let source = &self.sources[r.file];
        let name = match source.node(r.node).kind.as_str() {
            "Ident" => source.text(r.node),
            "Selector" => source.text(source.role(r.node, "field")?),
            _ => return None,
        };
        let typ = if matches!(name, "string" | "cstring" | "string16" | "cstring16") {
            Type::String { name: name.into(), untyped: false }
        } else if matches!(name,
            "bool" | "b8" | "b16" | "b32" | "b64" | "int" | "uint" | "uintptr"
            | "rawptr" | "any" | "rune" | "byte" | "i8" | "i16" | "i32" | "i64"
            | "i128" | "u8" | "u16" | "u32" | "u64" | "u128" | "f16" | "f32"
            | "f64" | "complex32" | "complex64" | "complex128" | "quaternion64"
            | "quaternion128" | "quaternion256")
        {
            Type::scalar(name)
        } else {
            return None;
        };
        // Most expression names cannot denote a primitive. Only resolve names
        // after the spelling matches, retaining ordinary lexical shadowing.
        if source.node(r.node).kind == "Ident" {
            if self.symbol(r).is_some() { return None; }
        } else {
            let owner = source.role(r.node, "expr")?;
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
            let native = self.namespace_imports(r.file, source.text(owner)).iter()
                .any(|path| path == "base:builtin");
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
        }
        Some(typ)
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
                self.canonical_path(&actual),
                self.canonical_path(&expected),
            ) {
                (Some(actual), Some(expected)) => actual == expected,
                _ => false,
            }
        })
    }
    pub(crate) fn canonical_path(&self, path: &std::path::Path) -> Option<std::path::PathBuf> {
        if let Some(cached) = self.canonical_paths.borrow().get(path) {
            return cached.clone();
        }
        crate::profile::count("lookup.canonical_path_resolutions", 1);
        let canonical = crate::project::canonicalize(path).ok();
        self.canonical_paths.borrow_mut().insert(path.to_path_buf(), canonical.clone());
        canonical
    }
    pub(crate) fn namespace_imports(&self, file: usize, name: &str) -> &[String] {
        self.index.imports[file].get(name).map(Vec::as_slice).unwrap_or_default()
    }
    pub(crate) fn expression_reference(&self, reference: Ref) -> bool {
        self.index.expression_names[reference.file].binary_search(&reference.node).is_ok()
    }
    pub fn native_builtin(&self, reference: Ref, name: &str) -> bool {
        let source = &self.sources[reference.file];
        source.node(reference.node).kind == "Ident"
            && source.text(reference.node) == name
            && self.symbol(reference).is_none()
            && !self.import_shadows_builtin(reference.file, name)
    }
    pub(crate) fn native_builtin_alias(&self, mut reference: Ref, name: &str) -> bool {
        let mut seen = HashSet::new();
        while seen.insert(reference) {
            if self.native_builtin(reference, name) { return true; }
            let source = &self.sources[reference.file];
            if source.node(reference.node).kind == "Paren" {
                let Some(node) = source.role(reference.node, "expr") else { return false; };
                reference.node = node;
                continue;
            }
            let Some(symbol) = self.symbol(reference).map(|index| &self.symbols[index]) else {
                return self.standard_library(reference, "base:builtin")
                    && self.library_alias_member(reference, "base:builtin").as_deref() == Some(name);
            };
            if !symbol.constant || symbol.immutable { return false; }
            let Some(node) = symbol.value else { return false; };
            reference = Ref { file: symbol.reference.file, node };
            if !matches!(self.sources[reference.file].node(node).kind.as_str(), "Ident" | "Selector" | "Paren") {
                return false;
            }
        }
        false
    }
    fn import_shadows_builtin(&self, file: usize, name: &str) -> bool {
        let source = &self.sources[file];
        source.selections.iter().any(|selection| {
            selection.names.iter().any(|selected| selected == name)
        }) || self.index.imports[file].contains_key(name)
    }
    pub(crate) fn generated_builtin_shadowed(&self, file: usize, name: &str) -> bool {
        let source = &self.sources[file];
        // A generated helper can move between lexical scopes within this file.
        // Keep qualification if any of those scopes might supply the name.
        self.symbols.iter().any(|symbol| {
            symbol.name == name
                && (symbol.reference.file == file
                    || symbol.scope == NodeId(0)
                        && self.sources[symbol.reference.file].path.parent() == source.path.parent())
        }) || self.import_shadows_builtin(file, name)
            || source.selections.iter().any(|selection| selection.wildcard)
            || source.nodes.iter().any(|node| node.kind == "Using" || node.data["using_field"] == true)
    }
    pub fn fields(&self, reference: Ref, role: &str) -> Vec<(String, Option<Ref>)> {
        if matches!(role, "params" | "results") && self.index.signatures.contains_key(&reference) {
            let layout = self.signature_layout(reference);
            return if role == "params" { layout.params.clone() } else { layout.results.clone() };
        }
        signaturelayout::collect_fields(self.sources, reference, role)
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
    pub(crate) fn file_private_declaration(&self, declaration: Ref) -> bool {
        let source = &self.sources[declaration.file];
        self.attributes(declaration).iter().any(|(name,value)| name == "private"
            && value.is_some_and(|value| crate::lower::unquote(self.sources[value.file].text(value.node))
                .ok().as_deref() == Some("file")))
            || source.info["file_tags"].as_array().into_iter().flatten().filter_map(|tag|tag.as_str())
                .any(|tag| tag.contains("private") && tag.split_whitespace().any(|word|word == "file"))
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
        if crate::resulttypes::is_query(self,reference) {
            if let Some(call)=crate::resulttypes::expression(self,reference) {
                if let Some(results)=crate::resulttypes::resolve(self,call,&Substitutions::new(),None) {
                    return crate::resulttypes::packed_type(&results);
                }
            }
            return Type::Unknown;
        }
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
        if let Some(typ) = self.types.get(&reference)
            .or_else(|| self.base_types.as_ref()?.get(&reference)) {
            return typ.clone();
        }
        if !self.active.insert(reference) {
            self.incomplete_plans.set(self.incomplete_plans.get().wrapping_add(1));
            return Type::Unknown;
        }
        let typ = self.infer(reference);
        self.active.remove(&reference);
        if !typ.is_unknown() {
            self.types.insert(reference, typ.clone());
        }
        typ
    }
    pub(crate) fn adopt_closed_type(&mut self, reference: Ref, typ: Type) {
        self.types.insert(reference, typ);
    }
    fn infer(&mut self, r: Ref) -> Type {
        if let Some(typ)=self.logical_constant_type(r){return typ;}
        if let Some(typ) = self.logical_type(r) {
            return typ;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let child = |name: &str| s.role(r.node, name).map(|node| Ref { file: r.file, node });
        match n.kind.as_str() {
            "Directive" if s.text(r.node)=="#caller_location" => self
                .package_symbol(r,"base:runtime","Source_Code_Location")
                .and_then(|i|self.symbols[i].value.map(|node|Ref{file:self.symbols[i].reference.file,node}))
                .map(|typ|self.type_of(typ)).unwrap_or(Type::Unknown),
            "Implicit" if s.text(r.node) == "context" => self
                .package_symbol(r, "base:runtime", "Context")
                .and_then(|symbol| self.symbols[symbol].value.map(|node| Ref {
                    file: self.symbols[symbol].reference.file, node,
                }))
                .map(|declaration| self.type_of(declaration))
                .unwrap_or(Type::Unknown),
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
                // Maybe is an implicit Odin runtime builtin, unless a lexical
                // declaration above has already supplied its type.
                if text == "Maybe" {
                    return self.package_symbol(r, "base:runtime", text)
                        .and_then(|symbol| self.symbols[symbol].value.map(|node| Ref {
                            file: self.symbols[symbol].reference.file, node,
                        }))
                        .map(|declaration| self.type_of(declaration))
                        .unwrap_or(Type::Unknown);
                }
                if text == "true" || text == "false" {
                    return Type::Scalar {
                        name: "bool".into(),
                        untyped: true,
                    };
                }
                if let Some(primitive) = self.primitive(r) {
                    return primitive;
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
            "CaseComparison" => Type::scalar("bool"),
            "HelperType"=>{let mut typ=child("expr").map(|v|self.type_of(v)).unwrap_or(Type::Unknown);if let Type::Matrix{row_major,..}=&mut typ{if ["row_major","column_major"].contains(&n.op.as_str()){*row_major=n.op=="row_major";}}typ},
            "Paren" | "AutoCast" | "Unary" | "Run" | "Runtime" => {
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
            "Deref" => child("expr")
                .map(|r| self.type_of(r))
                .and_then(|t| t.element().cloned())
                .unwrap_or(Type::Unknown),
            "Index" => child("expr").map(|operand|{
                let typ=self.type_of(operand);self.index_type(operand,typ)
            }).unwrap_or(Type::Unknown),
            "Slice" => {
                let typ = child("expr")
                    .map(|r| self.type_of(r))
                    .unwrap_or(Type::Unknown);
                child("expr").map(|operand|self.slice_type(operand,typ)).unwrap_or(Type::Unknown)
            }
            "MatrixType"=>Type::Matrix{row_major:false,elem:Box::new(child("elem").map(|v|self.type_of(v)).unwrap_or(Type::Unknown)),
                rows:child("rows").and_then(|v|self.integer(v,&mut HashSet::new())),columns:child("columns").and_then(|v|self.integer(v,&mut HashSet::new()))},
            "MatrixIndex"=>child("expr").map(|v|{let typ=self.type_of(v);self.index_type(v,typ)}).unwrap_or(Type::Unknown),
            "ArrayType" => {
                let elem = Box::new(
                    child("elem")
                        .map(|r| self.type_of(r))
                        .unwrap_or(Type::Unknown),
                );
                if let Some(len) = child("len") {
                    if n.op=="simd" {Type::Vector{elem,length:self.constant_int(len)}}else{Type::Array {
                        elem,
                        length: self.constant_int(len),
                    }}
                } else if n.op == "soa" {
                    Type::Soa(elem)
                } else {
                    Type::Slice(elem)
                }
            }
            // Variadic parameters are borrowed slices inside the procedure.
            // Keep ordinary type queries consistent with specialization so
            // iteration and indexing retain the declared element type.
            "Ellipsis" => Type::Slice(Box::new(
                child("expr")
                    .map(|r| self.type_of(r))
                    .unwrap_or(Type::Unknown),
            )),
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
            "PointerType" | "MultiPointerType" => Type::Pointer(Box::new(
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
            "StructType" | "BitFieldType" => Type::Struct(r),
            "BitSetType" => Type::Nominal(r, Box::new(Type::scalar("bit_set"))),
            "DistinctType" => Type::Nominal(
                r,
                Box::new(
                    child("type")
                        .map(|r| self.type_of(r))
                        .unwrap_or(Type::Unknown),
                ),
            ),
            "UnionType" => Type::Union(r, vec![]),
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
                if n.op == "++" {
                    return crate::lower::concatenation::result_type(s, r.node, |node| {
                        self.type_of(Ref { node, ..r })
                    });
                }
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
                if n.op == "*" && b.is_string() {
                    return b;
                }
                if let Some(typ)=typefacts::arithmetic_shape(&n.op,&a,&b){return typ;}
                if s.thor && a.is_unknown() && b.record_like() || a.untyped() && !b.untyped() {
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
            "SwitchExpr" | "CompileSwitchExpr" | "OrPanic" | "OrReturn" | "OrBranch" => {
                self.result_type(r, 0, &mut HashSet::new())
            }
            "Call" => {
                if let Some(typ)=self.logical_numeric_result(r){return typ;}
                if let Some(typ)=self.builtin_result_type(r,0,&Substitutions::new()){return typ;}
                let Some(callee) = child("callee") else {
                    return Type::Unknown;
                };
                let name = s.text(callee.node);
                if s.node(callee.node).kind == "Directive" && name == "#config" {
                    if let [_, default] = s.group(r.node, "args") {
                        return self.type_of(Ref { node: *default, ..r });
                    }
                }
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
                    return Type::Scalar{name:if name=="offset_of"{"uintptr"}else{"int"}.into(),untyped:matches!(name,"size_of"|"align_of")};
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
                    Type::Struct(_) | Type::Record(..) | Type::Union(..) => {
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
    pub(crate) fn result_type(&mut self, r: Ref, index: usize, seen: &mut HashSet<Ref>) -> Type {
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
    pub fn runtime_operand(&mut self, r: Ref) -> bool {
        self.runtime(r, &mut HashSet::new())
    }
    /// Conclusive type expressions for lazy value-use diagnostics. Approximate
    /// shapes, generic parameters and competing definitions remain Odin's job.
    pub fn known_type_operand(&self,r:Ref)->bool {
        self.known_type_seen(r,&mut HashSet::new())
    }
    fn known_type_seen(&self,r:Ref,seen:&mut HashSet<Ref>)->bool {
        if crate::resulttypes::is_query(self,r) {return true;}
        if !seen.insert(r){return false;}
        let source=&self.sources[r.file];let node=source.node(r.node);
        if node.kind=="Paren" {return source.role(r.node,"expr").is_some_and(|node|self.known_type_seen(Ref{file:r.file,node},seen));}
        if node.kind.ends_with("Type"){return node.kind!="PolyType";}
        if let Some(index)=self.symbol(r) {
            let symbol=&self.symbols[index];let owner=&self.sources[symbol.reference.file];
            if symbol.parameter||symbol.result||!symbol.constant||symbol.immutable{return false;}
            if self.scopes.get(&Ref { file: symbol.reference.file, node: symbol.scope }).and_then(|names|names.get(&symbol.name)).is_some_and(|symbols|symbols.len()>1){return false;}
            return owner.node(symbol.reference.node).kind=="Declaration"&&symbol.value.is_some_and(|node|self.known_type_seen(Ref{file:symbol.reference.file,node},seen));
        }
        if node.kind=="Call" {return source.role(r.node,"callee").is_some_and(|node|self.native_builtin(Ref{file:r.file,node},"type_of"));}
        node.kind=="Ident"&&self.denotes_type(r)
    }
    pub fn denotes_type(&self, r: Ref) -> bool {
        self.type_operand(r, &mut HashSet::new())
    }
    fn type_operand(&self, r: Ref, seen: &mut HashSet<Ref>) -> bool {
        if crate::resulttypes::is_query(self,r) {return true;}
        if !seen.insert(r) {
            return false;
        }
        if self.primitive(r).is_some() || self.logical_type(r).is_some() {
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
                    s.node(parent).kind == "ArrayType" && s.role(parent, "len") == Some(symbol.reference.node)
                        || s.node(parent).kind=="MatrixType" && ["rows","columns"].iter().any(|role|s.role(parent,role)==Some(symbol.reference.node))
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
    fn runtime(&mut self, r: Ref, seen: &mut HashSet<Ref>) -> bool {
        if !seen.insert(r) {
            return false;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        if self.logical_constants.contains_key(&r) || self.evaluated.contains_key(&r)
            || matches!(n.kind.as_str(), "Run" | "Literal")
            || n.kind.ends_with("Type")
        {
            return false;
        }
        if n.kind == "Selector" {
            return s
                .role(r.node, "expr")
                .is_some_and(|node| self.runtime(Ref { file: r.file, node }, seen));
        }
        if let Some(id) = self.symbol(r) {
            let symbol = &self.symbols[id];
            let owner = &self.sources[symbol.reference.file];
            let declaration = owner.node(symbol.reference.node);
            if matches!(declaration.kind.as_str(), "Range" | "TypeSwitch") {
                return true;
            }
            if declaration.kind == "Field" {
                return !owner
                    .group(symbol.reference.node, "names")
                    .iter()
                    .any(|name| owner.node(*name).kind == "PolyType");
            }
            if declaration.kind == "Declaration" && (declaration.mutable || declaration.immutable) {
                return true;
            }
        }
        if n.kind == "Call" {
            if let Some(callee) = s.role(r.node, "callee") {
                let callee = Ref {
                    file: r.file,
                    node: callee,
                };
                if crate::purity::builtin_call(self, r).is_none() && !self.denotes_type(callee) {
                    return true;
                }
            }
            return s.group(r.node, "args").iter().any(|node| {
                self.runtime(
                    Ref {
                        file: r.file,
                        node: *node,
                    },
                    &mut seen.clone(),
                )
            });
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

#[cfg(test)]
mod index_tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn record_layouts_are_shared_but_owned_results_and_new_epochs_are_independent() {
        let mut resolver = Resolver::new(&[]);
        let typ = Type::Anonymous(vec![("value".into(), Type::scalar("int"))]);
        let first = resolver.shared_record_fields(&typ);
        let second = resolver.shared_record_fields(&typ);
        if !Arc::ptr_eq(&first, &second) { panic!("unchanged layout was copied"); }
        let mut owned = resolver.record_fields(&typ);
        owned[0].1 = Type::scalar("f64");
        if resolver.shared_record_fields(&typ)[0].1 != Type::scalar("int") {
            panic!("owned layout mutation reached the cache");
        }
        resolver.remember_evaluation(Ref { file:0, node:NodeId(0) }, json!(1));
        let next = resolver.shared_record_fields(&typ);
        if Arc::ptr_eq(&first, &next) || first.as_ref() != next.as_ref() {
            panic!("a new semantic epoch retained the old layout cache");
        }
    }
    #[test]
    fn pending_imports_do_not_disable_fixed_input_symbol_queries_or_cross_resolvers() {
        let source = Source::from_frontend("/example.thor".into(), "missing".into(), json!({
            "nodes":[{"kind":"File","start":0,"end":7,"parent":-1},
                     {"kind":"Ident","start":0,"end":7,"parent":0,"name":"missing"}],
            "features":[],"edits":[],"selections":[]
        }), true).unwrap();
        let sources = vec![source];
        let resolver = Resolver::new(&sources);
        let site = Ref { file:0, node:NodeId(1) };
        resolver.missing_packages.borrow_mut().insert("/pending".into());
        resolver.missing_symbols.borrow_mut().insert(ImportDemand {
            file: 0, path: "pending".into(), name: "VALUE".into(), owner: Some(site),
        });
        let outer = resolver.begin_symbol_queries();
        let first = resolver.symbol(site);
        let nested = resolver.begin_symbol_queries();
        let second = resolver.symbol(site);
        resolver.end_symbol_queries(nested);
        let retained = resolver.symbol_queries.borrow().as_ref().is_some_and(|queries| queries.contains_key(&site));
        if first.is_some() || second.is_some() || !retained || !resolver.has_demanded_packages() {
            panic!("fixed-input negative query or pending dependency was lost");
        }
        resolver.end_symbol_queries(outer);
        let next = Resolver::with_index(&sources, &[], Some(&resolver.syntax_index()));
        if next.symbol_queries.borrow().is_some() || next.has_demanded_packages() || !next.demanded_symbols().is_empty() {
            panic!("semantic queries leaked into the next source graph");
        }
    }
    #[test]
    fn metadata_is_invalidated_by_bindings_evaluations_and_context_preparation() {
        let mut resolver = Resolver::new(&[]);
        let site = Ref { file: 0, node: NodeId(0) };
        for operation in 0..4 {
            let id = resolver.type_id(&Type::Unknown);
            let memo = resolver.metadata.as_mut().unwrap();
            memo.fields.insert(id, vec![("stale".into(), Type::Unknown)].into());
            memo.members.entry(id).or_default().insert("field".into(), Type::Unknown);
            memo.paths.entry(id).or_default().insert("field".into(), None);
            memo.specialized.entry(site).or_default().insert(Substitutions::new(), Type::Unknown);
            memo.calls.entry(site).or_default().insert(Substitutions::new(), vec![CallPlan {
                procedure:site, signature:site, arguments:vec![], bindings:Substitutions::new(),
            }]);
            memo.local_calls.entry(site).or_default().insert((Substitutions::new(),false),vec![]);
            memo.local_call_count=1;
            match operation {
                0 => resolver.remember_evaluation(site, json!(1)),
                1 => resolver.remember_logical_constant(site, json!(2)),
                2 => resolver.set_lexical_bindings([((site, "T".into()), Type::scalar("int"))].into(), BTreeMap::new()),
                _ => { resolver.set_evaluations(BTreeMap::new()); resolver.prepare_contexts(); }
            }
            let memo = resolver.metadata.as_ref().unwrap();
            if !memo.fields.is_empty() || !memo.members.is_empty() || !memo.paths.is_empty()
                || !memo.specialized.is_empty() || !memo.calls.is_empty()
                || !memo.local_calls.is_empty() || memo.local_call_count!=0 { panic!("stale metadata after operation {operation}"); }
        }
    }
    #[test]
    fn graph_local_call_answers_do_not_escape_discovery_or_recursion_guards() {
        let raw="missing()";
        let source=Source::from_frontend("/example.thor".into(),raw.into(),json!({
            "nodes":[{"kind":"File","start":0,"end":9,"parent":-1},
                {"kind":"Call","start":0,"end":9,"parent":0,"roles":{"callee":[0,7]}},
                {"kind":"Ident","start":0,"end":7,"parent":1,"name":"missing"}],
            "features":[],"edits":[],"selections":[]
        }),true).unwrap();
        let sources=vec![source];let call=Ref{file:0,node:NodeId(1)};
        let mut resolver=Resolver::new(&sources);
        resolver.planning.insert((call,vec![]));
        let curtailed=resolver.call_plans(call,&Substitutions::new());
        if !curtailed.is_empty() || resolver.metadata.as_ref().unwrap().local_call_count!=0 {
            panic!("recursive cutoff entered the memo");
        }
        resolver.planning.clear();
        let answer=resolver.call_plans(call,&Substitutions::new());
        let closed=resolver.take_closed_calls();
        if !answer.is_empty() || !closed.is_empty() || resolver.metadata.as_ref().unwrap().local_call_count!=1 {
            panic!("graph-local answer was lost or exported as closed");
        }
        resolver.track_import_demands(true);
        let tracked=resolver.call_plans(call,&Substitutions::new());
        if !tracked.is_empty() || resolver.metadata.as_ref().unwrap().local_call_count!=2 {
            panic!("untracked answer suppressed a demand-tracking query");
        }
    }
    #[test]
    fn shared_indexes_do_not_cache_flag_selected_definitions() {
        let raw = "when ODIN_DEBUG {N::1}else{N::2};N";
        let a = raw.find("N::1").unwrap();
        let b = raw.find("N::2").unwrap();
        let end = raw.len();
        let source = Source::from_frontend("/example.thor".into(), raw.into(), json!({
            "nodes":[
                {"kind":"File","start":0,"end":end,"parent":-1},
                {"kind":"When","start":0,"end":end-2,"parent":0,
                 "roles":{"cond":[5,15],"body":[a-1,a+5],"else":[b-1,b+5]}},
                {"kind":"Ident","name":"ODIN_DEBUG","start":5,"end":15,"parent":1},
                {"kind":"Block","start":a-1,"end":a+5,"parent":1},
                {"kind":"Declaration","mutable":false,"start":a,"end":a+4,"parent":3,
                 "lists":{"names":[[a,a+1]],"values":[[a+3,a+4]]}},
                {"kind":"Ident","name":"N","start":a,"end":a+1,"parent":4},
                {"kind":"Literal","op":"integer","start":a+3,"end":a+4,"parent":4},
                {"kind":"Block","start":b-1,"end":b+5,"parent":1},
                {"kind":"Declaration","mutable":false,"start":b,"end":b+4,"parent":7,
                 "lists":{"names":[[b,b+1]],"values":[[b+3,b+4]]}},
                {"kind":"Ident","name":"N","start":b,"end":b+1,"parent":8},
                {"kind":"Literal","op":"integer","start":b+3,"end":b+4,"parent":8},
                {"kind":"Ident","name":"N","start":end-1,"end":end,"parent":0}
            ],"features":[],"edits":[],"selections":[]
        }), true).expect("valid conditional syntax");
        let sources = vec![source];
        let site = Ref { file:0, node:NodeId(11) };
        let flags = vec!["-debug".into()];
        let debug = Resolver::with_flags(&sources, &flags);
        let index = debug.syntax_index();
        let release = Resolver::with_index(&sources, &[], Some(&index));
        let restored = Resolver::with_index(&sources, &flags, Some(&index));
        let values = (debug.constant_int(site), release.constant_int(site), restored.constant_int(site));
        if values != (Some(1), Some(2), Some(1)) { panic!("shared index selected stale declarations: {values:?}"); }
    }
}
