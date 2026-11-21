//! RNG identities and source-verified native entropy/generator boundaries.
//! Standard distributions are checked normally; custom callbacks are never
//! trusted solely because they have the Random_Generator_Proc signature.
use super::*;
use std::{collections::BTreeMap, path::Path};

#[derive(Default)]
pub(super) struct State {
    ready: bool,
    variants: Vec<Fact>,
    unknown: Option<Ref>,
}
fn child(r: &Resolver<'_>, at: Ref, name: &str) -> Option<Ref> {
    r.sources[at.file]
        .role(at.node, name)
        .map(|node| Ref { node, ..at })
}
fn builtin_context(r: &Resolver<'_>, at: Ref) -> bool {
    let s = &r.sources[at.file];
    matches!(s.node(at.node).kind.as_str(), "Ident" | "Implicit")
        && s.text(at.node) == "context"
        && r.symbol(at).is_none()
}
fn runtime_record(r: &Resolver<'_>, typ: &Type, name: &str) -> bool {
    let owner = match base_type(typ) {
        Type::Struct(at) | Type::Record(at, _) => *at,
        _ => return false,
    };
    let s = &r.sources[owner.file];
    s.info["odin_root"]
        .as_str()
        .is_some_and(|root| s.path == Path::new(root).join("base/runtime/core.odin"))
        && s.node(owner.node).parent.is_some_and(|decl| {
            s.group(decl, "names")
                .iter()
                .any(|node| s.text(*node) == name)
        })
}
fn identity(r: &Resolver<'_>, at: Ref) -> Option<String> {
    let s = &r.sources[at.file];
    if s.node(at.node).kind == "Procedure" {
        let root = s.info["odin_root"].as_str()?;
        if s.path.parent() != Some(Path::new(root).join("base/runtime").as_path()) {
            return None;
        }
        let decl = s.node(at.node).parent?;
        return s
            .group(decl, "names")
            .first()
            .map(|node| s.text(*node).into());
    }
    if let Some(index) = r.symbol(at) {
        let symbol = &r.symbols[index];
        if !symbol.constant || symbol.parameter || symbol.result || symbol.immutable {
            return None;
        }
        if let Some(node) = symbol.value {
            let kind = r.sources[symbol.reference.file].node(node).kind.as_str();
            if kind == "Procedure" {
                return identity(
                    r,
                    Ref {
                        node,
                        file: symbol.reference.file,
                    },
                );
            }
            if !matches!(kind, "Ident" | "Selector" | "Paren") {
                return None;
            }
        }
    }
    if !r.standard_library(at, "base:runtime") {
        return None;
    }
    r.library_alias_member(at, "base:runtime")
}
fn generator(procedure: Fact, data: Fact) -> Fact {
    Fact::Record(BTreeMap::from([
        ("procedure".into(), procedure),
        ("data".into(), data),
    ]))
}
pub(super) fn zero(r: &mut Resolver<'_>, at: Ref, bindings: &Substitutions) -> Option<Fact> {
    let typ = r.specialized_type(at, bindings);
    if runtime_record(r, &typ, "Random_Generator") {
        Some(generator(Fact::Nil, Fact::Nil))
    } else if runtime_record(r, &typ, "Context") {
        Some(Fact::Record(BTreeMap::from([(
            "random_generator".into(),
            generator(Fact::Nil, Fact::Nil),
        )])))
    } else {
        None
    }
}
pub(super) fn value(
    flow: &Flow,
    r: &mut Resolver<'_>,
    at: Ref,
    bindings: &Substitutions,
    depth: usize,
) -> Option<Fact> {
    let s = &r.sources[at.file];
    let kind = s.node(at.node).kind.as_str();
    if builtin_context(r, at) {
        return Some(Fact::Record(BTreeMap::from([(
            "random_generator".into(),
            Fact::AmbientRandom,
        )])));
    }
    if matches!(kind, "Ident" | "Selector" | "Procedure") {
        if identity(r, at).as_deref() == Some("default_random_generator_proc") {
            return Some(Fact::DefaultRandom);
        }
        return None;
    }
    if kind == "Call" {
        let callee = child(r, at, "callee")?;
        if identity(r, callee).as_deref() != Some("default_random_generator") {
            return None;
        }
        let state = s
            .group(at.node, "args")
            .iter()
            .enumerate()
            .find_map(|(i, node)| {
                let label = s.role(*node, "field");
                ((i == 0 && label.is_none()) || label.is_some_and(|label| s.text(label) == "state"))
                    .then_some(Ref {
                        node: s.role(*node, "value").unwrap_or(*node),
                        ..at
                    })
            });
        let data = state
            .map(|arg| flow.fact_at(r, arg, bindings, depth + 1))
            .unwrap_or(Fact::Nil);
        return Some(generator(Fact::DefaultRandom, data));
    }
    if kind == "Compound" {
        let typ = r.specialized_type(at, bindings);
        let gen = runtime_record(r, &typ, "Random_Generator");
        let context = runtime_record(r, &typ, "Context");
        if !gen && !context {
            return None;
        }
        let elems = r.sources[at.file].group(at.node, "elems").to_vec();
        let fields = r.record_fields(&typ);
        let mut result = if gen {
            BTreeMap::from([("procedure".into(), Fact::Nil), ("data".into(), Fact::Nil)])
        } else {
            BTreeMap::from([("random_generator".into(), generator(Fact::Nil, Fact::Nil))])
        };
        for (i, node) in elems.into_iter().enumerate() {
            let s = &r.sources[at.file];
            let name = s
                .role(node, "field")
                .map(|node| s.text(node).to_owned())
                .or_else(|| fields.get(i).map(|(name, _)| name.clone()))?;
            if !gen && name != "random_generator" {
                continue;
            }
            let value = Ref {
                node: s.role(node, "value").unwrap_or(node),
                ..at
            };
            result.insert(name, flow.fact_at(r, value, bindings, depth + 1));
        }
        return Some(Fact::Record(result));
    }
    None
}
fn callback_place(r: &mut Resolver<'_>, callee: Ref, bindings: &Substitutions) -> bool {
    let s = &r.sources[callee.file];
    if s.node(callee.node).kind != "Selector"
        || !s
            .role(callee.node, "field")
            .is_some_and(|node| s.text(node) == "procedure")
    {
        return false;
    }
    let Some(base) = child(r, callee, "expr") else {
        return false;
    };
    let typ = r.specialized_type(base, bindings);
    runtime_record(r, indexed_type(&typ), "Random_Generator")
}
fn context_place(r: &mut Resolver<'_>, mut at: Ref) -> bool {
    for _ in 0..24 {
        if builtin_context(r, at) {
            return true;
        }
        let s = &r.sources[at.file];
        if s.node(at.node).kind == "Paren" {
            let Some(expr) = child(r, at, "expr") else {
                return false;
            };
            at = expr;
            continue;
        }
        if let Some(symbol) = r.symbol(at).map(|i| &r.symbols[i]) {
            if let Some(projection) = &symbol.projection {
                at = projection.owner;
                continue;
            }
        }
        if s.node(at.node).kind == "Selector" {
            let Some(field) = s.role(at.node, "field") else {
                return false;
            };
            if !["random_generator", "procedure", "data"].contains(&s.text(field)) {
                return false;
            }
            let Some(expr) = child(r, at, "expr") else {
                return false;
            };
            let typ = r.specialized_type(expr, &Substitutions::new());
            if let Type::Pointer(inner) = base_type(&typ) {
                if runtime_record(r, inner, "Context") {
                    return true;
                }
            }
            at = expr;
            continue;
        }
        if s.node(at.node).kind == "Deref" {
            let typ = r.specialized_type(at, &Substitutions::new());
            return runtime_record(r, &typ, "Context");
        }
        return false;
    }
    false
}
fn assigned_root(r: &Resolver<'_>, mut at: Ref) -> Option<Ref> {
    for _ in 0..24 {
        if let Some(index) = r.symbol(at) {
            if let Some(projection) = &r.symbols[index].projection {
                at = projection.owner;
                continue;
            }
        }
        if let Some(expr) = child(r, at, "expr") {
            at = expr;
            continue;
        }
        if let Some(key) = Flow::lexical_key(r, at) {
            return Some(key);
        }
        return None;
    }
    None
}
fn static_value(r: &mut Resolver<'_>, at: Ref, changed: &HashSet<Ref>, depth: usize) -> Fact {
    if depth > 24 {
        return Fact::Unknown;
    }
    let flow = Flow::default();
    let value = flow.fact_at(r, at, &Substitutions::new(), depth);
    if value != Fact::Unknown {
        return value;
    }
    if let Some(symbol) = r.symbol(at).map(|i| r.symbols[i].clone()) {
        if r.sources[symbol.reference.file]
            .node(symbol.reference.node)
            .kind
            == "Declaration"
            && !changed.contains(&Ref {
                file: symbol.reference.file,
                node: symbol.name_node,
            })
        {
            if let Some(node) = symbol.value {
                return static_value(
                    r,
                    Ref {
                        file: symbol.reference.file,
                        node,
                    },
                    changed,
                    depth + 1,
                );
            }
        }
    }
    Fact::Unknown
}
impl Check<'_, '_> {
    fn random_world(&mut self, w: &Work, at: Ref) -> Result<()> {
        if self.random.ready {
            return Ok(());
        }
        // Complete source-owned imports before trusting ambient mutable state.
        for (file, s) in self
            .r
            .sources
            .iter()
            .enumerate()
            .filter(|(_, s)| !s.excluded)
        {
            for (i, n) in s
                .nodes
                .iter()
                .enumerate()
                .filter(|(_, n)| n.kind == "Import")
            {
                if !self.r.active_definition(Ref {
                    file,
                    node: NodeId(i),
                }) {
                    continue;
                }
                let Ok(path) = crate::lower::unquote(&n.op) else {
                    continue;
                };
                if !path.starts_with("core:") && !path.starts_with("base:") {
                    let _ = self.r.package_symbol(
                        Ref {
                            file,
                            node: NodeId(i),
                        },
                        &path,
                        "__thor_noalloc_rng_graph",
                    );
                }
            }
        }
        if self.r.demanded_packages().iter().any(|path| {
            !self
                .r
                .sources
                .iter()
                .any(|s| s.path.parent() == Some(path.as_path()))
        }) {
            return Err(self.error(
                w,
                at,
                "cannot prove the ambient RNG until its source dependencies have been inspected",
            ));
        }
        let mut reachable = HashSet::new();
        let mut ordered = Vec::new();
        let mut queue = VecDeque::new();
        for (file, s) in self
            .r
            .sources
            .iter()
            .enumerate()
            .filter(|(_, s)| !s.excluded)
        {
            if s.info["odin_root"]
                .as_str()
                .is_some_and(|root| s.path.starts_with(root))
            {
                continue;
            }
            for (i, n) in s.nodes.iter().enumerate() {
                let site = Ref {
                    file,
                    node: NodeId(i),
                };
                if n.kind == "Procedure" {
                    let decl = n.parent.filter(|node| s.node(*node).kind == "Declaration");
                    if file_enabled(s)
                        || decl.is_some_and(|decl| {
                            s.group(decl, "names")
                                .iter()
                                .any(|node| s.text(*node) == "main")
                                || self.r.attributes(Ref { node: decl, ..site }).iter().any(
                                    |(name, _)| {
                                        ["init", "test", "export", "no_alloc"]
                                            .contains(&name.as_str())
                                    },
                                )
                        })
                    {
                        queue.push_back(site);
                    }
                } else if n.kind == "Declaration"
                    && n.mutable
                    && !s
                        .ancestors(site.node)
                        .any(|node| s.node(node).kind == "Procedure")
                {
                    queue.push_back(site);
                }
            }
        }
        while let Some(site) = queue.pop_front() {
            let s = &self.r.sources[site.file];
            if s.info["odin_root"]
                .as_str()
                .is_some_and(|root| s.path.starts_with(root))
            {
                continue;
            }
            for node in std::iter::once(site.node).chain(s.descendants(site.node)) {
                let here = Ref { node, ..site };
                if !reachable.insert(here) || !self.r.active_definition(here) {
                    continue;
                }
                ordered.push(here);
                if matches!(s.node(node).kind.as_str(), "Ident" | "Selector")
                    && (s.node(node).kind != "Ident" || self.r.expression_reference(here))
                {
                    for target in self.r.candidates(here) {
                        if self.r.sources[target.file].node(target.node).kind == "Procedure" {
                            queue.push_back(target);
                        }
                    }
                }
            }
        }
        let mut changed = HashSet::new();
        for site in &ordered {
            let s = &self.r.sources[site.file];
            if s.node(site.node).kind == "Assignment" {
                for lhs in s.group(site.node, "lhs") {
                    if let Some(key) = assigned_root(
                        self.r,
                        Ref {
                            node: *lhs,
                            ..*site
                        },
                    ) {
                        changed.insert(key);
                    }
                }
            }
            if s.node(site.node).kind == "Unary" && s.node(site.node).op == "&" {
                if let Some(expr) = child(self.r, *site, "expr") {
                    if let Some(key) = assigned_root(self.r, expr) {
                        changed.insert(key);
                    }
                }
            }
        }
        let mut variants = vec![generator(Fact::DefaultRandom, Fact::Nil)];
        let mut unknown = None;
        for site in ordered {
            if !self.r.active_definition(site) {
                continue;
            }
            let s = &self.r.sources[site.file];
            let kind = s.node(site.node).kind.clone();
            if kind == "Assignment" {
                let lhs = s.group(site.node, "lhs").to_vec();
                let rhs = s.group(site.node, "rhs").to_vec();
                for (i, node) in lhs.into_iter().enumerate() {
                    let place = Ref { node, ..site };
                    if !context_place(self.r, place) {
                        continue;
                    }
                    let Some(value) = rhs
                        .get(i)
                        .copied()
                        .filter(|_| self.r.sources[site.file].node(site.node).op == "=")
                    else {
                        unknown = Some(site);
                        continue;
                    };
                    let mut fact = static_value(
                        self.r,
                        Ref {
                            node: value,
                            ..site
                        },
                        &changed,
                        0,
                    );
                    if builtin_context(self.r, place) {
                        fact = Flow::default().field(&fact, "random_generator");
                    } else if !child(self.r, place, "field").is_some_and(|field| {
                        self.r.sources[field.file].text(field.node) == "random_generator"
                    }) {
                        unknown = Some(site);
                        continue;
                    }
                    match fact {
                        Fact::AmbientRandom => {}
                        Fact::Record(_) => variants.push(fact),
                        _ => unknown = Some(site),
                    }
                }
            } else if matches!(kind.as_str(), "Cast" | "AutoCast")
                || kind == "Unary" && s.node(site.node).op == "&"
            {
                if child(self.r, site, "expr").is_some_and(|expr| context_place(self.r, expr)) {
                    unknown = Some(site);
                }
            }
        }
        self.random.variants = variants;
        self.random.unknown = unknown;
        self.random.ready = true;
        crate::profile::count("no_alloc.rng_graphs", 1);
        Ok(())
    }
    pub(super) fn random_call(&mut self, w: &Work, at: Ref, callee: Ref) -> Result<bool> {
        let direct = identity(self.r, callee);
        if direct.as_deref().is_some_and(|name| {
            [
                "default_random_generator",
                "default_random_generator_proc",
                "rand_bytes",
            ]
            .contains(&name)
        }) {
            self.io
                .verify_sdk(self.r, self.overlays)
                .map_err(|reason| self.error(w, at, &reason.replace("I/O", "RNG")))?;
            crate::profile::count("no_alloc.rng_calls", 1);
            return Ok(true);
        }
        if crate::parallelcompile::known_callback(self.r, callee) {
            return Ok(false);
        }
        let fact = self.flow.fact(self.r, callee, &w.bindings);
        if fact == Fact::DefaultRandom {
            self.io
                .verify_sdk(self.r, self.overlays)
                .map_err(|reason| self.error(w, at, &reason.replace("I/O", "RNG")))?;
            crate::profile::count("no_alloc.rng_calls", 1);
            return Ok(true);
        }
        if fact == Fact::AmbientRandomCallback {
            self.io
                .verify_sdk(self.r, self.overlays)
                .map_err(|reason| self.error(w, at, &reason.replace("I/O", "RNG")))?;
            self.random_world(w, at)?;
            if let Some(site) = self.random.unknown {
                let mut error=self.error(w,at,"the ambient RNG can be replaced or escape through an unverified context; pass a known generator explicitly");
                let source = &self.r.sources[site.file];
                let span = source.node(site.node).span;
                let (line, column) = source.position(span.start);
                let (end_line, end_column) = source.position(span.end);
                let message = "This context write or escape prevents proving the ambient RNG.";
                error.diagnostics[0]["related"].as_array_mut().unwrap().push(serde_json::json!({"pos":{"file":source.path,"offset":span.start,"end_offset":span.end,"line":line,"column":column,"end_line":end_line,"end_column":end_column},"message":message,"msgs":[message]}));
                return Err(error);
            }
            let args = self.r.sources[at.file].group(at.node, "args").to_vec();
            let facts = args
                .iter()
                .map(|node| {
                    self.flow
                        .fact(self.r, Ref { node: *node, ..at }, &w.bindings)
                })
                .collect::<Vec<_>>();
            for variant in self.random.variants.clone() {
                match self.flow.field(&variant, "procedure") {
                    Fact::Nil | Fact::DefaultRandom => {}
                    Fact::Function(target) => {
                        let data = self.flow.field(&variant, "data");
                        let trace = self.traces.len();
                        self.traces.push((at, w.path));
                        self.queue.push_back(Work {
                            at: target,
                            bindings: w.bindings.clone(),
                            root: w.root,
                            path: Some(trace),
                            procedure: true,
                            arguments: vec![
                                data,
                                facts.get(1).cloned().unwrap_or_default(),
                                facts.get(2).cloned().unwrap_or_default(),
                            ],
                        });
                    }
                    _ => {
                        return Err(self.error(
                            w,
                            at,
                            "the ambient RNG callback target is unknown and may allocate",
                        ))
                    }
                }
            }
            for fact in facts {
                self.flow.forget_addresses(&fact);
            }
            crate::profile::count("no_alloc.rng_calls", 1);
            return Ok(true);
        }
        if callback_place(self.r, callee, &w.bindings) && fact == Fact::Unknown {
            return Err(self.error(w, at, "RNG callback target is unknown and may allocate"));
        }
        Ok(false)
    }
}
