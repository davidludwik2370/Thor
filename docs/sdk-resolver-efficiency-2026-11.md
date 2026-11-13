# SDK loading and semantic reuse

Baseline: `4979ebe`. This pass changes Rust compiler work, preserving the
Python reference and Odin backend. The caches below contain common SDK syntax
or live command state; they do not cache a project's complete generated output.

## SDK syntax

Shared SDK parser hits can load a compact normalized arena directly. The cache
interns repeated strings and stores typed node links, spans and extension data.
It retains the original frontend packet for explicit protocol output, without
decoding that packet on a normal hit. Source/node order and procedure bodies are
preserved, so existing purity, ownership, helper and diagnostic consumers keep
their ordinary behavior. Lazy removal of bodies is deliberately not part of
this change.

Entries are bound to the parser executable, exact source bytes/overlay, path,
target, test mode, project name, and the source revision of the normalizer and
cache codec. Checksums and bounded structural decoding reject damaged entries;
the raw parser cache or frontend recomputes them. Atomic publication supports
concurrent commands. `--no-cache` bypasses both cache layers. Shared entries
live under the toolchain's `.thor-cache/sdk-parser/rust-syntax-v1` directory.

The first five-sample alternating release comparison gave warm transpile medians:

| Project | Baseline | SDK cache |
| --- | ---: | ---: |
| Poker | 0.395 s | 0.299 s |
| UI demo | 0.834 s | 0.693 s |
| Othello AI | 0.977 s | 0.839 s |

These exclude the final Odin backend. Project caches are isolated per engine,
and common parser caches remain warm. Final combined measurements and validation
are recorded separately after all stages.

## Completed resolver state

Discovery, compile-time scheduling and ordinary lowering can transfer a closed
resolver between phases. Owned snapshots share immutable source arenas and move
their query tables back into the next serial resolver; they contain no borrowed
reference into the project's source vector. Exact syntax identity, source views,
collection bindings and flags must match. Active inference, incomplete imports
and worker-local deltas cannot be published as a completed snapshot.

Changing evaluations or lexical bindings invalidates type/member/name answers
and contextual witnesses. Repeating an unchanged contextual preparation or
logical constant does not discard those answers. Generic evaluator bindings
are reset before the scheduler returns its resolver to the enclosing project.
Helper projects keep their own semantic state even when their syntax is shared.

## Dependency worklists

Within a discovery command, project expression sites, pure roots and `#run`
request sites are collected once per newly loaded source. A positive resolved
type answer can be reused across the addition of separate SDK namespaces only
if it was obtained before any outstanding dependency demand and without
evaluations, generic bindings or contextual witnesses. This conservative rule
also excludes answers obtained from an earlier memoized partial dependency.
Unknown answers stay on the retry path. Record fields, signature dependencies,
purity and slice effects are rechecked against the enlarged source graph.

New files in an existing namespace, reparsed syntax, changed source views or
collection roots, or new Thor files discard the discovery worklist. Existing
source/node order is preserved. This is command-local reuse, not an incremental
project artifact cache.

## Attributing remaining time

Opt-in profiling now separates CLI setup, resolver destruction and final project
destruction. On warm copied projects, final project teardown accounts for about
66 ms (Poker), 92 ms (UI), and 102 ms (Othello AI). Permission graph copying takes
only about 1–2 ms in these samples, so changing its semantics or adding a broad
POD-copy shortcut would have little headroom. Further permission work is guided
by function-level timings rather than assuming record expansion is dominant.

Profiler worker durations are aggregate CPU-side measurements and overlap wall
time. They must not be added to the parent's spans or treated as command latency.

An experiment freeing independent syntax arenas on four workers was slower on
this host. Five alternating warm samples gave 0.280→0.315 s for Poker,
0.632→0.665 s for UI and 0.701→0.753 s for Othello AI. The experiment was removed;
cleanup remains serial. Metadata and syntax destruction are still timed
separately, including early-error exits.

## Complete call plans

The finer profile identified repeated call planning as the useful permission
hotspot. The resolver memoizes a unique plan only with fully resolved argument
and generic binding types, no outstanding imports, and no recursive inference,
planning or condition cutoff during its construction. Ambiguous, empty and
partial answers retain the original planning path. Keys include the complete
substitution map, rather than only the procedure or call-site name.

Plans share the existing resolver metadata epoch: evaluated results, lexical
bindings and contextual witness changes invalidate them. Completed serial
snapshots and frozen worker snapshots can reuse them without sharing mutable
state. Default/named/variadic argument arrangement, overload selection and
readonly effects remain ordinary planner/analysis operations. The UI's separate
permission profile falls from about 170 ms before this pass to 82 ms after these
changes; opt-in nested timings are not used as wall-time benchmark samples.

Record graph copying measured only about 1–2 ms. No new aggregate-copy shortcut
or field omission was added to the checker.

## Final measurements and verification

Seven alternating warm release samples per engine, with four workers:

| Workload | Baseline | Final | Reduction |
| --- | ---: | ---: | ---: |
| General demo | 0.858 s | 0.654 s | 24% |
| Hilo | 0.316 s | 0.214 s | 32% |
| Poker | 0.393 s | 0.281 s | 28% |
| UI demo | 0.831 s | 0.550 s | 34% |
| Circles | 0.246 s | 0.164 s | 33% |
| Othello AI | 0.979 s | 0.682 s | 30% |
| 26-file workload | 0.234 s | 0.177 s | 25% |
| 200 SDK-calling procedures | 0.152 s | 0.120 s | 21% |
| 300 scalar procedures | 0.062 s | 0.058 s | 6% |
| Long alias chain | 0.035 s | 0.032 s | 8% |

These are transpile-only command medians, excluding the final Odin compilation.
Native compile-time helper work is included on a result-cache miss. The engines
use separate project caches and retain common SDK/parser caches; no other test
jobs ran during the final timing pass. Generated stress programs were executed
on both engines with assertions, bounds checks and type assertions disabled
before timing. Real projects were copied, checked after timing, and left
unchanged. These samples do not promise a latency bound for arbitrarily large
inputs or expensive uncached compile-time execution.

All 350 focused compiler methods and 16 vet methods passed, including the 12 new
regressions for these optimizations. Rust passed 70 unit tests in both debug and
release; Clippy passed for all targets with warnings denied. TLS passed 37 tests,
live CodeLLDB passed 43, and assertion hygiene passed seven checks. Checked/debug
and unchecked native configurations are included in the compiler tests.

The copied corpus passed 204 build comparisons: 95 valid and seven expected
invalid cases in each of the renamed/idiomatic variants, with zero status
changes. Four projects' own suites passed in Odin, renamed Thor and idiomatic
Thor (12 runs). Original-file verification reported no changes. These are
focused regression groups, not a full repository-suite claim.

The unoptimized Rust development executable overflowed its main stack in the
existing 1,000-level member stress test. The shipping release executable passed
that test with both one and four workers. Debug builds of the generated native
programs and live source breakpoints also passed.

[Structured evidence](sdk-resolver-efficiency-results.json) includes raw timing
samples, source/executable hashes, profiles, checkpoints and validation scope.
The implementation checkpoint is `17ba34c`.

## Investigation notes

The explicit contextual array call `#run add([3,4])` with a `[2]i8` parameter was
found to return an unresolved-specialization limitation in both the baseline
and new compiler. `#run add([2]i8{3,4})` works. This pre-existing language gap is
not changed by the performance work.
