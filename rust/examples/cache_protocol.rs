//! Differential harness for contents-cache records, independent of scheduling.
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    io::{self, BufRead},
    path::{Path, PathBuf},
};
use thor_compiler::{
    digest::sha256,
    evaluationcache::{canonical, EvaluationCache, HelperCode},
    frontend::Frontend,
};
fn run(request: &Value) -> Value {
    if let Some(value) = request.get("canonical") {
        let data = canonical(value);
        return json!({"text":String::from_utf8(data.clone()).unwrap(),"sha256":sha256(&data)});
    }
    let Some(directory) = request["directory"].as_str() else {
        return json!({"error":"directory required"});
    };
    let mut cache = EvaluationCache::new(
        Path::new(directory),
        request["enabled"].as_bool().unwrap_or(true),
    );
    let mut sources = vec![];
    if let Some(paths) = request["sources"].as_array() {
        let frontend = Frontend {
            executable: request["frontend"].as_str().unwrap().into(),
            fingerprint: String::new(),
            odin: "odin".into(),
            cache_dir: None,
            test: false,
            target: None,
            project_name: "cache".into(),
        };
        for path in paths.iter().filter_map(Value::as_str) {
            match frontend.parse(Path::new(path), false) {
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
                    sources.push(source);
                }
                Err(error) => return json!({"error":error.message}),
            }
        }
    }
    let mut answers = vec![];
    for operation in request["operations"].as_array().into_iter().flatten() {
        let key = operation["key"].as_str();
        let namespace = operation["namespace"].as_str().unwrap_or("prepared-v1");
        let answer = match operation["op"].as_str().unwrap_or("") {
            "identity" => json!(cache.identity(
                Path::new(operation["odin"].as_str().unwrap()),
                Path::new(operation["root"].as_str().unwrap())
            )),
            "resources" => json!(EvaluationCache::resources(&sources)),
            "key" => {
                let generated = operation["generated"]
                    .as_object()
                    .map(|values| {
                        values
                            .iter()
                            .filter_map(|(path, value)| {
                                value
                                    .as_str()
                                    .map(|text| (PathBuf::from(path), text.as_bytes().to_vec()))
                            })
                            .collect::<BTreeMap<_, _>>()
                    })
                    .unwrap_or_default();
                let flags = operation["flags"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect::<Vec<_>>();
                json!(cache.key(
                    &sources,
                    HelperCode {
                        destination: Path::new(operation["destination"].as_str().unwrap()),
                        flags: &flags,
                        target: Path::new(operation["target"].as_str().unwrap()),
                        generated: &generated
                    },
                    Path::new(operation["odin"].as_str().unwrap()),
                    Path::new(operation["root"].as_str().unwrap()),
                ))
            }
            "put" => {
                cache.put(key, &operation["value"]);
                Value::Null
            }
            "get" => cache.get(key).unwrap_or(Value::Null),
            "write_record" => {
                cache.write_record(namespace, key, &operation["data"]);
                Value::Null
            }
            "read_record" => cache.read_record(namespace, key).unwrap_or(Value::Null),
            "remember" => {
                cache.remember(key, operation["target"].as_str());
                Value::Null
            }
            "prepared" => {
                let (value, target) = cache.prepared(key, operation["skip"].as_str());
                json!([value, target])
            }
            "clear" => {
                cache.clear();
                Value::Null
            }
            "parallel" => {
                let workers = operation["values"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|value| {
                        let value = value.clone();
                        let directory = Path::new(directory).to_path_buf();
                        let key = key.map(str::to_string);
                        std::thread::spawn(move || {
                            EvaluationCache::new(&directory, true).put(key.as_deref(), &value)
                        })
                    })
                    .collect::<Vec<_>>();
                let mut failed = false;
                for worker in workers {
                    if worker.join().is_err() {
                        failed = true;
                    }
                }
                json!({"failed":failed})
            }
            _ => json!({"error":"unknown operation"}),
        };
        answers.push(answer);
    }
    json!({"answers":answers,"stats":cache.stats,"prepared_hits":cache.prepared_hits})
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
