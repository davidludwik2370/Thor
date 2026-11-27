# Transpiler hardening, November 27, 2026

This pass used the feature index in the [README](../../README.md#thor-extensions) and followed the features through the Rust compiler, starting from `ab7989a`. The [preceding review](language-and-installer-review-2026-11-27.md) covers the installer and vet's application of corrections.

## What changed

| Failing case | Correction |
| --- | --- |
| A generic procedure allocated inside `when T != i32`. Calling it with a `rune` passed `no_alloc`. A `pmap` overload group could also infer the wrong named result fields. | Both generic branch selection and overload scoring distinguish `rune` from `i32`. The native `byte` alias still equals `u8`, including inside map types. |
| Some native type tests selected the wrong branch for pointers, enum-indexed arrays, inline records, bit fields or sets. | The resolver uses Odin's distinctions when it has the necessary information. If a compact type description cannot establish equality, the allocation checker considers both branches. Separately written anonymous record definitions can have the same native type. |
| In generic code, `text[0]` and `raw_data(text)[0]` for a `string16` could be analyzed as `u8`. Reading through `raw_data(&array)` could produce the type of the array in place of its element. | Indexing and raw-data queries preserve the native code-unit or element type, including pointer indexing, aliases and nested arrays. Tests put an allocation inside the branch that Odin actually selects. |
| Iterating a distinct map could give the loop variables the wrong types. The allocation checker could also treat an enum array index as an ordinary integer. | Generic and ordinary resolution use the same rules for native range bindings. They preserve distinct maps' key/value types and string iteration's rune type, and keep an unresolved enum index or bit-set member unknown. |
| A `fn` specialized on a struct containing an unnamed procedure-typed padding field was rejected during compile-time arithmetic. | The checker skips padding when looking for callable fields in a pure signature. A named procedure field at the same nesting depth still fails, and representation-inspecting operations retain their own validator. |
| A fixed or dynamic structure of arrays (SOA) had the same compiler representation as its ordinary array counterpart. | The resolver retains the SOA storage kind through generic binding, specialization and native type queries. Indexing and borrowing still follow its underlying storage. A rejected SOA in a pure function now gets a Thor diagnostic that names the type. An explicit invalid use of Odin's reflection intrinsic still gets the native diagnostic. |
| With `Rows :: distinct [][2]int`, `rows[i][i] += 4` could fail for a signed runtime index. The generated checker also reported a missing return. | Negative indexing preserves the addressability of distinct containers. Regression tests write through a view and a pointer, read the original storage, and check that a genuinely temporary array still produces the intended error. |
| Some valid powers with a large base-12 literal failed even when the result was `1`. | Constant indexing/type resolution and exact integer folding recognize the same five Odin integer prefixes. Overflow diagnostics depend on the result. |
| The constant evaluator checked a rational power's size only when the numerator was larger in magnitude than its denominator. | Power checks bound growth of either reduced component for positive and negative exponents. Left shifts account for the bits already present. Zero and unit bases retain their exact short paths. |

## Coverage by feature family

| Family | Interactions examined |
| --- | --- |
| Imports, bindings and views | Name resolution, selected and wildcard imports, shadowing, `using`, `:>`, `#ro`, `#mut`, native aliases and writes through nested views. |
| Expressions and operators | Inferred arrays, membership and constraints, intervals, ternaries, repetition, concatenation, powers, fixed-size struct fields and padding, negative indices and slices. |
| Iteration, construction and formatting | Typed range/index bindings, comprehensions, sets, formatted strings, expression lifetimes and formatted results. |
| Control flow | Value and comparison switches, discarded results, lazy fallback lists, early and implicit returns, `or_panic`, spread extrema and inactive compile-time arms. |
| Procedures and type inference | Pure and runtime callbacks, aliases, named/multiple results, dependent defaults, caller metadata, and polymorphic, specialized and runtime-dispatched parallel maps. |
| Evaluation and ownership | All four compile-time modes, explicit `#run`, runtime barriers, readonly literal storage, arbitrary-precision values, shallow scoped cleanup, purity and allocation contracts. |
| Diagnostics and integration | Original-source locations, errors in generic specializations, genuine temporary writes, unused contracts and vet controls. The README tests also exercise the public basic-library examples and their paired Odin versions. |

The combined regression in `tests/test_implicit_returns.py` evaluates a pure generic function with an implicit switch return, a negative index and exponentiation. The caller uses a scoped allocation, a typed loop index and string interpolation. All four compile-time modes must print `9/13`, in checked, assertion-disabled and unchecked builds.

## Verification

Across three overlapping macOS ARM64 selections, 1,094 distinct Python-harness tests passed. The language selection ran 471 tests, the formatting/imports selection ran 131, and the final release-binary interaction suite ran 609 with 608 passes and one platform skip. The skipped test completed its local vet and configuration checks, then needed a Windows SDK to check the Windows target. All 136 Rust unit tests passed. All seven assertion-hygiene checks passed independently, including rebuilding the native frontend without assertions.

Rerunning the float-power cache test exposed a test dependency on an empty shared cache. It now uses a disposable cache and verifies the cold write, warm hit and invalidation on consecutive executions.

After the suites finished, baseline and final release binaries alternated against separate warm caches on the same Mac. These are the medians of seven measurements per binary, in milliseconds.

| Vet workload | Baseline | Final |
| --- | ---: | ---: |
| 48 aliases of a local package | 127.8 | 123.8 |
| 48 aliases of an uncertain SDK import | 133.5 | 75.7 |
| Full vet on poker_pmap | 1,482.5 | 1,494.7 |

Full vet changed by 12.2 ms (0.8%) on this workload. The SDK fixture benefited from avoiding speculative edit validation. These timings cover warm caches on this machine.

## Follow-up decisions

- Exact numeric source literals now have a configurable input budget. The default allows 16,000,000 digits per component. See the [arbitrary-precision reference](../arbitrary-precision.md#types-construction-and-literals).
- The agreed pure-function rule applies to ordinary and structure-of-arrays (SOA) slices. A `fn` may read supported input rows and columns directly. Returning a slice backed by an input, including one inside an aggregate, requires an explicit allocation and copy. A function may forward a helper result whose independent ownership the compiler can prove. Returning an inline value such as an `f64` needs no allocation. See the [pure result examples](../language-guide.md#pure-functions-and-compile-time-execution).
