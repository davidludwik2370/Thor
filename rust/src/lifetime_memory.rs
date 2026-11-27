//! Physical allocation metadata. Sizes use the resolver's target layout for
//! supported primitive/array types; unknown or custom record layouts are never
//! guessed. Intervals are bounded, independent of the number of allocated bytes.
use super::*;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct Number {
    pub value: Option<i128>,
    pub wrapped: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Region {
    pub bytes: i128,
    pub alignment: Option<i128>,
    pub typ: Type,
    pub raw: bool,
    // Definitely uninitialized bytes, not merely bytes with unknown contents.
    uninit: Vec<(i128, i128)>,
}
impl Region {
    pub fn new(
        bytes: i128,
        alignment: Option<i128>,
        typ: Type,
        raw: bool,
        initialized: bool,
    ) -> Self {
        Self {
            bytes,
            alignment,
            typ,
            raw,
            uninit: if initialized || bytes == 0 {
                vec![]
            } else {
                vec![(0, bytes)]
            },
        }
    }
    pub fn join(&mut self, other: &Self) -> bool {
        if self.bytes != other.bytes || self.typ != other.typ || self.raw != other.raw {
            return false;
        }
        if self.alignment != other.alignment {
            self.alignment = None;
        }
        self.uninit = self
            .uninit
            .iter()
            .flat_map(|(a, b)| {
                other.uninit.iter().filter_map(move |(c, d)| {
                    let lo = (*a).max(*c);
                    let hi = (*b).min(*d);
                    (lo < hi).then_some((lo, hi))
                })
            })
            .take(65)
            .collect();
        if self.uninit.len() > 64 {
            self.uninit.clear();
        }
        true
    }
    fn initialized(&mut self, lo: i128, hi: i128) {
        self.uninit = self
            .uninit
            .iter()
            .flat_map(|(a, b)| {
                if hi <= *a || lo >= *b {
                    return vec![(*a, *b)];
                }
                let mut parts = vec![];
                if *a < lo {
                    parts.push((*a, lo));
                }
                if hi < *b {
                    parts.push((hi, *b));
                }
                parts
            })
            .collect();
        if self.uninit.len() > 64 {
            self.uninit.clear();
        }
    }
}
impl Analysis<'_, '_> {
    pub(super) fn fit_number(&self, value: &mut Value, t: &Type) {
        let Some(fact) = &mut value.number else {
            return;
        };
        let Some(n) = fact.value else { return };
        let Type::Scalar {
            name,
            untyped: false,
        } = base(t)
        else {
            return;
        };
        let signed = name == "int" || name == "rune" || name.starts_with('i');
        if !signed && !(name == "uint" || name == "uintptr" || name.starts_with('u')) {
            value.number = None;
            return;
        }
        let Some(bits) = self
            .r
            .simple_size(t)
            .and_then(|n| u32::try_from(n * 8).ok())
        else {
            value.number = None;
            return;
        };
        if bits >= 128 {
            if !signed && n < 0 {
                fact.wrapped = true;
                fact.value = None;
            }
            return;
        }
        let modulus = 1i128 << bits;
        let wrapped = n.rem_euclid(modulus);
        let wrapped = if signed && wrapped >= modulus / 2 {
            wrapped - modulus
        } else {
            wrapped
        };
        fact.wrapped |= wrapped != n;
        fact.value = Some(wrapped);
    }
    pub(super) fn number_unary(&self, op: &str, v: &Value) -> Value {
        let number = v.number.and_then(|n| {
            let value = match (op, n.value) {
                ("+", v) => v,
                ("-", Some(v)) => v.checked_neg(),
                ("~", Some(v)) => Some(!v),
                _ => return None,
            };
            Some(Number {
                value,
                wrapped: n.wrapped || value.is_none(),
            })
        });
        Value {
            number,
            ..Value::default()
        }
    }
    pub(super) fn number_binary(&self, op: &str, a: &Value, b: &Value) -> Value {
        let number = a.number.zip(b.number).and_then(|(a, b)| {
            let value = match (a.value, b.value) {
                (Some(a), Some(b)) => match op {
                    "+" => a.checked_add(b),
                    "-" => a.checked_sub(b),
                    "*" => a.checked_mul(b),
                    "/" => a.checked_div(b),
                    "%" => a.checked_rem(b),
                    "<<" => u32::try_from(b)
                        .ok()
                        .filter(|n| *n < 127)
                        .and_then(|n| 1i128.checked_shl(n))
                        .and_then(|n| a.checked_mul(n)),
                    ">>" => u32::try_from(b).ok().and_then(|n| a.checked_shr(n)),
                    "&" => Some(a & b),
                    "|" => Some(a | b),
                    "~" => Some(a ^ b),
                    "**" => u32::try_from(b)
                        .ok()
                        .filter(|n| *n <= 128)
                        .and_then(|n| a.checked_pow(n)),
                    _ => return None,
                },
                _ => None,
            };
            Some(Number {
                value,
                wrapped: a.wrapped || b.wrapped || value.is_none(),
            })
        });
        Value {
            number,
            ..Value::default()
        }
    }
    fn check_size_math(&mut self, at: Ref, args: &[Ref], vs: &[Value], names: &[(&str, usize)]) {
        for (name, position) in names {
            if let Some(i) = self.argument(args, name, *position) {
                if vs[i].number.is_some_and(|n| n.wrapped) {
                    self.report(at,None,false,"lifetime","allocation/buffer size arithmetic wraps or narrows a known value; the intended extent cannot be verified");
                    return;
                }
            }
        }
    }
    fn memory_integer(&mut self, at: Ref, f: &Frame) -> Option<i128> {
        let at = self.role(at, "value").unwrap_or(at);
        match self.r.constant_value(at, &f.bindings, None) {
            Some(Constant::Integer(n)) => Some(n),
            _ => None,
        }
    }
    // A lower bound on alignment, only where Odin's target representation is
    // known here. In particular no packed structs, SIMD or matrix ABI guesses.
    fn memory_alignment(&self, t: &Type) -> Option<i128> {
        match base(t) {
            Type::Array { elem, .. } => self.memory_alignment(elem),
            Type::Pointer(_) | Type::Procedure(_) | Type::Function(_, _) | Type::Slice(_) | Type::String { .. } => {
                self.r.simple_size(&Type::Scalar {
                    name: "uintptr".into(),
                    untyped: false,
                })
            }
            Type::Scalar { .. } => self.r.simple_size(t).filter(|n| *n <= 8),
            _ => None,
        }
    }
    pub(super) fn allocation_region(
        &mut self,
        state: &mut State,
        f: &Frame,
        at: Ref,
        v: &Value,
        args: &[Ref],
        name: &str,
    ) {
        if !matches!(name, "make" | "new" | "new_clone") {
            return;
        }
        let t = self.typ(at, f);
        let typ = match base(&t) {
            Type::Pointer(t) => (**t).clone(),
            Type::Slice(t) | Type::Dynamic(t) => {
                let i = self.argument(args, "len", 1);
                let Some(mut n) = i.and_then(|i| self.memory_integer(args[i], f)) else {
                    return;
                };
                if matches!(base(&t), Type::Dynamic(_)) {
                    if let Some(i) = self.argument(args, "cap", 2) {
                        let Some(cap) = self.memory_integer(args[i], f) else {
                            return;
                        };
                        if cap < n {
                            return;
                        }
                        n = cap;
                    }
                }
                Type::Array {
                    elem: t.clone(),
                    length: Some(n),
                }
            }
            _ => return,
        };
        let Some(bytes) = self.r.simple_size(&typ).filter(|n| *n >= 0) else {
            return;
        };
        let alignment = self.memory_alignment(&typ);
        for p in &v.refs {
            state.regions.insert(
                p.clone(),
                Region::new(bytes, alignment, typ.clone(), false, true),
            );
        }
    }
    pub(super) fn bind_region(&mut self, state: &mut State, f: &Frame, name: Ref, place: Place) {
        let typ = self.typ(name, f);
        if !matches!(base(&typ), Type::Array { .. }) {
            return;
        }
        let Some(bytes) = self.r.simple_size(&typ) else {
            return;
        };
        let alignment = self.memory_alignment(&typ);
        state
            .regions
            .entry(place)
            .or_insert_with(|| Region::new(bytes, alignment, typ, false, true));
    }
    pub(super) fn raw_span(
        &self,
        state: &State,
        p: &Place,
        logical: bool,
    ) -> Option<(Place, i128, i128)> {
        for (base, r) in state.regions.range(Place::root(p.id)..) {
            if base.id != p.id {
                break;
            }
            if !p.path.starts_with(&base.path) {
                continue;
            }
            if r.bytes < 0 {
                return None;
            }
            let mut typ = &r.typ;
            let mut offset = 0i128;
            let mut view = None;
            for key in &p.path[base.path.len()..] {
                match key {
                    Key::View(width) => view = Some(*width),
                    Key::Byte(Some(n)) => offset = offset.checked_add(*n)?,
                    Key::Item(Some(i)) => {
                        if *i < 0 {
                            return None;
                        } // Thor indices may be relative to length.
                        typ = match base_type(typ) {
                            Type::Array { elem, .. }|Type::Vector{elem,..} => elem,
                            Type::Matrix{..}=>return None,
                            _ => typ,
                        };
                        offset = offset.checked_add(
                            i.checked_mul(view.or_else(|| self.r.simple_size(typ))?)?,
                        )?;
                    }
                    _ => return None,
                }
            }
            let stride = view.or_else(|| match base_type(typ) {
                Type::Array { elem, .. } => self.r.simple_size(elem),
                _ => self.r.simple_size(typ),
            })?;
            if p.start? < 0 {
                return None;
            }
            offset = offset.checked_add(p.start?.checked_mul(stride)?)?;
            let end = if logical {
                if let Some(end) = p.end {
                    offset
                        .checked_sub(p.start?.checked_mul(stride)?)?
                        .checked_add(end.checked_mul(stride)?)?
                        .min(r.bytes)
                } else {
                    r.bytes
                }
            } else {
                r.bytes
            };
            return Some((base.clone(), offset, end));
        }
        None
    }
    pub(super) fn memory_access(
        &mut self,
        state: &State,
        ps: &[Place],
        typ: &Type,
        at: Ref,
        reading: bool,
    ) {
        let size = self.r.simple_size(typ);
        if size == Some(0) {
            return;
        }
        let raw = ps.iter().any(|p| Self::has_raw_region(state, p));
        let Some(bytes) = size.filter(|n| *n > 0) else {
            if raw {
                self.report(
                    at,
                    None,
                    false,
                    "lifetime",
                    "raw access has an unresolved or custom layout; byte extent cannot be verified",
                );
            }
            return;
        };
        if raw && ps.iter().any(|p| self.raw_span(state, p, false).is_none()) {
            self.report(at,None,false,"lifetime","raw pointer access has an unknown offset or allocation size; byte extent cannot be verified");
        }
        let value = Value {
            refs: ps.iter().cloned().collect(),
            ..Value::default()
        };
        self.check_bytes(state, &value, bytes, at, false);
        let align = self.memory_alignment(typ);
        let mut misaligned = None;
        for p in ps {
            let Some((root, offset, _)) = self.raw_span(state, p, false) else {
                misaligned = None;
                break;
            };
            let region = &state.regions[&root];
            if align
                .zip(region.alignment)
                .is_some_and(|(need, base)| base % need == 0 && offset.rem_euclid(need) != 0)
            {
                misaligned = Some(p.id)
            } else {
                misaligned = None;
                break;
            }
        }
        if let Some(id) = misaligned {
            self.report(
                at,
                Some(id),
                true,
                "lifetime",
                "typed pointer access is definitely misaligned",
            );
        }
        if reading {
            self.memory_read(state, &value, bytes, at);
        }
    }
    pub(super) fn memory_read(&mut self, state: &State, value: &Value, bytes: i128, at: Ref) {
        if bytes <= 0 || value.unknown || value.refs.is_empty() {
            return;
        }
        let mut bad = None;
        for p in &value.refs {
            if state.cells.get(&p.id).is_some_and(|c| c.life & DEAD != 0) {
                return;
            }
            let Some((root, lo, end)) = self.raw_span(state, p, false) else {
                return;
            };
            let Some(hi) = lo.checked_add(bytes).filter(|hi| lo >= 0 && *hi <= end) else {
                return;
            };
            if state.regions[&root]
                .uninit
                .iter()
                .any(|(a, b)| *a < hi && lo < *b)
            {
                bad = Some(p.id)
            } else {
                return;
            }
        }
        self.report(
            at,
            bad,
            true,
            "lifetime",
            "read includes definitely uninitialized heap bytes",
        );
    }
    pub(super) fn memory_written(&self, state: &mut State, value: &Value, count: Option<i128>) {
        if value.unknown || value.refs.len() != 1 {
            return;
        }
        let Some((root, lo, end)) = value
            .refs
            .first()
            .and_then(|p| self.raw_span(state, p, false))
        else {
            return;
        };
        let region = state.regions.get_mut(&root).unwrap();
        if let Some(hi) = count
            .and_then(|n| lo.checked_add(n))
            .filter(|hi| lo >= 0 && *hi >= lo && *hi <= end)
        {
            region.initialized(lo, hi);
        } else {
            region.uninit.clear();
        } // Unknown effects cannot prove uninitialized.
    }
    pub(super) fn memory_copy(
        &self,
        state: &mut State,
        dst: &Value,
        src: &Value,
        count: Option<i128>,
    ) {
        let snapshot = if !src.unknown && src.refs.len() == 1 {
            src.refs
                .first()
                .and_then(|p| self.raw_span(state, p, false))
                .zip(count)
                .and_then(|((root, lo, end), n)| {
                    let hi = lo
                        .checked_add(n)
                        .filter(|hi| lo >= 0 && n >= 0 && *hi <= end)?;
                    Some(
                        state.regions[&root]
                            .uninit
                            .iter()
                            .filter_map(|(a, b)| {
                                let a = (*a).max(lo);
                                let b = (*b).min(hi);
                                (a < b).then_some((a - lo, b - lo))
                            })
                            .collect::<Vec<_>>(),
                    )
                })
        } else {
            None
        };
        self.memory_written(state, dst, count);
        if let Some(parts) = snapshot {
            if !dst.unknown && dst.refs.len() == 1 {
                if let Some((root, lo, end)) = dst
                    .refs
                    .first()
                    .and_then(|p| self.raw_span(state, p, false))
                {
                    let r = state.regions.get_mut(&root).unwrap();
                    for (a, b) in parts {
                        if let (Some(a), Some(b)) = (lo.checked_add(a), lo.checked_add(b)) {
                            if a >= 0 && b <= end {
                                r.uninit.push((a, b));
                            }
                        }
                    }
                    r.uninit.sort_unstable();
                    if r.uninit.len() > 64 {
                        r.uninit.clear();
                    }
                }
            }
        }
    }
    pub(super) fn raw_summary_input(&self, state: &State, v: &Value) -> bool {
        v.refs.iter().any(|p| Self::has_raw_region(state, p))
    }
    pub(super) fn has_raw_region(state: &State, p: &Place) -> bool {
        state
            .regions
            .range(Place::root(p.id)..)
            .take_while(|(root, _)| root.id == p.id)
            .any(|(_, r)| r.raw)
    }
    pub(super) fn forget_memory(state: &mut State, id: Id) {
        state.regions.retain(|p, _| p.id != id);
    }
    pub(super) fn memory_call(
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
        if lib == "base:intrinsics" && crate::semantics::atomic_intrinsic(name) {
            if matches!(
                name,
                "atomic_thread_fence" | "atomic_signal_fence" | "atomic_type_is_lock_free"
            ) {
                return Some(vec![Value::default()]);
            }
            let i = self.argument(args, "dst", 0)?;
            let t = self.typ(self.role(args[i], "value").unwrap_or(args[i]), f);
            let Type::Pointer(elem) = base(&t) else {
                return None;
            };
            let reading = !name.starts_with("atomic_store");
            let places = vs[i].refs.iter().cloned().collect::<Vec<_>>();
            self.direct(state, &vs[i], at);
            self.memory_access(state, &places, elem, at, reading);
            let mut old = self.read_many(state, &places, at, vs[i].unknown);
            old.forget_scalar_facts();
            if old.has_refs() {
                old.unknown = true;
            }
            if !name.starts_with("atomic_load") {
                let compare = name.starts_with("atomic_compare_exchange");
                let j = self.argument(
                    args,
                    if compare { "new" } else { "val" },
                    if compare { 2 } else { 1 },
                )?;
                let mut value = if name.starts_with("atomic_store")
                    || name.starts_with("atomic_exchange")
                    || compare
                {
                    vs[j].clone()
                } else {
                    old.clone()
                };
                value.forget_scalar_facts();
                if value.has_refs() {
                    value.unknown = true;
                }
                for p in &places {
                    state.write(p, &value, compare || vs[i].unknown || places.len() != 1);
                }
                if !compare {
                    self.memory_written(state, &vs[i], self.r.simple_size(elem));
                }
            }
            crate::profile::count("lifetime.atomic_effects", 1);
            return Some(if name.starts_with("atomic_store") {
                vec![]
            } else if name.starts_with("atomic_compare_exchange") {
                vec![old, Value::default()]
            } else {
                vec![old]
            });
        }
        if builtin == Some("make") {
            self.check_size_math(at, args, vs, &[("len", 1), ("cap", 2)]);
        }
        if matches!(lib, "core:mem" | "base:runtime")
            && matches!(
                name,
                "alloc"
                    | "alloc_bytes"
                    | "alloc_bytes_non_zeroed"
                    | "mem_alloc"
                    | "mem_alloc_non_zeroed"
            )
        {
            self.check_size_math(at, args, vs, &[("size", 0)]);
        }
        if lib == "core:mem"
            && matches!(
                name,
                "slice_ptr"
                    | "byte_slice"
                    | "ptr_to_bytes"
                    | "copy"
                    | "copy_non_overlapping"
                    | "zero"
                    | "set"
            )
        {
            self.check_size_math(
                at,
                args,
                vs,
                &[(
                    "len",
                    if matches!(name, "copy" | "copy_non_overlapping" | "set") {
                        2
                    } else {
                        1
                    },
                )],
            );
        }
        if lib == "core:mem" && matches!(name, "slice_ptr" | "byte_slice" | "ptr_to_bytes") {
            let i = self.argument(args, if name == "byte_slice" { "data" } else { "ptr" }, 0)?;
            let count = self
                .argument(args, "len", 1)
                .and_then(|i| self.memory_integer(args[i], f))
                .or_else(|| (name == "ptr_to_bytes").then_some(1));
            let t = self.typ(self.role(args[i], "value").unwrap_or(args[i]), f);
            let size = if name == "byte_slice" {
                Some(1)
            } else {
                if let Type::Pointer(t) = base(&t) {
                    self.r.simple_size(t)
                } else {
                    None
                }
            };
            if let Some(bytes) = count.zip(size).and_then(|(a, b)| a.checked_mul(b)) {
                self.check_bytes(
                    state,
                    &vs[i],
                    if name == "byte_slice" {
                        bytes.max(0)
                    } else {
                        bytes
                    },
                    at,
                    false,
                );
            }
            // These construct borrowed headers, never independent allocations.
            let mut v = vs[i].clone();
            if let Some(width) = if name == "slice_ptr" { size } else { Some(1) } {
                v.refs = v
                    .refs
                    .into_iter()
                    .map(|p| p.child(Key::View(width)))
                    .collect();
            }
            v.bytes_len = count.zip(size).and_then(|(a, b)| a.max(0).checked_mul(b));
            return Some(vec![v]);
        }
        if lib == "core:fmt"
            && matches!(
                name,
                "tprint"
                    | "tprintln"
                    | "tprintf"
                    | "tprintfln"
                    | "ctprint"
                    | "ctprintf"
                    | "ctprintfln"
            )
            && !self.custom_formatting
        {
            for (a, v) in args.iter().zip(vs) {
                let t = self.typ(self.role(*a, "value").unwrap_or(*a), f);
                self.consume(state, v, at, &t, 0);
            }
            let mut result =
                self.allocate(state, f, at, state.temp_allocator.clone(), Value::default());
            result.may_nil = true;
            for p in &result.refs {
                state.cells.get_mut(&p.id).unwrap().life |= ABSENT;
            }
            crate::profile::count("lifetime.temporary_contracts", 1);
            return Some(vec![result]);
        }
        if builtin == Some("free_all")
            || matches!(
                (lib, name),
                ("core:mem", "free_all") | ("base:runtime", "mem_free_all")
            )
        {
            let allocator = self
                .argument(args, "allocator", 0)
                .map(|i| vs[i].allocator.clone().unwrap_or(Allocator::Unknown))
                .unwrap_or_else(|| state.allocator.clone());
            match &allocator {
                Allocator::Region(p) => self.end_region(state, p, at, true),
                Allocator::Temporary(_) => {
                    if !state.workers.is_empty() {
                        let affected=Value{refs:state.cells.keys().filter(|id|matches!(&self.objects[**id].storage,Storage::Heap(a)if a==&allocator)).map(|id|Place::root(*id)).collect(),..Value::default()};
                        self.check_worker_release(state, &affected, at);
                    }
                    for (id, c) in &mut state.cells {
                        if matches!(&self.objects[*id].storage,Storage::Heap(a)if a==&allocator) {
                            c.life = (c.life & ABSENT) | DEAD;
                            c.released = Some(at);
                            c.uncertain = false;
                        }
                    }
                }
                _ => return None,
            }
            crate::profile::count("lifetime.temporary_resets", 1);
            return Some(vec![Value::default()]);
        }
        if (lib == "base:intrinsics" || lib == "core:mem" || builtin == Some("ptr_offset"))
            && name == "ptr_offset"
        {
            let i = self.argument(args, "ptr", 0)?;
            let j = self.argument(args, "offset", 1)?;
            let t = self.typ(self.role(args[i], "value").unwrap_or(args[i]), f);
            let stride = if let Type::Pointer(t) = base(&t) {
                self.r.simple_size(t)
            } else {
                None
            };
            let offset = self
                .memory_integer(args[j], f)
                .zip(stride)
                .and_then(|(a, b)| a.checked_mul(b));
            let mut v = vs[i].clone();
            v.refs = v
                .refs
                .into_iter()
                .map(|p| p.child(Key::Byte(offset)))
                .collect();
            return Some(vec![v]);
        }
        if lib == "core:mem" && matches!(name, "alloc" | "alloc_bytes" | "alloc_bytes_non_zeroed")
            || lib == "base:runtime" && matches!(name, "mem_alloc" | "mem_alloc_non_zeroed")
        {
            let i = self.argument(args, "size", 0)?;
            let bytes = vs[i]
                .number
                .and_then(|n| n.value)
                .or_else(|| self.memory_integer(args[i], f));
            if bytes.is_some_and(|n| n <= 0) {
                return Some(vec![Value::default(), Value::default()]);
            }
            let alignment = self
                .argument(args, "alignment", 1)
                .and_then(|i| self.memory_integer(args[i], f));
            let alignment = if self.argument(args, "alignment", 1).is_none() {
                self.r
                    .simple_size(&Type::Scalar {
                        name: "uintptr".into(),
                        untyped: false,
                    })
                    .and_then(|n| n.checked_mul(2))
            } else {
                alignment
            };
            let allocator = self
                .argument(args, "allocator", 2)
                .map(|i| vs[i].allocator.clone().unwrap_or(Allocator::Unknown))
                .unwrap_or_else(|| state.allocator.clone());
            let mut v = self.allocate(state, f, at, allocator, Value::default());
            v.may_nil = true;
            v.bytes_len = bytes;
            for p in &v.refs {
                state.cells.get_mut(&p.id).unwrap().life |= ABSENT;
                if let Some(n) = bytes.filter(|n| *n >= 0) {
                    let init = !name.ends_with("non_zeroed");
                    state.regions.insert(
                        p.clone(),
                        Region::new(
                            n,
                            alignment,
                            Type::Array {
                                elem: Box::new(Type::Scalar {
                                    name: "u8".into(),
                                    untyped: false,
                                }),
                                length: Some(n),
                            },
                            true,
                            init,
                        ),
                    );
                    state
                        .buffers
                        .insert(p.clone(), buffers::Bytes::new(n, init.then_some(0)));
                } else {
                    state.regions.insert(
                        p.clone(),
                        Region::new(-1, alignment, Type::Unknown, true, true),
                    );
                }
            }
            return Some(vec![v, Value::default()]);
        }
        if matches!(
            (lib, name),
            ("core:mem", "free_with_size") | ("base:runtime", "mem_free_with_size")
        ) {
            if let (Some(i), Some(j)) = (
                self.argument(args, "ptr", 0),
                self.argument(args, "size", 1),
            ) {
                if let Some(size) = self.memory_integer(args[j], f).filter(|n| *n != 0) {
                    if !vs[i].unknown && vs[i].refs.len() == 1 {
                        let p = vs[i].refs.first().unwrap();
                        if let Some((root, offset, end)) = self.raw_span(state, p, false) {
                            if offset == 0 && size != end {
                                self.report(
                                    at,
                                    Some(root.id),
                                    true,
                                    "lifetime",
                                    "cleanup byte size does not match the original allocation size",
                                );
                            }
                        }
                    }
                }
            }
        }
        None
    }
}
fn base_type(t: &Type) -> &Type {
    base(t)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn bytes(r: &Region) -> BTreeSet<i128> {
        r.uninit.iter().flat_map(|(a, b)| *a..*b).collect()
    }
    #[test]
    fn partial_writes_and_branch_joins_match_byte_sets() {
        for len in 1..9 {
            for lo in 0..=len {
                for hi in lo..=len {
                    for other in 0..=len {
                        let mut a = Region::new(len, Some(8), Type::scalar("u8"), true, false);
                        a.initialized(lo, hi);
                        let expected = (0..len)
                            .filter(|n| *n < lo || *n >= hi)
                            .collect::<BTreeSet<_>>();
                        let actual = bytes(&a);
                        assert_eq!(actual, expected);
                        let mut b = Region::new(len, Some(8), Type::scalar("u8"), true, false);
                        b.initialized(0, other);
                        let expected = expected
                            .intersection(&bytes(&b))
                            .copied()
                            .collect::<BTreeSet<_>>();
                        let joined = a.join(&b);
                        assert!(joined);
                        let actual = bytes(&a);
                        assert_eq!(actual, expected);
                    }
                }
            }
        }
    }
    #[test]
    fn incompatible_layouts_do_not_publish_a_size_proof() {
        let mut a = Region::new(8, Some(8), Type::scalar("u64"), true, false);
        let b = Region::new(4, Some(4), Type::scalar("u32"), true, false);
        let joined = a.join(&b);
        assert!(!joined);
    }
}
