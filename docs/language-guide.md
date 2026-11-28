# Detailed language and library guide

For the feature tour and tested Thor/Odin comparisons, start with [the README](../README.md). This guide retains the extended rules and ownership details; dedicated references linked below take precedence for newer extensions.

## Basic library

The bundled [`thor:basic` package](../runtime/basic/README.md) works from any Thor
project without additional collection flags. Its canonical source is
[runtime/basic/basic.thor](../runtime/basic/basic.thor):

```odin
import "thor:basic"

// Inside a procedure:
result := basic.sorted([]int{3, 1, 2})
defer delete(result)
basic.print(result)
```

Use `import "thor:basic" / sorted, print` for selected unqualified names.
Use these forms to expose every public name, or select names without also
introducing the package namespace:

```odin
import * "thor:basic"                // All public names; basic.name also works.
// Alternatively:
import _ "thor:basic" / sorted, print // Only sorted and print; no basic namespace.
```

The unqualified names are local to the importing file. They are not re-exported,
and private declarations and internal imports stay hidden. Wildcard imports
preserve live access to exported variables, including assignments. Local
variables may shadow imported names; conflicting file-scope declarations and
imports are errors. Public names in conditional branches reserve their names
even when inactive, so two wildcard imports with overlapping potential exports
are rejected. An unused conditional export is not forced to exist.

Run the [import walkthrough](../examples/imports/demo.thor) with
`thor run /path/to/thor/examples/imports`.
Run the [commented example](../examples/basic/demo.thor) with
`thor run /path/to/thor/examples/basic`.

`basic.deepclone(&source)` returns an independently allocated graph root and an
error, preserving cycles, shared storage and interior references.
`basic.deepfree(root)` releases it once. See the [ownership contract](../docs/deepclone.md)
and [graph-copy demo](../examples/deepclone/demo.thor); these are library procedures
and require no changes to the Thor/Odin compilers.

For allocations that all share a block's lifetime:

```odin
{
    context.allocator = basic.scoped_arena()
    values := make([]int, 100)
    text := f"Created {len(values)} values"
    basic.print(text)
}
// The arena is destroyed and the enclosing allocator restored.
```

