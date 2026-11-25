//! Definite byte extent/termination checks. Unknown contents and sizes are not
//! called invalid. Metadata is bounded independently of the ownership graph.
use super::*;
#[derive(Clone)]
pub(super) enum Argument {
    Parameter(usize, i128, Option<i128>),
    Local(i128, bool),
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Bytes {
    pub len: i128,
    pub default: Option<u8>,
    pub writes: BTreeMap<i128, Option<u8>>,
}
impl Bytes {
    pub fn new(len: i128, default: Option<u8>) -> Self {
        Self {
            len,
            default,
            writes: BTreeMap::new(),
        }
    }
    fn get(&self, i: i128) -> Option<u8> {
        self.writes.get(&i).copied().unwrap_or(self.default)
    }
    pub fn join(&mut self, other: &Self) -> bool {
        if self.len != other.len {
            return false;
        }
        let keys = self
            .writes
            .keys()
            .chain(other.writes.keys())
            .copied()
            .collect::<BTreeSet<_>>();
        let default = if self.default == other.default {
            self.default
        } else {
            None
        };
        let mut writes = BTreeMap::new();
        for i in keys {
            let a = self.get(i);
            let b = other.get(i);
            let v = if a == b { a } else { None };
            if v != default {
                writes.insert(i, v);
            }
        }
        self.default = default;
        self.writes = writes;
        if self.writes.len() > 64 {
            self.default = None;
            self.writes.clear();
        }
        true
    }
    pub(super) fn forget(&mut self) {
        self.default = None;
        self.writes.clear();
    }
}
impl State {
    pub(super) fn write_bytes(&mut self, p: &Place, byte: Option<u8>, weak: bool) {
        for (base, b) in &mut self.buffers {
            if base.id != p.id {
                continue;
            }
            if p.path.starts_with(&base.path) {
                let tail = &p.path[base.path.len()..];
                if let [Key::Item(Some(i))] = tail {
                    let v = if weak { None } else { byte };
                    b.writes.insert(*i, v);
                    if b.writes.len() > 64 {
                        b.forget();
                    }
                } else {
                    b.forget();
                }
            } else if base.path.starts_with(&p.path) {
                b.forget();
            }
        }
    }
}
impl Analysis<'_, '_> {
    pub(super) fn cstring_type(t: &Type) -> bool {
        matches!(base(t),Type::String{name,..}|Type::Scalar{name,..}if name=="cstring")
    }
    pub(super) fn literal_buffer(&self, state: &mut State, place: Place, len: i128, v: &Value) {
        let mut b = Bytes::new(len, Some(0));
        for (key, item) in &v.fields {
            if let Key::Item(Some(i)) = key {
                b.writes.insert(*i, item.byte);
            }
        }
        if b.writes.len() > 64 {
            b.forget();
        }
        state.buffers.insert(place, b);
    }
    pub(super) fn buffer_argument(
        &mut self,
        at: Ref,
        f: &Frame,
        proof: &summaries::Proof,
    ) -> Option<Argument> {
        if matches!(self.kind(at), "FieldValue" | "Paren") {
            return self
                .role(
                    at,
                    if self.kind(at) == "FieldValue" {
                        "value"
                    } else {
                        "expr"
                    },
                )
                .and_then(|n| self.buffer_argument(n, f, proof));
        }
        if self.kind(at) == "Slice" {
            let expr = self.role(at, "expr")?;
            let arg = self.buffer_argument(expr, f, proof)?;
            let low = if let Some(n) = self.role(at, "low") {
                match self.r.constant_value(n, &f.bindings, None) {
                    Some(Constant::Integer(n)) if n >= 0 => n,
                    _ => return None,
                }
            } else {
                0
            };
            let high = if let Some(n) = self.role(at, "high") {
                match self.r.constant_value(n, &f.bindings, None) {
                    Some(Constant::Integer(n)) if n >= low => Some(n),
                    _ => return None,
                }
            } else {
                None
            };
            return match arg {
                Argument::Parameter(i, offset, limit) => {
                    let end = if let Some(n) = high {
                        Some(offset.checked_add(n)?)
                    } else {
                        limit
                    };
                    Some(Argument::Parameter(i, offset.checked_add(low)?, end))
                }
                Argument::Local(n, _) => Some(Argument::Local(
                    high.unwrap_or(n).min(n).checked_sub(low)?,
                    false,
                )),
            };
        }
        if let Some(i) = self.summary_parameter(at, proof) {
            return Some(Argument::Parameter(i, 0, None));
        }
        if let Some(Constant::Text(s)) = self.r.constant_value(at, &f.bindings, None) {
            return Some(Argument::Local(s.len() as i128 + 1, true));
        }
        let typ = self.typ(at, f);
        if let Some(n) = self.buffer_shape(&typ) {
            return Some(Argument::Local(n, false));
        }
        let symbol = self.r.symbol(at)?;
        let symbol = &self.r.symbols[symbol];
        let init = Ref {
            file: symbol.reference.file,
            node: symbol.value?,
        };
        if !proof.scratch_allocations.contains(&init) {
            return None;
        }
        let t = self.typ(init, f);
        match base(&t) {
            Type::Pointer(inner) => self
                .buffer_shape(inner)
                .or_else(|| Self::byte_element(inner).then_some(1))
                .map(|n| Argument::Local(n, false)),
            Type::Slice(elem) | Type::Dynamic(elem) if Self::byte_element(elem) => {
                let args = self.group(init, "args");
                let i = self.argument(&args, "len", 1)?;
                match self.r.constant_value(args[i], &f.bindings, None) {
                    Some(Constant::Integer(n)) => Some(Argument::Local(n, false)),
                    _ => None,
                }
            }
            _ => None,
        }
    }
    fn add_buffer_requirement(
        summary: &mut summaries::Summary,
        arg: &Argument,
        bytes: i128,
        nul: bool,
    ) -> bool {
        match arg {
            Argument::Parameter(i, low, high) => {
                if nul && *low != 0 || high.is_some_and(|n| n - low < bytes) {
                    return false;
                }
                let Some(needed) = low.checked_add(bytes) else {
                    return false;
                };
                if needed > 1_048_576 {
                    return false;
                }
                summary.bytes[*i] = summary.bytes[*i].max(needed);
                summary.nuls[*i] |= nul;
                true
            }
            Argument::Local(n, terminated) => *n >= bytes && (!nul || *terminated),
        }
    }
    pub(super) fn summary_buffer(
        &mut self,
        at: Ref,
        f: &Frame,
        proof: &mut summaries::Proof,
        bytes: i128,
        nul: bool,
    ) -> bool {
        self.buffer_argument(at, f, proof)
            .is_some_and(|arg| Self::add_buffer_requirement(&mut proof.summary, &arg, bytes, nul))
    }
    pub(super) fn close_buffer_requirements(
        &self,
        component: &mut HashMap<(Ref, Substitutions), summaries::Proof>,
    ) -> bool {
        for _ in 0..128 {
            let mut changes = vec![];
            for (key, proof) in &*component {
                for (callee, mapping) in &proof.buffer_edges {
                    let Some(target) = component
                        .get(callee)
                        .map(|p| &p.summary)
                        .or_else(|| self.summaries.entries.get(callee).and_then(Option::as_ref))
                    else {
                        continue;
                    };
                    for (i, bytes) in target.bytes.iter().enumerate() {
                        let nul = target.nuls[i];
                        if *bytes == 0 && !nul {
                            continue;
                        }
                        let Some(arg) = mapping.get(i).and_then(Option::as_ref) else {
                            return false;
                        };
                        let mut test = proof.summary.clone();
                        if !Self::add_buffer_requirement(&mut test, arg, *bytes, nul) {
                            return false;
                        }
                        if test.bytes != proof.summary.bytes || test.nuls != proof.summary.nuls {
                            changes.push((key.clone(), arg.clone(), *bytes, nul));
                        }
                    }
                }
            }
            if changes.is_empty() {
                return true;
            }
            for (key, arg, bytes, nul) in changes {
                if !Self::add_buffer_requirement(
                    &mut component.get_mut(&key).unwrap().summary,
                    &arg,
                    bytes,
                    nul,
                ) {
                    return false;
                }
            }
        }
        false
    }
    pub(super) fn byte_element(t: &Type) -> bool {
        matches!(base(t),Type::Scalar{name,..}if matches!(name.as_str(),"u8"|"byte"))
    }
    fn buffer_shape(&mut self, t: &Type) -> Option<i128> {
        match base(t) {
            Type::Array {
                elem,
                length: Some(n),
            }
            | Type::Fixed {
                elem,
                length: Some(n),
            } if Self::byte_element(elem) => Some(*n),
            _ => None,
        }
    }
    pub(super) fn bind_buffer(
        &mut self,
        state: &mut State,
        f: &Frame,
        name: Ref,
        place: Place,
        declaration: Option<NodeId>,
    ) {
        let typ = self.typ(name, f);
        let Some(len) = self.buffer_shape(&typ) else {
            return;
        };
        if state.buffers.contains_key(&place)
            && matches!(self.objects[place.id].storage, Storage::Static)
        {
            return;
        }
        let values = declaration
            .map(|n| self.group(Ref { node: n, ..name }, "values"))
            .unwrap_or_default();
        let initialized = declaration.is_some()
            && (values.is_empty() || values.len() == 1 && self.kind(values[0]) == "Compound");
        let mut b = Bytes::new(len, initialized.then_some(0));
        for (key, v) in state.read(&place).fields {
            if let Key::Item(Some(i)) = key {
                b.writes.insert(i, v.byte);
            }
        }
        if b.writes.len() > 64 {
            b.forget();
        }
        state.buffers.insert(place, b);
    }
    pub(super) fn allocation_buffer(
        &mut self,
        state: &mut State,
        f: &Frame,
        at: Ref,
        value: &Value,
        args: &[Ref],
        name: &str,
    ) {
        let typ = self.typ(at, f);
        let len = match base(&typ) {
            Type::Pointer(inner) => self
                .buffer_shape(inner)
                .or_else(|| Self::byte_element(inner).then_some(1)),
            Type::Slice(elem) | Type::Dynamic(elem) if Self::byte_element(elem) => {
                let pos = self.argument(args, "len", 1);
                pos.and_then(|i| {
                    match self.r.constant_value(
                        self.role(args[i], "value").unwrap_or(args[i]),
                        &f.bindings,
                        None,
                    ) {
                        Some(Constant::Integer(n)) if n >= 0 => Some(n),
                        _ => None,
                    }
                })
            }
            _ => None,
        };
        let Some(mut len) = len else { return };
        let initial = len;
        if matches!(base(&typ), Type::Dynamic(_)) {
            if let Some(i) = self.argument(args, "cap", 2) {
                let Some(Constant::Integer(cap)) = self.r.constant_value(
                    self.role(args[i], "value").unwrap_or(args[i]),
                    &f.bindings,
                    None,
                ) else {
                    return;
                };
                if cap < len {
                    return;
                }
                len = cap;
            }
        }
        if !matches!(name, "make" | "new") {
            return;
        }
        for p in &value.refs {
            let mut b = Bytes::new(len, if len == initial { Some(0) } else { None });
            if len != initial && initial <= 64 {
                for i in 0..initial {
                    b.writes.insert(i, Some(0));
                }
            }
            state.buffers.insert(p.clone(), b);
        }
    }
    pub(super) fn buffer_span(
        &self,
        state: &State,
        p: &Place,
        logical: bool,
    ) -> Option<(Place, i128, i128)> {
        for (base,b) in state.buffers.range(Place::root(p.id)..){
            if base.id!=p.id{break;}
            if !p.path.starts_with(&base.path){continue;}
            let mut offset=0i128;let mut stride=1i128;
            for key in &p.path[base.path.len()..]{match key{
                Key::View(width)=>stride=*width,
                Key::Byte(Some(n))=>offset=offset.checked_add(*n)?,
                Key::Item(Some(n))if *n>=0=>offset=offset.checked_add(n.checked_mul(stride)?)?,
                _=>return None,
            }}
            // Relative Thor indices require a known logical length; they are
            // not negative raw offsets. Explicit byte offsets remain physical.
            if p.start?<0{return None;}
            let end=if logical{p.end.map(|n|offset.checked_add(n.checked_mul(stride)?)).flatten().unwrap_or(b.len).min(b.len)}else{b.len};
            offset=offset.checked_add(p.start?.checked_mul(stride)?)?;
            if offset<0||end<0{return None;}
            return Some((base.clone(),offset,end));
        }
        None
    }
    pub(super) fn check_bytes(
        &mut self,
        state: &State,
        v: &Value,
        count: i128,
        at: Ref,
        logical: bool,
    ) {
        if count == 0 {
            return;
        }
        if count < 0 {
            self.report(
                at,
                None,
                true,
                "lifetime",
                "buffer operation has a negative byte count",
            );
            return;
        }
        if v.unknown || v.refs.is_empty() {
            return;
        }
        let mut bad = None;
        for p in &v.refs {
            if state.cells.get(&p.id).is_some_and(|c|c.life&DEAD!=0){return;}
            let Some((_, start, end)) = self.buffer_span(state, p, logical).or_else(||self.raw_span(state,p,logical)) else {
                return;
            };
            if start >= 0 && start.checked_add(count).is_some_and(|n| n <= end) {
                return;
            }
            bad = Some(p.id);
        }
        self.report(
            at,
            bad,
            true,
            "lifetime",
            "buffer operation definitely exceeds the available byte extent",
        );
    }
    pub(super) fn check_termination(&mut self, state: &State, v: &Value, at: Ref) {
        if v.unknown || v.refs.is_empty() {
            return;
        }
        let mut bad = None;
        for p in &v.refs {
            if state.cells.get(&p.id).is_some_and(|c|c.life&DEAD!=0){return;}
            let Some((base, start, end)) = self.buffer_span(state, p, false) else {
                return;
            };
            let b = &state.buffers[&base];
            if start < 0 || start > end {
                return;
            }
            // A default nonzero byte proves long buffers without scanning them.
            let nonzero = if b.default.is_some_and(|n| n != 0) {
                b.writes
                    .range(start..end)
                    .all(|(_, v)| v.is_some_and(|n| n != 0))
            } else if end - start <= 64 {
                (start..end).all(|i| b.get(i).is_some_and(|n| n != 0))
            } else {
                false
            };
            if !nonzero {
                return;
            }
            bad = Some(p.id);
        }
        self.report(
            at,
            bad,
            true,
            "lifetime",
            "C-string definitely has no zero terminator within its backing storage",
        );
    }
    pub(super) fn forget_buffer_contents(state: &mut State, v: &Value) {
        for (place, b) in &mut state.buffers {
            if v.refs.iter().any(|p| p.id == place.id) {
                b.forget();
            }
        }
    }
    pub(super) fn forget_buffer_extent(state: &mut State, v: &Value) {
        state.regions.retain(|place,_|!v.refs.iter().any(|p|p.id==place.id));
        state
            .buffers
            .retain(|place, _| !v.refs.iter().any(|p| p.id == place.id));
    }
    pub(super) fn buffer_call(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        args: &[Ref],
        vs: &[Value],
        builtin: Option<&str>,
        lib: &str,
        name: &str,
    ) -> Option<Vec<Value>> {
        if matches!(
            contracts::effect(lib, name),
            Some(contracts::Effect::Format)
        ) && name.starts_with('b')
        {
            if let Some(i) = self.argument(args, "buf", 0) {
                Self::forget_buffer_contents(state, &vs[i]);
            }
        }
        if builtin == Some("raw_data") {
            return Some(vec![vs.first().cloned().unwrap_or_default()]);
        }
        if lib == "core:mem" && name == "alloc" {
            let i = self.argument(args, "size", 0)?;
            let len = match self.r.constant_value(
                self.role(args[i], "value").unwrap_or(args[i]),
                &f.bindings,
                None,
            ) {
                Some(Constant::Integer(n)) if n >= 0 => Some(n),
                _ => None,
            };
            let allocator = self
                .argument(args, "allocator", usize::MAX)
                .map(|i| vs[i].allocator.clone().unwrap_or(Allocator::Unknown))
                .unwrap_or_else(|| state.allocator.clone());
            let mut v = self.allocate(state, f, at, allocator, Value::default());
            v.may_nil = true;
            for p in &v.refs {
                if let Some(n) = len {
                    state.buffers.insert(p.clone(), Bytes::new(n, None));
                }
                state.cells.get_mut(&p.id).unwrap().life |= ABSENT;
            }
            return Some(vec![v, Value::default()]);
        }
        if builtin == Some("len")
            && args
                .first()
                .is_some_and(|a| Self::cstring_type(&self.typ(*a, f)))
        {
            self.direct(state, &vs[0], at);
            self.check_termination(state, &vs[0], at);
            return Some(vec![Value::default()]);
        }
        if matches!(
            contracts::effect(lib, name),
            Some(contracts::Effect::Endian)
        ) {
            let width = if name.contains("16") {
                2
            } else if name.contains("32") {
                4
            } else {
                8
            };
            if let Some(i) = self.argument(args, "b", 0) {
                self.check_bytes(state, &vs[i], width, at, true);
            }
        }
        if lib == "vendor:raylib"
            && self.raylib_six
            && matches!(
                name,
                "DrawText" | "MeasureText" | "InitWindow" | "SetWindowTitle" | "ExportImage"
            )
        {
            let (param, pos) = match name {
                "InitWindow" => ("title", 2),
                "SetWindowTitle" => ("title", 0),
                "ExportImage" => ("fileName", 1),
                _ => ("text", 0),
            };
            if let Some(i) = self.argument(args, param, pos) {
                self.check_termination(state, &vs[i], at);
            }
        }
        if lib != "core:mem" || !matches!(name, "copy" | "copy_non_overlapping" | "zero" | "set") {
            return None;
        }
        let pos = if matches!(name, "copy" | "copy_non_overlapping" | "set") {
            2
        } else {
            1
        };
        let n = self.argument(args, "len", pos).and_then(|i| {
            match self.r.constant_value(
                self.role(args[i], "value").unwrap_or(args[i]),
                &f.bindings,
                None,
            ) {
                Some(Constant::Integer(n)) => Some(n),
                _ => None,
            }
        });
        let dst = self.argument(
            args,
            if name.starts_with("copy") {
                "dst"
            } else {
                "data"
            },
            0,
        )?;
        if n.is_some_and(|n| n > 0)
            && self
                .r
                .native_builtin(self.role(args[dst], "value").unwrap_or(args[dst]), "nil")
        {
            self.report(
                at,
                None,
                true,
                "lifetime",
                "nonempty byte operation uses a nil destination buffer",
            );
        }
        if let Some(n) = n {
            self.check_bytes(state, &vs[dst], n, at, false);
        }
        if n != Some(0) {
            self.direct(state, &vs[dst], at);
        }
        let source = if name.starts_with("copy") {
            self.argument(args, "src", 1)
        } else {
            None
        };
        if n.is_some_and(|n| n > 0)
            && source.is_some_and(|i| {
                self.r
                    .native_builtin(self.role(args[i], "value").unwrap_or(args[i]), "nil")
            })
        {
            self.report(
                at,
                None,
                true,
                "lifetime",
                "nonempty byte operation uses a nil source buffer",
            );
        }
        if let Some(i) = source {
            if let Some(n) = n {
                self.check_bytes(state, &vs[i], n, at, false);
                // Byte transport preserves uninitialized ranges below; it
                // does not interpret them as initialized typed values.
            }
            if n != Some(0) {
                self.direct(state, &vs[i], at);
            }
        }
        if let Some(i)=source{self.memory_copy(state,&vs[dst],&vs[i],n);}else{self.memory_written(state,&vs[dst],n);}
        if name == "copy_non_overlapping" && n.is_some_and(|n| n > 0) {
            if let Some(i) = source {
                if !vs[dst].unknown
                    && !vs[i].unknown
                    && vs[dst].refs.len() == 1
                    && vs[i].refs.len() == 1
                {
                    let dst_place=vs[dst].refs.first().unwrap();let src_place=vs[i].refs.first().unwrap();
                    let a = self.buffer_span(state,dst_place,false).or_else(||self.raw_span(state,dst_place,false));
                    let b = self.buffer_span(state,src_place,false).or_else(||self.raw_span(state,src_place,false));
                    if let (Some((a, x, _)), Some((b, y, _))) = (a, b) {
                        let n = n.unwrap();
                        if a == b
                            && x.checked_add(n).is_some_and(|end| y < end)
                            && y.checked_add(n).is_some_and(|end| x < end)
                        {
                            self.report(
                                at,
                                Some(a.id),
                                true,
                                "lifetime",
                                "non-overlapping byte copy definitely uses overlapping storage",
                            );
                        }
                    }
                }
            }
        }
        let copied = source.and_then(|i| {
            let n = n.filter(|n| *n >= 0 && *n <= 64)?;
            if vs[i].unknown || vs[i].refs.len() != 1 {
                return None;
            }
            let p = vs[i].refs.first()?;
            let (base, start, end) = self.buffer_span(state, p, false)?;
            if start < 0 || start + n > end {
                return None;
            }
            Some(
                (0..n)
                    .map(|j| {
                        let mut v = state.read(&p.child(Key::Item(Some(j))));
                        v.byte = state.buffers[&base].get(start + j);
                        if v.has_refs() {
                            v.invalidate();
                        }
                        v
                    })
                    .collect::<Vec<_>>(),
            )
        });
        let fill = if name == "zero" {
            Some(0)
        } else if name == "set" {
            self.argument(args, "value", 1).and_then(|i| vs[i].byte)
        } else {
            None
        };
        for p in &vs[dst].refs {
            let Some((base, start, end)) = self.buffer_span(state, p, false) else {
                continue;
            };
            let b = state.buffers.get_mut(&base).unwrap();
            if let Some(n) = n.filter(|n| {
                *n >= 0 && start >= 0 && start.checked_add(*n).is_some_and(|x| x <= end)
            }) {
                if start == 0 && n == b.len && source.is_none() {
                    b.default = fill;
                    b.writes.clear();
                } else if n <= 64 {
                    for i in start..start + n {
                        b.writes
                            .insert(i, if source.is_none() { fill } else { None });
                    }
                } else {
                    b.forget();
                }
            } else {
                b.forget();
            }
        }
        // Byte copies retain any encoded addresses; opaque extents are never
        // used to establish an ownership transfer through overwritten bytes.
        if let Some(i) = source {
            if let Some(copied) = copied {
                for p in &vs[dst].refs {
                    for (j, v) in copied.iter().enumerate() {
                        state.write(
                            &p.child(Key::Item(Some(j as i128))),
                            v,
                            vs[dst].unknown || vs[dst].refs.len() != 1,
                        );
                    }
                }
            } else {
                let mut contents = self.items(state, &vs[i], at);
                if contents.has_refs() {
                    contents.invalidate();
                }
                for p in &vs[dst].refs {
                    state.write(&p.child(Key::Item(None)), &contents, true);
                }
            }
        }
        Some(vec![vs[dst].clone()])
    }
}
