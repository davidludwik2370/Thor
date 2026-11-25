//! Optional exact-input memory-analysis memo. A cache miss runs the same proof;
//! no cache failure grants a contract or changes generated program behavior.
use super::*;
use crate::{evaluationcache::EvaluationCache, evaluationinputs::Inputs};

const NAMESPACE: &str = "lifetime-v1";

fn key(project: &Project, checks: &[String]) -> Option<String> {
    let _phase=crate::profile::span("vet.memory_cache_key");
    let sources=project.sources.iter().map(|s|json!([
        s.path,sha256(s.raw.as_bytes()),s.thor,s.excluded,s.shared_native,
        s.collection_roots,s.support_collection,s.prefix
    ])).collect::<Vec<_>>();
    let evaluated=project.evaluated.iter().map(|(at,value)|json!([at,value])).collect::<Vec<_>>();
    Some(sha256(&serde_json::to_vec(&json!([
        env!("THOR_COMPILER_REVISION"),project.frontend.fingerprint,project.frontend.odin_root,
        project.frontend.test,project.frontend.target,project.frontend.odin_syntax,
        project.options.odin_compatible,project.options.entry,
        project.options.root,project.options.flags,std::env::consts::OS,std::env::consts::ARCH,
        checks.iter().filter(|s|matches!(s.as_str(),"lifetime"|"leak")).collect::<Vec<_>>(),
        sources,evaluated
    ])).ok()?))
}

/// Content checks include every loaded dependency, package membership, import
/// symlink binding and #load/#exists resource. Unsupported snapshots bypass it.
fn inputs(project: &Project) -> Inputs {
    let _phase=crate::profile::span("vet.memory_cache_inputs");
    let mut result=Inputs::default();
    let suffixes=vec!["odin".into(),"thor".into()];
    let mut directories=BTreeSet::new();
    for source in &project.sources {
        result.source(source,true);
        result.binding(&source.path);
        if let Some(parent)=source.path.parent() {directories.insert(parent.to_path_buf());}
        for node in source.nodes.iter().filter(|n|n.kind=="Import") {
            let Ok(path)=unquote(&node.op) else {result.usable=false;continue;};
            let directory=if let Some((collection,suffix))=path.split_once(':') {
                source.collection_roots.get(collection).map(|root|root.join(suffix))
            } else {source.path.parent().map(|root|root.join(path))};
            if let Some(directory)=directory {
                result.binding(&directory);
                if directory.is_dir(){directories.insert(directory);}
            } else {result.usable=false;}
        }
    }
    for directory in directories {
        match crate::evaluationinputs::members(&directory,&suffixes) {
            Ok(names)=>{
                let paths=names.iter().map(|name|directory.join(name)).collect::<Vec<_>>();
                result.package(&directory,&paths,&suffixes);
            },
            Err(_)=>result.usable=false,
        }
    }
    result
}

impl Project {
    pub(super) fn lifetime_findings(&mut self, checks: &[String]) -> Result<Vec<Value>> {
        if !checks.iter().any(|s|matches!(s.as_str(),"lifetime"|"leak")){return Ok(vec![]);}
        let cache=self.frontend.cache_dir.as_ref().filter(|_|!self.options.no_cache)
            .map(|directory|EvaluationCache::new(directory,true));
        let key=cache.as_ref().and_then(|_|key(self,checks));
        if let Some(record)=cache.as_ref().and_then(|cache|cache.read_record(NAMESPACE,key.as_deref())) {
            if let (Some(dependencies),Some(findings))=(record.get("inputs"),record.get("findings").and_then(Value::as_array)) {
                if Inputs::new(Some(dependencies.clone())).valid() {
                    // Other convention checks can need the same declarations.
                    // Recreate the imported metadata frontier before reusing
                    // findings, rather than letting a warm run see fewer types.
                    let packages=record.get("packages").and_then(Value::as_array)
                        .and_then(|paths|paths.iter().map(|p|p.as_str().map(PathBuf::from)).collect::<Option<BTreeSet<_>>>());
                    let Some(mut packages)=packages else {return self.uncached_lifetime_findings(checks);};
                    packages.retain(|p|!self.sources.iter().any(|s|s.path.parent()==Some(p)));
                    if !packages.is_empty(){self.discover_type_facts_selected(packages,false)?;}
                    crate::profile::count("vet.memory_cache_hits",1);
                    return Ok(findings.clone());
                }
            }
        }
        crate::profile::count("vet.memory_cache_misses",1);
        let source_count=self.sources.len();
        self.discover_lifetime_result_facts()?;
        let dependencies=cache.as_ref().filter(|_|key.is_some()).map(|_|inputs(self));
        let findings=crate::lifetime::analyze(self,checks);
        if let (Some(cache),Some(dependencies))=(cache,dependencies) {
            if dependencies.valid() {
                let packages=self.sources[source_count..].iter().filter_map(|s|s.path.parent()).collect::<BTreeSet<_>>();
                cache.write_record(NAMESPACE,key.as_deref(),&json!({"inputs":dependencies.data,"findings":findings,"packages":packages}));
                crate::profile::count("vet.memory_cache_writes",1);
            } else {crate::profile::count("vet.memory_cache_bypasses",1);}
        }
        Ok(findings)
    }
    fn uncached_lifetime_findings(&mut self, checks:&[String])->Result<Vec<Value>> {
        self.discover_lifetime_result_facts()?;
        Ok(crate::lifetime::analyze(self,checks))
    }
}
