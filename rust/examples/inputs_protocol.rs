//! Compare dependency snapshots and invalidation with the reference implementation.
use serde_json::{json, Value};
use std::{
    fs,
    io::{self, BufRead},
    path::{Path, PathBuf},
};
use thor_compiler::{
    evaluationinputs::{self, Inputs},
    frontend::Frontend,
};
fn run(request: &Value) -> Value {
    let mut inputs = Inputs::new(request.get("data").cloned());
    let mut answers = vec![];
    for operation in request["operations"].as_array().into_iter().flatten() {
        let path = Path::new(operation["path"].as_str().unwrap_or("."));
        let answer = match operation["op"].as_str().unwrap_or("") {
            "file" => {
                inputs.file(
                    path,
                    operation["raw"].as_str().map(str::as_bytes),
                    operation["content"].as_bool().unwrap_or(true),
                );
                Value::Null
            }
            "binding" => {
                inputs.binding(path);
                Value::Null
            }
            "tree" => {
                inputs.tree(path);
                Value::Null
            }
            "directory" => {
                inputs.directory(path);
                Value::Null
            }
            "package" => {
                let paths = operation["paths"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(PathBuf::from)
                    .collect::<Vec<_>>();
                let suffixes = operation
                    .get("suffixes")
                    .and_then(Value::as_array)
                    .map(|values| {
                        values
                            .iter()
                            .filter_map(Value::as_str)
                            .map(str::to_string)
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_else(|| vec![".odin".into(), ".thor".into()]);
                inputs.package(path, &paths, &suffixes);
                Value::Null
            }
            "source" => {
                let frontend = Frontend {
                    executable: request["frontend"].as_str().unwrap().into(),
                    fingerprint: String::new(),
                    odin: PathBuf::from("odin"),
                    cache_dir: None,
                    test: false,
                    target: None,
                    project_name: "input".into(),
                };
                match frontend.parse_overlay(path, false, operation["raw"].as_str()) {
                    Ok(mut source) => {
                        source.collection_roots = request["collections"]
                            .as_object()
                            .map(|values| {
                                values
                                    .iter()
                                    .filter_map(|(name, value)| {
                                        value
                                            .as_str()
                                            .map(|path| (name.clone(), PathBuf::from(path)))
                                    })
                                    .collect()
                            })
                            .unwrap_or_default();
                        inputs.source(&source, operation["source_file"].as_bool().unwrap_or(true));
                        Value::Null
                    }
                    Err(error) => json!({"error":error.message}),
                }
            }
            "signature" => json!(evaluationinputs::signature(path).ok().flatten()),
            "resolve" => json!(evaluationinputs::resolve(path).ok()),
            "digest" => json!(evaluationinputs::file_digest(path).ok()),
            "valid" => json!(inputs.valid()),
            "read" => json!(fs::read_to_string(path).ok()),
            _ => json!({"error":"unknown operation"}),
        };
        answers.push(answer);
    }
    json!({"answers":answers,"data":inputs.data,"usable":inputs.usable,"valid":inputs.valid()})
}
fn main() {
    for line in io::stdin().lock().lines() {
        let answer = match line
            .ok()
            .and_then(|line| serde_json::from_str::<Value>(&line).ok())
        {
            Some(request) => run(&request),
            None => json!({"error":"invalid request"}),
        };
        println!("{answer}");
    }
}
