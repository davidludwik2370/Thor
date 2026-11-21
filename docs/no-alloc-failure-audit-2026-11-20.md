# Fatal-reporting allocation audit, 2026-11-20

The [allocation contract](no-alloc.md#fatal-diagnostic-reporting) now excludes
fatal diagnostic reporting. Ordinary calls, eager successful-path arguments,
unknown callbacks, and collection growth retain their checks.

## Reviewed paths

- Builtin `panic`, `unimplemented`, assertions, ensures, and their contextless
  variants, including constant aliases and runtime exports.
- `fmt.assertf`, `fmt.ensuref`, `fmt.panicf`, and the assertion/ensure/panic
  families in `core:log`. `log.fatal` remains checked because it returns.
- `or_panic` and `or_else panic`: the producer remains checked and an allowed
  fatal fallback cannot hide work on a successful continuation.
- Fixed-buffer `strconv` integer formatting and `io` integer writers. Invalid
  bases may panic; they do not require normal-path allocation.
- `fmt` printf variants: runtime formats, dynamic widths/precision, large
  formatting requests, and argument-count checks. This allocation proof does
  not validate formats or make disabled native checks safe. Argument creation,
  formatter registration, and writer/flush callbacks retain their checks.

Only known fatal reporters receive these rules. A `-> !` signature can also
describe an allocating infinite loop, so it is insufficient on its own. Work
before a panic remains checked; the checker does not exempt arbitrary blocks
merely because they end in a panic. Library summaries verify actual SDK source,
including unsaved overlays, and reject collection overrides or changed source.

Odin's disabled-assert behavior was checked directly: `-disable-assert` removes
direct/constant-alias assertion calls, but calls through runtime procedure
variables still evaluate arguments and execute the selected body. The proof
preserves that distinction. Callable declarations retain their own argument
names and defaults rather than inheriting the current callback target's names.

## Control flow and generated code

Fatal reporting in one path cannot hide allocation on another. Regression
cases cover runtime and generic `when` branches, ternaries, switches and match
side effects, callback reassignment across `fallthrough`, loops, defers, optional fallbacks, and their continuations. A
mutable condition fact never grants an exemption for eagerly built messages.

The allocation proof adds no runtime wrapper, allocator change, instrumentation,
or ABI parameter. A related f-string bug was corrected: argument helpers for
nonreturning calls now retain `-> !` and omit an unreachable cleanup defer.
Ordinary returning calls retain their existing temporary cleanup.

Runtime checks exercise both application and temporary allocator counts,
checked builds, and `-o:speed -disable-assert -no-bounds-check -no-type-assert`.
Fatal-path tests install an allocating reporting handler and verify its exit
code/output without creating OS crash-report dialogs. TLS checks cover unsaved
normal-path violations and recovery to valid panic-only code.

## Remaining conservative boundary

The RNG limitation recorded during this audit was resolved by the follow-up
RNG work on 2026-11-21. Partial SDK packages now discover missing helpers, the
native default RNG has a source-verified boundary, and custom/ambient generator
callbacks retain transitive allocation checks. See [the current contract](no-alloc.md#random-number-generators).

## Validation

The focused cases live in `tests/test_no_alloc_failure.py`; existing allocation,
I/O, editor, and f-string lifetime suites provide the surrounding regressions.

- Full allocation/edge/I/O/variadic/failure/TLS run: 129 tests passed.
- After the final SDK-verification and lookup changes, the installed release
  compiler passed all 49 failure, I/O, and TLS tests. An additional focused
  20-test run covered the changed proof paths, recursion, aliases, and shadowing.
- F-string tuples and temporary lifetime regressions: 27 tests passed, including
  checked and unchecked runtime execution.
- Rust library unit tests: 97 passed. Assertion hygiene: 7 passed. The reference
  inventory/local-link check and whitespace checks also passed.
- TLS was rebuilt, and its live tests cover allowed fatal formatting, unsaved
  ordinary allocation errors, original diagnostic ranges, and recovery.

[Paired timing results](benchmarks/no-alloc-failure-2026-11-20.md) show unchanged
ordinary lowering times in the measured cases. The existing proof over 1,000
procedures changed from 168.89 ms to 173.14 ms. A new 100-call formatted-check case
takes 77.45 ms in total. Native output is identical in the compared programs;
the benchmark report explains the normalization and caller-metadata setup.
