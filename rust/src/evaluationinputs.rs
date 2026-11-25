//! Reference file/package/resource snapshots for compile-time cache reuse.
use crate::{
    ast::{NodeId, Source},
    digest::sha256,
    lower::unquote,
    semantics::{Ref, Resolver},
};
use serde_json::{json, Value};
use std::{
    collections::BTreeSet,
    env, fs, io,
    path::{Component, Path, PathBuf},
};

pub fn file_digest(path: &Path) -> io::Result<String> {
    fs::read(path).map(|data| sha256(&data))
}
/// Enumerate every path in a bounded, source-local constant string expression.
/// Both conditional branches are dependencies, so this needs no target guesses.
/// Unknown expressions keep the optional result cache disabled.
fn resource_names<'a>(
    source: &'a Source,
    node: NodeId,
    resolver: &mut Option<Resolver<'a>>,
    seen: &mut BTreeSet<NodeId>,
    budget: &mut usize,
) -> Option<BTreeSet<String>> {
    *budget = budget.checked_sub(1)?;
    if !seen.insert(node) || seen.len() > 64 {
        return None;
    }
    let child = |role| source.role(node, role);
    let values = match source.node(node).kind.as_str() {
        "Literal" => BTreeSet::from([unquote(source.text(node)).ok()?]),
        "Paren" => resource_names(source, child("expr")?, resolver, seen, budget)?,
        "Ident" => {
            let value = {
                let context =
                    resolver.get_or_insert_with(|| Resolver::new(std::slice::from_ref(source)));
                let symbol = &context.symbols[context.symbol(Ref { file: 0, node })?];
                if !symbol.constant
                    || symbol.reference.file != 0
                    || source.node(symbol.reference.node).kind != "Declaration"
                    || source.ancestors(symbol.reference.node).any(|ancestor| {
                        matches!(
                            source.node(ancestor).kind.as_str(),
                            "When" | "CompileSwitch"
                        )
                    })
                {
                    return None;
                }
                symbol.value?
            };
            resource_names(source, value, resolver, seen, budget)?
        }
        "Ternary" | "WhenExpr" => {
            let mut values =
                resource_names(source, child("x")?, resolver, &mut seen.clone(), budget)?;
            values.extend(resource_names(
                source,
                child("y")?,
                resolver,
                &mut seen.clone(),
                budget,
            )?);
            values
        }
        "Binary" if source.node(node).op == "+" => {
            let left = resource_names(source, child("left")?, resolver, &mut seen.clone(), budget)?;
            let right =
                resource_names(source, child("right")?, resolver, &mut seen.clone(), budget)?;
            if left.len().checked_mul(right.len())? > 64 {
                return None;
            }
            let size = right.len() * left.iter().map(String::len).sum::<usize>()
                + left.len() * right.iter().map(String::len).sum::<usize>();
            if size > 1024 * 1024 {
                return None;
            }
            left.iter()
                .flat_map(|left| right.iter().map(move |right| format!("{left}{right}")))
                .collect()
        }
        _ => return None,
    };
    (values.len() <= 64 && values.iter().map(String::len).sum::<usize>() <= 1024 * 1024)
        .then_some(values)
}

