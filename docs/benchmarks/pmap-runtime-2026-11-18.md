# pmap runtime comparison, 2026-11-18

This records the baseline before callback specialization. See the
[specialization follow-up](pmap-specialized-2026-11-18.md) for the implemented
optimization and new measurements.

For the supplied Hi-Lo projects, `pmap` and the existing thread-pool code have
similar runtime. The important differences are callback inlining, thread reuse,
and load balancing. No compiler or runtime implementation was changed for this
investigation; the original projects were also left unchanged.

Measurements used an Apple M1 Pro with 10 CPU cores (8 performance, 2 efficiency),
32 GiB RAM, Odin `dev-2026-10:8169255b9`, and Thor 0.41.6 at `2472ceb`.
These are local desktop measurements without CPU affinity or frequency control.
Small differences and exact nanosecond costs should not be treated as guarantees.

## Actual project snapshots

`hilo_test` currently requests 100,000,000 trials; `hilo` requests 1,000,000.
All copies below were normalized to **1,000,000 trials**. These measurements
include executable startup and the projects' original output. Builds are excluded.
The native Odin copy mechanically changes `fn` to `proc`, `:>` to `:=`, and joins
the multiline ternary, preserving the simulation and pool implementation.

| Version | Unchecked release, median of 7 | Checked release, median of 5 |
| --- | ---: | ---: |
| Native Odin copy of `hilo`, using `thread.Pool` | 142.20 ms | 151.99 ms |
| Thor `hilo`, using `thread.Pool` | 144.77 ms | 160.11 ms |
| Thor `hilo_test`, using `pmap` | 147.62 ms | 157.57 ms |

Relative to the existing Thor pool, `pmap` was 1.97% slower unchecked and 1.59%
faster checked. Relative to the native Odin copy it was about 3.7–3.8% slower.
Individual sample ranges overlap substantially. There is no large `pmap`
slowdown in this workload, but these results do not establish zero overhead.
The snapshots use the original random seeding, so their win counts naturally vary.

## Controlled workloads

The shared simulation kernel is identical ordinary Odin in both executables.
Each job resets its RNG to a distinct deterministic seed, so every result is
compared exactly against an independently evaluated serial result. Verification
uses explicit conditional panics and remains active with assertions disabled.

The timer includes result allocation, scheduling, callback execution, and joining.
Input setup, reference calculation, result verification, result destruction, and
printing are outside the timer. A reused pool's initialization and destruction
are also outside its per-call timer. Samples are interleaved in a shuffled order
after warmup, with only one benchmark process running at a time.

The controlled pool uses nine worker threads plus the caller, matching `pmap`'s
ten execution slots. The actual snapshots above preserve their original pool
size of ten worker threads; their ten jobs still limit useful parallelism to ten.

Unchecked flags: `-o:speed -disable-assert -no-bounds-check -no-type-assert`.
All numbers below are medians of seven samples, in milliseconds per mapping call.

| Workload | pmap | Fresh native threads | Fresh chunked pool | Reused chunked pool | Dynamic pool, one task per job |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10 batches, 100,000 games each | 142.048 | 135.475 | 138.838 | 135.839 | — |
| 100 batches, 10,000 games each | 138.691 | 139.855 | 139.589 | 137.027 | 130.522 |
| 100 jobs, expensive work clustered in the first 10 | 1,018.011 | 1,010.730 | 1,010.552 | 1,008.335 | 136.625 |
| 10 trivial arithmetic jobs, repeated 100 times | 0.1184 | 0.1204 | 0.1785 | 0.0126 | — |
| 1 million trivial arithmetic jobs, 10 workers | 1.0007 | 0.9361 | 1.0871 | 0.7991 | — |
| 1 million trivial arithmetic jobs, 1 worker | 1.6190 | 0.8374 | 0.8904 | 0.8921 | — |

The one-worker pool paths intentionally execute the direct serial loop rather
than create a redundant background thread. Trivial-job measurements repeat ten
calls per sample, except the ten-job startup case, which repeats 100 calls.
Inputs and output types match across methods; every returned element is checked.

### Callback dispatch

The current native worker invokes a procedure pointer for each element:
`work.calculate(work.jobs[i])`. ARM64 disassembly contains `blr x9` in the loop.
A handwritten worker naming the callback directly inlines the multiply/xor
calculation and hoists its constants out of the loop.

