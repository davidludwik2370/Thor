//! Opt-in ordinary-call folding. Result eligibility preserves native ownership.
use super::*;
impl Scheduler<'_> {
    pub(super) fn self_contained(&mut self, typ:&Type)->bool {
        if let Some(eligible)=self.policy_types.get(typ) {return *eligible;}
        self.policy_types.insert(typ.clone(),false);
        let eligible=match typ {
            Type::Scalar{name,..}=>!["rawptr","any","typeid","cstring","cstring16"].contains(&name.as_str()),
            Type::Enum(_)=>true,
            Type::Nominal(_,base)|Type::Array{elem:base,..}|Type::Fixed{elem:base,..}
            |Type::Matrix{elem:base,..}|Type::Vector{elem:base,..}=>self.self_contained(base),
            Type::Struct(owner)|Type::Record(owner,_) if self.source(*owner).node(owner.node).op=="raw_union"=>false,
            Type::Struct(_)|Type::Record(..)|Type::Anonymous(_)=>self.resolver.record_fields(typ).iter().all(|(_,field)|self.self_contained(field)),
            Type::Union(..)=>self.resolver.union_variants(typ).iter().all(|field|self.self_contained(field)),
            _=>false,
        };
        self.policy_types.insert(typ.clone(),eligible);eligible
    }
    pub(super) fn ordinary_key(&mut self, node:Ref)->Option<(Ref,String)> {
        let plans=self.resolver.call_plans(node,&Substitutions::new());
        let [plan]=plans.as_slice() else{return None;};
        if self.source(plan.procedure).node(plan.procedure.node).data["pure"]==true {return None;}
        let args=self.group(node,"args").iter().map(|arg| {
            if self.kind(*arg)=="FieldValue" {self.role(*arg,"field").map(|f|self.source(f).text(f.node).to_owned()).unwrap_or_default()}
            else {String::new()}
        }).collect::<Vec<_>>();
        // A local procedure can capture enclosing constant/type parameters
        // without having any parameters of its own.
        let lexical=self.specializations.relevant(plan.procedure,&self.resolver);
        Some((plan.procedure,format!("{:?}:{args:?}:{lexical:?}",plan.bindings)))
    }
    pub(super) fn ordinary_eligible(&mut self,node:Ref)->bool {
        if !self.project.options.comptime.aggressive() {return false;}
        let result=self.resolver.type_of(node);
        if !self.self_contained(&result) {return false;}
        let Some(key)=self.ordinary_key(node) else{return false;};
        if let Some(eligible)=self.policy_calls.get(&key) {return *eligible;}
        crate::profile::count("evaluation.policy_verifications",1);
        let facts=crate::purity::Purity::analyze_calls(&mut self.resolver,&[node]);
        let pure=facts.faults.values().all(Vec::is_empty)&&facts.gaps.is_empty();
        let eligible=pure && self.resolver.call_plans(node,&Substitutions::new()).into_iter().next()
            .is_some_and(|plan|crate::noalloc::Check::ordinary_allocation_free(
                &mut self.resolver,plan.procedure,plan.bindings.clone(),&self.project.options.overlays));
        self.policy_calls.insert(key,eligible);eligible
    }
    pub(super) fn required_ordinary_rejection(&mut self,node:Ref)->Result<()> {
        if !self.project.options.comptime.aggressive() || self.kind(node)!="Call"
            || self.value(node).is_some() || self.queried(node) || self.unbound_generic(node)
            || !self.constant_context(node) || self.ordinary_key(node).is_none() {return Ok(());}
        // The enclosing explicit request owns both its result and diagnostics;
        // it need not store a second evaluation on the inner call node.
        if self.source(node).ancestors(node.node).take_while(|id|self.source(node).node(*id).kind!="Procedure")
            .any(|id|self.source(node).node(id).kind=="Run") {return Ok(());}
        if !self.project.options.comptime.overrides_runtime() {
            if self.source(node).ancestors(node.node).any(|id|self.source(node).node(id).kind=="Runtime") {return Ok(());}
            let plans=self.resolver.call_plans(node,&Substitutions::new());
            if let [plan]=plans.as_slice() {
                if self.has_runtime && self.runtime_barrier(plan.procedure,&plan.bindings) {return Ok(());}
            }
        }
        if self.active(node,false)?!=Some(true) {return Ok(());}
        for arg in self.group(node,"args") {if !self.known(arg)? {return Ok(());}}
        Err(self.fail(node,"Required compile-time call is not eligible for automatic ordinary-procedure evaluation. It must be pure, allocation-free and return a self-contained value. Explicit #run requests the existing compile-time execution contract and reports its eligibility restrictions."))
    }
}
