//! Disposable, compiler-revisioned SDK syntax cache. It stores the normalized
//! arena directly; raw parser packets remain the portable fallback. No semantic
//! answers, allocator state or project-specific bindings are persisted.
use super::*;
use std::sync::OnceLock;

const MAGIC: &[u8] = b"thor-syntax-1\0";

pub(crate) fn revision() -> &'static str {
    static REVISION: OnceLock<String> = OnceLock::new();
    REVISION.get_or_init(|| sha256(concat!(
        include_str!("ast.rs"), include_str!("astcache.rs"),
        include_str!("nodefields.rs"), include_str!("kind.rs"),
        include_str!("packed.rs"), include_str!("aststorage.rs")
    ).as_bytes()))
}

#[derive(Default)]
struct Writer<'a> {
    bytes: Vec<u8>,
    strings: HashMap<&'a str, usize>,
}
impl<'a> Writer<'a> {
    fn uint(&mut self, mut n: usize) {
        while n >= 128 { self.bytes.push((n as u8) | 128); n >>= 7; }
        self.bytes.push(n as u8);
    }
    fn blob(&mut self, bytes: &[u8]) {
        self.uint(bytes.len()); self.bytes.extend_from_slice(bytes);
    }
    fn text(&mut self, text: &'a str) {
        if let Some(id) = self.strings.get(text).copied() { self.uint(id * 2); }
        else {
            let id = self.strings.len(); self.strings.insert(text, id);
            self.uint(id * 2 + 1); self.blob(text.as_bytes());
        }
    }
    fn span(&mut self, span: Span) { self.uint(span.start); self.uint(span.end); }
    fn value(&mut self, value: &'a Value) {
        match value {
            Value::Null => self.bytes.push(0),
            Value::Bool(false) => self.bytes.push(1),
            Value::Bool(true) => self.bytes.push(2),
            Value::Number(n) => { self.bytes.push(3); self.blob(n.to_string().as_bytes()); }
            Value::String(s) => { self.bytes.push(4); self.text(s); }
            Value::Array(xs) => {
                self.bytes.push(5); self.uint(xs.len());
                for x in xs { self.value(x); }
            }
            Value::Object(xs) => {
                self.bytes.push(6); self.uint(xs.len());
                for (k, v) in xs { self.text(k); self.value(v); }
            }
        }
    }
    fn optional(&mut self, value: &'a Option<Value>) {
        self.uint(usize::from(value.is_some()));
        if let Some(value) = value { self.value(value); }
    }
}

struct Reader<'a> { bytes: &'a [u8], strings: Vec<Arc<str>> }
impl<'a> Reader<'a> {
    fn byte(&mut self) -> Option<u8> {
        let (&byte, rest) = self.bytes.split_first()?; self.bytes = rest; Some(byte)
    }
    fn uint(&mut self) -> Option<usize> {
        let mut n = 0usize;
        for shift in (0..usize::BITS).step_by(7) {
            let byte = self.byte()?;
            let part = usize::from(byte & 127);
            if part > usize::MAX >> shift { return None; }
            n |= part << shift;
            if byte < 128 { return Some(n); }
        }
        None
    }
    fn length(&mut self) -> Option<usize> {
        let n = self.uint()?;
        (n <= self.bytes.len()).then_some(n)
    }
    fn blob(&mut self) -> Option<&'a [u8]> {
        let n = self.length()?;
        let (bytes, rest) = self.bytes.split_at(n); self.bytes = rest; Some(bytes)
    }
    fn text(&mut self) -> Option<Arc<str>> {
        let tag = self.uint()?; let id = tag / 2;
        if tag & 1 == 0 { return self.strings.get(id).cloned(); }
        if id != self.strings.len() { return None; }
        let text: Arc<str> = std::str::from_utf8(self.blob()?).ok()?.into();
        self.strings.push(text.clone()); Some(text)
    }
    fn span(&mut self, raw: &str) -> Option<Span> {
        let span = Span::new(self.uint()?, self.uint()?);
        span.is_valid_for(raw).then_some(span)
    }
    fn id(&mut self, count: usize) -> Option<NodeId> {
        let n = self.uint()?; (n < count).then_some(NodeId(n))
    }
    fn value(&mut self, depth: usize) -> Option<Value> {
        if depth > 128 { return None; }
        Some(match self.byte()? {
            0 => Value::Null, 1 => Value::Bool(false), 2 => Value::Bool(true),
            3 => Value::Number(serde_json::from_slice(self.blob()?).ok()?),
            4 => Value::String(self.text()?.to_string()),
            5 => {
                let n = self.length()?; let mut xs = Vec::with_capacity(n);
                for _ in 0..n { xs.push(self.value(depth + 1)?); } Value::Array(xs)
            }
            6 => {
                let n = self.length()?; let mut xs = serde_json::Map::new();
                for _ in 0..n {
                    let key = self.text()?.to_string(); let value = self.value(depth + 1)?;
                    if xs.insert(key, value).is_some() { return None; }
                }
                Value::Object(xs)
            }
            _ => return None,
        })
    }
    fn optional(&mut self) -> Option<Option<Value>> {
        match self.byte()? { 0 => Some(None), 1 => Some(Some(self.value(0)?)), _ => None }
    }
}

