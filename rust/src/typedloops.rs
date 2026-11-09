//! Keep Odin's iteration and evaluation order; annotations only supply bounds
//! context or a checked conversion for its native index.
use super::*;

impl Lower<'_, '_> {
    fn loop_invalid(&mut self, annotation: NodeId, message: &str) -> Code {
        let token = self.error_token(annotation, message).finish().text;
        self.g(format!("#assert(false, {})", json!(token)), annotation)
    }

    pub(super) fn typed_loop(&mut self, id: NodeId) -> Result<Option<Code>> {
        let s = self.source;
        let value_type = s.role(id, "value_type");
        let index_type = s.role(id, "index_type");
        let annotation = match value_type.or(index_type) {
            Some(annotation) => annotation,
            None => {
                if let Some(annotation) = s.group(id, "bindings").iter().find_map(|binding| {
                    (s.node(*binding).kind == "FieldValue")
                        .then(|| s.role(*binding, "value"))
                        .flatten()
                }) {
                    return Ok(Some(self.loop_invalid(
                        annotation,
                        "Typed loops support at most a value binding and an index binding.",
                    )));
                }
                return Ok(None);
            }
        };
        let names = s.group(id, "names").to_vec();
        let bindings = s.group(id, "bindings").to_vec();
        let expr = self.role(id, "expr")?;
        let mut interval = expr;
        while s.node(interval).kind == "Paren" {
            interval = self.role(interval, "expr")?;
        }
        let is_range = s.node(interval).kind == "Binary"
            && matches!(s.node(interval).op.as_str(), ".." | "..<" | "..=");
        let problem = if names.len() > 2 {
            Some("Typed loops support at most a value binding and an index binding.".into())
        } else if names.len() == 2
            && s.text(names[0]).trim_start_matches('&') == s.text(names[1])
            && s.text(names[1]) != "_"
        {
            Some(format!("Duplicate loop binding name: {}", s.text(names[1])))
        } else if value_type.is_some() && !is_range {
            Some("A typed value binding is supported only for numeric ranges; collection element types are inferred.".into())
        } else if index_type.is_some() && is_range {
            Some("A typed index binding is supported only for arrays and slices, not numeric ranges.".into())
        } else if names.iter().skip(1).any(|n| s.node(*n).kind == "Unary") {
            Some("An index binding cannot be a mutable reference.".into())
        } else {
            None
        };
        if let Some(message) = problem {
            return Ok(Some(self.loop_invalid(annotation, &message)));
        }
        if index_type.is_some() && s.node(expr).kind == "Call" {
            let callee = self.role(expr, "callee")?;
            if let Some(signature) = self.resolver.signature(self.reference(callee)) {
                if self.resolver.fields(signature, "results").len() != 1 {
                    return Ok(Some(self.loop_invalid(annotation,
                        "Typed index bindings are supported only for arrays and slices; iterator procedures keep their inferred bindings.")));
                }
            }
        }
        self.meta = true;
        let meta = format!("{}meta", s.prefix);
        let target = self.render(annotation, None, false)?;
        let target_text = target.clone().finish().text;
        let native_index = format!("{}loop_index_{}", s.prefix, s.node(id).span.start);
        let mut replacements = vec![];
        let init = s.role(id, "init");
        if let Some(init) = init {
            let first = bindings
                .first()
                .ok_or_else(|| Error::new("loop bindings omitted"))?;
            let span = Span::new(s.node(init).span.start, s.node(*first).span.start);
            let comments = s
                .comments
                .iter()
                .filter(|comment| span.contains(**comment))
                .map(|comment| self.copy(*comment))
                .collect::<Vec<_>>();
            replacements.push((span, Code::concat(comments)));
        }
        for (i, binding) in bindings.iter().enumerate() {
            if let Some(name) = names.get(i) {
                let code = if i == 1 && index_type.is_some() {
                    self.g(native_index.clone(), *name)
                } else {
                    self.copy(s.node(*name).span)
                };
                replacements.push((s.node(*binding).span, code));
            }
        }
        let mut checks = vec![];
        let condition;
        if let Some(value_type) = value_type {
            condition = format!(
                "({meta}.type_is_integer({target_text}) || {meta}.type_is_float({target_text}))"
            );
            let token = self
                .error_token(
                    value_type,
                    "A typed numeric range requires a native integer or floating-point type.",
                )
                .finish()
                .text;
            checks.push(self.g(
                format!("#assert({condition}, {})\n", json!(token)),
                value_type,
            ));
            let helper = format!("{}loop_bound_{}", s.prefix, s.node(id).span.start);
            self.helpers.insert(
                (self.helper_scope(id), helper.clone()),
                self.g(
                    format!(
                "\n{helper} :: proc \"contextless\"($T: typeid, value: T) -> T {{return value}}\n"),
                    value_type,
                ),
            );
            let mut bounds = vec![];
            for side in ["left", "right"] {
                let bound = self.role(interval, side)?;
                let code = self.render(bound, None, false)?;
                bounds.push((
                    s.node(bound).span,
                    Code::concat([
                        self.g(format!("{helper}("), bound),
                        target.clone(),
                        self.g(", ", bound),
                        code,
                        self.g(")", bound),
                    ]),
                ));
            }
            replacements.push((s.node(expr).span, self.splice(expr, bounds, vec![])?));
        } else {
            let module = self.module("sequence");
            self.querying += 1;
            let rendered = self.render(expr, None, false);
            self.querying -= 1;
            let rendered = rendered?;
            let type_of = self.native("type_of");
            self.default = true;
            let typ = Code::concat([
                self.g(format!("{type_of}({}default_value(", s.prefix), expr),
                rendered,
                self.g("))", expr),
            ]);
            let typ_text = typ.clone().finish().text;
            checks.push(Code::concat([
                self.g(format!("{module}.validate_loop_index("), annotation),
                typ,
                self.g(", ", annotation),
                target.clone(),
                self.g(")\n", annotation),
            ]));
            let collection = |t: &str| {
                format!("(({meta}.type_is_array({t}) && !{meta}.type_is_enumerated_array({t})) || {meta}.type_is_slice({t}) || {meta}.type_is_dynamic_array({t}) || {meta}.type_is_fixed_capacity_dynamic_array({t}))")
            };
            let pointer = collection(&format!("{meta}.type_elem_type({typ_text})"));
            condition = format!("({pointer} when {meta}.type_is_pointer({typ_text}) else {}) && {meta}.type_is_integer({target_text})", collection(&typ_text));
        }
        let body = self.role(id, "body")?;
        if index_type.is_some() && s.raw.as_bytes()[s.node(body).span.start] != b'{' {
            // `do` accepts a single ordinary statement. Replace its token as
            // well as the body when the checked index needs a braced scope.
            let gap = Span::new(s.node(expr).span.end, s.node(body).span.start);
            if let Some((offset, _)) = s.slice(gap).match_indices("do").find(|(offset, _)| {
                let token = Span::new(gap.start + offset, gap.start + offset + 2);
                !s.comments.iter().any(|comment| comment.contains(token))
            }) {
                let token = Span::new(gap.start + offset, gap.start + offset + 2);
                replacements.push((token, self.gen("", token, "typed-loop-body")));
            }
        }
        let body_code = if index_type.is_some() {
            let name = *names
                .get(1)
                .ok_or_else(|| Error::new("typed index name omitted"))?;
            let module = self.module("sequence");
            let prologue = Code::concat([
                self.g("\n", name),
                self.copy(s.node(name).span),
                self.g(
                    format!(" := {module}.checked_loop_index({native_index}, "),
                    annotation,
                ),
                target,
                self.g(
                    format!(",{})\n", self.source_location(annotation)),
                    annotation,
                ),
            ]);
            let mut body_code = self.render(body, None, false)?;
            if s.raw.as_bytes()[s.node(body).span.start] != b'{' {
                body_code = Code::concat([self.g("{", body), body_code, self.g("\n}", body)]);
            }
            Code::concat([self.g("{", body), prologue, body_code, self.g("\n}", body)])
        } else {
            self.render(body, None, false)?
        };
        replacements.push((s.node(body).span, body_code));
        for child in &s.node(id).children {
            if !replacements
                .iter()
                .any(|(span, _)| span.contains(s.node(*child).span))
            {
                replacements.push((s.node(*child).span, self.render(*child, None, false)?));
            }
        }
        let loop_code = self.splice(id, replacements, vec![])?;
        let mut code = vec![self.g("{\n", id)];
        if let Some(init) = init {
            code.extend([self.render(init, None, false)?, self.g("\n{\n", id)]);
        }
        code.extend(checks);
        code.extend([
            self.g(format!("when {condition} {{\n"), annotation),
            loop_code,
            self.g("\n}\n", id),
        ]);
        if init.is_some() {
            code.push(self.g("}\n", id));
        }
        code.push(self.g("}", id));
        Ok(Some(Code::concat(code).expansion(&s.path, s.node(id).span)))
    }
}
