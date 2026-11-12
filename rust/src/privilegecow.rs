//! Persistent analysis collections. Empty values allocate nothing; snapshots
//! share populated collections and mutation detaches their backing storage.
use std::collections::BTreeSet;
use std::{
    ops::{Deref, DerefMut},
    sync::Arc,
};

enum State<T> {
    Empty(T),
    Data(Arc<T>),
}
pub(super) struct Shared<T>(State<T>);
impl<T: Default> Default for Shared<T> {
    fn default() -> Self {
        Self(State::Empty(T::default()))
    }
}
impl<T: Default> Clone for Shared<T> {
    fn clone(&self) -> Self {
        match &self.0 {
            State::Empty(_) => Self::default(),
            State::Data(value) => Self(State::Data(value.clone())),
        }
    }
}
impl<T: Default> Shared<T> {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn clear(&mut self) {
        *self = Self::default();
    }
}
impl<T: Default + PartialEq> From<T> for Shared<T> {
    fn from(value: T) -> Self {
        if value == T::default() {
            Self::default()
        } else {
            Self(State::Data(Arc::new(value)))
        }
    }
}
impl<T> Deref for Shared<T> {
    type Target = T;
    fn deref(&self) -> &T {
        match &self.0 {
            State::Empty(value) => value,
            State::Data(value) => value,
        }
    }
}
impl<T: Default + Clone> DerefMut for Shared<T> {
    fn deref_mut(&mut self) -> &mut T {
        if let State::Empty(value) = &mut self.0 {
            self.0 = State::Data(Arc::new(std::mem::take(value)));
        }
        match &mut self.0 {
            State::Data(value) => {
                if Arc::strong_count(value) > 1 {
                    crate::profile::count("permissions.collection_detaches", 1);
                }
                Arc::make_mut(value)
            }
            State::Empty(_) => unreachable!("empty collection was promoted"),
        }
    }
}
impl<T: PartialEq> PartialEq for Shared<T> {
    fn eq(&self, other: &Self) -> bool {
        match (&self.0, &other.0) {
            (State::Data(a), State::Data(b)) if Arc::ptr_eq(a, b) => true,
            _ => **self == **other,
        }
    }
}
impl<T: Eq> Eq for Shared<T> {}
impl<T: Ord> Ord for Shared<T> {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        (**self).cmp(&**other)
    }
}
impl<T: PartialOrd> PartialOrd for Shared<T> {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        (**self).partial_cmp(&**other)
    }
}
impl<T: std::hash::Hash> std::hash::Hash for Shared<T> {
    fn hash<H: std::hash::Hasher>(&self, h: &mut H) {
        (**self).hash(h);
    }
}
impl<T: std::fmt::Debug> std::fmt::Debug for Shared<T> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        (**self).fmt(f)
    }
}
impl<T: Default + PartialEq + FromIterator<I>, I> FromIterator<I> for Shared<T> {
    fn from_iter<U: IntoIterator<Item = I>>(iter: U) -> Self {
        T::from_iter(iter).into()
    }
}
impl<T: Clone + IntoIterator> IntoIterator for Shared<T> {
    type Item = T::Item;
    type IntoIter = T::IntoIter;
    fn into_iter(self) -> Self::IntoIter {
        match self.0 {
            State::Empty(value) => value,
            State::Data(value) => Arc::try_unwrap(value).unwrap_or_else(|value| (*value).clone()),
        }
        .into_iter()
    }
}
impl<'a, T> IntoIterator for &'a Shared<T>
where
    &'a T: IntoIterator,
{
    type Item = <&'a T as IntoIterator>::Item;
    type IntoIter = <&'a T as IntoIterator>::IntoIter;
    fn into_iter(self) -> Self::IntoIter {
        self.deref().into_iter()
    }
}
impl<T: Ord + Clone> Shared<BTreeSet<T>> {
    pub fn insert(&mut self, value: T) -> bool {
        if self.contains(&value) {
            return false;
        }
        self.deref_mut().insert(value)
    }
}
impl<T: Ord + Clone> Extend<T> for Shared<BTreeSet<T>> {
    fn extend<I: IntoIterator<Item = T>>(&mut self, iter: I) {
        for item in iter {
            self.insert(item);
        }
    }
}
impl<T: Ord, const N: usize> From<[T; N]> for Shared<BTreeSet<T>> {
    fn from(values: [T; N]) -> Self {
        Self::from(BTreeSet::from(values))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    #[test]
    fn snapshots_detach_only_on_changes_and_keep_empty_values_allocation_free() {
        let empty = Shared::<BTreeSet<i32>>::default();
        if !matches!(empty.0, State::Empty(_)) || !matches!(empty.clone().0, State::Empty(_)) {
            panic!("empty allocation");
        }
        let mut set: Shared<BTreeSet<i32>> = [1, 2].into();
        let saved = set.clone();
        let changed = set.insert(2);
        if changed
            || !matches!((&set.0,&saved.0),(State::Data(a),State::Data(b)) if Arc::ptr_eq(a,b))
        {
            panic!("unchanged insertion detached");
        }
        set.insert(3);
        set.extend([1, 4]);
        if saved.iter().copied().collect::<Vec<_>>() != vec![1, 2] || set.len() != 4 {
            panic!("set snapshot mutated");
        }
        let mut map: Shared<BTreeMap<String, i32>> = [("x".into(), 1)].into_iter().collect();
        let old = map.clone();
        map.insert("y".into(), 2);
        map.clear();
        if !map.is_empty() || old.len() != 1 || !matches!(map.0, State::Empty(_)) {
            panic!("map snapshot or clearing failed");
        }
        let mut path: Shared<Vec<i32>> = vec![1, 2].into();
        let before = path.clone();
        path.push(3);
        if *before != vec![1, 2] || *path != vec![1, 2, 3] {
            panic!("path extension mutated its prefix");
        }
    }
}
