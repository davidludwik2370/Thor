//! Bounded, request-local proofs for allocation/escape-free value operations
//! and readers. No summary is inferred merely from a procedure's signature.
use super::*;

type Key = (Ref, Substitutions);
const MAX_NODES: usize = 16_384;
const MAX_PROOF: usize = 1_024;
const MAX_PROCEDURES: usize = 64;

#[derive(Clone)]
pub(super) struct Summary {
    pub results: usize,
    pub borrowed: Vec<bool>,
    pub reads: Vec<bool>,
    pub writes: Vec<bool>,
    pub globals: BTreeSet<Ref>,
    pub global_writes:BTreeSet<Ref>,
    pub graphs: Vec<Option<super::recursive::Input>>,
    pub bytes:Vec<i128>,
    pub nuls:Vec<bool>,
    pub projected:Vec<bool>,
    pub projections:Vec<BTreeSet<Vec<String>>>,
}
#[derive(Default)]
pub(super) struct Cache {
    pub(super) entries: HashMap<Key, Option<Summary>>,
    pub(super) owners: HashMap<Key, Vec<bool>>,
    pub(super) builders: HashMap<Ref, bool>,
    nodes: usize,
    pub(super) exclusive: HashMap<(Ref, Substitutions), bool>,
    pub(super) fresh_headers: HashMap<(Ref, Substitutions), bool>,
    pub(super) exclusive_nodes: usize,
    pub(super) graph_types: HashMap<Type, bool>,
    pub(super) empty_tasks:HashMap<Ref,bool>,
    pub(super) header_pairs:HashMap<Key,Option<(Ref,Ref)>>,
}
pub(super) struct Proof {
    pub(super) summary: Summary,
    pub(super) parameters: HashMap<Ref, usize>,
    edges: Vec<(Key, Vec<Option<usize>>)>,
    pub(super) graph_edges: Vec<(Key, usize, usize, Vec<String>)>,
    pub(super) scratch: HashSet<Ref>,
    pub(super) scratch_allocations: HashSet<Ref>,
    pub(super) scratch_cleanups: HashSet<Ref>,
    pub(super) buffer_edges:Vec<(Key,Vec<Option<super::buffers::Argument>>)>,
    storage_arguments:Vec<(Key,Vec<Option<Ref>>)>,
    pub(super) projection_edges:Vec<(Key,Vec<Option<(usize,Vec<String>)>>)>,
}

