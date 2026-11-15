//! Keep native defer exit selection and translate its location after emission.
use super::sourceidentity::{active_procedure, location_procedure};
use super::*;
use std::ops::Bound::{Excluded, Unbounded};

struct LocationRow {
    line: i32,
    column: i32,
    end_column: usize,
    source_line: i32,
    source_column: i32,
    exact: bool,
}

impl Lower<'_, '_> {
    fn deferred_owner(&self, id: NodeId) -> Option<NodeId> {
        let owner = active_procedure(self.source, id)?;
        self.source
            .ancestors(id)
            .take_while(|parent| *parent != owner)
            .any(|parent| self.source.node(parent).kind == "Defer")
            .then_some(owner)
    }

    fn branch_value(&self, owner: NodeId) -> String {
        self.branch_captures
            .iter()
            .rev()
            .find(|(procedure, _)| *procedure == owner)
            .map(|(_, parameter)| parameter.clone())
            .unwrap_or_else(|| "#branch_location".into())
    }

    pub(super) fn branch_location(&mut self, id: NodeId) -> Option<Code> {
        // Leave invalid uses to Odin, including its lazy native when checking.
        let owner = self.deferred_owner(id)?;
        let value = self.branch_value(owner);
        if self.source_mode() == "none" {
            return Some(self.g(value, id));
        }
        self.branch_procedures.insert(owner);
        let runtime = format!("{}source_runtime", self.prefix);
        self.modules.insert(runtime, "base:runtime".into());
        // Prime hygiene before the import footer is emitted.
        for name in ["int", "i32", "bool", "string", "len"] {
            self.native(name);
        }
        let body = self.source.role(owner, "body")?;
        let procedure = self.source_string(&location_procedure(self.source, body), "P");
        Some(self.g(
            format!(
                "{}map_branch_location({value}, {})",
                self.prefix,
                json!(procedure)
            ),
            id,
        ))
    }

    pub(super) fn capture_branch_location(
        &mut self,
        id: NodeId,
        params: &mut Vec<String>,
        args: &mut Vec<Code>,
    ) -> Option<(NodeId, String)> {
        // Only a directive borrowed from the surrounding defer needs capture.
        // A defer declared inside the helper retains its own native exit site.
        let owner = self.deferred_owner(id)?;
        let needed = std::iter::once(id)
            .chain(self.source.descendants(id))
            .any(|child| {
                let node = self.source.node(child);
                node.kind == "Directive"
                    && self.source.names.get(node.name) == "branch_location"
                    && active_procedure(self.source, child) == Some(owner)
            });
        if !needed {
            return None;
        }
        let runtime = format!("{}source_runtime", self.prefix);
        self.modules.insert(runtime.clone(), "base:runtime".into());
        let parameter = format!("{}branch_capture", self.prefix);
        params.push(format!("{parameter}: {runtime}.Source_Code_Location"));
        args.push(self.g(self.branch_value(owner), id).region(
            &self.source.path,
            self.source.node(id).span,
            "debug-capture",
        ));
        Some((owner, parameter))
    }

    fn branch_owner_at(&self, offset: usize) -> Option<NodeId> {
        self.branch_procedures
            .iter()
            .copied()
            .filter(|procedure| {
                self.source.role(*procedure, "body").is_some_and(|body| {
                    let span = self.source.node(body).span;
                    span.start <= offset && offset < span.end
                })
            })
            .min_by_key(|procedure| self.source.node(*procedure).span.len())
    }

    fn location_row(
        &self,
        emitted: &Emitted,
        offset: usize,
        line: usize,
        column: usize,
        end_column: usize,
        point: bool,
        source_offset: Option<usize>,
    ) -> Option<LocationRow> {
        let origin = emitted.lookup(offset, 1)?;
        if origin.file != self.source.path.to_string_lossy() {
            return None;
        }
        let offset = source_offset.unwrap_or(origin.span.start);
        self.branch_owner_at(offset)?;
        let (source_line, source_column) = self.source.rune_position(offset);
        Some(LocationRow {
            line: self.source_number(line),
            column: self.source_number(column),
            end_column: if point { 0 } else { end_column },
            source_line: self.source_number(source_line),
            source_column: self.source_number(source_column),
            exact: origin.exact && !point && source_offset.is_none(),
        })
    }

