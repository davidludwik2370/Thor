# Formatting 0.30.0 verification — 2026-11-01

The expanded formatting implementation and original-source tooling checks pass:

- 16 focused formatting tests, including a 48-case independent Decimal rounding
  oracle, allocation tracking, 1,000-place output, fn/#run, cache invalidation,
  nested ownership, string/byte truncation, and mapped contract errors.
- All 1,127 native TLS tests.
- All 120 Node extension tests, including themed token/bracket metadata.
- Four live TLS regressions for formats, unsaved diagnostics, recovery, and full
  demo semantic-token ranges.
- The full isolated VS Code integration test, including a live Rational g/G
  error and recovery after an unsaved correction.
- All 25 CodeLLDB regressions, including stepping into a numeric field expression
  at the original .thor location.
- Both `demo` and the commented `examples/demo` ran successfully.

The broad compiler run exercised **1,001 tests**: 997 passed, one optional Finder
launch test was skipped, and three failures received follow-up investigation:

1. The existing SDK-overlay test exposed an older source-snapshot initialization
   gap. The snapshot now includes the two diagnostic metadata maps introduced in
   the prior diagnostic work. Its regression passes with the fix.
2. A cache-identity regression overlapped edits to the compiler/runtime sources.
   Its unchanged-source assumption did not hold during that run. It passed in
   multiple fresh runs after the source files were frozen.
3. One integer-only SDK `#run` fixture hit a stock Odin compiler SIGSEGV. The macOS
   crash report shows `alloc_cstring → lb_type_internal → lb_type →
   lb_generate_procedures_and_types_per_module`, matching the previously recorded
   parallel-code-generation crash signature. This does not independently prove
   the same race caused this occurrence. See the existing
   [upstream investigation](../upstream/odin-codegen-race/README.md).

A fresh **50-test** compilation/SDK/edited-build run passed, covering all three
cases. Ten additional fresh runs of the crashing fixture (both define branches
per run) also passed. No compiler retry or diagnostic suppression was added. The optional
Finder launch check requires `THOR_TEST_LAUNCH_SERVICES=1` and is unrelated to
formatting.

The tested local extension is 0.18.0, TLS is 0.23.0, and the compiler is 0.30.0.
Reload the VS Code window after installing the extension to reload its grammar.
