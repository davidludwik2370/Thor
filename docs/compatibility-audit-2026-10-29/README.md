# Odin project compatibility audit — October 29, 2026

**Resolved in Thor 0.20.4:** all 79 cases that build with Odin now also build
through Thor, including the 17 original conversion failures. The original
observations below are retained as historical evidence. See the
[fixes and final validation](RESOLUTION.md) for current results.

**Result: real compatibility regressions remain in Thor 0.20.3.** Of 102 build
cases, 62 built with both compilers, 17 built with Odin but failed or timed out
after conversion to Thor, and 23 failed with both compilers. The 17 conversion
failures share several causes; they are not 17 independent bugs.

The inputs were `/Users/davidludwik/Code/Odin` and
`/Users/davidludwik/Code/Odin_Projects`. **Original files were unchanged.** SHA-256
manifests before and after cover all 416 copied regular files, including 122
source files. [The change list is empty](original-files-changed.json).

This is an audit record and a set of reproductions, not a compiler fix. Compiler
behavior and the user's projects were not edited during this pass.

## Method and scope

- Made separate native and converted snapshots under an OS temporary directory.
  In the converted snapshot, renamed every local `.odin` source to `.thor`,
  including sources in imported local packages. Source contents stayed identical.
- Built each of the 82 source directories: 80 application/reference directories
  and two foreign-library packages. The latter produced static archives.
- Also built 20 individual files from directories with multiple apparent entry
  points. This checks independent archived versions instead of attributing their
  aggregate redeclaration errors to Thor. Two of those files have `#+build ignore`
  and intentionally do not supply an entry point.
- Used ordinary macOS ARM64 builds, default optimization, no debug flags, and
  explicit output paths. Ran at most two build jobs at once. Standard Odin SDK
  imports stayed native; no SDK sources or installed compiler were modified.
- Kept all outputs, generated sources and project caches in the temporary audit
  directory. Did not execute application binaries or project build scripts.
- Skipped Git metadata, old generated/cache directories, `.vscode/bin`, existing
  `.app`/`.dSYM` bundles, and the `Odin/thor` symlink to the compiler installation.
  No symlinked directory was followed. No missing flags were inferred or added.
- Limited each corpus build to 120 seconds; reduced repros use shorter limits.
  A recorded timeout status of `-9` means the audit runner killed that process
  group. **It is not an observed compiler crash.**

Environment: Thor **0.20.3**, commit `feb3cb99835ca764e7d0e078a54df86b214003ba`;
Odin `dev-2026-10:8169255b9`; macOS 15.6 ARM64. See
[environment.json](environment.json) and the exact [commands/results](results.json).

| Cases | Total | Both build | Odin builds; Thor fails/times out | Both fail |
| --- | ---: | ---: | ---: | ---: |
| Application/reference directories | 80 | 56 | 14 | 10 |
| Foreign-library directories | 2 | 2 | 0 | 0 |
| Additional individual versions | 20 | 4 | 3 | 13 |
| **Total** | **102** | **62** | **17** | **23** |

There were no cases that built only with Thor. Successful builds produced their
executable or static archive. Runtime correctness, GUI behavior, debugger use,
TLS behavior, other platforms and release optimization modes were not tested by
this build-only audit.

## Confirmed conversion problems

All reduced `.odin` fixtures listed below build with stock Odin. Identical
contents, copied to `.thor`, reproduce the indicated Thor failure. The three
positive controls in `repros.json` still build through both compilers.

