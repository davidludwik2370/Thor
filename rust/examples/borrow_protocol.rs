//! Internal real-parser differential harness for symbolic helper provenance.
use serde_json::{json, Value};
use std::{collections::BTreeMap, path::Path};
use thor_compiler::{
    borroweval::Evaluator,
    borrowprogram::Model,
    frontend::{self, Frontend},
    permissions::{Access, Aspect, Permission, Step, Trait},
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
fn access(value: &Access, resolver: &Resolver<'_>) -> Value {
    json!([
        resolver.sources[value.procedure.file]
            .node(value.procedure.node)
            .span
            .start,
        value.index,
        value.path.iter().map(step).collect::<Vec<_>>(),
        match value.aspect {
            Aspect::Borrowed => "borrowed",
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
            json!([
                "type",
                match trait_ {
                    Trait::Big => "big",
                    Trait::NotBig => "not-big",
                    Trait::Pointer => "pointer",
                    Trait::Sequence => "sequence",
                },
                span.start,
                span.end
            ])
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
    let (parser, odin) = frontend::bootstrap(
        root,
        &std::env::var("THOR_ODIN").unwrap_or_else(|_| "odin".into()),
    )?;
    let frontend = Frontend {
        executable: parser,
        fingerprint: String::new(),
        odin,
        cache_dir: None,
        test: false,
        target: None,
        project_name: String::new(),
    };
    let sources = vec![frontend.parse(Path::new(&entry), false)?];
    let mut resolver = Resolver::new(&sources);
    resolver.prepare_contexts();
    let facts = Purity::analyze(&mut resolver);
    let mut model = Model::prepare(&mut resolver, &facts);
    let mut checks = BTreeMap::new();
    for (node, value) in &model.checks {
        checks.insert(
            sources[node.file].node(node.node).span.start,
            permission(value, &resolver),
        );
    }
    let mut profiles = vec![];
    for (node, targets) in facts.targets {
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
                sources[node.file].node(node.node).span.start,
                sources[target.file].node(target.node).span.start,
                fields
            ]));
        }
    }
    let mutates = model
        .mutates
        .iter()
        .map(|node| sources[node.file].node(node.node).span.start)
        .collect::<Vec<_>>();
    let escapes = model
        .escapes
        .iter()
        .map(|(node, message)| json!([sources[node.file].node(node.node).span.start, message]))
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
