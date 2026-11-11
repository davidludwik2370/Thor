//! Bounded independent work with input-order result and diagnostic ordering.
use std::sync::atomic::{AtomicUsize, Ordering};

pub(crate) fn map<T: Sync, R: Send>(items: &[T], action: impl Fn(&T) -> R + Sync) -> Vec<R> {
    let workers = std::env::var("THOR_JOBS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|count| *count > 0)
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map_or(1, usize::from)
                .min(4)
        });
    map_with_workers(items, workers.min(32), action)
}

fn map_with_workers<T: Sync, R: Send>(
    items: &[T],
    workers: usize,
    action: impl Fn(&T) -> R + Sync,
) -> Vec<R> {
    let workers = workers.min(items.len());
    if workers <= 1 {
        return items.iter().map(action).collect();
    }
    crate::profile::count("parallel.batches", 1);
    crate::profile::count("parallel.tasks", items.len() as u64);
    crate::profile::count("parallel.workers", workers as u64);
    let next = AtomicUsize::new(0);
    let mut results = std::thread::scope(|scope| {
        let handles = (0..workers)
            .map(|_| {
                let action = &action;
                let next = &next;
                scope.spawn(move || {
                    crate::profile::worker(|| {
                        let mut results = Vec::new();
                        loop {
                            let index = next.fetch_add(1, Ordering::Relaxed);
                            let Some(item) = items.get(index) else { break };
                            results.push((index, action(item)));
                        }
                        results
                    })
                })
            })
            .collect::<Vec<_>>();
        let mut results = Vec::with_capacity(items.len());
        for handle in handles {
            let (mut batch, report) = handle.join().expect("independent compiler worker panicked");
            crate::profile::merge_worker(report);
            results.append(&mut batch);
        }
        results
    });
    results.sort_unstable_by_key(|(index, _)| *index);
    results.into_iter().map(|(_, value)| value).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn results_and_errors_follow_input_order_and_each_item_runs_once() {
        for workers in [1, 2, 4, 32] {
            let calls = (0..73).map(|_| AtomicUsize::new(0)).collect::<Vec<_>>();
            let inputs = (0..calls.len()).collect::<Vec<_>>();
            let values = map_with_workers(&inputs, workers, |index| {
                calls[*index].fetch_add(1, Ordering::Relaxed);
                if index % 5 == 0 {
                    Err(*index)
                } else {
                    Ok(*index * 2)
                }
            });
            let expected = inputs
                .iter()
                .map(|index| {
                    if index % 5 == 0 {
                        Err(*index)
                    } else {
                        Ok(*index * 2)
                    }
                })
                .collect::<Vec<_>>();
            if values != expected || calls.iter().any(|count| count.load(Ordering::Relaxed) != 1) {
                panic!("worker count {workers} changed ordered results or repeated work");
            }
        }
        let empty = map_with_workers::<usize, usize>(&[], 4, |value| *value);
        if !empty.is_empty() {
            panic!("empty batch produced a result");
        }
    }
}
