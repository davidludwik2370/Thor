//! Installation-owned native support libraries, excluding their own unit tests.
//! Links keep editable sources authoritative without a per-project library copy.
use super::*;

// Directory symlinks resolve to the source automatically. Copied/hard-linked
// files retain a distinct PDB pathname and need an explicit identity map.
// Only debug builds request these small, byte-verified alias records.
pub(super) fn debug_aliases(root:&Path,view:&Path)->Result<Vec<Value>> {
    fn collect(directory:&Path,files:&mut Vec<PathBuf>)->std::io::Result<()> {
        for entry in fs::read_dir(directory)? {
            let entry=entry?;let path=entry.path();let kind=entry.file_type()?;
            if kind.is_dir(){collect(&path,files)?;}
            else if path.extension().is_some_and(|e|e=="odin")&&!path.file_name().unwrap().to_string_lossy().ends_with("_test.odin"){files.push(path);}
        }
        Ok(())
    }
    let source=crate::project::canonicalize(root.join("runtime"))?;
    let mut files=vec![];collect(&source,&mut files)?;files.sort();
    let mut aliases=vec![];
    for original in files {
        let relative=original.strip_prefix(&source).unwrap();
        if relative.components().count()<2 {continue;}
        let generated=view.join(relative);
        if crate::project::canonicalize(&generated)?==crate::project::canonicalize(&original)?{continue;}
        let bytes=crate::platform_io::read(&generated)?;let original_bytes=crate::platform_io::read(&original)?;
        let offset=if bytes==original_bytes {0} else if bytes.strip_prefix(b"#+vet !tabs\n")==Some(original_bytes.as_slice()){b"#+vet !tabs\n".len()}else {
            return Err(Error::new(format!("Native runtime view differs from its source: {}. Rebuild the runtime view.",generated.display())));
        };
        aliases.push(json!({"generated":generated,"original":original,"generated_sha256":sha256(&bytes),"original_sha256":sha256(&original_bytes),"offset":offset}));
    }
    Ok(aliases)
}

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
    #[cfg(windows)]
    let file_identities=links.values().filter(|path|path.is_file()).map(|path| {
        let (signature,digest)=crate::platform_io::retry_sharing(|| {
            let mut contents_only=false;
            let signature=match crate::evaluationinputs::signature(path) {
                Ok(value)=>value,
                Err(error) if error.kind()==std::io::ErrorKind::Unsupported=>{contents_only=true;None},
                Err(error)=>return Err(error),
            };
            // Keep both identity and contents, including edits with restored
            // timestamps. A native editor can briefly open a support file
            // exclusively; retry that failed snapshot without repeating a read
            // that already succeeded.
            let digest=sha256(&fs::read(path)?);
            let signature=if contents_only{Some(json!(["contents",digest.clone()]))}else{signature};
            Ok((signature,digest))
        }).map_err(|error|Error::new(format!("Cannot fingerprint native support source {}: {error}",path.display())))?;
        Ok((path,signature,digest))
    }).collect::<Result<Vec<_>>>()?;
    let layout = if tabs {
        #[cfg(windows)]
        let key=serde_json::to_vec(&("rust-runtime-tabs-windows-v2",&source,&links,&copies,&file_identities))?;
        #[cfg(not(windows))]
        let key=serde_json::to_vec(&("rust-runtime-tabs-v1",&source,&links,&copies))?;
        sha256(&key)
    } else {
        #[cfg(windows)]
        let key=serde_json::to_vec(&("rust-runtime-windows-v3",&source,&links,&file_identities))?;
        #[cfg(not(windows))]
        let key=serde_json::to_vec(&("rust-runtime-v2",&source,&links))?;
        sha256(&key)
    };
    let directory = root
        .join("lib")
        .join(format!("runtime-rust-{}", &layout[..24]));
    let manifest = json!({"engine":"rust","shared_runtime":layout});
    let valid = || -> bool {
        !sdkmirror::is_link(&directory)
            && crate::platform_io::read(directory.join("thor-manifest.json"))
                .ok()
                .and_then(|raw| serde_json::from_slice::<Value>(&raw).ok())
                .as_ref()
                == Some(&manifest)
            && links.iter().all(|(relative, target)| {
                sdkmirror::points_to(&directory.join(relative),target)
            })
            && copies.iter().all(|(relative, bytes)| {
                crate::platform_io::read(directory.join(relative)).ok().as_ref() == Some(bytes)
            })
    };
    if directory.exists() || sdkmirror::is_link(&directory) {
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
            Err(error) => Err(Error::new(format!("Cannot publish native runtime {}: {error}",directory.display()))),
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
            bytes.extend(crate::platform_io::read(&path)?);
            copies.insert(relative, bytes);
        } else {
            links.insert(relative, path);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[cfg(windows)]
    #[test]
    fn support_snapshots_wait_for_exclusive_native_editor_reads() {
        use super::*;
        use std::os::windows::fs::OpenOptionsExt;
        let root=std::env::temp_dir().join(format!("thor-native-support-reader-{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir_all(root.join("runtime/sample")).unwrap();
        let source=root.join("runtime/sample/input.odin");let original=b"package sample\nVALUE :: 1\n";
        fs::write(&source,original).unwrap();fs::write(root.join("runtime/sample/input_test.odin"),b"package sample\n").unwrap();
        for tabs in [false,true] {
            let held=fs::OpenOptions::new().read(true).share_mode(0).open(&source).unwrap();
            let owner=root.clone();let (sender,receiver)=std::sync::mpsc::channel();
            let worker=std::thread::spawn(move||{sender.send(()).unwrap();prepare(&owner,tabs)});
            receiver.recv().unwrap();std::thread::sleep(std::time::Duration::from_millis(40));drop(held);
            let view=worker.join().unwrap().unwrap();let bytes=fs::read(view.join("sample/input.odin")).unwrap();
            if !bytes.ends_with(original) {panic!("native support snapshot changed its authoritative bytes");}
        }
        fs::remove_dir_all(root).unwrap();
    }
    use super::*;
    #[test]
    fn copied_native_debug_aliases_are_byte_verified_and_preserve_policy_prefixes() {
        let root=std::env::temp_dir().join(format!("thor-runtime-debug-aliases-{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir_all(root.join("runtime/sample")).unwrap();fs::create_dir_all(root.join("view/sample")).unwrap();
        let raw=b"package sample\nwork::proc(){}\n";
        fs::write(root.join("runtime/sample/code.odin"),raw).unwrap();
        fs::write(root.join("runtime/sample/code_test.odin"),b"excluded").unwrap();
        fs::write(root.join("view/sample/code.odin"),raw).unwrap();
        let plain=debug_aliases(&root,&root.join("view")).unwrap();
        fs::write(root.join("view/sample/code.odin"),[b"#+vet !tabs\n".as_slice(),raw.as_slice()].concat()).unwrap();
        let prefixed=debug_aliases(&root,&root.join("view")).unwrap();
        fs::write(root.join("view/sample/code.odin"),b"changed").unwrap();let rejected=debug_aliases(&root,&root.join("view")).is_err();
        fs::remove_dir_all(&root).unwrap();
        if plain.len()!=1||plain[0]["offset"]!=0||plain[0]["original_sha256"]!=sha256(raw)||prefixed.len()!=1||prefixed[0]["offset"]!=json!(b"#+vet !tabs\n".len())||!rejected {panic!("native alias debug map changed source identity or accepted different bytes");}
    }
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
        let link = sdkmirror::points_to(&first.join("sample/asset.txt"),&expected.join("asset.txt"));
        let normal = sdkmirror::points_to(&plain.join("sample/sample.odin"),&expected.join("sample.odin"));
        let again = prepare(&directory, true).unwrap();
        fs::write(package.join("sample.odin"), "package sample\nVALUE :: 7\n").unwrap();
        let changed = prepare(&directory, true).unwrap();
        let current = fs::read_to_string(changed.join("sample/sample.odin")).unwrap();
        fs::remove_dir_all(&directory).unwrap();
        if !header.starts_with("#+vet !tabs\n")
            || !nested.starts_with("#+vet !tabs\n")
            || test
            || !link
            || !normal
            || again != first
            || changed == first
            || !current.contains("VALUE :: 7")
        {
            panic!("runtime policy view changed authority, content caching or test filtering");
        }
    }
    #[cfg(windows)]
    #[test]
    fn atomically_replaced_support_files_get_a_fresh_unprivileged_view() {
        let root=std::env::temp_dir().join(format!("thor-runtime-replace-{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        let package=root.join("runtime/sample");fs::create_dir_all(&package).unwrap();
        let source=package.join("sample.odin");fs::write(&source,"package sample\nVALUE :: 1\n").unwrap();
        fs::write(package.join("sample_test.odin"),"excluded test").unwrap();
        let original_time=fs::metadata(&source).unwrap().modified().unwrap();
        let before=prepare(&root,false).unwrap();
        atomic_write(&source,b"package sample\nVALUE :: 2\n").unwrap();
        fs::OpenOptions::new().write(true).open(&source).unwrap().set_times(fs::FileTimes::new().set_modified(original_time)).unwrap();
        let after=prepare(&root,false).unwrap();let text=fs::read_to_string(after.join("sample/sample.odin")).unwrap();
        fs::remove_dir_all(&root).unwrap();
        if before==after||!text.contains("VALUE :: 2"){panic!("runtime view reused a replaced file with matching timestamps");}
    }

    #[cfg(windows)]
    #[test]
    fn readonly_support_copies_follow_edits_with_restored_metadata() {
        let root=std::env::temp_dir().join(format!("thor-runtime-readonly-{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        let package=root.join("runtime/sample");fs::create_dir_all(&package).unwrap();
        let source=package.join("asset.txt");fs::write(&source,b"original").unwrap();
        fs::write(package.join("sample_test.odin"),"excluded test").unwrap();
        let permissions=fs::metadata(&source).unwrap().permissions();
        let original_time=fs::metadata(&source).unwrap().modified().unwrap();
        let mut readonly=permissions.clone();readonly.set_readonly(true);fs::set_permissions(&source,readonly.clone()).unwrap();
        let before=prepare(&root,false).unwrap();
        fs::set_permissions(&source,permissions.clone()).unwrap();
        fs::write(&source,b"modified").unwrap();
        fs::OpenOptions::new().write(true).open(&source).unwrap().set_times(fs::FileTimes::new().set_modified(original_time)).unwrap();
        fs::set_permissions(&source,readonly).unwrap();
        let after=prepare(&root,false).unwrap();let bytes=crate::platform_io::read(after.join("sample/asset.txt")).unwrap();
        fs::set_permissions(&source,permissions).unwrap();fs::remove_dir_all(&root).unwrap();
        if before==after||bytes!=b"modified"{panic!("runtime view reused stale copied support bytes");}
    }

}
