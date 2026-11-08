# Restore test-command performance — Thor/TLS 0.41.6

Poker's full test command was spending its time in slice-access inference before
emitting Odin. The test files are still included and all 213 tests still run.
The correction changes compiler analysis, not runtime behavior or test defaults.

## Cause

The old recursive propagation loop revisited every collected procedure summary,
up to 24 whole-graph passes. Local header replacements could temporarily merge
and then replace the same permission facts, setting the shared `changed` flag
even though the final summary was identical. A bounded instrumented run recorded
2,100 replays across 442 summaries with **zero changes to completed graphs**.

Ordinary compilation also eagerly inferred hypothetical input-borrowing contracts
for every test procedure. That needlessly followed large testing/SDK dependency
graphs for calls with ordinary writable inputs. Repeated symbol, type, lexical
scope and filesystem path resolution amplified the cost.

## Changes

- Record summary dependencies and revisit the actual callers of changed recursive
  summaries. Compare completed permission graphs rather than transient writes.
  A bounded non-converging recursive graph retains a conservative write requirement.
- Check every procedure body with ordinary input facts. Infer full symbolic
  borrowing contracts on demand for reference-bearing calls. The ordinary-body
  and parameter-contract summaries have separate identities. Unused bodies still
  receive their local read-only checks, and reference-producing calls retain
  their result permissions.
- `--editor-info` requests full contracts for unused procedures too, preserving
  TLS `#mut` hints. Live editor diagnostics and related source locations continue
  to use the normal compiler check.
- Reuse resolved source facts during initial context preparation and the
  evaluation-suspended slice pass. Do not cache unresolved/generic query results
  or explicit recursive-query contexts. Active generic bindings bypass reuse;
  discovering another source invalidates the query caches. Caches are discarded
  after the analysis phase. Lexical scopes and import-directory resolution are
  also reused within the current compilation.

This adds no persistent whole-project cache, emitted support files, runtime
permission tags, allocation tracking or new ownership rules. A fresh compiler
invocation checks the current sources and active flags again.

## Measurements

The earlier command exceeded a 90-second bound before emission. Final ordinary
`thor test .` runs on the unchanged poker sources took **7.85 s and 6.98 s**.
All 213 tests passed; their execution took 2.75 s and 2.71 s respectively.

For comparison, Odin directly compiling and testing the generated source took
3.47 seconds, including 2.71 seconds of test execution. Thor still has additional
analysis and lowering overhead; it is not claimed to have zero overhead.

Additional measured runs passed all 213 tests with:

| Flags | Command time | Test execution |
| --- | ---: | ---: |
| `-disable-assert -no-bounds-check -no-type-assert` | 5.89 s | 1.50 s |
| `-o:speed -disable-assert -no-bounds-check -no-type-assert` | 9.04 s | 0.207 s |

These are local wall-clock observations, not portable guarantees. Default test
optimization and check settings remain unchanged. Every generated source, cache
and executable used for measurements is directed to temporary storage; project
source hashes are verified unchanged.

## Validation

The completed regression groups pass:

- 140 slice-access, copy-boundary, live TLS and inference-performance tests.
- 84 native-evaluation, Odin-compatibility, automatic-using, vet/TLS and
  assertion-hygiene tests.
- 163 pure-mutation, compile-time evaluation, comprehension, conditional,
  default-parameter and T07/T12 tests.

The new `tests/test_slice_analysis_performance.py` checks dependency replay counts
instead of imposing brittle wall-clock limits. It covers recursive argument
rotation, late returned aliases reaching their callers, independent bodies,
unused-body checks, generic bindings and source discovery, collection-path
changes, and checked/assertion-disabled execution. Differential cases compare
cached and uncached queries, and on-demand and editor-requested contracts, for
identical mapped diagnostics.

Dijkstra, hilo, othello and circles-on-dots also build successfully without source
edits. The recorded circles-on-dots build took 6.16 seconds; this is a local
observation rather than a replacement benchmark for the earlier project audit.
