# Lifetime and leak vet: local project audit

The audit covers all 21 projects in `/Users/davidludwik/Code/Thor`, plus explicit
test-mode checks of both Poker packages. Commands select `--vet=lifetime,leak`,
use `--comptime=required`, and limit the backend to two compiler threads. They
check sources without running game windows or the long simulation workloads.
Original project sources are not rewritten. Unsafe alias/scoped cases are
minimized compiler fixtures and are never executed.
The current application files have no scoped `~` declarations, so dedicated
fixtures exercise their scoped variants without changing application ownership.

## Corrections

* Arena teardown no longer reports a second free for allocations individually
  released earlier. Region identity includes field/array-element addresses;
  independent arenas inside one aggregate have independent lifetimes. A runtime
  choice between arenas remains uncertain. Arena init/reset/destroy also check
  the lifetime of the arena header itself.
* Calls in slice bounds are evaluated. Boolean short-circuiting skips an
  unreachable right operand and joins an uncertain operand's effects. An
  `or_else` producer executes before both outcomes, with only its fallback
  conditional. This prevents false definite findings and missed releases.
* Expired helper-local slots no longer obstruct loop convergence when aliases
  are passed to readers. Slots with escaped addresses, deferred captures, heap
  ownership obligations, and returned references remain tracked. Reference-free
  numeric calls skip the additional graph scan. Shared arena-address metadata
  keeps ordinary abstract values compact.
* Allocated `fmt.aprint*`/`fmt.caprint*` results have independent storage, while
  formatting still checks reads of input aliases and registered custom-format
  boundaries. C-string formatters also accept immutable slice inputs consistently
  with the existing string formatters; `no_alloc` still rejects allocating calls.
* `strings.fields`, `split`, `split_n`, and `split_lines` own the outer header
  slice and borrow the source text. Cleaning that slice does not free its text.
  Scoped construction distinguishes independent outer storage from borrowed
  elements, including shallow clones. Borrowed elements still cannot escape a
  scoped source, and two owners of the same outer allocation remain invalid.

## Remaining findings

The project runs contain no definite lifetime/leak findings. Unverified findings
remain review requests, not assertions that the program is broken. Important
boundaries include foreign graphics calls, thread/callback ownership, recursive
graphs, capacity changes with independent views, arbitrary allocators, and the
documented analysis budgets. Some warnings newly appear when improved loop
convergence allows analysis to reach farther through a procedure.

Application cleanup also needs human review. For example, HiLo's `batches`
allocation has no visible delete, and HiLo Test's allocated `trial_counts` and
anonymous `pmap` result have no visible cleanup. The default allocator may be
managed externally, so these remain unverified rather than definite heap-leak
claims. This audit does not add deletes or suppressions to the applications.

## Validation

* 145 compiler regressions passed, including 24 new hardening tests with
  subcases for aliases, regions, scoped owners, expression evaluation, native
  allocation models, and expected negative cases.
* 14 live TLS tests and 104 Rust unit tests passed. Unsaved text clears repaired
  findings and keeps original diagnostic ranges.
* Checked and unchecked native runs exercise scoped/aliased input, independent
  formatting, split-string headers, and loop readers. Verdicts remain enabled
  with `-o:speed -disable-assert -no-bounds-check -no-type-assert`.
* Assertion hygiene and the frozen Python-reference checks passed.
* Generated Odin matched byte-for-byte across all 21 projects (66 files).
  These changes introduce no runtime instrumentation or extra cleanup/copies
  in those programs. Ordinary builds still skip lifetime/leak graph analysis.

The accompanying [machine-readable results](benchmarks/lifetime-project-audit-2026-11-22.json)
record final diagnostics, paired warm timings, compiler hashes, test-package
checks, source preservation, and generated-code comparisons.

## Results and timings

All 21 final vet commands and both explicit test-package commands succeeded.
There were **0 definite findings**. Unverified findings total **382 before and
413 after**: corrections can preserve additional conditional alternatives, so
a lower warning count is not the correctness criterion. No acknowledgements or
ownership edits were applied to reduce the counts.

The table shows all findings (unverified), lifetime/leak analysis time, and the
full final vet command including native checking. Times are medians of three
warmed samples on this Mac; they are not runtime benchmarks.

| Project | Findings before → after | Analysis before → after (ms) | Final vet (s) |
| --- | ---: | ---: | ---: |
| 15_puzzle | 92 → 131 | 156.8 → 160.2 | 1.037 |
| ascending_primes | 11 → 11 | 58.8 → 58.7 | 0.323 |
| big_num_demo | 0 → 0 | 0.9 → 0.9 | 0.206 |
| bin_tree | 29 → 27 | 44.0 → 37.0 | 0.311 |
| circles_on_dots | 25 → 25 | 136.6 → 135.1 | 0.585 |
| Dijkstra | 20 → 20 | 44.4 → 52.9 | 0.276 |
| double_bet | 0 → 0 | 1.9 → 2.0 | 0.211 |
| hilo | 9 → 9 | 13.9 → 13.1 | 0.196 |
| hilo_test | 11 → 11 | 23.9 → 22.7 | 0.295 |
| life | 17 → 17 | 53.9 → 40.3 | 0.252 |
| md5 | 13 → 13 | 3.4 → 3.7 | 0.235 |
| othello | 11 → 11 | 34.1 → 39.6 | 0.234 |
| poker | 34 → 32 | 296.6 → 286.5 | 0.813 |
| poker_pmap | 35 → 31 | 294.5 → 288.8 | 0.837 |
| pythagorean_tripples | 4 → 4 | 20.6 → 22.2 | 0.270 |
| react_time | 15 → 10 | 6.9 → 6.7 | 0.183 |
| sandbox | 0 → 0 | 3.2 → 3.2 | 0.371 |
| sketch | 36 → 37 | 24.2 → 20.8 | 0.257 |
| sudoku | 10 → 13 | 164.2 → 229.3 | 0.427 |
| tetris | 6 → 7 | 54.6 → 68.8 | 0.259 |
| triangle_solver | 4 → 4 | 17.3 → 18.4 | 0.205 |

There is no blanket vet speedup claim. Correct conditional-state joins cost
more in some programs; Sudoku analysis rose from about 164 ms to 229 ms. The
most expensive analysis in this sweep remains below 300 ms. Ordinary lowering
did not run this analysis and showed no material slowdown in the three warm
control cases:

| Normal transpile | Before (s) | After (s) |
| --- | ---: | ---: |
| hilo_test | 0.181 | 0.174 |
| poker_pmap | 0.439 | 0.426 |
| sudoku | 0.125 | 0.126 |

Final generated-code comparisons still match all 66 Odin files, and all 27
application source files retain their recorded hashes. The measured correctness
work does not add application runtime instructions.
