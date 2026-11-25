//! Reuse completed slice checks only for identical semantic inputs in one command.
use crate::{
    ast::Source,
    privileges::{Facts, Privileges, SummaryCache},
    semantics::{ContextHint, ParameterWitness, Ref, Resolver, Type},
};
use std::collections::{BTreeMap, HashMap};

#[derive(Clone)]
struct Inputs {
    sources: Vec<Source>,
    flags: Vec<String>,
    resolver_flags: Vec<String>,
    editor: bool,
    logical_mode: bool,
    evaluated: BTreeMap<Ref, serde_json::Value>,
    logical_constants: BTreeMap<Ref, serde_json::Value>,
    bound_types: BTreeMap<(Ref, String), Type>,
    bound_constants: BTreeMap<(Ref, String), (Type, serde_json::Value)>,
    contexts: HashMap<Ref, ContextHint>,
    witnesses: BTreeMap<Ref, ParameterWitness>,
}
impl Inputs {
    fn new(resolver: &Resolver<'_>, flags: &[String], editor: bool) -> Self {
        Self {
            sources: resolver.sources.to_vec(),
            flags: flags.to_vec(),
            resolver_flags: resolver.flags.clone(),
            editor,
            logical_mode: Self::logical_mode(resolver),
            evaluated: resolver.evaluated.clone(),
            logical_constants: resolver.logical_constants.clone(),
            bound_types: resolver.bound_types.clone(),
            bound_constants: resolver.bound_constants.clone(),
            contexts: resolver.context_hints.clone(),
            witnesses: resolver.parameter_witnesses.clone(),
        }
    }
    fn matches(&self, resolver: &Resolver<'_>, flags: &[String], editor: bool) -> bool {
        self.sources.len() == resolver.sources.len()
            && self
                .sources
                .iter()
                .zip(resolver.sources)
                .all(|(old, current)| {
                    old.shares_syntax(current)
                        && old.shared_native == current.shared_native
                        && old.collection_roots == current.collection_roots
                })
            && self.flags == flags
            && self.resolver_flags == resolver.flags
            && self.editor == editor
            && self.logical_mode == Self::logical_mode(resolver)
            && self.evaluated == resolver.evaluated
            && self.logical_constants == resolver.logical_constants
            && self.bound_types == resolver.bound_types
            && self.bound_constants == resolver.bound_constants
            && self.contexts == resolver.context_hints
            && self.witnesses == resolver.parameter_witnesses
    }
    fn extends(&self, resolver: &Resolver<'_>, flags: &[String], editor: bool) -> bool {
        let count = self.sources.len();
        if resolver.sources.len() < count || self.flags != flags || self.resolver_flags != resolver.flags
            || self.editor != editor || self.logical_mode != Self::logical_mode(resolver) { return false; }
        if !self.sources.iter().zip(resolver.sources).all(|(old,current)|
            old.shares_syntax(current) && old.shared_native == current.shared_native
                && old.collection_roots == current.collection_roots) { return false; }
        let old_packages = self.sources.iter().filter_map(|source|source.path.parent()).collect::<std::collections::HashSet<_>>();
        if resolver.sources[count..].iter().any(|source|source.path.parent().is_some_and(|p|old_packages.contains(p))) {
            return false;
        }
        macro_rules! same_prefix {
            ($old:expr,$new:expr,$file:expr) => {{
                $old.iter().all(|(key,value)| $new.get(key)==Some(value))
                    && $new.keys().filter(|key| $file(key)<count).count()==$old.len()
            }};
        }
        same_prefix!(self.evaluated,resolver.evaluated,|key: &Ref|key.file)
            && same_prefix!(self.logical_constants,resolver.logical_constants,|key: &Ref|key.file)
            && same_prefix!(self.bound_types,resolver.bound_types,|key: &(Ref,String)|key.0.file)
            && same_prefix!(self.bound_constants,resolver.bound_constants,|key: &(Ref,String)|key.0.file)
            && same_prefix!(self.contexts,resolver.context_hints,|key: &Ref|key.file)
            && same_prefix!(self.witnesses,resolver.parameter_witnesses,|key: &Ref|key.file)
    }
    fn logical_mode(resolver:&Resolver<'_>)->bool {
        resolver.sources.iter().position(|source|source.thor).is_some_and(|file|resolver.logical_enabled(file))
    }
}

