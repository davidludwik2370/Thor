//! Ordered, once-only builtin extrema expansion. Ordinary scalar calls remain
//! Odin calls; borrowed slice expansion retains an optional success result.
use super::*;
#[derive(Clone, Copy, PartialEq, Eq)]
enum Part {
    Scalar,
    Array,
    Slice,
}
impl Lower<'_, '_> {
    pub(super) fn extrema_name(&self, id: NodeId, seen: &mut HashSet<Ref>) -> Option<String> {
        self.extrema_reference(self.reference(id), seen)
    }
    fn extrema_reference(&self, r: Ref, seen: &mut HashSet<Ref>) -> Option<String> {
        if !seen.insert(r) {
            return None;
        }
        let source = &self.resolver.sources[r.file];
        if source.node(r.node).kind == "Ident"
            && self.resolver.lookup(r, source.text(r.node)).is_none()
        {
            for selection in &source.selections {
                let Ok(path) = unquote(&selection.path) else {
                    continue;
                };
                if selection
                    .names
                    .iter()
                    .any(|name| name == source.text(r.node))
                    && matches!(path.as_str(), "core:math" | "base:builtin")
                    && self.resolver.standard_library(r, &path)
                    && matches!(source.text(r.node), "min" | "max")
                {
                    return Some(source.text(r.node).into());
                }
            }
        }
        if source.node(r.node).kind == "Selector" {
            if let (Some(base), Some(field)) =
                (source.role(r.node, "expr"), source.role(r.node, "field"))
            {
                let name = source.text(field);
                if matches!(name, "min" | "max")
                    && self
                        .resolver
                        .lookup(
                            Ref {
                                file: r.file,
                                node: base,
                            },
                            source.text(base),
                        )
                        .is_none()
                {
                    for import in &source.nodes {
                        if import.kind != "Import" {
                            continue;
                        }
                        let Ok(path) = unquote(&import.op) else {
                            continue;
                        };
                        let alias = source.names.get(import.name);
                        let alias = if alias.is_empty() {
                            path.rsplit(['/', ':']).next().unwrap_or("")
                        } else {
                            alias
                        };
                        if alias == source.text(base)
                            && matches!(path.as_str(), "core:math" | "base:builtin")
                            && self.resolver.standard_library(r, &path)
                        {
                            return Some(name.into());
                        }
                    }
                }
            }
        }
        if source.node(r.node).kind == "Paren" {
            return source
                .role(r.node, "expr")
                .and_then(|node| self.extrema_reference(Ref { file: r.file, node }, seen));
        }
        if let Some(symbol) = self.resolver.symbol(r) {
            let symbol = &self.resolver.symbols[symbol];
            if symbol.constant && !symbol.immutable {
                if let Some(node) = symbol.value {
                    return self.extrema_reference(
                        Ref {
                            file: symbol.reference.file,
                            node,
                        },
                        seen,
                    );
                }
            }
            return None;
        }
        if source.node(r.node).kind == "Ident"
            && matches!(source.text(r.node), "min" | "max")
            && self.resolver.native_builtin(r, source.text(r.node))
        {
            return Some(source.text(r.node).into());
        }
        if source.node(r.node).kind == "Selector" {
            let base = source.role(r.node, "expr")?;
            let field = source.role(r.node, "field")?;
            let name = source.text(field);
            if !matches!(name, "min" | "max")
                || self
                    .resolver
                    .lookup(
                        Ref {
                            file: r.file,
                            node: base,
                        },
                        source.text(base),
                    )
                    .is_some()
            {
                return None;
            }
            for import in &source.nodes {
                if import.kind != "Import" {
                    continue;
                }
                let path = unquote(&import.op).ok()?;
                let alias = source.names.get(import.name);
                let alias = if alias.is_empty() {
                    path.rsplit(['/', ':']).next().unwrap_or("")
                } else {
                    alias
                };
                if alias == source.text(base)
                    && matches!(path.as_str(), "core:math" | "base:builtin")
                {
                    return Some(name.into());
                }
            }
        }
        None
    }
    pub(super) fn extrema(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        let Some(callee) = self.source.role(id, "callee") else {
            return Ok(None);
        };
        let Some(name) = self.extrema_name(callee, &mut HashSet::new()) else {
            return Ok(None);
        };
        let ellipsis = self.source.node(id).spans.get("ellipsis").copied();
        let mut parts = vec![];
        for argument in self.source.group(id, "args") {
            let value = self.unparen(*argument);
            if ellipsis
                .is_some_and(|span| self.source.node(argument.to_owned()).span.start >= span.end)
            {
                parts.push((Part::Slice, *argument));
            } else if self.source.node(value).kind == "Unary" && self.source.node(value).op == "**"
            {
                parts.push((Part::Array, self.role(value, "expr")?));
            } else {
                parts.push((Part::Scalar, *argument));
            }
        }
        if parts.iter().all(|(kind, _)| *kind == Part::Scalar) {
            return Ok(None);
        }
        for (kind, value) in &parts {
            if self.source.node(*value).kind == "FieldValue" {
                return Ok(Some(self.error_token(
                    *value,
                    "Spread min/max calls take positional values, without named arguments.",
                )));
            }
            let typ = self.typ(*value);
            if *kind == Part::Slice
                && !matches!(typ, Type::Slice(_) | Type::Unknown | Type::Generic)
            {
                return Ok(Some(self.error_token(
                    *value,
                    "A '..' argument to min/max must be a slice; use values[:] for an array.",
                )));
            }
            if *kind == Part::Array
                && !matches!(typ, Type::Array { .. } | Type::Unknown | Type::Generic)
            {
                return Ok(Some(self.error_token(
                    *value,
                    "A '**' argument to min/max must be a fixed array.",
                )));
            }
            if *kind == Part::Array
                && matches!(
                    typ,
                    Type::Array {
                        length: Some(0),
                        ..
                    }
                )
            {
                return Ok(Some(self.error_token(
                    *value,
                    "An empty fixed array cannot be expanded into min/max.",
                )));
            }
        }
        if constant {
            return Err(self.unsupported(id, "compile-time expanded extrema evaluation"));
        }
        let module = self.module("sequence");
        if parts.len() == 1 {
            let (kind, value) = parts[0];
            let value = self.render(value, None, false)?;
            return Ok(Some(Code::concat([
                self.g(
                    format!(
                        "{module}.{name}_{}(",
                        if kind == Part::Slice {
                            "slice"
                        } else {
                            "array"
                        }
                    ),
                    id,
                ),
                value,
                self.g(")", id),
            ])));
        }
        let optional = ellipsis.is_some();
        let result_type = format!(
            "{}Result{}",
            self.source.prefix,
            if optional { "_0" } else { "" }
        );
        let prefix = format!(
            "{}extrema_{}_",
            self.source.prefix,
            self.source.node(id).span.start
        );
        let (helper,mut args)=self.closed(id,if optional{"extrema_optional"}else{"extrema"},Some(if optional{"tuple:2"}else{""}),|this|{
            let mut code=vec![];
            for(index,(kind,value))in parts.iter().enumerate(){let rendered=this.render(*value,if *kind==Part::Scalar{Some(Hint{code:this.g(result_type.clone(),*value),element_only:false,plain_array:false})}else{None},false)?;code.extend([this.g(if *kind==Part::Scalar{format!("{prefix}arg_{index}: {result_type} = ")}else{format!("{prefix}arg_{index} := ")},*value),rendered,this.g("\n",*value)]);}
            code.push(this.g(format!("{prefix}result: {result_type}\n{prefix}ok:=false\n"),id));
            for(index,(kind,value))in parts.iter().enumerate(){code.push(this.g(match kind{Part::Scalar=>format!("if {prefix}ok {{{prefix}result={module}.{name}_value({prefix}result,{prefix}arg_{index})}}else{{{prefix}result={prefix}arg_{index};{prefix}ok=true}}\n"),Part::Array=>format!("{prefix}result={module}.{name}_array_seeded({prefix}arg_{index},{prefix}result,{prefix}ok);{prefix}ok=true\n"),Part::Slice=>format!("{prefix}result,{prefix}ok={module}.{name}_slice_seeded({prefix}arg_{index},{prefix}result,{prefix}ok)\n")},*value));}
            code.push(this.g(if optional{format!("return {prefix}result,{prefix}ok")}else{format!("return {prefix}result")},id));Ok(Code::concat(code))
        })?;
        let witness = parts
            .iter()
            .find(|(kind, _)| *kind != Part::Scalar)
            .map(|(_, value)| *value)
            .ok_or_else(|| Error::new("missing extrema sequence"))?;
        let query = self.query_value(witness)?;
        let type_of = self.native("type_of");
        args.push(Code::concat([
            self.g(format!("{type_of}({module}.element("), witness),
            query,
            self.g("))", witness),
        ]));
        if optional {
            args.push(self.g("bool", id));
        }
        Ok(Some(self.helper_call(id, &helper, args)))
    }
}
