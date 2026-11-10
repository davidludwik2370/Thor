//! Differential harness for contents-cache records, independent of scheduling.
use serde_json::{json, Value};
use std::{
    io::{self, BufRead},
    path::Path,
};
use thor_compiler::{
    digest::sha256,
    evaluationcache::{canonical, EvaluationCache},
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
    let mut answers = vec![];
    for operation in request["operations"].as_array().into_iter().flatten() {
        let key = operation["key"].as_str();
        let namespace = operation["namespace"].as_str().unwrap_or("prepared-v1");
        let answer = match operation["op"].as_str().unwrap_or("") {
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
