# Compiler efficiency follow-up, November 2026

This follows the isolated Odin/Thor corpus audit at `4b71dbf`. It changes the
Rust implementation, without adding language features or changing Odin. The
Python reference and compile-time-result cache remain available. This pass does
not add a whole-project build cache.

## Changes in order

1. **Discover referenced record shapes before permission graphs.** Project
   expression types seed a declaration-only walk through record fields,
   container elements and procedure signatures. This brings transitive SDK data
   dependencies into the ordinary discovery loop before constructing expensive
   alias/effect graphs. Unrelated SDK procedure bodies are not visited by this
   preflight. It has both an input-sized work budget and a per-declaration
   expansion limit for types such as `Node(T).next: ^Node([]T)`. Unknown and
   expanding generic types yield to the existing demand-driven fallback; the
   preflight never rejects a program itself.

2. **Reuse metadata throughout a resolver's lifetime.** Specialized types,
   record fields, member types and promoted-field paths are shared by permission
   checks, purity checks and emission. Nested fixed-input analyses also share
   symbol queries, including attempts with pending imports: an outstanding
   import does not mutate the current resolver, and its dependency request
   survives cache hits. A new source graph gets a fresh query table.
   Binding/evaluation changes and contextual-literal preparation
   invalidate these answers. No metadata is retained across commands or reused
   across different source snapshots. Incomplete dependency queries and recursive
   inference do not publish speculative metadata answers.

3. **Reduce allocation in analysis.** Nested lookup tables accept borrowed type,
   name and substitution keys, avoiding copies just to find an existing answer.
   Record layouts are immutable shared slices; ownership graphs still own their
   values and paths. Layout readers no longer clone every field name and type
   merely to inspect a record. The public owned-layout API still returns an
   independent copy, and semantic invalidation releases the cached layouts.
   Permission convergence saves one canonical graph snapshot and compares the
   current graph directly against it, rather than constructing a second owned
   graph. Equality remains exact, including cycles, sharing, storage paths and
   effects. It does not rely on hash equality or an approximate change counter.

4. **Emit fewer native checks and runtime setup.** Concrete sequence helpers
   choose the appropriate storage/normalization branch immediately; unresolved
   generic and enum-indexed shapes retain native conditional checks. Pure leaf
   functions proven to use only inline values and no calls or allocating Thor
   expressions omit scratch-allocator setup. Unknown types, calls, owned results
   and allocated numeric intermediates keep normal scratch cleanup. A scalar
   result alone is insufficient to prove that its computation does not allocate.
   Debug builds retain the existing prologue: the live CodeLLDB checks caught
   unmaterialized parameter slots at a first-statement breakpoint without it.

5. **Parse sibling imported packages concurrently.** Independent files from an
   import frontier join one bounded worker batch. Parsed results and errors are
   consumed in the original depth-first order, preserving source ordering,
   compile-time scheduling and diagnostic selection. Canonical paths deduplicate
   shared dependencies. Ordinary one-file batches still run directly. The
   existing default is at most four workers, configurable through `THOR_JOBS`.

## Related correction found during validation

A new unsaved SDK file could reach Thor's resolver while remaining invisible to
Odin unless checked pure-call adaptation also activated the SDK view. SDK
overlays now activate that view for ordinary calls too. Installed SDK files are
not changed, and builds without overlays keep the original fast path.

## Parallelism boundaries

The boundaries below describe this earlier pass. The subsequent
[structural steps 3–5](compiler-structural-3-5-2026-11.md) add shared type IDs,
guarded completed summaries, and per-file lowering over a frozen semantic view.

Parsing and output serialization use bounded workers. Purity/slice graphs and
actual source lowering remain primarily serial and share resolved metadata.
This pass avoids duplicating their work in independent resolvers merely to
occupy more cores. Further semantic parallelism would require immutable,
complete procedure summaries and measurements showing sufficient independent
work. Compile-time evaluation retains its dependency and reference ordering.

Global type interning, incremental retention of partial effect summaries across
new dependencies, and full transitive allocation-effect proofs are not part of
this pass. They remain possible follow-ups, rather than assumptions about
already eliminated work.

## Regression coverage

`tests/test_rust_semantic_efficiency.py` covers dependency chains, bounded SDK
inspection, concrete helper simplification, allocation-free leaf functions,
allocated intermediates, sibling imports, deterministic maps and depth-first
parse errors. Rust unit checks compare the new graph equality operation with
full snapshot equality through cycles, aliases and successive graph mutations,
and check metadata invalidation. Existing suites cover generic specialization,
compile-time caches, readonly copies, mapped diagnostics, editor overlays,
allocation tracking and checked/unchecked native execution.

