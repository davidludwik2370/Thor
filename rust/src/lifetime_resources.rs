//! Reviewed resource origins and matching destruction. A procedure's name or
//! a pointer's type alone never proves that it is an ordinary native resource.
use super::*;

pub(super) fn file_type(r: &Resolver<'_>, typ: &Type) -> bool {
    let typ = match base(typ) {
        Type::Pointer(t) => base(t),
        t => t,
    };
    let owner = match typ {
        Type::Struct(o) | Type::Record(o, _) => *o,
        _ => return false,
    };
    let s = &r.sources[owner.file];
    s.info["odin_root"]
        .as_str()
        .is_some_and(|root| s.path == std::path::Path::new(root).join("core/os/file.odin"))
        && s.node(owner.node)
            .parent
            .is_some_and(|p| s.group(p, "names").iter().any(|n| s.text(*n) == "File"))
}

impl Analysis<'_, '_> {
    pub(super) fn native_global(
        &mut self,
        state: &mut State,
        symbol: Ref,
        _at: Ref,
    ) -> Option<Value> {
        let source = &self.r.sources[symbol.file];
        if source.text(symbol.node) == "args"
            && source.info["odin_root"].as_str().is_some_and(|root| {
                source.path == std::path::Path::new(root).join("core/os/process.odin")
            })
        {
            // os owns the header until its fini; each argument borrows runtime
            // process storage. Do not execute its native initializer as user code.
            let text = self.object(state, 0, symbol, 15, Storage::Static, Value::default());
            let items = Value {
                fields: BTreeMap::from([(Key::Item(None), Value::reference(Place::root(text)))]),
                ..Value::default()
            };
            let data = self.object(state, 0, symbol, 14, Storage::Static, items);
            return Some(Value::reference(Place::root(data)));
        }
        if !matches!(source.text(symbol.node), "stdin" | "stdout" | "stderr")
            || !source.info["odin_root"].as_str().is_some_and(|root| {
                source.path == std::path::Path::new(root).join("core/os/file.odin")
            })
        {
            return None;
        }
        let id = self.object(state, 0, symbol, 13, Storage::Static, Value::default());
        self.objects[id].resource = Some(Resource::StandardFile);
        Some(Value::reference(Place::root(id)))
    }
    pub(super) fn file_model(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        args: &[Ref],
        vs: &[Value],
        lib: &str,
        name: &str,
    ) -> Option<Vec<Value>> {
        if lib != "core:os" {
            return None;
        }
        let target = self
            .r
            .flags
            .iter()
            .find_map(|flag| flag.strip_prefix("-target:"));
        if target.is_some_and(|target| {
            !target.starts_with("darwin_")
                && !target.starts_with("linux_")
                && !target.starts_with("windows_")
        }) || target.is_none() && !matches!(std::env::consts::OS, "macos" | "linux" | "windows")
        {
            return None;
        }
        if matches!(name, "open" | "create") {
            if let Some(i) = self.argument(args, "name", 0) {
                self.direct(state, &vs[i], at);
            }
            // The installed os.file_allocator uses its heap allocator, not the
            // ambient arena. The opaque file includes its private name storage.
            let mut value = self.allocate(state, f, at, Allocator::Heap, Value::default());
            value.may_nil=true;
            for p in &value.refs {
                self.objects[p.id].resource = Some(Resource::File);
                state.file_origins.insert(p.id);
                state.cells.get_mut(&p.id).unwrap().life |= ABSENT;
            }
            let status=Value{file_status:value.refs.first().map(|p|p.id),..Value::default()};
            return Some(vec![value, status]);
        }
        if name != "close" {
            return None;
        }
        let i = self.argument(args, "f", 0)?;
        let value = &vs[i];
        let verified = !value.refs.is_empty()
            && value.refs.iter().all(|p| {
                p.path.is_empty() && p.start==Some(0) && !p.interior
                && matches!(self.objects[p.id].resource,Some(Resource::File|Resource::StandardFile))
                // Changes to File.impl or File.stream install an unverified
                // dispatch target. A copied File record has no resource origin.
                && state.cells.get(&p.id).is_some_and(|c| c.value==Value::default())
            });
        if !verified {
            return None;
        }
        self.direct(state, value, at);
        let mut owned = value.clone();
        owned
            .refs
            .retain(|p| self.objects[p.id].resource == Some(Resource::File));
        self.release_as(state, &owned, at, Some(Resource::File));
        // Closing a standard descriptor does not free its static File record.
        // Later I/O can return an OS error; it does not dereference freed memory.
        Some(vec![Value::default()])
    }
    pub(super) fn image_model(
        &mut self,
        state: &mut State,
        f: &mut Frame,
        at: Ref,
        args: &[Ref],
        vs: &[Value],
        name: &str,
    ) -> Option<Vec<Value>> {
        if name == "LoadImageFromTexture" {
            let data = self.allocate(state, f, at, Allocator::Heap, Value::default());
            for p in &data.refs {
                self.objects[p.id].resource = Some(Resource::Image);
                state.cells.get_mut(&p.id).unwrap().life |= ABSENT;
            }
            return Some(vec![Value {
                fields: BTreeMap::from([(Key::Field("data".into()), data)]),
                ..Value::default()
            }]);
        }
        if !matches!(name, "ImageFlipVertical" | "ExportImage" | "UnloadImage") {
            return None;
        }
        let i = self.argument(args, "image", 0)?;
        let key = Key::Field("data".into());
        if name == "ImageFlipVertical" {
            let target = &vs[i];
            self.direct(state, target, at);
            let mut image = Value::default();
            for p in &target.refs {
                image.merge(&state.read(p));
            }
            let data = image.child(&key);
            self.direct(state, &data, at);
            // Replacement is abstractly in-place only with an exclusive owner.
            // Otherwise even a shallow Image copy may hold the freed pixels.
            let standalone = self.r.sources[at.file]
                .node(at.node)
                .parent
                .is_some_and(|p| self.r.sources[at.file].node(p).kind == "ExpressionStatement");
            let exclusive=!data.unknown && !target.unknown && target.refs.len()==1
                && standalone && self.active.is_empty()
                && data.refs.iter().all(|p| {
                    self.objects[p.id].resource==Some(Resource::Image)
                        && state.cells.iter().all(|(id,c)| {
                            if c.life & LIVE==0 {return true;}
                            let except=target.refs.iter().filter(|h| h.id==*id).map(|h| {
                                let mut path=h.path.clone();path.push(key.clone());path
                            }).collect::<Vec<_>>();
                            !Self::outside_reference(&c.value,&[],&except,p.id)
                        })
                        && !state.cleanups.values().flatten().any(|pending| matches!(&pending.action,
                            Cleanup::Procedure{inputs,..} if inputs.iter().any(|v|v.references(p.id))))
                });
            if !exclusive {
                self.unknown_call(
                    state,
                    at,
                    &[data],
                    "image replacement may invalidate independently retained pixel storage",
                );
            }
            return Some(vec![]);
        }
        let data = vs[i].child(&key);
        self.direct(state, &data, at);
        if name == "ExportImage" {
            if let Some(path) = self.argument(args, "fileName", 1) {
                self.direct(state, &vs[path], at);
            }
            return Some(vec![Value::default()]);
        }
        if data
            .refs
            .iter()
            .any(|p| self.objects[p.id].resource != Some(Resource::Image))
        {
            self.unknown_call(
                state,
                at,
                &[data.clone()],
                "image data does not have a reviewed native allocation origin",
            );
        } else {
            self.release_as(state, &data, at, Some(Resource::Image));
        }
        Some(vec![])
    }
}
