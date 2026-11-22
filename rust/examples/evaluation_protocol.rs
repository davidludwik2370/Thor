//! Exercise the isolated helper independently of result scheduling/installation.
use serde_json::json;
use std::{collections::BTreeMap, path::Path, time::Duration};
use thor_compiler::{
    ast::NodeId,
    project::{Options, Project},
    semantics::Ref,
};
fn run() -> Result<(), thor_compiler::diagnostic::Error> {
    let entry = std::env::args().nth(1).expect("source path");
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let arguments = std::env::args().skip(2).collect::<Vec<_>>();
    let mut project = Project::new(Options {
            native_policy_probe: false,
            native_policy_only: false,
            odin_compatible: false,
        command: "check".into(),
        entry: entry.into(),
        output: None,
        cache: None,
        odin: std::env::var("THOR_ODIN").unwrap_or_else(|_| "odin".into()),
        flags: arguments
            .iter()
            .filter(|arg| arg.starts_with('-'))
            .cloned()
            .collect(),
        args: vec![],
        no_cache: true,
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
    })?;
    project.inspect_sources()?;
    let request = project
        .sources
        .iter()
        .enumerate()
        .filter(|(_, source)| source.path == project.options.entry)
        .find_map(|(file, source)| {
            source
                .nodes
                .iter()
                .enumerate()
                .find(|(_, node)| node.kind == "Run")
                .and_then(|(index, _)| source.role(NodeId(index), "expr"))
                .map(|node| Ref { file, node })
        })
        .expect("#run request");
    if let Some(destination) = arguments.iter().find_map(|arg| arg.strip_prefix("emit=")) {
        let mut helper = project.evaluation_helper(request, destination.into())?;
        let target = helper.lower()?;
        println!("{}", json!({"target":target}));
    } else {
        let seconds = arguments
            .iter()
            .find_map(|arg| arg.strip_prefix("timeout="))
            .and_then(|arg| arg.parse().ok())
            .unwrap_or(5.0);
        let value = project.execute_evaluation(request, Duration::from_secs_f64(seconds))?;
        println!("{}", json!({"value":value}));
    }
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