## Odin reference backend recurrence

One follow-up comparison encountered an Odin SIGSEGV while compiling the frozen
Python reference's `#run` helper. Rust succeeded and produced the expected
`3 3`; the case passed in the broad gate and in six subsequent fresh comparisons
on both engines, each checking ordinary and unchecked flags. The crash has the
same first stack offsets and null-source/nonzero-length string as the existing
Odin investigation. The extracted [November 11 record](../upstream/odin-codegen-race/evidence/recurrence-2026-11-11.txt)
is retained without claiming that this particular occurrence proves the race's
cause. No Odin patches or automatic retries were introduced.

## Measurements

Measurements use the production release executable on the same macOS arm64
host, with warm parser and compile-time caches. Build results are medians of
five sequential `-o:speed` builds, rotating compiler order. Frontend results are
medians of five warm `transpile` commands; that command also writes source maps.
Profiling runs are separate from those wall-time samples. The
[structured record](compiler-efficiency-results.json) retains executable/source
hashes, individual samples, stage profiles and the paired comparisons.

| Project | Odin build | Renamed Thor | Idiomatic Thor | Thor transpile, 4 workers |
| --- | ---: | ---: | ---: | ---: |

| Odin/demo | 2.778 s | 3.411 s | 4.006 s | 0.926 s |
| Odin/hilo | 1.270 s | 1.291 s | 1.603 s | 0.318 s |
| Odin/poker | 1.405 s | 1.420 s | 1.855 s | 0.395 s |
| Odin/ui_demo | 1.716 s | 1.798 s | 2.995 s | 1.218 s |
| Odin_Projects/Cricles_on_dots | 1.273 s | 1.293 s | 1.518 s | 0.246 s |
| Odin_Projects/Othello_pvai | 1.806 s | 2.007 s | 3.175 s | 1.061 s |

The earlier corpus audit recorded idiomatic build medians of 4.707 s for the
general demo, 3.521 s for UI and 3.538 s for Othello AI. These are historical
cross-session comparisons, rather than alternating old/new executable samples.
The smaller layout/query refinements were also tested in direct paired runs:
ten warm invocations per executable, alternating order, against fresh project
copies. Shared layouts reduced UI transpilation from 1.279 s to 1.222 s and
Othello AI from 1.077 s to 1.060 s. The pending-query refinement had smaller gains
of approximately 1% and 2%, respectively; it also simplifies the cache's
fixed-input invariant.

The separate UI profile shows permission analysis dropping from 1.000 s to
0.414 s and lookup calls from 1,696,026 to 39,407. Those counters explain reduced
work; instrumented times include profiler overhead and are not the production
wall-time comparison. Permission attempts fell from four to three. Some call
effect dependencies still require a discovery restart.

Four-worker versus single-worker frontend timings demonstrate useful scaling:

- Odin/demo: 1.325 s to 0.926 s.
- Odin/hilo: 0.595 s to 0.318 s.
- Odin/poker: 0.687 s to 0.395 s.
- Odin/ui_demo: 1.576 s to 1.218 s.
- Odin_Projects/Cricles_on_dots: 0.403 s to 0.246 s.
- Odin_Projects/Othello_pvai: 1.454 s to 1.061 s.

This measures all existing parallel parsing/serialization work together, rather
than isolating the new import-frontier scheduling. Semantic graph analysis and
source lowering remain primarily serial. The largest map-producing frontend
sample, UI at 1.218 s, remains above one second. Further removal of repeated
effect work would require incremental procedure summaries or changes to the
analysis graph; this pass does not claim to have eliminated all compiler work
or reached a universal one-second bound.

The four seeded CPU workloads were rebuilt and sampled five times per variant,
rotating order and checking their output on every run. Idiomatic runtime medians
were 1.055 s, 1.061 s, 0.220 s and 0.217 s; the matched Odin medians were 1.016 s,
1.062 s, 0.212 s and 0.209 s. Differences remain approximately 0–4.1%; these samples
do not establish zero runtime overhead or benchmark GUI frame rates.

## Validation results

- Broad compiler gate: 1,402 passed, one opt-in Finder test skipped. Two helper
  lifecycle checks required an enabled process-status rerun because the sandbox
  blocks `ps`; both passed there.
- Final layout/API follow-up: 84 passed. The preceding pending-query follow-up
  passed 74 checks; these groups overlap with the broad gate.
