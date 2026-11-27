//! The reference Flow operations, using arena IDs for its cyclic object graph.
//! This is the symbolic counterpart to concrete instantiated slice privileges.
use crate::{
    permissions::{Access, Aspect, Permission, Step, Trait, MAX_PATH, OWNED, READONLY},
    semantics::{Ref, Resolver, Type},
};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

pub type Id = usize;
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Kind {
    #[default]
    Unknown,
    Generic,
    Pointer,
    Array,
    Fixed,
    Other,
}
fn kind(typ: &Type) -> Kind {
    match base(typ) {
        Type::Unknown => Kind::Unknown,
        Type::Generic => Kind::Generic,
        Type::Pointer(_) => Kind::Pointer,
        Type::Array { .. } => Kind::Array,
        Type::Fixed { .. } => Kind::Fixed,
        _ => Kind::Other,
    }
}
#[derive(Clone, Debug)]
pub struct Flow {
    pub borrowed: Permission,
    pub fields: BTreeMap<String, Id>,
    pub element: Option<Id>,
    // Permission for Integer/Rational allocation storage. A generic value
    // assignment clones those values but leaves pointer/aggregate copies
    // shallow; retain both provenance domains rather than one copy flag.
    pub numeric_borrowed: Option<Permission>,
    pub target: Option<Id>,
    pub location: Permission,
    pub locations: BTreeSet<Access>,
    pub origins: BTreeSet<Access>,
    pub copied_from: Option<Ref>,
    pub aliases: Vec<Id>,
    pub owner: Option<Ref>,
    pub stack: BTreeSet<Ref>,
    pub copied_base: Option<Id>,
    pub typ: Type,
    pub kind: Kind,
}
impl Default for Flow {
    fn default() -> Self {
        Self {
            borrowed: OWNED,
            fields: BTreeMap::new(),
            element: None,
            numeric_borrowed: None,
            target: None,
            location: OWNED,
            locations: BTreeSet::new(),
            origins: BTreeSet::new(),
            copied_from: None,
            aliases: vec![],
            owner: None,
            stack: BTreeSet::new(),
            copied_base: None,
            typ: Type::Unknown,
            kind: Kind::Unknown,
        }
    }
}
#[derive(Default)]
pub struct Arena {
    pub values: Vec<Flow>,
}
fn base(typ: &Type) -> &Type {
    typ.storage_shape()
}
fn numeric_permission(flow:&Flow)->Permission {
    flow.numeric_borrowed.clone().unwrap_or_else(||flow.borrowed.substitute(&mut |access| {
        let mut numeric=access.clone();
        if numeric.aspect==Aspect::Borrowed {numeric.aspect=Aspect::NumericBorrowed;}
        Permission::Access(numeric)
    }))
}
impl Arena {
    pub fn numeric_borrowed(&self,id:Id)->Permission {
        numeric_permission(&self.values[id])
    }
    pub fn add(&mut self, mut flow: Flow) -> Id {
        if flow.origins.is_empty() && !flow.locations.is_empty() {
            flow.origins = flow.locations.clone();
        }
        let id = self.values.len();
        self.values.push(flow);
        id
    }
    pub fn merge(&mut self, into: Id, other: Id) -> bool {
        self.merge_seen(into, other, &mut HashSet::new())
    }
    fn merge_seen(&mut self, into: Id, other: Id, seen: &mut HashSet<(Id, Id)>) -> bool {
        if into == other || !seen.insert((into, other)) {
            return false;
        }
        let incoming = self.values[other].clone();
        let old_numeric=self.numeric_borrowed(into);
        let numeric=old_numeric.join(&numeric_permission(&incoming));
        let value = &mut self.values[into];
        let borrowed = value.borrowed.join(&incoming.borrowed);
        let mut changed = numeric != old_numeric || borrowed != value.borrowed;
        value.numeric_borrowed = Some(numeric);
        value.borrowed = borrowed;
        for access in incoming.origins {
            changed |= value.origins.insert(access);
        }
        for owner in incoming.stack {
            changed |= value.stack.insert(owner);
        }
        for (name, child) in incoming.fields {
            if let Some(existing) = self.values[into].fields.get(&name).copied() {
                changed |= self.merge_seen(existing, child, seen);
            } else {
                self.values[into].fields.insert(name, child);
                changed = true;
            }
        }
        if let Some(child) = incoming.element {
            if let Some(existing) = self.values[into].element {
                changed |= self.merge_seen(existing, child, seen);
            } else {
                self.values[into].element = Some(child);
                changed = true;
            }
        }
        if let Some(target) = incoming.target {
            if let Some(existing) = self.values[into].target {
                if target != existing {
                    let targets = if self.values[existing].aliases.is_empty() {
                        vec![existing]
                    } else {
                        self.values[existing].aliases.clone()
                    };
                    let others = if self.values[target].aliases.is_empty() {
                        vec![target]
                    } else {
                        self.values[target].aliases.clone()
                    };
                    let mut combined = targets.clone();
                    for other in others {
                        if !combined.contains(&other) {
                            combined.push(other);
                        }
                    }
                    if combined.len() > targets.len() {
                        let alias = self.add(Flow {
                            aliases: combined,
                            ..Flow::default()
                        });
                        self.values[into].target = Some(alias);
                        changed = true;
                    }
                }
            } else {
                self.values[into].target = Some(target);
                changed = true;
            }
        }
        changed
    }
    fn origin_location(&self, id: Id, step: Step) -> Permission {
        let mut result = OWNED;
        for origin in &self.values[id].origins {
            if origin.path.len() >= MAX_PATH {
                return READONLY;
            }
            result = result.join(&Permission::Access(
                origin.step(step.clone(), Some(Aspect::Location)),
            ));
        }
        result
    }
    fn location_for(&self, id: Id, step: Step) -> Permission {
        let mut result = OWNED;
        for location in &self.values[id].locations {
            if location.path.len() >= MAX_PATH {
                return READONLY;
            }
            result = result.join(&Permission::Access(
                location.step(step.clone(), Some(Aspect::Location)),
            ));
        }
        if let Some(node) = self.values[id].copied_from {
            let trait_ = if matches!(step, Step::Field(_)) {
                Trait::Pointer
            } else {
                Trait::Sequence
            };
            return result.meet(&Permission::Type(trait_, node));
        }
        result
    }
    pub fn member(&mut self, id: Id, name: &str, resolver: &mut Resolver<'_>) -> Id {
        let value = self.values[id].clone();
        if value.kind == Kind::Pointer || matches!(base(&value.typ), Type::Pointer(_)) {
            let target = self.deref(id, resolver);
            return self.member(target, name, resolver);
        }
        if let Some(path) = resolver.member_path(&value.typ, name) {
            if path.len() > 1 {
                let mut result = id;
                for part in path {
                    result = self.member(result, &part, resolver);
                }
                return result;
            }
        }
        let child = if let Some(child) = value.fields.get(name) {
            *child
        } else {
            let child = self.add(Flow {
                owner: value.owner,
                ..Flow::default()
            });
            self.values[id].fields.insert(name.into(), child);
            child
        };
        let typ = resolver.member_type(&value.typ, name);
        if !typ.is_unknown() {
            self.values[child].typ = typ;
        }
        let step = Step::Field(name.into());
        let origins = value
            .origins
            .iter()
            .filter(|access| access.path.len() < MAX_PATH)
            .map(|access| access.step(step.clone(), None));
        self.values[child].origins.extend(origins);
        if !value.borrowed.is_owned() {
            let permission = value.borrowed.project(step.clone(), None);
            let numeric=self.numeric_borrowed(child).join(&value.borrowed.project(step.clone(),Some(Aspect::NumericBorrowed)));
            self.values[child].borrowed = self.values[child].borrowed.join(&permission);
            self.values[child].numeric_borrowed = Some(numeric);
        }
        let location = if !value.locations.is_empty() {
            let paths = value
                .locations
                .iter()
                .filter(|access| access.path.len() < MAX_PATH)
                .map(|access| access.step(step.clone(), None));
            self.values[child].locations.extend(paths);
            self.location_for(id, step)
        } else if let Some(node) = value.copied_from.filter(|_| !value.origins.is_empty()) {
            self.origin_location(id, step)
                .meet(&Permission::Type(Trait::Pointer, node))
        } else {
            value.location
        };
        self.values[child].location = location;
        // A copied SOA header continues to point at the original columns.
        // Indexing a column, including through a local pointer alias, writes
        // the rows in that storage.
        if matches!(base(&value.typ), Type::Soa(_)) {
            let target = self.deref(child, resolver);
            let borrowed = self.values[child].borrowed.clone();
            self.values[target].location = self.values[target].location.join(&borrowed);
        }
        if let Some(element) = value.element {
            self.merge(child, element);
        }
        child
    }
    pub fn item(&mut self, id: Id) -> Id {
        let value = self.values[id].clone();
        let child = if let Some(child) = value.element {
            child
        } else {
            let inline = matches!(
                value.kind,
                Kind::Array | Kind::Fixed | Kind::Generic | Kind::Unknown
            );
            let child = self.add(Flow {
                owner: if inline { value.owner } else { None },
                ..Flow::default()
            });
            self.values[id].element = Some(child);
            child
        };
        if let Some(typ) = value.typ.element() {
            self.values[child].typ = typ.clone();
        }
        self.values[child].origins.extend(
            value
                .origins
                .iter()
                .filter(|access| access.path.len() < MAX_PATH)
                .map(|access| access.step(Step::Item, None)),
        );
        if !value.borrowed.is_owned() {
            let numeric=self.numeric_borrowed(child).join(&value.borrowed.project(Step::Item,Some(Aspect::NumericBorrowed)));
            self.values[child].borrowed = self.values[child]
                .borrowed
                .join(&value.borrowed.project(Step::Item, None));
            self.values[child].numeric_borrowed = Some(numeric);
        }
        self.values[child].location = if !value.locations.is_empty() {
            self.values[child].locations.extend(
                value
                    .locations
                    .iter()
                    .filter(|access| access.path.len() < MAX_PATH)
                    .map(|access| access.step(Step::Item, None)),
            );
            self.location_for(id, Step::Item)
        } else {
            value.borrowed
        };
        child
    }
    pub fn deref(&mut self, id: Id, resolver: &mut Resolver<'_>) -> Id {
        let value = self.values[id].clone();
        if let Some(target) = value.target {
            if !self.values[target].aliases.is_empty() {
                return self.joined(&self.values[target].aliases.clone(), resolver);
            }
        } else if let Some(original) = value.copied_base {
            let target = self.deref(original, resolver);
            self.values[id].target = Some(target);
            return target;
        } else {
            if value
                .origins
                .iter()
                .any(|access| access.path.len() >= MAX_PATH)
            {
                return self.add(Flow {
                    borrowed: READONLY,
                    location: READONLY,
                    ..Flow::default()
                });
            }
            let locations = value
                .origins
                .iter()
                .map(|access| access.step(Step::Deref, None))
                .collect();
            let target = self.add(Flow {
                borrowed: value.borrowed.project(Step::Deref, None),
                location: self.origin_location(id, Step::Deref),
                locations,
                ..Flow::default()
            });
            self.values[id].target = Some(target);
        }
        let target = self.values[id].target.unwrap();
        if let Type::Pointer(typ) = base(&value.typ) {
            self.values[target].typ = *typ.clone();
        }
        target
    }
    pub fn header_copy(
        &mut self,
        id: Id,
        typ: Type,
        node: Option<Ref>,
        owner: Option<Ref>,
        resolver: &mut Resolver<'_>,
    ) -> Id {
        self.copy_seen(id, typ, node, owner, resolver, &mut HashMap::new())
    }
    fn copy_seen(
        &mut self,
        id: Id,
        typ: Type,
        node: Option<Ref>,
        owner: Option<Ref>,
        resolver: &mut Resolver<'_>,
        seen: &mut HashMap<Id, Id>,
    ) -> Id {
        if let Some(copy) = seen.get(&id) {
            return *copy;
        }
        let value = self.values[id].clone();
        let numeric=numeric_permission(&value);
        let copy = self.add(Flow {
            borrowed: value.borrowed,
            numeric_borrowed: Some(numeric),
            target: value.target,
            copied_from: node,
            owner,
            stack: value.stack,
            origins: value.origins,
            copied_base: matches!(base(&typ), Type::Pointer(_) | Type::Generic | Type::Unknown)
                .then_some(id),
            typ: typ.clone(),
            kind: kind(&typ),
            ..Flow::default()
        });
        seen.insert(id, copy);
        match base(&typ) {
            Type::Struct(_) | Type::Record(..) | Type::Anonymous(_) => {
                for (name, typ) in resolver.record_fields(&typ) {
                    let child = self.member(id, &name, resolver);
                    let child = self.copy_seen(child, typ, node, owner, resolver, seen);
                    self.values[copy].fields.insert(name, child);
                }
                self.values[copy].borrowed = OWNED;
            }
            Type::Array { elem, .. } | Type::Fixed { elem, .. } => {
                self.values[copy].borrowed = OWNED;
                let child = self.item(id);
                let child = self.copy_seen(child, *elem.clone(), node, owner, resolver, seen);
                self.values[copy].element = Some(child);
            }
            _ => {
                self.values[copy].fields = value.fields;
                // Header copies share backing storage even when no element
                // has been inspected yet. Otherwise a helper argument creates
                // a fresh element node on every fixed-point pass and loses
                // writes made through that alias.
                self.values[copy].element = if matches!(base(&typ),Type::Slice(_)|Type::Dynamic(_)|Type::Map{..}|Type::String{..}|Type::Generic|Type::Unknown) {
                    Some(self.item(id))
                } else {value.element};
            }
        }
        copy
    }
    pub fn joined(&mut self, values: &[Id], resolver: &mut Resolver<'_>) -> Id {
        self.join_seen(values, resolver, &mut HashMap::new())
    }
    fn join_seen(
        &mut self,
        values: &[Id],
        resolver: &mut Resolver<'_>,
        seen: &mut HashMap<Vec<Id>, Id>,
    ) -> Id {
        let mut distinct = vec![];
        for value in values {
            if !distinct.contains(value) {
                distinct.push(*value);
            }
        }
        if distinct.len() == 1 {
            return distinct[0];
        }
        if let Some(join) = seen.get(&distinct) {
            return *join;
        }
        let join = self.add(Flow {
            aliases: distinct.clone(),
            ..Flow::default()
        });
        seen.insert(distinct.clone(), join);
        let mut names = BTreeSet::new();
        let mut elements = false;
        let mut targets = vec![];
        for id in &distinct {
            let value = self.values[*id].clone();
            let numeric=self.numeric_borrowed(join).join(&numeric_permission(&value));
            let result = &mut self.values[join];
            result.numeric_borrowed=Some(numeric);
            result.borrowed = result.borrowed.join(&value.borrowed);
            result.location = result.location.join(&value.location);
            result.locations.extend(value.locations);
            result.origins.extend(value.origins);
            result.stack.extend(value.stack);
            names.extend(value.fields.keys().cloned());
            elements |= value.element.is_some();
            targets.extend(value.target);
        }
        for name in names {
            let members = distinct
                .iter()
                .map(|id| self.member(*id, &name, resolver))
                .collect::<Vec<_>>();
            let child = self.join_seen(&members, resolver, seen);
            self.values[join].fields.insert(name, child);
        }
        if elements {
            let members = distinct.iter().map(|id| self.item(*id)).collect::<Vec<_>>();
            let child = self.join_seen(&members, resolver, seen);
            self.values[join].element = Some(child);
        }
        if !targets.is_empty() {
            let target = self.add(Flow {
                aliases: targets,
                ..Flow::default()
            });
            self.values[join].target = Some(target);
        }
        join
    }
    pub fn write(&mut self, id: Id, other: Id) -> bool {
        self.write_seen(id, other, &mut HashSet::new())
    }
    fn write_seen(&mut self, id: Id, other: Id, seen: &mut HashSet<Id>) -> bool {
        if !seen.insert(id) {
            return false;
        }
        let mut changed = self.merge(id, other);
        for alias in self.values[id].aliases.clone() {
            changed |= self.write_seen(alias, other, seen);
        }
        changed
    }
    pub fn stack_owners(&self, id: Id) -> BTreeSet<Ref> {
        self.stack_seen(id, &mut HashSet::new())
    }
    fn stack_seen(&self, id: Id, seen: &mut HashSet<Id>) -> BTreeSet<Ref> {
        if !seen.insert(id) {
            return BTreeSet::new();
        }
        let value = &self.values[id];
        let mut result = value.stack.clone();
        for child in value
            .fields
            .values()
            .copied()
            .chain(value.element)
            .chain(value.target)
            .chain(value.aliases.iter().copied())
        {
            result.extend(self.stack_seen(child, seen));
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ast::NodeId;
    fn procedure() -> Ref {
        Ref {
            file: 0,
            node: NodeId(1),
        }
    }
    fn input() -> Flow {
        let access = Access {
            procedure: procedure(),
            index: 0,
            path: vec![],
            aspect: Aspect::Borrowed,
        };
        Flow {
            borrowed: Permission::Access(access.clone()),
            location: READONLY,
            locations: [access].into(),
            owner: Some(procedure()),
            typ: Type::Generic,
            kind: Kind::Generic,
            ..Flow::default()
        }
    }
    #[test]
    fn a_generic_record_copy_retains_a_native_pointer_condition_for_field_locations() {
        let mut arena = Arena::default();
        let mut resolver = Resolver::new(&[]);
        let source = arena.add(input());
        let site = Ref {
            file: 0,
            node: NodeId(7),
        };
        let copied = arena.header_copy(
            source,
            Type::Generic,
            Some(site),
            Some(procedure()),
            &mut resolver,
        );
        let member = arena.member(copied, "value", &mut resolver);
        let expected = Permission::Access(Access {
            procedure: procedure(),
            index: 0,
            path: vec![Step::Field("value".into())],
            aspect: Aspect::Location,
        })
        .meet(&Permission::Type(Trait::Pointer, site));
        assert_eq!(arena.values[member].location, expected);
        assert!(arena.values[copied].locations.is_empty());
    }
    #[test]
    fn fixed_arrays_copy_element_headers_and_slice_headers_keep_borrowing() {
        let mut arena = Arena::default();
        let mut resolver = Resolver::new(&[]);
        let scalar = Type::scalar("int");
        let typ = Type::Array {
            elem: Box::new(scalar.clone()),
            length: Some(2),
        };
        let source = arena.add(Flow {
            borrowed: READONLY,
            typ: typ.clone(),
            kind: Kind::Array,
            ..Flow::default()
        });
        let original = arena.item(source);
        let copied = arena.header_copy(source, typ, None, None, &mut resolver);
        let item = arena.item(copied);
        assert_ne!(original, item);
        assert_eq!(arena.values[item].location, OWNED);
        let typ = Type::Slice(Box::new(scalar));
        let source = arena.add(Flow {
            borrowed: READONLY,
            typ: typ.clone(),
            kind: Kind::Other,
            ..Flow::default()
        });
        let copied = arena.header_copy(source, typ, None, None, &mut resolver);
        let item = arena.item(copied);
        assert_eq!(arena.values[copied].borrowed, READONLY);
        assert_eq!(arena.values[item].location, READONLY);
    }
    #[test]
    fn writes_through_a_joined_pointer_preserve_all_actual_alias_targets() {
        let mut arena = Arena::default();
        let mut resolver = Resolver::new(&[]);
        let first = arena.add(Flow::default());
        let second = arena.add(Flow::default());
        let a = arena.add(Flow {
            target: Some(first),
            ..Flow::default()
        });
        let b = arena.add(Flow {
            target: Some(second),
            ..Flow::default()
        });
        let merged = arena.merge(a, b);
        assert!(merged);
        let targets = arena.deref(a, &mut resolver);
        let borrowed = arena.add(Flow {
            borrowed: READONLY,
            ..Flow::default()
        });
        let changed = arena.write(targets, borrowed);
        assert!(changed);
        assert_eq!(arena.values[first].borrowed, READONLY);
        assert_eq!(arena.values[second].borrowed, READONLY);
        let stable = arena.merge(a, b);
        assert!(!stable);
    }
    #[test]
    fn cycles_merge_to_a_fixed_point_and_stack_escape_inspection_terminates() {
        let mut arena = Arena::default();
        let a = arena.add(Flow::default());
        let b = arena.add(Flow {
            borrowed: READONLY,
            stack: [procedure()].into(),
            ..Flow::default()
        });
        arena.values[a].fields.insert("self".into(), a);
        arena.values[b].fields.insert("self".into(), b);
        let changed = arena.merge(a, b);
        let stable = arena.merge(a, b);
        let stack = arena.stack_owners(a);
        let expected = [procedure()].into();
        assert!(changed);
        assert!(!stable);
        assert_eq!(stack, expected);
    }
}
