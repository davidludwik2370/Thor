//! Contextual type facts for instantiated analyses. Final overload/type checking
//! stays with Odin; these facts preserve nominal record and scalar identity.
use super::*;
use std::collections::BTreeMap;
pub type Substitutions = BTreeMap<String, Type>;
// Native aggregate arithmetic is distinct from elementwise array arithmetic.
// Result dimensions and the matrix layout participate in later generic proofs.
pub(super) fn arithmetic_shape(op:&str,left:&Type,right:&Type)->Option<Type>{
    fn base(mut typ:&Type)->&Type{while let Type::Nominal(_,inner)=typ{typ=inner;}typ}
    let a=base(left);let b=base(right);
    if op=="*"{
        match (a,b){
            (Type::Matrix{elem,rows,columns:_,row_major},Type::Matrix{columns,..})=>{
                return Some(if a==b{if matches!(right,Type::Nominal(..))&&!matches!(left,Type::Nominal(..)){right.clone()}else{left.clone()}}
                    else{Type::Matrix{elem:elem.clone(),rows:*rows,columns:*columns,row_major:*row_major}});
            },
            (Type::Matrix{elem,rows,row_major,..},Type::Array{length,..})=>{
                return Some(match (rows,length){(Some(rows),Some(length)) if rows==length=>right.clone(),(Some(_),Some(_))=>Type::Matrix{elem:elem.clone(),rows:*rows,columns:Some(1),row_major:*row_major},_=>Type::Unknown});
            },
            (Type::Array{length,..},Type::Matrix{elem,columns,row_major,..})=>{
                return Some(match (columns,length){(Some(columns),Some(length)) if columns==length=>left.clone(),(Some(_),Some(_))=>Type::Matrix{elem:elem.clone(),rows:Some(1),columns:*columns,row_major:*row_major},_=>Type::Unknown});
            },
            _=>{},
        }
    }
    if matches!(a,Type::Array{..}|Type::Vector{..}|Type::Matrix{..}) && b.numeric(){return Some(left.clone());}
    if a.numeric() && matches!(b,Type::Array{..}|Type::Vector{..}|Type::Matrix{..}){return Some(right.clone());}
    None
}

