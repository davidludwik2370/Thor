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
type-parameter default. An ordinary user library can use the same mechanism.
Pure mapping additionally recognizes the official scheduler declaration, as
described below. Forwarding helpers supply native type
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

## Compile-time mapping follow-up

`#run basic.pmap(jobs, callback)` and `pmap` inside `fn` now check the selected
callback transitively. The scheduler boundary is identified by its installed
declaration, including aliases and re-exports, rather than a bare identifier.
The scheduler still runs bounded parallel work in the isolated helper. Normal
runtime mapping remains a `proc` with the existing shallow ownership contract.
No language syntax or Odin compiler changes are involved.

Callbacks retain their native signatures. Ordinary procedures receive separate
checked direct-call and callback variants; per-call ownership proofs project
the job fields onto callback parameters. This permits local output mutation
without granting write access to a pure caller's borrowed data. Used defaults
and selected polymorphic branches are checked normally. Runtime callbacks and
explicit allocator overrides are rejected in pure mapping.

Each checked worker has an independent heap-backed scratch allocator, linked
to the caller before workers start. Its allocations survive joins and remain
available through result promotion and local use. The caller then releases
all worker scratch storage, including nested maps. Direct `#run` requests
materialize independent results before releasing their worker scratch storage.
Trees used by parallel calls synchronize allocation bookkeeping so explicit
delete, resize and allocation queries also work across worker/caller storage.
Ordinary scratch storage does not acquire that lock.
Ordinary runtime worker loops are unchanged.

The new `tests.test_pmap_comptime` suite covers callback inference, aliases,
inline callbacks, overloads, defaults, mixed direct/mapping calls, local versus
borrowed mutation, nested allocations, numeric results, cache reuse and
invalidation, failures and timeouts. Checked, debug allocation tracking, and
unchecked optimized builds exercise ownership paths. Live TLS coverage checks
result fields and original-location purity diagnostics through unsaved edits.
A related explicit-delete fallback now preserves native deletion of nonnumeric
record fields in programs that also use big numbers; its regression checks
that string and slice fields are freed with assertions disabled as well.

The follow-up release gate passed 103 mapping, purity, big-number and assertion
hygiene tests. All 17 new compile-time mapping tests also passed on the Python
reference. Additional validation passed 96 Rust unit tests, 9 live TLS tests,
all 1,166 native TLS tests, and all 11 native pure-runtime tests in both checked
and optimized unchecked builds. The updated demo ran cleanly with debug
allocation tracking and with assertions/bounds/type assertions disabled.
Two warm release transpiles of that demo took 0.321 and 0.302 seconds; these
measure lowering with cached compile-time results, excluding Odin compilation.

## Callback specialization follow-up

Known procedures now reach the native worker as compile-time value parameters,
including imported aliases, overload groups and inferred polymorphic callbacks.
The compiler materializes the selected monomorphic procedure with a constant
`procedure_of` declaration. It never passes that intrinsic as a runtime value;
this distinction also avoids partially specialized generic return types.
Runtime procedure storage, parameters, fields and factories keep indirect
dispatch. Result-type queries for these values inspect their procedure type
without evaluating the callback expression. Argument effects still occur once,
in their original order.

The checked and ordinary paths share inline call dispatchers and their existing
worker implementations. There is no runtime test for specialization, added
per-job allocation, scheduler change, or relaxation of purity checks. The
library reference and parallel demo describe both call paths. Additional
regressions inspect optimized LLVM IR for removal/preservation of indirect
calls and exercise live TLS types, unsaved edits, and source breakpoints.
The Python reference also emits its default collection flags in a stable order;
previous set iteration could give identical helpers different cache keys across
process hash seeds. The cache regression checks reuse across two explicit seeds
and invalidation after a callback edit.

One existing conservative purity rejection was reproduced on the parent
implementation during this work: a polymorphic callback returning a newly
allocated slice can be rejected as borrowed mutation when it initializes the
slice through indexed assignments. Building an equivalent local dynamic array
with `append` and returning its slice is accepted. Callback specialization
does not weaken that analysis; the accepted allocation case is covered with
`#run` and debug leak tracking.

That indexed-write rejection and related generic numeric/header-alias cases
are now corrected by the [purity follow-up](pure-map-permissions-2026-11-18.md).
The original indexed-assignment fixture has been restored and passes.

The final production gate passed 75 tests: 68 mapping/result-query regressions
and 7 assertion-hygiene checks. The Python reference exercised the same mapping
coverage; its cache failure was corrected and the cache, runtime-storage, and
type-witness cases were rechecked. Additional validation passed 96 Rust unit
tests, 128 related purity/slice/fallback tests, 12 live TLS tests, 6 live TLS
tests against an unchecked server, all 1,166 native TLS tests in both checked
and unchecked configurations, 3 CodeLLDB callback breakpoint tests and 143
editor tests. The demo passed normal, debug tracking and unchecked builds.
The [benchmark report](benchmarks/pmap-specialized-2026-11-18.md) records the
runtime measurements and unchanged scheduling limitations.
