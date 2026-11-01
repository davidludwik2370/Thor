# Interpolation formatting

Thor's suffix follows Odin `fmt` conventions, without a leading `%`:

```odin
text := f"{value:[quoted-fill][flags][width][,][.precision][verb]}"
```

Flags, fill, precision and the verb are literal syntax. Width is either a
nonnegative decimal integer or a nested `{expression}`. Precision stays literal.
A bare dot means precision zero (`:.f`). Precision requires a verb.
Fields still evaluate once, left to right; formatting never changes the value.
Parenthesize ternaries before `:`. Nested interpolation, raw literals, and
`{{`/`}}` retain their existing behavior.

| Suffix | Meaning |
| --- | --- |
| `.2f` | Fixed decimal, two places |
| `12.2f` / `-12.2f` | Minimum width 12, right / left aligned |
| `+.2f` / ` .2f` | Positive sign / leading space for a positive number |
| `08d` / `#08x` | Numeric zero padding, after the sign/base prefix |
| `,.2f` | Group the decimal integer portion, then pad |
| `10.2%` | Multiply by 100, fixed decimal, append `%`; width includes `%` |
| `'界'^12s` | Center with a quoted Unicode fill rune |
| `'_'12s` / `'_'-12s` | Custom fill, right / left aligned |
| `'0'8d` | Literal zero fill: `-42` becomes `00000-42` |
| `08d` | Numeric zero flag: `-42` becomes `-0000042` |
| `.10s` / `20.10s` | Truncate to ten runes, optionally pad to twenty |
| `q` | Quoted/escaped string or rune |
| `T` / `w` / `#v` | Type / source representation / expanded value |

Flags are `+`, space, `-`, `0`, `#`, and Thor's `^` for centering. `+` takes
precedence over space; `-` disables numeric zero padding, matching Odin. Centering
cannot combine with `-` or `0`. Quoted fill cannot combine with the numeric `0`
flag. The default fill is a space and **default alignment is right for all types**.
Odd center padding puts its extra rune on the right. Width counts Unicode
**runes**, not bytes, grapheme clusters, or terminal cells. `<` and `>` are removed;
use `-` for left alignment and omit alignment for right alignment.

Grouping belongs after width and before precision/verb: `012,.2f`. It groups only
the decimal integer part, leaving fractions, exponents, and `%` intact. Grouping
of nondecimal bases, text, booleans, or the default Rational fraction is rejected.
Width is applied after conversion, rounding, and grouping. Numeric zero padding
is applied last and does not introduce more grouping separators.

## Variable minimum widths

```odin
round_pad :> len(f"{ROUNDS}")
print(f"Round {i+1:{round_pad}}")       // Right aligned.
print(f"Round {i+1:-{round_pad}}")      // Left aligned.
print(f"{value:'_'^{round_pad+2}}")     // Centered with a computed width.
print(f"{probability:{round_pad}.2%}")  // Width plus literal precision.
print(f"{number:0{round_pad},d}")       // Zero padding and grouping.
```

Use nested braces around a variable or expression; `:round_pad` is not a width.
The expression uses ordinary Thor syntax, including calls, indexing, ternaries,
selected switch expressions, and nested strings. Widths accept native integer
types, including unsigned and distinct integers, and are checked before converting
to `int`. Negative values and values exceeding native `int` fail with a width
error; zero is valid. Width never truncates a longer value.

Each field's value evaluates first, then its width, exactly once each. The next
field starts after the previous one is formatted. Skipped branches do no work.
Borrowed Integer/Rational fields get a temporary snapshot before width evaluation,
so a width function can overwrite the original owner safely; that snapshot is
released after formatting. Existing aggregate/reference-copy rules stay shallow.
Fresh owned numeric results need no extra copy. Temporaries used to compute the
width are released normally. The same behavior works in runtime code, `fn`,
`#run`, and generic functions.

TLS treats width expressions as code: hover, definition, references, and rename
work inside their braces. Wrong types and undeclared names point to the original
width expression. Width calls remain executable original-source debugger targets.

## Odin verbs

