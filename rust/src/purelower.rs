//! Verified call scratch lifetimes and independently owned results.
//! Unresolved effect/ownership specializations remain explicit migration gaps.
use super::*;
impl Lower<'_, '_> {
    pub fn apply_purity(&mut self, facts: &crate::purity::Facts) -> Result<()> {
        let file = self.file;
        self.pure_conditional = facts
            .conditional
            .iter()
            .filter(|(site, _)| site.file == file)
            .map(|(site, fault)| (site.node, fault.message.clone()))
            .collect();
        self.pure_defaults = facts
            .defaults
            .iter()
            .filter(|field| field.file == file)
            .map(|field| field.node)
            .collect();
        for root in facts.checked.iter().copied().filter(|r| r.file == file) {
            if self.source.node(root.node).data["pure"] != true {
                continue;
            }
            facts.validate(self.resolver, root)?;
            // Local declarations can be retained in the synthetic entry's
            // scope without appearing in the file's ordinary keep set.
            let view = self.evaluation.take();
            let prepared = self.prepare_pure(root, false);
            self.evaluation = view;
            prepared?;
        }
        self.prepare_checked_variants(facts)?;
        Ok(())
    }
    pub(super) fn prepare_pure(&mut self, root: Ref, internal: bool) -> Result<()> {
        if let Some(body) = self.source.role(root.node, "body") {
            if self.source.raw.as_bytes()[self.source.node(body).span.start] != b'{' {
                return Err(Error::at(
                    self.source,
                    self.source.node(body).span,
                    "'do' for procedure bodies is not allowed, prefer {}",
                ));
            }
        }
        if self.borrowing.contracts.contains_key(&root) {
            self.verified_pure.insert(root.node);
            return Ok(());
        }
        let signature = self.role(root.node, "type")?;
        let results = self.resolver.fields(self.reference(signature), "results");
        let contextual = ["", "odin"].contains(
            &self.source.node(signature).data["calling_convention"]
                .as_str()
                .unwrap_or(""),
        );
        let owned = contextual
            && results
                .iter()
                .filter_map(|(_, annotation)| *annotation)
                .any(|annotation| {
                    let typ = self
                        .resolver
                        .specialized_type(annotation, &crate::semantics::Substitutions::new());
                    self.pure_owns(&typ, &mut HashSet::new())
                });
        let Some(body) = self.source.role(root.node, "body") else {
            return Ok(());
        };
        let module = self.module("pure");
        let mut checks = vec![];
        if let Some(fields) = self.source.role(signature, "params") {
            for field in self.source.node(fields).children.clone() {
                if self.source.node(field).kind != "Field" {
                    continue;
                }
                for name in self.source.group(field, "names").to_vec() {
                    let site = self
                        .source
                        .role(field, "type")
                        .filter(|site| !self.source.node(*site).span.is_empty())
                        .unwrap_or(name);
                    let value = if self.source.names.get(self.source.node(name).name) == "_"
                        || self.source.text(name) == "_"
                    {
                        self.pure_ignored
                            .entry(name)
                            .or_insert_with(|| {
                                format!("{}pure_ignored_{}", self.source.prefix, name.0)
                            })
                            .clone()
                    } else {
                        self.source.text(name).trim_start_matches('$').to_string()
                    };
                    let type_of = self.native("type_of");
                    let query = if self.source.node(name).kind == "PolyType"
                        && self
                            .source
                            .role(field, "type")
                            .is_some_and(|r| self.source.node(r).kind == "TypeidType")
                    {
                        value
                    } else {
                        format!("{type_of}({value})")
                    };
                    let validator = self.pure_validator(root.node);
                    let token = self.pure_type_site(
                        site,
                        root.node,
                        &format!("parameter '{}'", self.source.text(name)),
                    );
                    checks.push(self.g(
                        format!("\n{validator}({query},false,true,{internal},0,{token})\n"),
                        site,
                    ));
                }
            }
        }
        for (index, (name, annotation)) in results.into_iter().enumerate() {
            if let Some(annotation) = annotation {
                let typ = self.render(annotation.node, None, true)?;
                let validator = self.pure_validator(root.node);
                let token = self.pure_type_site(
                    annotation.node,
                    root.node,
                    &if name.is_empty() || name == "_" {
                        format!("result {}", index + 1)
                    } else {
                        format!("result '{name}'")
                    },
                );
                checks.push(Code::concat([
                    self.g(format!("\n{validator}("), annotation.node),
                    typ,
                    self.g(
                        format!(",true,{contextual},false,0,{token})\n"),
                        annotation.node,
                    ),
                ]));
            }
        }
        let mutates = internal
            && if self.borrowing.procedures.contains(&root) {
                self.borrowing.mutates.contains(&root)
            } else {
                self.source.role(signature, "params").is_some_and(|params| {
                    self.source
                        .descendants(params)
                        .any(|name| self.mutating_params.contains(&name))
                })
            };
        if contextual && !mutates {
            let scratch = format!(
                "{}pure_scratch_{}",
                self.prefix,
                self.source.node(root.node).span.start
            );
            checks.push(self.g(format!("\n{scratch}:{module}.Scratch\n{module}.scratch_init(&{scratch})\ndefer {module}.scratch_destroy(&{scratch})\ncontext.allocator={module}.scratch_allocator(&{scratch})\ncontext.temp_allocator=context.allocator\n"),root.node));
        }
        if owned {
            self.pure_owned.insert(root.node);
        }
        self.body_prologues.entry(body).or_default().extend(checks);
        self.verified_pure.insert(root.node);
        Ok(())
    }
    pub(super) fn pure_body(&mut self, proc: NodeId, body: NodeId) -> Result<Code> {
        let s = self.source;
        let sig = self.role(proc, "type")?;
        let params = self.role(sig, "params")?;
        let results = self.resolver.fields(self.reference(sig), "results");
        let prefix = format!("{}pure_{}", self.prefix, s.node(proc).span.start);
        let module = self.module("pure");
        let inner = format!("{prefix}_body");
        let allocator = format!("{prefix}_allocator");
        let procedure = format!("{prefix}_procedure");
        let mut slots = vec![];
        let mut names = vec![];
        let mut types = vec![];
        for (index, (name, annotation)) in results.into_iter().enumerate() {
            let annotation =
                annotation.ok_or_else(|| self.unsupported(proc, "inferred pure result types"))?;
            names.push(Self::pure_result_name(&prefix, name, index));
            slots.push(format!("{prefix}_slot_{index}"));
            types.push(self.render(annotation.node, None, true)?);
        }
        if self.source.raw.as_bytes()[self.source.node(body).span.start] != b'{' {
            return Err(Error::at(
                self.source,
                self.source.node(body).span,
                "'do' for procedure bodies is not allowed, prefer {}",
            ));
        }
        let prologues = self.body_prologues.remove(&body).unwrap_or_default();
        let promotion = slots
            .iter()
            .zip(&names)
            .map(|(slot, name)| format!("{slot}^={module}.clone({name},{allocator})"))
            .collect::<Vec<_>>()
            .join(";");
        self.body_prologues.insert(
            body,
            vec![self.g(format!("\ndefer{{{promotion}}}\n"), proc)],
        );
        let mut substitutions = vec![];
        for id in s.descendants(body) {
            if s.node(id).kind == "Directive"
                && s.names.get(s.node(id).name) == "procedure"
                && s.ancestors(id)
                    .find(|node| s.node(*node).kind == "Procedure")
                    == Some(proc)
            {
                self.switch_replacements.insert(id, self.g(&procedure, id));
                substitutions.push(id);
            }
        }
        self.pure_busy.insert(proc);
        let block = self.render(body, None, false);
        self.pure_busy.remove(&proc);
        self.body_prologues.remove(&body);
        for id in substitutions {
            self.switch_replacements.remove(&id);
        }
        let block = block?;
        let parameter_code = self.render(params, None, false)?;
        let mut code = vec![
            self.g(format!("{{\n{allocator}:=context.allocator\n"), proc),
            Code::concat(prologues),
        ];
        let extra = slots
            .iter()
            .map(|name| format!("{name}:^${name}_T"))
            .chain([
                format!("{procedure}:string"),
                format!("{allocator}:${allocator}_T"),
            ])
            .collect::<Vec<_>>()
            .join(",");
        code.push(self.g(
            format!(
                "\n{inner}::proc({extra}{}",
                if s.node(params).span.is_empty() {
                    ""
                } else {
                    ","
                }
            ),
            proc,
        ));
        code.push(parameter_code);
        code.push(self.g(")->(", proc));
        for (index, (name, typ)) in names.iter().zip(&types).enumerate() {
            if index > 0 {
                code.push(self.g(",", proc));
            }
            code.push(self.g(format!("{name}:"), proc));
            code.push(typ.clone());
        }
        code.push(self.g(")", proc));
        for (index, clause) in s.group(proc, "where").iter().enumerate() {
            code.push(self.g(if index == 0 { " where " } else { "," }, proc));
            code.push(self.render(*clause, None, true)?);
        }
        code.push(block);
        code.push(self.g("\n", proc));
        let locals = slots
            .iter()
            .map(|slot| format!("{slot}_value"))
            .collect::<Vec<_>>();
        for (local, typ) in locals.iter().zip(&types) {
            code.push(self.g(format!("{local}:"), proc));
            code.push(typ.clone());
            code.push(self.g("\n", proc));
        }
        let mut args = locals
            .iter()
            .map(|local| format!("&{local}"))
            .chain([
                if self.emitting_variants.contains(&proc) {
                    self.checked_procedure_literal(proc)
                } else {
                    "#procedure".into()
                },
                allocator.clone(),
            ])
            .collect::<Vec<_>>();
        let mut after_variadic = false;
        for field in s
            .node(params)
            .children
            .iter()
            .filter(|id| s.node(**id).kind == "Field")
        {
            for name in s.group(*field, "names") {
                let name = self
                    .pure_ignored
                    .get(name)
                    .map(String::as_str)
                    .unwrap_or_else(|| s.text(*name).trim_start_matches('$'));
                args.push(if s.node(*field).data["variadic"] == true {
                    after_variadic = true;
                    format!("..{name}")
                } else if after_variadic {
                    format!("{name}={name}")
                } else {
                    name.into()
                });
            }
        }
        code.push(self.g(
            format!(
                "{inner}({})\nreturn {}\n}}",
                args.join(","),
                locals.join(",")
            ),
            proc,
        ));
        Ok(Code::concat(code))
    }

