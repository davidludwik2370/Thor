# Arbitrary-precision runtime foundation

This is the explicit native API underlying Thor's built-in `Integer`/`Rational`
types. Thor stage 2 adds exact unquoted literals, read-only dot properties, and
automatic direct numeric copying/replacement. See the
[language walkthrough](../../examples/big_numbers/demo.thor) and
[remaining stages](../../docs/arbitrary-precision.md). Operators and compile-time
big values are still pending.

Use `import "thor:big"` from any Thor project. Native Odin users can supply
`-collection:thor=/path/to/Thor/runtime`.

```odin
package main
import "thor:big"

main :: proc() {
    integer, ok := big.parse_integer("1234567890123456789012345678901234567890")
    assert(ok)
    defer big.destroy(&integer)

    rational, rational_ok := big.parse_rational("123.45(89)")
    assert(rational_ok)
    defer big.destroy(&rational)

    independent := big.clone(rational)
    defer big.destroy(&independent)
    big.set_fraction(&rational, numerator=2, denominator=-8)

    text := big.to_string(independent) // "305561/2475"
    defer delete(text)
}
```

## API

All allocating APIs accept `allocator=`, defaulting to `context.allocator`.

| API | Result / behavior |
| --- | --- |
| `Integer`, `Rational` | Runtime structs; zero initialization allocates nothing |
| `from_integer(value)` | Owned Integer from any native integer type or Integer |
| `rational_from_integer(value)` | Owned Rational with denominator one |
| `fraction(numerator, denominator)` | Reduced Rational; accepts native integer widths and/or Integer inputs |
| `parse_integer(text)` | `(Integer, ok)` |
| `parse_rational(text)` | `(Rational, ok)` |
| `parse_fraction(numerator_text, denominator_text)` | `(Rational, ok)` |
| `clone(value)` | Independent Integer or Rational |
| `numerator(value)`, `denominator(value)` | Independent owned Integer components; logical zero has denominator one |
| `set_fraction(&target, numerator, denominator)` | Construct/normalize before replacing and freeing previous target storage |
| `is_zero(value)`, `equal(a, b)` | Allocation-free queries; never modify borrowed inputs |
| `to_string(value)` | Owned decimal Integer text or canonical `numerator/denominator` Rational text |
| `destroy(&value)` | Free using stored allocators and reset the caller's value to zero |

The `try_` counterparts return an `Error` instead of panicking:
`try_from_integer`, `try_rational_from_integer`, `try_fraction`, `try_parse_*`,
`try_clone`, `try_to_string`, and `try_set_fraction`. `Error` distinguishes
`.Invalid_Text`, `.Division_by_Zero`, `.Out_Of_Memory`, `.Invalid_Allocator`,
`.Integer_Overflow`, and `.Backend_Failure`; `.None` means success. Failed constructors return empty zero
values; failed text output returns an empty string. Failed replacement preserves
the old target. Partial results and scratch allocations are cleaned up.

Regular APIs panic for allocation/size/backend errors and zero denominators.
Only malformed text produces `ok=false`; allocation failure is not invalid text.
Panic wrappers accept caller locations for later source-map integration.

## Ownership

**Native Odin assignments are shallow copies.** Thor direct numeric assignments
clone and replacements free old storage; whole aggregates remain shallow in both
languages. Use `clone` for another
owner and do not destroy aliases twice. Underscore-prefixed fields are runtime
internals; manipulating them bypasses normalization and ownership rules.

Numbers retain their allocators and can be destroyed after context changes.
Strings must be deleted with their output allocator; their allocations exactly
match their lengths. Zero initialization, cloning zero, numeric zero
construction, and parsing valid zero forms allocate no number storage. Reading
the logical denominator of zero returns an owned Integer containing one.

## Text grammar

Inputs must be entirely valid ASCII numeric text: no whitespace, partial parses,
NUL, NaN, or infinity. Leading `+`/`-` are accepted; underscores are permitted
only between digits. Integer text accepts decimal and `0b`, `0o`, `0d`, `0z`
(base twelve), and `0x` prefixes, including uppercase forms.

Rational text accepts whole integer text; two integer strings separated by `/`
with independent signs/bases; finite decimals such as `.125`, `12.`, `-1.25e-6`;
and repeating decimals such as `0.(3)`, `0.1(6)`, `1.2(34)e2`, and `.(3)`.
Parentheses specify exactly the repeating block, including leading zeros.
Exponents apply to the entire value. Conversion uses integer arithmetic, never
floating-point. Fractions are reduced with a positive denominator. All zero
forms behave as `0/1`; empty internal storage is a valid zero sentinel.

There is no fixed digit limit. Memory and machine-sized lengths bound storage.
Exponent magnitudes must fit a machine `int`; overflow is reported before
constructing powers of ten. Large valid exponents may require substantial
resources.

## Backing-library workarounds and tests

Thor uses Odin `core:math/big` integer operations. It validates full inputs
because backend parsing accepts some malformed text, normalizes denominator
signs because the installed backend loses negative denominator signs, and frees
stored dynamic arrays itself because backend destruction uses the current
allocator and does not reset the caller. Decimal output formats the magnitude
and canonicalizes padding before adding the sign, avoiding an extra leading
zero in some large negative backend outputs. A conservative limb-based output
size avoids a scratch leak in the backend integer-log/power sizing path when an
allocation fails. The Odin checkout is unchanged.

Run the [commented example](../../examples/big_runtime/demo.thor) with
`./thor run examples/big_runtime --track-allocations=basic`.

```sh
odin test runtime/big -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
python3 -m unittest tests.test_big -v
```

Tests cover exact values, strict rejection, zero allocation, all native integer
widths, clone/getter independence, replacement aliasing, allocator changes, and
failures at successive allocation points. Python integers/Fraction provide
independent reference values, including 6,000-digit inputs. External-project
imports and isolated panic processes verify packaging and error distinctions.
