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
callback/foreign body is conservatively a read-only view: bind it with `:>` and
clone it for writable access. A fixed immutable alias of an inspectable reader
retains that reader's precise contract.

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
