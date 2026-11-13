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

## Investigation notes

The explicit contextual array call `#run add([3,4])` with a `[2]i8` parameter was
found to return an unresolved-specialization limitation in both the baseline
and new compiler. `#run add([2]i8{3,4})` works. This pre-existing language gap is
not changed by the performance work.
