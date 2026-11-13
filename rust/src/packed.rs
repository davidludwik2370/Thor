//! Immutable ranges in source-owned vectors. Mutation detaches the visible
//! range, so cloned nodes keep their original field and child values.
use std::{ops::{Deref, Range}, sync::Arc};

#[derive(Clone, Debug)]
pub struct PackedSlice<T> {
    storage: Option<Arc<Vec<T>>>,
    range: Range<usize>,
}

impl<T> Default for PackedSlice<T> {
    fn default() -> Self {
        Self { storage: None, range: 0..0 }
    }
}

impl<T> PackedSlice<T> {
    pub(crate) fn from_vec(values: Vec<T>) -> Self {
        if values.is_empty() {
            return Self::default();
        }
        let length = values.len();
        Self { storage: Some(Arc::new(values)), range: 0..length }
    }

    pub(crate) fn shared(storage: &Arc<Vec<T>>, range: Range<usize>) -> Self {
        assert!(range.start <= range.end && range.end <= storage.len());
        if range.is_empty() {
            Self::default()
        } else {
            Self { storage: Some(storage.clone()), range }
        }
    }

    pub fn as_slice(&self) -> &[T] {
        match &self.storage {
            Some(storage) => &storage[self.range.clone()],
            None => &[],
        }
    }
}

impl<T: Clone> PackedSlice<T> {
    fn values_mut(&mut self) -> &mut Vec<T> {
        let full_length = self.storage.as_ref().map_or(0, |values| values.len());
        if self.range != (0..full_length) {
            let values = self.as_slice().to_vec();
            self.range = 0..values.len();
            self.storage = Some(Arc::new(values));
        }
        Arc::make_mut(self.storage.get_or_insert_with(|| Arc::new(Vec::new())))
    }

    pub(crate) fn set(&mut self, index: usize, value: T) {
        self.values_mut()[index] = value;
    }

    pub(crate) fn insert(&mut self, index: usize, value: T) {
        self.values_mut().insert(index, value);
        self.range.end += 1;
    }

    pub(crate) fn into_vec(self) -> Vec<T> {
        let Some(storage) = self.storage else {
            return Vec::new();
        };
        match Arc::try_unwrap(storage) {
            Ok(values) if self.range == (0..values.len()) => values,
            Ok(values) => values[self.range].to_vec(),
            Err(values) => values[self.range].to_vec(),
        }
    }
}

impl<T> Deref for PackedSlice<T> {
    type Target = [T];
    fn deref(&self) -> &[T] { self.as_slice() }
}

impl<T: Clone> IntoIterator for PackedSlice<T> {
    type Item = T;
    type IntoIter = std::vec::IntoIter<T>;
    fn into_iter(self) -> Self::IntoIter { self.into_vec().into_iter() }
}

impl<'a, T> IntoIterator for &'a PackedSlice<T> {
    type Item = &'a T;
    type IntoIter = std::slice::Iter<'a, T>;
    fn into_iter(self) -> Self::IntoIter { self.iter() }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mutations_detach_only_the_visible_range_and_preserve_siblings() {
        let storage = Arc::new(vec![1, 2, 3, 4, 5]);
        let original = PackedSlice::shared(&storage, 1..4);
        let sibling = PackedSlice::shared(&storage, 0..2);
        let mut changed = original.clone();
        changed.set(0, 20);
        changed.insert(1, 30);
        let consumed = original.clone().into_vec();
        if changed.as_slice() != [20, 30, 3, 4] || original.as_slice() != [2, 3, 4]
            || sibling.as_slice() != [1, 2] || consumed != [2, 3, 4]
            || storage.as_slice() != [1, 2, 3, 4, 5]
        {
            panic!("packed mutation changed another range");
        }
        let empty: PackedSlice<i32> = PackedSlice::default();
        if empty.storage.is_some() || !empty.into_vec().is_empty() {
            panic!("empty range allocated storage");
        }
    }
}