pub(crate) fn write(source: &Source, key: &str) -> Vec<u8> {
    let _phase = crate::profile::span("parser.syntax_encode");
    let mut out = Writer::default();
    out.uint(source.nodes.len());
    for node in &source.nodes {
        out.text(node.kind.as_str()); out.span(node.span);
        out.uint(node.parent.map_or(0, |id| id.0 + 1));
        out.text(&node.op); out.text(source.names.get(node.name));
        out.uint(usize::from(node.mutable)); out.uint(usize::from(node.immutable));
        out.uint(node.roles.iter().count());
        for (name, id) in node.roles.iter() { out.text(name); out.uint(id.0); }
        out.uint(node.spans.iter().count());
        for (name, span) in node.spans.iter() { out.text(name); out.span(*span); }
        out.uint(node.lists.iter().count());
        for (name, ids) in node.lists.iter() {
            out.text(name); out.uint(ids.len()); for id in ids { out.uint(id.0); }
        }
        out.value(&node.data);
    }
    let migration_info = source.migration_identifiers.as_ref().map(|spans| {
        let mut info = source.info.clone();
        info["migration_identifiers"] = serde_json::json!(spans.iter()
            .map(|span| [span.start,span.end]).collect::<Vec<_>>());
        info
    });
    out.value(migration_info.as_ref().unwrap_or(&source.info)); out.text(&source.prefix);
    out.uint(usize::from(source.excluded)); out.blob(&source.protocol_bytes());
    out.uint(source.protocol_overrides.len());
    for (id, fields) in &source.protocol_overrides {
        out.uint(id.0); out.optional(&fields.roles); out.optional(&fields.lists);
    }
    let mut packet = Vec::with_capacity(MAGIC.len() + 128 + out.bytes.len());
    packet.extend_from_slice(MAGIC); packet.extend_from_slice(key.as_bytes());
    packet.extend_from_slice(sha256(&out.bytes).as_bytes()); packet.extend(out.bytes); packet
}

