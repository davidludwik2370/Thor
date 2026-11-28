# Transpiler hardening, November 28, 2026

The review started at `c1c6067` on macOS ARM64, with Odin `dev-2026-10:8169255b9`. The feature inventory came from the [README](../../README.md#thor-extensions), the [language specification](../language-spec.md) and the [language guide](../language-guide.md).

## Findings

| Area | Reproduction and correction |
| --- | --- |
| Compile-time numeric values | A quaternion inside a returned record changed component order during `#run`. A bit set with explicit big-endian integer storage could change members. The serializer writes quaternion components in protocol order and uses the bit set's declared storage byte order. Tests compare independent components and membership against runtime values, including after a cache hit. |
| Bounds checks | With assertions disabled, an oversized signed or unsigned 128-bit index could truncate to a valid machine index. Normalized bounds now use the bounds-check policy. Tests exercise reads, writes, slice endpoints and valid negative indices. |
| Result ownership | A pure function could return nonempty static strings assembled with `+`, `*` or `#config`, constant slice concatenations, and strings borrowed through a union. The checker follows the backing storage across these expressions, including reference-bearing array elements. Empty string constants, proven nil slice constants, inline values and explicit clones can be returned. |
| Independent numeric results | Replacing a local copy of an input `Integer` with `v += 1` could retain the input's borrow in the checker. An unconditional replacement now has the same ownership as `v = v + 1`. A conditional update can still leave the original borrow live. Generic rational component getters validate ownership at the selected native type, including when the result contains a record or array of components. A same-named input record field of type `Integer` needs an explicit clone. |
| Generated code and name lookup | A user declaration named `type_of` or `len` could break generated pure SOA checks or reconstruction of a `#run` result. Generated code binds its native operations explicitly. Tests use the user procedures in the same program to verify that their own names still resolve. |
| Evaluator limits | Unchecked builds could omit validation of unnamed enum-array slots and emit a different constant. Pure code accepts the native `#sparse` representation directive. The evaluator enforces its slot, depth, representation and 16 MiB output checks independently of runtime assertions. |
| Vet edits across configurations | A compile-time switch could select an independent slice in the current build and return a writable alias under a different `-define`. Vet now joins the possible result aliases before automatically freezing a binding. Literal-only selectors and matches can still establish that an arm is unreachable. |

## Coverage

- Bindings, selected and wildcard imports, shadowing, `using`, writable and read-only parameters.
- Array and set literals, membership, ranges, typed loops, comprehensions, repetition, concatenation, negative indexing, spread operations and struct operators.
- Optional result lists, runtime and compile-time switches, implicit returns and early exits.
- Generic parameter defaults, pure and runtime callbacks, result type queries, caller metadata, `pmap` and the paired Thor/Odin README examples.
- Allocation contracts, scoped cleanup, alias and lifetime analysis, logical-number ownership, SOA inputs and results, and source-mapped diagnostics.

An independent value check combined a generic callback, power, a signed index and an implicit switch result. It compared 24 deterministic inputs against independently computed answers under four compile-time modes and three build configurations, for 288 runtime/folded comparisons. The [regression fixtures](../../tests/test_transpiler_boundaries.py) exercise the confirmed boundary failures directly.

## Native compatibility replay

The maintained Rust compiler ran the historical [identical-source Odin corpus](../odin-superset-audit-2026-11-13/README.md) with checked and unchecked flags. Six fixtures failed native Odin's baseline and were excluded from each comparison. The two negative out-of-bounds programs were excluded from unchecked execution by the corpus.

| Configuration | Native-valid cases | Same outcome | Thor rejects | Different runtime result |
| --- | ---: | ---: | ---: | ---: |
| Checked | 104 | 86 | 14 | 4 |
| Unchecked | 102 | 86 | 14 | 2 |

Every case that matched Odin in the recorded November 13 results still matched. Native file vet tags, the vet flag and strict style also matched in both configurations. The remaining differences reproduce the recorded behavior of reserved or contextual names, negative indexing and the debug allocator. The [migration command](../odin-migration.md#conversion) handles the five colliding identifier spellings when converting existing Odin source.

## Verification

- Across the targeted compiler, vet, debugger, and TLS suites, 3,500 distinct regression tests passed after fixes and individual reruns. One platform-specific check was skipped. The count includes the 54 TLS fixtures selected for this pass.
- The release compiler checked the top-level demo and all 30 example projects.
- All 136 Rust library unit tests passed. Cargo checked all targets.
- Seven Odin runtime packages passed in checked builds, with `-disable-assert` alone, and with the unchecked release flags. The assertion-hygiene suite passed.
- Both live debugger checks reached their Thor source breakpoints on this Mac. The 23 shared-analysis and semantic-efficiency checks passed.
- A cross-target vet fixture validated both `-define:WRITE` settings locally, then skipped its final Windows AMD64 compilation because this Mac lacks the Windows SDK.
