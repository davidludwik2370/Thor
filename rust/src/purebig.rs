//! Exact native guards for generic logical-number implementation storage.
use super::*;
const STORAGE:&str="Pure code cannot inspect Integer/Rational implementation storage; use numeric operators and public properties.";
impl Lower<'_, '_> {
    fn pure_big_private_base(&mut self, id: NodeId) -> Option<NodeId> {
        if self.source.node(id).kind != "Selector" {
            return None;
        }
        if !matches!(
            self.source.text(self.source.role(id, "field")?),
            "_value" | "_numerator" | "_denominator"
        ) {
            return None;
        }
        let base = self.source.role(id, "expr")?;
        let mut typ = self.typ(base);
        while let Type::Pointer(inner) = typ {
            typ = *inner;
        }
        typ.is_unknown().then_some(base)
    }
    fn pure_big_storage_validation(&mut self, base: NodeId) -> Result<Code> {
        let name = format!(
            "{}pure_big_storage_{}",
            self.prefix,
            self.source.node(base).span.start
        );
        let key = (NodeId(0), self.prefix.clone(), name.clone());
        if !self.helpers.contains_key(&key) {
            self.meta = true;
            let meta = format!("{}meta", self.prefix);
            let module = self.module("big");
            let token = self.error_token(base, STORAGE).finish().text;
            self.pure_error_sites
                .insert(token.clone(), self.reference(base));
            self.helpers.insert(key,self.g(format!("\n@(private=\"file\")\n{name}::proc \"contextless\"($T:typeid){{when {meta}.type_is_pointer(T){{{name}({meta}.type_elem_type(T))}}else when T=={module}.Integer||T=={module}.Rational{{#panic({})}}}}\n",json!(token)),base));
        }
        let value = self.query_value(base)?;
        let type_of = self.native("type_of");
        Ok(Code::concat([
            self.g(format!("{name}({type_of}("), base),
            value,
            self.g("))\n", base),
        ]))
    }
    pub(super) fn pure_big_storage(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        constant: bool,
    ) -> Result<Option<Code>> {
        if !self.checked_mode
            || self.querying > 0
            || self.logical.pure_storage_busy.contains(&id)
            || self.queried_pure_expression(id)
            || !self.big_enabled()
        {
            return Ok(None);
        }
        if self.source.node(id).kind == "Assignment" {
            let mut bases = vec![];
            for target in self.source.group(id, "lhs").to_vec() {
                for child in std::iter::once(target).chain(self.source.descendants(target)) {
                    if let Some(base) = self.pure_big_private_base(child) {
                        bases.push(base);
                    }
                }
            }
            if bases.is_empty() {
                return Ok(None);
            }
            self.logical.pure_storage_busy.insert(id);
            let built = self.closed(id, "pure_big_storage_write", None, |this| {
                let mut code = vec![];
                for base in bases {
                    code.push(this.pure_big_storage_validation(base)?);
                }
                code.push(this.render(id, expected, constant)?);
                Ok(Code::concat(code))
            });
            self.logical.pure_storage_busy.remove(&id);
            let (name, args) = built?;
            return Ok(Some(self.helper_call(id, &name, args)));
        }
        let Some(base) = self.pure_big_private_base(id) else {
            return Ok(None);
        };
        let assignment = self.source.ancestors(id).any(|owner| {
            self.source.node(owner).kind == "Assignment"
                && self.source.group(owner, "lhs").iter().any(|target| {
                    self.source
                        .node(*target)
                        .span
                        .contains(self.source.node(id).span)
                })
        });
        if assignment {
            return Ok(None);
        }
        self.logical.pure_storage_busy.insert(id);
        let built = (|| {
            let result = self.native_type(id)?;
            let (name, mut args) = self.closed(id, "pure_big_storage_read", Some(""), |this| {
                Ok(Code::concat([
                    this.pure_big_storage_validation(base)?,
                    this.g("return ", id),
                    this.render(id, expected, constant)?,
                ]))
            })?;
            args.push(result);
            Ok(Some(self.helper_call(id, &name, args)))
        })();
        self.logical.pure_storage_busy.remove(&id);
        built
    }
}
