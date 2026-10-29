# Compiler, library and editor hardening — October 29, 2026

This pass reviews the work after the previous post-v1 audit (`487518c`, compiler
0.7.1), through `d0d81de` (0.19.0), plus the newly added `basic` functions.
The earlier ownership hardening and T01–T23 remain covered by
[the previous audit](../AUDIT.md), the [numbered specification](language-spec.md),
and the existing regression suite. Fixes from this pass are compiler **0.19.1**.

## Documentation and coverage inventory

The implementation checkpoints in [DEVELOPMENT.md](../DEVELOPMENT.md) give the
history. This table points to the current contracts and durable tests; historical
whole-project caching described in older checkpoints has been removed.

| Work reviewed | Current documentation | Tests |
| --- | --- | --- |
| Nested f-strings, quoted fill runes, directives, value/type/namespace colors | README; `editors/vscode/README.md`; `tls/THOR.md` | Grammar color/scoping tests; original-source semantic-token checks, Unicode, nested expressions and unsaved edits in `test_tls.py`; real VS Code integration. |
| Range membership and generic constraints | README; numbered spec | `test_ranges.py`, diagnostic tests, TLS and grammar tests; operand order, extreme/reversed bounds, lazy branches, `fn`/`#run`, contextless code and temporary cleanup. |
| Power operator | README; numbered spec | `test_power.py`, diagnostic/TLS tests; precedence/association, literal coercion, typed mismatches, overflow behavior, negative exponents, special floats, type queries, lazy execution and cache invalidation. |
| Redeclarations and generic callback defaults | README; `examples/default_callbacks`; TLS guide | `test_defaults.py`, `test_interactions.py`, diagnostics, TLS/rename; imported/native callers, overridden defaults, enclosing scopes, unsupported captures, inactive branches and mapped errors. |
| Wildcard and namespace-free selected imports | README; basic API | `test_imports.py`, TLS/rename; privacy, shadowing, cross-file isolation, overloads, native libraries, conflicting names, unused imports and unsaved edits. |
| Basic library and Set/sequence conversions | `runtime/basic/README.md`; `examples/basic`; runtime guide | `test_basic.py`, new `test_basic_extensions.py`, native Set tests, TLS; independent storage, shallow elements, allocator retention, empty/invalid inputs and all import forms. |
| Native library `#run` and transitive ordinary calls from `fn` | README; `examples/native_run`; `examples/pure_calls` | `test_native_run.py`, `test_pure_calls.py`, T18–T23 tests; resolved overloads, recursion, visibility, nominal types, mutable storage, resources, supplied compiler context, effect rejections and owned results. |
| Evaluation/parser caches and edited builds | README; `INSTALLATION.md` | `test_compilation.py`, `test_edited_builds.py`, `test_parse_cache.py`, T21–T23; changed dependencies, resources, defines, source overlays, corrupt/unwritable entries, toolchain changes, alias/shadowing changes and warm cache hits. |
| Shared installation, runtime output and executable naming | `INSTALLATION.md`; README | `test_installation.py`, CLI/compilation tests; fresh projects, custom outputs, working-directory names, shared maps, local adapted libraries, isolated caches and sparse SDK overlays. |
| Compiler diagnostics and TLS lifecycle | README; TLS guide | Diagnostics and real stdio LSP tests; original file/token ranges, Unicode/CRLF, original names, unsaved imports, clearing, cancellation, timeouts, rapid changes and package routing. |
| Hover type presentation, contextual enums, references and rename | TLS/editor guides | `test_tls.py`, `test_rename.py`, native TLS tests and real VS Code; source spans, argument context, generic values, callback values, namespace types and collision/partial-edit rejection. |
| `mkthor`, CodeLLDB and original-source debugging | `editors/vscode/DEBUGGING.md` | Project-template and Node adapter tests; 13 actual DAP tests; fresh VS Code project, build shortcut, breakpoints, Step In/Over/Out, watches, variable edits, restart and shared-library breakpoints. |
| Debug allocation tracking and ASan | `docs/memory-debugging.md` | Memory policy/runtime tests; direct/moved executables, retained dSYM, mapped heap/stack/global errors, custom allocators/hooks, defers/finalizers, native per-test tracking, opt-outs and unchanged compile-time caches. |

