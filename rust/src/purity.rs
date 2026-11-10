//! Verification of the effects reachable from a pure entry point. Ordinary
//! procedure declarations keep their native contract outside a checked path.
use crate::{
    ast::NodeId,
    diagnostic::{Error, Result},
    purediagnostics::{self, function_context, function_owner, procedure_name},
    semantics::{Ref, Resolver, Substitutions, Type},
};
use std::collections::{BTreeMap, BTreeSet, HashSet};
#[path = "pureintrinsics.rs"]
mod pureintrinsics;
pub(crate) use pureintrinsics::llvm_primitive;
pub(crate) fn intrinsic_allowed(name: &str) -> bool {
    Purity::intrinsic_allowed(name)
}

#[derive(Clone, Debug)]
pub struct Fault {
    pub site: Ref,
    pub message: String,
    pub related: Vec<Ref>,
}
#[derive(Default)]
pub struct Facts {
    pub checked: BTreeSet<Ref>,
    pub faults: BTreeMap<Ref, Vec<Fault>>,
    pub gaps: BTreeMap<Ref, Ref>,
    pub targets: BTreeMap<Ref, BTreeSet<Ref>>,
    pub defaults: BTreeSet<Ref>,
    /// Source call paths explain why an ordinary proc receives pure type proofs.
    pub call_paths: BTreeMap<Ref, BTreeSet<(Ref, Vec<Ref>)>>,
    /// Native `when` selection decides whether these body checks are reached.
    pub conditional: BTreeMap<Ref, Fault>,
    pub entry_roots: BTreeSet<Ref>,
}
pub struct Purity<'a, 'r> {
    r: &'r mut Resolver<'a>,
    facts: Facts,
    visiting: HashSet<VisitKey>,
    constants: HashSet<(Ref, Ref)>,
    speculative: Vec<Ref>,
    walk_stack: Vec<Ref>,
}
type VisitKey = (Ref, Vec<(String, Type)>, Option<Ref>);
pub(crate) fn builtin_call(r: &mut Resolver<'_>, call: Ref) -> Option<String> {
    if let Some(callee) = r.sources[call.file].role(call.node, "callee") {
        if let Some(name) = crate::extremaplan::name(
            r,
            Ref {
                file: call.file,
                node: callee,
            },
            &mut HashSet::new(),
        ) {
            return Some(name);
        }
    }
    crate::borroweval::Evaluator {
        resolver: r,
        arena: &mut crate::borrowflow::Arena::default(),
    }
    .builtin(call)
    .filter(|name| {
        BUILTINS.contains(&name.as_str()) || ["#assert", "#config"].contains(&name.as_str())
    })
}
impl<'a, 'r> Purity<'a, 'r> {
    pub fn analyze(r: &'r mut Resolver<'a>) -> Facts {
        let roots = r
            .sources
            .iter()
            .enumerate()
            .flat_map(|(file, source)| {
                source
                    .nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, n)| n.kind == "Procedure" && n.data["pure"] == true)
                    .map(move |(i, _)| Ref {
                        file,
                        node: NodeId(i),
                    })
            })
            .collect::<Vec<_>>();
        Self::analyze_from(r, &roots)
    }
    /// The evaluator verifies its retained entry roots, not unrelated fn
    /// declarations that will be absent from the isolated helper.
    pub fn analyze_from(r: &'r mut Resolver<'a>, roots: &[Ref]) -> Facts {
        Self::analyze_entries(r, roots, &BTreeSet::new())
    }
    pub fn analyze_entries(
        r: &'r mut Resolver<'a>,
        roots: &[Ref],
        entries: &BTreeSet<Ref>,
    ) -> Facts {
        let mut this = Self {
            r,
            facts: Facts {
                entry_roots: entries.clone(),
                ..Facts::default()
            },
            visiting: HashSet::new(),
            constants: HashSet::new(),
            speculative: vec![],
            walk_stack: vec![],
        };
        for root in roots.iter().copied() {
            if this.r.active_definition(root) {
                this.visiting.clear();
                this.visit(root, root, Substitutions::new(), vec![]);
            }
        }
        this.facts
    }
    pub fn analyze_calls(r: &'r mut Resolver<'a>, calls: &[Ref]) -> Facts {
        let mut this = Self {
            r,
            facts: Facts::default(),
            visiting: HashSet::new(),
            constants: HashSet::new(),
            speculative: vec![],
            walk_stack: vec![],
        };
        for call in calls.iter().copied() {
            this.walk(call, call, call, &Substitutions::new(), &[]);
            let source = &this.r.sources[call.file];
            for node in std::iter::once(call.node).chain(source.descendants(call.node)) {
                if let Some(targets) = this.facts.targets.get(&Ref {
                    file: call.file,
                    node,
                }) {
                    this.facts.entry_roots.extend(targets.iter().copied());
                }
            }
        }
        this.facts
    }
    fn role(&self, r: Ref, name: &str) -> Option<Ref> {
        self.r.sources[r.file]
            .role(r.node, name)
            .map(|node| Ref { file: r.file, node })
    }
    fn children(&self, r: Ref) -> Vec<Ref> {
        self.r.sources[r.file]
            .node(r.node)
            .children
            .iter()
            .map(|node| Ref {
                file: r.file,
                node: *node,
            })
            .collect()
    }
    fn text(&self, r: Ref) -> &str {
        self.r.sources[r.file].text(r.node)
    }
    fn kind(&self, r: Ref) -> &str {
        self.r.sources[r.file].node(r.node).kind.as_str()
    }
    fn conditional_reference(&self) -> Option<Ref> {
        let owner = self.speculative.last()?;
        self.walk_stack.iter().rev().copied().find(|site| {
            site.file == owner.file
                && self.r.sources[owner.file]
                    .node(owner.node)
                    .span
                    .contains(self.r.sources[site.file].node(site.node).span)
        })
    }
    fn fault(&mut self, root: Ref, origin: Ref, via: &[Ref], message: impl Into<String>) {
        let message = message.into();
        // Like the reference's checked bodies, leave conditional violations
        // inside their native branch. Relocate transitive failures to the call
        // or constant reference within that branch, never to an eager helper.
        if let Some(owner) = self.speculative.last().copied() {
            let inside = |site: Ref| {
                site.file == owner.file
                    && self.r.sources[owner.file]
                        .node(owner.node)
                        .span
                        .contains(self.r.sources[site.file].node(site.node).span)
            };
            let site = if inside(origin) {
                origin
            } else {
                self.conditional_reference().unwrap_or(owner)
            };
            let mut related = via
                .iter()
                .copied()
                .chain([origin, root])
                .filter(|reference| *reference != site)
                .collect::<Vec<_>>();
            related.sort();
            related.dedup();
            let entry = self.facts.conditional.entry(site).or_insert_with(|| Fault {
                site,
                message: message.clone(),
                related: vec![],
            });
            if !entry.message.lines().any(|line| line == message) {
                entry.message.push('\n');
                entry.message.push_str(&message);
            }
            entry.related.extend(related);
            entry.related.sort();
            entry.related.dedup();
            return;
        }
        let sources = self.r.sources;
        let via = if function_owner(sources, origin).is_some() {
            &[]
        } else if let Some((_, path)) = function_context(sources, via) {
            path
        } else if sources[origin.file].shared_native {
            // An explicit #run of native code still needs a visible call site.
            via.iter().rposition(|site| !sources[site.file].shared_native)
                .map_or(&[][..], |index| &via[index..])
        } else {
            &[]
        };
        let site = via.first().copied().unwrap_or(origin);
        let faults = self.facts.faults.entry(root).or_default();
        if !faults.iter().any(|f| {
            f.site == site
                && f.message == message
                && (site == origin || f.related.last() == Some(&origin))
        }) {
            let related = via
                .iter()
                .skip(1)
                .copied()
                .chain((site != origin).then_some(origin))
                .collect();
            faults.push(Fault {
                site,
                message,
                related,
            });
        }
    }
    fn gap(&mut self, root: Ref, origin: Ref) {
        self.facts.gaps.entry(root).or_insert(origin);
    }
    fn visit(&mut self, root: Ref, proc: Ref, bindings: Substitutions, via: Vec<Ref>) {
        if via.len() <= 40 {
            self.facts
                .call_paths
                .entry(proc)
                .or_default()
                .insert((root, via.clone()));
        }
        let key = (
            proc,
            bindings
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            self.conditional_reference(),
        );
        if !self.visiting.insert(key) {
            return;
        }
        if self.visiting.len() > 1024 || via.len() > 40 {
            self.gap(root, proc);
            return;
        }
        self.facts.checked.insert(proc);
        let Some(sig) = self.role(proc, "type") else {
            self.gap(root, proc);
            return;
        };
        let Some(body) = self.role(proc, "body") else {
            self.fault(root,proc,&via,"Declared pure functions require an inspectable body; foreign or bodyless procedures are not eligible.");
            return;
        };
        let convention = self.r.sources[sig.file].node(sig.node).data["calling_convention"]
            .as_str()
            .unwrap_or("");
        if self.r.sources[proc.file].node(proc.node).data["pure"] == true
            && !["", "odin"].contains(&convention)
        {
            self.fault(
                root,
                proc,
                &via,
                "Declared pure functions require the Odin calling convention.",
            );
        }
        let source = &self.r.sources[proc.file];
        if let Some(decl) = source.ancestors(proc.node).find(|id| source.node(*id).kind != "Paren") {
            for &attribute in source.group(decl, "attributes") {
                let names = source.group(attribute, "elems").iter()
                    .map(|&id| source.text(source.role(id, "field").unwrap_or(id)));
                let (mut lifecycle, mut deferred) = (false, false);
                for name in names {
                    lifecycle |= matches!(name, "init" | "fini");
                    deferred |= name.starts_with("deferred_");
                }
                if lifecycle {
                    self.fault(root, proc, &via, "Package initializers/finalizers are not eligible pure call targets.");
                }
                if deferred {
                    self.fault(root, Ref { node: attribute, ..proc }, &via,
                        "Pure calls cannot use implicit deferred procedure callbacks; make eligible calls explicit in the body.");
                }
            }
        }
        for (name, annotation) in self.r.fields(sig, "params") {
            if let Some(annotation) = annotation {
                if self.r.compiled_type_parameter(annotation, &name) {
                    continue;
                }
                let typ = self.r.specialized_type(annotation, &bindings);
                if self.unsafe_storage(&typ, &mut HashSet::new()) {
                    let procedure = procedure_name(self.r.sources, proc);
                    let cause = self.unsupported_signature_type(&typ);
                    self.fault(root, annotation, &via, format!(
                        "{cause}\nWhile checking parameter '{name}' in pure call '{procedure}'."
                    ));
                }
            }
        }
        for (_, annotation) in self.r.fields(sig, "results") {
            if let Some(annotation) = annotation {
                let typ = self.r.specialized_type(annotation, &bindings);
                if self.unsafe_storage(&typ, &mut HashSet::new()) {
                    let procedure = procedure_name(self.r.sources, proc);
                    let cause = self.unsupported_signature_type(&typ);
                    self.fault(root, annotation, &via, format!(
                        "{cause}\nWhile checking a result in pure call '{procedure}'."
                    ));
                }
            }
        }
        if let Some(params) = self.role(sig, "params") {
            for field in self.children(params) {
                if self.r.sources[field.file].node(field.node).data["using_field"] == true
                    && self
                        .role(field, "type")
                        .is_some_and(|typ| self.r.type_of(typ).is_unknown())
                {
                    self.fault(root,field,&via,"Pure using parameters require a resolved record schema; access members explicitly for unconstrained generic inputs.");
                }
            }
        }
        if !["", "odin"].contains(&convention) {
            for (_, annotation) in self.r.fields(sig, "results") {
                if let Some(annotation) = annotation {
                    let typ = self.r.specialized_type(annotation, &bindings);
                    if Self::owned_sequence(&typ) {
                        self.fault(root,annotation,&via,"Contextless compile-time procedures cannot return strings/slices; use fn or a contextful proc.");
                    }
                }
            }
        }
        self.walk(root, proc, body, &bindings, &via);
        // Defaults are part of a declared fn's contract. Defaults on an
        // ordinary helper are visited only when the caller actually omits it.
        if proc == root {
            if let Some(params) = self.role(sig, "params") {
                for field in self.children(params) {
                    if let Some(default) = self.role(field, "default") {
                        self.walk(root, proc, default, &bindings, &via);
                    }
                }
            }
        }
    }
    fn walk(&mut self, root: Ref, proc: Ref, node: Ref, bindings: &Substitutions, via: &[Ref]) {
        self.walk_stack.push(node);
        self.walk_inner(root, proc, node, bindings, via);
        self.walk_stack.pop();
    }
    fn walk_inner(
        &mut self,
        root: Ref,
        proc: Ref,
        node: Ref,
        bindings: &Substitutions,
        via: &[Ref],
    ) {
        let kind = self.kind(node).to_owned();
        if kind == "Call" && self.package_constant_resource(node) {
            // Odin evaluates these directives while checking the retained
            // declaration. The evaluation cache tracks the resource separately.
            return;
        }
        if kind == "Procedure" {
            return;
        }
        if kind == "Declaration"
            && !self.r.sources[node.file].node(node.node).mutable
            && !self.r.sources[node.file].node(node.node).immutable
        {
            return;
        }
        let source = &self.r.sources[node.file];
        if kind == "When" || kind == "WhenExpr" {
            if let Some(cond) = self.role(node, "cond") {
                match self.r.context_condition(cond, bindings) {
                    Some(value) => {
                        let target = self
                            .role(node, if value { "body" } else { "else" })
                            .or_else(|| self.role(node, if value { "x" } else { "y" }));
                        if let Some(target) = target {
                            self.walk(root, proc, target, bindings, via);
                        }
                        return;
                    }
                    None => {
                        self.walk(root, proc, cond, bindings, via);
                        self.speculative.push(proc);
                        for role in ["body", "else", "x", "y"] {
                            if let Some(branch) = self.role(node, role) {
                                self.walk(root, proc, branch, bindings, via);
                            }
                        }
                        self.speculative.pop();
                        return;
                    }
                }
            }
        }
        match kind.as_str() {
            "Using" => {
                for value in source.group(node.node,"values").to_vec() {
                    let value=Ref{file:node.file,node:value};
                    let typ=self.r.specialized_type(value,bindings);
                    if typ.is_unknown() {
                        self.fault(root,node,via,"Pure using requires a resolved record schema; unverified namespaces and unconstrained generic projections are not eligible.");
                    }
                }
            }
            "Return" if source.group(node.node,"values").is_empty()=> {
                if let Some(signature)=self.role(proc,"type") {
                    if self.r.fields(signature,"results").iter().any(|(name,_)|name.is_empty()||name=="_") {
                        self.fault(root,node,via,"Pure procedure results are unnamed; return values explicitly.");
                    }
                }
            }
            "Implicit" => self.fault(root,node,via,"Pure code cannot access the ambient context or implicit runtime state."),
            "Unsafe" => self.fault(root,node,via,"Assembly, uninitialized storage and unsafe operations are not permitted in pure code."),
            "Directive" => {
                let name=source.names.get(source.node(node.node).name);
                if ["file","line","directory","caller_location","defined"].contains(&name) {
                    self.fault(root,node,via,"Source-location-dependent constants are not eligible for pure execution yet; pass ordinary data explicitly.");
                } else if !["assert","config","type","procedure"].contains(&name) {
                    self.fault(root,node,via,format!("Directive '#{name}' is not permitted in pure code."));
                }
            }
            "Ident" | "Selector" => {
                if kind=="Selector"&&self.role(node,"field").is_some_and(|field|matches!(self.text(field),"_value"|"_numerator"|"_denominator")) {
                    if let Some(base)=self.role(node,"expr"){
                        let mut typ=self.r.specialized_type(base,bindings);while let Type::Pointer(inner)=typ{typ = *inner;}
                        if typ.big_name().is_some(){self.fault(root,node,via,"Pure code cannot inspect Integer/Rational implementation storage; use numeric operators and public properties.");}
                    }
                }
                if self.reference(node) {
                    if self.text(node)=="ODIN_BUILD_PROJECT_NAME" && self.r.symbol(node).is_none() {
                        self.fault(root,node,via,"Source-location-dependent constants are not eligible for pure execution yet; pass ordinary data explicitly.");
                    }
                    if let Some(symbol)=self.r.symbol(node) {
                        let symbol=self.r.symbols[symbol].clone();
                        if symbol.constant {
                            if let Some(value)=symbol.value {
                                let value=Ref{file:symbol.reference.file,node:value};
                                if !matches!(self.kind(value),"Procedure"|"ProcedureGroup") && self.constants.insert((root,value)) {
                                    self.walk(root,proc,value,bindings,via);
                                }
                            }
                        }
                        let symbol=&symbol;
                        let owner=&self.r.sources[symbol.reference.file];
                        let local=symbol.reference.file==proc.file && self.r.sources[proc.file].node(proc.node).span.contains(owner.node(symbol.reference.node).span);
                        if !local && (owner.node(symbol.reference.node).mutable || symbol.immutable) {
                            let message = format!("Pure code cannot read or write runtime storage '{}' outside the function.",symbol.name);
                            let ordinary_body = !source.shared_native
                                && !self.r.sources[proc.file].shared_native
                                && function_owner(self.r.sources, node).is_none()
                                && purediagnostics::procedure_owner(self.r.sources, node) == Some(proc);
                            if self.speculative.is_empty() && ordinary_body
                                && function_context(self.r.sources, via).is_some()
                            {
                                // Keep checking independent source expressions in the
                                // ordinary body. Only the checked copy rejects this use.
                                self.facts.conditional.entry(node).or_insert(Fault {
                                    site: node, message, related: vec![],
                                });
                            } else {
                                self.fault(root, node, via, message);
                            }
                        }
                    }
                }
            }
            "Interpolation" => {
                // Concrete invalid values can be reported before lowering;
                // generic values are checked by validate_pure_value in the
                // selected native formatting helper.
                for id in self.r.sources[node.file].group(node.node,"args").to_vec() {
                    let arg=Ref{file:node.file,node:id};
                    let typ=self.r.specialized_type(arg,bindings);
                    if !typ.is_unknown() && !matches!(typ,Type::Scalar{..}|Type::String{..}|Type::Enum(_)|Type::BigInteger|Type::BigRational) {
                        self.fault(root,arg,via,"Pure formatting supports deterministic scalar/string values only; custom record formatters are not eligible.");
                    }
                }
            }
            "Call" => { self.call(root,proc,node,bindings,via); return; }
            "Declaration" | "Compound" | "Comprehension" => {
                let types=if let Some(annotation)=self.role(node,"type") {vec![self.r.specialized_type(annotation,bindings)]}
                    else if kind=="Declaration" {self.r.sources[node.file].group(node.node,"values").iter().map(|value|self.r.specialized_type(Ref{file:node.file,node:*value},bindings)).collect()}
                    else {vec![self.r.specialized_type(node,bindings)]};
                if types.iter().any(|typ|self.unsafe_storage(typ,&mut HashSet::new())) {
                    self.fault(root,node,via,"Pure local storage cannot contain raw pointers, any, procedure values, maps, sets or raw unions.");
                }
                if source.node(node.node).data["using_field"]==true
                    && source.group(node.node,"names").iter().any(|name| self.r.specialized_type(Ref{file:node.file,node:*name},bindings).is_unknown()) {
                    self.fault(root,node,via,"Pure using requires a resolved record schema; unverified namespaces and unconstrained generic projections are not eligible.");
                }
            }
            "Cast" => {
                let target=self.role(node,"type").map(|r|if self.kind(r)=="TypeidType" {Type::scalar("typeid")}else{self.r.specialized_type(r,bindings)}).unwrap_or(Type::Unknown);
                if source.node(node.node).op=="transmute" {
                    let value=self.role(node,"expr").map(|r|self.r.specialized_type(r,bindings)).unwrap_or(Type::Unknown);
                    if Self::scalar_transmute(&target)==Some(false) || Self::scalar_transmute(&value)==Some(false) {
                        self.fault(root,node,via,"Pure transmute requires scalar numeric, boolean, enum or bit-set values; pointer and aggregate representations are not eligible.");
                    }
                } else if self.role(node,"expr").is_some_and(|value| {
                    let typ=self.r.specialized_type(value,bindings);self.pointers(&typ,&mut HashSet::new())
                }) {
                    self.fault(root,node,via,"Pure code cannot observe pointer identities or convert pointers to numeric values.");
                } else if Self::forbidden_conversion(&target) {
                    self.fault(root,node,via,"Pure code cannot convert values to pointers, procedure values, any or runtime type identities.");
                }
            }
            "Switch" | "SwitchExpr" => {
                let selector=self.role(node,"selector").or_else(||self.children(node).into_iter().rev().find(|r|!matches!(self.kind(*r),"Block"|"Declaration")));
                if let Some(selector)=selector {
                    let typ=self.r.specialized_type(selector,bindings);
                    if self.pointers(&typ,&mut HashSet::new()) {
                        let cases=self.children(node).into_iter().filter(|r|self.kind(*r)=="Block").flat_map(|r|self.children(r)).filter(|r|self.kind(*r)=="Case").collect::<Vec<_>>();
                        let observes=cases.iter().any(|case|self.r.sources[case.file].group(case.node,"types").iter().any(|m|self.r.sources[case.file].text(*m)!="nil"));
                        if observes || kind=="SwitchExpr" {self.fault(root,selector,via,"Pure code cannot observe pointer identities in switch selection.");}
                    }
                }
            }
            "Binary" => {
                if ["==","!=","<",">","<=",">=","in","not_in"].contains(&source.node(node.node).op.as_str()) {
                    let left=self.role(node,"left");let right=self.role(node,"right");
                    let null=left.into_iter().chain(right).any(|r|self.text(r)=="nil");
                    if !null && !left.into_iter().chain(right).any(|value|self.r.denotes_type(value)) {
                        for value in left.into_iter().chain(right) {
                            let typ=self.r.specialized_type(value,bindings);
                            if self.pointers(&typ,&mut HashSet::new()) {
                                self.fault(root,node,via,"Pure code cannot observe pointer identities or convert pointers to numeric values.");
                            }
                        }
                    }
                }
            }
            _=>{}
        }
        for child in self.children(node) {
            self.walk(root, proc, child, bindings, via);
        }
    }
    fn package_constant_resource(&self, site: Ref) -> bool {
        let Some(callee) = self.role(site, "callee") else {
            return false;
        };
        if !matches!(self.text(callee), "#load" | "#load_hash" | "#exists") {
            return false;
        }
        let source = &self.r.sources[site.file];
        source.ancestors(site.node)
            .take_while(|id| source.node(*id).kind != "Procedure")
            .find(|id| source.node(*id).kind == "Declaration")
            .is_some_and(|id| {
                let declaration = source.node(id);
                !declaration.mutable && !declaration.immutable
                    && crate::semantics::scope(source, id) == NodeId(0)
            })
    }
    fn reference(&self, r: Ref) -> bool {
        let s = &self.r.sources[r.file];
        let Some(parent) = s.node(r.node).parent else {
            return true;
        };
        !(["Declaration", "Field", "Range"].contains(&s.node(parent).kind.as_str())
            && s.group(parent, "names").contains(&r.node)
            || ["Selector", "FieldValue"].contains(&s.node(parent).kind.as_str())
                && s.role(parent, "field") == Some(r.node))
    }
    fn io_call(&self,callee:Ref)->bool {
        crate::evaluation::library_member(self.r,callee,"core:fmt").is_some_and(|name|IO.contains(&name.as_str()))
            ||crate::evaluation::library_member(self.r,callee,"thor:basic").is_some_and(|name|["print","input","read","write"].contains(&name.as_str()))
    }
    fn io_message(&self,proc:Ref,callee:Ref)->String {
        let source=&self.r.sources[proc.file];
        let name=source.node(proc.node).parent.and_then(|declaration|source.group(declaration,"names").first()).map(|name|source.text(*name));
        let subject=if source.node(proc.node).data["pure"]==true {name.map(|name|format!("Pure function '{name}'")).unwrap_or("Pure code".into())}else{"Pure code".into()};
        format!("{subject} cannot call '{}' because it performs I/O. Use proc for I/O and keep it outside fn/#run call chains.",self.text(callee))
    }
    fn call(&mut self, root: Ref, proc: Ref, call: Ref, bindings: &Substitutions, via: &[Ref]) {
        let Some(callee) = self.role(call, "callee") else {
            self.gap(root, call);
            return;
        };
        if let Some(message) = self.r.private_call_reason(callee) {
            self.fault(root, callee, via, message);
            return;
        }
        if self.io_call(callee) {
            self.fault(root,call,via,self.io_message(proc,callee));
            return;
        }
        let args = self.r.sources[call.file]
            .group(call.node, "args")
            .iter()
            .map(|node| Ref {
                file: call.file,
                node: *node,
            })
            .collect::<Vec<_>>();
        let builtin = builtin_call(self.r, call);
        if self.kind(callee) == "Directive"
            && ["assert", "config"].contains(
                &self.r.sources[callee.file]
                    .names
                    .get(self.r.sources[callee.file].node(callee.node).name),
            )
        {
            for arg in args {
                self.walk(root, proc, arg, bindings, via);
            }
            return;
        }
        if let Some(name) = self.intrinsic_name(callee, &mut HashSet::new()) {
            if !Self::intrinsic_allowed(&name) {
                self.fault(
                    root,
                    callee,
                    via,
                    format!("Pure code cannot call compiler intrinsic '{}': it is not an eligible pure operation.",self.text(callee)),
                );
            }
            for arg in args {
                self.walk(root, proc, arg, bindings, via);
            }
            return;
        }
        let target_type = self.r.specialized_type(callee, bindings);
        if self.r.denotes_type(callee) {
            if let Some(arg) = args.first() {
                let typ = self.r.specialized_type(*arg, bindings);
                if self.pointers(&typ, &mut HashSet::new()) && target_type.numeric() {
                    self.fault(root,call,via,"Pure code cannot observe pointer identities or convert pointers to numeric values.");
                }
            }
            if Self::forbidden_conversion(&target_type) {
                self.fault(root,call,via,"Pure code cannot convert values to pointers, procedure values, any or runtime type identities.");
            }
        } else if let Some(name) = builtin.as_deref() {
            if ["type_of", "size_of", "align_of", "offset_of"].contains(&name) {
                return;
            }
            let explicit_allocator = args.iter().any(|&arg| {
                self.kind(arg) == "FieldValue"
                    && self.role(arg, "field").is_some_and(|field| self.text(field) == "allocator")
            });
            if name == "delete" {
                let mut positional = args.iter().copied().filter(|&arg| self.kind(arg) != "FieldValue");
                let target = args.iter().copied().find_map(|arg| {
                    let field = self.role(arg, "field")?;
                    matches!(self.text(field), "array" | "str" | "m")
                        .then(|| self.role(arg, "value")).flatten()
                }).or_else(|| positional.next());
                let allocator = explicit_allocator || positional.next().is_some() && target.is_some_and(|target| {
                    !matches!(self.r.specialized_type(target, bindings), Type::Dynamic(_) | Type::Map { .. })
                });
                if allocator {
                    self.fault(root, call, via, "Pure code cannot select an allocator.");
                }
            }
            if name == "make" {
                let typ = args
                    .first()
                    .map(|r| self.r.specialized_type(*r, bindings))
                    .unwrap_or(Type::Unknown);
                if !matches!(typ, Type::Slice(_) | Type::Dynamic(_)) {
                    self.fault(
                        root,
                        call,
                        via,
                        "Pure allocation supports fresh slice/dynamic buffers only.",
                    );
                }
                let limit = if matches!(typ, Type::Dynamic(_)) {
                    3
                } else {
                    2
                };
                if args.len() > limit || explicit_allocator {
                    self.fault(
                        root,
                        call,
                        via,
                        "Pure code cannot select an allocator or invoke allocator callbacks.",
                    );
                }
            }
        } else if crate::evaluation::approved_library(self.r, callee).is_some() {
            if crate::evaluation::explicit_allocator(self.r, call) {
                self.fault(root, call, via, "Pure code cannot select an allocator.");
            }
        } else {
            let plans = self.r.call_plans(call, bindings);
            let indirect = self.r.symbol(callee).is_some_and(|i| {
                let sym = &self.r.symbols[i];
                sym.parameter || sym.result || !sym.constant
            });
            if indirect {
                self.fault(
                    root,
                    call,
                    via,
                    format!("Pure code requires a resolved direct call to an eligible procedure; An indirect call through runtime procedure value '{}' is not eligible.",self.text(callee)),
                );
            } else if plans.is_empty() {
                let source = &self.r.sources[callee.file];
                let missing_name = self.kind(callee) == "Ident"
                    && self.r.symbol(callee).is_none()
                    && source.selections.is_empty()
                    && !source
                        .nodes
                        .iter()
                        .any(|node| node.kind == "Using" || node.data["using_field"] == true);
                if !missing_name && !self.native_argument_error(call, callee, bindings) {
                    self.gap(root, call);
                }
            } else {
                let mut selected = 0;
                let native_selection = plans.len() > 1;
                for plan in plans {
                    let candidate = plan.procedure;
                    if self.llvm_primitive(candidate) {
                        selected += 1;
                        continue;
                    }
                    if self.kind(candidate) != "Procedure" || self.role(candidate, "body").is_none()
                    {
                        self.fault(
                            root,
                            callee,
                            via,
                            "Pure code cannot call foreign or bodyless procedures.",
                        );
                        continue;
                    }
                    let owner = &self.r.sources[candidate.file];
                    let mut declaration = owner.node(candidate.node).parent;
                    while declaration.is_some_and(|id| owner.node(id).kind == "Paren") {
                        declaration = declaration.and_then(|id| owner.node(id).parent);
                    }
                    if !declaration.is_some_and(|id| owner.node(id).kind == "Declaration") {
                        self.fault(root,call,via,"Pure code requires a resolved direct call to an eligible procedure; indirect, foreign and unknown calls are forbidden.");
                        selected += 1;
                        continue;
                    }
                    let owner = &self.r.sources[candidate.file];
                    let declaration = owner.node(candidate.node).parent;
                    let name = declaration
                        .and_then(|decl| owner.group(decl, "names").first())
                        .map(|name| owner.text(*name))
                        .unwrap_or("");
                    let odin = owner.info["odin_root"].as_str().map(std::path::Path::new);
                    let native_fmt =
                        odin.is_some_and(|odin| owner.path.starts_with(odin.join("core/fmt")));
                    let basic = owner.path.parent()
                        == Some(
                            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                                .parent()
                                .unwrap()
                                .join("runtime/basic")
                                .as_path(),
                        );
                    if native_fmt && IO.contains(&name)
                        || basic && ["print", "input", "read", "write"].contains(&name)
                    {
                        if native_selection {
                            let body=self.role(candidate,"body").unwrap();
                            self.facts.checked.insert(candidate);
                            self.facts.targets.entry(call).or_default().insert(candidate);
                            self.speculative.push(body);
                            let mut chain=via.to_vec();chain.push(call);
                            self.fault(root,body,&chain,self.io_message(proc,callee));
                            self.speculative.pop();
                        } else {
                            self.fault(root,call,via,self.io_message(proc,callee));
                        }
                        selected += 1;
                        continue;
                    }
                    let sig = plan.signature;
                    let contextual = plan.bindings;
                    self.facts
                        .targets
                        .entry(call)
                        .or_default()
                        .insert(candidate);
                    selected += 1;
                    let mut chain = via.to_vec();
                    chain.push(call);
                    if native_selection {
                        self.speculative.push(self.role(candidate, "body").unwrap());
                    }
                    self.visit(root, candidate, contextual, chain.clone());
                    if let Some(params) = self.role(sig, "params") {
                        let mut position = 0;
                        for field in self.children(params) {
                            let names = self.r.sources[field.file]
                                .group(field.node, "names")
                                .to_vec();
                            let omitted = names.iter().any(|name| {
                                let name = self.r.sources[field.file].text(*name);
                                let explicit = args.iter().any(|arg| {
                                    self.kind(*arg) == "FieldValue"
                                        && self
                                            .role(*arg, "field")
                                            .is_some_and(|r| self.text(r) == name)
                                });
                                let missing = !explicit && position >= args.len();
                                position += 1;
                                missing
                            });
                            if omitted {
                                self.facts.defaults.insert(field);
                                if let Some(default) = self.role(field, "default") {
                                    self.walk(root, candidate, default, bindings, &chain);
                                }
                            }
                        }
                    }
                    if native_selection {
                        self.speculative.pop();
                    }
                }
                if selected == 0 {
                    self.gap(root, call);
                }
            }
        }
        for arg in args {
            self.walk(root, proc, arg, bindings, via);
        }
    }
    /// Leave definite source argument errors to Odin's original call checker.
    /// Unknown generic shapes/spreads still require an inspectable checked plan.
    fn native_argument_error(&mut self, call: Ref, callee: Ref, bindings: &Substitutions) -> bool {
        let candidates = self.r.candidates(callee);
        if candidates.is_empty() {
            return false;
        }
        let args = self.r.sources[call.file]
            .group(call.node, "args")
            .iter()
            .map(|node| Ref {
                file: call.file,
                node: *node,
            })
            .collect::<Vec<_>>();
        if args.iter().any(|arg| {
            self.kind(*arg) == "Unary" && self.r.sources[arg.file].node(arg.node).op == "**"
        }) || self.kind(callee) == "Selector"
            && self.r.sources[callee.file].node(callee.node).op == "->"
        {
            return false;
        }
        candidates
            .into_iter()
            .all(|target| self.native_target_argument_error(target, &args, bindings))
    }
    fn native_target_argument_error(
        &mut self,
        target: Ref,
        args: &[Ref],
        bindings: &Substitutions,
    ) -> bool {
        let Some(signature) = self.role(target, "type") else {
            return false;
        };
        let Some(groups) = self.r.arrange_arguments(signature, args) else {
            return true;
        };
        for ((_, annotation), group) in self.r.fields(signature, "params").into_iter().zip(groups) {
            let Some(annotation) = annotation else {
                continue;
            };
            let expected = self.r.specialized_type(annotation, bindings);
            for arg in group {
                if self.kind(arg) == "AutoCast" {
                    continue;
                }
                let actual = self.r.specialized_type(arg, bindings);
                if matches!(expected, Type::Scalar { .. } | Type::String { .. })
                    && matches!(actual, Type::Scalar { .. } | Type::String { .. })
                    && self.r.shape_score(annotation, &actual, bindings).is_none()
                {
                    return true;
                }
            }
        }
        false
    }
    fn forbidden_conversion(typ: &Type) -> bool {
        match typ {
            Type::Nominal(_, base) => Self::forbidden_conversion(base),
            Type::Pointer(_) | Type::Procedure(_) | Type::Map { .. } => true,
            Type::Scalar { name, .. } => {
                ["rawptr", "any", "typeid", "cstring"].contains(&name.as_str())
            }
            Type::String { name, .. } => name != "string",
            _ => false,
        }
    }
    pub(crate) fn scalar_transmute(typ: &Type) -> Option<bool> {
        match typ {
            Type::Nominal(_, base) => Self::scalar_transmute(base),
            Type::Scalar { name, .. } => {
                Some(!["rawptr", "any", "typeid", "cstring", "cstring16"].contains(&name.as_str()))
            }
            Type::Enum(_) => Some(true),
            Type::Unknown | Type::Generic => None,
            _ => Some(false),
        }
    }
    fn owned_sequence(typ: &Type) -> bool {
        match typ {
            Type::String { .. } | Type::Slice(_) | Type::Dynamic(_) => true,
            Type::Nominal(_, base)
            | Type::Array { elem: base, .. }
            | Type::Fixed { elem: base, .. } => Self::owned_sequence(base),
            _ => false,
        }
    }
    fn raw_union(&self, typ: &Type) -> bool {
        match typ {
            Type::Nominal(_, base) => self.raw_union(base),
            Type::Struct(owner) | Type::Record(owner, _) => {
                self.r.sources[owner.file].node(owner.node).op == "raw_union"
            }
            _ => false,
        }
    }
    fn unsupported_signature_type(&self, typ: &Type) -> &'static str {
        if self.raw_union(typ) {
            "Raw-union storage cannot be proved pure."
        } else {
            "Unsupported pure value type: raw pointers, typeid, any, maps, sets and procedure values are not pure inputs/results."
        }
    }
    fn unsafe_storage(&mut self, t: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(t.clone()) {
            return false;
        }
        match t {
            Type::Nominal(_, base)
            | Type::Slice(base)
            | Type::Dynamic(base)
            | Type::Pointer(base)
            | Type::Array { elem: base, .. }
            | Type::Fixed { elem: base, .. } => self.unsafe_storage(base, seen),
            Type::String { name, .. } => name != "string",
            Type::Scalar { name, .. } => {
                ["rawptr", "any", "typeid", "cstring", "cstring16"].contains(&name.as_str())
            }
            Type::Map { .. } | Type::Set(_) | Type::Procedure(_) => true,
            Type::Struct(_) | Type::Record(..) if self.raw_union(t) => true,
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(t)
                .iter()
                .any(|(_, t)| self.unsafe_storage(t, seen)),
            Type::Union(owner) => self
                .r
                .union_variants(*owner)
                .iter()
                .any(|typ| self.unsafe_storage(typ, seen)),
            _ => false,
        }
    }
    fn pointers(&mut self, t: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(t.clone()) {
            return false;
        }
        match t {
            Type::Pointer(_) => true,
            Type::Scalar { name, .. } => {
                ["rawptr", "any", "cstring", "typeid"].contains(&name.as_str())
            }
            Type::Nominal(_, base)
            | Type::Array { elem: base, .. }
            | Type::Fixed { elem: base, .. } => self.pointers(base, seen),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(t)
                .iter()
                .any(|(_, t)| self.pointers(t, seen)),
            Type::Union(owner) => self
                .r
                .union_variants(*owner)
                .iter()
                .any(|typ| self.pointers(typ, seen)),
            _ => false,
        }
    }
}
pub(crate) const IO: &[&str] = &[
    "print",
    "println",
    "printf",
    "printfln",
    "eprint",
    "eprintln",
    "eprintf",
    "eprintfln",
    "fprint",
    "fprintln",
    "fprintf",
    "fprintfln",
    "wprint",
    "wprintln",
    "wprintf",
    "wprintfln",
    "fprint_type", "fprint_typeid", "wprint_type", "wprint_typeid",
];
pub(crate) const BUILTINS: &[&str] = &[
    "len",
    "cap",
    "size_of",
    "align_of",
    "offset_of",
    "type_of",
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
    "make",
    "copy",
    "delete",
    "assert",
    "assert_contextless",
    "panic",
    "append",
    "clear",
    "pop",
    "pop_safe",
    "pop_front",
    "pop_front_safe",
];

