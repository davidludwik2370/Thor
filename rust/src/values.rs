//! Contents-only compile-time value reconstruction. No process addresses enter
//! a value packet or its emitted Odin expression.
use serde_json::Value;
use std::{collections::HashSet, fmt};

const MAX_PACKET: usize = 16 * 1024 * 1024;
const MAX_DEPTH: usize = 32;
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtocolError(pub String);
impl fmt::Display for ProtocolError {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(out)
    }
}
impl std::error::Error for ProtocolError {}
pub type Result<T> = std::result::Result<T, ProtocolError>;
fn error(message: &str) -> ProtocolError {
    ProtocolError(message.into())
}

pub fn decode(data: &[u8]) -> Result<Value> {
    if data.len() > MAX_PACKET {
        return Err(error("Compile-time result exceeds 16 MiB."));
    }
    let mut packet: Value =
        serde_json::from_slice(data).map_err(|_| error("Invalid compile-time result protocol."))?;
    if packet.get("thor_value") != Some(&Value::from(1)) || packet.get("value").is_none() {
        return Err(error("Unsupported compile-time result protocol version."));
    }
    Ok(packet.as_object_mut().unwrap().remove("value").unwrap())
}
fn kind(value: &Value) -> Result<&str> {
    let object = value
        .as_object()
        .ok_or_else(|| error("Invalid nested compile-time value."))?;
    Ok(object.get("kind").and_then(Value::as_str).unwrap_or(""))
}

pub(crate) fn bytes(value: &Value) -> Result<Vec<u8>> {
    let text = value
        .get("bytes")
        .and_then(Value::as_str)
        .ok_or_else(|| error("Invalid result bytes."))?;
    // Native packets use contiguous hex; Python bytes.fromhex also accepts ASCII
    // whitespace, so accept it without accepting non-ASCII digit lookalikes.
    let digits = text.bytes().filter(|b| b.is_ascii_whitespace()).count();
    let mut output = Vec::with_capacity((text.len() - digits) / 2);
    let mut high = None;
    for byte in text.bytes() {
        if byte.is_ascii_whitespace() {
            if high.is_some() {
                return Err(error("Invalid result bytes."));
            }
            continue;
        }
        let digit = match byte {
            b'0'..=b'9' => byte - b'0',
            b'a'..=b'f' => byte - b'a' + 10,
            b'A'..=b'F' => byte - b'A' + 10,
            _ => return Err(error("Invalid result bytes.")),
        };
        if let Some(first) = high.take() {
            output.push(first * 16 + digit);
        } else {
            high = Some(digit);
        }
    }
    if high.is_some() {
        return Err(error("Invalid result bytes."));
    }
    Ok(output)
}
fn identifier(name: &str) -> bool {
    let mut chars = name.chars();
    chars
        .next()
        .is_some_and(|c| c == '_' || unicode_ident::is_xid_start(c))
        && chars.all(unicode_ident::is_xid_continue)
}
fn list<'a>(value: &'a Value, name: &str, message: &str) -> Result<&'a [Value]> {
    value
        .get(name)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .ok_or_else(|| error(message))
}
fn integer(value: &Value) -> Result<String> {
    let text = value
        .get("value")
        .and_then(Value::as_str)
        .ok_or_else(|| error("Invalid integer result."))?;
    let digits = text.strip_prefix('-').unwrap_or(text);
    if digits.is_empty() || digits.len() > 40 || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(error("Invalid integer result."));
    }
    if text.starts_with('-') {
        text.parse::<i128>()
            .map(|n| n.to_string())
            .map_err(|_| error("Integer result is out of range."))
    } else {
        text.parse::<u128>()
            .map(|n| n.to_string())
            .map_err(|_| error("Integer result is out of range."))
    }
}
fn numeric(value: &Value, typ: &str, float: bool, builtin: &str) -> Result<String> {
    let raw = bytes(value)?;
    if ![1, 2, 4, 8, 16].contains(&raw.len()) {
        return Err(error("Unsupported numeric result width."));
    }
    if float && ![2, 4, 8].contains(&raw.len()) {
        return Err(error("Unsupported float result width."));
    }
    let default = if cfg!(target_endian = "little") {
        "little"
    } else {
        "big"
    };
    let order = value
        .get("byte_order")
        .map(|v| v.as_str().ok_or_else(|| error("Invalid byte order.")))
        .transpose()?
        .unwrap_or(default);
    if !["little", "big"].contains(&order) {
        return Err(error("Invalid byte order."));
    }
    let mut buffer = [0u8; 16];
    let number = if order == "little" {
        buffer[..raw.len()].copy_from_slice(&raw);
        u128::from_le_bytes(buffer)
    } else {
        buffer[16 - raw.len()..].copy_from_slice(&raw);
        u128::from_be_bytes(buffer)
    };
    if float {
        Ok(format!("{typ}(0h{number:0width$x})", width = 2 * raw.len()))
    } else {
        Ok(format!(
            "(transmute({typ}){builtin}u{}(0x{number:x}))",
            8 * raw.len()
        ))
    }
}

