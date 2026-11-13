//! Syntax-only indexes. Selected definitions still depend on the resolver's
//! current flags, constants and lexical bindings and are never cached here.
use super::*;
use std::path::PathBuf;

#[derive(Clone)]
pub(super) struct NameIndex {
    pub package_for: Vec<usize>,
    pub packages: HashMap<PathBuf, usize>,
    pub members: Vec<Vec<usize>>,
    pub package_symbols: Vec<HashMap<String, Vec<usize>>>,
    pub imports: Vec<HashMap<String, Vec<String>>>,
    pub using: Vec<Vec<NodeId>>,
    pub promotions: Vec<Vec<NodeId>>,
    pub expression_names: Vec<Vec<NodeId>>,
    heads: Vec<Vec<NodeId>>,
    parents: Vec<Vec<NodeId>>,
}
impl NameIndex {
    pub fn new(sources: &[Source]) -> Self {
        let mut index = Self {
            package_for: vec![],
            packages: HashMap::new(),
            members: vec![],
            package_symbols: vec![],
            imports: vec![],
            using: vec![],
            promotions: vec![],
            expression_names: vec![],
            heads: vec![],
            parents: vec![],
        };
        index.append(sources, 0);
        index
    }
    pub fn append(&mut self, sources: &[Source], start: usize) {
        for (file, source) in sources.iter().enumerate().skip(start) {
            let directory = source.path.parent().unwrap_or(&source.path).to_path_buf();
            let next = self.packages.len();
            let package = *self.packages.entry(directory).or_insert(next);
            if package == self.members.len() {
                self.members.push(vec![]);
                self.package_symbols.push(HashMap::new());
            }
            self.package_for.push(package);
            self.members[package].push(file);
            let mut heads: Vec<NodeId> = Vec::with_capacity(source.nodes.len());
            let mut parents = Vec::with_capacity(source.nodes.len());
            let mut imports: HashMap<String, Vec<String>> = HashMap::new();
            let mut using = vec![];
            let mut promotions = vec![];
            let mut expression_names = vec![];
            for (n, node) in source.nodes.iter().enumerate() {
                let id = NodeId(n);
                let parent = node.parent.map(|p| heads[p.0]).unwrap_or(NodeId(0));
                heads.push(if is_scope(source, id) { id } else { parent });
                parents.push(parent);
                if expression_name(source, id) {
                    expression_names.push(id);
                }
                if node.kind == "Using" {
                    using.push(id);
                    promotions.push(id);
                } else if matches!(node.kind.as_str(), "Declaration" | "Field")
                    && node.data["using_field"] == true
                {
                    promotions.push(id);
                }
                if node.kind == "Import" {
                    if let Ok(path) = crate::lower::unquote(&node.op) {
                        let explicit = source.names.get(node.name);
                        let namespace = if explicit.is_empty() {
                            path.rsplit(['/', ':']).next().unwrap_or("")
                        } else {
                            explicit
                        };
                        imports.entry(namespace.into()).or_default().push(path);
                    }
                }
            }
            self.heads.push(heads);
            self.parents.push(parents);
            self.imports.push(imports);
            self.using.push(using);
            self.promotions.push(promotions);
            self.expression_names.push(expression_names);
        }
    }
    pub fn scopes(&self, reference: Ref) -> impl Iterator<Item = NodeId> + '_ {
        std::iter::successors(
            Some(self.heads[reference.file][reference.node.0]),
            move |id| (id.0 != 0).then(|| self.parents[reference.file][id.0]),
        )
    }
}

fn expression_name(s: &Source, node: NodeId) -> bool {
    let n = s.node(node);
    if n.kind != "Ident" {
        return false;
    }
    if let Some(parent) = n.parent {
        let p = s.node(parent);
        if matches!(
            p.kind.as_str(),
            "Declaration" | "Field" | "Range" | "TypeSwitch"
        ) && s.group(parent, "names").contains(&node)
        {
            return false;
        }
        if matches!(p.kind.as_str(), "Selector" | "FieldValue")
            && s.role(parent, "field") == Some(node)
        {
            return false;
        }
        if matches!(
            p.kind.as_str(),
            "ImplicitSelector" | "EnumType" | "PolyType"
        ) {
            return false;
        }
    }
    true
}
