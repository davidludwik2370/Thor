# Resolution — Thor 0.20.4

All **17 original conversion failures are resolved**. A fresh run of the full
102-case corpus produced **79 successful builds with both Odin and Thor** and
23 failures shared by both compilers. There were no conversion-only failures,
timeouts or compiler crashes. The originals remained unchanged: all 416 source,
configuration and resource file hashes match the initial manifests.

See the [final per-project table](fixed/build-results.md),
[exact final commands/results](fixed/results.json), and
[unchanged-file evidence](fixed/original-files-changed.json). The original
0.20.3 observations are preserved separately in the parent directory.

## Changes

| Audit issues | Fix |
| --- | --- |
| C01/C02 | Preserve native range operands and enum/type iteration, including parenthesized aliases, generic type parameters and opaque SDK names/aliases. Keep multi-result native iterator calls under Odin's iteration checker. |
| C03/C06/C08 | Distinguish writable named results and pointer type-switch bindings from readonly value parameters, promoted `using` members and value type-switch bindings. Capture and address storage according to those distinctions. Preserve existing pointer-member writes. |
| C04/C05 | Place generated helpers outside case-match scope and deferred statements. Their explicit captured inputs still evaluate at their original call sites, preserving laziness, side effects and deferred reads. The frontend now identifies deferred statements explicitly. |
| C07 | Runtime local bindings enter scope after their initializer. In `g := g`, the initializer and earlier expressions still refer to the enclosing parameter; subsequent expressions refer to the local. Native forward visibility for constants/procedures is retained. |
| C09 | Identify fixed-capacity dynamic array storage explicitly in frontend metadata. Helpers preserve its original inline storage when taking addresses or returning views, including generic accesses. Readonly value parameters remain readonly. |
| C10 | Keep enum and implicit-selector indices native, retaining their contextual type. They never need signed sequence-index normalization. |
| C11 | Keep recognized type operands and opaque SDK aliases under native `len`. Generic value dispatch uses a compile-time expression containing native `len` operations, retaining native constant array lengths and avoiding value-only wrappers around type operands. Its type guard uses an unevaluated, typed witness so untyped string constants, including `#switch` results, remain valid. |
| C12 | Replace generated calls to the overloaded Set iterator adapter with a compile-time Set/native branch around the loop. The native branch retains the original iterable and specialized SOA semantics. This avoids Odin's problematic `Set($T)` overload matching entirely. No change to the installed Odin compiler or its threading flags is needed. |
| C13 | Discover potential loop support dependencies before emission, so final collection flags and generated imports agree. |

No extension syntax changed. Ordinary `.odin` parsing, native pointer indexing,
map keys and enumerated arrays retain their existing meaning. In particular,
Thor's negative sequence-index normalization does not reinterpret pointer or
multi-pointer arithmetic; explicit dereference exposes an array as a sequence.
No new cache type or project output category was added.

The old internal `sets.compiler_iterate` overload remains available in the
runtime for compatibility, but the transpiler no longer generates calls to it.
The independently reduced Odin overload-checking stall remains an upstream
investigation item; working source no longer encounters it through Thor's loop
lowering.

## Regression coverage

`tests/test_odin_compatibility.py` builds every original reduced fixture from
identical Odin/Thor source text and adds executed behavior checks for:

- Nested dependent ranges; enum aliases, SDK enum types and generic type
  parameters; sparse enum/type lengths and constant generic array lengths;
  untyped string constants from compile-time switches. Parentheses preserve the
  same behavior for ranges, iterator calls and contextual enum indices.
- Named return writes; fixed-capacity views/pointers aliasing original storage;
  generic reads/writes and readonly rejection.
- Case-condition helpers, lazy conditions, side-effect order, deferred loops and
  reads that observe changes made before scope exit.
- Concrete/generic readonly `using` records and writable pointer parameters.
- Same-block parameter shadowing and initializer lookup.
- Value/pointer type switches over nested arrays, including negative sequence
  indices, with mutation visible through the original union storage.
- Native multi-result iterator protocols, generic Set/native loops, inferred
  zipped SOA values, mutable SOA iteration and labels.

Additional tests verify original-source diagnostic ranges after helper movement,
Unicode, errors in deferred/case bodies, readonly versus writable union payloads,
TLS diagnostics clearing after an unsaved fix, original semantic-token spans,
and rename distinguishing a parameter from a later same-name local/initializer.

Run the permanent compiler tests with:

```sh
python3 -m unittest tests.test_odin_compatibility -v
```

The standalone [repro runner](reproduce.py) also requires all builds to pass by
default. The native-only SOA overload probes in the original audit are historical
upstream repros, rather than Thor acceptance cases.

## Final validation

- **836** compiler/runtime/cache/stdio-TLS tests pass in two clean groups (361 in
  378.504 seconds, 475 in 452.294 seconds). The optional Finder test is skipped
  in that sandboxed run and passes separately outside it.
- **13** real CodeLLDB tests pass; together with the separate Finder check, all
  **849 distinct Python cases** available in the workspace are covered.
- **1,126** native TLS tests and **89** Node/editor tests pass.
- The added behavior suite passes, including parenthesized operands, readonly
  rejection and constant generic lengths. The corrected untyped-string case
  and allocation-tracked walkthrough pass; the 162-case focused suite also passes
  after that correction.
- Both checked-in Thor demos run successfully. The main demo is checked at
  `-o:none` and `-o:speed`; the commented walkthrough is also checked at
  `-o:speed` and through its allocation-tracked test entry point.
- All **79** Odin-successful corpus cases are rebuilt once more through the final
  type guard with **zero failures**. The 23 native baseline failures remain
  unchanged. No application from the external corpus is executed.
- No compiler crash occurs in the final validation. The original 416 files are
  hash-checked again after testing and remain unchanged.

[Validation summary](fixed/validation.json), [final 79-case confirmation and
compiler hashes](fixed/post-guard-confirmation.json), and
[final original-file check](fixed/original-files-final-check.json) retain the
results. The `fixed/thor-compat-*.log` files contain the complete validation logs.
