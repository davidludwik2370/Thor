# Local compiler optimization and validation

This follow-up starts at checkpoint `3ad3dd1`. The requested order is Linux
baselines, warm lowering, vet analysis, cold compile-time work, full Linux
regressions and editor coverage, upstream integration comparisons, then final
regressions and documentation. Windows validation remains deferred.

## Baseline environment

The Mac has 32 GiB of memory. The local Ubuntu 24.04 ARM64 container has three
CPUs and initially had a 4 GiB memory limit. Editor validation uses a 6 GiB
container limit within the approximately 7.65 GiB VM. Functional regressions
use up to five CPUs with three concurrent test modules. Timing comparisons
retain the original three-CPU allocation. Native compiler jobs run with one worker during
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

A native Mac ASan compilation of `runtime/format` also hit the previously
recorded Odin type-name-cache race signature. The OS crash report names the Odin
compiler's `alloc_cstring` and `lb_type` worker stack. This command invokes Odin
directly. An unchanged immediate repetition passed, followed by three passing
single-worker ASan controls, each running all ten native format tests. The fixture
now passes `-thread-count:1`, matching the native integration controls. The
recurrence is recorded in `upstream/odin-codegen-race/evidence/recurrence-2026-11-15.txt`.
A direct cache-hit regression invocation now also uses the shared functional
tracking-flag adaptation on Linux.

The Mac full Python regression suite completed successfully across all 214
modules. The changed fixture modules were rerun after their corrections. The
snapshot records 3,650 tests before the additional integration-runner policy
check. Both hosts also passed all 88 Rust unit tests. The native TLS suite passed
all 1,151 tests again with the matching Linux SDK.

## Upstream matrix follow-up

The Mac verifies its existing migrated and vet-applied projects while the Linux
functional suite finishes. Each host advances to its upstream comparisons after
its own regression pass. The 22-repository manifest contains 175 command recipes.
Builds across all three stages retain their original strict warning flags.
The intervening Thor style pass now permits warnings while applying supported
edits. Previously, 17 example vet recipes returned failure because inherited
`-warnings-as-errors` promoted newly reported unused parameters. The corrected
recipes keep their target, defines and syntax checks, then the full original
build command validates the applied result. A regression checks this separation.

Linux preparation retains the pinned native compiler revisions, rebuilds
Speedrun's C dependencies as native ARM64 ELF objects, and installs TOML
conformance tooling and ARM64 Chromium/Firefox binaries. The platform-adjusted
command manifest and complete invocations are retained with the local results.

Both full functional suites completed all 214 modules successfully. The current
snapshot records 3,651 tests per host, with four platform/configuration skips on
Mac and 67 on Linux. Linux skips cover the Mac allocation tracker and signing/app
integration, Windows-specific cases, and host-selected optional configurations.
The completed suites include the corrected direct invocation helpers and the
explicit frontend-cancellation wrapper configuration. One older loop fixture
transferred an anonymous f-string to a deleting callee. It now uses `f!` for that
transfer and also checks the borrowed `f` form in checked and unchecked loops.
The assertion-hygiene checks passed with the corrected fixture.

The Mac matrix follow-up matched its native test outcomes and retained successful
strict builds for the usable example configurations. The style-warning policy
correction cleared all 17 previously failing example vet passes. Three cold
builds hit the 6 GiB command limit during slice-permission analysis, including
OLS tests, Speedrun and the SDK normal suite. The saved pre-optimization compiler
also exceeds a 5 GiB control limit on the same OLS input. Sampled stacks show
recursive procedure summaries and abstract result-graph instantiation. An
isolated diagnostic build is testing arena lifetime changes before any production
change is applied. The ordinary compiler and regression-suite binaries remain
unchanged during that experiment.

The Linux matrix exposed an independent scalar false positive in ode_ecs after
vet converted a generic multi-result declaration to `:>`. The permission summary
still held an unknown placeholder for a distinct-integer ID, while declaration
typing already resolved its scalar type. Both frontends now refine an unknown
declaration value from that resolved type before freezing its permissions. A
forced-placeholder reference test covers scalar and slice results, and the
normal entry-point regression covers generic multi-result IDs in checked and
unchecked builds. All 64 targeted Mac checks passed. The affected 283-test
ode_ecs suite also passed with the converted declaration retained.

Integration baseline selection now ignores the runner's test-presentation,
scheduling, report-path and random-seed defines while retaining project defines
and target selection. This lets an unavailable native Anvil Flux baseline also
skip its style pass. A separate runner regression preserves previous runtime
failure logs when a fixture is marked unavailable, without carrying its obsolete
comparison verdict forward.

The native Linux karl2d coordinate tests are intermittent under Xvfb. Nine of
twenty unchanged native binary runs failed during graphics initialization, with
the subsequent tests observing its unfinished initialization state. Converted
binaries also passed unchanged control runs. The matrix retains the evidence and
marks those runtime fixtures unavailable. Its native, migrated and vetted
compilation checks and API verifier remain enabled. TOML conformance matched
all 583 passing cases and 98 upstream skips across all three stages on both hosts.

The cold permission-analysis memory failure was resolved by separating parameter
storage locations from borrowed-access qualifiers. Calls whose arguments carry
no borrowed data reuse a writable-input summary. Output locations, callee-created
views, returned aliases and the full returned-object graph remain tracked.
Pure-call and editor contract checks retain their borrowed-input analysis.
The final change removes the earlier experimental graph pruning and collection
approaches. An isolated run completed all 1,078 OLS tests with a measured compiler
peak of 593 MiB, after the original input exceeded the 5 GiB limit. The production
compiler also passed the OLS, Speedrun and 1,407-test SDK normal comparisons.

