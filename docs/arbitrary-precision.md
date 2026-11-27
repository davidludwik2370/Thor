# Integer and Rational: design and implementation stages

This records the accepted design. Stages 1–5 are implemented. This document records behavior and intentional boundaries.
See the [complete commented tour](../examples/demo/demo.thor),
[focused numeric demo](../examples/big_numbers/demo.thor), and
[explicit runtime API](../runtime/big/README.md).

## Types, construction, and literals

`Integer` and `Rational` are predefined types resolved by scope/type identity,
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

An exact numeric source literal has a default budget of **16,000,000 digits**
for each of its numerator and denominator. Thor checks the expanded size before
constructing either number. For example, `Rational(1e-100000000)` needs
100,000,001 denominator digits. Decimal literals discard insignificant zeros
before the check and before fraction reduction. Prefixed integers count
significant digits in their written base. Set `THOR_MAX_LITERAL_DIGITS` to a
positive whole number to change the budget for a compiler invocation. The
separate limits on compile-time arithmetic and serialized results still apply.
Runtime text parsers such as `Rational(text)` accept their usual inputs.

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
2. **Types and ownership (implemented):** predefined types, constructors, unquoted
   contextual literals, properties, direct copy/replacement rules, simultaneous
   assignment, parameter/result boundaries, TLS support.
3. **Runtime operators (implemented):** arithmetic/comparisons, coercion, compound
   updates, powers/bitwise operations, temporary cleanup and expression/generic
   interactions, including `a,b=b,a+1`.
4. **Compile-time support (implemented):** exact constants, read-only data, fn/#run,
   arbitrary-size value protocol, independent reconstruction and caching.
5. **Integration/hardening (implemented):** formatting, aggregate/native boundaries,
   memory/debugging, complete TLS behavior, commented demo and full suites.

Every user-facing batch includes source-map diagnostics, relevant TLS work,
regression/interaction tests, and documentation. See the
[stage-two implementation and validation record](arbitrary-precision-stage2.md).

## Ownership boundaries and examples

Run `thor run examples/big_numbers --track-allocations=basic` for the language
walkthrough. The stage-one `examples/big_runtime` demo still exercises the native
package API, now with direct Thor numeric-copy ownership enabled.

- Direct declarations (`:=`, `:>`, explicit types), numeric fields/elements,
  individual `append` values, generic copies/returns, simultaneous assignments,
  map-entry replacement, optional-result fallbacks/early branches, lazy value
  switches, `case: break`, and global numeric initializers establish independent owners. Fresh numeric call results transfer.
- `delete` resets mutable numeric owners, including a map's stored value. Borrowed
  numeric parameters and by-value loop bindings cannot be deleted; copy first.
  By-reference loop bindings can destroy their actual elements.
- Named locals returned by value are copied before deferred cleanup; use
  `defer delete(local)` for their own storage. A bare return transfers an owned
  named result. Named values are not automatically destroyed at scope exit unless declared as
  [scoped owners](scoped-owners.md), for example `number ~:= Integer(123)`.
- A whole aggregate copy, `copy` of aggregate elements, and `append(..slice)` keep
  native shallow ownership. Destroy contained numbers exactly once, then free the
  outer container if it owns storage. A fixed/stack-backed array has no outer
  heap allocation to delete. Deleting a container does not recursively delete
  its numeric elements.
- An ordinary native Odin procedure keeps native copying rules. Its numeric
  returned value is assumed to be a fresh owned result; a native procedure that
  returns a borrowed header must explicitly clone it. Thor does not rewrite native
  source to repair that convention. Native code using explicit `big.clone` and
  `big.destroy` remains supported.
- Custom allocators can be supplied to constructors/explicit clone helpers.
  Implicit direct copies use the current allocator. Global initialization uses
  Odin's default context before `main`, so main-scoped tracking does not see those
  allocations; this follows existing global-initializer behavior.
- Read-only properties resolve on concrete and generic Rational receivers,
  including pointers. Ordinary records retain fields with the same names.
- F-strings and `basic.print`/`basic.table` display logical numbers. Use
  `big.to_string` for an owned explicit conversion. Native `fmt` integration uses
  an explicit registry, described below.
