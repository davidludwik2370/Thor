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
    pub contextual_calls: Vec<Vec<NodeId>>,
    pub constant_requests: Vec<HashMap<NodeId, Vec<NodeId>>>,
    pub signatures: HashMap<Ref, std::sync::OnceLock<Arc<signaturelayout::SignatureLayout>>>,
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
            contextual_calls: vec![],
            constant_requests: vec![],
            signatures: HashMap::new(),
            heads: vec![],
            parents: vec![],
        };
        index.append(sources, 0);
        index
    }
    pub fn append(&mut self, sources: &[Source], start: usize) {
        for (file, source) in sources.iter().enumerate().skip(start) {
            crate::profile::count("resolver.context_candidate_scans", source.nodes.len() as u64);
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
            let mut contextual_calls = vec![];
            let mut constant_requests: HashMap<NodeId, Vec<NodeId>> = HashMap::new();
            for (n, node) in source.nodes.iter().enumerate() {
                let id = NodeId(n);
                if matches!(node.kind.as_str(), "ProcedureType" | "StructType") {
                    self.signatures.insert(Ref { file, node: id }, std::sync::OnceLock::new());
                }
                let parent = node.parent.map(|p| heads[p.0]).unwrap_or(NodeId(0));
                heads.push(if is_scope(source, id) { id } else { parent });
                parents.push(parent);
                if expression_name(source, id) {
                    expression_names.push(id);
                }
                if (matches!(node.kind.as_str(), "Call" | "Run" | "Comprehension")
                    || node.kind == "Binary" && node.op == "**")
                    && !source.ancestors(id).any(|owner| source.node(owner).kind == "Procedure")
                {
                    if let Some(declaration) = source.ancestors(id)
                        .find(|owner| source.node(*owner).kind == "Declaration")
                    {
                        let definition = source.node(declaration);
                        if !definition.mutable && !definition.immutable
                            && source.group(declaration, "values").iter().any(|value|
                                source.node(*value).span.contains(node.span))
                        {
                            constant_requests.entry(declaration).or_default().push(id);
                        }
                    }
                }
                if node.kind == "Call" && source.group(id, "args").iter().any(|arg|
                    std::iter::once(*arg).chain(source.descendants(*arg)).any(|node|
                        source.node(node).kind == "Compound" && source.node(node).spans.get("open")
                            .is_some_and(|span| source.slice(*span) == "[")))
                {
                    contextual_calls.push(id);
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
            self.contextual_calls.push(contextual_calls);
            self.constant_requests.push(constant_requests);
        }
    }
    pub fn scopes(&self, reference: Ref) -> impl Iterator<Item = NodeId> + '_ {
        std::iter::successors(
            Some(self.heads[reference.file][reference.node.0]),
            move |id| (id.0 != 0).then(|| self.parents[reference.file][id.0]),
        )
    }
}

impl Resolver<'_> {
    pub(crate) fn declaration_evaluations(&self, declaration: Ref) -> Vec<Ref> {
        self.index.constant_requests[declaration.file].get(&declaration.node)
            .into_iter().flatten().map(|node| Ref { file: declaration.file, node: *node }).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn source(path: &str) -> Source {
        Source::from_frontend(path.into(), "take([1])".into(), json!({
            "nodes":[
                {"kind":"File","start":0,"end":9,"parent":-1},
                {"kind":"Call","start":0,"end":9,"parent":0,"roles":{"callee":[0,4]},"lists":{"args":[[5,8]]}},
                {"kind":"Ident","start":0,"end":4,"parent":1},
                {"kind":"Compound","start":5,"end":8,"parent":1,"roles":{"open":[5,6]}},
                {"kind":"Literal","start":6,"end":7,"parent":3}
            ],"features":[],"edits":[],"selections":[]
        }), true).unwrap()
    }

    #[test]
    fn contextual_candidates_survive_resets_and_extend_with_new_syntax() {
        let mut sources = vec![source("/project/main.thor")];
        let mut resolver = Resolver::new(&sources);
        if resolver.index.contextual_calls != vec![vec![NodeId(1)]] {
            panic!("contextual array call was omitted");
        }
        resolver.set_evaluations(BTreeMap::new());
        let index = resolver.syntax_index();
        drop(resolver);
        sources.push(source("/dependency/lib.thor"));
        let extended = Resolver::extend_index(&sources, &[], index, 1);
        if extended.index.contextual_calls != vec![vec![NodeId(1)], vec![NodeId(1)]] {
            panic!("syntax extension changed contextual call candidates");
        }
    }
}

fn expression_name(s: &Source, node: NodeId) -> bool {
    let n = s.node(node);
    if n.kind != "Ident" {
        return false;
    }
    if let Some(parent) = n.parent {
        let p = s.node(parent);
        if s.role(parent,"label")==Some(node){return false;}
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
