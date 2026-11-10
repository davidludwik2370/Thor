//! Compile-time helper dependency extraction. Keep reference decision order;
//! helper compilation/scheduling is connected separately from this graph pass.
use crate::{
    ast::{NodeId, Span},
    borroweval::Evaluator as FlowEvaluator,
    borrowflow::Arena,
    diagnostic::{Error, Result},
    lower::unquote,
    semantics::{scope, Ref, Resolver, Type},
    specialization::TypeCode,
};
use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    path::PathBuf,
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CallTarget {
    Builtin(String),
    Procedure(Ref),
}
#[derive(Default)]
pub struct Closure {
    pub keep: BTreeSet<Ref>,
    pub imports: BTreeSet<Ref>,
    pub aliases: BTreeSet<(usize, String)>,
    pub procedures: BTreeSet<Ref>,
    pub queries: BTreeMap<Ref, String>,
    pub calls: BTreeMap<Ref, CallTarget>,
    pub external: BTreeMap<Ref, (String, String)>,
    pub helper_imports: BTreeMap<(usize, String), PathBuf>,
    pub nested_runs: BTreeSet<Ref>,
}
pub struct Extraction<'a, 'r> {
    r: &'r mut Resolver<'a>,
    graph: Closure,
    visited: HashSet<Ref>,
}
impl<'a, 'r> Extraction<'a, 'r> {
    pub fn build(r: &'r mut Resolver<'a>, roots: &[Ref]) -> Result<Closure> {
        let mut this = Self {
            r,
            graph: Closure::default(),
            visited: HashSet::new(),
        };
        for root in roots {
            if this.kind(*root) == "Declaration" {
                this.declaration(*root)?;
            } else {
                this.visit(*root)?;
            }
        }
        for (file, name) in this.graph.aliases.clone() {
            if let Some(import) = this.selection_import(file, &name) {
                this.graph.imports.insert(import);
            }
        }
        Ok(this.graph)
    }
    fn kind(&self, node: Ref) -> &str {
        &self.r.sources[node.file].node(node.node).kind
    }
    fn role(&self, node: Ref, name: &str) -> Option<Ref> {
        self.r.sources[node.file]
            .role(node.node, name)
            .map(|node_id| Ref {
                file: node.file,
                node: node_id,
            })
    }
    fn children(&self, node: Ref) -> Vec<Ref> {
        self.r.sources[node.file]
            .node(node.node)
            .children
            .iter()
            .map(|node_id| Ref {
                file: node.file,
                node: *node_id,
            })
            .collect()
    }
    fn group(&self, node: Ref, name: &str) -> Vec<Ref> {
        self.r.sources[node.file]
            .group(node.node, name)
            .iter()
            .map(|node_id| Ref {
                file: node.file,
                node: *node_id,
            })
            .collect()
    }
    fn owner(&self, node: Ref) -> Option<Ref> {
        self.r.sources[node.file]
            .ancestors(node.node)
            .find(|node_id| self.r.sources[node.file].node(*node_id).kind == "Procedure")
            .map(|node_id| Ref {
                file: node.file,
                node: node_id,
            })
    }
    fn builtin(&mut self, node: Ref) -> Option<String> {
        FlowEvaluator {
            resolver: self.r,
            arena: &mut Arena::default(),
        }
        .builtin(node)
        .filter(|name| {
            crate::purity::BUILTINS.contains(&name.as_str())
                || ["#assert", "#config"].contains(&name.as_str())
        })
    }
    fn io_call(&mut self, node: Ref) -> bool {
        FlowEvaluator {
            resolver: self.r,
            arena: &mut Arena::default(),
        }
        .io_call(node)
    }
    fn approved(&self, callee: Ref) -> Option<(String, String)> {
        for (path, names) in [
            ("core:slice", &["clone"][..]),
            ("core:strings", &["clone"][..]),
            (
                "thor:format",
                &[
                    "format",
                    "format_plain",
                    "format_options",
                    "format_spec",
                    "group_integer",
                ][..],
            ),
            (
                "thor:big",
                &[
                    "from_integer",
                    "rational_from_integer",
                    "fraction",
                    "parse_integer",
                    "parse_rational",
                    "parse_fraction",
                    "clone",
                    "numerator",
                    "denominator",
                    "to_string",
                    "equal",
                    "is_zero",
                    "sum",
                    "ascending",
                    "descending",
                ][..],
            ),
        ] {
            if let Some(name) = self
                .r
                .library_alias_member(callee, path)
                .filter(|name| names.contains(&name.as_str()))
            {
                return Some((path.into(), name));
            }
        }
        None
    }
    fn import_alias(&self, import: Ref) -> Option<String> {
        let source = &self.r.sources[import.file];
        let node = source.node(import.node);
        let name = source.names.get(node.name);
        if !name.is_empty() {
            return Some(name.into());
        }
        unquote(&node.op)
            .ok()?
            .rsplit(['/', ':'])
            .next()
            .map(str::to_string)
    }
    fn selection_import(&self, file: usize, name: &str) -> Option<Ref> {
        let source = &self.r.sources[file];
        let selected = source.selections.iter().find(|selection| {
            if selection.names.iter().any(|item| item == name) {
                return true;
            }
            if !selection.wildcard {
                return false;
            }
            let Ok(path) = unquote(&selection.path) else {
                return false;
            };
            let directory = if let Some((collection, suffix)) = path.split_once(':') {
                source
                    .collection_roots
                    .get(collection)
                    .map(|root| root.join(suffix))
            } else {
                source.path.parent().map(|root| root.join(path))
            };
            let directory = directory.and_then(|path| std::fs::canonicalize(path).ok());
            self.r.symbols.iter().any(|symbol| {
                symbol.name == name
                    && symbol.scope == NodeId(0)
                    && self.r.sources[symbol.reference.file].path.parent() == directory.as_deref()
            })
        })?;
        source
            .nodes
            .iter()
            .enumerate()
            .rev()
            .find(|(_, node)| {
                node.kind == "Import"
                    && node.span.start <= selected.start
                    && node.op == selected.path
            })
            .map(|(index, _)| Ref {
                file,
                node: NodeId(index),
            })
    }
    fn query(&mut self, node: Ref) -> Option<(String, Type)> {
        if self.kind(node) != "Call" {
            return None;
        }
        let builtin = self.builtin(node)?;
        if !["type_of", "size_of", "align_of", "offset_of"].contains(&builtin.as_str()) {
            return None;
        }
        let args = self.group(node, "args");
        if args.len() != if builtin == "offset_of" { 2 } else { 1 } {
            return None;
        }
        let typ = self.r.specialized_type(args[0], &Default::default());
        if typ.is_unknown() {
            return None;
        }
        let mut context = TypeCode::new(node.file, Some(node));
        let mut code = context.render(&typ, self.r).ok()?;
        for (alias, directory) in context.imports {
            self.graph
                .helper_imports
                .insert((node.file, alias), directory);
        }
        if builtin != "type_of" {
            code = format!(
                "{builtin}({code}{})",
                args.iter()
                    .skip(1)
                    .map(|arg| format!(",{}", self.r.sources[arg.file].text(arg.node)))
                    .collect::<String>()
            );
        }
        Some((code, typ))
    }
    fn type_definition(typ: &Type) -> Option<Ref> {
        match typ {
            Type::Nominal(owner, _)
            | Type::Struct(owner)
            | Type::Record(owner, _)
            | Type::Enum(owner)
            | Type::Union(owner) => Some(*owner),
            Type::Array { elem, .. }
            | Type::Slice(elem)
            | Type::Pointer(elem)
            | Type::Dynamic(elem) => Self::type_definition(elem),
            _ => None,
        }
    }
    fn declaration(&mut self, node: Ref) -> Result<()> {
        let source = &self.r.sources[node.file];
        let data = source.node(node.node);
        if data.kind == "Declaration"
            && (data.mutable || data.immutable)
            && scope(source, node.node) == NodeId(0)
        {
            return Ok(());
        }
        if !self.visited.insert(node) {
            return Ok(());
        }
        self.graph.keep.insert(node);
        for id in source.ancestors(node.node).collect::<Vec<_>>() {
            let ancestor = Ref {
                file: node.file,
                node: id,
            };
            match self.kind(ancestor) {
                "When" => {
                    if let Some(cond) = self.role(ancestor, "cond") {
                        self.graph.keep.insert(cond);
                        self.visit(cond)?;
                    }
                }
                "ForeignBlock" => self.graph.keep.extend(self.group(ancestor, "attributes")),
                "CompileSwitch" | "CompileSwitchExpr" => {
                    if let Some(selector) = self.role(ancestor, "selector") {
                        self.graph.keep.insert(selector);
                        self.visit(selector)?;
                    }
                    let source = &self.r.sources[node.file];
                    let matches = source.node(id).data["cases"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .flat_map(|case| case["matches"].as_array().into_iter().flatten())
                        .filter_map(Span::from_wire)
                        .filter_map(|span| source.at(span, id))
                        .map(|node_id| Ref {
                            file: node.file,
                            node: node_id,
                        })
                        .collect::<Vec<_>>();
                    for matched in matches {
                        self.graph.keep.insert(matched);
                        self.visit(matched)?;
                    }
                }
                _ => {}
            }
        }
        self.visit(node)
    }
    fn conditional_alternatives(&self, symbol: usize) -> Vec<Ref> {
        let symbol = &self.r.symbols[symbol];
        self.r
            .symbols
            .iter()
            .filter(|candidate| {
                candidate.name == symbol.name
                    && candidate.reference != symbol.reference
                    && ((candidate.reference.file == symbol.reference.file
                        && candidate.scope == symbol.scope)
                        || candidate.scope == NodeId(0)
                            && symbol.scope == NodeId(0)
                            && self.r.sources[candidate.reference.file].path.parent()
                                == self.r.sources[symbol.reference.file].path.parent())
            })
            .filter(|candidate| {
                self.r.sources[candidate.reference.file]
                    .ancestors(candidate.reference.node)
                    .any(|node| {
                        ["When", "CompileSwitch"].contains(
                            &self.r.sources[candidate.reference.file]
                                .node(node)
                                .kind
                                .as_str(),
                        )
                    })
            })
            .map(|candidate| candidate.reference)
            .collect()
    }
    fn reference(&self, node: Ref) -> bool {
        let source = &self.r.sources[node.file];
        let Some(parent) = source.node(node.node).parent else {
            return true;
        };
        !(["Declaration", "Field", "Range"].contains(&source.node(parent).kind.as_str())
            && source.group(parent, "names").contains(&node.node)
            || ["Selector", "FieldValue"].contains(&source.node(parent).kind.as_str())
                && source.role(parent, "field") == Some(node.node))
    }
    fn contract_body(&mut self, proc: Ref) -> bool {
        if let Some(declaration) = self.r.sources[proc.file].node(proc.node).parent {
            if self
                .r
                .attributes(Ref {
                    file: proc.file,
                    node: declaration,
                })
                .iter()
                .any(|(name, _)| name.starts_with("deferred_"))
            {
                return true;
            }
        }
        let Some(signature) = self.role(proc, "type") else {
            return false;
        };
        let Some(params) = self.role(signature, "params") else {
            return false;
        };
        self.children(params).into_iter().any(|field| {
            self.r.sources[field.file].node(field.node).data["using_field"] == true
                && self.role(field, "type").is_some_and(|annotation| {
                    self.r
                        .specialized_type(annotation, &Default::default())
                        .is_unknown()
                })
        })
    }
    fn visit(&mut self, node: Ref) -> Result<()> {
        if let Some((code, typ)) = self.query(node) {
            self.graph.queries.insert(node, code);
            if let Some(owner) = Self::type_definition(&typ) {
                if let Some(declaration) = self.r.sources[owner.file]
                    .node(owner.node)
                    .parent
                    .filter(|parent| self.r.sources[owner.file].node(*parent).kind == "Declaration")
                {
                    self.declaration(Ref {
                        file: owner.file,
                        node: declaration,
                    })?;
                } else if self.kind(owner).ends_with("Type") {
                    self.visit(owner)?;
                }
            }
            return Ok(());
        }
        if self.kind(node) == "Run" {
            self.graph.nested_runs.insert(node);
            if let Some(expr) = self.role(node, "expr") {
                self.visit(expr)?;
            }
            return Ok(());
        }
        if self.kind(node) == "Call" {
            let callee = self
                .role(node, "callee")
                .ok_or_else(|| Error::new("evaluation call omitted callee"))?;
            if self.io_call(node) {
                for arg in self.group(node, "args") {
                    self.visit(arg)?;
                }
                return Ok(());
            }
            if let Some(message) = self.r.private_call_reason(callee) {
                return Err(Error::at(
                    &self.r.sources[callee.file],
                    self.r.sources[callee.file].node(callee.node).span,
                    message,
                ));
            }
            if let Some(name) = self.builtin(node) {
                self.graph.calls.insert(callee, CallTarget::Builtin(name));
                for arg in self.group(node, "args") {
                    self.visit(arg)?;
                }
                return Ok(());
            }
            if let Some(library) = self.approved(callee) {
                self.graph.external.insert(callee, library);
                for arg in self.group(node, "args") {
                    self.visit(arg)?;
                }
                return Ok(());
            }
            let plans = self.r.call_plans(node, &Default::default());
            if let [plan] = plans.as_slice() {
                let target = plan.procedure;
                if self.kind(target) == "Procedure" && self.role(target, "body").is_some() {
                    self.graph
                        .calls
                        .insert(callee, CallTarget::Procedure(target));
                    self.graph.procedures.insert(target);
                    if let Some(declaration) = self.r.sources[target.file].node(target.node).parent
                    {
                        self.declaration(Ref {
                            file: target.file,
                            node: declaration,
                        })?;
                    }
                    if let Some(symbol) = self.r.symbol(callee) {
                        for alternative in self.conditional_alternatives(symbol) {
                            self.declaration(alternative)?;
                        }
                    }
                    for arg in self.group(node, "args") {
                        self.visit(arg)?;
                    }
                    return Ok(());
                }
            }
        }
        if self.kind(node) == "Procedure" && self.role(node, "body").is_some() {
            self.graph.procedures.insert(node);
            if self.contract_body(node) {
                for child in self.children(node) {
                    if Some(child) != self.role(node, "body") {
                        self.visit(child)?;
                    }
                }
                return Ok(());
            }
        }
        if self.kind(node) == "Ident" && self.reference(node) {
            let source = &self.r.sources[node.file];
            let name = source.text(node.node);
            let local = self.r.lookup(node, name).is_some_and(|index| {
                self.r.sources[self.r.symbols[index].reference.file]
                    .path
                    .parent()
                    == source.path.parent()
            });
            if !local && self.selection_import(node.file, name).is_some() {
                self.graph.aliases.insert((node.file, name.into()));
            }
            if self.r.lookup(node, name).is_none() {
                for (index, _) in source
                    .nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, import)| import.kind == "Import")
                {
                    let import = Ref {
                        file: node.file,
                        node: NodeId(index),
                    };
                    if self.import_alias(import).as_deref() == Some(name) {
                        self.graph.imports.insert(import);
                    }
                }
            }
            if let Some(symbol) = self.r.symbol(node) {
                let declaration = self.r.symbols[symbol].reference;
                if declaration != node
                    && !self.r.sources[node.file]
                        .ancestors(node.node)
                        .any(|parent| {
                            declaration
                                == Ref {
                                    file: node.file,
                                    node: parent,
                                }
                        })
                {
                    if self
                        .owner(declaration)
                        .is_none_or(|owner| !self.graph.procedures.contains(&owner))
                    {
                        self.declaration(declaration)?;
                    }
                    for alternative in self.conditional_alternatives(symbol) {
                        self.declaration(alternative)?;
                    }
                }
            }
        }
        if self.kind(node) == "Selector" {
            if let Some(symbol) = self.r.symbol(node) {
                self.declaration(self.r.symbols[symbol].reference)?;
                for alternative in self.conditional_alternatives(symbol) {
                    self.declaration(alternative)?;
                }
            }
        }
        if self.kind(node) == "Call" {
            if let Some(callee) = self
                .role(node, "callee")
                .filter(|callee| !self.r.denotes_type(*callee))
            {
                for target in self.r.candidates(callee) {
                    if self.kind(target) == "Procedure" && self.role(target, "body").is_some() {
                        self.graph.procedures.insert(target);
                        if let Some(declaration) =
                            self.r.sources[target.file].node(target.node).parent
                        {
                            if self.r.sources[target.file].node(declaration).kind == "Declaration" {
                                self.declaration(Ref {
                                    file: target.file,
                                    node: declaration,
                                })?;
                            }
                        }
                    }
                }
            }
        }
        for child in self.children(node) {
            self.visit(child)?;
        }
        Ok(())
    }
}
