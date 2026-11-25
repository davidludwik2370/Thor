//! Reference Resolver.protection/prepare_storage_checks, retaining native type
//! predicates when a generic reference path has not been instantiated yet.
use super::*;

#[derive(Clone)]
enum Predicate {
    Immutable(Ref,String),
    NotPointer(NodeId),
    InlineIndex(NodeId),
    Set(NodeId),
    Arrow(NodeId, String),
    Array(NodeId),
    Permission(crate::permissions::Permission),
}
#[derive(Clone)]
pub(super) struct Guard {
    predicates: Vec<Predicate>,
    origin: NodeId,
    message: String,
    checked_only: bool,
}
struct Protection {
    name: String,
    seed:Ref,
    predicates: Vec<Predicate>,
}
pub(super) struct NativeStorage {
    pub replacements: Vec<(Span, Code)>,
    pub inserts: Vec<(usize, Code)>,
}
fn storage_type(mut typ: Type) -> Type {
    while let Type::Nominal(_, inner) = typ {
        typ = *inner;
    }
    typ
}
impl Lower<'_, '_> {
    pub fn apply_borrowing(&mut self, facts: &crate::borrowprogram::Emission) {
        self.borrowing = facts.clone();
        for (site, permission) in &facts.checks {
            if site.file == self.file {
                self.place_storage_guard(
                    site.node,
                    Guard {
                        predicates: vec![Predicate::Permission(permission.clone())],
                        origin: site.node,
                        message:
                            "Pure code cannot mutate or delete storage borrowed from an input."
                                .into(),
                        checked_only: true,
                    },
                );
            }
        }
        for (site, message) in &facts.escapes {
            if site.file == self.file {
                self.place_storage_guard(
                    site.node,
                    Guard {
                        predicates: vec![Predicate::Permission(crate::permissions::READONLY)],
                        origin: site.node,
                        message: message.clone(),
                        checked_only: true,
                    },
                );
            }
        }
    }
    pub fn apply_local_borrowing(&mut self, facts: &crate::borrowlocals::Facts) {
        for (site, permission) in &facts.checks {
            if site.file == self.file {
                self.place_storage_guard(
                    site.node,
                    Guard {
                        predicates: vec![Predicate::Permission(permission.clone())],
                        origin: site.node,
                        message:
                            "Pure code cannot mutate or delete storage borrowed from an input."
                                .into(),
                        checked_only: true,
                    },
                );
            }
        }
    }
    pub(super) fn permission_query(
        &mut self,
        permission: &crate::permissions::Permission,
        site: NodeId,
    ) -> Result<String> {
        use crate::permissions::{Permission, Trait};
        let meta = format!("{}meta", self.prefix);
        match permission {
            Permission::Access(access) => {
                self.meta = true;
                let key = json!(access.key(self.resolver));
                let ownership_type = format!("{}_ownership_Type", self.borrow_marker);
                Ok(format!(
                    "({meta}.type_field_type({ownership_type},{key})==[0]bool)"
                ))
            }
            Permission::Constant(value) => Ok(value.to_string()),
            Permission::Type(trait_, node) if node.file == self.file => {
                let query = self.storage_query(node.node)?;
                match trait_ {
                    Trait::Pointer=>Ok(format!("{meta}.type_is_pointer({query})")),
                    Trait::Sequence=>Ok(format!("({meta}.type_is_slice({query}) || {meta}.type_is_dynamic_array({query}) || {meta}.type_is_string({query}))")),
                    Trait::Big|Trait::NotBig=>{let module=self.module("big");let predicate=format!("({query}=={module}.Integer || {query}=={module}.Rational)");Ok(if *trait_==Trait::NotBig{format!("!{predicate}")}else{predicate})},
                }
            }
            Permission::And(left, right) => Ok(format!(
                "({} && {})",
                self.permission_query(left, site)?,
                self.permission_query(right, site)?
            )),
            Permission::Or(parts) => {
                let parts = parts
                    .iter()
                    .map(|part| self.permission_query(part, site))
                    .collect::<Result<Vec<_>>>()?;
                Ok(format!("({})", parts.join(" || ")))
            }
            _ => Err(self.unsupported(site, "symbolic helper ownership arguments")),
        }
    }
    fn place_storage_guard(&mut self, id: NodeId, guard: Guard) {
        let mut statement = std::iter::once(id)
            .chain(self.source.ancestors(id))
            .find(|node| {
                matches!(
                    self.source.node(*node).kind.as_str(),
                    "Assignment"
                        | "Declaration"
                        | "Return"
                        | "ExpressionStatement"
                        | "Range"
                        | "If"
                        | "For"
                        | "Switch"
                        | "When"
                )
            })
            .unwrap_or(id);
        // A prefixed guard must not become the single statement owned by defer.
        // The deferred operation must retain its original execution time.
        if matches!(self.source.node(statement).kind.as_str(),"ExpressionStatement"|"Assignment") {
            if let Some(parent)=self.source.node(statement).parent.filter(|parent|self.source.node(*parent).kind=="Defer"){statement=parent;}
        }
        let parent = self.source.node(statement).parent;
        let control = parent
            .filter(|parent| matches!(self.source.node(*parent).kind.as_str(), "If" | "For"))
            .or_else(|| {
                matches!(self.source.node(statement).kind.as_str(), "If" | "For")
                    .then_some(statement)
            });
        let converted = self.source.ancestors(statement).find(|node| {
            self.source.node(*node).kind == "Block"
                && self.source.raw.as_bytes()[self.source.node(*node).span.start] != b'{'
        });
        if let Some(block) = control
            .and_then(|control| self.source.role(control, "body"))
            .or(converted)
        {
            self.storage_block_guards
                .entry(block)
                .or_default()
                .push(guard);
        } else {
            self.storage_guards
                .entry(statement)
                .or_default()
                .push(guard);
        }
    }
    fn value_iteration_slice(&self, mut id: NodeId) -> bool {
        while let Some(parent) = self.source.node(id).parent {
            if self.source.node(parent).kind != "Paren" {
                return self.source.node(parent).kind == "Range"
                    && self.source.role(parent, "expr") == Some(id)
                    && !self.source.group(parent, "names").iter().any(|name| {
                        self.source.node(*name).kind == "Unary" && self.source.node(*name).op == "&"
                    });
            }
            id = parent;
        }
        false
    }
    pub(super) fn native_storage(&mut self) -> Result<NativeStorage> {
        let mut replacements = vec![];
        let mut inserts = vec![];
        for (id, message) in self.blocked.clone() {
            let span = self.source.node(id).span;
            if self
                .edits
                .iter()
                .any(|(edit, _)| edit.start < span.end && span.start < edit.end)
            {
                continue;
            }
            let code = self.error_token(id, &message);
            replacements.push((span, code));
        }
        for (statement, guards) in self.storage_guards.clone() {
            let code = guards
                .into_iter()
                .map(|guard| self.render_storage_guard(guard))
                .collect::<Result<Vec<_>>>()?;
            let start = self.source.node(statement).span.start;
            inserts.push((start, Code::concat(code)));
        }
        for (block, guards) in self.storage_block_guards.clone() {
            let code = guards
                .into_iter()
                .map(|guard| self.render_storage_guard(guard))
                .collect::<Result<Vec<_>>>()?;
            let span = self.source.node(block).span;
            if self.source.raw.as_bytes()[span.start] == b'{' {
                let start = span.start + 1;
                inserts.push((start, Code::concat(code)));
            } else {
                self.convert_do(block);
                inserts.push((
                    span.start,
                    Code::concat([self.g("{\n", block), Code::concat(code)]),
                ));
                inserts.push((span.end, self.g("\n}", block)));
            }
        }
        Ok(NativeStorage {
            replacements,
            inserts,
        })
    }
    fn storage_query(&mut self, id: NodeId) -> Result<String> {
        let code = self.query_value(id)?.finish().text;
        let query = self.native("type_of");
        Ok(format!("{query}({code})"))
    }
    fn protection(&mut self, id: NodeId) -> Result<Option<Protection>> {
        let source = self.source;
        let node = source.node(id);
        if node.kind == "Paren" {
            return self.protection(self.role(id, "expr")?);
        }
        if let Some(projection) = self.resolver.projection(self.reference(id)) {
            if self.resolver.projection_indirect(&projection) {
                return Ok(None);
            }
            if projection.owner.file == self.file {
                return self.protection(projection.owner.node);
            }
            let owner = self
                .resolver
                .symbol(projection.owner)
                .map(|id| self.resolver.symbols[id].clone());
            return Ok(owner.and_then(|owner|{
                let marker=self.resolver.immutable_markers.get(&(owner.reference,owner.name.clone()));
                (owner.immutable||marker.is_some()).then(||Protection{name:owner.name.clone(),seed:Ref{file:owner.reference.file,node:owner.name_node},predicates:marker.map(|name|vec![Predicate::Immutable(owner.reference,name.clone())]).unwrap_or_default()})
            }));
        }
        if let Some(index) = self.resolver.symbol(self.reference(id)) {
            let sym = &self.resolver.symbols[index];
            let owner = &self.resolver.sources[sym.reference.file];
            let typed_index = owner.node(sym.reference.node).kind == "Range"
                && sym.index == 1
                && owner.role(sym.reference.node, "index_type").is_some();
            let marker=self.resolver.immutable_markers.get(&(sym.reference,sym.name.clone()));
            return Ok((sym.immutable||typed_index||marker.is_some()).then(||Protection{
                name:sym.name.clone(),seed:Ref{file:sym.reference.file,node:sym.name_node},predicates:marker.map(|name|vec![Predicate::Immutable(sym.reference,name.clone())]).unwrap_or_default(),
            }));
        }
        if node.kind == "Deref" || !matches!(node.kind.as_str(), "Selector" | "Index" | "MatrixIndex") {
            return Ok(None);
        }
        let base = self.role(id, "expr")?;
        let Some(mut protected) = self.protection(base)? else {
            return Ok(None);
        };
        let typ = storage_type(self.typ(base));
        if node.kind == "Selector" {
            if matches!(typ, Type::Pointer(_)) {
                return Ok(None);
            }
            // A concrete promoted pointer is also a shallow access path.
            if let Some(field) = source.role(id, "field") {
                if let Some(path) = self.resolver.member_path(&typ, source.text(field)) {
                    let mut current = typ.clone();
                    for field in path {
                        if matches!(storage_type(current.clone()), Type::Pointer(_)) {
                            return Ok(None);
                        }
                        current = self.resolver.member_type(&current, &field);
                    }
                }
            }
            if typ.is_unknown() {
                protected.predicates.push(Predicate::NotPointer(base));
            }
        } else {
            if matches!(
                typ,
                Type::Slice(_) | Type::Dynamic(_) | Type::Fixed{..} | Type::Pointer(_) | Type::String { .. }
            ) {
                return Ok(None);
            }
            if typ.is_unknown() {
                protected.predicates.push(Predicate::InlineIndex(base));
            }
        }
        Ok(Some(protected))
    }
    pub(super) fn render_storage_guard(&mut self, guard: Guard) -> Result<Code> {
        if guard.checked_only && !self.checked_mode {
            return Ok(Code::concat([]));
        }
        // Resolve reference expressions in the current capture environment.
        // A lowered helper may name the original local through a parameter.
        self.meta = true;
        let meta = format!("{}meta", self.prefix);
        let mut parts = vec![];
        for predicate in guard.predicates {
            let part = match predicate {
                Predicate::Immutable(owner,name)=>self.immutable_access(owner,&name),
                Predicate::NotPointer(id) => {
                    format!("!{meta}.type_is_pointer({})", self.storage_query(id)?)
                }
                Predicate::InlineIndex(id) => {
                    let query = self.storage_query(id)?;
                    format!("({meta}.type_is_array({query}) || {meta}.type_is_enumerated_array({query}) || {meta}.type_is_map({query}) || {meta}.type_is_matrix({query}))")
                }
                Predicate::Array(id) => {
                    let query = self.storage_query(id)?;
                    format!("({meta}.type_is_array({query}) || {meta}.type_is_enumerated_array({query}))")
                }
                Predicate::Set(id) => self.set_trait(id)?.finish().text,
                Predicate::Arrow(id, field) => {
                    let code = self.query_value(id)?.finish().text;
                    let query = self.native("type_of");
                    format!("(!{meta}.type_is_pointer({query}({code})) && {meta}.type_is_pointer({meta}.type_proc_parameter_type({query}(({code}).{field}),0)))")
                }
                Predicate::Permission(permission) => {
                    self.permission_query(&permission, guard.origin)?
                }
            };
            parts.push(format!("({part})"));
        }
        let condition = parts.join(" && ");
        let token = self.error_token(guard.origin, &guard.message).finish().text;
        if guard.checked_only {
            self.pure_error_sites
                .insert(token.clone(), self.reference(guard.origin));
        }
        // These are compile-time inspection predicates. No operand of type_of
        // is executed, including with assertions disabled in the final program.
        Ok(self.g(
            format!("#assert(!({condition}),{})\n", json!(token)),
            guard.origin,
        ))
    }
    pub(super) fn storage(&mut self) -> Result<()> {
        for (i, node) in self.source.nodes.iter().enumerate() {
            let id = NodeId(i);
            let targets = if node.kind == "Assignment" && node.op != "in" {
                self.source
                    .group(id, "lhs")
                    .iter()
                    .map(|target| (*target, *target, "write"))
                    .collect::<Vec<_>>()
            } else if node.kind == "Unary" && node.op == "&" {
                vec![(id, self.role(id, "expr")?, "take a mutable address of")]
            } else if node.kind == "Slice" && !self.source.thor && !self.value_iteration_slice(id) {
                let expr = self.role(id, "expr")?;
                vec![(id, expr, "create a mutable slice of")]
            } else if node.kind == "Cast"
                && !self.source.thor
                && matches!(
                    storage_type(self.typ(self.role(id, "type")?)),
                    Type::Slice(_)
                )
            {
                vec![(id, self.role(id, "expr")?, "create a mutable slice of")]
            } else if node.kind == "Range"
                && self.source.group(id, "names").iter().any(|name| {
                    self.source.node(*name).kind == "Unary" && self.source.node(*name).op == "&"
                })
            {
                let expr = self.role(id, "expr")?;
                vec![(expr, expr, "iterate by mutable reference over")]
            } else if node.kind == "Call" {
                let callee = self.role(id, "callee")?;
                let name = self.source.text(callee);
                if ["raw_data", "soa_unzip", "clear"].contains(&name)
                    && self.resolver.native_builtin(self.reference(callee), name)
                {
                    if let Some(arg) = self.source.group(id, "args").first().copied() {
                        let typ = storage_type(self.typ(arg));
                        if name != "clear" {
                            vec![(id, arg, "obtain a mutable data pointer to")]
                        } else if matches!(typ, Type::Set(_) | Type::Unknown | Type::Generic) {
                            vec![(id, arg, "write")]
                        } else {
                            vec![]
                        }
                    } else {
                        vec![]
                    }
                } else if self.source.node(callee).kind == "Selector"
                    && self.source.node(callee).op == "->"
                {
                    let receiver = self.role(callee, "expr")?;
                    let field = self.role(callee, "field")?;
                    let typ = storage_type(self.typ(receiver));
                    if typ.is_unknown() {
                        vec![(id, receiver, "take an implicit mutable address of")]
                    } else if !matches!(typ, Type::Pointer(_)) {
                        let member = self.resolver.member_type(&typ, self.source.text(field));
                        let pointer = if let Type::Procedure(signature) = member {
                            self.resolver
                                .fields(signature, "params")
                                .first()
                                .and_then(|(_, annotation)| *annotation)
                                .is_some_and(|annotation| {
                                    matches!(
                                        storage_type(self.resolver.type_of(annotation)),
                                        Type::Pointer(_)
                                    )
                                })
                        } else {
                            false
                        };
                        if pointer {
                            vec![(id, receiver, "take an implicit mutable address of")]
                        } else {
                            vec![]
                        }
                    } else {
                        vec![]
                    }
                } else {
                    vec![]
                }
            } else {
                vec![]
            };
            for (origin, target, action) in targets {
                let Some(mut protected) = self.protection(target)? else {
                    continue;
                };
                let typ = storage_type(self.typ(target));
                if action == "write" && node.kind == "Call" && typ.is_unknown() {
                    protected.predicates.push(Predicate::Set(target));
                }
                if action == "take an implicit mutable address of" && typ.is_unknown() {
                    let field = self
                        .source
                        .text(self.role(self.role(id, "callee")?, "field")?)
                        .to_string();
                    protected.predicates.push(Predicate::Arrow(target, field));
                }
                if [
                    "iterate by mutable reference over",
                    "obtain a mutable data pointer to",
                    "create a mutable slice of",
                ]
                .contains(&action)
                {
                    if matches!(
                        typ,
                        Type::Slice(_)
                            | Type::Dynamic(_)
                            | Type::Fixed{..}
                            | Type::Map { .. }
                            | Type::Pointer(_)
                            | Type::String { .. }
                    ) {
                        continue;
                    }
                    if typ.is_unknown() {
                        protected.predicates.push(Predicate::Array(target));
                    }
                }
                let message = format!("Cannot {action} immutable storage '{}'", protected.name);
                self.storage_roots.insert(origin,protected.seed);
                if protected.predicates.is_empty() {
                    self.blocked.insert(origin, message);
                    continue;
                }
                let guard = Guard {
                    predicates: protected.predicates,
                    origin,
                    message,
                    checked_only: false,
                };
                self.place_storage_guard(id, guard);
            }
        }
        Ok(())
    }
}

