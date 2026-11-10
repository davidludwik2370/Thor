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
    Region(Code, Origin),
    Annotation(Code, String),
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
    pub fn annotated(self, kind: &str) -> Self {
        Self(Rc::new(Fragment::Annotation(self, kind.into())))
    }
    pub fn region(self, path: &Path, span: Span, kind: &str) -> Self {
        Self(Rc::new(Fragment::Region(
            self,
            Origin {
                file: path.to_string_lossy().into_owned(),
                span,
                kind: kind.into(),
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
                if matches!(
                    origin.kind.as_str(),
                    "expression-helper" | "debug-capture" | "source-call-target"
                ) || origin.kind.starts_with("pure-procedure|")
                {
                    out.regions.push((start, out.text.len(), origin.clone()));
                }
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
            Fragment::Annotation(code, kind) => {
                let first = out.segments.len();
                code.append(out);
                for segment in &mut out.segments[first..] {
                    if segment.origin.kind == "generated" {
                        segment.origin.kind = kind.clone();
                    }
                }
            }
            Fragment::Region(code, origin) => {
                let start = out.text.len();
                let first = out.segments.len();
                code.append(out);
                for segment in &mut out.segments[first..] {
                    if segment.origin.kind == "generated" {
                        segment.origin.kind =
                            origin.kind.split('|').next().unwrap_or_default().into();
                    }
                }
                if out.text.len() > start {
                    out.regions.push((start, out.text.len(), origin.clone()));
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
    pub regions: Vec<(usize, usize, Origin)>,
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
        let regions: Vec<_> = self
            .regions
            .iter()
            .map(|(start, end, origin)| {
                let mut parts = origin.kind.split('|');
                let kind = parts.next().unwrap_or_default();
                let mut row = vec![
                    json!(start),
                    json!(end),
                    json!(kind),
                    json!(origin.file),
                    json!(origin.span.start),
                    json!(origin.span.end),
                ];
                if kind == "pure-procedure" {
                    row.extend(parts.map(|part| json!(part)));
                }
                Value::Array(row)
            })
            .collect();
        json!({"version":1,"encoding":"utf-8","generated":generated,"sources":sources,
            "segments":segments,"expansions":expansions,"regions":regions})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn nested_debug_regions_preserve_capture_spans_and_checked_identity() {
        let path = Path::new("/original.thor");
        let body = Code::concat([
            Code::generated("helper(", path, Span::new(0, 9), "support"),
            Code::source("value", path, Span::new(2, 7)).region(
                path,
                Span::new(2, 7),
                "debug-capture",
            ),
            Code::generated(")", path, Span::new(0, 9), "support"),
        ])
        .region(path, Span::new(0, 9), "expression-helper")
        .region(path, Span::new(0, 9), "pure-procedure|work|checked_work");
        let emitted = body.finish();
        let packet = emitted.map_json(Path::new("/generated.odin"), &[]);
        assert_eq!(emitted.text, "helper(value)");
        assert_eq!(emitted.lookup(8, 1).unwrap().span, Span::new(3, 4));
        assert_eq!(
            packet["regions"],
            json!([
                [7, 12, "debug-capture", "/original.thor", 2, 7],
                [0, 13, "expression-helper", "/original.thor", 0, 9],
                [
                    0,
                    13,
                    "pure-procedure",
                    "/original.thor",
                    0,
                    9,
                    "work",
                    "checked_work"
                ],
            ])
        );
    }
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
#[derive(Clone, Debug)]
pub struct MappedEdit {
    pub span: Span,
    pub text: String,
    pub origin: Origin,
}
impl Emitted {
    pub fn rewrite(&self, changes: &[MappedEdit]) -> Result<Self, String> {
        let mut edits = changes.iter().collect::<Vec<_>>();
        edits.sort_by_key(|edit| edit.span.start);
        let mut output = Self::default();
        let mut cursor = 0;
        let copy = |start: usize, end: usize, out: &mut Self| {
            let first = out.text.len();
            out.text.push_str(&self.text[start..end]);
            for segment in &self.segments {
                let low = segment.start.max(start);
                let high = segment.end.min(end);
                if low >= high {
                    continue;
                }
                let mut origin = segment.origin.clone();
                if origin.exact {
                    origin.span.start += low - segment.start;
                    origin.span.end = origin.span.start + high - low;
                }
                out.segments.push(Segment {
                    start: first + low - start,
                    end: first + high - start,
                    origin,
                });
            }
        };
        for edit in &edits {
            if edit.span.start < cursor
                || edit.span.end < edit.span.start
                || edit.span.end > self.text.len()
                || !self.text.is_char_boundary(edit.span.start)
                || !self.text.is_char_boundary(edit.span.end)
            {
                return Err("Overlapping or invalid generated-source edits".into());
            }
            copy(cursor, edit.span.start, &mut output);
            let start = output.text.len();
            output.text.push_str(&edit.text);
            if !edit.text.is_empty() {
                output.segments.push(Segment {
                    start,
                    end: output.text.len(),
                    origin: edit.origin.clone(),
                });
            }
            cursor = edit.span.end;
        }
        copy(cursor, self.text.len(), &mut output);
        let shifted = |offset: usize, boundary: bool| {
            let delta = edits
                .iter()
                .filter(|edit| {
                    edit.span.end < offset
                        || edit.span.end == offset && (edit.span.start < edit.span.end || boundary)
                })
                .map(|edit| edit.text.len() as isize - edit.span.len() as isize)
                .sum::<isize>();
            offset
                .checked_add_signed(delta)
                .ok_or_else(|| "Invalid shifted source-map boundary".to_string())
        };
        let regions = |rows: &[(usize, usize, Origin)]| {
            rows.iter()
                .map(|(a, b, origin)| {
                    Ok((shifted(*a, true)?, shifted(*b, a == b)?, origin.clone()))
                })
                .collect::<Result<Vec<_>, String>>()
        };
        output.expansions = regions(&self.expansions)?;
        output.regions = regions(&self.regions)?;
        Ok(output)
    }
}

#[cfg(test)]
mod rewrite_tests {
    use super::*;
    #[test]
    fn rewrite_keeps_unicode_source_spans_and_shifts_nested_regions() {
        let path = Path::new("/source.thor");
        let emitted = Code::source("ab😀cd", path, Span::new(0, 8))
            .region(path, Span::new(0, 8), "expression-helper")
            .expansion(path, Span::new(0, 8))
            .finish();
        let origin = Origin {
            file: "/source.thor".into(),
            span: Span::new(1, 2),
            kind: "unused-local".into(),
            exact: false,
        };
        let edits = vec![
            MappedEdit {
                span: Span::new(0, 0),
                text: "{\n".into(),
                origin: origin.clone(),
            },
            MappedEdit {
                span: Span::new(2, 6),
                text: "x".into(),
                origin: origin.clone(),
            },
            MappedEdit {
                span: Span::new(8, 8),
                text: "\n}".into(),
                origin,
            },
        ];
        let result = emitted.rewrite(&edits).unwrap();
        assert_eq!(result.text, "{\nabxcd\n}");
        assert_eq!(result.lookup(5, 1).unwrap().span, Span::new(6, 7));
        assert_eq!(result.regions[0].0, 2);
        assert_eq!(result.regions[0].1, 7);
    }
    #[test]
    fn rewrite_rejects_overlaps_and_utf8_splits() {
        let path = Path::new("/source.thor");
        let emitted = Code::source("😀", path, Span::new(0, 4)).finish();
        let origin = Origin {
            file: "/source.thor".into(),
            span: Span::new(0, 4),
            kind: "generated".into(),
            exact: false,
        };
        let split = emitted.rewrite(&[MappedEdit {
            span: Span::new(1, 2),
            text: "x".into(),
            origin: origin.clone(),
        }]);
        assert!(split.is_err());
        let overlap = emitted.rewrite(&[
            MappedEdit {
                span: Span::new(0, 4),
                text: "x".into(),
                origin: origin.clone(),
            },
            MappedEdit {
                span: Span::new(0, 4),
                text: "y".into(),
                origin,
            },
        ]);
        assert!(overlap.is_err());
    }
}
