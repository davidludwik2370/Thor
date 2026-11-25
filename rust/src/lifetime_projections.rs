//! Bounded field-only contracts. Unused pointer fields are copied, not read.
//! No caller identities or values enter cached contracts.
use super::*;
impl Analysis<'_, '_> {
    pub(super) fn projectable_record(&mut self, t: &Type) -> bool {
        match base(t) {
            Type::Struct(o) | Type::Record(o, _) => {
                self.r.sources[o.file].node(o.node).op != "raw_union" && !self.inline_type(t, 0)
            }
            Type::Anonymous(_) => !self.inline_type(t, 0),
            _ => false,
        }
    }
    pub(super) fn summary_projection(
        &self,
        mut at: Ref,
        p: &summaries::Proof,
    ) -> Option<(usize, Vec<String>)> {
        let mut path = vec![];
        for _ in 0..16 {
            match self.kind(at) {
                "FieldValue" => at = self.role(at, "value")?,
                "Paren" => at = self.role(at, "expr")?,
                "Selector" => {
                    path.push(self.text(self.role(at, "field")?).to_owned());
                    at = self.role(at, "expr")?;
                }
                "Ident" => {
                    let i = *p.parameters.get(&self.symbol(at)?)?;
                    path.reverse();
                    return Some((i, path));
                }
                _ => return None,
            }
        }
        None
    }
    pub(super) fn close_projection_requirements(
        &self,
        component: &mut HashMap<(Ref, Substitutions), summaries::Proof>,
    ) -> bool {
        for _ in 0..128 {
            let mut changes = vec![];
            for (key, p) in component.iter() {
                for (target, mapping) in &p.projection_edges {
                    let Some(s) = component
                        .get(target)
                        .map(|p| &p.summary)
                        .or_else(|| self.summaries.entries.get(target).and_then(Option::as_ref))
                    else {
                        return false;
                    };
                    for (i, paths) in s.projections.iter().enumerate() {
                        let Some(Some((parameter, prefix))) = mapping.get(i) else {
                            if !paths.is_empty() {
                                return false;
                            }
                            continue;
                        };
                        if !p.summary.projected[*parameter] {
                            continue;
                        }
                        for suffix in paths {
                            let mut path = prefix.clone();
                            path.extend(suffix.iter().cloned());
                            if path.len() > 16 {
                                return false;
                            }
                            if !p.summary.projections[*parameter].contains(&path) {
                                changes.push((key.clone(), *parameter, path));
                            }
                        }
                    }
                }
            }
            if changes.is_empty() {
                return true;
            }
            for (key, i, path) in changes {
                let paths = &mut component.get_mut(&key).unwrap().summary.projections[i];
                paths.insert(path);
                if paths.len() > 32 {
                    return false;
                }
            }
        }
        false
    }
    pub(super) fn projected_reads(
        &self,
        state: &State,
        input: &Value,
        paths: &BTreeSet<Vec<String>>,
    ) -> Option<Vec<Value>> {
        let mut reads = vec![];
        for path in paths {
            let mut value = input.clone();
            for field in path {
                if value.unknown {
                    return None;
                }
                let mut next = value.child(&Key::Field(field.clone()));
                for place in &value.refs {
                    reads.push(Value::reference(place.clone()));
                    let contents = state.read(place);
                    if contents.unknown {
                        return None;
                    }
                    next.merge(&contents.child(&Key::Field(field.clone())));
                }
                value = next;
            }
            if value.unknown || value.has_refs() {
                return None;
            }
        }
        Some(reads)
    }
}
