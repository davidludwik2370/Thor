//! Discovery worklists survive append-only loading of new SDK namespaces.
//! Only closed positive type queries are retained. Record shapes and effects
//! are always checked again against the enlarged graph.
use crate::{ast::{NodeId, Source}, semantics::{Ref, Resolver, Type}};
use std::collections::{HashMap, HashSet};
use std::path::Path;

#[derive(Default)]
pub(super) struct Work {
    sources: Vec<Source>,
    queries: Vec<Ref>,
    complete: HashMap<Ref, Type>,
    pub roots: Vec<Ref>,
    pub requests: Vec<Ref>,
}
impl Work {
    fn extends(&self, sources: &[Source]) -> bool {
        let count = self.sources.len();
        if sources.len() < count || !self.sources.iter().zip(sources).all(|(old, current)|
            old.shares_syntax(current) && old.shared_native == current.shared_native
                && old.collection_roots == current.collection_roots) { return false; }
        let packages = self.sources.iter().filter_map(|source|source.path.parent()).collect::<HashSet<_>>();
        // New files in an existing namespace can extend overloads/definitions.
        // A new Thor file can also change logical-type activation globally.
        sources[count..].iter().all(|source| !source.thor
            && source.path.parent().is_none_or(|path|!packages.contains(path)))
    }
    pub fn update(&mut self, resolver: &Resolver<'_>, runtime: &Path) {
        if !self.extends(resolver.sources) {
            *self = Self::default();
            crate::profile::count("discovery.worklist_resets", 1);
        }
        for (file, source) in resolver.sources.iter().enumerate().skip(self.sources.len()) {
            crate::profile::count("discovery.source_scans", 1);
            for (index, node) in source.nodes.iter().enumerate() {
                let reference = Ref {file, node:NodeId(index)};
                if !source.shared_native && !source.path.starts_with(runtime) && node.immutable {
                    self.queries.extend(source.group(reference.node, "names").iter()
                        .map(|node| Ref {file, node:*node}));
                }
                if node.data["pure"] == true { self.roots.push(reference); }
                if node.kind == "Run" {
                    self.requests.extend(source.descendants(reference.node)
                        .filter(|id|source.node(*id).kind == "Call")
                        .map(|node|Ref {file,node}));
                }
            }
        }
        self.sources = resolver.sources.to_vec();
    }
    pub fn types(&mut self, resolver: &mut Resolver<'_>) -> Vec<Type> {
        let ordinary = resolver.bound_types.is_empty() && resolver.bound_constants.is_empty()
            && resolver.evaluated.is_empty() && resolver.logical_constants.is_empty()
            && resolver.context_hints.is_empty() && resolver.parameter_witnesses.is_empty();
        if !ordinary { self.complete.clear(); }
        self.queries.iter().map(|query| {
            if let Some(typ) = self.complete.get(query) {
                crate::profile::count("discovery.type_query_reuses", 1);
                resolver.adopt_closed_type(*query, typ.clone());
                return typ.clone();
            }
            crate::profile::count("discovery.type_queries", 1);
            let typ = resolver.type_of(*query);
            // This deliberately uses the whole pending set. Even an answer
            // obtained through a memoized partial dependency is excluded. A
            // later retry may promote more queries after imports are complete.
            if ordinary && typ.resolved() && !resolver.has_demanded_packages() {
                self.complete.insert(*query, typ.clone());
            }
            typ
        }).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn source(path: &str, thor: bool) -> Source {
        Source::from_frontend(path.into(), "x".into(), serde_json::json!({
            "nodes":[{"kind":"File","start":0,"end":1,"parent":-1}],
            "features":[],"edits":[],"selections":[]
        }), thor).unwrap()
    }
    #[test]
    fn append_guard_rejects_changed_syntax_views_roots_and_existing_namespaces() {
        let old = vec![source("/project/main.thor",true)];
        let work = Work {sources:old.clone(), ..Work::default()};
        let mut added = old.clone(); added.push(source("/sdk/library.odin",false));
        if !work.extends(&old) || !work.extends(&added) { panic!("closed namespace rejected"); }
        for next in [vec![source("/project/main.thor",true)],
            vec![old[0].clone(),source("/project/more.odin",false)],
            vec![old[0].clone(),source("/other/main.thor",true)]] {
            if work.extends(&next) { panic!("changing lookup universe reused queries"); }
        }
        let mut changed = old.clone(); changed[0].shared_native = true;
        if work.extends(&changed) { panic!("source view change ignored"); }
        changed = old.clone(); changed[0].collection_roots.insert("core".into(),"/new".into());
        if work.extends(&changed) { panic!("collection change ignored"); }
    }
}
