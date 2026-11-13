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

## Investigation notes

The explicit contextual array call `#run add([3,4])` with a `[2]i8` parameter was
found to return an unresolved-specialization limitation in both the baseline
and new compiler. `#run add([2]i8{3,4})` works. This pre-existing language gap is
not changed by the performance work.