    pub(super) fn finish_branch_locations(&mut self, mut emitted: Emitted) -> Emitted {
        if self.branch_procedures.is_empty() {
            return emitted;
        }
        let starts: Vec<_> = std::iter::once(0)
            .chain(
                emitted
                    .text
                    .bytes()
                    .enumerate()
                    .filter_map(|(index, byte)| (byte == b'\n').then_some(index + 1)),
            )
            .collect();
        let position = |offset: usize| {
            let row = starts
                .partition_point(|start| *start <= offset)
                .saturating_sub(1);
            (
                row + 1,
                emitted.text[starts[row]..offset].chars().count() + 1,
            )
        };
        let mut rows = vec![];
        let endpoints = self.branch_expression_endpoints(&emitted);
        if self.source_mode() == "obfuscated" {
            // Obfuscation destroys column intervals. Record native token starts
            // only for procedures that use the directive, using the existing
            // native-token scanner rather than adding a parser subprocess.
            for token in crate::diagnosticcleanup::tokens(&emitted.text) {
                let offset = token.as_ptr() as usize - emitted.text.as_ptr() as usize;
                let (line, column) = position(offset);
                if let Some(row) = self.location_row(
                    &emitted,
                    offset,
                    line,
                    column,
                    0,
                    true,
                    endpoints.get(&offset).copied(),
                ) {
                    rows.push(row);
                }
            }
        } else {
            let boundaries: std::collections::BTreeSet<_> = endpoints
                .keys()
                .flat_map(|offset| {
                    [
                        *offset,
                        *offset
                            + emitted.text[*offset..]
                                .chars()
                                .next()
                                .map(char::len_utf8)
                                .unwrap_or(0),
                    ]
                })
                .collect();
            for segment in &emitted.segments {
                let mut cursor = segment.start;
                while cursor < segment.end {
                    let (line, column) = position(cursor);
                    let mut stop = segment
                        .end
                        .min(starts.get(line).copied().unwrap_or(emitted.text.len()));
                    if let Some(boundary) = boundaries.range((Excluded(cursor), Unbounded)).next() {
                        stop = stop.min(*boundary);
                    }
                    let end_column = column + emitted.text[cursor..stop].chars().count();
                    if let Some(row) = self.location_row(
                        &emitted,
                        cursor,
                        line,
                        column,
                        end_column,
                        false,
                        endpoints.get(&cursor).copied(),
                    ) {
                        rows.push(row);
                    }
                    cursor = stop;
                }
            }
        }
        rows.sort_by_key(|row| (row.line, row.column));
        let runtime = format!("{}source_runtime", self.prefix);
        let row_type = format!("{}Branch_Location_Row", self.prefix);
        let table = format!("{}branch_locations", self.prefix);
        let mapper = format!("{}map_branch_location", self.prefix);
        let integer = self.native("int");
        let i32_type = self.native("i32");
        let boolean = self.native("bool");
        let string = self.native("string");
        let length = self.native("len");
        let file = self.source_string(&self.source.path.to_string_lossy(), "F");
        let mut footer = format!(
            r#"
@(private="file")
{row_type} :: struct {{
    generated_line, generated_column, end_column: {integer},
    source_line, source_column: {i32_type},
    exact: {boolean},
}}
@(private="file", rodata)
{table} := []{row_type}{{
"#
        );
        for row in rows {
            footer.push_str(&format!(
                "{{{}, {}, {}, {}, {}, {}}},\n",
                row.line, row.column, row.end_column, row.source_line, row.source_column, row.exact
            ));
        }
        footer.push_str(&format!(r#"}}
@(private="file")
{mapper} :: #force_inline proc "contextless"(loc: {runtime}.Source_Code_Location, procedure: {string}) -> {runtime}.Source_Code_Location {{
    low, high := 0, {length}({table})
    for low < high {{
        middle := low + (high-low)/2
        row := {table}[middle]
        if row.generated_line < {integer}(loc.line) || row.generated_line == {integer}(loc.line) && row.generated_column <= {integer}(loc.column) {{
            low = middle+1
        }} else {{ high = middle }}
    }}
    index := low-1
    if index >= 0 {{
        row := {table}[index]
        if row.generated_line == {integer}(loc.line) && row.generated_column <= {integer}(loc.column) &&
            (row.end_column == 0 && row.generated_column == {integer}(loc.column) || {integer}(loc.column) < row.end_column) {{
            result := loc
            result.file_path = {file}
            result.line = row.source_line
            result.column = row.source_column
            result.procedure = procedure
            if row.exact {{ result.column += {i32_type}({integer}(loc.column)-row.generated_column) }}
            return result
        }}
    }}
    return loc
}}
"#, file=json!(file)));
        // Native vet policies also inspect the generated mapping helper.
        let footer = footer.split_inclusive('\n').map(|line| {
            let indentation = line.bytes().take_while(|byte| *byte == b' ').count() / 4;
            format!("{}{}", "\t".repeat(indentation), &line[indentation * 4..])
        }).collect::<String>();
        let appended = self
            .gen(footer, Span::new(0, 0), "source-location-support")
            .finish();
        let offset = emitted.text.len();
        emitted.text.push_str(&appended.text);
        emitted
            .segments
            .extend(appended.segments.into_iter().map(|mut segment| {
                segment.start += offset;
                segment.end += offset;
                segment
            }));
        emitted
    }

    fn branch_expression_endpoints(&self, emitted: &Emitted) -> BTreeMap<usize, usize> {
        let mut endpoints = BTreeMap::new();
        let mut expansions = emitted.expansions.iter().collect::<Vec<_>>();
        // An enclosing expression owns a shared final token.
        expansions.sort_by_key(|(_, _, origin)| origin.span.len());
        for (start, end, origin) in expansions {
            if origin.file != self.source.path.to_string_lossy()
                || self.branch_owner_at(origin.span.start).is_none()
            {
                continue;
            }
            let raw = &emitted.text[*start..*end];
            let Some(token) = crate::diagnosticcleanup::tokens(raw).last().copied() else {
                continue;
            };
            let offset = token.as_ptr() as usize - emitted.text.as_ptr() as usize;
            let source_end = self
                .source
                .at(origin.span, NodeId(0))
                .map(|node| original_end_token(self.source, node))
                .unwrap_or(origin.span.start);
            endpoints.insert(offset, source_end);
        }
        endpoints
    }
}

fn original_end_token(source: &Source, id: NodeId) -> usize {
    let node = source.node(id);
    let child = match node.kind.as_str() {
        "Binary" => source.role(id, "right"),
        "Unary" | "Cast" | "AutoCast" | "ExpressionStatement" => source.role(id, "expr"),
        "Selector" | "ImplicitSelector" => source.role(id, "field"),
        "TypeAssertion" => source.role(id, "type"),
        "FieldValue" => source.role(id, "value"),
        "Ternary" | "WhenExpr" | "OrElse" => source.role(id, "y"),
        "Declaration" => source
            .group(id, "values")
            .last()
            .copied()
            .or_else(|| source.role(id, "type")),
        _ => None,
    };
    if let Some(child) = child {
        return original_end_token(source, child);
    }
    if matches!(
        node.kind.as_str(),
        "Ident" | "Implicit" | "Literal" | "Directive" | "Unsafe"
    ) {
        return node.span.start;
    }
    if matches!(
        node.kind.as_str(),
        "Call"
            | "Index"
            | "Slice"
            | "Paren"
            | "Compound"
            | "ProcedureGroup"
            | "Interpolation"
            | "Comprehension"
            | "SwitchExpr"
            | "CompileSwitchExpr"
    ) {
        return node.span.end.saturating_sub(1);
    }
    crate::diagnosticcleanup::tokens(source.text(id))
        .last()
        .map(|token| token.as_ptr() as usize - source.raw.as_ptr() as usize)
        .unwrap_or(node.span.start)
}
