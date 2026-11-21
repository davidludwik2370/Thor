use crate::ast::{Source, Span};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fmt;
use std::io::IsTerminal;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Position {
    pub file: String,
    pub offset: usize,
    pub end_offset: usize,
    pub line: usize,
    pub column: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Diagnostic {
    #[serde(rename = "type")]
    pub severity: String,
    pub stage: String,
    pub pos: Option<Position>,
    pub msgs: Vec<String>,
}

#[derive(Debug)]
pub struct Error {
    pub message: String,
    pub diagnostics: Vec<Value>,
    /// A migration gap is different from an invalid Thor program.
    pub unsupported: bool,
    pub exit_status: u8,
}
pub type Result<T> = std::result::Result<T, Error>;

/// Complete a copied word without widening an explicit expression or a
/// synthetic source-map range. Work is bounded by the adjacent word.
pub(crate) fn whole_word_span(raw: &str, span: Span) -> Span {
    let Some(first) = raw.get(span.start..).and_then(|rest| rest.chars().next()) else {
        return span;
    };
    let word = |ch: char| ch.is_alphanumeric() || ch == '_';
    if !word(first) { return span; }
    let mut start = span.start;
    for ch in raw[..start].chars().rev() {
        if !word(ch) { break; }
        start -= ch.len_utf8();
    }
    let end = start + raw[start..].chars().take_while(|ch| word(*ch))
        .map(char::len_utf8).sum::<usize>();
    if span.end <= end { Span::new(start, end) } else { span }
}

impl Error {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            diagnostics: vec![],
            unsupported: false,
            exit_status: 1,
        }
    }
    pub fn usage(message: impl Into<String>) -> Self {
        let mut error = Self::new(message);
        error.exit_status = 2;
        error
    }
    pub fn at(source: &Source, span: Span, message: impl Into<String>) -> Self {
        let message = message.into();
        let (line, column) = source.position(span.start);
        let (end_line, end_column) = source.position(span.end);
        Self {
            message: message.clone(),
            unsupported: false,
            exit_status: 1,
            diagnostics: vec![json!({"type":"error", "stage":"thor", "pos": {
                "file":source.path, "offset":span.start, "end_offset":span.end,
                "line":line, "column":column,"end_line":end_line,"end_column":end_column}, "msgs":[message]})],
        }
    }
    pub fn unsupported(source: &Source, span: Span, feature: &str) -> Self {
        let mut error = Self::at(source, span,
            format!("Compiler analysis limitation: {feature} remains unresolved for this program; please report this source pattern with a minimal reproducer."));
        error.unsupported = true;
        error.diagnostics[0]["stage"] = json!("rust-migration");
        error
    }
    pub fn io(action: &str, error: impl fmt::Display) -> Self {
        Self::new(format!("{action}: {error}"))
    }
}
impl fmt::Display for Error {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str(&self.message)
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(error: std::io::Error) -> Self {
        Self::io("I/O failure", error)
    }
}
impl From<serde_json::Error> for Error {
    fn from(error: serde_json::Error) -> Self {
        Self::io("invalid parser protocol", error)
    }
}

/// Render source diagnostics with the reference's source excerpts and related
/// notes. Source-map coordinates remain UTF-8 offsets and UTF-16 LSP columns;
/// the terminal excerpt uses Unicode scalars and four-column tab stops.
pub fn text(error: &Value, raw: Option<&str>, terse: bool, unix: bool) -> String {
    let color = std::io::stderr().is_terminal()
        && std::env::var_os("NO_COLOR").is_none_or(|value| value.is_empty())
        && std::env::var("TERM").as_deref() != Ok("dumb")
        && std::env::var("CLICOLOR").as_deref() != Ok("0");
    render(error, raw, terse, unix, color)
}

fn style(text: &str, severity: &str, color: bool) -> String {
    let code = match (color, severity) {
        (true, "Error") => "31",
        (true, "Warning") => "33",
        _ => return text.to_string(),
    };
    format!("\x1b[{code}m{text}\x1b[0m")
}

