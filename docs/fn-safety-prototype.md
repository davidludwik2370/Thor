# Fast fn safety prototype

The fast liveness checker is experimental and disabled by default. It is a
prototype of one part of the [fn safety plan](fn-memory-safety-plan.md), and does
not establish a memory-safety guarantee. Ordinary builds and generated runtime
code retain their existing behavior.

```sh
thor build . --fn-safety=prototype
thor check . --fn-safety=strict
thor build . --fn-safety=off
```

Both `--fn-safety=prototype` and `--fn-safety prototype` work. The modes are:

| Mode | Behavior |
| --- | --- |
| `off` | Default. Skip the new analysis. |
| `prototype` | Reject detected lifetime/callback defects. Report incomplete checks as information with code `thor/fn-safety-unverified`. |
| `strict` | Also reject every reported incomplete check. This is a development setting for the prototype, not a safety certificate. |

Use `check` or `build` to inspect the complete informational diagnostics. TLS
accepts the same flag through its compiler arguments and preserves diagnostic
locations, severity and unsaved recovery. Proof gaps are explicit and independent
of vet's `--unverified` summary policy. They cannot be suppressed with `vet_ignore`.

## Initial coverage

The checker follows allocated owners and borrowed aliases through local
bindings, value-record fields, slice views, multi-value declarations and
assignments. It diagnoses access after release, repeated release, release of
stack/static storage or rebased views, and tracked nil callback calls. It checks
returned storage after deferred actions, models late-bound cleanup expressions,
and follows scoped cleanup at block exit. Literal zero-length default slice
construction has no backing owner to release.

```thor
bad :: fn() -> int {
    values := make([]int, 2)
    alias := values[:]
    delete(values)
    alias[0] // fn safety error
}
```

Branches and conditional expressions merge finite ownership states. Simple
loop break/continue transfers are tracked, and a range source's effects are
evaluated once. Known compile-time selections and
unreachable nil-guard branches are skipped. Read-only scalar copies and header
queries such as `len(values)` retain their distinction from backing-store reads.
The checker respects source/command bounds-check settings and assertion elision;
it does not turn disabled checks back on.

Procedure summaries record input reads, input release effects and returned
ownership. A helper is analyzed once per resolved procedure/type substitution
within a command. Multiple call sites reuse its summary. Runtime callback
alternatives are bounded; opaque `fn` inputs use the valid-input callback
contract. Constant aliases, generic call sites, callback specializations, named
arguments, independent owned results and explicit `#run` share this analysis.
A named actual expression is evaluated once in the abstract flow.

The existing purity, type/schema, borrowed-result and native compiler checks
continue to apply. The prototype neither replaces those passes nor introduces
implicit cloning or runtime lifetime tracking. A regression compares emitted
native source with the feature enabled and disabled.

## Execution and cache ordering

The original-source pass runs before scheduling automatic compile-time work or
`#run`. Evaluation helpers inherit the mode and concrete specialization facts.
Known bad bodies therefore fail before native helper execution. A warm `#run`
cache cannot bypass validation of a changed source body.

Summaries are kept only within their resolved analysis command. There is no new
persistent proof cache. Existing parser, metadata and evaluation caches retain
their dependency/configuration validation, while the new pass runs again on
warm builds. Disabled builds skip it entirely.

## Explicit limitations

The prototype assumes borrowed inputs are valid and live throughout the call.
Callback inputs must identify valid non-null implementations with the required
contract. Unrestricted `proc` callers are outside this pass; those entry
preconditions are not checked throughout arbitrary caller code. The compiler,
reviewed primitives, native SDK and generated scratch/result promotion remain
trusted implementation boundaries.

The current flow domain is deliberately limited. Reported gaps include:

* Switch coverage/correlations and unsupported value expressions.
* Branch-dependent replacement of owners whose selected-reference/liveness
  correlation is lost at a join.
* Recursive summaries, uninstantiated generics and unselected type-dependent
  branches.
* Unreviewed native/indirect callees, deep opaque result graphs, and unknown
  callback initialization/results.
* Field-sensitive interprocedural effects for aggregate inputs/results, and
  heap-field or reference/callback element stores through shared aliases.
* Repeated stack/heap generations, capacity growth, relocation, complex
  deferred cleanup and custom allocator effects.
* Bounds checks disabled without a corresponding static bounds proof.
* Analysis beyond the fixed budgets. Limits never establish safety.

The initial budgets are four loop-state passes, eight callback alternatives,
64 traversal/call levels, 512 storage facts per frame and 32,768 frame steps.
Type/aggregate traversal is limited to 24 levels. These are prototype limits,
not source-language restrictions. Unmodeled value correlations can also require
additional precision before general-purpose mandatory checking is appropriate.

