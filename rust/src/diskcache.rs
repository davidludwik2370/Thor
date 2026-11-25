//! Bounded, disposable disk storage. Cache failures and eviction are misses,
//! never compiler errors. Independent buckets bound both space and maintenance
//! work; no whole-cache walk, index database or background process is needed.
use std::{
    env, fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex, OnceLock,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};

pub const BUDGET: u64 = 512 * 1024 * 1024;
const BUCKETS: u64 = 16;
const ENTRIES: usize = 256;
const DIRECTORY: &str = "store-v2";
static SERIAL: AtomicU64 = AtomicU64::new(0);
static WRITERS: OnceLock<[Mutex<()>; 16]> = OnceLock::new();

pub fn default_root() -> Option<PathBuf> {
    if let Some(path) = env::var_os("THOR_CACHE_DIR").filter(|v| !v.is_empty()) {
        return Some(path.into());
    }
    if cfg!(windows) {
        return env::var_os("LOCALAPPDATA").map(|p| PathBuf::from(p).join("Thor/cache"));
    }
    if cfg!(target_os = "macos") {
        return env::var_os("HOME").map(|p| PathBuf::from(p).join("Library/Caches/Thor"));
    }
    if let Some(path) = env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
    {
        return Some(path.join("thor"));
    }
    env::var_os("HOME").map(|p| PathBuf::from(p).join(".cache/thor"))
}

fn valid_namespace(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 96
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
fn digest(name: &str) -> bool {
    name.len() == 64
        && name
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn managed(name: &str) -> bool {
    name.strip_suffix(".bin")
        .and_then(|s| s.rsplit_once('-'))
        .is_some_and(|(namespace, key)| valid_namespace(namespace) && digest(key))
}
fn directory(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_dir())
}
fn file(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_file())
}
fn location(root: &Path, namespace: &str, key: &str) -> Option<PathBuf> {
    if !valid_namespace(namespace) || !digest(key) {
        return None;
    }
    let hash = crate::digest::sha256(format!("{namespace}:{key}").as_bytes());
    Some(
        root.join(DIRECTORY)
            .join(&hash[..1])
            .join(format!("{namespace}-{key}.bin")),
    )
}
fn safe_parent(root: &Path, path: &Path) -> bool {
    directory(root) && directory(&root.join(DIRECTORY)) && path.parent().is_some_and(directory)
}

pub fn read(root: &Path, namespace: &str, key: &str) -> Option<Vec<u8>> {
    let path = location(root, namespace, key)?;
    if !safe_parent(root, &path) || !file(&path) {
        return None;
    }
    let mut source = fs::File::open(&path).ok()?;
    let metadata = source.metadata().ok()?;
    if metadata.len() > BUDGET / BUCKETS {
        return None;
    }
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    (&mut source)
        .take(BUDGET / BUCKETS + 1)
        .read_to_end(&mut bytes)
        .ok()?;
    if bytes.len() as u64 > BUDGET / BUCKETS {
        return None;
    }
    // Approximate LRU, without a metadata write for every hot SDK lookup.
    if metadata
        .modified()
        .ok()
        .and_then(|t| t.elapsed().ok())
        .is_some_and(|age| age >= Duration::from_secs(3600))
    {
        let times = fs::FileTimes::new().set_modified(SystemTime::now());
        if source.set_times(times).is_err() {
            // Windows read handles need FILE_WRITE_ATTRIBUTES to refresh mtime.
            // Reopen only an old entry; read-only caches still remain readable.
            if let Ok(writable) = fs::OpenOptions::new().write(true).open(&path) {
                let _ = writable.set_times(fs::FileTimes::new().set_modified(SystemTime::now()));
            }
        }
    }
    Some(bytes)
}

struct Lock(PathBuf);
impl Drop for Lock {
    fn drop(&mut self) {
        let _ = fs::remove_dir(&self.0);
    }
}
fn charge(bytes: u64) -> u64 {
    bytes.max(1).div_ceil(4096) * 4096
}

