//! Internal differential harness for canonical logical-number contents.
use serde_json::{json, Value};
use std::io::{self, BufRead};
use thor_compiler::{bigstatic, bigvalues, values};
fn run(request: Value) -> bigvalues::Result<Value> {
    let op = request["operation"].as_str().ok_or("Invalid operation.")?;
    match op {
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
