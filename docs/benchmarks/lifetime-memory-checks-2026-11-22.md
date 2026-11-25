# Lifetime precision and memory checks: 2026-11-22

This follows the [contracts and recursion round](lifetime-contracts-recursion-2026-11-22.md).
All four precision improvements, allocator mismatch checks and definite byte-buffer/
termination checks are implemented in Rust. The frozen Python compiler and the
application sources were not changed by this round. Both release compilers were
rerun on the same project snapshot; no suppressions were added.

## Implemented behavior

| Area | Proof and retained limits |
| --- | --- |
| Cached local scratch | Local stack views and inline-element `new`/`make` storage can participate in cached non-retaining proofs when scoped cleanup or an immediately registered unconditional defer protects the allocation. Early unprotected exits, arbitrary allocators, header replacement, nested owners and escaping addresses still require ordinary analysis or review. Fixed-capacity inline append/pop/clear effects can be summarized. |
| Branch correlation | Bounded facts connect native file success/error results, standard-stream choices and conditional cleanup. Independent file owners are preserved. Unknown values and arbitrary pointer identities are never treated as proven resource choices. |
| Loop and nested provenance | Inline records/arrays do not consume reference-depth budget. Only expired private current-frame slots are removed at completed loop back-edges. Live aliases, caller temporaries, exports, deferred inputs, allocator owners and queued tasks protect their storage. |
| Thread pools | Reviewed `core:thread` init/start/add-task/finish/destroy contracts check payload lifetime until completion. Callback identities and effects are rechecked. Custom hooks, copied pool headers, unresolved callbacks and shared reference changes remain review boundaries. This is not a data-race proof. |
| Allocator mismatch | Releases through proven distinct native heap/arena identities are findings. Pointer/slice/string cleanup follows the selected allocator; dynamic containers follow their stored allocator. Unknown custom allocators are not guessed. A mismatched cleanup does not discharge the owner. |
| Byte extents and termination | Known byte storage is checked for oversized/negative raw operations, literal nil accesses and proven overlap in non-overlapping copies. Known nonzero contents through physical allocation end reveal missing C-string terminators. Unknown sizes, writes and layouts cannot create an invented definite finding. Cached readers forward size and termination requirements to their actual callers. |

Byte contents retain at most 64 overrides, pool queues at most eight call sites,
and resource choices at most eight origins. Summary requirement propagation has
128 rounds and a bounded requirement size. Existing interpretation budgets were
not raised to remove warnings. Unsupported cases retain explicit boundaries.

## Related correctness fixes

* Native Odin defers capture allocator context at registration. Vet now models
  that captured context, while ordinary bindings continue to be evaluated at
  cleanup time. Active native-executable checks cover changed context and cleanup.
* Cached writers invalidate old scalar/branch and byte-content facts, including
  forwarded record fields, globals and every node of a recursive writer. Readonly
  calls and writes to by-value array copies preserve the source facts.
* Asynchronous callbacks cannot use scheduling-time scalar values to hide a
  freeing branch. `pmap` distinguishes inline scalar writes from shared-reference
  changes; repeated callback cleanup remains visible.
* Named arguments require actual `FieldValue` syntax. Positional selectors such
  as `holder.pointer`, `holder.loc` and `holder.allocator` retain their real roles
  in cleanup, formatting, builders, splitting and parsing.
* Native formatting reads a C-string even with `%p`, because `fmt_cstring`
  converts it to a string before dispatch. CString identity casts do not read it.
  Growth and opaque/format/I/O writes invalidate stale extent/content knowledge.
* Repeated resolver constants and types are cached by exact specialization.
  Type-cache lookup happens before resolver work, including for concrete types.
  Caller storage, mutable graph values and asynchronous scheduling facts are
  never cached as resolver constants.

The SDK is Odin `8169255b97b85d6a08691d99764f51fb01f92b5a` (`dev-2026-10:8169255b9`).
Reviewed Raylib contracts retain the pinned C revision recorded in the raw data.
These memory contracts do not grant purity or allocation-free exemptions.

## Regression validation

* **81 new tests**: 77 compiler/runtime tests plus four live TLS tests. Positive
  cases are paired with escaping/early-exit, alias, freed-owner, wrong-allocator,
  recursive-writer, async-cleanup, extent and termination counterexamples.
