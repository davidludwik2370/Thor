# Lifetime contracts and bounded recursion: 2026-11-22

This follows the [static-storage and effect audit](static-lifetime-effects-2026-11-22.md).
The first three improvements and bounded recursive summaries are implemented in
Rust. The Python reference remains frozen. Both release compilers were measured
against the same current application sources, with no suppressions or project
cleanup edits added by this round.

## Implemented proofs

| Area | New proof and retained boundaries |
| --- | --- |
| Shared library contracts | Cached procedure proofs reuse the interpreter's reviewed formatting, parsing, endian, path, random and text contracts. Aliases, selected overloads, named arguments and defaulted scalar parameters work. Custom formatters, streams and generators remain boundaries. Borrowed formatting results and reference-bearing arguments cannot become reference-free summary results. |
| Native file resources | On the installed Darwin/Linux/Windows implementations, `os.open` and `os.create` establish an owned native file on success; `os.close` releases that same resource. Returned files transfer the obligation. Copied/custom `File` records and changed stream implementations remain unverified. Standard streams are borrowed static records; closing the descriptor does not free their record memory. |
| Image resources | Reviewed Raylib 6 contracts cover `LoadImageFromTexture`, `ExportImage`, `UnloadImage` and exclusive `ImageFlipVertical` replacement. Shallow image copies still share pixels. Existing views, enclosing expression temporaries and registered callbacks prevent treating replaced pixels as an unchanged buffer. Acquisitions with custom callbacks still carry a cleanup obligation. |
| Exclusive loop replacement | A local inline-element slice/dynamic-array owner can be deleted and replaced with a fresh buffer without endlessly accumulating abstract generations. The proof requires exclusive storage and unconditional cleanup before replacement. Surviving aliases, element addresses, captured cleanups, integer-carried addresses, nested owners, different allocators and conditional cleanup prevent equivalence. |
| Bounded recursive summaries | Cached field-traversal programs cover closed pointer/record readers and inline scalar-field writers, including mutual recursion, aliases, generics, named forwarding and cycles. Every concrete call rechecks the exact traversed fields. Pointer-field mutations/permutations, allocation, cleanup, opaque effects and reference-valued results retain ordinary analysis or explicit review warnings. |

The loop proof is capped at 4,096 nodes per scan and 16,384 per request, with an
additional eight-helper fresh-return depth limit. Recursive graph programs are
capped at 16 record shapes, 128 states and 128 visited place/state pairs per call,
within the existing summary construction budgets. Positive and negative proofs
are cached. Budgets are not raised to eliminate warnings.

## Related correctness checks

The broader proofs exposed cases where native metadata or modeled output writes
needed more precise provenance handling:

* Qualified runtime globals follow their real declarations. Lifetime vet loads
  metadata for unresolved globals actually used as values, rather than inspecting
  every imported SDK package. `os.args` and its strings are borrowed process-owned
  storage; explicit user replacements still follow their new references.
* Parsing clears overwritten output-count provenance. Endian writes preserve
  encoded pointer provenance in the actual byte slots, and reads recover it.
  Reordered named buffer/value arguments have the same effects as positional ones.
* `fmt.bprintf` follows its actual `buf` argument. Overwritten output bytes cannot
  falsely count as a verified transfer of ownership. Pointer formatting keeps its
  verb-dependent interpreter handling; printing an explicit freed pointee still
  produces a lifetime finding.
* Symbolic incoming pointer tails carry borrowed preconditions only. Opaque
  returned pointers and mutated unknown state never acquire those preconditions.
  Tracked addresses in scalar fields prevent recursive inline-value shortcuts.