| ID | Trigger and observed result | Corpus examples | Reduced fixture |
| --- | --- | --- | --- |
| C01 | An outer loop variable used as a range bound, e.g. `for start in 1..<3 { for n in start..<3 {} }`. The range is routed through Set iterable dispatch as though it were a value; errors include `compiler_iterate(int)` and unknown `..<`. | `ascendingPrimesOptimized`, `poker`, Sudoku | [range_nested](repros/range_nested.odin) |
| C02 | `for color in Color`, where `Color` is an enum type. Set iterable dispatch expects a value, losing Odin's native enum-type iteration. | `poker`, its multithreaded reference | [enum_iteration](repros/enum_iteration.odin) |
| C03 | Variable indexing into a named array return parameter. Thor calls the writable result a temporary and rejects `board[i] = 7`. Named results need different addressability treatment from ordinary value parameters. | `hilo`, its original version, Odin demo | [named_result_write](repros/named_result_write.odin) |
| C04 | An indexed expression in a `case` condition. Its generated helper is declared inside the case body, outside the condition's lookup scope. The mapped diagnostic says `Undeclared name: Thor expression`. | `merge_sort`, `snek_refactor`, poker reference | [switch_case_index](repros/switch_case_index.odin) |
| C05 | Indexing inside `defer for ...`. Odin cannot resolve the generated helper placed inside the deferred loop. A plain deferred call with an indexed argument did work in the initial control probe. | `parallel_sum`, `parallel_sum_refactor` | [deferred_loop_index](repros/deferred_loop_index.odin) |
| C06 | Reading an array member promoted by a readonly `using` parameter. Thor takes the promoted member's address even though Odin allows only a value read there. | `tetris` | [using_array_read](repros/using_array_read.odin) |
| C07 | A later `g := g` shadows a value parameter. Earlier array accesses are resolved as though that later mutable local already existed, causing illegal address-taking from the parameter. The same readonly array access without the later declaration builds. | `tic-tac-toe` | [parameter_shadowing](repros/parameter_shadowing.odin) |
| C08 | `switch &v in board[y][x]`. Index lowering does not preserve the addressable union operand needed by a pointer type switch. Odin rejects making `v` addressable; further errors can cascade into its case bodies. | `sudoku`, `sudoku_gui` | [type_switch_index](repros/type_switch_index.odin) |
| C09 | Variable indexing or slicing a fixed-capacity dynamic array, `[dynamic; N]T`. A helper copies inline storage and then tries to return a pointer/view into that copy. Odin correctly rejects the generated escape. | multithreaded poker reference | [fixed_dynamic_index](repros/fixed_dynamic_index.odin), [fixed_dynamic_slice](repros/fixed_dynamic_slice.odin) |
| C10 | A contextual enum index in an imported record, e.g. `ctx.style.colors[.TEXT]`. Extracting the index into a helper local removes the context that determines `.TEXT`'s type. A local, explicitly typed enum-array control works. | `ui_demo` | [imported_enum_index](repros/imported_enum_index.odin) |
| C11 | `len(Color)` for an enum type. Set length dispatch accepts values, not type operands. This also affects constant assertions using enum/enumerated-array type lengths. | Odin demo; additional errors in already-invalid `Othello_pvai` | [len_enum_type](repros/len_enum_type.odin) |
| C12 | Iterating an inferred `soa_zip` result. Thor adds `sets.compiler_iterate(s)` and Odin stalls checking it. The full demo timed out after 120 seconds, versus a 0.314-second native build. See the investigation below. | Odin demo | [soa_zip_range](repros/soa_zip_range.odin) |
| C13 | A tiny enum-iteration program whose only generated dependency is Set dispatch can fail earlier with `Unknown library collection: 'thor'`. Adding another supported construct that needs sequence support reveals C02 instead. Generated support dependencies are not always registered before final compiler flags are assembled. | Found while reducing C02 | [support_import_bootstrap](repros/support_import_bootstrap.odin) |

The C13 diagnosis about registration order is an inference from the generated
imports and differing minimal cases. C05's precise Odin scope/checking behavior
still needs investigation. The table distinguishes these from directly observed
errors; no claim is made that the proposed grouping exhausts all underlying
implementation defects.

Most failures involve broad adaptation of ordinary Odin operations to support
Thor Sets and negative indices. The next compatibility pass should preserve
native type operands, ranges, enum contexts, storage identity and specialized
iterables, then fix helper placement and source-order name lookup. A workaround
that accepts an escaping pointer to copied inline storage would be incorrect.

## The demo timeout

The full demo's generated Odin file was written successfully. A second run with
Python stack dumps and subprocess timestamps confirmed Thor was blocked waiting
for the final Odin process, rather than looping in diagnostic mapping. Evidence:
[stack dumps](logs/demo-probe.log), [subprocess events](logs/demo-commands.jsonl).

