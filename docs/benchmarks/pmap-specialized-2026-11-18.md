# pmap callback specialization, 2026-11-18

Known callbacks now reach the worker loop as compile-time procedure constants.
Runtime callback storage keeps procedure-pointer dispatch. Public call syntax,
polymorphic inference, result naming, scheduling, allocator ownership and pure
call eligibility are unchanged. This implements the first recommendation from
[the baseline investigation](pmap-runtime-2026-11-18.md).

## Runtime results

Measurements used the same Apple M1 Pro (10 cores: 8 performance, 2 efficiency),
32 GiB RAM and Odin `dev-2026-10:8169255b9` as the baseline. Original project
source hashes and the controlled simulation kernel match that investigation.
No original project was edited. Desktop scheduling and frequency variation
remain uncontrolled; compare methods within a run rather than interpreting
cross-run differences as gains.

Times are milliseconds per complete mapping call, including output allocation,
worker startup, callback execution and joins. Seven interleaved samples follow
warmup. Input/reference setup, output verification, destruction and printing
are outside the timer. Each deterministic output is checked with explicit
conditional panics, including with assertions disabled.

Unchecked release flags: `-o:speed -disable-assert -no-bounds-check
-no-type-assert`.

| Workload | Specialized pmap | Runtime-callback pmap | Handwritten Odin threads |
| --- | ---: | ---: | ---: |
| 10 balanced Hi-Lo batches | 127.7848 | 130.0778 | 125.3820 |
| 100 balanced Hi-Lo batches | 128.4962 | 129.4913 | 131.1674 |
| 100 Hi-Lo batches, work clustered in first 10 | 1006.7036 | 1006.5340 | 995.3447 |
| 10 cheap jobs, fresh threads | 0.1178 | 0.1225 | 0.1209 |
| 1 million cheap jobs, 10 workers | 0.9084 | 0.9939 | 0.8558 |
| 1 million cheap jobs, 1 worker | 0.8176 | 1.3243 | 0.8101 |

The cheap one-worker case uses about **38% less time** with specialization and is close
to the handwritten Odin loop. The separate eleven-sample control below confirms
roughly **39% less time**, including a native Odin call to the same generic worker engine.
Cheap ten-worker maps improved around **9–12%** locally. Heavy callbacks remain
close: the work inside each call already dwarfs callback dispatch. Small heavy
workload differences overlap sample ranges and do not establish a general gain.

| Cheap jobs, 11 samples | Public specialized pmap | Runtime pmap | Native Odin dynamic engine | Native Odin static engine | Handwritten Odin loop |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10 workers | 0.8632 | 0.9808 | 0.9832 | 0.8910 | 0.8278 |
| 1 worker | 0.8133 | 1.3274 | 1.3224 | 0.8007 | 0.8115 |

Checked release retains assertions and bounds/type checks. Five samples gave
1.0132 ms specialized versus
1.5535 ms dynamic for the one-worker cheap case.
An eleven-sample repeat gave 1.0448 versus
1.5608 ms: about **33–35%** less time.

## Original project snapshots

Both supplied Hi-Lo projects were copied and normalized to **one million games**.
These timings include executable startup and original output; RNG seeds and
win counts vary normally. Builds are excluded.

| Program | Unchecked, 7 samples | Checked, 5 samples |
| --- | ---: | ---: |
| Native Odin pool | 134.80 ms | 130.62 ms |
| Thor pool | 132.38 ms | 130.99 ms |
| Specialized Thor pmap | 129.52 ms | 139.89 ms |

The checked pmap snapshot was slower than the pool copy. An additional
interleaved eleven-sample run compares the **same pmap program**, changing only
the callback from a declaration to runtime procedure storage:

| Checked snapshot control | Median |
| --- | ---: |
| Specialized pmap | 137.49 ms |
| Runtime-callback pmap | 137.81 ms |
| Thor pool | 131.69 ms |
| Native Odin pool | 132.57 ms |

This control shows no substantial specialization slowdown in that program.
Pool/task partitioning, worker lifecycle and random stream initialization still
make the original programs different benchmarks.

## Remaining costs and validation

The worker's compile-time branch removes the per-job indirect call for known
callbacks; the optimized LLVM IR regression verifies both its removal and its
presence for runtime procedure storage. Actual callback inlining is Odin's
optimizer decision. Dispatch helpers are forced inline, not user callbacks, so
source breakpoints remain usable in debug builds.

There is no additional per-job allocation or runtime specialization branch.
Distinct known callbacks can produce more native code. Fresh thread startup
still dominates tiny maps. Fixed chunks still perform badly on deliberately
uneven work: the skewed case remains about 1.007 seconds versus 0.135 seconds
for the dynamic native pool. This change adds neither a global pool nor a new
scheduler; those remain separate possible improvements.

Regression coverage includes compile-time aliases, imports/re-exports, groups,
polymorphic and inline callbacks, mutable/immutable procedure storage, fields,
indices, pointers, factories, reassignment, argument order, defaults, named
multiple/void results, allocator fallbacks, pure ownership checks and `#run`.
Additional gates cover live TLS hover/completion/unsaved recovery, optimized and
checked TLS native tests, and real CodeLLDB callback breakpoints. The parallel
demo runs cleanly in normal, debug tracking and unchecked builds. The Python
reference's unordered collection flags could cause cache misses across process
hash seeds; that ordering is now stable and callback-edit invalidation is tested.

An existing conservative purity rejection for a generic callback returning a
fresh slice initialized by indexed assignments was reproduced on the parent
implementation; see [the implementation notes](../parallel-mapping-2026-11.md#callback-specialization-follow-up).
The equivalent dynamic-array construction using `append` is tested and accepted.

## Reproduction and raw data

```sh
python3 tools/benchmark_pmap.py \
  --hilo "$HOME/Code/Thor/hilo/hilo.thor" \
  --pmap-project "$HOME/Code/Thor/hilo_test/hilo_test.thor" \
  --samples 7 --snapshots --save /tmp/pmap-specialized.json
```

Use `--checked --samples 5 --case hilo-balanced --case cheap-one-worker` for the
checked control, `--samples 11 --native-pmap --case cheap-many --case
cheap-one-worker` for direct native engine comparisons, and `--checked --samples
11 --case cheap-one-worker --snapshots` for the checked snapshot comparison.
`--snapshots` now includes the runtime callback copy as well. Add `--artifacts`
to retain generated sources and binaries outside the original projects.

Raw reports:

- [Unchecked release](pmap-specialized-release-2026-11-18.json)
- [Checked release](pmap-specialized-checked-2026-11-18.json)
- [Native engine control](pmap-specialized-native-2026-11-18.json)
- [Checked snapshot control](pmap-specialized-snapshot-control-2026-11-18.json)