pub fn expression(value: &Value, typ: &str, meta: &str) -> Result<String> {
    expression_with_big(value, typ, meta, &mut |_, _| {
        Err(error(
            "Big-number reconstruction requires static numeric backing.",
        ))
    })
}
pub fn validate(value: &Value) -> Result<()> {
    expression_with_big(value, "T", "meta", &mut |_, _| Ok("0".into())).map(|_| ())
}
pub fn expression_with_big(
    value: &Value,
    typ: &str,
    meta: &str,
    big: &mut NumericView<'_>,
) -> Result<String> {
    expression_in(value, typ, meta, "", big)
}
pub(crate) fn expression_in(
    value: &Value,
    typ: &str,
    meta: &str,
    builtin: &str,
    big: &mut NumericView<'_>,
) -> Result<String> {
    expression_at(value, typ, meta, 0, builtin, big)
}
fn expression_at(
    value: &Value,
    typ: &str,
    meta: &str,
    depth: usize,
    builtin: &str,
    big: &mut NumericView<'_>,
) -> Result<String> {
    if depth > MAX_DEPTH {
        return Err(error("Invalid nested compile-time value."));
    }
    let variant = kind(value)?;
    let mut child = |v: &Value, t: &str| expression_at(v, t, meta, depth + 1, builtin, big);
    match variant {
        "specialized" => {
            for name in list(value, "types", "Invalid specialization metadata.")? {
                if kind(name)? != "string" {
                    return Err(error("Invalid specialization metadata."));
                }
                child(name, &format!("{builtin}string"))?;
            }
            child(&value["value"], typ)
        }
        "big_integer" | "big_rational" => {
            crate::bigvalues::number(value).map_err(ProtocolError)?;
            big(value, typ)
        }
        "bool" => value
            .get("value")
            .and_then(Value::as_bool)
            .map(|b| format!("{typ}({b})"))
            .ok_or_else(|| error("Invalid boolean result.")),
        "integer" => Ok(format!("{typ}({})", integer(value)?)),
        "string" => {
            let raw = bytes(value)?;
            let mut text = format!("{typ}(\"");
            const DIGITS: &[u8; 16] = b"0123456789abcdef";
            text.reserve(raw.len() * 4);
            for byte in raw {
                text.push_str("\\x");
                text.push(DIGITS[(byte >> 4) as usize] as char);
                text.push(DIGITS[(byte & 15) as usize] as char);
            }
            text.push_str("\")");
            Ok(text)
        }
        "float" | "bits" => numeric(value, typ, variant == "float", builtin),
        "complex" | "quaternion" => {
            let parts = list(value, "values", "Invalid complex/quaternion result.")?;
            if parts.len() != if variant == "complex" { 2 } else { 4 } {
                return Err(error("Invalid complex/quaternion result."));
            }
            let mut components = vec![];
            for (index, part) in parts.iter().enumerate() {
                let width = 8 * bytes(part)?.len();
                let text = child(part, &format!("{builtin}f{width}"))?;
                components.push(if variant == "quaternion" {
                    format!("{}={text}", ["real", "imag", "jmag", "kmag"][index])
                } else {
                    text
                });
            }
            Ok(format!("{typ}({builtin}{variant}({}))", components.join(",")))
        }
        "sequence" => {
            let items = list(value, "values", "Invalid sequence result.")?;
            let storage = value
                .get("storage")
                .map(|v| v.as_str().ok_or_else(|| error("Invalid sequence storage.")))
                .transpose()?
                .unwrap_or("array");
            if !["array", "slice", "dynamic"].contains(&storage) {
                return Err(error("Invalid sequence storage."));
            }
            let nil = value
                .get("nil")
                .and_then(Value::as_bool)
                .ok_or_else(|| error("Invalid sequence result."))?;
            let zero = value.get("zero_sized") == Some(&Value::Bool(true));
            if storage == "dynamic" {
                let capacity = value
                    .get("capacity")
                    .and_then(Value::as_u64)
                    .filter(|n| *n >= items.len() as u64 && *n <= isize::MAX as u64)
                    .ok_or_else(|| error("Invalid dynamic-array capacity."))?;
                if nil && capacity > 0 && !zero {
                    return Err(error(
                        "A nil dynamic array cannot have nonzero-sized capacity.",
                    ));
                }
            }
            if nil && items.is_empty() {
                return Ok(format!("{typ}(nil)"));
            }
            if nil && !zero {
                return Err(error("A nil sequence cannot have nonzero-sized elements."));
            }
            let element = if value["soa"] == true {format!("{builtin}type_of((cast(^{typ})nil)^[0])")} else {format!("{meta}.type_elem_type({typ})")};
            let texts = items
                .iter()
                .map(|v| child(v, &element))
                .collect::<Result<Vec<_>>>()?;
            Ok(format!("{typ}{{{}}}", texts.join(",")))
        }
        "bitset" => {
            let texts = list(value, "values", "Invalid bit-set result.")?
                .iter()
                .map(|v| child(v, &format!("{meta}.type_bit_set_elem_type({typ})")))
                .collect::<Result<Vec<_>>>()?;
            Ok(format!("{typ}{{{}}}", texts.join(",")))
        }
        "record" | "enum_array" => {
            let mut seen = HashSet::new();
            let mut parts = vec![];
            for pair in list(value, "fields", "Invalid record result.")? {
                let pair = pair
                    .as_array()
                    .filter(|v| v.len() == 2)
                    .ok_or_else(|| error("Invalid record field."))?;
                let name = pair[0]
                    .as_str()
                    .filter(|v| identifier(v) && seen.insert(*v))
                    .ok_or_else(|| error("Invalid record field."))?;
                let field = if variant == "record" {
                    format!("{builtin}type_of(({typ}{{}}).{name})")
                } else {
                    format!("{meta}.type_elem_type({typ})")
                };
                parts.push(format!(
                    "{}{name}={}",
                    if variant == "record" { "" } else { "." },
                    child(&pair[1], &field)?
                ));
            }
            Ok(format!(
                "{}{typ}{{{}}}",
                if variant == "enum_array" {
                    "#partial "
                } else {
                    ""
                },
                parts.join(",")
            ))
        }
        "union" => {
            let index = value
                .get("variant")
                .and_then(Value::as_i64)
                .filter(|i| *i >= -1)
                .ok_or_else(|| error("Invalid union variant."))?;
            if index == -1 {
                return Ok(format!("{typ}{{}}"));
            }
            Ok(format!(
                "{typ}({})",
                child(
                    &value["value"],
                    &format!("{meta}.type_variant_type_of({typ},{index})")
                )?
            ))
        }
        _ => Err(error("Unsupported compile-time value kind.")),
    }
}