Splitting its top-level procedures isolated `soa_struct_layout`. A four-line
input using `soa_zip` was enough to reproduce the stall. Ordinary `#soa` array
and slice iteration controls built successfully.

Further controls narrowed it to **overload matching on the inferred zipped SOA
type**. A plain generic identity procedure works. A generic identity procedure
with the Set-detection predicate works. An overload group containing a
`Set($T)` parameter candidate and a generic identity candidate stalls even in a
small, manually written Odin program with no Thor imports:

[native overload repro](native-soa-probes/soa-overload-probes/overload/main.odin).

The native overload probe timed out after seven seconds; both
`-thread-count:1` and `-use-single-module` also timed out. These flags therefore
did not work around this reproduction. This is separate from the previously
investigated intermittent LLVM code-generation crash: **no compiler crash was
observed in this audit**. The SOA stall appears to expose an Odin checker issue,
but Thor is responsible for introducing the overload call into otherwise
working source. Preserving native SOA iteration is the immediate Thor-side
direction to investigate. No upstream ticket was submitted.

## Existing build failures

The 23 both-failing cases should not be counted as 23 Thor regressions:

- Five aggregate directories contain independent programs with duplicate
  declarations/entry points: `ascendingPrimesOptimized/archive`, `file_tests`,
  `poker/ref`, `Connect_4/archive`, and `tetris/archive`. The applicable individual
  versions were also tested.
- Two `breakout/steps.odin` files have `#+build ignore`, intentionally providing
  no entry point when built by themselves.
- Ten individual archived Connect 4/Tetris versions require Odin's opt-in
  `#+feature using-stmt`. This audit kept their source untouched.
- `factorial` and `Othello_pvai` use `!` on results that are no longer booleans
  under the installed SDK.
- `sum_squares` imports the unavailable `core:sys/haiku` package.
- `tyringout` uses the removed `os.processor_core_count` API.
- The single-threaded poker reference has an undeclared `print`.
- `tic-tac-toe_gui` has an undeclared `printBoard` in its test source.

Both-failing inputs can still contain additional Thor-specific errors: for
example, the archived Tetris files also expose array-lowering problems, and
`Othello_pvai` exposes C03/C11. They cannot be used alone to establish an otherwise
successful project conversion, so they were excluded from the 17-case count.

## Reproduction and retained evidence

Run the reduced examples without touching the projects:

```sh
python3 docs/compatibility-audit-2026-10-29/reproduce.py
python3 docs/compatibility-audit-2026-10-29/reproduce.py --only parameter_shadowing
```

The runner copies each fixture to fresh `.odin`/`.thor` locations, builds both,
never executes the outputs, and prints its temporary artifact directory. It
requires both builds to pass by default. Use `--recorded-outcomes` when checking
the original observations against compiler 0.20.3; `--require-pass` is an explicit
alias for the current default.
`--odin`, `--thor`, `--output-dir` and `--timeout` can override its defaults.
The recorded reduced set has 17 cases: 13 Thor failures, one timeout, and three
positive controls, all with successful native Odin baselines.

- [results.json](results.json): all 102 commands, exit statuses, durations and
  artifact paths. `$AUDIT` denotes the temporary directory in `environment.json`.
- [logs/](logs/): native/Thor diagnostics for every case, plus the reduced cases.
  Checked-in transcripts normalize temporary paths and trim trailing whitespace;
  source content and reported positions are unchanged.
- [inventory.json](inventory.json): all discovered source directories and files.
- [corpus-audit.py](corpus-audit.py): the original corpus build harness, using the
  saved inventory and the two original read-only roots. It creates fresh copies
  and outputs in a temporary directory. Update the inventory before using it to
  audit newly added or moved projects.
- [repros.json](repros.json): reduced fixtures, issue IDs and recorded outcomes.
- The `*-results.json` files retain demo-reduction and SOA-control observations.
- Before/after manifests retain source/resource hashes without copying the
  complete private project corpus into this repository.

The per-case build table is in [build-results.md](build-results.md).
