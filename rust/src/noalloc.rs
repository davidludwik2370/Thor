//! Opt-in, closed-world allocation verification. This pass never changes code
//! generation. Unknown effects are errors, rather than promises to trust.
use crate::{
    ast::{NodeId, Source, Span},
    diagnostic::{Error, Result},
    semantics::{Ref, Resolver, Substitutions, Type},
};
use std::collections::{HashSet, VecDeque};
#[path = "noallocflow.rs"]
mod flow;
use flow::{Fact, Flow};
#[path = "noallocfailure.rs"]
mod failure;
#[path = "noallocio.rs"]
mod io;
#[path = "noallocrandom.rs"]
mod random;
#[path = "noallocreturns.rs"]
mod returns;
#[path = "noalloccollections.rs"]
mod collections;
#[path = "noallocsystem.rs"]
mod system;
#[path = "noallocdeferred.rs"]
mod deferred;
fn base_type(mut typ: &Type) -> &Type {
    while let Type::Nominal(_, inner) = typ {
        typ = inner;
    }
    typ
}
fn indexed_type(mut typ: &Type) -> &Type {
    loop {
        match typ {
            Type::Nominal(_, inner) | Type::Pointer(inner) => typ = inner,
            _ => return typ,
        }
    }
}

pub(crate) fn present(sources: &[Source]) -> bool {
    sources.iter().any(|s| {
        s.thor
            && !s.excluded
            && s.raw.contains("no_alloc")
            && (file_enabled(s)
                || s.nodes
                    .iter()
                    .enumerate()
                    .any(|(i, n)| n.kind == "Attribute" && !elements(s, NodeId(i)).is_empty()))
    })
}
pub(crate) fn elements(s: &Source, attr: NodeId) -> Vec<NodeId> {
    s.group(attr, "elems")
        .iter()
        .copied()
        .filter(|id| s.text(s.role(*id, "field").unwrap_or(*id)) == "no_alloc")
        .collect()
}
pub(crate) fn file_enabled(s: &Source) -> bool {
    s.thor
        && s.info["file_tags"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str())
            .any(|tag| {
                let mut words = tag.trim_start_matches("#+").split_whitespace();
                words.next() == Some("vet")
                    && words.flat_map(|w| w.split(',')).any(|w| w == "no_alloc")
            })
}
/// File tags are outside the syntax arena. Preserve other Odin checks and the
/// original newline; source maps retain the original file and byte positions.
pub(crate) fn tag_edits(s: &Source) -> Vec<(Span, String)> {
    if !s.thor || !s.raw.contains("no_alloc") {
        return vec![];
    }
    let mut edits = vec![];
    let mut cursor = 0;
    for tag in s.info["file_tags"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|v| v.as_str())
    {
        let mut words = tag.trim_start_matches("#+").split_whitespace();
        if words.next() != Some("vet") {
            continue;
        }
        let names = words
            .flat_map(|w| w.split(','))
            .filter(|w| !w.is_empty())
            .collect::<Vec<_>>();
        if !names.contains(&"no_alloc") {
            continue;
        }
        let rest = names
            .into_iter()
            .filter(|w| *w != "no_alloc")
            .collect::<Vec<_>>();
        let text = if rest.is_empty() {
            String::new()
        } else {
            format!("#+vet {}", rest.join(" "))
        };
        if let Some((start, _)) = s.raw.match_indices(tag).find(|(start, _)| {
            *start >= cursor
                && !s
                    .comments
                    .iter()
                    .any(|c| c.start <= *start && *start < c.end)
        }) {
            edits.push((Span::new(start, start + tag.len()), text));
            cursor = start + tag.len();
        }
    }
    edits
}

