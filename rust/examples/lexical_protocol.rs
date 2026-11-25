//! Verify bound lookup, shadowing and restoration in the native resolver.
use serde_json::{json, Value};
use std::{collections::BTreeMap, path::Path};
use thor_compiler::{
    ast::NodeId,
    project::{Options, Project},
    semantics::{Ref, Resolver, Type},
    specialization::{Binding, Specializations, TypeCode},
};
fn queries(
    r: &mut Resolver<'_>,
    held: Option<&BTreeMap<String, Type>>,
) -> (Value, BTreeMap<String, Type>) {
    let mut results = BTreeMap::new();
    let mut captured = BTreeMap::new();
    for (file, source) in r.sources.iter().enumerate() {
        for (index, _) in source
            .nodes
            .iter()
            .enumerate()
            .filter(|(_, node)| node.kind == "Call")
        {
            let id = NodeId(index);
            if source
                .role(id, "callee")
                .is_none_or(|callee| !["query", "query_bool"].contains(&source.text(callee)))
            {
                continue;
            }
            let args = source.group(id, "args");
            if args.len() != 2 {
                continue;
            }
            let at = Ref {
                file,
                node: args[1],
            };
            let label = thor_compiler::lower::unquote(source.text(args[0])).unwrap();
            let typ = held
                .and_then(|held| held.get(&label))
                .cloned()
                .unwrap_or_else(|| r.specialized_type(at, &Default::default()));
            captured.insert(label.clone(), typ.clone());
            let mut context = TypeCode::new(file, Some(at));
            let code = context.render(&typ, r).ok();
            let integer = r.constant_int(at).map(|n| n.to_string());
            let boolean = source
                .role(id, "callee")
                .filter(|callee| source.text(*callee) == "query_bool")
                .and_then(|_| r.context_condition(at, &Default::default()));
            let fields = r
                .record_fields(&typ)
                .into_iter()
                .map(|(name, typ)| (name, context.render(&typ, r).ok()))
                .collect::<BTreeMap<_, _>>();
            results.insert(
                label,
                json!({"type":code,"integer":integer,"fields":fields,"boolean":boolean}),
            );
        }
    }
    (json!(results), captured)
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let mut project = Project::new(Options {
            native_policy_probe: false,
            native_policy_only: false,
            odin_compatible: false,
        command: "check".into(),
        entry: std::env::args().nth(1).ok_or("source")?.into(),
        output: None,
        cache: None,
        odin: std::env::var("THOR_ODIN").unwrap_or_else(|_| "odin".into()),
        flags: vec!["-debug".into()],
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
        unverified: Default::default(),
    })?;
    project.inspect_sources()?;
    let mut resolver = Resolver::new(&project.sources);
    let constant = std::env::args()
        .nth(2)
        .map(|value| serde_json::from_str::<Value>(&value))
        .transpose()?
        .unwrap_or_else(|| json!({"kind":"integer","value":"7"}));
    let mut state = Specializations::default();
    let symbols = resolver.symbols.clone();
    for (index, symbol) in symbols.iter().cloned().enumerate() {
        let source = &resolver.sources[symbol.reference.file];
        if !source.node(symbol.scope).parent.is_some_and(|decl| {
            source
                .group(decl, "names")
                .iter()
                .any(|name| source.text(*name) == "generic")
        }) {
            continue;
        }
        if !["T", "N"].contains(&symbol.name.as_str()) {
            continue;
        }
        let at = Ref {
            file: symbol.reference.file,
            node: source.role(symbol.scope, "body").unwrap(),
        };
        if resolver.lookup(at, &symbol.name) != Some(index) {
            continue;
        }
        let typ = if symbol.name == "N" {
            symbol
                .annotation
                .map(|node| {
                    resolver.specialized_type(
                        Ref {
                            file: symbol.reference.file,
                            node,
                        },
                        &Default::default(),
                    )
                })
                .unwrap_or(Type::scalar("int"))
        } else {
            Type::scalar("int")
        };
        state.bindings.insert(
            (symbol.reference, symbol.name.clone()),
            Binding {
                symbol: index,
                typ,
                value: (symbol.name == "N").then(|| constant.clone()),
                canonical: String::new(),
                condition: String::new(),
            },
        );
    }
    let before = queries(&mut resolver, None).0;
    state.apply(&mut resolver);
    let (bound, types) = queries(&mut resolver, None);
    let saved = state.enter(Default::default(), None, None);
    state.apply(&mut resolver);
    let cleared = queries(&mut resolver, None).0;
    let held = queries(&mut resolver, Some(&types)).0;
    state.restore(saved);
    state.apply(&mut resolver);
    let restored = queries(&mut resolver, None).0;
    println!(
        "{}",
        json!({"before":before,"bound":bound,"cleared":cleared,"restored":restored,"held":held})
    );
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
