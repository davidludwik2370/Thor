//! Reference diagnostic restoration/filtering. Emission boundaries, rather
//! than guesses about user source text, identify lowered expressions.
use crate::{
    ast::{NodeId, Source, Span},
    emit::Origin,
    project::Project,
    semantics::Ref,
};
use serde_json::{json, Value};
use std::{collections::BTreeSet, path::Path};

fn word(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_' || byte >= 128
}
fn number_end(raw: &[u8], start: usize) -> usize {
    let mut end = start;
    if raw[start] == b'0' && raw.get(start + 1).is_some_and(|b| b"bodzxh".contains(b)) {
        end += 2;
        while raw.get(end).is_some_and(|b| b.is_ascii_hexdigit() || *b == b'_') {
            end += 1;
        }
        return end;
    }
    while raw.get(end).is_some_and(|b| b.is_ascii_digit() || *b == b'_') {
        end += 1;
    }
    if raw.get(end) == Some(&b'.') && raw.get(end + 1) != Some(&b'.') {
        end += 1;
        while raw.get(end).is_some_and(|b| b.is_ascii_digit() || *b == b'_') {
            end += 1;
        }
    }
    if raw.get(end).is_some_and(|b| b"eE".contains(b)) {
        end += 1;
        if raw.get(end).is_some_and(|b| b"+-".contains(b)) { end += 1; }
        while raw.get(end).is_some_and(|b| b.is_ascii_hexdigit() || *b == b'_') {
            end += 1;
        }
    }
    if raw.get(end).is_some_and(|b| b"ijk".contains(b)) { end += 1; }
    end
}
pub(crate) fn tokens(raw: &str) -> Vec<&str> {
    let b = raw.as_bytes();
    let mut out = vec![];
    let mut i = 0;
    while i < b.len() {
        if b[i].is_ascii_whitespace() {
            i += 1;
            continue;
        }
        if b[i..].starts_with(b"//") {
            while i < b.len() && !matches!(b[i], b'\r' | b'\n') {
                i += 1;
            }
            continue;
        }
        if b[i..].starts_with(b"/*") {
            i += 2;
            let mut depth = 1;
            while i < b.len() && depth > 0 {
                if b[i..].starts_with(b"/*") {
                    depth += 1;
                    i += 2;
                } else if b[i..].starts_with(b"*/") {
                    depth -= 1;
                    i += 2;
                } else {
                    i += 1;
                }
            }
            continue;
        }
        let start = i;
        if matches!(b[i], b'"' | b'\'' | b'`') {
            let quote = b[i];
            i += 1;
            while i < b.len() {
                if b[i] == quote {
                    i += 1;
                    break;
                }
                if quote != b'`' && b[i] == b'\\' {
                    i = (i + 2).min(b.len());
                } else {
                    i += 1;
                }
            }
        } else if b[i].is_ascii_digit()
            || b[i] == b'.' && b.get(i + 1).is_some_and(u8::is_ascii_digit) {
            i = number_end(b, i);
        } else if b[i].is_ascii_alphabetic() || b[i] == b'_' || b[i] >= 128 {
            i += 1;
            while i < b.len() && word(b[i]) {
                i += 1;
            }
        } else {
            i += 1;
        }
        out.push(&raw[start..i]);
    }
    out
}
fn enclosed(text: &str) -> bool {
    if !text.starts_with('(') || !text.ends_with(')') {
        return false;
    }
    let parts = tokens(text);
    let mut depth = 0;
    for (index, token) in parts.iter().enumerate() {
        if *token == "(" {
            depth += 1;
        } else if *token == ")" {
            depth -= 1;
        }
        if depth == 0 && index + 1 < parts.len() {
            return false;
        }
    }
    depth == 0
}
fn replace_tokens(message: &str, pattern: &[&str], original: &str) -> String {
    if pattern.is_empty() {
        return message.into();
    }
    let mut result = String::new();
    let mut cursor = 0;
    let mut search = 0;
    while let Some(found) = message[search..].find(pattern[0]) {
        let first = search + found;
        let mut end = first;
        let mut matched = first == 0 || !word(message.as_bytes()[first - 1]);
        if matched {
            for (i, token) in pattern.iter().enumerate() {
                if i > 0 {
                    while end < message.len() && message.as_bytes()[end].is_ascii_whitespace() {
                        end += 1;
                    }
                }
                if !message[end..].starts_with(token) {
                    matched = false;
                    break;
                }
                end += token.len();
            }
        }
        matched &= end == message.len() || !word(message.as_bytes()[end]);
        if matched {
            result.push_str(&message[cursor..first]);
            result.push_str(original);
            cursor = end;
            search = end;
        } else {
            search = first + pattern[0].chars().next().map_or(1, char::len_utf8);
        }
        if search >= message.len() {
            break;
        }
    }
    result.push_str(&message[cursor..]);
    result
}
fn span(error: &Value) -> Option<(String, Span)> {
    let pos = &error["pos"];
    let start = pos["offset"].as_u64()? as usize;
    Some((
        pos["file"].as_str()?.into(),
        Span::new(
            start,
            pos["end_offset"].as_u64().unwrap_or(start as u64) as usize,
        ),
    ))
}
fn position(source: &Source, span: Span) -> Value {
    let (line, column) = source.position(span.start);
    let (end_line, end_column) = source.position(span.end);
    json!({"file":source.path,"offset":span.start,"end_offset":span.end,"line":line,"column":column,"end_line":end_line,"end_column":end_column})
}
fn messages(error: &Value) -> Vec<String> {
    error["msgs"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}
fn query(messages: &[String]) -> bool {
    messages.iter().any(|m| {
        m == "Expected a type to assign to the type parameter"
            || m.contains("'type_of' of polymorphic type cannot be determined")
            || m.contains("Cannot determine polymorphic type from parameter: 'invalid type'")
    })
}
fn sequence_group(project: &Project, message: &str, operation: &str) -> bool {
    let Some((_, group)) = message.split_once("procedure group '") else {
        return false;
    };
    let Some((name, _)) = group.split_once('\'') else {
        return false;
    };
    name.strip_suffix(operation)
        .is_some_and(|module| module.ends_with("sequence."))
        && project
            .sources
            .iter()
            .any(|source| name.starts_with(&source.prefix))
}
fn inference_contexts(source: &Source, span: Span) -> Vec<Span> {
    let operations = [
        "Binary",
        "Unary",
        "Paren",
        "Ternary",
        "Index",
        "Slice",
        "Selector",
        "Deref",
        "Compound",
        "SwitchExpr",
        "CompileSwitchExpr",
        "OrPanic",
        "Call",
        "Interpolation",
        "Run",
    ];
    let atoms = ["Ident", "Literal", "ImplicitSelector"];
    let Some((index, node)) = source
        .nodes
        .iter()
        .enumerate()
        .filter(|(_, node)| {
            (operations.contains(&node.kind.as_str()) || atoms.contains(&node.kind.as_str()))
                && node.span.contains(span)
        })
        .min_by_key(|(_, node)| node.span.len())
    else {
        return vec![];
    };
    let mut current = if atoms.contains(&node.kind.as_str()) {
        if let Some(parent) = node.parent.filter(|id| source.node(*id).kind == "Call") {
            return vec![source.node(parent).span];
        }
        node.parent
    } else {
        Some(NodeId(index))
    };
    let mut result = vec![];
    while let Some(id) = current {
        let node = source.node(id);
        if !operations.contains(&node.kind.as_str()) {
            break;
        }
        result.push(node.span);
        current = node.parent;
        if let Some(parent) = current {
            let node = source.node(parent);
            if node.kind == "Call" {
                result.push(node.span);
                break;
            }
            if matches!(node.kind.as_str(), "Interpolation" | "Run") {
                break;
            }
        }
    }
    result
}

struct Candidate {
    packet: Value,
    location: Option<(String, Span)>,
    purity_cause: Option<(String, Span)>,
    depends_on: Vec<Span>,
    invalid_target: Option<crate::semantics::Ref>,
    concat_operand_failure: bool,
    cascade: bool,
}

pub(crate) fn clean(
    project: &Project,
    errors: Vec<(Value, Option<Origin>, Option<Ref>)>,
) -> Vec<Value> {
    if errors.is_empty() {
        return vec![];
    }
    let mut patterns = vec![];
    for (_, emitted) in project.outputs.values() {
        for (start, end, origin) in &emitted.expansions {
            let Some(source) = project
                .sources
                .iter()
                .find(|s| s.path == Path::new(&origin.file))
            else {
                continue;
            };
            let text = &emitted.text[*start..*end];
            let original = source.slice(origin.span);
            if text != original
                && project
                    .sources
                    .iter()
                    .any(|source| text.contains(&source.prefix))
            {
                patterns.push((text, original));
                let mut inner = text;
                while enclosed(inner) {
                    inner = &inner[1..inner.len() - 1];
                    patterns.push((inner, original));
                }
            }
        }
    }
    patterns.sort_by_key(|(text, original)| (std::cmp::Reverse(text.len()), *text, *original));
    patterns.dedup();
    let patterns = patterns
        .into_iter()
        .map(|(text, original)| (text, tokens(text), original))
        .collect::<Vec<_>>();
    let mut resolver =
        project.resolver();
    let runtime = project.options.root.join("runtime");
    let mut items = vec![];
    for (mut error, origin, purity_cause) in errors {
        let mut msgs = messages(&error);
        let original = msgs.clone();
        if msgs.first().is_some_and(|message| {
            message.starts_with("Missing diverging call at the end of the procedure")
                && project.sources.iter().any(|source| {
                    message.contains(&source.prefix) && message.contains("switch_nonreturning_")
                })
        }) {
            msgs = vec!["This switch arm does not return a value. A valueless arm must call a nonreturning (-> !) procedure.".into()];
        }
        let mut invalid_target = None;
        let mut concat_operand_failure = false;
        let mut concat_declaration_failure = false;
        let mut exact = origin.as_ref().is_none_or(|origin| origin.exact);
        let kind = origin
            .as_ref()
            .map(|origin| origin.kind.as_str())
            .unwrap_or("");
        let mut location = span(&error);
        let mentions_concat = msgs.iter().any(|message| {
            message.contains("'concatenate'") || message.starts_with("Invalid declaration value")
        });
        if let Some((file, at)) = location.as_ref().filter(|_| mentions_concat) {
            let concatenation = project.sources.iter().any(|source| {
                source.path == Path::new(file)
                    && source.nodes.iter().any(|node| {
                        node.kind == "Binary" && node.op == "++" && node.span.contains(*at)
                    })
            });
            if concatenation {
                concat_operand_failure = msgs
                    .iter()
                    .any(|text| text.contains("'concatenate' expects a constant array or slice"));
                concat_declaration_failure = msgs
                    .iter()
                    .any(|text| text.starts_with("Invalid declaration value"));
                for message in &mut msgs {
                    *message = message
                        .replace("'concatenate' expects a constant array or slice",
                                 "Array concatenation ('++') requires compile-time constant arrays or slices")
                        .replace("'concatenate' expects constant values of the same element-type, got",
                                 "Array concatenation ('++') requires matching element types; got")
                        .replace("'concatenate' expects constant values of the same slice type, got",
                                 "Array concatenation ('++') requires matching slice types; got")
                        .replace("'concatenate' does not allow the use of 'field = value' to be concatenated together",
                                 "Array concatenation ('++') requires positional entries; named/indexed literal entries are not supported");
                }
            }
        }
        if let Some(name) = msgs
            .first()
            .and_then(|message| message.strip_prefix("Undeclared name: "))
        {
            if let Some((file, at)) = &location {
                if let Some(source) = project
                    .sources
                    .iter()
                    .find(|source| source.path == Path::new(file))
                {
                    if let Some(node) = source
                        .nodes
                        .iter()
                        .filter(|node| {
                            node.kind == "Ident"
                                && node.span.start >= at.start
                                && node.span.end <= at.end
                                && source.slice(node.span) == name
                        })
                        .min_by_key(|node| node.span.len())
                    {
                        error["pos"] = position(source, node.span);
                        location = span(&error);
                    }
                }
            }
        }
        if !exact && msgs.first().is_some_and(|message| {
            (message.starts_with("Cannot assign value ") || message.starts_with("Cannot convert "))
                && (message.contains(".literal_integer(") || message.contains(".literal_rational("))
        }) {
            let constructor = location.as_ref().and_then(|(path, at)| {
                let (file, source) = project.sources.iter().enumerate()
                    .find(|(_, source)| source.path == Path::new(path))?;
                source.nodes.iter().enumerate()
                    .filter(|(_, node)| node.kind == "Call" && node.span.contains(*at))
                    .filter(|(index, _)| resolver.logical_constructor(Ref {
                        file,
                        node: NodeId(*index),
                    }).is_some())
                    .min_by_key(|(_, node)| node.span.len())
                    .map(|(_, node)| (source, node.span))
            });
            if let Some((source, at)) = constructor {
                error["pos"] = position(source, at);
                location = span(&error);
            }
        }
        if let Some((file, at)) = &location {
            if let Some(source) = project
                .sources
                .iter()
                .find(|source| source.path == Path::new(file))
            {
                if let Some((member, detail)) = msgs.first().and_then(|message| {
                    let (_, field) = message.rsplit_once(" has no field '")?;
                    let (_, detail) = message.split_once(" of type ")?;
                    Some((field.strip_suffix('\'')?, detail.to_owned()))
                }) {
                    let selectors = source
                        .nodes
                        .iter()
                        .enumerate()
                        .filter_map(|(index, node)| {
                            if node.kind != "Selector"
                                || !(node.span.contains(*at) || at.contains(node.span))
                            {
                                return None;
                            }
                            let id = NodeId(index);
                            let field = source.role(id, "field")?;
                            (source.text(field) == member).then_some(id)
                        })
                        .collect::<Vec<_>>();
                    if let [selector] = selectors.as_slice() {
                        if let Some(base) = source.role(*selector, "expr") {
                            msgs[0] = format!("'{}' of type {detail}", source.text(base));
                            error["pos"] = position(source, source.node(*selector).span);
                            location = span(&error);
                        }
                    }
                }
            }
        }
        let support = location.as_ref().is_some_and(|(file, _)| {
            let path = Path::new(file);
            [
                "sequence/sequence.odin",
                "sets/compiler.odin",
                "pure/pure.odin",
                "eval/eval.odin",
                "optional/optional.odin",
            ]
            .iter()
            .any(|suffix| path == runtime.join(suffix))
        });
        let mut contract = None;
        if let Some((file, _)) = &location {
            let path = Path::new(file);
            if path.starts_with(runtime.join("format")) {
                if path == runtime.join("format/format.odin")
                    && msgs.iter().any(|m| {
                        m.contains("type_is_integer(T)") && !m.contains("Grouping requires")
                    })
                {
                    contract = Some("Integer grouping requires an integer value.");
                }
                if contract.is_none() {
                    contract = FORMAT_CONTRACTS
                        .iter()
                        .copied()
                        .find(|text| msgs.iter().any(|m| m.contains(text)));
                }
            } else if path == runtime.join("sets/sets.odin")
                && msgs.iter().any(|m| {
                    m.contains("type_is_valid_map_key(T)")
                        || m.contains("Invalid type of a key for a map")
                })
            {
                contract = Some("Set elements must be valid Odin map keys.");
            } else if path == runtime.join("numeric/numeric.odin") {
                contract = Some(
                    "Power ('**') requires integer or floating-point operands of matching types.",
                );
            } else if path == runtime.join("sequence/sequence.odin") {
                if msgs.iter().any(|m| m.contains("type_is_ordered(T)")) {
                    contract =
                        Some("Spread min/max requires ordered numeric, enum, or string elements.");
                } else {
                    contract = LOOP_CONTRACTS
                        .iter()
                        .copied()
                        .find(|text| msgs.iter().any(|m| m.contains(text)));
                }
            }
        }
        if support || contract.is_some() || kind == "membership-helper" {
            if let Some((file, offset)) = project
                .source_locations(&error, false)
                .into_iter()
                .find(|(file, _)| !project.sources[*file].path.starts_with(&runtime))
            {
                let source = &project.sources[file];
                let mut at = source
                    .nodes
                    .iter()
                    .filter(|node| node.span.start <= offset && offset < node.span.end)
                    .filter(|node| {
                        matches!(
                            node.kind.as_str(),
                            "Call" | "Binary" | "Index" | "Slice" | "OrPanic" | "Range"
                        )
                    })
                    .min_by_key(|node| node.span.len())
                    .map(|node| node.span)
                    .unwrap_or(Span::new(offset, offset + 1));
                if contract.is_some_and(|message| FORMAT_CONTRACTS.contains(&message)) {
                    if let Some(field) = source
                        .nodes
                        .iter()
                        .enumerate()
                        .filter(|(_, node)| node.kind == "Interpolation")
                        .flat_map(|(index, _)| source.group(NodeId(index), "args"))
                        .map(|field| source.node(*field).span)
                        .filter(|span| span.start <= offset && offset < span.end)
                        .min_by_key(|span| span.len())
                    {
                        at = field;
                    }
                } else if contract.is_some_and(|message| LOOP_CONTRACTS.contains(&message)) {
                    if let Some(annotation) = source
                        .nodes
                        .iter()
                        .enumerate()
                        .filter(|(_, node)| node.kind == "Range" && node.span.contains(at))
                        .min_by_key(|(_, node)| node.span.len())
                        .and_then(|(index, _)| source.role(NodeId(index), "index_type"))
                    {
                        at = source.node(annotation).span;
                    }
                }
                error["pos"] = position(source, at);
                location = Some((source.path.to_string_lossy().into(), at));
                exact = false;
                if let Some(contract) = contract {
                    msgs = vec![contract.into()];
                }
            }
        }
        if kind == "membership-helper" && !msgs.is_empty() {
            msgs[0] = format!("Membership requires comparable elements. {}", msgs[0]);
        }
        let generated_query = !exact
            && (msgs.iter().any(|m| {
                m == "Expected a constant boolean condition in ternary when expression"
                    || m == "Non-constant condition in 'when' statement"
                    || project
                        .sources
                        .iter()
                        .any(|source| m.contains(&source.prefix))
                        && (m.starts_with("Expected a type for 'type_is_")
                            || m.ends_with("' is not a type")
                            || m.contains("is not an expression but a type"))
                    || sequence_group(project, m, "element")
            }) || msgs.iter().any(|m| m.contains("invalid type"))
                && msgs.iter().any(|m| sequence_group(project, m, "normalize")));
        let mut inference = generated_query
            || (kind == "power-type"
                && msgs
                    .iter()
                    .any(|m| m.starts_with("Cannot determine polymorphic type")))
            || !exact && query(&msgs)
            || !exact
                && msgs
                    .iter()
                    .any(|m| m.starts_with("A typed numeric range requires "))
            || msgs.iter().any(|m| {
                project.sources.iter().any(|source| {
                    m.contains(&format!("to '${}", source.prefix))
                        || m.contains(&format!("of type '${}", source.prefix))
                })
            });
        if let Some((file, at)) = &location {
            if let Some((file_index, source)) = project
                .sources
                .iter()
                .enumerate()
                .find(|(_, s)| s.path == Path::new(file))
            {
                if !exact {
                    if let Some(power) = source
                        .nodes
                        .iter()
                        .filter(|node| {
                            node.kind == "Binary" && node.op == "**" && node.span.contains(*at)
                        })
                        .min_by_key(|node| node.span.len())
                    {
                        if msgs
                            .iter()
                            .any(|m| m.starts_with("Cannot determine polymorphic type"))
                        {
                            inference = true;
                        }
                        if let Some(mismatch) = msgs.iter().find_map(|m| {
                            m.strip_prefix("Mismatched types in ternary if expression, ")
                        }) {
                            msgs=vec![format!("Power ('**') requires matching operand types: {mismatch}."),"Convert an operand explicitly; only untyped numeric constants are coerced.".into()];
                            error["pos"] = position(source, power.span);
                            inference = false;
                        }
                    }
                }
                let normalization = msgs.iter().any(|m| sequence_group(project, m, "normalize"));
                if normalization && msgs.iter().any(|message| message.contains("invalid type")) {
                    inference = true;
                }
                for (index, operation) in source
                    .nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, node)| matches!(node.kind.as_str(), "Index" | "Slice"))
                {
                    let id = NodeId(index);
                    for (role, label) in if operation.kind == "Index" {
                        vec![("index", "index")]
                    } else {
                        vec![("low", "lower"), ("high", "upper")]
                    } {
                        let Some(bound) = source.role(id, role) else {
                            continue;
                        };
                        let bound_span = source.node(bound).span;
                        if !bound_span.contains(*at)
                            || !(normalization
                                || kind == "sequence-bound"
                                || msgs.iter().any(|m| m.starts_with("Cannot cast ")))
                        {
                            continue;
                        }
                        let typ = resolver.type_of(crate::semantics::Ref {
                            file: file_index,
                            node: bound,
                        });
                        let actual = match typ {
                            crate::semantics::Type::String { name, untyped } => Some(if untyped {
                                "untyped string".into()
                            } else {
                                name
                            }),
                            crate::semantics::Type::Scalar { name, untyped }
                                if name == "bool"
                                    || name.starts_with('f')
                                    || name.starts_with("complex")
                                    || name.starts_with("quaternion") =>
                            {
                                Some(if untyped {
                                    if name == "bool" {
                                        "untyped boolean"
                                    } else {
                                        "untyped float"
                                    }
                                    .into()
                                } else {
                                    name
                                })
                            }
                            _ => None,
                        };
                        let actual = actual.or_else(|| {
                            normalization
                                .then(|| {
                                    msgs.iter()
                                        .filter_map(|m| m.trim_start().strip_prefix("• "))
                                        .nth(1)
                                        .filter(|typ| *typ != "invalid type")
                                        .map(str::to_owned)
                                })
                                .flatten()
                        });
                        if let Some(actual) = actual {
                            let subject = if label == "index" {
                                "Sequence indices".into()
                            } else {
                                format!("The {label} slice bound")
                            };
                            let notes = msgs
                                .iter()
                                .filter(|m| m.contains("instantiated as"))
                                .cloned()
                                .collect::<Vec<_>>();
                            msgs = vec![format!(
                                "{subject} must have an integer type; '{}' has type '{actual}'.",
                                source.text(bound)
                            )];
                            msgs.extend(notes);
                            error["pos"] = position(source, bound_span);
                            inference = false;
                        }
                    }
                }
                // Keep native lazy checking: clarify type-as-value errors
                // only after Odin has actually diagnosed that operation.
                if msgs
                    .iter()
                    .any(|m| m.contains("is not an expression but a type"))
                {
                    for (index, node) in source
                        .nodes
                        .iter()
                        .enumerate()
                        .filter(|(_, n)| matches!(n.kind.as_str(), "Index" | "Slice"))
                    {
                        let Some(base) = source.role(NodeId(index), "expr") else {
                            continue;
                        };
                        let base_ref = crate::semantics::Ref {
                            file: file_index,
                            node: base,
                        };
                        if !source.node(base).span.contains(*at)
                            || !resolver.known_type_operand(base_ref)
                        {
                            continue;
                        }
                        let name = source.text(base);
                        let verb = if node.kind == "Index" {
                            "index"
                        } else {
                            "slice"
                        };
                        let value = if matches!(
                            resolver.type_of(base_ref),
                            crate::semantics::Type::Array { .. }
                        ) {
                            "an array value"
                        } else {
                            "a value"
                        };
                        msgs = vec![format!(
                            "Cannot {verb} '{name}': it names a type, not {value}."
                        )];
                        error["pos"] = position(source, source.node(base).span);
                        invalid_target = Some(crate::semantics::Ref {
                            file: file_index,
                            node: NodeId(index),
                        });
                        if let Some(symbol) = resolver
                            .symbol(base_ref)
                            .map(|index| &resolver.symbols[index])
                        {
                            let owner = &project.sources[symbol.reference.file];
                            let name_span = owner.node(symbol.name_node).span;
                            error["related"] = json!([{"pos":position(owner,name_span),"message":format!("'{}' is declared as a type here.",symbol.name)}]);
                            if let Some(value) = symbol
                                .value
                                .filter(|value| owner.node(*value).kind == "ArrayType")
                            {
                                msgs.push(format!(
                                    "To create a zero-initialized array variable, use '{}: {}'.",
                                    symbol.name,
                                    owner.text(value)
                                ));
                            }
                        }
                        break;
                    }
                }
            }
        }
        if let Some((file, at)) = span(&error) {
            if msgs.first().is_some_and(|m| {
                m.starts_with("Cannot convert ") || m.starts_with("Cannot assign ")
            }) {
                if let Some((file_index, source)) = project
                    .sources
                    .iter()
                    .enumerate()
                    .find(|(_, source)| source.path == Path::new(&file))
                {
                    for (index, _node) in source.nodes.iter().enumerate().filter(|(_, node)| {
                        node.kind == "Binary" && matches!(node.op.as_str(), "in" | "not_in")
                    }) {
                        let id = NodeId(index);
                        let Some(needle) = source.role(id, "left") else {
                            continue;
                        };
                        let Some(mut values) = source.role(id, "right") else {
                            continue;
                        };
                        while source.node(values).kind == "Paren" {
                            let Some(inner) = source.role(values, "expr") else {
                                break;
                            };
                            values = inner;
                        }
                        if source.node(values).kind != "Compound"
                            || source.role(values, "type").is_some()
                            || !source
                                .group(values, "elems")
                                .iter()
                                .any(|value| source.node(*value).span.start == at.start)
                        {
                            continue;
                        }
                        let target = msgs[0]
                            .split(" to '")
                            .nth(1)
                            .and_then(|tail| tail.split("' from ").next());
                        let typed = if !resolver.untyped_operand(crate::semantics::Ref {
                            file: file_index,
                            node: needle,
                        }) {
                            target
                                .map(|typ| format!(" (type '{typ}')"))
                                .unwrap_or_default()
                        } else {
                            String::new()
                        };
                        msgs.push(format!("In this membership test, each list element must be comparable with '{}'{}.",source.text(needle),typed));
                        let mut related = error["related"].as_array().cloned().unwrap_or_default();
                        related.push(json!({"pos":position(source,source.node(needle).span),"message":"The value being tested for membership is here."}));
                        error["related"] = json!(related);
                        break;
                    }
                }
            }
        }
        if let Some(first) = msgs.first() {
            if project
                .sources
                .iter()
                .any(|s| first.contains(&format!("procedure group '{}contains'", s.prefix)))
            {
                let types = msgs
                    .iter()
                    .skip(1)
                    .filter(|m| m.contains("Given argument types") || m.contains(" • "))
                    .map(|m| {
                        m.replace(" • value =", " • element =")
                            .replace(" • values =", " • container =")
                    })
                    .collect::<Vec<_>>();
                msgs = vec![
                    "Invalid operands for membership ('in' / 'not_in'): no matching operation."
                        .into(),
                ];
                msgs.extend(types);
            } else {
                for (name, op) in [
                    ("union", "|"),
                    ("intersection", "&"),
                    ("difference", "-"),
                    ("symmetric_difference", "~"),
                    ("equal", "== / !="),
                    ("add_assign", "+="),
                    ("sub_assign", "-="),
                    ("union_assign", "|="),
                    ("intersection_assign", "&="),
                    ("xor_assign", "~="),
                ] {
                    if project.sources.iter().any(|s| {
                        first.contains(&format!(
                            "procedure group '{}sets.compiler_{name}'",
                            s.prefix
                        ))
                    }) {
                        let types = msgs
                            .iter()
                            .skip(1)
                            .filter(|m| m.contains("Given argument types") || m.contains(" • "))
                            .cloned()
                            .collect::<Vec<_>>();
                        msgs = vec![format!(
                            "Invalid operands for '{op}': no matching operation."
                        )];
                        msgs.extend(types);
                        break;
                    }
                }
            }
        }
        let immutable = msgs
            .first()
            .is_some_and(|message| message.contains(" immutable storage '"));
        msgs.retain(|message| {
            if message.trim_start().starts_with("Called within")
                && (immutable || project.sources.iter().any(|source| message.contains(&source.prefix)))
            {
                return false;
            }
            !(message.contains("instantiated as")
                && (project
                    .sources
                    .iter()
                    .any(|source| message.contains(&source.prefix))
                    || support
                        && [
                            "compiler_",
                            "normalize_signed(",
                            "normalize_unsigned(",
                            "validate(",
                            "validate_format(",
                            "write(",
                            "emit(",
                        ]
                        .iter()
                        .any(|name| message.contains(name))))
        });
        for (text, pattern, original) in &patterns {
            for message in &mut msgs {
                *message = if message.contains(text) {
                    message.replace(text, original)
                } else {
                    replace_tokens(message, pattern, original)
                };
            }
        }
        let location = span(&error);
        let depends_on = location
            .as_ref()
            .and_then(|(file, at)| {
                project
                    .sources
                    .iter()
                    .find(|s| s.path == Path::new(file))
                    .map(|source| {
                        let contexts = inference_contexts(source, *at);
                        if contexts.is_empty() {
                            vec![*at]
                        } else {
                            contexts
                        }
                    })
            })
            .unwrap_or_default();
        let set_cascade = !exact
            && original.iter().any(|message| {
                project
                    .sources
                    .iter()
                    .any(|source| message.contains(&format!("{}sets.compiler_", source.prefix)))
            })
            && (msgs.iter().any(|m| m.contains("invalid type"))
                || msgs.first().is_some_and(|m| {
                    m.starts_with("Cannot assign value ") && m.contains("of type '$Set(")
                }));
        error["msgs"] = json!(msgs);
        let generated_return = !exact
            && original.iter().any(|message| {
                message.starts_with("Cannot assign value ")
                    && message.contains("in return statement")
                    && project
                        .sources
                        .iter()
                        .any(|source| message.contains(&format!("of type '${}", source.prefix)))
            });
        items.push(Candidate {
            packet: error,
            location,
            purity_cause: purity_cause.map(|site| {
                let source = &project.sources[site.file];
                (
                    source.path.to_string_lossy().into_owned(),
                    source.node(site.node).span,
                )
            }),
            depends_on,
            invalid_target,
            concat_operand_failure,
            cascade: inference
                || generated_return
                || set_cascade
                || concat_declaration_failure
                || kind == "range-type",
        });
    }
    let mut result = vec![];
    let mut seen = BTreeSet::new();
    for candidate in &items {
        if candidate.packet["msgs"][0].as_str()
            .is_some_and(|message| message.contains(": read-only slice access from '"))
            && items.iter().any(|other| {
                other.location == candidate.location
                    && other.packet["msgs"][0].as_str()
                        .is_some_and(|message| message.contains(" immutable storage '"))
            })
        {
            continue;
        }
        if candidate.concat_operand_failure
            && candidate.location.as_ref().is_some_and(|(file, span)| {
                items.iter().any(|other| {
                    !other.concat_operand_failure
                        && !other.cascade
                        && other.packet["type"] == "error"
                        && other.location.as_ref().is_some_and(|(other_file, at)| {
                            file == other_file && !at.is_empty() && span.contains(*at)
                        })
                })
            })
        {
            continue;
        }
        if items
            .iter()
            .filter_map(|item| item.invalid_target)
            .any(|target| {
                let source = &project.sources[target.file];
                candidate.location.as_ref().is_some_and(|(file, span)| {
                    source.path == Path::new(file)
                        && contextual_compound_failure(
                            source,
                            target.node,
                            *span,
                            &candidate.packet,
                        )
                })
            })
        {
            continue;
        }
        if candidate.cascade
            && items.iter().any(|other| {
                if other.cascade || other.packet["type"] != "error" {
                    return false;
                }
                let Some((file, _)) = &candidate.location else {
                    return false;
                };
                other
                    .location
                    .iter()
                    .chain(&other.purity_cause)
                    .any(|(other_file, at)| {
                        file == other_file
                            && !at.is_empty()
                            && candidate
                                .depends_on
                                .iter()
                                .any(|context| context.contains(*at))
                    })
            })
        {
            continue;
        }
        if seen.insert(candidate.packet.to_string()) {
            result.push(candidate.packet.clone());
        }
    }
    result
}
// An invalid array target can make Odin infer a compound's type from the
// array itself. Discard only errors supplied by that incorrect context.
fn contextual_compound_failure(source: &Source, target: NodeId, at: Span, error: &Value) -> bool {
    let mut target = target;
    while let Some(parent) = source.node(target).parent.filter(|parent| {
        matches!(
            source.node(*parent).kind.as_str(),
            "Paren" | "Selector" | "Index" | "Deref"
        )
    }) {
        target = parent;
    }
    let Some(assignment) = source
        .node(target)
        .parent
        .filter(|parent| source.node(*parent).kind == "Assignment")
    else {
        return false;
    };
    let lhs = source.group(assignment, "lhs");
    let rhs = source.group(assignment, "rhs");
    if lhs.len() != rhs.len() {
        return false;
    }
    let Some(value) = lhs
        .iter()
        .position(|node| *node == target)
        .map(|index| rhs[index])
    else {
        return false;
    };
    let node = source.node(value);
    if node.kind != "Compound"
        || source.role(value, "type").is_some()
        || node
            .spans
            .get("open")
            .is_some_and(|open| source.slice(*open) == "[")
        || !node.span.contains(at)
    {
        return false;
    }
    let message = error["msgs"][0].as_str().unwrap_or("");
    if at.start == node.span.start
        && (message.starts_with("Illegal compound literal,")
            || message.starts_with("Expected ")
                && (message.contains(" values for this array literal")
                    || message.contains(" values for this struct literal")))
    {
        return true;
    }
    message.starts_with("Cannot convert untyped value ")
        && source.group(value, "elems").iter().any(|element| {
            let element = source.role(*element, "value").unwrap_or(*element);
            let item = source.node(element);
            item.span.start == at.start
                && !(item.kind == "Compound" && source.role(element, "type").is_some())
        })
}

