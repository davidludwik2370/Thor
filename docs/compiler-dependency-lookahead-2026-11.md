# General dependency lookahead before permission-analysis retries

This follows [earlier cache lookup and declaration discovery](compiler-cache-discovery-2026-11.md),
using `40e8845` as the performance baseline. It changes Rust compiler analysis;
Thor syntax, runtime ownership, and the Odin compiler are unchanged.

## What changes

When permission analysis stops at an unresolved import, it now records both the
requested name and its enclosing declaration. After loading that namespace, a
small name-resolution walk resumes the requesting declaration and follows the
newly resolved declaration. This batches dependencies before constructing the
next permission graph.

Resuming the caller matters even for constants: encountering `a.VALUE` can stop
analysis before a later call to `b.work()` in the same procedure. Following only
`a.VALUE` would miss that independent dependency. Same-package aliases and direct
helper calls are also followed, with cycles deduplicated using stable AST IDs.
The algorithm uses requested names and lexical relationships; it contains no
project filenames, SDK package-name rules, or benchmark-specific call patterns.

This work activates only after effect discovery stalls. Ordinary declaration
discovery and programs whose permission graph is already complete do not run
the lookahead. Import-name tracing is enabled only during that discovery and
lookahead, and starts empty in every resolver. All state is local to the current
compilation; there is no new persistent cache or output-file category.

## Bounds and correctness

The lookahead has a shared 4,096-node inspection budget per call-discovery pass.
Signature inspection consumes that budget too, and large child lists are capped
before queueing. Queue traversal and source loading remain deterministic.

It skips uncalled nested procedure bodies, ambiguous overload groups, generic
procedure bodies, compile-time execution nodes, and branches whose compile-time
condition is unresolved. Known conditions follow the selected branch and flags.
Annotations and directly resolved declarations can still contribute names.
Exhaustion or ambiguity simply yields to the existing discovery loop.

The walk produces dependency requests only. It does not publish partial effect
summaries or decide whether an argument may be mutated. The ordinary permission
and purity analysis runs on the expanded source graph and remains responsible
for errors, editor hints, diagnostics, and call eligibility. Existing exact-input
fact-cache validation is unchanged.

## Regression coverage

`tests/test_rust_dependency_lookahead.py` builds synthetic SDK overlays rather
than modifying installed libraries. It covers multi-package call chains,
constant misses followed by independent imports, aliases and recursion,
ambiguous overloads, generic fallback, uncalled nested helpers, conditional
effects, and a body larger than the work budget. It verifies transitive mutation
errors at the original Thor argument, serial/parallel output and source maps,
and pure-helper execution with warm result reuse. Native execution uses both
debug and unchecked release flags, with explicit checks that survive assertion
elision.

Broader validation and quiet before/after measurements are recorded in
[the structured evidence](compiler-dependency-lookahead-results.json).

All 258 focused compiler methods passed, including the 11 new synthetic cases.
TLS passed 37 methods, live CodeLLDB passed all 43, assertion hygiene passed all
seven, and Rust's 57 units passed in both debug and release configurations.
Clippy passed for all targets with warnings denied. The isolated corpus retained
95 valid projects and seven expected invalid projects in each of the renamed
and idiomatic Thor variants, with no unexpected results or original-file changes.
These are focused gates, not a full repository-suite claim.

## Measurements

Measurements use release executables on the same macOS arm64 host, four workers,
warm parser/result caches, and seven alternating before/after invocations per
case. They time `transpile`, including source-map output and cache reporting;
the final Odin backend is excluded. No profiling or regression checks ran during
these timing samples. Each copied project was type checked afterwards, and
source hashes were checked before and after.

| Workload | Before | After |
| --- | ---: | ---: |
| General demo | 0.827 s | 0.834 s |
| Hilo | 0.311 s | 0.307 s |
| Poker | 0.383 s | 0.386 s |
| UI demo | 0.842 s | 0.850 s |
| Circles on dots | 0.242 s | 0.243 s |
| Othello AI | 1.044 s | 0.963 s |
| Generated: 200 scalar procedures | 0.066 s | 0.066 s |
| Generated: 200 SDK scalar calls | 0.152 s | 0.150 s |

Othello AI improves by 7.8%. The other seven medians range from 1.2% faster to
1.0% slower; these small changes do not establish gains or zero overhead. Its
separate profile shows three permission analyses/two dependency retries falling
to two analyses/one retry. Lookahead inspects 490 nodes and batches the remaining
namespace without a third effect graph. UI already needed one analysis and
retains that path without lookahead.

The generated five-package regression chain was also measured independently:
nine alternating warm samples improve from 0.255 s to 0.241 s (5.5%). Permission
analyses fall from five to two and dependency retries from four to one. Its
lookahead inspects 123 nodes. Native execution was checked after timing on both
executables. This validates a general dependency pattern independently of the
existing application corpus.

The first incomplete effect pass is still necessary to obtain exact demand
sites. Unknown generic/overloaded bodies and budget exhaustion can still need
additional retries, and changed compile-time values can require fresh analysis.
This removes avoidable repeated work without retaining incomplete summaries or
claiming a universal one-second bound.