* Final release suite: **571/571 passed**.
  Live TLS: **43/43 passed**, including unsaved invalidation/recovery, aliases,
  original call ranges and UTF-16 positions.
* Allocation-contract checks: **103/103 on the final release compiler**; the
  broader **174/174 development checks** also pass. **104 Rust unit tests** and
  **seven assertion-hygiene tests** pass. The Python reference remains frozen.
* Three executable fixtures run checked and with
  `-o:speed -disable-assert -no-bounds-check -no-type-assert`, checking joined
  worker results, byte copies/zero termination and captured allocator context.
  Their verdicts use explicit `if`/`panic` checks.
* All 20 valid project vet checks and both explicit Poker test-mode checks pass
  with both compilers. The intentionally invalid immutable-static sandbox sample
  is excluded from the valid-project sweep.
* All **62 generated Odin files are byte-identical** across the 20 projects.
  Normal-transpile profiles contain no lifetime-vet phase. The implementation
  changes analysis only and adds no runtime instructions or cleanup behavior.

## Findings and timing

The paired sweep changes unverified findings from **205 to 115**.
Both sweeps have zero definite findings. The remaining 115 comprise 52
statement/project/expression-budget boundaries, 19 recursive-summary boundaries,
15 loop-convergence boundaries and 29 other uncertain ownership/provenance cases.
The raw data records certainty and message totals; unknown boundaries are not
promoted to verified safety merely to reduce this count.

| Changed project | Before | After |
| --- | ---: | ---: |
| 15_puzzle | 113 | 23 |
| bin_tree | 31 | 29 |
| Dijkstra | 10 | 18 |
| hilo | 5 | 1 |
| life | 3 | 0 |
| md5 | 2 | 0 |
| sudoku | 2 | 5 |

Broader proof coverage changes which paths reach the existing analysis limits.
Dijkstra and Sudoku therefore have additional unverified findings; these were
not converted to definite memory errors or suppressed to improve the total.

Three sequential paired warm samples per engine/project on this Mac use two
backend threads and `--comptime=required`. Other builds and tests were stopped
before measurement. Source hashes stayed unchanged. Total vet includes backend
checking and process startup; graph time is the `vet.lifetime` profile phase.

| Project | Findings before → after | Graph before → after (ms) | Total vet before → after (s) | Normal transpile before → after (s) |
| --- | ---: | ---: | ---: | ---: |
| hilo_test | 1 → 1 | 19.3 → 15.3 | 0.250 → 0.246 | 0.156 → 0.156 |
| poker_pmap | 13 → 13 | 275.5 → 448.7 | 0.791 → 0.960 | 0.407 → 0.407 |
| sudoku | 2 → 5 | 167.7 → 228.9 | 0.361 → 0.422 | 0.115 → 0.115 |
| life | 3 → 0 | 36.3 → 25.3 | 0.227 → 0.214 | 0.121 → 0.121 |
| 15_puzzle | 113 → 23 | 154.5 → 94.5 | 0.971 → 0.540 | 0.340 → 0.338 |
| md5 | 2 → 0 | 4.0 → 4.9 | 0.200 → 0.198 | 0.140 → 0.140 |
| circles_on_dots | 2 → 2 | 152.3 → 79.3 | 0.577 → 0.499 | 0.341 → 0.339 |
| bin_tree | 31 → 29 | 37.0 → 31.6 | 0.332 → 0.323 | 0.165 → 0.166 |
| hilo | 5 → 1 | 7.6 → 12.8 | 0.180 → 0.184 | 0.111 → 0.111 |
| sketch | 6 → 6 | 22.4 → 24.7 | 0.235 → 0.236 | 0.140 → 0.141 |

These are short warm samples, not a claim of uniform speedup. Extra proof facts
have a bounded analysis cost, while caching removes repeated interpretation.
Poker's total vet median increases by 168 ms (0.791 to 0.960 seconds); Sudoku adds
61 ms. Several other projects improve, including 15 Puzzle by 431 ms. This round
does not eliminate every extra vet cost introduced by the new checks.
Normal compilation does not acquire this opt-in analysis work. Runtime behavior
and performance are preserved by unchanged generated code.

[Raw diagnostics, timing samples, counters, compiler/source hashes and validation results](lifetime-memory-checks-2026-11-22.json).
