# Struct arithmetic

Thor supports binary `+`, `-`, `*`, `/` and compound `+=`, `-=`, `*=`, `/=`
for two values of the same struct type. Each operation applies independently to
the corresponding fields and preserves the struct's type.

```odin
Pair :: struct {a, b: int}
x := Pair{1, 2}
y := x + {3, 4} // Pair{4, 6}
x += {3, 4}     // x becomes Pair{4, 6}.
```

An untyped `{...}` literal receives the struct type from either typed operand,
or from the expected result type. Named entries, omitted entries, and defaults
follow ordinary Odin literal rules. Two separately declared struct types remain
different even if their fields have identical names and types; aliases of the
same type are allowed. There is no scalar broadcast to all struct fields.

All fields must be self-contained fixed-size values supporting the requested
operation. Native arithmetic values, fixed arrays, and nested eligible structs
are supported. Fixed arrays of eligible structs recurse element by element;
native numeric arrays, matrices, complex numbers, quaternions and bit sets retain their
existing arithmetic semantics. Each field result must fit the original field
type. Overflow, division, and representability rules remain Odin's.

Pointers, strings, slices, dynamic arrays, fixed-capacity dynamic arrays, maps,
`set[T]` values, unions, procedure values, `any`, `Integer`, and `Rational` are excluded,
including when nested inside an array or another struct. A slice header's fixed
size does not make its backing storage a value owned by the struct. Raw-union
storage and bit fields do not gain arithmetic operators. Existing native
operators and Thor logical-number/set operators retain their meanings.

Explicit `_` fields are padding: arithmetic and eligibility checks ignore them,
regardless of their declared types. Only named fields participate, including in
generics and compile-time execution. Result padding is normally initialized;
its source bytes are not copied or combined. Implicit alignment padding is also
ignored. Unsupported named fields remain errors even alongside padding.
In a `fn`, explicit `expand_values`/`**` expansion of a padded record requires
self-contained fields. Reference-containing records should use named field
access instead. Ignoring padding for arithmetic does not permit exposing a
hidden pointer or mutable backing storage in pure code. Ordinary `proc`
expansion keeps its native Odin behavior.

```odin
Padded :: struct {_: [3]byte, value: int, _: bool}
a := Padded{value=6}
b := a / {value=2} // value is 3; padding does not participate in division.
```

Runtime operands are evaluated once. Compound assignment evaluates the target
once and consumes the complete right-hand value before writing the result,
including indexed and map destinations. Immutable destinations remain invalid.
Arithmetic does not allocate or perform runtime reflection. Generated helpers
are contextless and forced inline; generic type selection happens at compile
time. No ownership or automatic cleanup rules change.

```odin
BASE :: Pair{1, 2} + {3, 4} // A native constant, not an allocated value.
grow :: fn(value: Pair) -> Pair { return value + {1, 2} }
AUTO :: grow(BASE)
EXPLICIT :: #run grow(BASE)
```

Constant arithmetic emits native constant struct expressions. Pure functions,
explicit `#run`, generic procedures and generic records support the same value
restrictions. TLS retains the original expressions for hover, completion,
semantic tokens, rename and source diagnostics. Field completion in untyped
literals uses the same contextual type as the compiler.

Run the [standalone example](../examples/struct_arithmetic/demo.thor) with
`thor run examples/struct_arithmetic`. The general language tour also includes
a `struct_arithmetic` section.

The differential and live TLS regressions are in
`tests/test_struct_arithmetic.py`, `tests/test_struct_padding.py` and
`tests/test_struct_arithmetic_tls.py`.
After building the release compiler, run them with:

```sh
THOR_TEST_PREBUILT=1 python3 -m unittest tests.test_struct_arithmetic tests.test_struct_padding tests.test_struct_arithmetic_tls
```

Runtime cases run both checked and with `-o:speed -disable-assert
-no-bounds-check -no-type-assert`. An LLVM comparison also verifies that optimized
numeric field arithmetic matches the handwritten equivalent without helper calls.