impl Analysis<'_, '_> {
    fn reader_type(&mut self, typ: &Type) -> bool {
        matches!(base(typ), Type::String { .. })
            || matches!(base(typ),Type::Scalar{name,..} if matches!(name.as_str(),"cstring"|"cstring16"))
            || matches!(base(typ),Type::Slice(t)|Type::Dynamic(t)|Type::Pointer(t) if self.inline_type(t,0))
    }
    fn summary_interface(&mut self, proc: Ref, bindings: &Substitutions) -> Option<Proof> {
        if self.r.sources[proc.file].shared_native || self.role(proc, "body").is_none() {
            return None;
        }
        let declaration = self.r.sources[proc.file]
            .node(proc.node)
            .parent
            .map(|node| Ref { node, ..proc })
            .unwrap_or(proc);
        if self
            .r
            .attributes(declaration)
            .iter()
            .any(|(name, _)| name.starts_with("deferred_") || name == "disabled")
        {
            return None;
        }
        let signature = self.role(proc, "type")?;
        if self.r.sources[signature.file].node(signature.node).data["diverging"] == true {
            return None;
        }
        let mut borrowed = vec![];
        let mut graphs = vec![];
        let mut projected=vec![];
        let fields = self.r.fields(signature, "params");
        for (name, annotation) in &fields {
            let source = &self.r.sources[signature.file];
            let inferred = annotation.or_else(|| {
                let params = source.role(signature.node, "params")?;
                source
                    .node(params)
                    .children
                    .iter()
                    .flat_map(|field| source.group(*field, "names"))
                    .find(|node| source.text(**node).trim_start_matches('$') == name)
                    .map(|node| Ref {
                        node: *node,
                        ..signature
                    })
            })?;
            let typ = self.r.specialized_type(inferred, bindings);
            let projectable=self.projectable_record(&typ);
            projected.push(projectable);
            if self.inline_type(&typ, 0) {
                borrowed.push(false);
                graphs.push(None);
            } else if self.reader_type(&typ) {
                borrowed.push(true);
                graphs.push(None);
            } else if self.graph_reader_type(&typ) {
                borrowed.push(true);
                graphs.push(Some(super::recursive::Input {
                    typ,
                    entry: 0,
                    program: None,
                }));
            } else if projectable {
                borrowed.push(false);
                graphs.push(None);
            } else {
                return None;
            }
        }
        let results = self.r.fields(signature, "results");
        for (_, annotation) in &results {
            let typ = self.r.specialized_type((*annotation)?, bindings);
            if !self.inline_type(&typ, 0) {
                return None;
            }
        }
        let mut parameters = HashMap::new();
        if let Some(params) = self.role(signature, "params") {
            let mut index = 0;
            for child in self.r.sources[params.file]
                .node(params.node)
                .children
                .clone()
            {
                for name in self.group(
                    Ref {
                        node: child,
                        ..params
                    },
                    "names",
                ) {
                    parameters.insert(self.symbol(name).unwrap_or(name), index);
                    index += 1;
                }
            }
        }
        Some(Proof {
            summary: Summary {
                results: results.len(),
                reads: vec![false; borrowed.len()],
                writes: vec![false; borrowed.len()],
                globals: BTreeSet::new(),
                global_writes:BTreeSet::new(),
                graphs,
                bytes:vec![0;borrowed.len()],
                nuls:vec![false;borrowed.len()],
                projected,
                projections:vec![BTreeSet::new();borrowed.len()],
                borrowed,
            },
            parameters,
            edges: vec![],
            graph_edges: vec![],
            scratch: HashSet::new(),
            scratch_allocations: HashSet::new(),
            scratch_cleanups: HashSet::new(),
            buffer_edges:vec![],
            storage_arguments:vec![],
            projection_edges:vec![],
        })
    }
    /// Verify an entire dependency component before publishing any member.
    /// A back edge alone is never treated as an established contract.
    pub(super) fn neutral_summary(
        &mut self,
        proc: Ref,
        bindings: &Substitutions,
    ) -> Option<Summary> {
        let key = (proc, bindings.clone());
        if let Some(answer) = self.summaries.entries.get(&key) {
            crate::profile::count("lifetime.summary_hits", 1);
            return answer.clone();
        }
        if self.summaries.nodes >= MAX_NODES {
            return None;
        }
        crate::profile::count("lifetime.summary_proofs", 1);
        let mut pending = vec![key.clone()];
        let mut component = HashMap::<Key, Proof>::new();
        let start = self.summaries.nodes;
        let mut failed = false;
        while let Some(candidate) = pending.pop() {
            if component.contains_key(&candidate) {
                continue;
            }
            if let Some(answer) = self.summaries.entries.get(&candidate) {
                if answer.is_none() {
                    failed = true;
                    break;
                }
                continue;
            }
            if component.len() >= MAX_PROCEDURES {
                failed = true;
                break;
            }
            let Some(mut proof) = self.summary_interface(candidate.0, &candidate.1) else {
                failed = true;
                break;
            };
            let f = Frame {
                id: 0,
                procedure: candidate.0,
                env: HashMap::new(),
                range_places: HashMap::new(),
                scope: candidate.0,
                bindings: candidate.1.clone(),
                keys: HashMap::new(),
            };
            // Defaults may contain calls and execute in an ordinary invocation.
            // Verify them in the callee's generic context too.
            let signature = self.role(candidate.0, "type").unwrap();
            let defaults = self
                .role(signature, "params")
                .map(|params| {
                    self.r.sources[params.file]
                        .node(params.node)
                        .children
                        .iter()
                        .filter_map(|node| {
                            self.role(
                                Ref {
                                    node: *node,
                                    ..params
                                },
                                "default",
                            )
                        })
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            let body = self.role(candidate.0, "body").unwrap();
            for root in defaults.into_iter().chain([body]) {
                if !self.summary_node(root, &f, &mut proof, &mut pending, start, 0, false) {
                    failed = true;
                    break;
                }
            }
            if failed {
                break;
            }
            component.insert(candidate, proof);
        }
        if failed {
            self.summaries.entries.insert(key, None);
            return None;
        }
        // Propagate data reads through parameter forwarding, including cycles.
        // Masks only grow, so this closes in at most the number of parameter bits.
        loop {
            let mut changes = vec![];
            for (key, proof) in &component {
                for (callee, mapping) in &proof.edges {
                    let target = component
                        .get(callee)
                        .map(|p| &p.summary)
                        .or_else(|| self.summaries.entries.get(callee).and_then(Option::as_ref));
                    let Some(target) = target else {
                        continue;
                    };
                    for (index, read) in target.reads.iter().enumerate() {
                        if *read {
                            if let Some(Some(parameter)) = mapping.get(index) {
                                if !proof.summary.reads[*parameter] {
                                    changes.push((key.clone(), *parameter));
                                }
                            }
                        }
                    }
                }
            }
            if changes.is_empty() {
                break;
            }
            for (key, index) in changes {
                component.get_mut(&key).unwrap().summary.reads[index] = true;
            }
        }
        loop {
            let mut changes = vec![];
            for (key, proof) in &component {
                for (callee, mapping) in &proof.edges {
                    let target = component
                        .get(callee)
                        .map(|p| &p.summary)
                        .or_else(|| self.summaries.entries.get(callee).and_then(Option::as_ref));
                    let Some(target) = target else {
                        continue;
                    };
                    for (i, write) in target.writes.iter().enumerate() {
                        if *write {
                            if let Some(Some(p)) = mapping.get(i) {
                                if !proof.summary.writes[*p] {
                                    changes.push((key.clone(), Some(*p), None,false));
                                }
                            }
                        }
                    }
                    for global in &target.globals {
                        if !proof.summary.globals.contains(global) {
                            changes.push((key.clone(), None, Some(*global),false));
                        }
                    }
                    for global in &target.global_writes{if !proof.summary.global_writes.contains(global){changes.push((key.clone(),None,Some(*global),true));}}
                }
                for(callee,args)in &proof.storage_arguments{
                    let Some(target)=component.get(callee).map(|p|&p.summary).or_else(||self.summaries.entries.get(callee).and_then(Option::as_ref))else{continue};
                    for(i,write)in target.writes.iter().enumerate(){if !write{continue;}
                        if let Some(arg)=args.get(i).copied().flatten(){let(parameter,global)=self.summary_storage_root(arg,proof);
                            if let Some(p)=parameter{if proof.summary.borrowed[p]&&!proof.summary.writes[p]{changes.push((key.clone(),Some(p),None,false));}}
                            if let Some(g)=global{if !proof.summary.global_writes.contains(&g){changes.push((key.clone(),None,Some(g),true));}}
                        }
                    }
                }
            }
            if changes.is_empty() {
                break;
            }
            for (key, write, global,is_write) in changes {
                let summary = &mut component.get_mut(&key).unwrap().summary;
                if let Some(i) = write {
                    summary.writes[i] = true;
                }
                if let Some(g) = global {
                    summary.globals.insert(g);
                    if is_write{summary.global_writes.insert(g);}
                }
            }
        }
        if !self.close_projection_requirements(&mut component) || !self.compile_graph_program(&mut component) {
            self.summaries.entries.insert(key, None);
            return None;
        }
        if !self.close_buffer_requirements(&mut component){self.summaries.entries.insert(key,None);return None;}
        for (key, proof) in component {
            self.summaries.entries.insert(key, Some(proof.summary));
        }
        crate::profile::count("lifetime.summary_verified", 1);
        self.summaries.entries.get(&key).cloned().flatten()
    }
    pub(super) fn summary_parameter(&self, at: Ref, proof: &Proof) -> Option<usize> {
        let mut at = at;
        for _ in 0..MAX_VALUE_DEPTH {
            if matches!(self.kind(at), "Paren" | "Slice") {
                at = self.role(at, "expr")?;
            } else {
                break;
            }
        }
        proof.parameters.get(&self.symbol(at)?).copied()
    }
    fn summary_storage_root(&self,mut at:Ref,proof:&Proof)->(Option<usize>,Option<Ref>){
        for _ in 0..MAX_VALUE_DEPTH{
            if let Some(symbol)=self.symbol(at){
                if let Some(i)=proof.parameters.get(&symbol){return(Some(*i),None);}
                if proof.summary.globals.contains(&symbol){return(None,Some(symbol));}
            }
            if !matches!(self.kind(at),"Paren"|"Slice"|"Selector"|"Index"|"Deref"|"Unary"){break;}
            let Some(expr)=self.role(at,"expr")else{break};at=expr;
        }
        (None,None)
    }
    fn summary_write_root(&self,at:Ref,proof:&mut Proof){
        let(parameter,global)=self.summary_storage_root(at,proof);
        if let Some(i)=parameter{proof.summary.writes[i]|=proof.summary.borrowed[i];}
        if let Some(g)=global{proof.summary.global_writes.insert(g);}
    }
    fn summary_global(&mut self, at: Ref, f: &Frame, proof: &mut Proof) -> bool {
        let Some(index) = self.r.symbol(at) else {
            return false;
        };
        let symbol = self.r.symbols[index].clone();
        let s = &self.r.sources[symbol.reference.file];
        if symbol.parameter
            || symbol.result
            || s.ancestors(symbol.name_node)
                .any(|n| s.node(n).kind == "Procedure")
        {
            return false;
        }
        if self
            .r
            .attributes(symbol.reference)
            .iter()
            .any(|(name, _)| name == "thread_local")
        {
            return false;
        }
        let typ = self.typ(at, f);
        if !self.inline_type(&typ, 0) {
            return false;
        }
        if let Some(value) = symbol.value {
            if self
                .r
                .constant_value(
                    Ref {
                        file: symbol.reference.file,
                        node: value,
                    },
                    &Substitutions::new(),
                    None,
                )
                .is_none()
            {
                return false;
            }
        }
        proof.summary.globals.insert(Ref {
            file: symbol.reference.file,
            node: symbol.name_node,
        });
        true
    }
    fn summary_contract(
        &mut self,
        at: Ref,
        callee: Ref,
        args: &[Ref],
        f: &Frame,
        proof: &mut Proof,
        pending: &mut Vec<Key>,
        start: usize,
        depth: usize,
    ) -> Option<bool> {
        use contracts::Effect;
        if !crate::parallelcompile::known_callback(self.r, callee) {
            return None;
        }
        let model = self.call_model(at, callee);
        let (lib, name) = model.library?;
        let effect = contracts::effect(lib, &name)?;
        if effect == Effect::Format
            && name == "assertf"
            && self.r.flags.iter().any(|f| f == "-disable-assert")
        {
            return Some(true);
        }
        if effect == Effect::Format
            && (self.custom_formatting || self.custom_io && !name.starts_with('b'))
            || effect == Effect::TextRead && (!self.raylib_six || self.custom_foreign_callbacks)
            || matches!(effect, Effect::RandomCollection | Effect::RandomScalar)
                && !self.known_generator(at, args, lib, &name)
        {
            return Some(false);
        }
        let discard = self.r.sources[at.file]
            .node(at.node)
            .parent
            .is_some_and(|p| {
                let s = &self.r.sources[at.file];
                s.node(p).kind == "ExpressionStatement"
                    || s.node(p).kind == "Assignment"
                        && s.group(p, "lhs").iter().all(|lhs| s.text(*lhs) == "_")
            });
        // Borrowed formatter results cannot be dropped into an inline result
        // shortcut. Ignoring such a result does not transfer any ownership.
        if effect == Effect::Format && name.starts_with('b') && !discard {
            return Some(false);
        }
        if effect == Effect::RandomCollection && name == "choice" {
            let typ = self.typ(at, f);
            if !self.inline_type(&typ, 0) {
                return Some(false);
            }
        }
        if effect == Effect::Math {
            let typ = self.typ(at, f);
            if !self.inline_type(&typ, 0) {
                return Some(false);
            }
        }
        for (i, arg) in args.iter().enumerate() {
            let actual = self.role(*arg, "value").unwrap_or(*arg);
            if effect==Effect::Endian && self.argument(args,"b",0)==Some(i){
                let width=if name.contains("16"){2}else if name.contains("32"){4}else{8};
                if !self.summary_buffer(actual,f,proof,width,false){return Some(false);}
            }
            if effect==Effect::TextRead && i==0 && !self.summary_buffer(actual,f,proof,0,true){return Some(false);}
            if effect==Effect::Format && Self::cstring_type(&self.typ(actual,f))
                && !self.summary_buffer(actual,f,proof,0,true){return Some(false);}
            if self.r.denotes_type(actual) {
                continue;
            }
            if effect == Effect::Format && matches!(base(&self.typ(actual, f)), Type::Pointer(_)) {
                // Pointer verbs have different dereference semantics. Preserve
                // the interpreter's conditional consume, not a definite read.
                return Some(false);
            }
            // A concrete default generator constructor has no caller reference
            // effects. Opaque callbacks were rejected above.
            let generator = matches!(effect, Effect::RandomCollection | Effect::RandomScalar)
                && self.role(actual, "callee").is_some_and(|c| {
                    let m = self.call_model(actual, c);
                    m.library.is_some_and(|(l, n)| {
                        matches!(l, "core:math/rand" | "base:runtime")
                            && n == "default_random_generator"
                    })
                });
            if generator {
                continue;
            }
            let read = effect != Effect::Math && effect != Effect::RandomScalar;
            if !self.summary_node(actual, f, proof, pending, start, depth + 1, read) {
                return Some(false);
            }
            let write = effect == Effect::Endian
                && name.starts_with("unchecked_put_")
                && self.argument(args, "b", 0) == Some(i)
                || effect == Effect::RandomCollection
                    && name == "shuffle"
                    && self.argument(args, "array", 0) == Some(i)
                || effect == Effect::Parse && self.parse_output_argument(at, args, f) == Some(i)
                || effect == Effect::Format
                    && name.starts_with('b')
                    && self.argument(args, "buf", 0) == Some(i);
            if write {
                self.summary_write_root(actual,proof);
            }
        }
        crate::profile::count("lifetime.summary_library_contracts", 1);
        Some(true)
    }
    fn summary_node(
        &mut self,
        at: Ref,
        f: &Frame,
        proof: &mut Proof,
        pending: &mut Vec<Key>,
        start: usize,
        depth: usize,
        read: bool,
    ) -> bool {
        if depth > MAX_VALUE_DEPTH
            || self.summaries.nodes >= MAX_NODES
            || self.summaries.nodes - start >= MAX_PROOF
        {
            return false;
        }
        self.summaries.nodes += 1;
        crate::profile::count("lifetime.summary_nodes", 1);
        let kind = self.kind(at).to_owned();
        match kind.as_str() {
            "Literal" | "Implicit" | "ImplicitSelector" | "EmptyStatement" | "Branch" => {
                return true
            }
            "Procedure" | "ProcedureGroup" => return false,
            "Ident" | "PolyType" => {
                if self.text(at) == "_" || self.r.native_builtin(at, "nil") {
                    return true;
                }
                if let Some(index) = self.summary_parameter(at, proof) {
                    if proof.summary.projected[index] && read{return false;}
                    if read {
                        proof.summary.reads[index] |= proof.summary.borrowed[index];
                    }
                    return true;
                }
                if let Some(p) = self.r.projection(at) {
                    return self.summary_node(p.owner, f, proof, pending, start, depth + 1, true);
                }
                let Some(symbol) = self.r.symbol(at) else {
                    return self.r.denotes_type(at);
                };
                let symbol = &self.r.symbols[symbol];
                let local = self.r.sources[symbol.reference.file]
                    .ancestors(symbol.name_node)
                    .find(|n| self.r.sources[symbol.reference.file].node(*n).kind == "Procedure")
                    .is_some_and(|node| {
                        Ref {
                            file: symbol.reference.file,
                            node,
                        } == f.procedure
                    });
                if !local && (!symbol.constant || symbol.immutable) {
                    return self.summary_global(at, f, proof);
                }
                let typ = self.typ(at, f);
                return self.inline_type(&typ, 0) || proof.scratch.contains(&self.symbol(at).unwrap_or(at));
            }
            "Call" => {
                let Some(callee) = self.role(at, "callee") else {
                    return false;
                };
                if !matches!(self.kind(callee), "Ident" | "Selector" | "Paren") {
                    return false;
                }
                let args = self.group(at, "args");
                if proof.scratch_allocations.contains(&at) {
                    return args.into_iter().all(|arg| self.r.denotes_type(arg)
                        || self.summary_node(arg,f,proof,pending,start,depth+1,false));
                }
                let builtin = {
                    let mut arena = crate::borrowflow::Arena::default();
                    crate::borroweval::Evaluator {
                        resolver: self.r,
                        arena: &mut arena,
                    }
                    .builtin(at)
                };
                if builtin.as_deref() == Some("assert")
                    && self.r.flags.iter().any(|flag| flag == "-disable-assert")
                {
                    return true;
                }
                if builtin.as_deref()==Some("len") && args.first().is_some_and(|a|Self::cstring_type(&self.typ(*a,f))){
                    let actual=args[0];if !self.summary_buffer(actual,f,proof,0,true){return false;}
                    if let Some(i)=self.summary_parameter(actual,proof){proof.summary.reads[i]=true;}
                }
                let fixed_mutation=builtin.as_deref().is_some_and(|name|matches!(name,"append"|"append_elem"|"pop"|"clear"))
                    && args.first().is_some_and(|arg|matches!(base(&self.typ(*arg,f)),Type::Pointer(inner)
                        if matches!(base(inner),Type::Fixed{..}|Type::Array{..})&&self.inline_type(inner,0)));
                if fixed_mutation {
                    if let Some(index)=self.summary_parameter(args[0],proof){proof.summary.reads[index]=true;proof.summary.writes[index]=true;}
                } else if self.r.denotes_type(callee) {
                    let typ = self.typ(at, f);
                    if !self.inline_type(&typ, 0) {
                        return false;
                    }
                    for arg in &args {
                        let actual = self.role(*arg, "value").unwrap_or(*arg);
                        let typ = self.typ(actual, f);
                        if !self.r.denotes_type(actual) && !self.inline_type(&typ, 0) {
                            return false;
                        }
                    }
                } else if builtin.as_deref().is_some_and(|n| {
                    matches!(
                        n,
                        "len"
                            | "cap"
                            | "size_of"
                            | "align_of"
                            | "offset_of"
                            | "min"
                            | "max"
                            | "abs"
                            | "clamp"
                            | "assert"
                    )
                }) {
                    if builtin.as_deref() == Some("assert")
                        && args.first().is_some_and(|arg| {
                            matches!(
                                self.r.constant_value(*arg, &f.bindings, None),
                                Some(Constant::Boolean(false))
                            )
                        })
                    {
                        return false;
                    }
                } else if let Some(answer) =
                    self.summary_contract(at, callee, &args, f, proof, pending, start, depth)
                {
                    return answer;
                } else {
                    if !crate::parallelcompile::known_callback(self.r, callee) {
                        return false;
                    }
                    let plans = self.r.call_plans(at, &f.bindings);
                    if plans.len() != 1 {
                        return false;
                    }
                    let plan = &plans[0];
                    if self.r.sources[plan.procedure.file].shared_native {
                        return false;
                    }
                    let target = (plan.procedure, plan.bindings.clone());
                    proof.projection_edges.push((target.clone(),plan.arguments.iter().map(|g|g.first().and_then(|a|self.summary_projection(*a,proof))).collect()));
                    proof.storage_arguments.push((target.clone(),plan.arguments.iter().map(|args|args.first().copied()).collect()));
                    let buffer_mapping=plan.arguments.iter().map(|g|g.first().and_then(|a|self.buffer_argument(*a,f,proof))).collect();
                    proof.buffer_edges.push((target.clone(),buffer_mapping));
                    let mapping = plan
                        .arguments
                        .iter()
                        .map(|group| {
                            group
                                .first()
                                .and_then(|a| self.summary_parameter(*a, proof))
                        })
                        .collect();
                    for (index, args) in plan.arguments.iter().enumerate() {
                        if let Some((parameter, path)) =
                            args.first().and_then(|a| self.graph_argument(*a, proof))
                        {
                            proof
                                .graph_edges
                                .push((target.clone(), index, parameter, path));
                        }
                    }
                    proof.edges.push((target.clone(), mapping));
                    pending.push(target);
                }
                return args.into_iter().all(|arg| {
                    let actual = self.role(arg, "value").unwrap_or(arg);
                    self.r.denotes_type(actual)
                        || self.summary_node(actual, f, proof, pending, start, depth + 1, false)
                });
            }
            "Selector" => {
                if let Some((index,path))=self.summary_projection(at,proof){
                    if proof.summary.projected[index] {
                        let typ=self.typ(at,f);
                        if path.is_empty()||!self.inline_type(&typ,0){return false;}
                        proof.summary.projections[index].insert(path);
                        return proof.summary.projections[index].len()<=32;
                    }
                }
                if let Some(symbol) = self.r.symbol(at) {
                    // Namespace selections follow the same global-state rule
                    // as identifiers. Even an integer global can carry an
                    // address; its type alone cannot justify erasing it.
                    let symbol = &self.r.symbols[symbol];
                    if !symbol.constant || symbol.immutable {
                        return self.summary_global(at, f, proof);
                    }
                    let typ = self.typ(at, f);
                    return self.inline_type(&typ, 0);
                }
                let Some(expr) = self.role(at, "expr") else {
                    return false;
                };
                let typ = self.typ(at, f);
                let inline = self.inline_type(&typ, 0);
                if !inline && !self.graph_reader_type(&typ) {
                    return false;
                }
                if self
                    .graph_argument(expr, proof)
                    .is_some_and(|(_, path)| !path.is_empty())
                {
                    // Explicit multi-hop accesses need a longer path contract;
                    // this bounded domain follows them through verified calls.
                    return false;
                }
                return self.summary_node(expr, f, proof, pending, start, depth + 1, true);
            }
            "Deref" | "Index" | "MatrixIndex" => {
                let typ = self.typ(at, f);
                if !self.inline_type(&typ, 0) {
                    return false;
                }
                let Some(expr) = self.role(at, "expr") else {
                    return false;
                };
                if kind=="Index" && Self::byte_element(&typ){if let Some(index)=self.role(at,"index"){
                    if let Some(Constant::Integer(n))=self.r.constant_value(index,&f.bindings,None){
                        if n>=0 && n.checked_add(1).is_none_or(|count|!self.summary_buffer(expr,f,proof,count,false)){return false;}
                    }
                }}
                if !self.summary_node(expr, f, proof, pending, start, depth + 1, true) {
                    return false;
                }
                return ["index", "x", "y"]
                    .into_iter()
                    .filter_map(|r| self.role(at, r))
                    .collect::<Vec<_>>()
                    .into_iter()
                    .all(|n| self.summary_node(n, f, proof, pending, start, depth + 1, false));
            }
            "Unary" => {
                if self.r.sources[at.file].node(at.node).op == "&" {
                    return self.role(at,"expr").is_some_and(|expr| self.scratch_place(expr,f,proof)
                        && self.summary_node(expr,f,proof,pending,start,depth+1,true));
                }
            }
            "Slice" => {
                let Some(expr) = self.role(at, "expr") else {
                    return false;
                };
                let typ = self.typ(expr, f);
                if !self.inline_type(&typ, 0) && !self.reader_type(&typ) {
                    return false;
                }
                if !self.summary_node(expr, f, proof, pending, start, depth + 1, read) {
                    return false;
                }
                return ["low", "high"]
                    .into_iter()
                    .filter_map(|r| self.role(at, r))
                    .collect::<Vec<_>>()
                    .into_iter()
                    .all(|n| self.summary_node(n, f, proof, pending, start, depth + 1, false));
            }
            "Cast" | "AutoCast" | "TypeAssertion" | "OrReturn" | "OrBranch" | "OrElse" | "Run"
            | "Comprehension" | "FString" | "Interpolation" | "OrPanic" => return false,
            "FieldValue" => {
                return self.role(at, "value").is_some_and(|n| {
                    self.summary_node(n, f, proof, pending, start, depth + 1, read)
                })
            }
            "When" | "WhenExpr" | "If" | "Ternary" => {
                if let Some(init) = self.role(at, "init") {
                    if !self.summary_node(init, f, proof, pending, start, depth + 1, false) {
                        return false;
                    }
                }
                if let Some(cond) = self.role(at, "cond") {
                    let known = if matches!(kind.as_str(), "When" | "WhenExpr") {
                        self.r.context_condition(cond, &f.bindings)
                    } else {
                        match self.r.constant_value(cond, &f.bindings, None) {
                            Some(Constant::Boolean(v)) => Some(v),
                            _ => None,
                        }
                    };
                    if let Some(value) = known {
                        if !self.summary_node(cond, f, proof, pending, start, depth + 1, false) {
                            return false;
                        }
                        let branch = self
                            .role(at, if value { "body" } else { "else" })
                            .or_else(|| self.role(at, if value { "x" } else { "y" }));
                        return branch.is_none_or(|branch| {
                            self.summary_node(branch, f, proof, pending, start, depth + 1, read)
                        });
                    }
                }
            }
            "Using" => {
                return self
                    .group(at, "values")
                    .into_iter()
                    .all(|n| self.summary_node(n, f, proof, pending, start, depth + 1, false))
            }
            "Assignment" => {
                for lhs in self.group(at, "lhs") {
                    if self.text(lhs) == "_" {
                        continue;
                    }
                    if !matches!(
                        self.kind(lhs),
                        "Ident" | "Index" | "MatrixIndex" | "Selector" | "Deref"
                    ) {
                        return false;
                    }
                    let typ = self.typ(lhs, f);
                    if !self.inline_type(&typ, 0)
                        || !self.summary_node(lhs, f, proof, pending, start, depth + 1, true)
                    {
                        return false;
                    }
                    let mut root = lhs;
                    for _ in 0..MAX_VALUE_DEPTH {
                        if let Some(index) = self.summary_parameter(root, proof) {
                            proof.summary.writes[index] |= proof.summary.borrowed[index];
                            break;
                        }
                        if let Some(global)=self.symbol(root).filter(|g|proof.summary.globals.contains(g)){proof.summary.global_writes.insert(global);break;}
                        let Some(expr) = self.role(root, "expr") else {
                            break;
                        };
                        root = expr;
                    }
                }
                return self
                    .group(at, "rhs")
                    .into_iter()
                    .all(|n| self.summary_node(n, f, proof, pending, start, depth + 1, false));
            }
            "Declaration" => {
                if self
                    .r
                    .attributes(at)
                    .iter()
                    .any(|(name, _)| matches!(name.as_str(), "static" | "thread_local"))
                {
                    return false;
                }
                let values = self.group(at, "values");
                self.register_summary_scratch(at,f,proof);
                let node = self.r.sources[at.file].node(at.node);
                if !node.mutable
                    && !node.immutable
                    && values.iter().all(|v| {
                        matches!(self.kind(*v), "Procedure" | "ProcedureGroup")
                            || matches!(self.typ(*v, f), Type::Procedure(_))
                    })
                {
                    return true;
                }
                for name in self.group(at, "names") {
                    let typ = self.typ(name, f);
                    if self.text(name) != "_" && !self.inline_type(&typ, 0)
                        && !proof.scratch.contains(&self.symbol(name).unwrap_or(name)) {
                        return false;
                    }
                }
                return values
                    .into_iter()
                    .all(|v| self.summary_node(v, f, proof, pending, start, depth + 1, false));
            }
            "Defer" => return proof.scratch_cleanups.contains(&at),
            "Range" => {
                if let Some(init) = self.role(at, "init") {
                    if !self.summary_node(init, f, proof, pending, start, depth + 1, false) {
                        return false;
                    }
                }
                let Some(expr) = self.role(at, "expr") else {
                    return false;
                };
                if !self.summary_node(expr, f, proof, pending, start, depth + 1, true) {
                    return false;
                }
                for name in self.group(at, "names") {
                    if self.kind(name) == "Unary" {
                        let mut root = expr;
                        let mut parameter = None;
                        for _ in 0..MAX_VALUE_DEPTH {
                            if let Some(i) = self.summary_parameter(root, proof) {
                                parameter = Some(i);
                                break;
                            }
                            let Some(value) = self.role(root, "expr") else {
                                break;
                            };
                            root = value;
                        }
                        if let Some(i) = parameter.filter(|i| proof.summary.borrowed[*i]) {
                            let Some(binding) =
                                self.role(name, "expr").and_then(|name| self.symbol(name))
                            else {
                                return false;
                            };
                            proof.parameters.insert(binding, i);
                        } else {
                            let typ = self.typ(expr, f);
                            if !self.inline_type(&typ, 0) {
                                return false;
                            }
                        }
                    }
                }
                return self.role(at, "body").is_some_and(|n| {
                    self.summary_node(n, f, proof, pending, start, depth + 1, false)
                });
            }
            "Block"
            | "Case"
            | "For"
            | "Switch"
            | "CompileSwitch"
            | "TypeSwitch"
            | "ExpressionStatement"
            | "Return"
            | "Paren"
            | "Runtime"
            | "SwitchResults" => {}
            "Binary" | "Compound" => {
                if kind == "Binary"
                    && matches!(
                        self.r.sources[at.file].node(at.node).op.as_str(),
                        "&&" | "||"
                    )
                {
                    if let Some(left) = self.role(at, "left") {
                        let value = self.r.constant_value(left, &f.bindings, None);
                        let op = &self.r.sources[at.file].node(at.node).op;
                        if matches!(value,Some(Constant::Boolean(v)) if v==(op=="||")) {
                            return self.summary_node(
                                left,
                                f,
                                proof,
                                pending,
                                start,
                                depth + 1,
                                read,
                            );
                        }
                    }
                }
                let typ = self.typ(at, f);
                if !self.inline_type(&typ, 0) {
                    return false;
                }
            }
            k if k.ends_with("Type") => return true,
            _ => return false,
        }
        let children = self.r.sources[at.file].node(at.node).children.clone();
        children.into_iter().all(|node| {
            let child = Ref { node, ..at };
            self.r.denotes_type(child)
                || self.summary_node(child, f, proof, pending, start, depth + 1, read)
        })
    }
}
