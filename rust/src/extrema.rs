//! Ordered, once-only builtin extrema expansion. Ordinary scalar calls remain
//! Odin calls; borrowed slice expansion retains an optional success result.
use super::*;
use crate::extremaplan::Part;
impl Lower<'_, '_> {
    pub(super) fn extrema_name(&self, id: NodeId, seen: &mut HashSet<Ref>) -> Option<String> {
        crate::extremaplan::name(self.resolver, self.reference(id), seen)
    }
    pub(super) fn extrema(&mut self, id: NodeId, constant: bool) -> Result<Option<Code>> {
        let Some(plan) = crate::extremaplan::plan(self.resolver, self.reference(id)) else {
            return Ok(None);
        };
        let name = plan.name;
        let parts = plan.parts;
        let optional = plan.optional;
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
        let result_type = format!("{}Result{}", self.prefix, if optional { "_0" } else { "" });
        let prefix = format!(
            "{}extrema_{}_",
            self.prefix,
            self.source.node(id).span.start
        );
        let (helper,mut args)=self.closed(id,if optional{"extrema_optional"}else{"extrema"},Some(if optional{"tuple:2"}else{""}),|this|{
            let mut code=vec![];
            for(index,(kind,value))in parts.iter().enumerate(){let rendered=this.render(*value,if *kind==Part::Scalar{Some(Hint{constant_literal:false,semantic:None,code:this.g(result_type.clone(),*value),element_only:false,plain_array:false})}else{None},false)?;code.extend([this.g(if *kind==Part::Scalar{format!("{prefix}arg_{index}: {result_type} = ")}else{format!("{prefix}arg_{index} := ")},*value),rendered,this.g("\n",*value)]);}
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
