use crate::{
    ast::{Source, Span},
    diagnostic::{Error, Result},
    digest::sha256,
    emit::Emitted,
    frontend::{atomic_write, bootstrap, Frontend},
    lower::unquote,
    semantics::Resolver,
    VERSION,
};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    env, fs,
    path::{Component, Path, PathBuf},
    process::{Command, Stdio},
};
#[path = "backend.rs"]
mod backend;
#[path = "evaluationexec.rs"]
mod evaluationexec;
#[path = "evaluationrequest.rs"]
mod evaluationrequest;
#[path = "evaluationinstall.rs"]
mod evaluationinstall;
pub(crate) use evaluationexec::capture as capture_command;
#[cfg(unix)]
pub use evaluationexec::{cancel_active_evaluation, evaluation_cancelled};
#[path = "nativefacts.rs"]
mod nativefacts;
#[path = "dependencylookahead.rs"]
mod dependencylookahead;
#[path = "runtimeview.rs"]
mod runtimeview;
#[path = "sdkmirror.rs"]
mod sdkmirror;
#[path = "filelowering.rs"]
mod filelowering;

#[derive(Clone)]
pub struct Options {
    pub command: String,
    pub entry: PathBuf,
    pub output: Option<PathBuf>,
    pub cache: Option<PathBuf>,
    pub odin: String,
    pub flags: Vec<String>,
    pub args: Vec<String>,
    pub no_cache: bool,
    pub json: bool,
    pub root: PathBuf,
    pub maps: bool,
    pub tracking: String,
    pub no_sanitize: bool,
    pub overlays: BTreeMap<PathBuf, String>,
    pub editor_info: bool,
    pub run_timeout: std::time::Duration,
    pub clear_cache: bool,
    pub cache_report: bool,
    pub raw_diagnostics: bool,
    pub vet: Option<Vec<String>>,
    pub vet_apply: bool,
    pub vet_ask: bool,
}
struct LoweringNotes {
    operation: Option<crate::semantics::Ref>,
    related: Vec<Value>,
}
#[derive(Clone)]
struct SourceFacts {
    sources: Vec<Source>,
    flags: Vec<String>,
}
#[derive(Clone)]
struct IndexedSources {
    sources: Vec<Source>,
    index: crate::semantics::ResolverIndex,
}
pub struct Project {
    pub publish_layout: Option<String>,
    pub native_override: Option<Vec<String>>,
    pub collected_diagnostics: Option<std::cell::RefCell<Vec<Value>>>,
    pub options: Options,
    pub frontend: Frontend,
    pub sources: Vec<Source>,
    pub destination: PathBuf,
    pub outputs: BTreeMap<PathBuf, (usize, Emitted)>,
    pub errors: HashMap<String, String>,
    pub editor_info: Vec<Value>,
    pub evaluated: BTreeMap<crate::semantics::Ref, Value>,
    pub specialized: crate::specialization::Cases,
    pub evaluation_scheduled: bool,
    pub evaluation_skips: BTreeMap<crate::semantics::Ref, bool>,
    pub evaluation_witnesses: BTreeMap<crate::semantics::Ref, String>,
    related: HashMap<String, LoweringNotes>,
    type_sites: HashMap<String, (crate::semantics::Ref, String)>,
    pure_error_sites: HashMap<String, crate::semantics::Ref>,
    pure_call_paths: BTreeMap<
        crate::semantics::Ref,
        BTreeSet<(crate::semantics::Ref, Vec<crate::semantics::Ref>)>,
    >,
    collections: BTreeMap<String, PathBuf>,
    common: PathBuf,
    file_entry: bool,
    memory: crate::memory::Memory,
    native_environment: BTreeMap<String, PathBuf>,
    backend_runtime: PathBuf,
    evaluation: Option<crate::evaluation::Plan>,
    type_probes: BTreeMap<crate::semantics::Ref, Vec<String>>,
    type_probe_imports: BTreeMap<usize, BTreeMap<String, String>>,
    source_facts: Option<SourceFacts>,
    resolver_index: std::cell::RefCell<Option<IndexedSources>>,
    resolver_state: std::cell::RefCell<Option<crate::semantics::ResolverSeed>>,
    permission_cache: std::cell::RefCell<crate::permissioncache::Cache>,
    // Parsed import frontiers are consumed in the original depth-first order.
    // Workers may finish in any order without changing source/evaluation order.
    parsed_imports: HashMap<(PathBuf, bool), Result<Source>>,
}
pub fn absolute(path: &Path) -> Result<PathBuf> {
    let input = if path.is_absolute() {
        path.to_path_buf()
    } else {
        env::current_dir()?.join(path)
    };
    if input.exists() {
        return Ok(fs::canonicalize(input)?);
    }
    if let Some(parent) = input.parent() {
        if parent.exists() {
            return Ok(fs::canonicalize(parent)?.join(
                input
                    .file_name()
                    .ok_or_else(|| Error::new("invalid output path"))?,
            ));
        }
    }
    let mut result = PathBuf::new();
    for part in input.components() {
        match part {
            Component::CurDir => {}
            Component::ParentDir => {
                result.pop();
            }
            other => result.push(other.as_os_str()),
        }
    }
    Ok(result)
}
fn reference_coordinates(suffix: &str) -> Option<(usize, usize, usize)> {
    let (line, column, length) = if let Some(rest) = suffix.strip_prefix('(') {
        let close = rest.find(')')?;
        let (line, column) = rest[..close].split_once([':', ','])?;
        (line, column, close + 2)
    } else {
        let rest = suffix.strip_prefix(':')?;
        let (line, rest) = rest.split_once(':')?;
        let column_end = rest.bytes().take_while(u8::is_ascii_digit).count();
        let delimiter = usize::from(rest.as_bytes().get(column_end) == Some(&b':'));
        (line, &rest[..column_end], line.len() + 2 + column_end + delimiter)
    };
    let line = line.trim().parse::<usize>().ok()?;
    let column = column.trim().parse::<usize>().ok()?;
    (line > 0 && column > 0).then_some((line, column, length))
}

