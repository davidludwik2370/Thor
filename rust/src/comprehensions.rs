//! One-clause collection construction with native loops and checked allocation.
use super::*;
impl Lower<'_, '_> {
    fn comp_interval(&self, mut id: NodeId) -> Option<NodeId> {
        while self.source.node(id).kind == "Paren" {
            id = self.source.role(id, "expr")?;
        }
        (self.source.node(id).kind == "Binary"
            && matches!(self.source.node(id).op.as_str(), ".." | "..<" | "..="))
        .then_some(id)
    }
    fn comp_query(&mut self, id: NodeId) -> Result<Code> {
        self.querying += 1;
        let code = self.render(id, None, false);
        self.querying -= 1;
        code
    }
    pub(super) fn comp_target(&mut self, id: NodeId, expected: Option<Hint>) -> Result<Code> {
        let s = self.source;
        if let Some(annotation) = s.role(id, "type") {
            return self.render(annotation, None, true);
        }
        if let Some(expected) = expected.filter(|hint| !hint.element_only) {
            return Ok(expected.code);
        }
        if let Some(annotation) = s.comprehension_annotation(id) {
            let typ = self.typ(annotation);
            if matches!(typ, Type::Array { .. } | Type::Slice(_)) {
                self.meta = true;
                let annotation = self.render(annotation, None, true)?;
                return Ok(Code::concat([
                    self.g(format!("[]{}meta.type_elem_type(", self.prefix), id),
                    annotation,
                    self.g(")", id),
                ]));
            }
        }
        let source = self.role(id, "expr")?;
        let loop_ = self.role(id, "loop")?;
        let output = self.role(id, "output")?;
        let module = self.module("comprehension");
        let type_of = self.native("type_of");
        self.default = true;
        self.meta = true;
        let mut types = vec![];
        if let Some(range) = self.comp_interval(source) {
            if let Some(annotation) = s.role(loop_, "value_type") {
                types.push(self.render(annotation, None, true)?);
            } else {
                let low = self.comp_query(self.role(range, "left")?)?;
                let high = self.comp_query(self.role(range, "right")?)?;
                types.push(Code::concat([
                    self.g(
                        format!("{type_of}({}default_value(true ? (", self.prefix),
                        id,
                    ),
                    low,
                    self.g(") : (", id),
                    high,
                    self.g(")))", id),
                ]));
            }
            types.push(self.g("int", id));
        } else if self.resolver.denotes_type(self.reference(source)) {
            types.push(self.render(source, None, true)?);
            types.push(self.g("int", id));
        } else {
            let value = self.comp_query(source)?;
            types.push(Code::concat([
                self.g(format!("{type_of}({module}.first("), id),
                value.clone(),
                self.g("))", id),
            ]));
            types.push(Code::concat([
                self.g(format!("{type_of}({module}.second_enum("), id),
                value.clone(),
                self.g(
                    format!(
                        ") when {}meta.type_is_enumerated_array({type_of}(",
                        self.prefix
                    ),
                    id,
                ),
                value.clone(),
                self.g(format!(")) else {module}.second("), id),
                value,
                self.g("))", id),
            ]));
        }
        let mut aliases = HashMap::new();
        for (index, name) in s.group(loop_, "names").iter().enumerate() {
            let symbol = self
                .resolver
                .symbols
                .iter()
                .enumerate()
                .find(|(_, sym)| {
                    sym.reference == self.reference(loop_)
                        && sym.name == s.text(*name).trim_start_matches('&')
                })
                .map(|(index, _)| index);
            if let Some(symbol) = symbol {
                let annotation = s.role(
                    loop_,
                    if index == 0 {
                        "value_type"
                    } else {
                        "index_type"
                    },
                );
                let typ = if let Some(annotation) = annotation {
                    self.render(annotation, None, true)?
                } else {
                    types
                        .get(index)
                        .cloned()
                        .unwrap_or_else(|| self.g("int", id))
                };
                aliases.insert(
                    symbol,
                    Code::concat([self.g("(", *name), typ, self.g("){}", *name)]),
                );
            }
        }
        self.captures.push(aliases);
        let mut replaced = vec![];
        let witness = (|| -> Result<Code> {
            let descendants = std::iter::once(output)
                .chain(s.descendants(output))
                .collect::<Vec<_>>();
            for call in descendants.into_iter().rev() {
                if s.node(call).kind == "Interpolation" {
                    self.switch_replacements
                        .insert(call, self.g("string{}", call));
                    replaced.push(call);
                } else if s.node(call).kind == "Call" {
                    let callee = self.role(call, "callee")?;
                    if let Some(signature) = self.resolver.signature(self.reference(callee)) {
                        let count = self.resolver.fields(signature, "results").len();
                        let rendered = self.comp_query(call)?;
                        let query = |i| {
                            Code::concat([self.g(format!("({}meta.type_proc_return_type({type_of}({}meta.procedure_of(",self.prefix,self.prefix),call),rendered.clone(),self.g(format!(")),{i})){{}}"),call)])
                        };
                        let mut parent = call;
                        while let Some(p) =
                            s.node(parent).parent.filter(|p| s.node(*p).kind == "Paren")
                        {
                            parent = p;
                        }
                        let optional = s.node(parent).parent.is_some_and(|p| {
                            (s.node(p).kind == "OrElse" && s.role(p, "x") == Some(parent))
                                || (matches!(
                                    s.node(p).kind.as_str(),
                                    "OrPanic" | "OrReturn" | "OrBranch"
                                ) && s.role(p, "expr") == Some(parent))
                        });
                        let optional_single = count == 2
                            && (self.resolver.sources[signature.file]
                                .node(signature.node)
                                .data["optional_ok"]
                                == true
                                || self.resolver.sources[signature.file]
                                    .node(signature.node)
                                    .data["optional_allocator_error"]
                                    == true);
                        let code = if optional && count == 2 {
                            Code::concat([
                                self.g(format!("{module}.optional_pair("), call),
                                query(0),
                                self.g(", ", call),
                                query(1),
                                self.g(")", call),
                            ])
                        } else if count == 1 || optional_single {
                            query(0)
                        } else {
                            continue;
                        };
                        self.switch_replacements.insert(call, code);
                        replaced.push(call);
                    }
                }
            }
            let rendered = self.comp_query(output)?;
            Ok(if s.node(id).data["spread"] == true {
                Code::concat([
                    self.g(format!("[]{type_of}({module}.first("), id),
                    rendered,
                    self.g("))", id),
                ])
            } else {
                Code::concat([
                    self.g(format!("[]{type_of}({}default_value(", self.prefix), id),
                    rendered,
                    self.g("))", id),
                ])
            })
        })();
        for node in replaced {
            self.switch_replacements.remove(&node);
        }
        self.captures.pop();
        witness
    }
    pub(super) fn comprehension(
        &mut self,
        id: NodeId,
        expected: Option<Hint>,
        constant: bool,
    ) -> Result<Code> {
        if constant {
            return Err(self.unsupported(id, "compile-time comprehension evaluation"));
        }
        let s = self.source;
        let loop_ = self.role(id, "loop")?;
        let source = self.role(id, "expr")?;
        let output = self.role(id, "output")?;
        let kind = if let Some(annotation) = s.role(id, "type") {
            let mut typ = self.resolver.specialized_type(
                self.reference(annotation),
                &crate::semantics::Substitutions::new(),
            );
            while let Type::Nominal(_, base) = typ {
                typ = *base;
            }
            match typ {
                Type::Slice(_) => "slice",
                Type::Dynamic(_) => "dynamic",
                Type::Set(_) => "set",
                Type::Map { .. } => "map",
                _ => "invalid",
            }
        } else {
            "slice"
        };
        if kind == "invalid" {
            return Ok(self.error_token(id,"Comprehensions construct slices, dynamic arrays, sets or maps; omit the type for a compile-time fixed array."));
        }
        let key = s.role(id, "key");
        let spread = s.node(id).data["spread"] == true;
        if kind == "map" && key.is_none() && !spread {
            return Ok(self.error_token(id, "A map comprehension requires key = value or ..map."));
        }
        if let Some(key) = key {
            if kind != "map" || spread {
                return Ok(self.error_token(key,"Only map comprehensions use key = value; unpacking contributes complete entries."));
            }
        }
        if s.node(source).kind == "Call" {
            let callee = self.role(source, "callee")?;
            if let Some(signature) = self.resolver.signature(self.reference(callee)) {
                if self.resolver.fields(signature, "results").len() > 1 {
                    return Ok(self.error_token(source,"A comprehension source must be a collection, enum or range; iterator procedures with multiple results are not supported."));
                }
            }
        }
        let names = s.group(loop_, "names").to_vec();
        let value_type = s.role(loop_, "value_type");
        let index_type = s.role(loop_, "index_type");
        let interval = self.comp_interval(source);
        if let Some(annotation) = value_type.filter(|_| interval.is_none()) {
            return Ok(self.error_token(annotation,"A typed value binding is supported only for numeric ranges; collection element types are inferred."));
        }
        if let Some(annotation) = index_type.filter(|_| interval.is_some()) {
            return Ok(self.error_token(annotation,"A typed index binding is supported only for arrays and slices, not numeric ranges."));
        }
        if names.len() == 2
            && s.text(names[0]).trim_start_matches('&') == s.text(names[1])
            && s.text(names[1]) != "_"
        {
            return Ok(self.error_token(
                names[1],
                &format!("Duplicate comprehension binding: {}", s.text(names[1])),
            ));
        }
        let semantic = expected.as_ref().and_then(|hint|hint.semantic.clone()).unwrap_or_else(||self.typ(id));
        let target = self.comp_target(id, expected)?;
        let module = self.module("comprehension");
        self.meta = true;
        self.default = true;
        let meta = format!("{}meta", self.prefix);
        let type_of = self.native("type_of");
        let builder = format!("{}comprehension_{}", self.prefix, s.node(id).span.start);
        let global = !self.evaluation_mode
            && !s
                .ancestors(id)
                .any(|parent| s.node(parent).kind == "Procedure");
        let (name,mut args)=self.closed(id,"comprehension",Some(""),|this|{
            let result=format!("{}Result",this.prefix);let element=format!("{meta}.type_elem_type({result})");
            let mut code=vec![];
            if global{let runtime=format!("{}comprehension_runtime",this.prefix);this.modules.insert(runtime.clone(),"base:runtime".into());code.push(this.g(format!("context = {runtime}.default_context()\n"),id));}
            code.push(this.g(if matches!(kind,"slice"|"dynamic"){format!("{builder}: [dynamic]{element}\n")}else{format!("{builder}: {result}\n")},id));
            let insert=|this:&Self,value:Code,key:Option<Code>|->Code{
                let name=match kind{"slice"|"dynamic"=>"append_one","set"=>"set_insert",_=>"map_insert"};
                let mut code=vec![this.g(format!("{module}.{name}(&{builder},"),output)];if let Some(key)=key{code.extend([key,this.g(",",output)]);}code.extend([value,this.g(format!(",{})",this.source_location(output)),output)]);Code::concat(code)
            };
            let mut emitted=vec![];
            if spread {
                let part=format!("{builder}_part");let item=format!("{builder}_item");let index=format!("{builder}_key");
                let value=this.render(output,None,false)?;
                emitted.extend([this.g(format!("{part} := "),output),value,this.g("\n",output)]);
                let typ=format!("{type_of}({part})");let sequence=format!("({meta}.type_is_array({typ}) || {meta}.type_is_slice({typ}) || {meta}.type_is_dynamic_array({typ}) || {meta}.type_is_fixed_capacity_dynamic_array({typ}))");
                let (allowed,message,iterator,bindings)=match kind{
                    "map"=>(format!("{meta}.type_is_map({typ})"),"Map comprehension unpacking requires a map.",part.clone(),format!("{index},{item}")),
                    "set"=>{let sets=this.module("sets");let trait_=format!("((({typ}=={sets}.Set({type_of}({sets}.key_witness({part}.table)))) when {meta}.type_is_map({type_of}({part}.table)) else false) when {meta}.type_has_field({typ},\"table\") else false)");(format!("({sequence} || {trait_})"),"Set comprehension unpacking requires an array, slice, dynamic array or set.",format!("{sets}.compiler_iterate({part})"),item.clone())},
                    _=>(sequence,"Sequence comprehension unpacking requires an array, slice or dynamic array.",part.clone(),item.clone()),
                };
                let error=this.error_token(output,message);
                emitted.extend([this.g(format!("{{when !{allowed} {{_ = "),output),error,this.g(format!("}} else {{for {bindings} in {iterator} {{"),output),insert(this,this.g(item,output),if kind=="map"{Some(this.g(index,output))}else{None}),this.g("\n}}}",output)]);
            }else if kind=="map"{
                let key=key.ok_or_else(||Error::new("map key omitted"))?;let key_name=format!("{builder}_key");let value_name=format!("{builder}_value");
                let kt=this.g(format!("{type_of}({module}.first({builder}))"),key);let vt=this.g(format!("{type_of}({module}.second({builder}))"),output);
                let k=this.render(key,Some(Hint{constant_literal:false,semantic:match &semantic{Type::Map{key,..}=>Some(*key.clone()),_=>Some(Type::Generic)},code:kt.clone(),element_only:false,plain_array:false}),false)?;
                let v=this.big_owned(output,Some(Hint{constant_literal:false,semantic:match &semantic{Type::Map{value,..}=>Some(*value.clone()),_=>Some(Type::Generic)},code:vt.clone(),element_only:false,plain_array:false}),false)?;
                emitted.extend([this.g(format!("{key_name}: "),key),kt,this.g(" = ",key),k,this.g(format!("\n{value_name}: "),output),vt,this.g(" = ",output),v,this.g("\n",output),insert(this,this.g(value_name,output),Some(this.g(key_name,key)))]);
            }else{
                let element=if kind=="set"{let sets=this.module("sets");this.g(format!("{type_of}({sets}.element_witness({builder}))"),output)}else{this.g(element,output)};
                let value=this.big_owned(output,Some(Hint{constant_literal:false,semantic:semantic.element().cloned().or(Some(Type::Generic)),code:element,element_only:false,plain_array:false}),false)?;emitted.push(insert(this,value,None));
            }
            let mut emitted=Code::concat(emitted);
            if let Some(condition)=s.role(id,"cond"){let condition=this.render(condition,None,false)?;emitted=Code::concat([this.g("if ",id),condition,this.g(" {\n",id),emitted,this.g("\n}",id)]);}
            let mut iterable=this.render(source,None,false)?;
            if let Some(interval)=interval{
                let low=format!("{builder}_low");let high=format!("{builder}_high");
                let lo=this.render(this.role(interval,"left")?,None,false)?;let hi=this.render(this.role(interval,"right")?,None,false)?;
                let typ=if let Some(annotation)=value_type{this.render(annotation,None,true)?}else{Code::concat([this.g(format!("{type_of}({}default_value(true ? (",this.prefix),id),lo.clone(),this.g(") : (",id),hi.clone(),this.g(")))",id)])};
                code.extend([this.g(format!("{low}: "),source),typ.clone(),this.g(" = ",source),lo,this.g(format!("\n{high}: "),source),typ,this.g(" = ",source),hi,this.g("\n",source)]);
                iterable=this.g(format!("{low}{}{high}",s.node(interval).op),source);
            }else if !this.resolver.denotes_type(this.reference(source))&&!names.iter().any(|name|s.node(*name).kind=="Unary"){
                let name=format!("{builder}_source");code.extend([this.g(format!("{name} := "),source),iterable,this.g("\n",source)]);
                iterable=if matches!(this.typ(source),Type::Set(_)){let sets=this.module("sets");this.g(format!("{sets}.entries({name})"),source)}else if this.typ(source).is_unknown(){let sets=this.module("sets");this.g(format!("{sets}.compiler_iterate({name})"),source)}else{this.g(name,source)};
            }
            if let Some(annotation)=index_type{
                let sequence=this.module("sequence");let typ=this.render(annotation,None,true)?;
                code.extend([this.g(format!("{sequence}.validate_loop_index({type_of}("),annotation),iterable.clone(),this.g("),",annotation),typ.clone(),this.g(")\n",annotation)]);
                emitted=Code::concat([this.copy(s.node(names[1]).span),this.g(format!(" := {sequence}.checked_loop_index({builder}_index,"),annotation),typ,this.g(format!(",{})\n",this.source_location(annotation)),annotation),emitted]);
            }
            code.push(this.g("for ",id));for (i,name) in names.iter().enumerate(){if i>0{code.push(this.g(", ",id));}code.push(if i==1&&index_type.is_some(){this.g(format!("{builder}_index"),*name)}else{this.copy(s.node(*name).span)});}
            code.extend([this.g(" in ",id),iterable,this.g(" {\n",id),emitted,this.g("\n}\n",id)]);
            code.push(this.g(match kind{"slice"=>format!("return {result}({module}.finish_slice(&{builder},{}))",this.source_location(id)),"dynamic"=>format!("return {result}({builder})"),_=>format!("return {builder}")},id));
            Ok(Code::concat(code))
        })?;
        args.push(target);
        Ok(self.helper_call(id, &name, args))
    }
}
