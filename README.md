![Thor](assets/branding/logo-slim.png)

# Thor

Thor extends Odin with concise syntax, checked contracts and compile-time procedure execution. It generates ordinary Odin and uses Odin for native code generation and linking.

## Install

```sh
git clone https://github.com/davidludwik2370/Thor.git
cd Thor
./thor-install --vscode --profile Thor --path
```

On Windows, use `.\thor-install.cmd --vscode --profile Thor --path` instead of the last command. The installer acquires supported prerequisites, installs the toolchain and optional editor tools, sets up PATH and verifies the installation. OS authorization prompts can still require approval.

Omit `--vscode` for the toolchain alone. A named profile preserves your normal VS Code preferences. Add `--from-source` to install exactly the checked-out revision; downloaded releases can lag this README. See the [installation guide](docs/installing.md) for supported systems, update, verification and uninstall.

## Thor extensions

This tour assumes familiarity with Odin. [Odin’s overview](https://odin-lang.org/docs/overview/) introduces the underlying language. Native syntax, standard libraries, `when`, build tags and mixed `.odin`/`.thor` projects remain available.

The index and sections below use the same order. Odin comparisons demonstrate equivalent behavior for the example; Thor-only compiler guarantees are called out explicitly.

**Imports and bindings**

- [Selective and wildcard imports](#selective-and-wildcard-imports) — Bring selected public names, or every public name, into the current file.
- [Immutable bindings and views](#immutable-bindings-and-views) — Initialize once at runtime with `:>` and prevent writes through that binding.
- [Read-only and writable slice parameters](#read-only-and-writable-slice-parameters) — Enforce `#ro` readers and `#mut` writers, with inferred writable requirements.

**Data and expressions**

- [Inferred array literals](#inferred-array-literals) — Infer a fixed array’s length and element type.
- [Membership and intervals](#membership-and-intervals) — Test collections and numeric or ordered intervals with `in` and `not_in`.
- [Membership in generic constraints](#membership-in-generic-constraints) — Express allowed types and constant values as membership constraints.
- [Multiline ternaries](#multiline-ternaries) — Continue a ternary before its colon without a line-continuation escape.
- [Constant string repetition](#constant-string-repetition) — Repeat a compile-time string with `*`.
- [Constant sequence concatenation](#constant-sequence-concatenation) — Concatenate constant arrays or same-type slices with `++`.
- [Exponentiation](#exponentiation) — Use infix `**` for integer and floating-point powers.
- [Struct operators](#struct-operators) — Apply arithmetic, bitwise operations, shifts, power and complement field by field.
- [Typed loop bindings](#typed-loop-bindings) — Choose a numeric range’s value type or a sequence loop’s index type.
- [Formatted strings](#formatted-strings) — Embed expressions in quoted or raw strings, with optional formatting.
- [Formatting helpers](#formatting-helpers) — Use the native formatting support package directly when interpolation is not needed.
- [Collection comprehensions](#collection-comprehensions) — Build collections with an output expression, iteration and an optional filter.
- [Sets](#sets) — Use `set[T]`, deduplicating literals and mathematical set operations.
- [Negative indices and slices](#negative-indices-and-slices) — Count backward from the end of sequences.

**Control flow**

- [Switch expressions](#switch-expressions) — Use a switch as a value, including multiple results and contextual literals.
- [Comparison switch cases](#comparison-switch-cases) — Match `<`, `<=`, `>`, `>=` or `!=` against the switch selector.
- [Optional results with or_panic](#optional-results-with-or-panic) — Unwrap a successful optional result or panic with the original source context.
- [Multiple-value or_else defaults](#multiple-value-or-else-defaults) — Provide a parenthesized fallback list with matching discarded positions.
- [Spread minimum and maximum](#spread-minimum-and-maximum) — Reduce sequences with `min`/`max` and receive an optional presence flag.
- [Using without a feature header](#using-without-a-feature-header) — Use standalone `using` statements and `using` parameters directly.

**Functions and compile-time execution**

- [Generic callback defaults](#generic-callback-defaults) — Infer an inline default callback from earlier generic inputs.
- [Pure functions](#pure-functions) — Declare deterministic functions with checked read-only inputs and effects.
- [Implicit function returns](#implicit-function-returns) — Return a value-returning `fn` body’s last expression automatically.
- [Pure callback types](#pure-callback-types) — Preserve a function’s purity contract through callback values and aliases.
- [Automatic caller metadata](#automatic-caller-metadata) — Expose caller expressions and locations as documented, non-overridable parameters.
- [Procedure result type queries](#procedure-result-type-queries) — Derive one result’s type or a named record of several result types.
- [Dependent type-parameter defaults](#dependent-type-parameter-defaults) — Infer a compile-time type parameter from earlier inputs and callbacks.
- [Compile-time execution](#compile-time-execution) — Evaluate eligible known-argument `fn` calls automatically, or request execution with `#run`.
- [Runtime barriers and evaluation modes](#runtime-barriers-and-evaluation-modes) — Choose optional compile-time work and mark calls that should stay at runtime.
- [Compile-time switches](#compile-time-switches) — Select statements or values using `#switch` and leave inactive arms unchecked.

**Ownership and contracts**

- [Scoped owners](#scoped-owners) — Attach shallow end-of-scope cleanup to a local declaration with `~`.
- [Arbitrary-precision numbers](#arbitrary-precision-numbers) — Use predefined `Integer` and exact `Rational` values, literals and operators.
- [Allocation-free contracts](#allocation-free-contracts) — Check `@(no_alloc)` procedures or an entire `#+vet no_alloc` file.
- [Unused declaration contracts](#unused-declaration-contracts) — Mark declarations intentionally unused and reject later references to them.
- [Local vet suppressions](#local-vet-suppressions) — Silence selected vet advice on one declaration using named enum options.

**Basic package**

- [Importing basic helpers](#importing-basic-helpers) — Use the namespace, selected names or `import * "thor:basic"`.
- [Mapping and filtering](#mapping-and-filtering) — Allocate mapped and filtered slices with polymorphic callbacks inferred from the data.
- [Parallel mapping](#parallel-mapping) — Run a mapping callback with bounded workers and ordered results.
- [Sorted copies](#sorted-copies) — Sort an allocated copy while keeping the input unchanged.
- [Scoped arenas](#scoped-arenas) — Use one growing arena for allocations with the same block lifetime.
- [Deep graph copies](#deep-graph-copies) — Clone supported reachable data while preserving cycles, sharing and interior references.
- [Allocation-free value hashing](#allocation-free-value-hashing) — Hash supported logical contents without allocating.
- [Named zip views](#named-zip-views) — Give parallel slices field names without allocating a new array of records.
- [Printing conversion and reductions](#printing-conversion-and-reductions) — Use small printing, parsing and collection helpers with explicit ownership.

Also: [compiler commands](#compiler-commands), [vetting](#thor-vetting), [VS Code and TLS](#vs-code-and-tls), and [compatibility and development](#compatibility-and-development).

## Reading the examples

Examples omit `package main`. Unless `main` is shown, put imports at file scope and the remaining code inside `main :: proc() { ... }`; examples using `fmt` also import `core:fmt`. Place `#+…` file tags before `package main`. Odin examples importing `thor:…` use ordinary Odin support libraries and need `-collection:thor=/path/to/Thor/runtime`. Comments after the final call show the complete printed results; the Odin counterpart prints the same results. The tests compile the exact displayed snippets, run both languages, verify those comments and check rejection examples in checked and unchecked builds.

## Imports and bindings

### Selective and wildcard imports

Bring selected public names, or every public name, into the current file.

**Thor**

<!-- readme-example: selective-and-wildcard-imports:thor -->
```odin
import "core:fmt" / print, println
import _ "core:math" / sqrt
import * "core:strings"

print("Square root: ")
println(sqrt(f64(9)))
println("Has prefix:", has_prefix("Thor", "Th"))
// Square root: 3
// Has prefix: true
```

**Odin**

<!-- readme-example: selective-and-wildcard-imports:odin -->
```odin
import "core:fmt"
import "core:math"
import "core:strings"

fmt.print("Square root: ")
fmt.println(math.sqrt(f64(9)))
fmt.println("Has prefix:", strings.has_prefix("Thor", "Th"))
```

`import "core:fmt" / print, println` selects both procedures and retains the `fmt` namespace. `import _ "core:math" / sqrt` imports only `sqrt` without a `math` namespace. `import * "core:strings"` brings in every public name and retains the `strings` namespace. Names are file-local, are not re-exported, and obey privacy and collision checks. Selected mutable package variables require their namespace; wildcard imports preserve live variable access.

[Import rules](docs/language-guide.md#expressions-arrays-and-ranges).

### Immutable bindings and views

Use `:>` instead of `:=` to initialize once at runtime and prevent writes through that binding. This is separate from Odin’s `::` compile-time constants.

**Thor**

<!-- readme-example: immutable-bindings-and-views:thor -->
```odin
number := 3
value :> number+1
values :> [2]int{10, 20}
fmt.println(value, values[0])
// 4 10
```

**Odin**

<!-- readme-example: immutable-bindings-and-views:odin -->
```odin
number := 3
value := number+1
values := [2]int{10, 20}
fmt.println(value, values[0])
```

The Odin code reproduces the values, not the write restrictions. Thor also supports `name: Type > expression`. Records/fixed arrays protect directly stored subobjects; slices become read-only views. Existing writable aliases may still change shared backing storage. Pointer targets and dynamic-array contents retain their documented native behavior. `:>` is not a compile-time constant or a general ownership guarantee.

[Immutability, aliases and storage](docs/slice-access.md).

**Rejected in Thor**

<!-- readme-example: immutable-write-rejected:thor -->
```odin
value :> 3
value = 4
```

### Read-only and writable slice parameters

Enforce `#ro` readers and `#mut` writers, with inferred writable requirements.

**Thor**

<!-- readme-example: read-only-and-writable-slice-parameters:thor -->
```odin
copy_values :: proc(#ro source, #mut destination: []int) { copy(destination, source) }
source :> []int{1, 2}
destination := make([]int, 2)
defer delete(destination)
copy_values(source, destination)
fmt.println(destination)
// [1, 2]
```

**Odin**

<!-- readme-example: read-only-and-writable-slice-parameters:odin -->
```odin
copy_values :: proc(source, destination: []int) { copy(destination, source) }
source := []int{1, 2}
destination := make([]int, 2)
defer delete(destination)
copy_values(source, destination)
fmt.println(destination)
```

`#ro` prevents writes through the parameter, even with writable caller storage. `#mut` requires a writable view and applies to the next name only; omitted requirements are inferred from use and transitive calls. Returned/stored aliases retain permissions. These are Thor compile-time checks, not qualifiers carried in Odin’s native slice representation.

[Slice access and alias propagation](docs/slice-access.md).

## Data and expressions

### Inferred array literals

Infer a fixed array’s length and element type.

**Thor**

<!-- readme-example: inferred-array-literals:thor -->
```odin
values := [10, 20, 30]
fmt.println(values)
// [10, 20, 30]
```

**Odin**

<!-- readme-example: inferred-array-literals:odin -->
```odin
values := [?]int{10, 20, 30}
fmt.println(values)
```

Odin’s `[?]T{...}` already infers the length; Thor also infers the element type. Context supplies element types in arguments, returns, assignments and nested literals. Otherwise the first element establishes the type. Empty literals need context. A fixed array itself needs no heap cleanup.

[Array inference](docs/language-guide.md#expressions-arrays-and-ranges).

### Membership and intervals

Test collections and numeric or ordered intervals with `in` and `not_in`.

**Thor**

<!-- readme-example: membership-and-intervals:thor -->
```odin
values := [2, 4, 6]
needle := 4
fmt.println(needle in values, needle not_in values, needle in 0..<5)
// true false true
```

**Odin**

<!-- readme-example: membership-and-intervals:odin -->
```odin
values := [3]int{2, 4, 6}
needle := 4
found := false
for value in values { if value == needle { found = true; break } }
fmt.println(found, !found, 0 <= needle && needle < 5)
```

Works with arrays, slices, dynamic arrays, maps, bit sets and Thor sets. Intervals accept `..<` and `..=`; reversed intervals are empty. Operands run once in source order. String substring membership is not added. Ordered string intervals are supported.

[Membership and coercion rules](docs/language-guide.md#expressions-arrays-and-ranges).

### Membership in generic constraints

Express allowed types and constant values as membership constraints.

**Thor**

<!-- readme-example: membership-in-generic-constraints:thor -->
```odin
numeric :: proc(value: $T) -> T where T in [int, f64] { return value }
fmt.println(numeric(3))
// 3
```

**Odin**

<!-- readme-example: membership-in-generic-constraints:odin -->
```odin
numeric :: proc(value: $T) -> T where T == int || T == f64 { return value }
fmt.println(numeric(3))
```

Type membership compares exact type identities: aliases match, distinct types remain distinct. Constant value constraints can also use lists and intervals, such as `where N in 1..=8`.

[Generic membership](docs/language-guide.md#expressions-arrays-and-ranges).

### Multiline ternaries

Continue a ternary before its colon without a line-continuation escape.

**Thor**

<!-- readme-example: multiline-ternaries:thor -->
```odin
enabled := true
value := enabled ? 10
                 : 20
fmt.println(value)
// 10
```

**Odin**

<!-- readme-example: multiline-ternaries:odin -->
```odin
enabled := true
value := enabled ? 10 : 20
fmt.println(value)
```

Nested ternaries and comments preserve the normal lazy evaluation of the selected operand. Odin-style `value if condition else other` and `value when condition else other` remain available.

[Expression rules](docs/language-guide.md#expressions-arrays-and-ranges).

### Constant string repetition

Repeat a compile-time string with `*`.

**Thor**

<!-- readme-example: constant-string-repetition:thor -->
```odin
TEXT :: "ab" * 3
fmt.println(TEXT)
// ababab
```

**Odin**

<!-- readme-example: constant-string-repetition:odin -->
```odin
TEXT :: "ab" + "ab" + "ab"
fmt.println(TEXT)
```

Either operand order works. Both operands must be compile-time constants, and the count must be nonnegative. The result is borrowed constant storage, so do not delete it. Runtime `:>` bindings do not qualify as constants.

[Repetition limits and generics](docs/language-guide.md#string-repetition).

### Constant sequence concatenation

Concatenate constant arrays or same-type slices with `++`.

**Thor**

<!-- readme-example: constant-sequence-concatenation:thor -->
```odin
FIRST :: [1, 2]
SECOND :: [3, 4]
ALL :: FIRST ++ SECOND
fmt.println(ALL)
// [1, 2, 3, 4]
```

**Odin**

<!-- readme-example: constant-sequence-concatenation:odin -->
```odin
import "base:intrinsics"
FIRST :: [2]int{1, 2}
SECOND :: [2]int{3, 4}
ALL :: intrinsics.concatenate(FIRST, SECOND)
fmt.println(ALL)
```

Array lengths may differ; element types must agree. This is shallow, outer-sequence concatenation with no heap allocation. Arrays and slices cannot be mixed implicitly. `++` has addition precedence; it is not an increment operator and has no `++=` form.

[Concatenation rules](docs/language-guide.md#sequence-concatenation).

### Exponentiation

Use infix `**` for integer and floating-point powers.

**Thor**

<!-- readme-example: exponentiation:thor -->
```odin
fmt.println(2 ** 10, 2.0 ** -3)
// 1024 0.125
```

**Odin**

<!-- readme-example: exponentiation:odin -->
```odin
import "core:math"
power :: proc(base, exponent: int) -> int {
    if exponent < 0 { panic("negative integer exponent") }
    result := 1
    for _ in 0..<exponent { result *= base }
    return result
}
fmt.println(power(2, 10), math.pow(f64(2), f64(-3)))
```

Power associates right: `2 ** 3 ** 2` is 512. It binds more tightly than unary signs. Integer exponents must be nonnegative; invalid runtime exponents panic. Operand types must agree after normal literal coercion. Prefix `**values` retains Odin’s argument expansion.

[Power, overflow and generic rules](docs/language-guide.md#exponentiation).

### Struct operators

Apply arithmetic, bitwise operations, shifts, power and complement field by field. An untyped literal such as `{5, 6}` takes its struct type from the other operand or assignment target.

**Thor**

<!-- readme-example: struct-operators:thor -->
```odin
Pair :: struct { a, b: int }
left := Pair{1, 2}
right := Pair{3, 4}
result := left + right
result += {5, 6}
fmt.println(result.a, result.b)
// 9 12
```

**Odin**

<!-- readme-example: struct-operators:odin -->
```odin
Pair :: struct { a, b: int }
left := Pair{1, 2}
right := Pair{3, 4}
result := Pair{left.a + right.a, left.b + right.b}
result.a += 5
result.b += 6
fmt.println(result.a, result.b)
```

Operands must have the same struct type; untyped literals receive that context. Every non-padding field must be a supported self-contained fixed-size value with the operator. `_` padding is ignored. Pointer, string, slice, map, set, dynamic and Integer/Rational fields are excluded. Compound assignments, nested structs, generics and compile-time values follow the same rules.

[Complete operator restrictions](docs/struct-arithmetic.md).

### Typed loop bindings

Choose a numeric range’s value type or a sequence loop’s index type.

**Thor**

<!-- readme-example: typed-loop-bindings:thor -->
```odin
total := 0
for i: i16 in 0..<3 { total += int(i) }
values := [10, 20]
for value, (i: u8) in values { total += value + int(i) }
fmt.println(total)
// 34
```

**Odin**

<!-- readme-example: typed-loop-bindings:odin -->
```odin
total := 0
for i := i16(0); i < 3; i += 1 { total += int(i) }
values := [2]int{10, 20}
for value, index in values {
    if index > int(max(u8)) { panic("index does not fit") }
    i := u8(index)
    total += value + int(i)
}
fmt.println(total)
```

Thor range values and indices remain immutable. `&value` keeps native reference iteration. Index narrowing is checked even in unchecked builds; fixed arrays that cannot fit the index type are rejected. The Odin counter above reproduces the iteration but does not enforce Thor’s immutability.

[Typed range and index rules](docs/language-guide.md#typed-loops).

<a id="interpolation-switches-sets-and-negative-indices"></a>

### Formatted strings

Embed expressions in quoted or raw strings, with optional formatting.

**Thor**

<!-- readme-example: formatted-strings:thor -->
```odin
name := "Ada"
count := 3
text := f"Hello {name}: {count}"
defer delete(text)
fmt.println(text)
// Hello Ada: 3
```

**Odin**

<!-- readme-example: formatted-strings:odin -->
```odin
name := "Ada"
count := 3
text := fmt.aprintf("Hello %s: %d", name, count)
defer delete(text)
fmt.println(text)
```

Supports nested expressions, raw backticks, escaped braces, precision, grouping, Unicode padding and dynamic widths. Named results own their strings. Anonymous f-string arguments are cleaned up after the enclosing expression; `f!"..."` opts out for an API that takes ownership. Other referenced values retain their normal ownership.

[Formatting grammar](docs/interpolation-formatting.md) and [temporary ownership](docs/language-guide.md#interpolation-switches-sets-and-negative-indices).

### Formatting helpers

Use the native formatting support package directly when interpolation is not needed.

**Thor**

<!-- readme-example: formatting-helpers:thor -->
```odin
import f "thor:format"
text := f.format(1234567, grouping=true)
defer delete(text)
fmt.println(text)
// 1,234,567
```

**Odin**

<!-- readme-example: formatting-helpers:odin -->
```odin
import f "thor:format"
text := f.format(1234567, grouping=true)
defer delete(text)
fmt.println(text)
```

Both languages can call this ordinary Odin library. It supports grouping, width, alignment and Unicode fills; returned strings belong to the caller. The formatting package also supports explicit native fmt registration for logical numbers.

[Formatting and ownership](docs/language-guide.md#expressions-arrays-and-ranges), [big-number formatting](runtime/big/README.md#language-integration-helpers).

### Collection comprehensions

Build collections with an output expression, iteration and an optional filter.

**Thor**

<!-- readme-example: collection-comprehensions:thor -->
```odin
squares := [n*n for n in 0..<5 if n % 2 == 0]
defer delete(squares)
fmt.println(squares)
// [0, 4, 16]
```

**Odin**

<!-- readme-example: collection-comprehensions:odin -->
```odin
buffer := make([dynamic]int)
for n in 0..<5 { if n % 2 == 0 { append(&buffer, n*n) } }
squares := make([]int, len(buffer))
copy(squares, buffer[:])
delete(buffer)
defer delete(squares)
fmt.println(squares)
```

A bare runtime comprehension returns an owned slice. Explicit slice, dynamic-array, set and map constructors are supported. Leading `..` flattens one level or merges map entries. Copies are shallow; free separately owned elements as needed. Eligible constant comprehensions produce fixed arrays without runtime allocation.

[Constructors, flattening and compile-time arrays](docs/comprehensions.md).

### Sets

Use `set[T]`, deduplicating literals and mathematical set operations.

**Thor**

<!-- readme-example: sets:thor -->
```odin
values := set[int]{1, 2, 2}
defer delete(values)
values += 3
fmt.println(len(values), 2 in values)
// 3 true
```

**Odin**

<!-- readme-example: sets:odin -->
```odin
values := make(map[int]struct{})
defer delete(values)
values[1] = {}
values[2] = {}
values[3] = {}
_, found := values[2]
fmt.println(len(values), found)
```

Supports union, intersection, difference, symmetric difference, in-place updates, logical equality, membership and iteration. Iteration order is unspecified. The backing table is owned and copies are shallow; free it once. Native Odin can also use the ordinary `thor:sets` runtime API.

[Set operators and ownership](docs/language-guide.md#interpolation-switches-sets-and-negative-indices).

### Negative indices and slices

Count backward from the end of sequences.

**Thor**

<!-- readme-example: negative-indices-and-slices:thor -->
```odin
values := [10, 20, 30]
values[-1] += 1
fmt.println(values[-1], values[:-1])
// 31 [10, 20]
```

**Odin**

<!-- readme-example: negative-indices-and-slices:odin -->
```odin
values := [3]int{10, 20, 30}
values[len(values)-1] += 1
fmt.println(values[len(values)-1], values[:len(values)-1])
```

Applies to signed sequence indices and slice bounds, including writes and addressable access. Bounds retain their normal checks. Map keys, enum indices and pointer behavior keep their native meanings.

[Indexing and slice rules](docs/slice-access.md).

## Control flow

### Switch expressions

Use a switch as a value, including multiple results and contextual literals.

**Thor**

<!-- readme-example: switch-expressions:thor -->
```odin
number := 2
label := switch number { case 1: "one"; case 2: "two"; case: "other" }
fmt.println(label)
// two
```

**Odin**

<!-- readme-example: switch-expressions:odin -->
```odin
number := 2
label: string
switch number { case 1: label = "one"; case 2: label = "two"; case: label = "other" }
fmt.println(label)
```

Selectorless switches use Boolean conditions. Arms may return several values or forward a multi-result call. Value switches need a default unless an enum is exhaustively covered. Nonreturning calls may replace a value arm. `break` is allowed only when a switch is the entire RHS of ordinary assignment, where it skips that assignment’s stores.

[Switch values, cancellation and ownership](docs/language-guide.md#interpolation-switches-sets-and-negative-indices).

### Comparison switch cases

Match `<`, `<=`, `>`, `>=` or `!=` against the switch selector.

**Thor**

<!-- readme-example: comparison-switch-cases:thor -->
```odin
number := 12
result := 0
switch number { case < 10: result = 1; case < 20: result = 2; case: result = 3 }
fmt.println(result)
// 2
```

**Odin**

<!-- readme-example: comparison-switch-cases:odin -->
```odin
number := 12
result := 0
switch { case number < 10: result = 1; case number < 20: result = 2; case: result = 3 }
fmt.println(result)
```

Available in statements, value switches and `#switch`. The selector executes once, and matching uses the first applicable case. Equality keeps the ordinary `case value:` spelling; `case == value:` is rejected. Prefix comparisons require a value selector, not a type switch.

[Runnable comparison examples](examples/switch_comparisons/demo.thor).

<a id="optional-results-with-or-panic"></a>

### Optional results with or_panic

Unwrap a successful optional result or panic with the original source context.

**Thor**

<!-- readme-example: optional-results-with-or-panic:thor -->
```odin
import "core:strconv"
number := strconv.parse_int("42") or_panic
fmt.println(number)
// 42
```

**Odin**

<!-- readme-example: optional-results-with-or-panic:odin -->
```odin
import "core:strconv"
number, ok := strconv.parse_int("42")
if !ok { panic("parse_int failed") }
fmt.println(number)
```

Accepts a trailing success Boolean or nil-comparable error, optional lookups/assertions, and multiple successful values. The operand runs once. Failure reporting includes the original expression and location, even with assertions disabled. Successful owned results still need their normal cleanup.

[Unwrap and panic rules](docs/language-guide.md#optional-results-with-or_panic).

<a id="multiple-value-or-else-defaults"></a>

### Multiple-value or_else defaults

Provide a parenthesized fallback list with matching discarded positions.

**Thor**

<!-- readme-example: multiple-value-or-else-defaults:thor -->
```odin
load :: proc() -> (int, string, bool) { return 0, "", false }
_, text := load() or_else (_, "fallback")
fmt.println(text)
// fallback
```

**Odin**

<!-- readme-example: multiple-value-or-else-defaults:odin -->
```odin
load :: proc() -> (int, string, bool) { return 0, "", false }
_, text, ok := load()
if !ok { text = "fallback" }
fmt.println(text)
```

Fallbacks run only on failure, in source order. `_` must match discarded target positions; do not supply a real value for a discarded slot. Each retained slot keeps its own type and ownership. The fallback list itself allocates nothing.

[Complete fallback-list rules](docs/or-else-fallback-lists.md).

### Spread minimum and maximum

Reduce sequences with `min`/`max` and receive an optional presence flag.

**Thor**

<!-- readme-example: spread-minimum-and-maximum:thor -->
```odin
values := [7, 2, 9]
smallest, ok := min(..values[:])
fmt.println(smallest, ok, max(**values))
// 2 true 9
```

**Odin**

<!-- readme-example: spread-minimum-and-maximum:odin -->
```odin
values := [3]int{7, 2, 9}
smallest, largest, ok := 0, 0, false
for value in values {
    if !ok || value < smallest { smallest = value }
    if !ok || value > largest { largest = value }
    ok = true
}
fmt.println(smallest, ok, largest)
```

A slice spread returns `(value, ok)`; an empty input without scalar starting values yields zero/false. The flag can be omitted using native optional-result behavior. Fixed-array expansion returns one value and rejects empty arrays. Inputs execute once in order; the reduction allocates nothing and returned strings remain borrowed.

[Reduction details](docs/language-guide.md#spread-minimum-and-maximum).

### Using without a feature header

Use standalone `using` statements and `using` parameters directly.

**Thor**

<!-- readme-example: using-without-a-feature-header:thor -->
```odin
Person :: struct { age: int }
person := Person{42}
using person
fmt.println(age)
// 42
```

**Odin**

<!-- readme-example: using-without-a-feature-header:odin -->
```odin
#+feature using-stmt
Person :: struct { age: int }
person := Person{42}
using person
fmt.println(age)
```

Thor supplies Odin’s feature tag only when needed. Promoted fields retain their types, permissions and f-string context. Existing tags are preserved; an explicit `#+feature !using-stmt` still disables these forms. Embedded `using` fields keep their native behavior.

[Using compatibility](docs/language-guide.md#using-without-a-feature-header).

## Functions and compile-time execution

### Generic callback defaults

Infer an inline default callback from earlier generic inputs.

**Thor**

<!-- readme-example: generic-callback-defaults:thor -->
```odin
choose :: proc(a, b: $T, less := proc(x, y: T) -> bool { return x < y }) -> T {
    return a if less(a, b) else b
}
fmt.println(choose(7, 3))
// 3
```

**Odin**

<!-- readme-example: generic-callback-defaults:odin -->
```odin
less_than :: proc(x, y: $E) -> bool { return x < y }
choose :: proc(a, b: $T, less: proc(x, y: T) -> bool = less_than) -> T {
    return a if less(a, b) else b
}
fmt.println(choose(7, 3))
```

Callers may omit the callback or supply a compatible runtime value. Dependencies must be inferable from its parameter types. The literal cannot capture enclosing runtime parameters. Indirect procedure values still require ordinary explicit callback arguments.

[Default callback restrictions](docs/language-guide.md#generic-callback-defaults).

<a id="pure-functions-and-compile-time-execution"></a>

### Pure functions

Declare deterministic functions with checked read-only inputs and effects.

**Thor**

<!-- readme-example: pure-functions:thor -->
```odin
square :: fn(value: int) -> int { return value*value }
number := 4
fmt.println(square(number))
// 16
```

**Odin**

<!-- readme-example: pure-functions:odin -->
```odin
square :: proc(value: int) -> int { return value*value }
number := 4
fmt.println(square(number))
```

The Odin procedure has the same result but does not enforce purity. Thor rejects I/O, ambient mutable state, escaping mutation and unverified calls, including transitive effects. Supported scratch allocation is allowed: purity does not imply allocation-free execution. A `fn` that returns allocated data must construct or explicitly clone it. It can forward an owned helper result directly. To return a tail of an input slice, write `slice.clone(values[1:])`. Fixed SOAs and SOA slice inputs support read-only row and column access. Use `pure.destroy` for nested owned results.

[Purity and supported values](docs/purity-diagnostics.md), [result ownership](docs/language-guide.md#pure-functions-and-compile-time-execution).

### Implicit function returns

Return a value-returning `fn` body’s last expression automatically.

**Thor**

<!-- readme-example: implicit-function-returns:thor -->
```odin
quad :: fn(x: int) -> int { 4*x }
choose :: fn(x: int) -> int { switch x { case 0: 4; case: 8 } }
fmt.println(quad(3), choose(0))
// 12 4
```

**Odin**

<!-- readme-example: implicit-function-returns:odin -->
```odin
quad :: proc(x: int) -> int { return 4*x }
choose :: proc(x: int) -> int {
    switch x { case 0: return 4; case: return 8 }
}
fmt.println(quad(3), choose(0))
```

Early explicit returns remain valid. Trailing value-producing `switch`, `#switch` and `#partial switch` work, including multiple results. Assignments, loops, `if`/`when` statements and nested statement blocks do not imply returns. Ordinary `proc` bodies keep their explicit-return rules.

[Runnable implicit-return example](examples/implicit_returns/demo.thor).

### Pure callback types

Preserve a function’s purity contract through callback values and aliases.

**Thor**

<!-- readme-example: pure-callback-types:thor -->
```odin
twice :: fn(x: $T) -> T { return x*2 }
apply :: fn(x: int, callback: fn(int) -> int) -> int { return callback(x) }
callback: fn(int) -> int = twice
number := 3
fmt.println(apply(number, callback))
// 6
```

**Odin**

<!-- readme-example: pure-callback-types:odin -->
```odin
twice :: proc(x: $T) -> T { return x*2 }
apply :: proc(x: int, callback: proc(int) -> int) -> int { return callback(x) }
callback: proc(int) -> int = twice
number := 3
fmt.println(apply(number, callback))
```

Odin’s `proc` type does not carry Thor’s purity guarantee. Runtime-selected callbacks use ordinary procedure pointers, without runtime purity tags or adapters. Aliases, generic forwarding and storage preserve the Thor contract. Plain `proc` values cannot be converted back into `fn` types. A pure callback’s identity and arguments must be known for compile-time execution.

[Callback types, conversions and compile-time rules](docs/pure-callbacks.md).

### Automatic caller metadata

Expose caller expressions and locations as documented, non-overridable parameters.

**Thor**

<!-- readme-example: automatic-caller-metadata:thor -->
```odin
show :: proc(value: int, expression :> #caller_expression(value)) {
    fmt.println(expression, value)
}
number := 3
show(number)
// number 3
```

**Odin**

<!-- readme-example: automatic-caller-metadata:odin -->
```odin
show :: proc(value: int, expression := #caller_expression(value)) {
    fmt.println(expression, value)
}
number := 3
show(number)
```

Thor’s `:>` parameters cannot be supplied by the caller; Odin’s default above can be overridden. Also supports whole-call `#caller_expression` and `#caller_location`. Metadata remains explicit native parameters, is static data, and survives compatible aliases/callbacks. These directives belong in parameters, not directly in a body; foreign/exported interfaces cannot use automatic slots.

[Automatic parameter contracts](docs/attributes-and-directives.md#param-automatic-caller-metadata).

### Procedure result type queries

Derive one result’s type or a named record of several result types.

**Thor**

<!-- readme-example: procedure-result-type-queries:thor -->
```odin
import ti "thor:intrinsics"
calculate :: proc(x: int) -> (value: int, valid: bool) { return x, x > 0 }
Result :: ti.result_type_of(calculate(3))
result := Result{3, true}
fmt.println(result.value, result.valid)
// 3 true
```

**Odin**

<!-- readme-example: procedure-result-type-queries:odin -->
```odin
Result :: struct { value: int, valid: bool }
result := Result{3, true}
fmt.println(result.value, result.valid)
```

The query does not execute the call or its arguments. One result yields its exact type; several yield a struct with result names, or `v0`, `v1`, etc. No-result calls are errors. The Odin example defines the record manually; Thor derives and checks it against the actual procedure.

[Result-type query reference](runtime/intrinsics/README.md).

### Dependent type-parameter defaults

A compile-time type parameter can default to a type derived from earlier parameters. Here, `Result` is inferred from the callback’s results.

**Thor**

<!-- readme-example: dependent-type-parameter-defaults:thor -->
```odin
import ti "thor:intrinsics"

Job :: struct { left, right: int }
calculate :: proc(a, b: int) -> (sum, product: int) {
    return a+b, a*b
}

first :: proc(jobs: []$J, callback: $F,
    $Result: typeid = ti.result_type_of(callback(**J{})),
) -> Result {
    return compress_values(callback(**jobs[0]))
}

result := first([]Job{{2, 3}}, calculate)
fmt.println(result.sum, result.product)
// 5 6
```

`result_type_of` inspects the callback without executing it. Native Odin’s `**` expands the job’s fields, and `compress_values` packs the returned values into `Result`. This example expects a nonempty slice.

**Odin**

<!-- readme-example: dependent-type-parameter-defaults:odin -->
```odin
Job :: struct { left, right: int }
Result :: struct { sum, product: int }
calculate :: proc(a, b: int) -> (sum, product: int) {
    return a+b, a*b
}

first :: proc(jobs: []$J, callback: $F, $Result: typeid) -> Result {
    return compress_values(callback(**jobs[0]))
}

result := first([]Job{{2, 3}}, calculate, Result)
fmt.println(result.sum, result.product)
```

The Odin version defines and passes `Result` explicitly. Thor supplies the omitted default at the call site, after resolving the earlier parameters. This is the mechanism used by [parallel mapping](#parallel-mapping) to infer its output element type.

[Dependent result-type defaults](runtime/parallel/README.md#result-type-queries).

### Compile-time execution

Eligible `fn` calls with constant inputs can run during compilation. `#run` explicitly requests compile-time execution of an eligible call, including an ordinary `proc`; the same purity restrictions apply.

**Thor**

<!-- readme-example: compile-time-execution:thor -->
```odin
square :: fn(x: int) -> int { return x*x }
ordinary :: proc(x: int) -> int { return x+1 }
SIZE :: square(4)
NEXT :: #run ordinary(SIZE)
values: [SIZE]int
fmt.println(len(values), NEXT)
// 16 17
```

There is no equivalent Odin language facility for executing these procedure calls as constants. Thor verifies dependencies and effects, executes native helpers, and caches contents-only results. Required work cannot silently fall back to runtime. Supported allocated results materialize independent runtime owners; constants use their documented borrowed-storage rules. Runtime bindings, including `:>`, are not automatically constant inputs. Debug builds leave optional calls at runtime; required constants and `#run` still evaluate. Evaluation currently uses the host target.

[Execution, caches and ownership](docs/language-guide.md#pure-functions-and-compile-time-execution).

### Runtime barriers and evaluation modes

Choose optional compile-time work and mark calls that should stay at runtime.

**Thor**

<!-- readme-example: runtime-barriers-and-evaluation-modes:thor -->
```odin
square :: fn(x: int) -> int { return x*x }
value := #runtime square(4)
fmt.println(value)
// 16
```

**Odin**

<!-- readme-example: runtime-barriers-and-evaluation-modes:odin -->
```odin
square :: proc(x: int) -> int { return x*x }
value := square(4)
fmt.println(value)
```

`#runtime` blocks Thor’s optional evaluator; Odin may still optimize the generated call. `--comptime=required` keeps required work and explicit `#run`; `normal` also folds eligible `fn` calls; `aggressive` also folds eligible allocation-free ordinary calls; `override` additionally overrides `#runtime`. None permits impure compile-time execution.

[Mode eligibility and failure behavior](docs/comptime-modes.md).

### Compile-time switches

Select statements or values using `#switch` and leave inactive arms unchecked.

**Thor**

<!-- readme-example: compile-time-switches:thor -->
```odin
MODE :: 2
VALUE :: #switch MODE { case 1: 10; case: 20 }
fmt.println(VALUE)
// 20
```

**Odin**

<!-- readme-example: compile-time-switches:odin -->
```odin
MODE :: 2
VALUE :: 10 when MODE == 1 else 20
fmt.println(VALUE)
```

Statement-form `#switch` corresponds to an Odin `when` / `else when` chain. Selectorless arms use compile-time Boolean conditions in order. Value forms need their required results/default. Native Odin `when` remains available directly; Thor does not add a `#if` preprocessor.

[Compile-time branch selection](docs/attributes-and-directives.md#dir-switch).

## Ownership and contracts

### Scoped owners

Declare a local with `~:=` or immutable `~:>` to attach shallow end-of-scope cleanup.

**Thor**

<!-- readme-example: scoped-owners:thor -->
```odin
values ~:= make([]int, 3)
values[0] = 7
fmt.println(values)
// [7, 0, 0]
```

**Odin**

<!-- readme-example: scoped-owners:odin -->
```odin
values := make([]int, 3)
defer delete(values)
values[0] = 7
fmt.println(values)
```

Prefer `values ~:=` and immutable `values ~:>`. The `values~ :=` / `values~ :>` spellings remain supported. Cleanup reads the final binding and retains the correct allocator. Supported owners include pointers, slices, strings, dynamic arrays, maps, sets and logical numbers. Cleanup is shallow; nested allocations and graph roots need their own cleanup. Tracked escaping owners/views are rejected, but this is not a complete memory-safety proof.

[Supported owners, escapes and allocator rules](docs/scoped-owners.md).

### Arbitrary-precision numbers

Use predefined `Integer` and exact `Rational` values, literals and operators.

**Thor**

<!-- readme-example: arbitrary-precision-numbers:thor -->
```odin
import "thor:big"
whole: Integer = 123456789012345678901234567890
fraction: Rational = 1.25
defer delete(whole)
defer delete(fraction)
a := big.to_string(whole)
b := big.to_string(fraction)
defer delete(a)
defer delete(b)
fmt.println(a, b)
// 123456789012345678901234567890 5/4
```

**Odin**

<!-- readme-example: arbitrary-precision-numbers:odin -->
```odin
import "thor:big"
whole, ok := big.parse_integer("123456789012345678901234567890")
if !ok { panic("invalid integer") }
fraction := big.fraction(5, 4)
defer big.destroy(&whole)
defer big.destroy(&fraction)
a := big.to_string(whole)
b := big.to_string(fraction)
defer delete(a)
defer delete(b)
fmt.println(a, b)
```

The Odin version uses Thor’s ordinary Odin runtime library. Thor adds exact unquoted literals, constructors, arithmetic/comparisons, Integer bitwise operations, read-only numeric components, formatting and `fn`/`#run` support. Direct numeric copies own independent storage and replacement cleans up the old value; whole aggregates remain shallow. Delete runtime owners, not borrowed constants. Native Odin must clone owning numeric headers explicitly.

[Language and ownership reference](docs/arbitrary-precision.md), [native runtime API](runtime/big/README.md).

### Allocation-free contracts

Check `@(no_alloc)` procedures or an entire `#+vet no_alloc` file.

**Thor**

<!-- readme-example: allocation-free-contracts:thor -->
```odin
@(no_alloc)
sum :: proc(values: []int) -> int {
    total := 0
    for value in values { total += value }
    return total
}
values := [1, 2, 3]
fmt.println(sum(values[:]))
// 6
```

**Odin**

<!-- readme-example: allocation-free-contracts:odin -->
```odin
sum :: proc(values: []int) -> int {
    total := 0
    for value in values { total += value }
    return total
}
values := [3]int{1, 2, 3}
fmt.println(sum(values[:]))
```

Odin executes the same algorithm without this checked contract. Thor checks ordinary explicit/transitive call paths conservatively; runtime-capacity guesses do not exempt operations such as `append`. Fatal panic/trap reporting is excluded. Supported fixed-buffer I/O and formatting can be allocation-free. No runtime allocation tracker is added by the contract.

[Contract and panic-path boundaries](docs/no-alloc.md), [fixed-buffer I/O](examples/no_alloc_io/demo.thor).

**Rejected in Thor**

<!-- readme-example: no-alloc-rejected:thor -->
```odin
@(no_alloc)
allocate :: proc() { values := make([]int, 2); defer delete(values) }
allocate()
```

### Unused declaration contracts

Mark declarations intentionally unused and reject later references to them.

**Thor**

<!-- readme-example: unused-declaration-contracts:thor -->
```odin
@(unused)
reserved :: 64
fmt.println("ready")
// ready
```

**Odin**

<!-- readme-example: unused-declaration-contracts:odin -->
```odin
reserved :: 64
_ = reserved
fmt.println("ready")
```

`@(unused)` suppresses the tagged declaration’s unused warning and makes reads, writes, aliases, address-taking and type queries on it errors. Its initializer still runs and retains normal cleanup requirements. The Odin discard only suppresses unused advice; it does not forbid later use.

[Unused attribute rules](docs/attributes-and-directives.md#attr-unused).

**Rejected in Thor**

<!-- readme-example: unused-use-rejected:thor -->
```odin
@(unused)
reserved :: 64
fmt.println(reserved)
```

### Local vet suppressions

Silence selected vet advice on one declaration using named enum options.

**Thor**

<!-- readme-example: local-vet-suppressions:thor -->
```odin
@(vet_ignore={.immutable})
value := 4
fmt.println(value)
// 4
```

**Odin**

<!-- readme-example: local-vet-suppressions:odin -->
```odin
value := 4
fmt.println(value)
```

Odin has no equivalent for Thor’s vet categories; its block shows the same executable code. `@(vet_ignore={.immutable, .pure})` selects multiple checks. Dedicated `.lifetime_unknown` and `.leak_unknown` options acknowledge intentional unverified cases; definite memory findings retain their own rules. Suppressions do not bypass type checking, immutability, purity or `no_alloc` contracts. TLS completes the option names after `.`.

[Suppression options](docs/attributes-and-directives.md#attr-vet_ignore) and [vet policy](docs/compiler-guide.md#thor-vetting).

<a id="basic-library"></a>

## Basic package

`import "thor:basic"` exposes bundled conveniences. This package uses Thor syntax and is consumed with Thor; its ordinary Odin support packages can also be used directly from Odin. All allocating results retain their documented ownership.

### Importing basic helpers

Use the package namespace, select individual names, or import all public helpers.

**Thor**

<!-- readme-example: basic-imports:thor -->
```odin
import * "thor:basic"
print("Hello, Thor!")
// Hello, Thor!
```

**Odin**

<!-- readme-example: basic-imports:odin -->
```odin
import "core:fmt"
fmt.println("Hello, Thor!")
```

With `import * "thor:basic"`, both `print(...)` and `basic.print(...)` work. For selected helpers, use `import "thor:basic" / print, range`; for namespace-only access, use `import "thor:basic"`. The Odin example uses `fmt` for the same output; `thor:basic` itself is a Thor package.

[Basic API and ownership](runtime/basic/README.md).

### Mapping and filtering

Allocate mapped and filtered slices with polymorphic callbacks inferred from the data.

**Thor**

<!-- readme-example: mapping-and-filtering:thor -->
```odin
import "thor:basic"
twice :: proc(x: $T) -> T { return x*2 }
positive :: proc(x: $T) -> bool { return x > 0 }
values := []int{-1, 0, 3}
mapped := basic.fmap(values, twice)
kept := basic.filter(values, positive)
defer delete(mapped)
defer delete(kept)
fmt.println(mapped, kept)
// [-2, 0, 6] [3]
```

**Odin**

<!-- readme-example: mapping-and-filtering:odin -->
```odin
values := []int{-1, 0, 3}
mapped := make([]int, len(values))
buffer := make([dynamic]int)
for value, i in values {
    mapped[i] = value*2
    if value > 0 { append(&buffer, value) }
}
kept := make([]int, len(buffer))
copy(kept, buffer[:])
delete(buffer)
defer delete(mapped)
defer delete(kept)
fmt.println(mapped, kept)
```

Argument order is data first: `fmap(values, callback)` and `filter(values, predicate)`. Order is preserved; results own their outer slices and copies are shallow. `fmap` retains the input element type. Empty inputs are supported, and no extra callback annotation is needed for polymorphic procedures.

[Basic API](runtime/basic/README.md).

### Parallel mapping

Run a mapping callback with bounded workers and ordered results.

**Thor**

<!-- readme-example: parallel-mapping:thor -->
```odin
import "thor:basic"

Job :: struct { left, right: int }
calculate :: proc(a, b: $T) -> (sum, product: T) {
    return a+b, a*b
}

main :: proc() {
    jobs := []Job{{2, 3}, {4, 5}}
    results := basic.pmap(jobs, calculate)
    defer delete(results)

    for row in results {
        fmt.println(row.sum, row.product)
    }
    // 5 6
    // 9 20
}
```

Each job supplies two arguments. `results` is an allocated slice of structs with `sum` and `product` fields, inferred from the named results. The polymorphic callback needs no extra annotations.

**Odin**

<!-- readme-example: parallel-mapping:odin -->
```odin
import "core:thread"

Result :: struct { sum, product: int }
Work :: struct { left, right: int, output: Result }

calculate :: proc(a, b: int) -> (sum, product: int) {
    return a+b, a*b
}

worker :: proc(t: ^thread.Thread) {
    work := cast(^Work)t.data
    work.output.sum, work.output.product = calculate(work.left, work.right)
}

main :: proc() {
    jobs := [?]Work{{left=2, right=3}, {left=4, right=5}}
    threads: [2]^thread.Thread

    for &job, i in jobs {
        threads[i] = thread.create(worker)
        if threads[i] == nil { panic("thread creation failed") }
        threads[i].data = &job
        thread.start(threads[i])
    }
    for t in threads { thread.join(t); thread.destroy(t) }

    results := make([]Result, len(jobs))
    defer delete(results)
    for job, i in jobs { results[i] = job.output }
    for row in results { fmt.println(row.sum, row.product) }
}
```

The small Odin example shows explicit threads, not pmap’s scheduler implementation. `pmap` uses bounded workers, joins before returning, preserves result order, and retains a runtime callback fallback. One callback input receives the whole element; several use struct fields in order; zero inputs use empty structs. One result yields `[]T`; several yield result structs with named fields or `v0`, `v1`, etc.; no results cannot be assigned. `result_type=Result` overrides result names/type mapping. Inputs and results are shallow; worker allocations and shared mutable state need their normal ownership/synchronization. Eligible `#run` mappings use the compile-time execution path.

[Inputs, specialization, scheduling, RNG and allocators](runtime/parallel/README.md).

### Sorted copies

Sort an allocated copy while keeping the input unchanged.

**Thor**

<!-- readme-example: sorted-copies:thor -->
```odin
import "thor:basic"
values := []int{3, 1, 2}
ordered := basic.sorted(values)
defer delete(ordered)
fmt.println(ordered, values)
// [1, 2, 3] [3, 1, 2]
```

**Odin**

<!-- readme-example: sorted-copies:odin -->
```odin
import "core:slice"
values := []int{3, 1, 2}
ordered := make([]int, len(values))
copy(ordered, values)
slice.sort(ordered)
defer delete(ordered)
fmt.println(ordered, values)
```

Pass a comparator for records or alternate ordering. The returned outer slice is owned by the caller; referenced element storage is still shallow.

[Sorting API](runtime/basic/README.md).

### Scoped arenas

Use one growing arena for allocations with the same block lifetime.

**Thor**

<!-- readme-example: scoped-arenas:thor -->
```odin
import "thor:basic"
{
    context.allocator = basic.scoped_arena()
    values := make([]int, 10)
    fmt.println(len(values))
    // 10
}
```

**Odin**

<!-- readme-example: scoped-arenas:odin -->
```odin
import "core:mem/virtual"
{
    arena: virtual.Arena
    err := virtual.arena_init_growing(&arena)
    if err != nil { panic("arena initialization failed") }
    defer virtual.arena_destroy(&arena)
    context.allocator = virtual.arena_allocator(&arena)
    values := make([]int, 10)
    fmt.println(len(values))
}
```

The helper uses Odin’s caller-scope deferred cleanup. Allocations must not escape the block. Early returns, breaks and continues perform normal scoped cleanup; explicit allocator choices and `context.temp_allocator` remain separate. This is a library convenience, not general lifetime proof.

[Arena lifetime and allocator rules](runtime/basic/README.md#scoped-arena-allocations).

### Deep graph copies

Clone supported reachable data while preserving cycles, sharing and interior references.

**Thor**

<!-- readme-example: deep-graph-copies:thor -->
```odin
import "thor:basic"
source := [1, 2]
cloned, err := basic.deepclone(&source)
if err != .None { panic("clone failed") }
defer { release_err := basic.deepfree(cloned); if release_err != .None { panic("free failed") } }
cloned^[0] = 9
fmt.println(source[0], cloned^[0])
// 1 9
```

**Odin**

<!-- readme-example: deep-graph-copies:odin -->
```odin
import "thor:graph"
source := [2]int{1, 2}
cloned, err := graph.deepclone(&source)
if err != .None { panic("clone failed") }
defer { release_err := graph.deepfree(cloned); if release_err != .None { panic("free failed") } }
cloned^[0] = 9
fmt.println(source[0], cloned^[0])
```

The Odin version calls the same ordinary Odin support package. `deepclone` returns an owned graph root plus an error; release it once with `deepfree` using the matching allocator. Preserve its documented ownership boundaries and reachability. `~` shallow cleanup is not a substitute for `deepfree`.

[Supported graph shapes and ownership](docs/deepclone.md).

### Allocation-free value hashing

Hash supported logical contents without allocating.

**Thor**

<!-- readme-example: allocation-free-value-hashing:thor -->
```odin
import "thor:basic"
first := [1, 2]
second := [1, 2]
fmt.println(basic.hash(first) == basic.hash(second))
// true
```

**Odin**

<!-- readme-example: allocation-free-value-hashing:odin -->
```odin
import hashing "thor:hash"
first := [2]int{1, 2}
second := [2]int{1, 2}
fmt.println(hashing.hash(first) == hashing.hash(second))
```

The native Odin package provides the same API. Struct padding, allocator metadata and spare capacity are ignored. Pointers hash addresses; unsupported values and container cycles are rejected. `try_hash` returns a recoverable error. Algorithms include XXH3_64, XXH64 and FNV64a.

[Hashing contract](runtime/hash/README.md).

### Named zip views

Give parallel slices field names without allocating a new array of records.

**Thor**

<!-- readme-example: named-zip-views:thor -->
```odin
import "thor:basic"
names := []string{"Ada", "Lin"}
ages := []int{36, 28}
people := basic.zip(name=names, age=ages)
for person in people { fmt.println(person.name, person.age) }
// Ada 36
// Lin 28
```

**Odin**

<!-- readme-example: named-zip-views:odin -->
```odin
names := []string{"Ada", "Lin"}
ages := []int{36, 28}
people := soa_zip(name=names, age=ages)
for person in people { fmt.println(person.name, person.age) }
```

`basic.zip` is an alias for native `soa_zip`. It borrows the columns, stops at the shortest column, and retains their slice permissions. Thor also carries those field types and readonly properties through `using` and interpolations.

[Basic library ownership](runtime/basic/README.md).

### Printing conversion and reductions

Use small printing, parsing and collection helpers with explicit ownership.

**Thor**

<!-- readme-example: printing-conversion-and-reductions:thor -->
```odin
import "thor:basic"
number := basic._int("42") or_panic
values := basic.range(1, 4)
defer delete(values)
basic.print(number, basic.sum(values), basic.and(true, false))
// 42 6 false
```

**Odin**

<!-- readme-example: printing-conversion-and-reductions:odin -->
```odin
import "core:strconv"
number, ok := strconv.parse_int("42")
if !ok { panic("invalid integer") }
values := make([]int, 3)
for i in 0..<3 { values[i] = i+1 }
defer delete(values)
total := 0
for value in values { total += value }
fmt.println(number, total, true && false)
```

Allocating helpers use their supplied allocator or `context.allocator`. Boolean reduction procedures may stop traversing values early, but ordinary call arguments are all evaluated before the procedure starts; use `&&`/`||` for lazy expressions. The table below groups the remaining API, with full contracts in the linked reference.

[Complete basic API](runtime/basic/README.md).

### More basic helpers

| Family | Helpers and behavior |
| --- | --- |
| Output and reflection | `print`, `type`, `size`; separators/terminators and value type/byte-size queries. |
| Parsing and conversion | `_int`, `_uint`, `_i8`…`_i128`, `_u8`…`_u64`, `_f32`, `_f64`, `_bool`, `_string`; checked parsing or owned text. |
| I/O | `input`, `read`, `write`; line/file input and file output with status results. Free owned input text. |
| Strings | `split`, `join`; split returns borrowed views in an owned outer slice, join returns owned text. |
| Collections | `swap` exchanges pointed-to values; `clone` is shallow; `to_set`/`to_slice` allocate outer containers; `range` allocates integer sequences. |
| Reductions | `and`, `or`, `sum`; Boolean identities and native numeric arithmetic. |
| Unicode | `chr`, `ord`; single Unicode-scalar conversions with input restrictions. |
| Randomness | `random(T, lo, hi, gen)`; inclusive bounds and an optional generator. |
| Tables | `table`; owned formatted text for struct rows, with heading tags and alignment. |

See the [complete API and ownership rules](runtime/basic/README.md). Pass custom allocators consistently when constructing and freeing owned data. Basic convenience functions do not introduce general garbage collection.

<a id="commands"></a>

<a id="compilation-and-odin-flags"></a>

## Compiler commands

Commands accept a package directory or a single file. Original sources are preserved; generated Odin and caches live separately.

| Command | Purpose |
| --- | --- |
| `thor new app` | Create a project with editor/run/debug configuration. |
| `thor check .` | Check Thor contracts and native Odin types. |
| `thor build . -o:speed` | Build an executable or a selected native build mode. |
| `thor run . -- args` | Build and run, forwarding program arguments after `--`. |
| `thor test .` | Build and run native `@(test)` procedures. |
| `thor transpile .` | Emit the generated Odin tree for inspection. |
| `thor vet .` | Check conventions, contracts and memory findings. |
| `thor migrate path/to/odin/project` | Create and validate a Thor copy of an Odin project. |
| `thor publish .` | Produce a distributable application/archive. |
| `thor help` / `thor help build -full` | Show command and extended flag help. |

Native flags keep Odin’s `-flag:value` spelling, including `-debug`, `-o:speed`, `-target:…`, `-define:NAME=value` and collection flags. Thor options such as `--comptime=aggressive` control the transpiler. `--run-timeout SEC` bounds compile-time execution. Diagnostics and debugger maps point back to original source.

The [compiler guide](docs/compiler-guide.md) covers outputs, caches, flags, diagnostics and project discovery. See [publishing](docs/publishing.md), [migration](docs/odin-migration.md) and [memory debugging](docs/memory-debugging.md) for complete workflows.

## Thor vetting

```sh
thor vet .
thor vet . --apply
thor build . --vet
thor vet . --unverified
thor build . --vet --unverified=error
```

Vet suggests immutable declarations and pure functions, reports unused imports/types/procedures/locals, and checks supported lifetime, leak, allocator, buffer and other memory hazards. `--apply` performs verified corrections; uncertain configuration or escaping-reference edits stay manual. It can remove unused imports and fix unused iterator bindings, but it does not delete unused types, procedures or ordinary locals.

Definite findings are shown normally. Unverified analysis is summarized by default; `--unverified` shows individual explanations, and `--unverified=error` makes uncertainty fail the command. These options also work with `build --vet`. Passing vet is not a general memory-safety guarantee. Odin’s native vet flags and supported `#+vet` options remain available; `no_alloc` additionally establishes its explicit contract.

See [vet checks and suppression policy](docs/compiler-guide.md#thor-vetting), [immutable advice](docs/immutable-vet.md) and [lifetime/ownership analysis](docs/lifetime-vet.md).

<a id="create-run-and-debug-a-project"></a>

## VS Code and TLS

`--vscode --profile Thor` installs the Thor extension/TLS, standard Odin OLS and CodeLLDB. Open a project in that profile:

```sh
code --profile Thor /path/to/project
```

TLS provides completion, hover with type sizes and target-dependence labels, signature help, definitions, references/rename, semantic highlighting and compiler-backed diagnostics for unsaved text. Thor colors are optional and apply to Thor tokens; normal OLS owns `.odin` files.

Run/debug shortcuts are **Cmd+R / Cmd+Shift+R** on macOS and **Ctrl+R / Ctrl+Shift+R** elsewhere. Debugging maps breakpoints and stepping to original `.thor` lines. Trust your own project when VS Code asks, and reload the window after extension/server updates. Debugger capabilities retain the documented native platform limitations.

See the [editor guide](editors/vscode/README.md), [debugger guide](editors/vscode/DEBUGGING.md) and [TLS notes](tls/THOR.md).

<a id="implemented-syntax"></a>

<a id="run-the-demo"></a>

<a id="compiler-and-development"></a>

<a id="tests-and-rejection-examples"></a>

<a id="implementation-and-current-boundary"></a>

<a id="compiler-diagnostics"></a>

## Compatibility and development

Thor is experimental; [platform validation](docs/validation/installer-compatibility-2026-11-26.md) records tested configurations and limits. Native `.odin` dependencies retain Odin semantics; `.thor` files add the documented extensions. Unsafe native operations and unverified foreign contracts remain outside any general safety guarantee. Compile-time execution currently runs on the host; cross-target evaluation has documented limits.

Try the [commented tour](examples/demo/demo.thor) with `thor run examples/demo`. Feature-specific runnable programs live under [examples](examples). The [language guide](docs/language-guide.md), [language specification](docs/language-spec.md), [attribute/directive inventory](docs/attributes-and-directives.md) and [compiler guide](docs/compiler-guide.md) provide detailed contracts and limits.

For compiler development:

```sh
python3 tools/build_compiler.py
python3 tools/build_tls.py
THOR_TEST_PREBUILT=1 python3 tools/test_compiler.py --match tests.test_readme_examples
```

The README’s displayed examples are the test inputs, with a small manifest supplying wrapper context and expected results. Paired examples are run in checked and unchecked builds; compile-time examples also exercise all four evaluation modes. New extensions should add a linked feature entry and a tested example. See [development instructions](DEVELOPMENT.md), [test integration](tests/integration/README.md), [storage retention](docs/storage.md) and [Python reference retirement](docs/python-retirement.md).

Thor-owned code uses [0BSD](LICENSE). Incorporated software retains its own licenses; see [NOTICE](NOTICE).
