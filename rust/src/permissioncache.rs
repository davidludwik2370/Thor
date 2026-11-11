//! Reuse completed slice checks only for identical semantic inputs in one command.
use crate::{
    ast::Source,
    privileges::{Facts, Privileges},
    semantics::{ContextHint, ParameterWitness, Ref, Resolver, Type},
};
use std::collections::{BTreeMap, HashMap};

#[derive(Clone)]
struct Inputs {
    sources: Vec<Source>,
    flags: Vec<String>,
    resolver_flags: Vec<String>,
    editor: bool,
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
            && self.evaluated == resolver.evaluated
            && self.logical_constants == resolver.logical_constants
            && self.bound_types == resolver.bound_types
            && self.bound_constants == resolver.bound_constants
            && self.contexts == resolver.context_hints
            && self.witnesses == resolver.parameter_witnesses
    }
}

#[derive(Clone, Default)]
pub(crate) struct Cache {
    completed: Option<(Inputs, Facts)>,
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
            if inputs.matches(resolver, flags, editor) {
                crate::profile::count("permissions.reuses", 1);
                return facts.clone();
            }
        }
        crate::profile::count("permissions.analyses", 1);
        let inputs = Inputs::new(resolver, flags, editor);
        let facts = if discovering {
            Privileges::discover(resolver, flags, editor, None)
        } else {
            Privileges::analyze_project(resolver, flags, editor, None)
        };
        if !resolver.has_demanded_packages() {
            self.completed = Some((inputs, facts.clone()));
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
}
