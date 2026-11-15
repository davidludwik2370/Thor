# Local compiler optimization and validation

This follow-up starts at checkpoint `3ad3dd1`. The requested order is Linux
baselines, warm lowering, vet analysis, cold compile-time work, full Linux
regressions and editor coverage, upstream integration comparisons, then final
regressions and documentation. Windows validation remains deferred.

## Baseline environment

The Mac has 32 GiB of memory. The local Ubuntu 24.04 ARM64 container has three
CPUs and a 4 GiB memory limit. Native compiler jobs run with one worker during
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