The arena uses Odin's native caller-scope deferred cleanup, including early
returns. Keep its allocations within that scope; explicit allocator choices and
`context.temp_allocator` retain their usual behavior. See the
[scoped arena contract](../runtime/basic/README.md#scoped-arena-allocations).

Use `nums ~:= make([]int, 5)` for a single local with automatic shallow
cleanup. Both `:=` and `:>` are supported. See [scoped owners](../docs/scoped-owners.md)
and the [runnable example](../examples/scoped_owners/main.thor).

`basic.hash(value)` returns a runtime `u64` value hash without allocating.
Select `.XXH64` or `.FNV64a` with `algorithm=`, or use the default `.XXH3_64`.
`basic.try_hash(value)` returns `(hash, Hash_Error)` for recoverable failures.
Strings/ordered containers hash contents, structs ignore padding, and pointers
hash addresses. See the [hashing contract](../runtime/hash/README.md).

`basic._string(value, allocator=...)` returns owned text and an optional
`runtime.Allocator_Error`. It supports numeric values including `Integer` and
`Rational`, bools, enums, runes, strings/cstrings, and byte/rune sequences:

```odin
text := basic._string([]rune{'a', '界', '😀'}) or_panic
defer delete(text)
```

Strings/bytes are copied; rune sequences are UTF-8 encoded. Scalar formatting
matches plain interpolation. Failures return empty text and release temporary
allocations. See the [conversion contract](../runtime/basic/README.md#converting-values-to-strings).


## Optional results with `or_panic`

Use postfix `or_panic` when an optional result must succeed:

```odin
value := strconv.parse_int("42") or_panic
entry := records[key] or_panic
node := item.(^Node) or_panic
first, second := get_pair() or_panic // Drops only the final status.
minimum := min(..values) or_panic
```

Like `or_else panic(...)`, it unwraps optional lookups/assertions and procedure
results whose final value is a success boolean or a nil-comparable error.
Booleans succeed when true; error values succeed when nil. The operand executes
exactly once, at its original expression site; short-circuited expressions stay
unevaluated. Ordinary type checking and ownership rules still apply. The
operator does not free the successful result or introduce automatic cleanup of
user-owned values.

On failure the ordinary panic handler receives the original `.thor` file, line,
column, and procedure, plus a message containing the original expression and
returned error/status. The default handler writes to stderr, for example:

```text
/path/main.thor(27:13) panic: unexpected failure
  expression: strconv.parse_int(text)
  procedure: main
  error: false
```

The location remains present with `-disable-assert`; panic is never compiled out.
The status check itself allocates nothing and formats no text; result allocation
and numeric-copy ownership retain their usual rules. Only the failure path
formats the returned error, using the temporary allocator.
Contextless procedures use a contextless panic; ordinary procedures retain the
current custom panic handler. `fn` and `#run` use the same unwrap semantics:
failed evaluations report both the evaluation request and the original failing
unwrap. `#run` still requires a direct call: put the unwrap inside the called
procedure rather than writing `#run (operation() or_panic)`. Native `#load`
retains its own constant-expression restrictions.

TLS supports result types, immutable highlighting, keyword help/completion,
references/rename, and diagnostics. Original-source debug mapping applies as
usual. Thor reserves `or_panic` in ordinary binding positions. Member names
can retain that spelling, for example `item.or_panic`. In a scope with a
promoted member, `(or_panic)` explicitly reads that member. Native `.odin`
files use Odin syntax.


## Multiple-value `or_else` defaults

Optional operations with several successful results can use a parenthesized
fallback list. Discarded positions must match the assignment targets:

```odin
_, cores :> info.cpu_core_count() or_else (_, 1)
number, text := read_pair() or_else (7, "fallback")
```

The operation executes once; defaults execute in order only on failure. Each
slot retains its corresponding result type, ownership, and slice permissions.
The fallback list itself does not allocate. See the
[complete rules](../docs/or-else-fallback-lists.md) and
[runnable example](../examples/or-else-lists/or_else_lists.thor).


## Spread minimum and maximum

Thor extends Odin's builtins with fixed-array expansion and slice spreading:

```odin
a := [?]int{1, 2, 3}
maximum := max(**a)         // int: 3
minimum, ok := min(..a[:]) // int: 1, bool: true
lowest := min(..a[:])      // The presence flag is optional.

empty: []int
missing, found := min(..empty) // 0, false
fallback, valid := max(7, ..empty) // 7, true
```

Every call containing a slice spread returns `(value, ok)` with Odin's
`#optional_ok` behavior. `ok` reports whether the whole call has at least one
candidate, including scalar starting values. Empty slices without starting values
return the element type's zero value and false. `or_else` and `or_return` work
with the status. A fixed-array expansion produces a single value; expanding a
zero-length array is a compile error.

Arguments execute once in source order. Fixed-array expansion snapshots its
elements; a slice borrows its backing storage. Reductions allocate no heap
storage or mutate their inputs. Results preserve the element type. Untyped scalar
literals can adapt to it; typed mismatches are errors. Ordered numbers, strings,
enums and their distinct aliases retain native comparison semantics. Selected
strings remain borrowed from the input.

Scalar calls such as `min(x,y)` and type limits such as `max(int)` retain their
native behavior. User-defined procedures named min/max remain ordinary calls.
Immutable aliases of the actual builtins and `core:math.min/max` also support the
new forms. Declared `fn`/`#run` bodies can reduce sequences, and required constants
with known inputs are evaluated. The existing single-result compile-time rule
still applies: return a record containing both value and status from a function
when both must be retained at compile time.

Run the [commented example](../examples/extrema/demo.thor) with
`./thor run examples/extrema`.


## Expressions, arrays and ranges

Conditional compilation uses Odin's `when`, `else when`, and `else` directly.
Use ordinary `if` / `else if` for runtime branching. The `#switch` and `#run`
directives retain their existing behavior.

```odin
import "core:math" / sin,
    cos

numeric :: proc(value: $T) -> T where T in [int, f64] {
    return value
}

MODE :: 2
when MODE in [1, 2] {
    ENABLED :: true
} else {
    ENABLED :: false
}

main :: proc() {
    values := [1, 2, 3]
    typed: [2]f64 = [1, 2]
    empty: [0]int = []
    result := ENABLED ? 27
                      : 31
    assert(result == 27 && 2 in values && 9 not_in typed)
    assert(len(empty) == 0 && sin(f64(0)) == 0 && cos(f64(0)) == 1)
}
```

Import selections have no trailing comma. Selected names may be procedures,
procedure groups, types, or constants; mutable package-variable selections are
rejected, including when unused. Local variables/parameters can shadow a selected
name. The implementation resolves these aliases to qualified references, and
validates selections with local aliases in a private initializer: Odin does not
allow privacy attributes on every kind of alias declaration.

Array literals infer their element type from context, otherwise from the first
element with Odin's normal literal defaulting. A type-only helper is used inside
`type_of` when necessary; it does not evaluate the element an extra time. Every
literal element executes once. An empty literal needs context, except that an
empty membership list can obtain its element type from the needle.
An inline membership list also receives context from an already typed needle:
`tile: i8 = 4; found := tile in [4, 8]` uses an `[2]i8` list. Untyped constants
and arithmetic expressions in the list receive the same context. Conversely,
`4 in existing_i8_array` coerces the untyped `4` to the array's element type.
Named arrays keep their established types: `values := [4, 8]` remains `[2]int`,
so an `i8` needle does not implicitly convert to match it. Explicitly typed list
elements must match too; out-of-range literals are errors rather than narrowing
casts. `not_in`, compile-time conditions and generic value constraints follow
the same rules. With untyped operands on both sides, normal first-element
defaulting remains in effect, so `1.5 in [1, 2]` is an error.
The inferred element type also supplies context to later nested array/record
literals. Procedure defaults and assignments through indexing or pointers are
covered by the tests.

Runtime membership evaluates the needle first and the container second, once
each. All literal elements execute before searching, even when the first element
matches. Enclosing `&&`, `||`, and ternaries retain their lazy behavior. Generated
membership helpers are contextless and allocate no storage. Map membership tests
keys, and string substring membership is intentionally unsupported.

Intervals also support membership: `value in low..<high`, `value in low..=high`
and `value not_in (low..<high)`. The half-open form excludes `high`; inclusive
membership compares directly without incrementing the bound. Reversed intervals
are empty. Compatible numeric values, runes, enums and strings use Odin's ordered
comparisons; string intervals are lexicographic.

Runtime operands execute once in value/low/high order, including the high bound
when the value is outside the interval. Anonymous owned strings/views are freed
after comparison. Constants, `when`, `#switch`, `fn`/`#run` and constraints work too:

```odin
allowed_size_range :: proc($N: int) -> int where N in 0..<10 {
    return N
}
```

Arithmetic bounds work directly, as in `value in low+1..<high-1 && value != 5`.
Use `for (value in low..<high)` for a condition-only loop; native `for value in
low..<high` retains iteration. Three-part loop headers accept membership as their
condition, including an empty post statement. Intervals here are syntax for
membership, rather than first-class range values.

### String repetition

String repetition uses `*` with a constant string and a nonnegative integer
constant, in either order. It has ordinary multiplication precedence:

```odin
X :: "a" * 3          // "aaa"
Y :: "a" * 3 + "b"    // "aaab"
Z :: 4 * X + "z"      // "aaaaaaaaaaaaz"
PHRASE :: ("a" + "b") * 2 // "abab"
EMPTY :: "anything" * 0   // ""
```

Like Odin's string `+`, both operands must be compile-time constants; runtime
immutable bindings (`:>`) are not compile-time constants. Named/imported
constants, `#config` values, `#run` results and constant parameters inside generic
procedure bodies work. The result is constant storage: assigning it with `:=`
does not allocate, and it must not be deleted. Untyped literal/constant inputs
retain native coercions such as a `cstring`
procedure argument, including after concatenation. Explicitly typed string
inputs retain their type, including for zero repetitions.
Strings repeat their exact bytes, including UTF-8, escapes and raw strings.
The result is limited to 16 MiB, matching the existing compile-time value budget.
Thor emits a doubling chain of
native constant concatenations, with no repetition execution helper or new cache
files. See [the runnable example](../examples/string-repetition/main.thor).
In generic signatures and type expressions, a repetition depending on generic
parameters currently needs a fixed integer count from 0 through 128. A count
that varies with a generic parameter works inside the procedure body.

### Sequence concatenation

Compile-time sequence concatenation uses `++`:

```odin
FIRST :: [1, 2]
SECOND :: [3, 4, 5]
ALL :: FIRST ++ SECOND ++ [6] // [6]int
WORDS :: []string{"hello"} ++ []string{"Thor"} // []string
ROWS :: [[1, 2]] ++ [[3, 4]] // [2][2]int; rows stay nested

SMALL :: [1]i8{1}
MORE :: SMALL ++ [2] // New literals inherit the established element type.
EMPTY:[0]i8: [] ++ [] // Empty literals need an element type from context.
```

Operands must be native compile-time values. Runtime variables, including `:>`
bindings and ordinary procedure parameters, do not qualify; `$` constant
parameters do. Arrays may have different lengths but must have matching element
types. Slices must have the same slice type. Arrays and slices cannot be mixed
implicitly. An array result is a plain fixed array of the combined length; a
slice result retains its slice type. Concatenation is shallow and combines only
the outer sequence. Constant backing storage follows Odin's usual materialization
rules, including for nested slices; no independent heap clone is made.

`++` has the same precedence as `+` and associates left to right. Native array
`+` keeps its element-wise meaning, and strings keep their existing constant
`+`. There is no `++=` or increment operation. Chains lower to one
`intrinsics.concatenate` call, with no evaluation helper, heap allocation or
cache entries. A runtime binding may copy the resulting fixed array normally;
constant slice backing must not be deleted. Odin's intrinsic restrictions still
apply: positional constant literals are supported; named/indexed literal entries
and runtime array arithmetic are not silently evaluated as constants.

See `constant_concatenation` in [the commented tour](../examples/demo/demo.thor).
The old `basic.CONCAT` alias has been removed; use `A ++ B` instead.

### Exponentiation

Power uses infix `**`; native prefix `**values` expansion remains available.
It associates to the right and binds above multiplication and unary signs:
`2 ** 3 ** 2 == 512`, `-2 ** 2 == -4`, and `(-2) ** 2 == 4`.
Both operands must have the same integer or floating-point type. Untyped numeric
constants follow native coercions, so `2.0 ** -3 == 0.125` and `f32_value ** 3`
work; typed `int_value ** f64_value` requires an explicit conversion.

### Typed loops

Numeric loops can specify their value type, and array/slice loops can specify
only the index type using a parenthesized binding:

```odin
ROUNDS :: 10
for i: f64 in 1..=ROUNDS { probability := 0.5 ** i; _ = probability }
for value, (i: i8) in values { _ = value; _ = i }
for &value, (i: u8) in values { value += int(i) }
```

Range annotations supply context for untyped bounds; already-typed bounds must
match or be explicitly converted. Range values and indices remain immutable;
`&value` keeps its existing reference behavior. Element types are inferred, and
the ambiguous `for value, i: i8 in values` spelling is rejected. Index types must
be integers. Fixed arrays must fit their complete index range at compile time;
slices and dynamic arrays panic if a visited index cannot be represented, even
with `-disable-assert`. The internal counter retains Odin's normal type, so a
narrow index cannot wrap. Annotations allocate no memory and preserve native
range-bound evaluation, labels, and loop control flow. See the
[commented examples](../examples/demo/demo.thor).
Floating-point ranges keep Odin's unit stepping and precision limits, including
the inability to advance by one at sufficiently large magnitudes.

Floating-point powers use `core:math.pow`, including fractional exponents.
Integer powers require a nonnegative exponent (a compile error when known, a
panic otherwise). `0 ** 0` is `1`; runtime integer overflow follows ordinary
Odin multiplication. Untyped integer constants retain exact values through 128
bits. Operands execute once in base/exponent order and retain lazy surrounding
control flow. Powers work in constants, array sizes, `when`/`#switch`, specialized
procedure generics and `fn`/`#run`, using the existing native evaluation cache.
Type-level generic expressions without a resolvable call site currently require
a fixed nonnegative integer exponent through 128; other generic constant powers
need a resolvable procedure specialization.

`T in [int, f64]` compares exact type identity; aliases match, unlisted distinct
types do not. Generic constraints and other constant contexts use constant
comparisons rather than runtime search. Parentheses, `not_in`, duplicate entries,
and empty type lists are supported. Named constant arrays can also be used for
value membership when their length is statically determined by the frontend.

```odin
import f "thor:format"
import sets "thor:sets"

main :: proc() {
    text := f.format(-1234567, grouping=true, width=14, align=.Center, fill='界')
    defer delete(text) // returned formatting strings belong to the caller
    names: sets.Set(string)
    defer sets.destroy(names) // destroy each table once, including its aliases
    sets.insert(&names, "apple")
    for name in sets.entries(names) { assert(name == "apple") }

    value :> len(text) // runtime value; direct writes and &value are rejected
    data := [1, 2]
    view :> data[:]
    data[0] = value // writable aliases can change what the read-only view sees
    // view[0] = value // rejected
}
```

Immutable records and fixed arrays also protect directly stored subobjects.
Slices reached through an immutable binding become read-only views, including
nested slice references. Existing writable aliases can still modify the shared
storage. Pointer targets and dynamic arrays retain their existing behavior.
Taking an immutable header's mutable address is rejected.
Immutable slice literals with provably compile-time contents use read-only
backing storage: `@(rodata)` at package scope and `@(static, rodata)` inside a
procedure. Local backing is shared between calls, avoiding repeated stack
initialization. This is a storage optimization; the binding remains a runtime
value. Runtime initializers and views of existing storage retain their normal
behavior. See [slice literal storage](../docs/slice-access.md#constant-literal-storage).
Native map entry assignment can change the map header, so it requires a mutable
header (a mutable shallow copy may still access shared entries). Ordinary Odin
copying and ownership rules apply; this feature does not add lifetime checking.
Compile-time branches and generic shapes are checked by Odin after lowering, so
inactive branches remain unchecked and reference views are distinguished from
directly stored arrays. TLS marks immutable symbols readonly and explains them
in hover; live compiler checks enforce mutations on unsaved text.

An immutable array can be sliced directly for value iteration:

```odin
values :> [10, 20, 30]
for value in values[:2] { fmt.println(value) }
```

This reads the original storage without allocating or copying the whole array.
Named immutable views and passing them to readers are supported. Writable aliases
and `for &value in values[:]` remain rejected. Thor infers per-parameter writable
access requirements; explicit `#mut` applies to the next name only:

```odin
copy_values :: proc(source, #mut destination: []int) {
    copy(destination, source)
}
```

TLS displays inferred `#mut` in grey and includes requirements in hover text.
Resolved calls propagate read-only views stored through output parameters,
including forwarding procedures. Output storage and returned values preserve
their shared references.
See [the complete slice access contract](../docs/slice-access.md) and the commented
[slice access demo](../examples/slice_access/demo.thor).

`:>` enforces storage immutability in Thor; Odin's `@(rodata)` is a storage
placement attribute and does not provide the same compile-time checks.


## Using without a feature header

Thor enables standalone `using value` statements and `using` procedure
parameters by default. Write them directly in a `.thor` file; the transpiler
adds `#+feature using-stmt` to that file's generated Odin when needed.
This includes `using` parameters in procedure types and anonymous procedures.
Fields promoted by `using person` inside a loop over `soa_zip` or `basic.zip`
retain their inferred types in Thor expressions and f-string captures. Constant
aliases of `soa_zip` preserve this inference.

Embedded fields (`struct { using base: Base }`) and declarations such as
`using value := Record{}` already work in Odin without the feature, so those
forms do not cause a tag to be added. Comments and strings are ignored.
Existing feature tags are preserved without duplicates; an explicit
`#+feature !using-stmt` retains the restriction for that file. Native `.odin`
dependencies keep Odin's feature rules.

Original `.thor` files are never edited. Compiler diagnostics, TLS locations,
breakpoints and stepping use the normal source maps to account for the
generated header line.
Promoted fields keep their own value types when supplying expression context.
For example, assigning `kind = switch { case condition: .A; case: .B }` through
`using record: ^Record` uses the type of `record.kind`, including enum and
generic fields.


## Interpolation, switches, Sets and negative indices

```odin
main :: proc() {
    names := set[string]{"apple", "pear", "apple"}
    names += "plum"
    assert(len(names) == 3 && "pear" in names)
    for name in names { assert(name in names) }

    data := [10, 20, 30]
    data[-1] += 1
    tail := data[-2:]
    label := switch data[-1] {
        case 30: "before"
        case 31: "after"
        case: "other"
    }
    value := switch { case len(tail) == 2: 1234567; case: 0 }
    text := f"{{label}} {label}: {value:'0'12,}"
    assert(text == "{label} after: 0001,234,567")
    delete(text)
    delete(names)
}
```

Interpolation accepts quoted or raw backtick literals. `{{` and `}}` escape
literal braces. Fields contain real Thor expressions, including nested literals,
switches and interpolations. Parenthesize a ternary before a format separator.
The [format grammar](../docs/interpolation-formatting.md) is
`[quoted-fill][flags][width][,][.precision][verb]`, following Odin's `fmt` verbs.
For example, `.2f`, `12.2f`, `-12.2f`, `#08x`, `012,.2f`, and `.2%`.
Use nested braces for a runtime width: `f"{value:{width}.2f}"`.
Alignment defaults to right for every type; `-` means left and `^` means center.
`<` and `>` are removed. Unicode fills use quoted runes (`'界'^12s`);
width counts runes. `f/F` defaults to three decimal places, `e/E` to six,
and native `g/G` to shortest round-trip text. Big-number decimals are exact;
Rational `g/G` requires explicit precision (`.6g`). String `.10s` truncates at
rune boundaries. Large precisions use sized storage. Fields execute once, left to right. The final string is owned by
the caller. Internal formatting buffers and owned nested interpolations are
released after use, including mixed branches such as
`f"{(flag ? f"value {n}" : borrowed)}"`: only the selected owned string is freed.
Named field values keep their existing ownership.

Formatting streams into bounded stack scratch, with a heap fallback for large
output. Common short native f-strings allocate only the final owned string.
See [formatting performance and benchmarks](../docs/fstring-performance.md).

Anonymous f-string arguments are temporary by default. Thor releases them when
the whole enclosing expression finishes, including nested calls and selected
conditional/switch branches. This applies to every function, including native
Odin functions; printing has no special compiler rule.

```odin
print(f"value {n}")                  // Automatically freed after printing.
print(identity(f"value {n}"))        // Lives through both calls.
save_for_later(f!"value {n}")        // Opt out: the receiving API owns cleanup.
append(&messages, f!"value {n}")     // Containers retain string headers too.

text := f"value {n}"                 // Named owners still require deletion.
defer delete(text)
other := f!"value {n}"               // Legal; same ownership as a named f string.
defer delete(other)
```

The `f!` spelling also supports raw backticks. It disables automatic temporary
cleanup; use it when the receiving API retains or deletes the argument. This is
a caller-controlled lifetime rule, not a checked borrowing contract. Returning
or storing a reference to a default anonymous argument beyond its expression
would leave a dangling reference. Named strings, literals and arbitrary
string-returning procedures are not automatically deleted at call sites.
Ordinary factory results still need the lifetime specified by their API.
Native type conversions preserve ownership: `text := string(f"value {n}")`
owns its string and needs deletion. In `print(string(f"value {n}"))`, the
enclosing `print` call still controls temporary cleanup.
See [the lifetime implementation and validation notes](../docs/fstring-lifetimes.md).

Expression switches require a default arm unless a runtime enum switch covers
every named value. Odin checks that coverage; missing enum cases are errors.
`#partial switch` also works in expression position, with a default supplying
the omitted cases' result (or `panic(...)`). An unmatched enum value produced
by an explicit cast panics rather than returning a fabricated zero value.
Arms may supply one result or a
comma-separated list of results. They support comma-separated cases and native ranges, check duplicate
constant cases, evaluate the selector once, and execute only the first matching
arm. Expected destination/argument/return types supply result context.
Each result receives its own destination type, including distinct types, enum
members and inferred record/array literals. Multiple results use ordinary Odin
multiple-value rules; swaps evaluate every selected RHS before storing results.
An arm can also call a procedure returning the required number of values.

```odin
a, angle, b = switch {
    case rotate: b, other_angle, a
    case:        break
}

NUMBER, TEXT :: #switch {
    case DEBUG: 1, "debug"
    case:       2, "release"
}
```

`break` skips the entire assignment and leaves all destinations unchanged. It is
allowed only when the switch is the entire RHS of an ordinary `=` assignment
(parentheses are fine), in any arm. It does not break an enclosing loop. It is
rejected in declarations, returns, arguments, nested arithmetic and compound
assignments. Destination locations/indices are evaluated once before switch
selection, as in native assignment; skipping performs no stores. The same
default/enum-coverage rule applies. `#switch` supports the same result lists and assignment-only
skip; compile-time constants and unchecked inactive arms retain native `when`
behavior. Anonymous Thor allocations discarded into `_` are cleaned up; named
values remain borrowed. Run the [commented examples](../examples/switch-results/demo.thor)
with `thor run examples/switch-results`.
A switch result arm may call any procedure declared `-> !`, such as `panic`,
`unreachable`, or a custom/imported nonreturning procedure. It terminates instead
of supplying a value, so it does not contribute to result-type inference.
For example, `direction: Coord > switch { case valid: {-1, 0}; case: panic("invalid move") }`
is valid. Aliases, procedure-valued parameters and generic specializations follow
the same rule. Ordinary void calls are not result arms. A switch with only
nonreturning arms needs an expected result type, or expected types for multiple
results. Normal argument checking, calling conventions and purity rules apply.
`#switch` has the same selector forms in statement and expression positions;
it uses native `when` semantics for constant selection, declaration visibility,
control flow, and unchecked inactive arms. Later conditions, including those in
a comma-separated case, stay unchecked after a match. Explicit `#run` follows
selected compile-time branches and known lazy conditions.
A selected nonreturning arm in a `#switch` expression also needs an expected
result type; inactive arms are unchecked. `fn` may call `panic`, and a panic
reached during `#run` or automatic compile-time execution fails the compilation.

Sets use `|`, `&`, `-`, `~`, `==` and `!=`, with `|=`, `&=`, `-=`, `~=`
for in-place updates; `+=` inserts one element, while `-=` accepts an element
or another Set. Binary mathematical results own independent tables. Assignment
copies a shallow header. Anonymous Thor intermediates are cleaned up automatically:
`result := a | b | c` reuses one result table; only `result` needs deletion, and
named operands remain untouched. `clear(s)` keeps the table allocation;
`delete(s)` releases it. Keys retain their ordinary shallow
ownership. Native operations on other types keep their Odin meaning.

`to_set` and `to_slice` are available from both `thor:sets` and `thor:basic`:

```odin
import * "thor:basic"

unique := to_set([1, 2, 2, 3])
defer delete(unique)
values := to_slice(unique)
defer delete(values)
```

`to_set` accepts fixed arrays, slices and dynamic arrays, including distinct
sequence types. Duplicates become one key. `to_slice` returns an allocated `[]T`
with one copy of each key in unspecified order; sort it separately if needed.
Both produce independent container storage and accept `allocator=` (defaulting
to `context.allocator`). Element copies are shallow: separately owned strings
and pointees retain their existing lifetimes. Empty inputs are supported.
Sets retain their allocator for later insertion and deletion; delete slices
with the allocator that created them when supplying a custom allocator.

Automatic cleanup covers Thor-owned expressions consumed by operators, queries,
formatting, indexing, iteration, unused expressions and blank destinations. Loop
temporaries survive through the loop and are released on exit. Assigning or returning an owned result
leaves cleanup to the caller. Arbitrary procedure arguments/results, aggregate
fields and separately allocated Set keys retain Odin's manual ownership rules;
this does not introduce a general ownership checker. Use named keys when their
allocations need independent cleanup.

A view that must survive its expression needs a named owner:

```odin
owner := f"value {n}"
defer delete(owner)
view := owner[1:]
```

Thor diagnoses `view := f"value {n}"[1:]` and equivalent temporary pointer/field
views. Immediate reads such as `len(f"value {n}"[1:])` keep the owner alive until
the read completes. Borrowed views through mixed branches are conservatively
rejected when the owner cannot be carried safely.

Negative signed bounds normalize to `len(sequence) + bound`; unsigned bounds
keep their type. Strings index UTF-8 bytes. Bounds are checked after normalization
without clamping; excessively wide signed bounds are checked before machine-index
conversion. The base and supplied bounds execute once in source order. Map keys,
enumerated arrays, pointers and multi-pointers retain their native interpretation.

The emitter uses named procedures with explicit lexical inputs, rather than
closures (which Odin does not provide). Calls stay at the original expression
site; declarations do no work. The same source maps cover helper bodies and their
instantiations, so errors point back to fields, arms, elements and accesses.
Expressions inside `for &row` over SOA views preserve native row references,
including nested helpers and writes to the original columns.
Nonallocating helpers also work inside native C/system/contextless procedures;
allocating features require a context, which such callbacks can assign explicitly.


## Pure functions and compile-time execution

```odin
square :: fn(value: $T) -> T { value * value }
SIZE :: square(4) // automatically evaluated: a genuine integer constant
buffer: [SIZE]int
NEXT :: #run eligible_proc(SIZE) // ordinary proc execution still needs #run
```

`fn` is a contextual procedure spelling. It lowers to an Odin procedure with a
purity contract. A value-returning `fn` implicitly returns its final expression;
explicit and early `return` statements remain valid. A final declaration,
assignment, loop, `if`, or `when` statement does not supply a result. A trailing
value-producing `switch`, `#switch`, or `#partial switch` follows exactly the
same rules as `return switch`, including multiple results and exhaustive cases.
Statement switches with assignments, explicit returns, or other statements keep
their normal behavior. Only the
function body's final expression is implicit; nested statement blocks keep their
normal rules. Trailing comments or semicolons do not discard that value.
Parenthesize an untyped brace literal, such as `({x, y})`, to distinguish it from
a statement block. Ordinary `proc` bodies still require explicit returns, and
void `fn` bodies keep ordinary call-statement behavior. See
[the example](../examples/implicit_returns/demo.thor).

Calls with known values/types are evaluated automatically;
runtime inputs remain runtime calls. Literals, named constants, supported constant
expressions, type queries and evaluated results qualify. Mutable variables and
immutable runtime bindings (`:>`) do not become constants through propagation.
`#run` also accepts an ordinary `proc` when its relevant transitive call graph
passes the same checks. Recursion, concrete generic calls, local constants,
overloads, defaults, named arguments and forward/dependent results are supported.
Explicit `#run` requires known inputs and reports a runtime argument as an error.

A `fn` may directly call an ordinary Odin or Thor `proc` when that procedure
and its transitive callees satisfy the pure rules. This applies to runtime calls
as well as compile-time evaluation:

```odin
square :: proc(n: int) -> int { return n*n }
adjusted :: proc(n: int) -> int { return square(n)+1 }
calculate :: fn(n: int) -> int { return adjusted(n) }
VALUE :: calculate(3) // 10
```

Calls from `fn` use checked versions of ordinary bodies, including the same
scratch cleanup and independent returned storage as `fn`. An ordinary helper may
mutate storage created within the enclosing `fn`, recursively through other
helpers. Typed pointers may be used internally to address that storage:

```odin
set_y :: proc(out: ^int) { out^ = 7 }
calculate :: fn(x: int) -> int {
    y: int
    set_y(&y)
    return x + y
}
```

Numeric casts, including `auto_cast`, keep Odin's type and overload inference.
Pure code cannot use casts to manufacture pointers or expose pointer identities;
that restriction also covers pointer elements inside converted arrays/slices.
Native type queries may still inspect such types without executing a conversion.

The permission follows the affected storage. A local record/header containing an
input slice still borrows that slice; forwarding it through a pointer, field,
subslicing, reference loop or another helper does not permit writing its elements.
Fresh local arrays/buffers and scalar fields may be changed. Helpers may allocate
buffers through output parameters; their storage lives until the enclosing pure
invocation finishes. Helpers cannot store references to their own stack locals
or slice literals through output parameters; use `make` for surviving buffers.
Pointer-bearing results and public `fn` inputs remain
outside the supported value subset; pointer identity comparisons and numeric
address conversions remain forbidden. Provenance joins are conservative across
branches and reassignments, rather than a full flow-sensitive borrow checker.

Direct ordinary calls
retain their original behavior and ownership; their known arguments alone do
not trigger automatic compile-time evaluation. See
[the transitive-call demo](../examples/pure_calls/demo.thor).

Printing, file/stream I/O, and the basic library's `print`, `input`, `read` and
`write` are excluded from pure call chains. Calls such as `fmt.print(...)` in a
`fn` report a purity error on the original call, including in an unused
non-generic `fn`. Use `proc` for I/O. Aliases and transitive calls retain the same
rule; inactive compile-time branches and unevaluated type queries remain unchecked.
Formatting into a value is separate from observable I/O and remains subject to
the usual pure formatting, allocation and input-type rules.
See [pure-function and evaluation diagnostics](../docs/purity-diagnostics.md) for
the rejection categories, CLI/TLS source locations and execution-failure limits.

Generic `$T`/`$N` bindings are scheduled separately for each resolved instantiation,
including forwarding through imported procedures and inferred fixed-array counts.
A normal parameter is still runtime storage inside a generic body. For example:

```odin
work :: proc($N:int, value:$T) -> int {
    COUNT :: square(N)
    WIDTH :: #run bytes(T)
    data: [COUNT]int
    return len(data) + WIDTH
}
bytes :: fn($T:typeid) -> int { return size_of(T) }
```

The scheduler follows selected `when`, both `#switch` forms and known lazy
conditions. Calls under unknown runtime branches/loop bodies remain runtime;
this avoids executing a failing call on a path that may never be taken. Explicit
`#run` remains a compile-time directive even in runtime code. Type queries inspect
types without executing the queried expression. Invalid dependency cycles are
reported separately from valid native recursion. Unresolved generic `#run` calls
receive a source diagnostic rather than a fabricated result.

Inputs may contain scalar values, enums, strings, slices, fixed arrays, fixed SOAs, SOA slices, records,
bit sets and tagged unions. Borrowed contents are protected through aliases,
fields, slices, shallow copies, loops, union matches/assertions and calls. Local fixed-array
copies and fresh buffers can be mutated. Pointers, maps, dynamic-array inputs,
`any`, procedure values, runtime global storage, ambient context, uninspectable
foreign/indirect calls, pointer identity observation, pointer/aggregate bit casts, uninitialized
storage and unapproved operations are rejected. Numeric, boolean, enum and bit-set
scalar bit casts are eligible. Checks inside
inactive native `when` branches remain inactive.

Compiler-controlled deterministic operations include numeric conversions/operations,
length/type/size queries, `make` for local slice/dynamic buffers, `append`, `clear`,
pop-family operations, `copy`, `delete`,
assertions, `core:strings.clone`, `core:slice.clone`, and Thor's scalar/string
formatters. Other direct calls require inspectable, transitively eligible bodies. Explicit
allocator overrides and custom record formatters are rejected. Each checked
invocation uses private tracking scratch storage, including for interpolation;
mutating ordinary helpers share the enclosing invocation's scratch lifetime;
all outstanding internal allocations are released on every return. Ordinary
`proc` results and named f-string owners retain explicit cleanup. Anonymous
f-string arguments follow the expression lifetime rule described above.

Every allocated part of a `fn` result needs an explicit owner. Returning an input slice,
an alias of it, or a view into a local array is a compile error. Use `slice.clone` for
a slice of inline values. For a slice of strings or a record containing slices,
`pure.clone` also copies the referenced contents. A nonempty static string needs an
explicit clone when returned as an owned string.

```odin
import "core:slice"
import pure "thor:pure"

copy_tail :: fn(values: []int) -> []int { slice.clone(values[1:]) }
tail :: fn(values: []int) -> []int { copy_tail(values) }
copy_rows :: fn(rows: [][]int) -> [][]int { pure.clone(rows) }

values := [3, 5, 7]
result := tail(values[:])
defer delete(result)
```

The wrapper transfers the helper's allocation to its caller. The same rule applies
to named and implicit returns, concretely instantiated generics and pure callbacks.
Repeated references in an owned result get independent storage so each member can
be freed once. Use `pure.destroy(result)` for nested allocated members.

A pure function can read rows and columns of `#soa[]Row` and `#soa[N]Row` when
the logical fields are supported pure values. Pass a dynamic SOA's slice view
as input. To return rows, allocate a result with `make(#soa[]Row, n)` or clone
the input with `pure.clone(rows)`. A scalar read such as `rows[i].x` needs no
allocation. `#run` reconstructs owned SOA results with the same row values and
dynamic capacity.

Dynamic-array results retain their length and capacity and use the caller's
allocator, so later `append` and ordinary `delete` work normally. For example:

```odin
load_points :: fn() -> [dynamic]Pos {
    points: [dynamic]Pos
    for point in STARTING_POINTS { append(&points, point) }
    return points
}
points := load_points() // May fold automatically when all inputs are constants.
defer delete(points)
compiled := #run load_points()
defer delete(compiled)
```

`pure.destroy` recursively frees owned strings/slices/dynamic arrays and their supported
aggregate members. `pure.clone(value)` creates another deep owned copy. Both
accept an explicit allocator outside pure code. Ordinary assignments remain
shallow: destroy an owner once; free an owned field before overwriting it. Do not
use recursive destruction on borrowed literals or shallow native containers.
Arbitrary user procedure results and anonymous arguments remain manual Odin
ownership. Procedures without an Odin context, including `proc "contextless"`
and C-convention bodies, cannot promote allocated returns;
use `fn` or a contextful `proc` for those results.

Runtime `x := #run work()` and automatically folded calls materialize fresh owned storage on every execution,
including loop iterations and cache hits. In constant contexts, strings and fixed
aggregates containing strings become ordinary borrowed Odin constants. A
slice- or dynamic-array-containing result needs a runtime binding (`:=`), or a
pure consumer that returns a scalar constant. Native slice/dynamic-array constants
are not implemented. Public pure inputs still accept slice views instead of
dynamic-array headers.

The value protocol preserves arbitrary string bytes, nil/non-nil empty slices,
zero-sized sequence elements, dynamic-array capacities, fields, active union variants and exact float bits. It
serializes contents without padding or process addresses.

Explicit `#run` can execute an eligible ordinary Odin or Thor procedure,
including procedures in the installed `base`, `core` and `vendor` collections.
Thor discovers their source on demand and checks the implementation and required
dependencies under the same rules as project code. Library names do not confer
purity or require an allowlist entry. For example:

```odin
import "core:math" / round

SIZE :: 1_000
ARY_SIZE :: int(#run round(SIZE ** .33333))
values: [ARY_SIZE]int
```

Eligible overloads, generics, immutable procedure aliases and selected/wildcard
imports use the same path. Immutable library constants are inspected as
dependencies; mutable runtime state remains forbidden. Compiler intrinsics are
separate leaf operations: deterministic numeric/structural queries and known LLVM
math operations are supported, while clock/cycle counters, memory operations and
source identity queries such as `type_canonical_name` are not eligible user calls.
A procedure with an inspectable Odin body can use a non-Odin calling convention;
an external declaration without a body remains uninspectable. Existing input,
result and ownership constraints still apply. An optional second return value is
ignored by a single-value `#run`; non-optional multiple results need a consumer
that returns one supported value.

`--comptime=required|normal|aggressive|override` controls Thor's execution policy.
`normal` is the default. `required` keeps explicit `#run` and evaluations needed
for constants/types, while leaving optional calls at runtime. `aggressive` also
evaluates eligible ordinary `proc` calls with known inputs; `override` does the
same while ignoring `#runtime` barriers. Ordinary automatic calls must be proven
pure and allocation-free and return a self-contained fixed-size value. Borrowed
or allocated results keep their runtime ownership, and ordinary temporary
allocator effects are retained. Explicit `#run` and declared `fn` calls keep
their existing scratch-allocation/result contracts. See
[compile-time execution modes](../docs/comptime-modes.md) for examples, boundaries,
cache behavior, and the matching `tls.comptime` editor setting.

Compile-time execution uses temporary native helpers containing only
the required declarations/imports. User entry points, unrelated runtime globals
and package initializers are excluded; referenced runtime storage is rejected.
Only selected native overload implementations enter the helper, with private
implementations kept behind public evaluation bridges. Native source failures
highlight the original `#run` and include their library source location. Public
names and overload notes retain their original spelling.

For runtime checked calls into installed Odin libraries, Thor writes a sparse
SDK overlay under generated output and selects it for the backend compilation.
Only files containing checked procedures are augmented; untouched packages,
source files and resources link directly to the installed SDK. Unsaved SDK
replacements supplied by an editor are emitted into the overlay as well.
Ordinary and checked library calls therefore share the same nominal types;
the installed SDK is unchanged. The manifest's `odin_environment` records the
required `ODIN_ROOT`; use it alongside `odin_flags` when compiling generated Odin
manually. Ordinary project-only call graphs do not need this SDK overlay.

Run the [commented native-call walkthrough](../examples/native_run/demo.thor) with
`./thor run examples/native_run`.
Helpers preserve the original defines, `ODIN_TEST` and `ODIN_BUILD_MODE`, including
dependencies in test-tagged files. Cross-target
evaluation is rejected. Compilation errors map to original source; execution failures
point to the original call or `#run` expression. Helpers are deleted after use.
Helper compilation and execution have no deadline by default or with
`--run-timeout 0`. Positive limits set a deadline in seconds, for
example `--run-timeout 0.5`. With a positive execution limit, helper compilation
allows at least 30 seconds.
Independent automatic calls to global, non-generic `fn` declarations can share
a helper in batches of up to 32 compatible scalar results. Each call retains its
own result-cache key and scratch lifetime. Batch failures return to individual
evaluation in source order. Explicit `#run`, conditional and loop execution,
local captures, variadic calls and positive per-call deadlines keep individual
helpers. Native helper compilation stays serial to bound memory use.

Successful helper results use the shared bounded per-user cache. Keys include emitted transitive code/types/constants/arguments, native
flags/defines, compiler binary/version, Thor/frontend/runtime and installed
base/core/vendor source contents, plus static resource contents/existence. Keys are
conservative: equivalent calls at different sites or irrelevant source edits may
miss. Dynamic resource paths bypass caching. Partial, corrupt or incompatible
entries recompute; publication is atomic. Cache storage failures do not prevent
compilation. Cached entries contain typed contents, never executables or addresses.

```sh
./thor run demo --cache-report # repeat to see warm hits
./thor run demo --no-cache
./thor run demo --clear-cache
./thor check demo --cache-dir /tmp/thor-evaluations
```

`THOR_CACHE_DIR` supplies a default cache root; `--cache-dir` overrides project
records. SDK syntax uses the shared default root, or `THOR_CACHE_DIR` when set.
Each explicitly selected root has its own 512 MiB budget. See
[storage and development retention](../docs/storage.md).
`--cache-report` prints opt-in JSON counters on stdout, leaving compiler diagnostics
on stderr. Evaluation-cache lookups follow eligibility checks. Project analysis
and final Odin checking still run on every invocation. Late result-cache hits
hash generated evaluator code in memory and skip writing or compiling the
temporary evaluator project.

Pure callback types support runtime-selected functions, aliases, generic callbacks,
and known-callback `#run` calls. They use the same procedure-pointer representation
as Odin; their effect contracts are checked before lowering. See
[pure callback types](../docs/pure-callbacks.md) for examples and conversion rules.

Current conservative boundaries: the native compiler cannot reflect record
fields at compile time, so Thor supplies checked source field schemas. Unknown
external record schemas, some `using`-promoted record shapes and raw unions are
rejected; a record sharing a known raw union's field-name shape may also be
rejected. Provenance joins can reject writes to a fresh buffer after it has shared
a binding with borrowed storage. Compile-time evaluation currently supports direct,
resolved calls with one result; void/multiple-result `fn` calls retain their native
runtime behavior. The scheduler uses Thor's partial resolver, so unsupported or
ambiguous specializations may still need an explicit type or simpler wrapper.
Enum-indexed array constants support declared keys and zeroed
unnamed slots; nonzero unnamed slots fail rather than being changed on output.
Source-location-dependent `#file`, `#directory`, `#line`, `#caller_location`,
`ODIN_BUILD_PROJECT_NAME` and source/type identity queries, including
constants derived from them, are rejected in pure execution until their values
can be kept identical across generated runtime code and helpers. `#defined`
depends on the declarations in scope and is also excluded. Implicit deferred
procedure callbacks are also rejected; make eligible calls explicit in the body.
TLS infers call result types but does not execute procedures for hover values or
its own branch selection; live compiler checks perform the actual evaluation.

Lifetime and cleanup analysis is available through `thor vet`; see
[Lifetime and leak checks](../docs/lifetime-vet.md) for definite findings, unverified
boundaries, and `vet_ignore` acknowledgements. These checks add no ordinary-build
analysis passes or application runtime instrumentation.

## Generic callback defaults

Thor supports inline generic callback defaults in named procedures:

```odin
sorted :: proc(data: $T/[]$E,
               less := proc(i, j: E) -> bool { return i < j }) -> []E {
    ret := make([]E, len(data))
    copy(ret, data)
    slice.sort_by(ret, less)
    return ret
}
```

Import `core:slice` for this example. Calls can omit `less` or supply their own
comparator, including a runtime procedure value. The returned slice belongs to
the caller; delete it when finished. See the runnable, commented example in
[examples/default_callbacks/demo.thor](../examples/default_callbacks/demo.thor).

Thor emits a named generic helper, an explicit callback parameter type, and the
omitted argument at a statically resolved call. This works across Thor/Odin files,
package imports and selective aliases. Supplying a comparator skips specialization
of the default, so records can use a custom comparator even though `<` is not
defined for records. Explicit `nil` stays `nil`. Ordinary Odin procedure-value
types do not retain default arguments; indirect calls still supply the callback.
Overloaded calls that Thor cannot resolve also need an explicit callback.

Generic dependencies must be inferable from the callback's parameter types, such
as `E` in `proc(a,b:E)` or `N` in `proc(a:[N]int)`. A dependency appearing only in
the result/body is currently rejected with a mapped signature diagnostic.
Callbacks cannot capture enclosing runtime parameters. An independently generic
literal such as `proc(a,b:$U)` needs an explicit callback parameter type.
Ordinary `proc` parameters remain outside the `fn`/`#run` pure-input contract.
Use a `fn(...) -> ...` callback type when the callable itself must be pure; see
[pure callback types](../docs/pure-callbacks.md).

This extension applies to `.thor` declarations; native `.odin` declarations retain
their source-mapped unsupported-default check. Named generic defaults, concrete
callbacks and callbacks using already-bound outer types retain native behavior.
Odin still selects active branches and instantiates enclosing bodies before
checking guards or helper bodies. Computed type expressions that cannot be
classified confidently stay with Odin's checker.
