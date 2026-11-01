# Diagnostic review — 2026-11-01

This is a focused review of indexing, contextual literals and adjacent lowered
expressions. Thirteen small programs were checked against the real compiler.
All five follow-up improvements below are implemented in **Thor 0.29.2** and
covered by real compiler and TLS transport tests.

## Implemented: indexing a type instead of an array variable

```odin
Rows :: [3]struct {x: int}
main :: proc() {
    i := 1
    Rows[i] = {1}
}
```

The declaration names a type. It does not allocate or initialize an array value.
Previously, a runtime index could produce a series of generated type-trait and
polymorphic-type errors. Thor now leaves conclusively known type operands out of
the value-indexing helpers and translates Odin's rejection into:

```text
Error: Cannot index 'Rows': it names a type, not an array value.
To create a zero-initialized array variable, use 'Rows: [3]struct {x: int}'.
Note: 'Rows' is declared as a type here.
```

The primary range covers the type operand at its use. The note points to the
original declaration, including imported and unsaved source. The terminal prints
the note and source excerpt; the JSON diagnostic adds a `related` array of
`{pos, message}` entries. TLS exposes those notes as LSP `relatedInformation`, so
VS Code can navigate to the declaration. Notes do not increase the error count.
`--raw-diagnostics` retains the native backend output.

The same behavior covers reads, writes, compound assignments and slices. It does
not reinterpret arbitrary unresolved expressions as types. Conditional bindings,
generic formals and runtime `typeid` values remain under native checking. Inactive
`when` branches and uninstantiated generic bodies retain native laziness.

Odin itself may infer an incorrect contextual aggregate type after rejecting the
destination. Filtering is limited to implicit compound arity/type cascades from
the same invalid assignment target. Generated type-query failures enclosing the
same proven error in an f-string or switch expression are also filtered.
Independent diagnostics, other statements, and failures inside explicitly typed
nested compounds remain visible.

## Follow-up improvements implemented in 0.29.2

### 1. Suppress proven indexing cascades after an undeclared name

```odin
i := 1
missing[i] = 2
```

Before 0.29.2: eight errors, starting with generated `type_is_array` / `type_of`
failures; the useful `Undeclared name: missing` is buried among them. An undeclared
index in `values[missing]` similarly produces the name error and an additional
`normalize` overload error.

Implemented: retain the original undeclared-name error and suppress only generated
inference/normalization failures tied to that same unresolved operand. Do not
suppress unrelated errors merely because they share a line or statement.

The same handling covers a missing record field used as an index. Tests preserve
multiple independent errors on the same line and in separate slice bounds, and
cover indexing inside switches and f-strings.

### 2. Explain invalid index and slice-bound types as language operations

```odin
values := [1, 2, 3]
i: f64 = 1
_ = values[i]
```

Before 0.29.2: `No procedures or ambiguous call for procedure group 'Thor
expression.normalize' that match with the given arguments`, followed by overload
details. String-valued slice bounds have the same problem.

Now:

```text
Error: Sequence indices must have an integer type; 'i' has type 'f64'.
```

For slicing, the message identifies the lower or upper bound. Native enum-array and
map-key rules are preserved; a noninteger map key can be completely valid. Tests
cover wide unsigned indices, distinct types, original library locations, generic
parameters and constant fractional bounds.

The message explains the existing contract without changing accepted index types
or adding implicit conversions.

### 3. Show original expressions in result-type mismatch messages

```odin
values := [1, 2, 3]
i := 1
result: string = values[i]
```

Before 0.29.2: the message correctly reported `int` versus `string`, but quoted a long
generated expression containing captures and `sequence.element` type witnesses.

Now the native diagnostic quotes the original expression:

```text
Error: Cannot assign value 'values[i]' of type 'int' to 'string' in a variable declaration
```

Recorded emission boundaries and original AST spans reconstruct the quoted
expression. Token-boundary matching also handles Odin's pretty-printed
whitespace while preserving string/rune contents and identifier boundaries.
The same presentation rule covers lowered calls, slices, switches, interpolation
and numeric expressions. Specific errors in individual switch arms keep their
more precise arm expressions and ranges.

Actual result/destination types and original source ranges remain available.

### 4. Avoid generated builtin queries being captured by user declarations

```odin
type_of :: proc() -> [2]int { return {1, 2} }
main :: proc() {
    i := 1
    _ = type_of()[i]
}
```

Before 0.29.2: generated calls to `type_of(value)` resolved to the user's zero-argument
procedure, producing `Too many arguments for 'type_of', expected 0 arguments, got
1`. The source call has no arguments. The constant-index form does not need the
helper and works.

Implemented: when a user binding can shadow a generated native name, Thor imports
Odin's `base:builtin` namespace under a private generated alias and qualifies
only the affected generated native calls. User-written references, literals,
comments, selectors and renamed/specialized user call targets retain their
bindings. The resolver also distinguishes user `type_of` procedures from the
native query, including selected imports.

Tests cover local, parameter, same-package and imported shadows, user type names,
f-string allocation/cleanup, declared pure functions and cold/cached `#run`.
Source-map segments, expression boundaries and debugger regions shift with the
inserted qualifiers. User procedures named `type_of` keep runtime argument
breakpoints and stepping; they are not mistaken for unevaluated type queries.
No new reserved names, frontend passes or compiler subprocesses are introduced.
Files without relevant shadowing need no additional builtin import.

### 5. Add operation context to inline membership conversion failures

```odin
record := struct {x: int} {1}
_ = record in [1, 2]
```

Before 0.29.2: two separate native messages about converting `1` and `2` to the
record's struct type. This follows Thor's contextual membership-list typing, but
does not explain why the literals were being converted to a struct.

Now each native conversion diagnostic keeps its element range and conversion
details and adds:

```text
In this membership test, each list element must be comparable with 'record' (type 'struct {x: int}').
```

A related source location identifies the needle. Native errors inside an
explicitly typed list value or nested call retain their own context. `in` and
`not_in` use the same behavior; useful per-element information is preserved.

TLS carries the explanation and clickable needle note through unsaved changes
and clears the diagnostics after a fix.

## Cases that already read clearly

The review also checked nonindexable struct values, incorrect switch result
types, empty array literals without context, invalid Set key types, unordered
spread `min` elements and mismatched power operand types. These already produced
focused, useful messages in the tested examples; no changes were made to them.

These changes introduce no new syntax or implicit coercion.
