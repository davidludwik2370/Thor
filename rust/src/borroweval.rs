//! Reference Purity.flow/context_type/copied/storage/result_flow operations.
//! Kept separate from the fixed-point driver and native permission emission.
use crate::{
    ast::Span,
    borrowflow::{Arena, Flow, Id, Kind},
    permissions::{Permission, Trait, OWNED},
    semantics::{Ref, Resolver, Substitutions, Type},
};
use std::collections::{HashMap, HashSet};

#[derive(Default)]
pub struct Environment {
    pub symbols: HashMap<usize, Id>,
    pub literals: HashMap<Ref, Id>,
}
pub struct Evaluator<'a, 'r, 'f> {
    pub resolver: &'r mut Resolver<'a>,
    pub arena: &'f mut Arena,
}
fn base(mut typ: &Type) -> &Type {
    while let Type::Nominal(_, inner) = typ {
        typ = inner;
    }
    typ
}
impl Evaluator<'_, '_, '_> {
    fn empty(&mut self) -> Id {
        self.arena.add(Flow::default())
    }
    pub fn role(&self, node: Ref, role: &str) -> Option<Ref> {
        self.resolver.sources[node.file]
            .role(node.node, role)
            .map(|id| Ref {
                file: node.file,
                node: id,
            })
    }
    pub fn group(&self, node: Ref, group: &str) -> Vec<Ref> {
        self.resolver.sources[node.file]
            .group(node.node, group)
            .iter()
            .map(|id| Ref {
                file: node.file,
                node: *id,
            })
            .collect()
    }
    pub fn owner(&self, node: Ref) -> Option<Ref> {
        let source = &self.resolver.sources[node.file];
        source
            .ancestors(node.node)
            .find(|id| {
                source.node(*id).kind == "Procedure"
                    || source.node(*id).data["checked_comprehension"] == true
            })
            .map(|id| Ref {
                file: node.file,
                node: id,
            })
    }
    pub fn queried(&self, node: Ref) -> bool {
        self.resolver.sources[node.file]
            .ancestors(node.node)
            .any(|parent| {
                let parent = Ref {
                    file: node.file,
                    node: parent,
                };
                self.kind(parent) == "Call"
                    && self.role(parent, "callee") != Some(node)
                    && self.builtin(parent).as_deref().is_some_and(|name| {
                        ["type_of", "size_of", "align_of", "offset_of"].contains(&name)
                    })
            })
    }
    pub(crate) fn kind(&self, node: Ref) -> &str {
        &self.resolver.sources[node.file].node(node.node).kind
    }
    pub(crate) fn text(&self, node: Ref) -> &str {
        self.resolver.sources[node.file].text(node.node)
    }
    pub fn builtin(&self, call: Ref) -> Option<String> {
        let callee = self.role(call, "callee")?;
        if self.kind(callee) == "Directive" {
            let name = self.resolver.sources[callee.file]
                .names
                .get(self.resolver.sources[callee.file].node(callee.node).name);
            if ["assert", "config"].contains(&name) {
                return Some(format!("#{name}"));
            }
        }
        self.builtin_inner(callee, &mut HashSet::new())
    }
    pub fn clone_library(&self, call: Ref) -> Option<&'static str> {
        let callee = self.role(call, "callee")?;
        ["core:slice", "core:strings"].into_iter().find(|path| {
            self.resolver.library_alias_member(callee, path).as_deref() == Some("clone")
        })
    }
    pub fn io_call(&self, call: Ref) -> bool {
        self.role(call, "callee")
            .is_some_and(|callee| self.io_callee(callee, &mut HashSet::new()))
    }
    fn io_callee(&self, callee: Ref, seen: &mut HashSet<Ref>) -> bool {
        if !seen.insert(callee) {
            return false;
        }
        if self
            .resolver
            .library_alias_member(callee, "core:fmt")
            .is_some_and(|name| crate::purity::IO.contains(&name.as_str()))
        {
            return true;
        }
        if self.kind(callee) == "Paren" {
            return self
                .role(callee, "expr")
                .is_some_and(|value| self.io_callee(value, seen));
        }
        let Some(index) = self.resolver.symbol(callee) else {
            return false;
        };
        let symbol = &self.resolver.symbols[index];
        if !symbol.constant || symbol.immutable {
            return false;
        }
        let source = &self.resolver.sources[symbol.reference.file];
        let basic = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("runtime/basic");
        if source.path.parent() == Some(basic.as_path())
            && ["print", "input", "read", "write"].contains(&symbol.name.as_str())
        {
            return true;
        }
        symbol
            .value
            .map(|node| Ref {
                file: symbol.reference.file,
                node,
            })
            .filter(|value| matches!(self.kind(*value), "Ident" | "Selector" | "Paren"))
            .is_some_and(|value| self.io_callee(value, seen))
    }
    fn builtin_inner(&self, callee: Ref, seen: &mut HashSet<Ref>) -> Option<String> {
        if !seen.insert(callee) {
            return None;
        }
        if self.kind(callee) == "Paren" {
            return self.builtin_inner(self.role(callee, "expr")?, seen);
        }
        if let Some(id) = self.resolver.symbol(callee) {
            let symbol = &self.resolver.symbols[id];
            return (symbol.constant && !symbol.immutable)
                .then_some(symbol.value)
                .flatten()
                .and_then(|node| {
                    self.builtin_inner(
                        Ref {
                            file: symbol.reference.file,
                            node,
                        },
                        seen,
                    )
                });
        }
        let name = self.text(callee);
        self.resolver
            .native_builtin(callee, name)
            .then(|| name.into())
    }
    pub fn argument(&self, call: Ref, name: &str, index: usize) -> Option<Ref> {
        let args = self.group(call, "args");
        args.iter()
            .find(|arg| {
                self.kind(**arg) == "FieldValue"
                    && self
                        .role(**arg, "field")
                        .is_some_and(|field| self.text(field) == name)
            })
            .and_then(|arg| self.role(*arg, "value"))
            .or_else(|| {
                args.into_iter()
                    .filter(|arg| self.kind(*arg) != "FieldValue")
                    .nth(index)
            })
    }
    fn role_flow(&mut self, node: Ref, role: &str, env: &mut Environment) -> Id {
        self.role(node, role)
            .map(|child| self.flow(child, env))
            .unwrap_or_else(|| self.empty())
    }
    fn cases(&self, node: Ref) -> Vec<Ref> {
        let source = &self.resolver.sources[node.file];
        source.node(node.node).data["cases"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|case| {
                Span::from_wire(&case["result"]).and_then(|span| source.at(span, node.node))
            })
            .map(|id| Ref {
                file: node.file,
                node: id,
            })
            .collect()
    }
    pub fn flow(&mut self, node: Ref, env: &mut Environment) -> Id {
        let kind = self.kind(node).to_string();
        match kind.as_str() {
            "Ident" => {
                if let Some(projection) = self.resolver.projection(node) {
                    let owner = self.flow(projection.owner, env);
                    return self.arena.member(owner, &projection.member, self.resolver);
                }
                if let Some(symbol) = self.resolver.symbol(node) {
                    if let Some(value) = env.symbols.get(&symbol) {
                        return *value;
                    }
                    let value = self.empty();
                    env.symbols.insert(symbol, value);
                    return value;
                }
                self.empty()
            }
            "Paren" | "Cast" | "AutoCast" | "TypeAssertion" | "OrReturn" | "OrBranch"
            | "OrPanic" => self.role_flow(node, "expr", env),
            "FieldValue" => self.role_flow(node, "value", env),
            "Unary" if self.resolver.sources[node.file].node(node.node).op == "&" => {
                let target = self.role_flow(node, "expr", env);
                let stack = self.arena.values[target].owner.into_iter().collect();
                self.arena.add(Flow {
                    target: Some(target),
                    stack,
                    kind: Kind::Pointer,
                    ..Flow::default()
                })
            }
            "Deref" => {
                let base = self.role_flow(node, "expr", env);
                self.arena.deref(base, self.resolver)
            }
            "Selector" => {
                let Some(base_node) = self.role(node, "expr") else {
                    return self.empty();
                };
                let mut value = self.flow(base_node, env);
                let typ = self.resolver.type_of(base_node);
                if self.arena.values[value].typ.is_unknown() {
                    self.arena.values[value].typ = typ.clone();
                }
                if matches!(base(&typ), Type::Pointer(_)) {
                    value = self.arena.deref(value, self.resolver);
                }
                let name = self
                    .role(node, "field")
                    .map(|field| self.text(field).to_string())
                    .unwrap_or_default();
                self.arena.member(value, &name, self.resolver)
            }
            "Index" => {
                let base = self.role_flow(node, "expr", env);
                self.arena.item(base)
            }
            "Comprehension" => {
                let Some(output) = self.role(node, "output") else {
                    return self.empty();
                };
                let mut value = self.flow(output, env);
                if self.resolver.sources[node.file].node(node.node).data["spread"] == true {
                    value = self.arena.item(value);
                }
                let element = self.copied(value, output);
                self.arena.add(Flow {
                    element: Some(element),
                    ..Flow::default()
                })
            }
            "Slice" => {
                let Some(base_node) = self.role(node, "expr") else {
                    return self.empty();
                };
                let value = self.flow(base_node, env);
                let original = self.arena.values[value].clone();
                let element = self.arena.item(value);
                let mut stack = original.stack;
                if matches!(
                    base(&self.resolver.type_of(base_node)),
                    Type::Array { .. } | Type::Fixed { .. } | Type::Generic | Type::Unknown
                ) {
                    stack.extend(original.owner);
                }
                self.arena.add(Flow {
                    borrowed: original.borrowed,
                    element: Some(element),
                    stack,
                    ..Flow::default()
                })
            }
            "Compound" => {
                let typ = self.context_type(node);
                let owner = matches!(
                    base(&typ),
                    Type::Array { .. }
                        | Type::Fixed { .. }
                        | Type::Struct(_)
                        | Type::Record(..)
                        | Type::Anonymous(_)
                )
                .then(|| self.owner(node))
                .flatten();
                let elements = self.group(node, "elems");
                let stack = if matches!(base(&typ), Type::Slice(_)) && !elements.is_empty() {
                    self.owner(node).into_iter().collect()
                } else {
                    Default::default()
                };
                let result = self.arena.add(Flow {
                    owner,
                    stack,
                    ..Flow::default()
                });
                let fields = self.resolver.record_fields(&typ);
                for (index, element) in elements.into_iter().enumerate() {
                    if self.kind(element) == "FieldValue" {
                        let name = self
                            .role(element, "field")
                            .map(|field| self.text(field).to_string())
                            .unwrap_or_default();
                        let value = self.role_flow(element, "value", env);
                        self.arena.values[result].fields.insert(name, value);
                    } else if let Some((name, _)) = fields.get(index) {
                        let value = self.flow(element, env);
                        self.arena.values[result].fields.insert(name.clone(), value);
                    } else {
                        let target = if let Some(target) = self.arena.values[result].element {
                            target
                        } else {
                            let target = self.empty();
                            self.arena.values[result].element = Some(target);
                            target
                        };
                        let value = self.flow(element, env);
                        self.arena.merge(target, value);
                    }
                }
                if let Some(stable) = env.literals.get(&node).copied() {
                    self.arena.merge(stable, result);
                    stable
                } else {
                    env.literals.insert(node, result);
                    result
                }
            }
            "Ternary" | "WhenExpr" | "OrElse" => {
                let result = self.empty();
                for role in ["x", "y"] {
                    let value = self.role_flow(node, role, env);
                    self.arena.merge(result, value);
                }
                result
            }
            "SwitchResults" => {
                let result = self.empty();
                for value in self.group(node, "args") {
                    let value = self.flow(value, env);
                    self.arena.merge(result, value);
                }
                result
            }
            "SwitchExpr" | "CompileSwitchExpr" => {
                let result = self.empty();
                for value in self.cases(node) {
                    let value = self.flow(value, env);
                    self.arena.merge(result, value);
                }
                result
            }
            "Call" => {
                if self
                    .role(node, "callee")
                    .is_some_and(|callee| self.resolver.denotes_type(callee))
                {
                    return self
                        .group(node, "args")
                        .first()
                        .map(|arg| self.flow(*arg, env))
                        .unwrap_or_else(|| self.empty());
                }
                if self.builtin(node).is_some_and(|name| {
                    ["pop", "pop_safe", "pop_front", "pop_front_safe"].contains(&name.as_str())
                }) {
                    if let Some(target) = self.argument(node, "array", 0) {
                        let value = self.flow(target, env);
                        let value = self.arena.deref(value, self.resolver);
                        let value = self.arena.item(value);
                        let typ = self.resolver.type_of(node);
                        return self
                            .arena
                            .header_copy(value, typ, Some(node), None, self.resolver);
                    }
                }
                if self.clone_library(node) == Some("core:slice") {
                    if let Some(source) = self.argument(node, "a", 0) {
                        let value = self.flow(source, env);
                        let element = self.arena.item(value);
                        return self.arena.add(Flow {
                            element: Some(element),
                            ..Flow::default()
                        });
                    }
                }
                self.empty() // Checked callees promote independent result storage.
            }
            _ => self.empty(),
        }
    }
    pub fn context_type(&mut self, node: Ref) -> Type {
        self.context_seen(node, &mut HashSet::new())
    }
    fn context_seen(&mut self, node: Ref, seen: &mut HashSet<Ref>) -> Type {
        if !seen.insert(node) {
            return Type::Unknown;
        }
        let typ = self.resolver.type_of(node);
        if !typ.is_unknown() || self.kind(node) != "Compound" || self.role(node, "type").is_some() {
            return typ;
        }
        let source = &self.resolver.sources[node.file];
        let mut child = node.node;
        let mut parent = source.node(child).parent;
        while parent.is_some_and(|parent| source.node(parent).kind == "Paren") {
            child = parent.unwrap();
            parent = source.node(child).parent;
        }
        let Some(parent_id) = parent else {
            return typ;
        };
        let parent = Ref {
            file: node.file,
            node: parent_id,
        };
        if self.kind(parent) == "Declaration" {
            if let Some(typ) = self.role(parent, "type") {
                return self.resolver.type_of(typ);
            }
        }
        if self.kind(parent) == "Assignment" {
            if let Some(index) = source
                .group(parent.node, "rhs")
                .iter()
                .position(|id| *id == child)
            {
                if let Some(lhs) = source.group(parent.node, "lhs").get(index) {
                    return self.resolver.type_of(Ref {
                        file: node.file,
                        node: *lhs,
                    });
                }
            }
        }
        let aggregate = if self.kind(parent) == "FieldValue" {
            source.node(parent.node).parent.map(|id| Ref {
                file: node.file,
                node: id,
            })
        } else {
            Some(parent)
        };
        if let Some(aggregate) = aggregate {
            if self.kind(aggregate) == "Compound" {
                let record = self.context_seen(aggregate, seen);
                if self.kind(parent) == "FieldValue" {
                    if let Some(field) = self.role(parent, "field") {
                        return self
                            .resolver
                            .member_type(&record, self.text(field).to_string().as_str());
                    }
                } else if let Some(index) = self.resolver.sources[node.file]
                    .group(aggregate.node, "elems")
                    .iter()
                    .position(|id| *id == child)
                {
                    if let Some((_, typ)) = self.resolver.record_fields(&record).get(index) {
                        return typ.clone();
                    }
                    if let Some(elem) = record.element() {
                        return elem.clone();
                    }
                }
            } else if self.kind(aggregate) == "Call" {
                {
                    let plans = self.resolver.call_plans(aggregate, &Substitutions::new());
                    if plans.len() == 1 {
                        let plan = &plans[0];
                        if let Some(index) = plan.arguments.iter().position(|arguments| {
                            arguments
                                .iter()
                                .any(|value| value.file == node.file && value.node == child)
                        }) {
                            if let Some((_, Some(annotation))) =
                                self.resolver.fields(plan.signature, "params").get(index)
                            {
                                return self.resolver.specialized_type(*annotation, &plan.bindings);
                            }
                        }
                    }
                }
            }
        }
        typ
    }
    pub fn copied(&mut self, value: Id, node: Ref) -> Id {
        let typ = self.resolver.type_of(node);
        match base(&typ) {
            Type::Array { .. } => {
                let element = self.arena.item(value);
                self.arena.add(Flow {
                    element: Some(element),
                    ..Flow::default()
                })
            }
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                let result = self.empty();
                for (name, _) in self.resolver.record_fields(&typ) {
                    let child = self.arena.member(value, &name, self.resolver);
                    self.arena.values[result].fields.insert(name, child);
                }
                result
            }
            _ => value,
        }
    }
    pub fn storage(&mut self, node: Ref, env: &mut Environment) -> Permission {
        if self.kind(node) == "Paren" {
            return self
                .role(node, "expr")
                .map(|node| self.storage(node, env))
                .unwrap_or(OWNED);
        }
        if self.kind(node) == "Index" {
            let Some(base_node) = self.role(node, "expr") else {
                return OWNED;
            };
            let value = self.flow(base_node, env);
            let typ = self.resolver.type_of(base_node);
            return match base(&typ) {
                Type::Slice(_) | Type::Dynamic(_) | Type::String { .. } | Type::Map { .. } => {
                    self.arena.values[value].borrowed.clone()
                }
                Type::Pointer(_) => {
                    let target = self.arena.deref(value, self.resolver);
                    self.arena.values[target].location.clone()
                }
                Type::Generic | Type::Unknown => {
                    let pointer = Permission::Type(Trait::Pointer, base_node);
                    let sequence = Permission::Type(Trait::Sequence, base_node);
                    let target = self.arena.deref(value, self.resolver);
                    pointer
                        .meet(&self.arena.values[target].location)
                        .join(&sequence.meet(&self.arena.values[value].borrowed))
                }
                _ => self.storage(base_node, env),
            };
        }
        if self.kind(node) == "Slice" {
            let value = self.role_flow(node, "expr", env);
            return self.arena.values[value].borrowed.clone();
        }
        let value = self.flow(node, env);
        self.arena.values[value].location.clone()
    }
    pub fn result_flow(&mut self, node: Ref, index: usize, env: &mut Environment) -> Id {
        match self.kind(node) {
            "Paren" => self
                .role(node, "expr")
                .map(|child| self.result_flow(child, index, env))
                .unwrap_or_else(|| self.empty()),
            "SwitchResults" => self
                .group(node, "args")
                .get(index)
                .map(|child| self.flow(*child, env))
                .unwrap_or_else(|| self.empty()),
            "SwitchExpr" | "CompileSwitchExpr" => {
                let result = self.empty();
                for value in self.cases(node) {
                    let value = self.result_flow(value, index, env);
                    self.arena.merge(result, value);
                }
                result
            }
            _ if index == 0 => self.flow(node, env),
            _ => self.empty(),
        }
    }
}
