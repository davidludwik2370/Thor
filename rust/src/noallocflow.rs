//! Bounded call-context facts. Unknown/escaped storage never gains a target.
use super::*;
use crate::semantics::Constant;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::{cell::RefCell, rc::Rc};

#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub(super) enum Fact {
    #[default]
    Unknown,
    Nil,
    Function(Ref),
    Constant(Constant),
    Atom(String),
    Record(BTreeMap<String, Fact>),
    Pointer(Option<Ref>, Box<Fact>, Type),
    StandardFile(u8),
    DefaultRandom,
    AmbientRandom,
    AmbientRandomCallback,
    Stream(Box<Fact>),
    Buffered(Box<Fact>),
    BufferedReader(Box<Fact>),
    Pack(Vec<Fact>),
    Spread(Type),
    Typed(Type),
}
#[derive(Default, Clone)]
pub(super) struct Flow {
    pub values: BTreeMap<Ref, Fact>,
    pub terminated: bool,
    bindings: Substitutions,
    escaped: BTreeSet<Ref>,
    interests: Rc<RefCell<HashMap<Ref, BTreeSet<String>>>>,
    pub(super) returned: Rc<RefCell<HashMap<(Ref, Substitutions, Vec<Fact>), Fact>>>,
}
impl Flow {
    fn bounded(value: Fact) -> Fact {
        let mut pending = vec![&value];
        let mut count = 0;
        while let Some(value) = pending.pop() {
            count += 1;
            if count > 2048 {
                return Fact::Unknown;
            }
            match value {
                Fact::Record(fields) => pending.extend(fields.values()),
                Fact::Pack(values) => pending.extend(values.iter()),
                Fact::Pointer(_, inner, _)
                | Fact::Stream(inner)
                | Fact::Buffered(inner)
                | Fact::BufferedReader(inner) => pending.push(inner),
                _ => {}
            }
        }
        value
    }
    fn store_field(place: &mut Fact, path: &[String], value: &Fact) -> bool {
        let Fact::Record(fields) = place else {
            return false;
        };
        let Some((name, rest)) = path.split_last() else {
            return false;
        };
        if rest.is_empty() {
            fields.insert(name.clone(), value.clone());
            return true;
        }
        fields
            .get_mut(name)
            .is_some_and(|place| Self::store_field(place, rest, value))
    }
    pub(super) fn detached_return_argument(&self, value: &Fact) -> Fact {
        self.detach_return_inner(value, 0, &mut 1024)
    }
    fn detach_return_inner(&self, value: &Fact, depth: usize, budget: &mut usize) -> Fact {
        if depth > 16 || *budget == 0 {
            return Fact::Unknown;
        }
        *budget -= 1;
        match value {
            Fact::Pointer(_, _, typ) => Fact::Pointer(
                None,
                Box::new(self.detach_return_inner(&self.deref(value), depth + 1, budget)),
                typ.clone(),
            ),
            Fact::Record(fields) => Fact::Record(
                fields
                    .iter()
                    .map(|(name, value)| {
                        (
                            name.clone(),
                            self.detach_return_inner(value, depth + 1, budget),
                        )
                    })
                    .collect(),
            ),
            Fact::Pack(values) => Fact::Pack(
                values
                    .iter()
                    .map(|v| self.detach_return_inner(v, depth + 1, budget))
                    .collect(),
            ),
            Fact::Stream(v) => {
                Fact::Stream(Box::new(self.detach_return_inner(v, depth + 1, budget)))
            }
            Fact::Buffered(v) => {
                Fact::Buffered(Box::new(self.detach_return_inner(v, depth + 1, budget)))
            }
            Fact::BufferedReader(v) => {
                Fact::BufferedReader(Box::new(self.detach_return_inner(v, depth + 1, budget)))
            }
            _ => value.clone(),
        }
    }
    pub(super) fn invalidate_all(&mut self) {
        self.escaped.extend(self.values.keys().copied());
        self.values.clear();
    }
    fn zero(r: &mut Resolver<'_>, typ: &Type, depth: usize) -> Fact {
        Self::zero_inner(r, typ, depth, &mut 1024)
    }
    fn zero_inner(r: &mut Resolver<'_>, typ: &Type, depth: usize, budget: &mut usize) -> Fact {
        if depth > 12 || *budget == 0 {
            return Fact::Unknown;
        }
        *budget -= 1;
        match base_type(typ) {
            Type::Procedure(_) | Type::Function(_, _)
            | Type::Pointer(_)
            | Type::Slice(_)
            | Type::Dynamic(_)
            | Type::Map { .. } => Fact::Nil,
            Type::Scalar { name, .. } if matches!(name.as_str(),"rawptr"|"any"|"typeid") => Fact::Nil,
            Type::Scalar { name, .. } if matches!(name.as_str(),"bool"|"b8"|"b16"|"b32"|"b64") => Fact::Constant(Constant::Boolean(false)),
            Type::Scalar { .. } => Fact::Constant(Constant::Integer(0)),
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                let fields = r.record_fields(typ);
                if fields.len() > 32 {
                    return Fact::Unknown;
                }
                Self::bounded(Fact::Record(
                    fields
                        .into_iter()
                        .map(|(name, typ)| (name, Self::zero_inner(r, &typ, depth + 1, budget)))
                        .collect(),
                ))
            }
            _ => Fact::Unknown,
        }
    }
    fn clear_pointees(value: &mut Fact) {
        match value {
            Fact::Pointer(_, inner, _) => **inner = Fact::Unknown,
            Fact::Record(fields) => {
                for value in fields.values_mut() {
                    Self::clear_pointees(value);
                }
            }
            Fact::Stream(value) | Fact::Buffered(value) | Fact::BufferedReader(value) => {
                Self::clear_pointees(value)
            }
            Fact::Pack(values) => {
                for value in values {
                    Self::clear_pointees(value);
                }
            }
            _ => {}
        }
    }
    pub(super) fn opaque_write(&mut self) {
        for (key, value) in &mut self.values {
            if self.escaped.contains(key) {
                *value = Fact::Unknown;
            } else {
                Self::clear_pointees(value);
            }
        }
    }
    pub(super) fn reset(&mut self) {
        self.values.clear();
        self.escaped.clear();
        self.terminated = false;
        self.bindings.clear();
    }
    pub(super) fn address_taken(&mut self, r: &mut Resolver<'_>, mut at: Ref) {
        for _ in 0..32 {
            if let Some(index) = r.symbol(at) {
                if let Some(projection) = &r.symbols[index].projection {
                    at = projection.owner;
                    continue;
                }
            }
            if let Some(key) = self.key(r, at) {
                self.escaped.insert(key);
                return;
            }
            let Some(node) = r.sources[at.file].role(at.node, "expr") else {
                return;
            };
            at = Ref { node, ..at };
        }
    }
    // Odin's implicit any boxing and #by_ptr parameters may address caller
    // storage without an explicit '&'. Retain formatting payload facts, but
    // invalidate their owners after an ordinary call can mutate that storage.
    pub(super) fn box_address(&mut self, r: &mut Resolver<'_>, at: Ref) {
        let mut pending = vec![at.node];
        while let Some(node) = pending.pop() {
            let source=&r.sources[at.file];
            let syntax=source.node(node);
            match syntax.kind.as_str() {
                "Ident"|"Selector"|"Index"|"MatrixIndex"|"Deref" => self.address_taken(r,Ref{node,..at}),
                "Paren"|"Runtime"|"FieldValue" => {
                    if let Some(node)=source.role(node,if syntax.kind=="FieldValue"{"value"}else{"expr"}) {pending.push(node);}
                }
                "Call" => {
                    let name=crate::borroweval::Evaluator{resolver:r,arena:&mut crate::borrowflow::Arena::default()}.builtin(Ref{node,..at});
                    if name.as_deref().is_some_and(|name|matches!(name,"expand_values"|"compress_values")) {
                        pending.extend(r.sources[at.file].group(node,"args").iter().copied());
                    }
                }
                "Ternary"|"WhenExpr" => {
                    for role in ["x","y"] {if let Some(node)=source.role(node,role){pending.push(node);}}
                }
                _=>{}, // Computed values get new temporary storage when boxed.
            }
        }
    }
    pub(super) fn box_value(&mut self,r:&mut Resolver<'_>,at:Ref,typ:&Type,depth:usize) {
        if Self::any_type(typ) {self.box_address(r,at);return;}
        if depth>24 {self.box_address(r,at);return;}
        let source=&r.sources[at.file];
        if source.node(at.node).kind=="Paren" {
            if let Some(node)=source.role(at.node,"expr"){self.box_value(r,Ref{node,..at},typ,depth+1);}
            return;
        }
        if source.node(at.node).kind!="Compound" {return;}
        let elems=source.group(at.node,"elems").to_vec();
        let fields=r.record_fields(typ);
        for (index,node) in elems.into_iter().enumerate() {
            let source=&r.sources[at.file];
            let name=source.role(node,"field").map(|name|source.text(name));
            let field=if let Some(name)=name {fields.iter().find(|(field,_)|field==name)}else{fields.get(index)};
            let destination=typ.element().cloned().or_else(||field.map(|(_,typ)|typ.clone()));
            let value=source.role(node,"value").unwrap_or(node);
            if let Some(destination)=destination {self.box_value(r,Ref{node:value,..at},&destination,depth+1);}
        }
    }
    pub(super) fn any_type(typ: &Type) -> bool {
        matches!(base_type(typ),Type::Scalar{name,..} if name=="any")
    }
    pub(super) fn contains_any(r: &mut Resolver<'_>, typ: &Type, depth:usize) -> bool {
        if depth>16 {return true;}
        match base_type(typ) {
            Type::Scalar{name,..} => name=="any",
            Type::Array{elem,..}|Type::Vector{elem,..}|Type::Slice(elem)|Type::Dynamic(elem)|Type::Fixed{elem,..} => Self::contains_any(r,elem,depth+1),
            Type::Struct(_)|Type::Record(..)|Type::Anonymous(_) => r.record_fields(typ).into_iter().any(|(_,t)|Self::contains_any(r,&t,depth+1)),
            _=>false,
        }
    }
    pub fn key(&self, r: &mut Resolver<'_>, at: Ref) -> Option<Ref> {
        let index = r.context_symbol(at, &self.bindings)?;
        Self::symbol_key(r, index)
    }
    pub(super) fn lexical_key(r: &Resolver<'_>, at: Ref) -> Option<Ref> {
        Self::symbol_key(r, r.symbol(at)?)
    }
    fn symbol_key(r: &Resolver<'_>, index: usize) -> Option<Ref> {
        let symbol = &r.symbols[index];
        let source = &r.sources[symbol.reference.file];
        if !symbol.constant
            && !source
                .ancestors(symbol.reference.node)
                .any(|node| source.node(node).kind == "Procedure")
        {
            return None;
        }
        if !source.group(symbol.reference.node, "attributes").is_empty()
            && r.attributes(symbol.reference)
                .iter()
                .any(|(name, _)| name == "static")
        {
            return None;
        }
        Some(Ref {
            node: symbol.name_node,
            file: symbol.reference.file,
        })
    }
    pub fn deref(&self, value: &Fact) -> Fact {
        if let Fact::Pointer(origin, value, _) = value {
            return origin
                .and_then(|key| self.values.get(&key))
                .cloned()
                .unwrap_or_else(|| *value.clone());
        }
        value.clone()
    }
    pub fn field(&self, value: &Fact, name: &str) -> Fact {
        match self.deref(value) {
            Fact::Record(fields) => fields.get(name).cloned().unwrap_or_default(),
            Fact::AmbientRandom if name == "procedure" => Fact::AmbientRandomCallback,
            _ => Fact::Unknown,
        }
    }
    pub fn fact(&self, r: &mut Resolver<'_>, at: Ref, bindings: &Substitutions) -> Fact {
        self.fact_at(r, at, bindings, 0)
    }
    pub(super) fn fact_at(
        &self,
        r: &mut Resolver<'_>,
        at: Ref,
        bindings: &Substitutions,
        depth: usize,
    ) -> Fact {
        if depth > 24 {
            return Fact::Unknown;
        }
        let s = &r.sources[at.file];
        let n = s.node(at.node);
        let role = |name| s.role(at.node, name).map(|node| Ref { node, ..at });
        if let Some(key) = self.key(r, at) {
            if let Some(value) = self.values.get(&key) {
                return value.clone();
            }
        }
        if n.kind == "Ident" && s.text(at.node) == "nil" {
            return Fact::Nil;
        }
        if let Some(value) = super::io::value(self, r, at, bindings, depth) {
            return value;
        }
        if let Some(value) = super::random::value(self, r, at, bindings, depth) {
            return value;
        }
        if let Some(value) = r.constant_value(at, bindings, None) {
            return Fact::Constant(value);
        }
        match n.kind.as_str() {
            "Paren" | "Runtime" | "FieldValue" => role(if n.kind == "FieldValue" {
                "value"
            } else {
                "expr"
            })
            .map(|v| self.fact_at(r, v, bindings, depth + 1))
            .unwrap_or_default(),
            "Procedure" => Fact::Function(at),
            "ImplicitSelector" => Fact::Atom(
                s.role(at.node, "field")
                    .map(|node| s.text(node))
                    .unwrap_or_else(|| s.text(at.node).trim_start_matches('.'))
                    .into(),
            ),
            "Ident" | "Selector" => {
                if n.kind == "Selector" {
                    if let (Some(base), Some(name)) = (role("expr"), s.role(at.node, "field")) {
                        let name = s.text(name).to_owned();
                        let receiver=r.specialized_type(base,bindings);
                        if Self::any_type(&receiver) {return Fact::Unknown;}
                        let value = self.fact_at(r, base, bindings, depth + 1);
                        if let Fact::Record(_) | Fact::Pointer(..) | Fact::AmbientRandom = value {
                            return self.field(&value, &name);
                        }
                        let typ = r.specialized_type(at, bindings);
                        if matches!(base_type(&typ), Type::Enum(_)) {
                            return Self::normalize(r, Fact::Atom(name), &typ, bindings);
                        }
                    }
                }
                if crate::parallelcompile::known_callback(r, at) {
                    let candidates = r.candidates(at);
                    if candidates.len() == 1 {
                        return Fact::Function(candidates[0]);
                    }
                }
                if let Some(symbol) = r.symbol(at).map(|i| r.symbols[i].clone()) {
                    if symbol.constant {
                        if let Some(node) = symbol.value {
                            return self.fact_at(
                                r,
                                Ref {
                                    node,
                                    file: symbol.reference.file,
                                },
                                bindings,
                                depth + 1,
                            );
                        }
                    }
                }
                Fact::Unknown
            }
            "Unary" if n.op == "&" => {
                let Some(expr) = role("expr") else {
                    return Fact::Unknown;
                };
                Fact::Pointer(
                    self.key(r, expr),
                    Box::new(self.fact_at(r, expr, bindings, depth + 1)),
                    r.specialized_type(expr, bindings),
                )
            }
            "Deref" => role("expr")
                .map(|v| self.deref(&self.fact_at(r, v, bindings, depth + 1)))
                .unwrap_or_default(),
            "Cast" | "AutoCast" => role("expr")
                .map(|v| {
                    let value = self.fact_at(r, v, bindings, depth + 1);
                    self.convert(r, at, v, bindings, value)
                })
                .unwrap_or_default(),
            "Compound" => {
                let elems = s.group(at.node, "elems").to_vec();
                if elems.len() > 32 {
                    return Fact::Unknown;
                }
                let typ = r.specialized_type(at, bindings);
                let fields = r.record_fields(&typ);
                let mut values = if let Fact::Record(fields) = Self::zero(r, &typ, 0) {
                    fields
                } else {
                    BTreeMap::new()
                };
                for (index, node) in elems.into_iter().enumerate() {
                    let s = &r.sources[at.file];
                    let name = s
                        .role(node, "field")
                        .map(|field| s.text(field).to_string())
                        .or_else(|| fields.get(index).map(|(name, _)| name.clone()))
                        .unwrap_or_else(|| index.to_string());
                    let value = Ref {
                        node: s.role(node, "value").unwrap_or(node),
                        ..at
                    };
                    values.insert(name, self.fact_at(r, value, bindings, depth + 1));
                }
                Self::bounded(Fact::Record(values))
            }
            "Ternary" => {
                let Some(cond) = role("cond") else {
                    return Fact::Unknown;
                };
                if let Some(selected) = self.condition(r, cond, bindings) {
                    return role(if selected { "x" } else { "y" })
                        .map(|v| self.fact_at(r, v, bindings, depth + 1))
                        .unwrap_or_default();
                }
                let a = role("x").map(|v| self.fact_at(r, v, bindings, depth + 1));
                let b = role("y").map(|v| self.fact_at(r, v, bindings, depth + 1));
                if a == b {
                    a.unwrap_or_default()
                } else {
                    Fact::Unknown
                }
            }
            "Call" => {
                if let Some(callee) = role("callee") {
                    if r.denotes_type(callee)
                        || (crate::borroweval::Evaluator {
                            resolver: r,
                            arena: &mut crate::borrowflow::Arena::default(),
                        })
                        .builtin(at)
                        .as_deref()
                            == Some("transmute")
                    {
                        if let Some(node) =
                            r.sources[at.file].group(at.node, "args").last().copied()
                        {
                            let expr = Ref { node, ..at };
                            let value = self.fact_at(r, expr, bindings, depth + 1);
                            return self.convert(r, at, expr, bindings, value);
                        }
                    }
                }
                super::returns::value(self, r, at, bindings, depth + 1)
            }
            _ => Fact::Unknown,
        }
    }
    // Pointer facts retain the actual pointee type through rawptr erasure.
    // A different typed view can reorder/overlay fields, so identities from the
    // original record must never be interpreted using the new field names.
    fn convert(
        &self,
        r: &mut Resolver<'_>,
        at: Ref,
        expr: Ref,
        bindings: &Substitutions,
        value: Fact,
    ) -> Fact {
        let destination = r.specialized_type(at, bindings);
        if let Fact::Pointer(_, _, original) = &value {
            match base_type(&destination) {
                Type::Scalar { name, .. } if name == "rawptr" => return value,
                Type::Pointer(inner) if inner.as_ref() == original => return value,
                _ => return Fact::Unknown,
            }
        }
        if matches!(value, Fact::Nil) {
            return Fact::Nil;
        }
        if matches!(value, Fact::Constant(_)) {
            return Fact::Unknown;
        }
        let source = r.specialized_type(expr, bindings);
        if destination == source {
            value
        } else {
            Fact::Unknown
        }
    }
    pub fn condition(
        &self,
        r: &mut Resolver<'_>,
        at: Ref,
        bindings: &Substitutions,
    ) -> Option<bool> {
        let s = &r.sources[at.file];
        let n = s.node(at.node);
        if let Some(value) = r.context_condition(at, bindings) {
            return Some(value);
        }
        let role = |name| s.role(at.node, name).map(|node| Ref { node, ..at });
        match n.kind.as_str() {
            "Paren" => self.condition(r, role("expr")?, bindings),
            "Unary" if n.op == "!" => self.condition(r, role("expr")?, bindings).map(|v| !v),
            "Binary" => {
                let left = role("left")?;
                let right = role("right")?;
                if matches!(n.op.as_str(), "&&" | "||") {
                    let a = self.condition(r, left, bindings);
                    let b = self.condition(r, right, bindings);
                    return match (n.op.as_str(), a, b) {
                        ("&&", Some(false), _) | ("&&", _, Some(false)) => Some(false),
                        ("||", Some(true), _) | ("||", _, Some(true)) => Some(true),
                        (_, Some(a), Some(b)) => Some(if n.op == "&&" { a && b } else { a || b }),
                        _ => None,
                    };
                }
                let typ = r.specialized_type(left, bindings);
                let right_type=r.specialized_type(right,bindings);
                if Self::any_type(&typ) || Self::any_type(&right_type) || typ.native_float_constant() || right_type.native_float_constant() {return None;}
                let a = self.fact(r, left, bindings);
                let b = self.fact(r, right, bindings);
                let a = Self::normalize(r, a, &typ, bindings);
                let b = Self::normalize(r, b, &typ, bindings);
                // Equal summary records/pointer contents do not imply equal
                // runtime values or addresses. Only scalar constants and nil
                // carry an exact value suitable for pruning a runtime branch.
                if !matches!(a, Fact::Constant(_) | Fact::Nil)
                    || !matches!(b, Fact::Constant(_) | Fact::Nil)
                {
                    return None;
                }
                match n.op.as_str() {
                    "==" => Some(a == b),
                    "!=" => Some(a != b),
                    _ => None,
                }
            }
            _ => {
                if let Fact::Constant(Constant::Boolean(value)) = self.fact(r, at, bindings) {
                    Some(value)
                } else {
                    None
                }
            }
        }
    }
    pub fn normalize(r: &Resolver<'_>, value: Fact, typ: &Type, bindings: &Substitutions) -> Fact {
        if let Type::Enum(owner) = base_type(typ) {
            match value {
                Fact::Atom(name) => {
                    return r
                        .enum_ordinal(*owner, &name, bindings)
                        .map(|n| Fact::Constant(Constant::Enum(*owner, n)))
                        .unwrap_or_default()
                }
                Fact::Constant(Constant::Integer(n)) => {
                    return Fact::Constant(Constant::Enum(*owner, n))
                }
                _ => {}
            }
        }
        if let Fact::Constant(Constant::Integer(n)) = value {
            if let Type::Scalar{name,untyped} = base_type(typ) {
                if name.starts_with('f') || name.starts_with("complex") || name.starts_with("quaternion") {return Fact::Unknown;}
                if !untyped && (name.starts_with('i') || name.starts_with('u') || matches!(name.as_str(),"byte"|"rune")) {
                    let Some(bits)=r.simple_size(typ).and_then(|bytes|u32::try_from(bytes*8).ok()) else{return Fact::Unknown;};
                    if bits==128 {return if name.starts_with('u') && n<0 {Fact::Unknown}else{Fact::Constant(Constant::Integer(n))};}
                    if bits==0 || bits>128 {return Fact::Unknown;}
                    let mask=(1_i128<<bits)-1;
                    let mut converted=n&mask;
                    if (name.starts_with('i') || name=="rune") && converted&(1_i128<<(bits-1))!=0 {converted-=1_i128<<bits;}
                    return Fact::Constant(Constant::Integer(converted));
                }
            }
            return Fact::Constant(Constant::Integer(n));
        }
        value
    }
    pub fn begin(
        &mut self,
        r: &mut Resolver<'_>,
        procedure: Ref,
        args: &[Fact],
        bindings: &Substitutions,
    ) {
        self.reset();
        self.bindings = bindings.clone();
        let s = &r.sources[procedure.file];
        if let Some(signature) = s.role(procedure.node, "type") {
            for ((name, field), value) in crate::caller_metadata::params(s, signature)
                .into_iter()
                .zip(args)
            {
                let typ = r.sources[procedure.file]
                    .role(field, "type")
                    .map(|node| r.specialized_type(Ref { node, ..procedure }, bindings))
                    .unwrap_or(Type::Unknown);
                self.values.insert(
                    Ref {
                        node: name,
                        ..procedure
                    },
                    Self::normalize(r, value.clone(), &typ, bindings),
                );
            }
            let source = &r.sources[procedure.file];
            let fields = source
                .role(signature, "results")
                .map(|node| source.node(node).children.to_vec())
                .unwrap_or_default();
            for field in fields {
                let names = r.sources[procedure.file].group(field, "names").to_vec();
                let typ = r.sources[procedure.file]
                    .role(field, "type")
                    .map(|node| r.specialized_type(Ref { node, ..procedure }, bindings))
                    .unwrap_or(Type::Unknown);
                for node in names {
                    self.values
                        .insert(Ref { node, ..procedure }, Self::zero(r, &typ, 0));
                }
            }
        }
    }
    fn detach(&self, value: &Fact, constants: &BTreeSet<String>, name: &str, depth: usize) -> Fact {
        if depth > 16 {
            return Fact::Unknown;
        }
        match value {
            Fact::Constant(_) | Fact::Atom(_) if !constants.contains(name) => Fact::Unknown,
            Fact::Pointer(_, _, typ) => Fact::Pointer(
                None,
                Box::new(self.detach(&self.deref(value), constants, name, depth + 1)),
                typ.clone(),
            ),
            Fact::Record(fields) => Fact::Record(
                fields
                    .iter()
                    .map(|(key, value)| {
                        (key.clone(), self.detach(value, constants, key, depth + 1))
                    })
                    .collect(),
            ),
            Fact::Pack(values) => Fact::Pack(values.clone()),
            value => value.clone(),
        }
    }
    pub fn arguments(
        &mut self,
        r: &mut Resolver<'_>,
        plan: &crate::semantics::CallPlan,
    ) -> Vec<Fact> {
        let constants = self
            .interests
            .borrow_mut()
            .entry(plan.procedure)
            .or_insert_with(|| {
                let s = &r.sources[plan.procedure.file];
                let mut names = BTreeSet::new();
                if let Some(body) = s.role(plan.procedure.node, "body") {
                    for node in s.descendants(body) {
                        if matches!(
                            s.node(node).kind.as_str(),
                            "If" | "Switch" | "When" | "WhenExpr" | "Ternary" | "Call"
                        ) || s.node(node).kind == "Binary"
                            && matches!(s.node(node).op.as_str(), "&&" | "||")
                        {
                            for child in s
                                .node(node)
                                .children
                                .iter()
                                .copied()
                                .filter(|id| s.node(*id).kind != "Block")
                            {
                                for value in std::iter::once(child).chain(s.descendants(child)) {
                                    if s.node(value).kind == "Ident" {
                                        names.insert(s.text(value).to_string());
                                    }
                                }
                            }
                        }
                    }
                }
                names
            })
            .clone();
        let fields =
            crate::caller_metadata::params(&r.sources[plan.signature.file], plan.signature.node);
        plan.arguments
            .iter()
            .enumerate()
            .map(|(i, args)| {
                let name = fields
                    .get(i)
                    .map(|(name, _)| r.sources[plan.signature.file].text(*name))
                    .unwrap_or("")
                    .to_owned();
                let field = fields.get(i).map(|(_, field)| *field);
                let variadic = field.is_some_and(|field| {
                    r.sources[plan.signature.file].node(field).data["variadic"] == true
                });
                if let Some(field)=field {
                    let source=&r.sources[plan.signature.file];
                    let by_ptr=source.node(field).data["by_ptr"]==true;
                    let annotation=source.role(field,"type").map(|node|Ref{node,file:plan.signature.file});
                    let typ=annotation.map(|at|r.specialized_type(at,&plan.bindings)).unwrap_or(Type::Unknown);
                    if by_ptr || Self::any_type(&typ) || variadic && typ.element().is_some_and(Self::any_type) {
                        for arg in args {self.box_address(r,*arg);}
                    }
                }
                let value = if variadic {
                    Fact::Pack(
                        args.iter()
                            .flat_map(|arg| {
                                let value = self.fact(r, *arg, &plan.bindings);
                                let s = &r.sources[arg.file];
                                let parent = s.node(arg.node).parent;
                                let call = parent.and_then(|node| {
                                    if s.node(node).kind == "FieldValue" {
                                        s.node(node).parent
                                    } else {
                                        Some(node)
                                    }
                                });
                                let packed = call.is_some_and(|node| {
                                    s.node(node).kind == "Call"
                                        && (parent.is_some_and(|p| s.node(p).kind == "FieldValue")
                                            || s.node(node).spans.get("ellipsis").is_some_and(
                                                |span| s.node(arg.node).span.start >= span.end,
                                            ))
                                });
                                if packed {
                                    if let Fact::Pack(values) = value {
                                        return values;
                                    }
                                    let typ = r.specialized_type(*arg, &plan.bindings);
                                    return vec![Fact::Spread(
                                        typ.element().cloned().unwrap_or(Type::Unknown),
                                    )];
                                }
                                if s.node(arg.node).kind == "Ellipsis"
                                    || s.node(arg.node).kind == "Unary"
                                        && s.node(arg.node).op == ".."
                                {
                                    if let Some(node) = s.role(arg.node, "expr") {
                                        if let Fact::Pack(values) =
                                            self.fact(r, Ref { node, ..*arg }, &plan.bindings)
                                        {
                                            return values;
                                        }
                                        let typ = r
                                            .specialized_type(Ref { node, ..*arg }, &plan.bindings);
                                        return vec![Fact::Spread(
                                            typ.element().cloned().unwrap_or(Type::Unknown),
                                        )];
                                    }
                                }
                                vec![if value == Fact::Unknown {
                                    Fact::Typed(r.specialized_type(*arg, &plan.bindings))
                                } else {
                                    value
                                }]
                            })
                            .collect(),
                    )
                } else if args.len() == 1 {
                    self.fact(r, args[0], &plan.bindings)
                } else {
                    Fact::Unknown
                };
                self.detach(&value, &constants, &name, 0)
            })
            .collect()
    }
    pub fn bind_declaration(&mut self, r: &mut Resolver<'_>, at: Ref, bindings: &Substitutions) {
        let s = &r.sources[at.file];
        let names = s.group(at.node, "names").to_vec();
        let values = s.group(at.node, "values").to_vec();
        for (i, node) in names.into_iter().enumerate() {
            let value = values
                .get(i)
                .copied()
                .map(|node| self.fact(r, Ref { node, ..at }, bindings))
                .unwrap_or_else(|| {
                    if !values.is_empty() {
                        return Fact::Unknown;
                    }
                    super::random::zero(r, Ref { node, ..at }, bindings).unwrap_or_else(|| {
                        let typ = r.specialized_type(Ref { node, ..at }, bindings);
                        Self::zero(r, &typ, 0)
                    })
                });
            let typ=r.specialized_type(Ref{node,..at},bindings);
            if Self::contains_any(r,&typ,0) {if let Some(value)=values.get(i){self.box_value(r,Ref{node:*value,..at},&typ,0);}}
            self.values.insert(Ref { node, ..at }, Self::normalize(r,value,&typ,bindings));
        }
    }
    pub fn assign(&mut self, r: &mut Resolver<'_>, at: Ref, value: Fact) {
        let s = &r.sources[at.file];
        if s.node(at.node).kind == "Ident" && s.text(at.node) == "_" {
            return;
        }
        if s.node(at.node).kind == "Ident"
            && r.symbol(at)
                .is_none_or(|i| r.symbols[i].projection.is_none())
        {
            if let Some(key) = self.key(r, at) {
                self.values.insert(key, value);
            } else {
                self.forget_addresses(&value);
            }
            return;
        }
        if s.node(at.node).kind == "Paren" {
            if let Some(node) = s.role(at.node, "expr") {
                self.assign(r, Ref { node, ..at }, value);
                return;
            }
        }
        // A known store to a private by-value record updates that member. Do
        // not apply this to pointer/slice projections or unknown alias storage.
        let mut owner = at;
        let mut path = vec![];
        let mut private = true;
        for _ in 0..24 {
            let source = &r.sources[owner.file];
            if let Some(index) = r.symbol(owner) {
                if let Some(projection) = &r.symbols[index].projection {
                    path.push(projection.member.clone());
                    owner = projection.owner;
                    continue;
                }
            }
            if source.node(owner.node).kind == "Selector" {
                let Some(field) = source.role(owner.node, "field") else {
                    private = false;
                    break;
                };
                path.push(source.text(field).to_owned());
                let Some(node) = source.role(owner.node, "expr") else {
                    private = false;
                    break;
                };
                owner = Ref { node, ..owner };
                if !r
                    .specialized_type(owner, &Substitutions::new())
                    .record_like()
                {
                    private = false;
                    break;
                }
            } else {
                break;
            }
        }
        if private && !path.is_empty() && r.sources[owner.file].node(owner.node).kind == "Ident" {
            if let Some(key) = self.key(r, owner) {
                self.forget_addresses(&value);
                if self
                    .values
                    .get_mut(&key)
                    .is_some_and(|place| Self::store_field(place, &path, &value))
                {
                    return;
                }
            }
        }
        // A member store invalidates that local value. An indirect store can
        // change pointees/addressed locals, but cannot rewrite an unrelated
        // private parameter or local header passed by value.
        self.forget_addresses(&value);
        let mut base = r
            .symbol(at)
            .and_then(|i| {
                r.symbols[i]
                    .projection
                    .as_ref()
                    .map(|projection| projection.owner)
            })
            .unwrap_or(at);
        while let Some(node) = r.sources[base.file].role(base.node, "expr") {
            base = Ref { node, ..base };
        }
        if let Some(key) = self.key(r, base) {
            let typ = r.specialized_type(base, &Substitutions::new());
            if !matches!(
                base_type(&typ),
                Type::Pointer(_) | Type::Slice(_) | Type::Dynamic(_) | Type::Map { .. }
            ) {
                self.values.insert(key, Fact::Unknown);
            }
        }
        self.opaque_write();
    }
    pub fn forget_addresses(&mut self, value: &Fact) {
        match value {
            Fact::Pointer(Some(key), _, _) => {
                self.escaped.insert(*key);
                self.values.insert(*key, Fact::Unknown);
            }
            Fact::Pointer(None, _, _) => {
                self.opaque_write();
            }
            Fact::Record(fields) => {
                for value in fields.values() {
                    self.forget_addresses(value)
                }
            }
            Fact::Stream(value) | Fact::Buffered(value) | Fact::BufferedReader(value) => {
                self.forget_addresses(value)
            }
            Fact::Pack(values) => {
                for value in values {
                    self.forget_addresses(value)
                }
            }
            _ => {}
        }
    }
    pub fn join(&mut self, other: &Self) {
        if other.terminated {
            return;
        }
        if self.terminated {
            *self = other.clone();
            return;
        }
        for (key, value) in &mut self.values {
            if other.values.get(key) != Some(value) {
                *value = Fact::Unknown;
            }
        }
        self.escaped.extend(other.escaped.iter().copied());
    }
    pub fn through_pointer(&mut self, value: &Fact, replacement: Fact) {
        if let Fact::Pointer(Some(key), _, _) = value {
            self.values.insert(*key, replacement);
        } else {
            for value in self.values.values_mut() {
                *value = Fact::Unknown;
            }
        }
    }
}
