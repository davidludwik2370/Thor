# Local compiler optimization and validation

This follow-up starts at checkpoint `3ad3dd1`. The requested order is Linux
baselines, warm lowering, vet analysis, cold compile-time work, full Linux
regressions and editor coverage, upstream integration comparisons, then final
regressions and documentation. Windows validation remains deferred.

## Baseline environment

The Mac has 32 GiB of memory. The local Ubuntu 24.04 ARM64 container has three
CPUs and initially had a 4 GiB memory limit. Editor validation uses a 6 GiB
container limit within the approximately 7.65 GiB VM. Native compiler jobs run with one worker during
performance comparisons. The Linux environment includes LLVM 18, Rust 1.100,
Node 22.14.0, CodeLLDB 1.12.3, SDL 3.2.10 and rebuilt native stb libraries.

The larger Linux comparison uses native Odin revision
`537fac56cfee40cd51967d509ef089aa0a71444b`, also present in the pinned upstream
matrix. The Mac Skald comparison uses its installed October compiler
`8169255b97b85d6a08691d99764f51fb01f92b5a`.
The newer `9a69916` distribution fails its own JSON-reporting test build at
`core/testing/runner.odin:1050`. Its `json.marshal_to_writer` invocation passes
`^Marshal_Options`, while the installed JSON implementation accepts options by
value. The pinned compiler provides the baseline without an SDK source patch.

Small and medium fixtures use the prebuilt `9a69916` distribution. Both main
and test routines use explicit failure branches, keeping verdicts active in
unchecked builds. The medium fixture has 600 procedures. The compile-time
fixture has 32 independent pure calls and a native constant-valued equivalent.

| Linux workload | Recorded elapsed time |
| --- | --- |
| Small warm transpile | 0.035 s |
| 600-procedure warm transpile | 0.122 s |
| 32-call cold transpile | 17.572 s |
| 32-call warm transpile | 0.226 s |
| 32-call first optimized build | 73.506 s |
| Corrected Skald cold optimized test build | 211.355 s |
| Corrected Skald warm optimized test build | 35.531, 35.419 s |

The small-fixture timings were collected while native dependency preparation
was still running. Final comparisons require quiet paired repeats. Every
Skald sample matched all 108 native test names and verdicts. Peak single-process
RSS was 1,380 MiB for the cold sample and about 1,196 MiB for warm samples.
These RSS measurements are separate from the combined process-tree limit used
by the integration runner.

The warm Linux profile attributes 17.4 seconds to final native compilation,
8.7 seconds to call planning, and 1.7 seconds to contextual-array preparation.
The helper cache eliminates native helper preparation on these warm samples.

Detailed local logs and profiles are retained under
`integration-projects/results/performance-followup`.

## Warm analysis

Contextual-array call candidates now belong to the immutable syntax index.
Semantic resets still recompute witnesses against current bindings, evaluated
conditions and loaded imports. Extending a source inventory adds candidates for
the new syntax. Each context refresh iterates those candidates directly.

Primitive spelling is checked before lexical lookup. Matching primitive names
still undergo shadowing and collection checks. Logical numeric names avoid
constructing the runtime-library path for unrelated symbols. Ordinary call
arguments retain the already-resolved type during overload scoring. Variadic
packing, type arguments and per-overload bindings retain their separate paths.

Mac warm Skald samples took 14.878 and 15.189 seconds after these changes. Its
call-planning self time was 4.725 seconds, compared with 9.8 seconds in the
previous quiet profile. Context preparation self time fell from about 1.6
seconds to 0.00022 seconds across 474 invocations. Native compilation accounted
for 3.052 seconds in that sample. A final quiet paired comparison is pending.

The Mac and Linux selections each passed 60 regression tests, including checked
and unchecked generic/default/variadic calls, lexical shadowing, conditional
array contexts, read-only alias rejection, cycle diagnostics and assertion
hygiene. Linux also passed all 88 Rust unit tests, including the new syntax-index
extension regression. The same unit suite passed on Mac.

Linux warm optimized Skald test builds subsequently took 29.393 and 27.743
seconds, compared with 35.531 and 35.419 seconds before these changes. All 108
test names and verdicts matched. The cold sample took 198.489 seconds with
1,192 MiB peak single-process RSS.

## Vet graph reuse

Vet retains procedure portability results for its current source snapshot.
Shared helper bodies are traversed once. Rejection propagates through callers
before any successful result is cached, including callers in recursive cycles.
Native validation still checks the complete proposed edits.

The regression adds 48 candidates sharing one helper and a recursive group
that reaches a conditional implementation. It verifies both bounded body scans
and the retained rejection. The 73-test Mac vet selection passed. Its larger
Skald samples took 201.021 seconds initially and 41.528 seconds on repetition.
The latter includes two correction-validation checks. These timings include
the preceding warm-analysis improvements.

The same 73-test selection passed on Linux. Its Skald vet samples took 207.612
and 41.421 seconds, with two validation checks. Both hosts retained the same 13
rejected proposals, including 12 read-only changes and one pure-function change.

