//! Arena AST and interned names. Node links are stable integers, never owning graphs.
use crate::kind::Kind;
use crate::{
    diagnostic::{Error, Result},
    digest::sha256,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, HashMap},
    path::PathBuf,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct NodeId(pub usize);
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct NameId(pub u32);
#[derive(Default, Debug)]
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
}

#[derive(Clone, Debug)]
pub struct Node {
    pub kind: Kind,
    pub span: Span,
    pub parent: Option<NodeId>,
    pub children: Vec<NodeId>,
    pub roles: BTreeMap<String, NodeId>,
    pub spans: BTreeMap<String, Span>,
    pub lists: BTreeMap<String, Vec<NodeId>>,
    pub op: String,
    pub name: NameId,
    pub mutable: bool,
    pub immutable: bool,
    pub data: Value,
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

#[derive(Debug)]
pub struct Source {
    pub path: PathBuf,
    pub raw: String,
    pub nodes: Vec<Node>,
    pub names: Names,
    pub edits: Vec<Edit>,
    pub selections: Vec<Selection>,
    pub comments: Vec<Span>,
    pub info: Value,
    pub prefix: String,
    pub excluded: bool,
    pub thor: bool,
    pub shared_native: bool,
    pub collection_roots: BTreeMap<String, PathBuf>,
    line_starts: Vec<usize>,
}
impl Source {
    pub fn from_frontend(path: PathBuf, raw: String, mut info: Value, thor: bool) -> Result<Self> {
        let rows = info["nodes"]
            .as_array_mut()
            .ok_or_else(|| Error::new("frontend omitted nodes"))?;
        if rows.is_empty() {
            rows.push(serde_json::json!({"kind":"File","start":0,"end":raw.len(),"parent":-1}));
        }
        let mut names = Names::default();
        let mut nodes = Vec::with_capacity(rows.len());
        let mut by_span: HashMap<Span, Vec<NodeId>> = HashMap::new();
        for (i, row) in rows.iter().enumerate() {
            let start = row["start"]
                .as_u64()
                .ok_or_else(|| Error::new("invalid AST start"))? as usize;
            let end = row["end"]
                .as_u64()
                .ok_or_else(|| Error::new("invalid AST end"))? as usize;
            if start > end
                || end > raw.len()
                || !raw.is_char_boundary(start)
                || !raw.is_char_boundary(end)
            {
                return Err(Error::new(format!("invalid AST byte span {start}..{end}")));
            }
            let parent = row["parent"].as_i64().unwrap_or(-1);
            if parent >= i as i64 {
                return Err(Error::new("invalid AST parent order"));
            }
            let span = Span::new(start, end);
            by_span.entry(span).or_default().push(NodeId(i));
            nodes.push(Node {
                kind: row["kind"].as_str().unwrap_or("Other").into(),
                span,
                parent: if parent < 0 {
                    None
                } else {
                    Some(NodeId(parent as usize))
                },
                children: vec![],
                roles: BTreeMap::new(),
                spans: BTreeMap::new(),
                lists: BTreeMap::new(),
                op: row["op"].as_str().unwrap_or("").into(),
                name: names.intern(row["name"].as_str().unwrap_or("")),
                mutable: row["mutable"].as_bool().unwrap_or(false),
                immutable: false,
                data: row.clone(),
            });
        }
        for i in 1..nodes.len() {
            if let Some(parent) = nodes[i].parent {
                nodes[parent.0].children.push(NodeId(i));
            }
        }
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
                        }
                    }
                    if matches!(kind, "SwitchExpr" | "CompileSwitchExpr" | "CompileSwitch") {
                        nodes[id.0].data["roles"]["selector"] = feature["selector"].clone();
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
        for node in &mut nodes {
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
                }
            }
        }
        // Features can replace roles/lists. Resolve them only after merging,
        // distinguishing same-span wrappers using native ancestry.
        for i in 0..nodes.len() {
            let row = nodes[i].data.clone();
            if let Some(roles) = row["roles"].as_object() {
                for (name, value) in roles {
                    if let Some(span) = Span::from_wire(value) {
                        if span.start > span.end
                            || span.end > raw.len()
                            || !raw.is_char_boundary(span.start)
                            || !raw.is_char_boundary(span.end)
                        {
                            return Err(Error::new("invalid AST role span"));
                        }
                        nodes[i].spans.insert(name.clone(), span);
                        if let Some(id) = resolve_span(&nodes, &by_span, span, NodeId(i)) {
                            nodes[i].roles.insert(name.clone(), id);
                        }
                    }
                }
            }
            if let Some(lists) = row["lists"].as_object() {
                for (name, value) in lists {
                    let mut ids = vec![];
                    for value in value.as_array().into_iter().flatten() {
                        if let Some(span) = Span::from_wire(value) {
                            if let Some(id) = resolve_span(&nodes, &by_span, span, NodeId(i)) {
                                ids.push(id);
                            }
                        }
                    }
                    nodes[i].lists.insert(name.clone(), ids);
                }
            }
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
                    name.as_str(),
                    "loop" | "expr" | "type" | "output" | "key" | "cond"
                ))
                .map(|(name, id)| (name.clone(), id.0))
                .collect::<BTreeMap<_, _>>());
            nodes[loop_id.0].data["comprehension"] = serde_json::json!(id.0);
        }
        info["nodes"] = Value::Array(nodes.iter().map(|node| node.data.clone()).collect());
        let mut edits = vec![];
        for value in info["edits"].as_array().into_iter().flatten() {
            let start = value["start"]
                .as_u64()
                .ok_or_else(|| Error::new("invalid edit start"))? as usize;
            let end = value["end"]
                .as_u64()
                .ok_or_else(|| Error::new("invalid edit end"))? as usize;
            if start > end
                || end > raw.len()
                || !raw.is_char_boundary(start)
                || !raw.is_char_boundary(end)
            {
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
        Ok(Self {
            path,
            raw,
            nodes,
            names,
            edits,
            selections,
            comments,
            info,
            prefix,
            excluded,
            thor,
            shared_native: false,
            collection_roots: BTreeMap::new(),
            line_starts,
        })
    }
    pub fn node(&self, id: NodeId) -> &Node {
        &self.nodes[id.0]
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
            .map(Vec::as_slice)
            .unwrap_or(&[])
    }
    pub fn ancestors(&self, id: NodeId) -> impl Iterator<Item = NodeId> + '_ {
        std::iter::successors(self.node(id).parent, move |id| self.node(*id).parent)
    }
    pub fn is_descendant(&self, id: NodeId, parent: NodeId) -> bool {
        is_descendant(&self.nodes, id, parent)
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
    pub fn at(&self, span: Span, relative: NodeId) -> Option<NodeId> {
        self.nodes
            .iter()
            .enumerate()
            .find(|(i, n)| {
                n.span == span && *i != relative.0 && self.is_descendant(NodeId(*i), relative)
            })
            .or_else(|| {
                self.nodes
                    .iter()
                    .enumerate()
                    .find(|(i, n)| n.span == span && *i != relative.0)
            })
            .map(|(i, _)| NodeId(i))
    }
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
    spans: &HashMap<Span, Vec<NodeId>>,
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
