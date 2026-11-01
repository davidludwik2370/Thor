# Diagnostic review — 2026-11-01

This is a focused review of indexing, contextual literals and adjacent lowered
expressions. Thirteen small programs were checked against the real compiler.
The additional proposals below are **not implemented**; they are recorded for
review before a further diagnostic change.

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

## Proposals awaiting approval

### 1. Suppress proven indexing cascades after an undeclared name

```odin
i := 1
missing[i] = 2
```

Currently: eight errors, starting with generated `type_is_array` / `type_of`
failures; the useful `Undeclared name: missing` is buried among them. An undeclared
index in `values[missing]` similarly produces the name error and an additional
`normalize` overload error.

Proposed: retain the original undeclared-name error and suppress only generated
inference/normalization failures tied to that same unresolved operand. Do not
suppress unrelated errors merely because they share a line or statement.

Priority: high. This is diagnostic handling, with regression coverage needed for
generic instantiation and multiple independent missing names.

### 2. Explain invalid index and slice-bound types as language operations

```odin
values := [1, 2, 3]
i: f64 = 1
_ = values[i]
```

Currently: `No procedures or ambiguous call for procedure group 'Thor
expression.normalize' that match with the given arguments`, followed by overload
details. String-valued slice bounds have the same problem.

Proposed:

```text
Error: Sequence indices must have an integer type; 'i' has type 'f64'.
```

For slicing, identify the lower or upper bound. Preserve native enum-array and
map-key rules; a noninteger map key can be completely valid. Cover wide unsigned
indices, distinct integer types, imported types and generic parameters.

Priority: high. This should explain the existing contract, not change accepted
index types or add implicit conversions.

### 3. Show original expressions in result-type mismatch messages

```odin
values := [1, 2, 3]
i := 1
result: string = values[i]
```

Currently: the message correctly reports `int` versus `string`, but quotes a long
generated expression containing captures and `sequence.element` type witnesses.

Proposed:

```text
Error: Cannot assign 'values[i]' of type 'int' to 'result' of type 'string'.
```

Use recorded emission boundaries and original AST spans to reconstruct the
quoted expression. Do not perform global text substitutions that could replace
user identifiers or alter real type names. Apply the same presentation rule to
similar lowered switch, interpolation and numeric expressions where possible.

Priority: high. The current type information and source range are useful; the
quoted generated implementation is the confusing part.

### 4. Avoid generated builtin queries being captured by user declarations

```odin
type_of :: proc() -> [2]int { return {1, 2} }
main :: proc() {
    i := 1
    _ = type_of()[i]
}
```

Currently: generated calls to `type_of(value)` resolve to the user's zero-argument
procedure, producing `Too many arguments for 'type_of', expected 0 arguments, got
1`. The source call has no arguments. The constant-index form does not need the
helper and works.

Proposed: investigate a reliable, scope-independent way for generated code to
refer to native builtins, and apply it consistently. Merely rewriting the error
would conceal a code-generation compatibility problem. If native Odin provides
no such mechanism for a particular builtin, discuss the limitation explicitly
before introducing any new reserved names.

Priority: medium. This is a correctness investigation as well as a clarity issue;
it needs broader compatibility tests before deployment.

### 5. Add operation context to inline membership conversion failures

```odin
record := struct {x: int} {1}
_ = record in [1, 2]
```

Currently: two separate native messages about converting `1` and `2` to the
record's struct type. This follows Thor's contextual membership-list typing, but
does not explain why the literals were being converted to a struct.

Proposed: add a short explanation identifying membership and the needle's type,
while keeping the actual offending element ranges and native conversion details.
For example, explain that the membership list's elements must be comparable with
the struct-valued needle. Grouping repeated failures could be considered later;
the first change should preserve useful per-element information.

Priority: lower. The present errors are accurate, but lack the operation's context.

## Cases that already read clearly

The review also checked nonindexable struct values, incorrect switch result
types, empty array literals without context, invalid Set key types, unordered
spread `min` elements and mismatched power operand types. These already produced
focused, useful messages in the tested examples; no changes were made to them.

The five proposals above do not include any new syntax or implicit coercion.
