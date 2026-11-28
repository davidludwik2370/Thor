# Mandatory memory safety for fn

This plan follows the November 28, 2026 audit of pure-function and callback vetting.
The implemented changes improve vet's diagnostics. A future mandatory compiler
check would establish the safety contract described here.

## Audit findings and corrections

The audit follows pure inputs and results through procedure aliases, callback
aliases, generic instantiations, named and implicit returns, runtime-selected
callbacks, records, slices, constant callback defaults and `#run`.

* A runtime `fn` callback was treated as potentially retaining or releasing its
  borrowed inputs. Vet now uses the compiler-checked purity contract, checks
  input storage, and preserves the caller's ownership facts.
* Opaque pure callback results were conflated with borrowed input. Supported
  slice, string, dynamic-array and record result shapes now receive independent
  potential owners. Nil and empty results retain conditional allocation facts.
  Type-shape facts are cached within the analysis and shared across roots.
* Runtime-selected callbacks lost their inspectable implementations. Vet checks
  up to eight known alternatives using the existing call interpreter. Arguments
  retain their once-only evaluation, named argument arrangement and declared
  callback defaults. Ordinary procedure effects remain conservative. Pure
  alternatives share the checked independent-result contract.
* Result promotion reused one abstract allocation for repeated aliases. The
  runtime produces independently releasable storage for those result members.
  The analysis now follows that behavior while retaining a recursion guard.
  Explicit `_` padding fields follow the runtime's skipped-field rule and
  create no caller-owned allocation obligations.
* Inferred record literals in returns could lose their field names, producing
  spurious missing-cleanup notices. Return annotations now supply their context.
* A buffer released on one branch before return could be made abstractly live
  during promotion. Promotion now checks the source storage, including uncertain
  releases. Explicit and deferred release defects remain findings.
* Explicitly nil and zero-initialized callback bindings could be invoked without
  a finding. Vet now tracks those values through copies and branch joins, reports
  invalid calls, and respects nil guards.
* Exhausting the abstract-value budget could erase a constant procedure's known
  identity and add misleading unknown-call findings. Constant identities remain
  available after that limit. Invalidated runtime callback storage still requires
  conservative effect handling, and the original budget finding remains.

The implementation and runtime oracles are covered by
[test_lifetime_pure_callbacks.py](../tests/test_lifetime_pure_callbacks.py).
[Live TLS tests](../tests/test_pure_callbacks_tls.py) cover original source ranges
and unsaved recovery. Existing ownership, summary, recursive-reader, scratch,
callback, compile-time and lifetime suites provide the surrounding regressions.
The alias-result runtime oracles run with assertions enabled, disabled, and with
unchecked release flags. Their verdicts use explicit conditionals and panics.

## Proposed guarantee

Every accepted `fn` would preserve memory safety for inputs satisfying its entry
contract. Borrowed storage must be initialized, valid for its declared type,
alive throughout the call, and protected from concurrent invalidation or
conflicting access. Callback values must identify valid implementations with the
required safety contract. The compiler would prove the function's own reads,
writes, cleanup, local references and returned ownership under those assumptions.

Pure calls may terminate through checked panic/trap paths. Termination, bounded
memory consumption and algorithmic correctness are additional properties.
The existing scratch cleanup and owned-result rules also support a separate
cleanup guarantee on successful return.

A caller written as an unrestricted `proc` can violate an entry contract, for
example by passing a previously freed slice. Extending the guarantee to those
call sites requires checking the caller's relevant ownership operations as well.
A function's declaration alone cannot establish the lifetime of external storage.

Purity and ownership of a result do not establish that every intermediate access
is safe. For example, this function needs a lifetime error even though it only
changes its own local storage.

```thor
bad :: fn(flag: bool) -> []int {
    values := make([]int, 2)
    if flag { delete(values) }
    return values
}
```

The audit adds a regression for that conditional release. A mandatory checker
would reject the unresolved live/released state during an ordinary build.

Guaranteeing safe calls from unrestricted `proc` code is a larger boundary
decision. The current raw slice ABI cannot identify every dangling input at
runtime. Requiring callers to prove the relevant borrow, introducing checked
reference representations, or documenting a valid-input precondition are
different contracts. The recommended first milestone proves the `fn` body under
its valid-input precondition and states that boundary explicitly.

The intended language has one checked `fn` form. Code outside the supported proof
rules receives a compiler error explaining the unresolved obligation and the
operations that can make the ownership explicit. General `proc` retains its
existing role for unrestricted operations.

