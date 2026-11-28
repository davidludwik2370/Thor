//! Internal real-parser differential harness for symbolic helper provenance.
use serde_json::{json, Value};
use std::{collections::BTreeMap, path::Path};
use thor_compiler::{
    borroweval::Evaluator,
    borrowprogram::Model,
    frontend::{self, Frontend},
    permissions::{Access, Aspect, Permission, Step, Trait},
    project::{Options, Project},
    purity::Purity,
    semantics::{Ref, Resolver},
};
fn step(step: &Step) -> Value {
    match step {
        Step::Field(name) => json!(["field", name]),
        Step::Item => json!(["item"]),
        Step::Deref => json!(["deref"]),
    }
}
fn location(node: Ref, resolver: &Resolver<'_>) -> Value {
    let source = &resolver.sources[node.file];
    if resolver.sources.len() == 1 {
        json!(source.node(node.node).span.start)
    } else {
        json!([source.path, source.node(node.node).span.start])
    }
}
fn access(value: &Access, resolver: &Resolver<'_>) -> Value {
    json!([
        location(value.procedure, resolver),
        value.index,
        value.path.iter().map(step).collect::<Vec<_>>(),
        match value.aspect {
            Aspect::Borrowed => "borrowed",
            Aspect::NumericBorrowed => "numeric",
            Aspect::Location => "location",
        }
    ])
}
fn permission(value: &Permission, resolver: &Resolver<'_>) -> Value {
    match value {
        Permission::Constant(value) => json!(["constant", value]),
        Permission::Access(value) => json!(["access", access(value, resolver)]),
        Permission::Type(trait_, node) => {
            let span = resolver.sources[node.file].node(node.node).span;
            let mut value = json!([
                "type",
                match trait_ {
                    Trait::Big => "big",
                    Trait::NotBig => "not-big",
                    Trait::Pointer => "pointer",
                    Trait::Sequence => "sequence",
                },
                span.start,
                span.end
            ]);
            if resolver.sources.len() > 1 {
                value
                    .as_array_mut()
                    .unwrap()
                    .insert(2, json!(resolver.sources[node.file].path));
            }
            value
        }
        Permission::And(a, b) => json!(["and", permission(a, resolver), permission(b, resolver)]),
        Permission::Or(values) => json!([
            "or",
            values
                .iter()
                .map(|value| permission(value, resolver))
                .collect::<Vec<_>>()
        ]),
    }
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let entry = std::env::args().nth(1).ok_or("expected source file")?;
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("compiler root")?;
    let (parser, odin, odin_root) = frontend::bootstrap(
        root,
        &std::env::var("THOR_ODIN").unwrap_or_else(|_| "odin".into()),
    )?;
    let frontend = Frontend {
            odin_syntax: false,
        executable: parser,
        fingerprint: String::new(),
        odin,
        odin_root,
        cache_dir: None,
        normalized_cache: false,
        test: false,
        target: None,
        project_name: String::new(),
    };
    let sources = if std::env::args().any(|arg| arg == "--project") {
        let mut project = Project::new(Options {
            native_policy_probe: false,
            native_policy_only: false,
            odin_compatible: false,
            command: "check".into(),
            entry: entry.clone().into(),
            output: None,
            cache: None,
            odin: frontend.odin.to_string_lossy().into_owned(),
            flags: vec!["-debug".into()],
            args: vec![],
            no_cache: true,
            json: false,
            root: root.into(),
            maps: false,
            tracking: "off".into(),
            no_sanitize: true,
            overlays: Default::default(),
            editor_info: false,
            run_timeout: std::time::Duration::from_secs(5),
            comptime: Default::default(),
            fn_safety: Default::default(),
            clear_cache: false,
            cache_report: false,
            raw_diagnostics: false,
            vet: None,
            vet_apply: false,
            vet_ask: false,
            unverified: Default::default(),
        })?;
        project.inspect_sources()?;
        project.sources
    } else {
        vec![frontend.parse(Path::new(&entry), false)?]
    };
    let mut resolver = Resolver::new(&sources);
    resolver.prepare_contexts();
    let facts = Purity::analyze(&mut resolver);
    let mut model = Model::prepare(&mut resolver, &facts);
    let mut checks = BTreeMap::new();
    for (node, value) in &model.checks {
        checks.insert(
            if sources.len() == 1 {
                sources[node.file].node(node.node).span.start.to_string()
            } else {
                format!(
                    "{}:{}",
                    sources[node.file].path.display(),
                    sources[node.file].node(node.node).span.start
                )
            },
            permission(value, &resolver),
        );
    }
    let mut profiles = vec![];
    for (node, targets) in model.targets.clone() {
        let owner = sources[node.file]
            .ancestors(node.node)
            .find(|id| sources[node.file].node(*id).kind == "Procedure")
            .map(|id| Ref {
                file: node.file,
                node: id,
            });
        let Some(env) = owner.and_then(|owner| model.environments.get_mut(&owner)) else {
            continue;
        };
        let mut eval = Evaluator {
            resolver: &mut resolver,
            arena: &mut model.arena,
        };
        for target in targets {
            let Some(requirements) = model.requirements.get(&target) else {
                continue;
            };
            let bindings = eval.bindings(node, target, env);
            let mut fields = vec![];
            for item in requirements {
                let value = eval.resolve_access(&bindings, item);
                fields.push(json!([
                    access(item, eval.resolver),
                    permission(&value, eval.resolver)
                ]));
            }
            profiles.push(json!([
                location(node, eval.resolver),
                location(target, eval.resolver),
                fields
            ]));
        }
    }
    let mutates = model
        .mutates
        .iter()
        .map(|node| location(*node, &resolver))
        .collect::<Vec<_>>();
    let escapes = model
        .escapes
        .iter()
        .map(|(node, message)| json!([location(*node, &resolver), message]))
        .collect::<Vec<_>>();
    println!(
        "{}",
        json!({"converged":model.converged,"checks":checks,"profiles":profiles,"mutates":mutates,"escapes":escapes})
    );
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