| Values | Supported verbs |
| --- | --- |
| General values | `v` default, `#v` expanded, `w` source, `T` type |
| Booleans | `t` |
| Native integers | `d`/`i`, `b`, `o`, `z` (base 12), `x`/`X`, `c`/`r`, `U`, `m`/`M` |
| Runes | Native rune `v`, `c`/`r`, `q`/`w`, and integer verbs |
| Native floats | `f`/`F`, `e`/`E`, `g`/`G`, `h`/`H` (raw bits) |
| Complex/quaternion | Decimal verbs on components; native `v`/`w`, `h`/`H` |
| Strings | `s`, `q`, `x`/`X` (encoded bytes); space separates hex bytes |
| Byte arrays/slices/dynamic arrays | `s`, `q`/`w`, `x`/`X`; otherwise native container formatting |
| Pointers, enums, aggregates, custom values | Native Odin formatting where applicable |

`f`/`F` defaults to **three decimal places**; `e`/`E` to six; native float `g`/`G`
uses the shortest round-trip representation. These are Odin defaults. `F` is an
alias for `f`, and native `G` follows Odin's `g` spelling. Percentage defaults to
three decimal places too. Thor also accepts decimal/percentage verbs on native
integers, without losing integer precision. `m`/`M` use powers of 1024, lowercase /
capitalized units; `#` adds a space before the unit.

String precision truncates **before** quoting/escaping; width includes resulting
quotes/escapes. String truncation preserves rune boundaries. Precision on byte
containers instead limits raw bytes, useful for a hex dump. Ordinary Odin escape
spelling is preserved (`q` may encode an emoji as `\U0001f600`). Native custom
formatters remain registered and owned by the application; interpolation does
not replace their registry. Scalar sign/width handling is outside callbacks.

Dynamic precision, printf-style `*` widths, argument indices, Python `!r`/`!s`,
and locale `n` are not supported. Use `{expression}` for variable widths.
Precision on aggregate values is rejected: recursively forwarding
large precision into Odin's aggregate formatter would hit its fixed float buffer.
Format the individual numeric fields instead. Custom aggregate formatting without
precision continues to use Odin's formatter.

## Integer and Rational

No format (or `v`/`s`) preserves exact Integer decimal text or a reduced Rational
fraction. `T` produces the Thor type name; `w` produces `Integer(...)` or
`Rational(numerator, denominator)`.

```odin
large := Integer(123456789012345678901234567890)
ratio := Rational(1, 3)
defer delete(large)
defer delete(ratio)

print(f"{large:,.2f}")  // 123,456,789,012,345,678,901,234,567,890.00
print(f"{ratio:.20f}") // 0.33333333333333333333
print(f"{ratio:.6g}")  // 0.333333
print(f"{ratio:.2%}")  // 33.33%
print(f"{ratio}")     // 1/3
```

Integer bases `d`/`i`, `b`, `o`, `z`, `x`/`X` are exact. Both big types support
`f`/`F`, `e`/`E`, `g`/`G`, and `%`, with exact nearest, ties-to-even rounding.
Integer `m`/`M` is exact too. Rational does not support bases or raw-bit verbs;
Integer does not have a float bit pattern. Negative values that round to zero
retain their sign (`Rational(-1,1000):.2f` produces `-0.00`).

**Rational `g`/`G` requires explicit precision**, for terminating and repeating
fractions alike. A repeating decimal has no finite shortest exact expansion.
Use `:.6g` for significant digits, `:.6f` for decimal places, or omit the format
for the exact fraction. Explicit `g/G` precision zero means one significant digit.
Integer bare `g/G` remains exact; it uses scientific notation below exponent -4
or from exponent 6, stripping insignificant fractional zeros.

Formatting a Rational never approximates through `f64`. Floats use Odin's
conversion routine with sized storage, avoiding its ordinary formatter's fixed
scratch buffer. Precision can exceed 386 characters; size arithmetic is checked,
and actual storage is limited by the allocator. Results are identical at runtime,
in `fn` constant calls, and in `#run`.

## Ownership and tooling

The current allocator supplies all storage. Intermediates are released; a named
result remains yours to `delete`. Anonymous `f` arguments are freed after their
enclosing expression; `f!` retains its explicit ownership-transfer behavior.
No formatting-specific exception is added to printing.

TLS uses the same compiler checks for malformed formats, invalid type/verb pairs,
and missing Rational precision, including unsaved buffers. Field diagnostics map
to the original Thor expression. VS Code colors quoted fills as runes, digits as
numbers, verbs as type-format keywords, and punctuation/flags as ordinary
operators. Literal format syntax creates no executable debugger stops. Nested
width expressions are ordinary code; stepping follows those expressions, the
original field values, and surrounding Thor code.
