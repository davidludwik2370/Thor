//! Result shapes of compiler-defined primitives. These are type facts, never
//! effect exemptions: callers must still validate every evaluated argument.
use super::*;

impl Resolver<'_> {
    pub(super) fn builtin_result_type(&mut self, call: Ref, index: usize, context: &Substitutions) -> Option<Type> {
        if let Some(typ)=self.intrinsic_result_type(call,index,context){return Some(typ);}
        let source=&self.sources[call.file];
        let callee=Ref{node:source.role(call.node,"callee")?,..call};
        if let Some(symbol)=self.symbol(callee).map(|i|&self.symbols[i]) {
            if symbol.parameter || symbol.result || !symbol.constant || symbol.immutable {return None;}
            if let Some(node)=symbol.value {
                let owner=&self.sources[symbol.reference.file];
                if !matches!(owner.node(node).kind.as_str(),"Ident"|"Selector"|"Paren") {
                    // Ordinary procedure/result queries dominate real projects.
                    // Only compiler builtin declarations need classification.
                    let root=owner.info["odin_root"].as_str()?;
                    if owner.path.parent()!=Some(std::path::Path::new(root).join("base/builtin").as_path()){return None;}
                }
            }
        }
        let name=crate::borroweval::Evaluator {resolver:self,arena:&mut crate::borrowflow::Arena::default()}.builtin(call)?;
        if name=="typeid_of"{return (index==0).then(||Type::scalar("typeid"));}
        if name=="type_info_of"{
            if index!=0{return None;}
            let typ=self.package_symbol(call,"base:runtime","Type_Info").and_then(|i|{
                let symbol=&self.symbols[i];symbol.value.map(|node|Ref{node,file:symbol.reference.file})
            }).map(|at|self.specialized_type(at,context)).unwrap_or(Type::Unknown);
            return Some(Type::Pointer(Box::new(typ)));
        }
        if matches!(name.as_str(),"size_of"|"align_of"|"offset_of") {
            return (index==0).then(||Type::Scalar{name:if name=="offset_of"{"uintptr"}else{"int"}.into(),untyped:name!="offset_of"});
        }
        if !matches!(name.as_str(),"expand_values"|"compress_values"|"soa_unzip"|"min"|"max"|"abs"|"clamp"|"conj"|"real"|"imag"|"jmag"|"kmag"|"complex"|"quaternion"|"swizzle"|"raw_data"|"copy"|"append"|"non_zero_append"|"append_nothing"|"resize"|"non_zero_resize"|"inject_at"|"assign_at"|"pop"|"pop_safe"|"pop_front"|"pop_front_safe"){return None;}
        let source=&self.sources[call.file];
        let args=source.group(call.node,"args").iter().map(|node| Ref{node:source.role(*node,"value").unwrap_or(*node),..call}).collect::<Vec<_>>();
        let first=*args.first()?;
        let typ=self.specialized_type(first,context);
        let mut base=&typ;
        while let Type::Nominal(_,inner)=base {base=inner;}
        let mut container=base;
        while let Type::Pointer(inner)|Type::Nominal(_,inner)=container{container=inner;}
        if matches!(container,Type::Fixed{..}){
            match name.as_str(){
                "append"|"non_zero_append"=>return (index==0).then(||Type::scalar("int")),
                "append_nothing"=>return match index{0=>Some(Type::scalar("int")),1=>Some(Type::scalar("bool")),_=>None},
                "resize"|"non_zero_resize"|"inject_at"|"assign_at"=>return (index==0).then(||Type::scalar("bool")),
                _=>{},
            }
        }
        if matches!(container,Type::Fixed{..}|Type::Dynamic(_)){
            if ["pop","pop_front","pop_safe","pop_front_safe"].contains(&name.as_str()){
                return match index{0=>container.element().cloned(),1 if name.ends_with("_safe")=>Some(Type::scalar("bool")),_=>None};
            }
        }
        match name.as_str() {
            "expand_values" => Some(if let Type::Array{elem,length}=base {
                if length.is_some_and(|length|(index as i128)<length) {*elem.clone()}else{Type::Unknown}
            } else {self.record_fields(&typ).get(index).map(|(_,typ)|typ.clone()).unwrap_or(Type::Unknown)}),
            "soa_unzip" => Some(if let Type::Soa(elem)=base {
                self.record_fields(elem).get(index).map(|(_,typ)|Type::Slice(Box::new(typ.clone()))).unwrap_or(Type::Unknown)
            }else{Type::Unknown}),
            _ if index!=0 => None,
            "min"|"max" => {
                let packed=source.node(call.node).spans.get("ellipsis").is_some_and(|span|source.node(first.node).span.start>=span.end);
                let spread=source.node(first.node).kind=="Unary" && source.node(first.node).op=="**";
                Some(if packed||spread {typ.element().cloned().unwrap_or(Type::Unknown)}else{self.numeric_builtin_type(&args,context,typ)})
            }
            "abs"|"clamp"|"conj" => Some(if name=="abs" {component_type(typ)}else if name=="clamp"{self.numeric_builtin_type(&args,context,typ)}else{typ}),
            "real"|"imag"|"jmag"|"kmag" => Some(component_type(typ)),
            "complex"|"quaternion" => {
                if typ.is_unknown(){return Some(typ);}
                let typ=self.numeric_builtin_type(&args,context,typ);
                let Type::Scalar{name:scalar,..}=&typ else{return Some(Type::Unknown);};
                let bits=match scalar.as_str(){"f16"=>16,"f32"=>32,_=>64};
                Some(Type::scalar(&format!("{}{}",name,bits*if name=="complex"{2}else{4})))
            }
            "swizzle" => Some(match base {
                Type::Array{elem,..}=>Type::Array{elem:elem.clone(),length:Some(args.len().saturating_sub(1) as i128)},
                Type::Vector{elem,..}=>Type::Vector{elem:elem.clone(),length:Some(args.len().saturating_sub(1) as i128)},
                _=>Type::Unknown,
            }),
            "compress_values" => {
                let mut groups=vec![];
                for arg in &args {
                    let source=&self.sources[arg.file];
                    let mut count=1;
                    if source.node(arg.node).kind=="Call" {
                        let plans=self.call_plans(*arg,context);
                        if plans.len()==1 {count=self.signature_layout(plans[0].signature).results.len().max(1);}
                        else if source.role(arg.node,"callee").is_some_and(|node|self.native_builtin_alias(Ref{node,..*arg},"expand_values")) {
                            if let Some(node)=source.group(arg.node,"args").first() {
                                let expanded=self.specialized_type(Ref{node:*node,..*arg},context);
                                if let Type::Array{elem,length:Some(n)}=expanded {
                                    groups.push((*elem,usize::try_from(n).ok()?));
                                    continue;
                                }
                                count=self.record_fields(&expanded).len();
                            }
                        }
                    }
                    for index in 0..count {groups.push((self.specialized_value_result(*arg,index,context,&mut HashSet::new()).defaulted(),1));}
                }
                let Some((first,_))=groups.first() else{return Some(Type::Unknown);};
                let count=groups.iter().try_fold(0usize,|n,(_,count)|n.checked_add(*count))?;
                if count==1{return Some(first.clone());}
                if groups.iter().all(|(t,_)|t==first){return Some(Type::Array{elem:Box::new(first.clone()),length:Some(count as i128)});}
                // A heterogeneous tuple needs individual field identities. Bound
                // that metadata by source size; homogeneous expansions stay O(1).
                if count>self.sources[call.file].nodes.len().saturating_mul(4).max(4096){return Some(Type::Unknown);}
                let types=groups.into_iter().flat_map(|(typ,n)|std::iter::repeat_n(typ,n));
                Some(Type::Anonymous(types.enumerate().map(|(i,t)|(format!("v{i}"),t)).collect()))
            }
            "raw_data" => Some(Type::Pointer(Box::new(if typ.is_string(){Type::scalar("u8")}else{typ.element().cloned().unwrap_or(Type::Unknown)}))),
            "copy" => Some(Type::scalar("int")),
            _ => None,
        }
    }
    fn numeric_builtin_type(&mut self,args:&[Ref],context:&Substitutions,mut typ:Type)->Type {
        for arg in args.iter().skip(1){
            let next=self.specialized_type(*arg,context);
            if typ.untyped() && (!next.untyped() || matches!(&next,Type::Scalar{name,..} if name.starts_with('f'))){typ=next;}
        }
        typ
    }
}
fn component_type(typ:Type)->Type {
    let mut base=&typ;while let Type::Nominal(_,inner)=base{base=inner;}
    if let Type::Scalar{name,..}=base {
        let float=match name.as_str(){"complex32"|"quaternion64"=>Some("f16"),"complex64"|"quaternion128"=>Some("f32"),"complex128"|"quaternion256"=>Some("f64"),_=>None};
        if let Some(float)=float{return Type::scalar(float);}
    }
    typ
}
