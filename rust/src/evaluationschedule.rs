//! Reference-order readiness and lazy execution scheduling. Generic caller
//! frames are connected separately; an unbound request is never run eagerly.
use super::*;
use crate::specialization::{Binding, Bindings, Cases, EvaluatedCase, Specializations};
use crate::{project::Project, semantics::Substitutions};
use serde_json::Value;
use std::time::Duration;
#[path = "evaluationcall.rs"]
mod call;
#[path = "evaluationinteger.rs"]
mod integer;
#[path = "evaluationprepared.rs"]
mod prepared;
#[path = "evaluationscalar.rs"]
mod scalar;
#[path = "evaluationbatch.rs"]
mod batch;
#[path = "evaluationspecialize.rs"]
mod specialize;

pub struct Scheduler<'p> {
    project: &'p Project,
    resolver: Resolver<'p>,
    pub results: BTreeMap<Ref, Value>,
    pub skipped: BTreeSet<Ref>,
    pub unchecked: BTreeSet<Ref>,
    pub inactive: BTreeSet<Ref>,
    pub cases: Cases,
    specializations: Specializations,
    specialized_skips: BTreeSet<(Ref, String)>,
    specialized_unchecked: BTreeSet<(Ref, String)>,
    busy: BTreeSet<(Ref, String)>,
    guard_busy: BTreeSet<(Ref, String)>,
    explicit_arguments: usize,
    has_pure: bool,
    schedule_generics: bool,
    work_sources: Option<BTreeSet<usize>>,
    timeout: Duration,
    cache: std::cell::RefCell<crate::evaluationcache::EvaluationCache>,
    execution_records: BTreeMap<Ref, scalar::ExecutionRecord>,
    failed_scalar_targets: BTreeMap<Ref, String>,
    prepared_inputs: std::cell::OnceCell<prepared::PreparedInputs>,
    batch_failed: bool,
    pub scalar_witnesses: BTreeMap<Ref, String>,
}
impl<'p> Scheduler<'p> {
    pub(crate) fn retain_resolver(mut self) {
        // Generic evaluator frames are transient, not the enclosing project's
        // lexical environment. Keep ordinary lowering independent of the last
        // specialization visited by the scheduler.
        self.resolver.set_lexical_bindings(BTreeMap::new(), BTreeMap::new());
        self.project.retain_resolver(self.resolver);
    }
    pub fn new(project: &'p Project, timeout: Duration) -> Self {
        let mut resolver = project.resolver();
        resolver.prepare_contexts();
        crate::lower::prime_logical_constants(&mut resolver);
        let cache_directory = project
            .frontend
            .cache_dir
            .clone()
            .or_else(|| project.options.cache.clone())
            .unwrap_or_else(|| {
                let entry = &project.options.entry;
                if entry.is_file() {
                    entry.parent().unwrap().join(".thor-cache")
                } else {
                    entry.join(".thor-cache")
                }
            });
        let mut this = Self {
            project,
            resolver,
            results: BTreeMap::new(),
            skipped: BTreeSet::new(),
            unchecked: BTreeSet::new(),
            inactive: BTreeSet::new(),
            cases: Cases::new(),
            specializations: Specializations::default(),
            specialized_skips: BTreeSet::new(),
            specialized_unchecked: BTreeSet::new(),
            busy: BTreeSet::new(),
            guard_busy: BTreeSet::new(),
            explicit_arguments: 0,
            has_pure: project
                .sources
                .iter()
                .any(|source| source.nodes.iter().any(|node| node.data["pure"] == true)),
            schedule_generics: false,
            work_sources: None,
            timeout,
            cache: std::cell::RefCell::new(crate::evaluationcache::EvaluationCache::new(
                &cache_directory,
                !project.options.no_cache,
            )),
            execution_records: BTreeMap::new(),
            failed_scalar_targets: BTreeMap::new(),
            prepared_inputs: std::cell::OnceCell::new(),
            batch_failed: false,
            scalar_witnesses: BTreeMap::new(),
        };
        this.prepare_work_sources();
        this
    }
    pub fn cache_report(&self) -> Value {
        let cache = self.cache.borrow();
        serde_json::json!({"stats":cache.stats,"prepared_hits":cache.prepared_hits,"scalar_reused":self.scalar_witnesses.len()})
    }
    fn prepare_work_sources(&mut self) {
        self.schedule_generics = self.has_pure;
        if self.has_pure {
            return;
        }
        let mut sources = BTreeSet::new();
        for (file, source) in self.project.sources.iter().enumerate() {
            for (index, ast) in source.nodes.iter().enumerate() {
                let node = Ref {
                    file,
                    node: NodeId(index),
                };
                let request = ast.kind == "Run"
                    || self.numeric_power(node)
                    || ast.kind == "Comprehension" && self.constant_context(node)
                    || ast.kind == "Call"
                        && crate::extremaplan::plan(&self.resolver, node).is_some()
                        && self.constant_context(node);
                if !request {
                    continue;
                }
                sources.insert(file);
                if source.ancestors(node.node).any(|owner| {
                    let kind = source.node(owner).kind.as_str();
                    kind == "Procedure"
                        && !crate::specialization::parameters(
                            Ref { file, node: owner },
                            &self.resolver,
                        )
                        .is_empty()
                        || kind == "StructType"
                            && source
                                .role(owner, "params")
                                .is_some_and(|params| !source.node(params).children.is_empty())
                }) {
                    self.schedule_generics = true;
                }
            }
        }
        if !self.schedule_generics {
            self.work_sources = Some(sources);
        }
    }
    fn source(&self, node: Ref) -> &'p crate::ast::Source {
        &self.project.sources[node.file]
    }
    fn kind(&self, node: Ref) -> &str {
        self.source(node).node(node.node).kind.as_str()
    }
    fn role(&self, node: Ref, name: &str) -> Option<Ref> {
        self.source(node).role(node.node, name).map(|id| Ref {
            file: node.file,
            node: id,
        })
    }
    fn group(&self, node: Ref, name: &str) -> Vec<Ref> {
        self.source(node)
            .group(node.node, name)
            .iter()
            .map(|id| Ref {
                file: node.file,
                node: *id,
            })
            .collect()
    }
    fn children(&self, node: Ref) -> Vec<Ref> {
        std::iter::once(node.node)
            .chain(self.source(node).descendants(node.node))
            .map(|id| Ref {
                file: node.file,
                node: id,
            })
            .collect()
    }
    fn fail(&self, node: Ref, message: impl Into<String>) -> Error {
        Error::at(
            self.source(node),
            self.source(node).node(node.node).span,
            message,
        )
    }
    fn call(&self, node: Ref) -> Ref {
        if self.kind(node) == "Run" {
            self.role(node, "expr").unwrap_or(node)
        } else {
            node
        }
    }
    fn builtin(&mut self, node: Ref) -> Option<String> {
        crate::purity::builtin_call(&mut self.resolver, node)
    }
    fn type_query(&mut self, node: Ref) -> bool {
        if self.kind(node) != "Call"
            || !self.builtin(node).is_some_and(|name| {
                ["type_of", "size_of", "align_of", "offset_of"].contains(&name.as_str())
            })
        {
            return false;
        }
        let args = self.group(node, "args");
        if args.len()
            != if self.builtin(node).as_deref() == Some("offset_of") {
                2
            } else {
                1
            }
        {
            return false;
        }
        let typ = self
            .resolver
            .specialized_type(args[0], &Substitutions::new());
        if typ.is_unknown() {
            return false;
        }
        TypeCode::new(node.file, Some(node))
            .render(&typ, &self.resolver)
            .is_ok()
    }
    fn queried(&mut self, node: Ref) -> bool {
        self.source(node).ancestors(node.node).any(|id| {
            self.kind(Ref {
                file: node.file,
                node: id,
            }) == "Call"
                && self
                    .builtin(Ref {
                        file: node.file,
                        node: id,
                    })
                    .is_some_and(|name| {
                        ["type_of", "size_of", "align_of", "offset_of"].contains(&name.as_str())
                    })
        })
    }
    fn unbound_generic(&self, node: Ref) -> bool {
        self.source(node).ancestors(node.node).any(|owner| {
            self.source(node).node(owner).kind == "Procedure"
                && crate::specialization::parameters(
                    Ref {
                        file: node.file,
                        node: owner,
                    },
                    &self.resolver,
                )
                .iter()
                .any(|(name, index, _)| {
                    !self
                        .specializations
                        .bindings
                        .contains_key(&(self.resolver.symbols[*index].reference, name.clone()))
                })
        })
    }
    fn key(&self, node: Ref) -> (Ref, String) {
        self.specializations.key(node, &self.resolver)
    }
    fn value(&self, node: Ref) -> Option<&Value> {
        let (_, key) = self.key(node);
        if key.is_empty() {
            self.results.get(&node)
        } else {
            self.cases
                .get(&node)?
                .get(&key)
                .filter(|case| case.skipped.is_none())
                .map(|case| &case.value)
        }
    }
    fn is_skipped(&self, node: Ref) -> bool {
        let key = self.key(node);
        if key.1.is_empty() {
            self.skipped.contains(&node)
        } else {
            self.specialized_skips.contains(&key)
        }
    }
    fn skip(&mut self, node: Ref, inactive: bool) {
        let key = self.key(node);
        if key.1.is_empty() {
            self.skipped.insert(node);
            if inactive {
                self.inactive.insert(node);
            }
        } else {
            self.specialized_skips.insert(key.clone());
            if inactive {
                // A helper must see that this binding was processed even when
                // an inactive native when arm needs neither a value nor a type.
                let unchecked = self.specialized_unchecked.contains(&key);
                let mut bindings = self.specializations.relevant(node, &self.resolver);
                for binding in bindings
                    .values_mut()
                    .filter(|binding| !unchecked && binding.value.is_none() && binding.canonical.is_empty())
                {
                    let mut names = TypeCode::new(node.file, Some(node));
                    if names.render(&binding.typ, &self.resolver).is_err() {
                        // An inactive runtime branch still needs its native
                        // result type. Probe its local type even though no
                        // helper execution supplied an initial identity.
                        binding.canonical = "__thor_unresolved_native_type__".into();
                    }
                }
                self.cases.entry(node).or_default().insert(
                    key.1.clone(),
                    EvaluatedCase {
                        bindings,
                        value: Value::Null,
                        skipped: Some(unchecked),
                    },
                );
            }
        }
    }
    fn mark_unchecked(&mut self, node: Ref) {
        let key = self.key(node);
        if key.1.is_empty() {
            self.unchecked.insert(node);
        } else {
            self.specialized_unchecked.insert(key);
        }
    }
    pub fn constant_context(&mut self, node: Ref) -> bool {
        let source = self.source(node);
        let mut child = node.node;
        for parent in source.ancestors(node.node) {
            let ast = source.node(parent);
            let role = |name| source.role(parent, name);
            match ast.kind.as_str() {
                "Run" => return true,
                "Procedure" => return Some(child) != role("body"),
                "Declaration" => {
                    if Some(child) == role("type") {
                        return true;
                    }
                    if source.group(parent, "values").contains(&child) {
                        return !ast.mutable && !ast.immutable;
                    }
                }
                "ArrayType" if Some(child) == role("len") => return true,
                "StructType" | "UnionType" | "ProcedureType" | "EnumType" | "BitSetType" => {
                    return true
                }
                "When" | "WhenExpr" | "CompileSwitch" | "CompileSwitchExpr" => {
                    if Some(child) == role("cond") || Some(child) == role("selector") {
                        return true;
                    }
                    if ast.data["cases"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .flat_map(|case| case["matches"].as_array().into_iter().flatten())
                        .filter_map(Span::from_wire)
                        .any(|span| span.contains(source.node(node.node).span))
                    {
                        return true;
                    }
                }
                "Call" => {
                    let call = Ref {
                        file: node.file,
                        node: parent,
                    };
                    if self
                        .builtin(call)
                        .is_some_and(|name| ["#assert", "#config"].contains(&name.as_str()))
                    {
                        return true;
                    }
                    if let [plan] = self
                        .resolver
                        .call_plans(call, &Substitutions::new())
                        .as_slice()
                    {
                        for (name, annotation) in self.resolver.fields(plan.signature, "params") {
                            let field = annotation.and_then(|annotation| {
                                self.source(annotation)
                                    .node(annotation.node)
                                    .parent
                                    .map(|id| Ref {
                                        file: annotation.file,
                                        node: id,
                                    })
                            });
                            if let Some(field) = field.filter(|field| {
                                self.group(*field, "names")
                                    .iter()
                                    .any(|name| self.kind(*name) == "PolyType")
                            }) {
                                let arguments = &plan.arguments;
                                let index = self
                                    .resolver
                                    .fields(plan.signature, "params")
                                    .iter()
                                    .position(|(candidate, _)| *candidate == name)
                                    .unwrap_or(arguments.len());
                                if arguments.get(index).is_some_and(|args| {
                                    args.iter().any(|arg| {
                                        arg.file == node.file
                                            && source
                                                .node(arg.node)
                                                .span
                                                .contains(source.node(child).span)
                                    })
                                }) {
                                    let _ = field;
                                    return true;
                                }
                            }
                        }
                    }
                }
                _ => {}
            }
            child = parent;
        }
        false
    }
    fn numeric_power(&mut self,node:Ref)->bool{
        let source=self.source(node);
        if !source.thor||self.kind(node)!="Binary"||source.node(node.node).op!="**"{return false;}
        if self.resolver.logical_enabled(node.file){
            if self.resolver.logical_operator(node,None){return false;}
            for ancestor in source.ancestors(node.node){
                let at=Ref{file:node.file,node:ancestor};let n=source.node(ancestor);
                if n.kind=="Call"{if let Some(callee)=source.role(ancestor,"callee").map(|node|Ref{file:at.file,node}){if self.resolver.denotes_type(callee)&&self.resolver.type_of(callee).big_name().is_some(){return false;}}}
                if n.kind=="Declaration"&&source.role(ancestor,"type").is_some_and(|node|self.resolver.type_of(Ref{file:at.file,node}).big_name().is_some()){return false;}
                if n.kind=="Binary"&&self.resolver.logical_operator(at,None){return false;}
            }
        }
        true
    }
    pub fn automatic(&mut self, node: Ref) -> bool {
        if self.kind(node) == "Comprehension" {
            return self.constant_context(node);
        }
        if self.kind(node) == "Binary" && self.source(node).node(node.node).op == "**" {
            return self.numeric_power(node)&&!self.queried(node);
        }
        if self.kind(node) != "Call"
            || self
                .source(node)
                .node(node.node)
                .parent
                .is_some_and(|id| self.source(node).node(id).kind == "Run")
            || self.queried(node)
        {
            return false;
        }
        if crate::extremaplan::plan(&self.resolver, node).is_some() {
            return self.constant_context(node)
                && !self
                    .source(node)
                    .node(node.node)
                    .parent
                    .is_some_and(|parent| {
                        self.source(node).node(parent).kind == "Declaration"
                            && self.source(node).group(parent, "names").len() > 1
                    });
        }
        if self
            .project
            .options
            .flags
            .iter()
            .any(|flag| flag == "-debug")
            && !self.constant_context(node)
        {
            return false;
        }
        if !self.has_pure {
            return false;
        }
        let plans = self.resolver.call_plans(node, &Substitutions::new());
        if let [plan] = plans.as_slice() {
            self.source(plan.procedure).node(plan.procedure.node).data["pure"] == true
                && self.resolver.fields(plan.signature, "results").len() == 1
        } else {
            false
        }
    }
    pub fn known(&mut self, node: Ref) -> Result<bool> {
        self.known_seen(node, &mut BTreeSet::new())
    }
    fn known_seen(&mut self, node: Ref, seen: &mut BTreeSet<Ref>) -> Result<bool> {
        if !seen.insert(node) {
            return Ok(false);
        }
        if self.value(node).is_some() {
            return Ok(true);
        }
        if self.busy.contains(&self.key(node)) {
            return Err(self.fail(node, "Cyclic #run or automatic fn constant dependency."));
        }
        let source = self.source(node);
        let ast = source.node(node.node);
        let text = source.text(node.node);
        if ast.kind == "Run" {
            self.evaluate(node)?;
            return Ok(self.value(node).is_some());
        }
        if ast.kind == "Comprehension" {
            return if self.constant_context(node) {
                Ok(self.evaluate(node)?.is_some())
            } else {
                Ok(false)
            };
        }
        if ast.kind == "Directive" {
            return Ok(!["file", "directory", "line"].contains(&source.names.get(ast.name)));
        }
        if ["Literal", "ImplicitSelector"].contains(&ast.kind.as_str())
            || ["true", "false", "nil"].contains(&text)
        {
            return Ok(true);
        }
        if self.type_query(node) {
            return Ok(true);
        }
        if let Some(index) = self.resolver.symbol(node) {
            let symbol = &self.resolver.symbols[index];
            if self
                .specializations
                .bindings
                .contains_key(&(symbol.reference, symbol.name.clone()))
            {
                return Ok(true);
            }
        }
        if self.resolver.denotes_type(node) {
            return Ok(!self.children(node).iter().any(|child| {
                self.kind(*child) == "PolyType"
                    || self.resolver.symbol(*child).is_some_and(|index| {
                        let symbol = &self.resolver.symbols[index];
                        symbol.parameter
                            && ["Field", "PolyType", "Range", "TypeSwitch"].contains(
                                &self
                                    .source(symbol.reference)
                                    .node(symbol.reference.node)
                                    .kind
                                    .as_str(),
                            )
                    })
            }));
        }
        match ast.kind.as_str() {
            "Ident" | "Selector" => {
                if ast.kind == "Selector" {
                    let typ = self.resolver.specialized_type(node, &Substitutions::new());
                    if matches!(
                        self.resolver
                            .constant_value(node, &Substitutions::new(), Some(&typ)),
                        Some(crate::semantics::Constant::Enum(..))
                    ) {
                        return Ok(true);
                    }
                }
                if let Some(index) = self.resolver.symbol(node) {
                    let symbol = self.resolver.symbols[index].clone();
                    if !symbol.constant
                        || symbol.immutable
                        || symbol.parameter
                        || symbol.result
                        || ["Field", "Range", "TypeSwitch"].contains(&self.kind(symbol.reference))
                    {
                        return Ok(false);
                    }
                    return symbol
                        .value
                        .map(|id| {
                            self.known_seen(
                                Ref {
                                    file: symbol.reference.file,
                                    node: id,
                                },
                                seen,
                            )
                        })
                        .unwrap_or(Ok(true));
                }
                if ast.kind == "Ident" {
                    return Ok(text.starts_with("ODIN_") && text != "ODIN_BUILD_PROJECT_NAME");
                }
            }
            "Procedure" | "ProcedureGroup" => return Ok(true),
            "Call" => {
                let callee = self.role(node, "callee").unwrap_or(node);
                let mut args = self.group(node, "args");
                let builtin = self.builtin(node);
                if self.explicit_allocator(node) {
                    return Ok(false);
                }
                let library = approved_library(&self.resolver, callee);
                if library
                    .as_ref()
                    .is_some_and(|(path, _)| path == "thor:format")
                {
                    if let Some(arg) = args.first().copied() {
                        let arg = if self.kind(arg) == "FieldValue" {
                            self.role(arg, "value").unwrap_or(arg)
                        } else {
                            arg
                        };
                        let typ = self.resolver.type_of(arg);
                        if !matches!(
                            typ,
                            Type::Scalar { .. } | Type::Enum(_) | Type::String { .. } | Type::BigInteger | Type::BigRational
                        ) {
                            return Ok(false);
                        }
                    }
                }
                if builtin.as_deref() == Some("make") {
                    let typ = args
                        .first()
                        .map(|arg| self.resolver.specialized_type(*arg, &Substitutions::new()))
                        .unwrap_or(Type::Unknown);
                    let limit = if matches!(typ, Type::Dynamic(_)) {
                        3
                    } else {
                        2
                    };
                    if !matches!(typ, Type::Slice(_) | Type::Dynamic(_)) || args.len() > limit {
                        return Ok(false);
                    }
                }
                let approved = library.is_some() || primitive(&mut self.resolver, callee);
                if self.resolver.denotes_type(callee) {
                    let typ = self
                        .resolver
                        .specialized_type(callee, &Substitutions::new());
                    if matches!(typ, Type::Pointer(_) | Type::Procedure(_))
                        || matches!(typ,Type::Scalar {ref name,..}|Type::String {ref name,..} if ["rawptr","cstring","any","typeid"].contains(&name.as_str()))
                    {
                        return Ok(false);
                    }
                }
                if builtin.as_deref() == Some("#config") && !args.is_empty() {
                    args.remove(0);
                }
                let targets = self.resolver.call_plans(node, &Substitutions::new());
                let direct = self.resolver.symbol(callee).is_none_or(|index| {
                    let symbol = &self.resolver.symbols[index];
                    symbol.constant && !symbol.immutable && !symbol.parameter && !symbol.result
                });
                let eligible = direct
                    && !targets.is_empty()
                    && targets.iter().all(|plan| {
                        self.kind(plan.procedure) == "Procedure"
                            && self.role(plan.procedure, "body").is_some()
                    });
                if builtin.is_none()
                    && !approved
                    && !self.resolver.denotes_type(callee)
                    && !eligible
                {
                    return Ok(false);
                }
                if !targets.is_empty()
                    && self.explicit_arguments == 0
                    && targets.iter().any(|plan| {
                        self.source(plan.procedure).node(plan.procedure.node).data["pure"] != true
                    })
                {
                    return Ok(false);
                }
                for arg in args {
                    if !self.known_seen(arg, &mut seen.clone())? {
                        return Ok(false);
                    }
                }
                if self.automatic(node) {
                    self.evaluate(node)?;
                }
                return Ok(true);
            }
            "FieldValue" => {
                return self
                    .role(node, "value")
                    .map(|value| self.known_seen(value, seen))
                    .unwrap_or(Ok(false))
            }
            "Compound" | "Interpolation" | "SwitchResults" => {
                if ast.kind == "Compound"
                    && self.role(node, "type").is_some_and(|typ| {
                        source_location_dependent(&self.resolver, typ, &mut HashSet::new())
                    })
                {
                    return Ok(false);
                }
                let group = if ast.kind == "Compound" {
                    "elems"
                } else {
                    "args"
                };
                for value in self.group(node, group) {
                    if !self.known_seen(value, &mut seen.clone())? {
                        return Ok(false);
                    }
                }
                return Ok(true);
            }
            "Paren" | "Unary" | "Cast" => {
                if ast.kind == "Unary" && ast.op == "&" {
                    return Ok(false);
                }
                if ast.kind == "Cast" && ast.op == "transmute" {
                    let typ = self
                        .role(node, "type")
                        .map(|typ| self.resolver.specialized_type(typ, &Substitutions::new()))
                        .unwrap_or(Type::Unknown);
                    let input = self
                        .role(node, "expr")
                        .map(|value| self.resolver.specialized_type(value, &Substitutions::new()))
                        .unwrap_or(Type::Unknown);
                    if crate::purity::Purity::scalar_transmute(&typ) != Some(true)
                        || crate::purity::Purity::scalar_transmute(&input) != Some(true)
                    {
                        return Ok(false);
                    }
                }
                return self
                    .role(node, "expr")
                    .map(|value| self.known_seen(value, seen))
                    .unwrap_or(Ok(false));
            }
            "Slice" | "Index" => {
                for role in if ast.kind == "Slice" {
                    &["expr", "low", "high"][..]
                } else {
                    &["expr", "index"][..]
                } {
                    if let Some(value) = self.role(node, role) {
                        if !self.known_seen(value, &mut seen.clone())? {
                            return Ok(false);
                        }
                    }
                }
                return Ok(true);
            }
            "Binary" => {
                let (Some(left), Some(right)) = (self.role(node, "left"), self.role(node, "right"))
                else {
                    return Ok(false);
                };
                if !self.known_seen(left, &mut seen.clone())? {
                    return Ok(false);
                }
                if ["&&", "||"].contains(&ast.op.as_str()) {
                    let selected = self.boolean(left)?;
                    if ast.op == "&&" && !selected || ast.op == "||" && selected {
                        return Ok(true);
                    }
                }
                let ready = self.known_seen(right, &mut seen.clone())?;
                if ready && self.numeric_power(node) {
                    self.evaluate(node)?;
                }
                return Ok(ready);
            }
            "Ternary" | "WhenExpr" => {
                let Some(condition) = self.role(node, "cond") else {
                    return Ok(false);
                };
                if !self.known_seen(condition, &mut seen.clone())? {
                    return Ok(false);
                }
                let selected = self.boolean(condition)?;
                return self
                    .role(node, if selected { "x" } else { "y" })
                    .map(|value| self.known_seen(value, seen))
                    .unwrap_or(Ok(false));
            }
            "SwitchExpr" | "CompileSwitchExpr" => {
                if let Some(selector) = self.role(node, "selector") {
                    if !self.known_seen(selector, &mut seen.clone())? {
                        return Ok(false);
                    }
                }
                for (index, case) in ast.data["cases"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .enumerate()
                {
                    for value in case["matches"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Span::from_wire)
                        .filter_map(|span| source.at(span, node.node))
                    {
                        if !self.known_seen(
                            Ref {
                                file: node.file,
                                node: value,
                            },
                            &mut seen.clone(),
                        )? {
                            return Ok(false);
                        }
                    }
                    if self.execute(node, Mode::Case(index))?["value"] == true {
                        return Span::from_wire(&case["result"])
                            .and_then(|span| source.at(span, node.node))
                            .map(|value| {
                                self.known_seen(
                                    Ref {
                                        file: node.file,
                                        node: value,
                                    },
                                    seen,
                                )
                            })
                            .unwrap_or(Ok(false));
                    }
                }
            }
            _ => {}
        }
        Ok(false)
    }
    pub(super) fn required_evaluations(&mut self, graph: &Closure) -> BTreeSet<Ref> {
        let mut required = graph.nested_runs.clone();
        for declaration in &graph.keep {
            for request in self.resolver.declaration_evaluations(*declaration) {
                if self.automatic(request) { required.insert(request); }
            }
        }
        required
    }
    fn execute(&mut self, node: Ref, mode: Mode) -> Result<Value> {
        let roots = request_roots(self.source(node), node, mode);
        let graph = Extraction::build(&mut self.resolver, &roots)?;
        for nested in self.required_evaluations(&graph) {
            if self.value(nested).is_none() && !self.cases.contains_key(&nested) {
                self.evaluate(nested)?;
            }
        }
        let prepared = self.prepared_key(node, &roots, &graph, mode);
        let mut cache_misses = self
            .failed_scalar_targets
            .get(&node)
            .cloned()
            .into_iter()
            .collect::<BTreeSet<_>>();
        if let Some(key) = &prepared {
            let (value, target) = self.cache.borrow_mut().prepared(
                Some(key),
                self.failed_scalar_targets.get(&node).map(String::as_str),
            );
            if let Some(value) = value {
                self.execution_records.insert(
                    node,
                    scalar::ExecutionRecord {
                        target,
                        keep: graph.keep,
                        procedures: graph.procedures,
                    },
                );
                return Ok(value);
            }
            cache_misses.extend(target);
        }
        if matches!(mode, Mode::Value) {
            if let Some(value) = self.batch(node)? { return Ok(value); }
        }
        let skips = self
            .inactive
            .iter()
            .map(|node| (*node, self.unchecked.contains(node)))
            .collect();
        let executed = self.project.execute_evaluation_with_context(
            node,
            self.timeout,
            &Context {
                batch: &[],
                results: &self.results,
                skips: &skips,
                cases: &self.cases,
                bindings: &self.specializations.bindings,
                frames: &self.specializations.frames,
                cache: Some(&self.cache),
                cache_misses: &cache_misses,
            },
            mode,
        )?;
        if matches!(mode, Mode::Value) {
            self.cache
                .borrow()
                .remember(prepared.as_deref(), executed.target.as_deref());
            self.execution_records.insert(
                node,
                scalar::ExecutionRecord {
                    target: executed.target,
                    keep: graph.keep,
                    procedures: graph.procedures,
                },
            );
        }
        let mut value = executed.value;
        if value["kind"] == "specialized" {
            let names = value["types"]
                .as_array()
                .ok_or_else(|| self.fail(node, "Invalid specialization metadata."))?;
            let mut names = names.iter();
            for binding in self
                .specializations
                .bindings
                .values_mut()
                .filter(|binding| binding.value.is_none())
            {
                let packet = names
                    .next()
                    .ok_or_else(|| Error::new("Invalid specialization metadata."))?;
                let bytes =
                    crate::values::bytes(packet).map_err(|error| Error::new(error.to_string()))?;
                let mut canonical = String::from_utf8(bytes)
                    .map_err(|_| Error::new("Invalid specialization type name."))?;
                for source in self
                    .project
                    .sources
                    .iter()
                    .filter(|source| source.shared_native)
                {
                    if let Some(span) = source.node(NodeId(0)).spans.get("package") {
                        let internal = format!(
                            "thor_eval_{}",
                            &crate::digest::sha256(
                                source.path.parent().unwrap().to_string_lossy().as_bytes()
                            )[..12]
                        );
                        canonical = canonical.replace(
                            &(internal + "."),
                            &(source.raw[span.start..span.end].to_string() + "."),
                        );
                    }
                }
                binding.canonical = canonical;
            }
            if names.next().is_some() {
                return Err(self.fail(node, "Invalid specialization metadata."));
            }
            return Ok(value["value"].take());
        }
        Ok(value)
    }
    fn ensure_runs(&mut self, node: Ref) -> Result<()> {
        for child in self.children(node) {
            if self.kind(child) == "Run" && self.value(child).is_none() && !self.is_skipped(child)
                || self.automatic(child) && !self.busy.contains(&self.key(child))
            {
                self.evaluate(child)?;
            }
        }
        Ok(())
    }
    pub fn boolean(&mut self, node: Ref) -> Result<bool> {
        if let Some(crate::semantics::Constant::Boolean(value)) =
            self.resolver
                .constant_value(node, &Substitutions::new(), None)
        {
            return Ok(value);
        }
        let key = self.key(node);
        if !self.guard_busy.insert(key.clone()) {
            return Err(self.fail(node, "Cyclic compile-time selection dependency."));
        }
        let result = (|| {
            self.ensure_runs(node)?;
            let data = self.execute(node, Mode::Condition)?;
            data["value"].as_bool().ok_or_else(|| {
                self.fail(node, "Compile-time selection requires a boolean condition.")
            })
        })();
        self.guard_busy.remove(&key);
        result
    }
    pub fn active(&mut self, node: Ref, specialization: bool) -> Result<Option<bool>> {
        let source = self.source(node);
        let span = source.node(node.node).span;
        for id in source
            .ancestors(node.node)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
        {
            let parent = Ref {
                file: node.file,
                node: id,
            };
            let ast = source.node(id);
            if specialization
                && ["If", "Ternary", "Binary", "For", "Range", "SwitchExpr"]
                    .contains(&ast.kind.as_str())
            {
                continue;
            }
            let inside = |value: Option<Ref>| {
                value.is_some_and(|value| source.node(value.node).span.contains(span))
            };
            if ["When", "WhenExpr", "Ternary", "If"].contains(&ast.kind.as_str()) {
                let Some(condition) = self.role(parent, "cond") else {
                    continue;
                };
                if inside(Some(condition)) {
                    continue;
                }
                if ["If", "Ternary"].contains(&ast.kind.as_str()) && !self.known(condition)? {
                    if self.kind(node) != "Run" {
                        return Ok(None);
                    }
                    continue;
                }
                if !self.known(condition)? {
                    return Ok(None);
                }
                let selected = self.boolean(condition)?;
                let yes = self.role(
                    parent,
                    if ["When", "If"].contains(&ast.kind.as_str()) {
                        "body"
                    } else {
                        "x"
                    },
                );
                let no = self.role(
                    parent,
                    if ["When", "If"].contains(&ast.kind.as_str()) {
                        "else"
                    } else {
                        "y"
                    },
                );
                if inside(yes) && !selected || inside(no) && selected {
                    if ["When", "WhenExpr"].contains(&ast.kind.as_str()) {
                        self.mark_unchecked(node);
                    }
                    return Ok(Some(false));
                }
            } else if ast.kind == "Binary" && ["&&", "||"].contains(&ast.op.as_str()) {
                if inside(self.role(parent, "right")) {
                    let Some(left) = self.role(parent, "left") else {
                        continue;
                    };
                    if self.kind(node) != "Run" && !self.known(left)? {
                        return Ok(None);
                    }
                    if self.known(left)? {
                        let selected = self.boolean(left)?;
                        if ast.op == "&&" && !selected || ast.op == "||" && selected {
                            return Ok(Some(false));
                        }
                    }
                }
            } else if ["For", "Range"].contains(&ast.kind.as_str()) && self.kind(node) != "Run" {
                if inside(self.role(parent, "body"))
                    || ast.kind == "For" && inside(self.role(parent, "post"))
                {
                    let Some(condition) = self.role(parent, "cond") else {
                        return Ok(None);
                    };
                    if !self.known(condition)? {
                        return Ok(None);
                    }
                    if !self.boolean(condition)? {
                        return Ok(Some(false));
                    }
                }
            } else if ["CompileSwitch", "CompileSwitchExpr", "SwitchExpr"]
                .contains(&ast.kind.as_str())
            {
                let selector = self.role(parent, "selector");
                if inside(selector) {
                    continue;
                }
                if let Some(selector) = selector {
                    if !self.known(selector)? {
                        return Ok(None);
                    }
                }
                for (index, case) in ast.data["cases"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .enumerate()
                {
                    let matches = case["matches"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Span::from_wire)
                        .collect::<Vec<_>>();
                    if matches.iter().any(|matched| matched.contains(span)) {
                        break;
                    }
                    for matched in &matches {
                        if let Some(value) = source.at(*matched, id) {
                            if !self.known(Ref {
                                file: node.file,
                                node: value,
                            })? {
                                return Ok(None);
                            }
                        }
                    }
                    let selected = self.execute(parent, Mode::Case(index))?["value"] == true;
                    let inside = case["start"]
                        .as_u64()
                        .is_some_and(|start| start as usize <= span.start)
                        && case["end"]
                            .as_u64()
                            .is_some_and(|end| span.end <= end as usize);
                    if inside || selected {
                        if ast.kind != "SwitchExpr" && (!inside || !selected) {
                            self.mark_unchecked(node);
                        }
                        return Ok(Some(inside && selected));
                    }
                }
            }
        }
        Ok(Some(true))
    }
    pub fn evaluate(&mut self, node: Ref) -> Result<Option<Value>> {
        if self.unbound_generic(node) {
            return Ok(None);
        }
        if let Some(value) = self.value(node) {
            return Ok(Some(value.clone()));
        }
        let key = self.key(node);
        if !self.busy.insert(key.clone()) {
            return Err(self.fail(node, "Cyclic #run or automatic fn constant dependency."));
        }
        let result = self.evaluate_inner(node);
        self.busy.remove(&key);
        result
    }
    fn evaluate_inner(&mut self, node: Ref) -> Result<Option<Value>> {
        match self.active(node, false)? {
            None => {
                self.skip(node, false);
                return Ok(None);
            }
            Some(false) => {
                self.skip(node, true);
                return Ok(None);
            }
            Some(true) => {}
        }
        let call = self.call(node);
        if self.kind(node) == "Comprehension" && self.role(node, "type").is_some() {
            return Err(self.fail(node,"Use bare [expression for ...] for a compile-time fixed array; typed allocated comprehensions require a runtime binding."));
        }
        if self.kind(call) != "Call" && !["Binary", "Comprehension"].contains(&self.kind(call)) {
            return Err(self.fail(node, "#run requires a direct eligible procedure call."));
        }
        if self.kind(call) == "Call" {
            let callee = self.role(call, "callee").unwrap_or(call);
            let io = FlowEvaluator {
                resolver: &mut self.resolver,
                arena: &mut Arena::default(),
            }
            .io_call(call);
            if io {
                return Err(self.fail(node, self.io_message(call)));
            }
            if self.kind(node) == "Run" {
                if let Some(value) = self.load_scalar(node) {
                    self.results.insert(node, value.clone());
                    self.resolver.remember_evaluation(node, value.clone());
                    return Ok(Some(value));
                }
            }
            let plans = self.resolver.call_plans(call, &Substitutions::new());
            let builtin = self.builtin(call);
            let approved = approved_library(&self.resolver, callee).is_some()
                || primitive(&mut self.resolver, callee);
            let direct = self.resolver.symbol(callee).is_none_or(|index| {
                let symbol = &self.resolver.symbols[index];
                symbol.constant && !symbol.immutable && !symbol.parameter && !symbol.result
            });
            let fallback = (direct && plans.is_empty()).then(|| self.resolver.candidates(callee));
            let inspectable = |target| {
                self.kind(target) == "Procedure" && self.role(target, "body").is_some()
            };
            let targets = direct && if let Some(candidates) = fallback {
                // Let the helper's checker diagnose argument errors for a
                // direct overload group whose implementations are available.
                // Purity verification still runs before the helper can execute.
                !candidates.is_empty() && candidates.into_iter().all(inspectable)
            } else {
                plans.iter().any(|plan| inspectable(plan.procedure))
            };
            let constructor=self.role(call,"callee").is_some_and(|callee|self.resolver.denotes_type(callee)&&self.resolver.type_of(callee).big_name().is_some());
            if !targets && builtin.is_none() && !approved && !constructor {
                if library_origin(&self.resolver, callee)
                    .is_some_and(|(path, _)| path == "base:intrinsics")
                {
                    return Err(self.fail(node,format!("#run cannot evaluate compiler intrinsic '{}': it is not an eligible pure operation.",self.source(callee).text(callee.node))));
                }
                if library_origin(&self.resolver, callee).is_some() {
                    return Err(self.fail(node,format!("#run cannot verify '{}': no eligible procedure body is available; foreign and unresolved implementations are not eligible.",self.source(callee).text(callee.node))));
                }
                return Err(self.fail(node,"#run requires a resolved direct call; indirect and foreign calls are not eligible."));
            }
            if self.explicit_allocator(call) {
                return Err(self.fail(call,"Compile-time calls cannot select an allocator; use the evaluation scratch allocator."));
            }
            if self.kind(node) == "Run" {
                if let [plan] = plans.as_slice() {
                    let sig = self.source(plan.signature).node(plan.signature.node);
                    let count = self.resolver.fields(plan.signature, "results").len();
                    if count != 1
                        && sig.data["optional_ok"] != true
                        && sig.data["optional_allocator_error"] != true
                    {
                        return Err(self.fail(node,format!("#run requires exactly one supported result value; this procedure returns {count} values.")));
                    }
                }
            }
        }
        if self.kind(node) == "Run" || self.kind(node) == "Comprehension" {
            self.explicit_arguments += 1;
        }
        let args = if self.kind(call) == "Call" {
            self.group(call, "args")
        } else if self.kind(call) == "Binary" {
            vec![
                self.role(call, "left").unwrap(),
                self.role(call, "right").unwrap(),
            ]
        } else {
            self.role(call, "expr").into_iter().collect()
        };
        let ready = (|| {
            for arg in args {
                if !self.known(arg)? {
                    if self.kind(node) == "Run" {
                        return Err(self.fail(arg,"#run arguments and required types must be compile-time known; runtime storage is not a constant."));
                    }
                    if self.kind(node) == "Comprehension" {
                        return Err(self.fail(arg,"Compile-time comprehension inputs and bounds must be compile-time known; runtime storage is not a constant."));
                    }
                    if crate::extremaplan::plan(&self.resolver, call).is_some() && self.constant_context(node) {
                        return Err(self.fail(arg,"Constant min/max expansion inputs must be compile-time known; runtime storage is not a constant."));
                    }
                    return Ok(false);
                }
            }
            Ok(true)
        })();
        if self.kind(node) == "Run" || self.kind(node) == "Comprehension" {
            self.explicit_arguments -= 1;
        }
        if !ready? {
            return Ok(None);
        }
        if self.kind(call) == "Call" {
            self.process_specialization(call)?;
        }
        self.ensure_runs(call)?;
        let value = if let Some(value) = self.integer_power(call)? {
            value
        } else {
            self.execute(call, Mode::Value)?
        };
        let bindings = self.specializations.relevant(node, &self.resolver);
        if bindings.is_empty() {
            self.results.insert(node, value.clone());
        } else {
            self.cases.entry(node).or_default().insert(
                Specializations::fingerprint(&bindings, &self.resolver),
                EvaluatedCase {
                    bindings,
                    value: value.clone(),
                    skipped: None,
                },
            );
        }
        self.resolver.remember_evaluation(node, value.clone());
        if self.kind(node) == "Run" {
            self.save_scalar(node);
        }
        Ok(Some(value))
    }
    pub fn evaluate_all(&mut self) -> Result<()> {
        for (file, source) in self.project.sources.iter().enumerate() {
            if source.shared_native
                || source.excluded
                || self
                    .work_sources
                    .as_ref()
                    .is_some_and(|sources| !sources.contains(&file))
            {
                continue;
            }
            for index in 0..source.nodes.len() {
                let node = Ref {
                    file,
                    node: NodeId(index),
                };
                if (self.kind(node) == "Run" || self.automatic(node))
                    && self.value(node).is_none()
                    && !self.is_skipped(node)
                {
                    self.evaluate(node)?;
                }
                if self.kind(node) == "Call" {
                    self.process_specialization(node)?;
                }
            }
        }
        let skips = self
            .inactive
            .iter()
            .map(|node| (*node, self.unchecked.contains(node)))
            .collect();
        self.project
            .resolve_evaluation_identities(&self.results, &skips, &mut self.cases)
    }
}
