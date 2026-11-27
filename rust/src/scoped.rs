//! Lexical owners. Entirely dormant for sources without scoped declarations.
//! Track storage separately from references: copying a scalar/record field is
//! valid, while taking its address or returning a slice view borrows the owner.
use crate::{
    ast::{NodeId, Source},
    diagnostic::{Error, Result},
    semantics::{Ref, Resolver, Type},
};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

pub(crate) fn names(source: &Source, decl: NodeId) -> Vec<NodeId> {
    let Some(rows) = source.info["scoped"].as_array() else {
        return vec![];
    };
    if rows.is_empty() {
        return vec![];
    }
    // Parser metadata is emitted in token order. No per-declaration scan of
    // every marked name, nor a project-wide type analysis on ordinary builds.
    source
        .group(decl, "names")
        .iter()
        .copied()
        .filter(|name| {
            rows.binary_search_by_key(&(source.node(*name).span.start as u64), |row| {
                row[0].as_u64().unwrap_or(u64::MAX)
            })
            .is_ok()
        })
        .collect()
}
pub(crate) fn present(sources: &[Source]) -> bool {
    sources
        .iter()
        .any(|s| !s.excluded && s.info["scoped"].as_array().is_some_and(|r| !r.is_empty()))
}
// Follow result positions, not nested arguments or comprehension elements:
// only the allocation supplying the outer owner controls its shallow cleanup.
pub(crate) fn initializations(r: &mut Resolver<'_>, at: Ref, index: usize) -> Vec<Ref> {
    fn visit(r: &mut Resolver<'_>, at: Ref, index: usize, depth: usize, out: &mut Vec<Ref>) {
        if depth > 96 {
            out.push(at);
            return;
        }
        let s = &r.sources[at.file];
        let kind = s.node(at.node).kind.as_str();
        if s.text(at.node) == "nil" {
            return;
        }
        let roles = match kind {
            "Paren" | "Runtime" | "OrPanic" | "OrReturn" | "OrBranch" | "Cast" | "AutoCast"
            | "TypeAssertion" => vec!["expr"],
            "OrElse" => vec!["x", "y"],
            "Ternary" | "WhenExpr" => vec!["x", "y"],
            _ => vec![],
        };
        if !roles.is_empty() {
            let children = roles
                .into_iter()
                .filter_map(|role| s.role(at.node, role))
                .map(|node| Ref { node, ..at })
                .collect::<Vec<_>>();
            for child in children {
                visit(r, child, index, depth + 1, out);
            }
            return;
        }
        if matches!(kind, "FallbackList" | "SwitchResults") {
            if let Some(node) = s.group(at.node, "args").get(index).copied() {
                visit(r, Ref { node, ..at }, 0, depth + 1, out);
            }
            return;
        }
        if matches!(kind, "SwitchExpr" | "CompileSwitchExpr") {
            let cases = s.node(at.node).data["cases"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|case| {
                    crate::ast::Span::from_wire(&case["result"])
                        .and_then(|span| s.at(span, at.node))
                })
                .collect::<Vec<_>>();
            for node in cases {
                visit(r, Ref { node, ..at }, index, depth + 1, out);
            }
            return;
        }
        if kind == "Call" {
            let callee = s.role(at.node, "callee").map(|node| Ref { node, ..at });
            let arg = s.group(at.node, "args").first().copied();
            if callee.is_some_and(|callee| r.denotes_type(callee))
                && r.type_of(at).big_name().is_none()
            {
                if let Some(node) = arg {
                    visit(r, Ref { node, ..at }, 0, depth + 1, out);
                    return;
                }
            }
            let builtin = {
                let mut arena = crate::borrowflow::Arena::default();
                crate::borroweval::Evaluator {
                    resolver: r,
                    arena: &mut arena,
                }
                .builtin(at)
            };
            if builtin
                .as_deref()
                .is_some_and(|name| matches!(name, "panic" | "unreachable"))
                || r.call_signature(at)
                    .is_some_and(|sig| r.fields(sig, "results").is_empty())
            {
                return;
            }
        }
        out.push(at);
    }
    let mut out = vec![];
    visit(r, at, index, 0, &mut out);
    out
}
pub(crate) fn stores_allocator(typ: &Type) -> bool {
    matches!(
        base(typ),
        Type::Dynamic(_) | Type::Map { .. } | Type::Set(_) | Type::BigInteger | Type::BigRational
    )
}
pub(crate) fn allocator_argument(r: &mut Resolver<'_>, call: Ref) -> Option<Ref> {
    let s = &r.sources[call.file];
    if s.node(call.node).kind != "Call" {
        return None;
    }
    let args = s.group(call.node, "args").to_vec();
    if let Some(node) = args.iter().find_map(|arg| {
        (s.node(*arg).kind == "FieldValue"
            && s.role(*arg, "field")
                .is_some_and(|f| s.text(f) == "allocator"))
        .then(|| s.role(*arg, "value"))
        .flatten()
    }) {
        return Some(Ref { node, ..call });
    }
    let plans = r.call_plans(call, &Default::default());
    if plans.len() == 1 {
        let fields = r.fields(plans[0].signature, "params");
        if let Some(index) = fields.iter().position(|(name, _)| name == "allocator") {
            if let Some(arg) = plans[0]
                .arguments
                .get(index)
                .filter(|args| args.len() == 1)
                .map(|args| args[0])
                .filter(|arg| {
                    arg.file == call.file && s.node(call.node).span.contains(s.node(arg.node).span)
                })
            {
                return Some(arg);
            }
        }
    }
    let builtin = {
        let mut arena = crate::borrowflow::Arena::default();
        crate::borroweval::Evaluator {
            resolver: r,
            arena: &mut arena,
        }
        .builtin(call)
    };
    let positional = args
        .into_iter()
        .filter(|arg| s.node(*arg).kind != "FieldValue")
        .collect::<Vec<_>>();
    if builtin.as_deref() == Some("new") {
        return positional.get(1).map(|node| Ref {
            node: *node,
            ..call
        });
    }
    if builtin.as_deref() == Some("make") {
        for node in positional.into_iter().skip(1) {
            let typ = r.type_of(Ref { node, ..call });
            if let Type::Struct(owner) | Type::Record(owner, _) = base(&typ) {
                let source = &r.sources[owner.file];
                if source
                    .ancestors(owner.node)
                    .chain(std::iter::once(owner.node))
                    .any(|id| {
                        source.node(id).kind == "Declaration"
                            && source
                                .group(id, "names")
                                .iter()
                                .any(|name| source.text(*name) == "Allocator")
                    })
                {
                    return Some(Ref { node, ..call });
                }
            }
        }
    }
    None
}
fn base(typ: &Type) -> &Type {
    typ.storage_shape()
}
fn reference(typ: &Type) -> bool {
    if matches!(base(typ),Type::Scalar{name,..} if matches!(name.as_str(),"rawptr"|"any")) {
        return true;
    }
    !matches!(
        base(typ),
        Type::Scalar { .. }
            | Type::Constant(_)
            | Type::Enum(_)
            | Type::BigInteger
            | Type::BigRational
    )
}
#[derive(Clone, Default, PartialEq, Eq)]
struct Value {
    refs: BTreeSet<Ref>,
    address: BTreeSet<Ref>,
    fields: BTreeMap<String, Value>,
    item: Option<Box<Value>>,
    target: Option<Box<Value>>,
}
impl Value {
    fn bounded(&self) -> bool {
        let mut pending = vec![(self, 0)];
        let mut budget = 8192usize;
        while let Some((v, depth)) = pending.pop() {
            if budget == 0 || depth > 64 {
                return false;
            }
            budget -= 1;
            pending.extend(
                v.fields
                    .values()
                    .chain(v.item.iter().map(Box::as_ref))
                    .chain(v.target.iter().map(Box::as_ref))
                    .map(|v| (v, depth + 1)),
            );
        }
        true
    }
    fn owners(&self) -> BTreeSet<Ref> {
        let mut result = self.refs.clone();
        for child in self
            .fields
            .values()
            .chain(self.item.iter().map(Box::as_ref))
            .chain(self.target.iter().map(Box::as_ref))
        {
            result.extend(child.owners());
        }
        result
    }
    fn merge(&mut self, other: Self) {
        self.refs.extend(other.refs);
        self.address.extend(other.address);
        for (name, v) in other.fields {
            self.fields.entry(name).or_default().merge(v);
        }
        if let Some(v) = other.item {
            self.item.get_or_insert_with(Default::default).merge(*v);
        }
        if let Some(v) = other.target {
            self.target.get_or_insert_with(Default::default).merge(*v);
        }
    }
}
type Env = HashMap<usize, Value>;
enum Step {
    Field(String),
    Item,
    Deref,
}
fn write(value: &mut Value, path: &[Step], incoming: Value) {
    let Some((step, tail)) = path.split_first() else {
        *value = incoming;
        return;
    };
    match step {
        Step::Field(name) => write(
            value.fields.entry(name.clone()).or_default(),
            tail,
            incoming,
        ),
        Step::Item => {
            let mut update = Value::default();
            write(&mut update, tail, incoming);
            value
                .item
                .get_or_insert_with(Default::default)
                .merge(update);
        }
        Step::Deref => write(
            value.target.get_or_insert_with(Default::default),
            tail,
            incoming,
        ),
    }
}
fn write_aliases(value: &mut Value, owners: &BTreeSet<Ref>, path: &[Step], incoming: &Value) {
    if !value.refs.is_disjoint(owners) {
        write(value, path, incoming.clone());
        return;
    }
    for child in value
        .fields
        .values_mut()
        .chain(value.item.iter_mut().map(Box::as_mut))
        .chain(value.target.iter_mut().map(Box::as_mut))
    {
        write_aliases(child, owners, path, incoming);
    }
}
struct Check<'a, 'r> {
    resolver: &'r mut Resolver<'a>,
    owners: HashMap<Ref, Ref>, // owning name -> enclosing procedure
    active: HashSet<Ref>,
}
impl Check<'_, '_> {
    fn complexity(&self, at: Ref) -> Error {
        Error::at(&self.resolver.sources[at.file],self.resolver.sources[at.file].node(at.node).span,"Scoped lifetime analysis cannot verify this recursive or excessively large alias graph; simplify it or use explicit defer cleanup.")
    }
    fn check_env(&self, at: Ref, env: &Env) -> Result<()> {
        if env.values().any(|v| !v.bounded()) {
            return Err(self.complexity(at));
        }
        Ok(())
    }
    fn kind(&self, r: Ref) -> &str {
        &self.resolver.sources[r.file].node(r.node).kind
    }
    fn role(&self, r: Ref, name: &str) -> Option<Ref> {
        self.resolver.sources[r.file]
            .role(r.node, name)
            .map(|node| Ref { node, ..r })
    }
    fn group(&self, r: Ref, name: &str) -> Vec<Ref> {
        self.resolver.sources[r.file]
            .group(r.node, name)
            .iter()
            .map(|node| Ref { node: *node, ..r })
            .collect()
    }
    fn cases(&self, r: Ref) -> Vec<Ref> {
        let s = &self.resolver.sources[r.file];
        s.node(r.node).data["cases"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|case| {
                crate::ast::Span::from_wire(&case["result"]).and_then(|span| s.at(span, r.node))
            })
            .map(|node| Ref { node, ..r })
            .collect()
    }
    fn error(&self, at: Ref, owner: Ref, message: &str) -> Error {
        let s = &self.resolver.sources[at.file];
        let name = self.resolver.sources[owner.file].text(owner.node);
        let mut error = Error::at(
            s,
            s.node(at.node).span,
            format!("Scoped storage '{name}~' {message}"),
        );
        error.diagnostics[0]["code"] = serde_json::json!("thor/scoped-lifetime");
        let os = &self.resolver.sources[owner.file];
        let note = Error::at(os, os.node(owner.node).span, "");
        error.diagnostics[0]["related"] = serde_json::json!([{"pos":note.diagnostics[0]["pos"],"message":format!("Scoped owner '{name}~' declared here; shallow cleanup runs when this scope exits.")}]);
        error
    }
    fn local_escape(&self, at: Ref, proc: Ref, v: &Value) -> Result<()> {
        if let Some(owner) = v
            .owners()
            .into_iter()
            .find(|o| self.owners.get(o) == Some(&proc))
        {
            return Err(self.error(at,owner,"cannot escape its scope through a return value or output storage; return an independent owned copy instead."));
        }
        Ok(())
    }
    fn value(&mut self, r: Ref, env: &mut Env, depth: usize) -> Result<Value> {
        if depth > 96 {
            return Err(Error::at(
                &self.resolver.sources[r.file],
                self.resolver.sources[r.file].node(r.node).span,
                "Scoped lifetime analysis exceeded its expression depth; simplify this expression.",
            ));
        }
        let role = |this: &Self, name| this.role(r, name);
        let mut value = match self.kind(r).to_string().as_str() {
            "Ident" => {
                if let Some(projection) = self.resolver.projection(r) {
                    let v = self.value(projection.owner, env, depth + 1)?;
                    self.member(v, &projection.member)
                } else {
                    self.resolver
                        .symbol(r)
                        .and_then(|s| env.get(&s).cloned())
                        .unwrap_or_default()
                }
            }
            "Paren" | "Cast" | "AutoCast" | "TypeAssertion" | "OrPanic" | "OrReturn"
            | "OrBranch" | "Runtime" => {
                if let Some(expr) = role(self, "expr") {
                    self.value(expr, env, depth + 1)?
                } else {
                    Value::default()
                }
            }
            "FieldValue" => {
                if let Some(v) = role(self, "value") {
                    self.value(v, env, depth + 1)?
                } else {
                    Value::default()
                }
            }
            "Unary" => {
                let mut v = if let Some(expr) = role(self, "expr") {
                    self.value(expr, env, depth + 1)?
                } else {
                    Value::default()
                };
                if self.resolver.sources[r.file].node(r.node).op == "&" {
                    let refs = v.address.clone();
                    v.address.clear();
                    Value {
                        refs,
                        target: Some(Box::new(v)),
                        ..Value::default()
                    }
                } else {
                    Value::default()
                }
            }
            "Deref" => {
                let v = self.value(role(self, "expr").unwrap(), env, depth + 1)?;
                self.deref(v)
            }
            "Selector" => {
                let expr = role(self, "expr").unwrap();
                let mut v = self.value(expr, env, depth + 1)?;
                if matches!(base(&self.resolver.type_of(expr)), Type::Pointer(_)) {
                    v = self.deref(v);
                }
                let name = role(self, "field")
                    .map(|f| self.resolver.sources[f.file].text(f.node).to_string())
                    .unwrap_or_default();
                if name == "table" && matches!(base(&self.resolver.type_of(expr)), Type::Set(_)) {
                    let owners = v.refs.clone();
                    let mut child = self.member(v, &name);
                    child.refs.extend(owners);
                    child
                } else {
                    self.member(v, &name)
                }
            }
            "Index" | "MatrixIndex" => {
                let expr = role(self, "expr").unwrap();
                let v = self.value(expr, env, depth + 1)?;
                let mut item = v.item.map(|i| *i).unwrap_or_default();
                item.address.extend(v.refs);
                if matches!(
                    base(&self.resolver.type_of(expr)),
                    Type::Array { .. } | Type::Fixed { .. } | Type::Unknown | Type::Generic
                ) {
                    item.address.extend(v.address);
                }
                item
            }
            "Slice" => {
                let expr = role(self, "expr").unwrap();
                let mut v = self.value(expr, env, depth + 1)?;
                if matches!(
                    base(&self.resolver.type_of(expr)),
                    Type::Array { .. } | Type::Fixed { .. } | Type::Unknown | Type::Generic
                ) {
                    v.refs.extend(v.address.iter().copied());
                }
                v
            }
            "Compound" => {
                let mut result = Value::default();
                let typ = self.resolver.type_of(r);
                let fields = self.resolver.record_fields(&typ);
                for (i, elem) in self.group(r, "elems").into_iter().enumerate() {
                    let v = self.value(elem, env, depth + 1)?;
                    if let Some(field) = self.role(elem, "field") {
                        result.fields.insert(
                            self.resolver.sources[field.file]
                                .text(field.node)
                                .to_string(),
                            v,
                        );
                    } else if let Some((name, _)) = fields.get(i) {
                        result.fields.insert(name.clone(), v);
                    } else {
                        result.item.get_or_insert_with(Default::default).merge(v);
                    }
                }
                result
            }
            "Ternary" | "WhenExpr" | "OrElse" => {
                let mut result = Value::default();
                for name in ["x", "y"] {
                    if let Some(v) = role(self, name) {
                        result.merge(self.value(v, env, depth + 1)?);
                    }
                }
                result
            }
            "Call" => self
                .call(r, env, depth + 1)?
                .into_iter()
                .next()
                .unwrap_or_default(),
            "SwitchExpr" | "CompileSwitchExpr" => {
                let mut result = Value::default();
                for child in self.cases(r) {
                    result.merge(self.value(child, env, depth + 1)?);
                }
                result
            }
            "SwitchResults" | "FallbackList" => {
                let mut result = Value::default();
                for child in self.group(r, "args") {
                    result.merge(self.value(child, env, depth + 1)?);
                }
                result
            }
            "Comprehension" => {
                if let (Some(loop_), Some(expr)) = (role(self, "loop"), role(self, "expr")) {
                    self.iteration(loop_, expr, env)?;
                }
                let mut item = if let Some(output) = role(self, "output") {
                    self.value(output, env, depth + 1)?
                } else {
                    Value::default()
                };
                if self.resolver.sources[r.file].node(r.node).data["spread"] == true {
                    item = item.item.map(|item| *item).unwrap_or_default();
                }
                Value {
                    item: Some(Box::new(item)),
                    ..Value::default()
                }
            }
            _ => Value::default(),
        };
        // A scalar load is an independent value; its address still refers to
        // storage, but the value cannot carry a lifetime through a return.
        if !reference(&self.resolver.type_of(r)) {
            value.refs.clear();
            value.fields.clear();
            value.item = None;
            value.target = None;
        }
        if !value.bounded() {
            return Err(self.complexity(r));
        }
        Ok(value)
    }
    fn member(&self, v: Value, name: &str) -> Value {
        let mut child = v.fields.get(name).cloned().unwrap_or_default();
        child.address.extend(v.address);
        child
    }
    fn deref(&self, v: Value) -> Value {
        let mut child = v.target.map(|t| *t).unwrap_or_default();
        child.address.extend(v.refs);
        child
    }
    fn call(&mut self, r: Ref, env: &mut Env, depth: usize) -> Result<Vec<Value>> {
        let args = self.group(r, "args");
        let mut values = Vec::new();
        for arg in &args {
            values.push(self.value(*arg, env, depth + 1)?);
        }
        let Some(callee) = self.role(r, "callee") else {
            return Ok(vec![]);
        };
        if self.resolver.denotes_type(callee) {
            return Ok(values.into_iter().take(1).collect());
        }
        if crate::parallelcompile::is_call(self.resolver, r)
            && crate::privileges::verified_library(self.resolver, callee, "thor:parallel")
        {
            // The scheduler owns a new outer slice. Reference-valued callback
            // results may still borrow from inputs; never turn that shallow
            // independence into a claim that the elements are owned copies.
            let results = crate::resulttypes::resolve(self.resolver, r, &Default::default(), None);
            let mut item = Value::default();
            if results.as_ref().is_none_or(|results| {
                results
                    .fields
                    .first()
                    .is_none_or(|(_, t)| !matches!(base(t),Type::Slice(elem) if !reference(elem)))
            }) {
                for value in values {
                    item.merge(value);
                }
            }
            return Ok(vec![Value {
                item: Some(Box::new(item)),
                ..Value::default()
            }]);
        }
        if let Some(name) = crate::evaluation::library_member(self.resolver, callee, "core:fmt") {
            if matches!(
                name.as_str(),
                "aprint"
                    | "aprintln"
                    | "aprintf"
                    | "aprintfln"
                    | "caprint"
                    | "caprintf"
                    | "caprintfln"
            ) && crate::privileges::verified_library(self.resolver, callee, "core:fmt")
            {
                // Formatting copies data into independent output storage.
                return Ok(vec![Value::default()]);
            }
        }
        if let Some(name) = crate::evaluation::library_member(self.resolver, callee, "core:strings")
        {
            if matches!(
                name.as_str(),
                "fields" | "split" | "split_n" | "split_lines"
            ) && crate::privileges::verified_library(self.resolver, callee, "core:strings")
            {
                let input = args
                    .iter()
                    .position(|arg| {
                        self.role(*arg, "field").is_some_and(|field| {
                            self.resolver.sources[field.file].text(field.node) == "s"
                        })
                    })
                    .or_else(|| {
                        args.iter()
                            .position(|arg| self.role(*arg, "field").is_none())
                    });
                return Ok(vec![
                    Value {
                        item: Some(Box::new(
                            input
                                .and_then(|i| values.get(i))
                                .cloned()
                                .unwrap_or_default(),
                        )),
                        ..Value::default()
                    },
                    Value::default(),
                ]);
            }
        }
        let clone = {
            let mut arena = crate::borrowflow::Arena::default();
            let eval = crate::borroweval::Evaluator {
                resolver: self.resolver,
                arena: &mut arena,
            };
            eval.clone_library(r).is_some()
                || eval
                    .builtin(r)
                    .as_deref()
                    .is_some_and(|name| matches!(name, "clone" | "new_clone"))
        };
        if clone {
            let mut v = values.first().cloned().unwrap_or_default();
            v.refs.clear();
            v.address.clear();
            return Ok(vec![v]);
        }
        let outputs = self
            .resolver
            .call_signature(r)
            .map(|signature| self.resolver.fields(signature, "results"));
        if outputs.as_ref().is_none_or(|outputs| outputs.len() <= 1)
            && !reference(&self.resolver.type_of(r))
        {
            return Ok(vec![]);
        }
        let mut union = Value::default();
        for v in &values {
            union.merge(v.clone());
        }
        if union.owners().is_empty() {
            return Ok(vec![]);
        }
        let plans = self.resolver.call_plans(r, &Default::default());
        if plans.len() == 1 {
            let plan = &plans[0];
            {
                let proc = plan.procedure;
                let source = &self.resolver.sources[proc.file];
                if !source.shared_native
                    && self.role(proc, "body").is_some()
                    && !self.active.contains(&proc)
                {
                    let mut inputs = HashMap::new();
                    let signature = self.role(proc, "type").unwrap();
                    let fields = self.resolver.fields(signature, "params");
                    for (i, (name, _)) in fields.iter().enumerate() {
                        let mut v = Value::default();
                        for arg in plan.arguments.get(i).into_iter().flatten() {
                            v.merge(self.value(*arg, env, depth + 1)?);
                        }
                        inputs.insert(name.clone(), v);
                    }
                    return self.procedure(proc, inputs);
                }
            }
        }
        // Unresolved callbacks and foreign implementations cannot promise that
        // a reference result is independent of the inputs.
        union.address.clear();
        if let Some(outputs) = outputs {
            Ok(outputs
                .into_iter()
                .map(|(_, typ)| {
                    if typ.is_some_and(|typ| !reference(&self.resolver.type_of(typ))) {
                        Value::default()
                    } else {
                        union.clone()
                    }
                })
                .collect())
        } else {
            Ok(vec![union])
        }
    }
    fn results(&mut self, r: Ref, count: usize, env: &mut Env) -> Result<Vec<Value>> {
        match self.kind(r) {
            "Paren" | "Runtime" | "OrPanic" | "OrReturn" | "OrBranch" => {
                if let Some(expr) = self.role(r, "expr") {
                    return self.results(expr, count, env);
                }
            }
            "SwitchResults" | "FallbackList" => {
                let mut result = vec![];
                for arg in self.group(r, "args") {
                    result.push(self.value(arg, env, 0)?);
                }
                return Ok(result);
            }
            "OrElse" | "SwitchExpr" | "CompileSwitchExpr" => {
                let mut result = vec![Value::default(); count];
                let children = if self.kind(r) == "OrElse" {
                    [self.role(r, "x"), self.role(r, "y")]
                        .into_iter()
                        .flatten()
                        .collect()
                } else {
                    self.cases(r)
                };
                for child in children {
                    for (i, v) in self
                        .results(child, count, env)?
                        .into_iter()
                        .enumerate()
                        .take(count)
                    {
                        result[i].merge(v);
                    }
                }
                return Ok(result);
            }
            _ => {}
        }
        if self.kind(r) == "Call" {
            let mut result = self.call(r, env, 0)?;
            result.resize(count, Value::default());
            return Ok(result);
        }
        Ok(vec![self.value(r, env, 0)?])
    }
    fn bind(&self, r: Ref, v: Value, env: &mut Env) {
        if let Some(symbol) = self.resolver.symbol(r) {
            env.insert(symbol, v);
        }
    }
    fn destination(&mut self, r: Ref) -> Option<(usize, Vec<Step>)> {
        if self.kind(r) == "Ident" {
            return self.resolver.symbol(r).map(|s| (s, vec![]));
        }
        let expr = self.role(r, "expr")?;
        let (symbol, mut path) = self.destination(expr)?;
        match self.kind(r) {
            "Selector" => {
                if matches!(base(&self.resolver.type_of(expr)), Type::Pointer(_)) {
                    path.push(Step::Deref);
                }
                let name = self.role(r, "field").map(|field| {
                    self.resolver.sources[field.file]
                        .text(field.node)
                        .to_string()
                })?;
                path.push(Step::Field(name));
            }
            "Deref" => path.push(Step::Deref),
            "Index" | "MatrixIndex" => path.push(Step::Item),
            "Paren" => {}
            _ => return None,
        }
        Some((symbol, path))
    }
    fn iteration(&mut self, r: Ref, expr: Ref, env: &mut Env) -> Result<()> {
        let source = self.value(expr, env, 0)?;
        let mut item = source.item.map(|v| *v).unwrap_or_default();
        item.address.extend(source.refs);
        if matches!(
            base(&self.resolver.type_of(expr)),
            Type::Array { .. } | Type::Fixed { .. } | Type::Unknown | Type::Generic
        ) {
            item.address.extend(source.address);
        }
        for (i, mut name) in self.group(r, "names").into_iter().enumerate() {
            if self.kind(name) == "Unary" {
                name = self.role(name, "expr").unwrap_or(name);
            }
            self.bind(
                name,
                if i == 0 {
                    item.clone()
                } else {
                    Value::default()
                },
                env,
            );
        }
        Ok(())
    }
    fn store(&mut self, r: Ref, mut v: Value, env: &mut Env, proc: Ref, at: Ref) -> Result<()> {
        if self.kind(r) == "Ident" {
            if let Some(symbol) = self.resolver.symbol(r) {
                let s = &self.resolver.symbols[symbol];
                if !self.resolver.sources[s.reference.file]
                    .ancestors(s.reference.node)
                    .any(|n| n == proc.node && s.reference.file == proc.file)
                {
                    self.local_escape(at, proc, &v)?;
                }
                let name = Ref {
                    file: s.reference.file,
                    node: s.name_node,
                };
                if self.owners.contains_key(&name) {
                    if let Some(owner) = v
                        .owners()
                        .into_iter()
                        .find(|owner| self.owners.contains_key(owner))
                    {
                        return Err(self.error(
                            at,
                            owner,
                            "cannot be rebound to a borrowed scoped view.",
                        ));
                    }
                    v.refs.insert(name);
                    v.address.insert(name);
                }
                env.insert(symbol, v);
            }
        } else {
            let target = self.value(r, env, 0)?;
            let mut root = r;
            while let Some(expr) = self.role(root, "expr") {
                root = expr;
            }
            let local = self.resolver.symbol(root).is_some_and(|s| {
                let s = &self.resolver.symbols[s];
                !s.parameter
                    && self.resolver.sources[s.reference.file]
                        .ancestors(s.reference.node)
                        .any(|n| n == proc.node && s.reference.file == proc.file)
            });
            if target.address.is_empty() && !local {
                self.local_escape(at, proc, &v)?;
            }
            // Retain references stored in a local aggregate, including fields
            // assigned after its declaration.
            if let Some((symbol, path)) = self.destination(r) {
                let aliases = env.get(&symbol).map(|v| v.refs.clone()).unwrap_or_default();
                if aliases.is_empty() {
                    write(env.entry(symbol).or_default(), &path, v);
                } else {
                    for value in env.values_mut() {
                        write_aliases(value, &aliases, &path, &v);
                    }
                }
            }
        }
        self.check_env(at, env)
    }
    fn statement(
        &mut self,
        r: Ref,
        env: &mut Env,
        proc: Ref,
        returns: &mut Vec<Value>,
    ) -> Result<()> {
        let kind = self.kind(r).to_string();
        match kind.as_str() {
            "Procedure" => return Ok(()),
            "Declaration" => {
                let names = self.group(r, "names");
                let expressions = self.group(r, "values");
                let marked = self::names(&self.resolver.sources[r.file], r.node);
                let mut vals = if expressions.len() == 1 {
                    self.results(expressions[0], names.len(), env)?
                } else {
                    let mut vs = vec![];
                    for e in &expressions {
                        vs.push(self.value(*e, env, 0)?);
                    }
                    vs
                };
                vals.resize(names.len(), Value::default());
                for (i, name) in names.into_iter().enumerate() {
                    let mut v = vals[i].clone();
                    v.address.clear();
                    if marked.contains(&name.node) {
                        let typ = self.resolver.type_of(name);
                        if !matches!(
                            base(&typ),
                            Type::Pointer(_)
                                | Type::Slice(_)
                                | Type::Dynamic(_)
                                | Type::Map { .. }
                                | Type::Set(_)
                                | Type::String { .. }
                                | Type::BigInteger
                                | Type::BigRational
                                | Type::Generic
                                | Type::Unknown
                        ) {
                            return Err(self.error(name,name,"requires a supported owning pointer, slice, string, dynamic array, map, set, Integer, or Rational."));
                        }
                        if let Some(expr) = expressions.get(i).or_else(|| expressions.first()) {
                            let result_index = if expressions.len() == 1 { i } else { 0 };
                            let leaves = initializations(self.resolver, *expr, result_index);
                            for expr in leaves {
                                if matches!(
                                    self.kind(expr),
                                    "Ident"
                                        | "Selector"
                                        | "Index"
                                        | "Slice"
                                        | "Unary"
                                        | "Literal"
                                        | "Deref"
                                ) && self.resolver.sources[expr.file].text(expr.node) != "nil"
                                {
                                    return Err(self.error(expr,name,"requires independent owned storage, not a borrowed value, literal, or address; allocate or clone it first."));
                                }
                                if self.kind(expr) == "Compound"
                                    && matches!(base(&typ), Type::Slice(_))
                                {
                                    return Err(self.error(expr,name,"cannot own a slice literal's stack/static storage; clone the literal or use make."));
                                }
                                if self.kind(expr) == "Call" {
                                    for plan in self.resolver.call_plans(expr, &Default::default())
                                    {
                                        let declaration = self.resolver.sources
                                            [plan.procedure.file]
                                            .node(plan.procedure.node)
                                            .parent
                                            .map(|node| Ref {
                                                node,
                                                ..plan.procedure
                                            })
                                            .unwrap_or(plan.procedure);
                                        if self.resolver.attributes(declaration).iter().any(
                                            |(name, _)| {
                                                name.starts_with("deferred_out")
                                                    || name.starts_with("deferred_in_out")
                                            },
                                        ) {
                                            return Err(self.error(expr,name,"cannot own a result that already has deferred-result cleanup."));
                                        }
                                    }
                                }
                            }
                            if let Some(owner) = v
                                .refs
                                .iter()
                                .copied()
                                .find(|owner| self.owners.contains_key(owner))
                            {
                                return Err(self.error(*expr,owner,"is borrowed by this initializer; a second scoped owner would clean it twice."));
                            }
                        }
                        v.refs.insert(name);
                        v.address.insert(name);
                    }
                    self.bind(name, v, env);
                }
                return Ok(());
            }
            "Assignment" => {
                let lhs = self.group(r, "lhs");
                let rhs = self.group(r, "rhs");
                let mut vs = if rhs.len() == 1 {
                    self.results(rhs[0], lhs.len(), env)?
                } else {
                    let mut vs = vec![];
                    for e in rhs {
                        vs.push(self.value(e, env, 0)?);
                    }
                    vs
                };
                vs.resize(lhs.len(), Value::default());
                for (name, v) in lhs.into_iter().zip(vs) {
                    self.store(name, v, env, proc, r)?;
                }
                return Ok(());
            }
            "Return" => {
                let args = self.group(r, "values");
                let signature = self.role(proc, "type").unwrap();
                let fields = self.resolver.fields(signature, "results");
                let mut values = if args.len() == 1 {
                    self.results(args[0], fields.len().max(1), env)?
                } else {
                    let mut vs = vec![];
                    for a in &args {
                        vs.push(self.value(*a, env, 0)?);
                    }
                    vs
                };
                if args.is_empty() {
                    if let Some(results) = self.role(signature, "results") {
                        for field in self.resolver.sources[results.file]
                            .node(results.node)
                            .children
                            .clone()
                        {
                            for name in self.group(
                                Ref {
                                    node: field,
                                    ..results
                                },
                                "names",
                            ) {
                                values.push(self.value(name, env, 0)?);
                            }
                        }
                    }
                }
                for (i, v) in values.into_iter().enumerate() {
                    self.local_escape(args.get(i).copied().unwrap_or(r), proc, &v)?;
                    if returns.len() <= i {
                        returns.resize(i + 1, Value::default());
                    }
                    returns[i].merge(v);
                }
                return Ok(());
            }
            "If" | "When" => {
                if let Some(init) = self.role(r, "init") {
                    self.statement(init, env, proc, returns)?;
                }
                if kind == "When" {
                    if let Some(known) = self
                        .role(r, "cond")
                        .and_then(|cond| self.resolver.condition(cond))
                    {
                        if let Some(body) = self.role(r, if known { "body" } else { "else" }) {
                            self.statement(body, env, proc, returns)?;
                        }
                        return Ok(());
                    }
                }
                let before = env.clone();
                let mut yes = before.clone();
                let mut no = before;
                if let Some(body) = self.role(r, "body") {
                    self.statement(body, &mut yes, proc, returns)?;
                }
                if let Some(body) = self.role(r, "else") {
                    self.statement(body, &mut no, proc, returns)?;
                }
                for (s, v) in no {
                    yes.entry(s).or_default().merge(v);
                }
                *env = yes;
                return Ok(());
            }
            "Range" | "For" => {
                if let Some(init) = self.role(r, "init") {
                    self.statement(init, env, proc, returns)?;
                }
                if kind == "Range" {
                    if let Some(expr) = self.role(r, "expr") {
                        self.iteration(r, expr, env)?;
                    }
                }
                // Union the zero-iteration state and back edge. Provenance is
                // finite; cap iterations by the number of local bindings.
                let entry = env.clone();
                for _ in 0..env.len().saturating_add(2) {
                    let old = env.clone();
                    if let Some(body) = self.role(r, "body") {
                        self.statement(body, env, proc, returns)?;
                    }
                    if let Some(post) = self.role(r, "post") {
                        self.statement(post, env, proc, returns)?;
                    }
                    for (s, v) in &entry {
                        env.entry(*s).or_default().merge(v.clone());
                    }
                    self.check_env(r, env)?;
                    if *env == old {
                        break;
                    }
                }
                return Ok(());
            }
            _ => {}
        }
        if kind == "Call" {
            self.call(r, env, 0)?;
            return Ok(());
        }
        for child in self.resolver.sources[r.file].node(r.node).children.clone() {
            self.statement(Ref { node: child, ..r }, env, proc, returns)?;
        }
        Ok(())
    }
    fn procedure(&mut self, proc: Ref, mut inputs: HashMap<String, Value>) -> Result<Vec<Value>> {
        if self.active.len() >= 64 {
            return Err(self.complexity(proc));
        }
        self.active.insert(proc);
        let mut env = Env::new();
        let signature = self.role(proc, "type").unwrap();
        if let Some(params) = self.role(signature, "params") {
            for field in self.resolver.sources[params.file]
                .node(params.node)
                .children
                .clone()
            {
                for name in self.group(
                    Ref {
                        node: field,
                        ..params
                    },
                    "names",
                ) {
                    let text = self.resolver.sources[name.file].text(name.node);
                    let v = inputs.remove(text).unwrap_or_default();
                    self.bind(name, v, &mut env);
                }
            }
        }
        let mut result = vec![];
        if let Some(body) = self.role(proc, "body") {
            self.statement(body, &mut env, proc, &mut result)?;
        }
        self.active.remove(&proc);
        Ok(result)
    }
}
pub(crate) fn validate(resolver: &mut Resolver<'_>) -> Result<()> {
    if !present(resolver.sources) {
        return Ok(());
    }
    let _phase = crate::profile::span("analysis.scoped_owners");
    let mut owners = HashMap::new();
    let mut roots = BTreeSet::new();
    for (file, s) in resolver.sources.iter().enumerate() {
        if s.excluded
            || !s.info["scoped"]
                .as_array()
                .is_some_and(|rows| !rows.is_empty())
        {
            continue;
        }
        for (i, n) in s.nodes.iter().enumerate() {
            if n.kind != "Declaration" {
                continue;
            }
            let decl = NodeId(i);
            if !resolver.active_definition(Ref { file, node: decl }) {
                continue;
            }
            let marked = names(s, decl);
            if marked.is_empty() {
                continue;
            }
            let proc = s
                .ancestors(decl)
                .find(|id| s.node(*id).kind == "Procedure")
                .map(|node| Ref { file, node });
            if proc.is_none()
                || !n.parent.is_some_and(|p| {
                    s.node(p).kind == "Case"
                        || s.node(p).kind == "Block"
                            && (s.text(p).trim_start().starts_with('{')
                                || s.role(p, "label").is_some())
                })
            {
                return Err(Error::at(s,n.span,"Scoped storage '~' requires a local declaration in a braced scope, not a field, parameter, global, or control-flow initializer."));
            }
            let proc = proc.unwrap();
            roots.insert(proc);
            for name in marked {
                owners.insert(Ref { file, node: name }, proc);
            }
        }
    }
    let mut check = Check {
        resolver,
        owners,
        active: HashSet::new(),
    };
    for proc in roots {
        check.procedure(proc, HashMap::new())?;
    }
    Ok(())
}
