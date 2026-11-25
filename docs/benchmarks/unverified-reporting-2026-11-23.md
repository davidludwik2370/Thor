# Unverified analysis reporting: 2026-11-23

Unfinished lifetime, leak and correction checks now produce a nonfatal summary
by default. `--unverified` / `--unverified=show` displays individual information
diagnostics; `--unverified=error` makes them errors. This is a reporting policy,
not a reduction in checking or a memory-safety guarantee. See
[the reporting contract](../lifetime-vet.md#reporting-incomplete-analyses).

## Reporting work

Vet diagnostics already carry original-source coordinates. They now bypass
generated-helper source mapping and name cleanup, including when mixed with
native diagnostics. Deduplication uses typed keys rather than serialized JSON
keys. Plain-text summaries count hidden notices without building their extra
explanations. JSON, TLS and explicit details retain that evidence.

No interpretation budget, procedure summary, allocation, cleanup or emitted
runtime operation changed. Ordinary builds do not run lifetime analysis unless
Thor vet is enabled.

## Local projects

Compared the previous optimized compiler snapshot with the final optimized
compiler, sequentially, on macOS ARM64. All 20 valid local projects retained
identical diagnostic evidence: **99 unverified findings at 74 locations**.
All **62 generated Odin files** were byte-identical. Project source hashes
were unchanged. The intentionally invalid sandbox was excluded.

The table contains warm wall-time medians from three samples per compiler and
command. Vet selects lifetime/leak checks; compile-time evaluation is required
only. This isolates reporting without running the applications or applying edits.
These small samples show mostly similar times, with improvements in several
projects; differences of a few milliseconds should not be treated as a stable
speedup or regression.

| Project | Vet before | Vet after | Transpile before | Transpile after |
| --- | ---: | ---: | ---: | ---: |
| hilo_test | 0.243 s | 0.247 s | 0.150 s | 0.151 s |
| poker_pmap | 0.983 s | 0.987 s | 0.406 s | 0.414 s |
| sudoku | 0.537 s | 0.550 s | 0.116 s | 0.122 s |
| 15_puzzle | 0.573 s | 0.555 s | 0.348 s | 0.348 s |
| bin_tree | 0.335 s | 0.304 s | 0.165 s | 0.164 s |
| sketch | 0.249 s | 0.241 s | 0.140 s | 0.140 s |

New reporting-phase medians were 0.002–0.337 ms for these projects. Ordinary
transpile profiles contained no lifetime-analysis phase. Regression tests also
compare analysis counters across summary, show and error modes: changing the
mode does not repeat or omit analysis.

Summary output reduced stderr from 26,312 to 188 bytes for 15 Puzzle, 15,645 to
188 bytes for bin_tree, and 5,814 to 187 bytes for poker_pmap. Individual details
remain available on request and in JSON.

## SDK-heavy check

The pinned migrated Odin SDK core suite was checked using `-file -all-packages
-build-mode:test --odin-compatible --vet=lifetime,leak --comptime=required
-thread-count:1`. The test binary was built, not executed. Four sequential runs
used before/after/after/before order, two samples per compiler.

| Measurement | Before | After |
| --- | ---: | ---: |
| Median command wall time | 37.103 s | 33.058 s |
| Unverified findings | 469 | 469 |
| Lifetime operations | 67,054 | 67,054 |
| Lifetime objects | 3,718 | 3,718 |
| stderr bytes | 283,517 | 190 |
| stderr lines | 2,935 | 2 |

The summary distinguishes 469 findings from 364 source locations. Reporting
took approximately 4.4–4.6 ms after the change. The measured total includes native
compilation, lowering and permission analysis; it is not lifetime-analysis time
alone. Two samples are limited evidence, but both improved while retaining the
same analysis work and notice count.

## Validation

Runs overlap; these counts must not be added together:

* Broad memory, scoped-owner, immutable, vet, CLI and diagnostic regression run:
  **653 tests, 652 passed and 1 skipped**.
* New command/reporting policy suite: **18/18 passed**, including all modes,
  build/check/run/test/publish, native vet and warning flags, JSON, safe apply,
  original source ranges and strict execution gating.
* Live TLS suites: **57/57 passed**, including six new summary/details/strict,
  unsaved recovery, ranges, ordinary fixes and acknowledgement cases. An earlier
  focused lifecycle/precision combination passed **29/29**.
* Rust units: **111/111 passed**. Extension tests: **153/153 passed**, including
  vet enabled/disabled, all reporting modes and compile-time-mode combinations.
* Assertion hygiene: **7/7 passed**. Frozen Python-reference verification and
  `git diff --check` passed.
* Positive native execution checks run checked and with
  `-o:speed -disable-assert -no-bounds-check -no-type-assert`, using explicit
  `if`/`panic` or Python verdicts. Unsafe fixtures are checked without execution.

The updated TLS executable and VS Code extension were built and installed
locally. Reloading the VS Code window loads the new `tls.unverified` setting.
No Windows or Linux validation is claimed in this round.

[Raw samples, source hashes, diagnostic evidence and code-generation comparisons](unverified-reporting-2026-11-23.json)