pub fn write(root: &Path, namespace: &str, key: &str, bytes: &[u8], sync: bool) -> bool {
    write_budget(root, namespace, key, bytes, sync, BUDGET / BUCKETS, ENTRIES)
}
fn write_budget(
    root: &Path,
    namespace: &str,
    key: &str,
    bytes: &[u8],
    sync: bool,
    budget: u64,
    entries: usize,
) -> bool {
    let Some(path) = location(root, namespace, key) else {
        return false;
    };
    if charge(bytes.len() as u64) > budget || entries == 0 {
        return false;
    }
    if fs::create_dir_all(root).is_err() || !directory(root) {
        return false;
    }
    let store = root.join(DIRECTORY);
    if fs::create_dir_all(&store).is_err() || !directory(&store) {
        return false;
    }
    let parent = path.parent().unwrap();
    if fs::create_dir_all(parent).is_err() || !safe_parent(root, &path) {
        return false;
    }
    // Workers within one compiler coordinate rather than discarding useful
    // cold SDK writes. Independent compiler processes still never wait.
    let bucket = parent
        .file_name()
        .and_then(|s| s.to_str())
        .and_then(|s| u8::from_str_radix(s, 16).ok())
        .unwrap() as usize;
    let Ok(_writer) =
        WRITERS.get_or_init(|| std::array::from_fn(|_| Mutex::new(())))[bucket].lock()
    else {
        return false;
    };
    // Another writer may be active. Skipping optional storage avoids waiting on
    // compilation, and also handles abandoned locks without stealing a live one.
    let lock = parent.join(".write-lock");
    if fs::create_dir(&lock).is_err() {
        return false;
    }
    let _lock = Lock(lock);
    if fs::symlink_metadata(&path).is_ok() && !file(&path) {
        return false;
    }
    let mut stored = Vec::new();
    let mut used = 0;
    let Ok(children) = fs::read_dir(parent) else {
        return false;
    };
    for entry in children.flatten() {
        if !entry.file_type().is_ok_and(|t| t.is_file()) {
            continue;
        }
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if !managed(name) {
            continue;
        }
        let Ok(metadata) = entry.metadata() else {
            return false;
        };
        let size = charge(metadata.len());
        used += size;
        stored.push((
            metadata.modified().unwrap_or(UNIX_EPOCH),
            entry.path(),
            size,
        ));
    }
    // Remove the old version before reserving replacement space. A reader that
    // already opened it can finish; any other reader simply recomputes.
    if let Some(index) = stored.iter().position(|(_, p, _)| p == &path) {
        let (_, _, size) = stored.swap_remove(index);
        if fs::remove_file(&path).is_err() {
            return false;
        }
        used -= size;
    }
    stored.sort_unstable_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
    let mut count = stored.len();
    for (_, old, size) in stored {
        if used + charge(bytes.len() as u64) <= budget && count < entries {
            break;
        }
        if fs::remove_file(old).is_err() {
            return false;
        }
        used -= size;
        count -= 1;
    }
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temporary = parent.join(format!(
        ".pending-{}-{nonce}-{}",
        std::process::id(),
        SERIAL.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| -> std::io::Result<()> {
        let mut output = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        output.write_all(bytes)?;
        if sync {
            output.sync_all()?;
        }
        drop(output);
        fs::rename(&temporary, &path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result.is_ok()
}

pub fn clear(root: &Path, accept: impl Fn(&str) -> bool) {
    let store = root.join(DIRECTORY);
    if !directory(root) || !directory(&store) {
        return;
    }
    for bucket in "0123456789abcdef".chars() {
        let directory = store.join(bucket.to_string());
        if !self::directory(&directory) {
            continue;
        }
        let Ok(entries) = fs::read_dir(directory) else {
            continue;
        };
        for entry in entries.flatten() {
            let name = entry.file_name();
            let Some(name) = name.to_str() else {
                continue;
            };
            if managed(name)
                && name
                    .strip_suffix(".bin")
                    .and_then(|n| n.rsplit_once('-'))
                    .is_some_and(|(namespace, _)| accept(namespace))
                && entry.file_type().is_ok_and(|kind| kind.is_file())
            {
                let _ = fs::remove_file(entry.path());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Temp(PathBuf);
    impl Temp {
        fn new() -> Self {
            let p = env::temp_dir().join(format!(
                "thor-storage-{}-{}",
                std::process::id(),
                SERIAL.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&p).unwrap();
            Self(p)
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    #[test]
    fn bounded_bucket_evicts_old_records_and_replacement_is_atomic() {
        let root = Temp::new();
        let mut keys = Vec::new();
        for n in 0..1000 {
            let key = crate::digest::sha256(n.to_string().as_bytes());
            if location(&root.0, "test", &key).unwrap().parent()
                == location(&root.0, "test", &"a".repeat(64)).unwrap().parent()
            {
                keys.push(key);
            }
            if keys.len() == 5 {
                break;
            }
        }
        for key in &keys {
            let saved = write_budget(&root.0, "test", key, b"value", false, 8192, 2);
            if !saved {
                panic!("cache write failed");
            }
        }
        let hits = keys
            .iter()
            .filter(|key| read(&root.0, "test", key).is_some())
            .count();
        if hits != 2 {
            panic!("entry/byte budget was not enforced: {hits}");
        }
        let saved = write_budget(&root.0, "test", &keys[4], b"replacement", true, 8192, 2);
        if !saved || read(&root.0, "test", &keys[4]).as_deref() != Some(b"replacement") {
            panic!("replacement failed");
        }
        let saved = write_budget(&root.0, "test", &keys[4], &vec![0; 9000], false, 8192, 2);
        if saved || read(&root.0, "test", &keys[4]).as_deref() != Some(b"replacement") {
            panic!("oversize write damaged existing cache");
        }
    }
    #[test]
    fn namespaces_and_keys_cannot_escape_and_clear_preserves_unknown_files() {
        let root = Temp::new();
        let key = "a".repeat(64);
        for namespace in ["../escape", "", "with/slash"] {
            let saved = write(&root.0, namespace, &key, b"data", false);
            if saved {
                panic!("invalid namespace accepted");
            }
        }
        let saved = write(&root.0, "test", &key, b"data", false);
        if !saved {
            panic!("write failed");
        }
        let path = location(&root.0, "test", &key).unwrap();
        let notes = path.parent().unwrap().join("notes.txt");
        fs::write(&notes, b"keep").unwrap();
        clear(&root.0, |_| true);
        if path.exists() || fs::read(notes).unwrap() != b"keep" {
            panic!("clear changed unrelated files");
        }
    }
    #[test]
    fn concurrent_writers_never_publish_partial_records() {
        let root = Temp::new();
        let key = "b".repeat(64);
        let workers = (0..16)
            .map(|n| {
                let root = root.0.clone();
                let key = key.clone();
                std::thread::spawn(move || write(&root, "test", &key, &vec![n; 8192], false))
            })
            .collect::<Vec<_>>();
        let mut writes = 0;
        for worker in workers {
            writes += usize::from(worker.join().unwrap());
        }
        let bytes = read(&root.0, "test", &key).unwrap();
        if writes == 0 || bytes.len() != 8192 || bytes.iter().any(|b| *b != bytes[0]) {
            panic!("partial concurrent cache record");
        }
        let parent = location(&root.0, "test", &key).unwrap();
        if fs::read_dir(parent.parent().unwrap())
            .unwrap()
            .flatten()
            .any(|e| e.file_name().to_string_lossy().starts_with('.'))
        {
            panic!("writer left temporary files/locks");
        }
    }
    #[test]
    fn namespace_shares_quota_and_recent_reads_protect_hot_entries() {
        let root = Temp::new();
        let key = "e".repeat(64);
        let bucket = location(&root.0, "first", &key).unwrap();
        let other = (0..1000)
            .map(|n| crate::digest::sha256(n.to_string().as_bytes()))
            .find(|key| location(&root.0, "second", key).unwrap().parent() == bucket.parent())
            .unwrap();
        let a = write_budget(&root.0, "first", &key, b"first", false, 16384, 1);
        let b = write_budget(&root.0, "second", &other, b"second", false, 16384, 1);
        if !a
            || !b
            || read(&root.0, "first", &key).is_some()
            || read(&root.0, "second", &other).as_deref() != Some(b"second")
        {
            panic!("namespaces escaped shared entry budget");
        }
        let source = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(location(&root.0, "second", &other).unwrap())
            .unwrap();
        source
            .set_times(fs::FileTimes::new().set_modified(UNIX_EPOCH + Duration::from_secs(1)))
            .unwrap();
        let _bytes = read(&root.0, "second", &other).unwrap();
        if source
            .metadata()
            .unwrap()
            .modified()
            .unwrap()
            .elapsed()
            .unwrap()
            > Duration::from_secs(10)
        {
            panic!("cache hit did not refresh stale LRU timestamp");
        }
    }
    #[test]
    fn unavailable_or_locked_storage_is_optional() {
        let root = Temp::new();
        let blocked = root.0.join("file");
        fs::write(&blocked, b"preserve").unwrap();
        let key = "d".repeat(64);
        let saved = write(&blocked, "test", &key, b"data", false);
        if saved
            || read(&blocked, "test", &key).is_some()
            || fs::read(&blocked).unwrap() != b"preserve"
        {
            panic!("unavailable cache changed files");
        }
        let saved = write(&root.0, "test", &key, b"good", false);
        if !saved {
            panic!("write failed");
        }
        let path = location(&root.0, "test", &key).unwrap();
        let lock = path.parent().unwrap().join(".write-lock");
        fs::create_dir(&lock).unwrap();
        let saved = write(&root.0, "test", &key, b"replacement", false);
        if saved || read(&root.0, "test", &key).as_deref() != Some(b"good") {
            panic!("busy cache changed existing entry");
        }
        fs::remove_dir(lock).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn links_are_never_followed_for_write_read_or_eviction() {
        let root = Temp::new();
        let outside = Temp::new();
        let key = "c".repeat(64);
        fs::create_dir(root.0.join(DIRECTORY)).unwrap();
        let path = location(&root.0, "test", &key).unwrap();
        std::os::unix::fs::symlink(&outside.0, path.parent().unwrap()).unwrap();
        let saved = write(&root.0, "test", &key, b"data", false);
        clear(&root.0, |_| true);
        if saved
            || read(&root.0, "test", &key).is_some()
            || fs::read_dir(&outside.0).unwrap().next().is_some()
        {
            panic!("cache followed a directory link");
        }
    }
    #[cfg(unix)]
    #[test]
    fn linked_records_and_bucket_links_survive_clear() {
        let root = Temp::new();
        let outside = Temp::new();
        let key = "f".repeat(64);
        let path = location(&root.0, "test", &key).unwrap();
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let target = outside.0.join("data");
        fs::write(&target, b"keep").unwrap();
        std::os::unix::fs::symlink(&target, &path).unwrap();
        let saved = write(&root.0, "test", &key, b"replace", false);
        clear(&root.0, |_| true);
        if saved
            || read(&root.0, "test", &key).is_some()
            || !path.is_symlink()
            || fs::read(&target).unwrap() != b"keep"
        {
            panic!("cache changed a linked record");
        }
    }
}
