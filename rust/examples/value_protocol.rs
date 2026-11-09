//! Differential-test harness for the internal native value protocol.
use serde_json::{json, Value};
use std::io::{self, BufRead};
use thor_compiler::values;
fn run(request: Value) -> values::Result<Value> {
    let packet = serde_json::to_vec(&request["packet"]).unwrap();
    let value = values::decode(&packet)?;
    let expression = values::expression(&value, "T", "meta")?;
    let materialized = values::materialize(&value, "T", "meta", "pure", "value_")?;
    Ok(
        json!({"expression":expression,"materialize":materialized,"owned":values::has_owned(&value,false),"slices":values::has_owned(&value,true),"dynamic":values::has_dynamic(&value)}),
    )
}
fn main() {
    for line in io::stdin().lock().lines() {
        let answer = match line.ok().and_then(|line| serde_json::from_str(&line).ok()) {
            Some(request) => match run(request) {
                Ok(result) => json!({"ok":result}),
                Err(error) => json!({"error":error.to_string()}),
            },
            None => json!({"error":"Invalid request"}),
        };
        println!("{answer}");
    }
}
