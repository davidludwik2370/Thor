# Result types and parallel mapping

This change builds on `7b7274b`. Both the Rust compiler and Python reference
implement the same call-result query. No Odin compiler source is changed.

## Public behavior

`thor:intrinsics.result_type_of(call)` is an unevaluated type query. A single
result keeps its type; multiple results form an anonymous struct, preserving
declared names and using `v0`, `v1`, etc. for unnamed results. No-result calls
have no result type. Identity follows the imported declaration through aliases;
an unrelated procedure named `result_type_of` keeps ordinary call behavior.

`basic.pmap(jobs, callback)` maps a slice of structs by expanding each job's
fields into positional callback arguments. Results are ordered and allocated
once. An explicit `result_type=Result` can override result field names while
preserving their count, order and types. A callback with no results makes
`pmap` a statement without a return value.

The [library reference](../runtime/parallel/README.md) documents worker limits,
allocator ownership, nested calls, shallow results, and read-only slice rules.
The [demo](../examples/parallel/demo.thor) includes polymorphic multi-result
callbacks, overrides, result type queries and no-result actions. `fmap` and
`filter` retain their data-first API and existing inference.

## Implementation boundaries

The compiler handles the general type query and a query used as a dependent
type-parameter default. It does not recognize `pmap` by name. An ordinary user
library can use the same mechanism. Forwarding helpers supply native type
arguments positionally and preserve runtime argument evaluation exactly once,
in written order. Known polymorphic and overloaded callbacks receive a concrete
expected procedure type automatically; callers need no extra type annotations.
Runtime procedure variables use their declared result names.

Native `procedure_of` is used only inside unevaluated type witnesses. Passing
its result as a runtime procedure value can crash the installed Odin backend.
Inline callback literals are lifted into constant declarations for native
specialization, and empty struct spreads become zero-argument calls. Queries
inside compile-time helpers retain nominal result type declarations and any
transitive imports, without retaining or executing impure callback bodies.

The runtime worker loop is ordinary Odin library source. It partitions jobs
into contiguous chunks, uses a bounded number of threads, includes the caller
as a worker, and joins all started threads. There is no per-item scheduler
allocation, runtime type reflection or boxing. Nested maps are serial within
their worker. Scheduling allocation failures fall back to serial execution;
output allocation failure panics at the original Thor call site.

TLS resolves query records and dependent defaults in the caller's generic
context. It supports aliases, runtime callback variables, explicit output types,
inline generic callbacks and standard-library overloads. Returned fields appear
in hover and completion, and invalid calls retain original diagnostic ranges.

## Validation

`tests.test_pmap` covers scalar/struct/multiple/void results, imported and inline
polymorphic callbacks, overloads, native parameter directives, defaults,
variadics, named argument order, empty/nil inputs, worker bounds and joins,
nested maps, allocation failures, read-only views and original panic locations.
Owned slice and big-number results are exercised in checked, debug tracking,
and unchecked optimized builds. Test verdicts remain active when assertions
are disabled.

Additional checks include live TLS hover/completion/unsaved-edit recovery tests,
the complete native TLS suite, Rust unit tests, existing basic-library and
purity/slice regression suites, assertion hygiene, and a real CodeLLDB source
breakpoint inside a polymorphic mapping callback with result-field inspection.

The final Mac release gate passed 51 mapping/basic/assertion/debugger tests,
6 focused live TLS tests, 96 Rust unit tests and all 1,166 native TLS tests.
The new 20-test mapping/query suite also passed on the Python reference.
The demo passed both debug allocation tracking and optimized unchecked flags.
Three warm release transpiles of the demo took 0.180, 0.185 and 0.171 seconds;
these measurements exclude Odin compilation and are not a runtime speedup claim.

The Python reference passes the new mapping/query suite. Its existing full
basic-library demo test exceeds its 40-second harness timeout. An isolated copy
of parent `7b7274b` also exceeded 45 seconds; the stack is in the pre-existing
Python slice-analysis path. The production Rust demo passes. A separate Python
anonymous-struct field lookup failure exposed by ZIP coverage was corrected
and its targeted ZIP regression passes.
