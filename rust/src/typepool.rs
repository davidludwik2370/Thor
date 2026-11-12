//! Canonical immutable type handles for hot analysis keys and graph snapshots.
use super::Type;
use std::{
    collections::HashMap,
    hash::{Hash, Hasher},
    ops::Deref,
    sync::Arc,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) struct TypeId(u32);
struct Canonical {
    id: TypeId,
    raw: Arc<Type>,
    fingerprint: u64,
}
#[derive(Clone)]
pub(crate) struct SharedType(Arc<Canonical>);
impl SharedType {
    pub fn id(&self) -> TypeId {
        self.0.id
    }
    pub fn owned(&self) -> Type {
        (**self).clone()
    }
}
impl Deref for SharedType {
    type Target = Type;
    fn deref(&self) -> &Type {
        &self.0.raw
    }
}
impl std::fmt::Debug for SharedType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.deref().fmt(f)
    }
}
impl PartialEq for SharedType {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}
impl Eq for SharedType {}
impl Hash for SharedType {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.fingerprint.hash(state);
    }
}
#[derive(Clone, Default)]
pub(crate) struct TypePool {
    types: HashMap<Arc<Type>, SharedType>,
    base: Option<Arc<TypePool>>,
}
impl TypePool {
    pub fn fork(base: Arc<Self>) -> Self {
        Self {
            types: HashMap::new(),
            base: Some(base),
        }
    }
    fn len(&self) -> usize {
        self.types.len() + self.base.as_ref().map_or(0, |base| base.len())
    }
    fn get(&self, typ: &Type) -> Option<&SharedType> {
        self.types.get(typ).or_else(|| self.base.as_ref()?.get(typ))
    }
    pub fn intern(&mut self, typ: &Type) -> SharedType {
        if let Some(shared) = self.get(typ) {
            crate::profile::count("types.intern_hits", 1);
            return shared.clone();
        }
        let id = TypeId(u32::try_from(self.len()).expect("type arena exhausted"));
        let mut hash = std::collections::hash_map::DefaultHasher::new();
        typ.hash(&mut hash);
        let raw = Arc::new(typ.clone());
        let shared = SharedType(Arc::new(Canonical {
            id,
            raw: raw.clone(),
            fingerprint: hash.finish(),
        }));
        self.types.insert(raw, shared.clone());
        crate::profile::count("types.interned", 1);
        shared
    }
}
pub(crate) enum TypeInput {
    Owned(Type),
    Shared(SharedType),
}
impl From<Type> for TypeInput {
    fn from(value: Type) -> Self {
        Self::Owned(value)
    }
}
impl From<SharedType> for TypeInput {
    fn from(value: SharedType) -> Self {
        Self::Shared(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn worker_type_deltas_keep_base_ids_and_never_mix_new_identities() {
        let mut parent = TypePool::default();
        let typ = Type::scalar("int");
        let base_type = parent.intern(&typ);
        let seed = Arc::new(parent);
        let mut a = TypePool::fork(seed.clone());
        let mut b = TypePool::fork(seed.clone());
        let fresh_a = a.intern(&Type::Slice(Box::new(typ.clone())));
        let fresh_b = b.intern(&Type::Slice(Box::new(Type::scalar("f64"))));
        if a.intern(&typ) != base_type
            || b.intern(&typ) != base_type
            || fresh_a.id() == base_type.id()
            || fresh_a.id() != fresh_b.id()
            || fresh_a == fresh_b
            || seed.len() != 1
        {
            panic!("local type IDs or canonical storage crossed worker boundaries");
        }
    }
    #[test]
    fn identities_share_storage_and_remain_scoped_to_their_arena() {
        let mut pool = TypePool::default();
        let typ = Type::Slice(Box::new(Type::scalar("int")));
        let a = pool.intern(&typ);
        let b = pool.intern(&typ);
        let other = TypePool::default().intern(&typ);
        if !Arc::ptr_eq(&a.0, &b.0) || a == other || a.id() != b.id() {
            panic!("type sharing/equality changed");
        }
        let mut set = std::collections::HashSet::new();
        set.insert(a.clone());
        set.insert(b);
        set.insert(other);
        if set.len() != 2 {
            panic!("type arenas were conflated");
        }
        let nominal = Type::Nominal(
            crate::semantics::Ref {
                file: 0,
                node: crate::ast::NodeId(1),
            },
            Box::new(typ),
        );
        if pool.intern(&nominal) == a {
            panic!("nominal identity was erased");
        }
    }
}
