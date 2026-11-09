//! Source-preserving native emitter with explicit guards for known migration
//! gaps. Differential acceptance, not compilation alone, establishes parity.
use crate::{
    ast::{NodeId, Source, Span},
    diagnostic::{Error, Result},
    emit::{Code, Emitted},
    semantics::{Ref, Resolver, Type},
};
use serde_json::json;
use std::collections::{BTreeMap, HashMap, HashSet};

#[derive(Clone, Debug)]
pub struct Hint {
    pub code: Code,
    pub element_only: bool,
    pub plain_array: bool,
}
pub struct Lower<'a, 'r> {
    pub source: &'a Source,
    pub file: usize,
    pub resolver: &'r mut Resolver<'a>,
    edits: Vec<(Span, Code)>,
    selected: HashMap<String, String>,
    modules: BTreeMap<String, String>,
    helpers: BTreeMap<(NodeId, String), Code>,
    captures: Vec<HashMap<usize, Code>>,
    fstring_plans: HashMap<NodeId, HashSet<NodeId>>,
    fstring_frames: Vec<(String, HashSet<NodeId>)>,
    fstring_busy: HashSet<NodeId>,
    querying: usize,
    default: bool,
    meta: bool,
    builtin: bool,
    pub errors: HashMap<String, String>,
    blocked: HashMap<NodeId, String>,
}
impl<'a, 'r> Lower<'a, 'r> {
    pub fn new(file: usize, resolver: &'r mut Resolver<'a>) -> Result<Self> {
        let source = &resolver.sources[file];
        let mut this = Self {
            source,
            file,
            resolver,
            edits: vec![],
            selected: HashMap::new(),
            modules: BTreeMap::new(),
            helpers: BTreeMap::new(),
            captures: vec![],
            fstring_plans: HashMap::new(),
            fstring_frames: vec![],
            fstring_busy: HashSet::new(),
            querying: 0,
            default: false,
            meta: false,
            builtin: false,
            errors: HashMap::new(),
            blocked: HashMap::new(),
        };
        for edit in &source.edits {
            this.edits
                .push((edit.span, this.gen(edit.text.clone(), edit.span, "syntax")));
        }
        this.imports()?;
        this.resources()?;
        this.storage()?;
        this.discover_fstrings();
        Ok(this)
    }
    fn gen(&self, text: impl Into<String>, span: Span, kind: &str) -> Code {
        Code::generated(text, &self.source.path, span, kind)
    }
    fn g(&self, text: impl Into<String>, id: NodeId) -> Code {
        self.gen(text, self.source.node(id).span, "generated")
    }
    fn copy(&self, span: Span) -> Code {
        Code::source(self.source.slice(span), &self.source.path, span)
    }
    fn reference(&self, id: NodeId) -> Ref {
        Ref {
            file: self.file,
            node: id,
        }
    }
    fn typ(&mut self, id: NodeId) -> Type {
        self.resolver.type_of(self.reference(id))
    }
    fn role(&self, id: NodeId, name: &str) -> Result<NodeId> {
        self.source.role(id, name).ok_or_else(|| {
            Error::new(format!(
                "parser omitted {name} role for {}",
                self.source.node(id).kind
            ))
        })
    }
    fn module(&mut self, name: &str) -> String {
        let alias = format!("{}{name}", self.source.prefix);
        self.modules.insert(alias.clone(), format!("thor:{name}"));
        alias
    }
    fn native(&mut self, name: &str) -> String {
        self.builtin = true;
        format!("{}builtin.{name}", self.source.prefix)
    }
    fn unsupported(&self, id: NodeId, name: &str) -> Error {
        Error::unsupported(self.source, self.source.node(id).span, name)
    }
    fn error_token(&mut self, id: NodeId, message: &str) -> Code {
        let token = format!(
            "{}expression_error_{}",
            self.source.prefix,
            self.source.node(id).span.start
        );
        self.errors.insert(token.clone(), message.into());
        self.g(token, id)
    }
    pub fn finish(mut self) -> Result<(Emitted, HashMap<String, String>)> {
        if self.source.excluded {
            return Ok((
                self.copy(Span::new(0, self.source.raw.len())).finish(),
                self.errors,
            ));
        }
        if !self.source.thor {
            return Ok((
                self.splice(NodeId(0), vec![], vec![])?.finish(),
                self.errors,
            ));
        }
        self.preflight()?;
        let mut code = vec![self.render(NodeId(0), None, false)?];
        let helpers: Vec<_> = self
            .helpers
            .iter()
            .filter(|((owner, _), _)| owner.0 == 0)
            .map(|(_, code)| code.clone())
            .collect();
        code.extend(helpers);
        if self.default {
            let name = format!("{}default_value", self.source.prefix);
            code.push(self.gen(format!("\n@(private=\"file\")\n{name} :: proc \"contextless\" (value: $T) -> T {{ return value }}\n"),Span::new(0,0),"support"));
        }
        if self.meta {
            code.push(self.gen(
                format!("\nimport {}meta \"base:intrinsics\"\n", self.source.prefix),
                Span::new(0, 0),
                "support",
            ));
        }
        if self.builtin {
            code.push(self.gen(
                format!("\nimport {}builtin \"base:builtin\"\n", self.source.prefix),
                Span::new(0, 0),
                "support",
            ));
        }
        for (alias, path) in &self.modules {
            code.push(self.gen(
                format!("\nimport {alias} {}\n", json!(path)),
                Span::new(0, 0),
                "support",
            ));
        }
        Ok((Code::concat(code).finish(), self.errors))
    }
    fn preflight(&mut self) -> Result<()> {
        for (i, node) in self.source.nodes.iter().enumerate() {
            let id = NodeId(i);
            if self
                .resolver
                .conditional_immutables
                .contains(&self.reference(id))
            {
                return Err(self.unsupported(id, "conditional immutable binding selection"));
            }
            if node.kind == "Using" {
                for value in self.source.group(id, "values") {
                    if self.protected(*value).is_some() {
                        return Err(self.unsupported(id, "using-promoted immutable fields"));
                    }
                }
            }
            if node.immutable && node.data["using_field"] == true {
                return Err(self.unsupported(id, "using-promoted immutable declarations"));
            }
            if node.data["pure"] == true {
                return Err(self.unsupported(id, "pure-function verification and execution"));
            }
            if matches!(
                node.kind.as_str(),
                "Binary" | "Ternary" | "WhenExpr" | "Index" | "Slice" | "Selector" | "Unary"
            ) {
                let temporary = self.source.nodes.iter().enumerate().any(|(i, n)| {
                    n.kind == "Interpolation"
                        && n.data["manual_lifetime"] != true
                        && self.source.is_descendant(NodeId(i), id)
                });
                let managed = self.fstring_plans.values().any(|targets| {
                    targets
                        .iter()
                        .any(|target| self.source.is_descendant(*target, id))
                });
                if temporary && !managed {
                    return Err(
                        self.unsupported(id, "anonymous-string ownership in composite expressions")
                    );
                }
            }
            if node.kind == "Assignment"
                && self
                    .source
                    .group(id, "lhs")
                    .iter()
                    .all(|lhs| self.source.text(*lhs) == "_")
                && self
                    .source
                    .group(id, "rhs")
                    .iter()
                    .any(|rhs| self.anonymous_string(*rhs))
            {
                return Err(self.unsupported(id, "discarded anonymous-string cleanup"));
            }
            if matches!(
                node.kind.as_str(),
                "Run"
                    | "Comprehension"
                    | "SwitchExpr"
                    | "CompileSwitchExpr"
                    | "CompileSwitch"
                    | "SetType"
            ) {
                return Err(self.unsupported(id, &node.kind));
            }
            if node.kind == "Ident"
                && matches!(self.source.text(id), "Integer" | "Rational")
                && self.resolver.symbol(self.reference(id)).is_none()
            {
                return Err(self.unsupported(id, "arbitrary-precision numbers"));
            }
            if node.kind == "Range"
                && (self.source.role(id, "value_type").is_some()
                    || self.source.role(id, "index_type").is_some())
            {
                return Err(self.unsupported(id, "typed loop bindings"));
            }
            if node.kind == "Slice" {
                let base = self.role(id, "expr")?;
                if matches!(self.typ(base), Type::Array { .. }) && self.protected(base).is_some() {
                    return Err(
                        self.unsupported(id, "read-only array views and slice borrowing analysis")
                    );
                }
            }
            if node.kind == "Cast"
                && self
                    .source
                    .role(id, "type")
                    .is_some_and(|target| matches!(self.typ(target), Type::Slice(_)))
            {
                let base = self.role(id, "expr")?;
                if self.protected(base).is_some() {
                    return Err(self.unsupported(id, "read-only array casts to slices"));
                }
            }
            if node.kind == "Call" && self.source.group(id, "args").len() == 1 {
                let callee = self.role(id, "callee")?;
                let argument = self.source.group(id, "args")[0];
                if matches!(self.typ(callee), Type::Slice(_)) && self.protected(argument).is_some()
                {
                    return Err(self.unsupported(id, "read-only array conversions to slices"));
                }
            }
            if node.immutable {
                for symbol in self
                    .resolver
                    .symbols
                    .iter()
                    .filter(|sym| sym.reference == self.reference(id))
                    .cloned()
                    .collect::<Vec<_>>()
                {
                    let typ = self.resolver.type_of(Ref {
                        file: self.file,
                        node: symbol.name_node,
                    });
                    if !self.inline_type(&typ, &mut HashSet::new()) {
                        return Err(
                            self.unsupported(id, "immutable slice/aggregate borrowing analysis")
                        );
                    }
                }
            }
        }
        if self.source.info["mut_parameters"]
            .as_array()
            .is_some_and(|rows| !rows.is_empty())
        {
            return Err(self.unsupported(NodeId(0), "#mut slice contracts"));
        }
        Ok(())
    }
    fn inline_type(&mut self, typ: &Type, seen: &mut HashSet<Ref>) -> bool {
        match typ {
            Type::Scalar { name, .. } => name != "any",
            Type::String { .. } | Type::Enum(_) => true,
            Type::Array { elem, .. } => self.inline_type(elem, seen),
            Type::Struct(owner) => {
                if !seen.insert(*owner) {
                    return true;
                }
                let source = &self.resolver.sources[owner.file];
                if let Some(fields) = source.role(owner.node, "fields") {
                    for field in &source.node(fields).children {
                        if let Some(node) = source.role(*field, "type") {
                            let field_type = self.resolver.type_of(Ref {
                                file: owner.file,
                                node,
                            });
                            if !self.inline_type(&field_type, seen) {
                                return false;
                            }
                        }
                    }
                }
                true
            }
            _ => false,
        }
    }
    fn imports(&mut self) -> Result<()> {
        let s = self.source;
        for selection in &s.selections {
            if selection.wildcard {
                return Err(Error::unsupported(
                    s,
                    Span::new(selection.start, selection.end),
                    "wildcard export discovery",
                ));
            }
            let path = unquote(&selection.path)?;
            let mut namespace = if selection.package_name.is_empty() {
                path.rsplit(['/', ':']).next().unwrap_or("").to_string()
            } else {
                selection.package_name.clone()
            };
            if namespace == "_" {
                namespace = format!("{}import_{}", s.prefix, selection.start);
                let import = s.nodes.iter().enumerate().rev().find(|(_, n)| {
                    n.kind == "Import" && n.span.end <= selection.start && n.op == selection.path
                });
                if let Some((_, node)) = import {
                    if let Some(span) = node.spans.get("alias") {
                        self.edits
                            .push((*span, self.gen(namespace.clone(), *span, "import")));
                    }
                }
            }
            let alias = format!("{}import_{}", s.prefix, self.modules.len());
            self.modules.insert(alias.clone(), path);
            for (i, name) in selection.names.iter().enumerate() {
                let start = selection
                    .positions
                    .get(i)
                    .and_then(|v| v["offset"].as_u64())
                    .unwrap_or(selection.start as u64) as usize;
                let span = Span::new(start, start + name.len());
                if self
                    .selected
                    .insert(name.clone(), format!("{alias}.{name}"))
                    .is_some()
                {
                    return Err(Error::at(s, span, "duplicate name in selective import"));
                }
                // Resolve even unused selected names without making them package
                // exports. The init-local aliases retain native privacy checks.
                let owner = NodeId(0);
                let name_key = format!("validate_import_{start}");
                let body=format!("\n@(private=\"file\", init)\n{}{name_key} :: proc \"contextless\" () {{ checked :: {alias}.{name} }}\n",s.prefix);
                self.helpers
                    .insert((owner, name_key), self.gen(body, span, "import"));
                if let Some(symbol) = self.resolver.lookup(
                    Ref {
                        file: self.file,
                        node: NodeId(0),
                    },
                    name,
                ) {
                    let declaration = self.resolver.symbols[symbol].reference;
                    if declaration.file == self.file {
                        return Err(Error::at(
                            s,
                            s.node(self.resolver.symbols[symbol].name_node).span,
                            format!("declaration conflicts with selective import: {name}"),
                        ));
                    }
                }
            }
            let mut comments = String::from("\n");
            for span in &s.comments {
                if selection.start <= span.start && span.end <= selection.end {
                    comments.push_str(s.slice(*span));
                    comments.push('\n');
                }
            }
            let span = Span::new(selection.start, selection.end);
            self.edits.push((span, self.gen(comments, span, "syntax")));
            let _ = namespace;
        }
        Ok(())
    }
    fn protected(&mut self, id: NodeId) -> Option<String> {
        let source = self.source;
        let node = source.node(id);
        if let Some(symbol) = self.resolver.symbol(self.reference(id)) {
            let sym = &self.resolver.symbols[symbol];
            if sym.immutable {
                return Some(sym.name.clone());
            }
            return None;
        }
        match node.kind.as_str() {
            "Paren" => self.protected(source.role(id, "expr")?),
            "Selector" | "Index" => {
                let base = source.role(id, "expr")?;
                let typ = self.typ(base);
                if matches!(
                    typ,
                    Type::Pointer(_) | Type::Slice(_) | Type::Dynamic(_) | Type::String { .. }
                ) {
                    None
                } else {
                    self.protected(base)
                }
            }
            _ => None,
        }
    }
    fn storage(&mut self) -> Result<()> {
        for (i, node) in self.source.nodes.iter().enumerate() {
            let id = NodeId(i);
            let targets = if node.kind == "Assignment" {
                self.source
                    .group(id, "lhs")
                    .iter()
                    .map(|id| (*id, *id, "write"))
                    .collect::<Vec<_>>()
            } else if node.kind == "Unary" && node.op == "&" {
                vec![(id, self.role(id, "expr")?, "take a mutable address of")]
            } else if node.kind == "Range"
                && self.source.group(id, "names").iter().any(|name| {
                    self.source.node(*name).kind == "Unary" && self.source.node(*name).op == "&"
                })
            {
                let expr = self.role(id, "expr")?;
                vec![(expr, expr, "iterate by mutable reference over")]
            } else if node.kind == "Call"
                && self.source.role(id, "callee").is_some_and(|callee| {
                    matches!(self.source.text(callee), "raw_data" | "soa_unzip")
                        && self.resolver.symbol(self.reference(callee)).is_none()
                })
            {
                if let Some(arg) = self.source.group(id, "args").first() {
                    if matches!(self.typ(*arg), Type::Array { .. }) {
                        vec![(id, *arg, "obtain a mutable data pointer to")]
                    } else {
                        vec![]
                    }
                } else {
                    vec![]
                }
            } else {
                vec![]
            };
            for (origin, target, action) in targets {
                if let Some(name) = self.protected(target) {
                    self.blocked.insert(
                        origin,
                        format!("Cannot {action} immutable storage '{name}'"),
                    );
                }
            }
        }
        Ok(())
    }
    fn resources(&mut self) -> Result<()> {
        for (i, node) in self.source.nodes.iter().enumerate() {
            let id = NodeId(i);
            let paths = if node.kind == "ForeignImport" {
                self.source.group(id, "paths").to_vec()
            } else if node.kind == "Call"
                && self.source.role(id, "callee").is_some_and(|callee| {
                    matches!(
                        self.source.text(callee),
                        "#load" | "#load_hash" | "#load_directory" | "#exists"
                    )
                })
            {
                self.source
                    .group(id, "args")
                    .first()
                    .copied()
                    .into_iter()
                    .collect()
            } else {
                vec![]
            };
            for literal in paths {
                if self.source.node(literal).kind != "Literal" {
                    continue;
                }
                let path = unquote(self.source.text(literal))?;
                let path = if let Some((collection, suffix)) = path.split_once(':') {
                    self.source
                        .collection_roots
                        .get(collection)
                        .map(|base| base.join(suffix))
                } else if std::path::Path::new(&path).is_absolute() {
                    None
                } else {
                    Some(
                        self.source
                            .path
                            .parent()
                            .ok_or_else(|| Error::new("source has no parent"))?
                            .join(path),
                    )
                };
                if let Some(path) = path {
                    let path = crate::project::absolute(&path)?;
                    let span = self.source.node(literal).span;
                    self.edits.push((
                        span,
                        self.gen(json!(path.to_string_lossy()).to_string(), span, "resource"),
                    ));
                }
            }
        }
        Ok(())
    }
    fn splice(
        &self,
        id: NodeId,
        mut replacements: Vec<(Span, Code)>,
        inserts: Vec<(usize, Code)>,
    ) -> Result<Code> {
        let span = self.source.node(id).span;
        let mut chosen: Vec<(Span, Code)> = vec![];
        replacements.sort_by_key(|(span, _)| (span.start, std::cmp::Reverse(span.end)));
        for (child, code) in replacements {
            if span.contains(child)
                && !child.is_empty()
                && !chosen.iter().any(|(outer, _)| outer.contains(child))
            {
                chosen.push((child, code));
            }
        }
        for (edit, code) in &self.edits {
            if span.contains(*edit)
                && !edit.is_empty()
                && !chosen.iter().any(|(outer, _)| outer.contains(*edit))
            {
                chosen.push((*edit, code.clone()));
            }
        }
        for (offset, code) in inserts {
            if span.start <= offset
                && offset <= span.end
                && !chosen
                    .iter()
                    .any(|(child, _)| child.start < offset && offset < child.end)
            {
                chosen.push((Span::new(offset, offset), code));
            }
        }
        chosen.sort_by_key(|(span, _)| (span.start, span.end));
        let mut cursor = span.start;
        let mut parts = vec![];
        for (child, code) in chosen {
            if child.start < cursor {
                return Err(Error::new(format!(
                    "overlapping source transformations in {}",
                    self.source.path.display()
                )));
            }
            parts.push(self.copy(Span::new(cursor, child.start)));
            parts.push(code);
            cursor = child.end;
        }
        parts.push(self.copy(Span::new(cursor, span.end)));
        Ok(Code::concat(parts))
    }
    pub fn render(&mut self, id: NodeId, expected: Option<Hint>, constant: bool) -> Result<Code> {
        if let Some(message) = self.blocked.get(&id).cloned() {
            return Ok(self.error_token(id, &message));
        }
        let node = self.source.node(id);
        let kind = node.kind.clone();
        if kind == "Procedure" && !self.fstring_frames.is_empty() {
            let frames = std::mem::take(&mut self.fstring_frames);
            let code = self.render(id, expected, constant);
            self.fstring_frames = frames;
            return code;
        }
        if !constant && self.querying == 0 && !self.fstring_busy.contains(&id) {
            if let Some(targets) = self.fstring_plans.get(&id).cloned() {
                return self.fstring_expression(id, expected, targets);
            }
        }
        if kind == "Interpolation" {
            let code = self.interpolation(id)?;
            if self.querying == 0 {
                if let Some((pointer, _)) = self
                    .fstring_frames
                    .iter()
                    .rev()
                    .find(|(_, targets)| targets.contains(&id))
                {
                    let pointer = pointer.clone();
                    let module = self.module("temporaries");
                    return Ok(Code::concat([
                        self.g(format!("{module}.capture({pointer},"), id),
                        code,
                        self.g(")", id),
                    ]));
                }
            }
            return Ok(code);
        }
        if kind == "Ident" {
            if let Some(symbol) = self.resolver.symbol(self.reference(id)) {
                for captures in self.captures.iter().rev() {
                    if let Some(code) = captures.get(&symbol) {
                        return Ok(code.clone());
                    }
                }
            }
            if let Some(target) = self.selected.get(self.source.text(id)).filter(|_| {
                self.resolver
                    .lookup(self.reference(id), self.source.text(id))
                    .is_none()
            }) {
                let parent = node.parent;
                let binding = parent.is_some_and(|p| {
                    let parent = self.source.node(p);
                    matches!(
                        parent.kind.as_str(),
                        "ImplicitSelector" | "EnumType" | "PolyType"
                    ) || matches!(parent.kind.as_str(), "Selector" | "FieldValue")
                        && self.source.role(p, "field") == Some(id)
                        || matches!(parent.kind.as_str(), "Declaration" | "Field" | "Range")
                            && self.source.group(p, "names").contains(&id)
                });
                if !binding {
                    return Ok(self
                        .g(target.clone(), id)
                        .expansion(&self.source.path, node.span));
                }
            }
        }
        if kind == "Compound" && self.is_array(id) {
            return self.array(id, expected, None, constant);
        }
        if kind == "Binary" {
            match node.op.as_str() {
                "in" | "not_in" => return self.membership(id, constant),
                "++" => return self.concatenate(id, expected),
                "**" => return self.power(id, constant),
                "*" => {
                    let a = self.role(id, "left")?;
                    let b = self.role(id, "right")?;
                    if matches!(self.typ(a), Type::String { .. })
                        || matches!(self.typ(b), Type::String { .. })
                    {
                        return self.repeat(id, a, b);
                    }
                }
                _ => {}
            }
        }
        if kind == "OrPanic" {
            return self.or_panic(id);
        }
        if kind == "Call" {
            let callee = self.role(id, "callee")?;
            if self.source.node(callee).kind == "Selector" {
                let base = self.role(callee, "expr")?;
                let field = self.role(callee, "field")?;
                let namespace = self.source.text(base);
                let format_import = self.source.nodes.iter().any(|node| {
                    node.kind == "Import"
                        && unquote(&node.op).ok().as_deref() == Some("thor:format")
                        && (self.source.names.get(node.name) == namespace
                            || self.source.names.get(node.name).is_empty() && namespace == "format")
                });
                if format_import
                    && self.source.text(field) == "group_integer"
                    && self.resolver.symbol(self.reference(base)).is_none()
                {
                    if let Some(arg) = self.source.group(id, "args").first() {
                        if matches!(self.typ(*arg),Type::Scalar{name,..} if name.starts_with('f')) {
                            return Ok(
                                self.error_token(id, "Integer grouping requires an integer value.")
                            );
                        }
                    }
                }
            }
        }
        if matches!(kind.as_str(), "Index" | "Slice") {
            if let Some(code) = self.access(id, constant)? {
                return Ok(code);
            }
        }
        let mut overrides: HashMap<NodeId, (Option<Hint>, bool)> = HashMap::new();
        match kind.as_str() {
            "Declaration" => {
                let hint = if let Some(annotation) = self.source.role(id, "type") {
                    Some(Hint {
                        code: self.render(annotation, None, false)?,
                        element_only: false,
                        plain_array: self.source.node(annotation).kind == "ArrayType",
                    })
                } else {
                    None
                };
                for value in self.source.group(id, "values") {
                    overrides.insert(*value, (hint.clone(), !node.mutable));
                }
            }
            "Assignment" => {
                for (lhs, rhs) in self
                    .source
                    .group(id, "lhs")
                    .iter()
                    .zip(self.source.group(id, "rhs"))
                {
                    let left = self.render(*lhs, None, false)?;
                    let type_of = self.native("type_of");
                    let hint = if self.source.text(*lhs) == "_" {
                        None
                    } else {
                        Some(Hint {
                            code: Code::concat([
                                self.g(format!("{type_of}("), id),
                                left,
                                self.g(")", id),
                            ]),
                            element_only: false,
                            plain_array: false,
                        })
                    };
                    overrides.insert(*rhs, (hint, constant));
                }
            }
            "Field" => {
                if let (Some(annotation), Some(value)) = (
                    self.source.role(id, "type"),
                    self.source.role(id, "default"),
                ) {
                    let code = self.render(annotation, None, false)?;
                    overrides.insert(
                        value,
                        (
                            Some(Hint {
                                code,
                                element_only: false,
                                plain_array: self.source.node(annotation).kind == "ArrayType",
                            }),
                            constant,
                        ),
                    );
                }
            }
            "Procedure" => {
                for child in &node.children {
                    overrides.insert(*child, (None, false));
                }
                for clause in self.source.group(id, "where") {
                    overrides.insert(*clause, (None, true));
                }
            }
            "When" | "WhenExpr" => {
                if let Some(cond) = self.source.role(id, "cond") {
                    overrides.insert(cond, (None, true));
                }
            }
            "ArrayType" => {
                if let Some(len) = self.source.role(id, "len") {
                    overrides.insert(len, (None, true));
                }
            }
            "Paren" | "FieldValue" => {
                if let Some(expr) = self
                    .source
                    .role(id, if kind == "Paren" { "expr" } else { "value" })
                {
                    overrides.insert(expr, (expected.clone(), constant));
                }
            }
            "Return" => {
                if let Some(owner) = self
                    .source
                    .ancestors(id)
                    .find(|id| self.source.node(*id).kind == "Procedure")
                {
                    if let Some(signature) = self.source.role(owner, "type") {
                        let results = self.resolver.fields(self.reference(signature), "results");
                        for (value, (_, annotation)) in
                            self.source.group(id, "values").iter().zip(results)
                        {
                            if let Some(annotation) = annotation.filter(|r| r.file == self.file) {
                                let code = self.render(annotation.node, None, false)?;
                                overrides.insert(
                                    *value,
                                    (
                                        Some(Hint {
                                            code,
                                            element_only: false,
                                            plain_array: self.source.node(annotation.node).kind
                                                == "ArrayType",
                                        }),
                                        constant,
                                    ),
                                );
                            }
                        }
                    }
                }
            }
            "Call" => {
                let callee = self.role(id, "callee")?;
                if let Some(signature) = self.resolver.signature(self.reference(callee)) {
                    let params = self.resolver.fields(signature, "params");
                    for (i, arg) in self.source.group(id, "args").iter().enumerate() {
                        let index = if self.source.node(*arg).kind == "FieldValue" {
                            let name = self
                                .source
                                .role(*arg, "field")
                                .map(|id| self.source.text(id))
                                .unwrap_or("");
                            params.iter().position(|(p, _)| p == name).unwrap_or(i)
                        } else {
                            i
                        };
                        if let Some((_, Some(annotation))) = params.get(index) {
                            if annotation.file == self.file
                                && self.source.text(annotation.node).contains('$')
                            {
                                continue;
                            }
                            self.meta = true;
                            let target = self.render(callee, None, false)?;
                            let type_of = self.native("type_of");
                            let code = Code::concat([
                                self.g(
                                    format!(
                                        "#type {}meta.type_proc_parameter_type({type_of}(",
                                        self.source.prefix
                                    ),
                                    id,
                                ),
                                target,
                                self.g(format!("), {index})"), id),
                            ]);
                            overrides.insert(
                                *arg,
                                (
                                    Some(Hint {
                                        code,
                                        element_only: false,
                                        plain_array: false,
                                    }),
                                    constant,
                                ),
                            );
                        }
                    }
                }
            }
            _ => {}
        }
        if kind == "Ternary" {
            let cond = self.role(id, "cond")?;
            let x = self.role(id, "x")?;
            let y = self.role(id, "y")?;
            let cond = self.render(cond, None, constant)?;
            let a = self.render(x, expected.clone(), constant)?;
            let b = self.render(y, expected.clone(), constant)?;
            return Ok(if constant {
                Code::concat([
                    self.g("(", id),
                    a,
                    self.g(" when (", id),
                    cond,
                    self.g(") else ", id),
                    b,
                    self.g(")", id),
                ])
            } else {
                Code::concat([
                    self.g("((", id),
                    cond,
                    self.g(") ? (", id),
                    a,
                    self.g(") : (", id),
                    b,
                    self.g("))", id),
                ])
            }
            .expansion(&self.source.path, node.span));
        }
        let mut replacements = vec![];
        for child in &node.children {
            let (hint, con) = overrides.get(child).cloned().unwrap_or((None, constant));
            replacements.push((
                self.source.node(*child).span,
                self.render(*child, hint, con)?,
            ));
        }
        let mut inserts = vec![];
        if id.0 == 0 {
            let has_flag = self.source.info["file_tags"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|v| v.as_str())
                .any(|tag| {
                    tag.contains("feature")
                        && tag
                            .split_whitespace()
                            .any(|s| s == "using-stmt" || s == "!using-stmt")
                });
            if !has_flag {
                let origin = self.source.nodes.iter().find(|n| {
                    n.kind == "Using"
                        || n.kind == "Field"
                            && n.data["using_field"] == true
                            && n.parent.is_some_and(|p| {
                                self.source.node(p).kind == "Fields"
                                    && self.source.node(p).parent.is_some_and(|p| {
                                        self.source.node(p).kind == "ProcedureType"
                                            && self.source.role(p, "params") == n.parent
                                    })
                            })
                });
                if let Some(origin) = origin {
                    let newline = if self.source.raw.contains("\r\n") {
                        "\r\n"
                    } else {
                        "\n"
                    };
                    let offset = if self.source.raw.starts_with('\u{feff}') {
                        3
                    } else {
                        0
                    };
                    inserts.push((
                        offset,
                        self.gen(
                            format!("#+feature using-stmt{newline}"),
                            origin.span,
                            "support",
                        ),
                    ));
                }
            }
        }
        if kind == "Block" {
            let helpers: Vec<_> = self
                .helpers
                .iter()
                .filter(|((owner, _), _)| *owner == id)
                .map(|(_, code)| code.clone())
                .collect();
            if !helpers.is_empty() {
                if self.source.raw.as_bytes()[node.span.start] == b'{' {
                    inserts.push((node.span.start + 1, Code::concat(helpers)));
                } else {
                    return Err(self.unsupported(id, "generated expression helpers in do bodies"));
                }
            }
        }
        self.splice(id, replacements, inserts)
    }
    fn is_array(&self, id: NodeId) -> bool {
        self.source.node(id).kind == "Compound"
            && self
                .source
                .node(id)
                .spans
                .get("open")
                .is_some_and(|span| self.source.slice(*span) == "[")
    }
    fn array(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        needle: Option<NodeId>,
        constant: bool,
    ) -> Result<Code> {
        let elems = self.source.group(id, "elems").to_vec();
        let mut first_rendered = None;
        let mut target = None;
        let element = if let Some(hint) = &expected {
            if hint.element_only {
                hint.code.clone()
            } else {
                self.meta = true;
                target = Some(hint.code.clone());
                Code::concat([
                    self.g(format!("{}meta.type_elem_type(", self.source.prefix), id),
                    hint.code.clone(),
                    self.g(")", id),
                ])
            }
        } else {
            let anchor = needle
                .filter(|needle| !self.typ(*needle).untyped())
                .or_else(|| elems.first().copied())
                .or(needle);
            if let Some(anchor) = anchor {
                let code = self.render(anchor, None, constant)?;
                if elems.first() == Some(&anchor) {
                    first_rendered = Some(code.clone());
                }
                self.default = true;
                let type_of = self.native("type_of");
                Code::concat([
                    self.g(
                        format!("{type_of}({}default_value(", self.source.prefix),
                        id,
                    ),
                    code,
                    self.g("))", id),
                ])
            } else {
                return Ok(self.error_token(
                    id,
                    "An empty array literal needs an element type from its context.",
                ));
            }
        };
        let hint = Hint {
            code: element.clone(),
            element_only: false,
            plain_array: false,
        };
        let mut values = vec![];
        for (i, value) in elems.iter().enumerate() {
            if i != 0 {
                values.push(self.g(", ", id));
            }
            values.push(if i == 0 && first_rendered.is_some() {
                first_rendered.clone().unwrap()
            } else {
                self.render(*value, Some(hint.clone()), constant)?
            });
        }
        for span in &self.source.comments {
            if self.source.node(id).span.contains(*span)
                && !elems
                    .iter()
                    .any(|e| self.source.node(*e).span.contains(*span))
            {
                values.insert(
                    0,
                    self.gen(
                        format!("{}\n", self.source.slice(*span)),
                        *span,
                        "source-comment",
                    ),
                );
            }
        }
        let mut literal = Code::concat([
            self.g(format!("([{}]", elems.len()), id),
            element,
            self.g("{", id),
            Code::concat(values),
            self.g("})", id),
        ]);
        if let Some(target) = target {
            if !expected.as_ref().is_some_and(|hint| hint.plain_array) {
                literal = Code::concat([
                    self.g("cast(", id),
                    target,
                    self.g(")(", id),
                    literal,
                    self.g(")", id),
                ]);
            }
        }
        Ok(literal.expansion(&self.source.path, self.source.node(id).span))
    }
    fn membership(&mut self, id: NodeId, constant: bool) -> Result<Code> {
        let left = self.role(id, "left")?;
        let right = self.role(id, "right")?;
        let mut sequence = right;
        while self.source.node(sequence).kind == "Paren" {
            sequence = self.role(sequence, "expr")?;
        }
        let op = self.source.node(id).op.clone();
        if self.source.node(sequence).kind == "Binary"
            && matches!(self.source.node(sequence).op.as_str(), ".." | "..<" | "..=")
        {
            return Err(self.unsupported(id, "range membership"));
        }
        let type_membership =
            self.resolver.denotes_type(self.reference(left)) && self.is_array(sequence);
        if (constant || type_membership) && self.is_array(sequence) {
            let elems = self.source.group(sequence, "elems").to_vec();
            let lhs = self.render(left, None, true)?;
            let rhs = if type_membership {
                self.g("", id)
            } else {
                self.array(sequence, None, Some(left), true)?
            };
            let mut parts = vec![self.g("(", id)];
            if elems.is_empty() {
                parts.extend([
                    self.g("((", id),
                    lhs.clone(),
                    self.g(")==(", id),
                    lhs.clone(),
                    self.g(
                        if op == "in" {
                            ")) && false"
                        } else {
                            ")) || true"
                        },
                        id,
                    ),
                ]);
            }
            for (i, elem) in elems.iter().enumerate() {
                if i > 0 {
                    parts.push(self.g(if op == "in" { " || " } else { " && " }, id));
                }
                if type_membership {
                    parts.extend([
                        self.g("((", id),
                        lhs.clone(),
                        self.g(if op == "in" { ") == (" } else { ") != (" }, id),
                        self.render(*elem, None, true)?,
                        self.g("))", id),
                    ]);
                } else {
                    parts.extend([
                        self.g("((", id),
                        lhs.clone(),
                        self.g(if op == "in" { ") == ((" } else { ") != ((" }, id),
                        rhs.clone(),
                        self.g(format!(")[{i}]))"), id),
                    ]);
                }
            }
            parts.push(self.g(")", id));
            return Ok(Code::concat(parts).expansion(&self.source.path, self.source.node(id).span));
        }
        let typ = self.typ(right);
        if matches!(typ, Type::Map { .. }) {
            let a = self.render(left, None, constant)?;
            let b = self.render(right, None, constant)?;
            return Ok(Code::concat([a, self.g(format!(" {op} "), id), b]));
        }
        if !typ.is_sequence() && !self.is_array(sequence) {
            return Err(self.unsupported(id, "generic/bit-set/set membership dispatch"));
        }
        let helper = format!("{}contains", self.source.prefix);
        let key = (NodeId(0), helper.clone());
        if !self.helpers.contains_key(&key) {
            let mut parts = String::new();
            let mut names = vec![];
            for (suffix, signature) in [
                ("array", "$C/[$N]$E"),
                ("slice", "$C/[]$E"),
                ("dynamic", "$C/[dynamic]$E"),
                ("fixed", "$C/[dynamic; $N]$E"),
            ] {
                let name = format!("{helper}_{suffix}");
                names.push(name.clone());
                parts.push_str(&format!("\n@(private=\"file\")\n{name} :: proc \"contextless\"(values: {signature}, value:E)->bool {{ for element in values {{if element==value {{return true}}}}; return false }}\n"));
            }
            parts.push_str(&format!(
                "\n@(private=\"file\")\n{helper} :: proc{{{}}}\n",
                names.join(", ")
            ));
            self.helpers.insert(key, self.g(parts, id));
        }
        let a = self.render(left, None, constant)?;
        let b = if self.is_array(sequence) {
            self.array(sequence, None, Some(left), constant)?
        } else {
            self.render(right, None, constant)?
        };
        Ok(Code::concat([
            self.g(
                format!(
                    "{}{}(value=(",
                    if op == "not_in" { "(!" } else { "" },
                    helper
                ),
                id,
            ),
            a,
            self.g("), values=(", id),
            b,
            self.g(if op == "not_in" { ")))" } else { "))" }, id),
        ])
        .expansion(&self.source.path, self.source.node(id).span))
    }
    fn concatenate(&mut self, id: NodeId, expected: Option<Hint>) -> Result<Code> {
        fn flatten(s: &Source, id: NodeId, out: &mut Vec<NodeId>) {
            let mut n = id;
            while s.node(n).kind == "Paren" {
                if let Some(inner) = s.role(n, "expr") {
                    n = inner;
                } else {
                    break;
                }
            }
            if s.node(n).kind == "Binary" && s.node(n).op == "++" {
                for role in ["left", "right"] {
                    if let Some(child) = s.role(n, role) {
                        flatten(s, child, out);
                    }
                }
            } else {
                out.push(id);
            }
        }
        let mut values = vec![];
        flatten(self.source, id, &mut values);
        let mut kind = None;
        for value in &values {
            if self.resolver.runtime_operand(self.reference(*value)) {
                return Ok(self.error_token(*value,"Array concatenation ('++') requires compile-time constant operands; runtime variables and immutable bindings do not qualify."));
            }
            let typ = self.typ(*value);
            let current=match typ{Type::Array{..}=>"array",Type::Slice(_)=>"slice",Type::Unknown|Type::Generic=>return Err(self.unsupported(*value,"generic concatenation types")),_=>return Ok(self.error_token(*value,"Array concatenation ('++') requires fixed arrays or constant slices; use + for constant strings."))};
            if kind.is_some_and(|kind| kind != current) {
                return Ok(self.error_token(*value,"Array concatenation ('++') cannot mix fixed arrays and slices; convert the operands to the same container kind explicitly."));
            }
            kind = Some(current);
        }
        self.meta = true;
        let mut parts = vec![self.g(format!("{}meta.concatenate(", self.source.prefix), id)];
        let mut hint = expected.map(|hint| Hint {
            code: Code::concat([
                self.g(format!("{}meta.type_elem_type(", self.source.prefix), id),
                hint.code,
                self.g(")", id),
            ]),
            element_only: true,
            plain_array: false,
        });
        if hint.is_none() {
            if let Some(anchor) = values
                .iter()
                .find(|id| !self.is_array(**id))
                .or(values.first())
            {
                let code = if self.is_array(*anchor) {
                    self.source
                        .group(*anchor, "elems")
                        .first()
                        .map(|id| self.render(*id, None, true))
                        .transpose()?
                } else {
                    Some(self.render(*anchor, None, true)?)
                };
                if let Some(code) = code {
                    self.default = true;
                    let typeof_ = self.native("type_of");
                    let code = if self.is_array(*anchor) {
                        Code::concat([
                            self.g(
                                format!("{typeof_}({}default_value(", self.source.prefix),
                                id,
                            ),
                            code,
                            self.g("))", id),
                        ])
                    } else {
                        Code::concat([
                            self.g(
                                format!("{}meta.type_elem_type({typeof_}(", self.source.prefix),
                                id,
                            ),
                            code,
                            self.g("))", id),
                        ])
                    };
                    hint = Some(Hint {
                        code,
                        element_only: true,
                        plain_array: false,
                    });
                }
            }
        }
        for (i, value) in values.iter().enumerate() {
            if i > 0 {
                parts.push(self.g(", ", id));
            }
            parts.push(self.render(
                *value,
                if self.is_array(*value) {
                    hint.clone()
                } else {
                    None
                },
                true,
            )?);
        }
        parts.push(self.g(")", id));
        Ok(Code::concat(parts).expansion(&self.source.path, self.source.node(id).span))
    }
    fn repeat(&mut self, id: NodeId, mut text: NodeId, mut count: NodeId) -> Result<Code> {
        if matches!(self.typ(count), Type::String { .. }) {
            std::mem::swap(&mut text, &mut count);
        }
        if self.resolver.runtime_operand(self.reference(text))
            || self.resolver.runtime_operand(self.reference(count))
        {
            return Ok(self.error_token(id,"String repetition requires compile-time constant operands, like string concatenation (+)."));
        }
        let Some(amount) = self.resolver.constant_int(self.reference(count)) else {
            return Err(
                self.unsupported(count, "native constant-expression string repetition counts")
            );
        };
        if amount < 0 {
            return Ok(self.error_token(count, "String repetition requires a nonnegative count."));
        }
        if amount > 16 * 1024 * 1024 {
            return Ok(self.error_token(
                count,
                "String repetition result exceeds the 16 MiB compile-time limit.",
            ));
        }
        let name = format!(
            "{}repeat_{}_{}",
            self.source.prefix,
            self.source.node(id).span.start,
            self.source.node(id).span.end
        );
        let scope = self.helper_scope(id);
        let key = (scope, name.clone());
        if !self.helpers.contains_key(&key) {
            let value = self.render(text, None, true)?;
            let mut parts = vec![
                self.g(format!("\n{name}_base :: ("), id),
                value,
                self.g(") + \"\"\n", id),
            ];
            let len = self.native("len");
            let max = self.native("max");
            parts.push(self.g(format!("#assert({len}({name}_base) == 0 || {amount} <= 16777216 / {max}({len}({name}_base), 1), \"String repetition result exceeds the 16 MiB compile-time limit.\")\n"),id));
            let mut remaining = amount as u128;
            let mut bit = 0;
            let mut terms = vec![];
            while remaining > 0 {
                if bit == 0 {
                    parts.push(self.g(format!("{name}_chunk_0 :: {name}_base\n"), id));
                } else {
                    parts.push(self.g(
                        format!(
                            "{name}_chunk_{bit} :: {name}_chunk_{} + {name}_chunk_{}\n",
                            bit - 1,
                            bit - 1
                        ),
                        id,
                    ));
                }
                if remaining & 1 != 0 {
                    terms.push(format!("{name}_chunk_{bit}"));
                }
                remaining >>= 1;
                bit += 1;
            }
            parts.push(self.g(
                format!(
                    "{name} :: {}\n",
                    if terms.is_empty() {
                        format!("{name}_base[:0]")
                    } else {
                        terms.join(" + ")
                    }
                ),
                id,
            ));
            self.helpers.insert(key, Code::concat(parts));
        }
        Ok(self
            .g(name, id)
            .expansion(&self.source.path, self.source.node(id).span))
    }
    fn power(&mut self, id: NodeId, constant: bool) -> Result<Code> {
        if constant {
            return Err(self.unsupported(id, "compile-time numeric power"));
        }
        let left = self.role(id, "left")?;
        let right = self.role(id, "right")?;
        let a = self.render(left, None, false)?;
        let b = self.render(right, None, false)?;
        let module = self.module("numeric");
        let typeof_ = self.native("type_of");
        self.default = true;
        let typ = Code::concat([
            self.g(
                format!("{typeof_}({}default_value(true ? (", self.source.prefix),
                id,
            ),
            a.clone(),
            self.g(") : (", id),
            b.clone(),
            self.g(")))", id),
        ]);
        Ok(Code::concat([
            self.g(format!("{module}.power(("), id),
            typ.clone(),
            self.g(")(", id),
            a,
            self.g("), (", id),
            typ,
            self.g(")(", id),
            b,
            self.g("))", id),
        ])
        .expansion(&self.source.path, self.source.node(id).span))
    }
    fn access(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        let base = self.role(id, "expr")?;
        let typ = self.typ(base);
        if matches!(typ, Type::Map { .. } | Type::Enum(_)) {
            return Ok(None);
        }
        let bounds = if self.source.node(id).kind == "Index" {
            vec![self.source.role(id, "index")]
        } else {
            vec![self.source.role(id, "low"), self.source.role(id, "high")]
        };
        let needs = bounds.iter().flatten().any(|bound| {
            self.resolver
                .constant_int(self.reference(*bound))
                .map(|n| n < 0)
                .unwrap_or(true)
        });
        if !needs {
            return Ok(None);
        }
        if !typ.is_sequence() {
            return Err(self.unsupported(id, "generic/indirect sequence access"));
        }
        if let Type::Array {
            length: Some(length),
            ..
        } = typ
        {
            if bounds.len() == 1 {
                if let Some(index) = self
                    .resolver
                    .constant_int(self.reference(bounds[0].unwrap()))
                {
                    let normalized = if index < 0 { length + index } else { index };
                    if normalized < 0 {
                        return Ok(Some(self.error_token(bounds[0].unwrap(),&format!("Negative sequence index {index} is out of bounds for length {length}."))));
                    }
                    let base = self.render(base, None, constant)?;
                    return Ok(Some(
                        Code::concat([
                            self.g("(", id),
                            base,
                            self.g(format!(")[{normalized}]"), id),
                        ])
                        .expansion(&self.source.path, self.source.node(id).span),
                    ));
                }
            }
        }
        // Full access helpers must preserve base/bound evaluation, addressability
        // and temporary lifetimes. Do not emit a deceptively simpler duplicate.
        Err(self.unsupported(id, "single-evaluation runtime sequence bounds"))
    }
    fn has_context(&self, id: NodeId) -> bool {
        let owner = self
            .source
            .ancestors(id)
            .find(|id| self.source.node(*id).kind == "Procedure");
        let Some(owner) = owner else {
            return true;
        };
        let convention = self
            .source
            .role(owner, "type")
            .and_then(|id| self.source.node(id).data["calling_convention"].as_str())
            .unwrap_or("");
        matches!(convention, "" | "odin")
    }
    fn or_panic(&mut self, id: NodeId) -> Result<Code> {
        let mut primary = self.role(id, "expr")?;
        while self.source.node(primary).kind == "Paren" {
            primary = self.role(primary, "expr")?;
        }
        let module = self.module("optional");
        let owner = self
            .source
            .ancestors(id)
            .find(|id| self.source.node(*id).kind == "Procedure");
        let procedure = owner
            .and_then(|id| self.source.node(id).parent)
            .and_then(|id| self.source.group(id, "names").first())
            .map(|id| self.source.text(*id))
            .unwrap_or(if owner.is_some() {
                "<anonymous>"
            } else {
                "<global>"
            });
        let offset = self.source.node(id).span.start;
        let (line, _) = self.source.position(offset);
        // Source_Code_Location follows the reference's rune column; LSP/source
        // map positions separately use UTF-16 columns.
        let column = self.source.raw[..offset]
            .rsplit('\n')
            .next()
            .unwrap_or("")
            .chars()
            .count()
            + 1;
        let location = format!(
            "{{{},{line},{column},{}}}",
            json!(self.source.path.to_string_lossy()),
            json!(procedure)
        );
        let summary = json!(format!(
            "unexpected failure\n  expression: {}\n  procedure: {procedure}",
            self.source.text(primary)
        ))
        .to_string();
        let context = self.has_context(id);
        let fail = if context {
            "failure"
        } else {
            "failure_contextless"
        };
        let extra = if context { ",false" } else { "" };
        let mut count = 1;
        if self.source.node(primary).kind == "Call" {
            let callee = self.role(primary, "callee")?;
            if self.source.text(callee) == "#load" {
                let query = self.render(primary, None, false)?;
                return Ok(Code::concat([
                    self.g("(", id),
                    query,
                    self.g(
                        format!(" or_else {module}.{fail}(false,{summary},{location}{extra}))"),
                        id,
                    ),
                ]));
            }
            let Some(signature) = self.resolver.signature(self.reference(callee)) else {
                return Err(self.unsupported(
                    id,
                    "optional-result signatures from imported/overloaded procedures",
                ));
            };
            count = self
                .resolver
                .fields(signature, "results")
                .len()
                .saturating_sub(1);
        }
        if count > 1 {
            return Err(self.unsupported(id, "multi-value or_panic results"));
        }
        let query = if count == 1 {
            self.querying += 1;
            let code = self.render(primary, None, false);
            self.querying -= 1;
            let code = code?;
            let typeof_ = self.native("type_of");
            Some(Code::concat([
                self.g(format!("{typeof_}("), id),
                code,
                self.g(
                    format!(" or_else {module}.failure_contextless(false,\"\",{{}}))"),
                    id,
                ),
            ]))
        } else {
            None
        };
        let prefix = format!(
            "{}optional_{}_",
            self.source.prefix,
            self.source.node(id).span.start
        );
        let (name,mut args)=self.closed(id,"or_panic",if count==0{None}else{Some("")},|this|{
            let expression=this.render(primary,None,false)?;
            let value=if count==0{String::new()}else{format!("{prefix}value, ")};
            let mut code=vec![this.g(format!("{value}{prefix}status := "),id),expression,this.g(format!("\nif !{module}.succeeded({prefix}status) {{ {module}.{fail}({prefix}status,{summary},{location}{extra}) }}\n"),id)];
            if count==1{code.push(this.g(format!("return {prefix}value"),id));}Ok(Code::concat(code))
        })?;
        if let Some(query) = query {
            args.push(query);
        }
        Ok(self.helper_call(id, &name, args))
    }
    fn closed<F>(
        &mut self,
        id: NodeId,
        flavor: &str,
        result: Option<&str>,
        build: F,
    ) -> Result<(String, Vec<Code>)>
    where
        F: FnOnce(&mut Self) -> Result<Code>,
    {
        let node = self.source.node(id);
        let span = node.span;
        let owner = self.helper_scope(id);
        let variant = if self.querying > 0 {
            "_query"
        } else if self.fstring_frames.is_empty() {
            ""
        } else {
            "_temporary"
        };
        let name = format!(
            "{}{flavor}_{}_{}{variant}",
            self.source.prefix, span.start, span.end
        );
        let key = (owner, name.clone());
        let mut variables = vec![];
        let mut seen = HashSet::new();
        for (index, node) in self.source.nodes.iter().enumerate() {
            let child = NodeId(index);
            if node.kind != "Ident" || !self.source.is_descendant(child, id) {
                continue;
            }
            if let Some(parent) = node.parent {
                let parent_node = self.source.node(parent);
                if matches!(parent_node.kind.as_str(), "Declaration" | "Field" | "Range")
                    && self.source.group(parent, "names").contains(&child)
                    || matches!(parent_node.kind.as_str(), "Selector" | "FieldValue")
                        && self.source.role(parent, "field") == Some(child)
                {
                    continue;
                }
            }
            let Some(symbol) = self.resolver.symbol(self.reference(child)) else {
                continue;
            };
            let sym = &self.resolver.symbols[symbol];
            let source = &self.resolver.sources[sym.reference.file];
            if sym.scope.0 == 0
                || sym.constant
                || source.node(sym.reference.node).kind == "PolyType"
                || span.contains(source.node(sym.reference.node).span)
            {
                continue;
            }
            if sym.parameter
                && source
                    .group(sym.reference.node, "names")
                    .iter()
                    .any(|name| source.node(*name).kind == "PolyType")
            {
                continue;
            }
            if seen.insert(symbol) {
                variables.push((symbol, child));
            }
        }
        let mut params = vec![];
        let mut args = vec![];
        let mut aliases = HashMap::new();
        for (i, (symbol, child)) in variables.iter().enumerate() {
            let sym = &self.resolver.symbols[*symbol];
            let declaration = self.source.node(sym.reference.node);
            let byref = if declaration.kind == "Field" {
                sym.result
            } else if matches!(declaration.kind.as_str(), "Range" | "TypeSwitch") {
                self.source.node(sym.name_node).kind == "Unary"
            } else {
                true
            };
            let parameter = format!("{}capture_{i}", self.source.prefix);
            params.push(format!(
                "{parameter}: {}${}Capture_{i}",
                if byref { "^" } else { "" },
                self.source.prefix
            ));
            let argument = self.render(*child, None, false)?;
            args.push(if byref {
                Code::concat([self.g("&(", id), argument, self.g(")", id)])
            } else {
                argument
            });
            aliases.insert(
                *symbol,
                self.g(
                    format!("({parameter}{})", if byref { "^" } else { "" }),
                    *child,
                ),
            );
        }
        if let Some((pointer, _)) = self.fstring_frames.last() {
            let pointer = pointer.clone();
            let module = self.module("temporaries");
            params.push(format!("{pointer}: ^{module}.Scope"));
            args.push(self.g(pointer, id));
        }
        let returns = if let Some(result) = result {
            if result.is_empty() {
                params.push(format!("${}Result: typeid", self.source.prefix));
                format!(" -> {}Result", self.source.prefix)
            } else {
                format!(" -> {result}")
            }
        } else {
            String::new()
        };
        if params.is_empty() {
            params.push(format!("${}Site: bool", self.source.prefix));
            args.push(self.g("true", id));
        }
        if !self.helpers.contains_key(&key) {
            self.helpers.insert(key.clone(), self.g("", id));
            self.captures.push(aliases);
            let mut cleanup = None;
            for index in 0..self.source.nodes.len() {
                let child = NodeId(index);
                if self.source.node(child).kind != "Call"
                    || child != id && !self.source.is_descendant(child, id)
                {
                    continue;
                }
                if child != id
                    && self
                        .source
                        .ancestors(child)
                        .take_while(|parent| *parent != id)
                        .any(|parent| self.source.node(parent).kind == "Procedure")
                {
                    continue;
                }
                if self
                    .source
                    .ancestors(child)
                    .take_while(|parent| *parent != id)
                    .any(|parent| {
                        self.source.node(parent).kind == "Call"
                            && self.source.role(parent, "callee").is_some_and(|callee| {
                                matches!(
                                    self.source.text(callee),
                                    "type_of"
                                        | "typeid_of"
                                        | "type_info_of"
                                        | "size_of"
                                        | "align_of"
                                        | "offset_of"
                                ) && self.resolver.symbol(self.reference(callee)).is_none()
                            })
                    })
                {
                    continue;
                }
                let callee = self.role(child, "callee")?;
                if let Some(signature) = self.resolver.signature(self.reference(callee)) {
                    let source = &self.resolver.sources[signature.file];
                    let declaration = source
                        .node(signature.node)
                        .parent
                        .filter(|parent| source.node(*parent).kind == "Procedure")
                        .and_then(|parent| source.node(parent).parent);
                    if let Some(declaration) = declaration {
                        let deferred = source
                            .group(declaration, "attributes")
                            .iter()
                            .flat_map(|attribute| source.group(*attribute, "elems"))
                            .any(|item| {
                                source.node(*item).kind == "FieldValue"
                                    && source.role(*item, "field").is_some_and(|field| {
                                        source.text(field).starts_with("deferred_")
                                    })
                            });
                        if deferred {
                            cleanup = Some(child);
                            break;
                        }
                    }
                }
            }
            let body = if let Some(cleanup) = cleanup {
                let token=self.error_token(cleanup,"A procedure with automatic scope cleanup must be called directly in its owning scope. Assign its result to a named value first, then use that value in this expression.").finish().text;
                Ok(self.g(format!("#panic({})", json!(token)), cleanup))
            } else {
                build(self)
            };
            self.captures.pop();
            let body = body?;
            let private = if owner.0 == 0 {
                "@(private=\"file\")\n"
            } else {
                ""
            };
            let convention = if self.has_context(id) {
                "odin"
            } else {
                "contextless"
            };
            let header = self.g(
                format!(
                    "\n{private}{name} :: proc \"{convention}\"({}){returns} {{\n",
                    params.join(", ")
                ),
                id,
            );
            self.helpers
                .insert(key, Code::concat([header, body, self.g("\n}\n", id)]));
        }
        Ok((name, args))
    }
    fn helper_call(&self, id: NodeId, name: &str, args: Vec<Code>) -> Code {
        let mut code = vec![self.g(format!("{name}("), id)];
        for (i, arg) in args.into_iter().enumerate() {
            if i > 0 {
                code.push(self.g(", ", id));
            }
            code.push(arg);
        }
        code.push(self.g(")", id));
        Code::concat(code).expansion(&self.source.path, self.source.node(id).span)
    }
    fn interpolation(&mut self, id: NodeId) -> Result<Code> {
        use crate::formatting::{parse, WIDTH_MARKER};
        let parts = self.source.node(id).data["parts"]
            .as_array()
            .cloned()
            .ok_or_else(|| Error::new("interpolation parts omitted"))?;
        let mut fields = self.source.group(id, "args").to_vec().into_iter();
        let mut parsed = vec![];
        let manual = self.source.node(id).data["manual_lifetime"] == true;
        let quote = self.source.raw.as_bytes()
            [self.source.node(id).span.start + if manual { 2 } else { 1 }]
            as char;
        for part in parts {
            if part["literal"] == true {
                let span = Span::new(
                    part["start"].as_u64().unwrap_or(0) as usize,
                    part["end"].as_u64().unwrap_or(0) as usize,
                );
                let literal = self
                    .source
                    .slice(span)
                    .replace("{{", "{")
                    .replace("}}", "}");
                parsed.push((None, Some(format!("{quote}{literal}{quote}")), None, vec![]));
                continue;
            }
            let field = fields
                .next()
                .ok_or_else(|| Error::new("interpolation field omitted"))?;
            let start = part["format_start"].as_i64().unwrap_or(-1);
            let end = part["format_end"].as_i64().unwrap_or(-1);
            let mut spec = if start >= 0 && end >= start {
                self.source
                    .slice(Span::new(start as usize, end as usize))
                    .to_string()
            } else {
                String::new()
            };
            let slots = part["format_exprs"].as_array().cloned().unwrap_or_default();
            let mut widths = vec![];
            for _ in &slots {
                widths.push(
                    fields
                        .next()
                        .ok_or_else(|| Error::new("interpolation width omitted"))?,
                );
            }
            for span in slots.iter().rev() {
                let span = Span::from_wire(span)
                    .ok_or_else(|| Error::new("invalid interpolation width span"))?;
                spec.replace_range(
                    span.start - 1 - start as usize..span.end + 1 - start as usize,
                    WIDTH_MARKER,
                );
            }
            let spec = parse(&spec).map_err(|error| {
                Error::at(
                    self.source,
                    if start < 0 {
                        self.source.node(field).span
                    } else {
                        Span::new(start as usize, end as usize)
                    },
                    error.message,
                )
            })?;
            parsed.push((Some(field), None, Some(spec), widths));
        }
        let formatting = self.module("format");
        let prefix = format!(
            "{}format_{}_",
            self.source.prefix,
            self.source.node(id).span.start
        );
        let (name,args)=self.closed(id,"format",Some("string"),|this|{
            let builder=format!("{prefix}builder");
            let mut code=vec![this.g(format!("{builder}: {formatting}.Buffer = ---\n{formatting}.buffer_init(&{builder})\ndefer {formatting}.buffer_destroy(&{builder})\n"),id)];
            for (index,(field,literal,spec,widths)) in parsed.iter().enumerate(){
                if let Some(literal)=literal{code.push(this.g(format!("{formatting}.buffer_write_string(&{builder}, {literal})\n"),id));continue;}
                let field=field.ok_or_else(||Error::new("missing interpolation field"))?;
                let value=format!("{prefix}value_{index}");let expression=this.render(field,None,false)?;
                code.extend([this.g(format!("{value} := "),field),expression,this.g("\n",field)]);
                let owned=this.anonymous_string(field);
                let captured=this.fstring_frames.iter().any(|(_,targets)|targets.contains(&field));
                let width_name=format!("{prefix}width_{index}");
                for width in widths{
                    let rendered=this.render(*width,None,false)?;
                    code.extend([this.g(format!("{width_name} := {formatting}.normalize_width("),*width),rendered,this.g(")\n",*width)]);
                }
                let options=spec.as_ref().ok_or_else(||Error::new("missing interpolation format"))?.options(&width_name);
                code.push(this.g(format!("{formatting}.write_spec(&{builder}.builder,{value},{options})\n"),field));
                if owned&&!captured{let delete=this.native("delete");code.push(this.g(format!("{delete}({value})\n"),field));}
            }
            code.push(this.g(format!("return {formatting}.buffer_owned_text(&{builder})"),id));Ok(Code::concat(code))
        })?;
        Ok(self.helper_call(id, &name, args))
    }
    fn anonymous_string(&self, mut id: NodeId) -> bool {
        while self.source.node(id).kind == "Paren" {
            if let Some(expr) = self.source.role(id, "expr") {
                id = expr;
            } else {
                break;
            }
        }
        self.source.node(id).kind == "Interpolation"
            && self.source.node(id).data["manual_lifetime"] != true
    }
    fn discover_fstrings(&mut self) {
        const EXPRESSIONS: &[&str] = &[
            "Call",
            "FieldValue",
            "Paren",
            "Interpolation",
            "Binary",
            "Unary",
            "Ternary",
            "WhenExpr",
            "Index",
            "Slice",
            "Selector",
            "Deref",
            "Compound",
            "SwitchExpr",
            "CompileSwitchExpr",
            "SwitchResults",
            "Cast",
            "AutoCast",
            "OrElse",
            "OrPanic",
        ];
        for (i, node) in self.source.nodes.iter().enumerate() {
            let id = NodeId(i);
            if node.kind != "Interpolation" || node.data["manual_lifetime"] == true {
                continue;
            }
            let mut argument = None;
            for parent in self.source.ancestors(id) {
                let p = self.source.node(parent);
                if p.kind == "Call" {
                    let callee = self.source.role(parent, "callee");
                    let query = callee.is_some_and(|callee| {
                        matches!(
                            self.source.text(callee),
                            "type_of"
                                | "typeid_of"
                                | "size_of"
                                | "align_of"
                                | "offset_of"
                                | "type_info_of"
                                | "#assert"
                                | "#config"
                                | "#defined"
                        ) && self.resolver.symbol(self.reference(callee)).is_none()
                    });
                    if query {
                        break;
                    }
                    if self
                        .source
                        .group(parent, "args")
                        .iter()
                        .any(|arg| self.source.node(*arg).span.contains(node.span))
                    {
                        argument = Some(parent);
                        break;
                    }
                }
                if matches!(
                    p.kind.as_str(),
                    "Interpolation"
                        | "Procedure"
                        | "Declaration"
                        | "Return"
                        | "Assignment"
                        | "Field"
                ) {
                    break;
                }
            }
            if let Some(call) = argument {
                let mut root = call;
                for parent in self.source.ancestors(call) {
                    if EXPRESSIONS.contains(&self.source.node(parent).kind.as_str()) {
                        root = parent;
                    } else {
                        break;
                    }
                }
                self.fstring_plans.entry(root).or_default().insert(id);
            }
        }
    }
    fn fstring_expression(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        targets: HashSet<NodeId>,
    ) -> Result<Code> {
        let statement = self
            .source
            .node(id)
            .parent
            .is_some_and(|p| self.source.node(p).kind == "ExpressionStatement");
        if !statement && self.source.node(id).kind == "Call" {
            let callee = self.role(id, "callee")?;
            if let Some(signature) = self.resolver.signature(self.reference(callee)) {
                if self.resolver.fields(signature, "results").len() != 1 {
                    return Err(self.unsupported(id, "multi-result f-string argument scopes"));
                }
            }
        }
        self.fstring_busy.insert(id);
        self.querying += 1;
        let query = if statement {
            Ok(None)
        } else if let Some(hint) = expected {
            Ok(Some(hint.code))
        } else {
            self.default = true;
            let typeof_ = self.native("type_of");
            self.render(id, None, false).map(|code| {
                Some(Code::concat([
                    self.g(
                        format!("{typeof_}({}default_value(", self.source.prefix),
                        id,
                    ),
                    code,
                    self.g("))", id),
                ]))
            })
        };
        self.querying -= 1;
        let query = match query {
            Ok(value) => value,
            Err(error) => {
                self.fstring_busy.remove(&id);
                return Err(error);
            }
        };
        let module = self.module("temporaries");
        let prefix = format!(
            "{}fstrings_{}_",
            self.source.prefix,
            self.source.node(id).span.start
        );
        let pointer = format!("{}fstring_scope", self.source.prefix);
        let (name,mut args)=self.closed(id,"fstring_expression",if statement{None}else{Some("")},|this|{
            let code=this.g(format!("{prefix}entries:[{}]{module}.Entry\n{prefix}scope: {module}.Scope = {{entries={prefix}entries[:]}}\n{pointer} := &{prefix}scope\ndefer {module}.destroy({pointer})\n",targets.len()),id);
            this.fstring_frames.push((pointer.clone(),targets));let expression=this.render(id,None,false);this.fstring_frames.pop();
            Ok(Code::concat([code,this.g(if statement{""}else{"return "},id),expression?]))
        })?;
        self.fstring_busy.remove(&id);
        if let Some(query) = query {
            args.push(query);
        }
        Ok(self.helper_call(id, &name, args))
    }
    fn helper_scope(&self, id: NodeId) -> NodeId {
        self.source
            .ancestors(id)
            .find(|id| {
                self.source.node(*id).kind == "Block"
                    && self.source.node(*id).parent.is_none_or(|p| {
                        !matches!(self.source.node(p).kind.as_str(), "Switch" | "TypeSwitch")
                    })
            })
            .unwrap_or(NodeId(0))
    }
}
pub fn unquote(text: &str) -> Result<String> {
    if text.starts_with('`') && text.ends_with('`') {
        return Ok(text[1..text.len() - 1].into());
    }
    serde_json::from_str(text).map_err(|_| Error::new(format!("unsupported path literal: {text}")))
}
