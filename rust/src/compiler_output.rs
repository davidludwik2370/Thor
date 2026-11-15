//! Keep compiler progress separate from structured source diagnostics.
use serde_json::{json, Value};

pub fn decode(stderr: &[u8], successful: bool, reporting: bool) -> Value {
    if stderr.is_empty() {
        return json!({"errors":[]});
    }
    if let Ok(packet) = serde_json::from_slice::<Value>(stderr) {
        if packet["errors"].is_array() {
            return packet;
        }
    }
    let mut errors = vec![];
    let mut information = String::new();
    let mut cursor = 0;
    let mut scan = 0;
    while scan < stderr.len() {
        if stderr[scan] != b'{' {
            scan += 1;
            continue;
        }
        let mut stream = serde_json::Deserializer::from_slice(&stderr[scan..]).into_iter::<Value>();
        if let Some(Ok(packet)) = stream.next() {
            if let Some(items) = packet["errors"].as_array() {
                information.push_str(&String::from_utf8_lossy(&stderr[cursor..scan]));
                errors.extend(items.iter().cloned());
                scan += stream.byte_offset();
                cursor = scan;
                continue;
            }
        }
        scan += 1;
    }
    if cursor > 0 || successful && reporting {
        information.push_str(&String::from_utf8_lossy(&stderr[cursor..]));
        if !successful && errors.is_empty() && !information.trim().is_empty() {
            return json!({"errors":[{"type":"error","stage":"toolchain","pos":null,"msgs":[information.trim()]}]});
        }
        return json!({"errors":errors,"information":information});
    }
    json!({"errors":[{"type":if successful {"warning"}else{"error"},"stage":"toolchain","pos":null,
        "msgs":[String::from_utf8_lossy(stderr).trim()]}]})
}

pub fn has_source_errors(packet: &Value) -> bool {
    packet["errors"].as_array().into_iter().flatten().any(|error| {
        error["type"] == "error" && error["pos"]["file"].is_string()
    })
}

/// Native file-private generic specializations can collide when Odin splits
/// generated helpers across LLVM modules. A single module avoids that linker bug.
pub(crate) fn generic_module_collision(packet: &Value) -> bool {
    !has_source_errors(packet) && packet["errors"].as_array().into_iter().flatten()
        .flat_map(|error| error["msgs"].as_array().into_iter().flatten())
        .filter_map(Value::as_str)
        .any(|message| message.lines().any(|line|
            line.contains("duplicate symbol") && line.contains("::") && line.contains(":proc(")))
}

pub fn reporting(flags: &[String]) -> bool {
    flags.iter().any(|flag| {
        flag.starts_with("-show-")
            || flag.starts_with("-export-")
            || matches!(flag.as_str(), "-build-diagnostics" | "-print-linker-flags")
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn source_errors_remain_distinct_from_unlocated_failures_and_warnings() {
        let source = json!({"type":"error","pos":{"file":"original.thor"},"msgs":["bad source"]});
        let warning = json!({"type":"warning","pos":{"file":"original.thor"}});
        let toolchain = json!({"type":"error","stage":"toolchain","pos":null});
        if !has_source_errors(&json!({"errors":[source, toolchain.clone()]}))
            || has_source_errors(&json!({"errors":[warning, toolchain]})) {
            panic!("source failures were confused with warnings or native failure details");
        }
    }
    #[test]
    fn mixed_progress_keeps_primary_diagnostics() {
        let error = json!({"type":"error","pos":{"file":"original.thor","offset":7},"msgs":["Undeclared name"]});
        let raw = format!(
            "Checking...\n{}\nTotal Time: 3 ms\n",
            json!({"errors":[error]})
        );
        let result = decode(raw.as_bytes(), false, true);
        assert_eq!(result["errors"], json!([error]));
        assert_eq!(result["information"], "Checking...\n\nTotal Time: 3 ms\n");
    }
    #[test]
    fn reports_are_information_but_plain_warnings_and_failures_stay_diagnostics() {
        let progress = decode(b"Total Time: 3 ms", true, true);
        let warning = decode(b"actual warning", true, false);
        let failure = decode(b"link failed", false, true);
        assert_eq!(progress["errors"], json!([]));
        assert_eq!(warning["errors"][0]["type"], "warning");
        assert_eq!(failure["errors"][0]["type"], "error");
    }
    #[test]
    fn braces_in_progress_and_multiple_packets_are_not_lost() {
        let first = json!({"errors":[{"type":"warning","msgs":["first"]}]});
        let second = json!({"errors":[{"type":"error","msgs":["second"]}]});
        let text = format!("progress {{not JSON}}\n{first}\n{second}\nDone\n");
        let result = decode(text.as_bytes(), false, true);
        assert_eq!(result["errors"].as_array().unwrap().len(), 2);
        assert!(result["information"]
            .as_str()
            .unwrap()
            .contains("{not JSON}"));
        assert!(result["information"].as_str().unwrap().ends_with("Done\n"));
    }
}
