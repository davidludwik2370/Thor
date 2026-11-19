//! Arena AST and interned names. Node links are stable integers, never owning graphs.
use crate::kind::Kind;
use crate::{
    diagnostic::{Error, Result},
    digest::sha256,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    path::PathBuf,
    sync::Arc,
};
#[path = "nodefields.rs"]
mod nodefields;
pub use nodefields::NodeFields;
#[path = "packed.rs"]
mod packed;
pub use packed::PackedSlice;
#[path = "aststorage.rs"]
mod storage;
#[path = "astcache.rs"]
pub(crate) mod cache;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct NodeId(pub usize);
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct NameId(pub u32);
#[derive(Clone, Default, Debug)]
pub struct Names {
    values: Vec<String>,
    ids: HashMap<String, NameId>,
}
impl Names {
    pub fn intern(&mut self, name: &str) -> NameId {
        if let Some(id) = self.ids.get(name) {
            return *id;
        }
        let id = NameId(self.values.len() as u32);
        self.values.push(name.to_owned());
        self.ids.insert(name.to_owned(), id);
        id
    }
    pub fn get(&self, id: NameId) -> &str {
        &self.values[id.0 as usize]
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Span {
    pub start: usize,
    pub end: usize,
}
impl Span {
    pub fn new(start: usize, end: usize) -> Self {
        Self { start, end }
    }
    pub fn contains(self, other: Self) -> bool {
        self.start <= other.start && other.end <= self.end
    }
    pub fn from_wire(value: &Value) -> Option<Self> {
        let xs = value.as_array()?;
        Some(Self::new(
            xs.first()?.as_u64()? as usize,
            xs.get(1)?.as_u64()? as usize,
        ))
    }
    pub fn len(self) -> usize {
        self.end - self.start
    }
    pub fn is_empty(self) -> bool {
        self.start == self.end
    }
    fn is_valid_for(self, raw: &str) -> bool {
        self.start <= self.end
            && self.end <= raw.len()
            && raw.is_char_boundary(self.start)
            && raw.is_char_boundary(self.end)
    }
}

#[derive(Clone, Debug)]
pub struct Node {
    pub kind: Kind,
    pub span: Span,
    pub parent: Option<NodeId>,
    pub children: PackedSlice<NodeId>,
    pub roles: NodeFields<NodeId>,
    pub spans: NodeFields<Span>,
    pub lists: NodeFields<PackedSlice<NodeId>>,
    pub op: String,
    pub name: NameId,
    pub mutable: bool,
    pub immutable: bool,
    /// Extension metadata. Core syntax identity is stored in typed fields.
    pub data: Value,
}

// Default wire flags do not need per-node tree-map entries. Keep conventions
// on signatures/foreign blocks, where an empty string has contextual meaning.
fn compact_defaults(kind: &str, metadata: &mut serde_json::Map<String, Value>) {
    for key in ["using_field", "variadic", "diverging", "optional_ok", "optional_allocator_error"] {
        if metadata.get(key) == Some(&Value::Bool(false)) { metadata.remove(key); }
    }
    for key in ["bounds_check", "type_assert_check"] {
        if metadata.get(key).and_then(Value::as_i64) == Some(0) { metadata.remove(key); }
    }
    if !matches!(kind, "ProcedureType" | "ForeignBlock")
        && metadata.get("calling_convention").and_then(Value::as_str) == Some("") {
        metadata.remove("calling_convention");
    }
}

#[derive(Default, Deserialize)]
struct WireNode {
    kind: Option<Kind>,
    start: Option<usize>,
    end: Option<usize>,
    parent: Option<i64>,
    op: Option<String>,
    name: Option<String>,
    mutable: Option<bool>,
    roles: Option<Value>,
    lists: Option<Value>,
    #[serde(flatten)]
    data: serde_json::Map<String, Value>,
}
#[derive(Deserialize)]
struct WireSource {
    nodes: Option<Vec<WireNode>>,
    #[serde(flatten)]
    info: serde_json::Map<String, Value>,
}
#[derive(Debug)]
struct WireOverrides {
    roles: Option<Value>,
    lists: Option<Value>,
}

#[derive(Clone, Debug)]
pub struct Edit {
    pub span: Span,
    pub text: String,
}
#[derive(Clone, Debug, Deserialize)]
pub struct Selection {
    pub start: usize,
    pub end: usize,
    pub wildcard: bool,
    pub package_name: String,
    pub path: String,
    pub names: Vec<String>,
    pub positions: Vec<Value>,
}

#[derive(Clone, Debug)]
pub struct Source {
    syntax: Arc<SourceSyntax>,
    pub edits: Vec<Edit>,
    pub shared_native: bool,
    pub collection_roots: BTreeMap<String, PathBuf>,
    pub support_collection: String,
}
/// Parsed syntax is immutable. Helper projects share it while retaining their
/// own edits, package views and collection bindings.
#[derive(Debug)]
pub struct SourceSyntax {
    pub path: PathBuf,
    pub raw: String,
    pub nodes: Vec<Node>,
    pub names: Names,
    pub selections: Vec<Selection>,
    pub comments: Vec<Span>,
    pub info: Value,
    pub prefix: String,
    pub excluded: bool,
    pub thor: bool,
    protocol: Arc<[u8]>,
    protocol_compressed: bool,
    pub(crate) migration_identifiers: Option<Arc<[Span]>>,
    protocol_overrides: HashMap<NodeId, WireOverrides>,
    line_starts: Vec<usize>,
    by_span: HashMap<Span, PackedSlice<NodeId>>,
    traversal: Vec<NodeId>,
    subtree: Vec<(usize, usize)>,
}
impl std::ops::Deref for Source {
    type Target = SourceSyntax;
    fn deref(&self) -> &SourceSyntax { &self.syntax }
}
impl Source {
    pub(crate) fn shares_syntax(&self, other: &Source) -> bool {
        Arc::ptr_eq(&self.syntax, &other.syntax)
    }
    pub fn from_frontend(path: PathBuf, raw: String, info: Value, thor: bool) -> Result<Self> {
        Self::from_frontend_bytes(path, raw, &serde_json::to_vec(&info)?, thor)
    }
    /// Migration keeps many independent packages resident for name resolution.
    /// Preserve the complete wire output in compressed storage and omit only
    /// default extension flags whose semantic queries already default to zero.
    pub(crate) fn from_migration_bytes(path: PathBuf, raw: String, packet: &[u8]) -> Result<Self> {
        use std::io::Write;
        let mut source = Self::from_frontend_bytes(path, raw, packet, false)?;
        let syntax = Arc::get_mut(&mut source.syntax).expect("new migration syntax is unshared");
        if let Some(spans) = syntax.info.as_object_mut().expect("source metadata").remove("migration_identifiers") {
            syntax.migration_identifiers = Some(spans.as_array().into_iter().flatten()
                .filter_map(Span::from_wire).collect::<Vec<_>>().into());
        }
        let mut encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        encoder.write_all(&syntax.protocol)?;
        syntax.protocol = encoder.finish()?.into();
        syntax.protocol_compressed = true;
        Ok(source)
    }
    fn protocol_bytes(&self) -> std::borrow::Cow<'_, [u8]> {
        if !self.protocol_compressed { return std::borrow::Cow::Borrowed(&self.protocol); }
        use std::io::Read;
        let mut raw = Vec::new();
        let result = flate2::read::GzDecoder::new(&*self.protocol).read_to_end(&mut raw);
        result.expect("validated internal migration protocol");
        std::borrow::Cow::Owned(raw)
    }
    pub(crate) fn from_frontend_bytes(
        path: PathBuf, raw: String, packet: &[u8], thor: bool,
    ) -> Result<Self> {
        let wire: WireSource = {
            let _phase = crate::profile::span("parser.decode");
            serde_json::from_slice(packet)?
        };
        let _phase = crate::profile::span("parser.normalize");
        let mut info = Value::Object(wire.info);
        let mut rows = wire.nodes
            .ok_or_else(|| Error::new("frontend omitted nodes"))?;
        if rows.is_empty() {
            rows.push(WireNode {
                kind: Some(Kind::File), start: Some(0), end: Some(raw.len()), parent: Some(-1),
                ..WireNode::default()
            });
        }
        let mut names = Names::default();
        let mut nodes = Vec::with_capacity(rows.len());
        for (i, mut row) in rows.into_iter().enumerate() {
            let start = row.start.ok_or_else(|| Error::new("invalid AST start"))?;
            let end = row.end.ok_or_else(|| Error::new("invalid AST end"))?;
            let span = Span::new(start, end);
            if !span.is_valid_for(&raw) {
                return Err(Error::new(format!("invalid AST byte span {start}..{end}")));
            }
            let parent = row.parent.unwrap_or(-1);
            if parent >= i as i64 {
                return Err(Error::new("invalid AST parent order"));
            }
            for (key, value) in [("roles", row.roles.take()), ("lists", row.lists.take())] {
                if let Some(value) = value {
                    if !value.is_object() && !value.is_null() {
                        return Err(Error::new(format!("invalid AST {key}")));
                    }
                    row.data.insert(key.into(), value);
                }
            }
            nodes.push(Node {
                kind: row.kind.unwrap_or(Kind::Other),
                span,
                parent: if parent < 0 {
                    None
                } else {
                    Some(NodeId(parent as usize))
                },
                children: PackedSlice::default(),
                roles: NodeFields::default(),
                spans: NodeFields::default(),
                lists: NodeFields::default(),
                op: row.op.unwrap_or_default(),
                name: names.intern(row.name.as_deref().unwrap_or("")),
                mutable: row.mutable.unwrap_or(false),
                immutable: false,
                data: Value::Object(row.data),
            });
        }
        storage::pack_children(&mut nodes);
        let by_span = storage::pack_spans(&nodes);
        let mut changed_roles = vec![false; nodes.len()];
        let mut changed_lists = vec![false; nodes.len()];
        for feature in info["features"].as_array().into_iter().flatten() {
            let start = feature["start"].as_u64().unwrap_or(0) as usize;
            let end = feature["end"].as_u64().unwrap_or(0) as usize;
            let kind = feature["kind"].as_str().unwrap_or("");
            let candidates = by_span
                .get(&Span::new(start, end))
                .cloned()
                .unwrap_or_default();
            if let Some(id) = candidates.into_iter().find(|id| {
                let k = nodes[id.0].kind.as_str();
                if kind == "ComprehensionBody" {
                    k == "Procedure"
                } else {
                    matches!(k, "Call" | "Switch" | "Procedure")
                }
            }) {
                if kind == "PureFunction" {
                    nodes[id.0].data["pure"] = Value::Bool(true);
                } else {
                    for (key, value) in feature.as_object().into_iter().flatten() {
                        if matches!(key.as_str(), "roles" | "lists") {
                            if !value.is_object() && !value.is_null() {
                                return Err(Error::new(format!("invalid AST feature {key}")));
                            }
                            if key == "roles" { changed_roles[id.0] = true; }
                            else { changed_lists[id.0] = true; }
                        }
                        nodes[id.0].data[key] = value.clone();
                    }
                    nodes[id.0].kind = kind.into();
                    if kind == "Run" {
                        if let Some(expr) = nodes[id.0].data["lists"]["args"]
                            .as_array()
                            .and_then(|args| args.first())
                            .cloned()
                        {
                            nodes[id.0].data["roles"]["expr"] = expr;
                            changed_roles[id.0] = true;
                        }
                    }
                    if matches!(kind, "SwitchExpr" | "CompileSwitchExpr" | "CompileSwitch") {
                        nodes[id.0].data["roles"]["selector"] = feature["selector"].clone();
                        changed_roles[id.0] = true;
                    }
                }
            }
        }
        let immutables: std::collections::HashSet<_> = info["immutables"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Span::from_wire)
            .map(|span| span.start)
            .collect();
        let separators: HashMap<_, _> = info["declaration_separators"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|row| Some((row[0].as_u64()?, [row[1].as_u64()?, row[2].as_u64()?])))
            .collect();
        for (index, node) in nodes.iter_mut().enumerate() {
            if node.kind == "Declaration" {
                node.immutable = immutables.contains(&node.span.start);
                node.data["immutable"] = Value::Bool(node.immutable);
                let first = node.data["lists"]["names"]
                    .as_array()
                    .and_then(|names| names.first())
                    .and_then(Span::from_wire)
                    .map(|span| span.start)
                    .unwrap_or(node.span.start);
                if let Some(separator) = separators.get(&(first as u64)) {
                    node.data["roles"]["separator"] = serde_json::json!(separator);
                    changed_roles[index] = true;
                }
            }
        }
        // Features can replace roles/lists. Resolve them only after merging,
        // distinguishing same-span wrappers using native ancestry.
        let mut field_names = HashSet::<Arc<str>>::new();
        for i in 0..nodes.len() {
            let row = &nodes[i].data;
            let mut resolved_roles = Vec::new();
            let mut role_spans = Vec::new();
            let mut resolved_lists = Vec::new();
            if let Some(roles) = row["roles"].as_object() {
                resolved_roles.reserve(roles.len());
                role_spans.reserve(roles.len());
                for (name, value) in roles {
                    if let Some(span) = Span::from_wire(value) {
                        if !span.is_valid_for(&raw) {
                            return Err(Error::new("invalid AST role span"));
                        }
                        let key = intern_field(&mut field_names, name);
                        role_spans.push((key.clone(), span));
                        if let Some(id) = resolve_span(&nodes, &by_span, span, NodeId(i)) {
                            resolved_roles.push((key, id));
                        }
                    }
                }
            }
            if let Some(lists) = row["lists"].as_object() {
                resolved_lists.reserve(lists.len());
                for (name, value) in lists {
                    let mut ids = vec![];
                    for value in value.as_array().into_iter().flatten() {
                        if let Some(span) = Span::from_wire(value) {
                            if !span.is_valid_for(&raw) {
                                return Err(Error::new("invalid AST list span"));
                            }
                            if let Some(id) = resolve_span(&nodes, &by_span, span, NodeId(i)) {
                                ids.push(id);
                            }
                        }
                    }
                    resolved_lists.push((intern_field(&mut field_names, name), PackedSlice::from_vec(ids)));
                }
            }
            nodes[i].roles = NodeFields::from_entries(resolved_roles);
            nodes[i].spans = NodeFields::from_entries(role_spans);
            nodes[i].lists = NodeFields::from_entries(resolved_lists);
        }
        for feature in info["features"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|f| f["kind"] == "Comprehension")
        {
            let span = Span::new(
                feature["start"].as_u64().unwrap_or(0) as usize,
                feature["end"].as_u64().unwrap_or(0) as usize,
            );
            let Some(id) = by_span
                .get(&span)
                .into_iter()
                .flatten()
                .find(|id| nodes[id.0].kind == "Comprehension")
                .copied()
            else {
                return Err(Error::new("comprehension node omitted"));
            };
            let descendants: Vec<_> = (0..nodes.len())
                .map(NodeId)
                .filter(|child| is_descendant(&nodes, *child, id))
                .collect();
            let Some(loop_id) = descendants
                .iter()
                .find(|child| nodes[child.0].kind == "Range")
                .copied()
            else {
                return Err(Error::new("comprehension loop omitted"));
            };
            nodes[id.0].roles.insert("loop".into(), loop_id);
            if let Some(expr) = nodes[loop_id.0].roles.get("expr").copied() {
                nodes[id.0].roles.insert("expr".into(), expr);
            }
            for (role, key) in [
                ("type", "container_type"),
                ("output", "output"),
                ("key", "key"),
                ("cond", "condition"),
            ] {
                if let Some(span) = Span::from_wire(&feature[key]) {
                    if let Some(child) = descendants
                        .iter()
                        .find(|child| {
                            nodes[child.0].span == span
                                && !matches!(
                                    nodes[child.0].kind.as_str(),
                                    "Return"
                                        | "Block"
                                        | "If"
                                        | "Range"
                                        | "ComprehensionBody"
                                        | "ProcedureType"
                                        | "Fields"
                                )
                        })
                        .copied()
                    {
                        nodes[id.0].roles.insert(role.into(), child);
                    }
                }
            }
            nodes[id.0].data["role_nodes"] = serde_json::json!(nodes[id.0]
                .roles
                .iter()
                .filter(|(name, _)| matches!(
                    *name,
                    "loop" | "expr" | "type" | "output" | "key" | "cond"
                ))
                .map(|(name, id)| (name.to_owned(), id.0))
                .collect::<BTreeMap<_, _>>());
            nodes[loop_id.0].data["comprehension"] = serde_json::json!(id.0);
        }
        let mut protocol_overrides = HashMap::new();
        for (index, node) in nodes.iter_mut().enumerate() {
            let data = node.data.as_object_mut().expect("node extension metadata");
            let roles = data.remove("roles");
            let lists = data.remove("lists");
            compact_defaults(node.kind.as_str(), data);
            if changed_roles[index] || changed_lists[index] {
                protocol_overrides.insert(NodeId(index), WireOverrides {
                    roles: changed_roles[index].then_some(roles).flatten(),
                    lists: changed_lists[index].then_some(lists).flatten(),
                });
            }
        }
        // Keep core wire fields as compact bytes for explicit protocol output.
        // Semantic passes use the typed identity and extension metadata above.
        info["nodes"] = Value::Null;
        let mut edits = vec![];
        for value in info["edits"].as_array().into_iter().flatten() {
            let start = value["start"]
                .as_u64()
                .ok_or_else(|| Error::new("invalid edit start"))? as usize;
            let end = value["end"]
                .as_u64()
                .ok_or_else(|| Error::new("invalid edit end"))? as usize;
            if !Span::new(start, end).is_valid_for(&raw) {
                return Err(Error::new("invalid edit span"));
            }
            edits.push(Edit {
                span: Span::new(start, end),
                text: value["text"].as_str().unwrap_or("").into(),
            });
        }
        let selections = serde_json::from_value(info["selections"].clone()).unwrap_or_default();
        let comments = info["comments"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Span::from_wire)
            .collect();
        let mut prefix = format!(
            "__thor_{}_",
            &sha256(path.to_string_lossy().as_bytes())[..12]
        );
        while raw.contains(&prefix) {
            prefix.push_str("x_");
        }
        let excluded = info["excluded"].as_bool().unwrap_or(false);
        let mut line_starts = vec![0];
        for (i, byte) in raw.bytes().enumerate() {
            if byte == b'\n' {
                line_starts.push(i + 1);
            }
        }
        // Euler intervals make descendant queries constant-time even when
        // equal-span wrappers or a future frontend reorder sibling nodes.
        let mut traversal = Vec::with_capacity(nodes.len());
        let mut subtree = vec![(0, 0); nodes.len()];
        let mut stack = vec![(NodeId(0), false)];
        while let Some((id, leaving)) = stack.pop() {
            if leaving {
                subtree[id.0].1 = traversal.len();
                continue;
            }
            subtree[id.0].0 = traversal.len();
            traversal.push(id);
            stack.push((id, true));
            stack.extend(nodes[id.0].children.iter().rev().map(|id| (*id, false)));
        }
        if traversal.len() != nodes.len() {
            return Err(Error::new("AST contains disconnected roots"));
        }
        storage::FieldsBuilder::pack(&mut nodes);
        Ok(Self {
            syntax: Arc::new(SourceSyntax {
            path,
            raw,
            nodes,
            names,
            selections,
            comments,
            info,
            prefix,
            excluded,
            thor,
            protocol: Arc::from(packet),
            protocol_compressed: false,
            migration_identifiers: None,
            protocol_overrides,
            line_starts,
            by_span,
            traversal,
            subtree,
            }),
            edits,
            shared_native: false,
            collection_roots: BTreeMap::new(),
            support_collection: "thor".into(),
        })
    }
    pub fn node(&self, id: NodeId) -> &Node {
        &self.nodes[id.0]
    }
    pub fn frontend_info(&self) -> Value {
        let mut info = self.info.clone();
        let mut protocol: Value = serde_json::from_slice(&self.protocol_bytes())
            .expect("validated frontend protocol");
        if self.migration_identifiers.is_some() {
            info["migration_identifiers"] = protocol["migration_identifiers"].take();
        }
        let mut rows = protocol["nodes"].as_array_mut()
            .map(std::mem::take).expect("validated frontend node rows");
        if rows.is_empty() {
            rows.push(serde_json::json!({"kind":"File","start":0,"end":self.raw.len(),"parent":-1}));
        }
        for (index, (row, node)) in rows.iter_mut().zip(&self.nodes).enumerate() {
            for (name, value) in node.data.as_object().into_iter().flatten() {
                row[name] = value.clone();
            }
            if let Some(overrides) = self.protocol_overrides.get(&NodeId(index)) {
                if let Some(roles) = &overrides.roles { row["roles"] = roles.clone(); }
                if let Some(lists) = &overrides.lists { row["lists"] = lists.clone(); }
            }
        }
        info["nodes"] = Value::Array(rows);
        info
    }
    pub fn text(&self, id: NodeId) -> &str {
        self.slice(self.node(id).span)
    }
    pub fn slice(&self, span: Span) -> &str {
        &self.raw[span.start..span.end]
    }
    pub fn role(&self, id: NodeId, name: &str) -> Option<NodeId> {
        self.node(id).roles.get(name).copied()
    }
    pub fn group(&self, id: NodeId, name: &str) -> &[NodeId] {
        self.node(id)
            .lists
            .get(name)
            .map(PackedSlice::as_slice)
            .unwrap_or(&[])
    }
    pub fn ancestors(&self, id: NodeId) -> impl Iterator<Item = NodeId> + '_ {
        std::iter::successors(self.node(id).parent, move |id| self.node(*id).parent)
    }
    /// A directly enclosing annotation supplies the type even when an
    /// expression is extracted from its declaration for compile-time execution.
    pub fn value_annotation(&self, mut id: NodeId) -> Option<NodeId> {
        loop {
            let parent = self.node(id).parent?;
            match self.node(parent).kind.as_str() {
                "Paren" => id = parent,
                "Declaration" | "Field" => return self.role(parent, "type"),
                "Return" => {
                    let procedure = self
                        .ancestors(parent)
                        .find(|node| self.node(*node).kind == "Procedure")?;
                    let signature = self.role(procedure, "type")?;
                    let results = self.role(signature, "results")?;
                    let fields = self
                        .node(results)
                        .children
                        .iter()
                        .filter(|node| self.node(**node).kind == "Field")
                        .copied()
                        .collect::<Vec<_>>();
                    if fields.len() == 1 && self.group(fields[0], "names").len() <= 1 {
                        return self.role(fields[0], "type");
                    }
                    return None;
                }
                _ => return None,
            }
        }
    }
    pub fn comprehension_annotation(&self, id: NodeId) -> Option<NodeId> {
        self.ancestors(id)
            .find(|ancestor| matches!(self.node(*ancestor).kind.as_str(), "Declaration" | "Procedure"))
            .filter(|declaration| self.node(*declaration).kind == "Declaration")
            .and_then(|declaration| self.role(declaration, "type"))
    }
    pub fn is_descendant(&self, id: NodeId, parent: NodeId) -> bool {
        let (start, end) = self.subtree[parent.0];
        let child = self.subtree[id.0].0;
        start < child && child < end
    }
    pub fn descendants(&self, id: NodeId) -> impl Iterator<Item = NodeId> + '_ {
        let (start, end) = self.subtree[id.0];
        self.traversal[start + 1..end].iter().copied()
    }
    pub fn position(&self, mut offset: usize) -> (usize, usize) {
        offset = offset.min(self.raw.len());
        while !self.raw.is_char_boundary(offset) {
            offset -= 1;
        }
        let line = self
            .line_starts
            .partition_point(|start| *start <= offset)
            .saturating_sub(1);
        (
            line + 1,
            self.raw[self.line_starts[line]..offset]
                .encode_utf16()
                .count()
                + 1,
        )
    }
    pub fn rune_position(&self, offset: usize) -> (usize, usize) {
        let line = self
            .line_starts
            .partition_point(|start| *start <= offset)
            .saturating_sub(1);
        (
            line + 1,
            self.raw[self.line_starts[line]..offset].chars().count() + 1,
        )
    }
    pub fn at(&self, span: Span, relative: NodeId) -> Option<NodeId> {
        let nodes = self.by_span.get(&span)?;
        nodes
            .iter()
            .copied()
            .find(|id| *id != relative && self.is_descendant(*id, relative))
            .or_else(|| nodes.iter().copied().find(|id| *id != relative))
    }
}
fn intern_field(names: &mut HashSet<Arc<str>>, name: &str) -> Arc<str> {
    if let Some(key) = names.get(name) {
        return key.clone();
    }
    let key: Arc<str> = name.into();
    names.insert(key.clone());
    key
}
fn is_descendant(nodes: &[Node], id: NodeId, ancestor: NodeId) -> bool {
    let mut parent = nodes[id.0].parent;
    while let Some(id) = parent {
        if id == ancestor {
            return true;
        }
        parent = nodes[id.0].parent;
    }
    false
}
fn resolve_span(
    nodes: &[Node],
    spans: &HashMap<Span, PackedSlice<NodeId>>,
    span: Span,
    relative: NodeId,
) -> Option<NodeId> {
    let candidates = spans.get(&span)?;
    candidates
        .iter()
        .find(|id| **id != relative && is_descendant(nodes, **id, relative))
        .or_else(|| candidates.iter().find(|id| **id != relative))
        .copied()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn parsed(raw: &str, nodes: Value) -> Result<Source> {
        Source::from_frontend(
            PathBuf::from("/example.thor"),
            raw.into(),
            json!({"nodes":nodes,"edits":[],"features":[],"selections":[]}),
            true,
        )
    }
    #[test]
    fn compact_migration_syntax_preserves_wire_output_semantics_and_cache_roundtrips() {
        let rows = (0..200).map(|index| json!({"kind":if index==0 {"File"} else {"Ident"},
            "start":0,"end":1,"parent":if index==0 {-1} else {0},"name":if index==0 {""} else {"x"},
            "using_field":false,"variadic":false,"diverging":false,"optional_ok":false,
            "optional_allocator_error":false,"bounds_check":0,"type_assert_check":0,
            "calling_convention":"","roles":{},"lists":{}})).collect::<Vec<_>>();
        let packet = serde_json::to_vec(&json!({"nodes":rows,"features":[],"edits":[],"selections":[],
            "migration_identifiers":[[0,1],[0,1]]})).unwrap();
        let ordinary = Source::from_frontend_bytes("/example.odin".into(),"x".into(),&packet,false).unwrap();
        let compact = Source::from_migration_bytes("/example.odin".into(),"x".into(),&packet).unwrap();
        let original_info = ordinary.frontend_info();
        let compact_info = compact.frontend_info();
        let cached = cache::write(&compact,"migration");
        let restored = cache::read(compact.path.clone(),compact.raw.clone(),false,"migration",&cached).unwrap();
        let restored_info = restored.frontend_info();
        if !compact.protocol_compressed || compact.protocol.len() >= packet.len()/4
            || original_info != compact_info || compact_info != restored_info {
            panic!("compact migration syntax lost wire or cache behavior");
        }
        if compact.node(NodeId(1)).data["using_field"] == true
            || compact.node(NodeId(1)).data["bounds_check"].as_i64().unwrap_or_default() != 0
            || compact.text(NodeId(1)) != ordinary.text(NodeId(1))
            || !compact.node(NodeId(1)).data.as_object().is_some_and(|data| data.is_empty())
            || !ordinary.node(NodeId(1)).data.as_object().is_some_and(|data| data.is_empty()) {
            panic!("compact migration syntax changed semantic defaults");
        }
    }
    #[test]
    fn helper_sources_share_syntax_but_own_their_edits_and_collections() {
        let source = parsed("x", json!([
            {"kind":"File","start":0,"end":1,"parent":-1},
            {"kind":"Ident","start":0,"end":1,"parent":0,"name":"x"}
        ])).expect("valid source");
        let mut helper = source.clone();
        if !source.shares_syntax(&helper) { panic!("syntax was copied"); }
        helper.edits.push(Edit {span:Span::new(0,1), text:"helper".into()});
        helper.collection_roots.insert("custom".into(), PathBuf::from("/custom"));
        if !source.edits.is_empty() || !source.collection_roots.is_empty()
            || source.text(NodeId(1)) != "x" || helper.text(NodeId(1)) != "x" {
            panic!("a helper modified its parent's source view");
        }
    }
    #[test]
    fn roles_distinguish_equal_span_wrappers_by_ancestry() {
        let result = parsed(
            "xy",
            json!([
                {"kind":"File","start":0,"end":2,"parent":-1},
                {"kind":"Block","start":1,"end":2,"parent":0},
                {"kind":"Paren","start":1,"end":2,"parent":1,"roles":{"expr":[1,2]}},
                {"kind":"Ident","start":1,"end":2,"parent":2,"name":"y"}
            ]),
        );
        let source = result.expect("valid source");
        let child = source.role(NodeId(2), "expr");
        assert_eq!(child, Some(NodeId(3)));
        assert_eq!(source.nodes[3].parent, Some(NodeId(2)));
    }
    #[test]
    fn descendant_intervals_handle_noncontiguous_ids_and_equal_spans() {
        let source = parsed(
            "abcd",
            json!([
                {"kind":"File","start":0,"end":4,"parent":-1},
                {"kind":"Block","start":0,"end":2,"parent":0},
                {"kind":"Block","start":2,"end":4,"parent":0},
                {"kind":"Ident","start":0,"end":2,"parent":1,"name":"ab"},
                {"kind":"Ident","start":2,"end":4,"parent":2,"name":"cd"}
            ]),
        )
        .expect("valid ordered parents");
        for parent in 0..source.nodes.len() {
            for child in 0..source.nodes.len() {
                let expected = is_descendant(&source.nodes, NodeId(child), NodeId(parent));
                let indexed = source.is_descendant(NodeId(child), NodeId(parent));
                assert_eq!(indexed, expected);
            }
        }
        let children = source.descendants(NodeId(1)).collect::<Vec<_>>();
        let resolved = source.at(Span::new(0, 2), NodeId(1));
        let expected_children = vec![NodeId(3)];
        assert_eq!(children, expected_children);
        assert_eq!(resolved, Some(NodeId(3)));
    }
    #[test]
    fn malformed_spans_and_parent_cycles_are_rejected() {
        for row in [
            json!({"kind":"File","start":2,"end":1,"parent":-1}),
            json!({"kind":"File","start":0,"end":3,"parent":-1}),
            json!({"kind":"File","start":0,"end":2,"parent":0}),
            json!({"kind":"File","start":0,"end":1,"parent":-1}),
        ] {
            let result = parsed("é", json!([row]));
            assert!(result.is_err());
        }
    }
    #[test]
    fn list_spans_receive_the_same_byte_and_utf8_validation_as_roles() {
        for span in [json!([0, 3]), json!([1, 2]), json!([2, 0])] {
            let result = parsed("é", json!([
                {"kind":"File","start":0,"end":2,"parent":-1,"lists":{"elems":[span]}},
                {"kind":"Ident","start":0,"end":2,"parent":0,"name":"é"}
            ]));
            if result.is_ok() {
                panic!("invalid list span was accepted");
            }
        }
    }
    #[test]
    fn unknown_role_keys_and_metadata_keep_the_frontend_protocol() {
        let row = json!({"kind":"File","start":0,"end":2,"parent":-1,
            "roles":{"future_role":[0,2,77]},"lists":{"future_list":[[0,2,99],"extension"]},
            "future_metadata":{"label":"é","values":[3,null]}});
        let source = parsed("é", json!([
            row,
            {"kind":"Ident","start":0,"end":2,"parent":0,"name":"é"}
        ])).expect("valid extension metadata");
        let roles = source.role(NodeId(0), "future_role");
        let list = source.group(NodeId(0), "future_list");
        let info = source.frontend_info();
        if roles != Some(NodeId(1)) || list != [NodeId(1)] || info["nodes"][0] != row {
            panic!("unknown frontend metadata was changed");
        }
    }
    #[test]
    fn typed_nodes_reconstruct_core_fields_and_feature_overlays_for_protocol_output() {
        let payload = json!({"nodes":[
            {"kind":"File","start":0,"end":3,"parent":-1},
            {"kind":"Procedure","start":0,"end":3,"parent":0,"name":"outer"},
            {"kind":"Call","start":2,"end":3,"parent":1,"op":"x","name":"call_name",
             "mutable":false,"lists":{"args":[[2,3]]}},
            {"kind":"Ident","start":2,"end":3,"parent":2,"name":"a"}
        ],"features":[
            {"kind":"PureFunction","start":0,"end":3},
            {"kind":"Run","start":2,"end":3,"custom":"kept",
             "roles":{"future_role":[2,3,77]},
             "lists":{"args":[[2,3]],"future_list":[[2,3,99],"extension"]}}
        ],"edits":[],"selections":[]});
        let packet = serde_json::to_vec(&payload).expect("wire packet");
        let source = Source::from_frontend_bytes("/example.thor".into(), "éa".into(), &packet, true)
            .expect("valid typed protocol");
        let restored = source.frontend_info();
        let call = &restored["nodes"][2];
        if source.node(NodeId(2)).kind != "Run" || source.node(NodeId(2)).op != "x"
            || source.names.get(source.node(NodeId(2)).name) != "call_name"
            || source.role(NodeId(2), "expr") != Some(NodeId(3))
            || restored["nodes"][1]["pure"] != true || call["kind"] != "Run"
            || call["op"] != "x" || call["name"] != "call_name" || call["mutable"] != false
            || call["roles"]["expr"] != json!([2,3]) || call["custom"] != "kept"
            || call["roles"]["future_role"] != json!([2,3,77])
            || call["lists"]["future_list"] != json!([[2,3,99],"extension"]) {
            panic!("typed node decoding changed the frontend protocol");
        }
        let empty = parsed("", json!([])).expect("empty frontend node list");
        if empty.frontend_info()["nodes"] != json!([
            {"kind":"File","start":0,"end":0,"parent":-1}
        ]) {
            panic!("empty frontend packet lost its synthesized file node");
        }
    }
    #[test]
    fn malformed_link_tables_are_errors_instead_of_panics_during_feature_merging() {
        for key in ["roles", "lists"] {
            let mut row = json!({"kind":"Call","start":0,"end":1,"parent":0});
            row[key] = json!(7);
            let result = parsed("x", json!([
                {"kind":"File","start":0,"end":1,"parent":-1},row
            ]));
            if result.is_ok() { panic!("invalid core link table accepted"); }
            let mut feature = json!({"kind":"Run","start":0,"end":1});
            feature[key] = json!(7);
            let result = Source::from_frontend("/example.thor".into(), "x".into(), json!({
                "nodes":[{"kind":"File","start":0,"end":1,"parent":-1},
                         {"kind":"Call","start":0,"end":1,"parent":0}],
                "features":[feature],"edits":[],"selections":[]
            }),true);
            if result.is_ok() { panic!("invalid feature link table accepted"); }
        }
    }
    #[test]
    fn source_coordinates_use_bytes_and_utf16_separately() {
        let raw = "\u{feff}a😀\r\nbé";
        let source = parsed(
            raw,
            json!([{"kind":"File","start":0,"end":raw.len(),"parent":-1}]),
        )
        .expect("valid unicode");
        let after_emoji = source.position(8);
        let second_line = source.position(13);
        assert_eq!(after_emoji, (1, 5));
        assert_eq!(second_line, (2, 3));
    }
    #[test]
    fn names_are_interned_once() {
        let mut names = Names::default();
        let first = names.intern("same");
        let second = names.intern("same");
        let third = names.intern("other");
        assert_eq!(first, second);
        assert_ne!(first, third);
        assert_eq!(names.get(first), "same");
    }
}
