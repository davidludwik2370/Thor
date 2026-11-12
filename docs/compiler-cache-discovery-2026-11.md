# Earlier evaluation-cache lookup and dependency discovery

This follows the [compiler-efficiency pass](compiler-efficiency-2026-11.md).
It changes the Rust implementation without changing Thor syntax, ownership,
runtime behavior, or the Odin compiler. The Python reference remains available.

## Cached execution before helper construction

The existing result cache could avoid compiling and executing a `#run` helper
while still constructing, analyzing, and lowering that helper to determine its
cache key. Prepared/scalar lookups already bypassed that work for many calls;
generic records and identity-sensitive local types could still take the longer
path on an unchanged project.

A new `request-v1` record points to an existing validated `eval-v1` result. It
identifies the complete current source snapshot, toolchain, resources, target,
semantic flags, overlays, collections, evaluation mode, generic bindings,
previous results, branch state, and lexical frames. The lookup occurs before
creating a helper directory. Output paths and `-keep-executable` do not affect
the computation and are excluded from this request identity.

Both the alias and result retain the existing checksums. Helper-only sources,
package membership, resource contents, missing paths, and symlink bindings are
validated before reuse. Failed, corrupt, unsupported, or disabled cache lookups
fall back to ordinary helper construction. Nested helper views retain their
existing full-code validation because their retention/bridge state requires
additional context. Cache clearing removes these aliases too.

This stores computation results, not generated project output or executables.
An exact request alias deliberately misses after a source-snapshot change; the
existing dependency-granular prepared/scalar caches remain the edit-friendly
path. Some complex edited requests can still require helper lowering to validate
reuse even when their computed value ultimately remains cached.

## Dependencies before permission analysis

The record-shape preflight now also inspects unconditional file-level imports
for pointer-reachable SDK records with container or pointer fields. Context
records often expose such imported types through reference-taking APIs. Their
declaration namespaces join the ordinary dependency frontier before expensive
permission graphs are built.

The preflight shares existing record layouts, has an input-sized work budget and
a per-declaration expansion bound, and does not inspect unrelated SDK procedure
bodies. Scalar-only pointer records do not trigger this namespace expansion.
Unknown or conditional dependencies keep the existing demand-driven fallback.
No partial permission summary is reused across a changed source graph.

Optional `THOR_PROFILE` JSON now includes lazy dependency-discovery events, along
with the existing stage timings and counters. Event payloads are constructed only
when profiling is enabled. Instrumented runs are kept separate from wall-time
benchmarks; nested stage times must not be added together.

## Measurements and validation

The [measurement record](compiler-cache-discovery-results.json) records the
release executables, source hashes, samples, and profiles. Warm timings exclude
first-time compile-time execution, and transpile timings include source-map
emission. Build timings also include the unchanged Odin backend.

Seven warm transpile samples per executable were taken in alternating order on
the same copied project, using four workers and no profiler. The baseline is
`0932d23`; caches are separate for the two executables. The existing
[project benchmark harness](../tools/benchmark_lowering.py) performs the copies,
post-timing type checks and source-hash verification.

| Project | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| General demo | 0.985 s | 0.879 s | 10.8% |
| UI demo | 1.247 s | 0.879 s | 29.5% |
| Othello AI | 1.142 s | 1.093 s | 4.2% |

Separate production-path measurements used five sequential `-o:speed` builds,
rotating Odin/renamed/idiomatic compiler order. These are absolute current times,
not a paired before/after build comparison:

| Project | Odin build | Renamed Thor | Idiomatic Thor | Thor transpile, 4 workers |
| --- | ---: | ---: | ---: | ---: |
| General demo | 2.740 s | 3.315 s | 3.844 s | 0.815 s |
| UI demo | 1.661 s | 1.739 s | 2.535 s | 0.843 s |
| Othello AI | 1.759 s | 1.952 s | 3.136 s | 1.090 s |

Separate instrumented profiles explain the work removed. The general demo goes
from nine helper preparations to zero, with nine exact request hits and no
helper compilations. UI goes from three permission analyses/two dependency
retries to one analysis/no retries; graph snapshots fall from 410 to 187.

Othello AI still discovers `core:unicode/utf8` and then `core:unicode` through
selected SDK call bodies, causing two retries. The general demo has one remaining
late `core:c` dependency. The declaration-only preflight deliberately retains
those fallback paths rather than eagerly traversing SDK procedure bodies. These
are concrete candidates for further bounded dependency discovery or reusable
complete procedure summaries. This pass does not establish a universal
one-second bound, and its exact snapshot aliases do not eliminate helper
lowering for every source edit.

The focused regressions cover generic/local nominal identities, large generic
constants, evaluation modes, branch frames, independent owned results,
dependency edits, resources, unsaved overlays, overload selection, output-path
changes, corrupt packets, cache clearing, and disabled caches. Warm and cold
generated Odin/source maps are compared. SDK tests cover early namespace
discovery and avoidance of unrelated scalar-only SDK imports. Native cases run
with checked/debug flags and unchecked release flags.

Validation passed 257 focused native compiler methods after updating four
prepared-cache tests to isolate their intended layer from the new aliases (the
entire 16-method module passed its rerun), 192 language regression methods, 37
TLS methods, and all 43 live CodeLLDB methods. Rust's 57 unit tests passed in both
debug and release configurations; Clippy with warnings denied and assertion
hygiene passed. These groups overlap and are not a full repository-suite claim.
The isolated corpus passed all 95 valid projects in both renamed and idiomatic
form; all seven expected invalid cases matched in both forms. Original project
files are unchanged.

Parsing and output serialization remain parallel. Semantic graphs and source
lowering remain primarily serial. This pass removes redundant work rather than
introducing another parallel semantic engine or a whole-project cache.
