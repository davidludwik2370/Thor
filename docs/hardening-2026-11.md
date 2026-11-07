# General hardening: Thor 0.40.1

This pass covers changes after the general purity/compile-time audit in
`d012351` (0.33.3), through read-only slice views (0.40.0). It reviews accepted
behavior, rejection behavior, allocation lifetimes, compiler diagnostics, live
unsaved TLS updates and original-source debugging together. It does not change
the installed Odin compiler or normal Thor build flags.

## Feature and documentation inventory

| Change | Reference | Main regression coverage |
| --- | --- | --- |
| Confined mutation through ordinary procs inside `fn`; transitive call-site errors | [Purity diagnostics](purity-diagnostics.md), [README](../README.md#pure-functions-and-compile-time-execution) | `test_pure_mutation*`, `test_pure_call_context*`, `test_purity_hardening*` |
| Owned dynamic-array `fn`/`#run` results | [Purity diagnostics](purity-diagnostics.md), [development notes](../DEVELOPMENT.md#owned-dynamic-pure-results-thor-0360) | `test_pure_dynamic_results*`, `test_evaluation_hardening`, debugger suite |
| Nonreturning `-> !` calls in switch expressions | [Language spec](language-spec.md), [README](../README.md#interpolation-switches-sets-and-negative-indices) | `test_switch_diverging*`, `test_enum_switches*`, `test_switch_results` |
| Selected-import usage, builtin inference and immutable colors | [TLS guide](../editors/vscode/README.md) | `test_selected_import_usage_tls`, `test_binding_inference_tls`, editor suite |
| Constant array/slice concatenation `++` | [README](../README.md#implemented-syntax), [language spec](language-spec.md) | `test_concatenation*` |
| Source-aware optional unwrapping `or_panic` | [README](../README.md#optional-results-with-or_panic) | `test_or_panic*`, debugger suite |
| `basic.scoped_arena()` and scope cleanup | [Basic library](../runtime/basic/README.md#scoped-arena-allocations) | `test_scoped_arena*`, `test_assertion_hygiene` |
| Run/debug from the active file's directory | [Editor guide](../editors/vscode/README.md), [debugging guide](../editors/vscode/DEBUGGING.md) | Node run/debug tests, `test_mkthor`, real editor/debugger checks |
| Runtime value hashing and error contracts | [Hash library](../runtime/hash/README.md), [basic library](../runtime/basic/README.md) | `test_hash*`, native hash runtime tests |
| Owned `_string` conversions with optional allocation errors | [Basic library](../runtime/basic/README.md#converting-values-to-strings) | `test_string_conversion`, `test_string_tls` |
| Single-clause comprehensions, flattening, optional expression outputs | [Comprehensions](comprehensions.md) | `test_comprehensions*`, debugger suite |
| Read-only slice views, inferred access and per-name `#mut` | [Slice access contract](slice-access.md), [commented demo](../examples/slice_access/demo.thor) | `test_slice_access*`, `test_post_040_hardening`, native parser/TLS, editor and debugger suites |

The public behavior was already documented in the references above. This report
connects those contracts with the cross-feature audit and records the corrections
below. `DEVELOPMENT.md` retains the implementation/validation history per batch.

## Corrections found by the new interaction tests

### Calls in less obvious expression positions

Read-only access checking previously missed calls inside switch-expression
selectors and case matches, and map-comprehension keys. These now visit the same
call/argument analysis as direct calls. Reference-bearing map/Set comprehension
keys also retain the existing conservative restriction on inserting read-only
slice references.

```odin
change :: proc(values: []int) -> int { values[0] += 1; return values[0] }
values :> []int{1}
// Each is rejected at the argument to change, with the mutation as a note:
// result := switch change(values) { case 2: 7; case: 8 }
// result := switch 2 { case change(values): 7; case: 8 }
// result := map[int]int{change(values) = x for x in 0..<1}
```

Mutable controls remain valid. The analysis does not evaluate expressions or
change their runtime order.

### Every multiple result keeps its own provenance

Parentheses, forwarding, optional-result unwrapping and sole-call argument
expansion now preserve each result's access permission. Opaque callbacks also
preserve their secondary result shapes instead of losing later slice results.

```odin
pair :: proc(values: []int) -> (int, []int, bool) { return 7, values, true }
values :> []int{1}
_, view :> (pair(values) or_panic)
// view[0] = 9 // Rejected; parentheses/unwrapping do not erase permissions.
```

Conversely, map lookups, type assertions, `make`, spread extrema and reviewed
basic-library helpers keep their Boolean/error statuses independent of their
slice result. An immutable tuple's scalar status can be copied to a mutable
local. Runtime allocator errors retain their enum identity without treating the
compiler's runtime sources as ordinary SDK input.

### Inactive compile-time arms do not impose access restrictions

Statically resolved `when`/`#switch` expressions take provenance from their
selected arm. An inactive read-only result no longer prevents a selected mutable
slice from being used. Runtime branches remain conservative; unresolved native
conditions do not trigger a new evaluation pass merely for access analysis.

### Diagnostics identify the actual operation

A blocked expression can appear in both a generated type witness and a runtime
helper. Its unique access-check token now anchors every occurrence to the
original argument or write, then ordinary deduplication leaves one diagnostic.
This removes redundant whole-expression underlines without hiding independent
errors. Real TLS tests cover Unicode/UTF-16 positions, related mutation locations,
status hovers and unsaved corrections.
The adapter also accepts metadata-only native SDK overlay files, preserving
unsaved native source and relative resource imports alongside Thor access checks.

### Editor-test operations survive assertion removal

Required editor edits and run commands now execute before assertions inspect
their results. The assertion-hygiene guard covers these setup operations too.
The full automated VS Code suite uses actual completion-provider results and
client edits without relying on background-window keyboard focus. Use
`python3 tools/test_vscode.py --interactive-typing` for the optional foreground
suggestion-widget path. The default test verifies completion/edit integration;
it does not claim to simulate an OS keypress.

## Remaining boundaries

- Access inference is conservative for unresolved callbacks, foreign calls,
  opaque conversions and native conditions that it cannot statically select.
  This pass adds no ownership, borrowing-lifetime or whole-program alias system.
- Read-only slices are views. Preexisting writable aliases can change their
  backing data; callers still manage deletion and allocator lifetimes.
- `fn` purity and writable-input inference remain different contracts. I/O can
  consume a read-only slice; a pure helper may mutate independently owned local
  data while still being forbidden from ambient randomness or global state.
- A native backend signal without a source location cannot reliably be assigned
  to a source expression. The existing [upstream Odin issue](odin-codegen-race.md)
  remains separate from Thor's mapped checker diagnostics.
- Finder/LaunchServices integration remains opt-in outside the sandbox; it was
  run separately and passed in this audit. No expected-failure tests are used to
  mask the access-analysis corrections.

## Validation

The full Python matrix exercised all 1,696 discovered cases, including 39
CodeLLDB/DAP checks. It exposed one SDK-overlay adapter regression in the new
diagnostic anchoring; that was fixed, and all 60 related compilation, diagnostic
and slice-access TLS cases passed on rerun. The normally skipped Finder test
passed separately. Native TLS passed 1,150 tests in each of checked/unchecked
builds, Node passed 140 tests, and the complete VS Code editor and run/debug
integration suites passed. No identified failure remains unresolved.

The exact configurations, baseline and post-fix results are recorded in
[DEVELOPMENT.md](../DEVELOPMENT.md#general-hardening-thor-0401). New integration
programs combine compile-time concatenation/comprehensions, confined pure
mutation, independent dynamic results, read-only views, optional statuses,
scoped arenas, hashing, Unicode conversion, big numbers and f-string formatting.
They run with allocation tracking in checked debug and optimized unchecked
assertion-disabled builds; verdicts use explicit panic checks and Python checks.
Real CodeLLDB tests step into comprehension-key calls and parenthesized optional
multiple-result calls and verify original filenames, lines and scalar locals.
