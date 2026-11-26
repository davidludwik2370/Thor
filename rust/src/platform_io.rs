//! Bounded handling of Windows file-sharing responses; successful I/O runs once.
use std::{fs, io, path::Path};

pub(crate) fn retry_sharing<T>(mut operation: impl FnMut() -> io::Result<T>) -> io::Result<T> {
    #[cfg(not(windows))]
    {
        operation()
    }
    #[cfg(windows)]
    {
        let mut attempt = 0;
        loop {
            match operation() {
                Err(error) if matches!(error.raw_os_error(), Some(5 | 32 | 33)) && attempt < 10 => {
                    // Access denied is also returned for blocked MoveFileEx calls.
                    std::thread::sleep(std::time::Duration::from_millis(2 << attempt.min(5)));
                    attempt += 1;
                }
                result => return result,
            }
        }
    }
}

pub(crate) fn read(path: &Path) -> io::Result<Vec<u8>> {
    retry_sharing(|| fs::read(path))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn successful_and_unrelated_failed_operations_are_not_repeated() {
        let mut calls = 0;
        let value = retry_sharing(|| {
            calls += 1;
            Ok(7)
        });
        let successful_calls = calls;
        calls = 0;
        let failed = retry_sharing::<()>(|| {
            calls += 1;
            Err(io::Error::from_raw_os_error(87))
        });
        if value.unwrap() != 7 || successful_calls != 1 || failed.is_ok() || calls != 1 {
            panic!("I/O without a sharing violation was repeated");
        }
    }
    #[cfg(windows)]
    #[test]
    fn file_reads_wait_for_exclusive_readers_and_keep_persistent_failures() {
        use std::os::windows::fs::OpenOptionsExt;
        let root = std::env::temp_dir().join(format!(
            "thor-exclusive-read-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&root).unwrap();
        let source = root.join("source.odin");
        fs::write(&source, b"unchanged").unwrap();
        let held = fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(&source)
            .unwrap();
        let path = source.clone();
        let (sender, receiver) = std::sync::mpsc::channel();
        let worker = std::thread::spawn(move || {
            sender.send(()).unwrap();
            read(&path)
        });
        receiver.recv().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(40));
        drop(held);
        let bytes = worker.join().unwrap().unwrap();
        let held = fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(&source)
            .unwrap();
        let blocked = read(&source);
        drop(held);
        let original = fs::read(&source).unwrap();
        fs::remove_dir_all(root).unwrap();
        if bytes != b"unchanged" || blocked.is_ok() || original != b"unchanged" {
            panic!("file sharing retries lost their read or failure contract");
        }
    }
}
