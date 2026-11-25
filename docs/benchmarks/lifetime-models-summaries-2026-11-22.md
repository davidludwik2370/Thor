# Lifetime models and cached procedure proofs: 2026-11-22

This follows the [safe-model audit](lifetime-safe-models-2026-11-22.md).
The comparison uses a saved pre-change release compiler and the new release
compiler on exactly the same updated application sources. No suppressions or
ownership auto-fixes were inserted.

## Completed changes

| Area | Verified behavior |
| --- | --- |
| Formatting | Added missing `fmt.printfln`, `eprint*`, `fprintfln`, `bprintfln`, and `assertf` models. Aliases and freed inputs remain checked; registered formatters keep their review boundary. Disabled assertion arguments follow native elision. |
| Builtins | `free` and constant aliases release their targets. `remove_range` neither releases the outer buffer nor discharges ownership of discarded nested elements. User declarations with the same names receive no builtin contract. |
| Endian I/O | The installed library's unchecked 16/32/64-bit little/big-endian buffer getters/setters borrow their buffers without retaining them. Invalid targets remain diagnosed. |
| Inline copies | Spread append into fixed-capacity arrays and `copy` copy elements, not the source buffer's address. Nested pointer/slice fields keep their references. |
| Growth | Standalone root-level `resize`/`reserve` accepts an exclusive tracked owner. Other live views, deferred captures, helper-call contexts and enclosing expression temporaries remain protected. |
| Type facts | Reference-free values do not acquire fictitious references at an abstraction depth limit. Known foreign scalar returns, including through `using`, have no invented ownership. Native result discovery visits user calls and requested declarations, not unused runtime helper bodies or unrelated SDK imports. |

Procedure summaries are cached per request and generic specialization, after
verifying bodies, defaults and complete dependency components. Read masks follow
named arguments, slicing, `using` and recursion; caller storage is checked on
every summarized read. Header-only inspection remains distinct from reading
backing data. Mutable callbacks, reference mutation/escape, deferred effects,
allocation, opaque calls and reference-valued results retain full analysis or
explicit review boundaries. Address provenance carried by integers, including
integer elements inside a borrowed buffer, bypasses the value shortcut.
Positive and negative proofs are cached, with explicit work bounds.
See [the analysis contract](../lifetime-vet.md#cached-procedure-proofs).

## 15 Puzzle cleanup

Both strings returned by `commaSep` in `runTests` are now deleted: the total
count and the failure count. The returned builder view remains alive until its
caller finishes formatting it.

The actual old/new `commaSep` and `runTests` bodies were extracted into a native
tracking-allocator harness, with solver/GUI dependencies stubbed. Both report
branches execute with two trials. Before cleanup they leave **24 bytes**;
afterward they leave **zero**, with no bad frees. This passes checked and
`-o:speed -disable-assert -no-bounds-check -no-type-assert` runs. The complete
interactive project also builds in both configurations. Its UI was not exercised.
A committed portable regression independently covers both report paths.

## Validation

* **261** final release compiler/lifetime/scoped-owner/vet-ignore/request-cache
  regressions passed; all **60** focused model/summary/native-cleanup tests also
  passed separately on that binary.
* **19** live TLS tests, **104** Rust unit tests, and **7** assertion-hygiene tests
  passed. The frozen Python reference is unchanged.
* Safe/unsafe pairs cover inline versus shallow copies, nested lost owners,
  aliases and views, pointer-backed `using`, defaults, generics, recursion,
  runtime callback changes, forwarded readers, source-range recovery, integer
  address casts, disabled assertions and bounded-proof fallbacks.
* All **21** project vet checks and both explicit Poker test-mode checks complete
  successfully. Every generated Odin file is byte-identical between compilers
  on the same updated sources: **21 projects, 66 files**. The analysis changes add
  no runtime instructions. The cleanup edits intentionally add the requested frees.
* One earlier debug-compiler cache run, concurrent with release compilation,
  returned an Odin backend SIGSEGV. The isolated release rerun and full final
  release suite passed; no change was made to Odin.

## Findings and performance

Unverified findings change from **337 to 294**, with **zero definite findings** in the final project sweep. The preceding audit's 335 findings used the earlier application sources; the baseline here uses the same new cleanup source as the new compiler. Remaining warnings include foreign graphics effects, reference graphs outside the supported summary domain, correlated buffer swaps, arbitrary allocators and explicit graph budgets. They still require review.

Samples below are sequential paired warm medians of three runs, on an otherwise idle Mac, with two backend threads and `--comptime=required`. Vet selects `lifetime,leak`. The metadata column includes the new vet-only native-result lookup; it is not hidden inside the graph-only numbers.

| Project | Findings before → after | Graph before → after (ms) | New metadata (ms) | Total vet before → after (s) |
| --- | ---: | ---: | ---: | ---: |
| hilo_test | 3 → 3 | 19.0 → 19.8 | 2.7 | 0.267 → 0.272 |
| poker_pmap | 20 → 15 | 304.7 → 293.7 | 27.2 | 0.837 → 0.827 |
| sudoku | 13 → 11 | 182.0 → 188.5 | 13.7 | 0.371 → 0.394 |
| life | 6 → 6 | 35.5 → 36.2 | 2.9 | 0.234 → 0.234 |
| 15_puzzle | 133 → 127 | 156.0 → 159.3 | 13.3 | 0.986 → 0.997 |
| md5 | 15 → 5 | 3.9 → 3.7 | 2.6 | 0.210 → 0.204 |

| Normal transpile | Before (s) | After (s) |
| --- | ---: | ---: |
| hilo_test | 0.160 | 0.166 |
| poker_pmap | 0.420 | 0.410 |
| sudoku | 0.118 | 0.118 |
| life | 0.123 | 0.120 |
| 15_puzzle | 0.346 | 0.349 |
| md5 | 0.142 | 0.144 |

None of the normal-transpile profiles contains either lifetime-vet phase. These are small warm timing samples, not a claim of a speedup for every project. Correctly tracing additional effects can cost work, and unsupported reference-mutating procedures still use the full graph. Cached verified summaries remove repeated proof/interpreter work where their contract applies. The first timing pass caught and rejected a broad SDK-import lookup; the final implementation restricts that lookup and has a regression guarding its user-call inventory.

[Raw diagnostics, samples, counters, compiler/source hashes and cleanup oracle](lifetime-models-summaries-2026-11-22.json).
