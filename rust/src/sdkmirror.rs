//! Project-specific checked SDK files share one sparse native package tree.
//! A layout-specific root prevents a prior directory link becoming a write parent.
use super::*;

#[derive(Default)]
pub(super) struct SdkMirror {
    pub environment: BTreeMap<String, PathBuf>,
    pub links: BTreeMap<PathBuf, PathBuf>,
    pub replacements: BTreeMap<PathBuf, Vec<u8>>,
    roots: BTreeMap<PathBuf, PathBuf>,
    changed: BTreeSet<PathBuf>,
    runtime: PathBuf,
    backend_runtime: PathBuf,
}
impl SdkMirror {
    pub fn plan(
        resolver: &Resolver<'_>,
        facts: &crate::purity::Facts,
        collections: &mut BTreeMap<String, PathBuf>,
        destination: &Path,
        overlays: &BTreeMap<PathBuf, String>,
        runtime_paths: (&Path, &Path),
        evaluation: Option<&crate::evaluation::Plan>,
    ) -> Result<Self> {
        let sources = resolver.sources;
        let (runtime, backend_runtime) = runtime_paths;
        let mut changed = facts
            .targets
            .values()
            .flatten()
            .filter_map(|target| {
                let source = &sources[target.file];
                source.shared_native.then(|| source.path.clone())
            })
            .collect::<BTreeSet<_>>();
        changed.extend(resolver.parameter_witnesses.values().filter_map(|witness| {
            let source = &sources[witness.declaration.file];
            (source.shared_native && evaluation.is_none_or(|plan| {
                plan.emits(source, witness.declaration.node)
            })).then(|| source.path.clone())
        }));
        if let Some(plan) = evaluation {
            changed.extend(
                sources
                    .iter()
                    .filter(|source| {
                        source.shared_native
                            && !source.path.starts_with(runtime)
                            && plan
                                .files
                                .get(&source.path)
                                .is_some_and(|view| !view.keep.is_empty())
                    })
                    .map(|source| source.path.clone()),
            );
            let packages = changed
                .iter()
                .filter_map(|path| path.parent().map(Path::to_path_buf))
                .collect::<BTreeSet<_>>();
            changed.extend(
                sources
                    .iter()
                    .filter(|source| {
                        source.shared_native
                            && source
                                .path
                                .parent()
                                .is_some_and(|path| packages.contains(path))
                    })
                    .map(|source| source.path.clone()),
            );
        }
        if changed.is_empty() {
            return Ok(Self::default());
        }
        let sdk = sources
            .iter()
            .find_map(|s| s.info["odin_root"].as_str())
            .map(fs::canonicalize)
            .transpose()?;
        let mut roots = BTreeSet::new();
        for path in &changed {
            if let Some(sdk) = sdk.as_ref().filter(|sdk| path.starts_with(sdk)) {
                roots.insert(sdk.clone());
                continue;
            }
            let root = sources
                .iter()
                .flat_map(|s| s.collection_roots.values())
                .filter_map(|root| fs::canonicalize(root).ok())
                .filter(|root| path.starts_with(root))
                .max_by_key(|root| root.components().count())
                .ok_or_else(|| {
                    Error::new(format!(
                        "checked native source has no collection root: {}",
                        path.display()
                    ))
                })?;
            roots.insert(root);
        }
        changed.extend(
            overlays
                .keys()
                .filter(|path| roots.iter().any(|root| path.starts_with(root)))
                .cloned(),
        );
        let layout = sha256(&serde_json::to_vec(&(&roots, &changed))?);
        let base = destination.join(format!(".thor-sdk-linked-{}", &layout[..12]));
        let mut this = Self {
            changed,
            runtime: runtime.to_path_buf(),
            backend_runtime: backend_runtime.to_path_buf(),
            ..Self::default()
        };
        for root in roots {
            let native = sdk.as_ref() == Some(&root);
            let output = if native {
                base.join("odin")
            } else {
                base.join(format!(
                    "collection-{}",
                    &sha256(root.to_string_lossy().as_bytes())[..12]
                ))
            };
            this.roots.insert(root.clone(), output.clone());
            if native {
                if evaluation.is_none() {
                    this.environment.insert("ODIN_ROOT".into(), output);
                }
                for name in ["base", "core", "vendor", "shared", "bin", "lib"] {
                    let path = root.join(name);
                    if path.exists() {
                        this.include(&path)?;
                    }
                }
            } else {
                this.include(&root)?;
            }
        }
        for (path, raw) in overlays {
            if this.changed.contains(path) {
                let output = this
                    .output(path)
                    .ok_or_else(|| Error::new("SDK overlay has no output root"))?;
                this.replacements.insert(output, raw.as_bytes().to_vec());
            }
        }
        for root in collections.values_mut().filter(|_| evaluation.is_none()) {
            if let Ok(canonical) = fs::canonicalize(&root) {
                if let Some(output) = this.output(&canonical) {
                    *root = output;
                }
            }
        }
        Ok(this)
    }
    pub fn output(&self, path: &Path) -> Option<PathBuf> {
        self.roots
            .iter()
            .filter_map(|(root, output)| {
                path.strip_prefix(root)
                    .ok()
                    .map(|suffix| (root.components().count(), output.join(suffix)))
            })
            .max_by_key(|(depth, _)| *depth)
            .map(|(_, path)| path)
    }
    pub fn adapts(&self, path: &Path) -> bool {
        self.changed.contains(path)
    }
    fn include(&mut self, path: &Path) -> Result<()> {
        if path.starts_with(&self.runtime)
            && path.file_name().is_some_and(|name| {
                let name = name.to_string_lossy();
                name.ends_with("_test.odin") || name.ends_with("_test.thor")
            })
        {
            return Ok(());
        }
        if self.changed.contains(path) {
            return Ok(());
        }
        if self.changed.iter().any(|changed| changed.starts_with(path)) {
            let mut children = fs::read_dir(path)?
                .map(|entry| entry.map(|entry| entry.path()))
                .collect::<std::io::Result<Vec<_>>>()?;
            children.sort();
            for child in children {
                self.include(&child)?;
            }
        } else {
            let output = self
                .output(path)
                .ok_or_else(|| Error::new("SDK link has no output root"))?;
            let target = path
                .strip_prefix(&self.runtime)
                .map(|suffix| self.backend_runtime.join(suffix))
                .unwrap_or_else(|_| path.to_path_buf());
            self.links.insert(output, target);
        }
        Ok(())
    }
}

pub(super) fn write_link(path: &Path, target: &Path) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| Error::new("SDK link has no parent"))?;
    fs::create_dir_all(parent)?;
    if path.is_symlink() || path.is_file() {
        fs::remove_file(path)?;
    }
    #[cfg(unix)]
    std::os::unix::fs::symlink(target, path)?;
    #[cfg(windows)]
    if target.is_dir() {
        std::os::windows::fs::symlink_dir(target, path)?;
    } else {
        std::os::windows::fs::symlink_file(target, path)?;
    }
    #[cfg(not(any(unix, windows)))]
    return Err(Error::new(
        "native SDK overlays require filesystem symbolic links",
    ));
    Ok(())
}
