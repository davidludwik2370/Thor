//! Optional, atomic contents-only storage. Dependency/key policy is layered on
//! top of this API; no cache I/O error changes successful compilation.
use crate::{digest::sha256, values};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

pub const MAX_PACKET: usize = 16 * 1024 * 1024;
const SCHEMA: u64 = 1;
static SERIAL: AtomicU64 = AtomicU64::new(0);
#[path = "evaluationcachekey.rs"]
mod key;
pub use key::HelperCode;

/// Python's sorted, ASCII JSON representation, including its float notation.
pub fn canonical(value: &Value) -> Vec<u8> {
    fn string(text: &str, out: &mut String) {
        for character in serde_json::to_string(text).unwrap().chars() {
            if character.is_ascii() {
                out.push(character);
            } else {
                for unit in character.encode_utf16(&mut [0; 2]) {
                    use std::fmt::Write;
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
        }
    }
    fn emit(value: &Value, out: &mut String) {
        match value {
            Value::Null => out.push_str("null"),
            Value::Bool(value) => out.push_str(if *value { "true" } else { "false" }),
            Value::String(value) => string(value, out),
            Value::Number(value) => out.push_str(&if value.is_f64() {
                float(&value.to_string())
            } else {
                value.to_string()
            }),
            Value::Array(values) => {
                out.push('[');
                for (index, value) in values.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    emit(value, out);
                }
                out.push(']');
            }
            Value::Object(values) => {
                out.push('{');
                let mut sorted: Vec<_> = values.iter().collect();
                sorted.sort_unstable_by(|a, b| a.0.cmp(b.0));
                for (index, (key, value)) in sorted.into_iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    string(key, out);
                    out.push(':');
                    emit(value, out);
                }
                out.push('}');
            }
        }
    }
    let mut out = String::new();
    emit(value, &mut out);
    out.into_bytes()
}
fn float(raw: &str) -> String {
    let (sign, raw) = raw
        .strip_prefix('-')
        .map_or(("", raw), |value| ("-", value));
    let (fraction, exponent) = raw
        .split_once(['e', 'E'])
        .map_or((raw, 0), |(fraction, exponent)| {
            (fraction, exponent.parse::<i32>().unwrap())
        });
    let point = fraction.find('.').unwrap_or(fraction.len()) as i32;
    let mut digits = fraction.replace('.', "");
    if digits.bytes().all(|byte| byte == b'0') {
        return format!("{sign}0.0");
    }
    let leading = digits.bytes().take_while(|byte| *byte == b'0').count();
    digits.drain(..leading);
    while digits.ends_with('0') {
        digits.pop();
    }
    let power = point + exponent - leading as i32 - 1;
    if !(-4..16).contains(&power) {
        let tail = if digits.len() > 1 {
            format!(".{}", &digits[1..])
        } else {
            String::new()
        };
        return format!(
            "{sign}{}{tail}e{}{:02}",
            &digits[..1],
            if power >= 0 { "+" } else { "-" },
            power.unsigned_abs()
        );
    }
    let point = power + 1;
    if point <= 0 {
        return format!("{sign}0.{}{}", "0".repeat((-point) as usize), digits);
    }
    let point = point as usize;
    if point >= digits.len() {
        return format!("{sign}{digits}{}.0", "0".repeat(point - digits.len()));
    }
    format!("{sign}{}.{}", &digits[..point], &digits[point..])
}
#[derive(Default, Debug, Serialize, PartialEq, Eq)]
pub struct Stats {
    pub hits: u64,
    pub misses: u64,
    pub writes: u64,
    pub corrupt: u64,
    pub bypasses: u64,
}
pub struct EvaluationCache {
    pub directory: PathBuf,
    pub enabled: bool,
    pub stats: Stats,
    pub prepared_hits: u64,
    identities: std::collections::BTreeMap<PathBuf, String>,
    source_key: Option<key::SourceKey>,
}
fn key_valid(key: &str) -> bool {
    key.len() == 64
        && key
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn namespace_valid(namespace: &str) -> bool {
    !namespace.is_empty()
        && namespace
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
}
fn read(path: &Path, limit: usize) -> std::io::Result<Value> {
    let file = fs::File::open(path)?;
    if file.metadata()?.len() > limit as u64 {
        return Err(std::io::Error::other("oversize cache record"));
    }
    let mut data = vec![];
    file.take(limit as u64 + 1).read_to_end(&mut data)?;
    if data.len() > limit {
        return Err(std::io::Error::other("oversize cache record"));
    }
    serde_json::from_slice(&data).map_err(std::io::Error::other)
}
fn publish(path: &Path, data: &[u8], sync: bool) -> std::io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| std::io::Error::other("cache directory missing"))?;
    fs::create_dir_all(parent)?;
    let serial = SERIAL.fetch_add(1, Ordering::Relaxed);
    let time = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temporary = parent.join(format!(".entry-{}-{time}-{serial}", std::process::id()));
    let result = (|| {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(data)?;
        if sync {
            file.sync_all()?;
        }
        drop(file);
        fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}
impl EvaluationCache {
    pub fn new(directory: &Path, enabled: bool) -> Self {
        let directory = crate::project::absolute(directory)
            .unwrap_or_else(|_| directory.to_path_buf())
            .join(format!("eval-v{SCHEMA}"));
        Self {
            directory,
            enabled,
            stats: Stats::default(),
            prepared_hits: 0,
            identities: std::collections::BTreeMap::new(),
            source_key: None,
        }
    }
    pub fn clear(&self) {
        for directory in std::iter::once(self.directory.clone()).chain(
            ["prepared-v1", "identity-v1", "scalar-v1", "request-v1"]
                .map(|name| self.directory.parent().unwrap().join(name)),
        ) {
            if let Ok(entries) = fs::read_dir(directory) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().is_some_and(|ext| ext == "json")
                        && path
                            .file_stem()
                            .and_then(|name| name.to_str())
                            .is_some_and(key_valid)
                        && entry.file_type().is_ok_and(|typ| typ.is_file())
                    {
                        let _ = fs::remove_file(path);
                    }
                }
            }
        }
    }
    pub fn read_record(&self, namespace: &str, key: Option<&str>) -> Option<Value> {
        let key = key.filter(|key| key_valid(key))?;
        if !self.enabled || !namespace_valid(namespace) {
            return None;
        }
        let packet = read(
            &self
                .directory
                .parent()?
                .join(namespace)
                .join(format!("{key}.json")),
            MAX_PACKET,
        )
        .ok()?;
        let data = packet.get("data")?;
        (packet["checksum"].as_str() == Some(sha256(&canonical(data)).as_str()))
            .then(|| data.clone())
    }
    pub fn write_record(&self, namespace: &str, key: Option<&str>, data: &Value) {
        let Some(key) = key.filter(|key| key_valid(key)) else {
            return;
        };
        if !self.enabled || !namespace_valid(namespace) {
            return;
        }
        let packet = canonical(&json!({"data":data,"checksum":sha256(&canonical(data))}));
        let Some(parent) = self.directory.parent() else {
            return;
        };
        let _ = publish(
            &parent.join(namespace).join(format!("{key}.json")),
            &packet,
            false,
        );
    }
    pub fn prepared(
        &mut self,
        key: Option<&str>,
        skip: Option<&str>,
    ) -> (Option<Value>, Option<String>) {
        let Some(record) = self.read_record("prepared-v1", key) else {
            return (None, None);
        };
        let Some(target) = record["target"].as_str().filter(|key| key_valid(key)) else {
            return (None, None);
        };
        if Some(target) == skip {
            return (None, Some(target.into()));
        }
        let value = self.get(Some(target));
        if value.is_some() {
            self.prepared_hits += 1;
        }
        (value, Some(target.into()))
    }
    pub fn remember(&self, prepared: Option<&str>, target: Option<&str>) {
        if let Some(target) = target {
            self.write_record("prepared-v1", prepared, &json!({"target":target}));
        }
    }
    pub fn get(&mut self, key: Option<&str>) -> Option<Value> {
        let key = key.filter(|key| key_valid(key))?;
        let packet = read(
            &self.directory.join(format!("{key}.json")),
            MAX_PACKET + 4096,
        );
        let value = match packet {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                self.stats.misses += 1;
                return None;
            }
            Ok(packet)
                if packet["schema"].as_u64() == Some(SCHEMA)
                    && packet["key"].as_str() == Some(key) =>
            {
                packet
                    .get("value")
                    .filter(|value| {
                        packet["checksum"].as_str() == Some(sha256(&canonical(value)).as_str())
                    })
                    .filter(|value| values::validate(value).is_ok())
                    .cloned()
            }
            _ => None,
        };
        if value.is_some() {
            self.stats.hits += 1;
        } else {
            self.stats.corrupt += 1;
            self.stats.misses += 1;
        }
        value
    }
    pub fn put(&mut self, key: Option<&str>, value: &Value) {
        let Some(key) = key.filter(|key| key_valid(key)) else {
            return;
        };
        if values::validate(value).is_err() {
            return;
        }
        let contents = canonical(value);
        if contents.len() > MAX_PACKET {
            return;
        }
        let packet = canonical(
            &json!({"schema":SCHEMA,"key":key,"value":value,"checksum":sha256(&contents)}),
        );
        if publish(&self.directory.join(format!("{key}.json")), &packet, true).is_ok() {
            self.stats.writes += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn canonical_float_notation_matches_reference_boundaries() {
        for (input, expected) in [
            ("-0.0", "-0.0"),
            ("1e-4", "0.0001"),
            ("1e-5", "1e-05"),
            ("1e15", "1000000000000000.0"),
            ("1e16", "1e+16"),
            ("5e-324", "5e-324"),
            ("2.2705124243424068e-153", "2.2705124243424068e-153"),
        ] {
            let value: Value = serde_json::from_str(input).unwrap();
            let actual = String::from_utf8(canonical(&value)).unwrap();
            assert_eq!(actual, expected);
        }
    }
    #[test]
    fn canonical_strings_preserve_unicode_and_controls_as_ascii() {
        let value = json!({"z":"😀","a":"héllo\n\u{0000}"});
        let actual = String::from_utf8(canonical(&value)).unwrap();
        let expected = r#"{"a":"h\u00e9llo\n\u0000","z":"\ud83d\ude00"}"#;
        assert_eq!(actual, expected);
    }
}