pub fn has_owned(value: &Value, slices_only: bool) -> bool {
    match value.get("kind").and_then(Value::as_str).unwrap_or("") {
        "specialized" => has_owned(&value["value"], slices_only),
        "string" | "big_integer" | "big_rational" => !slices_only,
        "sequence" => {
            value.get("nil") == Some(&Value::Bool(true))
                || matches!(
                    value.get("storage").and_then(Value::as_str),
                    Some("slice" | "dynamic")
                )
                || value["values"]
                    .as_array()
                    .is_some_and(|v| v.iter().any(|v| has_owned(v, slices_only)))
        }
        "record" | "enum_array" => value["fields"].as_array().is_some_and(|fields| {
            fields.iter().any(|p| {
                p.as_array()
                    .and_then(|p| p.get(1))
                    .is_some_and(|v| has_owned(v, slices_only))
            })
        }),
        "union" => {
            value["variant"].as_i64().is_some_and(|i| i >= 0)
                && has_owned(&value["value"], slices_only)
        }
        _ => false,
    }
}
pub fn has_dynamic(value: &Value) -> bool {
    match value.get("kind").and_then(Value::as_str).unwrap_or("") {
        "specialized" => has_dynamic(&value["value"]),
        "sequence" => {
            value.get("storage").and_then(Value::as_str) == Some("dynamic")
                || value["values"]
                    .as_array()
                    .is_some_and(|v| v.iter().any(has_dynamic))
        }
        "record" | "enum_array" => value["fields"].as_array().is_some_and(|fields| {
            fields
                .iter()
                .any(|p| p.as_array().and_then(|p| p.get(1)).is_some_and(has_dynamic))
        }),
        "union" => {
            value["variant"].as_i64().is_some_and(|i| i >= 0) && has_dynamic(&value["value"])
        }
        _ => false,
    }
}

