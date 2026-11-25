//! Finite read automata for recursive pointer/record readers and inline-field
//! writers. Programs contain field transitions, never allocation identities.
//! Reference writes, allocations, opaque effects and oversized graphs fall
//! back to the ordinary interpreter and its explicit proof boundaries.
use super::*;
type CacheKey = (Ref, Substitutions);
const MAX_STATES: usize = 128;
const MAX_READS: usize = 128;

#[derive(Clone)]
pub(super) struct Input {
    pub typ: Type,
    pub entry: usize,
    pub program: Option<Arc<Program>>,
}
#[derive(Clone)]
pub(super) struct ReadState {
    typ: Type,
    read: bool,
    edges: Vec<(Vec<String>, usize)>,
}
pub(super) struct Program {
    states: Vec<ReadState>,
}

impl Analysis<'_, '_> {
    pub(super) fn graph_reader_type(&mut self, typ: &Type) -> bool {
        if let Some(answer) = self.summaries.graph_types.get(typ) {
            return *answer;
        }
        let answer = self.prove_graph_type(typ);
        self.summaries.graph_types.insert(typ.clone(), answer);
        answer
    }
    fn prove_graph_type(&mut self, typ: &Type) -> bool {
        let Type::Pointer(inner) = base(typ) else {
            return false;
        };
        if self.inline_type(inner, 0) {
            return false;
        }
        let mut todo = vec![typ.clone()];
        let mut seen = HashSet::new();
        while let Some(typ) = todo.pop() {
            if !seen.insert(typ.clone()) {
                continue;
            }
            if seen.len() > 16 {
                return false;
            }
            let Type::Pointer(inner) = base(&typ) else {
                return false;
            };
            if !matches!(
                base(inner),
                Type::Struct(_) | Type::Record(..) | Type::Anonymous(_)
            ) {
                return false;
            }
            if matches!(base(inner),Type::Struct(o)|Type::Record(o,_) if self.r.sources[o.file].node(o.node).op=="raw_union")
            {
                return false;
            }
            for (_, field) in self.r.record_fields(inner) {
                if self.inline_type(&field, 0) {
                    continue;
                }
                if matches!(base(&field), Type::Pointer(_)) {
                    todo.push(field);
                } else {
                    return false;
                }
            }
        }
        true
    }
    pub(super) fn graph_argument(
        &self,
        mut at: Ref,
        proof: &summaries::Proof,
    ) -> Option<(usize, Vec<String>)> {
        let mut path = vec![];
        for _ in 0..8 {
            match self.kind(at) {
                "FieldValue" => at = self.role(at, "value")?,
                "Paren" => at = self.role(at, "expr")?,
                "Selector" => {
                    path.push(self.text(self.role(at, "field")?).to_owned());
                    at = self.role(at, "expr")?;
                }
                "Ident" => {
                    let parameter = *proof.parameters.get(&self.symbol(at)?)?;
                    proof.summary.graphs.get(parameter)?.as_ref()?;
                    path.reverse();
                    return Some((parameter, path));
                }
                _ => return None,
            }
        }
        None
    }
    pub(super) fn compile_graph_program(
        &mut self,
        component: &mut HashMap<CacheKey, summaries::Proof>,
    ) -> bool {
        if !component
            .values()
            .any(|p| p.summary.graphs.iter().any(Option::is_some))
        {
            return true;
        }
        let mut indices = HashMap::new();
        let mut states = vec![];
        for (key, proof) in component.iter() {
            for (i, input) in proof.summary.graphs.iter().enumerate() {
                if let Some(input) = input {
                    indices.insert((key.clone(), i), states.len());
                    states.push(ReadState {
                        typ: input.typ.clone(),
                        read: proof.summary.reads[i],
                        edges: vec![],
                    });
                }
            }
        }
        if states.len() > MAX_STATES {
            return false;
        }
        // Imported programs are immutable and already verified. Copy each at
        // most once into this small component so recursive steps are O(1).
        let mut imported = HashMap::new();
        for (key, proof) in component.iter() {
            for (target, index, parameter, path) in &proof.graph_edges {
                let Some(&from) = indices.get(&(key.clone(), *parameter)) else {
                    return false;
                };
                let to = if let Some(&id) = indices.get(&(target.clone(), *index)) {
                    id
                } else {
                    let Some(input) = self
                        .summaries
                        .entries
                        .get(target)
                        .and_then(Option::as_ref)
                        .and_then(|s| s.graphs.get(*index))
                        .and_then(Option::as_ref)
                    else {
                        continue;
                    };
                    let Some(program) = &input.program else {
                        return false;
                    };
                    let id = Arc::as_ptr(program) as usize;
                    let offset = if let Some(offset) = imported.get(&id) {
                        *offset
                    } else {
                        if states.len() + program.states.len() > MAX_STATES {
                            return false;
                        }
                        let offset = states.len();
                        states.extend(program.states.iter().map(|s| {
                            ReadState {
                                typ: s.typ.clone(),
                                read: s.read,
                                edges: s
                                    .edges
                                    .iter()
                                    .map(|(p, n)| (p.clone(), n + offset))
                                    .collect(),
                            }
                        }));
                        imported.insert(id, offset);
                        offset
                    };
                    offset + input.entry
                };
                states[from].edges.push((path.clone(), to));
            }
        }
        let program = Arc::new(Program { states });
        for (key, proof) in component.iter_mut() {
            for (i, input) in proof.summary.graphs.iter_mut().enumerate() {
                if let Some(input) = input {
                    input.entry = indices[&(key.clone(), i)];
                    input.program = Some(program.clone());
                }
            }
        }
        crate::profile::count("lifetime.graph_programs", 1);
        true
    }
    pub(super) fn graph_reads(
        &mut self,
        state: &State,
        value: &Value,
        input: &Input,
    ) -> Option<Vec<Value>> {
        let program = input.program.as_ref()?;
        let mut todo = vec![(value.clone(), input.entry)];
        let mut seen = BTreeSet::new();
        let mut reads = vec![];
        while let Some((value, index)) = todo.pop() {
            let instruction = program.states.get(index)?;
            if !instruction.read && instruction.edges.is_empty() {
                continue;
            }
            // Root parameter graphs are borrowed preconditions. Their finite
            // synthetic prefix does not turn the unexpanded tail into an
            // opaque call result. Every concrete caller is checked separately.
            if value.caller_tail && value.unknown && !value.has_refs() && value.fields.is_empty() {
                continue;
            }
            if value.unknown || !value.fields.is_empty() {
                return None;
            }
            let Type::Pointer(inner) = base(&instruction.typ) else {
                return None;
            };
            let fields = self.r.record_fields(inner);
            for place in &value.refs {
                if !seen.insert((place.clone(), index)) {
                    continue;
                }
                if seen.len() > MAX_READS {
                    return None;
                }
                let contents = state.read(place);
                if contents.caller_tail && contents.unknown && !contents.has_refs() {
                    if instruction.read {
                        reads.push(Value::reference(place.clone()));
                    }
                    continue;
                }
                if contents.unknown || !contents.refs.is_empty() {
                    return None;
                }
                // Scalar fields may still carry addresses. A scalar result or
                // inline writer cannot discard those tracked edges.
                for (name, typ) in &fields {
                    let v = contents.child(&Key::Field(name.clone()));
                    if self.inline_type(typ, 0) && (v.has_refs() || v.unknown) {
                        return None;
                    }
                }
                if contents
                    .fields
                    .keys()
                    .any(|key| !matches!(key,Key::Field(name) if fields.iter().any(|(n,_)|n==name)))
                {
                    return None;
                }
                if instruction.read {
                    reads.push(Value::reference(place.clone()));
                }
                for (path, target) in &instruction.edges {
                    let mut value = Value::reference(place.clone());
                    for name in path {
                        if value.unknown {
                            return None;
                        }
                        let mut next = Value::default();
                        for pointer in &value.refs {
                            let item = state.read(pointer).child(&Key::Field(name.clone()));
                            next.merge(&item);
                        }
                        value = next;
                    }
                    todo.push((value, *target));
                }
            }
        }
        crate::profile::count("lifetime.graph_summary_reads", reads.len() as u64);
        Some(reads)
    }
}