impl Resolver<'_> {
    /// Preserve the native distinction erased by the compact Pointer shape.
    /// A pointer to an array auto-dereferences; a multipointer to arrays does not.
    pub(super) fn multipointer(&mut self, at:Ref, depth:usize)->Option<bool> {
        self.multipointer_bounded(at, depth, &mut 128)
    }
    fn multipointer_bounded(&mut self, at:Ref, depth:usize, budget:&mut usize)->Option<bool> {
        *budget = budget.checked_sub(1)?;
        if depth>32{return None;}
        let s=&self.sources[at.file];let n=s.node(at.node);
        let role=|name|s.role(at.node,name).map(|node|Ref{node,..at});
        if n.kind=="Call" && role("callee").is_some_and(|callee|self.native_builtin_alias(callee,"raw_data")){return Some(true);}
        if matches!(n.kind.as_str(), "Ternary" | "WhenExpr" | "OrElse") {
            let left = role("x")?;
            let right = role("y")?;
            let a = self.multipointer_bounded(left, depth + 1, budget);
            let b = self.multipointer_bounded(right, depth + 1, budget);
            return if a == b { a } else { None };
        }
        let next=match n.kind.as_str(){
            "PointerType" if n.op == "soa" => return None,
            "PointerType"=>return Some(false),"MultiPointerType"=>return Some(true),
            "Unary" if n.op=="&"=>return Some(false),
            "Paren"|"HelperType"=>role("expr"),"Cast"=>role("type"),
            "Slice" if s.role(at.node,"high").is_none()=>role("expr"),
            "Binary" if n.op=="/"=>role("right"),
            "Ident"|"Selector"=>self.symbol(at).and_then(|i|{let sym=&self.symbols[i];sym.annotation.or(sym.value).map(|node|Ref{file:sym.reference.file,node})}),
            "Call"=>role("callee").and_then(|callee|{
                if self.denotes_type(callee){Some(callee)}else{
                    self.signature(callee).and_then(|signature|self.fields(signature,"results").first().and_then(|(_,t)|*t))
                }
            }),
            _=>None,
        }?;
        self.multipointer_bounded(next,depth+1,budget)
    }
    pub(super) fn slice_type(&mut self, operand:Ref, typ:Type)->Type {
        let mut base=&typ;while let Type::Nominal(_,inner)=base{base=inner;}
        if typ.is_string(){return typ;}
        if let Type::Pointer(inner)=base {
            return match self.multipointer(operand,0){
                Some(true)=>{
                    let source=&self.sources[operand.file];
                    if source.node(operand.node).parent.is_some_and(|parent|source.node(parent).kind=="Slice"&&source.role(parent,"high").is_none()){
                        typ
                    }else{Type::Slice(inner.clone())}
                },
                Some(false)=>if inner.is_string(){*inner.clone()}else{Type::Slice(Box::new(inner.element().cloned().unwrap_or(Type::Unknown)))},
                None=>Type::Unknown,
            };
        }
        if matches!(base,Type::Soa(_)){return typ;}
        if let Type::SoaStorage(storage) = base {
            return Type::Soa(Box::new(storage.element().cloned().unwrap_or(Type::Unknown)));
        }
        Type::Slice(Box::new(typ.element().cloned().unwrap_or(Type::Unknown)))
    }
    pub(super) fn index_type(&mut self, operand:Ref, typ:Type)->Type {
        let mut base=&typ;while let Type::Nominal(_,inner)=base{base=inner;}
        if let Some(unit) = typ.string_code_unit() { return unit; }
        if let Type::Pointer(inner)=base {
            return match self.multipointer(operand,0){
                Some(true)=>*inner.clone(),
                Some(false) => inner.string_code_unit().or_else(|| inner.indexed_element().cloned()).unwrap_or(Type::Unknown),
                None=>Type::Unknown,
            };
        }
        typ.indexed_element().cloned().unwrap_or(Type::Unknown)
    }
    /// Storage affected by freezing a value, including an inline array that
    /// can later be sliced. Pointer/map/dynamic-array targets retain Odin access.
    pub fn frozen_slice_storage(&mut self, typ: &Type) -> bool {
        let mut pending = vec![typ.clone()];
        let mut seen = HashSet::new();
        while let Some(typ) = pending.pop() {
            if !seen.insert(typ.clone()) { continue; }
            match typ {
                Type::Slice(_) | Type::Soa(_) | Type::Array {..} | Type::Fixed {..}
                | Type::Unknown | Type::Generic => return true,
                Type::Nominal(_, inner) | Type::SoaStorage(inner) => pending.push(*inner),
                Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) =>
                    pending.extend(self.shared_record_fields(&typ).iter().map(|(_, typ)| typ.clone())),
                Type::Union(..) => pending.extend(self.union_variants(&typ)),
                _ => {}
            }
        }
        false
    }

    pub fn union_variants(&mut self, typ: &Type) -> Vec<Type> {
        let Type::Union(owner, bindings) = typ else { return vec![]; };
        let bindings = bindings.iter().cloned().collect();
        self.sources[owner.file]
            .group(owner.node, "variants")
            .to_vec()
            .into_iter()
            .map(|node| self.specialized_type(Ref { file: owner.file, node }, &bindings))
            .collect()
    }
    pub fn record_fields(&mut self, typ: &Type) -> Vec<(String, Type)> {
        self.shared_record_fields(typ).to_vec()
    }
    pub(crate) fn shared_record_fields(&mut self, typ: &Type) -> RecordFields {
        let id = self.type_id(typ);
        if let Some(fields) = self.metadata.as_ref().and_then(|memo| memo.fields.get(&id))
            .or_else(|| self.base_metadata.as_ref()?.fields.get(&id)) {
            crate::profile::count("resolver.field_hits", 1);
            return fields.clone();
        }
        let stable = self.active.is_empty() && self.planning.is_empty();
        let fields: RecordFields = self.record_fields_uncached(typ).into();
        if stable && !self.has_demanded_packages() {
            if let Some(memo) = &mut self.metadata { memo.fields.insert(id, fields.clone()); }
        }
        fields
    }
    fn record_fields_uncached(&mut self, typ: &Type) -> Vec<(String, Type)> {
        let (owner, bindings) = match typ {
            Type::Anonymous(fields) => return fields.clone(),
            Type::Struct(owner) => (*owner, Substitutions::new()),
            Type::Record(owner, bindings) => (*owner, bindings.iter().cloned().collect()),
            Type::Nominal(_, base) | Type::Pointer(base) => return self.record_fields(base),
            _ => return vec![],
        };
        let s = &self.sources[owner.file];
        let fields = s.role(owner.node, "fields")
            .map(|fields| s.node(fields).children.as_slice())
            .unwrap_or_else(|| s.group(owner.node, "fields"));
        let mut result = vec![];
        for field in fields {
            if s.node(*field).kind != "Field" {
                continue;
            }
            let ty = s
                .role(*field, "type")
                .map(|node| {
                    self.specialized_type(
                        Ref {
                            file: owner.file,
                            node,
                        },
                        &bindings,
                    )
                })
                .unwrap_or(Type::Unknown);
            for name in s.group(*field, "names") {
                result.push((s.text(*name).into(), ty.clone()));
            }
        }
        result
    }
    pub fn member_type(&mut self, typ: &Type, name: &str) -> Type {
        let id = self.type_id(typ);
        if let Some(member) = self.metadata.as_ref().and_then(|memo| memo.members.get(&id).and_then(|members| members.get(name)))
            .or_else(|| self.base_metadata.as_ref()?.members.get(&id)?.get(name)) {
            crate::profile::count("resolver.member_hits", 1);
            return member.clone();
        }
        let stable = self.active.is_empty() && self.planning.is_empty();
        let member = self.member_type_uncached(typ, name);
        if stable && !self.has_demanded_packages() {
            if let Some(memo) = &mut self.metadata { memo.members.entry(id).or_default().insert(name.to_owned(), member.clone()); }
        }
        member
    }
    fn member_type_uncached(&mut self, typ: &Type, name: &str) -> Type {
        match typ {
            Type::Scalar{name:scalar,..} if scalar=="any" => match name {"id"=>Type::scalar("typeid"),"data"=>Type::scalar("rawptr"),_=>Type::Unknown},
            Type::BigRational if matches!(name,"numerator"|"denominator") => Type::BigInteger,
            Type::Nominal(_, base) | Type::Pointer(base) => self.member_type(base, name),
            Type::Array { elem, length } | Type::Vector{elem,length}
                if !name.is_empty() && name.chars().all(|c| "xyzwrgba".contains(c)) =>
            {
                let positions = name
                    .chars()
                    .map(|c| "xyzw".find(c).or_else(|| "rgba".find(c)).unwrap_or(4))
                    .collect::<Vec<_>>();
                if length.is_some_and(|length| {
                    positions.iter().any(|position| *position as i128 >= length)
                }) {
                    return Type::Unknown;
                }
                if name.len() == 1 {
                    *elem.clone()
                } else {
                    if matches!(typ,Type::Vector{..}){Type::Vector{elem:elem.clone(),length:Some(name.len() as i128)}}else{Type::Array {
                        elem: elem.clone(),
                        length: Some(name.len() as i128),
                    }}
                }
            }
            Type::SoaStorage(storage) => match storage.as_ref() {
                Type::Array { elem, length } => Type::Array {
                    elem: Box::new(self.member_type(elem, name)), length: *length,
                },
                Type::Dynamic(elem) => Type::Pointer(Box::new(self.member_type(elem, name))),
                _ => Type::Unknown,
            },
            Type::Soa(elem) => Type::Pointer(Box::new(self.member_type(elem, name))),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                let Some(path) = self.member_path(typ, name) else {
                    return Type::Unknown;
                };
                let mut typ = typ.clone();
                for component in path {
                    typ = self
                        .shared_record_fields(&typ)
                        .iter()
                        .find(|(field, _)| field == &component)
                        .map(|(_, typ)| typ.clone())
                        .unwrap_or(Type::Unknown);
                }
                typ
            }
            Type::Enum(owner) => Type::Enum(*owner),
            _ => Type::Unknown,
        }
    }
    /// Retain the actual storage path through promoted `using` fields.
    pub fn member_path(&mut self, typ: &Type, name: &str) -> Option<Vec<String>> {
        let id = self.type_id(typ);
        if let Some(path) = self.metadata.as_ref().and_then(|memo| memo.paths.get(&id).and_then(|paths| paths.get(name)))
            .or_else(|| self.base_metadata.as_ref()?.paths.get(&id)?.get(name)) {
            crate::profile::count("resolver.path_hits", 1);
            return path.clone();
        }
        let stable = self.active.is_empty() && self.planning.is_empty();
        let path = self.member_path_seen(typ, name, &mut HashSet::new());
        if stable && !self.has_demanded_packages() {
            if let Some(memo) = &mut self.metadata { memo.paths.entry(id).or_default().insert(name.to_owned(), path.clone()); }
        }
        path
    }
    fn member_path_seen(
        &mut self,
        typ: &Type,
        name: &str,
        seen: &mut HashSet<Ref>,
    ) -> Option<Vec<String>> {
        let owner = match typ {
            Type::Nominal(_, base) | Type::Pointer(base) => {
                return self.member_path_seen(base, name, seen)
            }
            Type::Anonymous(fields) => {
                return fields
                    .iter()
                    .any(|(field, _)| field == name)
                    .then(|| vec![name.into()])
            }
            Type::Struct(owner) | Type::Record(owner, _) => *owner,
            _ => return None,
        };
        if !seen.insert(owner) {
            return None;
        }
        let fields = self.shared_record_fields(typ);
        if fields.iter().any(|(field, _)| field == name) {
            return Some(vec![name.into()]);
        }
        let s = &self.sources[owner.file];
        let block = s.role(owner.node, "fields")?;
        let promoted = s
            .node(block)
            .children
            .iter()
            .filter(|field| s.node(**field).data["using_field"] == true)
            .flat_map(|field| s.group(*field, "names"))
            .map(|name| s.text(*name).to_string())
            .collect::<HashSet<_>>();
        let mut found = None;
        for (field, typ) in fields.iter() {
            if !promoted.contains(field) {
                continue;
            }
            if let Some(mut path) = self.member_path_seen(typ, name, &mut seen.clone()) {
                if found.is_some() {
                    return None;
                }
                path.insert(0, field.clone());
                found = Some(path);
            }
        }
        found
    }
    pub fn specialized_type(&mut self, r: Ref, bindings: &Substitutions) -> Type {
        if let Some(typ) = self.metadata.as_ref().and_then(|memo| memo.specialized.get(&r).and_then(|types| types.get(bindings)))
            .or_else(|| self.base_metadata.as_ref()?.specialized.get(&r)?.get(bindings)) {
            crate::profile::count("resolver.specialized_hits", 1);
            return typ.clone();
        }
        let stable = self.active.is_empty() && self.planning.is_empty();
        let epoch = self.incomplete_plans.get();
        let typ = self.specialize(r, bindings, &mut HashSet::new());
        // Retain the existing top-level partial-shape cache, and also reuse
        // fully resolved nested answers that did not hit a recursion guard.
        if (stable || typ.resolved() && epoch == self.incomplete_plans.get()) && !self.has_demanded_packages() {
            if let Some(memo) = &mut self.metadata { memo.specialized.entry(r).or_default().insert(bindings.clone(), typ.clone()); }
        }
        typ
    }
    pub fn bound_type<'b>(&'b self, r: Ref, bindings: &'b Substitutions) -> Option<&'b Type> {
        if bindings.is_empty() && self.bound_types.is_empty() && self.bound_constants.is_empty() {
            return None;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let name = if n.kind == "PolyType" {
            s.names.get(n.name)
        } else {
            s.text(r.node).trim_start_matches('$')
        };
        if let Some(owner) = s
            .ancestors(r.node)
            .find(|node| s.node(*node).kind == "EnumType")
        {
            if let Some(typ) = bindings.get(&Self::enum_binding_key(
                Ref {
                    file: r.file,
                    node: owner,
                },
                name,
            )) {
                return Some(typ);
            }
        }
        let Some(index) = self.symbol(r) else {
            return bindings.get(name).filter(|_| n.kind == "PolyType");
        };
        let symbol = &self.symbols[index];
        let owner = &self.sources[symbol.reference.file];
        if owner.node(symbol.name_node).kind == "PolyType"
            || symbol.parameter && owner.node(symbol.scope).kind == "StructType"
        {
            if let Some(typ) = bindings.get(name) {
                return Some(typ);
            }
        }
        let key = (symbol.reference, symbol.name.clone());
        self.bound_types
            .get(&key)
            .or_else(|| self.bound_constants.get(&key).map(|(typ, _)| typ))
    }
    pub(super) fn enum_binding_key(owner: Ref, name: &str) -> String {
        format!("#enum:{}:{}:{name}", owner.file, owner.node.0)
    }
    pub fn compiled_type_parameter(&self, annotation: Ref, name: &str) -> bool {
        let s = &self.sources[annotation.file];
        s.node(annotation.node).kind == "TypeidType"
            && s.node(annotation.node).parent.is_some_and(|field| {
                let record = s
                    .node(field)
                    .parent
                    .and_then(|fields| s.node(fields).parent)
                    .is_some_and(|owner| {
                        s.node(owner).kind == "StructType"
                            && s.role(owner, "params") == s.node(field).parent
                    });
                s.group(field, "names").iter().any(|id| {
                    let poly = s.node(*id).kind == "PolyType";
                    (poly || record)
                        && (if poly {
                            s.names.get(s.node(*id).name)
                        } else {
                            s.text(*id)
                        }) == name.trim_start_matches('$')
                })
            })
    }
    fn specialize(&mut self, r: Ref, bindings: &Substitutions, seen: &mut HashSet<Ref>) -> Type {
        if crate::resulttypes::is_query(self,r) {
            return crate::resulttypes::expression(self,r)
                .and_then(|call|crate::resulttypes::resolve(self,call,bindings,None))
                .map(|results|crate::resulttypes::packed_type(&results)).unwrap_or(Type::Unknown);
        }
        if let Some(typ)=self.logical_constant_type(r){return typ;}
        if !seen.insert(r) {
            self.incomplete_plans.set(self.incomplete_plans.get().wrapping_add(1));
            return Type::Unknown;
        }
        if let Some(typ) = self.logical_type(r) { return typ; }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let child = |role: &str| s.role(r.node, role).map(|node| Ref { file: r.file, node });
        if matches!(n.kind.as_str(), "Ident" | "PolyType") {
            if let Some(typ) = self.bound_type(r, bindings).cloned() {
                if typ.is_unknown(){
                    if let Some(annotation)=self.symbol(r).and_then(|i|{
                        let symbol=&self.symbols[i];let source=&self.sources[symbol.reference.file];
                        symbol.annotation.filter(|node|source.node(symbol.name_node).kind=="PolyType"&&source.node(*node).kind!="TypeidType")
                            .map(|node|Ref{file:symbol.reference.file,node})
                    }){return self.specialize(annotation,bindings,seen);}
                }
                if let Type::Constant(value) = typ {
                    if let Some(annotation) = self.symbol(r).and_then(|index| {
                        let symbol = &self.symbols[index];
                        symbol.annotation.map(|node| Ref {
                            file: symbol.reference.file,
                            node,
                        })
                    }) {
                        return self.specialize(annotation, bindings, seen);
                    }
                    return match value {
                        Constant::Integer(_) => Type::scalar("int"),
                        Constant::Boolean(_) => Type::scalar("bool"),
                        Constant::Text(_) => Type::String {
                            name: "string".into(),
                            untyped: false,
                        },
                        Constant::Enum(owner, _) => Type::Enum(owner),
                    };
                }
                return typ;
            }
            if n.kind == "PolyType" {
                return child("specialization")
                    .map(|target| self.specialize(target, bindings, seen))
                    .unwrap_or(Type::Generic);
            }
            if let Some(symbol) = self.context_symbol(r,bindings) {
                let symbol = self.symbols[symbol].clone();
                if let Some(projection) = symbol.projection {
                    return self.projected_type(&projection, bindings);
                }
                if self.sources[symbol.reference.file].node(symbol.reference.node).kind == "TypeSwitch" {
                    let source = &self.sources[r.file];
                    if let Some(case) = source.ancestors(r.node).find(|node| source.node(*node).kind == "Case"
                        && source.node(*node).parent.and_then(|p|source.node(p).parent) == Some(symbol.reference.node)) {
                        let types = source.group(case,"types");
                        if let [node] = types {
                            if source.text(*node) != "nil" {
                                // `switch &value` makes the alias addressable;
                                // its expression type remains the case type.
                                return self.specialize(Ref{node:*node,..r},bindings,seen);
                            }
                        }
                    }
                    if let Some(node)=self.sources[symbol.reference.file].role(symbol.reference.node,"expr") {
                        return self.specialize(Ref{node,file:symbol.reference.file},bindings,seen);
                    }
                }
                if self.sources[symbol.reference.file]
                    .node(symbol.reference.node)
                    .kind
                    == "PolyType"
                    || symbol.annotation.is_some_and(|annotation| {
                        self.compiled_type_parameter(
                            Ref {
                                file: symbol.reference.file,
                                node: annotation,
                            },
                            &symbol.name,
                        )
                    })
                {
                    return Type::Generic;
                }
                if symbol.annotation.is_none() {
                    let source = &self.sources[symbol.reference.file];
                    if source.group(symbol.reference.node, "names").len() > 1
                        && source.group(symbol.reference.node, "values").len() == 1
                    {
                        if let Some(node) = symbol.value {
                            let typ = self.specialized_value_result(
                                Ref {
                                    file: symbol.reference.file,
                                    node,
                                },
                                symbol.index,
                                bindings,
                                seen,
                            );
                            return if symbol.constant {
                                typ
                            } else {
                                typ.defaulted()
                            };
                        }
                    }
                }
                if let Some(node) = symbol.annotation.or(symbol.value) {
                    let typ = self.specialize(
                        Ref {
                            file: symbol.reference.file,
                            node,
                        },
                        bindings,
                        seen,
                    );
                    return if symbol.annotation.is_some() || symbol.constant { typ } else { typ.defaulted() };
                }
                // Range bindings inherit the specialized iterable element,
                // rather than the generic type from an ordinary type query.
                let source = &self.sources[symbol.reference.file];
                if source.node(symbol.reference.node).kind == "Range" {
                    if let Some(node) = source.role(symbol.reference.node, "expr") {
                        let iterable = self.specialize(Ref { file: symbol.reference.file, node }, bindings, seen);
                        return iterable.range_binding(symbol.index);
                    }
                }
            }
        }
        let specialization_division = n.kind == "Binary" && n.op == "/"
            && child("left").is_some_and(|left|self.denotes_type(left));
        let mut subtype = |role: &str| {
            child(role)
                .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                .unwrap_or(Type::Unknown)
        };
        match n.kind.as_str() {
            "ProcedureType" => self.callable_type(r, bindings),
            "Procedure" => child("type").map(|signature| self.callable_type(signature, bindings)).unwrap_or(Type::Unknown),
            "HelperType"=>{let mut typ=subtype("expr");if let Type::Matrix{row_major,..}=&mut typ{if ["row_major","column_major"].contains(&n.op.as_str()){*row_major=n.op=="row_major";}}typ},
            "Paren" | "Run" | "Runtime" => subtype("expr"),
            "Unary" => {
                let typ=subtype("expr");
                if n.op == "&" {Type::Pointer(Box::new(typ))} else if n.op == "!" {Type::scalar("bool")} else {typ}
            }
            "Cast" | "TypeAssertion" => subtype("type"),
            "AutoCast" => subtype("expr"),
            "Slice" => {
                let typ=subtype("expr");
                child("expr").map(|operand|self.slice_type(operand,typ)).unwrap_or(Type::Unknown)
            }
            "Ternary" => {
                let a=subtype("x");let b=subtype("y");
                if a.is_unknown() || a.untyped() && !b.untyped() {b}else{a}
            }
            "Binary" if n.op == "++" => {
                crate::lower::concatenation::result_type(s, r.node, |node| {
                    self.specialize(Ref { node, ..r }, bindings, &mut seen.clone())
                })
            }
            "Binary" if s.thor && !specialization_division && matches!(n.op.as_str(),"+"|"-"|"*"|"/"|"&"|"|"|"~"|"&~"|"<<"|">>"|"**") => {
                let a=subtype("left");let b=subtype("right");
                if a.record_like() {a} else if b.record_like() {b} else {arithmetic_shape(&n.op,&a,&b).unwrap_or_else(||self.type_of(r))}
            }
            "WhenExpr" => {
                if let Some(cond) = child("cond").and_then(|r| self.context_condition(r, bindings))
                {
                    return child(if cond { "x" } else { "y" })
                        .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                        .unwrap_or(Type::Unknown);
                }
                let a = child("x")
                    .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                    .unwrap_or(Type::Unknown);
                let b = child("y")
                    .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                    .unwrap_or(Type::Unknown);
                if a == b {
                    a
                } else {
                    self.type_of(r)
                }
            }
            "Compound" if child("type").is_some() => subtype("type"),
            "Selector" => {
                if let Some(primitive) = self.primitive(r) {
                    return primitive;
                }
                if let Some(symbol) = self.symbol(r) {
                    let symbol = self.symbols[symbol].clone();
                    if let Some(node) = symbol.annotation.or(symbol.value) {
                        return self.specialize(
                            Ref {
                                file: symbol.reference.file,
                                node,
                            },
                            bindings,
                            seen,
                        );
                    }
                }
                let typ = child("expr")
                    .map(|r| self.specialize(r, bindings, &mut seen.clone()))
                    .unwrap_or(Type::Unknown);
                let name = child("field")
                    .map(|r| self.sources[r.file].text(r.node))
                    .unwrap_or("");
                self.member_type(&typ, name)
            }
            "Deref" => subtype("expr").element().cloned().unwrap_or(Type::Unknown),
            "Index" => {
                let typ=subtype("expr");
                child("expr").map(|operand|self.index_type(operand,typ)).unwrap_or(Type::Unknown)
            }
            "MatrixType"=>Type::Matrix{row_major:false,elem:Box::new(subtype("elem")),rows:child("rows").and_then(|r|self.context_integer(r,bindings)),columns:child("columns").and_then(|r|self.context_integer(r,bindings))},
            "MatrixIndex"=>{let typ=subtype("expr");child("expr").map(|v|self.index_type(v,typ)).unwrap_or(Type::Unknown)},
            "ArrayType" => {
                let elem = Box::new(subtype("elem"));
                if let Some(len) = child("len") {
                    let length = self.context_integer(len, bindings);
                    match n.op.as_str() {
                        "soa" => Type::SoaStorage(Box::new(Type::Array { elem, length })),
                        "simd" => Type::Vector { elem, length },
                        _ => Type::Array { elem, length },
                    }
                } else if n.op == "soa" {
                    Type::Soa(elem)
                } else {
                    Type::Slice(elem)
                }
            }
            "DynamicArrayType" => {
                let elem = Box::new(subtype("elem"));
                if n.op == "soa" {
                    Type::SoaStorage(Box::new(Type::Dynamic(elem)))
                } else if let Some(capacity) = child("capacity") {
                    Type::Fixed {
                        elem,
                        length: self.context_integer(capacity, bindings),
                    }
                } else {
                    Type::Dynamic(elem)
                }
            }
            "PointerType" | "MultiPointerType" => Type::Pointer(Box::new(subtype("elem"))),
            "Ellipsis" => Type::Slice(Box::new(subtype("expr"))),
            "MapType" => {
                let key = Box::new(subtype("key"));
                let value = Box::new(subtype("value"));
                Type::Map { key, value }
            }
            "SetType" => Type::Set(Box::new(
                s.group(r.node, "args")
                    .first()
                    .copied()
                    .map(|node| {
                        self.specialize(Ref { file: r.file, node }, bindings, &mut seen.clone())
                    })
                    .unwrap_or(Type::Unknown),
            )),
            "DistinctType" => Type::Nominal(r, Box::new(subtype("type"))),
            "BitSetType" => Type::Nominal(r, Box::new(Type::scalar("bit_set"))),
            "StructType" | "BitFieldType" | "UnionType" => {
                let params = s.role(r.node, "params");
                let names = params
                    .into_iter()
                    .flat_map(|params| s.node(params).children.iter())
                    .flat_map(|field| s.group(*field, "names"))
                    .map(|name| s.text(*name).trim_start_matches('$').to_string())
                    .collect::<Vec<_>>();
                let contents = if n.kind == "UnionType" {
                    s.group(r.node, "variants").to_vec()
                } else {
                    s.role(r.node, "fields").into_iter().collect()
                };
                let captures = contents.into_iter()
                    .flat_map(|field| s.descendants(field))
                    .filter(|node| s.node(*node).kind == "Ident")
                    .filter_map(|node| {
                        let name = s.text(node);
                        if names.iter().any(|parameter| parameter == name) {
                            return None;
                        }
                        let reference = Ref { file: r.file, node };
                        let typ = self
                            .constant_value(reference, bindings, None)
                            .map(Type::Constant)
                            .or_else(|| self.bound_type(reference, bindings).cloned())?;
                        let symbol = &self.symbols[self.symbol(reference)?];
                        let owner = &self.sources[symbol.reference.file];
                        (owner.node(symbol.name_node).kind == "PolyType"
                            && (symbol.reference.file != r.file
                                || !s
                                    .node(r.node)
                                    .span
                                    .contains(owner.node(symbol.reference.node).span)))
                        .then(|| (name.to_string(), typ.clone()))
                    })
                    .collect::<BTreeMap<_, _>>();
                if names.is_empty() && captures.is_empty() {
                    return if n.kind == "UnionType" { Type::Union(r, vec![]) } else { Type::Struct(r) };
                }
                let arguments = names.into_iter().map(|name| {
                    let typ = bindings.get(&name).cloned().unwrap_or(Type::Generic);
                    (name, typ)
                }).chain(captures).collect();
                if n.kind == "UnionType" { Type::Union(r, arguments) } else { Type::Record(r, arguments) }
            }
            "Call" => {
                let Some(callee) = child("callee") else {
                    return Type::Unknown;
                };
                let base = self.specialize(callee, bindings, &mut seen.clone());
                if base.signature().is_some() && self.denotes_type(callee) {return base;}
                let owner = match &base {
                    Type::Struct(owner) | Type::Record(owner, _) | Type::Union(owner, _) => Some(*owner),
                    _ => None,
                };
                if let Some(owner) = owner {
                    let params = self.fields(owner, "params");
                    if !params.is_empty() {
                        let args = s.group(r.node, "args");
                        let mut arguments: Vec<_> = params
                            .into_iter()
                            .enumerate()
                            .map(|(i, (name, annotation))| {
                                let argument = args
                                    .iter()
                                    .copied()
                                    .find(|arg| {
                                        s.node(*arg).kind == "FieldValue"
                                            && s.role(*arg, "field")
                                                .is_some_and(|field| s.text(field) == name)
                                    })
                                    .or_else(|| args.get(i).copied());
                                let typ = argument
                                    .map(|node| {
                                        let node = s.role(node, "value").unwrap_or(node);
                                        let r = Ref { file: r.file, node };
                                        let type_param = annotation.is_some_and(|a| {
                                            self.sources[a.file].node(a.node).kind == "TypeidType"
                                        });
                                        if type_param {
                                            self.specialize(r, bindings, &mut seen.clone())
                                        } else {
                                            let expected = annotation.map(|a| {
                                                self.specialize(a, bindings, &mut seen.clone())
                                            });
                                            self.constant_value(r, bindings, expected.as_ref())
                                                .map(Type::Constant)
                                                .unwrap_or(Type::Unknown)
                                        }
                                    })
                                    .unwrap_or(Type::Unknown);
                                (name, typ)
                            })
                            .collect();
                        if let Type::Record(_, captured) | Type::Union(_, captured) = &base {
                            for (name, typ) in captured {
                                if !arguments.iter().any(|(parameter, _)| parameter == name) {
                                    arguments.push((name.clone(), typ.clone()));
                                }
                            }
                        }
                        return self.record_instance(owner, arguments);
                    }
                }
                if !matches!(base, Type::Procedure(_) | Type::Function(..) | Type::Unknown | Type::Generic) {
                    base
                } else {
                    let typ = self.specialized_result(r, 0, bindings);
                    if typ.is_unknown() {
                        self.type_of(r)
                    } else {
                        typ
                    }
                }
            }
            "Binary" if specialization_division=>{let actual=subtype("left");if actual.is_unknown(){subtype("right")}else{actual}},
            "Binary" => {
                if matches!(n.op.as_str(), "in" | "not_in" | "==" | "!=" | "<" | ">" | "<=" | ">=" | "&&" | "||") {
                    return Type::scalar("bool");
                }
                let left = subtype("left");
                let right = subtype("right");
                if let Some(typ)=arithmetic_shape(&n.op,&left,&right){return typ;}
                if n.op == "*" && right.is_string() || left.untyped() && !right.untyped() {right} else {left}
            }
            _ => self.type_of(r),
        }
    }
    /// Select a value from a multi-result expression without losing generic
    /// substitutions. Assignment slots are not all the first result's type.
    pub(crate) fn specialized_value_result(
        &mut self,
        r: Ref,
        index: usize,
        bindings: &Substitutions,
        seen: &mut HashSet<Ref>,
    ) -> Type {
        if !seen.insert(r) {
            self.incomplete_plans.set(self.incomplete_plans.get().wrapping_add(1));
            return Type::Unknown;
        }
        let s = &self.sources[r.file];
        let n = s.node(r.node);
        let child = |role: &str| s.role(r.node, role).map(|node| Ref { file: r.file, node });
        match n.kind.as_str() {
            "Paren" | "HelperType" | "Run" | "OrPanic" | "OrReturn" | "OrBranch" => child("expr")
                .map(|r| self.specialized_value_result(r, index, bindings, seen))
                .unwrap_or(Type::Unknown),
            "OrElse" => child("x")
                .map(|r| self.specialized_value_result(r, index, bindings, seen))
                .unwrap_or(Type::Unknown),
            "SwitchResults" | "FallbackList" => s
                .group(r.node, "args")
                .get(index)
                .copied()
                .map(|node| self.specialized_type(Ref { file: r.file, node }, bindings))
                .unwrap_or(Type::Unknown),
            "SwitchExpr" | "CompileSwitchExpr" => {
                if n.kind == "CompileSwitchExpr" {
                    if let Some(arm) = self.selected_compile_switch_value(r, bindings) {
                        return self.specialized_value_result(arm, index, bindings, seen);
                    }
                }
                let arms = n.data["cases"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|case| {
                        crate::ast::Span::from_wire(&case["result"])
                            .and_then(|span| s.at(span, r.node))
                    })
                    .collect::<Vec<_>>();
                for node in arms {
                    let typ = self.specialized_value_result(
                        Ref { file: r.file, node },
                        index,
                        bindings,
                        &mut seen.clone(),
                    );
                    if !typ.is_unknown() {
                        return typ;
                    }
                }
                Type::Unknown
            }
            "Call" => {
                if let Some(typ) = self.logical_result(r,index) { return typ; }
                let typ = self.specialized_result(r, index, bindings);
                if !typ.is_unknown() {
                    return typ;
                }
                if let Some(signature) = child("callee").and_then(|r| self.signature(r)) {
                    if let Some((_, Some(annotation))) =
                        self.fields(signature, "results").get(index)
                    {
                        return self.specialized_type(*annotation, bindings);
                    }
                }
                if index == 0 {
                    self.type_of(r)
                } else {
                    Type::Unknown
                }
            }
            "Index" | "TypeAssertion" if index == 1 => Type::scalar("bool"),
            _ if index == 0 => self.specialized_type(r, bindings),
            _ => Type::Unknown,
        }
    }
    pub(crate) fn selected_compile_switch_value(&mut self, r: Ref, bindings: &Substitutions) -> Option<Ref> {
        let source = &self.sources[r.file];
        let cases = source.node(r.node).data["cases"].as_array()?;
        let reference = |span: &serde_json::Value| {
            crate::ast::Span::from_wire(span)
                .and_then(|span| source.at(span, r.node))
                .map(|node| Ref { node, ..r })
        };
        let selector = if let Some(node) = source.role(r.node, "selector") {
            let selector = Ref { node, ..r };
            let typ = self.specialized_type(selector, bindings);
            let value = self.constant_value(selector, bindings, Some(&typ))?;
            Some((value, typ))
        } else {
            None
        };
        let mut default = None;
        for case in cases {
            let operands = case["matches"].as_array()?;
            if operands.is_empty() {
                default = reference(&case["result"]);
                continue;
            }
            for operand in operands {
                let operand = reference(operand)?;
                let matches = if let Some((value, typ)) = &selector {
                    let ast = source.node(operand.node);
                    if ast.kind == "Binary" && matches!(ast.op.as_str(), ".." | "..<" | "..=") {
                        let integer = |role| {
                            let node = source.role(operand.node, role)?;
                            self.context_integer(Ref { node, ..r }, bindings)
                        };
                        let (low, high) = (integer("left")?, integer("right")?);
                        let Constant::Integer(value) = *value else { return None; };
                        value >= low && (value < high || value == high && ast.op == "..=")
                    } else {
                        self.constant_value(operand, bindings, Some(typ)).as_ref()? == value
                    }
                } else {
                    self.context_condition(operand, bindings)?
                };
                if matches {
                    return reference(&case["result"]);
                }
            }
        }
        default
    }
    fn record_instance(&self, owner: Ref, arguments: Vec<(String, Type)>) -> Type {
        let s = &self.sources[owner.file];
        if s.node(owner.node).kind == "UnionType" {
            return Type::Union(owner, arguments);
        }
        let expected = self.runtime_root().join("sets/sets.odin");
        let genuine = matches!((crate::project::canonicalize(&s.path),crate::project::canonicalize(expected)),(Ok(actual),Ok(expected)) if actual==expected);
        let named_set = s.node(owner.node).parent.is_some_and(|declaration| {
            s.group(declaration, "names")
                .iter()
                .any(|name| s.text(*name) == "Set")
        });
        if genuine && named_set {
            Type::Set(Box::new(
                arguments
                    .into_iter()
                    .find(|(name, _)| name == "T")
                    .map(|(_, typ)| typ)
                    .unwrap_or(Type::Unknown),
            ))
        } else {
            Type::Record(owner, arguments)
        }
    }
    pub fn parameter_bindings(&mut self, signature: Ref, args: &[Type]) -> Substitutions {
        let mut bindings = Substitutions::new();
        let layout = self.signature_layout(signature);
        for ((name, annotation), actual) in layout.params.iter().zip(args) {
            if let Some(annotation) = annotation {
                let s = &self.sources[annotation.file];
                if s.node(annotation.node).kind == "TypeidType" {
                    bindings.insert(name.trim_start_matches('$').into(), actual.clone());
                }
                self.bind_shape(*annotation, actual, &mut bindings, &mut HashSet::new());
            }
        }
        bindings
    }
    pub fn shape_matches(
        &mut self,
        annotation: Ref,
        actual: &Type,
        bindings: &Substitutions,
    ) -> bool {
        self.shape_score(annotation, actual, bindings).is_some()
    }
    pub fn shape_score(
        &mut self,
        annotation: Ref,
        actual: &Type,
        bindings: &Substitutions,
    ) -> Option<u32> {
        let s = &self.sources[annotation.file];
        let n = s.node(annotation.node);
        if actual.is_unknown() {
            return Some(0);
        }
        if n.kind == "PolyType" {
            return if let Some(node) = s.role(annotation.node, "specialization") {
                let actual = if let Type::Nominal(_, base) = actual {
                    base.as_ref()
                } else {
                    actual
                };
                self.shape_score(
                    Ref {
                        file: annotation.file,
                        node,
                    },
                    actual,
                    bindings,
                )
            } else {
                let bound = bindings.get(s.names.get(n.name));
                if bound.is_some_and(|bound| Self::type_score(bound, actual).is_none()) {
                    None
                } else {
                    Some(1)
                }
            };
        }
        let child = |name| {
            s.role(annotation.node, name).map(|node| Ref {
                file: annotation.file,
                node,
            })
        };
        if matches!(
            n.kind.as_str(),
            "MatrixType" | "ArrayType" | "DynamicArrayType" | "PointerType" | "MultiPointerType" | "Ellipsis"
        ) {
            let expected_kind = n.kind.as_str();
            let actual = match actual {
                Type::SoaStorage(storage) if n.op == "soa" => storage.as_ref(),
                Type::SoaStorage(_) => return None,
                Type::Array { .. } | Type::Dynamic(_) if n.op == "soa" => return None,
                other => other,
            };
            let elem = match (expected_kind, actual) {
                ("MatrixType",Type::Matrix{elem,rows,columns,..})=>{
                    for (role,actual) in [("rows",rows),("columns",columns)]{
                        let expected=child(role).and_then(|r|self.context_integer(r,bindings));
                        if expected.is_some()&&actual.is_some()&&expected!=*actual{return None;}
                    }elem.as_ref()
                },
                ("ArrayType", Type::Array { elem, length }) if child("len").is_some() && n.op!="simd" => {
                    let count = child("len").and_then(|r| self.context_integer(r, bindings));
                    if count.is_some() && length.is_some() && count != *length {
                        return None;
                    }
                    elem.as_ref()
                }
                ("ArrayType",Type::Vector{elem,length}) if n.op=="simd"=>{
                    let count=child("len").and_then(|r|self.context_integer(r,bindings));
                    if count.is_some()&&length.is_some()&&count!=*length{return None;}
                    elem.as_ref()
                },
                ("ArrayType", Type::Slice(elem)) if child("len").is_none() && n.op != "soa" => {
                    elem.as_ref()
                }
                ("ArrayType", Type::Soa(elem)) if n.op == "soa" && child("len").is_none() => elem.as_ref(),
                ("DynamicArrayType", Type::Dynamic(elem)) if child("capacity").is_none() => {
                    elem.as_ref()
                }
                ("DynamicArrayType", Type::Fixed { elem, length })
                    if child("capacity").is_some() =>
                {
                    let count = child("capacity").and_then(|r| self.context_integer(r, bindings));
                    if count.is_some() && length.is_some() && count != *length {
                        return None;
                    }
                    elem.as_ref()
                }
                ("PointerType" | "MultiPointerType", Type::Pointer(elem))
                | ("Ellipsis", Type::Slice(elem)) => elem.as_ref(),
                _ => return None,
            };
            return child(if expected_kind == "Ellipsis" {
                "expr"
            } else {
                "elem"
            })
            .and_then(|r| self.shape_score(r, elem, bindings))
            .map(|score| score + 3);
        }
        if n.kind == "Binary" && n.op == "/" {
            return s.role(annotation.node, "right").and_then(|node| {
                self.shape_score(
                    Ref {
                        file: annotation.file,
                        node,
                    },
                    actual,
                    bindings,
                )
            });
        }
        let expected = self.specialized_type(annotation, bindings);
        Self::type_score(&expected, actual)
    }
    fn type_score(expected: &Type, actual: &Type) -> Option<u32> {
        if expected.is_unknown() || actual.is_unknown() {
            return Some(0);
        }
        fn canonical(name:&str)->&str{match name{"byte"=>"u8",other=>other}}
        match (expected, actual) {
            (Type::Scalar { name, .. }, _) if name == "any" => Some(0),
            (Type::Scalar { name, .. }, Type::Pointer(_)) if name == "rawptr" => Some(1),
            (
                Type::Scalar { name, .. },
                Type::Scalar {
                    name: other,
                    untyped,
                },
            ) => {
                if canonical(name) == canonical(other) {
                    Some(if *untyped { 2 } else { 5 })
                } else if *untyped && expected.numeric() && actual.numeric() {
                    Some(0)
                } else {
                    None
                }
            }
            (
                Type::String { name, .. },
                Type::String {
                    name: other,
                    untyped,
                },
            ) => {
                if name == other || *untyped {
                    Some(if *untyped { 2 } else { 5 })
                } else {
                    None
                }
            }
            (Type::Matrix{elem:a,rows:r,columns:c,row_major:x},Type::Matrix{elem:b,rows:s,columns:d,row_major:y})=>{
                if x!=y||r.is_some()&&s.is_some()&&r!=s||c.is_some()&&d.is_some()&&c!=d{None}else{Self::type_score(a,b)}
            },
            (Type::Array { elem: a, length: n }, Type::Array { elem: b, length: m })
            | (Type::Vector { elem: a, length: n }, Type::Vector { elem: b, length: m })
            | (Type::Fixed { elem: a, length: n }, Type::Fixed { elem: b, length: m }) => {
                if n.is_some() && m.is_some() && n != m {
                    return None;
                }
                Self::type_score(a, b).map(|score| score + 3)
            }
            (Type::Slice(a), Type::Slice(b))
            | (Type::Dynamic(a), Type::Dynamic(b))
            | (Type::Pointer(a), Type::Pointer(b))
            | (Type::Set(a), Type::Set(b))
            | (Type::SoaStorage(a), Type::SoaStorage(b))
            | (Type::Soa(a), Type::Soa(b)) => Self::type_score(a, b).map(|score| score + 3),
            (Type::Map { key: a, value: b }, Type::Map { key: c, value: d }) => {
                Some(Self::type_score(a, c)? + Self::type_score(b, d)? + 3)
            }
            (Type::Record(a, bindings), Type::Record(b, other)) if a == b => {
                let mut score = 5;
                for ((name, typ), (other_name, other_typ)) in bindings.iter().zip(other) {
                    if name != other_name {
                        return None;
                    }
                    score += Self::type_score(typ, other_typ)?;
                }
                Some(score)
            }
            (Type::Nominal(a, _), Type::Nominal(b, _)) if a == b => Some(5),
            (Type::Nominal(_, base), value) if value.untyped() => Self::type_score(base, value),
            (Type::Procedure(_) | Type::Function(..), Type::Procedure(_) | Type::Function(..)) => Some(1), // Odin checks complete calling signatures.
            (Type::Union(..), _) => Some(0), // Native union injection is checked by Odin.
            _ if expected == actual => Some(5),
            _ => None,
        }
    }
    fn bind_shape(
        &mut self,
        annotation: Ref,
        actual: &Type,
        bindings: &mut Substitutions,
        seen: &mut HashSet<Ref>,
    ) {
        if !seen.insert(annotation) {
            return;
        }
        let s = &self.sources[annotation.file];
        let n = s.node(annotation.node);
        let child = |role: &str| {
            s.role(annotation.node, role).map(|node| Ref {
                file: annotation.file,
                node,
            })
        };
        match n.kind.as_str() {
            "ProcedureType" => {
                if let Some(signature) = actual.signature() {
                    let context = if let Type::Function(_, captures)=actual {captures.iter().cloned().collect()}else{Substitutions::new()};
                    for role in ["params", "results"] {
                        for ((_,expected),(_,actual)) in self.fields(annotation,role).into_iter().zip(self.fields(signature,role)) {
                            if let (Some(expected),Some(actual))=(expected,actual) {
                                let actual=self.specialized_type(actual,&context);
                                self.bind_shape(expected,&actual,bindings,&mut seen.clone());
                            }
                        }
                    }
                }
            }
            "HelperType"|"Paren"=>{if let Some(expr)=child("expr"){self.bind_shape(expr,actual,bindings,seen);}},
            "PolyType" => {
                let binding = bindings
                    .entry(s.names.get(n.name).into())
                    .or_insert(Type::Unknown);
                if binding.is_unknown() {
                    *binding = actual.clone();
                }
                if let Some(specialization) = child("specialization") {
                    self.bind_shape(specialization, actual, bindings, seen);
                }
            }
            "Binary" if n.op == "/" => {
                for role in ["left", "right"] {
                    if let Some(child) = child(role) {
                        self.bind_shape(child, actual, bindings, &mut seen.clone());
                    }
                }
            }
            "MatrixType" | "ArrayType" | "DynamicArrayType" | "PointerType" | "MultiPointerType" | "Ellipsis" => {
                let actual = match actual { Type::SoaStorage(storage) => storage.as_ref(), other => other };
                if let Type::Matrix{rows,columns,..}=actual{
                    for (role,count) in [("rows",rows),("columns",columns)]{
                        if let (Some(count),Some(value))=(count,child(role)){
                            if s.node(value.node).kind=="PolyType"{bindings.insert(s.names.get(s.node(value.node).name).into(),Type::Constant(Constant::Integer(*count)));}
                        }
                    }
                }
                if let Type::Array {
                    length: Some(length),
                    ..
                }
                | Type::Vector{length:Some(length),..}
                | Type::Fixed {
                    length: Some(length),
                    ..
                } = actual
                {
                    if let Some(count) = child(if n.kind == "ArrayType" {
                        "len"
                    } else {
                        "capacity"
                    }) {
                        if s.node(count.node).kind == "PolyType" {
                            bindings.insert(
                                s.names.get(s.node(count.node).name).into(),
                                Type::Constant(Constant::Integer(*length)),
                            );
                        }
                    }
                }
                if let (Some(elem), Some(actual)) = (
                    child(if n.kind == "Ellipsis" { "expr" } else { "elem" }),
                    actual.element(),
                ) {
                    self.bind_shape(elem, actual, bindings, seen);
                }
            }
            "Ident" => {
                let name = s.text(annotation.node);
                let generic = self.symbol(annotation).is_some_and(|index| {
                    self.sources[self.symbols[index].reference.file]
                        .node(self.symbols[index].reference.node)
                        .kind
                        == "PolyType"
                });
                if generic {
                    let binding = bindings.entry(name.into()).or_insert(Type::Unknown);
                    // A typed argument can supply context for a preceding
                    // literal. Untyped defaults must not select an overload
                    // while the explicit polymorphic argument is unresolved.
                    if binding.is_unknown() && !actual.untyped() {
                        *binding = actual.clone();
                    }
                }
            }
            "MapType" => {
                if let Type::Map { key, value } = actual {
                    if let Some(k) = child("key") {
                        self.bind_shape(k, key, bindings, &mut seen.clone());
                    }
                    if let Some(v) = child("value") {
                        self.bind_shape(v, value, bindings, seen);
                    }
                }
            }
            "Call" => {
                if let Type::Set(elem) = actual {
                    if let Some(arg) = s.group(annotation.node, "args").first() {
                        self.bind_shape(
                            Ref {
                                file: annotation.file,
                                node: *arg,
                            },
                            elem,
                            bindings,
                            &mut seen.clone(),
                        );
                    }
                }
                if let Type::Record(_, arguments) | Type::Union(_, arguments) = actual {
                    for (arg, (_, typ)) in s.group(annotation.node, "args").iter().zip(arguments) {
                        self.bind_shape(
                            Ref {
                                file: annotation.file,
                                node: *arg,
                            },
                            typ,
                            bindings,
                            &mut seen.clone(),
                        );
                    }
                }
            }
            _ => {}
        }
    }
    pub fn candidates(&mut self, callee: Ref) -> Vec<Ref> {
        self.candidate_inner(callee, &mut HashSet::new())
    }
    // Native `when` declarations share their enclosing scope. A generic
    // specialization must use the selected arm's annotation, not whichever
    // same-name declaration the syntax-only lookup encountered first.
    pub(crate) fn context_symbol(&mut self,site:Ref,bindings:&Substitutions)->Option<usize>{
        let index=self.symbol(site)?;
        if bindings.is_empty(){return Some(index);}
        let symbol=&self.symbols[index];let Some(owner)=self.enclosing_when(symbol.reference)else{return Some(index);};
        let scope=Ref{file:symbol.reference.file,node:symbol.scope};
        let alternatives=self.scopes.get(&scope).and_then(|names|names.get(&symbol.name)).cloned().unwrap_or_default();
        let mut active=vec![];
        for alternative in alternatives{
            let reference=self.symbols[alternative].reference;
            if self.enclosing_when(reference)!=Some(owner){continue;}
            let source=&self.sources[reference.file];let span=source.node(reference.node).span;
            let conditions=source.ancestors(reference.node).filter(|n|source.node(*n).kind=="When")
                .filter_map(|n|Some((source.role(n,"cond")?,source.role(n,"body"),source.role(n,"else")))).collect::<Vec<_>>();
            let mut selected=true;
            for (cond,body,other) in conditions{
                if let Some(value)=self.context_condition(Ref{node:cond,..reference},bindings){
                    let branch=if value{body}else{other};
                    if !branch.is_some_and(|node|self.sources[reference.file].node(node).span.contains(span)){selected=false;break;}
                }
            }
            if selected{active.push(alternative);}
        }
        (active.len()==1).then(||active[0])
    }
    fn enclosing_when(&self, declaration: Ref) -> Option<NodeId> {
        let source = &self.sources[declaration.file];
        source
            .ancestors(declaration.node)
            .take_while(|node| !is_scope(source, *node))
            .filter(|node| source.node(*node).kind == "When")
            .last()
    }
    fn conditional_procedures(&self, symbol: &Symbol) -> Vec<Ref> {
        let Some(owner) = self.enclosing_when(symbol.reference) else {
            return vec![];
        };
        let file = symbol.reference.file;
        let source = &self.sources[file];
        self.scopes
            .get(&Ref { file, node: symbol.scope }).and_then(|names| names.get(&symbol.name))
            .into_iter()
            .flatten()
            .filter_map(|index| {
                let alternative = &self.symbols[*index];
                let mut value = alternative.value?;
                while source.node(value).kind == "Paren" {
                    value = source.role(value, "expr")?;
                }
                (source.node(value).kind == "Procedure"
                    && self.enclosing_when(alternative.reference) == Some(owner))
                    .then_some(Ref { file, node: value })
            })
            .collect()
    }
    pub(crate) fn checked_procedure_identity(&self, procedure: Ref) -> Ref {
        let source = &self.sources[procedure.file];
        let mut value = procedure.node;
        while let Some(parent) = source.node(value).parent {
            if source.node(parent).kind != "Paren" {
                break;
            }
            value = parent;
        }
        let Some(declaration) = source
            .node(value)
            .parent
            .filter(|parent| source.node(*parent).kind == "Declaration")
        else {
            return procedure;
        };
        let declaration = Ref { node: declaration, ..procedure };
        if self.enclosing_when(declaration).is_none() {
            return procedure;
        }
        let declaration_scope = Ref {file: procedure.file, node: super::scope(source, declaration.node)};
        let Some(symbol) = self.scopes.get(&declaration_scope)
            .into_iter()
            .flat_map(|names| source.group(declaration.node, "names").iter()
                .filter_map(|name| names.get(source.text(*name))))
            .flatten()
            .map(|index| &self.symbols[*index])
            .find(|symbol| {
                crate::profile::count("resolver.conditional_identity_candidates", 1);
                symbol.reference == declaration && symbol.value == Some(value)
            })
        else {
            return procedure;
        };
        self.conditional_procedures(symbol)
            .into_iter()
            .min()
            .unwrap_or(procedure)
    }
    fn candidate_inner(&mut self, callee: Ref, seen: &mut HashSet<Ref>) -> Vec<Ref> {
        if !seen.insert(callee) {
            self.incomplete_plans.set(self.incomplete_plans.get().wrapping_add(1));
            return vec![];
        }
        let s = &self.sources[callee.file];
        let n = s.node(callee.node);
        if n.kind == "ProcedureType" {
            return vec![callee];
        }
        if n.kind == "Procedure" {
            return vec![callee];
        }
        if matches!(n.kind.as_str(), "Paren" | "HelperType") {
            return s
                .role(callee.node, "expr")
                .map(|node| {
                    self.candidate_inner(
                        Ref {
                            file: callee.file,
                            node,
                        },
                        seen,
                    )
                })
                .unwrap_or_default();
        }
        if n.kind == "ProcedureGroup" {
            let mut result = vec![];
            for child in &n.children {
                result.extend(self.candidate_inner(
                    Ref {
                        file: callee.file,
                        node: *child,
                    },
                    &mut seen.clone(),
                ));
            }
            result.sort_by_key(|r| (r.file, r.node));
            result.dedup();
            return result;
        }
        if let Some(symbol) = self.symbol(callee) {
            let symbol = self.symbols[symbol].clone();
            if let Some(target) = self.bound_constants.get(&(symbol.reference, symbol.name.clone()))
                .and_then(|(_, value)|crate::specialization::callback_value(value)) {
                return vec![target];
            }
            let conditional = self.conditional_procedures(&symbol);
            if conditional.len() > 1 {
                return conditional
                    .into_iter()
                    .filter(|candidate| self.active_definition(*candidate))
                    .collect();
            }
            if let Some(node) = symbol.annotation.or(symbol.value) {
                return self.candidate_inner(
                    Ref {
                        file: symbol.reference.file,
                        node,
                    },
                    seen,
                );
            }
        }
        vec![]
    }
}
