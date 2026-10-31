# Integer and Rational: design and implementation stages

This records the accepted design. Stage 1 is implemented; language changes remain
planned until their stage is completed. See [the runtime API](../runtime/big/README.md)
for code usable now.

## Types, construction, and literals

`Integer` and `Rational` will be predefined types resolved by scope/type identity,
not reserved lexical keywords. Helpers live in `thor:big`, using ordinary
qualified, selected, or wildcard imports; no helper names are injected globally.

```odin
i: Integer = 1234567890123456789012345678901234567890
zero_i: Integer
native_i := Integer(some_u128)
text_i, ok := Integer("12345678901234567890")

r: Rational = 12341234134.4123412341
zero_r: Rational
parts := Rational(123423, 12)
named := Rational(numerator=123423, denominator=12)
whole := Rational(123423)
text_r, text_ok := Rational("123.45(89)")
strings_r, strings_ok := Rational("234234", "72638463")
```

Numeric construction returns one value; text parsing returns value/status.
Malformed text returns false and a safe zero value. A zero denominator panics at
runtime and is a compiler error when statically known. Allocation failure must
not become `ok=false`. Native integer inputs preserve their signed/unsigned
range. Floating-point variables are initially rejected; decimal literals use
exact source digits without passing through float. Repeating decimals use
parentheses inside strings, not a new `r` suffix or ellipsis token.

Long literals are accepted unquoted in Integer/Rational contexts. Ordinary type
inference and overflow behavior remain unchanged:

```odin
x := 123                // Ordinary int.
y := max(u128) * 2       // Compile error; no automatic promotion to Integer.
```

Rationals are reduced with positive denominators. Logical zero is `0/1`.
Zero-initialized numbers and zero values need not allocate.

## Read-only components

```odin
r := Rational(22, 13)
print(r.numerator, r.denominator)
n := r.numerator         // Independent owned Integer.
r.numerator = 12         // Compile error.
r.denominator *= 2       // Compile error.
```

These computed properties need no import. They do not expose writable storage.
Materialized component values own independent copies; anonymous results are
cleaned up after known use. A read-only consumer may borrow internally as an
optimization. `big.set_fraction(&r, numerator=2, denominator=4)` validates and
normalizes both parts together.

## Ownership compromise

Direct Integer/Rational copies in Thor create independent values, in local and
global code. Replacement releases old owned storage after safely evaluating the
entire RHS. Fresh results transfer without another clone. Immutable parameters
may borrow for reading; retaining/returning a borrowed number must establish
independent ownership. Final named values still require explicit deletion; this
does not introduce garbage collection or automatic scope destruction.

```odin
a := Integer(10)
b := Integer(20)
a, b = b, a + 1          // a=20, b=11; overwritten storage does not leak.
delete(a)
delete(b)
```

Handle self-assignment and simultaneous assignment before destroying inputs.
Individual numeric fields follow numeric rules. **Copying a whole struct, array,
or aggregate remains shallow for contained allocated numbers**, as with Odin's
reference-containing values. Such a copy is not an independent owner; mutation,
replacement, or deletion may invalidate aliases. Explicit deep copying is
needed for independent aggregate ownership. Native Odin procedures retain Odin
copying rules, requiring documented/tested boundaries rather than an implicit
promise that every native copy becomes a Thor clone.

Arithmetic never mutates named operands. Anonymous intermediates are managed by
the compiler. Storage reuse must preserve values, evaluation order, and ownership.

## Operators, constants, and tooling

Plan exact arithmetic/comparisons, contextual literal coercion, and explicit
conversion between differently typed operands. Integer division truncates toward
zero; Rational division is exact. Integer bitwise operators and shifts are
supported; Rational bitwise operations are rejected. Remainders must match the
respective Odin `%`/`%%` rules. Rational remainders must be exact.
Integer powers require nonnegative exponents; Rational
powers allow negative whole exponents but reject fractional ones. Do not
implicitly mix Integer/Rational or typed native numeric operands.

```odin
VALUE :: Integer(123456789012345678901234567890)
runtime_value := #run calculate_large_integer()
```

`VALUE` is a true compile-time constant, with read-only backing data if runtime
code needs it. `runtime_value` receives independent allocated storage. Runtime
materialization must not expose writable constant data or free it. Extend fn
validation, serialization/reconstruction, and content-based caches explicitly;
the current machine-integer result protocol is insufficient.

TLS should present logical types, recognize arbitrarily long numeric tokens,
support properties/constructors and normal symbol operations, and report errors
at original Thor expressions. Formatting must show numbers, not implementation
structs. Numerical map keys/set elements are initially rejected unless suitable
hashing/equality is added.

## Stages

1. **Runtime foundation (implemented):** zero storage, construction, independent
   cloning, destruction, exact/strict parsing, normalization, allocator/errors,
   atomic fraction replacement, runtime and external-project tests.
2. **Types and ownership (pending):** predefined types, constructors, unquoted
   contextual literals, properties, direct copy/replacement rules, simultaneous
   assignment, parameter/result boundaries, TLS support.
3. **Runtime operators (pending):** arithmetic/comparisons, coercion, compound
   updates, powers/bitwise operations, temporary cleanup and expression/generic
   interactions, including `a,b=b,a+1`.
4. **Compile-time support (pending):** exact constants, read-only data, fn/#run,
   arbitrary-size value protocol, independent reconstruction and caching.
5. **Integration/hardening (pending):** formatting, aggregate/native boundaries,
   memory/debugging, complete TLS behavior, commented demo and full suites.

Every user-facing batch includes source-map diagnostics, relevant TLS work,
regression/interaction tests, and documentation. Stage 1 uses ordinary package
APIs and native copying; it does not partially activate the planned language
ownership or literal rules.
