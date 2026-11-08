# Thor project build audit — 2026-11-07

Checked all 19 immediate project directories containing `.thor` sources under
`/Users/davidludwik/Code/Thor`, using Thor 0.41.3 and the installed Odin compiler.
The audit redirects generated sources, caches, binaries and debug symbols to
`/private/tmp/thor-project-sweep-20261107`; it does not edit project sources.
Normal builds use Thor’s default optimization. Debug builds use `-debug -o:none`.
GUI programs are compiled and linked without launching their windows.

## Final results

18 projects compile and link in both configurations. `sandbox` has a source error.
Times below are elapsed seconds from two concurrent project builds per sweep,
with both sweeps and the regression suite running concurrently; they are not
isolated performance benchmarks.

| Project | Normal build | Debug build |
| --- | --- | --- |
| `15_puzzle` | Pass (6.93s) | Pass (7.49s) |
| `Dijkstra` | Pass (4.57s) | Pass (5.04s) |
| `ascending_primes` | Pass (10.31s) | Pass (6.22s) |
| `big_num_demo` | Pass (3.63s) | Pass (5.13s) |
| `bin_tree` | Pass (9.18s) | Pass (11.76s) |
| `circles_on_dots` | Pass (73.56s) | Pass (73.07s) |
| `double_bet` | Pass (1.79s) | Pass (2.60s) |
| `hilo` | Pass (1.19s) | Pass (1.85s) |
| `life` | Pass (2.05s) | Pass (2.54s) |
| `md5` | Pass (1.75s) | Pass (2.15s) |
| `othello` | Pass (2.39s) | Pass (2.91s) |
| `poker` | Pass (17.93s) | Pass (18.99s) |
| `pythagorean_tripples` | Pass (3.85s) | Pass (5.93s) |
| `react_time` | Pass (1.55s) | Pass (1.94s) |
| `sandbox` | Source error (1.15s) | Source error (1.21s) |
| `sketch` | Pass (3.55s) | Pass (3.92s) |
| `sudoku` | Pass (3.53s) | Pass (4.04s) |
| `tetris` | Pass (2.95s) | Pass (3.44s) |
| `triangle_solver` | Pass (17.45s) | Pass (18.00s) |

The current `sandbox/sandbox.thor:58` references `x` in `print(x)` while its
declaration is commented out. Earlier in the audit the active example instead
declared `x :> []int{1, 2, 3}` and then wrote `x[0] = 7`, which is also correctly
rejected by the read-only slice rules. Neither source example warrants weakening
compiler enforcement.

### Remaining performance finding

`circles_on_dots` builds successfully but remains unusually slow: a subsequent
isolated warm build using the same cache succeeds in 47.26 seconds. The initial
concurrent sweep times therefore do not explain the entire delay. This remains
a compile-time performance issue; the audit has not established its cause or
changed its runtime behavior.

## Compiler corrections

- Preserve every result type on the slice-analysis fast path for reference-free
  procedure calls. This fixes immutable bindings after multi-result calls and
  optional-result unwrapping, including `info.cpu_core_count()` in `hilo`.
- Recognize qualified primitive types from the genuine `base:builtin` namespace.
  Odin’s `core:c` aliases such as `c.int` and `c.uint` now retain scalar shape;
  immutable window dimensions and Raylib texture records are not slice references.
- Add argument-specific contracts for the four native `fmt.bprint*` buffer
  formatters: the buffer requires writable access, other formatting arguments
  are readers, and the result is a string. Named arguments and `**` expansion
  use the normal binder. This prevents expansion into the formatting engine’s
  large reference graph while continuing to reject read-only destination buffers.

The initial sweep found 15 successful builds, a false error in `hilo`, and
100-second analysis timeouts in `15_puzzle` and `sketch`, plus the `sandbox`
source error. All compiler failures are resolved in the final sweeps.

## Validation

- Normal and debug project sweeps: 18 passes and the one expected source error
  in each configuration; all completed without timeout or backend crash.
- Poker: all 213 project tests compile and pass.
- The 167-case compiler/TLS/native/assertion group passes 166 cases on the full
  run. One new fixture needed an explicit `i32`-to-`int` cast in its own numeric
  check; after correcting that fixture, all four focused regressions pass.
- New compiler regressions cover multiple scalar results, optional unwrapping,
  qualified builtins and C aliases, Raylib records, formatter reader/writer
  contracts, named arguments, expansion and local namespace shadowing.
- New TLS regressions cover inferred `#mut` hints, scalar hovers, original source
  diagnostic positions and recovery after unsaved edits.
- Buffer runtime tests also run with `-disable-assert -no-bounds-check
  -no-type-assert`, with explicit checks that remain enabled.