This is a real cost for extremely cheap callbacks. It is effectively amortized
when a callback runs 100,000 simulations, as in the balanced Hi-Lo case.
There is no per-element allocation, reflection, dynamic argument packing, or
runtime branch to select the newly added whole-element input rule.

A further eleven-sample control calls the native engine directly from **both**
Odin and Thor. The public wrapper and direct native calls can therefore share
the same worker specialization within the Thor executable:

| Workload | Public Thor pmap | Native engine called from Thor | Native engine called from Odin | Handwritten native Odin loop |
| --- | ---: | ---: | ---: | ---: |
| 1 million cheap jobs, 10 workers | 1.0403 ms | 1.0232 ms | 1.0277 ms | 1.0186 ms |
| 1 million cheap jobs, 1 worker | 1.5645 ms | 1.5821 ms | 1.6106 ms | 1.0364 ms |

The callback and worker-loop instruction sequences match in the native and Thor
generic cases. Binary layout and scheduling affect microbenchmarks this small;
the robust finding is the loss of inlining relative to the handwritten loop,
rather than a Thor-only operation inserted per element.

The manual Thor control is retained in the JSON as well. Its indexed loop may
also pay for Thor's negative-index support when a start index loaded from a work
record cannot be proven nonnegative. That is separate from `pmap`, whose loop
is native Odin. This investigation does not change indexing semantics.

### Thread lifecycle

`pmap` creates and joins fresh threads for each top-level call. Native direct
threads have essentially the same startup cost in the ten-job case. Reusing a
pool is about nine times faster there, because it avoids thread creation and
destruction. A single substantial Hi-Lo mapping amortizes that cost; repeatedly
calling `pmap` for tiny jobs does not.

### Uneven jobs

`pmap` currently divides input into contiguous, fixed chunks. If all expensive
jobs land in one chunk, one worker does nearly all the work. The same fixed
partition in handwritten Odin has the same problem. In the deliberately skewed
case, a dynamic pool was about 7.5 times faster. Dynamic scheduling also helped
the many-batch case slightly on this CPU with different core speeds.

## Recommended optimization order

1. **Specialize statically known callbacks.** Let the native worker name the
   resolved callback as a compile-time constant, allowing inlining and constant
   hoisting. Preserve the existing procedure-pointer path for runtime callback
   values and preserve polymorphic inference, ownership checks, and `#run`.
2. **Consider explicit pool reuse for repeated calls.** This needs a visible
   lifecycle/ownership contract; do not silently introduce a global pool to
   improve a benchmark.
3. **Consider optional dynamic chunk assignment for uneven work.** Use chunks
   rather than a synchronized task for every tiny element. Keep the cheap static
   partition available for uniform work.

These are proposals, not implementation changes made by the benchmark.
Allocation-heavy callbacks, large result records, nested maps, and the scratch
allocation paths used by pure callers need their own measurements before
generalizing these results to those cases.

## Reproduction and evidence

From the compiler checkout:

```sh
python3 tools/benchmark_pmap.py \
  --hilo "$HOME/Code/Thor/hilo/hilo.thor" \
  --pmap-project "$HOME/Code/Thor/hilo_test/hilo_test.thor" \
  --samples 7 --snapshots \
  --save /tmp/pmap-release.json
```

Add `--artifacts /tmp/pmap-artifacts` to retain generated sources and executables.
`--workers` defaults to the host CPU count. Temporary and generated files are
kept outside the original projects.

For checked-release confirmation, add `--checked --samples 5 --case hilo-balanced
--case cheap-one-worker --snapshots`. For the direct native-engine control, use
`--samples 11 --case cheap-many --case cheap-one-worker --native-pmap`.

Evidence files contain all samples, exact flags, source hashes, compiler
versions, output checksums, and snapshot win counts:

- [Unchecked release](pmap-runtime-2026-11-18.json)
- [Checked release](pmap-runtime-checked-2026-11-18.json)
- [Direct native-engine control](pmap-native-control-2026-11-18.json)

Validation also includes a checked two-worker calibration through both native
engine entry points, Python syntax checking of the driver, and verification
that the original source hashes still match. All seven assertion-hygiene tests
pass. No benchmark verdict depends on an assertion being enabled.
