# Immutable vet audit: November 21, 2026

Five paired warm samples per compiler/workload on macOS ARM64, using Cargo
release builds. Samples alternate compiler order and run with other validation
workers idle. Sources are unchanged; output and profile files use temporary
directories. [Raw results](immutable-vet-2026-11-21.json) include every sample,
binary hashes, commands, source hash and work counters. The baseline is commit
`1e3b93b`.

| Workload | Before | After | Findings before / after | Validation checks before / after |
| --- | ---: | ---: | ---: | ---: |
| 1,000 independent locals, immutable check | 0.478 s | 0.440 s | 1,000 / 1,000 | 1 / 1 |
| `poker_pmap`, immutable check | 0.568 s | 2.267 s | 0 / 15 | 0 / 3 |
| `poker_pmap`, default vet | 0.594 s | 2.269 s | 0 / 15 | 0 / 3 |

Times are medians of complete CLI invocations. The poker comparison includes
substantially more work: the baseline silently missed every immutable finding in
this source snapshot and therefore did no proposed-edit validation. The audit
reports 15 compiler-accepted declarations, including four manual-review warnings.
It validates the expanded batch, rejects eight invalid readonly candidates
together, and separately verifies the automatic subset. The roughly 1.7-second
increase pays for those three checks rather than a compile per declaration.

An intermediate version of this audit took 3.927 seconds and six validations on
the immutable poker pass. Opaque variadic calls reported only one readonly origin
at a time. Retaining all unconditional origins in their declaration notes reduced
this to three validations without changing permission verdicts or runtime code.
The raw report retains those intermediate samples separately. Conditional origins
keep their existing native guards.

The independent-local case hashes the diagnostic source once rather than 1,000
times. Type properties and call-graph properties are shared within each vet
analysis. Normal builds without vet do not run this candidate/validation pass.
These measurements cover the listed workloads, not a universal timing bound.

Validation passed 532 related native tests with one Windows-SDK-dependent skip,
98 Rust unit tests and seven assertion-hygiene tests. A 34-case compiler oracle
checks warning eligibility against actual readonly conversion, with additional
batch/application tests. The real poker project passed all 213 tests in checked
and unchecked builds with line 409 made readonly through a temporary overlay;
project sources remained unchanged. The frozen Python reference is unchanged.

See [immutable declaration advice](../immutable-vet.md) for manual-review policy
and alias-dependent editor actions.
