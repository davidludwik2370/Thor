# Cleanup fixes and safe lifetime models: 2026-11-22

This follows the [original project audit](../lifetime-project-audit-2026-11-22.md).
The project sources now include explicit cleanup; the earlier report records the
state before those application edits. No vet suppressions were added.

## Project cleanup

* HiLo deletes its batch slice and destroys the thread pool after joining it.
* HiLo Test names and deletes both the trial-count comprehension and the `pmap`
  output. Summing the output does not take ownership of it.
* MD5 deletes the final input buffer and closes a file it opened, preserving stdin.
* Ascending Primes keeps and deletes the final promoted number array.
* Circles on Dots deletes the final points, circles, and offset arrays, including
  replacement headers after resets.
* Sketch frees each stroke's points and then both undo/redo history buffers.
  Pop/append continues to transfer the same stroke between histories.

## Analysis corrections

Reviewed builder, iterator, UTF-8 conversion, primitive numeric reduction,
sort-comparator and `pmap` models distinguish reads, borrowed results and owned
outer storage. Known callbacks remain inspected, including aliases, generics,
defaults, multiple results and result-type overrides. Opaque callbacks, shared
reference changes and foreign implementations retain their review boundaries.

`pop` preserves an element's reference edges without inventing a leak merely
because it moves between containers. Dropped nested owners still have cleanup
obligations. Contextual record literals and by-reference range writes retain
edges, including into fixed arrays. Allocation-site reuse no longer revives a
freed generation. Dead scalar/helper slots can be compacted without losing
escaped addresses or references temporarily held by argument evaluation.
An exhausted proof budget reports the boundary rather than inventing leaks
from downstream defers that the analysis did not reach.

Dependent-type refinements are cached by expression and generic substitutions.
Reviewed call models are cached; ordinary builds still skip lifetime graph
analysis. These changes add no runtime tracking or instructions.

## Validation

* 187 compiler regressions passed (the complete lifetime, hardening, safe-model,
  native cleanup, scoped-owner and vet-ignore suites), plus 16 live TLS tests,
  104 Rust unit tests and 7 assertion-hygiene tests. The frozen Python reference
  remains unchanged.
* Safe/unsafe pairs cover aliases, temporary stack addresses, borrowed iterator
  tokens, builder backing, contextually typed literals, by-reference range
  stores, reused allocation generations, opaque callbacks, nested output
  ownership and independent `pmap` result storage. Different generic
  instantiations cannot share a cached reference/scalar verdict.
* Actual MD5, Ascending Primes, HiLo and HiLo Test sources passed checked and
  unchecked native runs before and after cleanup. Temporary copies reduced
  simulation counts to 10,000; the application files retain their original
  counts. Post-cleanup tracking-allocator checks passed in all updated runs.
  Deterministic output matched; randomized simulation results stayed within
  their expected bounds. The pre-existing MD5 hex-padding output issue was
  preserved, rather than folded into these ownership changes.
* Circles on Dots and Sketch build in checked and unchecked configurations.
  Their interactive UI paths were not exercised. Native tracking fixtures
  exercise final-header replacement, undo/redo transfer and nested cleanup,
  thread-pool join/destroy, and comprehension/`pmap` cleanup. Oracles remain
  enabled with `-o:speed -disable-assert -no-bounds-check -no-type-assert`.
* All 21 project vet checks and both explicit Poker test-mode checks passed.
  The old and new compilers emitted identical Odin for the same updated sources:
  all 21 projects, 66 files. The six intentional cleanup edits themselves add
  the requested cleanup operations; the analyzer changes add no runtime work.

## Results and performance

Both compiler revisions below use the **same updated project sources**, so this
comparison isolates analysis changes from the cleanup edits. The archived audit
used the original application files. Samples are sequential, paired, warmed
medians of three runs, with the backend limited to two threads.

The sweep has **0 definite findings**. Unverified findings changed from **416 to 335**. Remaining warnings still include foreign graphics APIs, recursive graphs, correlated buffer swaps/loop summaries, arbitrary allocators and explicit proof budgets. A safe application can still require review at those boundaries; the checks do not claim universal memory safety.

| Project | Findings before → after | Analysis before → after (ms) | Vet before → after (s) |
| --- | ---: | ---: | ---: |
| hilo_test | 12 → 3 | 22.8 → 19.0 | 0.266 → 0.260 |
| life | 17 → 6 | 40.6 → 39.3 | 0.246 → 0.246 |
| poker_pmap | 31 → 20 | 296.0 → 303.7 | 0.835 → 0.828 |
| sudoku | 13 → 13 | 229.0 → 193.3 | 0.424 → 0.392 |

| Normal transpile | Before (s) | After (s) |
| --- | ---: | ---: |
| hilo_test | 0.158 | 0.159 |
| life | 0.124 | 0.122 |
| poker_pmap | 0.429 | 0.426 |
| sudoku | 0.119 | 0.119 |

[Raw findings, timings, compiler/source hashes and validation results](lifetime-safe-models-2026-11-22.json). See [the analysis contract](../lifetime-vet.md) for model details and limits.
