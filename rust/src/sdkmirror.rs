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
        // SDK overlays also apply to ordinary calls. A new unsaved SDK file
        // need not be inspected by Thor's analyses for Odin to require it.
        // Keep this lookup out of ordinary builds without overlays.
        if !overlays.is_empty() {
            let sdk = sources.iter().find_map(|source| source.info["odin_root"].as_str())
                .map(crate::project::canonicalize).transpose()?;
            changed.extend(overlays.keys().filter(|path| {
                sdk.as_ref().is_some_and(|sdk| path.starts_with(sdk))
                    || sources.iter().any(|source| source.shared_native && source.path == **path)
            }).cloned());
        }
        if changed.is_empty() {
            return Ok(Self::default());
        }
        let sdk = sources
            .iter()
            .find_map(|s| s.info["odin_root"].as_str())
            .map(crate::project::canonicalize)
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
                .filter_map(|root| crate::project::canonicalize(root).ok())
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
            if let Ok(canonical) = crate::project::canonicalize(&root) {
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

/// Inspect an alias without treating an ordinary directory as a removable link.
pub(super) fn is_link(path:&Path)->bool {
    let Ok(metadata)=fs::symlink_metadata(path) else{return false;};
    if metadata.file_type().is_symlink(){return true;}
    #[cfg(windows)] {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes()&0x400!=0 {return junction::exists(path).unwrap_or(false);}
    }
    false
}

/// Generated views may use unprivileged Windows junctions and file hard links.
pub(super) fn points_to(path:&Path,target:&Path)->bool {
    let linked=fs::read_link(path).ok();
    #[cfg(windows)]
    let linked=linked.or_else(||junction::get_target(path).ok());
    if let Some(linked)=linked {
        if linked==target {return true;}
        let linked=if linked.is_absolute(){linked}else{path.parent().unwrap_or(Path::new(".")).join(linked)};
        let expected=crate::project::canonicalize(target).ok();
        return expected.is_some()&&crate::project::canonicalize(linked).ok()==expected;
    }
    #[cfg(windows)] {
        if path.is_file()&&target.is_file() {
            if let (Ok(a),Ok(b))=(file_id::get_high_res_file_id(path),file_id::get_high_res_file_id(target)) {
                if a==b {return true;}
            }
            // A different-volume/read-only source may require an independent
            // file copy. Certify its bytes, never just a timestamp or pathname.
            return fs::read(path).ok().zip(fs::read(target).ok()).is_some_and(|(a,b)|a==b);
        }
    }
    false
}

/// Remove an owned file or alias without following its target. Windows uses
/// RemoveDirectory for directory reparse points, including dangling junctions.
pub(super) fn remove_file_or_link(path:&Path)->std::io::Result<()> {
    #[cfg(windows)] {
        use std::os::windows::fs::{FileTypeExt,MetadataExt};
        let metadata=fs::symlink_metadata(path)?;
        if metadata.file_type().is_symlink_dir() ||
            metadata.file_attributes()&0x410==0x410&&junction::exists(path)? {
            return fs::remove_dir(path);
        }
    }
    fs::remove_file(path)
}

#[cfg(windows)]
fn copy_view_file(path:&Path,target:&Path)->std::io::Result<()> {
    use std::io;
    let parent=path.parent().ok_or_else(||io::Error::other("view file has no parent"))?;
    let nonce=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(io::Error::other)?.as_nanos();
    let temporary=parent.join(format!(".thor-view-copy-{}-{nonce}",std::process::id()));
    let copied=(|| {
        let mut input=fs::File::open(target)?;
        let mut output=fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
        io::copy(&mut input,&mut output)?;
        drop(output);
        fs::rename(&temporary,path)
    })();
    if copied.is_err(){let _=fs::remove_file(&temporary);}
    copied
}

#[cfg(windows)]
fn unprivileged_link(path:&Path,target:&Path)->std::io::Result<()> {
    if target.is_dir(){return junction::create(target,path);}
    // Hard links preserve edits without copying support files. Generated writes
    // always use atomic replacement, so they cannot change the original inode.
    if !fs::metadata(target)?.permissions().readonly()&&fs::hard_link(target,path).is_ok(){return Ok(());}
    copy_view_file(path,target)
}