    fn pure_result_name(prefix: &str, name: String, index: usize) -> String {
        if name.is_empty() || name == "_" {
            format!("{prefix}_result_{index}")
        } else {
            name
        }
    }

    pub(super) fn pure_return(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        if self.pure_busy.is_empty() {
            return Ok(None);
        }
        let s = self.source;
        let Some(proc) = s
            .ancestors(id)
            .find(|node| s.node(*node).kind == "Procedure")
            .filter(|proc| self.pure_busy.contains(proc))
        else {
            return Ok(None);
        };
        let signature = self.role(proc, "type")?;
        let results = self.resolver.fields(self.reference(signature), "results");
        let prefix = format!("{}pure_{}", self.prefix, s.node(proc).span.start);
        let names = results
            .iter()
            .enumerate()
            .map(|(index, (name, _))| Self::pure_result_name(&prefix, name.clone(), index))
            .collect::<Vec<_>>();
        let values = s.group(id, "values");
        if let Some(parent) = s.node(id).parent.filter(|parent| {
            s.node(*parent).kind == "Block" && s.raw.as_bytes()[s.node(*parent).span.start] != b'{'
        }) {
            self.convert_do(parent);
        }
        let span = s.node(id).span;
        let mut code = vec![self.gen("{", span, "pure-return")];
        if !values.is_empty() {
            code.push(self.gen(format!("{}=", names.join(",")), span, "pure-return"));
            for (index, &value) in values.iter().enumerate() {
                if index > 0 {
                    code.push(self.gen(",", span, "pure-return"));
                }
                let hint = if let Some((_, Some(annotation))) = results.get(index) {
                    Some(Hint {
                        constant_literal: false,
                        semantic: Some(self.typ(annotation.node)),
                        code: self.render(annotation.node, None, true)?,
                        element_only: false,
                        plain_array: s.node(annotation.node).kind == "ArrayType",
                    })
                } else {
                    None
                };
                code.push(self.big_owned(value, hint, constant)?);
            }
            code.push(self.gen(";", span, "pure-return"));
        }
        // These native results are discarded. The deferred promotion reads
        // the named slots after user defers while their local storage is alive.
        let type_of = self.native("type_of");
        let returned = names
            .iter()
            .map(|name| format!("transmute({type_of}({name})){name}"))
            .collect::<Vec<_>>()
            .join(",");
        code.push(self.gen(format!("return {returned}}}"), span, "pure-return"));
        Ok(Some(Code::concat(code)))
    }

    pub(super) fn pure_owns(&mut self, typ: &Type, seen: &mut HashSet<Type>) -> bool {
        if !seen.insert(typ.clone()) {
            return false;
        }
        match typ {
            Type::Nominal(_, base)
            | Type::Array { elem: base, .. }
            | Type::Fixed { elem: base, .. } => self.pure_owns(base, seen),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => self
                .resolver
                .record_fields(typ)
                .iter()
                .any(|(_, typ)| self.pure_owns(typ, seen)),
            Type::Union(owner) => self
                .resolver
                .union_variants(*owner)
                .iter()
                .any(|typ| self.pure_owns(typ, seen)),
            Type::BigInteger
            | Type::BigRational
            | Type::Slice(_)
            | Type::Dynamic(_)
            | Type::String { .. }
            | Type::Unknown
            | Type::Generic => true,
            _ => false,
        }
    }
}
