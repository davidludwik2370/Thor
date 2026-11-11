//! Syntax-only indexes. Selected definitions still depend on the resolver's
//! current flags, constants and lexical bindings and are never cached here.
use super::*;
use std::path::PathBuf;

pub(super) struct NameIndex {
    pub package_for: Vec<usize>,
    pub packages: HashMap<PathBuf, usize>,
    pub members: Vec<Vec<usize>>,
    pub package_symbols: Vec<HashMap<String, Vec<usize>>>,
    pub imports: Vec<HashMap<String, Vec<String>>>,
    pub using: Vec<Vec<NodeId>>,
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
            heads: vec![],
            parents: vec![],
        };
        for (file, source) in sources.iter().enumerate() {
            let directory = source.path.parent().unwrap_or(&source.path).to_path_buf();
            let next = index.packages.len();
            let package = *index.packages.entry(directory).or_insert(next);
            if package == index.members.len() {
                index.members.push(vec![]);
                index.package_symbols.push(HashMap::new());
            }
            index.package_for.push(package);
            index.members[package].push(file);
            let mut heads: Vec<NodeId> = Vec::with_capacity(source.nodes.len());
            let mut parents = Vec::with_capacity(source.nodes.len());
            let mut imports: HashMap<String, Vec<String>> = HashMap::new();
            let mut using = vec![];
            for (n, node) in source.nodes.iter().enumerate() {
                let id = NodeId(n);
                let parent = node.parent.map(|p| heads[p.0]).unwrap_or(NodeId(0));
                heads.push(if is_scope(source, id) { id } else { parent });
                parents.push(parent);
                if node.kind == "Using" {
                    using.push(id);
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
            index.heads.push(heads);
            index.parents.push(parents);
            index.imports.push(imports);
            index.using.push(using);
        }
        index
    }
    pub fn scopes(&self, reference: Ref) -> impl Iterator<Item = NodeId> + '_ {
        std::iter::successors(
            Some(self.heads[reference.file][reference.node.0]),
            move |id| (id.0 != 0).then(|| self.parents[reference.file][id.0]),
        )
    }
}
