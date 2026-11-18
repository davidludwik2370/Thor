# Immutable-slice copy hardening — Thor/TLS 0.41.4

This pass checks the distinction between copying inline data and borrowing
reference-containing data. The original project failures exposed cases where a
numeric or record copy inherited slice permissions from its source. The expanded
tests also found cases where shallow operations could lose a nested permission.
Both directions are covered: valid copies compile, and writes through protected
aliases remain errors.

## Corrections

| Area | Behavior now checked |
| --- | --- |
| Inline copies | Scalar, fixed-array and record results stop referring to the caller's inline storage; nested slice fields retain their views. |
| Native numeric aliases | Conditional numeric type aliases, including `core:c` aliases, retain numeric value semantics. Conditional reference types still preserve slice restrictions. |
| Union narrowing | Numeric variants discard unrelated reference facts; record/array variants retain their nested references. |
| Generic and defaulted parameters | Defaults supply inferred types. Actual specializations distinguish scalar/reference elements, generic record arguments and array lengths. |
| Builtin copies | `new_clone` understands its named `data` argument, aliases and implicit pointer dereferencing. `swizzle` and `expand_values` preserve the appropriate permission for each copied value. |
| Multiple results | Returned scalar/status results stay independent from borrowed slice results. Aliases shared by multiple reference results remain shared in analysis. |
| Replaced local headers | Replacing a copied local's slice header with independent storage restores access only through that header. Prior aliases retain their original restriction. |
| Control flow | Complete fresh replacements on both `if` branches are recognized. Partial branches, outer loop-carried state and deferred replacements cannot erase a protected reference. Locals created within a branch/loop can replace their own headers. |
| Structure of arrays | `#soa`, `soa_zip` and `soa_unzip` retain individual column contracts. Copying an inline row is independent; nested references remain shallow. |
| Slice/array overloads | A `[]T` formal and `[$N]T` formal no longer tie merely because both share an array-type AST node. Each selected overload uses its own access contract. |
| TLS | Read-only property coloring, inferred `#mut`, UTF-16 diagnostic positions, related locations and unsaved-edit recovery reflect these distinctions. |

The native frontend now preserves array-layout tags in its AST metadata. It does
not insert source lines or change generated program layout. The permission
changes are compile-time analysis; no new runtime allocation, ownership rule,
reference count or permission tag is introduced.

`basic.clone` remains a shallow value copy. `core:slice.clone` and
`clone_to_dynamic` allocate outer collection storage, while `new_clone` allocates
the outer pointed-to value. None of those operations independently clones nested
slice backing storage. The diagnostic now names `core:slice.clone` when suggesting
a writable copy, rather than suggesting an ambiguous `clone`.

Normal directory builds and native SDK inspection exclude files tagged `#+test`.
The earlier filename-based `_test.thor`/`_test.odin` exclusion was incorrect and
has been removed for user/SDK sources: Odin includes those filenames normally.
Test-only invalid examples must use `#+test`; ordinary builds skip them before
analysis, while `thor test` checks them. The regression for a test-only read-only
violation uses that explicit tag. Installation-owned compiler support views
continue to omit Thor's own unit-test files.

The source-discovery follow-up was checked directly against installed Odin
`dev-2026-10:8169255b9`: a sole `hilo_test.odin` containing `main` builds and runs
normally. `tests.test_source_discovery` covers the same Thor/native filenames,
checked and unchecked builds, imported compile-time helpers, unsaved overlays,
ordinary body checking, and explicit test tags. The user's `hilo_test.thor`
also built successfully as a directory project in an isolated copy.

## Validation

The new `tests/test_slice_copy_hardening.py` contains 25 test methods and executes
111 compiler programs across positive/negative matrices and runtime configurations.
Its four live TLS companions are in `tests/test_slice_copy_hardening_tls.py`.

The final runs pass:

- 149 tests across the new compiler/TLS matrices, existing slice-access suites
  and post-0.40 hardening.
- 110 native compatibility, runtime, T07/T12 and assertion-hygiene tests.
- An additional 86-test interaction run covering purity hardening, native
  execution, conditional expressions, power and automatic `using` support.

Runtime copy/clone fixtures run with allocation tracking in checked debug builds
and with `-o:speed -disable-assert -no-bounds-check -no-type-assert`. Explicit
panic checks and Python comparisons verify their results; required setup is not
hidden in assertions. The frontend metadata is compared between its ordinary
and assertion-disabled builds, including `#soa` and `#simd` types.

The automated regression groups use an Odin wrapper with `-thread-count:1` to
avoid the previously recorded unrelated parallel backend crash. The real-project
build sweep uses the ordinary installed Odin invocation.

All 19 projects in `/Users/davidludwik/Code/Thor` produce executables in both
normal and debug configurations:

`15_puzzle`, `Dijkstra`, `ascending_primes`, `big_num_demo`, `bin_tree`,
`circles_on_dots`, `double_bet`, `hilo`, `life`, `md5`, `othello`, `poker`,
`pythagorean_triples`, `react_time`, `sandbox`, `sketch`, `sudoku`, `tetris`,
and `triangle_solver`.

The sweep redirects binaries, generated source and caches to temporary storage
and verifies that project source bytes did not change. It builds these programs;
it does not launch their interactive GUIs. Sandbox's current source now builds,
unlike its intentionally incomplete source in the preceding audit.

Excluding test-only sources fixes a poker build timeout observed during this
pass; a separate ordinary build completed in 2.82 seconds. This is not a general
build-time benchmark. Circles-on-dots still takes substantially longer than the
other projects, as recorded in the preceding audit; this pass does not claim to
resolve that separate performance concern.

## Boundaries

The [slice-access contract](slice-access.md) remains a read-only-view contract,
not a guarantee that other writable aliases cannot change the backing data.
Existing pointer targets remain mutable; protecting a slice of pointers protects
its pointer slots, not those separate targets. Allocation lifetime is manual.

Generic bodies follow Odin's specialization rules. Unresolved callbacks and
foreign implementations remain conservative. Arbitrary indexed header updates
or complex loop-carried alias rewrites are not a complete ownership proof and
may still need an explicit independent-copy boundary. These limitations must not
be bypassed by dropping nested permissions to make a program compile.
