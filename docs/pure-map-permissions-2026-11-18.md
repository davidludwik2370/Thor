# Pure mapping permission hardening, 2026-11-18

The generic indexed-allocation example previously documented as a purity false
positive now compiles and runs. The `append` workaround is unnecessary. Rust and
the Python reference enforce the same ownership rules; no language rules or
native Odin sources were changed.

## Corrections

### Apply complete callback checks after specialization

A checked helper's permission profile says which parameter paths are borrowed.
It is input metadata, not a list of unconditional mutation effects. The old
`pmap` bridge rejected every true profile field before the callback's native
`when` branches and exact type predicates could run. Even copying an ordinary
integer into a new slice could therefore be diagnosed as borrowed mutation.

Checked callbacks now receive a leading compiler-only type parameter describing
the caller's permissions. `procedure_of` selects the concrete callback with that
parameter bound. The resulting procedure value retains the original runtime
parameters and calling convention. Odin evaluates the callback's complete
compile-time checks, including generic type predicates and inactive branches, rather than
asserting that every profile field must be false. Anonymous callbacks and
imported aliases use the same mechanism. Public signatures and TLS type queries
continue to describe the source callback.

This is compile-time metadata: there is no new worker argument, allocation,
runtime permission check, or callback dispatch branch.

### Preserve logical-number clone provenance

A single copy flag could not distinguish a generic `Integer`/`Rational` clone
from borrowed references in a shallow aggregate copy. Repeated element
projection and joins could lose that distinction, especially when a separate
helper initialized a fresh buffer.

The analysis now tracks logical-number allocation permissions separately from
ordinary reference permissions. Direct logical-number copies clear the former;
projecting fields or elements of shallow copies retains their original
protection. Generic assignment analysis also considers the destination type,
matching the existing lowering when a value's type comes from another parameter
such as `fill(values: []$T, value: T)`.

Consequently, fresh numeric buffers can be filled through helpers and mutated
without modifying the source number. A copied record's shared numeric fields,
borrowed slices, and references copied with the native `copy` builtin remain
protected. The runtime clone/free behavior is unchanged.

### Share uninspected backing-storage metadata

A slice header copy could receive a separate element-provenance node if neither
header had been indexed yet. Helper writes then updated a temporary copy of the
metadata, and each analysis pass appeared to discover another new write.

Reference-container header copies now share a stable backing-element summary
from the start. Array value copies retain their separate value-copy handling.
This fixes imported helper initialization and variadic slice forwarding while
preserving aliases and borrowed-write diagnostics. The analysis bound and
nonconvergence rejection remain in place; no broad fixed-point workaround or
higher iteration limit was introduced.

## Regression coverage

The original specialization test now uses `make` and indexed tuple assignment.
`tests.test_pmap_permissions` additionally covers:

- Single and parallel assignments, local pointer aliases, record-held slices,
  dynamic buffers, and direct/inline `#run` callbacks.
- Array copies versus slice aliases, and inactive generic mutation branches.
- Fresh `Integer` and `Rational` results, direct helpers, imported generic
  helpers, and shallow numeric copies that must remain read-only.
- Variadic slice forwarding, including the formerly nonconverging reference
  fixture, with both owned and borrowed destinations.
- Debug allocation tracking and unchecked release execution. Rejection tests
  also build with assertions, bounds checks, and type assertions disabled.

Live TLS tests check result hover/completion and original-source diagnostics
through invalid unsaved edits and recovery. A CodeLLDB test stops in the
specialized generic callback and inspects its argument and allocated buffer.
The symbolic permission protocol also generates numeric-provenance accesses
in its Rust/Python differential corpus.

The parallel demo runs without tracker leaks in debug and unchecked builds.
Three cached release transpiles of an isolated demo copy took 0.324, 0.316 and
0.318 seconds, excluding Odin compilation. These are local desktop measurements,
not a guarantee for other projects.

## Validation

- The production CLI gate passed 84 tests covering mapping, compile-time
  execution, specialization, and assertion hygiene. The final expanded
  permission module also passed all 10 tests in both the Rust compiler and
  Python reference.
- The related purity, conversion, dynamic-result, and slice-copy suites passed
  128 tests.
- Native Rust unit tests passed all 96 cases. Rust/Python permission and borrow
  differential tests passed all 23 cases, including the expanded randomized
  numeric-permission corpus.
- Live TLS checks passed 16 cases. The new CodeLLDB callback breakpoint/local
  inspection test passed. Assertion hygiene passed all 7 cases.

Positive runtime cases exercise debug allocation tracking and unchecked
optimized builds. Borrowed-mutation rejection cases also exercise unchecked
builds, so disabling assertions cannot disable these purity rules.
