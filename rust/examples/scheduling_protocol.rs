//! Compare scheduling decisions and contents with the frozen reference.
use serde_json::json;
use std::{collections::BTreeMap, path::Path, time::Duration};
use thor_compiler::{
    evaluation::Scheduler,
    project::{Options, Project},
};
fn run() -> Result<(), thor_compiler::diagnostic::Error> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let entry = std::env::args().nth(1).expect("source");
    let arguments = std::env::args().skip(2).collect::<Vec<_>>();
    let flags = arguments
        .iter()
        .filter(|arg| arg.starts_with('-'))
        .cloned()
        .collect();
    let mut project = Project::new(Options {
            native_policy_probe: false,
            native_policy_only: false,
            odin_compatible: false,
        command: "check".into(),
        entry: entry.into(),
        output: None,
        cache: arguments
            .iter()
            .find_map(|arg| arg.strip_prefix("cache="))
            .map(Into::into),
        odin: std::env::var("THOR_ODIN").unwrap_or_else(|_| "odin".into()),
        flags,
        args: vec![],
        no_cache: !arguments.iter().any(|arg| arg.starts_with("cache=")),
        json: true,
        root: root.into(),
        maps: false,
        tracking: "off".into(),
        no_sanitize: true,
        overlays: BTreeMap::new(),
        editor_info: false,
        run_timeout: std::time::Duration::from_secs(5),
        comptime: Default::default(),
        clear_cache: false,
        cache_report: false,
        raw_diagnostics: false,
        vet: None,
        vet_apply: false,
        vet_ask: false,
        unverified: Default::default(),
    })?;
    project.inspect_sources()?;
    let mut scheduler = Scheduler::new(&project, Duration::from_secs(5));
    scheduler.evaluate_all()?;
    let cache_report = scheduler.cache_report();
    let results = scheduler
        .results
        .iter()
        .map(|(node, value)| {
            let source = &project.sources[node.file];
            let ast = source.node(node.node);
            json!([
                [source.path, ast.kind.as_str(), ast.span.start, ast.span.end],
                value
            ])
        })
        .collect::<Vec<_>>();
    let cases = std::mem::take(&mut scheduler.cases);
    let specialized = cases
        .iter()
        .flat_map(|(node, cases)| {
            cases
                .values()
                .filter(|case| case.skipped.is_none())
                .map(|case| {
                    let source = &project.sources[node.file];
                    let ast = source.node(node.node);
                    json!([
                        [source.path, ast.kind.as_str(), ast.span.start, ast.span.end],
                        case.value
                    ])
                })
        })
        .collect::<Vec<_>>();
    let values = std::mem::take(&mut scheduler.results);
    let skips = scheduler
        .inactive
        .iter()
        .map(|node| (*node, scheduler.unchecked.contains(node)))
        .collect();
    drop(scheduler);
    let mut packet = json!({"results":results,"specialized":specialized,"cache":cache_report});
    if let Some(destination) = arguments.iter().find_map(|arg| arg.strip_prefix("emit=")) {
        project.evaluated = values;
        project.specialized = cases;
        project.evaluation_scheduled = true;
        project.evaluation_skips = skips;
        project.destination = std::fs::canonicalize(Path::new(destination).parent().unwrap())?
            .join(Path::new(destination).file_name().unwrap());
        let target = project.lower()?;
        packet["target"] = json!(target);
    }
    println!("{}", packet);
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!(
            "{}",
            json!({"message":error.message,"diagnostics":error.diagnostics,"unsupported":error.unsupported})
        );
        std::process::exit(1);
    }
}
