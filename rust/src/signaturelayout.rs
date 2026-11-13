//! Syntax-only parameter layouts. Types and constant argument values remain
//! specific to the current resolver and call-site substitutions.
use super::*;

pub(super) struct ArgumentField {
    pub default: Option<Ref>,
    pub variadic: bool,
}

pub(super) struct ArgumentLayout {
    pub fields: Vec<ArgumentField>,
    pub positions: HashMap<String, usize>,
}

pub(super) struct ConstantParameter {
    pub index: usize,
    pub name: String,
    pub annotation: Option<Ref>,
}

pub(super) struct SignatureLayout {
    pub params: Vec<(String, Option<Ref>)>,
    pub results: Vec<(String, Option<Ref>)>,
    pub arguments: Option<ArgumentLayout>,
    pub constants: Vec<ConstantParameter>,
}

impl SignatureLayout {
    pub fn new(sources: &[Source], signature: Ref) -> Self {
        crate::profile::count("resolver.signature_layouts", 1);
        let source = &sources[signature.file];
        let at = |node| Ref { file: signature.file, node };
        let mut constants = vec![];
        let arguments = source.role(signature.node, "params").map(|params| {
            let mut fields = vec![];
            let mut positions = HashMap::new();
            let mut name_index = 0;
            for field in &source.node(params).children {
                for name in source.group(*field, "names") {
                    let index = name_index;
                    name_index += 1;
                    let name_text = source.text(*name).trim_start_matches('$');
                    // Keep the first matching name, as the original linear
                    // argument lookup did, even for an invalid declaration.
                    if source.node(*field).kind == "Field" {
                        positions.entry(name_text.into()).or_insert(fields.len());
                        fields.push(ArgumentField {
                            default: source.role(*field, "default").map(at),
                            variadic: source.node(*field).data["variadic"] == true,
                        });
                    }
                    if source.node(*name).kind == "PolyType" {
                        let Some(parent) = source.node(*name).parent else { continue; };
                        let annotation = source.role(parent, "type");
                        if !annotation.is_some_and(|node| source.node(node).kind == "TypeidType") {
                            constants.push(ConstantParameter {
                                index, name: name_text.into(), annotation: annotation.map(at),
                            });
                        }
                    }
                }
            }
            ArgumentLayout { fields, positions }
        });
        Self {
            params: collect_fields(sources, signature, "params"),
            results: collect_fields(sources, signature, "results"),
            arguments,
            constants,
        }
    }
}

pub(super) fn collect_fields(sources: &[Source], reference: Ref, role: &str) -> Vec<(String, Option<Ref>)> {
    let source = &sources[reference.file];
    let Some(fields) = source.role(reference.node, role) else { return vec![]; };
    let mut result = vec![];
    for field in &source.node(fields).children {
        if source.node(*field).kind != "Field" { continue; }
        let annotation = source.role(*field, "type").map(|node| Ref { file: reference.file, node });
        let names = source.group(*field, "names");
        if names.is_empty() { result.push((String::new(), annotation)); }
        for name in names {
            let name = if source.names.get(source.node(*name).name) == "_" {
                "_"
            } else {
                source.text(*name).trim_start_matches('$')
            };
            result.push((name.into(), annotation));
        }
    }
    result
}

impl Resolver<'_> {
    pub(super) fn signature_layout(&self, signature: Ref) -> Arc<SignatureLayout> {
        if let Some(layout) = self.index.signatures.get(&signature) {
            return layout.get_or_init(|| Arc::new(SignatureLayout::new(self.sources, signature))).clone();
        }
        Arc::new(SignatureLayout::new(self.sources, signature))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn source(path: &str) -> Source {
        Source::from_frontend(path.into(), "proc()".into(), json!({
            "nodes":[
                {"kind":"File","start":0,"end":6,"parent":-1},
                {"kind":"ProcedureType","start":0,"end":6,"parent":0,"roles":{"params":[4,6]}},
                {"kind":"FieldList","start":4,"end":6,"parent":1}
            ],"features":[],"edits":[],"selections":[]
        }), false).unwrap()
    }

    #[test]
    fn layouts_are_lazy_and_survive_semantic_resets_and_namespace_extensions() {
        let mut sources = vec![source("/project/main.odin")];
        let signature = Ref { file: 0, node: NodeId(1) };
        let mut resolver = Resolver::new(&sources);
        if resolver.index.signatures[&signature].get().is_some() { panic!("unused layout was allocated"); }
        let first = resolver.signature_layout(signature);
        let empty = resolver.arrange_arguments(signature, &[]);
        if empty != Some(vec![]) { panic!("empty parameter list changed"); }
        resolver.set_evaluations(BTreeMap::new());
        let reset = resolver.signature_layout(signature);
        if !Arc::ptr_eq(&first, &reset) { panic!("semantic reset discarded syntax layout"); }
        let index = resolver.syntax_index();
        let other = Resolver::with_index(&sources, &[], Some(&index));
        let reused = other.signature_layout(signature);
        if !Arc::ptr_eq(&first, &reused) { panic!("syntax index did not share its layout"); }
        drop(other);
        drop(resolver);
        sources.push(source("/sdk/new/lib.odin"));
        let extended = Resolver::extend_index(&sources, &[], index, 1);
        let reused = extended.signature_layout(signature);
        if !Arc::ptr_eq(&first, &reused) { panic!("namespace extension discarded an existing layout"); }
        let added = Ref { file: 1, node: NodeId(1) };
        if extended.index.signatures[&added].get().is_some() { panic!("extension eagerly allocated an unused layout"); }
        let missing = extended.arrange_arguments(Ref { file: 0, node: NodeId(0) }, &[]);
        if missing.is_some() { panic!("missing parameter list became an empty list"); }
    }
}
