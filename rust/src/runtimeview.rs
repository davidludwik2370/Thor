//! Installation-owned native support libraries, excluding their own unit tests.
//! Links keep editable sources authoritative without a per-project library copy.
use super::*;

pub(super) fn prepare(root: &Path, tabs: bool) -> Result<PathBuf> {
    let source = crate::project::canonicalize(root.join("runtime"))?;
    let mut links = BTreeMap::new();
    let mut copies = BTreeMap::new();
    for package in fs::read_dir(&source)? {
        let package = package?;
        if !package.file_type()?.is_dir() {
            continue;
        }
        if tabs {
            collect_tab_views(&source, &package.path(), &mut links, &mut copies)?;
            continue;
        }
        let members = fs::read_dir(package.path())?.collect::<std::io::Result<Vec<_>>>()?;
        let is_test = |entry: &fs::DirEntry| {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            name.ends_with("_test.odin") || name.ends_with("_test.thor")
        };
        if !members.iter().any(is_test) {
            // Directory links preserve package identity and native relative
            // imports. Only packages that actually contain tests need filtering.
            links.insert(PathBuf::from(package.file_name()), package.path());
            continue;
        }
        for file in members {
            if is_test(&file) {
                continue;
            }
            links.insert(
                PathBuf::from(package.file_name()).join(file.file_name()),
                file.path(),
            );
        }
    }
    let layout = if tabs {
        sha256(&serde_json::to_vec(&(
            "rust-runtime-tabs-v1",
            &source,
            &links,
            &copies,
        ))?)
    } else {
        sha256(&serde_json::to_vec(&("rust-runtime-v2", &source, &links))?)
    };
    let directory = root
        .join("lib")
        .join(format!("runtime-rust-{}", &layout[..24]));
    let manifest = json!({"engine":"rust","shared_runtime":layout});
    let valid = || -> bool {
        !directory.is_symlink()
            && fs::read(directory.join("thor-manifest.json"))
                .ok()
                .and_then(|raw| serde_json::from_slice::<Value>(&raw).ok())
                .as_ref()
                == Some(&manifest)
            && links.iter().all(|(relative, target)| {
                fs::read_link(directory.join(relative)).ok().as_ref() == Some(target)
            })
            && copies.iter().all(|(relative, bytes)| {
                fs::read(directory.join(relative)).ok().as_ref() == Some(bytes)
            })
    };
    if directory.exists() || directory.is_symlink() {
        if valid() {
            return Ok(directory);
        }
        return Err(Error::new(format!(
            "invalid shared native runtime view: {}",
            directory.display()
        )));
    }
    let parent = directory
        .parent()
        .ok_or_else(|| Error::new("runtime view has no parent"))?;
    fs::create_dir_all(parent)?;
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| Error::new("system clock precedes epoch"))?
        .as_nanos();
    let temporary = parent.join(format!(".rust-runtime-{}-{nonce}", std::process::id()));
    fs::create_dir(&temporary)?;
    let publish = (|| -> Result<()> {
        for (relative, target) in &links {
            sdkmirror::write_link(&temporary.join(relative), target)?;
        }
        for (relative, bytes) in &copies {
            let destination = temporary.join(relative);
            fs::create_dir_all(destination.parent().unwrap())?;
            atomic_write(&destination, bytes)?;
        }
        atomic_write(
            &temporary.join("thor-manifest.json"),
            &serde_json::to_vec(&manifest)?,
        )?;
        match fs::rename(&temporary, &directory) {
            Ok(()) => Ok(()),
            Err(_) if valid() => Ok(()),
            Err(error) => Err(error.into()),
        }
    })();
    // Only this newly created temporary directory is recursively removed.
    // remove_dir_all removes symbolic links without following their targets.
    if temporary.exists() {
        fs::remove_dir_all(&temporary)?;
    }
    publish?;
    Ok(directory)
}

// Only a native source-policy probe needs this view. User and SDK sources keep
// their original flags; installation-owned support never inherits vet-tabs.
// Byte content enters the key, so editable support sources remain authoritative.
fn collect_tab_views(
    source: &Path,
    directory: &Path,
    links: &mut BTreeMap<PathBuf, PathBuf>,
    copies: &mut BTreeMap<PathBuf, Vec<u8>>,
) -> Result<()> {
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        if entry.file_type()?.is_dir() {
            collect_tab_views(source, &path, links, copies)?;
            continue;
        }
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.ends_with("_test.odin") || name.ends_with("_test.thor") {
            continue;
        }
        let relative = path
            .strip_prefix(source)
            .map_err(|_| Error::new("runtime view outside installation"))?
            .to_path_buf();
        if path
            .extension()
            .is_some_and(|extension| extension == "odin")
        {
            let mut bytes = b"#+vet !tabs\n".to_vec();
            bytes.extend(fs::read(&path)?);
            copies.insert(relative, bytes);
        } else {
            links.insert(relative, path);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn policy_views_filter_tests_preserve_links_and_follow_support_edits() {
        let directory = std::env::temp_dir().join(format!(
            "thor-runtime-tabs-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let package = directory.join("runtime/sample");
        fs::create_dir_all(package.join("nested")).unwrap();
        fs::write(package.join("sample.odin"), "package sample\n").unwrap();
        fs::write(package.join("nested/code.odin"), "package nested\n").unwrap();
        fs::write(package.join("sample_test.odin"), "invalid test\n").unwrap();
        fs::write(package.join("asset.txt"), "asset\n").unwrap();
        let expected = crate::project::canonicalize(&package).unwrap();
        let plain = prepare(&directory, false).unwrap();
        let first = prepare(&directory, true).unwrap();
        let header = fs::read_to_string(first.join("sample/sample.odin")).unwrap();
        let nested = fs::read_to_string(first.join("sample/nested/code.odin")).unwrap();
        let test = first.join("sample/sample_test.odin").exists();
        let link = fs::read_link(first.join("sample/asset.txt")).unwrap();
        let normal = fs::read_link(plain.join("sample/sample.odin")).unwrap();
        let again = prepare(&directory, true).unwrap();
        fs::write(package.join("sample.odin"), "package sample\nVALUE :: 7\n").unwrap();
        let changed = prepare(&directory, true).unwrap();
        let current = fs::read_to_string(changed.join("sample/sample.odin")).unwrap();
        fs::remove_dir_all(&directory).unwrap();
        if !header.starts_with("#+vet !tabs\n")
            || !nested.starts_with("#+vet !tabs\n")
            || test
            || link != expected.join("asset.txt")
            || normal != expected.join("sample.odin")
            || again != first
            || changed == first
            || !current.contains("VALUE :: 7")
        {
            panic!("runtime policy view changed authority, content caching or test filtering");
        }
    }
}
