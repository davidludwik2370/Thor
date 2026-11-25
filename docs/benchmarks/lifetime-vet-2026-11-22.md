# Lifetime/leak vet validation — 2026-11-22

The analysis runs only when Thor vet selects `lifetime` or `leak`. Ordinary
lowering does not call it, and no application runtime code is added.

## Paired performance checks

Mac ARM64, optimized Rust compiler, `--comptime=required -thread-count:1`.
Two warmups followed by five alternating before/after samples. Both binaries
use the same current native parser. Compiler evaluation/parser caches are warm;
these are lowering CLI wall times, excluding Odin backend compilation. The
baseline includes the scoped-owner feature but predates lifetime/leak vet.

| Project | Normal before | Normal after | New vet analysis | Lifetime-only vet total |
| --- | ---: | ---: | ---: | ---: |
| hilo_test | 0.197 s | 0.204 s | 22.9 ms | 0.326 s |
| poker_pmap | 0.469 s | 0.489 s | 294.1 ms | 0.938 s |

The small wall-time differences are reported as measured, rather than as proof
of zero compiler overhead. The lifetime stage did not run in either normal
transpile. Normal lowering remains under half a second on these inputs.

The vet total is `thor vet --vet=lifetime,leak`, including native checking;
it excludes the existing convention correction-validation work. Analysis timings
are inclusive profiled stage medians over three samples.

Normalized emitted Odin was byte-identical before/after for both projects.
Only the temporary output-directory path was normalized. Emission hashes,
source/binary hashes, raw timings and work counters are in
[lifetime-vet-2026-11-22.json](lifetime-vet-2026-11-22.json). Together with the
vet-only execution gate, this establishes that this feature adds no application
runtime instrumentation or runtime overhead.

HiLo reported 11 unverified findings, Poker 35, and neither reported a definite
issue in this run. These are not claims that every operation was proved safe.
Poker reaches the deterministic project work budget and explicitly reports the
remaining proof boundary. Call/loop/parameter and project budgets prevent SDK
shapes or repeated complex roots from recreating the previous vet slowdown.

## Validation

* 75 dedicated CLI regression tests cover aliases, helper outputs, globals,
  container elements/keys, subslice projections and clones, immutable/static
  storage, f-strings, comprehensions, pure scratch/promotion, native deferred
  results, arenas, generics, callbacks, assertion elision, ownership-safe apply,
  original/related diagnostic offsets, import source identities and budgets.
  Unsafe fixtures are checked but never run.
* Six new graph invariant tests; all 104 Rust library tests pass. All-target
  Cargo checking passes.
* 31 live TLS convention/parameter/lifetime tests pass. Four focused lifetime
  TLS tests also pass with the server built with assertions, bounds checks and
  type assertions disabled; the checked server was restored afterwards.
* 1,169 native TLS tests pass in both checked and unchecked configurations;
  152 editor tests pass. Assertion hygiene passes (7 tests).
* A broader 297-case compiler run covered conventions, immutable oracles,
  parameter contracts, scoped runtime owners, native vet features and request
  caching. One cache-hit assertion was invalidated when the test executable was
  rebuilt during that run. The full 17-test request-cache suite subsequently
  passed with a stable executable, and that specific case was also rerun on the
  final optimized compiler. No cache implementation change was needed.
* Existing scoped-owner runtime fixtures exercise checked and unchecked native
  flags with persistent `if ... { panic(...) }`/Python verdicts. The frozen
  Python reference manifest remains unchanged.

See [Lifetime and leak checks](../lifetime-vet.md) for the supported proof
boundaries, diagnostic certainty, and manual acknowledgements. `--apply` never
inserts cleanup, changes ownership, or creates suppressions.