fn render(error: &Value, raw: Option<&str>, terse: bool, unix: bool, color: bool) -> String {
    let pos = &error["pos"];
    let file = pos["file"].as_str();
    let location = if let Some(file) = file {
        if unix {
            format!("{}:{}:{}:", file, pos["line"], pos["column"])
        } else {
            format!("{}({}:{}):", file, pos["line"], pos["column"])
        }
    } else {
        "thor".into()
    };
    let severity = match error["type"].as_str().unwrap_or("error") {
        "warning" => "Warning",
        "note" => "Note",
        _ => "Error",
    };
    let messages = error["msgs"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect::<Vec<_>>()
        .join("\n");
    let label = style(&format!("{severity}:"), severity, color);
    let separator = if file.is_some() && severity != "Note" { "\n" } else { " " };
    let mut output = format!("{location}{separator}{label} {messages}");
    let read = if raw.is_none() {
        file.and_then(|file| std::fs::read_to_string(file).ok())
    } else {
        None
    };
    if let Some(raw) = raw.or(read.as_deref()).filter(|_| !terse) {
        let row = pos["line"].as_u64().unwrap_or(0) as usize;
        if let Some(line) = row.checked_sub(1).and_then(|row| raw.lines().nth(row)) {
            let offset = pos["offset"].as_u64().unwrap_or(0) as usize;
            let end = pos["end_offset"].as_u64().unwrap_or(offset as u64) as usize;
            let prefix = raw
                .get(..offset)
                .unwrap_or("")
                .rsplit('\n')
                .next()
                .unwrap_or("");
            let selected = raw
                .get(offset..end)
                .unwrap_or("")
                .split('\n')
                .next()
                .unwrap_or("");
            output.push_str(&format!(
                "\n    {}\n    {}{}",
                tabs(line),
                " ".repeat(tabs(prefix).chars().count()),
                style(&"^".repeat(tabs(selected).chars().count().max(1)), severity, color)
            ));
        }
    }
    for note in error["related"].as_array().into_iter().flatten() {
        let messages = note["message"]
            .as_str()
            .map(|message| vec![json!(message)])
            .unwrap_or_else(|| note["msgs"].as_array().cloned().unwrap_or_default());
        let same = file.is_some() && note["pos"]["file"].as_str() == file;
        output.push('\n');
        output.push_str(&render(
            &json!({"type":"note","pos":note["pos"],"msgs":messages}),
            raw.filter(|_| same),
            terse,
            unix,
            color,
        ));
    }
    output
}
fn tabs(text: &str) -> String {
    let mut out = String::new();
    let mut column = 0;
    for ch in text.chars() {
        if ch == '\t' {
            let count = 4 - column % 4;
            out.extend(std::iter::repeat_n(' ', count));
            column += count;
        } else {
            out.push(ch);
            column = if ch == '\n' || ch == '\r' {
                0
            } else {
                column + 1
            };
        }
    }
    out
}

/// An early parser error can precede Project construction. Retain the unsaved
/// source excerpt rather than reading the older on-disk document at this point.
pub fn overlay_source(error: &Value, path: &std::path::Path) -> Option<String> {
    let file = std::path::Path::new(error["pos"]["file"].as_str()?);
    let packet: Value = serde_json::from_slice(&std::fs::read(path).ok()?).ok()?;
    for (original, replacement) in packet["Replace"].as_object()? {
        if crate::project::absolute(std::path::Path::new(original))
            .ok()
            .as_deref()
            == Some(file)
        {
            return std::fs::read_to_string(replacement.as_str()?).ok();
        }
    }
    None
}

#[cfg(test)]
mod migration_tests {
    use super::*;
    #[test]
    fn colors_preserve_source_spacing_related_notes_and_terse_locations() {
        let pos = json!({"file":"/source.thor","line":1,"column":2,"offset":1,"end_offset":4});
        for (severity, label, code) in [("error", "Error", "31"), ("warning", "Warning", "33")] {
            let diagnostic = json!({"type":severity,"pos":pos,"msgs":["message"],
                "related":[{"pos":pos,"message":"related note"}]});
            for (terse, unix) in [(false, false), (true, false), (false, true), (true, true)] {
                let plain = render(&diagnostic, Some("\tbad"), terse, unix, false);
                let colored = render(&diagnostic, Some("\tbad"), terse, unix, true);
                let location = if unix { "/source.thor:1:2:" } else { "/source.thor(1:2):" };
                if !plain.starts_with(&format!("{location}\n{label}: message\n")) {
                    panic!("source diagnostic severity must start on the next line");
                }
                let prefix = format!("\x1b[{code}m");
                let stripped = colored.replace(&prefix, "").replace("\x1b[0m", "");
                if stripped != plain || !colored.contains(&format!("{prefix}{label}:\x1b[0m")) {
                    panic!("coloring changed the diagnostic contents or omitted the severity label");
                }
                if !terse && !colored.contains(&format!("        {prefix}^^^\x1b[0m")) {
                    panic!("coloring changed tab alignment or omitted the source underline");
                }
                if !colored.contains(" Note: related note") {
                    panic!("related notes inherited an error or warning style");
                }
            }
        }
    }

    #[test]
    fn source_less_diagnostics_keep_the_tool_prefix() {
        for (severity, label) in [("error", "Error"), ("warning", "Warning"), ("note", "Note")] {
            let diagnostic = json!({"type":severity,"msgs":["message"]});
            let output = render(&diagnostic, None, false, false, false);
            if output != format!("thor {label}: message") {
                panic!("source-less diagnostics must retain the tool prefix");
            }
        }
    }

    #[test]
    fn unresolved_guard_preserves_its_status_without_recommending_the_frozen_compiler() {
        let source = Source::from_frontend("/audit.thor".into(), String::new(),
            json!({"nodes":[],"features":[],"edits":[],"selections":[]}), true)
            .expect("empty source protocol");
        let error = Error::unsupported(&source, Span::new(0, 0), "symbolic ownership");
        if !error.unsupported || error.diagnostics[0]["stage"] != "rust-migration"
            || error.message.contains("thor-python") || !error.message.contains("minimal reproducer")
            || error.message.contains("has not been ported") {
            panic!("unresolved guard lost its category or recommended an archived compiler");
        }
    }
}