`strict` rejects the gaps this prototype knows how to identify. Complete safety
also needs the remaining obligations from the plan, including full heap/borrow
invalidation, exact allocator/initialization contracts, native helper contracts,
complete control-flow semantics, and validated lowering/runtime boundaries.
Neither mode currently promises that an accepted program is memory safe.

## Performance experiment

The experiment compares the same release compiler with the pass disabled and
enabled. Paired build order alternates to reduce first/second-run bias. Cold
runs get empty disposable compiler data caches; the installed frontend and OS
page caches remain available. Warm runs reuse separate, prewarmed caches for
each mode. One Odin worker is used throughout. Output programs, object files
and temporary caches are removed automatically.

The synthetic workload contains 200 `fn` declarations using borrowed slice
aliases and scalar results. A second synthetic workload has 40 functions using
owned slice results, generic clones and callbacks. A plain program measures the
no-fn control. Real workloads include `hilo_test`, `poker_pmap` and
`examples/parallel`. The report
records every timing pair, median overhead, a bootstrap interval, analysis
phase time, summary counters and informational proof gaps. Analysis profiles
are taken separately from the wall-clock comparison.

The acceptance target is at most 5% overhead. Coverage and diagnostic accuracy
are additional requirements before making any safety checking mandatory. A
small sample meeting the timing target alone is insufficient.

Measured on November 28, 2026 with the optimized native compiler on this ARM64
Mac (Darwin 24.6.0), using seven pairs per workload and temperature:

| Workload | Cold overhead | Warm overhead | Safety phase, profiled separately |
| --- | ---: | ---: | ---: |
| `hilo_test` | +0.52% | -0.02% | 0.67 ms |
| `poker_pmap` | +0.02% | +0.43% | 1.85 ms |
| `examples/parallel` | +0.45% | -0.39% | 0.06 ms |
| Plain no-fn control | +0.33% | -0.42% | 0.001 ms |
| 200 borrowed-slice functions | -0.93% | +0.06% | 1.72 ms |
| 40 owned-result/callback callers | +0.26% | +0.22% | 1.80 ms |

Overheads are medians of paired percentage differences, rather than ratios of
the independent medians. Negative measurements reflect timing noise, not an
optimization. Every median and every upper endpoint of the exploratory 95%
bootstrap intervals was below 5%; the largest upper endpoint was 2.96%.
This sample meets the timing target on this host. It does not establish a bound
on other projects or platforms. Wall-clock measurements include resolver
preparation and all downstream compiler work; the safety phase column records
only its own inclusive span.

The synthetic slice workload analyzed all 200 summaries without a reported gap.
The owned/callback workload analyzed 42 summaries, reused 40 cached summaries
and reported no gaps. `hilo_test` analyzed one summary without gaps.
`poker_pmap` analyzed eight summaries, five without a reported gap; its four
informational findings concern two switch operations and two stores through
indexed/indirect aliases. `examples/parallel` currently uses `proc` callbacks
and has no user `fn` roots, so it measures the quick skip path. None of these
coverage counters constitutes a safety certificate.

The checker remains **disabled by default** despite meeting the measured timing
target. Switch/control-flow completeness, heap effects and the other limitations
above must be addressed and validated before mandatory checking is appropriate.

[Raw timing pairs, intervals, counters, gaps and validation inventory](benchmarks/fn-safety-prototype-2026-11-28.json)
are retained with the compiler binary hash. All temporary benchmark caches and
generated outputs were removed after measurement; project sources were unchanged.

Reproduce the experiment after building the release compiler:

```sh
python3 tools/build_compiler.py
python3 tools/benchmark_fn_safety.py \
  --project hilo_test=~/Code/Thor/hilo_test \
  --project poker_pmap=~/Code/Thor/poker_pmap \
  --repeats 7 --output /tmp/fn-safety-benchmark.json
```

The focused tests are `tests.test_fn_safety` and `tests.test_fn_safety_tls`.
They include checked/unchecked runtime oracles, assertion elision, original
source ranges, aliases, callbacks, generic specializations, cache invalidation
and rejection before compile-time helper execution.

Validation passed 37 focused compiler/editor tests against both development and
optimized compilers, 172 distinct compiler/editor regressions across the selected
suites, 136 Rust unit tests and seven assertion-hygiene checks. The focused 37
are included in the 172. The broader suites cover result ownership, pure callback
types, lifetime vetting, compile-time modes, CLI controls and extension contracts.

Run the focused regressions with:

```sh
python3 tools/test_compiler.py --match 'tests.test_fn_safety' \
  --output /tmp/fn-safety-tests.json
```