Contracts require the verified installed library identity and implementation;
matching a procedure name is insufficient. The SDK used here is Odin
`8169255b97b85d6a08691d99764f51fb01f92b5a` (`dev-2026-10:8169255b9`). Image
replacement and resource effects were reviewed against Raylib C revision
`f5994221b4a1b57cae9e2f8871503cd309e12f14`, including
[`rtextures.c`](https://github.com/raysan5/raylib/blob/f5994221b4a1b57cae9e2f8871503cd309e12f14/src/rtextures.c).
These lifetime contracts do not grant purity or `no_alloc` exemptions.

## Regression coverage

* **56 new compiler/runtime tests** pass. Positive/negative pairs cover shared
  contracts, aliases, generics, defaults, named overloads, resources, conditional
  cleanup, escaped old generations, nested owners, recursive components, cycles,
  proof budgets, opaque callbacks and addresses carried in integers.
* Three native fixtures run checked and with
  `-o:speed -disable-assert -no-bounds-check -no-type-assert`. Active verdicts
  verify 1,000 buffer replacements with a tracking allocator, recursive readers
  and scalar writers, and real file creation/write/read/close/cleanup.
* **39 live TLS tests**, including four new contract/loop/recursion cases, pass.
  They check original-source ranges, unsaved invalidation and recovery, aliases,
  and image-copy lifetime warnings. **103 `no_alloc` regressions**, **104 Rust unit
  tests** and **7 assertion-hygiene tests** also pass. The frozen Python reference
  matches `python-reference-2026-11-21` unchanged.
* The final broad release run passed **493 of 494 tests**. The remaining existing
  static-counter test hit an internal Odin `src/array.cpp(13)` assertion
  (`Index 288` outside `0..<0`, SIGTRAP) in normal comptime mode. Its isolated rerun
  passed all four modes with the same compiler, sources and flags. It also passed
  in the preceding full run. No retry, suppression, or Odin modification was
  added to the test suite. That preceding run's sole failure was an old expected
  warning for read-only recursion, now provable; the corrected expectation and a
  new freed-cycle negative counterexample pass.
* All **20 valid project vet checks** and both explicit Poker test-mode checks
  pass with each compiler. All **62 generated Odin files are byte-identical**.
  These changes add no runtime instructions. The intentionally invalid static
  immutable sandbox example remains excluded from the valid-project sweep.

## Findings and performance

Unverified findings fall from **227 to 205**. Both sweeps have zero definite
findings; negative regression fixtures continue to require definite findings
where the graph proves an error. Changes in the project sweep are:

| Project | Before | After |
| --- | ---: | ---: |
| circles_on_dots | 19 | 2 |
| sketch | 9 | 6 |
| bin_tree | 33 | 31 |
| md5 | 3 | 2 |
| 15_puzzle | 112 | 113 |

Other project counts are unchanged. The remaining **205** comprise 100 recursive
summary boundaries, 57 statement/project/expression-budget boundaries, 16 loop
convergence boundaries and 32 other uncertain lifetime/cleanup/provenance cases.
The extra 15 Puzzle finding is an expression-depth boundary as shared proofs
change where bounded interpretation stops; it is not an invented definite leak.
MD5 still requires open-success and conditional standard-stream cleanup
correlation. Arbitrary recursive reference mutation remains unverified.

Sequential paired warm medians of three runs on this Mac use two backend threads,
`--comptime=required` and `--vet=lifetime,leak`. Both engines use the same output
layout and current source snapshot. Source hashes were stable throughout the
paired audit. The snapshot differs from the preceding report in `judge_test.thor`
and the excluded sandbox; comparisons below rerun both engines on current files.

| Project | Findings before → after | Graph before → after (ms) | Total vet before → after (s) | Normal transpile before → after (s) |
| --- | ---: | ---: | ---: | ---: |
| hilo_test | 1 → 1 | 18.6 → 19.3 | 0.267 → 0.255 | 0.162 → 0.178 |
| poker_pmap | 13 → 13 | 306.8 → 310.5 | 0.848 → 0.859 | 0.425 → 0.426 |
| sudoku | 2 → 2 | 227.0 → 208.7 | 0.444 → 0.421 | 0.132 → 0.121 |
| life | 3 → 3 | 38.0 → 38.7 | 0.245 → 0.244 | 0.126 → 0.125 |
| 15_puzzle | 112 → 113 | 167.4 → 174.7 | 1.038 → 1.029 | 0.380 → 0.377 |
| md5 | 3 → 2 | 3.9 → 4.0 | 0.221 → 0.205 | 0.139 → 0.139 |
| circles_on_dots | 19 → 2 | 152.1 → 161.4 | 0.578 → 0.592 | 0.334 → 0.361 |
| bin_tree | 33 → 31 | 41.0 → 39.6 | 0.373 → 0.352 | 0.180 → 0.179 |

These short warm samples show similar total vet cost, not a uniform speedup.
Additional proofs cost a few milliseconds in some graphs while eliminating
repeated interpretation elsewhere. Normal-transpile profiles contain neither
lifetime-vet phase. Their small timing differences include native backend/process
variation; no lifetime analysis was added to normal compilation.

[Raw diagnostics, timing samples, counters, source/compiler hashes and validation results](lifetime-contracts-recursion-2026-11-22.json).
