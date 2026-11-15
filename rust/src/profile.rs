//! Opt-in command profiling. THOR_PROFILE names a JSON output file; ordinary
//! stdout/stderr protocols and successful program behavior are unchanged.
use serde::Serialize;
use std::{
    cell::RefCell,
    collections::BTreeMap,
    path::PathBuf,
    sync::atomic::{AtomicBool, Ordering},
    time::Instant,
};

static ENABLED: AtomicBool = AtomicBool::new(false);
#[derive(Default, Serialize)]
struct Timing {
    calls: u64,
    inclusive_seconds: f64,
    self_seconds: f64,
}
#[derive(Default)]
struct Report {
    stages: BTreeMap<&'static str, Timing>,
    worker_stages: BTreeMap<&'static str, Timing>,
    counters: BTreeMap<&'static str, u64>,
    children: Vec<f64>,
    events: Vec<serde_json::Value>,
}
thread_local! { static REPORT: RefCell<Report> = RefCell::new(Report::default()); }

pub struct Session(Option<(PathBuf, Instant)>);
impl Session {
    pub fn from_env() -> Self {
        let path = std::env::var_os("THOR_PROFILE").filter(|path| !path.is_empty());
        if let Some(path) = path {
            REPORT.with(|report| *report.borrow_mut() = Report::default());
            ENABLED.store(true, Ordering::Relaxed);
            Self(Some((path.into(), Instant::now())))
        } else {
            Self(None)
        }
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        let Some((path, started)) = self.0.take() else {
            return;
        };
        ENABLED.store(false, Ordering::Relaxed);
        let packet = REPORT.with(|report| {
            let report = report.borrow();
            serde_json::json!({"schema":1,"engine":"rust","thor_version":crate::VERSION,
                "elapsed_seconds":started.elapsed().as_secs_f64(),
                "stages":report.stages,"worker_stages":report.worker_stages,"counters":report.counters,"events":report.events})
        });
        let result = serde_json::to_vec_pretty(&packet)
            .map_err(crate::diagnostic::Error::from)
            .and_then(|bytes| crate::frontend::atomic_write(&path, &bytes));
        if let Err(error) = result {
            eprintln!("Thor profile: {}: {}", path.display(), error.message);
        }
    }
}
pub struct Span(Option<(&'static str, Instant)>);
pub fn span(name: &'static str) -> Span {
    if ENABLED.load(Ordering::Relaxed) {
        REPORT.with(|report| report.borrow_mut().children.push(0.0));
        Span(Some((name, Instant::now())))
    } else {
        Span(None)
    }
}
impl Drop for Span {
    fn drop(&mut self) {
        let Some((name, started)) = self.0.take() else {
            return;
        };
        let seconds = started.elapsed().as_secs_f64();
        REPORT.with(|report| {
            let mut report = report.borrow_mut();
            let children = report.children.pop().unwrap_or_default();
            if let Some(parent) = report.children.last_mut() {
                *parent += seconds;
            }
            let timing = report.stages.entry(name).or_default();
            timing.calls += 1;
            timing.inclusive_seconds += seconds;
            timing.self_seconds += (seconds - children).max(0.0);
        });
    }
}
pub fn count(name: &'static str, amount: u64) {
    if ENABLED.load(Ordering::Relaxed) {
        REPORT.with(|report| *report.borrow_mut().counters.entry(name).or_default() += amount);
    }
}

/// Attribute destruction without changing object lifetime or cleanup behavior.
/// Ordinary compilation still performs exactly the same drop operations.
pub fn destroy<T>(name: &'static str, value: T) {
    let _phase = span(name);
    drop(value);
}
pub struct Owned<T> { value: Option<T>, name: &'static str, cleanup: fn(T) }
impl<T> Owned<T> {
    pub fn new(name: &'static str, value: T) -> Self { Self::with_cleanup(name,value,drop) }
    pub fn with_cleanup(name: &'static str, value: T, cleanup: fn(T)) -> Self {
        Self {value:Some(value), name, cleanup}
    }
}
impl<T> std::ops::Deref for Owned<T> {
    type Target = T;
    fn deref(&self) -> &T { self.value.as_ref().expect("live profiled owner") }
}
impl<T> std::ops::DerefMut for Owned<T> {
    fn deref_mut(&mut self) -> &mut T { self.value.as_mut().expect("live profiled owner") }
}
impl<T> Drop for Owned<T> {
    fn drop(&mut self) {
        let _phase = span(self.name);
        if let Some(value) = self.value.take() { (self.cleanup)(value); }
    }
}
pub(crate) fn event(name: &'static str, data: impl FnOnce() -> serde_json::Value) {
    if ENABLED.load(Ordering::Relaxed) {
        let data = data();
        REPORT.with(|report| report.borrow_mut().events.push(serde_json::json!({"name":name,"data":data})));
    }
}

pub(crate) struct WorkerReport(Report);

pub(crate) fn worker<T>(action: impl FnOnce() -> T) -> (T, Option<WorkerReport>) {
    if !ENABLED.load(Ordering::Relaxed) {
        return (action(), None);
    }
    REPORT.with(|report| *report.borrow_mut() = Report::default());
    let result = action();
    let report = REPORT.with(|report| std::mem::take(&mut *report.borrow_mut()));
    (result, Some(WorkerReport(report)))
}

pub(crate) fn merge_worker(worker: Option<WorkerReport>) {
    let Some(WorkerReport(worker)) = worker else { return };
    REPORT.with(|report| {
        let mut report = report.borrow_mut();
        report.events.extend(worker.events);
        for (name, count) in worker.counters {
            *report.counters.entry(name).or_default() += count;
        }
        // Worker spans overlap in wall time. Keep their accumulated durations
        // separate from the parent thread's inclusive/self-time stack.
        for (name, timing) in worker.stages.into_iter().chain(worker.worker_stages) {
            let combined = report.worker_stages.entry(name).or_default();
            combined.calls += timing.calls;
            combined.inclusive_seconds += timing.inclusive_seconds;
            combined.self_seconds += timing.self_seconds;
        }
    });
}
