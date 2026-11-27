# Pure callback validation and analysis cost, 2026-11-26

Pure `fn` callback contracts are checked before lowering to ordinary Odin
procedure pointers. Runtime-selected callbacks keep indirect dispatch; known
constant callbacks retain native specialization. No runtime effect tags or
adapters are introduced.

## Runtime representation

`tests/test_pure_callbacks.py` compiles matched `fn` and `proc` forwarding
functions to optimized LLVM IR. Runtime callback versions have identical native
arguments and one indirect call each. Constant callback versions also have
identical arguments, with no indirect call. Neither case introduces callback
scratch/clone calls. The zero-sized specialization witness adds no native
argument. Ownership-bearing callback results retain the existing pure-function
scratch and independent-result rules.

Runtime tests exercise checked/debug and `-o:speed -disable-assert
-no-bounds-check -no-type-assert` builds. They use explicit output comparisons or
conditional panics for verdicts. Recursive cleanup of records containing both
callbacks and owned slices is also checked with allocation tracking.

## Analysis measurements

Native release compiler on macOS 15.6, ARM64. One warmup and five repeated
`check` commands per workload; medians below. Separate temporary output/cache
directories were cleaned after the measurements. No user project was edited.

| Workload | Complete check | Callback verification, inclusive | Callback verification, self |
| --- | ---: | ---: | ---: |
| ordinary | 53.04 ms | 0.00 ms | 0.00 ms |
| pure_callbacks | 200.09 ms | 0.37 ms | 0.33 ms |
| hilo_test | 260.85 ms | 21.23 ms | 16.00 ms |
| poker_pmap | 664.07 ms | 46.04 ms | 27.51 ms |

The two callback verification passes are combined. Inclusive time contains
resolver work whose cached answers can be reused by later analyses; self time
excludes nested profiled stages. These are phase measurements, not a controlled
before/after build-time comparison. They do not establish a wall-clock speedup
or an exact net build-time penalty.

The ordinary program has no pure functions and skips callback verification.
Concrete type comparisons preserve Odin's parameter/result names and erase the
pure effect qualifier. The checker memoizes type compatibility and concrete
procedure visits, and bounds recursive specialization work. Optional folding
leaves callback-valued results inside the enclosing helper or at runtime rather
than attempting to serialize process-local code pointers.

Raw measurements: [pure-callbacks-2026-11-26.json](pure-callbacks-2026-11-26.json).

## Regression coverage

Validation passed 100 feature/default/metadata/editor tests, 86 pmap/record
regressions, 136 Rust unit tests, 1,178 TLS unit tests, and 7 assertion-hygiene
checks. The broader 245-test comptime/no_alloc pass caught an opaque bit-set
identity regression during implementation; after correcting it, all 31 SDK
checks and the feature suite were rerun successfully. The final feature suite
includes 27 callback test methods with rejection matrices, checked/unchecked
runs and native type-identity cases. Fifteen repeated runtime-callback test runs
also passed after naming inline callback literals for native type queries.

The focused release runs cover pure callback assignments and conversions,
mutable containers, aliases, generic forwarding, overloads, inline callbacks,
callback factories, `using`, checked ownership, native type comparisons,
compile-time callbacks and `#run`, caller metadata, pmap, no_alloc, and live TLS
hover/completion/diagnostic recovery. The LLVM comparison protects the native
calling convention and dispatch costs.

Reproduce the core feature tests with:

```sh
python3 tools/build_compiler.py
python3 tools/build_tls.py
THOR_TEST_PREBUILT=1 python3 tools/test_compiler.py \
  --match 'tests.test_pure_callbacks' --output /tmp/pure-callbacks-tests.json
```

Profile a project with `THOR_PROFILE=/tmp/callback-profile.json thor check .`.
The relevant phase is `analysis.pure_callbacks`.