The canonical specification now lives in `docs/language-spec.md`, rather than
depending on a root source file that has been moved into the user's playground.
The basic API and commented runnable demo now cover all the added helpers.

## Problems found and corrected

1. **`basic.random` could not compile when instantiated.** The package referenced
   `rand` without importing it. Added `core:math/rand`; tests cover integer/float
   widths, signed and unsigned 128-bit ranges, exact endpoints with a controlled
   generator, seeded generators, omitted versus explicit-zero bounds, and invalid
   bounds including NaN/infinity.

2. **A native `when` expression caused per-project copies of `basic`.** The
   floating-point precision selection in `random` triggered an overly broad
   shared-runtime exclusion. Native `when` expressions remain native syntax and
   can share a generic translation. Thor-evaluated/configuration-dependent forms
   retain their project-local rules. Existing installation tests caught this;
   shared-library reuse, original maps and project isolation pass again.

3. **`#run` checked unused impure overload bodies.** SDK calls already extracted
   only the resolved implementation, but ordinary project/package calls could
   retain a whole overload group. The same selection now applies to both. Tests
   cover local/imported overloads, `basic.ord(rune)`, warm cached results, edits to
   unused bodies, and invalidation when the selected implementation changes.
   Truly ambiguous calls retain native checking.

4. **Owned returns under `do` could emit invalid syntax.** Promoting a returned
   string/slice expands `return` into a block, which is not allowed after Odin's
   `do` keyword. Lowering now converts that single-statement body to ordinary
   braces. Tests exercise runtime and folded calls, checked ordinary callees,
   allocation cleanup and wrong-return-type diagnostics at the original token.

5. **Owned variadic returns could mis-forward arguments.** A generated wrapper
   forwarded parameters after `..values` positionally. They now retain their
   required named form. Allocation-tracked tests exercise variadic inputs with a
   named-only scaling parameter and independently owned slice returns.

6. **Relative imports inside inspected SDK packages were not discovered.** A
   standard-library source could import a child/sibling using a relative path;
   evaluation copied a dangling import rather than inspecting the dependency.
   SDK-relative dependencies now use the same source-discovery/rebasing path as
   collection imports. A synthetic SDK fixture verifies successful execution;
   rejected runtime helpers now reach their actual purity diagnostics rather
   than reporting a missing generated package.

7. **Only the last alternative `main` in one file got a tracking prologue.**
   Preparing each `when` branch replaced the previous branch's prologue. They
   now accumulate. Tests select both branches and require the expected mapped
   leak count/size for each.

8. **An excluded platform file could suppress ASan's Thor symbolizer.** Hook
   discovery now ignores excluded files. A Linux-only hook imported by a macOS
   executable no longer disables original-source reports.

9. **A backend crash could look like an unexplained exit code.** Native compiler
   termination by signal now produces a toolchain diagnostic naming the signal,
   with no fabricated source location and a conventional `128 + signal` exit
   status. JSON mode emits one valid diagnostic object even when the compiler
   supplied no stderr. Tests simulate both silent and nonempty fatal stderr;
   executed programs keep their separate output and runtime-exit behavior.

10. **Generic/imported map lookups could lose their optional `ok` result.**
    Unknown record members entered negative-sequence-index lowering, whose
    single-result helper discarded native map status. Multiple destinations,
    multi-result returns and `or_else`/`or_return` optional contexts now keep the
    native lookup. Map keys retain their native meaning, and invalid optional
    sequence accesses remain Odin errors. Tests cover generic records, missing
    and negative keys, assignments, returns, fallbacks and unsaved TLS errors.

11. **`basic.input` did not preserve allocation sizes.** The native buffered
    reader returned a slice of a dynamic buffer, and trimming LF/CRLF further
    shortened its visible length. A caller deleting that string supplied an
    incorrect size to a sized allocator. Input now retains its temporary buffer's
    capacity through cleanup and returns an exact-size copy. A strict tracking
    allocator tests multi-line, empty, Unicode, CRLF and EOF cases and requires
    every allocation to be freed with its correct size.

