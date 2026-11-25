//! Real-parser comparison harness for evaluator binding/type operations.
use serde_json::{json, Value};
use std::{collections::BTreeMap, path::Path};
use thor_compiler::{
    ast::NodeId,
    borroweval::Evaluator,
    borrowflow::Arena,
    project::{Options, Project},
    semantics::{Ref, Resolver, Type},
    specialization::{Binding, Bindings, Specializations, TypeCode},
};
fn packet(result: Result<String, thor_compiler::specialization::Unresolved>) -> Value {
    match result {
        Ok(code) => json!({"code":code}),
        Err(error) => json!({"error":error.0}),
    }
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let entry = std::env::args().nth(1).ok_or("expected source")?;
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("compiler root")?;
    let mut project = Project::new(Options {
        native_policy_probe: false,
        native_policy_only: false,
        odin_compatible: false,
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
    let mut resolver = Resolver::new(&project.sources);
    let mut types = BTreeMap::new();
    let mut type_imports = BTreeMap::new();
    let symbols = resolver.symbols.clone();
    for symbol in symbols.iter()
        .filter(|symbol| symbol.name.starts_with("sample_"))
        .cloned()
    {
        let source = &resolver.sources[symbol.reference.file];
        let at = Ref {
            file: symbol.reference.file,
            node: symbol.name_node,
        };
        let typ = resolver.specialized_type(at, &Default::default());
        let mut local = TypeCode::new(at.file, Some(at));
        let mut detached = TypeCode::new(at.file, None);
        let mut bridged = TypeCode::new(at.file, None);
        bridged.allow_imports = true;
        types.insert(format!("{}:{}",source.path.display(),source.node(symbol.name_node).span.start),
            json!({"local":packet(local.render(&typ,&resolver)),"detached":packet(detached.render(&typ,&resolver)),"bridged":packet(bridged.render(&typ,&resolver))}));
        type_imports.extend(local.imports);
        type_imports.extend(detached.imports);
        type_imports.extend(bridged.imports);
    }
    let mut state = Specializations::default();
    let mut lexical = BTreeMap::new();
    for (file, source) in resolver.sources.iter().enumerate() {
        if !source
            .path
            .starts_with(project.options.entry.parent().unwrap())
        {
            continue;
        }
        for (index, node) in source
            .nodes
            .iter()
            .enumerate()
            .filter(|(_, node)| node.kind == "Procedure")
        {
            let proc = Ref {
                file,
                node: NodeId(index),
            };
            if !node.parent.is_some_and(|parent| {
                source
                    .group(parent, "names")
                    .iter()
                    .any(|name| source.text(*name) == "generic")
            }) {
                continue;
            }
            let mut arena = Arena::default();
            let parameters = Evaluator {
                resolver: &mut resolver,
                arena: &mut arena,
            }
            .parameters(proc);
            for (name, _) in parameters {
                let Some(symbol) = resolver.symbol(name) else {
                    continue;
                };
                let declaration = &resolver.symbols[symbol];
                if !["T", "N"].contains(&declaration.name.as_str()) {
                    continue;
                }
                state.bindings.insert(
                    (declaration.reference, declaration.name.clone()),
                    Binding {
                        symbol,
                        typ: Type::scalar("int"),
                        value: (declaration.name == "N")
                            .then(|| json!({"kind":"integer","value":"7"})),
                        canonical: String::new(),
                        condition: String::new(),
                    },
                );
            }
        }
    }
    for (file, source) in resolver.sources.iter().enumerate() {
        for (index, node) in source
            .nodes
            .iter()
            .enumerate()
            .filter(|(_, node)| ["Ident", "PolyType"].contains(&node.kind.as_str()))
        {
            let at = Ref {
                file,
                node: NodeId(index),
            };
            let mut names = state
                .relevant(at, &resolver)
                .keys()
                .map(|(_, name)| name.clone())
                .collect::<Vec<_>>();
            names.sort();
            lexical.insert(
                format!("{}:{}", source.path.display(), node.span.start),
                names,
            );
        }
    }
    let before = Specializations::fingerprint(&state.bindings, &resolver);
    let mut changed = state.bindings.clone();
    for binding in changed
        .values_mut()
        .filter(|binding| binding.value.is_some())
    {
        binding.value = Some(json!({"kind":"integer","value":"8"}));
    }
    let different = Specializations::fingerprint(&changed, &resolver);
    let saved = state.enter(
        changed,
        Some(Ref {
            file: 0,
            node: NodeId(0),
        }),
        Some(Bindings::new()),
    );
    let frame_count = state.frames.len();
    state.restore(saved);
    let mut context = TypeCode::new(0, None);
    println!(
        "{}",
        json!({"types":types,"imports":type_imports,"lexical":lexical,"declarations":packet(Specializations::declarations(&state.bindings,&mut context,&resolver)),
        "predicate":packet(Specializations::predicate(&state.bindings,&mut context,&resolver)),
        "restored":Specializations::fingerprint(&state.bindings,&resolver)==before,"changed":before!=different,
        "frames":[frame_count,state.frames.len()]})
    );
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
