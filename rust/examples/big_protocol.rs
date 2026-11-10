//! Internal differential harness for canonical logical-number contents.
use serde_json::{json, Value};
use std::io::{self, BufRead};
use thor_compiler::{bigstatic, bigvalues, values};
fn resolve(request: &Value) -> bigvalues::Result<Value> {
    use std::{collections::BTreeMap, path::Path};
    use thor_compiler::{
        ast::NodeId,
        project::{Options, Project},
        semantics::{Ref, Resolver, Type},
    };
    fn shape(typ: &Type) -> Value {
        match typ {
            Type::BigInteger => json!({"kind":"big_integer"}),
            Type::BigRational => json!({"kind":"big_rational"}),
            Type::Scalar { name, untyped } => {
                json!({"kind":"scalar","name":name,"untyped":untyped})
            }
            Type::String { name, untyped } => {
                json!({"kind":"string","name":name,"untyped":untyped})
            }
            Type::Array { elem, length } => {
                json!({"kind":"array","length":length,"elem":shape(elem)})
            }
            Type::Slice(elem) | Type::Dynamic(elem) | Type::Pointer(elem) => {
                json!({"kind":match typ{Type::Slice(_)=>"slice",Type::Dynamic(_)=>"dynamic",_=>"pointer"},"elem":shape(elem)})
            }
            Type::Struct(_) | Type::Record(..) => json!({"kind":"struct"}),
            Type::Generic => json!({"kind":"generic"}),
            _ => json!({"kind":"unknown"}),
        }
    }
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let mut project = Project::new(Options {
        command: "check".into(),
        entry: request["file"]
            .as_str()
            .ok_or("Source path omitted.")?
            .into(),
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
    })
    .map_err(|error| error.to_string())?;
    project
        .inspect_sources()
        .map_err(|error| error.to_string())?;
    let mut resolver = Resolver::new(&project.sources);
    let mut out = BTreeMap::new();
    for (file, source) in resolver.sources.iter().enumerate() {
        for (index, node) in source.nodes.iter().enumerate() {
            let id = NodeId(index);
            if node.kind != "Call"
                || source
                    .role(id, "callee")
                    .is_none_or(|callee| source.text(callee) != "query")
            {
                continue;
            }
            let args = source.group(id, "args");
            if args.len() != 2 {
                continue;
            }
            let label = thor_compiler::lower::unquote(source.text(args[0]))
                .map_err(|error| error.to_string())?;
            let at = Ref {
                file,
                node: args[1],
            };
            let typ = resolver.specialized_type(at, &Default::default());
            out.insert(
                label,
                json!({"shape":shape(&typ),"type_operand":resolver.denotes_type(at)}),
            );
        }
    }
    Ok(json!(out))
}
fn run(request: Value) -> bigvalues::Result<Value> {
    let op = request["operation"].as_str().ok_or("Invalid operation.")?;
    match op {
        "resolve" => resolve(&request),
        "literal" => {
            let number =
                bigvalues::literal(request["text"].as_str().ok_or("Invalid numeric literal.")?)?;
            bigvalues::packet(request["kind"].as_str().unwrap_or("big_rational"), &number)
        }
        "number" => {
            let number = bigvalues::number(&request["value"])?;
            bigvalues::packet(request["value"]["kind"].as_str().unwrap_or(""), &number)
        }
        "without_backing" => {
            let value = values::decode(&serde_json::to_vec(&request["packet"]).unwrap())
                .map_err(|e| e.to_string())?;
            values::expression(&value, "T", "meta")
                .map(Value::String)
                .map_err(|e| e.to_string())
        }
        "reconstruct" => {
            let value = values::decode(&serde_json::to_vec(&request["packet"]).unwrap())
                .map_err(|e| e.to_string())?;
            values::validate(&value).map_err(|e| e.to_string())?;
            let mut backing = bigstatic::Backing::new("data_", "big");
            let mut emit =
                |value: &Value, _typ: &str| backing.view(value).map_err(values::ProtocolError);
            let expression = values::expression_with_big(&value, "T", "meta", &mut emit)
                .map_err(|e| e.to_string())?;
            let materialize =
                values::materialize_with_big(&value, "T", "meta", "pure", "value_", &mut emit)
                    .map_err(|e| e.to_string())?;
            Ok(
                json!({"expression":expression,"materialize":materialize,"declarations":backing.declarations,"owned":values::has_owned(&value,false),"slices":values::has_owned(&value,true),"dynamic":values::has_dynamic(&value)}),
            )
        }
        "backing" => {
            let mut backing = bigstatic::Backing::new("data_", "big");
            let view = backing.view(&request["value"])?;
            Ok(json!({"expression":view,"declarations":backing.declarations}))
        }
        "contains" => Ok(Value::Bool(bigvalues::contains(&request["value"]))),
        _ => Err("Invalid operation.".into()),
    }
}
fn main() {
    for line in io::stdin().lock().lines() {
        let answer = match line.ok().and_then(|line| serde_json::from_str(&line).ok()) {
            Some(request) => match run(request) {
                Ok(value) => json!({"ok":value}),
                Err(error) => json!({"error":error}),
            },
            None => json!({"error":"Invalid request"}),
        };
        println!("{answer}");
    }
}