pub type NumericView<'a> = dyn FnMut(&Value, &str) -> Result<String> + 'a;

pub fn materialize(
    value: &Value,
    typ: &str,
    meta: &str,
    module: &str,
    prefix: &str,
) -> Result<String> {
    materialize_with_big(value, typ, meta, module, prefix, &mut |_, _| {
        Err(error(
            "Big-number reconstruction requires static numeric backing.",
        ))
    })
}
pub fn materialize_with_big(
    value: &Value,
    typ: &str,
    meta: &str,
    module: &str,
    prefix: &str,
    big: &mut NumericView<'_>,
) -> Result<String> {
    materialize_in(value, typ, meta, module, prefix, "", big)
}
pub(crate) fn materialize_in(
    value: &Value,
    typ: &str,
    meta: &str,
    module: &str,
    prefix: &str,
    builtin: &str,
    big: &mut NumericView<'_>,
) -> Result<String> {
    expression_in(value, typ, meta, builtin, big)?;
    let mut materializer = Materializer {
        meta,
        module,
        prefix,
        builtin,
        big,
        statements: vec![],
        serial: 0,
    };
    let result = materializer.build(value, typ)?;
    materializer.statements.push(format!("return {result}"));
    Ok(materializer.statements.join("\n"))
}
struct Materializer<'a, 'b> {
    meta: &'a str,
    module: &'a str,
    prefix: &'a str,
    builtin: &'a str,
    big: &'a mut NumericView<'b>,
    statements: Vec<String>,
    serial: usize,
}
impl Materializer<'_, '_> {
    fn build(&mut self, value: &Value, typ: &str) -> Result<String> {
        if !has_owned(value, false) {
            return expression_in(value, typ, self.meta, self.builtin, self.big);
        }
        let module = self.module;
        let builtin = self.builtin;
        match kind(value)? {
            "specialized" => self.build(&value["value"], typ),
            "string" | "big_integer" | "big_rational" => Ok(format!(
                "{module}.clone({})",
                expression_in(value, typ, self.meta, self.builtin, self.big)?
            )),
            "sequence" => {
                let storage = value["storage"].as_str().unwrap_or("array");
                let soa = value["soa"] == true;
                let element = if soa {format!("{builtin}type_of((cast(^{typ})nil)^[0])")} else {format!("{}.type_elem_type({typ})", self.meta)};
                if storage == "dynamic" || storage == "slice" || value["nil"] == true {
                    self.serial += 1;
                    let name = format!("{}{}", self.prefix, self.serial);
                    let length = value["values"].as_array().unwrap().len();
                    let non_nil = value["nil"] != true;
                    let allocate = if soa && storage == "dynamic" {
                        format!("{builtin}make({typ},{length},{})",value["capacity"])
                    } else if soa {format!("{builtin}make({typ},{length})")} else if storage == "dynamic" {
                        format!(
                            "{module}.allocate_dynamic({typ},{length},{},{non_nil})",
                            value["capacity"]
                        )
                    } else {
                        format!("{module}.allocate_slice({typ},{length},{non_nil})")
                    };
                    self.statements.push(format!("{name}:={allocate}"));
                    for (index, item) in value["values"].as_array().unwrap().iter().enumerate() {
                        let text = self.build(item, &element)?;
                        self.statements.push(format!("{name}[{index}]={text}"));
                    }
                    return Ok(name);
                }
                let texts = value["values"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|item| self.build(item, &element))
                    .collect::<Result<Vec<_>>>()?;
                Ok(format!("{typ}{{{}}}", texts.join(",")))
            }
            "record" | "enum_array" => {
                let mut parts = vec![];
                let enumeration = kind(value)? == "enum_array";
                for pair in value["fields"].as_array().unwrap() {
                    let name = pair[0].as_str().unwrap();
                    let field = if enumeration {
                        format!("{}.type_elem_type({typ})", self.meta)
                    } else {
                        format!("{builtin}type_of(({typ}{{}}).{name})")
                    };
                    let text = self.build(&pair[1], &field)?;
                    parts.push(format!(
                        "{}{name}={text}",
                        if enumeration { "." } else { "" }
                    ));
                }
                Ok(format!(
                    "{}{typ}{{{}}}",
                    if enumeration { "#partial " } else { "" },
                    parts.join(",")
                ))
            }
            "union" => Ok(format!(
                "{typ}({})",
                self.build(
                    &value["value"],
                    &format!(
                        "{}.type_variant_type_of({typ},{})",
                        self.meta, value["variant"]
                    )
                )?
            )),
            _ => expression_in(value, typ, self.meta, self.builtin, self.big),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn packet_limits_and_versions() {
        let decoded =
            decode(br#"{"thor_value":1,"value":{"kind":"integer","value":"3"}}"#).unwrap();
        let expected = json!({"kind":"integer","value":"3"});
        assert_eq!(decoded, expected);
        for data in [
            b"not JSON".as_slice(),
            b"{}",
            br#"{"thor_value":2,"value":0}"#,
            br#"{"thor_value":true,"value":0}"#,
            b"\xff",
        ] {
            let result = decode(data);
            assert!(result.is_err());
        }
        let oversized = vec![b' '; MAX_PACKET + 1];
        let result = decode(&oversized);
        let message = result.unwrap_err().0;
        assert_eq!(message, "Compile-time result exceeds 16 MiB.");
    }
    #[test]
    fn integers_preserve_all_supported_bits() {
        for text in [
            i128::MIN.to_string(),
            u128::MAX.to_string(),
            "0003".into(),
            "-000".into(),
        ] {
            let value = json!({"kind":"integer","value":text});
            let code = expression(&value, "T", "m").unwrap();
            assert!(code.starts_with("T("));
        }
        for text in [
            "340282366920938463463374607431768211456",
            "-170141183460469231731687303715884105729",
            "1;panic(\"bad\")",
        ] {
            let value = json!({"kind":"integer","value":text});
            let result = expression(&value, "T", "m");
            assert!(result.is_err());
        }
    }
    #[test]
    fn strings_are_byte_exact_and_cannot_inject_source() {
        let value = json!({"kind":"string","bytes":"00ff225c0a"});
        let code = expression(&value, "T", "m").unwrap();
        assert_eq!(code, "T(\"\\x00\\xff\\x22\\x5c\\x0a\")");
    }
    #[test]
    fn float_bits_retain_signed_zero_and_nan_payloads() {
        for (bytes, order, expected) in [
            ("0000000000000080", "little", "T(0h8000000000000000)"),
            ("7ff8000000000001", "big", "T(0h7ff8000000000001)"),
        ] {
            let value = json!({"kind":"float","bytes":bytes,"byte_order":order});
            let code = expression(&value, "T", "m").unwrap();
            assert_eq!(code, expected);
        }
    }
    #[test]
    fn record_fields_are_unique_identifiers() {
        for fields in [
            json!([["x",{"kind":"bool","value":true}],["x",{"kind":"bool","value":false}]]),
            json!([["x;bad()",{"kind":"bool","value":true}]]),
        ] {
            let value = json!({"kind":"record","fields":fields});
            let result = expression(&value, "T", "m");
            assert!(result.is_err());
        }
        let value = json!({"kind":"record","fields":[["e\u{301}",{"kind":"bool","value":true}]]});
        let result = expression(&value, "T", "m");
        assert!(result.is_ok());
    }
    #[test]
    fn nested_values_and_dynamic_capacity_are_bounded() {
        let mut value = json!({"kind":"integer","value":"1"});
        for _ in 0..33 {
            value = json!({"kind":"sequence","storage":"array","nil":false,"values":[value]});
        }
        let result = expression(&value, "T", "m");
        assert!(result.is_err());
        for (nil, capacity) in [(false, 0), (true, 2)] {
            let value = json!({"kind":"sequence","storage":"dynamic","nil":nil,"capacity":capacity,"values":[{"kind":"integer","value":"1"}]});
            let result = expression(&value, "T", "m");
            assert!(result.is_err());
        }
    }
    #[test]
    fn reconstruction_allocates_only_returned_storage() {
        let value = json!({"kind":"sequence","storage":"dynamic","nil":false,"capacity":4,"values":[{"kind":"string","bytes":"78"}]});
        let code = materialize(&value, "T", "m", "p", "value_").unwrap();
        assert_eq!(code,"value_1:=p.allocate_dynamic(T,1,4,true)\nvalue_1[0]=p.clone(m.type_elem_type(T)(\"\\x78\"))\nreturn value_1");
        let owned = has_owned(&value, false);
        let slices = has_owned(&value, true);
        let dynamic = has_dynamic(&value);
        assert!(owned && slices && dynamic);
    }
}
