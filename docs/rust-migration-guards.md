# Rust migration guard audit, 2026-11-11

The native compiler remains the default `thor` implementation. `thor-python`
retains the reference implementation for comparisons. This audit changes Rust
analysis, scheduling and diagnostics; it adds no language features and makes no
changes to Odin.

## Confirmed failures corrected

An inactive native `when` branch inside a generic procedure could lose the
record that its nested `#run` request had been processed. Preparing an enclosing
helper then reported an unscheduled evaluation, even though no evaluation was
needed in that branch. The scheduler now retains skipped cases separately for
each relevant binding. An unchecked `when` arm needs neither an executed value
nor a result-type probe. An inactive runtime `if` arm still needs its type and
continues to be checked. The regression tests cover both cases, absent callees,
selected traps, imported procedures, repeated type bindings, warm result caches
and independently owned returned slices.
Skipped unchecked cases remain scheduling records; lowering and nominal-identity
probing omit their unnecessary type predicates. Local types used only in an
inactive native branch therefore need no name in another scope. Runtime `if`
skips still retain their native type checks.

Purity analysis previously stopped after 1,024 distinct visited call instances.
That rejected a finite pure function calling 1,024 different eligible procedures.
Distinct procedures and repeated identical visits no longer consume the
specialization budget. New specializations of an already visited procedure do:
their budget scales with the number of non-SDK syntax nodes, with a minimum of
1,024. Exhaustion produces an explicit analysis-limit diagnostic instead of
claiming the program uses an unported feature. Existing recursion-depth limits
remain in place. Unit tests exercise budget exhaustion and resetting between
roots; a language test checks a graph with 1,025 distinct callees.

Several ordinary invalid programs incorrectly reached migration guards. Known
missing struct members, calls to scalar data fields, unknown names in a file
with an unrelated selective import, and unknown namespace calls now produce
source errors. Unknown generic shapes continue to require safe resolution;
this change does not allow an unresolved call through purity verification.

Compile-time comprehensions and expanded `min`/`max` initialized from runtime
collection storage now explain that their inputs must be compile-time known.
Constant collection inputs remain supported. TLS tests check original UTF-16
source ranges and diagnostics clearing after an unsaved correction.

Testing inferred result defaults uncovered an ownership bug shared by the
starting native compiler and the frozen Python reference. A result without an
explicit type annotation was omitted from ownership detection. A returned slice
could therefore refer to freed function scratch storage, including during
automatic constant execution. Reconstructing an owned-result wrapper also lost
native named-result defaults.

The native fix derives the result's type from its default and includes it in
validation and ownership detection. Defaults are preserved on the inner body
and removed from the outer wrapper, so initialization happens in one place.
Promotion copies results to the caller before scratch cleanup. Result defaults
also participate in purity effect checks. The new native-only tests check actual
values, independent allocations, mixed named returns, ordinary checked helper
results and imported types under ASan and unchecked release allocation tracking.
The Python reference is intentionally unchanged; these tests do not assert
equivalence to its known incorrect behavior. Odin's restrictions on constant
defaults and polymorphic compound literals remain in force.
The broader regressions also cover the parser's special unnamed-result marker,
generic multiple returns and deferred result updates, so inferred named results
do not change the handling of unnamed native signatures.

Migration diagnostics previously recommended `thor` as the Python command,
which became incorrect when the default launcher switched to Rust. They now
name `thor-python` and describe the unresolved analysis path. Exit status `3`
and the structured `rust-migration` stage remain compatible with existing tools.

## Retained guard inventory

The following guards remain conservative failures, not successful fallbacks.
The audit did not prove them unreachable for every possible program.