- Rust units: 57 passed in both debug and release configurations. Clippy passed
  for all targets with warnings denied; assertion hygiene passed all seven checks.
- TLS: 122 passed. Editor JavaScript: 141 passed. Live CodeLLDB: all 42 passed
  after retaining the debug prologue.
- Corpus: 95 valid cases built in both renamed and idiomatic Thor form. All
  seven expected invalid cases matched in both forms, for 204 build checks.
  Original user-project files are unchanged.
- Checked and unchecked native paths, allocation tracking, generic records,
  readonly copies, compile-time cache reuse, overlays and original-source maps
  are exercised by the integration gates. One reference-only Odin SIGSEGV and
  its six successful retries are described above.

The [validation record](compiler-efficiency-validation.json) records scope and
overlap. Detailed logs and historical comparison executables remain locally in
`thor_test/artifacts/optimization/`. Reproduce the corpus measurements with
`python3 thor_test/measure_optimization.py --repeats 5`; ordinary build output
locations and names are unchanged.


## SDK discovery follow-up on November 16

This pass keeps completed call resolutions across compatible SDK discovery
retries. The retained plans contain stable syntax references and type
substitutions. The existing planner supplies their completeness checks.
Appending files to an existing namespace, adding Thor source, changing syntax,
source views, collection roots, flags or logical-number activation resets this
reuse. Generic bindings, evaluated values and contextual witnesses exclude a
resolver from this transfer. Record shapes and permission effects receive their
usual analysis against the enlarged source graph.

Using projections now restrict scope queries to files with relevant using
owners or package-level projections. Scope vectors are allocated after a
visible owner is found. Conditional overload identity lookup uses the declaring
scope's existing name index. Its search cost is independent of unrelated
symbols in other procedures or packages.

A broader nested-inference memoization experiment increased recursive type
resolution work in the large fixture and was removed. The retained changes use
the original call-plan cache eligibility rules.

Paired warm Skald runs use the same native compiler, optimization flags,
single-worker backend and fixed test seed. The lowering figures include
profiling. Mac figures average two pairs. Linux figures average three pairs.
The ordinary command measurements have profiling disabled and average two
pairs on each host.

| Measurement | Mac before | Mac after | Linux before | Linux after |
| --- | --- | --- | --- | --- |
| Warm lowering | 11.89 s | 10.98 s | 11.26 s | 10.29 s |
| Ordinary optimized test build | 22.47 s | 21.50 s | 26.03 s | 25.36 s |

Each run preserves all 108 test names and verdicts. On both hosts, all 122
emitted Odin files match byte-for-byte between the saved compiler and the
updated compiler. The optimized executable text sections also match exactly,
covering Mach-O `__TEXT,__text` on Mac and ELF `.text` on Linux.

The 600-procedure control keeps optimized Thor builds at 0.441–0.499 seconds
against native Odin's 0.395–0.398 seconds on Mac. Its updated warm transpile
samples take 0.074–0.132 seconds. The 32-call scalar control retains a single
cold helper compilation and warm builds of 0.390–0.398 seconds. These short
wall-time measurements include process startup and monitoring granularity.

Focused checks cover namespace readiness, ordinary and contextual overloads,
parenthesized procedure values, lexical shadowing, checked and unchecked native
runs, using projections and readonly aliases. New regression checks exercise
closed-call transfer invalidation and ensure unrelated source growth adds no
using-scope queries or conditional-identity candidates. All 51 focused tests
and all 96 Rust unit tests pass.

The final full gate passes all 214 Python test modules on both hosts. Each host
completes 3,692 tests, with four existing skips on Mac and 67 platform or
configuration skips on Linux. The 96 Rust units also pass in Mac release mode.
All 142 editor JavaScript tests pass on each host. Checked and unchecked native
fixtures, assertion hygiene, TLS diagnostics, debugger mappings, compile-time
execution and cache invalidation are included in the compiler gate.

Linux timing runs use a three-CPU quota. Functional validation uses up to five
CPUs and a six-GiB container limit. The final run records zero out-of-memory
or out-of-memory-kill events.

The large workload still spends roughly 11 seconds lowering. Call planning and
repeated permission discovery remain significant costs. These changes provide
command-local analysis reuse without additional generated files.

Detailed local evidence is under
`integration-projects/results/performance-followup/remaining-analysis`, including
paired profiles, generated-source hashes, executable-text hashes, the discarded
experiment and the regression logs.
