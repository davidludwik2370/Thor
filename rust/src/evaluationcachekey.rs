//! Conservative native result keys. Toolchain content is hashed once per session;
//! result keys cover emitted dependencies, flags and observable resource inputs.
use super::*;
use crate::{
    ast::Source,
    evaluationinputs::{self, Inputs},
};
use std::{collections::BTreeMap, process::Command, time::Duration};
pub struct HelperCode<'a> {
    pub destination: &'a Path,
    pub flags: &'a [String],
    pub target: &'a Path,
    pub generated: &'a BTreeMap<PathBuf, Vec<u8>>,
}
pub(super) struct SourceKey {
    sources: Vec<Source>,
    inputs: Inputs,
    digest: String,
    resources: String,
}
impl SourceKey {
    fn matches(&self, sources: &[Source]) -> bool {
        self.sources.len() == sources.len() && self.sources.iter().zip(sources).all(|(old, current)| {
            old.shares_syntax(current) && old.path == current.path && old.thor == current.thor
                && old.excluded == current.excluded && old.shared_native == current.shared_native
                && old.collection_roots == current.collection_roots
        }) && self.inputs.valid()
    }
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
fn files(tree: &Path, out: &mut Vec<PathBuf>) -> std::io::Result<()> {
    if !tree.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(tree)? {
        let entry = entry?;
        let path = entry.path();
        let typ = entry.file_type()?;
        if typ.is_dir() {
            if !["__pycache__", ".git", ".thor-cache", ".thor-build"]
                .contains(&entry.file_name().to_string_lossy().as_ref())
            {
                files(&path, out)?;
            }
        } else if !(typ.is_symlink() && path.is_dir())
            && path.is_file()
            && path.extension().is_some_and(|extension| {
                ["odin", "thor", "rs", "py"]
                    .iter()
                    .any(|expected| extension == *expected)
            })
        {
            out.push(path);
        }
    }
    Ok(())
}
impl EvaluationCache {
    /// Reuse immutable syntax inventories within this evaluation session.
    /// Resource signatures are revalidated on every use.
    pub fn source_key(&mut self, sources: &[Source]) -> Option<String> {
        let _phase = crate::profile::span("cache.source_key");
        if let Some(key) = self.source_key.as_ref().filter(|key| key.matches(sources)) {
            crate::profile::count("cache.source_key_hits", 1);
            return Some(key.digest.clone());
        }
        crate::profile::count("cache.source_key_builds", 1);
        let mut inputs = Inputs::default();
        for source in sources.iter().filter(|source| !source.excluded) {
            inputs.source(source, false);
        }
        let resources = Self::resources(sources)?;
        let inventory = sources.iter().map(|source| json!([
            source.path, sha256(source.raw.as_bytes()), source.thor, source.excluded,
            source.shared_native, source.collection_roots
        ])).collect::<Vec<_>>();
        let digest = sha256(&canonical(&json!([inventory, resources])));
        if !inputs.valid() { return None; }
        self.source_key = Some(SourceKey { sources: sources.to_vec(), inputs, digest: digest.clone(), resources });
        Some(digest)
    }
    pub fn resource_key(&mut self, sources: &[Source]) -> Option<String> {
        self.source_key(sources)?;
        self.source_key.as_ref().map(|key| key.resources.clone())
    }
    pub fn identity(&mut self, odin: &Path, root: &Path) -> Option<String> {
        let executable = crate::project::canonicalize(odin).ok()?;
        if let Some(value) = self.identities.get(&executable) {
            return Some(value.clone());
        }
        let mut commands = vec![];
        for name in ["version", "root"] {
            let result = crate::project::capture_command(
                Command::new(&executable).arg(name),
                Duration::from_secs(15),
            )
            .ok()?;
            if !result.status.success() || result.timed_out || result.oversized {
                return None;
            }
            commands.push((name, result.stdout));
        }
        let native = PathBuf::from(std::str::from_utf8(&commands[1].1).ok()?.trim());
        let compiler = std::env::current_exe().and_then(crate::project::canonicalize).ok()?;
        let config = json!({"schema":SCHEMA,"engine":"rust","platform":std::env::consts::OS,"machine":std::env::consts::ARCH});
        let trees = [
            root.join("rust/src"),
            root.join("frontend"),
            root.join("runtime"),
            native.join("base"),
            native.join("core"),
            native.join("vendor"),
        ];
        let mut digest = canonical(&config);
        digest.extend(canonical(&json!([
            executable.to_string_lossy(),
            evaluationinputs::file_digest(&executable).ok()?
        ])));
        digest.extend(canonical(&json!([
            compiler.to_string_lossy(),
            evaluationinputs::file_digest(&compiler).ok()?
        ])));
        for (name, output) in commands {
            digest.extend(canonical(&json!([name, hex(&output)])));
        }
        for tree in trees {
            let mut sources = vec![];
            files(&tree, &mut sources).ok()?;
            sources.sort();
            for source in sources {
                digest.extend(canonical(&json!([
                    source.to_string_lossy(),
                    evaluationinputs::file_digest(&source).ok()?
                ])));
            }
        }
        let identity = sha256(&digest);
        self.identities.insert(executable, identity.clone());
        Some(identity)
    }
    pub fn resources(sources: &[Source]) -> Option<String> {
        let _phase = crate::profile::span("cache.resources");
        let mut digest = vec![];
        for source in sources.iter().filter(|source| !source.excluded) {
            for (name, path) in evaluationinputs::source_resources(source)? {
                let path = evaluationinputs::resolve(&path).ok()?;
                digest.extend(canonical(&json!(path.to_string_lossy())));
                if path.is_dir() {
                    digest.extend(b"directory");
                    if name == "#load_directory" {
                        let mut entries = fs::read_dir(&path)
                            .ok()?
                            .collect::<std::io::Result<Vec<_>>>()
                            .ok()?;
                        entries.sort_by_key(|entry| entry.file_name());
                        for entry in entries {
                            let path = entry.path();
                            if path.is_file() {
                                digest.extend(canonical(&json!([
                                    entry.file_name().to_string_lossy(),
                                    evaluationinputs::file_digest(&path).ok()?
                                ])));
                            }
                        }
                    }
                } else if path.is_file() {
                    digest.extend(evaluationinputs::file_digest(&path).ok()?.as_bytes());
                } else {
                    digest.extend(b"missing");
                }
            }
        }
        Some(sha256(&digest))
    }
    pub fn key(
        &mut self,
        sources: &[Source],
        helper: HelperCode<'_>,
        odin: &Path,
        root: &Path,
    ) -> Option<String> {
        let HelperCode {
            destination,
            flags,
            target,
            generated,
        } = helper;
        let key = (|| {
            if !self.enabled {
                return None;
            }
            let resources = self.resource_key(sources)?;
            let normalize =
                |text: &str| text.replace(destination.to_string_lossy().as_ref(), "<helper>");
            let flags = flags.iter().map(|flag| normalize(flag)).collect::<Vec<_>>();
            let mut digest = canonical(
                &json!({"identity":self.identity(odin,root)?,"resources":resources,"flags":flags,"target":normalize(&target.to_string_lossy())}),
            );
            for (path, raw) in generated.iter().filter(|(path, _)| {
                path.extension()
                    .is_some_and(|extension| extension == "odin")
            }) {
                digest.extend(canonical(&json!([
                    path.strip_prefix(destination).ok()?.to_string_lossy(),
                    normalize(std::str::from_utf8(raw).ok()?)
                ])));
            }
            Some(sha256(&digest))
        })();
        if key.is_none() {
            self.stats.bypasses += 1;
        }
        key
    }
}