| Location | Guarded condition | Why retained |
| --- | --- | --- |
| `project.rs`, slice-analysis finalization | Residual unresolved generic slice branches | Generating code without resolving access privileges could admit mutation through an immutable view. |
| `storage.rs`, `permission_query` | Symbolic helper ownership terms outside the supported form, including a foreign-source type reference | A safe ownership predicate must be expressible in the generated helper's scope. |
| `purity.rs`, `Facts::validate` | Pure calls whose specialization remains unresolved | Unknown call effects cannot be treated as pure. Definite source errors are handled separately. |
| `evaluation.rs`, helper planning | A retained nested evaluation lacks a result, skip or specialization case | An outer helper must not silently execute a missing or wrong compile-time request. |
| `lower.rs`, preflight | An active pure body was not verified, or a `#run` was not scheduled | Lowering must preserve the analysis/scheduling contract. |
| `purelower.rs`, result types | A checked pure result has neither an annotation nor a default from which to derive its type | Result storage cannot be emitted with a guessed type. Native inferred result defaults are covered by positive tests. |
| `purevariants.rs`, checked bodies | A checked procedure cannot be associated with its named declaration | Checked variants require a declaration identity and correctly mapped body. |
| `comprehensions.rs` and `extrema.rs`, constant lowering | A constant collection operation reaches lowering without an evaluated value | The lowerer must not replace a required constant with a runtime allocation. Known runtime-input errors are diagnosed earlier. |

The audit probes include generic array copies versus borrowed slice mutation,
Thor and Odin library procedures, imported generic results, native `using`,
inferred result defaults, type-dependent branches and nested evaluation. These
checks establish coverage for those shapes, not a proof that arbitrary symbolic
ownership or specialization is complete. Native Windows/Linux hosts remain
unverified by this macOS arm64 pass.

`thor --capabilities` retains `complete: false`, `python_fallback: false` and the
existing `parallel_lowering` field. Additive `parallel_work` metadata identifies
the implemented parallel source parsing and source-map serialization separately
from semantic lowering. Performance characterization is no longer listed as
unimplemented work. Further optimization opportunities have their own field.

To compare a reported limitation, run equivalent commands with separate output
directories and retain both original-source diagnostics:

```sh
thor check . --output-dir /tmp/thor-native-check -json-errors
thor-python check . --output-dir /tmp/thor-reference-check -json-errors
```

A failing guard should be reduced and fixed with an acceptance test. Removing
the guard without establishing the required type, purity or ownership facts is
not a migration fix.

## Validation and timing

The corrected release compiler passed 759 compiler integration methods: 703
existing checks covering resolution, purity, borrowing, scheduling, execution,
caching and diagnostics; 18 new guard-audit methods; and 38 Integer/Rational
checks. This includes checked/debug ASan runs and unchecked release runs with
allocation tracking. The broader pass exposed unnamed-return and inactive
local-type regressions in the first candidate; both were corrected before the
complete gate was repeated successfully.

All 170 stdio TLS checks and all 42 live CodeLLDB checks passed, including the
new hover, unsaved-recovery and inferred-result frame checks. Both debug and
release Rust library runs passed all 53 unit checks. All-target Clippy with
warnings denied and all seven assertion-hygiene checks passed. Copies of all 19
user projects built with both compilers, for 38 successful builds; original Thor
source hashes were unchanged.

The separate stress run completed 64 cold builds with four concurrent compiler
processes. Half exercised nested generic `when` requests and repeated type
bindings; the rest exercised nested ordinary calls. Both patterns ran in debug
and unchecked release configurations with result caching disabled. Profiles
recorded 512 actual helper compilations, with at least one in every case. There
were no build failures or timeouts. The default five-second execution deadline
is unchanged. The cause of the historical isolated timeout remains unconfirmed.

The following medians compare the pre-audit and corrected production release
binaries on the same macOS arm64 host. Each case has nine warm samples per
binary, paired in alternating order with four workers. Initial invocations and
separate profiles are excluded, as is Odin's final compilation step. The results
show no meaningful timing regression in this sample.

| Workload | Before | After |
| --- | ---: | ---: |
| Minimal program | 0.019 s | 0.019 s |
| `double_bet` | 0.170 s | 0.169 s |
| `poker` | 0.423 s | 0.421 s |
| `hilo` | 0.227 s | 0.223 s |
| `circles_on_dots` | 0.349 s | 0.342 s |
| `poker`, test mode | 0.882 s | 0.886 s |
| 256 procedures with generic calls | 0.370 s | 0.370 s |

Poker test mode's corrected p95/maximum was 0.894 seconds. Nine samples are a
regression check rather than a universal time bound. Cold compile-time work,
project size and OS/filesystem delays remain outside this warm timing claim.

The [audit evidence](rust-guard-audit.json) records the code checkpoint, compiler
and input hashes, module groups, raw paired samples, separate profiles, project
verdicts and per-case helper-stress results. The Python reference remains
available, and the native launcher remains the default.
