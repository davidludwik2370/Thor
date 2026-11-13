//! Small immutable AST field tables. Keys are shared within a source, and each
//! table stores its entries contiguously instead of allocating a tree node.
use std::sync::Arc;
use super::PackedSlice;

#[derive(Clone, Debug)]
pub struct NodeFields<T> {
    entries: PackedSlice<(Arc<str>, T)>,
}
impl<T> Default for NodeFields<T> {
    fn default() -> Self {
        Self {
            entries: PackedSlice::default(),
        }
    }
}
impl<T> NodeFields<T> {
    pub(crate) fn from_entries(mut entries: Vec<(Arc<str>, T)>) -> Self {
        entries.sort_unstable_by(|a, b| a.0.cmp(&b.0));
        Self {
            entries: PackedSlice::from_vec(entries),
        }
    }
    pub(crate) fn shared(entries: &Arc<Vec<(Arc<str>, T)>>, range: std::ops::Range<usize>) -> Self {
        Self { entries: PackedSlice::shared(entries, range) }
    }
    pub fn get(&self, name: &str) -> Option<&T> {
        self.entries
            .iter()
            .find_map(|(key, value)| (key.as_ref() == name).then_some(value))
    }
    pub fn contains_key(&self, name: &str) -> bool {
        self.get(name).is_some()
    }
    pub fn iter(&self) -> impl Iterator<Item = (&str, &T)> {
        self.entries
            .iter()
            .map(|(name, value)| (name.as_ref(), value))
    }
}
impl<T: Clone> NodeFields<T> {
    pub(crate) fn into_entries(self) -> Vec<(Arc<str>, T)> {
        self.entries.into_vec()
    }
    pub(crate) fn insert(&mut self, name: Arc<str>, value: T) {
        match self.entries.binary_search_by(|(key, _)| key.cmp(&name)) {
            Ok(index) => self.entries.set(index, (name, value)),
            Err(index) => self.entries.insert(index, (name, value)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn extension_fields_keep_sorted_order_and_clones_own_their_values() {
        let original = NodeFields::from_entries(vec![("é".into(), 4), ("body".into(), 1)]);
        let mut copy = original.clone();
        copy.insert("body".into(), 2);
        copy.insert("future".into(), 3);
        let keys = copy.iter().map(|(name, _)| name).collect::<Vec<_>>();
        if original.get("body") != Some(&1)
            || copy.get("body") != Some(&2)
            || copy.get("é") != Some(&4)
            || copy.get("missing").is_some()
            || keys != ["body", "future", "é"]
        {
            panic!("AST field storage changed values or ordering");
        }
    }
}
