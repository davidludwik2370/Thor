//! Sparse values with direct lookup through stable source/node identifiers.
//! Allocate a file's slot array only after its first stored value; keep the
//! larger values packed rather than reserving one value for every syntax node.
use super::Ref;

const EMPTY: usize = usize::MAX;

#[derive(Clone)]
struct Slots {
    count: usize,
    indexes: Vec<usize>,
}

#[derive(Clone)]
pub(super) struct NodeTable<T> {
    files: Vec<Slots>,
    values: Vec<(Ref, T)>,
}

impl<T> NodeTable<T> {
    pub fn new(counts: impl IntoIterator<Item = usize>) -> Self {
        Self {
            files: counts
                .into_iter()
                .map(|count| Slots {
                    count,
                    indexes: vec![],
                })
                .collect(),
            values: vec![],
        }
    }
    pub fn get(&self, key: &Ref) -> Option<&T> {
        let index = *self.files.get(key.file)?.indexes.get(key.node.0)?;
        self.values.get(index).map(|entry| &entry.1)
    }
    pub fn insert(&mut self, key: Ref, value: T) -> Option<T> {
        let file = &mut self.files[key.file];
        if file.indexes.is_empty() {
            file.indexes.resize(file.count, EMPTY);
        }
        let slot = &mut file.indexes[key.node.0];
        if *slot != EMPTY {
            return Some(std::mem::replace(&mut self.values[*slot].1, value));
        }
        *slot = self.values.len();
        self.values.push((key, value));
        None
    }
    pub fn get_or_insert_with(&mut self, key: Ref, make: impl FnOnce() -> T) -> &mut T {
        let file = &mut self.files[key.file];
        if file.indexes.is_empty() {
            file.indexes.resize(file.count, EMPTY);
        }
        let slot = &mut file.indexes[key.node.0];
        if *slot == EMPTY {
            let value = make();
            *slot = self.values.len();
            self.values.push((key, value));
        }
        &mut self.values[*slot].1
    }
    pub fn clear(&mut self) {
        for (key, _) in &self.values {
            self.files[key.file].indexes[key.node.0] = EMPTY;
        }
        self.values.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ast::NodeId;

    #[test]
    fn separate_files_replacements_clears_and_clones_preserve_independent_values() {
        let a = Ref {
            file: 0,
            node: NodeId(2),
        };
        let b = Ref {
            file: 1,
            node: NodeId(2),
        };
        let mut table = NodeTable::new([4, 5, 1_000]);
        table.insert(a, String::from("first"));
        table
            .get_or_insert_with(b, || String::from("second"))
            .push('!');
        table
            .get_or_insert_with(a, || panic!("existing entry rebuilt"))
            .push('?');
        if table.get(&a).map(String::as_str) != Some("first?")
            || table.get(&b).map(String::as_str) != Some("second!")
            || !table.files[2].indexes.is_empty()
        {
            panic!("node table mixed files or allocated untouched file slots");
        }
        let mut copy = table.clone();
        let previous = table.insert(a, String::from("replacement"));
        table.clear();
        table.insert(b, String::from("after clear"));
        copy.get_or_insert_with(a, String::new).push('!');
        if previous.as_deref() != Some("first?")
            || table.get(&a).is_some()
            || table.get(&b).map(String::as_str) != Some("after clear")
            || copy.get(&a).map(String::as_str) != Some("first?!")
            || copy.get(&b).map(String::as_str) != Some("second!")
        {
            panic!("replacement, clearing or cloning changed another table entry");
        }
        table.clear();
        table.clear();
        if table.get(&b).is_some() {
            panic!("cleared slot kept an old value");
        }
    }
}