## Proofs suitable for regular builds

The regular compiler should use finite dataflow facts and checked interfaces.
It should reuse the existing purity, permission and result-provenance machinery.

| Obligation | Regular-build approach |
| --- | --- |
| Supported input and result types | Existing exact native schema checks and concrete generic substitutions |
| Read-only borrowed inputs | Existing permission graph, extended with liveness at each use |
| Locally allocated storage | Track live, moved, released and possibly released owners through control flow |
| Copies, aliases and slice views | Track storage identity and live borrows, including nested reference fields |
| Local addresses passed to helpers | Checked read/write/retention summaries for the pure calling context |
| Cleanup | Require a live owner and compatible allocator, and invalidate dependent views |
| Append and resize | Require outstanding borrows to end or prove the operation cannot invalidate them |
| Results | Require live, independently owned backing and valid nested members at the actual transfer point |
| Callbacks | Require the same safety contract for every permitted target and verify non-nullness at use |
| Generic functions | Verify each reachable concrete specialization with its exact callback and storage contracts |
| Indexing and slicing | Retain runtime checks wherever a static bounds proof is unavailable |
| Recursion | Check finite interface summaries with a bounded fixed-point computation |

The analysis should merge states at control-flow joins instead of enumerating all
paths. A bounded result includes a proof or an unresolved obligation. Reaching a
budget cannot produce acceptance.

Safety validation must precede automatic compile-time execution and `#run`.
Reusing a cached result must still validate the source function's safety contract.
Dependent type/default evaluation needs the same staged ordering and explicit
cycle handling. The host evaluator cannot supply a safety proof by successfully
executing one input.

Existing build flags and source directives need an explicit safety rule. Required
checks inside an accepted `fn` must remain active until the compiler proves their
removal safe. Flag combinations that defeat that requirement must receive a clear
error. Compiler assertions cannot perform required validation or cleanup.

Callback non-nullness deserves its own treatment. Existing `fn` types currently
permit nil values and zero-initialized storage. Mandatory safety checking needs
to distinguish storage awaiting initialization from a callable value and track
that distinction through aggregates, returns, parameters and aliases.
The audit diagnoses tracked explicit nil values and zero-initialized bindings.
It does not certify non-nullness for every opaque callback result or default
aggregate field.

## Work suited to slower vet analysis

Vet can retain its larger interprocedural interpreter and offer deeper
explanations for safe code rejected by the fast rules. Useful extensions include
path-dependent disjointness, correlated conditional owners, loop invariants,
recursive reference graphs, container relocation, and opaque library boundaries.
Its larger budgets also help inspect unrestricted `proc` callers of `fn` APIs.

An expensive vet result cannot be the sole safety gate for an ordinary build.
Two sound workflows are available.

1. The mandatory fast checker accepts a conservative subset. Vet provides
   explanations and suggested code changes for the rest.
2. A slower proof producer emits certificates for difficult functions. Regular
   builds validate those certificates with a small deterministic checker. A
   changed dependency or incompatible certificate triggers proof regeneration
   or an explicit unresolved-obligation error.

The first workflow is the recommended starting point. It avoids a second proof
format during the initial implementation. Certificates can follow once measured
rejections justify their complexity.

## Remaining analysis boundaries

Current vet is a bounded diagnostic analysis. Its findings and lack of findings
are independent of the future certification state.

* Opaque callbacks returning recursive reference graphs, nested dynamic slices,
  reference-bearing unions, SOA layouts or unresolved types still require more
  precise result models. Supported ordinary records and small fixed arrays use
  bounded shapes. Unsupported shapes retain unverified diagnostics.
* Current alias and loop approximations can lose correlations. Unknown effects
  and analysis-budget diagnostics remain visible through `--unverified`.
* A pure callback's checked effect and result-ownership contract establishes
  facts usable at its caller. Its body's complete safety still needs proof.
* Native helpers and generated promotion code form a trusted implementation
  boundary. Their exact layouts, allocation arithmetic, failure handling and
  allocator behavior need dedicated verification and regression coverage.
* A source-level guarantee needs all memory-relevant constructs to have defined
  proof rules, including valid callback initialization, bounds-check options,
  generic defaults, helper effects and compiler-generated operations.
* Borrowed arguments arriving from arbitrary `proc` code retain caller
  obligations. Whole-program race freedom requires additional concurrency rules.