#[derive(Clone)]
struct Work {
    at: Ref,
    bindings: Substitutions,
    root: Ref,
    path: Option<usize>,
    procedure: bool,
    arguments: Vec<Fact>,
}
pub(crate) struct Check<'a, 'r> {
    r: &'r mut Resolver<'a>,
    overlays: &'a std::collections::BTreeMap<std::path::PathBuf, String>,
    queue: VecDeque<Work>,
    visited: HashSet<(Ref, Substitutions, Vec<Fact>)>,
    budget: usize,
    traces: Vec<(Ref, Option<usize>)>,
    flow: Flow,
    io: io::State,
    failure: failure::State,
    random: random::State,
}
impl<'a, 'r> Check<'a, 'r> {
    pub(crate) fn analyze(
        r: &'r mut Resolver<'a>,
        inspected: &std::collections::BTreeSet<std::path::PathBuf>,
        sdk_verified: std::rc::Rc<std::cell::Cell<bool>>,
        overlays: &'a std::collections::BTreeMap<std::path::PathBuf, String>,
    ) -> Result<()> {
        let mut this = Self {
            budget: r
                .sources
                .iter()
                .filter(|s| !s.shared_native)
                .map(|s| s.nodes.len())
                .sum::<usize>()
                .max(1024),
            r,
            overlays,
            queue: VecDeque::new(),
            visited: HashSet::new(),
            traces: vec![],
            flow: Flow::default(),
            io: io::State::new(inspected, sdk_verified),
            failure: failure::State::default(),
            random: random::State::default(),
        };
        this.roots()?;
        while let Some(work) = this.queue.pop_front() {
            if !this.r.active_definition(work.at) {
                continue;
            }
            let key = (work.at, work.bindings.clone(), work.arguments.clone());
            // A successful universal proof covers every runtime argument fact
            // at the same type/constant specialization. File-policy roots must
            // not be rechecked separately for each later call context.
            if this.visited.contains(&key)
                || this
                    .visited
                    .contains(&(work.at, work.bindings.clone(), vec![]))
            {
                continue;
            }
            // Bound retained substitution storage as well as the number of
            // visits: recursive [1]T specializations grow their type keys.
            let mut pending = work.bindings.values().collect::<Vec<_>>();
            let mut cost = 1usize;
            while let Some(typ) = pending.pop() {
                cost += 1;
                if cost > this.budget {
                    break;
                }
                match typ {
                    Type::Array { elem, .. } | Type::Matrix{elem,..}
            | Type::Vector { elem, .. }
                    | Type::Fixed { elem, .. }
                    | Type::Slice(elem)
                    | Type::Dynamic(elem)
                    | Type::Set(elem)
                    | Type::Pointer(elem)
                    | Type::Nominal(_, elem)
                    | Type::Soa(elem) => pending.push(elem),
                    Type::Map { key, value } => pending.extend([key.as_ref(), value.as_ref()]),
                    Type::Record(_, fields) | Type::Union(_, fields) | Type::Anonymous(fields) => {
                        pending.extend(fields.iter().map(|(_, typ)| typ))
                    }
                    _ => {}
                }
            }
            if cost > this.budget {
                return Err(this.error(
                    &work,
                    work.at,
                    "cannot prove the allocation effects of expanding generic recursion",
                ));
            }
            this.visited.insert(key);
            this.budget -= cost;
            crate::profile::count("no_alloc.procedures", usize::from(work.procedure) as u64);
            if work.procedure {
                this.procedure(&work)?;
            } else {
                this.flow.reset();
                this.walk(&work, work.at)?;
            }
        }
        Ok(())
    }
    fn roots(&mut self) -> Result<()> {
        for file in 0..self.r.sources.len() {
            let s = &self.r.sources[file];
            if !s.thor || s.excluded || !s.raw.contains("no_alloc") {
                continue;
            }
            let whole = file_enabled(s);
            for i in 0..s.nodes.len() {
                let id = NodeId(i);
                let n = s.node(id);
                if n.kind == "Attribute" {
                    let attrs = elements(s, id);
                    if attrs.is_empty() {
                        continue;
                    }
                    if attrs.len() > 1 {
                        return Err(Error::at(
                            s,
                            s.node(attrs[1]).span,
                            "Duplicate @(no_alloc) attribute.",
                        ));
                    }
                    if s.node(attrs[0]).kind != "Ident" {
                        return Err(Error::at(
                            s,
                            s.node(attrs[0]).span,
                            "@(no_alloc) takes no value or arguments.",
                        ));
                    }
                    let owner = n.parent.filter(|p| s.node(*p).kind == "Declaration");
                    let values = owner.map(|d| s.group(d, "values")).unwrap_or(&[]);
                    if let Some(owner) = owner {
                        let count = s
                            .group(owner, "attributes")
                            .iter()
                            .map(|a| elements(s, *a).len())
                            .sum::<usize>();
                        if count > 1 {
                            return Err(Error::at(s, n.span, "Duplicate @(no_alloc) attribute."));
                        }
                    }
                    if values.len() != 1 || s.node(values[0]).kind != "Procedure" {
                        return Err(Error::at(s,n.span,"@(no_alloc) requires a procedure or fn definition with an inspectable body; it is not a trusted foreign or callback annotation."));
                    }
                    let at = Ref {
                        file,
                        node: values[0],
                    };
                    self.queue.push_back(Work {
                        at,
                        bindings: Substitutions::new(),
                        root: at,
                        path: None,
                        procedure: true,
                        arguments: vec![],
                    });
                }
                if whole && n.kind == "Procedure" {
                    let at = Ref { file, node: id };
                    self.queue.push_back(Work {
                        at,
                        bindings: Substitutions::new(),
                        root: at,
                        path: None,
                        procedure: true,
                        arguments: vec![],
                    });
                }
                // File policy covers runtime initializers as well as bodies.
                if whole
                    && n.kind == "Declaration"
                    && n.mutable
                    && !s.ancestors(id).any(|p| s.node(p).kind == "Procedure")
                {
                    if !s.group(id, "values").is_empty() {
                        let at = Ref { file, node: id };
                        self.queue.push_back(Work {
                            at,
                            bindings: Substitutions::new(),
                            root: at,
                            path: None,
                            procedure: false,
                            arguments: vec![],
                        });
                    }
                }
            }
        }
        Ok(())
    }
    fn error(&self, w: &Work, at: Ref, reason: &str) -> Error {
        let source=&self.r.sources[at.file];
        if source.shared_native && ["unknown","unresolved","generic"].iter().any(|word|reason.contains(word)) {
            for node in std::iter::once(at.node).chain(source.descendants(at.node)) {
                let site=Ref{node,..at};let name=source.text(node);
                if source.node(node).kind=="Ident" && self.r.expression_reference(site)
                    && !SAFE_BUILTINS.contains(&name)&&!UNEVALUATED.contains(&name)
                    && !["nil","true","false","context","make","new","delete","reserve","resize","append"].contains(&name)
                    && self.r.namespace_imports(at.file,name).is_empty() && self.r.symbol(site).is_none()
                    && !self.r.denotes_type(site) {
                    let _=self.r.package_symbol(site,".",name);
                }
            }
        }
        let mut path = vec![];
        let mut trace = w.path;
        while let Some(index) = trace {
            let (site, parent) = self.traces[index];
            path.push(site);
            trace = parent;
        }
        path.push(w.root);
        path.reverse();
        let implementation = |file: usize| {
            let s = &self.r.sources[file];
            s.shared_native
                || s.collection_roots
                    .get("thor")
                    .is_some_and(|root| s.path.starts_with(root))
        };
        let primary = if implementation(at.file) {
            path.iter()
                .rev()
                .find(|site| !implementation(site.file))
                .copied()
                .unwrap_or(at)
        } else {
            at
        };
        let root = if self.r.sources[w.root.file].node(w.root.node).kind == "Procedure" {
            crate::purediagnostics::procedure_name(self.r.sources, w.root)
        } else {
            "file runtime initializer"
        };
        let source = &self.r.sources[primary.file];
        let mut error = Error::at(
            source,
            source.node(primary.node).span,
            format!("no_alloc: {reason}. Required by '{root}' (allocation-free contract)."),
        );
        if primary != at {
            path.push(at);
        }
        for site in &path {
            let s = &self.r.sources[site.file];
            let span = s.node(site.node).span;
            let (line, column) = s.position(span.start);
            let (end_line, end_column) = s.position(span.end);
            let message = if *site == w.root {
                "Allocation-free contract is required here."
            } else if *site == at && primary != at {
                "Unverified or allocating operation in the library implementation."
            } else {
                "Called from this allocation-free path."
            };
            let note = serde_json::json!({"pos":{"file":s.path,"offset":span.start,"end_offset":span.end,"line":line,"column":column,"end_line":end_line,"end_column":end_column},
                "message":message,"msgs":[message]});
            if !error.diagnostics[0]["related"].is_array() {
                error.diagnostics[0]["related"] = serde_json::json!([]);
            }
            error.diagnostics[0]["related"]
                .as_array_mut()
                .unwrap()
                .push(note);
        }
        error
    }
    fn procedure(&mut self, w: &Work) -> Result<()> {
        self.flow.begin(self.r, w.at, &w.arguments,&w.bindings);
        let s = &self.r.sources[w.at.file];
        if !self.r.active_definition(w.at) {
            return Ok(());
        }
        let body=s.role(w.at.node,"body");
        if self.system_leaf(w)?{return Ok(());}
        let s=&self.r.sources[w.at.file];
        let Some(body) = body else {
            if crate::purity::llvm_primitive(self.r, w.at) {
                return Ok(());
            }
            return Err(self.error(
                w,
                w.at,
                "foreign or bodyless calls have unknown allocation effects",
            ));
        };
        // Deferred attributes create real calls at the enclosing scope exit.
        let signature=s.role(w.at.node,"type");
        let declaration=s.node(w.at.node).parent;
        if let (Some(signature),Some(declaration))=(signature,declaration){
            self.deferred_calls(w,Ref{node:declaration,..w.at},Ref{node:signature,..w.at})?;
        }
        let s=&self.r.sources[w.at.file];
        if let Some(signature) = s.role(w.at.node, "type") {
            for role in ["params", "results"] {
                for (name, annotation) in self.r.fields(
                    Ref {
                        node: signature,
                        ..w.at
                    },
                    role,
                ) {
                    if let Some(annotation) = annotation {
                        let source = &self.r.sources[annotation.file];
                        if source.node(annotation.node).kind == "TypeidType"
                            || role == "params"
                                && source.node(annotation.node).parent.is_some_and(|field| {
                                    source.group(field, "names").iter().any(|node| {
                                        source.node(*node).kind == "PolyType"
                                            && source.text(*node).trim_start_matches('$') == name
                                    })
                                })
                        {
                            // Compile-time arguments and native type IDs do not
                            // create runtime value storage at the boundary.
                            continue;
                        }
                        let typ = self.r.specialized_type(annotation, &w.bindings);
                        if self.big(&typ, &mut HashSet::new()) {
                            return Err(self.error(
                                w,
                                w.at,
                                "Integer/Rational parameter or result storage may allocate",
                            ));
                        }
                        if self.value_storage(&typ, &mut HashSet::new(), true) {
                            return Err(self.error(w,w.at,"generic value copies or parameter/result conversions do not yet have provably allocation-free types"));
                        }
                    }
                }
            }
        }
        if s.node(w.at.node).data["pure"] == true {
            let signature = s.role(w.at.node, "type");
            if let Some(signature) = signature {
                if let Some(results) = s.role(signature, "results") {
                    for field in s.node(results).children.iter().copied() {
                        if let Some(typ) = s.role(field, "type") {
                            let typ = self
                                .r
                                .specialized_type(Ref { node: typ, ..w.at }, &w.bindings);
                            if self.owned(&typ, &mut HashSet::new()) {
                                return Err(self.error(
                                    w,
                                    w.at,
                                    "fn result ownership promotion may allocate",
                                ));
                            }
                        }
                    }
                }
            }
        }
        self.walk(w, Ref { node: body, ..w.at })
    }
    fn owned(&mut self, t: &Type, seen: &mut HashSet<Type>) -> bool {
        match t {
            Type::String { .. }
            | Type::Slice(_)
            | Type::Dynamic(_)
            | Type::Soa(_)
            | Type::Map { .. }
            | Type::Set(_)
            | Type::BigInteger
            | Type::BigRational
            | Type::Unknown
            | Type::Generic => return true,
            Type::Scalar { .. }
            | Type::Constant(_)
            | Type::Enum(_)
            | Type::Procedure(_)
            | Type::Pointer(_) => return false,
            _ => {}
        }
        if !seen.insert(t.clone()) {
            return false;
        }
        match t {
            Type::String { .. }
            | Type::Slice(_)
            | Type::Dynamic(_)
            | Type::Map { .. }
            | Type::Set(_)
            | Type::BigInteger
            | Type::BigRational
            | Type::Unknown
            | Type::Generic => true,
            Type::Array { elem, .. } | Type::Matrix{elem,..}
            | Type::Vector { elem, .. }
            | Type::Fixed { elem, .. }
            | Type::Nominal(_, elem)
            | Type::Soa(elem) => self.owned(elem, seen),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(t)
                .iter()
                .any(|(_, t)| self.owned(t, seen)),
            Type::Union(..) => self.r.union_variants(t).iter().any(|t| self.owned(t, seen)),
            _ => false,
        }
    }
    fn big(&mut self, t: &Type, seen: &mut HashSet<Type>) -> bool {
        self.value_storage(t, seen, false)
    }
    fn value_storage(&mut self, t: &Type, seen: &mut HashSet<Type>, unknown: bool) -> bool {
        // Most values are primitives/borrowed descriptors. Avoid allocating a
        // visited-type set (or cloning their names) for these common cases.
        match t {
            Type::BigInteger | Type::BigRational => return true,
            Type::Unknown | Type::Generic => return unknown,
            Type::Scalar { .. }
            | Type::Constant(_)
            | Type::Enum(_)
            | Type::String { .. }
            | Type::Procedure(_)
            | Type::Pointer(_)
            | Type::Slice(_)
            | Type::Dynamic(_)
            | Type::Map { .. }
            | Type::Set(_)
            | Type::Soa(_) => return false,
            _ => {}
        }
        if !seen.insert(t.clone()) {
            return false;
        }
        match t {
            Type::BigInteger | Type::BigRational => true,
            Type::Unknown | Type::Generic => unknown,
            Type::Array { elem, .. } | Type::Vector{elem,..} | Type::Matrix{elem,..} | Type::Fixed { elem, .. } | Type::Nominal(_, elem) => {
                self.value_storage(elem, seen, unknown)
            }
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(t)
                .iter()
                .any(|(_, t)| self.value_storage(t, seen, unknown)),
            Type::Union(..) => self
                .r
                .union_variants(t)
                .iter()
                .any(|t| self.value_storage(t, seen, unknown)),
            _ => false,
        }
    }
    /// Elided native literals inherit their storage from the enclosing value.
    /// Never treat an unresolved literal as a stack value by default.
    fn storage_type(&mut self, at: Ref, bindings: &Substitutions, depth: usize) -> Type {
        let typ = self.r.specialized_type(at, bindings);
        // These compiler-provided values have fixed scalar/borrowed-string
        // storage even when their anonymous native enum type is unavailable.
        // Do not invent an enum identity or use this to select native branches.
        if typ.is_unknown() && ["ODIN_OS","ODIN_ARCH","ODIN_ENDIAN","ODIN_BUILD_MODE","ODIN_SUBTARGET",
            "ODIN_OPTIMIZATION_MODE","ODIN_DEBUG","ODIN_TEST","ODIN_OS_STRING","ODIN_ARCH_STRING",
            "ODIN_ROOT","ODIN_VENDOR","ODIN_VERSION","ODIN_BUILD_PROJECT_NAME"].iter().any(|name|self.r.native_builtin_alias(at,name)){
            return Type::scalar("__native_compiler_constant");
        }
        if matches!(typ, Type::Unknown | Type::Generic)
            && self.r.sources[at.file].node(at.node).kind == "Binary"
        {
            let s = &self.r.sources[at.file];
            if let (Some(left), Some(right)) = (s.role(at.node, "left"), s.role(at.node, "right")) {
                let a = self.r.specialized_type(Ref { node: left, ..at }, bindings);
                let b = self.r.specialized_type(Ref { node: right, ..at }, bindings);
                if matches!((&a, &b), (Type::Scalar { .. }, Type::Scalar { .. })) {
                    return a;
                }
            }
        }
        if !matches!(typ, Type::Unknown) || depth > 32 {
            return typ;
        }
        let s = &self.r.sources[at.file];
        if !matches!(
            s.node(at.node).kind.as_str(),
            "Compound" | "Paren" | "Ternary" | "AutoCast"
        ) {
            return typ;
        }
        let Some(mut parent) = s.node(at.node).parent else {
            return typ;
        };
        let mut item = at.node;
        if s.node(parent).kind == "FieldValue" {
            item = parent;
            let Some(p) = s.node(parent).parent else {
                return typ;
            };
            parent = p;
        }
        let p = Ref { node: parent, ..at };
        match s.node(parent).kind.as_str() {
            "Paren" | "AutoCast" => self.storage_type(p, bindings, depth + 1),
            "Ternary" if s.role(parent, "cond") != Some(item) => {
                self.storage_type(p, bindings, depth + 1)
            }
            "Binary" => {
                let side = if s.role(parent, "left") == Some(item) {
                    "right"
                } else {
                    "left"
                };
                s.role(parent, side)
                    .map(|node| self.r.specialized_type(Ref { node, ..at }, bindings))
                    .unwrap_or(typ)
            }
            "Assignment" => s
                .group(parent, "rhs")
                .iter()
                .position(|id| *id == item)
                .and_then(|i| s.group(parent, "lhs").get(i))
                .copied()
                .map(|node| self.r.specialized_type(Ref { node, ..at }, bindings))
                .unwrap_or(typ),
            "Declaration" | "Field" => s
                .role(parent, "type")
                .map(|node| self.r.specialized_type(Ref { node, ..at }, bindings))
                .unwrap_or(typ),
            "Return" => {
                let index = s
                    .group(parent, "values")
                    .iter()
                    .position(|id| *id == item)
                    .unwrap_or(0);
                let signature = s
                    .ancestors(parent)
                    .find(|id| s.node(*id).kind == "Procedure")
                    .and_then(|id| s.role(id, "type"));
                signature
                    .and_then(|signature| {
                        self.r
                            .fields(
                                Ref {
                                    node: signature,
                                    ..at
                                },
                                "results",
                            )
                            .get(index)
                            .cloned()
                    })
                    .and_then(|(_, annotation)| annotation)
                    .map(|annotation| self.r.specialized_type(annotation, bindings))
                    .unwrap_or(typ)
            }
            "Compound" => {
                let label = s.role(item, "field").map(|id| s.text(id).to_string());
                let index = s
                    .group(parent, "elems")
                    .iter()
                    .position(|id| *id == item)
                    .unwrap_or(0);
                let container = self.storage_type(p, bindings, depth + 1);
                if let Some(elem) = container.element() {
                    return elem.clone();
                }
                self.r
                    .record_fields(&container)
                    .into_iter()
                    .enumerate()
                    .find(|(i, (name, _))| {
                        label.as_ref().map_or(*i == index, |label| label == name)
                    })
                    .map(|(_, (_, typ))| typ)
                    .unwrap_or(typ)
            }
            "Call" => {
                for plan in self.r.call_plans(p, bindings) {
                    let fields = crate::caller_metadata::params(
                        &self.r.sources[plan.signature.file],
                        plan.signature.node,
                    );
                    for (i, args) in plan.arguments.iter().enumerate() {
                        if !args.iter().any(|a| a.node == at.node || a.node == item) {
                            continue;
                        }
                        if let Some((_, field)) = fields.get(i) {
                            if let Some(node) =
                                self.r.sources[plan.signature.file].role(*field, "type")
                            {
                                return self.r.specialized_type(
                                    Ref {
                                        node,
                                        file: plan.signature.file,
                                    },
                                    &plan.bindings,
                                );
                            }
                        }
                    }
                }
                typ
            }
            _ => typ,
        }
    }
    fn walk(&mut self, w: &Work, at: Ref) -> Result<()> {
        if self.flow.terminated {
            return Ok(());
        }
        let s = &self.r.sources[at.file];
        let n = s.node(at.node).clone();
        let kind = n.kind.as_str();
        if matches!(
            kind,
            "Procedure" | "ProcedureType" | "Attribute" | "Import" | "Directive" | "PolyType"
        ) {
            return Ok(());
        }
        if kind == "Ident"
            && (matches!(s.text(at.node), "_" | "nil")
                || s.text(at.node).starts_with('#')
                || !self.r.expression_reference(at)
                || self.r.denotes_type(at))
        {
            return Ok(());
        }
        if kind == "Ident"
            && self.r.symbol(at).is_none()
            && !self
                .r
                .namespace_imports(at.file, s.text(at.node))
                .is_empty()
        {
            return Ok(());
        }
        if kind == "Selector" && self.r.denotes_type(at) {
            return Ok(());
        }
        if kind.ends_with("Type") {
            return Ok(());
        }
        if kind == "FieldValue" {
            if let Some(node) = s.role(at.node, "value") {
                self.walk(w, Ref { node, ..at })?;
            }
            return Ok(());
        }
        if kind == "Runtime" {
            if let Some(node) = s.role(at.node, "expr") {
                self.walk(w, Ref { node, ..at })?;
            }
            return Ok(());
        }
        if matches!(kind, "FallbackList" | "SwitchResults") {
            for node in s.group(at.node, "args").to_vec() {
                self.walk(w, Ref { node, ..at })?;
            }
            return Ok(());
        }
        if kind == "Run" || self.r.evaluated.contains_key(&at) {
            let typ = self.storage_type(at, &w.bindings, 0);
            let materialized = self
                .r
                .evaluated
                .get(&at)
                .map(|value| crate::values::has_owned(value, false))
                .unwrap_or_else(|| self.owned(&typ, &mut HashSet::new()));
            if materialized {
                return Err(self.error(
                    w,
                    at,
                    "materializing this compile-time result may allocate",
                ));
            }
            return Ok(());
        }
        if matches!(kind, "When" | "WhenExpr") {
            if let Some(cond) = s.role(at.node, "cond") {
                let selected = self
                    .r
                    .context_condition(Ref { node: cond, ..at }, &w.bindings);
                if let Some(selected) = selected {
                    if let Some(branch) = s
                        .role(at.node, if selected { "body" } else { "else" })
                        .or_else(|| s.role(at.node, if selected { "x" } else { "y" }))
                    {
                        self.walk(w, Ref { node: branch, ..at })?;
                    }
                    return Ok(());
                }
            }
            let branches = [
                s.role(at.node, "body").or_else(|| s.role(at.node, "x")),
                s.role(at.node, "else").or_else(|| s.role(at.node, "y")),
            ];
            let before = self.flow.clone();
            let mut joined = before.clone();
            for node in branches.into_iter().flatten() {
                self.flow = before.clone();
                self.walk(w, Ref { node, ..at })?;
                joined.join(&self.flow);
            }
            self.flow = joined;
            return Ok(());
        }
        if kind == "Defer" {
            let before = self.flow.clone();
            for node in n.children.clone() {
                self.walk(w, Ref { node, ..at })?;
            }
            self.flow = before;
            return Ok(());
        }
        if kind == "OrElse" {
            let value = s.role(at.node, "x");
            let fallback = s.role(at.node, "y");
            if let Some(node) = value {
                self.walk(w, Ref { node, ..at })?;
            }
            let before = self.flow.clone();
            if let Some(node) = fallback {
                self.walk(w, Ref { node, ..at })?;
            }
            self.flow.join(&before);
            return Ok(());
        }
        if kind == "If" {
            if let Some(node) = s.role(at.node, "init") {
                self.walk(w, Ref { node, ..at })?;
            }
            let s = &self.r.sources[at.file];
            let cond = s.role(at.node, "cond").map(|node| Ref { node, ..at });
            let branches = [s.role(at.node, "body"), s.role(at.node, "else")];
            if let Some(cond) = cond {
                self.walk(w, cond)?;
                if let Some(value) = self.flow.condition(self.r, cond, &w.bindings) {
                    if let Some(node) = branches[usize::from(!value)] {
                        self.walk(w, Ref { node, ..at })?;
                    }
                    return Ok(());
                }
            }
            let before = self.flow.clone();
            if let Some(node) = branches[0] {
                self.walk(w, Ref { node, ..at })?;
            }
            let left = self.flow.clone();
            self.flow = before;
            if let Some(node) = branches[1] {
                self.walk(w, Ref { node, ..at })?;
            }
            self.flow.join(&left);
            return Ok(());
        }
        if kind == "Ternary" {
            if let Some(node) = s.role(at.node, "cond") {
                self.walk(w, Ref { node, ..at })?;
            }
            let s = &self.r.sources[at.file];
            let selected = s
                .role(at.node, "cond")
                .and_then(|node| self.flow.condition(self.r, Ref { node, ..at }, &w.bindings));
            let branches = [s.role(at.node, "x"), s.role(at.node, "y")];
            if let Some(selected) = selected {
                if let Some(node) = branches[usize::from(!selected)] {
                    self.walk(w, Ref { node, ..at })?;
                }
            } else {
                let before = self.flow.clone();
                if let Some(node) = branches[0] {
                    self.walk(w, Ref { node, ..at })?;
                }
                let left = self.flow.clone();
                self.flow = before;
                if let Some(node) = branches[1] {
                    self.walk(w, Ref { node, ..at })?;
                }
                self.flow.join(&left);
            }
            return Ok(());
        }
        if kind == "Binary" && matches!(n.op.as_str(), "&&" | "||") {
            if let Some(node) = s.role(at.node, "left") {
                self.walk(w, Ref { node, ..at })?;
            }
            let s = &self.r.sources[at.file];
            let selected = s
                .role(at.node, "left")
                .and_then(|node| self.flow.condition(self.r, Ref { node, ..at }, &w.bindings));
            let right = s.role(at.node, "right");
            if selected == Some(n.op == "||") {
                return Ok(());
            }
            let before = self.flow.clone();
            if let Some(node) = right {
                self.walk(w, Ref { node, ..at })?;
            }
            if selected.is_none() {
                self.flow.join(&before);
            }
            return Ok(());
        }
        if matches!(
            kind,
            "Switch" | "TypeSwitch" | "SwitchExpr" | "CompileSwitchExpr"
        ) {
            let mut conditions = vec![];
            if matches!(kind, "Switch" | "TypeSwitch") {
                for child in s
                    .descendants(at.node)
                    .filter(|node| s.node(*node).kind == "Case")
                {
                    conditions.extend(s.group(child, "types").iter().copied());
                }
            } else {
                for case in n.data["cases"].as_array().into_iter().flatten() {
                    for value in case["matches"].as_array().into_iter().flatten() {
                        if let Some(node) =
                            Span::from_wire(value).and_then(|span| s.at(span, at.node))
                        {
                            conditions.push(node);
                        }
                    }
                }
            }
            let arguments = conditions
                .into_iter()
                .flat_map(|condition| std::iter::once(condition).chain(s.descendants(condition)))
                .filter(|node| s.node(*node).kind == "Call")
                .flat_map(|call| s.group(call, "args").iter().copied())
                .collect::<Vec<_>>();
            for node in arguments {
                let fact = self.flow.fact(self.r, Ref { node, ..at }, &w.bindings);
                self.flow.forget_addresses(&fact);
            }
        }
        if matches!(kind, "Switch" | "TypeSwitch") {
            let s = &self.r.sources[at.file];
            // A fallthrough destination inherits stores and address escapes
            // from preceding cases. Do not analyze it with a stale entry target.
            let falls_through = s.descendants(at.node).any(|node| {
                s.node(node).kind == "Other"
                    && s.text(node).trim() == "fallthrough"
                    && s.ancestors(node).find(|owner| {
                        matches!(s.node(*owner).kind.as_str(), "Switch" | "TypeSwitch")
                    }) == Some(at.node)
            });
            if falls_through {
                let effects = s
                    .descendants(at.node)
                    .filter_map(|node| match s.node(node).kind.as_str() {
                        "Call" => Some((false, s.group(node, "args").to_vec())),
                        "Assignment" => Some((true, s.group(node, "lhs").to_vec())),
                        _ => None,
                    })
                    .collect::<Vec<_>>();
                for (store, nodes) in effects {
                    for node in nodes {
                        if store {
                            self.flow.assign(self.r, Ref { node, ..at }, Fact::Unknown);
                        } else {
                            let value = self.flow.fact(self.r, Ref { node, ..at }, &w.bindings);
                            self.flow.forget_addresses(&value);
                        }
                    }
                }
            }
            let s = &self.r.sources[at.file];
            let selector = s.role(at.node, "selector").or_else(|| {
                n.children
                    .iter()
                    .copied()
                    .rev()
                    .find(|id| !matches!(s.node(*id).kind.as_str(), "Block" | "Declaration"))
            });
            let blocks = n
                .children
                .iter()
                .copied()
                .filter(|id| s.node(*id).kind == "Block")
                .collect::<Vec<_>>();
            let initializers = n
                .children
                .iter()
                .copied()
                .filter(|id| {
                    Some(*id) != selector
                        && !blocks.contains(id)
                        && !(kind == "TypeSwitch" && s.group(at.node, "names").contains(id))
                })
                .collect::<Vec<_>>();
            for node in initializers {
                self.walk(w, Ref { node, ..at })?;
            }
            if let Some(selector) = selector.filter(|_| kind == "Switch") {
                let typ = self.r.specialized_type(
                    Ref {
                        node: selector,
                        ..at
                    },
                    &w.bindings,
                );
                let fact = self.flow.fact(
                    self.r,
                    Ref {
                        node: selector,
                        ..at
                    },
                    &w.bindings,
                );
                let value = Flow::normalize(self.r, fact, &typ, &w.bindings);
                if value != Fact::Unknown {
                    let s = &self.r.sources[at.file];
                    let cases = n
                        .children
                        .iter()
                        .filter(|id| s.node(**id).kind == "Block")
                        .flat_map(|id| s.node(*id).children.iter())
                        .copied()
                        .filter(|id| s.node(*id).kind == "Case")
                        .collect::<Vec<_>>();
                    let mut selected = None;
                    let mut fallback = None;
                    let mut proven = true;
                    for case in cases {
                        let matches = self.r.sources[at.file].group(case, "types").to_vec();
                        if matches.is_empty() {
                            fallback = Some(case);
                            continue;
                        }
                        for node in matches {
                            let fact = self.flow.fact(self.r, Ref { node, ..at }, &w.bindings);
                            let candidate = Flow::normalize(self.r, fact, &typ, &w.bindings);
                            if candidate == Fact::Unknown {
                                proven = false;
                            } else if candidate == value {
                                selected = Some(case);
                            }
                        }
                    }
                    if proven {
                        if selected.or(fallback).is_some_and(|case| {
                            self.r.sources[at.file].descendants(case).any(|node| {
                                let source = &self.r.sources[at.file];
                                source.node(node).kind == "Other"
                                    && source.text(node).trim() == "fallthrough"
                                    && source
                                        .ancestors(node)
                                        .find(|owner| source.node(*owner).kind == "Switch")
                                        == Some(at.node)
                            })
                        }) {
                            proven = false;
                        }
                    }
                    if proven {
                        self.walk(
                            w,
                            Ref {
                                node: selector,
                                ..at
                            },
                        )?;
                        if let Some(node) = selected.or(fallback) {
                            self.walk(w, Ref { node, ..at })?;
                        }
                        return Ok(());
                    }
                }
            }
            if let Some(node) = selector {
                self.walk(w, Ref { node, ..at })?;
            }
            let before = self.flow.clone();
            let mut joined = before.clone();
            for block in blocks {
                for node in self.r.sources[at.file].node(block).children.clone() {
                    self.flow = before.clone();
                    self.walk(w, Ref { node, ..at })?;
                    joined.join(&self.flow);
                }
            }
            self.flow = joined;
            return Ok(());
        }
        let s = &self.r.sources[at.file];
        if kind == "For"
            && s.role(at.node, "cond").is_some_and(|node| {
                self.r.context_condition(Ref { node, ..at }, &w.bindings) == Some(false)
            })
        {
            let init = s.role(at.node, "init");
            let cond = s.role(at.node, "cond");
            if let Some(node) = init {
                self.walk(w, Ref { node, ..at })?;
            }
            if let Some(node) = cond {
                self.walk(w, Ref { node, ..at })?;
            }
            return Ok(());
        }
        let mut loop_entry = None;
        if matches!(kind, "For" | "Range") {
            // A back edge can change targets before the next iteration. Kill
            // assigned facts before analyzing the loop, rather than assuming
            // its first iteration describes all later iterations.
            let calls = s
                .descendants(at.node)
                .filter(|node| {
                    s.node(*node).kind == "Call"
                        && !s.role(*node, "callee").is_some_and(|callee| {
                            io::descriptor_view(self.r, Ref { node: callee, ..at })
                        })
                })
                .flat_map(|node| s.group(node, "args").iter().copied())
                .collect::<Vec<_>>();
            for node in calls {
                let fact = self.flow.fact(self.r, Ref { node, ..at }, &w.bindings);
                self.flow.forget_addresses(&fact);
            }
            let mut stores = vec![];
            for node in s.descendants(at.node) {
                if s.node(node).kind == "Assignment" {
                    stores.extend(s.group(node, "lhs").iter().copied());
                }
            }
            for node in stores {
                self.flow.assign(self.r, Ref { node, ..at }, Fact::Unknown);
            }
            loop_entry = Some(self.flow.clone());
        }
        if kind == "OrPanic" {
            let expr = s
                .role(at.node, "expr")
                .or_else(|| s.role(at.node, "x"))
                .or_else(|| s.node(at.node).children.first().copied());
            if let Some(node) = expr {
                self.walk(w, Ref { node, ..at })?;
            }
            return Ok(());
        }
        if matches!(kind, "Interpolation" | "Comprehension") {
            return Err(self.error(
                w,
                at,
                "this Thor operation may allocate generated runtime storage",
            ));
        }
        if kind == "Other" && native_branch(s.text(at.node)) {
            return Ok(());
        }
        if kind == "Other" || kind == "Unsafe" && s.text(at.node).trim() != "---" {
            return Err(self.error(
                w,
                at,
                "unverified native operation has unknown allocation effects",
            ));
        }
        if kind == "Call" {
            return self.call(w, at);
        }
        if matches!(kind, "SwitchExpr" | "CompileSwitchExpr") {
            let cases = n.data["cases"].as_array().cloned().unwrap_or_default();
            if let Some(selector) = s.role(at.node, "selector").or_else(|| {
                Span::from_wire(&n.data["selector"]).and_then(|span| s.at(span, at.node))
            }) {
                self.walk(
                    w,
                    Ref {
                        node: selector,
                        ..at
                    },
                )?;
            }
            let before = self.flow.clone();
            let mut joined = before.clone();
            for case in cases {
                self.flow = before.clone();
                for value in case["matches"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .chain(std::iter::once(&case["result"]))
                {
                    if let Some(node) = Span::from_wire(value)
                        .and_then(|span| self.r.sources[at.file].at(span, at.node))
                    {
                        self.walk(w, Ref { node, ..at })?;
                    }
                }
                joined.join(&self.flow);
            }
            self.flow = joined;
            return Ok(());
        }
        // Big-number value operations and copies are lowered to owned storage.
        if matches!(
            kind,
            "Ident"
                | "Literal"
                | "Selector"
                | "Binary"
                | "Unary"
                | "Compound"
                | "Cast"
                | "AutoCast"
                | "Index" | "MatrixIndex"
                | "Deref"
                | "TypeAssertion"
        ) {
            let typ = self.storage_type(at, &w.bindings, 0);
            if matches!(
                kind,
                "Ident" | "Selector" | "Index" | "MatrixIndex" | "Deref" | "Compound" | "TypeAssertion"
            ) && matches!(typ, Type::Unknown)
            {
                return Err(self.error(
                    w,
                    at,
                    "cannot prove allocation freedom: value storage has an unresolved type",
                ));
            }
            if self.big(&typ, &mut HashSet::new()) {
                return Err(self.error(
                    w,
                    at,
                    "Integer/Rational value storage or operations may allocate",
                ));
            }
            if kind == "Compound"
                && typ
                    .element()
                    .is_some_and(|elem| self.value_storage(elem, &mut HashSet::new(), true))
            {
                return Err(self.error(w, at, "Integer/Rational literal elements may allocate"));
            }
            if kind == "Compound"
                && matches!(
                    base_type(&typ),
                    Type::Map { .. } | Type::Set(_) | Type::Dynamic(_)
                )
            {
                return Err(self.error(w, at, "collection literals may allocate"));
            }
            if kind == "Binary" && matches!(base_type(&typ), Type::Set(_)) {
                return Err(self.error(w, at, "set operations may allocate"));
            }
            if matches!(
                kind,
                "Ident"
                    | "Selector"
                    | "Index" | "MatrixIndex"
                    | "Deref"
                    | "TypeAssertion"
                    | "Unary"
                    | "Cast"
                    | "AutoCast"
                    | "Compound"
            ) && matches!(typ, Type::Generic)
                && (kind != "Ident" || self.r.expression_reference(at))
            {
                return Err(self.error(
                    w,
                    at,
                    "generic value copies do not yet have provably allocation-free types",
                ));
            }
            if kind == "Binary"
                && matches!(typ, Type::Unknown | Type::Generic)
                && !matches!(
                    n.op.as_str(),
                    "==" | "!=" | "<" | ">" | "<=" | ">=" | "&&" | "||" | ".." | "..<" | "..="
                )
            {
                return Err(self.error(
                    w,
                    at,
                    "generic value operations do not yet have provably allocation-free types",
                ));
            }
        }
        if kind == "Assignment" {
            for node in self.r.sources[at.file].group(at.node, "lhs").to_vec() {
                let mut lhs = Ref { node, ..at };
                while self.r.sources[lhs.file].node(lhs.node).kind == "Paren" {
                    let Some(node) = self.r.sources[lhs.file].role(lhs.node, "expr") else {
                        break;
                    };
                    lhs.node = node;
                }
                let s = &self.r.sources[lhs.file];
                if s.node(lhs.node).kind == "Index" {
                    if let Some(node) = s.role(lhs.node, "expr") {
                        if matches!(
                            indexed_type(
                                &self.r.specialized_type(Ref { node, ..lhs }, &w.bindings)
                            ),
                            Type::Map { .. } | Type::Unknown | Type::Generic
                        ) {
                            return Err(self.error(w, lhs, "map writes may grow storage"));
                        }
                    }
                }
                if matches!(
                    base_type(&self.r.specialized_type(lhs, &w.bindings)),
                    Type::Set(_)
                ) {
                    return Err(self.error(w, lhs, "set updates may allocate"));
                }
            }
        }
        let s = &self.r.sources[at.file];
        let children = if kind == "Declaration" {
            // Type expressions and constant declarations are not runtime code.
            if !s.node(at.node).mutable {
                return Ok(());
            }
            {
                if let Some(annotation) = s.role(at.node, "type") {
                    let typ = self.r.specialized_type(
                        Ref {
                            node: annotation,
                            ..at
                        },
                        &w.bindings,
                    );
                    if self.big(&typ, &mut HashSet::new()) {
                        return Err(self.error(
                            w,
                            at,
                            "Integer/Rational value storage or implicit cleanup may allocate",
                        ));
                    }
                    if self.value_storage(&typ, &mut HashSet::new(), true) {
                        return Err(self.error(w, at, "generic value storage or implicit cleanup has unproven allocation effects"));
                    }
                }
            }
            if !self.r.sources[at.file].group(at.node, "values").is_empty() {
                for node in self.r.sources[at.file].group(at.node, "names").to_vec() {
                    if self.r.sources[at.file].text(node) == "_" {
                        continue;
                    }
                    let typ = self.r.specialized_type(Ref { node, ..at }, &w.bindings);
                    if self.big(&typ, &mut HashSet::new()) {
                        return Err(self.error(
                            w,
                            at,
                            "Integer/Rational initialized value storage may allocate",
                        ));
                    }
                    if self.value_storage(&typ, &mut HashSet::new(), true) {
                        return Err(self.error(
                            w,
                            at,
                            "generic value initializers have unproven allocation effects",
                        ));
                    }
                }
            }
            self.r.sources[at.file].group(at.node, "values").to_vec()
        } else {
            s.node(at.node).children.to_vec()
        };
        for node in children {
            self.walk(w, Ref { node, ..at })?;
        }
        if kind == "Unary" && n.op == "&" {
            if let Some(node) = self.r.sources[at.file].role(at.node, "expr") {
                self.flow.address_taken(self.r, Ref { node, ..at });
            }
        }
        if matches!(kind,"Compound"|"Cast"|"AutoCast") {
            let typ=self.storage_type(at,&w.bindings,0);
            if Flow::contains_any(self.r,&typ,0) {self.flow.box_value(self.r,at,&typ,0);}
        }
        if kind == "Return" {
            self.flow.terminated = true;
        }
        if let Some(entry) = loop_entry {
            self.flow.join(&entry);
        }
        if kind == "Declaration" {
            self.flow.bind_declaration(self.r, at, &w.bindings);
        }
        if kind == "Assignment" {
            let s = &self.r.sources[at.file];
            let lhs = s.group(at.node, "lhs").to_vec();
            let rhs = s.group(at.node, "rhs").to_vec();
            let values = rhs
                .iter()
                .map(|node| {
                    self.flow
                        .fact(self.r, Ref { node: *node, ..at }, &w.bindings)
                })
                .collect::<Vec<_>>();
            for (i, node) in lhs.into_iter().enumerate() {
                let typ=self.r.specialized_type(Ref{node,..at},&w.bindings);
                if Flow::contains_any(self.r,&typ,0) {if let Some(value)=rhs.get(i){self.flow.box_value(self.r,Ref{node:*value,..at},&typ,0);}}
                let value=if n.op=="=" {values.get(i).cloned().unwrap_or_default()}else{Fact::Unknown};
                let value=Flow::normalize(self.r,value,&typ,&w.bindings);
                self.flow.assign(
                    self.r,
                    Ref { node, ..at },
                    value,
                );
            }
        }
        Ok(())
    }
    fn call(&mut self, w: &Work, at: Ref) -> Result<()> {
        if crate::resulttypes::is_query(self.r, at) {
            return Ok(());
        }
        let s = &self.r.sources[at.file];
        let Some(callee) = s.role(at.node, "callee").map(|node| Ref { node, ..at }) else {
            return Err(self.error(w, at, "unresolved call has unknown allocation effects"));
        };
        if self.r.sources[callee.file].node(callee.node).kind == "Directive"
            && self.r.sources[callee.file].text(callee.node) == "#caller_expression"
        {
            return Ok(());
        }
        let builtin = crate::borroweval::Evaluator {
            resolver: self.r,
            arena: &mut crate::borrowflow::Arena::default(),
        }
        .builtin(at)
        .filter(|_| !self.r.denotes_type(callee));
        let may_write=!self.r.denotes_type(callee)&&builtin.as_deref().is_none_or(|name|name=="copy");
        if builtin
            .as_ref()
            .is_some_and(|name| UNEVALUATED.contains(&name.as_str()))
        {
            return Ok(());
        }
        // A computed callee executes before its invocation. Its producer must
        // retain effect checking even when the returned target has an SDK summary.
        if matches!(self.r.sources[callee.file].node(callee.node).kind.as_str(),"Call"|"Index"){
            self.walk(w,callee)?;
            if self.flow.terminated{return Ok(());}
        }
        if self.failure_call(w, at, callee, builtin.as_deref())? {
            return Ok(());
        }
        let args = self.r.sources[at.file].group(at.node, "args").to_vec();
        for node in &args {
            self.walk(w, Ref { node: *node, ..at })?;
        }
        if self.flow.terminated {
            return Ok(());
        }
        if builtin.as_deref() == Some("unreachable") {
            self.flow.terminated = true;
        }
        if !crate::parallelcompile::known_callback(self.r, callee) {
            let typ = self.r.specialized_type(callee, &w.bindings);
            if let Type::Procedure(signature) = base_type(&typ) {
                let source = &self.r.sources[signature.file];
                if let Some(decl) = source
                    .node(signature.node)
                    .parent
                    .and_then(|p| source.node(p).parent)
                    .filter(|d| source.node(*d).kind == "Declaration")
                {
                    let declaration=Ref{node:decl,file:signature.file};
                    let signature=*signature;
                    self.deferred_calls(w,declaration,signature)?;
                }
            }
        }
        if self.random_call(w,at,callee)? {return Ok(());}
        if self.collection_call(w,at,callee)? {return Ok(());}
        if self.io_call(w, at, callee)? {
            return Ok(());
        }
        if let Some(name) = builtin {
            if UNEVALUATED.contains(&name.as_str()) {
                return Ok(());
            }
            let borrowed=self.borrowed_builtin(w,at,&name)?;
            if name=="type_info_of" && args.first().is_some_and(|node|!self.r.denotes_type(Ref{node:*node,..at})){
                self.io.verify_sdk(self.r,self.overlays).map_err(|reason|self.error(w,at,&reason))?;
            }
            // A discarded result is still produced. In particular min/max
            // over a borrowed logical-number slice clone their selected value;
            // checking only argument headers or named locals misses that work.
            let typ = self.r.specialized_type(at, &w.bindings);
            if self.big(&typ, &mut HashSet::new()) {
                return Err(self.error(w, at, "Integer/Rational builtin results may allocate"));
            }
            if SAFE_BUILTINS.contains(&name.as_str())
                && !HEADER_BUILTINS.contains(&name.as_str())
                && self.value_storage(&typ, &mut HashSet::new(), true)
            {
                return Err(self.error(
                    w,
                    at,
                    "builtin result type has unproven allocation effects",
                ));
            }
            if !borrowed && !SAFE_BUILTINS.contains(&name.as_str()) {
                return Err(self.error(
                    w,
                    at,
                    &format!(
                        "builtin '{name}' may allocate or invoke an unverified runtime callback"
                    ),
                ));
            }
        } else if self.r.denotes_type(callee) {
            let typ = self.r.specialized_type(at, &w.bindings);
            if Flow::any_type(&typ) {for node in &args {self.flow.box_address(self.r,Ref{node:*node,..at});}}
            if self.big(&typ, &mut HashSet::new()) {
                return Err(self.error(w, at, "big-number conversions may allocate"));
            }
            if self.value_storage(&typ, &mut HashSet::new(), true) {
                return Err(self.error(
                    w,
                    at,
                    "generic conversions have unproven allocation effects",
                ));
            }
        } else if self
            .r
            .library_alias_member(callee, "base:intrinsics")
            .is_some_and(|name| {
                crate::purity::intrinsic_allowed(&name)
                    || self.r.standard_library(callee,"base:intrinsics") && crate::semantics::allocation_free_intrinsic(&name)
                    || self.r.standard_library(callee, "base:intrinsics")
                        && [
                            "mem_copy",
                            "mem_copy_non_overlapping",
                            "mem_zero",
                            "mem_zero_volatile",
                            "unaligned_load", "unaligned_store", "volatile_load", "volatile_store",
                            "non_temporal_load", "non_temporal_store", "ptr_offset", "ptr_sub",
                        ]
                        .contains(&name.as_str())
            })
        {
            if self.r.library_alias_member(callee,"base:intrinsics").as_deref()==Some("trap"){self.flow.terminated=true;}
            // Only reviewed scalar/type-query compiler primitives.
            if self.r.library_alias_member(callee,"base:intrinsics").is_some_and(|name|["unaligned_load","unaligned_store","volatile_load","non_temporal_load"].contains(&name.as_str())){
                let typ=self.r.specialized_type(at,&w.bindings);
                if self.value_storage(&typ,&mut HashSet::new(),true){return Err(self.error(w,at,"memory intrinsic result has unproven allocation effects"));}
            }
        } else {
            let known = crate::parallelcompile::known_callback(self.r, callee);
            let target = if known {
                Fact::Unknown
            } else {
                self.flow.fact(self.r, callee, &w.bindings)
            };
            if !known && !matches!(target, Fact::Function(_)) {
                // Trigger import discovery before reporting an unknown identity.
                let _ = self.r.candidates(callee);
                return Err(self.error(
                    w,
                    at,
                    "indirect or unresolved calls have unknown allocation effects",
                ));
            }
            // Reuse the normal overload/specialization cache for static calls.
            // Explicit targets are only needed for runtime callback facts.
            let plans = if known {
                self.r.call_plans(at, &w.bindings)
            } else if let Fact::Function(target) = target {
                self.r.call_plans_for_targets(at, &w.bindings, &[target])
            } else {
                self.r.call_plans(at, &w.bindings)
            };
            if plans.len() != 1 {
                crate::profile::event("no_alloc.unresolved_call",||{
                    let actuals=args.iter().map(|node|{
                        let site=Ref{node:*node,..at};
                        (self.r.sources[site.file].text(site.node).to_owned(),format!("{:?}",self.r.specialized_type(site,&w.bindings)))
                    }).collect::<Vec<_>>();
                    serde_json::json!({"source":self.r.sources[at.file].path,"call":self.r.sources[at.file].text(at.node),"arguments":actuals})
                });
                return Err(self.error(
                    w,
                    at,
                    "the selected overload or specialization has unknown allocation effects",
                ));
            }
            let plan = &plans[0];
            let supplied = args
                .iter()
                .map(|node| {
                    let source = &self.r.sources[at.file];
                    Ref {
                        node: source.role(*node, "value").unwrap_or(*node),
                        ..at
                    }
                })
                .collect::<HashSet<_>>();
            for argument in plan
                .arguments
                .iter()
                .flatten()
                .filter(|arg| !supplied.contains(arg))
            {
                self.walk(
                    &Work {
                        bindings: plan.bindings.clone(),
                        ..w.clone()
                    },
                    *argument,
                )?;
            }
            let trace = self.traces.len();
            self.traces.push((at, w.path));
            let arguments = self.flow.arguments(self.r, plan);
            if self.r.sources[plan.signature.file]
                .node(plan.signature.node)
                .data["diverging"]
                == true
            {
                self.flow.terminated = true;
            }
            self.queue.push_back(Work {
                at: plan.procedure,
                bindings: plan.bindings.clone(),
                root: w.root,
                path: Some(trace),
                procedure: true,
                arguments,
            });
        }
        let s = &self.r.sources[at.file];
        if may_write {
            for node in s.group(at.node, "args").to_vec() {
                let fact = self.flow.fact(self.r, Ref { node, ..at }, &w.bindings);
                self.flow.forget_addresses(&fact);
            }
            self.flow.opaque_write();
        }
        Ok(())
    }
}
fn native_branch(text: &str) -> bool {
    let words = text.split_whitespace().collect::<Vec<_>>();
    match words.as_slice() {
        ["break" | "continue" | "fallthrough"] => true,
        ["break" | "continue", label] => label
            .chars()
            .enumerate()
            .all(|(i, c)| c == '_' || c.is_alphabetic() || i > 0 && c.is_ascii_digit()),
        _ => false,
    }
}
const UNEVALUATED: &[&str] = &[
    "size_of",
    "align_of",
    "offset_of",
    "type_of", "typeid_of",
    "#assert",
    "#config",
    "#caller_expression",
];
const SAFE_BUILTINS: &[&str] = &[
    "len", "type_info_of",
    "cap",
    "min",
    "max",
    "abs",
    "clamp",
    "complex",
    "quaternion",
    "real",
    "imag",
    "jmag",
    "kmag",
    "conj",
    "swizzle",
    "expand_values",
    "compress_values",
    "soa_zip",
    "soa_unzip",
    "raw_data",
    "copy",
    "transmute",
    "unreachable",
];
// These primitives inspect/copy existing storage or return borrowed headers.
// They never acquire logical-number ownership, even for generic elements.
const HEADER_BUILTINS: &[&str] = &[
    "len",
    "cap",
    "raw_data",
    "copy",
    "soa_zip",
    "soa_unzip",
    "unreachable",
];
