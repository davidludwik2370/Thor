# Pure callback types

`fn(...) -> ...` in a type position means a callback that obeys Thor's pure
function contract. It accepts declared `fn` definitions and literals, including
polymorphic functions that Odin can specialize to the requested signature.

```odin
Callback :: fn(x: int) -> int
Alias :: Callback

square :: fn(x: $T) -> T { return x*x }

apply :: fn(value: $T, callback: fn(T) -> T) -> T {
    return callback(value)
}

ANSWER :: #run apply(7, square)

main :: proc() {
    callback: Alias = square
    value := 8
    result := apply(value, callback)
    if ANSWER != 49 || result != 64 { panic("unexpected result") }
}
```

Keep independently inferred data arguments before polymorphic callbacks, as with
`fmap`, `filter` and `pmap`. Odin's left-to-right generic inference still applies;
there is no new annotation requirement for those APIs.

## Runtime values and storage

A callback can be selected or reassigned at runtime. Its pure contract remains
part of its Thor type. Aliases, `type_of`, generic forwarding, return values,
`using` fields, arrays, slices and record fields preserve that contract.
A variable inferred directly from a `fn` value also retains it.

A pure function may call a runtime-selected `fn` callback. The invocation uses
an ordinary Odin procedure pointer. No allocation, thunk, runtime purity check,
or additional runtime field is introduced for the callback type.
A pure callback may allocate under the existing pure scratch/result-ownership
rules; purity does not imply `no_alloc`. Its inputs remain borrowed, and owned
results remain independent of its inputs.

Ordinary `proc` values cannot be assigned or cast to `fn` types, even if a
particular implementation looks side-effect free. Declare the implementation
as `fn`, or write an explicit `fn` wrapper whose calls Thor can verify:

```odin
ordinary :: proc(x: int) -> int { return x+1 }
pure_wrapper :: fn(x: int) -> int { return ordinary(x) }
callback: Callback = pure_wrapper
```

A `fn` value can be copied into a plain `proc` value to use ordinary Odin APIs.
That copy loses its pure contract; it cannot later be converted back based only
on the matching native signature. Native type queries use Odin's procedure-type
identity: the pure qualifier is absent from runtime type information. Thor checks
concrete comparisons using the erased procedure shape, including Odin’s
parameter/result names, so `when` selects the same branch.
Nested callable signatures and writable
containers must retain matching callback contracts. For example, `^Callback`
cannot be passed as `^proc(int)->int`, nor can `[]Callback` be passed as
`[]proc(int)->int`: another alias could otherwise write an impure procedure into
the original storage. Casts and builtins such as `append` and `copy` are checked
as well. Raw unions and uninitialized callback storage cannot establish a pure
contract. Foreign/bodyless declarations cannot promise pure callback values.
As elsewhere in Thor, these rules are not a general memory-safety guarantee
against undefined behavior in unsafe native code.

Callbacks use the Odin calling convention. Nil callbacks are permitted; check
for nil before invoking an optional callback. To disambiguate a resultless type
alias from a call to an ordinary identifier named `fn`, write
`Action :: #type fn()` (or `Action :: fn() -> ()`). Parameter annotations such as
`callback: fn()` need no disambiguation.

## Compile-time execution

Known arguments and known callback identities can run at compile time under
Thor's normal evaluation policy, or through an explicit `#run` on the enclosing
call. A runtime-selected callback remains a runtime call: a pure effect contract
does not make its identity or arguments compile-time constants.

Generic constant callback parameters are also supported:

```odin
square :: fn(x: $T) -> T { return x*x }
work :: fn($callback: fn(int) -> int) -> int {
    return #run callback(7)
}
ANSWER :: #run work(square)
```

The compiler uses zero-sized type witnesses to select constant-callback
specializations. These add no runtime storage or callback representation changes.
Existing `#run` result-serialization restrictions remain: a result containing a
procedure pointer cannot be serialized into another process. Returning a pure
callback at runtime is supported. A callback factory can also execute inside an
enclosing `#run` expression; only that expression's final result is serialized.
Calls returning callbacks are left in place instead of being automatically
folded into pointer-valued constants.

If a native-only or unresolved generic guard cannot be proved, each possible
branch must preserve the callback contract. This is a conservative check;
native type erasure must never select an unchecked impure branch.

TLS parses the same types, preserves `fn` in signatures and aliases, infers
callback results for hover and completion, and reports contract violations at
the original assignment or argument. Hover on a runtime callback explains that
compile-time execution requires a known callback.

See the [validation and analysis-cost report](benchmarks/pure-callbacks-2026-11-26.md)
for test coverage, native ABI checks, and measured verification time.
