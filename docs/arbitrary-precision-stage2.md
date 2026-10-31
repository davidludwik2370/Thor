# Integer/Rational stage-two implementation

Completed 2026-10-31. Compiler version 0.24.0; TLS version
`thor-0.17.0+ols-0c9438f`. The [accepted design](arbitrary-precision.md) remains the
feature reference; [the walkthrough](../examples/big_numbers/demo.thor) is runnable
with `thor run examples/big_numbers -debug`.

## Delivered

- Scope-resolved predefined types, aliases, arbitrary-length unquoted integers,
  exact decimal Rational literals, numeric/text constructors, named fractions,
  allocation-free zero, and read-only owned components.
- Independent direct copies and replacements, simultaneous/repeated destinations,
  individual aggregate fields/elements and append values, map-entry destruction,
  generic copies/returns, and borrowed-parameter protection. Whole aggregates and
  native procedures retain the documented shallow boundaries.
- Optional parsing, lazy fallback ownership, and early return/loop-branch cleanup.
  A failed optional numeric result is released before control leaves the caller;
  return/break/continue themselves stay in the caller's lexical scope.
- Mapped errors for invalid inputs, nonintegral Integer literals, zero literal
  denominators, private storage, property mutation, unsupported big keys/distinct
  types, and premature compile-time constants. Native overflow does not promote.
- TLS type/property hovers and completions, constructor signatures, original
  source tokens/rename/definitions, hidden storage, read-only colors, source
  diagnostics and unsaved correction. Native Odin hovers keep native spelling.

## Edge cases caught during implementation

Global multi-result parses needed a private initialized record because Odin does
not allow that native initializer form. The parser now permits Thor's call form
for later semantic lowering; native `.odin` parsing keeps its previous rule.

Replacement must capture all locations and values first, then destroy/commit each
destination in order. This also handles repeated destinations without double
freeing. Map headers must be captured by address so first insertion updates an
initially empty map's real header; keys are evaluated once. Destroying a stored
numeric map value resets the actual slot.

Multiple-result discards, unused constructor calls, optional failures, and
borrowed fallback expressions each needed explicit ownership handling. Tests
check stderr as well as exit status: tracking can report a leak without returning
a failure status. Native numeric parse/allocation failures retain the runtime's
separate invalid-text, zero-denominator, and allocator-error behavior.

Contextual literals in generics must use the result type parameter, rather than
the typeid of that parameter. Generated type witnesses retain native overload
resolution instead of guessing types from spelling. Predefined names remain
shadowable, and native source is never given Thor's cloning rewrite.

## Validation

- 27 focused language tests plus 7 runtime integration/oracle tests: **34 passed**.
  These include 6,000-digit unquoted input, tracking, debug ASan/trace builds,
  custom allocators, constructors, aliases, immutable owners, aggregates, optional control flow,
  generic/native boundaries, and the commented demos.
- Language/runtime/source-map regression batch: **116 passed**, with later
  focused reruns covering the added optional/trace cases.
- Existing broad compiler suite: **858 executions**, including imported test
  classes discovered twice. All 827 non-debugger executions passed, with one
  explicitly gated Finder-launch test skipped. The 30 debugger executions were
  blocked by macOS launching inside the shell sandbox; rerunning the 15 distinct
  debugger tests plus the new numeric breakpoint test outside it gave **16 passed**.
- TLS/rename transport suite and new numeric editor tests: **148 passed**.
- Native TLS: **1,126 passed**. Native big runtime with ASan/tracking: **12 passed**.
- VS Code extension Node tests: **109 passed**. Real isolated VS Code extension
  host passed hover, completion, token, rename, and diagnostic integration,
  including the new predefined types and numeric properties.
- Additional deliberate numeric-copy leak test verified an original `.thor`
  allocation trace without generated helper names/paths.

There are no outstanding failures from these checks. The Finder test remains
opt-in. Planned arithmetic, constants/#run, general formatting, and unconstrained
generic computed-member dispatch remain in their subsequent stages; see the
explicit boundaries in the main design document.