- Big `::` constants, exact constant operators and big `fn`/`#run` results are
  implemented. Constants borrow read-only limbs; runtime copies own allocations.
  Distinct big types and big map keys/set elements remain explicitly rejected;
  ordinary type aliases are supported.

TLS resolves the predefined names without an import, retains user shadowing and
native `.odin` type spelling, offers logical constructor signatures and read-only
properties, hides internal limb fields, and keeps rename/semantic/diagnostic
positions in the original Thor file. Numeric text can contain thousands of digits;
there is no lexer reservation of these type names.

## Runtime operator details (stage three)

[The operator walkthrough](../examples/big_operators/demo.thor) exercises the
implemented arithmetic, exact comparisons, unary signs, Integer complement,
bitwise operations (`&`, `|`, `~`, `&~`), shifts, powers and ordinary compound
assignments. Increment/decrement forms such as `x++` and `--x` remain
unsupported. Thor's infix `++` concatenates compile-time arrays/slices; it is
not a numeric increment operator.

Untyped literal expressions adopt a requested big-number context before native
inference can overflow or round: `r: Rational = 1/3 + 0.25` is exactly `7/12`.
The same applies in constructors and with untyped scalar constants. Typed native
values still require explicit conversion. Operands are preserved before RHS
side effects, and every anonymous numeric operand/result is released after its
last known use. Generics retain native operations for native instantiations.

Integer `/` truncates toward zero; `%` follows the dividend's sign. `%%` matches
Odin's `((a % b) + b) % b`, so nonzero results follow the divisor's sign. Rational
remainders use those same quotient/sign rules with exact fractions. Right shift
extends the sign. Shift operands follow the matching-type rule (or an untyped
count); negative counts are rejected. Integer exponents are nonnegative, Rational
exponents must be whole, and zero to a negative power panics. `0 ** 0` is one.

The native bigint APIs initialize empty input headers and contain an incorrect
signed-shift final subtraction and a misplaced quotient clamp. The Thor bridge
owns/frees input-initialization scratch, computes signed shifts through magnitude
quotient/remainder, and canonicalizes results. Allocation-failure sweeps cover
arithmetic, fractions, powers, shifts, complement and comparisons. No Odin SDK
source is patched.

Validation for this batch includes independent Python int/Fraction oracles,
4,096-bit operands, debug/tracking examples, native ASan and failure injection,
source diagnostics and TLS hover/edit recovery. The focused language/runtime
regression run passed 42 tests; the broader numeric/power/diagnostic run passed
142, TLS/rename passed 149, native TLS passed 1,126, and native bigint ASan plus
allocation-failure sweeps passed 13. Original-source arithmetic stepping was
also checked through CodeLLDB. Compile-time support and library integration are
described below; the final combined verification is recorded in
[the hardening report](arbitrary-precision-hardening.md).

## Compile-time numbers (stage four)

See [the compile-time walkthrough](../examples/big_comptime/demo.thor).
Constant construction and arithmetic preserve exact source digits and fractions,
including negative division/remainder, shifts, bitwise operations, and powers.
Constants can feed compile-time conditions and switches, other constants, and
`fn`/`#run` calls. The lightweight constant folder handles numeric literals and
expressions; use `:: #run Integer(...)` (or a known-argument fn call) when inputs
require native typed conversions, intrinsics, or procedure evaluation. Literal native operands still require the same contextual
coercion as runtime expressions; typed native values require explicit conversion.

Runtime access uses small allocation-free native headers over `@(rodata)` limb
arrays. This is a bridge to Odin, which does not support arbitrary-precision
struct constants: numeric *contents* are compiled into read-only data rather than
parsed/allocated at startup. Thor retains the original constant identity and
rejects writes, deletion, and taking its address. A direct runtime copy clones
its contents; runtime arithmetic never modifies the backing data.

`fn` return promotion recognizes numbers as logical numeric values, including
inside records, arrays, slices and unions. It never serializes private backend
fields, pointers or allocator identities. Each returned occurrence owns fresh
storage. Named direct numeric returns are preserved before deferred deletion;
existing returned slice views still observe user defers before promotion.
`pure.destroy` recursively releases an owned returned tree.

