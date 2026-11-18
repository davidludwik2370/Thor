//! Internal differential harness for the Python permission algebra.
use serde_json::{json, Value};
use std::io::{self, Read};
use thor_compiler::{
    ast::NodeId,
    permissions::{Access, Aspect, Permission, Step, Trait, OWNED, READONLY},
    semantics::Ref,
};
fn integer(value: &Value) -> Result<usize, String> {
    value
        .as_u64()
        .and_then(|n| usize::try_from(n).ok())
        .ok_or_else(|| "expected index".into())
}
fn string(value: &Value) -> Result<&str, String> {
    value.as_str().ok_or_else(|| "expected string".into())
}
fn array(value: &Value) -> Result<&Vec<Value>, String> {
    value.as_array().ok_or_else(|| "expected array".into())
}
fn aspect(value: &Value) -> Result<Aspect, String> {
    match string(value)? {
        "borrowed" => Ok(Aspect::Borrowed),
        "numeric" => Ok(Aspect::NumericBorrowed),
        "location" => Ok(Aspect::Location),
        _ => Err("invalid aspect".into()),
    }
}
fn trait_(value: &Value) -> Result<Trait, String> {
    match string(value)? {
        "big" => Ok(Trait::Big),
        "not-big" => Ok(Trait::NotBig),
        "pointer" => Ok(Trait::Pointer),
        "sequence" => Ok(Trait::Sequence),
        _ => Err("invalid trait".into()),
    }
}
fn step(value: &Value) -> Result<Step, String> {
    match value[0].as_str() {
        Some("field") => Ok(Step::Field(string(&value[1])?.into())),
        Some("item") => Ok(Step::Item),
        Some("deref") => Ok(Step::Deref),
        _ => Err("invalid step".into()),
    }
}
fn parse(value: &Value, depth: usize) -> Result<Permission, String> {
    if depth > 128 {
        return Err("permission expression too deep".into());
    }
    if let Some(value) = value.as_bool() {
        return Ok(Permission::Constant(value));
    }
    let nested = |value: &Value| parse(value, depth + 1);
    if let Some(value) = value.get("access") {
        return Ok(Permission::Access(Access {
            procedure: Ref {
                file: integer(&value[0])?,
                node: NodeId(integer(&value[1])?),
            },
            index: integer(&value[2])?,
            path: array(&value[3])?
                .iter()
                .map(step)
                .collect::<Result<_, _>>()?,
            aspect: aspect(&value[4])?,
        }));
    }
    if let Some(value) = value.get("type") {
        return Ok(Permission::Type(
            trait_(&value[0])?,
            Ref {
                file: integer(&value[1])?,
                node: NodeId(integer(&value[2])?),
            },
        ));
    }
    if let Some(value) = value.get("join") {
        return Ok(nested(&value[0])?.join(&nested(&value[1])?));
    }
    if let Some(value) = value.get("meet") {
        return Ok(nested(&value[0])?.meet(&nested(&value[1])?));
    }
    if let Some(value) = value.get("project") {
        let aspect = if value[2].is_null() {
            None
        } else {
            Some(aspect(&value[2])?)
        };
        return Ok(nested(&value[0])?.project(step(&value[1])?, aspect));
    }
    if let Some(value) = value.get("substitute") {
        let substitutions = array(&value[1])?
            .iter()
            .map(|value| value.as_bool().ok_or_else(|| "expected bool".to_string()))
            .collect::<Result<Vec<_>, _>>()?;
        return Ok(nested(&value[0])?.substitute(&mut |access| {
            substitutions
                .get(access.index)
                .map(|value| if *value { READONLY } else { OWNED })
                .unwrap_or_else(|| Permission::Access(access.clone()))
        }));
    }
    Err("invalid permission expression".into())
}
fn encode(permission: &Permission) -> Value {
    match permission {
        Permission::Constant(value) => json!(["constant", value]),
        Permission::Access(access) => json!([
            "access",
            access.procedure.file,
            access.procedure.node.0,
            access.index,
            access
                .path
                .iter()
                .map(|step| match step {
                    Step::Field(name) => json!(["field", name]),
                    Step::Item => json!(["item"]),
                    Step::Deref => json!(["deref"]),
                })
                .collect::<Vec<_>>(),
            match access.aspect {
                Aspect::Borrowed => "borrowed",
                Aspect::NumericBorrowed => "numeric",
                Aspect::Location => "location",
            }
        ]),
        Permission::Type(trait_, node) => json!([
            "type",
            match trait_ {
                Trait::Big => "big",
                Trait::NotBig => "not-big",
                Trait::Pointer => "pointer",
                Trait::Sequence => "sequence",
            },
            node.file,
            node.node.0
        ]),
        Permission::And(a, b) => json!(["and", encode(a), encode(b)]),
        Permission::Or(values) => json!(["or", values.iter().map(encode).collect::<Vec<_>>()]),
    }
}
fn run() -> Result<(), String> {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .map_err(|e| e.to_string())?;
    let requests: Vec<Value> = serde_json::from_str(&input).map_err(|e| e.to_string())?;
    let result = requests
        .iter()
        .map(|value| parse(value, 0).map(|value| encode(&value)))
        .collect::<Result<Vec<_>, _>>()?;
    println!(
        "{}",
        serde_json::to_string(&result).map_err(|e| e.to_string())?
    );
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
