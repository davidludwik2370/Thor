# Read-only slice views and inferred `#mut`

An immutable binding (`:>` or `name: Type > value`) now removes writable access
through slices contained in that value. This is a view restriction: an existing
writable alias can still change the shared backing storage. No allocation,
runtime tag, reference count, or lifetime tracking is added.

```odin
original := []int{1, 2, 3}
view :> original
original[0] = 9 // Allowed; view[0] now reads 9.
alias :> view   // Another read-only view.
// writable := view // Error.
// view[0] = 7      // Error.
// pointer := &view[0] // Error.
```

The binding itself remains immutable: replacing its header or taking its mutable
address is rejected. `raw_data` and writable reference iteration cannot expose
the protected element storage. Slicing preserves permission, so `part :> view[1:]`
is valid while `part := view[1:]` is rejected. Discarding a view with `_` does not
create a writable alias. Read-only views of immutable fixed arrays also work.

Fixed-array and record copies still copy their inline data. Scalar results from
`len`, arithmetic, indexing, or value iteration remain ordinary values. A copied
record's nested slice references retain their permissions. Nested slices reached
through a read-only slice remain read-only, including through returned records,
shallow clones, comprehensions, and calls to other procedures.

```odin
Row :: struct { count: int, items: []int }
rows :> []Row{{7, []int{1}}}
copy := rows[0]
copy.count = 9          // Independent inline field; allowed.
// copy.items[0] = 9    // Still the borrowed nested slice; rejected.
copy.items = make([]int, 1)
copy.items[0] = 9       // Independent replacement storage; allowed.
delete(copy.items)
```

These copy rules apply to indexing, value iteration, comprehensions, `copy`,
`append`, `swizzle`, `expand_values`, `new_clone`, and procedure results. Each
result keeps its own permission: a numeric result alongside a borrowed slice
does not inherit that slice's restriction. Narrowing a union to a numeric,
fixed-array or record variant uses that variant's actual copy semantics.
Target-dependent numeric aliases such as `core:c.long` remain numeric values.

`basic.clone` makes a shallow value copy. In particular, cloning a slice header
with it does not allocate writable backing storage. `core:slice.clone` and
`clone_to_dynamic` allocate independent outer storage; nested slices remain
shallow references. `new_clone` allocates the outer pointed-to value and likewise
preserves restrictions on nested slices. Use `deepclone` when independent nested
storage is required.

Odin array components (`.x`, `.y`, `.r`, etc.) and swizzles (`.yx`, `.rgba`)
follow the same rules as indexing and copying array elements. Reading a numeric
component from an immutable vector, including a foreign procedure's result,
produces an ordinary numeric value. Components that contain slices retain their
read-only permissions; writing a component through a read-only slice is rejected.

This extension applies to slices. Existing standalone pointer, map, Set and
dynamic-array rules remain in force. A pointer stored inside a protected slice
can still modify its pointee; the stored pointer value itself cannot be replaced.
Deleting backing storage and allocator destruction remain manual ownership and
lifetime responsibilities. A read-only view is not necessarily an owner, so do
not delete multiple aliases of one allocation.

## Constant literal storage

An immutable slice constructed entirely from proven native constant values can
use embedded read-only data:

```odin
TABLE :> []int{1, 2, 3} // Lowers with @(rodata).

example :: proc() {
    table :> []int{1, 2, 3} // Lowers with @(static, rodata).
    // The same read-only backing is reused on each call.
    _ = table
}
```

This avoids initializing local literal backing on every invocation. Ordinary
local slice literals already use stack storage rather than heap allocation;
this optimization removes the repeated initialization and backing stack space.
Literal backing is borrowed static data and must not be deleted. The optimization
does not make `:>` a compile-time constant declaration or extend its write access.

The proof handles native numbers, booleans, strings, enums, constant expressions,
inline arrays/records and nested slice literals. It follows genuine `::`
constants and supports contextually typed literals and groups of slice literals.
Numeric/Boolean/enum `#run` results qualify after evaluation. `#run` strings and
aggregates retain their existing materialization and ownership rules. The
optimization never executes an ordinary procedure to decide whether its result
could be constant.

Runtime values, existing slice views, dynamic containers, allocating logical big
numbers, unresolved generic shapes and explicit declaration attributes retain
their existing lowering. In particular, `view :> original` still shares
`original`'s storage and observes writes through its mutable aliases. Runtime
slice-to-string view conversions also retain their usual local lifetime.
Mixed groups containing non-slice initializers and declarations in statement
headers retain their existing lowering. If the compiler cannot prove eligibility,
it uses the ordinary implementation without adding a new restriction.

## Procedure contracts

Thor infers which parameter storage needs writable access. Requirements include
writes, obtaining writable aliases/addresses, and exposing references through
writable storage or unverified calls. Analysis follows helper calls, aliases,
named arguments, reference stores and returned views. I/O, mutable globals and
mutation of independent local allocations do not by themselves make an input
require writable access. These are access contracts, not purity contracts.

```odin
copy_values :: proc(source, destination: []int) {
    copy(destination, source)
}
// Inferred: only destination requires writable access.

copy_values_explicit :: proc(source, #mut destination: []int) {
    copy(destination, source)
}
```

`#mut` applies only to the immediately following parameter name, independently
of a shared type annotation:

```odin
first :: proc(#mut a, b: []int) {}      // a explicit; b inferred.
both  :: proc(#mut a, #mut b: []int) {} // Both explicit.
```

Explicit `#mut` requires writable input even if the implementation does not
currently modify it. There is no unused-annotation warning. Omitting it does not
promise read-only behavior. It is supported on slice parameters, including
variadics and generic specializations that are slices; other parameter types
receive a source-level diagnostic. The directive is erased before Odin sees the
signature, preserving calling conventions, evaluation order and debugger maps.