These boundaries are reasons to withhold a certificate for an unsupported case.
An acknowledgement such as `vet_ignore` cannot satisfy a mandatory safety proof.

## Implementation order

This is a substantial compiler feature spanning flow analysis, type contracts,
native helper contracts, lowering and caches. A prototype of the first two
stages should measure both compile cost and safe programs it cannot yet accept
before the language makes the guarantee mandatory.

1. Specify the guarantee and its boundary at unrestricted callers. Define the
   abstract owner/borrow states, normal and terminating exits, callback validity,
   required runtime checks, and supported operations as executable contracts.
2. Build a compact control-flow representation using resolved types. Reuse the
   current permission/provenance graph and add local liveness, release and
   borrow-invalidation checks. Start with scalar/fixed values and flat slices.
3. Extend helper and callback interfaces with checked safety and ownership
   summaries. Validate aliases, mutable callback storage, defaults and every
   concrete generic specialization. Memoize by the complete semantic context.
4. Cover nested records, nested slices, unions, SOAs, dynamic growth, recursion
   and ownership transfer. Keep unsupported cases as explicit errors while
   adding these rules incrementally.
5. Verify lowering and runtime boundaries against the source contracts. Exercise
   generated cleanup, duplicated result aliases, allocator failure, nil values,
   assertions disabled, unchecked flags, compile-time execution and cache replay.
6. Benchmark real projects and an adversarial suite of safe and unsafe programs.
   Enable mandatory checking for all `fn` declarations once the defined subset,
   diagnostics, compatibility consequences and performance meet the contract.

Caching must include compiler implementation, source and dependency content,
configuration, target, native type/layout facts, build flags and callback
contracts. Warm caches and fresh runs must agree. The exact-input vet cache
already provides this property for current diagnostics. Future certificates
would additionally need proof validation before reuse.

## Performance evidence

Measurements use this Mac, the release compiler, one Odin worker, and the shared
bounded cache. Each mode has one initial run and three measured repeats. Outputs
are generated in a temporary directory and removed automatically. The tables
report median wall-clock seconds for the repeats. These are small samples, so
small differences should be treated as measurement noise.

The ordinary-build column includes native compilation and linking. The warm-vet
column benefits from the exact-input memory-analysis cache. The uncached-vet
column measures recomputation and is the relevant warning against putting the
whole current interpreter into every regular build.

| Workload | Build before → after | Warm vet before → after | Uncached vet before → after |
| --- | ---: | ---: | ---: |
| sandbox | 0.945 → 0.851 | 0.445 → 0.435 | 1.554 → 1.472 |
| hilo_test | 0.907 → 0.854 | 0.351 → 0.344 | 0.957 → 0.903 |
| poker_pmap | 1.711 → 1.512 | 1.309 → 1.157 | 7.713 → 7.405 |
| parallel | 1.401 → 2.230 | 0.925 → 1.781 | 3.446 → 3.275 |

Uncached vet did not slow down in these samples. Three workloads also had lower
build and warm-vet medians. Those differences alone do not establish speedups.
The parallel example's warm build and vet times increased, and a profiled
recheck reproduced that difference. The ordinary build has no memory-analysis
stage. The warm vet hit its memory-analysis cache, with about 1.2 ms spent on
that cache key, while the uncached memory analysis took 38 ms in the profiled
sample. Both build and vet also compiled and ran a native `#run` helper. The
comparison does not isolate the cause of the warm wall-clock difference, so it
cannot establish a general build-time regression or its absence. The full
current vet interpreter would still be an unsuitable default build gate.

`sandbox` and `hilo_test` had no memory findings before or after this audit.
`poker_pmap` retained ten unverified findings at five locations, all caused by
analysis limits, with no definite findings. A limit still leaves work unchecked.
The same count does not establish that every remaining path is safe.

Validation passed 256 distinct compiler/vet tests, 17 live TLS tests, 136 Rust unit
tests and seven assertion-hygiene checks. Runtime ownership oracles cover checked,
assertion-disabled and unchecked release builds.

[Raw measurements and finding summaries](benchmarks/fn-vet-audit-2026-11-28.json)
preserve every timing sample and the comparison methodology.

An initial performance acceptance target for the future fast checker would be
at most 5% median overhead on representative builds, with an explicit absolute
budget for small projects. This is an acceptance target for the future checker.
Its implementation must demonstrate that cost against the benchmark corpus.
The audited changes execute only when memory vetting is requested and add no
generated runtime instructions.