pub(crate) fn read(path: PathBuf, raw: String, thor: bool, key: &str, packet: &[u8]) -> Option<Source> {
    let _phase = crate::profile::span("parser.syntax_decode");
    let rest = packet.strip_prefix(MAGIC)?.strip_prefix(key.as_bytes())?;
    let (digest, payload) = rest.split_at_checked(64)?;
    if digest != sha256(payload).as_bytes() { return None; }
    let mut input = Reader { bytes: payload, strings: vec![] };
    let count = input.length()?;
    if count == 0 { return None; }
    let mut names = Names::default(); let mut nodes: Vec<Node> = Vec::with_capacity(count);
    let mut fields = storage::FieldsBuilder::default();
    for i in 0..count {
        let kind = Kind::from(input.text()?.as_ref()); let span = input.span(&raw)?;
        let parent = input.uint()?;
        if (i == 0 && parent != 0) || (i > 0 && (parent == 0 || parent > i)) { return None; }
        let parent = parent.checked_sub(1).map(NodeId);
        let op = input.text()?.to_string(); let name = names.intern(&input.text()?);
        let mutable = match input.byte()? { 0 => false, 1 => true, _ => return None };
        let immutable = match input.byte()? { 0 => false, 1 => true, _ => return None };
        let role_count = input.length()?;
        for _ in 0..role_count { fields.roles.push((input.text()?, input.id(count)?)); }
        let span_count = input.length()?;
        for _ in 0..span_count { fields.spans.push((input.text()?, input.span(&raw)?)); }
        let list_count = input.length()?;
        for _ in 0..list_count {
            let name = input.text()?;
            let item_count = input.length()?;
            let start = fields.items.len();
            for _ in 0..item_count { fields.items.push(input.id(count)?); }
            fields.lists.push((name, start..fields.items.len()));
        }
        fields.counts.push([role_count, span_count, list_count]);
        let mut data = input.value(0)?;
        compact_defaults(kind.as_str(), data.as_object_mut()?);
        nodes.push(Node { kind, span, parent, children: PackedSlice::default(),
            roles: NodeFields::default(), spans: NodeFields::default(),
            lists: NodeFields::default(), op, name, mutable, immutable, data });
    }
    let info = input.value(0)?;
    if !info.is_object() { return None; }
    let prefix = input.text()?.to_string();
    let excluded = match input.byte()? { 0 => false, 1 => true, _ => return None };
    let protocol: Arc<[u8]> = input.blob()?.into();
    let n = input.length()?; let mut protocol_overrides = HashMap::with_capacity(n);
    for _ in 0..n {
        let id = input.id(count)?;
        let fields = WireOverrides { roles: input.optional()?, lists: input.optional()? };
        if protocol_overrides.insert(id, fields).is_some() { return None; }
    }
    if !input.bytes.is_empty() { return None; }
    fields.finish(&mut nodes);
    storage::pack_children(&mut nodes);
    let by_span = storage::pack_spans(&nodes);
    let mut edits = vec![];
    for row in info["edits"].as_array().into_iter().flatten() {
        let span = Span::new(row["start"].as_u64()? as usize, row["end"].as_u64()? as usize);
        if !span.is_valid_for(&raw) { return None; }
        edits.push(Edit { span, text: row["text"].as_str().unwrap_or("").into() });
    }
    let selections = serde_json::from_value(info["selections"].clone()).unwrap_or_default();
    let comments = info["comments"].as_array().into_iter().flatten()
        .filter_map(Span::from_wire).collect();
    let mut line_starts = vec![0];
    line_starts.extend(raw.bytes().enumerate().filter_map(|(i, b)| (b == b'\n').then_some(i + 1)));
    let mut traversal = Vec::with_capacity(count); let mut subtree = vec![(0, 0); count];
    let mut stack = vec![(NodeId(0), false)];
    while let Some((id, leaving)) = stack.pop() {
        if leaving { subtree[id.0].1 = traversal.len(); continue; }
        subtree[id.0].0 = traversal.len(); traversal.push(id); stack.push((id, true));
        stack.extend(nodes[id.0].children.iter().rev().map(|id| (*id, false)));
    }
    Some(Source { syntax: Arc::new(SourceSyntax { path, raw, nodes, names, selections, comments,
        info, prefix, excluded, thor, protocol, protocol_compressed: false, migration_identifiers: None, protocol_overrides, line_starts, by_span, traversal, subtree }),
        edits, shared_native: false, collection_roots: BTreeMap::new(), support_collection:"thor".into() })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn normalized_cache_preserves_protocol_and_rejects_corruption_and_wrong_source() {
        let source = Source::from_frontend("/source.thor".into(), "é 7".into(), serde_json::json!({
            "nodes":[{"kind":"File","start":0,"end":4,"parent":-1},
                {"kind":"Literal","op":"integer","start":3,"end":4,"parent":0}],
            "features":[],"selections":[],"edits":[],"extension":[-2,1.25,"😀",true,null]
        }), true).unwrap();
        let key = sha256(b"source identity"); let bytes = write(&source, &key);
        let restored = read(source.path.clone(), source.raw.clone(), true, &key, &bytes).unwrap();
        if restored.frontend_info() != source.frontend_info() || restored.text(NodeId(1)) != "7"
            || restored.position(3) != source.position(3) || restored.nodes.len() != source.nodes.len() {
            panic!("normalized cache changed syntax or protocol");
        }
        for end in 0..bytes.len() {
            if read(source.path.clone(), source.raw.clone(), true, &key, &bytes[..end]).is_some() {
                panic!("truncated syntax cache accepted");
            }
        }
        let mut corrupt = bytes.clone(); *corrupt.last_mut().unwrap() ^= 1;
        if read(source.path.clone(), source.raw.clone(), true, &key, &corrupt).is_some()
            || read(source.path.clone(), source.raw.clone(), true, &sha256(b"other"), &bytes).is_some() {
            panic!("changed syntax cache accepted");
        }
        // Structural validation is independent of the checksum.
        let mut invalid = vec![255; 40];
        let mut input = Reader { bytes: &invalid, strings: vec![] };
        if input.uint().is_some() { panic!("overflowing cache integer accepted"); }
        invalid = vec![5,255,255,255,255,255,255,255,255,255,127];
        let mut input = Reader { bytes: &invalid, strings: vec![] };
        if input.value(0).is_some() { panic!("unbounded cache allocation accepted"); }
    }
}
