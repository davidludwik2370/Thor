//! Independent source emission over a frozen project semantic view. Planning
//! and publication remain ordered; workers never write generated files.
use super::*;
use crate::lower::Lower;

pub(super) struct Job {
    pub file: usize,
    pub relative: PathBuf,
    view: Option<crate::evaluation::FileView>,
    imports: BTreeMap<String, String>,
    entry: bool,
}
impl Job {
    pub fn prepare(
        project: &Project,
        file: usize,
        sdk: &sdkmirror::SdkMirror,
        project_collection: &str,
        paths: &mut BTreeSet<PathBuf>,
        extra_imports: &[String],
    ) -> Result<Self> {
        let source = &project.sources[file];
        let mut relative = if source.shared_native {
            sdk.output(&source.path)
                .and_then(|path| {
                    path.strip_prefix(&project.destination)
                        .ok()
                        .map(Path::to_path_buf)
                })
                .ok_or_else(|| Error::new("adapted SDK file has no output path"))?
        } else {
            project.generated_relative(&source.path)?.with_extension("odin")
        };
        if project.evaluation.is_some()
            && relative
                .file_name()
                .is_some_and(|name| name.to_string_lossy().ends_with("_test.odin"))
        {
            relative.set_file_name(format!(
                "thor_evaluation_{}_source.odin",
                source.prefix.trim_matches('_')
            ));
        }
        if !paths.insert(relative.clone()) {
            return Err(Error::new(format!(
                "generated filename collision: {}",
                relative.display()
            )));
        }
        let view = project
            .evaluation
            .as_ref()
            .and_then(|plan| plan.files.get(&source.path))
            .cloned();
        let mut import_paths = BTreeMap::new();
        let mut imports = source
            .nodes
            .iter()
            .filter(|node| node.kind == "Import")
            .map(|node| unquote(&node.op))
            .collect::<Result<Vec<_>>>()?;
        imports.extend(extra_imports.iter().cloned());
        if let Some(view) = project
            .evaluation
            .as_ref()
            .and_then(|plan| plan.files.get(&source.path))
        {
            imports.extend(view.imports.values().cloned());
        }
        for path in imports {
            let directory = if let Some((collection, suffix)) = path.split_once(':') {
                source
                    .collection_roots
                    .get(collection)
                    .map(|root| root.join(suffix))
            } else {
                source.path.parent().map(|parent| parent.join(&path))
            };
            if let Some(directory) = directory.and_then(|dir| crate::project::canonicalize(dir).ok()) {
                if project.sources.iter().any(|unit| {
                    !unit.shared_native && unit.path.parent() == Some(directory.as_path())
                }) {
                    let relative = project.generated_relative(&directory)?;
                    let suffix = if relative.as_os_str().is_empty() {
                        ".".to_string()
                    } else {
                        relative.to_string_lossy().replace('\\', "/")
                    };
                    import_paths.insert(path, format!("{project_collection}:{suffix}"));
                }
            }
        }
        if project.evaluation.is_some() {
            for dependency in &project.sources {
                if dependency.shared_native && !sdk.adapts(&dependency.path) {
                    continue;
                }
                let directory = dependency.path.parent().unwrap();
                let generated = if dependency.shared_native {
                    sdk.output(directory)
                        .ok_or_else(|| Error::new("evaluation SDK package has no output"))?
                } else {
                    project.destination.join(project.generated_relative(directory)?)
                };
                let suffix = generated
                    .strip_prefix(&project.destination)
                    .map_err(|_| Error::new("evaluation import outside generated tree"))?;
                let suffix = if suffix.as_os_str().is_empty() {
                    ".".to_owned()
                } else {
                    suffix.to_string_lossy().replace('\\', "/")
                };
                let path = crate::evaluation::dependency_path(source, directory);
                import_paths.insert(path, format!("{project_collection}:{suffix}"));
            }
        }
        let entry = if project.file_entry {
            source.path == project.options.entry
        } else {
            source.path.parent() == Some(project.options.entry.as_path())
        };
        Ok(Self {
            file,
            relative,
            view,
            imports: import_paths,
            entry,
        })
    }
}
pub(super) struct Inputs<'a> {
    native_checks: bool,
    override_runtime: bool,
    sources: &'a [Source],
    type_probes: &'a BTreeMap<crate::semantics::Ref, Vec<String>>,
    type_probe_imports: &'a BTreeMap<usize, BTreeMap<String, String>>,
    evaluated: &'a BTreeMap<crate::semantics::Ref, Value>,
    evaluation_witnesses: &'a BTreeMap<crate::semantics::Ref, String>,
    specialized: &'a crate::specialization::Cases,
    evaluation_skips: &'a BTreeMap<crate::semantics::Ref, bool>,
    evaluation_scheduled: bool,
    privileges: &'a crate::privileges::Facts,
    purity: &'a crate::purity::Facts,
    local_borrowing: &'a crate::borrowlocals::Facts,
    borrowing: &'a crate::borrowprogram::Emission,
    memory: &'a crate::memory::Memory,
    memory_collection: &'a str,
    memory_map: &'a str,
}
pub(super) struct Output {
    pub emitted: Emitted,
    pub errors: HashMap<String, String>,
    pub related: HashMap<String, LoweringNotes>,
    pub type_sites: HashMap<String, (crate::semantics::Ref, String)>,
    pub pure_error_sites: HashMap<String, crate::semantics::Ref>,
}
impl<'a> Inputs<'a> {
    pub fn new(
        project: &'a Project,
        privileges: &'a crate::privileges::Facts,
        purity: &'a crate::purity::Facts,
        local_borrowing: &'a crate::borrowlocals::Facts,
        borrowing: &'a crate::borrowprogram::Emission,
        memory_collection: &'a str,
        memory_map: &'a str,
    ) -> Self {
        Self {
            native_checks: nativechecks::active(project),
            override_runtime: project.options.comptime.overrides_runtime(),
            sources: &project.sources,
            type_probes: &project.type_probes,
            type_probe_imports: &project.type_probe_imports,
            evaluated: &project.evaluated,
            evaluation_witnesses: &project.evaluation_witnesses,
            specialized: &project.specialized,
            evaluation_skips: &project.evaluation_skips,
            evaluation_scheduled: project.evaluation_scheduled,
            privileges,
            purity,
            local_borrowing,
            borrowing,
            memory: &project.memory,
            memory_collection,
            memory_map,
        }
    }
    pub fn emit(&self, resolver: &mut Resolver<'_>, job: &Job) -> Result<Output> {
        evaluationexec::check_cancellation()?;
        let _file = crate::profile::span("emit.file");
        crate::profile::count("emit.files", 1);
        let file = job.file;
        let mut related = HashMap::new();
        let mut lower = Lower::new_with_view(file, resolver, job.view.clone())?;
        lower.override_runtime = self.override_runtime;
        if self.native_checks { lower.strip_native_vet_tags(); }
        lower.remap_imports(job.imports.clone());
        lower.apply_type_probes(
            self.type_probes
                .iter()
                .filter(|(scope, _)| scope.file == file)
                .map(|(scope, codes)| (scope.node, codes.clone()))
                .collect(),
            self.type_probe_imports
                .get(&file)
                .cloned()
                .unwrap_or_default(),
        );
        lower.apply_evaluation_results(
            self.evaluated
                .iter()
                .filter(|(node, _)| node.file == file)
                .map(|(node, value)| (node.node, value.clone()))
                .collect(),
        );
        lower.apply_evaluation_witnesses(
            self.evaluation_witnesses
                .iter()
                .filter(|(node, _)| node.file == file)
                .map(|(node, witness)| (node.node, witness.clone()))
                .collect(),
        );
        lower.apply_specialized_results(
            self.specialized
                .iter()
                .filter(|(node, _)| node.file == file)
                .map(|(node, cases)| (node.node, cases.clone()))
                .collect(),
            self.evaluation_scheduled,
        );
        lower.apply_evaluation_skips(
            self.evaluation_skips
                .iter()
                .filter(|(node, _)| node.file == file)
                .map(|(node, unchecked)| (node.node, *unchecked))
                .collect(),
        );
        for (token, issues) in lower.apply_privileges(self.privileges) {
            let issue = issues[0];
            let notes = issues.iter().flat_map(|issue|
                std::iter::once((issue.seed, "Read-only view declared here."))
                .chain(
                    issue
                        .related
                        .iter()
                        .map(|site| (*site, "Slice effect occurs here.")),
                ))
                .map(|(site, message)| crate::purediagnostics::note(self.sources, site, message))
                .collect();
            related.insert(
                token,
                LoweringNotes {
                    operation: Some(issue.operation),
                    related: notes,
                },
            );
        }
        lower.apply_local_borrowing(self.local_borrowing);
        lower.apply_borrowing(self.borrowing);
        lower.apply_purity(self.purity)?;
        lower.instrument_memory(
            self.memory,
            self.memory_collection,
            self.memory_map,
            job.entry,
        );
        let (emitted, errors) = lower.finish()?;
        for (token, site) in std::mem::take(&mut lower.expression_error_sites) {
            related.insert(token, LoweringNotes {operation: Some(site), related: vec![]});
        }
        for (token,seed) in std::mem::take(&mut lower.immutable_error_sites){
            related.entry(token).or_insert(LoweringNotes{operation:None,related:vec![]}).related.push(
                crate::purediagnostics::note(self.sources,seed,"Immutable storage declared here."));
        }
        let output = Output {
            emitted,
            errors,
            related,
            type_sites: std::mem::take(&mut lower.type_sites),
            pure_error_sites: std::mem::take(&mut lower.pure_error_sites),
        };
        evaluationexec::check_cancellation()?;
        Ok(output)
    }
}