impl Lower<'_, '_>{
    fn immutable_access(&mut self,owner:Ref,name:&str)->String{
        let source=&self.resolver.sources[owner.file];
        // A for initializer has one binding, not alternative declarations in
        // its surrounding `when`. Its qualification is a literal fact. Emitting
        // an extra marker declaration would split the native for header.
        if source.node(owner.node).parent.is_some_and(|p|source.node(p).kind=="For"&&source.role(p,"init")==Some(owner.node)){
            return source.node(owner.node).immutable.to_string();
        }
        let directory=self.resolver.sources[owner.file].path.parent().unwrap();
        if self.source.path.parent()==Some(directory){return name.into();}
        for node in &self.source.nodes{if node.kind!="Import"{continue;}
            let Ok(path)=unquote(&node.op)else{continue;};
            let base=if let Some((collection,suffix))=path.split_once(':'){self.source.collection_roots.get(collection).map(|root|root.join(suffix))}
                else{self.source.path.parent().map(|root|root.join(&path))};
            if base.and_then(|path|crate::project::canonicalize(path).ok()).as_deref()!=Some(directory){continue;}
            let alias=format!("{}immutable_import_{}",self.source.prefix,node.span.start);self.modules.insert(alias.clone(),path);return format!("{alias}.{name}");
        }
        name.into()
    }
    pub(super) fn prepare_immutable_markers(&mut self){
        for (index,node) in self.source.nodes.iter().enumerate(){
            if node.kind!="Declaration"{continue;}
            let id=NodeId(index);
            if !self.source.group(id,"names").iter().any(|name|self.resolver.immutable_markers.contains_key(&(self.reference(id),self.source.text(*name).to_string()))){continue;}
            if let Some(block)=self.source.ancestors(id).find(|node|self.source.node(*node).kind=="Block"){
                if self.source.raw.as_bytes()[self.source.node(block).span.start]!=b'{'{let code=self.g("\n",block);self.body_prologues.entry(block).or_default().push(code);}
            }
        }
    }
    pub(super) fn immutable_declaration(&self,id:NodeId)->Code{
        if self.source.node(id).parent.is_some_and(|p|self.source.node(p).kind=="For"&&self.source.role(p,"init")==Some(id)){
            return Code::concat([]);
        }
        let mut parts=vec![];
        for name in self.source.group(id,"names"){
            if let Some(marker)=self.resolver.immutable_markers.get(&(self.reference(id),self.source.text(*name).to_string())){
                parts.push(self.gen(format!("\n{marker} :: {}\n",self.source.node(id).immutable),self.source.node(*name).span,"immutable"));
            }
        }
        Code::concat(parts)
    }
    pub(super) fn conditional_immutable_issue(&mut self,origin:NodeId,seed:Ref,message:&str)->bool{
        let Some((owner,name))=self.resolver.immutable_marker(seed)else{return false;};
        self.place_storage_guard(origin,Guard{predicates:vec![Predicate::Immutable(owner,name)],origin,message:message.into(),checked_only:false});true
    }
}