fn package_files(entry: &Path, test: bool, overlays: &BTreeMap<PathBuf, String>) -> Result<Vec<PathBuf>> {
    if entry.is_file() || overlays.contains_key(entry) {
        return Ok(vec![entry.to_path_buf()]);
    }
    let mut files = vec![];
    for item in fs::read_dir(entry)? {
        let item = item?;
        let path = item.path();
        if !item.file_type()?.is_file()
            || !path
                .extension()
                .is_some_and(|ext| ext == "thor" || ext == "odin")
        {
            continue;
        }
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        if !test && (name.ends_with("_test.thor") || name.ends_with("_test.odin")) {
            continue;
        }
        files.push(fs::canonicalize(path)?);
    }
    for path in overlays.keys() {
        if path.parent() != Some(entry)
            || !path.extension().is_some_and(|ext| ext == "thor" || ext == "odin") {
            continue;
        }
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        if !test && (name.ends_with("_test.thor") || name.ends_with("_test.odin")) {
            continue;
        }
        files.push(path.clone());
    }
    files.sort();
    files.dedup();
    Ok(files)
}
impl Project {
    pub fn new(mut options: Options) -> Result<Self> {
        let _phase = crate::profile::span("project.load");
        options.entry = absolute(&options.entry)?;
        if !options.entry.exists() && !options.overlays.contains_key(&options.entry) {
            return Err(Error::new(format!(
                "source does not exist: {}",
                options.entry.display()
            )));
        }
        let file_entry = options.entry.is_file() || options.overlays.contains_key(&options.entry);
        if file_entry
            && !options
                .entry
                .extension()
                .is_some_and(|ext| ext == "thor" || ext == "odin")
        {
            return Err(Error::new(
                "a source file must have a .thor or .odin extension",
            ));
        }
        let (parser, odin) = bootstrap(&options.root, &options.odin)?;
        let cache = options
            .cache
            .clone()
            .or_else(|| env::var_os("THOR_CACHE_DIR").map(PathBuf::from))
            .unwrap_or_else(|| {
                if file_entry {
                    options.entry.parent().unwrap().join(".thor-cache")
                } else {
                    options.entry.join(".thor-cache")
                }
            });
        if options.clear_cache {
            crate::evaluationcache::EvaluationCache::new(&cache, true).clear();
            if let Ok(entries) = fs::read_dir(cache.join("rust-parse-v1")) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if entry.file_type().is_ok_and(|kind| kind.is_file())
                        && path
                            .extension()
                            .is_some_and(|extension| extension == "json")
                        && path
                            .file_stem()
                            .and_then(|name| name.to_str())
                            .is_some_and(|name| {
                                name.len() == 64
                                    && name
                                        .bytes()
                                        .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
                            })
                    {
                        let _ = fs::remove_file(path);
                    }
                }
            }
        }
        let test = options.command == "test"
            || options.flags.iter().any(|flag| flag == "-build-mode:test");
        let target = options
            .flags
            .iter()
            .find_map(|flag| flag.strip_prefix("-target:"))
            .and_then(|target| target.split_once('_'))
            .map(|(os, arch)| (os.into(), arch.into()));
        let frontend = Frontend {
            normalized_cache: false,
            fingerprint: sha256(&fs::read(&parser)?),
            executable: parser,
            odin,
            cache_dir: if options.no_cache { None } else { Some(cache) },
            test,
            target,
            project_name: options
                .entry
                .file_stem()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned(),
        };
        let mut collections = BTreeMap::new();
        collections.insert("thor".into(), options.root.join("runtime"));
        for flag in &options.flags {
            if let Some(value) = flag.strip_prefix("-collection:") {
                let (name, path) = value
                    .split_once('=')
                    .ok_or_else(|| Error::new(format!("invalid collection flag: {flag}")))?;
                collections.insert(name.into(), absolute(Path::new(path))?);
            }
        }
        let base = options
            .entry
            .parent()
            .filter(|_| file_entry)
            .unwrap_or(&options.entry);
        let common = base.to_path_buf();
        let destination = absolute(&options.output.clone().unwrap_or_else(|| {
            env::current_dir()
                .unwrap_or_default()
                .join(".thor-rust-build")
                .join(&frontend.project_name)
        }))?;
        let mut project = Self {
            publish_layout: None,
            native_override: None,
            collected_diagnostics: None,
            memory: crate::memory::Memory::select(
                &options.command,
                &options.flags,
                &options.tracking,
                options.no_sanitize,
            )?,
            options,
            frontend,
            sources: vec![],
            destination,
            outputs: BTreeMap::new(),
            errors: HashMap::new(),
            editor_info: vec![],
            evaluated: BTreeMap::new(),
            specialized: BTreeMap::new(),
            evaluation_scheduled: false,
            evaluation_skips: BTreeMap::new(),
            evaluation_witnesses: BTreeMap::new(),
            related: HashMap::new(),
            type_sites: HashMap::new(),
            pure_error_sites: HashMap::new(),
            pure_call_paths: BTreeMap::new(),
            native_environment: BTreeMap::new(),
            backend_runtime: PathBuf::new(),
            evaluation: None,
            type_probes: BTreeMap::new(),
            type_probe_imports: BTreeMap::new(),
            source_facts: None,
            resolver_index: std::cell::RefCell::new(None),
            resolver_state: std::cell::RefCell::new(None),
            permission_cache: std::cell::RefCell::default(),
            parsed_imports: HashMap::new(),
            collections,
            common,
            file_entry,
        };
        let entry = project.options.entry.clone();
        project.discover(&entry, &mut HashSet::new())?;
        if project.sources.is_empty() {
            return Err(Error::new(format!(
                "no .thor or .odin sources found: {}",
                entry.display()
            )));
        }
        if project.sources.iter().any(|source| {
            source.path.parent() == Some(project.options.root.join("runtime/app").as_path())
        }) && project.options.flags.iter().any(|flag| {
            flag.starts_with("-define:THOR_APP_SOURCE_ROOT=")
                || flag.starts_with("-define:THOR_APP_LAYOUT=")
        }) {
            return Err(Error::new(
                "THOR_APP_SOURCE_ROOT and THOR_APP_LAYOUT are reserved for thor:app.",
            ));
        }
        for source in &project.sources {
            if source.shared_native {
                continue;
            }
            let parent = source.path.parent().unwrap();
            while !parent.starts_with(&project.common) {
                if !project.common.pop() {
                    return Err(Error::new("source packages have no common directory"));
                }
            }
        }
        for source in &mut project.sources {
            source.collection_roots = project.collections.clone();
        }
        Ok(project)
    }
    fn discover(&mut self, entry: &Path, visited: &mut HashSet<PathBuf>) -> Result<()> {
        let entry = absolute(entry)?;
        if !visited.insert(entry.clone()) {
            return Ok(());
        }
        for ancestor in entry.ancestors() {
            if ancestor.join("thor-manifest.json").is_file() {
                return Err(Error::new(format!(
                    "generated Odin is not a Thor input; use the original source: {}",
                    entry.display()
                )));
            }
        }
        let support_library =
            entry.starts_with(self.options.root.join("runtime")) && entry != self.options.entry;
        let files = package_files(&entry, self.frontend.test && !support_library, &self.options.overlays)?;
        let files = files.into_iter().filter(|path| !self.sources.iter().any(|s| s.path == *path))
            .collect::<Vec<_>>();
        self.parse_imports(files.iter().map(|path| (path.clone(), entry.is_dir())).collect());
        let mut imports = vec![];
        for path in files {
            let mut source = self.parsed_imports.remove(&(path.clone(), entry.is_dir()))
                .expect("requested parse result")?;
            source.shared_native = path.extension().is_some_and(|ext| ext == "odin")
                && path.starts_with(self.options.root.join("runtime"))
                && path != self.options.entry
                && path.parent() != Some(self.options.entry.as_path());
            if source.excluded {
                continue;
            }
            for node in &source.nodes {
                if node.kind == "Import" {
                    let import = unquote(&node.op)?;
                    let directory = if let Some((collection, suffix)) = import.split_once(':') {
                        self.collections
                            .get(collection)
                            .map(|base| base.join(suffix))
                    } else {
                        Some(path.parent().unwrap().join(import))
                    };
                    if let Some(directory) = directory {
                        if !directory.exists() {
                            return Err(Error::at(&source,node.span,format!("imported package does not exist: {}",directory.display())));
                        }
                        imports.push(directory);
                    }
                }
            }
            self.sources.push(source);
        }
        let mut frontier = Vec::new();
        // A single import already uses the ordinary per-package batch. Only
        // inspect a frontier early when sibling packages can share workers.
        for import in imports.iter().filter(|_| imports.len() > 1) {
            let Ok(import) = absolute(import) else { continue; };
            if visited.contains(&import) { continue; }
            let support_library = import.starts_with(self.options.root.join("runtime"))
                && import != self.options.entry;
            // Discovery reports directory errors in its original order. A
            // failed speculative parse is likewise retained until consumed.
            if let Ok(files) = package_files(&import, self.frontend.test && !support_library, &self.options.overlays) {
                frontier.extend(files.into_iter().map(|path| (path, import.is_dir())));
            }
        }
        self.parse_imports(frontier);
        for import in imports {
            self.discover(&import, visited)?;
        }
        Ok(())
    }
    fn parse_imports(&mut self, files: Vec<(PathBuf, bool)>) {
        let mut seen = HashSet::new();
        let pending = files.into_iter().filter(|key| seen.insert(key.clone())
            && !self.parsed_imports.contains_key(key)
            && !self.sources.iter().any(|source| source.path == key.0)).collect::<Vec<_>>();
        if pending.is_empty() { return; }
        let mut shared_frontend = self.frontend.clone();
        shared_frontend.normalized_cache = true;
        if shared_frontend.cache_dir.is_some() {
            shared_frontend.cache_dir = Some(self.options.root.join(".thor-cache/sdk-parser"));
        }
        let parsed = crate::parallel::map(&pending, |(path, package)| {
            let frontend = if path.starts_with(self.options.root.join("runtime"))
                && path.extension().is_some_and(|ext| ext == "odin") {
                &shared_frontend
            } else { &self.frontend };
            frontend.parse_overlay(path, *package, self.options.overlays.get(path).map(String::as_str))
        });
        self.parsed_imports.extend(pending.into_iter().zip(parsed));
    }
    /// Load the same source/type facts used by lowering, without generating or
    /// compiling output. The differential analysis harness uses this entry.
    pub fn inspect_sources(&mut self) -> Result<()> {
        let _phase = crate::profile::span("analysis.inspect_sources");
        self.discover_type_facts()?;
        self.discover_expression_facts()?;
        self.discover_call_facts()?;
        self.source_facts = Some(SourceFacts {
            sources: self.sources.clone(), flags: self.options.flags.clone(),
        });
        Ok(())
    }
    pub(crate) fn resolver(&self) -> Resolver<'_> {
        Self::resolver_for(&self.sources, &self.options.flags, &self.resolver_index, &self.resolver_state)
    }
    pub(crate) fn retain_resolver(&self, resolver: Resolver<'_>) {
        if resolver.can_freeze() {
            *self.resolver_state.borrow_mut() = Some(resolver.freeze());
        }
    }
    fn resolver_for<'a>(sources: &'a [Source], flags: &[String], index: &std::cell::RefCell<Option<IndexedSources>>,
        state: &std::cell::RefCell<Option<crate::semantics::ResolverSeed>>) -> Resolver<'a> {
        if let Some(saved) = state.borrow_mut().take().filter(|saved| saved.matches(sources, flags)) {
            return saved.resume(sources);
        }
        let cached = index.borrow();
        if let Some(cached) = cached.as_ref().filter(|cached| {
            cached.sources.len() == sources.len()
                && cached.sources.iter().zip(sources).all(|(old, current)| {
                    old.shares_syntax(current) && old.shared_native == current.shared_native
                        && old.collection_roots == current.collection_roots
                })
        }) {
            return Resolver::with_index(sources, flags, Some(&cached.index));
        }
        drop(cached);
        let resolver = Resolver::with_flags(sources, flags);
        *index.borrow_mut() = Some(IndexedSources {
            sources:sources.to_vec(), index:resolver.syntax_index(),
        });
        resolver
    }
    pub fn lower(&mut self) -> Result<PathBuf> {
        let _phase = crate::profile::span("lowering.total");
        let ready = self.source_facts.as_ref().is_some_and(|facts| {
            facts.flags == self.options.flags && facts.sources.len() == self.sources.len()
                && facts.sources.iter().zip(&self.sources).all(|(old, current)| {
                    old.shares_syntax(current) && old.shared_native == current.shared_native
                        && old.collection_roots == current.collection_roots
                })
        });
        if !ready {
            self.inspect_sources()?;
        } else {
            crate::profile::count("analysis.inherited_source_facts", 1);
        }
        self.schedule_evaluations()?;
        self.backend_runtime = runtimeview::prepare(&self.options.root)?;
        let mut resolver = Self::resolver_for(&self.sources, &self.options.flags, &self.resolver_index, &self.resolver_state);
        resolver.set_evaluations(self.evaluated.clone());
        resolver.prepare_contexts();
        if let Some(plan) = &self.evaluation {
            crate::specialization::Specializations::apply_bindings(&plan.bindings, &mut resolver);
        }
        crate::lower::prime_logical_constants(&mut resolver);
        resolver.begin_symbol_queries();
        let mut privileges = self.permission_cache.borrow_mut().analyze(
            &mut resolver,
            &self.options.flags,
            self.options.editor_info,
            self.evaluation.as_ref(),
        );
        self.editor_info = privileges.editor_info.clone();
        let purity = if let Some(plan) = &self.evaluation {
            if plan.requests.is_empty() {
                crate::purity::Purity::analyze_entries(&mut resolver, &plan.roots, &plan.entries)
            } else {
                crate::purity::Purity::analyze_calls(&mut resolver, &plan.requests)
            }
        } else {
            crate::purity::Purity::analyze(&mut resolver)
        };
        if let Some(plan) = &self.evaluation {
            for request in &plan.requests {
                purity.validate(&resolver, *request)?;
            }
        }
        if let Some(plan) = &self.evaluation {
            privileges
                .pure_faults
                .retain(|root, _| purity.checked.contains(root));
            privileges.unresolved.retain(|site, _| {
                plan.files
                    .get(&self.sources[site.file].path)
                    .is_none_or(|view| view.retains(&self.sources[site.file], site.node))
            });
        }
        let mut local_borrowing = crate::borrowlocals::Facts::analyze(&mut resolver, &purity);
        let borrowing = crate::borrowprogram::Model::prepare(&mut resolver, &purity)
            .emission(&mut resolver, &purity);
        local_borrowing
            .checks
            .retain(|site, _| !borrowing.covers(&resolver, *site));
        // In the ported domain, native checked variants enforce the reference
        // symbolic guards at the selected call. The earlier concrete summary
        // must not reject a generic/inactive path before those guards run.
        privileges
            .pure_faults
            .retain(|root, _| !borrowing.procedures.contains(root));
        privileges.unresolved.retain(|site, _| {
            !local_borrowing.covers(&resolver, *site) && !borrowing.covers(&resolver, *site)
        });
        self.pure_call_paths = purity.call_paths.clone();
        for (site, fault) in &purity.conditional {
            let source = &self.sources[site.file];
            let token = format!(
                "{}conditional_pure_error_{}_end",
                source.prefix,
                source.node(site.node).span.start
            );
            let notes=fault.related.iter().map(|reference|{
                let source=&self.sources[reference.file];let span=source.node(reference.node).span;
                let (line,column)=source.position(span.start);
                json!({"pos":{"file":source.path,"offset":span.start,"end_offset":span.end,"line":line,"column":column},
                    "msgs":["Required by this pure call; the selected implementation must obey pure function rules."]})
            }).collect();
            self.pure_error_sites.insert(token.clone(), *site);
            self.related.insert(
                token,
                LoweringNotes { operation: None, related: notes },
            );
        }
        let sdk = sdkmirror::SdkMirror::plan(
            &resolver,
            &purity,
            &mut self.collections,
            &self.destination,
            &self.options.overlays,
            (&self.options.root.join("runtime"), &self.backend_runtime),
            self.evaluation.as_ref(),
        )?;
        self.native_environment = sdk.environment.clone();
        for (root, faults) in &privileges.pure_faults {
            if let Some(fault) = faults.first() {
                let source = &self.sources[fault.operation.file];
                let mut error = Error::at(
                    source,
                    source.node(fault.operation.node).span,
                    &fault.message,
                );
                error.diagnostics[0]["related"]=json!(fault.related.iter().chain(std::iter::once(root)).map(|r|{
                    let s=&self.sources[r.file];let span=s.node(r.node).span;let (line,column)=s.position(span.start);
                    json!({"pos":{"file":s.path,"offset":span.start,"end_offset":span.end,"line":line,"column":column},"msgs":["Required by this pure call."]})
                }).collect::<Vec<_>>());
                return Err(error);
            }
        }
        if let Some((site, _)) = privileges.unresolved.iter().next() {
            let source = &self.sources[site.file];
            return Err(Error::unsupported(
                source,
                source.node(site.node).span,
                "unresolved native generic slice branch specialization",
            ));
        }
        let first = self
            .sources
            .iter()
            .position(|source| {
                if self.file_entry {
                    source.path == self.options.entry
                } else {
                    source.path.parent() == Some(self.options.entry.as_path())
                }
            })
            .unwrap_or(0);
        let memory_map = format!("{}memory_map", self.sources[first].prefix);
        let mut memory_collection = "__thor_memory".to_string();
        while self.collections.contains_key(&memory_collection) {
            memory_collection.push('_');
        }
        if self.memory.enabled() {
            self.collections
                .insert(memory_collection.clone(), self.options.root.join("runtime"));
        }
        let mut paths = BTreeSet::new();
        let mut planned = vec![];
        let mut project_collection = "__thor_project".to_string();
        while self.collections.contains_key(&project_collection) {
            project_collection.push('_');
        }
        self.collections
            .insert(project_collection.clone(), self.destination.clone());
        let mut jobs=Vec::new();
        let mut planning_error=None;
        for (file,source) in self.sources.iter().enumerate() {
            if source.shared_native && !sdk.adapts(&source.path) { continue; }
            match filelowering::Job::prepare(self,file,&sdk,&project_collection,&mut paths) {
                Ok(job)=>jobs.push(job),
                Err(error)=>{planning_error=Some(error);break;}
            }
        }
        let inputs=filelowering::Inputs::new(self,&privileges,&purity,&local_borrowing,
            &borrowing,&memory_collection,&memory_map);
        let results=if jobs.len()>1 && crate::parallel::workers()>1 && resolver.can_freeze() {
            let seed=resolver.freeze();
            crate::profile::count("emit.parallel_files",jobs.len() as u64);
            crate::parallel::map_with_local(&jobs,||seed.fork(),|worker,job|inputs.emit(worker,job))
        } else {
            jobs.iter().map(|job|inputs.emit(&mut resolver,job)).collect()
        };
        // Fold in source order, including any planning failure after this prefix.
        // No successful later worker can mask the first source's error.
        for (job,result) in jobs.into_iter().zip(results) {
            let output=result?;
            self.related.extend(output.related);
            self.type_sites.extend(output.type_sites);
            self.pure_error_sites.extend(output.pure_error_sites);
            self.errors.extend(output.errors);
            planned.push((self.destination.join(job.relative),(job.file,output.emitted)));
        }
        if let Some(error)=planning_error {return Err(error);}
        if self
            .sources
            .iter()
            .any(|source| source.path.starts_with(&self.destination))
        {
            return Err(Error::new(
                "output directory cannot contain original source files",
            ));
        }
        if self.destination.is_symlink() {
            return Err(Error::new("output directory must not be a symlink"));
        }
        let manifest_path = self.destination.join("thor-manifest.json");
        let old = if manifest_path.is_file() {
            serde_json::from_slice::<Value>(&fs::read(&manifest_path)?).map_err(|_| {
                Error::new(format!(
                    "invalid generated-output manifest: {}",
                    manifest_path.display()
                ))
            })?
        } else if self.destination.is_dir() && fs::read_dir(&self.destination)?.next().is_some() {
            return Err(Error::new(format!(
                "output directory is not empty and is not managed by Thor: {}",
                self.destination.display()
            )));
        } else {
            json!({})
        };
        self.outputs = planned.into_iter().collect();
        self.memory.finish(
            &mut self.outputs,
            &self.sources,
            first,
            &memory_collection,
            &self.options.root,
            &memory_map,
        )?;
        let _publish = crate::profile::span("emit.publish");
        let raw_sources: Vec<_> = self
            .sources
            .iter()
            .map(|source| {
                (
                    source.path.to_string_lossy().into_owned(),
                    source.raw.clone(),
                )
            })
            .collect();
        let mut files: BTreeMap<PathBuf, Vec<u8>> = BTreeMap::new();
        files.extend(sdk.replacements);
        let maps = self.options.maps || self.options.command == "transpile"
            || self.options.flags.iter().any(|flag| flag == "-debug");
        let outputs = self.outputs.iter().collect::<Vec<_>>();
        let artifacts = crate::parallel::map(&outputs, |(path, (_, emitted))| -> Result<_> {
            let _phase = crate::profile::span("emit.serialize");
            let mut artifacts = vec![((*path).clone(), emitted.text.as_bytes().to_vec())];
            if maps {
                let mut map = emitted.map_json(path, &raw_sources);
                map["generated_sha256"] = json!(sha256(emitted.text.as_bytes()));
                artifacts.push((
                    PathBuf::from(format!("{}.map.json", path.display())),
                    serde_json::to_vec(&map)?,
                ));
            }
            Ok(artifacts)
        });
        for artifacts in artifacts {
            files.extend(artifacts?);
        }
        let generated: Vec<_> = files
            .keys()
            .chain(sdk.links.keys())
            .map(|path| {
                path.strip_prefix(&self.destination)
                    .unwrap()
                    .to_string_lossy()
                    .into_owned()
            })
            .collect();
        let mut manifest = json!({"thor_version":VERSION,"engine":"rust","migration_complete":false,"entry":self.options.entry,"source_map_version":1,"debug_map_version":1,
            "generated":generated,"odin_flags":self.native_flags(),"odin_environment":self.native_environment,"debug_roots":[],"shared_maps":[]});
        if self.options.flags.iter().any(|flag| flag == "-debug") {
            let mut roots = vec![self.options.root.join("runtime")];
            if let Some(root) = self
                .sources
                .iter()
                .find_map(|source| source.info["odin_root"].as_str())
            {
                roots.extend(["base", "core", "vendor"].map(|name| Path::new(root).join(name)));
            }
            manifest["debug_roots"] = json!(roots);
        }
        if self.memory.enabled() {
            manifest["memory_debug"] = json!({"tracking":self.memory.tracking,"address_sanitizer":self.memory.address,"source_map_version":1,"llvm_symbolizer":self.memory.symbolizer});
        }
        // Lower every source before publishing any of them. Ownership is
        // recorded first so an interrupted first write stays recoverable.
        fs::create_dir_all(&self.destination)?;
        for path in files.keys().chain(sdk.links.keys()) {
            self.validate_output(path)?;
        }
        atomic_write(&manifest_path, &serde_json::to_vec_pretty(&manifest)?)?;
        for (path, data) in &files {
            atomic_write(path, data)?;
        }
        for (path, target) in &sdk.links {
            sdkmirror::write_link(path, target)?;
        }
        for relative in old["generated"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str())
        {
            let path = self.destination.join(relative);
            if files.contains_key(&path)
                || sdk.links.contains_key(&path)
                || self.validate_output(&path).is_err()
            {
                continue;
            }
            if path.is_file() || path.is_symlink() {
                fs::remove_file(path)?;
            }
        }
        if self.file_entry {
            self.outputs
                .iter()
                .find(|(_, (file, _))| self.sources[*file].path == self.options.entry)
                .map(|(path, _)| path.clone())
                .ok_or_else(|| Error::new("entry file excluded by target"))
        } else {
            self.outputs
                .iter()
                .find(|(_, (file, _))| {
                    self.sources[*file].path.parent() == Some(self.options.entry.as_path())
                })
                .map(|(path, _)| path.parent().unwrap().to_path_buf())
                .ok_or_else(|| Error::new("entry package excluded by target"))
        }
    }
    pub fn set_evaluation(&mut self, plan: crate::evaluation::Plan) {
        self.evaluation = Some(plan);
    }
    fn discover_type_facts(&mut self) -> Result<()> {
        self.discover_type_facts_from(BTreeSet::new())
    }
    fn discover_type_facts_from(&mut self, mut packages: BTreeSet<PathBuf>) -> Result<()> {
        let _phase = crate::profile::span("analysis.discover_types");
        let general = self.sources.iter().any(|s| {
            s.nodes.iter().any(|n| {
                n.immutable
                    || n.data["pure"] == true
                    || matches!(n.kind.as_str(), "Run" | "Interpolation" | "OrPanic")
            }) || s.selections.iter().any(|selection| selection.wildcard)
                || s.info["mut_parameters"]
                    .as_array()
                    .is_some_and(|rows| !rows.is_empty())
        });
        if packages.is_empty() && !general {
            return Ok(());
        }
        let Some(odin_root) = self
            .sources
            .iter()
            .find_map(|s| s.info["odin_root"].as_str())
            .map(PathBuf::from)
        else {
            return Ok(());
        };
        let mut roots = self.collections.clone();
        let mut frontend = self.frontend.clone();
        frontend.normalized_cache = true;
        if frontend.cache_dir.is_some() {
            frontend.cache_dir = Some(self.options.root.join(".thor-cache/sdk-parser"));
        }
        for name in ["base", "core", "vendor"] {
            roots
                .entry(name.into())
                .or_insert_with(|| odin_root.join(name));
        }
        if let Some(base) = roots.get("base").filter(|_| general) {
            let runtime = base.join("runtime");
            if runtime.join("core.odin").is_file() {
                packages.insert(runtime);
            }
        }
        for source in &self.sources {
            if !general || source.shared_native || source.path.starts_with(self.options.root.join("runtime")) {
                continue;
            }
            for node in &source.nodes {
                if node.kind != "Import" {
                    continue;
                }
                let import = unquote(&node.op)?;
                if ["base:builtin", "base:intrinsics"].contains(&import.as_str()) {
                    continue;
                }
                if let Some((collection, suffix)) = import.split_once(':') {
                    if ["base", "core", "vendor"].contains(&collection) {
                        if let Some(root) = roots.get(collection) {
                            packages.insert(root.join(suffix));
                        }
                    }
                }
            }
        }
        let mut pending = Vec::new();
        let mut seen = self.sources.iter().map(|source| source.path.clone()).collect::<HashSet<_>>();
        for package in packages {
            if !package.is_dir() {
                continue;
            }
            let files = if roots
                .get("base")
                .is_some_and(|base| package == base.join("runtime"))
            {
                vec![package.join("core.odin")]
            } else {
                // The imported SDK is a library even when the project's entry
                // package is being tested. Its own *_test files are not inputs.
                package_files(&package, false, &self.options.overlays)?
            };
            pending.extend(files.into_iter().filter(|path| seen.insert(path.clone())));
        }
        let parsed = crate::parallel::map(&pending, |path| {
            frontend.parse_overlay(path, true, self.options.overlays.get(path).map(String::as_str))
        });
        for source in parsed {
            let mut source = source?;
            if source.excluded {
                continue;
            }
            // Dependencies of unused SDK bodies are discovered when selected.
            source.shared_native = true;
            source.collection_roots = roots.clone();
            self.sources.push(source);
        }
        for source in &mut self.sources {
            source.collection_roots = roots.clone();
        }
        Ok(())
    }
    fn validate_output(&self, path: &Path) -> Result<()> {
        let relative = path
            .strip_prefix(&self.destination)
            .map_err(|_| Error::new("generated output escapes destination"))?;
        if relative
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
        {
            return Err(Error::new("invalid generated output path"));
        }
        let mut parent = path.parent();
        while let Some(directory) = parent {
            if directory == self.destination {
                break;
            }
            if directory.is_symlink() {
                return Err(Error::new("generated output parent must not be a symlink"));
            }
            parent = directory.parent();
        }
        Ok(())
    }
    pub(crate) fn native_flags(&self) -> Vec<String> {
        let mut result = vec![];
        result.extend(self.memory.extra.iter().cloned());
        for flag in &self.options.flags {
            if !flag.starts_with("-collection:")
                && !matches!(flag.as_str(), "-json-errors" | "-keep-executable")
            {
                result.push(flag.clone());
            }
        }
        for (name, base) in &self.collections {
            let base = if *base == self.options.root.join("runtime") {
                &self.backend_runtime
            } else {
                base
            };
            result.push(format!("-collection:{name}={}", base.display()));
        }
        for source in &self.sources{for (name,directory) in crate::lower::foreign_assets(source).0 {result.push(format!("-collection:{name}={}",directory.display()));}}
        if self.sources.iter().any(|source| {
            source.path.parent() == Some(self.options.root.join("runtime/app").as_path())
        }) {
            if let Some(layout) = &self.publish_layout {
                result.push(format!("-define:THOR_APP_LAYOUT={layout}"));
            } else {
                let root = if self.file_entry {
                    self.options.entry.parent().unwrap()
                } else {
                    &self.options.entry
                };
                result.push(format!("-define:THOR_APP_SOURCE_ROOT={}", root.display()));
            }
        }
        result
    }
    pub fn compile(&mut self, target: &Path) -> Result<i32> {
        let _phase = crate::profile::span("backend.final");
        let action = self.options.command.clone();
        let action = action.as_str();
        let mut command = Command::new(&self.frontend.odin);
        command.envs(&self.native_environment);
        command
            .arg(if action == "vet" {
                "check"
            } else if matches!(action, "run" | "test" | "publish") {
                "build"
            } else {
                action
            })
            .arg(target);
        if self.file_entry {
            command.arg("-file");
        }
        let effective_flags = self
            .native_override
            .clone()
            .unwrap_or_else(|| self.native_flags());
        command.args(&effective_flags);
        if !self.options.raw_diagnostics {
            command.arg("-json-errors");
        }
        let explicit = effective_flags
            .iter()
            .find_map(|flag| flag.strip_prefix("-out:"));
        let binary = if let Some(path) = explicit {
            absolute(Path::new(path))?
        } else if action == "build" {
            let cwd = env::current_dir()?;
            let name = cwd.file_name().unwrap_or_default().to_string_lossy();
            cwd.join(format!("{name}{}", env::consts::EXE_SUFFIX))
        } else {
            self.destination
                .join(format!("program{}", env::consts::EXE_SUFFIX))
        };
        if action == "build"
            && explicit.is_none()
            && [
                self.options.root.join("thor"),
                self.options.root.join("thor-rust"),
            ]
            .contains(&binary)
        {
            return Err(Error::new("The default output would overwrite a Thor launcher; choose another name with -out:name."));
        }
        if matches!(action, "build" | "run" | "test") && explicit.is_none() {
            command.arg(format!("-out:{}", binary.display()));
        }
        if action == "test" {
            command.arg("-build-mode:test");
        }
        if !self.options.raw_diagnostics
            && !self
                .options
                .flags
                .iter()
                .any(|flag| flag.starts_with("-max-error-count:"))
        {
            command.arg("-max-error-count:1000");
        }
        let output = if self.options.raw_diagnostics {
            let status = command
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit())
                .status()?;
            evaluationexec::Captured {
                status,
                stdout: vec![],
                stderr: vec![],
                timed_out: false,
                oversized: false,
            }
        } else {
            self.run_backend(&mut command, |command| {
                let output = command.output()?;
                Ok(evaluationexec::Captured {
                    status: output.status,
                    stdout: output.stdout,
                    stderr: output.stderr,
                    timed_out: false,
                    oversized: false,
                })
            })?
        };
        let mut backend_status = output.status.code().unwrap_or(1);
        let mut errors = if output.status.code().is_none() {
            #[cfg(unix)]
            let number = {
                use std::os::unix::process::ExitStatusExt;
                output.status.signal().unwrap_or(0)
            };
            #[cfg(not(unix))]
            let number = 0;
            backend_status = 128 + number;
            let display = output.status.to_string();
            let fallback = number.to_string();
            let name = display
                .rsplit_once('(')
                .and_then(|(_, suffix)| suffix.strip_suffix(')'))
                .unwrap_or(&fallback);
            let mut messages=vec![format!("Odin compiler terminated by {name} (signal {number}). This is a backend compiler failure; no source location was provided.")];
            if !output.stderr.is_empty() {
                messages.push(String::from_utf8_lossy(&output.stderr).trim().to_string());
            }
            vec![json!({"type":"error","stage":"toolchain","pos":null,"msgs":messages})]
        } else if output.stderr.is_empty() {
            vec![]
        } else if let Ok(packet) = serde_json::from_slice::<Value>(&output.stderr) {
            packet["errors"].as_array().cloned().unwrap_or_default()
        } else {
            vec![
                json!({"type":if output.status.success(){"warning"}else{"error"},"stage":"toolchain","pos":null,
                "msgs":[String::from_utf8_lossy(&output.stderr).trim()]}),
            ]
        };
        for source in &self.sources {
            errors.extend(
                source.info["diagnostics"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .cloned(),
            );
        }
        if output.status.success() && (action == "vet" || self.options.vet.is_some()) {
            let checks = self
                .options
                .vet
                .clone()
                .unwrap_or_else(|| vec!["immutable".into(), "pure".into()]);
            errors.extend(crate::vet::run(
                self,
                &checks,
                self.options.vet_apply,
                self.options.vet_ask,
            )?);
        }
        let reported_errors = if self.options.raw_diagnostics && errors.is_empty() {
            false
        } else {
            self.report(&errors)
        };
        if !output.status.success() || reported_errors {
            return Ok(if backend_status == 0 {
                1
            } else {
                backend_status
            });
        }
        if matches!(action, "build" | "run" | "test")
            && self.options.flags.iter().any(|flag| flag == "-debug")
            && binary.is_file()
        {
            let path = self.destination.join("thor-manifest.json");
            let mut manifest: Value = serde_json::from_slice(&fs::read(&path)?)?;
            manifest["debug_program"] = json!({"path":binary,"sha256":sha256(&fs::read(&binary)?)});
            atomic_write(&path, &serde_json::to_vec_pretty(&manifest)?)?;
        }
        if action == "run" || action == "test" {
            let status = Command::new(&binary)
                .args(&self.options.args)
                .stdin(Stdio::inherit())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit())
                .status();
            if !self
                .options
                .flags
                .iter()
                .any(|flag| flag == "-keep-executable")
            {
                let _ = fs::remove_file(&binary);
            }
            let status = status?;
            if let Some(code) = status.code() {
                return Ok(code);
            }
            #[cfg(unix)]
            {
                use std::os::unix::process::ExitStatusExt;
                if let Some(signal) = status.signal() {
                    return Ok(256 - signal);
                }
            }
            return Ok(1);
        }
        Ok(0)
    }
    pub fn translate(&self, errors: &[Value]) -> Vec<Value> {
        let translated=errors.iter().map(|error|{
            let mut error=error.clone();
            let mut purity_cause = None;
            let pure_site=error["msgs"].as_array().into_iter().flatten().filter_map(Value::as_str)
                .find_map(|message|self.pure_error_sites.iter().find(|(token,_)|message.contains(token.as_str())).map(|(_,site)|*site));
            let mut messages = error["msgs"].as_array().into_iter().flatten().filter_map(Value::as_str);
            let lowering_notes = |message: &str| {
                self.related.iter().find(|(token, _)| message.contains(token.as_str()))
            };
            let primary = messages.next().and_then(lowering_notes);
            let permission_site = primary.and_then(|(token, notes)| notes.operation.map(|site| (token, site)));
            if let Some((_, notes)) = primary.or_else(|| messages.find_map(lowering_notes)) {
                error["related"] = json!(notes.related);
            }
            let mut mapped = None;
            let mut expression_texts=vec![];
            let file=error["pos"]["file"].as_str().map(PathBuf::from);
            if let Some((index,emitted))=file.as_ref().and_then(|file|self.outputs.get(file)){
                let offset=error["pos"]["offset"].as_u64().unwrap_or(0) as usize;
                let length=error["pos"]["end_column"].as_u64().unwrap_or(0).saturating_sub(error["pos"]["column"].as_u64().unwrap_or(0)) as usize;
                if let Some(origin)=emitted.lookup(offset,length){
                    let source=&self.sources[*index];let (line,column)=source.position(origin.span.start);let (end_line,end_column)=source.position(origin.span.end);
                    if error["msgs"][0].as_str().is_some_and(|message|message.contains("'where' clause")) {
                        for (first,last,expression) in &emitted.expansions {
                            if expression.span==origin.span&&expression.file==origin.file {
                                expression_texts.push((emitted.text[*first..*last].to_string(),source.slice(origin.span).to_string()));
                            }
                        }
                    }
                    error["pos"]=json!({"file":origin.file,"offset":origin.span.start,"end_offset":origin.span.end,"line":line,"column":column,"end_line":end_line,"end_column":end_column});
                    mapped = Some(origin);
                }
            }
            if let Some(messages)=error["msgs"].as_array_mut(){for message in messages{
                if let Some(text)=message.as_str(){let mut text=self.remap_references(text);for (generated,original) in &expression_texts{if text.trim()==generated.trim(){let indent=&text[..text.len()-text.trim_start().len()];text=format!("{indent}{original}");break;}}for (token,replacement) in &self.errors{if text.contains(token){text=replacement.clone();break;}}*message=json!(text);}
            }}
            if let Some((token, site)) = permission_site {
                // Odin can report the same guard from the emitted expression and its
                // type witness. Both refer to the source operation that emitted it.
                error["pos"] = crate::purediagnostics::position(&self.sources, site);
                error["msgs"] = json!([self.errors[token]]);
                error["stage"] = json!("checker");
            }
            if let Some((token,(site,description)))=error["msgs"].as_array().into_iter().flatten().filter_map(Value::as_str)
                .find_map(|message|self.type_sites.iter().find(|(token,_)|message.contains(token.as_str()))) {
                let source=&self.sources[site.file];let span=crate::purediagnostics::diagnostic_span(source,site.node);
                let (line,column)=source.position(span.start);let (end_line,end_column)=source.position(span.end);
                let primary=error["msgs"][0].as_str().unwrap_or("Invalid pure value type")
                    .trim_start_matches("Compile time panic: ").replace(token,"");
                error["pos"]=json!({"file":source.path,"offset":span.start,"end_offset":span.end,"line":line,"column":column,"end_line":end_line,"end_column":end_column});
                error["stage"]=json!("thor");
                error["msgs"]=json!([primary,format!("While checking {description}")]);
                purity_cause = Some(*site);
                self.pure_type_context(&mut error,*site,false);
            }
            if let Some(site)=pure_site {
                purity_cause = Some(site);
                self.pure_type_context(&mut error,site,true);
                if let Some(messages) = error["msgs"].as_array_mut() {
                    messages.retain(|message| !message.as_str().is_some_and(|text| {
                        let text = text.trim_start();
                        text.starts_with("Called within") || text.contains("instantiated as")
                    }));
                }
            }
            self.optional_diagnostic(&mut error);
            (error, mapped, purity_cause)
        }).collect();
        let mut translated=crate::diagnosticcleanup::clean(self,translated);
        for error in &mut translated {
            if let Some(messages)=error["msgs"].as_array_mut(){for message in messages {
                if let Some(text)=message.as_str(){*message=json!(self.hide_generated_names(text));}
            }}
        }
        translated
    }
    fn optional_diagnostic(&self,error:&mut Value) {
        if error["pos"]["file"].as_str().is_some_and(|file|Path::new(file)==self.options.root.join("runtime/optional/optional.odin")) {
            if let Some((file,offset))=self.instantiation_locations(error).into_iter().find(|(file,_)|!self.sources[*file].path.starts_with(self.options.root.join("runtime"))) {
                let source=&self.sources[file];
                let span=source.nodes.iter().filter(|node|node.kind=="OrPanic"&&node.span.start<=offset&&offset<node.span.end)
                    .min_by_key(|node|node.span.len()).map(|node|node.span).unwrap_or(Span::new(offset,offset));
                let pos=Error::at(source,span,"");error["pos"]=pos.diagnostics[0]["pos"].clone();
            }
        }
        let source=error["pos"]["file"].as_str().and_then(|file|self.sources.iter().find(|source|source.path==Path::new(file)));
        if let Some(source)=source {
            let start=error["pos"]["offset"].as_u64().unwrap_or(0) as usize;
            let end=error["pos"]["end_offset"].as_u64().unwrap_or(start as u64) as usize;
            let inside=|kind:&str|source.nodes.iter().any(|node|node.kind==kind&&node.span.start<=start&&end<=node.span.end);
            if inside("OrPanic")&&!inside("OrElse") {
                if let Some(messages)=error["msgs"].as_array_mut() {
                    for message in messages {
                        if let Some(text)=message.as_str() {*message=json!(text.replace("'or_else'","'or_panic'"));}
                    }
                }
            }
        }
    }
    fn hide_generated_names(&self,text:&str)->String {
        let mut text=text.to_string();
        for source in &self.sources {
            let mut cursor=0;
            while let Some(found)=text[cursor..].find(&source.prefix) {
                let start=cursor+found;
                let end=start+text[start..].bytes().take_while(|ch|ch.is_ascii_alphanumeric()||*ch==b'_').count();
                text.replace_range(start..end,"Thor expression");cursor=start+"Thor expression".len();
            }
        }
        text
    }
    fn pure_type_context(
        &self,
        error: &mut Value,
        site: crate::semantics::Ref,
        select_active: bool,
    ) {
        use crate::{purediagnostics, semantics::Ref};
        let Some(procedure) = purediagnostics::procedure_owner(&self.sources, site) else {
            return;
        };
        if purediagnostics::function_owner(&self.sources, site).is_some() {
            return;
        }
        let Some(paths) = self.pure_call_paths.get(&procedure) else {
            return;
        };
        let mut paths = paths
            .iter()
            .filter_map(|(_, via)| purediagnostics::function_context(&self.sources, via))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if paths.is_empty() {
            return;
        }
        if select_active {
            // Follow the reference call_contexts decision order: an outer fn
            // call in native instantiation notes wins; otherwise retain the
            // best matching inner paths, including ties when evidence is absent.
            let hints = self.instantiation_locations(error);
            let covered = |call: Ref| {
                let source = &self.sources[call.file];
                let span = source.node(call.node).span;
                hints.iter().any(|(file, offset)| {
                    *file == call.file && span.start <= *offset && *offset < span.end
                })
            };
            let active = paths
                .iter()
                .filter(|(_, via)| covered(via[0]))
                .copied()
                .collect::<Vec<_>>();
            if !active.is_empty() {
                paths = active;
            } else {
                let scores = paths
                    .iter()
                    .map(|(_, via)| via.iter().filter(|call| covered(**call)).count())
                    .collect::<Vec<_>>();
                let best = scores.iter().copied().max().unwrap_or(0);
                paths = paths
                    .into_iter()
                    .zip(scores)
                    .filter_map(|(path, score)| (score == best).then_some(path))
                    .collect();
            }
        }
        purediagnostics::contextualize(&self.sources, error, site, &paths);
    }
    pub(crate) fn instantiation_locations(&self, error: &Value) -> Vec<(usize, usize)> {
        self.source_locations(error,true)
    }
    pub(crate) fn source_locations(&self,error:&Value,instantiations_only:bool)->Vec<(usize,usize)> {
        let mut result = vec![];
        for text in error["msgs"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .filter(|message| !instantiations_only||message.contains("instantiated as"))
        {
            for (file, source) in self.sources.iter().enumerate() {
                let path = source.path.to_string_lossy();
                for (offset, _) in text.match_indices(path.as_ref()) {
                    let suffix = &text[offset + path.len()..];
                    let Some((line, column, _)) = reference_coordinates(suffix) else {
                        continue;
                    };
                    let Some(row) = source.raw.split_inclusive('\n').nth(line - 1) else {
                        continue;
                    };
                    let base = source
                        .raw
                        .split_inclusive('\n')
                        .take(line - 1)
                        .map(str::len)
                        .sum::<usize>();
                    let mut units = 0;
                    for (offset, ch) in row.char_indices() {
                        if units == column - 1 {
                            result.push((file, base + offset));
                            break;
                        }
                        units += ch.len_utf16();
                        if ch == '\n' || units >= column {
                            break;
                        }
                    }
                }
            }
        }
        result
    }
    fn remap_references(&self, text: &str) -> String {
        let mut text = text.to_string();
        for (path, (file, emitted)) in &self.outputs {
            let pattern = path.to_string_lossy();
            let mut cursor = 0;
            while let Some(found) = text[cursor..].find(pattern.as_ref()) {
                let start = cursor + found;
                let coordinates = start + pattern.len();
                let suffix = &text[coordinates..];
                let Some((line, column, length)) = reference_coordinates(suffix) else {
                    cursor = coordinates;
                    continue;
                };
                let end = coordinates + length;
                // Odin's textual instantiation notes count Unicode scalars;
                // source-map spans use UTF-8 bytes. Primary JSON positions
                // already include their byte offset and take another path.
                let row = emitted
                    .text
                    .split_inclusive('\n')
                    .nth(line.saturating_sub(1));
                let Some(row) = row else {
                    cursor = end;
                    continue;
                };
                let offset = emitted
                    .text
                    .split_inclusive('\n')
                    .take(line.saturating_sub(1))
                    .map(str::len)
                    .sum::<usize>()
                    + row
                        .chars()
                        .take(column.saturating_sub(1))
                        .map(char::len_utf8)
                        .sum::<usize>();
                if let Some(origin) = emitted.lookup(offset, 1) {
                    let (line, column) = self.sources[*file].position(origin.span.start);
                    let replacement = if suffix.starts_with(':') {
                        let delimiter = if suffix[..length].ends_with(':') { ":" } else { "" };
                        format!("{}:{line}:{column}{delimiter}", origin.file)
                    } else {
                        format!("{}({line}:{column})", origin.file)
                    };
                    text.replace_range(start..end, &replacement);
                    cursor = start + replacement.len();
                } else {
                    cursor = end;
                }
            }
        }
        text
    }
    fn report(&self, errors: &[Value]) -> bool {
        let mut seen = BTreeSet::new();
        let errors = self
            .translate(errors)
            .into_iter()
            .filter_map(|mut error| {
                if error["type"] == "warning"
                    && self
                        .options
                        .flags
                        .iter()
                        .any(|flag| flag == "-ignore-warnings")
                {
                    return None;
                }
                if let Some(messages) = error["msgs"].as_array_mut() {
                    for message in messages {
                        if let Some(text) = message
                            .as_str()
                            .and_then(|text| text.strip_prefix("Syntax Warning: "))
                        {
                            *message = json!(text);
                        }
                    }
                }
                if error["type"] == "warning"
                    && self
                        .options
                        .flags
                        .iter()
                        .any(|flag| flag == "-warnings-as-errors")
                {
                    error["type"] = json!("error");
                }
                let key = json!([
                    error["type"],
                    error["pos"]["file"],
                    error["pos"]["offset"],
                    error["msgs"]
                ])
                .to_string();
                seen.insert(key).then_some(error)
            })
            .collect::<Vec<_>>();
        let failed = errors.iter().any(|error| error["type"] == "error");
        if let Some(collected) = &self.collected_diagnostics {
            collected.borrow_mut().extend(errors);
        } else if self.options.json {
            let mut packet = json!({"error_count":errors.iter().filter(|error|error["type"]=="error").count(),"errors":errors});
            if self.options.editor_info {
                packet["slice_access"] = json!(self.editor_info);
            }
            eprintln!("{packet}");
        } else {
            for error in errors {
                let raw=error["pos"]["file"].as_str().and_then(|file|self.sources.iter().find(|source|source.path==Path::new(file))).map(|source|source.raw.as_str());
                eprintln!("{}",crate::diagnostic::text(&error,raw,self.options.flags.iter().any(|flag|flag=="-terse-errors"),self.options.flags.iter().any(|flag|flag=="-error-pos-style:unix")));
            }
        }
        failed
    }
}
pub fn print_diagnostic(error: &Value) {
    eprintln!("{}",crate::diagnostic::text(error,None,false,false));
}
