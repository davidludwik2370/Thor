//! Closed, completed abstract procedure graphs reused only across guarded
//! append-only namespace discovery. Partial graphs never enter this cache.
use super::*;
use std::sync::Arc;

#[derive(Clone)]
pub(super) enum FactEvent {
    Issue(Arc<Issue>),
    Readonly(Ref),
    Invalid(Ref, Arc<String>),
}
impl FactEvent {
    fn apply(&self, facts: &mut Facts) {
        match self {
            Self::Issue(issue) => {
                // A variadic/opaque call can reject several independent roots
                // at the same site. Preserve their declarations for diagnostics
                // and batched vet rejection, retaining the first issue's verdict.
                let issues = facts.issues.entry(issue.operation).or_default();
                if !issues.iter().any(|existing| existing.seed == issue.seed) {
                    issues.push((**issue).clone());
                }
            }
            Self::Readonly(site) => {
                facts.readonly.insert(*site);
            }
            Self::Invalid(site, message) => {
                facts.invalid.insert(*site, (**message).clone());
            }
        }
    }
}
#[derive(Clone, Default)]
pub(crate) struct SummaryCache {
    entries: HashMap<SummaryKey, Arc<Entry>>,
}
struct Entry {
    graph: GraphState,
    env: Vec<(Ref, V)>,
    named: Vec<Option<Ref>>,
    events: Arc<[FactEvent]>,
    children: Vec<(SummaryKey, Arc<Entry>)>,
}
impl<'a, 'r> Privileges<'a, 'r> {
    pub(super) fn fact_event(&mut self, event: FactEvent) {
        event.apply(&mut self.facts);
        if self.reuse.is_some() {
            self.fact_events.push(event);
        }
    }
    pub(super) fn observe_cache_input(&mut self, site: Ref, c: C) {
        if !self.contexts[c].cacheable {
            return;
        }
        let source = self.s(site);
        if source.node(site.node).kind == "Ident"
            && source.text(site.node) == "context"
            && self.r.symbol(site).is_none()
        {
            self.contexts[c].cacheable = false;
            return;
        }
        let Some(proc) = self.contexts[c].proc else {
            return;
        };
        let Some(symbol) = self.r.symbol(site).map(|id| &self.r.symbols[id]) else {
            return;
        };
        let declaration = symbol.reference;
        if declaration.file == proc.file && self.s(proc).is_descendant(declaration.node, proc.node)
        {
            return;
        }
        if self.kind(declaration) != "Declaration" {
            return;
        }
        let safe = symbol.constant
            && symbol.value.is_some_and(|value| {
                matches!(
                    self.r.sources[declaration.file].node(value).kind.as_str(),
                    "Literal"
                        | "Procedure"
                        | "ProcedureType"
                        | "StructType"
                        | "EnumType"
                        | "UnionType"
                )
            });
        if !safe {
            self.contexts[c].cacheable = false;
        }
    }
    pub(super) fn restore_summary(&mut self, key: &SummaryKey, caller: Option<C>) -> Option<C> {
        let entry = self.reuse.as_ref()?.entries.get(key)?.clone();
        if !self.validate_summary(key, &entry, &mut HashSet::new()) {
            return None;
        }
        let c = self.import_summary(key, &entry);
        if let Some(caller) = caller {
            self.contexts[caller].callees.insert(c);
            self.contexts[c].callers.insert(caller);
        }
        for event in entry.events.iter() {
            self.fact_event(event.clone());
        }
        self.contexts[c].event_end = self.fact_events.len();
        crate::profile::count("permissions.summary_hits", 1);
        Some(c)
    }
    fn validate_summary(
        &self,
        key: &SummaryKey,
        entry: &Entry,
        seen: &mut HashSet<SummaryKey>,
    ) -> bool {
        if !seen.insert(key.clone()) {
            return true;
        }
        if self
            .summaries
            .get(key)
            .is_some_and(|c| self.contexts[*c].active)
        {
            return false;
        }
        entry
            .env
            .iter()
            .all(|(name, _)| self.r.symbol(*name).is_some())
            && entry
                .named
                .iter()
                .flatten()
                .all(|name| self.r.symbol(*name).is_some())
            && entry
                .children
                .iter()
                .all(|(key, entry)| self.validate_summary(key, entry, seen))
    }
    fn import_summary(&mut self, key: &SummaryKey, entry: &Entry) -> C {
        if let Some(c) = self.summaries.get(key) {
            return *c;
        }
        let c = self.contexts.len();
        self.contexts.push(Context::new(
            Some(key.procedure),
            key.bindings.iter().cloned().collect(),
        ));
        self.summaries.insert(key.clone(), c);
        let children = entry
            .children
            .iter()
            .map(|(key, entry)| self.import_summary(key, entry))
            .collect::<BTreeSet<_>>();
        let offset = self.values.len();
        for value in &entry.graph.values {
            let mut restored = Value::new(self.r.shared_type(value.typ.owned()));
            restored.data = value.data.clone();
            restored.storage = value.storage.clone();
            restored.locations = value.locations.clone();
            restored.path = value.path.clone();
            restored.fields = value
                .fields
                .iter()
                .map(|(name, v)| (name.clone(), offset + v))
                .collect();
            [restored.element, restored.target, restored.base] =
                value.children.map(|v| v.map(|v| offset + v));
            self.values.push(restored);
        }
        let mut context = Context::new(Some(key.procedure), key.bindings.iter().cloned().collect());
        context.checked = key.checked;
        context.complete = true;
        context.event_start = self.fact_events.len();
        context.cached_events = Some(entry.events.clone());
        context.env = entry
            .env
            .iter()
            .map(|(name, v)| {
                (
                    self.r.symbol(*name).expect("validated summary symbol"),
                    offset + v,
                )
            })
            .collect();
        context.named = entry
            .named
            .iter()
            .map(|name| name.map(|name| self.r.symbol(name).expect("validated result symbol")))
            .collect();
        context.results = entry.graph.results.iter().map(|v| offset + v).collect();
        context.stores = entry
            .graph
            .stores
            .iter()
            .map(|(path, v)| (path.clone(), offset + v))
            .collect();
        context.effects = entry.graph.effects.iter().cloned().collect();
        context.callees = children;
        self.contexts[c] = context;
        for child in self.contexts[c].callees.clone() {
            self.contexts[child].callers.insert(c);
        }
        c
    }
    pub(super) fn harvest(&self, output: &mut SummaryCache) {
        let keys = self
            .summaries
            .iter()
            .map(|(key, c)| (*c, key.clone()))
            .collect::<HashMap<_, _>>();
        let mut memo = HashMap::new();
        let mut budget = self
            .r
            .sources
            .iter()
            .map(|source| source.nodes.len())
            .sum::<usize>()
            .clamp(4096, 50_000);
        for c in 0..self.contexts.len() {
            if let Some(entry) =
                self.snapshot_summary(c, &keys, &mut memo, &mut HashSet::new(), &mut budget)
            {
                if let Some(key) = keys.get(&c) {
                    output.entries.insert(key.clone(), entry);
                }
            }
        }
        crate::profile::count("permissions.summary_entries", output.entries.len() as u64);
    }
    fn snapshot_summary(
        &self,
        c: C,
        keys: &HashMap<C, SummaryKey>,
        memo: &mut HashMap<C, Option<Arc<Entry>>>,
        visiting: &mut HashSet<C>,
        budget: &mut usize,
    ) -> Option<Arc<Entry>> {
        if let Some(entry) = memo.get(&c) {
            return entry.clone();
        }
        let context = &self.contexts[c];
        if !context.complete
            || !context.cacheable
            || !keys.contains_key(&c)
            || context
                .effects
                .values()
                .any(|effect| effect.unresolved.is_some())
            || !visiting.insert(c)
        {
            return None;
        }
        let result = (|| {
            if context
                .env
                .keys()
                .any(|id| self.r.symbols[*id].projection.is_some())
            {
                return None;
            }
            let mut children = vec![];
            for child in &context.callees {
                children.push((
                    keys.get(child)?.clone(),
                    self.snapshot_summary(*child, keys, memo, visiting, budget)?,
                ));
            }
            let events = context
                .cached_events
                .as_deref()
                .or_else(|| self.fact_events.get(context.event_start..context.event_end))?;
            let (graph, graph_cost) =
                self.cache_graph(c, budget.saturating_sub(events.len() + 1))?;
            if graph.values.iter().any(|value| !value.typ.resolved()) {
                return None;
            }
            let cost = graph_cost + events.len() + 1;
            if cost > *budget {
                return None;
            }
            *budget -= cost;
            let env = graph
                .roots
                .iter()
                .map(|(symbol, v)| {
                    let symbol = &self.r.symbols[*symbol];
                    (
                        Ref {
                            file: symbol.reference.file,
                            node: symbol.name_node,
                        },
                        *v,
                    )
                })
                .collect();
            let named = context
                .named
                .iter()
                .map(|symbol| {
                    symbol.map(|id| {
                        let symbol = &self.r.symbols[id];
                        Ref {
                            file: symbol.reference.file,
                            node: symbol.name_node,
                        }
                    })
                })
                .collect();
            Some(Arc::new(Entry {
                graph,
                env,
                named,
                events: events.to_vec().into(),
                children,
            }))
        })();
        visiting.remove(&c);
        memo.insert(c, result.clone());
        result
    }
    fn cache_graph(&self, c: C, limit: usize) -> Option<(GraphState, usize)> {
        let context = &self.contexts[c];
        if context.env.len() + context.results.len() + context.stores.len() + context.effects.len()
            > limit
        {
            return None;
        }
        let (mut graph, mut ids, mut queue) = self.state_roots(c);
        let mut remaining = limit;
        let mut cursor = 0;
        while cursor < queue.len() {
            let value = &self.values[queue[cursor]];
            let cost = 1
                + value.fields.len()
                + value.data.len()
                + value.storage.len()
                + value.locations.len();
            if cost > remaining {
                return None;
            }
            remaining -= cost;
            cursor += 1;
            let fields = value
                .fields
                .iter()
                .map(|(name, v)| (name.clone(), Self::register(*v, &mut ids, &mut queue)))
                .collect();
            let children = [value.element, value.target, value.base]
                .map(|v| v.map(|v| Self::register(v, &mut ids, &mut queue)));
            if queue.len() > limit {
                return None;
            }
            graph.values.push(GraphValue {
                typ: value.typ.clone(),
                data: value.data.clone(),
                storage: value.storage.clone(),
                locations: value.locations.clone(),
                path: value.path.clone(),
                fields,
                children,
            });
        }
        crate::profile::count(
            "permissions.summary_snapshot_values",
            graph.values.len() as u64,
        );
        Some((graph, limit - remaining))
    }
}
