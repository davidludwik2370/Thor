# Vet purity-candidate performance — Thor/TLS 0.41.5

Poker's reported `thor vet .` time was 24 minutes 40 seconds. The bottleneck was
purity-candidate discovery, rather than Odin compiling the application.

Before this change, vet invoked the complete purity/provenance analysis before
checking whether a procedure's public signature could ever become `fn`. That
analysis follows transitive helpers and solves their reference/store graphs.
Pointer and dynamic-array inputs, map results, and other unsupported value types
could therefore pull in large sorting, threading or operating-system helper
graphs, only to have the proposed conversion rejected later. A bounded
100-second profile of the old path was still in candidate discovery when stopped;
it had not reached proposed-edit validation.

Vet now rejects definitely unsupported public value types first, recursively
including record fields, generic record arguments and union variants. Defaulted
parameter and result types are inferred before filtering. Direct calls already
recognized as I/O by the existing purity rules are also rejected before preparing
their provenance graph. Unknown/native-dependent shapes still receive normal
validation. This filter does not change the language's purity rules.

In particular, filtering a pointer-input procedure as a public `fn` suggestion
does not disallow calling that ordinary procedure from a pure function with
locally created storage. Eligible callers still receive the full transitive
purity check, including the existing local-mutation rules.

All reported corrections still pass normal Thor/Odin checking using temporary
overlays. Vet checks the original program and then the proposed edits; it can
need further checks when a batch contains invalid candidates. It is consequently
not guaranteed to cost precisely one compilation, but it no longer solves
obviously ineligible purity graphs to discover that their signatures are invalid.
Ordinary builds without `--vet` do not run this additional candidate filtering.

## Poker measurements

A serial command sequence on the current source, with generated output and
caches redirected to temporary storage, produced:

| Command | Elapsed |
| --- | ---: |
| `thor build .` | 2.49 s |
| `thor vet .` | 5.35 s |
| `thor vet .` again | 5.46 s |

Both vet runs report the same 15 suggestions shown in the original report:
13 immutable declarations plus `less` and `is_straight` as `fn` candidates.
A profiled run spends 0.028 seconds discovering those candidates and accepts all
15 in one proposed-edit check. No poker source is edited; its source hash is
verified before and after the command sequence. These are local wall-clock
observations, not portable performance guarantees.

## Regression coverage

`tests/test_vet.py` checks that pointer, dynamic/fixed-capacity array, map,
callback, nested generic record, union, map-result, inferred-result and direct-I/O
candidates never reach the expensive purity analysis. It also verifies that
supported slices/records/unions/defaulted parameters and locally mutated helper
inputs still produce verified suggestions. Applied corrections run checked and
with assertions, bounds checks and type assertions disabled.

`tests/test_vet_tls.py` checks that ineligible signatures coexist with an eligible
reader without blocking its mapped warning, quick fix or subsequent symbol
requests. Existing vet tests retain coverage of large valid batches, mixed
mutable/read-only slices, transitive purity, apply/ask behavior, stale snapshots,
warning controls and rollback.

The complete vet/TLS/assertion-hygiene group passes 28 tests, including the new
analysis-avoidance and local-mutation regressions.
An additional 47-test purity-hardening and immutable-slice copy interaction run
also passes.

## Separate full-test timing observation

Applying the 15 suggestions to a temporary poker copy succeeds. An extra
`thor test` run on that copy exceeds a 90-second bound before emitting Odin.
A bounded control run on the untouched original also spends 30 seconds in the
same recursive slice-access inference loop during emission. This is a separate
full-test analysis cost; ordinary `thor vet`/build timings above exclude those
test files. This pass does not claim a completed full poker-suite run or resolve
that additional performance issue.

The full-test slowdown is subsequently resolved in Thor/TLS 0.41.6; see the
[test-command performance report](test-performance-2026-11-08.md).