pub fn source_resources(source: &Source) -> Option<Vec<(&str, PathBuf)>> {
    let mut resources = vec![];
    let mut resolver = None;
    for (index, _) in source
        .nodes
        .iter()
        .enumerate()
        .filter(|(_, node)| node.kind == "Call")
    {
        let call = NodeId(index);
        let Some(callee) = source.role(call, "callee") else {
            continue;
        };
        let name = source.text(callee);
        if !["#load", "#load_hash", "#load_directory", "#exists"].contains(&name) {
            continue;
        }
        let arg = *source.group(call, "args").first()?;
        for name_path in
            resource_names(source, arg, &mut resolver, &mut BTreeSet::new(), &mut 4096)?
        {
            let path = if let Some((collection, suffix)) = name_path.split_once(':') {
                source.collection_roots.get(collection)?.join(suffix)
            } else {
                source.path.parent()?.join(name_path)
            };
            resources.push((name, path));
        }
    }
    Some(resources)
}
pub fn signature(path: &Path) -> io::Result<Option<Value>> {
    #[cfg(not(windows))]
    let metadata = match fs::metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let stamp = |seconds: i64, nanos: i64| {
            serde_json::to_value(i128::from(seconds) * 1_000_000_000 + i128::from(nanos))
                .map_err(io::Error::other)
        };
        Ok(Some(json!([
            metadata.dev(),
            metadata.ino(),
            metadata.mode(),
            metadata.len(),
            stamp(metadata.mtime(), metadata.mtime_nsec())?,
            stamp(metadata.ctime(), metadata.ctime_nsec())?
        ])))
    }
    #[cfg(windows)]
    {
        windows_signature(path)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = metadata;
        // Missing native stat fields disable this optional fast path; they
        // never invent a potentially reusable metadata identity.
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "native cache stat signature unavailable",
        ))
    }
}
/// Use the safe native identity adapter while retaining Rust's no-unsafe-code
/// invariant. Byte-dependent snapshots always rehash content in `Inputs::valid`;
/// metadata alone is never used to certify an unchanged file's contents.
#[cfg(windows)]
fn windows_signature(path: &Path) -> io::Result<Option<Value>> {
    use std::os::windows::fs::MetadataExt;
    let identity = match file_id::get_high_res_file_id(path) {
        Ok(file_id::FileId::HighRes { volume_serial_number, file_id }) => (volume_serial_number, file_id),
        Ok(_) => return Err(io::Error::new(io::ErrorKind::Unsupported, "high-resolution file identity unavailable")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let metadata = fs::metadata(path)?;
    Ok(Some(json!(["windows-v1", identity.0, identity.1.to_le_bytes(),
        metadata.file_attributes(), metadata.file_size(), metadata.creation_time(), metadata.last_write_time()])))
}
fn directory_names(path: &Path) -> io::Result<Vec<String>> {
    let mut names = fs::read_dir(path)?.map(|entry|
        entry.map(|entry| entry.file_name().to_string_lossy().into_owned()))
        .collect::<io::Result<Vec<_>>>()?;
    names.sort();
    Ok(names)
}
pub fn members(path: &Path, suffixes: &[String]) -> io::Result<Vec<String>> {
    let mut names = vec![];
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let path = entry.path();
        if path.is_file()
            && (suffixes.iter().any(|suffix| suffix == "*") || path.extension().is_some_and(|extension| {
                suffixes
                    .iter()
                    .any(|suffix| suffix.strip_prefix('.').unwrap_or(suffix) == extension)
            }))
        {
            names.push(entry.file_name().to_string_lossy().into_owned());
        }
    }
    names.sort();
    Ok(names)
}
fn absolute(path: &Path) -> io::Result<PathBuf> {
    let path = if path.is_absolute() {
        path.into()
    } else {
        env::current_dir()?.join(path)
    };
    Ok(path.components().collect())
}
/// Resolve existing symlinks even when the final resource does not exist yet.
pub fn resolve(path: &Path) -> io::Result<PathBuf> {
    fn walk(path: &Path, depth: usize) -> io::Result<PathBuf> {
        if depth > 40 {
            return Err(io::Error::other("cyclic or excessive symlink nesting"));
        }
        let mut result = PathBuf::new();
        for component in path.components() {
            match component {
                Component::CurDir => {}
                Component::ParentDir => {
                    result.pop();
                }
                component => {
                    result.push(component.as_os_str());
                    match fs::symlink_metadata(&result) {
                        Ok(metadata) if metadata.file_type().is_symlink() => {
                            let link = fs::read_link(&result)?;
                            let target = if link.is_absolute() {
                                link
                            } else {
                                result.parent().unwrap().join(link)
                            };
                            result = walk(&target, depth + 1)?;
                        }
                        Err(error)
                            if ![io::ErrorKind::NotFound, io::ErrorKind::NotADirectory]
                                .contains(&error.kind()) =>
                        {
                            return Err(error)
                        }
                        _ => {}
                    }
                }
            }
        }
        Ok(result)
    }
    let path=absolute(path)?;
    // Win32 normalizes non-verbatim `..` components before following reparse
    // points. Walking them like POSIX paths can track a different resource than
    // the one read by the native compiler. Keep the original binding spelling,
    // but resolve its target with the host's path semantics.
    #[cfg(windows)]
    let path=std::path::absolute(path)?;
    walk(&path, 0)
}

#[cfg(all(test,windows))]
mod windows_resolve_tests {
    use super::*;
    #[test]
    fn parent_components_track_the_file_opened_by_win32() {
        let root=env::temp_dir().join(format!("thor-path-{}-{}",std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir_all(root.join("target/child")).unwrap();
        struct Cleanup(PathBuf);
        impl Drop for Cleanup {fn drop(&mut self){let _=fs::remove_dir_all(&self.0);}}
        let _cleanup=Cleanup(root.clone());
        fs::write(root.join("sibling"),b"native").unwrap();
        fs::write(root.join("target/sibling"),b"different").unwrap();
        std::os::windows::fs::symlink_dir(root.join("target/child"),root.join("link")).unwrap();
        let input=root.join("link/../sibling");
        let actual_bytes=fs::read(&input).unwrap();
        let target=resolve(&input).unwrap();
        let expected=fs::canonicalize(&input).unwrap();
        let actual=fs::canonicalize(&target).unwrap();
        assert_eq!(actual_bytes,b"native");
        assert_eq!(actual,expected);
        let missing=resolve(&root.join("link/../missing/tail")).unwrap();
        assert_eq!(missing,root.join("missing/tail"));
    }
}
pub struct Inputs {
    pub data: Value,
    pub usable: bool,
}
impl Default for Inputs {
    fn default() -> Self {
        Self::new(None)
    }
}
impl Inputs {
    pub fn new(data: Option<Value>) -> Self {
        Self {
            data: data
                .unwrap_or_else(|| json!({"files":{},"directories":{},"members":{},"bindings":{}})),
            usable: true,
        }
    }
    fn map(&mut self, name: &str) -> Option<&mut serde_json::Map<String, Value>> {
        let value = self.data.get_mut(name)?.as_object_mut();
        if value.is_none() {
            self.usable = false;
        }
        value
    }
    pub fn file(&mut self, path: &Path, raw: Option<&[u8]>, content: bool) {
        let run = (|| -> io::Result<(Option<Value>, Option<String>, Option<Value>)> {
            let before = signature(path)?;
            let digest = if content && before.is_some() {
                Some(file_digest(path)?)
            } else {
                None
            };
            Ok((before, digest, signature(path)?))
        })();
        let Ok((before, digest, after)) = run else {
            self.usable = false;
            return;
        };
        if before != after || raw.is_some_and(|raw| Some(sha256(raw)) != digest) {
            self.usable = false;
        }
        let path = path.to_string_lossy().into_owned();
        let previous = self.data.get("files").and_then(|files| files.get(&path));
        if previous.is_some_and(|previous| previous["stat"] != json!(before)) {
            self.usable = false;
        }
        let replace = previous.is_none() || content;
        if replace {
            if let Some(files) = self.map("files") {
                files.insert(path, json!({"stat":before,"sha256":digest}));
            }
        }
    }
    pub fn binding(&mut self, path: &Path) {
        let paths =
            absolute(path).and_then(|absolute| resolve(path).map(|resolved| (absolute, resolved)));
        let Ok((path, resolved)) = paths else {
            self.usable = false;
            return;
        };
        let path = path.to_string_lossy().into_owned();
        let resolved = json!(resolved.to_string_lossy());
        if let Some(bindings) = self.map("bindings") {
            if let Some(previous) = bindings.get(&path) {
                if previous != &resolved {
                    self.usable = false;
                }
            } else {
                bindings.insert(path, resolved);
            }
        }
    }
    pub fn package(&mut self, directory: &Path, paths: &[PathBuf], suffixes: &[String]) {
        let mut names = paths
            .iter()
            .filter_map(|path| path.file_name())
            .map(|name| name.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        names.sort();
        let value = json!({"suffixes":suffixes,"names":names});
        if let Some(packages) = self.map("members") {
            let key = directory.to_string_lossy().into_owned();
            if let Some(previous) = packages.get(&key) {
                if previous != &value {
                    self.usable = false;
                }
            } else {
                packages.insert(key, value);
            }
        }
    }
    pub fn directory(&mut self, path: &Path) {
        let stamp = match signature(path) {
            Ok(stamp) => stamp,
            Err(_) => {
                self.usable = false;
                return;
            }
        };
        if let Some(directories) = self.map("directories") {
            directories.insert(path.to_string_lossy().into_owned(), json!(stamp));
        }
    }
    pub fn tree(&mut self, root: &Path) {
        self.tree_with_extensions(root, &["odin", "py"]);
    }
    pub fn tree_with_extensions(&mut self, root: &Path, extensions: &[&str]) {
        if !root.exists() {
            self.file(root, None, false);
            return;
        }
        self.directory(root);
        let entries =
            fs::read_dir(root).and_then(|entries| entries.collect::<io::Result<Vec<_>>>());
        let Ok(mut entries) = entries else {
            self.usable = false;
            return;
        };
        entries.sort_by_key(|entry| entry.file_name());
        let names = entries.iter().map(|entry|
            entry.file_name().to_string_lossy().into_owned()).collect::<Vec<_>>();
        if let Some(directories) = self.map("directories") {
            if let Some(stamp) = directories.get_mut(root.to_string_lossy().as_ref()) {
                *stamp = json!({"stat":stamp.clone(), "entries":names});
            }
        }
        for entry in entries {
            let path = entry.path();
            let typ = match entry.file_type() {
                Ok(typ) => typ,
                Err(_) => {
                    self.usable = false;
                    continue;
                }
            };
            if typ.is_dir() {
                if !["__pycache__", ".git", ".thor-cache", ".thor-build"]
                    .contains(&entry.file_name().to_string_lossy().as_ref())
                {
                    self.tree_with_extensions(&path, extensions);
                }
            } else if !(typ.is_symlink() && path.is_dir())
                && path.extension().is_some_and(|extension| {
                    extensions.iter().any(|expected| extension == *expected)
                })
            {
                self.file(&path, None, true);
            }
        }
    }
    pub fn source(&mut self, source: &Source, source_file: bool) {
        if source_file {
            self.file(&source.path, Some(source.raw.as_bytes()), true);
        }
        let Some(resources) = source_resources(source) else {
            self.usable = false;
            return;
        };
        for (name, path) in resources {
            self.binding(&path);
            let path = match resolve(&path) {
                Ok(path) => path,
                Err(_) => {
                    self.usable = false;
                    continue;
                }
            };
            if path.is_dir() {
                self.directory(&path);
                if name == "#load_directory" {
                    match fs::read_dir(&path)
                        .and_then(|entries| entries.collect::<io::Result<Vec<_>>>())
                    {
                        Ok(mut entries) => {
                            entries.sort_by_key(|entry| entry.file_name());
                            let files = entries.iter().map(|entry| entry.path())
                                .filter(|path| path.is_file()).collect::<Vec<_>>();
                            self.package(&path, &files, &["*".into()]);
                            for entry in entries {
                                let path = entry.path();
                                if path.is_file() {
                                    self.file(&path, None, true);
                                }
                            }
                        }
                        Err(_) => self.usable = false,
                    }
                }
            } else {
                self.file(&path, None, true);
            }
        }
    }
    pub fn valid(&self) -> bool {
        let _phase = crate::profile::span("cache.validate_inputs");
        if !self.usable {
            return false;
        }
        let check = (|| -> Option<bool> {
            for (path, value) in self.data.get("files")?.as_object()? {
                let path = Path::new(path);
                let current = json!(signature(path).ok()?);
                let expected = value.get("stat")?;
                let digest = value.get("sha256")?;
                if &current != expected && digest.is_null() {
                    return Some(false);
                }
                if !digest.is_null() {
                    let expected_digest = digest.as_str()?;
                    if expected_digest.len() != 64 || !expected_digest.bytes().all(|byte|
                        byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
                        return Some(false);
                    }
                    crate::profile::count("cache.rehashed_files", 1);
                    if file_digest(path).ok()?.as_str() != expected_digest
                        || json!(signature(path).ok()?) != current {
                        return Some(false);
                    }
                }
            }
            for (path, expected) in self.data.get("directories")?.as_object()? {
                let path = Path::new(path);
                let stamp = json!(signature(path).ok()?);
                let current = if expected.is_object() {
                    json!({"stat":stamp, "entries":directory_names(path).ok()?})
                } else { stamp };
                if current != *expected { return Some(false); }
            }
            for (path, expected) in self.data.get("members")?.as_object()? {
                let suffixes = expected
                    .get("suffixes")?
                    .as_array()?
                    .iter()
                    .map(|value| value.as_str().map(str::to_string))
                    .collect::<Option<Vec<_>>>()?;
                if json!(members(Path::new(path), &suffixes).ok()?) != *expected.get("names")? {
                    return Some(false);
                }
            }
            for (path, expected) in self.data.get("bindings")?.as_object()? {
                if resolve(Path::new(path)).ok()?.to_string_lossy().as_ref() != expected.as_str()? {
                    return Some(false);
                }
            }
            Some(true)
        })();
        check == Some(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    struct Scratch(PathBuf);
    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = env::temp_dir().join(format!("thor-inputs-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Scratch { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }
    #[test]
    fn native_inputs_support_files_directories_and_missing_paths() {
        let scratch = Scratch::new();
        let file = scratch.0.join("héllo.txt");
        fs::write(&file, "contents").unwrap();
        let before = signature(&file).unwrap();
        let digest = file_digest(&file).unwrap();
        let after = signature(&file).unwrap();
        assert!(before.is_some());
        assert_eq!(before, after);
        assert_eq!(digest, sha256(b"contents"));
        let mut inputs = Inputs::default();
        inputs.file(&file, Some(b"contents"), true);
        inputs.directory(&scratch.0);
        inputs.file(&scratch.0.join("missing"), None, false);
        assert!(inputs.valid());
        fs::write(scratch.0.join("missing"), "new").unwrap();
        assert!(!inputs.valid());
    }
    #[test]
    fn native_inputs_rehash_same_size_edits_even_if_metadata_is_restored() {
        let scratch = Scratch::new();
        let file = scratch.0.join("data");
        fs::write(&file, "before").unwrap();
        let mut inputs = Inputs::default();
        inputs.file(&file, None, true);
        let mtime = filetime::FileTime::from_last_modification_time(&fs::metadata(&file).unwrap());
        fs::write(&file, "change").unwrap();
        filetime::set_file_mtime(&file, mtime).unwrap();
        // Even forged matching metadata cannot bypass the content digest.
        inputs.data["files"][file.to_string_lossy().as_ref()]["stat"] = json!(signature(&file).unwrap());
        assert!(!inputs.valid());
    }
    #[test]
    fn native_inputs_identical_rewrite_is_valid_but_replacement_identity_changes() {
        let scratch = Scratch::new();
        let file = scratch.0.join("data");
        fs::write(&file, "same").unwrap();
        let mut content = Inputs::default();
        let mut metadata = Inputs::default();
        content.file(&file, None, true);
        metadata.file(&file, None, false);
        let replacement = scratch.0.join("replacement");
        fs::write(&replacement, "same").unwrap();
        fs::remove_file(&file).unwrap();
        fs::rename(&replacement, &file).unwrap();
        assert!(content.valid());
        assert!(!metadata.valid());
        fs::remove_file(&file).unwrap();
        assert!(!content.valid());
    }
    #[test]
    fn native_inputs_overlay_and_package_membership_fail_closed() {
        let scratch = Scratch::new();
        let file = scratch.0.join("main.thor");
        fs::write(&file, "package main").unwrap();
        let mut overlay = Inputs::default();
        overlay.file(&file, Some(b"different"), true);
        assert!(!overlay.valid());
        let mut package = Inputs::default();
        package.package(&scratch.0, &[file], &["thor".into()]);
        assert!(package.valid());
        fs::write(scratch.0.join("new.thor"), "package main").unwrap();
        assert!(!package.valid());
    }
}