impl Facts {
    pub fn validate(&self, r: &Resolver<'_>, root: Ref) -> Result<()> {
        if let Some(faults) = self.faults.get(&root).filter(|v| !v.is_empty()) {
            let mut error = Error::new(&faults[0].message);
            for fault in faults {
                let source = &r.sources[fault.site.file];
                let io=fault.message.contains("because it performs I/O.");
                let site=if io && fault.related.is_empty() {source.role(fault.site.node,"callee").unwrap_or(fault.site.node)}else{fault.site.node};
                let mut diagnostic =
                    Error::at(source, purediagnostics::diagnostic_span(source, site), &fault.message)
                        .diagnostics
                        .remove(0);
                diagnostic["msgs"] = serde_json::json!(fault.message.lines().collect::<Vec<_>>());
                if io {diagnostic["stage"]=serde_json::json!("checker");}
                if fault.message.contains("An indirect call through runtime procedure value") {
                    for call in std::iter::once(fault.site).chain(fault.related.iter().copied()) {
                        let source=&r.sources[call.file];
                        let Some(callee)=source.role(call.node,"callee") else {continue;};
                        let Some(symbol)=r.symbol(Ref{file:call.file,node:callee}).map(|index|&r.symbols[index]) else {continue;};
                        if !symbol.parameter {continue;}
                        let owner=&r.sources[symbol.reference.file];
                        let name=owner.node(symbol.scope).parent.and_then(|decl|owner.group(decl,"names").first()).map(|name|owner.text(*name)).unwrap_or("anonymous fn");
                        diagnostic["msgs"].as_array_mut().unwrap().push(serde_json::json!(format!("While checking parameter '{}' in pure call '{name}'.",symbol.name)));
                        break;
                    }
                }
                if let Some((&origin, calls)) = fault.related.split_last() {
                    let mut path = vec![fault.site];
                    path.extend_from_slice(calls);
                    if let Some(context) = function_context(r.sources, &path) {
                        purediagnostics::contextualize(r.sources, &mut diagnostic, origin, &[context]);
                    } else {
                        let message = if r.sources[origin.file].shared_native {
                            "Purity violation in standard-library implementation."
                        } else {
                            "Underlying purity violation in this checked implementation."
                        };
                        diagnostic["related"] = serde_json::json!([purediagnostics::note(r.sources, origin, message)]);
                    }
                }
                error.diagnostics.push(diagnostic);
            }
            return Err(error);
        }
        if let Some(gap) = self.gaps.get(&root) {
            let source = &r.sources[gap.file];
            return Err(Error::unsupported(
                source,
                source.node(gap.node).span,
                "unresolved pure call specialization",
            ));
        }
        Ok(())
    }
}
