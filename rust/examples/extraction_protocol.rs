//! Reference closure comparison, using original source identities throughout.
use serde_json::{json, Value};
use std::{collections::BTreeMap, path::Path};
use thor_compiler::{
    ast::NodeId,
    evaluation::{CallTarget, Extraction},
    project::{Options, Project},
    semantics::{Ref, Resolver},
};
fn location(node: Ref, r: &Resolver<'_>) -> Value {
    let source = &r.sources[node.file];
    let ast = source.node(node.node);
    json!([source.path, ast.kind.as_str(), ast.span.start, ast.span.end])
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let entry = std::env::args().nth(1).ok_or("expected source")?;
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("compiler root")?;
    let mut project = Project::new(Options {
        command: "check".into(),
        entry: entry.into(),
        output: None,
        cache: None,
        odin: std::env::var("THOR_ODIN").unwrap_or_else(|_| "odin".into()),
        flags: vec!["-debug".into()],
        args: vec![],
        no_cache: true,
        json: false,
        root: root.into(),
        maps: false,
        tracking: "off".into(),
        no_sanitize: true,
        overlays: BTreeMap::new(),
        editor_info: false,
    })?;
    project.inspect_sources()?;
    let mut resolver = Resolver::new(&project.sources);
    let request = resolver
        .sources
        .iter()
        .enumerate()
        .filter(|(_, source)| source.path == project.options.entry)
        .flat_map(|(file, source)| {
            source
                .nodes
                .iter()
                .enumerate()
                .filter(|(_, node)| node.kind == "Run")
                .filter_map(move |(index, _)| {
                    source
                        .role(NodeId(index), "expr")
                        .map(|node| Ref { file, node })
                })
        })
        .next()
        .ok_or("expected #run")?;
    let graph = Extraction::build(&mut resolver, &[request])?;
    let imports = graph
        .imports
        .iter()
        .map(|node| location(*node, &resolver))
        .collect::<Vec<_>>();
    let aliases = graph
        .aliases
        .iter()
        .map(|(file, name)| json!([resolver.sources[*file].path, name]))
        .collect::<Vec<_>>();
    let queries = graph
        .queries
        .iter()
        .map(|(node, code)| json!([location(*node, &resolver), code]))
        .collect::<Vec<_>>();
    let calls = graph
        .calls
        .iter()
        .map(|(node, target)| {
            json!([
                location(*node, &resolver),
                match target {
                    CallTarget::Builtin(name) => json!(["builtin", name]),
                    CallTarget::Procedure(target) =>
                        json!(["procedure", location(*target, &resolver)]),
                }
            ])
        })
        .collect::<Vec<_>>();
    let external = graph
        .external
        .iter()
        .map(|(node, library)| json!([location(*node, &resolver), library]))
        .collect::<Vec<_>>();
    let helper_imports = graph
        .helper_imports
        .iter()
        .map(|((file, alias), path)| json!([resolver.sources[*file].path, alias, path]))
        .collect::<Vec<_>>();
    println!(
        "{}",
        json!({"keep":graph.keep.iter().map(|node|location(*node,&resolver)).collect::<Vec<_>>(),
        "imports":imports,"aliases":aliases,"procedures":graph.procedures.iter().map(|node|location(*node,&resolver)).collect::<Vec<_>>(),
        "queries":queries,"calls":calls,"external":external,"helper_imports":helper_imports})
    );
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
