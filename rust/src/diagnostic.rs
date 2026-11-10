use crate::ast::{Source, Span};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fmt;

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
        Self {
            message: message.clone(),
            unsupported: false,
            exit_status: 1,
            diagnostics: vec![json!({"type":"error", "stage":"thor", "pos": {
                "file":source.path, "offset":span.start, "end_offset":span.end,
                "line":line, "column":column}, "msgs":[message]})],
        }
    }
    pub fn unsupported(source: &Source, span: Span, feature: &str) -> Self {
        let mut error = Self::at(source, span,
            format!("Rust migration: {feature} has not been ported yet; use the Python compiler (`thor`) for this program."));
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
