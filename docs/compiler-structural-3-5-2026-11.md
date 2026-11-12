# Structural compiler optimization, steps 3–5

Baseline: `482a79c`. This pass covers shared type identities, completed procedure
summaries, and independent semantic/lowering work. The Python reference and Odin
compiler remain unchanged.

## Step 3: shared type identities

Resolver layout/member/path caches now use compact arena-local type IDs.
Permission graph values and convergence snapshots hold canonical immutable type
handles rather than cloning recursive type trees. Owned `Type` values remain at
the compiler's existing public boundaries. IDs do not enter emitted source,
source maps, serialized evaluation values, or persistent cache keys.

Type identity remains nominal. Handles from unrelated arenas are distinct;
metadata invalidation still clears context-dependent answers when bindings,
evaluated values, or contextual typing change. The type arena itself stores
immutable structural descriptions, not those answers.

Initial gates: 58 Rust units and 42 focused semantic/ownership integration
methods passed. Seven alternating warm transpile samples per executable gave
UI 0.844→0.842 s and Othello AI 0.962→0.960 s. These approximately 0.3% differences
are effectively flat, not evidence of a large speedup. The shared representation
is also used as the basis for summary snapshots and lowering workers below.

## Step 4: completed procedure summaries

Dependency retries can retain closed, completed abstract procedure graphs, with
their aliases, effects, and ordered diagnostics. Reuse requires identical existing
syntax, bindings, flags, context witnesses, evaluated values, and numeric-type
activation. Appending files to an existing namespace invalidates it. Helpers,
recursion, unresolved branches, ambient/global runtime state, and projected local
symbols use ordinary analysis. Snapshots have a source-size-dependent budget and
are only harvested when discovery stalls. This is command-local reuse, not a new
persistent project cache.

Seven new integration methods exercise checked and unchecked builds, aliases,
mutation, diagnostics, generic array copies versus slice views, named results,
recursive graphs, global writes, and recursion. The focused 51-method gate and
59 Rust units passed. Seven alternating warm samples versus `482a79c` were flat:
UI 0.867→0.870 s, Othello AI 1.023→1.023 s. These workloads provide controls rather
than evidence of a speedup; the synthetic dependency test verifies that completed
summaries actually reuse work.

## Step 5: independent lowering

Per-file semantic queries and source emission now run on bounded workers after
project-wide alias, permission, purity, and dependency analysis completes. A frozen
resolver snapshot shares symbols, scopes, prepared types, record metadata, and
canonical type storage. Each worker constructs one local query delta and retains
it for that worker's files. Binding/evaluation/context changes drop that worker's
frozen query view. New worker-local type IDs never merge across workers.

Planning and result collection preserve source order. Workers return owned emitted
text, maps, and diagnostic metadata; publication happens only after the whole
prefix succeeds. Cancellation is checked around each file. One-file jobs,
`THOR_JOBS=1`, and incomplete semantic snapshots use the direct serial path.
Compile-time helper scheduling keeps its existing dependency order.

Tests compare serial, two-worker, and four-worker output byte for byte, including
Odin, manifests, and maps. They also execute checked and unchecked builds and
compare errors at original Thor locations. Cross-file cases combine big-number
constants, generic inline versus reference values, contextual literals, automatic
using, comprehensions, and `#run`.

The default is still at most four workers; positive `THOR_JOBS` overrides are
capped at 32. This does not parallelize the shared global effect graph or change
language semantics, runtime code generation, the Python reference, or Odin.

Stress testing found that a 1,000-level record-member expression could transpile
serially but overflow Rust's default worker stack. Workers now reserve an 8 MiB
stack, matching the serial pipeline's stack budget on the validation host. A new
regression compares its serial and parallel source/maps; the extra reservation
does not require allocating 8 MiB of physical memory for every ordinary task.

## Validation and measurements

All 391 distinct focused compiler methods passed (390 in the broad groups, then
15 follow-up methods including the new deep-stack case). TLS passed 37 methods,
live CodeLLDB passed all 43, Rust passed 63 units in both debug and release,
Clippy passed with warnings denied, and assertion hygiene passed all seven.
The debugger's initial sandboxed run could not bind its localhost listener; the
full suite passed when rerun with debugger permissions.

The isolated corpus retained 95 valid and seven expected-invalid projects in each
of the renamed and idiomatic variants: 204 builds, zero status changes. Original
files were unchanged. Four copied projects' own test suites passed in all three
variants (Odin, renamed Thor, idiomatic Thor), for 12 successful runs.

Measurements below use release executables, four workers, seven alternating warm
samples, copied input projects, and independent result caches. The final Odin
backend is excluded. Compile-time helpers remain included on result-cache misses;
for example the demo's empty-project-cache run took 5.184→4.803 s, while warm
lowering took the values below. Shared SDK/parser caches and OS pages are retained.
No whole-project output cache was added.

| Workload | Before | After |
| --- | ---: | ---: |
| General demo | 0.850 s | 0.859 s |
| Hilo | 0.319 s | 0.317 s |
| Poker | 0.397 s | 0.397 s |
| UI demo | 0.862 s | 0.863 s |
| Circles | 0.253 s | 0.258 s |
| Othello AI | 0.992 s | 0.995 s |
| 200 scalar procedures, one file | 0.068 s | 0.070 s |
| 200 SDK-calling procedures, one file | 0.157 s | 0.158 s |
| 192 procedures across 26 files | 0.289 s | 0.254 s |

The multi-file workload improves about 12%. A separate current-compiler control
measured 0.348 s with one worker versus 0.246 s with four (about 29% faster); that
control includes existing parallel parsing/serialization as well as new lowering.
Separate profiles confirm that all 26 source emissions use four lowering workers.
Its native unchecked execution verifies the expected result with `if/panic`.

The existing applications emit one source file each, so their emission remains
serial. Their small timing differences do not demonstrate a meaningful speedup;
all measurements, including slower samples, are retained. Type identities and
summary retention provide shared foundations and bounded retry reuse, not a
blanket single-file speed claim. A synthetic namespace-discovery probe records
30 completed summaries reused, with 90 graph values snapshotted and two ordinary
permission analyses still performed.

Global semantic analysis remains the main limit on the larger single-file cases.
More workers cannot remove that dependency-bound work, and this pass does not
establish a universal sub-second upper bound. The detailed source hashes, raw
samples, profiles, controls, and validation are in the
[structured evidence](compiler-structural-3-5-results.json).
