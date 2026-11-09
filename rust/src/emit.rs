//! Structured emission: flatten once; no marker strings, regexes, or source rescanning.
use crate::ast::Span;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::Path, rc::Rc};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Origin {
    pub file: String,
    pub span: Span,
    pub kind: String,
    pub exact: bool,
}
#[derive(Clone, Debug)]
enum Fragment {
    Text(String, Origin),
    Concat(Vec<Code>),
    Expansion(Code, Origin),
}
#[derive(Clone, Debug)]
pub struct Code(Rc<Fragment>);
impl Code {
    pub fn generated(text: impl Into<String>, path: &Path, span: Span, kind: &str) -> Self {
        Self(Rc::new(Fragment::Text(
            text.into(),
            Origin {
                file: path.to_string_lossy().into_owned(),
                span,
                kind: kind.into(),
                exact: false,
            },
        )))
    }
    pub fn source(text: &str, path: &Path, span: Span) -> Self {
        Self(Rc::new(Fragment::Text(
            text.into(),
            Origin {
                file: path.to_string_lossy().into_owned(),
                span,
                kind: "source".into(),
                exact: true,
            },
        )))
    }
    pub fn concat(parts: impl IntoIterator<Item = Code>) -> Self {
        Self(Rc::new(Fragment::Concat(parts.into_iter().collect())))
    }
    pub fn expansion(self, path: &Path, span: Span) -> Self {
        Self(Rc::new(Fragment::Expansion(
            self,
            Origin {
                file: path.to_string_lossy().into_owned(),
                span,
                kind: "expression".into(),
                exact: false,
            },
        )))
    }
    pub fn finish(&self) -> Emitted {
        let mut out = Emitted::default();
        self.append(&mut out);
        out
    }
    fn append(&self, out: &mut Emitted) {
        match self.0.as_ref() {
            Fragment::Text(text, origin) => {
                if text.is_empty() {
                    return;
                }
                let start = out.text.len();
                out.text.push_str(text);
                if let Some(last) = out.segments.last_mut() {
                    if origin.exact
                        && last.origin.exact
                        && last.end == start
                        && last.origin.file == origin.file
                        && last.origin.span.end == origin.span.start
                        && last.origin.kind == origin.kind
                    {
                        last.end = out.text.len();
                        last.origin.span.end = origin.span.end;
                        return;
                    }
                }
                out.segments.push(Segment {
                    start,
                    end: out.text.len(),
                    origin: origin.clone(),
                });
            }
            Fragment::Concat(parts) => {
                for part in parts {
                    part.append(out);
                }
            }
            Fragment::Expansion(code, origin) => {
                let start = out.text.len();
                code.append(out);
                let identity = out.segments.last().is_some_and(|last| {
                    last.origin.exact
                        && last.start <= start
                        && last.end >= out.text.len()
                        && last.origin.file == origin.file
                        && last.origin.span.start + start - last.start == origin.span.start
                        && origin.span.len() == out.text.len() - start
                });
                if !identity {
                    out.expansions.push((start, out.text.len(), origin.clone()));
                }
            }
        }
    }
}
#[derive(Clone, Debug)]
pub struct Segment {
    pub start: usize,
    pub end: usize,
    pub origin: Origin,
}
#[derive(Clone, Debug, Default)]
pub struct Emitted {
    pub text: String,
    pub segments: Vec<Segment>,
    pub expansions: Vec<(usize, usize, Origin)>,
}
impl Emitted {
    pub fn lookup(&self, offset: usize, length: usize) -> Option<Origin> {
        let index = self
            .segments
            .partition_point(|s| s.start <= offset)
            .saturating_sub(1);
        let segment = self.segments.get(index)?;
        let mut origin = segment.origin.clone();
        if origin.exact {
            origin.span.start += offset
                .saturating_sub(segment.start)
                .min(segment.end - segment.start);
            origin.span.end = origin.span.end.min(origin.span.start + length.max(1));
        }
        Some(origin)
    }
    /// Version 1 wire format is shared with Python/TLS/debugger consumers.
    pub fn map_json(&self, generated: &Path, sources: &[(String, String)]) -> Value {
        let segments: Vec<_> = self
            .segments
            .iter()
            .map(|s| {
                json!([
                    s.start,
                    s.end,
                    s.origin.file,
                    s.origin.span.start,
                    s.origin.span.end,
                    s.origin.kind,
                    s.origin.exact
                ])
            })
            .collect();
        let expansions: Vec<_> = self
            .expansions
            .iter()
            .map(|(a, b, o)| json!([a, b, o.file, o.span.start, o.span.end]))
            .collect();
        let sources: serde_json::Map<_, _> = sources
            .iter()
            .filter(|(path, _)| self.segments.iter().any(|s| &s.origin.file == path))
            .map(|(p, s)| (p.clone(), Value::String(s.clone())))
            .collect();
        json!({"version":1,"encoding":"utf-8","generated":generated,"sources":sources,
            "segments":segments,"expansions":expansions,"regions":[]})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fragments_map_reordered_and_repeated_unicode() {
        let path = Path::new("/source.thor");
        let a = Code::source("😀", path, Span::new(2, 6));
        let b = Code::generated("helper(", path, Span::new(0, 8), "helper");
        let code = Code::concat([
            b,
            a.clone(),
            Code::generated(",", path, Span::new(0, 8), "helper"),
            a,
        ]);
        let output = code.expansion(path, Span::new(0, 8)).finish();
        assert_eq!(output.text, "helper(😀,😀");
        assert_eq!(output.lookup(12, 4).unwrap().span, Span::new(2, 6));
        assert_eq!(output.expansions.len(), 1);
        let mut cursor = 0;
        for segment in &output.segments {
            assert_eq!(segment.start, cursor);
            cursor = segment.end;
        }
        assert_eq!(cursor, output.text.len());
    }
    #[test]
    fn exact_chunks_merge_and_identity_is_not_expansion() {
        let path = Path::new("a");
        let doc = Code::concat([
            Code::source("ab", path, Span::new(0, 2)),
            Code::source("cd", path, Span::new(2, 4)),
        ]);
        let emitted = doc.expansion(path, Span::new(0, 4)).finish();
        assert_eq!(emitted.segments.len(), 1);
        assert!(emitted.expansions.is_empty());
        assert_eq!(emitted.lookup(3, 4).unwrap().span, Span::new(3, 4));
    }
}
