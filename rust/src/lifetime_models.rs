//! Reviewed library effects. These models never grant contracts to names in
//! user packages, and preserve element references, allocation identity and
//! independently retained views.
use super::*;

impl Analysis<'_, '_> {
    fn linear_builder(&mut self, proc: Ref, variable: Ref) -> bool {
        if let Some(answer) = self.summaries.builders.get(&variable) {
            return *answer;
        }
        let answer = self.prove_linear_builder(proc, variable);
        self.summaries.builders.insert(variable, answer);
        answer
    }
    fn prove_linear_builder(&mut self, proc: Ref, variable: Ref) -> bool {
        let Some(symbol) = self.r.symbol(variable).map(|i| self.r.symbols[i].clone()) else {
            return false;
        };
        if symbol.parameter
            || symbol.result
            || self
                .r
                .attributes(symbol.reference)
                .iter()
                .any(|(name, _)| name == "static")
        {
            return false;
        }
        if let Some(value) = symbol.value {
            let call = Ref {
                file: variable.file,
                node: value,
            };
            if !self.role(call, "callee").is_some_and(|callee| {
                crate::evaluation::library_member(self.r, callee, "core:strings")
                    .is_some_and(|name| name.starts_with("builder_make"))
                    && crate::privileges::verified_library(self.r, callee, "core:strings")
            }) {
                return false;
            }
        }
        let Some(body) = self.role(proc, "body") else {
            return false;
        };
        let nodes = self.r.sources[body.file]
            .descendants(body.node)
            .take(4097)
            .collect::<Vec<_>>();
        if nodes.len() > 4096 {
            return false;
        }
        for node in nodes {
            let at = Ref { node, ..body };
            if self.kind(at) != "Ident" || self.symbol(at) != Some(variable) {
                continue;
            }
            let s = &self.r.sources[body.file];
            let Some(parent) = s.node(node).parent else {
                return false;
            };
            if s.node(parent).kind == "Declaration" && s.group(parent, "names").contains(&node) {
                continue;
            }
            let mut parent = parent;
            if s.node(parent).kind == "Unary" && s.node(parent).op == "&" {
                let Some(p) = s.node(parent).parent else {
                    return false;
                };
                parent = p;
            }
            if s.node(parent).kind == "FieldValue" {
                let Some(p) = s.node(parent).parent else {
                    return false;
                };
                parent = p;
            }
            if s.node(parent).kind != "Call" {
                return false;
            }
            let Some(callee) = s.role(parent, "callee").map(|node| Ref { node, ..body }) else {
                return false;
            };
            let strings = crate::evaluation::library_member(self.r, callee, "core:strings")
                .is_some_and(|name| {
                    matches!(
                        name.as_str(),
                        "write_rune"
                            | "write_byte"
                            | "write_string"
                            | "to_string"
                            | "to_cstring"
                            | "unsafe_to_cstring"
                            | "builder_len"
                            | "builder_cap"
                            | "builder_space"
                            | "builder_grow"
                    )
                })
                && crate::privileges::verified_library(self.r, callee, "core:strings");
            let formatting =
                crate::evaluation::library_member(self.r, callee, "core:fmt").is_some_and(|name| {
                    matches!(
                        name.as_str(),
                        "sbprint" | "sbprintln" | "sbprintf" | "sbprintfln"
                    )
                }) && crate::privileges::verified_library(self.r, callee, "core:fmt");
            if !strings && !formatting {
                return false;
            }
        }
        true
    }
    pub(super) fn outside_reference(
        value: &Value,
        path: &[Key],
        except: &[Vec<Key>],
        id: Id,
    ) -> bool {
        if except.iter().any(|excluded| excluded.as_slice() == path) {
            return false;
        }
        value.refs.iter().any(|p| p.id == id)
            || value.fields.iter().any(|(key, v)| {
                let mut child = path.to_vec();
                child.push(key.clone());
                Self::outside_reference(v, &child, except, id)
            })
    }
    fn builder_views(
        &self,
        state: &State,
        header: &Value,
        data: &Value,
        extra: &[Value],
        frame: usize,
    ) -> bool {
        data.refs.iter().any(|p| {
            state.cells.iter().any(|(owner,cell)| {
                if cell.life & LIVE == 0 {return false;}
                let except=header.refs.iter().filter(|h| h.id==*owner)
                    .map(|h|{let mut path=h.path.clone();path.push(Key::Field("buf".into()));path}).collect::<Vec<_>>();
                Self::outside_reference(&cell.value,&[],&except,p.id)
            }) || extra.iter().any(|v|v.references(p.id))
                || state.cleanups.values().flatten().any(|pending|matches!(&pending.action,Cleanup::Procedure{inputs,..} if inputs.iter().any(|v|v.references(p.id))))
                // Caller expression temporaries are not stored in cells. A
                // buffer created in this frame cannot already be in one.
                || (!self.active.is_empty() && self.ids.get(&(frame,self.objects[p.id].origin,1))!=Some(&p.id))
        })
    }
    pub(super) fn builder_model(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        args: &[Ref],
        vs: &[Value],
        lib: &str,
        name: &str,
    ) -> Option<Vec<Value>> {
        let make = lib == "core:strings"
            && matches!(
                name,
                "builder_make" | "builder_make_none" | "builder_make_len" | "builder_make_len_cap"
            );
        if make {
            let fields = self
                .r
                .call_signature(at)
                .map(|sig| self.r.fields(sig, "params"))
                .unwrap_or_default();
            let allocator_pos = fields
                .iter()
                .position(|(name, _)| name == "allocator")
                .unwrap_or(match name {
                    "builder_make_none" => 0,
                    "builder_make_len" => 1,
                    "builder_make_len_cap" => 2,
                    _ => usize::MAX,
                });
            let allocator_arg = self
                .argument(args, "allocator", usize::MAX)
                .or_else(|| {
                    args.iter().enumerate().find_map(|(i, arg)| {
                        if self.argument_name(*arg).is_some() {
                            return None;
                        }
                        if vs[i].allocator.is_some() {
                            return Some(i);
                        }
                        let typ = self.typ(self.role(*arg, "value").unwrap_or(*arg), f);
                        let owner = match base(&typ) {
                            Type::Struct(owner) | Type::Record(owner, _) => *owner,
                            _ => return None,
                        };
                        let s = &self.r.sources[owner.file];
                        (s.info["odin_root"].as_str().is_some_and(|root| {
                            s.path == std::path::Path::new(root).join("base/runtime/core.odin")
                        }) && s.node(owner.node).parent.is_some_and(|decl| {
                            s.group(decl, "names")
                                .iter()
                                .any(|name| s.text(*name) == "Allocator")
                        }))
                        .then_some(i)
                    })
                })
                .or_else(|| self.argument(args, "allocator", allocator_pos));
            let allocator = if let Some(i) = allocator_arg {
                vs[i].allocator.clone().unwrap_or(Allocator::Unknown)
            } else {
                state.allocator.clone()
            };
            let mut empty = true;
            for (i, arg) in args.iter().enumerate() {
                if Some(i) == allocator_arg
                    || self
                        .argument_name(*arg)
                        .is_some_and(|name| matches!(self.text(name), "allocator" | "loc"))
                {
                    continue;
                }
                let actual = self.role(*arg, "value").unwrap_or(*arg);
                let constant = self.r.constant_value(actual, &f.bindings, None);
                let typ = self.typ(actual, f);
                if constant
                    .as_ref()
                    .is_some_and(|v| matches!(v, Constant::Integer(_)))
                    || typ.numeric()
                    || matches!(typ, Type::Unknown | Type::Generic)
                {
                    empty &= matches!(constant, Some(Constant::Integer(0)));
                }
            }
            let mut data = if empty {
                Value::default()
            } else {
                self.allocate(state, f, at, allocator.clone(), Value::default())
            };
            data.allocator = Some(allocator);
            return Some(vec![
                Value {
                    fields: BTreeMap::from([(Key::Field("buf".into()), data)]),
                    ..Value::default()
                },
                Value::default(),
            ]);
        }
        let string_op = lib == "core:strings"
            && matches!(
                name,
                "write_string"
                    | "write_byte"
                    | "write_rune"
                    | "to_string"
                    | "to_cstring"
                    | "unsafe_to_cstring"
                    | "builder_destroy"
                    | "builder_reset"
                    | "builder_grow"
                    | "builder_len"
                    | "builder_cap"
                    | "builder_space"
            );
        let fmt_op = lib == "core:fmt"
            && matches!(name, "sbprint" | "sbprintln" | "sbprintf" | "sbprintfln");
        if !string_op && !fmt_op {
            return None;
        }
        let i = self.argument(args, if fmt_op { "buf" } else { "b" }, 0)?;
        let target = &vs[i];
        let mut builder = if target.fields.contains_key(&Key::Field("buf".into())) {
            target.clone()
        } else {
            Value::default()
        };
        if builder == Value::default() {
            self.direct(state, target, at);
            for p in &target.refs {
                builder.merge(&self.read(state, p, at));
            }
        }
        if builder.unknown {
            return None;
        }
        let fixed = builder
            .fields
            .contains_key(&Key::Field("<fixed-builder>".into()));
        // A known empty Builder has no fields in the graph. Reject unknown
        // values above rather than manufacturing ownership for opaque inputs.
        let mut data = builder.child(&Key::Field("buf".into()));
        if data.unknown
            && data.refs.iter().all(|p| {
                matches!(self.objects[p.id].storage, Storage::Heap(_))
                    && state
                        .cells
                        .get(&p.id)
                        .is_some_and(|c| !c.uncertain && c.life & DEAD == 0)
            })
        {
            let mut actual = self.role(args[i], "value").unwrap_or(args[i]);
            if self.kind(actual) == "Unary" {
                if let Some(expr) = self.role(actual, "expr") {
                    actual = expr;
                }
            }
            if let Some(variable) = self.symbol(actual).filter(|v| f.env.contains_key(v)) {
                if self.linear_builder(f.procedure, variable) {
                    data.unknown = false;
                    state.write(
                        &Place::root(f.env[&variable]).child(Key::Field("buf".into())),
                        &data,
                        false,
                    );
                }
            }
        }
        let header_only = matches!(name, "builder_len" | "builder_cap" | "builder_space");
        if header_only {
            return Some(vec![Value::default()]);
        }
        if name == "builder_destroy" {
            if !fixed {
                self.release(state, &data, at);
            }
            for p in &target.refs {
                state.write(&p.child(Key::Field("buf".into())), &Value::default(), false);
            }
            return Some(vec![]);
        }
        self.direct(state, &data, at);
        let empty_write = name == "write_string" && self.argument(args,"s",1).is_some_and(|j|matches!(self.r.constant_value(self.role(args[j],"value").unwrap_or(args[j]),&f.bindings,None),Some(Constant::Text(s)) if s.is_empty()));
        let grows = name != "to_string" && name != "builder_reset" && !empty_write;
        if grows {
            for (j, (arg, value)) in args.iter().zip(vs).enumerate() {
                if j == i
                    || self
                        .argument_name(*arg)
                        .is_some_and(|field| self.text(field) == "loc")
                {
                    continue;
                }
                let typ = self.typ(self.role(*arg, "value").unwrap_or(*arg), f);
                self.consume(state, value, at, &typ, 0);
            }
            if fmt_op && self.custom_formatting {
                self.unknown_call(
                    state,
                    at,
                    vs,
                    "custom formatters may retain or release builder inputs",
                );
                return Some(vec![data, Value::default()]);
            }
            if !fixed {
                if data
                    .refs
                    .iter()
                    .any(|p| matches!(self.objects[p.id].storage, Storage::Caller))
                {
                    return None;
                }
                if data.unknown {
                    return None;
                }
                let extra = vs
                    .iter()
                    .enumerate()
                    .filter(|(j, _)| *j != i)
                    .map(|(_, v)| v.clone())
                    .collect::<Vec<_>>();
                if self.builder_views(state, target, &data, &extra, f.id) {
                    self.report(
                        at,
                        data.refs.first().map(|p| p.id),
                        false,
                        "lifetime",
                        "builder growth may invalidate an independently retained view",
                    );
                    for p in &data.refs {
                        if let Some(c) = state.cells.get_mut(&p.id) {
                            c.uncertain = true;
                        }
                    }
                }
                if data.refs.is_empty() {
                    let allocator = data
                        .allocator
                        .clone()
                        .unwrap_or_else(|| state.allocator.clone());
                    data = self.allocate(state, f, at, allocator.clone(), Value::default());
                    data.allocator = Some(allocator);
                    for p in &target.refs {
                        state.write(&p.child(Key::Field("buf".into())), &data, false);
                    }
                }
            }
        }
        Some(
            if fmt_op || matches!(name, "to_string" | "to_cstring" | "unsafe_to_cstring") {
                vec![data, Value::default()]
            } else {
                vec![Value::default(), Value::default()]
            },
        )
    }
    pub(super) fn scalar_library_model(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        args: &[Ref],
        vs: &[Value],
        lib: &str,
        name: &str,
    ) -> Option<Vec<Value>> {
        if matches!(contracts::effect(lib, name), Some(contracts::Effect::Parse)) {
            if let Some(i) = self
                .argument(args, "s", 0)
                .or_else(|| self.argument(args, "str", 0))
            {
                self.direct(state, &vs[i], at);
            }
            if let Some(i) = self.parse_output_argument(at, args, f) {
                self.direct(state, &vs[i], at);
                for pointer in &vs[i].refs {
                    state.write(
                        pointer,
                        &Value::default(),
                        vs[i].unknown || vs[i].refs.len() != 1,
                    );
                }
            }
            return Some(vec![Value::default(), Value::default()]);
        }
        if matches!(
            contracts::effect(lib, name),
            Some(contracts::Effect::PathRead)
        ) {
            if let Some(i) = self.argument(args, "path", 0) {
                self.direct(state, &vs[i], at);
            }
            return Some(vec![Value::default()]);
        }
        if matches!(
            contracts::effect(lib, name),
            Some(contracts::Effect::RandomCollection)
        ) {
            let i = self.argument(args, "array", 0)?;
            self.direct(state, &vs[i], at);
            let item = self.items(state, &vs[i], at);
            let custom = !self.known_generator(at, args, lib, name);
            if custom {
                self.unknown_call(
                    state,
                    at,
                    vs,
                    "custom random generator effects cannot be verified by the collection model",
                );
            }
            if name == "shuffle" {
                for p in &vs[i].refs {
                    state.write(&p.child(Key::Item(None)), &item, true);
                }
                return Some(vec![]);
            }
            let mut item = item;
            if custom {
                item.invalidate();
            }
            return Some(vec![item]);
        }
        if matches!(
            contracts::effect(lib, name),
            Some(contracts::Effect::RandomScalar)
        ) {
            if !self.known_generator(at, args, lib, name) || vs.iter().any(Value::has_refs) {
                if state.reachable(vs.iter().cloned()).is_empty() {
                    self.report(
                        at,
                        None,
                        false,
                        "lifetime",
                        "random generator effects on global reference state cannot be verified",
                    );
                }
                let value = self.unknown_call(
                    state,
                    at,
                    vs,
                    "random generator or scalar address effects cannot be verified",
                );
                return Some(vec![value]);
            }
            return Some(vec![Value::default()]);
        }
        let _ = f;
        None
    }
}