pub(super) fn write_link(path:&Path,target:&Path)->Result<()> {
    let parent=path.parent().ok_or_else(||Error::new("SDK link has no parent"))?;
    fs::create_dir_all(parent)?;
    if is_link(path)||path.is_file(){remove_file_or_link(path)?;}
    #[cfg(unix)]
    std::os::unix::fs::symlink(target,path)?;
    #[cfg(windows)] {
        use std::sync::atomic::{AtomicBool,Ordering};
        static MISSING_PRIVILEGE:AtomicBool=AtomicBool::new(false);
        if MISSING_PRIVILEGE.load(Ordering::Relaxed){unprivileged_link(path,target)?;}
        else {
            let result=if target.is_dir(){std::os::windows::fs::symlink_dir(target,path)}else{std::os::windows::fs::symlink_file(target,path)};
            if let Err(error)=result {
                if error.raw_os_error()==Some(1314){MISSING_PRIVILEGE.store(true,Ordering::Relaxed);}
                else if !matches!(error.kind(),std::io::ErrorKind::PermissionDenied|std::io::ErrorKind::Unsupported){return Err(error.into());}
                unprivileged_link(path,target)?;
            }
        }
    }
    #[cfg(not(any(unix,windows)))]
    return Err(Error::new("native SDK overlays require filesystem aliases"));
    Ok(())
}

#[cfg(all(test, any(unix, windows)))]
mod link_tests {
    use super::*;

    #[test]
    fn replacing_owned_links_preserves_targets_and_handles_dangling_directories() {
        let root = std::env::temp_dir().join(format!("thor-sdk-link-{}-{}", std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        let first = root.join("first");
        let second = root.join("second");
        let link = root.join("generated/link");
        fs::create_dir_all(&first).unwrap();
        fs::create_dir_all(&second).unwrap();
        fs::write(first.join("asset"), "original").unwrap();
        write_link(&link, &first).unwrap();
        write_link(&link, &first).unwrap();
        write_link(&link, &second).unwrap();
        let retargeted = points_to(&link, &second);
        fs::remove_dir(&second).unwrap();
        write_link(&link, &first).unwrap();
        let restored = fs::read_to_string(link.join("asset")).unwrap() == "original";
        remove_file_or_link(&link).unwrap();
        write_link(&link, &first.join("asset")).unwrap();
        write_link(&link, &first.join("asset")).unwrap();
        let file_link = fs::read_to_string(&link).unwrap() == "original";
        remove_file_or_link(&link).unwrap();
        let refused_directory = remove_file_or_link(&first).is_err();
        let original = fs::read_to_string(first.join("asset")).unwrap();
        fs::remove_dir_all(root).unwrap();
        if !retargeted || !restored || !file_link || !refused_directory || original != "original" {
            panic!("owned-link replacement changed target files or followed a directory");
        }
    }
    #[cfg(windows)]
    #[test]
    fn unprivileged_aliases_are_replaceable_and_generated_writes_do_not_modify_sources() {
        let root=std::env::temp_dir().join(format!("thor-unprivileged-links-{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir_all(&root).unwrap();
        let source=root.join("source");let link=root.join("link");let copied=root.join("copied");
        fs::create_dir(&source).unwrap();fs::write(source.join("file"),b"original").unwrap();
        unprivileged_link(&link,&source).unwrap();
        let directory=points_to(&link,&source);
        remove_file_or_link(&link).unwrap();
        unprivileged_link(&link,&source.join("file")).unwrap();
        let file=points_to(&link,&source.join("file"));
        atomic_write(&link,b"generated").unwrap();
        let original=fs::read(source.join("file")).unwrap();
        copy_view_file(&copied,&source.join("file")).unwrap();
        fs::write(&copied,b"separate").unwrap();
        let independent=fs::read(source.join("file")).unwrap();
        fs::remove_dir_all(&root).unwrap();
        if !directory||!file||original!=b"original"||independent!=b"original" {panic!("unprivileged aliases modified their authoritative source");}
    }

}