## New basic helpers: ownership and edge cases

`filter`, `sum`, `range`, `chr`, `ord`, `random` and `table` were reviewed as
library procedures, without granting them special language privileges.

The new tests cover filter order and shallow strings, all/no/empty matches,
successful shrinking and an allocator that refuses shrinking, exact-size frees,
numeric zero identities and distinct/complex/wide sums, ascending/descending
ranges including machine-integer extremes, invalid Unicode and grapheme versus
code-point differences, table headings/tags, numeric alignment, nested structs,
empty inputs, control characters, narrow columns and intact Unicode graphemes.
Allocator tests fail on leaked memory or invalid frees.

A combined allocation-tracked debug test crosses namespace-free imports, ranges,
filter/sum, Set conversion, spread min/max, power, contextual compile-time switch
arrays, negative indexing, transitive pure calls, `do` returns and nested f-strings.
The ordinary and debug basic demos also exercise the new APIs in a fresh caller.
TLS tests check their inferred types, original-library definitions, namespace
completion, original callback error ranges and clearing after an unsaved fix.

## Boundaries and remaining questions

- **One native Odin compiler crash was observed:** during the long regression
  run, Odin exited with SIGSEGV while building the small basic-print case.
  macOS's crash record identifies native `alloc_cstring`/`lb_type_internal`/
  `lb_generate_procedures_and_types_per_module` frames. The case immediately
  passed in isolation, 100 executions of its executable passed, and five normal
  plus five ASan/tracked fresh builds/runs passed. No Thor fix for that native
  crash is claimed. The driver now reports such failures clearly; a clean-suite
  rerun is included in the verification. If it recurs, preserve the generated
  Odin and compiler crash record for an upstream reproducer.
  **Follow-up:** [the compiler investigation](odin-codegen-race.md) has now
  reproduced a matching shared type-name cache race in a plain Odin program
  using C++ ThreadSanitizer. It includes an experimental, uninstalled fix and
  explicit workaround flags; the original native crash remains intermittent.
- **Conditionally declared ASan hooks:** hook discovery remains conservative
  inside an included file. A user hook under an inactive `when` can suppress the
  automatic Thor symbolizer choice. Instrumentation and debugging still work.
  `ASAN_SYMBOLIZER_PATH=/path/to/Thor/llvm-symbolizer-thor ./program` restores
  original Thor report locations; this workaround has a runtime regression test.
  A future improvement should let Odin decide hook presence without guessing
  compile-time branch selection in Python.
- **Library purity follows implementation effects:** `sum`, `chr`, and
  `ord(rune)` are eligible. `ord(string)` uses mutable Odin UTF-8 lookup tables;
  `filter`, `range`, `random` and `table` use callbacks, explicit allocation,
  context, by-reference iteration or reflection outside the current subset.
  They remain ordinary runtime helpers. These are tested rejections, not missing
  error messages or privileges silently granted to a standard library.
- **Existing language/editor boundaries remain:** the OLS-derived resolver is
  partial; arbitrary function-pointer calls, external state, unsupported pure
  schemas, slice constants, and implicit `using` pointer promotion through an
  unconstrained generic keep the limits documented in README and the old audit.
  Native compiler checks remain authoritative. Thor formatting and automatic
  imports/code actions are still deferred.
- **Platform scope:** runtime/debugger validation uses the installed macOS Odin,
  LLVM and CodeLLDB tools. Other-platform policy and cross-target checking have
  tests; this pass does not establish native Linux/Windows debugger or ASan
  support. Their automatic-memory-check defaults remain off.

## Validation

Final commands and totals are recorded with the 0.19.1 checkpoint in
[DEVELOPMENT.md](../DEVELOPMENT.md). Tests exercise actual Odin programs, the TLS
stdio protocol, allocation-tracked native runtime/TLS suites, grammar/adapter
units, and isolated real VS Code and CodeLLDB workflows. Temporary logs live in
`/tmp/thor-hardening-*`; the tests and this report are the durable record.
