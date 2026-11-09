//! Installation-owned native support libraries, excluding their own unit tests.
//! Links keep editable sources authoritative without a per-project library copy.
use super::*;

pub(super) fn prepare(root: &Path) -> Result<PathBuf> {
    let source = fs::canonicalize(root.join("runtime"))?;
    let mut links = BTreeMap::new();
    for package in fs::read_dir(&source)? {
        let package = package?;
        if !package.file_type()?.is_dir() {
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
    let layout = sha256(&serde_json::to_vec(&("rust-runtime-v2", &source, &links))?);
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