All 159 focused production checks passed, including assertion hygiene. The 89
Rust unit tests passed. New regressions cover writable parameter locations,
callee-created returned views and recursive builders with shared returned
storage. The builder's abstract summaries dropped from 38 to 20 and its value
count from 6,038 to 2,788 in the controlled fixture. The tests check bounded work,
runtime aliases and rejected writes in checked and unchecked configurations.

Linux SDK preparation additionally required native kb_text_shape and OpenEXRCore
3.5.0 libraries, libcurl development files and the SDK image assets under the
selected Odin root. The SDK migration itself peaked at 10,468.8 MiB on Mac. Its
verified portable output was transferred to the 7.65 GiB Linux VM, retaining
the failed native-Linux migration attempts in the report. The native Linux
migrated builds passed 359 core-speed, 254 internal and 43 vendor tests. A normal
native Odin build exceeded the VM command budget with LLVM 18. A matching-major
LLVM 22 backend is being checked before closing that configuration.

A native Kvist control reproduced the full-suite crash under GDB. The stack
contains repeated `macro_eval_expr`/`macro_eval_sequence` frames in
`macroexpand_deep_recursion_reports_a_bounded_error`. Its other native entry
points and their converted comparisons passed. The complete native control
stack is retained with the local validation evidence.

## Output-parameter hardening

The final review found a preexisting gap in void calls that construct a local
read-only slice and store a containing record through an output parameter.
The compiler now propagates those stores through forwarding procedures.
Call summaries share one graph-instantiation memo across output stores and
returned values. Return snapshots preserve referenced storage already exposed
through output parameters while detaching independent local return facts.
This also covers an allocation shared by multiple output parameters and a
return value. Both frontends have the corresponding correctness changes.

Additional output inspection uses cached reachable-body facts. Scalar writes
skip slice-permission store propagation, and unchanged call-store merges reuse
existing facts. Dependency discovery stops an incomplete view-source query
before the normal dependency retry. Existing returned-object graph checks
continue to inspect their complete graph.

Automatic immutable corrections now use the same conservative ownership policy
for reference-capable output parameters as for reference-bearing results.
Reference-sensitive locals in these procedures require manual review. Scalar
output parameters support ordinary immutable advice. This is a vet policy
choice. Explicit source declarations retain the language's normal checks.
The Speedrun matrix exposed a previously accepted conversion of a decoded image
slice whose view could escape through its output object.

Regressions cover direct and forwarded stores, declaration order, maps, arrays,
dynamic arrays, cycles, imported producers, shared output/result allocations,
independent owned replacements, bounded abstract work, and unsaved TLS recovery
with original Unicode source positions. Runtime checks include checked and
unchecked builds. The focused Mac run passed 178 tests, the Python-reference
copy/analysis run passed 43, and the vet/runner run passed 49.

The integration runner also excludes `.thor-rust-build` when creating a vetted
source copy. A regression verifies the source copy excludes managed outputs
from both compiler implementations.


## Union projections and permission-analysis bounds

Output-store propagation exposed a previously accepted unsafe vet conversion in
Skald's atlas-page constructor. Its read-only page slice escaped through renderer
state to an indirect drawing callback. The older vetted source exceeded the
6 GiB command budget before reporting that escape. The current diagnostic probe
reached the original Thor locations in 35.717 seconds with a measured process-tree
peak of 1,560.3 MiB. Fresh derived vet copies use the corrected ownership policy.
Original upstream and migrated source trees retain their source contents.

Symbolic argument reconstruction now preserves type-switch variant projections,
including their read-only storage qualifiers. Narrowing removes fields belonging
to other union variants. Both compiler implementations cover direct and forwarded
union output stores. Live TLS verifies caller-side diagnostics, Unicode positions
and unsaved recovery.

During one graph reconstruction, argument facts are read-only. The Rust analyzer
reuses origin resolutions and shared path prefixes within that phase, then drops
those caches before applying output stores. Broad reference origins subsume their
descendant data origins. Compaction retains storage requirements, concrete
read-only seeds and unrelated parameters. It preserves the full reference graph.
Unit tests cover bounded projection work, intervening writes, shared slice
permissions and the independence of storage requirements.

The Mac focused selection passed 74 tests, with an additional checked/unchecked
union-output runtime regression and assertion-hygiene rerun. The Python-reference
copy/analysis selection passed 44 tests and the same additional runtime regression. All 92 Rust unit tests passed. Runtime permission
fixtures retain checked and unchecked release coverage.

The real Mac and Linux VS Code debugger checks passed launch, breakpoints,
stepping, watches, restart and clean exit. The harness reads generated source
locations from the debug manifest. Its previous fixed generated-source path
was obsolete. The standalone terminal checks also passed on Mac.

The serial clean Linux Kvist rerun passed all 1,004 full-suite cases and its
22, 17, 2 and 2 entry-point suites, together with the CLI build. Native Linux
SDK baselines and migrated LLVM 22 builds passed 1,410 core-normal, 359 core-speed,
254 internal and 43 vendor tests. The native SDK source revision remains pinned
at 537fac56cfee40cd51967d509ef089aa0a71444b. Final vetted SDK validation is active.