## Cold compile-time work

Independent automatic pure calls with compatible scalar results share native
helpers in batches of at most 32. Results retain independent dependency keys.
Changing one callable recomputes its affected values. The path uses the normal
executor resource checks and publishes the batch only after complete validation.
A failed batch returns to individual evaluation to preserve source diagnostics.
Each scalar call owns a separate scratch lifetime, releasing its temporary
allocations before the next result is computed. A native helper instrumentation
test checks this before serialization in checked and unchecked builds.

Finite per-call deadlines, generic roots, explicit `#run`, conditional or loop
execution, local captures and variadic packs retain individual evaluation. The
helper builds remain serial to bound native compiler memory use. Parsing and
file lowering retain their existing bounded parallelism.

The investigation also found a forward-declared constant dependency that failed
in both frontends. Retained global constants now complete their required pure
calls before a dependent helper is built. The Rust syntax index records those
potential requests once, and the scheduler applies current semantic eligibility.

Linux repeated resource edits exposed identical filesystem timestamps across a
same-size rewrite. Content snapshots now verify their SHA-256 contents on reuse.
Directory resources also retain explicit immediate-file membership, including
extensionless files. Toolchain identity hashes content once per session. The
persisted metadata-only identity aliases have been removed. SHA-256 uses the
RustCrypto implementation with its runtime-selected native backend.

The final Mac scalar fixture took 46.543 seconds cold before batching and 1.970
seconds with batching. Warm samples were 0.348 and 0.398 seconds. The Linux
fixture took 66.473 and 2.590 seconds cold, with 0.282 and 0.331 second warm
samples. These measurements include final native compilation and executable
checks. The later scratch-lifetime regression also runs in checked and unchecked modes.

Mac and Linux each passed the 87-test targeted selection and all 88 Rust unit
tests. The additional native scratch-lifetime check passed on both hosts in
checked and unchecked modes. It confirms that existing pure-call cleanup also
covers batched scalar results.

The larger Mac optimized single-worker samples took 97.301 seconds cold and
22.875/23.351 seconds warm, matching all 108 native outcomes. Their peak
single-process RSS was 1,436–1,445 MiB. Warm lowering itself took 10.664 seconds.
These Mac samples explicitly use one native compiler worker. The earlier Mac
samples used their original native concurrency, so their total build times
require a final paired comparison with matching flags.

The larger Linux samples took 138.048 seconds cold and 36.281/29.116 seconds
warm, with all 108 outcomes matching. The first warm sample overlapped release
linking on the host. The quieter second sample retains the prior warm-analysis
improvement while adding content-based resource validation. Its peak
single-process RSS was about 1,196 MiB.

## Full platform regression pass

The functional regression suite now adapts its explicitly requested automatic
allocation-tracking flags on platforms where that tracker is unavailable.
Linux executes those fixtures with tracking disabled. Direct policy tests still
verify the platform rejection, and Mac fixtures retain their leak and call-stack
checks. This adjustment applies to test invocation helpers.

The Linux native checkout initially contained an older editor harness. That
harness opened the full compiler checkout and its upstream clones during rename.
The current tracked harness confines indexing to the demo fixture. Synchronizing
the tracked snapshot restored the intended editor scope. The full Linux VS Code
suite passed all 71 checks with TLS peaking at approximately 103 MiB. The separate
standalone-file run passed six checks. Earlier runs of the obsolete harness
reached approximately 4.8 GiB in TLS and triggered two container OOM kills. Those
runs are retained as failed setup evidence.

The native TLS suite passed 1,151 tests, and the editor's Node unit suite passed
142 tests. The Linux debugger suite passed all 45 tests. Two stepping fixtures
now remove their already-verified landing-line breakpoints before continuing to
program exit. On this ARM64 LLVM 18/CodeLLDB combination, stepping onto such a
breakpoint can trigger the same breakpoint again on continue. An independent
native Odin program through raw CodeLLDB reproduced that behavior with Thor's
source mapping bypassed. The fixtures continue to verify their original Thor
breakpoint positions, step lines and watched values.

Functional regression comparisons use the same Odin source revision as the
installed Mac compiler, `8169255b97b85d6a08691d99764f51fb01f92b5a`. The Linux
compiler was built from a pristine archive of that revision using LLVM 18.
The earlier pinned integration compiler rejects one later generic-default
syntax fixture and uses an older Unicode quoted-string representation. These
native version differences disappear with the matching source revision.
The upstream matrix retains each project's own pinned native compiler.

The low-level evaluation-input tree snapshots also record file contents and
immediate directory membership. Regression cases preserve identical metadata
while changing an existing file or adding a child package. Both frontends reject
those stale snapshots. Malformed optional snapshots consistently become cache
misses. This closes the corresponding low-level tree path after the content-based
cache hardening in the previous step.

The independent upstream JSON-reporting SDK failure has a standalone reproducer
and native control logs under `upstream/odin-testing-json-report`.
The complete Mac and Linux functional suites remain in progress at this point.