The value protocol stores canonical decimal Integer contents or a reduced
Rational numerator and positive denominator. It accepts numbers beyond Python's
native decimal-string conversion limit. Cache reads validate this canonical
schema, and reconstruction creates independent runtime owners every time.
Warm scalar/big-result dependency records survive unrelated runtime edits; pure
callee/argument/source changes invalidate them normally.

Aggregate `#run` results containing numbers require `:=`, preserving the explicit
shallow-aggregate ownership boundary. Individual numeric constants support `::`.

These values cannot be native Odin generic *constant parameters* (`$N: Integer`):
pass a borrowed numeric parameter to `fn`/`#run` instead. Generic type parameters
instantiated with Integer/Rational remain supported. Cross-target `#run` retains
the existing host-execution restriction; ordinary rodata constants are portable.

## Integration and hardening (stage five)

[The integration demo](../examples/big_integration/demo.thor) runs with `-debug`.
Untyped numeric literals adopt the big operand/context, including compound updates:

```odin
a: Integer = 1
a += 2                 // 3; no explicit Integer(2) required.
n := 2
a += Integer(n)         // A typed native variable needs explicit conversion.
```

`abs`, `min`, `max` and `clamp` return independent owned numeric results.
Fixed-array expansion and optional slice reductions work with `min`/`max`; an
empty slice returns zero and `ok=false`. All candidates are evaluated in source
order, and literal candidates are contextual. Native/generic instantiations keep
native behavior. `min(Integer)` has no finite bound and remains invalid.

Sequence membership compares numeric contents. Named containers are borrowed;
anonymous Thor array-list elements and needles are released after the comparison.
As with other array lists, every element is evaluated before testing membership.
Generic `r.numerator`/`r.denominator` reads dispatch by actual type, preserving
ordinary fields with the same names. Generic writes/address-taking reject actual
Rational properties while ordinary mutable record fields remain usable.

Runtime value switches also accept Integer/Rational selectors, including ranges,
multiple results, and skipped assignments. The selector is snapshotted once before
case evaluation, and temporary numeric case values are released after comparison.
Compile-time switches and locally declared numeric constants retain their exact
contents when used by generated expression helpers.

F-strings render canonical decimal integers and reduced fractions, clean up
anonymous arithmetic/components, and support deterministic pure formatting.
Integer grouping and numeric alignment work normally. Decimal and percentage
formats use exact rational rounding; grouping is allowed for Rational decimal
output, while grouping its default fraction is rejected. Rational g/G requires
explicit significant-digit precision. See [interpolation formatting](interpolation-formatting.md). `basic.print` and `basic.table` format numeric values automatically;
table numeric columns align right. Shared `basic.sum` and the default `sorted`
comparator support these logical types. Sorting/filtering
and whole aggregate copies retain their documented shallow element ownership.

Odin `fmt` owns a global custom-formatter registry. Thor does **not** install or
replace that global registry implicitly. To format numbers with native `fmt`,
including numbers nested inside records/containers, use its normal setup:

```odin
import "core:fmt"
import "thor:format"

registry: map[typeid]fmt.User_Formatter
format.add_big_formatters(&registry)
// Add your own formatters to the same map before installing it.
fmt.set_user_formatters(&registry)
defer format.destroy_formatter_registry(&registry) // After the final fmt call.
```

The cleanup helper detaches the registry before freeing it, so subsequent
formatting (including finalizers) cannot read a freed map. Keep a registry alive
longer if finalizers also need its custom formatters.

The numeric formatters support `%v`/`%s`, plus Integer `%d`, using exact decimal
contents and ordinary string padding. Use f-string numeric options for grouping.
The registry is an explicit runtime capability; custom formatter callbacks remain
ineligible for pure computation. Native .odin code retains explicit ownership.

Pure promotion uses allocation-free, init-registered logical clone/destroy hooks.
The base pure runtime has no dependency on the bigint backend, avoiding cycles
when checked math, time or OS functions themselves need pure validation. Numeric
payloads never expose the backend's allocator or limb implementation to CTFE.

The runtime/ownership, exact Fraction/Python arithmetic oracles, allocation-failure
sweeps, cache invalidation, native-library compatibility, TLS stdio/editor tests,
and real CodeLLDB stepping checks form the regression coverage. The complete
validation record is in [the hardening report](arbitrary-precision-hardening.md).
