//! Filesystem aliases used by build views and copied project trees.
use std::{io, path::Path};

#[cfg(windows)]
pub(crate) fn symbolic(target: &Path, path: &Path, directory: bool) -> io::Result<bool> {
    use std::sync::atomic::{AtomicBool, Ordering};
    static MISSING_PRIVILEGE: AtomicBool = AtomicBool::new(false);
    if MISSING_PRIVILEGE.load(Ordering::Relaxed) {
        return Ok(false);
    }
    let result = if directory {
        std::os::windows::fs::symlink_dir(target, path)
    } else {
        std::os::windows::fs::symlink_file(target, path)
    };
    match result {
        Ok(()) => Ok(true),
        Err(error) if error.raw_os_error() == Some(1314) => {
            MISSING_PRIVILEGE.store(true, Ordering::Relaxed);
            Ok(false)
        }
        Err(error)
            if matches!(
                error.kind(),
                io::ErrorKind::PermissionDenied | io::ErrorKind::Unsupported
            ) =>
        {
            Ok(false)
        }
        Err(error) => Err(error),
    }
}

#[cfg(windows)]
pub(crate) fn directory_junction(target: &Path, path: &Path) -> io::Result<()> {
    // Junction targets are absolute; relative symlink targets are relative to
    // their alias parent, not the compiler's working directory. GetFullPathName
    // in the native adapter also permits a not-yet-created target directory.
    let target = if target.is_absolute() {
        target.to_path_buf()
    } else {
        path.parent().unwrap_or(Path::new(".")).join(target)
    };
    junction::create(std::path::absolute(target)?, path)
}

pub(crate) fn directory(target: &Path, path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, path)
    }
    #[cfg(windows)]
    {
        if symbolic(target, path, true)? {
            Ok(())
        } else {
            directory_junction(target, path)
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "directory aliases are not supported on this platform",
        ))
    }
}

#[cfg(all(test, any(unix, windows)))]
mod tests {
    use super::*;
    use std::fs;
    #[test]
    fn relative_directory_aliases_follow_forward_targets_and_later_replacements() {
        let root = std::env::temp_dir().join(format!(
            "thor-directory-alias-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(root.join("output")).unwrap();
        let alias = root.join("output/alias");
        let target = root.join("output/actual");
        directory(Path::new("actual"), &alias).unwrap();
        let exists = fs::symlink_metadata(&alias).is_ok();
        fs::create_dir(&target).unwrap();
        fs::write(target.join("asset"), b"first").unwrap();
        let before = fs::read(alias.join("asset")).unwrap();
        fs::remove_file(target.join("asset")).unwrap();
        fs::remove_dir(&target).unwrap();
        fs::create_dir(&target).unwrap();
        fs::write(target.join("asset"), b"second").unwrap();
        let after = fs::read(alias.join("asset")).unwrap();
        fs::remove_dir_all(&root).unwrap();
        if !exists || before != b"first" || after != b"second" {
            panic!("directory alias lost relative or forward-target semantics");
        }
    }
}