#[derive(Clone, Default)]
pub(crate) struct Cache {
    completed: Option<(Inputs, Facts)>,
    retry: Option<(Inputs, SummaryCache)>,
}
impl Cache {
    pub fn analyze(
        &mut self,
        resolver: &mut Resolver<'_>,
        flags: &[String],
        editor: bool,
        evaluation: Option<&crate::evaluation::Plan>,
    ) -> Facts {
        self.resolve(resolver, flags, editor, evaluation, false)
    }
    pub fn discover(
        &mut self,
        resolver: &mut Resolver<'_>,
        flags: &[String],
        editor: bool,
        evaluation: Option<&crate::evaluation::Plan>,
    ) -> Facts {
        self.resolve(resolver, flags, editor, evaluation, true)
    }
    fn resolve(
        &mut self,
        resolver: &mut Resolver<'_>,
        flags: &[String],
        editor: bool,
        evaluation: Option<&crate::evaluation::Plan>,
        discovering: bool,
    ) -> Facts {
        // Helper plans retain different subsets of declarations and control flow.
        // They require their own analysis even when their sources are shared.
        if evaluation.is_some() {
            crate::profile::count("permissions.helper_analyses", 1);
            return if discovering {
                Privileges::discover(resolver, flags, editor, evaluation)
            } else {
                Privileges::analyze_project(resolver, flags, editor, evaluation)
            };
        }
        if let Some((inputs, facts)) = &self.completed {
            // An independently loaded native namespace adds no checked roots.
            // Completed discovery had no unresolved imports, so it cannot
            // change an old namespace's effects. The prefix guard also rejects
            // new members in an old package, flags, contexts and evaluations.
            let extension = resolver.sources.len() > inputs.sources.len()
                && inputs.extends(resolver, flags, editor)
                && resolver.sources[inputs.sources.len()..].iter().all(|s| s.shared_native);
            if inputs.matches(resolver, flags, editor) || extension {
                crate::profile::count("permissions.reuses", 1);
                if extension { crate::profile::count("permissions.native_extension_reuses", 1); }
                return facts.clone();
            }
        }
        crate::profile::count("permissions.analyses", 1);
        let mut summaries = self.retry.take().filter(|(inputs,_)| inputs.extends(resolver,flags,editor))
            .map(|(_,cache)|cache).unwrap_or_default();
        let inputs = Inputs::new(resolver, flags, editor);
        let facts = if discovering {
            Privileges::discover_cached(resolver, flags, editor, &mut summaries)
        } else {
            Privileges::analyze_project(resolver, flags, editor, None)
        };
        if !resolver.has_demanded_packages() {
            self.completed = Some((inputs, facts.clone()));
        } else if discovering {
            self.retry = Some((inputs,summaries));
        }
        facts
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn source() -> Source {
        Source::from_frontend(
            "/example.thor".into(),
            "x".into(),
            serde_json::json!({
                "nodes":[{"kind":"File","start":0,"end":1,"parent":-1}],
                "features":[],"edits":[],"selections":[]
            }),
            true,
        )
        .unwrap()
    }
    #[test]
    fn analysis_reuse_requires_identical_sources_flags_values_bindings_and_context() {
        let sources = vec![source()];
        let mut resolver = Resolver::new(&sources);
        let inputs = Inputs::new(&resolver, &[], false);
        let site = Ref {
            file: 0,
            node: crate::ast::NodeId(0),
        };
        if !inputs.matches(&resolver, &[], false) {
            panic!("identical context did not match");
        }
        if inputs.matches(&resolver, &["-debug".into()], false)
            || inputs.matches(&resolver, &[], true)
        {
            panic!("analysis flags/editor mode were ignored");
        }
        resolver.flags.push("-debug".into());
        if inputs.matches(&resolver, &[], false) {
            panic!("resolver flags were ignored");
        }
        resolver.flags.clear();
        resolver.evaluated.insert(site, serde_json::json!(3));
        if inputs.matches(&resolver, &[], false) {
            panic!("evaluation result was ignored");
        }
        resolver.evaluated.clear();
        resolver
            .logical_constants
            .insert(site, serde_json::json!(4));
        if inputs.matches(&resolver, &[], false) {
            panic!("logical value was ignored");
        }
        resolver.logical_constants.clear();
        resolver
            .bound_types
            .insert((site, "T".into()), Type::Unknown);
        if inputs.matches(&resolver, &[], false) {
            panic!("type binding was ignored");
        }
        resolver.bound_types.clear();
        resolver
            .bound_constants
            .insert((site, "N".into()), (Type::Unknown, serde_json::json!(2)));
        if inputs.matches(&resolver, &[], false) {
            panic!("constant binding was ignored");
        }
        resolver.bound_constants.clear();
        resolver.context_hints.insert(
            site,
            ContextHint {
                annotation: site,
                values: vec![],
                witnesses: vec![],
                plain_array: true,
            },
        );
        if inputs.matches(&resolver, &[], false) {
            panic!("contextual type was ignored");
        }
        resolver.context_hints.clear();
        resolver.parameter_witnesses.insert(
            site,
            ParameterWitness {
                declaration: site,
                annotation: site,
                used: vec![],
            },
        );
        if inputs.matches(&resolver, &[], false) {
            panic!("parameter witness was ignored");
        }
        let mut changed = sources.clone();
        changed[0]
            .collection_roots
            .insert("core".into(), "/other".into());
        if inputs.matches(&Resolver::new(&changed), &[], false) {
            panic!("collection changed");
        }
        changed[0] = source();
        if inputs.matches(&Resolver::new(&changed), &[], false) {
            panic!("source was reparsed");
        }
        changed = sources.clone();
        changed[0].shared_native = true;
        if inputs.matches(&Resolver::new(&changed), &[], false) {
            panic!("source view changed");
        }
    }
    #[test]
    fn reuse_returns_independent_facts_and_helpers_analyze_their_own_views() {
        let sources = vec![source()];
        let mut cache = Cache::default();
        let mut resolver = Resolver::new(&sources);
        let mut first = cache.analyze(&mut resolver, &[], false, None);
        let site = Ref {
            file: 0,
            node: crate::ast::NodeId(0),
        };
        first
            .invalid
            .insert(site, "only the caller changes this".into());
        let mut second_resolver = Resolver::new(&sources);
        let second = cache.analyze(&mut second_resolver, &[], false, None);
        if !second.invalid.is_empty() {
            panic!("cached facts were mutated by a consumer");
        }
        let helper = crate::evaluation::Plan::default();
        let own = cache.analyze(&mut second_resolver, &[], true, Some(&helper));
        if !own.invalid.is_empty() || cache.completed.as_ref().unwrap().0.editor {
            panic!("helper view replaced the whole-project cache");
        }
    }

    #[test]
    fn completed_facts_survive_only_independent_native_namespace_extensions() {
        let old = vec![source()];
        let site = Ref { file:0, node:crate::ast::NodeId(0) };
        let mut facts = Facts::default();
        facts.readonly.insert(site);
        let make = |path:&str| Source::from_frontend(path.into(),"x".into(),serde_json::json!({
            "nodes":[{"kind":"File","start":0,"end":1,"parent":-1}],
            "features":[],"edits":[],"selections":[]
        }),false).unwrap();
        let mut extra = make("/additional/library.odin");
        extra.shared_native = true;
        for change in 0..6 {
            let mut next = vec![old[0].clone(),extra.clone()];
            let flags = if change == 3 {vec!["-debug".into()]}else{vec![]};
            if change == 1 {next[1].shared_native=false;}
            if change == 2 {next[1]=make("/additional.odin");next[1].shared_native=true;}
            if change == 4 {next[0]=source();}
            if change == 5 {next[0].collection_roots.insert("core".into(),"/changed".into());}
            let mut cache=Cache {completed:Some((Inputs::new(&Resolver::new(&old),&[],false),facts.clone())),retry:None};
            let mut resolver=Resolver::new(&next);
            let actual=cache.analyze(&mut resolver,&flags,false,None);
            if actual.readonly.contains(&site) != (change==0) {
                panic!("native extension guard ignored change {change}");
            }
        }
    }
    #[test]
    fn missing_dependency_prevents_publication_of_partial_facts() {
        let directory = std::env::temp_dir().join(format!(
            "thor-permission-input-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(directory.join("library")).unwrap();
        let raw = "import \"library\"\nlibrary.VALUE";
        let base = raw.rfind("library").unwrap();
        let field = raw.rfind("VALUE").unwrap();
        let source = Source::from_frontend(
            directory.join("main.thor"),
            raw.into(),
            serde_json::json!({
                "nodes":[
                    {"kind":"File","start":0,"end":raw.len(),"parent":-1},
                    {"kind":"Import","start":0,"end":16,"parent":0,"op":"\"library\""},
                    {"kind":"Selector","start":base,"end":raw.len(),"parent":0,
                     "roles":{"expr":[base,base+7],"field":[field,raw.len()]}},
                    {"kind":"Ident","start":base,"end":base+7,"parent":2,"name":"library"},
                    {"kind":"Ident","start":field,"end":raw.len(),"parent":2,"name":"VALUE"}
                ],"features":[],"edits":[],"selections":[]
            }),
            true,
        )
        .unwrap();
        let sources = vec![source];
        let mut resolver = Resolver::new(&sources);
        let _unresolved = resolver.type_of(Ref {
            file: 0,
            node: crate::ast::NodeId(2),
        });
        let demanded = resolver.has_demanded_packages();
        let mut cache = Cache::default();
        let _partial = cache.discover(&mut resolver, &[], false, None);
        let published = cache.completed.is_some();
        std::fs::remove_dir_all(directory).unwrap();
        if !demanded || published {
            panic!("partial dependency graph was cached as complete");
        }
    }
    #[test]
    fn summary_extension_requires_unchanged_semantics_and_new_namespaces() {
        let old = vec![source()];
        let inputs = Inputs::new(&Resolver::new(&old), &[], false);
        let make = |path:&str| Source::from_frontend(path.into(),"x".into(),serde_json::json!({
            "nodes":[{"kind":"File","start":0,"end":1,"parent":-1}],
            "features":[],"edits":[],"selections":[]
        }),false).unwrap();
        let extended = vec![old[0].clone(),make("/additional/new.odin")];
        let mut resolver = Resolver::new(&extended);
        if !inputs.extends(&resolver,&[],false) { panic!("new namespace rejected"); }
        if inputs.extends(&resolver,&["-debug".into()],false) || inputs.extends(&resolver,&[],true) {
            panic!("changed modes reused summaries");
        }
        let site = Ref {file:0,node:crate::ast::NodeId(0)};
        resolver.evaluated.insert(site,serde_json::json!(3));
        if inputs.extends(&resolver,&[],false) { panic!("changed value reused summaries"); }
        resolver.evaluated.clear();
        resolver.bound_types.insert((site,"T".into()),Type::scalar("int"));
        if inputs.extends(&resolver,&[],false) { panic!("changed binding reused summaries"); }
        let augmented = vec![old[0].clone(),make("/another.odin")];
        if inputs.extends(&Resolver::new(&augmented),&[],false) { panic!("existing namespace augmented"); }
        let replaced = vec![source(),make("/additional/new.odin")];
        if inputs.extends(&Resolver::new(&replaced),&[],false) { panic!("reparsed source reused summaries"); }
        let raw="import \"thor:big\"";
        let logical=Source::from_frontend("/logical/new.thor".into(),raw.into(),serde_json::json!({
            "nodes":[{"kind":"File","start":0,"end":raw.len(),"parent":-1},
                {"kind":"Import","start":0,"end":raw.len(),"parent":0,"op":"\"thor:big\""}],
            "features":[],"edits":[],"selections":[]
        }),true).unwrap();
        let activation=vec![old[0].clone(),logical];
        if inputs.extends(&Resolver::new(&activation),&[],false) {panic!("changed logical activation reused summaries");}
    }
}
