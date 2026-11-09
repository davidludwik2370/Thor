//! Verification of the effects reachable from a pure entry point. Ordinary
//! procedure declarations keep their native contract outside a checked path.
use crate::{
    ast::NodeId,
    diagnostic::{Error, Result},
    semantics::{Ref, Resolver, Substitutions, Type},
};
use std::collections::{BTreeMap, BTreeSet, HashSet};
#[path = "pureintrinsics.rs"]
mod pureintrinsics;

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
}
pub struct Purity<'a, 'r> {
    r: &'r mut Resolver<'a>,
    facts: Facts,
    visiting: HashSet<(Ref, Vec<(String, Type)>)>,
    constants: HashSet<(Ref, Ref)>,
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
        let mut this = Self {
            r,
            facts: Facts::default(),
            visiting: HashSet::new(),
            constants: HashSet::new(),
        };
        for root in roots {
            if this.r.active_definition(root) {
                this.visiting.clear();
                this.visit(root, root, Substitutions::new(), vec![]);
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
    fn fault(&mut self, root: Ref, origin: Ref, via: &[Ref], message: impl Into<String>) {
        let site = via.first().copied().unwrap_or(origin);
        let faults = self.facts.faults.entry(root).or_default();
        let message = message.into();
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
        let key = (
            proc,
            bindings
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
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
        if let Some(decl) = self.r.sources[proc.file].node(proc.node).parent {
            for (name, value) in self.r.attributes(Ref {
                file: proc.file,
                node: decl,
            }) {
                if name.starts_with("deferred_") || ["init", "fini"].contains(&name.as_str()) {
                    self.fault(root,value.unwrap_or(Ref{file:proc.file,node:decl}),&via,"Pure calls cannot use package initialization/finalization or implicit deferred callbacks.");
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
                    self.fault(root,annotation,&via,"Unsupported pure value type: raw pointers, typeid, any, maps, sets and procedure values are not pure inputs/results.");
                }
                if proc != root && self.unresolved_schema(&typ, &mut HashSet::new()) {
                    self.gap(root, annotation);
                }
            }
        }
        for (_, annotation) in self.r.fields(sig, "results") {
            if let Some(annotation) = annotation {
                let typ = self.r.specialized_type(annotation, &bindings);
                if self.unsafe_storage(&typ, &mut HashSet::new()) {
                    self.fault(root,annotation,&via,"Unsupported pure value type: raw pointers, typeid, any, maps, sets, raw unions and procedure values are not pure inputs/results.");
                }
                if proc != root && self.unresolved_schema(&typ, &mut HashSet::new()) {
                    self.gap(root, annotation);
                }
            }
        }
        if let Some(params) = self.role(sig, "params") {
            for field in self.children(params) {
                if self.r.sources[field.file].node(field.node).data["using_field"] == true {
                    self.gap(root, field);
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
        let kind = self.kind(node).to_owned();
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
                        self.gap(root, cond);
                        return;
                    }
                }
            }
        }
        match kind.as_str() {
            "Using" => {
                for value in source.group(node.node,"values").to_vec() {
                    let value=Ref{file:node.file,node:value};
                    let mut typ=self.r.specialized_type(value,bindings);
                    while let Type::Pointer(elem)=typ {typ = *elem;}
                    if matches!(typ,Type::Struct(_)|Type::Record(..)|Type::Anonymous(_)) {self.gap(root,value);}
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
                            self.fault(root,node,via,format!("Pure code cannot read or write runtime storage '{}' outside the function.",symbol.name));
                        }
                    }
                }
            }
            "Interpolation" => {
                let args=self.r.sources[node.file].group(node.node,"args").to_vec();
                for arg in args {
                    let arg=Ref{file:node.file,node:arg};let typ=self.r.specialized_type(arg,bindings);
                    if typ.is_unknown() { self.gap(root,arg); }
                    else if !matches!(typ,Type::Scalar{..}|Type::String{..}|Type::Enum(_)) {self.fault(root,arg,via,"Pure formatting supports deterministic scalar/string values only; custom record formatters are not eligible.");}
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
                if source.node(node.node).data["using_field"]==true {self.gap(root,node);}
            }
            "Cast" => {
                let target=self.role(node,"type").map(|r|if self.kind(r)=="TypeidType" {Type::scalar("typeid")}else{self.r.specialized_type(r,bindings)}).unwrap_or(Type::Unknown);
                if source.node(node.node).op=="transmute" {
                    let value=self.role(node,"expr").map(|r|self.r.specialized_type(r,bindings)).unwrap_or(Type::Unknown);
                    if target.is_unknown() || value.is_unknown() {self.gap(root,node);}
                    else if !Self::scalar_transmute(&target) || !Self::scalar_transmute(&value) {
                        self.fault(root,node,via,"Pure transmute requires scalar numeric, boolean, enum or bit-set values; pointer and aggregate representations are not eligible.");
                    }
                } else if matches!(&target,Type::Pointer(_)|Type::Procedure(_)) || matches!(&target,Type::Scalar{name,..}if ["rawptr","any","typeid"].contains(&name.as_str())) || matches!(&target,Type::String{name,..}if name!="string") {
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
                    if !null {
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
    fn call(&mut self, root: Ref, proc: Ref, call: Ref, bindings: &Substitutions, via: &[Ref]) {
        let Some(callee) = self.role(call, "callee") else {
            self.gap(root, call);
            return;
        };
        if let Some(message) = self.r.private_call_reason(callee) {
            self.fault(root, callee, via, message);
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
            if matches!(
                target_type,
                Type::Pointer(_) | Type::Procedure(_) | Type::Map { .. }
            ) || matches!(&target_type,Type::Scalar{name,..} if ["any","rawptr","typeid","cstring"].contains(&name.as_str()))
                || matches!(&target_type,Type::String{name,..} if name!="string")
            {
                self.fault(root,call,via,"Pure code cannot convert values to pointers, procedure values, any or runtime type identities.");
            }
        } else if let Some(name) = BUILTINS
            .iter()
            .find(|name| self.r.native_builtin(callee, name))
        {
            if ["type_of", "size_of", "align_of", "offset_of"].contains(name) {
                return;
            }
            if *name == "delete" && args.len() > 1 {
                self.fault(root, call, via, "Pure code cannot select an allocator.");
            }
            if *name == "make" {
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
                if args.len() > limit {
                    self.fault(
                        root,
                        call,
                        via,
                        "Pure code cannot select an allocator or invoke allocator callbacks.",
                    );
                }
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
                    "Pure code cannot invoke an indirect procedure value.",
                );
            } else if plans.is_empty() {
                self.gap(root, call);
            } else {
                let mut selected = 0;
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
                        self.fault(root,callee,via,"Pure code cannot call this library procedure because it performs I/O. Use proc for I/O and keep it outside fn/#run call chains.");
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
                                if let Some(default) = self.role(field, "default") {
                                    self.walk(root, candidate, default, bindings, &chain);
                                }
                            }
                        }
                    }
                }
                if selected != 1 {
                    self.gap(root, call);
                }
            }
        }
        for arg in args {
            self.walk(root, proc, arg, bindings, via);
        }
    }
    fn scalar_transmute(typ: &Type) -> bool {
        match typ {
            Type::Nominal(_, base) => Self::scalar_transmute(base),
            Type::Scalar { name, .. } => {
                !["rawptr", "any", "typeid", "cstring", "cstring16"].contains(&name.as_str())
            }
            Type::Enum(_) => true,
            _ => false,
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
    fn unresolved_schema(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(typ.clone()) {
            return false;
        }
        match typ {
            Type::Unknown | Type::Generic | Type::Soa(_) => true,
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .r
                .record_fields(typ)
                .iter()
                .any(|(_, typ)| self.unresolved_schema(typ, seen)),
            Type::Union(owner) => self
                .r
                .union_variants(*owner)
                .iter()
                .any(|typ| self.unresolved_schema(typ, seen)),
            Type::Nominal(_, base)
            | Type::Pointer(base)
            | Type::Slice(base)
            | Type::Dynamic(base)
            | Type::Array { elem: base, .. }
            | Type::Fixed { elem: base, .. } => self.unresolved_schema(base, seen),
            _ => false,
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
            Type::Struct(owner) | Type::Record(owner, _)
                if self.r.sources[owner.file].node(owner.node).op == "raw_union" =>
            {
                true
            }
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
const IO: &[&str] = &[
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
];
const BUILTINS: &[&str] = &[
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
                let mut diagnostic =
                    Error::at(source, source.node(fault.site.node).span, &fault.message)
                        .diagnostics
                        .remove(0);
                if !fault.related.is_empty() {
                    diagnostic["related"]=serde_json::json!(fault.related.iter().map(|reference|{
                        let s=&r.sources[reference.file];let span=s.node(reference.node).span;let (line,column)=s.position(span.start);
                        serde_json::json!({"pos":{"file":s.path,"offset":span.start,"end_offset":span.end,"line":line,"column":column},"msgs":["Purity violation in the called implementation."]})
                    }).collect::<Vec<_>>());
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
