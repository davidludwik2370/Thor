//! Follow the reference interval predicate and its ownership/evaluation order.
use super::*;
impl Lower<'_, '_> {
    fn interval_static(&self, at:Ref, seen:&mut HashSet<Ref>)->bool {
        if !seen.insert(at){return false;}
        let source=&self.resolver.sources[at.file];let node=source.node(at.node);
        let child=|name|source.role(at.node,name).map(|node|Ref{file:at.file,node});
        match node.kind.as_str(){
            "Literal"=>true,
            "Paren"|"Unary" if !matches!(node.op.as_str(),"&"|"**")=>child("expr").is_some_and(|expr|self.interval_static(expr,seen)),
            "Binary"=>["left","right"].iter().all(|name|child(name).is_some_and(|expr|self.interval_static(expr,&mut seen.clone()))),
            _=>self.resolver.symbol(at).is_some_and(|index|{
                let symbol=&self.resolver.symbols[index];let source=&self.resolver.sources[symbol.reference.file];
                source.node(symbol.reference.node).kind=="Declaration"&&!source.node(symbol.reference.node).mutable
                    &&symbol.value.is_some_and(|node|self.interval_static(Ref{file:symbol.reference.file,node},seen))
            }),
        }
    }
    fn interval_common(&self,id:NodeId,parts:Vec<Code>)->Code {
        let mut parts=parts.into_iter();Code::concat([self.g("(true ? (",id),parts.next().unwrap(),self.g(") : (true ? (",id),parts.next().unwrap(),self.g(") : (",id),parts.next().unwrap(),self.g(")))",id)])
    }
    fn interval_predicate(&self,id:NodeId,interval:NodeId,parts:Vec<Code>)->Code{
        let value=&parts[0];let low=&parts[1];let high=&parts[2];let upper=if self.source.node(interval).op=="..="{"<="}else{"<"};
        Code::concat([self.g(if self.source.node(id).op=="not_in"{"!(("}else{"(("},id),value.clone(),self.g(") >= (",id),low.clone(),self.g(") && (",id),value.clone(),self.g(format!(") {upper} ("),id),high.clone(),self.g("))",id)])
    }
    pub(super) fn interval_membership(&mut self,id:NodeId,interval:NodeId,constant:bool)->Result<Code>{
        self.default=true;
        let operands=[self.role(id,"left")?,self.role(interval,"left")?,self.role(interval,"right")?];
        if constant||operands.iter().all(|operand|self.interval_static(self.reference(*operand),&mut HashSet::new())){
            let parts=operands.iter().map(|operand|self.render(*operand,None,true)).collect::<Result<Vec<_>>>()?;
            let common=self.interval_common(id,parts.clone());let type_of=self.native("type_of");
            let valid=Code::concat([self.g(format!("{type_of}({}default_value((",self.prefix),id),common.clone(),self.g(")==(",id),common,self.g("))) == bool",id)]).annotated("range-type");
            let predicate=self.interval_predicate(id,interval,parts);
            return Ok(Code::concat([self.g("((",id),valid,self.g(") && (",id),predicate,self.g("))",id)]));
        }
        let (name,args)=self.closed(id,"range_contains",Some("bool"),|this|{
            this.querying+=1;
            let parts=operands.iter().map(|operand|this.render(*operand,None,false)).collect::<Result<Vec<_>>>();
            this.querying-=1;
            let common=this.interval_common(id,parts?);let type_of=this.native("type_of");
            let typ=Code::concat([this.g(format!("{type_of}({}default_value(",this.prefix),id),common,this.g("))",id)]).annotated("range-type");
            let hint=Hint{code:typ,semantic:None,element_only:false,plain_array:false};
            let mut code=vec![];let values=operands.iter().map(|operand|this.temporary_evaluate(*operand,&mut code,Some(hint.clone()))).collect::<Result<Vec<_>>>()?;
            let predicate=this.interval_predicate(id,interval,values.iter().map(|value|this.g(&value.value,id)).collect());
            let result=this.temporary_name("range_result");code.extend([this.g(format!("{result} := "),id),predicate,this.g("\n",id)]);
            for value in values.iter().rev(){this.temporary_release(value,&mut code,false)?;}
            code.push(this.g(format!("return {result}"),id));Ok(Code::concat(code))
        })?;
        Ok(self.helper_call(id,&name,args))
    }
}
