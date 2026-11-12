# Slice-checker efficiency pass

Baseline: `6dcc021`. This pass changes Rust analysis only; it preserves the
Python reference, Odin, and the language's readonly and purity contracts.

## 1. Narrow permission roots

Ordinary compile/check runs no longer use every eligible procedure as an eager
permission root just because some file contains an immutable declaration.
Procedures without a readonly source can be left to demand-driven call summaries.
The conservative scan retains local immutable declarations, pure functions,
external data reads/captures, possible reference results, unknown calls/types,
nonliteral default arguments, generic/overloaded calls, explicit `#mut`
contracts, interpolations, and comprehensions. A procedure
skipped as an eager root still receives a complete summary when a readonly or
pure caller needs its effects. Editor mode retains its root sweep for mutation
hints and semantic information.

Default arguments need special care: they belong to the declaration's syntax,
not the call's descendants. A new regression found that a void call with an
omitted readonly global default needed explicit retention; this is included in
the final rule and tests.

## 2. Cache complete type-property queries

Slice containment, unresolved-value detection, and possible-reference results
are memoized by type identity within one fixed-input permission analysis.
Recursive child queries still use their original visited sets and bounds. Only
a complete fresh-root query is memoized, so a cycle or depth/breadth cutoff
beneath another type cannot leak a context-dependent answer into that child's
standalone query. Demanded-but-unloaded namespaces prevent publication.

## 3. Share unchanged graph collections

Paths, origin/location sets, field maps, and effect maps use persistent backing
storage. Empty collections allocate nothing. Snapshots share populated data;
mutation detaches it, and exact content equality remains the authority. Repeated
insertion of an existing origin and clearing a collection do not copy the old
tree. Transient iterations borrow shared collections rather than constructing
an extra tree copy solely to consume it.

## 4. Propagate only new information

Exact no-op merges and provenance-free leaves avoid graph traversal/allocation.
For bodies that require more than two convergence passes, bounded dependency
traces can reuse an individual statement after an observed execution changed no
existing input/output value, binding, store, effect or global binding. Reuse
checks the observed values and shapes and versions of callees. Changed or active
callees, new contexts, unknown/oversized traces, and budget exhaustion take the
ordinary path. Initial ordinary passes remain untraced; nested traces are not
created. Recursive callers are still scheduled by the existing dependency queue.

Crucially, the canonical whole-graph comparison and the existing convergence
limits remain in place. The statement cache omits proven redundant transfers;
it does not replace exact convergence with hash equality or a mutation count.

## Validation and measurements

All 440 distinct focused compiler methods passed, including the 219-method core
and 201-method emission groups, the additional focused regressions, and 17 vet
methods. Follow-up groups overlap those counts. TLS passed 37 methods; live
CodeLLDB passed 43 after the final metadata safeguard. Rust passed 67 units in
both debug and release, Clippy passed with warnings denied, and assertion hygiene
passed all seven checks.

Root-pruning validation caught omitted readonly defaults and generic
instantiation-dependent checks. The final implementation retains those cases;
the affected emission group and targeted cases passed on rerun. Additional tests
ensure that abstract no-op merges still report real forbidden writes, scalar
copies stay mutable, explicit contracts keep codegen metadata, and long alias
chains still propagate and diagnose readonly inputs correctly.

The project corpus retained 95 valid and seven expected-invalid cases per
variant, with no status changes across 204 builds and no original-file changes.
Four copied projects' own test suites passed in Odin, renamed Thor, and idiomatic
Thor (12 runs).

Seven alternating warm release samples per engine, with four workers, gave:

| Workload | Before | After |
| --- | ---: | ---: |
| General demo | 0.848 s | 0.850 s |
| Hilo | 0.307 s | 0.305 s |
| Poker | 0.388 s | 0.387 s |
| UI demo | 0.861 s | 0.827 s |
| Circles | 0.245 s | 0.247 s |
| Othello AI | 0.980 s | 0.979 s |
| 26-file workload | 0.242 s | 0.237 s |
| 200 SDK-calling procedures | 0.153 s | 0.154 s |
| 300 scalar procedures | 0.063 s | 0.062 s |
| Long alias chain | 0.036 s | 0.036 s |

The UI frontend improves about 4%, and the multi-file frontend about 2%.
Differences in most other cases are small; these are not blanket speedups.
The final Odin backend is excluded, while project-specific compile-time helper
work remains included. Timed runs retain shared parser/SDK caches and use separate
project result caches. No whole-project output cache is added.

Separate profiles show the named UI slice-permission pass at 0.202→0.171 s
(about 15% faster). It records 16,109 type-property hits and 81 full property
walks. Othello's named pass moves 0.0619→0.0593 s; its overall time stays flat.
The scalar stress case drops 302 graph snapshots to 2, and the alias-chain case
skips 487 redundant statement replays. These mechanism-level reductions do not
imply comparable percentage reductions in total command time: parsing, type
resolution, dependency discovery, and other semantic work remain.

Raw samples, source/executable hashes, profiles and validation details are in
[the structured evidence](slice-checker-efficiency-results.json). The generated
workloads execute successfully on both engines with assertions, bounds checks,
and type assertions disabled before timing begins.

To reproduce the paired benchmark using a retained baseline executable:

```sh
THOR_JOBS=4 python3 tools/benchmark_slice_checker.py \
  --before /path/to/baseline/thor-rust \
  --project /path/to/project --runs 7 --report /tmp/slice-benchmark.json
```

The script copies user projects and does not execute or change their original
files. It adds the generated scalar-root and alias-chain workloads itself.
