# Scoped owners (`~`)

A scoped local owns shallow cleanup at the end of its enclosing lexical scope:

```odin
nums ~:= make([]int, 5)
// Equivalent lifetime to a local followed by deferred shallow cleanup.
nums[0] = 7
```

Use `nums ~:=` and immutable `nums ~:>` as the conventional spellings.
`nums~ :=` and `nums~ :>` also work, including whitespace between the marker
and colon. The marker belongs to the declaration; the variable is still named
`nums` in uses, completion, references, and rename. `:=` permits mutation and `:>` retains the
normal immutable-storage rules. The marker does not change the existing binary
xor or unary complement operator.

```odin
values ~:> [x*x for x in 0..<10]
print(values)
// values[0] = 7 // Error: immutable slice storage.
```

Only initialized local declarations with `:=` or `:>` in a braced statement
scope are supported. Globals, constants, fields, parameters, `_`, and control
flow initializer declarations cannot be scoped owners. Multiple declarations
may mark just the owning results: `values~, ok := allocate_values()`.

## Cleanup

Cleanup is selected from the resulting type, not the constructor's name:

| Owner type | Shallow cleanup |
| --- | --- |
| Typed pointer `^T` | Free the pointed-to object allocation, with its size. |
| Slice, string, cstring, or UTF-16 string | Delete its backing allocation. |
| Dynamic array | Delete its final backing allocation using its stored allocator. |
| Map | Delete its table using its stored allocator. |
| Thor set | Destroy its backing map. |
| `Integer` or `Rational` | Destroy the numeric value's owned storage. |

Other types have no implicit destructor in v1. In particular, an arbitrary
struct containing a pointer does not acquire a destructor. Fixed arrays and
inline fixed-capacity arrays do not need outer-allocation cleanup.

Registration itself does not allocate. Cleanup runs on normal fallthrough,
`return`, `break`, and `continue`, in reverse registration order with ordinary
`defer` statements. It reads the **final binding**, so normal dynamic array and
map growth works. As with Odin `defer`, process termination and traps do not
provide stack unwinding.

Pointer/slice/string owners retain the current allocator from initialization.
An explicit `allocator` constructor argument is captured at its original
argument position, evaluated once, and used for cleanup; supported built-in
positional allocator arguments work too. Changing `context.allocator` later
does not change cleanup. The allocator's state must itself outlive the cleanup.
For a pointer/slice/string conditional or fallback initializer, if any allocating
branch specifies an allocator explicitly, every allocating branch must do so;
this lets cleanup retain the allocator of the branch that actually returned.
A `nil` or terminating branch needs no allocator. Alternatively, use explicit
`defer` cleanup for mixed implicit/custom-allocator branches. Dynamic arrays,
maps, sets, and big numbers carry allocation information in their own representation. A custom factory returning
an owning pointer/slice/string must follow the ordinary `free`/`delete` allocator
contract: use the caller's allocator, or accept and honor an `allocator`
parameter supplied explicitly when its default differs from the caller’s
allocator. The compiler cannot discover a different private allocator hidden
inside an arbitrary factory.

```odin
{
    growing ~:= make([dynamic]int)
    for i in 0..<1000 { append(&growing, i) }
    // The final resized buffer is deleted at the closing brace.
}
```

Cleanup is shallow. If a scoped `[][]int` contains separately allocated slices,
its cleanup frees only the outer slice. It does not delete those inner slices.
Likewise, shallow pointer cleanup is not `deepfree`, and does not recursively
release a graph returned by `deepclone`. Use explicit `deepfree` for graph
roots rather than `~`.

## Ownership and lifetimes

A scoped declaration must receive independent, compatible owning storage.
Obvious borrowed initializers (`other_slice`, `values[1:]`, `&local`, string
literals, and slice literals with stack/static backing) are rejected. Allocate
or clone them instead. A result already carrying deferred-result cleanup cannot
also become a scoped owner.

The usual single-owner rules apply: do not delete/free it manually, give two
scoped owners the same allocation, replace the owner with an interior/borrowed
view, or discard an old owning allocation by assigning a fresh one over it.
Neither ordinary Odin aliases nor this marker constitute a general borrow
checker or an ownership-transfer mechanism. Custom factories and foreign code
must honor their documented ownership contracts. Escape diagnostics cover local
value/assignment/return flows, including inspectable helper results. They are
not a proof of every mutation through external calls, separately managed
containers, or output parameters. Keep all borrowed references within the
owner’s lifetime, just as with explicit deferred cleanup.

A scoped allocation cannot be returned from its owning procedure. The lifetime
check follows local aliases, views, pointer addresses, aggregate values,
conditional results, and inspectable helper return values:

```odin
bad :: proc() -> []int {
    nums ~:= make([]int, 5)
    view := nums[1:]
    return view // Error: nums is cleaned before the caller can use this view.
}
```

Independent copies and scalar loads can be returned:

```odin
import "core:slice"

good :: proc() -> []int {
    nums ~:= make([]int, 5)
    return slice.clone(nums) // Caller owns the separate returned allocation.
}

count :: proc() -> int {
    nums ~:= make([]int, 5)
    return len(nums)
}
```

Allocated `fmt.aprint*` and `fmt.caprint*` results are also independent copies.
An allocated outer collection may be scoped while its elements borrow another
owner: for example `source ~:= strings.clone("a b")` followed by
`parts ~:= strings.fields(source)`. Cleanup of `parts` frees only the string
header array. Its text still borrows `source`, so those elements cannot escape
`source`'s scope. The same distinction applies to shallow clones of collections
containing references.

Unresolved reference-returning callbacks are treated conservatively when their
arguments borrow scoped storage. Unsafe pointer casts and external code remain
subject to normal manual-memory obligations. The v1 lifetime analysis is bounded;
an excessively large recursive alias graph receives a diagnostic asking for
simplification or explicit `defer` cleanup instead of unbounded analysis.
SoA/zip views and arbitrary resource structs are outside the v1 owner types.

Scoped temporaries also work inside `fn` and `#run`; their cleanup takes place
within compile-time execution. They do not grant permission to return dangling
storage or bypass purity. In v1, `@(no_alloc)` and `#+vet no_alloc`
conservatively reject scoped cleanup: it observes the final binding and may
invoke allocator/destructor hooks, even if the initial container is empty.
Vet's immutable fixes preserve the marker. TLS shows the owner's type and scoped lifetime on
hover, maintains immutable highlighting, and reports lifetime errors at the
original return expression with a related declaration location.

See the [validation and ordinary-build performance results](benchmarks/scoped-owners-2026-11-22.md).

## Deeper opt-in vet analysis

`thor vet` now follows allocation identities through containers, helper output
parameters and globals, and checks subsequent accesses after cleanup. Definite
issues and unverified boundaries have distinct diagnostics and narrowly scoped
acknowledgements. This does not replace the lightweight ordinary-build checks or
add runtime instrumentation. See [Lifetime and leak vet](lifetime-vet.md).
