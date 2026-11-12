//! A completed resolver snapshot. Workers share immutable answers and allocate
//! only their own query deltas; no RefCell or inference stack crosses threads.
use super::*;

pub(crate) struct ResolverSeed<'a> {
    sources: &'a [Source],
    symbols: Arc<Vec<Symbol>>,
    scopes: Arc<ScopeTable>,
    index: Arc<namescope::NameIndex>,
    syntax: Option<ResolverIndex>,
    imports: HashMap<(usize, String), Option<usize>>,
    paths: HashMap<std::path::PathBuf, Option<std::path::PathBuf>>,
    types: Arc<NodeTable<Type>>,
    metadata: Option<Arc<Metadata>>,
    pool: Arc<typepool::TypePool>,
    queries: Option<Arc<HashMap<Ref, Option<usize>>>>,
    context_hints: HashMap<Ref, ContextHint>,
    parameter_witnesses: BTreeMap<Ref, ParameterWitness>,
    markers: Arc<BTreeMap<(Ref, String), String>>,
    flags: Vec<String>,
    evaluated: BTreeMap<Ref, serde_json::Value>,
    logical_constants: BTreeMap<Ref, serde_json::Value>,
    logical_activation: Option<bool>,
    bound_types: BTreeMap<(Ref, String), Type>,
    bound_constants: BTreeMap<(Ref, String), (Type, serde_json::Value)>,
    using_symbols: HashMap<Ref, usize>,
}
impl<'a> Resolver<'a> {
    pub(crate) fn can_freeze(&self) -> bool {
        self.active.is_empty()
            && self.planning.is_empty()
            && self.conditions.borrow().is_empty()
            && !self.has_demanded_packages()
            && self.base_types.is_none()
            && self.base_metadata.is_none()
            && self.base_symbol_queries.is_none()
    }
    pub(crate) fn freeze(self) -> ResolverSeed<'a> {
        // The caller checks this before consuming the serial resolver. Never
        // publish partial queries, or recursively freeze worker-local deltas.
        if !self.can_freeze() {
            panic!("attempted to freeze incomplete resolver");
        }
        crate::profile::count("resolver.frozen_snapshots", 1);
        ResolverSeed {
            sources: self.sources,
            symbols: self.symbols,
            scopes: self.scopes,
            index: self.index,
            syntax: self.base,
            imports: self.import_packages.into_inner(),
            paths: self.canonical_paths.into_inner(),
            types: Arc::new(self.types),
            metadata: self.metadata.map(Arc::new),
            pool: Arc::new(self.type_pool.into_inner()),
            queries: self.symbol_queries.into_inner().map(Arc::new),
            context_hints: self.context_hints,
            parameter_witnesses: self.parameter_witnesses,
            markers: self.immutable_markers,
            flags: self.flags,
            evaluated: self.evaluated,
            logical_constants: self.logical_constants,
            logical_activation: self.logical_activation.get().copied(),
            bound_types: self.bound_types,
            bound_constants: self.bound_constants,
            using_symbols: self.using_symbols,
        }
    }
}
impl<'a> ResolverSeed<'a> {
    pub(crate) fn fork(&self) -> Resolver<'a> {
        crate::profile::count("resolver.lowering_workers", 1);
        Resolver {
            sources: self.sources,
            symbols: self.symbols.clone(),
            scopes: self.scopes.clone(),
            index: self.index.clone(),
            base: self.syntax.clone(),
            import_packages: RefCell::new(self.imports.clone()),
            canonical_paths: RefCell::new(self.paths.clone()),
            missing_packages: RefCell::new(BTreeSet::new()),
            missing_symbols: RefCell::new(BTreeSet::new()),
            track_import_demands: Cell::new(false),
            types: NodeTable::new(self.sources.iter().map(|source| source.nodes.len())),
            base_types: Some(self.types.clone()),
            active: HashSet::new(),
            planning: HashSet::new(),
            conditions: RefCell::new(HashSet::new()),
            context_hints: self.context_hints.clone(),
            parameter_witnesses: self.parameter_witnesses.clone(),
            immutable_markers: self.markers.clone(),
            flags: self.flags.clone(),
            evaluated: self.evaluated.clone(),
            logical_constants: self.logical_constants.clone(),
            logical_activation: self
                .logical_activation
                .map(std::cell::OnceCell::from)
                .unwrap_or_default(),
            bound_types: self.bound_types.clone(),
            bound_constants: self.bound_constants.clone(),
            using_symbols: self.using_symbols.clone(),
            symbol_queries: RefCell::new(self.queries.as_ref().map(|_| HashMap::new())),
            base_symbol_queries: self.queries.clone(),
            metadata: self.metadata.as_ref().map(|_| Metadata::default()),
            base_metadata: self.metadata.clone(),
            type_pool: RefCell::new(typepool::TypePool::fork(self.pool.clone())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn workers_share_completed_answers_and_invalidate_only_their_own_view() {
        let source = Source::from_frontend(
            "/example.thor".into(),
            "7".into(),
            json!({
                "nodes":[{"kind":"File","start":0,"end":1,"parent":-1},
                    {"kind":"Literal","op":"integer","start":0,"end":1,"parent":0}],
                "features":[],"edits":[],"selections":[]
            }),
            true,
        )
        .unwrap();
        let sources = vec![source];
        let site = Ref {
            file: 0,
            node: NodeId(1),
        };
        let typ = Type::Anonymous(vec![("field".into(), Type::scalar("int"))]);
        let mut parent = Resolver::new(&sources);
        parent.begin_symbol_queries();
        let before = parent.type_of(site);
        let fields = parent.shared_record_fields(&typ);
        let member = parent.member_type(&typ, "field");
        let path = parent.member_path(&typ, "field");
        parent.symbol(site);
        let seed = parent.freeze();
        let mut sibling = seed.fork();
        for operation in 0..4 {
            let mut worker = seed.fork();
            if worker.type_of(site) != before
                || worker.member_type(&typ, "field") != member
                || worker.member_path(&typ, "field") != path
                || !Arc::ptr_eq(&fields, &worker.shared_record_fields(&typ))
            {
                panic!("worker lost frozen query answers");
            }
            match operation {
                0 => worker.remember_evaluation(site, json!({"kind":"big_integer","value":"9"})),
                1 => worker.remember_logical_constant(
                    site,
                    json!({"kind":"big_rational","numerator":"2","denominator":"3"}),
                ),
                2 => worker.set_lexical_bindings(
                    [((site, "T".into()), Type::scalar("f64"))].into(),
                    BTreeMap::new(),
                ),
                _ => worker.prepare_contexts(),
            }
            if worker.base_types.is_some()
                || worker.base_metadata.is_some()
                || worker.base_symbol_queries.is_some()
                || Arc::ptr_eq(&fields, &worker.shared_record_fields(&typ))
                || !Arc::ptr_eq(&fields, &sibling.shared_record_fields(&typ))
                || sibling.type_of(site) != before
                || !seed.logical_constants.is_empty()
            {
                panic!("worker query invalidation crossed snapshot boundaries");
            }
            if operation == 0 && worker.type_of(site) != Type::BigInteger {
                panic!("evaluation invalidation kept the old inferred type");
            }
        }
    }
    #[test]
    fn frozen_snapshot_rejects_partial_or_worker_local_state() {
        let mut resolver = Resolver::new(&[]);
        resolver.active.insert(Ref {
            file: 0,
            node: NodeId(0),
        });
        if resolver.can_freeze() {
            panic!("active inference published");
        }
        resolver.active.clear();
        resolver
            .missing_packages
            .borrow_mut()
            .insert("/pending".into());
        if resolver.can_freeze() {
            panic!("pending dependency published");
        }
        resolver.missing_packages.borrow_mut().clear();
        let seed = resolver.freeze();
        if seed.fork().can_freeze() {
            panic!("worker deltas can recursively freeze");
        }
    }
}
