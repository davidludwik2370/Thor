//! Contiguous source-level storage for AST links and named field tables.
use super::*;
use std::ops::Range;

#[derive(Default)]
pub(super) struct FieldsBuilder {
    pub roles: Vec<(Arc<str>, NodeId)>,
    pub spans: Vec<(Arc<str>, Span)>,
    pub lists: Vec<(Arc<str>, Range<usize>)>,
    pub items: Vec<NodeId>,
    pub counts: Vec<[usize; 3]>,
}

impl FieldsBuilder {
    pub fn pack(nodes: &mut [Node]) {
        let mut builder = Self::default();
        for node in nodes.iter_mut() {
            let roles = std::mem::take(&mut node.roles).into_entries();
            let spans = std::mem::take(&mut node.spans).into_entries();
            let lists = std::mem::take(&mut node.lists).into_entries();
            builder.counts.push([roles.len(), spans.len(), lists.len()]);
            builder.roles.extend(roles);
            builder.spans.extend(spans);
            for (name, items) in lists {
                let start = builder.items.len();
                builder.items.extend(items);
                builder.lists.push((name, start..builder.items.len()));
            }
        }
        builder.finish(nodes);
    }

    pub fn finish(self, nodes: &mut [Node]) {
        assert_eq!(nodes.len(), self.counts.len());
        let items = Arc::new(self.items);
        let lists = Arc::new(self.lists.into_iter().map(|(name, range)| {
            (name, PackedSlice::shared(&items, range))
        }).collect());
        let roles = Arc::new(self.roles);
        let spans = Arc::new(self.spans);
        let mut offsets = [0; 3];
        for (node, counts) in nodes.iter_mut().zip(self.counts) {
            node.roles = NodeFields::shared(&roles, offsets[0]..offsets[0]+counts[0]);
            node.spans = NodeFields::shared(&spans, offsets[1]..offsets[1]+counts[1]);
            node.lists = NodeFields::shared(&lists, offsets[2]..offsets[2]+counts[2]);
            for (offset, count) in offsets.iter_mut().zip(counts) {
                *offset += count;
            }
        }
    }
}

pub(super) fn pack_children(nodes: &mut [Node]) {
    let mut offsets = vec![0; nodes.len()+1];
    for node in nodes.iter() {
        if let Some(parent) = node.parent {
            offsets[parent.0+1] += 1;
        }
    }
    for index in 1..offsets.len() {
        offsets[index] += offsets[index-1];
    }
    let mut children = vec![NodeId(0); offsets[nodes.len()]];
    let mut cursors = offsets.clone();
    for (index, node) in nodes.iter().enumerate() {
        if let Some(parent) = node.parent {
            children[cursors[parent.0]] = NodeId(index);
            cursors[parent.0] += 1;
        }
    }
    let children = Arc::new(children);
    for (index, node) in nodes.iter_mut().enumerate() {
        node.children = PackedSlice::shared(&children, offsets[index]..offsets[index+1]);
    }
}

pub(super) fn pack_spans(nodes: &[Node]) -> HashMap<Span, PackedSlice<NodeId>> {
    struct Bucket {
        start: usize,
        count: usize,
        written: usize,
    }
    let mut buckets: HashMap<Span, Bucket> = HashMap::new();
    for node in nodes {
        buckets.entry(node.span).or_insert(Bucket { start: 0, count: 0, written: 0 }).count += 1;
    }
    let mut offset = 0;
    for bucket in buckets.values_mut() {
        bucket.start = offset;
        offset += bucket.count;
    }
    let mut values = vec![NodeId(0); nodes.len()];
    for (index, node) in nodes.iter().enumerate() {
        let bucket = buckets.get_mut(&node.span).expect("indexed node span");
        values[bucket.start+bucket.written] = NodeId(index);
        bucket.written += 1;
    }
    let values = Arc::new(values);
    buckets.into_iter().map(|(span, bucket)| {
        (span, PackedSlice::shared(&values, bucket.start..bucket.start+bucket.count))
    }).collect()
}