const LOOP_CONTRACTS: &[&str] = &[
    "Typed loop indices require an integer type.",
    "Typed index bindings are supported only for arrays and slices.",
    "The array's indices do not fit in the declared loop index type.",
];
const FORMAT_CONTRACTS:&[&str]=&[
    "Grouping requires a decimal numeric format.",
    "Interpolation width must have an integer type.",
    "Rational g/G formatting requires explicit precision, e.g. :.6g; omit the verb for an exact fraction.",
    "Rational formatting does not support this verb.",
    "Integer formatting does not support this verb.",
    "Boolean formatting requires v, w, T, or t.",
    "Precision is not supported for boolean values.",
    "String formatting requires v, w, T, s, q, x, or X.",
    "Float formatting requires v, w, T, f/F, e/E, g/G, h/H, or %.",
    "Complex/quaternion formatting requires v, w, T, f/F, e/E, g/G, or h/H.",
    "Precision is supported for scalar numbers and strings, not aggregate values.",
    "Decimal formatting requires a numeric value.",
    "Byte precision requires s, q, w, x, or X.",
    "_string supports byte/rune sequences, numeric values, Integer, Rational, booleans, enums, strings and cstrings",
];
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn numeric_tokens_keep_native_boundaries() {
        let parsed = tokens("12345 -12 .75 1_000.25e-12 0x1e+3 0h3f800000 2i 1..=2");
        assert_eq!(parsed, vec!["12345", "-", "12", ".75", "1_000.25e-12", "0x1e", "+", "3", "0h3f800000", "2i", "1", ".", ".", "=", "2"]);
    }
    #[test]
    fn recorded_token_restoration_preserves_literals_and_boundaries() {
        let raw = "__thor_helper(a, \"a  b\")";
        let pattern = tokens(raw);
        assert_eq!(
            replace_tokens("value '__thor_helper ( a , \"a  b\" )'", &pattern, "[a]"),
            "value '[a]'"
        );
        assert_eq!(
            replace_tokens("x__thor_helper(a, \"a  b\")", &pattern, "[a]"),
            "x__thor_helper(a, \"a  b\")"
        );
        assert_eq!(
            replace_tokens("__thor_helper(a, \"a b\")", &pattern, "[a]"),
            "__thor_helper(a, \"a b\")"
        );
        assert_eq!(
            tokens("a/*outer/*nested*/end*/+ // line\n b"),
            vec!["a", "+", "b"]
        );
    }
}
