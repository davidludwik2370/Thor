# Arbitrary-precision integration and hardening

The accepted behavior is in [the language reference](arbitrary-precision.md).
Stages 1–4 have individual commits; stage 5 completes library/editor integration
and hardens the combined implementation. Compiler version 0.27.1, TLS
`thor-0.20.0+ols-0c9438f`.

## Changes and interactions covered

- Arbitrarily long exact literals, nominal type identity/shadowing, constructors,
  normalization, zero storage, text parsing and separate allocator errors.
- Runtime Integer/Rational arithmetic with independent Python/Fraction oracles,
  negative remainders/shifts/powers, literal coercion, typed-operand rejection,
  compound updates, simultaneous/repeated destinations and custom allocators.
- Direct ownership, borrowed parameters/iteration, anonymous cleanup, optional
  parsing, lazy switches/fallbacks, recursive fn promotion and deferred cleanup.
- Read-only exact constants, arbitrary-size portable result packets, nested
  allocated #run results, cache reuse after unrelated runtime edits and dependency
  invalidation. Selected compile-time switch results retain numeric identity and
  therefore clone when copied to runtime storage.
- Generic computed components, ordinary same-named writable fields, native
  boundaries, numerical builtins/spread extrema and sequence membership.
- F-string formatting/grouping/alignment, shared basic reductions/order/sort/
  print/table, explicit native fmt registry and safe registry detachment.
- Source-map diagnostics, original Unicode positions, readonly constant errors,
  TLS hover/type/token/rename/completion, real CodeLLDB breakpoints and stepping.

## Issues found and corrected

Odin's bigint shift path needed canonicalization and corrected negative arithmetic
shifts. Some backend operations initialize zero or undersized *input* headers;
the runtime now releases zero-input scratch storage and pads borrowed constant
limbs to the backend minimum. Constants never resize their read-only backing.

Generic operands must carry a semantic type hint as well as generated native type
text. Otherwise loop elements could be treated as fresh owners, or contextual
literal construction could be cloned twice. Tests now cover those cases through
shared generic basic functions and anonymous interpolation/membership operands.

Native procedure metadata excludes compile-time parameters. Argument type queries
now count runtime parameters, preserving functions such as `basic.random($T, ...)`.
Generic deletion retains both set and ordinary native delete behavior, and typeid
comparisons avoid runtime value-temporary lowering. Variadic calls use the element
type rather than the slice type, preserve the numeric owner before boxing into
`any`, and release anonymous inputs after the call. Method argument hints account
for the implicit receiver. Default expressions stay constant while procedure
bodies supplied as defaults retain ordinary Thor operator lowering.

The pure runtime must not import math/big: checked math/time/OS procedures can
need pure support themselves. Logical numeric clone/destroy hooks register once
at initialization without allocating, keeping the base pure runtime independent.
Numeric validation is emitted only where those types are available; native SDK
validation stays independent of the high-level numeric library.

Pure direct numeric returns clone before deferred deletion, while existing slice
views still observe deferred edits before final promotion. A wholesale change to
promotion timing would have broken existing semantics; regression tests retain
both behaviors.

One native Odin SIGSEGV recurred during a numeric/default-parameter test. Its
macOS report has the same leading `alloc_cstring` / `lb_type_internal` / `lb_type`
stack offsets as the previously investigated LLVM type-name cache race. Eight
isolated rebuilds and the isolated test retry passed. Compact evidence is preserved
in [the upstream folder](../upstream/odin-codegen-race/evidence/recurrence-2026-11-01.txt).
The installed Odin compiler remains unchanged.

Native fmt registration stays explicit. Its caller-owned registry must be detached
before freeing it, so later defers/finalizers can still use fmt safely.

## Intentional boundaries

Whole aggregate copies and native Odin assignments remain shallow. Named runtime
owners require explicit cleanup. Constant aggregate #run results containing big
numbers require `:=`; individual numeric constants support `::`. Big keys in maps/
sets and distinct big types remain rejected, and native generic constant parameters
cannot have Integer/Rational value types. Cross-target #run retains the existing
host-execution limitation. Native fmt supports the documented decimal verbs through
explicit registration; f-strings/basic formatting work without that registry.

## Verification

The clean full Python suite passed 1,081 tests with one opt-in Finder-launch test
skipped. After the final variadic/method argument refinements, the affected
language/library/evaluation subset passed another 227 tests, and the TLS/source
diagnostic subset passed 92 tests.

Additional checks passed: 1,126 native TLS tests, 109 Node extension tests, 13 native
numeric tests with ASan and allocation-failure sweeps, and 26 combined numeric
TLS/debugger tests. An isolated real VS Code session verified highlighting, long
numeric literals, hovers, properties, rename and diagnostics. CodeLLDB checked
breakpoints and stepping in original Thor source, including numeric constants and
compile-time results. The compile-time and integration demos also ran cleanly
with debug allocation tracking and ASan.

These checks include independent numerical oracles, cache invalidation and result
reconstruction. The earlier intermittent native compiler crash is recorded above;
it did not recur in the clean full-suite run. Runnable examples are in
`examples/big_runtime`, `big_numbers`, `big_operators`, `big_comptime`, and
`big_integration`.

## Main walkthrough follow-up (0.27.1)

The main [commented tour](../examples/demo/demo.thor) now includes five numeric
topics alongside the rest of the language. Its [coverage index](../examples/demo/README.md)
lists construction, ownership, operators/control flow, constants/evaluation, and
formatting/library examples. The mixed helper package includes an ordinary Odin
procedure that explicitly clones its numeric return.

Expanding that tour exposed two gaps. Runtime value switches now compare exact
Integer/Rational selectors and contextual case operands, including ranges. The
selector is evaluated and snapshotted once before case effects, and anonymous
case operands are consumed after comparison. Scalar, multiple-result, skipped
assignment and interpolation paths share this lowering. Duplicate/reversed
constant cases and typed operand mismatches retain source diagnostics.

Local numeric constants lower to read-only header views. Generated expression
helpers now reconstruct those views directly rather than referring to an
uncaptured local header. Tests cover direct constants, both compile-time switch
forms, local #run, runtime copies, comparisons and interpolation. TLS checks
logical hovers, original case-error locations and recovery after edits.

The focused language/ownership suite passed 95 tests, the native switch/diagnostic
subset passed 163, and the editor suite passed 10. The tour also passed its Odin
allocation-checking test wrapper and ran with Thor debug tracking/ASan. Real
CodeLLDB's full 19-test suite passed, covering breakpoints on numeric switch arms
and statements after them alongside the existing stepping cases. Generated
comparisons/operand snapshots retain diagnostic spans while being hidden from
breakpoint selection, avoiding duplicate stops on one arm line. Both alternate
tour modes (`TOUR_MODE=1` and `3`) passed as well.
