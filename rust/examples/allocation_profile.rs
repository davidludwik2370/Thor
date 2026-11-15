//! Development-only allocation counters for loading, lowering and dropping a project.
//! Timings for performance comparisons should use the production executable.
use serde::Serialize;
use std::{
    alloc::{GlobalAlloc, Layout, System},
    collections::BTreeMap,
    path::{Path, PathBuf},
    sync::atomic::{AtomicUsize, Ordering},
    time::{Duration, Instant},
};
use thor_compiler::{
    ast::Node,
    project::{Options, Project},
};

struct Allocator;
static CALLS: AtomicUsize = AtomicUsize::new(0);
static REALLOCS: AtomicUsize = AtomicUsize::new(0);
static BYTES: AtomicUsize = AtomicUsize::new(0);
static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);

fn added(bytes: usize) {
    BYTES.fetch_add(bytes, Ordering::Relaxed);
    let current = LIVE.fetch_add(bytes, Ordering::Relaxed) + bytes;
    PEAK.fetch_max(current, Ordering::Relaxed);
}
// SAFETY: All requests retain System's pointer/layout contract. The counters
// allocate no memory and are independent of program allocation behavior.
unsafe impl GlobalAlloc for Allocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() {
            CALLS.fetch_add(1, Ordering::Relaxed);
            added(layout.size());
        }
        pointer
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc_zeroed(layout) };
        if !pointer.is_null() {
            CALLS.fetch_add(1, Ordering::Relaxed);
            added(layout.size());
        }
        pointer
    }
    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        LIVE.fetch_sub(layout.size(), Ordering::Relaxed);
        unsafe { System.dealloc(pointer, layout) };
    }
    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        let result = unsafe { System.realloc(pointer, layout, size) };
        if !result.is_null() {
            REALLOCS.fetch_add(1, Ordering::Relaxed);
            LIVE.fetch_sub(layout.size(), Ordering::Relaxed);
            added(size);
        }
        result
    }
}
#[global_allocator]
static ALLOCATOR: Allocator = Allocator;

#[derive(Serialize)]
struct Snapshot {
    allocations: usize,
    reallocations: usize,
    requested_bytes: usize,
    live_bytes: usize,
    peak_live_bytes: usize,
}
fn snapshot() -> Snapshot {
    Snapshot {
        allocations: CALLS.load(Ordering::Relaxed),
        reallocations: REALLOCS.load(Ordering::Relaxed),
        requested_bytes: BYTES.load(Ordering::Relaxed),
        live_bytes: LIVE.load(Ordering::Relaxed),
        peak_live_bytes: PEAK.load(Ordering::Relaxed),
    }
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args().skip(1);
    let entry = PathBuf::from(arguments.next().ok_or("expected source directory")?);
    let output = PathBuf::from(arguments.next().ok_or("expected output directory")?);
    let cache = PathBuf::from(arguments.next().ok_or("expected cache directory")?);
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("toolchain root")?;
    let flags = arguments.collect();
    let started = Instant::now();
    let mut project = Project::new(Options {
            native_policy_probe: false,
            odin_compatible: false,
        command: "transpile".into(),
        entry,
        output: Some(output),
        cache: Some(cache),
        odin: std::env::var("THOR_ODIN").unwrap_or_else(|_| "odin".into()),
        flags,
        args: vec![],
        no_cache: false,
        json: true,
        root: root.into(),
        maps: false,
        tracking: "off".into(),
        no_sanitize: true,
        overlays: BTreeMap::new(),
        editor_info: false,
        run_timeout: Duration::from_secs(5),
        clear_cache: false,
        cache_report: false,
        raw_diagnostics: false,
        vet: None,
        vet_apply: false,
        vet_ask: false,
    })?;
    let loaded_time = started.elapsed().as_secs_f64();
    let loaded = snapshot();
    project.lower()?;
    let lowered_time = started.elapsed().as_secs_f64();
    let lowered = snapshot();
    let nodes = project
        .sources
        .iter()
        .map(|source| source.nodes.len())
        .sum::<usize>();
    let sources = project.sources.len();
    drop(project);
    let dropped_time = started.elapsed().as_secs_f64();
    let dropped = snapshot();
    println!(
        "{}",
        serde_json::json!({"scope":"Rust process, cumulative counts; native children excluded",
        "sources":sources,"nodes":nodes,"node_bytes":std::mem::size_of::<Node>(),
        "loaded":{"seconds":loaded_time,"counters":loaded},
        "lowered":{"seconds":lowered_time,"counters":lowered},
        "dropped":{"seconds":dropped_time,"counters":dropped}})
    );
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