Other native parameter directives retain Odin's grouped-field rules. Put them
before the shared name list, or use separate typed parameter clauses. Only
`#mut` has the per-name extension described here.

Returned aliases preserve the caller's permission. A mutable declaration cannot
turn such a result into writable access. A fresh clone has independent writable
outer storage; its reference-containing elements remain shallow. Use a deep copy
when independent nested storage is needed.

This applies separately to each result of a multiple-result call, including
parentheses, forwarded returns, argument expansion and `or_else`/`or_panic`
unwrapping. A Boolean or allocator-error status is an independent scalar value;
copying that status does not copy the accompanying slice's restriction.

Declared `fn` results retain their existing deep-copy guarantee. Returning an
input slice from a `fn` produces independent owned storage, so the result can be
mutable even when the input is a read-only view. An ordinary `proc` returning
the same slice preserves its caller's access permission.

```odin
tail :: proc(values: []int) -> []int { return values[1:] }
part :> tail(view)
// writable := tail(view) // Error.
```

Inference is conservative. Runtime conditions include every possible branch.
Statically resolved `when`/`#switch` expressions use their selected branch;
conditions the access analysis cannot resolve keep a conservative join until
their native specialization is known. Calls in switch selectors, case matches,
comprehension keys and filter/output expressions follow the same access rules
as direct calls.
Defaulted parameters are checked using their inferred types. Generic procedures
use their actual specialization, including record type arguments and fixed-array
lengths, rather than assuming every unknown element is a borrowed reference.

For `#soa` views, `soa_zip`/`soa_unzip` track permissions separately for each
column. A helper that writes only column `b` can receive a zipped read-only
column `a` and writable column `b`. Copying an SoA row copies its inline fields;
nested slice fields still borrow their original storage.

Replacing a copied local record's slice header with independent storage restores
writable access through that header. Earlier copies keep their old restrictions.
Definite fresh replacements at both ends of an `if` are recognized. A replacement
that may be skipped, delayed by `defer`, or occur only in a loop does not erase
an outer header's earlier permission. More complex indexed or loop-carried alias
updates remain conservative; this is not a complete ownership analysis.
Unresolved callbacks, foreign/bodyless procedures and opaque `any`/raw-pointer
conversions cannot silently shed permissions. Core printing, cloning, and the
reviewed basic string/hash interfaces have argument-specific contracts. Ordinary
inspectable Odin helpers can be checked without rewriting their declarations.
The `fmt.bprint`, `bprintln`, `bprintf` and `bprintfln` interfaces require a
writable destination buffer while reading their other formatting arguments.
Calls exposed by `using package_name` retain the same parameter and returned-view
contracts as calls written with the package prefix. Reviewed library readers
also retain their package identity; a same-named local procedure is checked by
its own implementation.

Mutable callbacks and procedure parameters do not borrow the access contract of
their initializer or default expression. A slice returned by an unresolved
callback/foreign body is conservatively a read-only view when a Thor view can
escape a procedure or reside in global storage. Bind that result with `:>` and
clone it for writable access. Local views inside procedures with value-only
results cannot be captured by Odin procedure literals, so they preserve ordinary
callback/foreign return permissions elsewhere. Writable escapes from those
local views are still checked at their use. A fixed immutable alias of an inspectable reader
retains that reader's precise contract. Immutable inline arrays and inline record fields also count as protected
storage when a returned slice can expose them.

Reference-containing map/Set keys are a conservative boundary: inserting a
pointer or record that carries a read-only slice reference is rejected rather
than erasing its permission through key enumeration. Ordinary scalar/string keys
and independently writable pointers are unaffected.

These reviewed library contracts assume that user-installed formatting callbacks
respect formatting's read-only-input convention. Arbitrary casts through erased
`any.data`/raw pointers are an unsafe boundary, rather than a way to acquire
checked writable access. There is no whole-program alias or lifetime guarantee.

## Editor and validation

TLS requests the compiler's contract metadata with each ordinary background
check, including unsaved overlays. It renders an inferred `#mut ` in grey before
the individual parameter name, and includes requirements in hover/signature
text. Explicit annotations are not duplicated. Metadata is tied to the exact
UTF-8 source snapshot and stale hints are suppressed after edits. Standard VS
Code inlay-hint settings control their display.

Fields stored inside a protected slice are also colored read-only. Copying an
inline record restores writable access to its independent fields while retaining
the qualifiers on any nested slice references.

Errors highlight the read-only argument or forbidden operation and include
related locations for the view's origin and the operation needing writable
access. No generated implementation types are part of the user contract.
Repeated instances of the same access failure in generated type witnesses and
runtime helpers collapse to one diagnostic at that original operation.

`examples/slice_access/demo.thor` is the commented runnable demonstration.
`tests/test_slice_access.py`, `tests/test_slice_access_tls.py`, the grammar suite,
and the real debugger test cover the feature and its interactions. Runtime checks
also run with allocation tracking, assertions disabled, and unchecked release
flags.

The [copy-boundary hardening report](slice-copy-hardening-2026-11-07.md) describes
the additional compiler and live TLS matrices in
`tests/test_slice_copy_hardening.py` and `tests/test_slice_copy_hardening_tls.py`.
Normal directory builds exclude `_test.thor`/`_test.odin` sources, matching Odin;
`thor test` includes the target package's tests. This keeps test-only examples
from producing access errors in an ordinary application build.

Ordinary compilation checks every body and infers borrowing contracts on demand
for reference-bearing calls. Editor metadata requests infer contracts for unused
procedures as well. Recursive summaries propagate changes only to their actual
callers, using completed permission graphs to determine convergence. See the
[test-command performance report](test-performance-2026-11-08.md) for validation
of these optimizations and their cache boundaries.
