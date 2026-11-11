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
    counters: BTreeMap<&'static str, u64>,
    children: Vec<f64>,
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
                "stages":report.stages,"counters":report.counters})
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
