//! Conservative native result keys. Toolchain metadata is validated lazily;
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
    pub fn identity(&mut self, odin: &Path, root: &Path) -> Option<String> {
        let executable = fs::canonicalize(odin).ok()?;
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
        let compiler = std::env::current_exe().and_then(fs::canonicalize).ok()?;
        let config = json!({"schema":SCHEMA,"engine":"rust","platform":std::env::consts::OS,"machine":std::env::consts::ARCH});
        let trees = [
            root.join("rust/src"),
            root.join("frontend"),
            root.join("runtime"),
            native.join("base"),
            native.join("core"),
            native.join("vendor"),
        ];
        let mut snapshot = Inputs::default();
        snapshot.file(&executable, None, false);
        snapshot.file(&compiler, None, false);
        snapshot.binding(&native);
        for tree in &trees {
            snapshot.tree_with_extensions(tree, &["odin", "thor", "rs", "py"]);
        }
        let stamp = snapshot.usable.then(|| {
            sha256(&canonical(&json!([
                config,
                hex(&commands[0].1),
                snapshot.data
            ])))
        });
        if let Some(value) = self
            .read_record("identity-v1", stamp.as_deref())
            .and_then(|value| {
                value
                    .as_str()
                    .filter(|key| key_valid(key))
                    .map(str::to_string)
            })
        {
            self.identities.insert(executable, value.clone());
            return Some(value);
        }
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
        if snapshot.valid() {
            self.write_record("identity-v1", stamp.as_deref(), &json!(identity));
        }
        Some(identity)
    }
    pub fn resources(sources: &[Source]) -> Option<String> {
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
            let resources = Self::resources(sources)?;
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
