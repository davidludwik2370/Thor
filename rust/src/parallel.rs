//! Bounded independent work with input-order result and diagnostic ordering.
use std::sync::atomic::{AtomicUsize, Ordering};
static REQUESTED_WORKERS: AtomicUsize = AtomicUsize::new(0);

pub(crate) fn set_workers(count: usize) {
    REQUESTED_WORKERS.store(count, Ordering::Relaxed);
}

pub(crate) fn map<T: Sync, R: Send>(items: &[T], action: impl Fn(&T) -> R + Sync) -> Vec<R> {
    map_with_workers(items, workers(), action)
}

pub(crate) fn workers() -> usize {
    let requested = REQUESTED_WORKERS.load(Ordering::Relaxed);
    if requested > 0 {
        return requested.min(32);
    }
    std::env::var("THOR_JOBS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|count| *count > 0)
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map_or(1, usize::from)
                .min(4)
        })
        .min(32)
}

/// Construct one mutable state per worker, inside its thread. State is never
/// shared or returned; only ordered, owned results cross the thread boundary.
pub(crate) fn map_with_local<T: Sync, R: Send, S>(
    items: &[T],
    make: impl Fn() -> S + Sync,
    action: impl Fn(&mut S, &T) -> R + Sync,
) -> Vec<R> {
    map_local_with_workers(items, workers(), make, action)
}

fn map_with_workers<T: Sync, R: Send>(
    items: &[T],
    workers: usize,
    action: impl Fn(&T) -> R + Sync,
) -> Vec<R> {
    map_local_with_workers(items, workers, || (), |_, item| action(item))
}

fn map_local_with_workers<T: Sync, R: Send, S>(
    items: &[T],
    workers: usize,
    make: impl Fn() -> S + Sync,
    action: impl Fn(&mut S, &T) -> R + Sync,
) -> Vec<R> {
    if items.is_empty() {
        return Vec::new();
    }
    let workers = workers.min(items.len());
    if workers <= 1 {
        let mut state = make();
        return items.iter().map(|item| action(&mut state, item)).collect();
    }
    crate::profile::count("parallel.batches", 1);
    crate::profile::count("parallel.tasks", items.len() as u64);
    crate::profile::count("parallel.workers", workers as u64);
    let next = AtomicUsize::new(0);
    let mut results = std::thread::scope(|scope| {
        let handles = (0..workers)
            .map(|_| {
                let action = &action;
                let make = &make;
                let next = &next;
                // Source/type emission can recurse as deeply as the serial
                // pipeline. Rust's smaller default worker stack regresses valid
                // deep member expressions that fit on the main thread.
                std::thread::Builder::new()
                    .name("thor-worker".into())
                    .stack_size(8 * 1024 * 1024)
                    .spawn_scoped(scope, move || {
                        crate::profile::worker(|| {
                            let mut state = make();
                            let mut results = Vec::new();
                            loop {
                                let index = next.fetch_add(1, Ordering::Relaxed);
                                let Some(item) = items.get(index) else { break };
                                results.push((index, action(&mut state, item)));
                            }
                            results
                        })
                    })
                    .expect("failed to spawn independent compiler worker")
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
    fn mutable_worker_state_is_local_initialized_once_and_not_required_to_be_send() {
        for workers in [1, 2, 4] {
            let initialized = AtomicUsize::new(0);
            let inputs = (0..80).collect::<Vec<_>>();
            let results = map_local_with_workers(
                &inputs,
                workers,
                || {
                    initialized.fetch_add(1, Ordering::Relaxed);
                    std::rc::Rc::new(std::cell::Cell::new(0))
                },
                |state, item| {
                    state.set(state.get() + 1);
                    *item * 2
                },
            );
            if initialized.load(Ordering::Relaxed) != workers
                || results != inputs.iter().map(|item| item * 2).collect::<Vec<_>>()
            {
                panic!("worker initialization or result ordering changed");
            }
        }
        let empty = map_local_with_workers::<usize, usize, ()>(
            &[],
            4,
            || panic!("empty batch initialized a worker"),
            |_, item| *item,
        );
        if !empty.is_empty() {
            panic!("empty batch returned values");
        }
    }
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
