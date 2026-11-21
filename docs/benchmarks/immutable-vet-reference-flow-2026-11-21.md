# Immutable vet reference-flow follow-up

This follows the [earlier immutable audit](immutable-vet-2026-11-21.md).
The baseline is commit `26cc4b4`. Five paired warm CLI samples alternate compiler
order, using release builds on macOS ARM64 with validation workers idle. Output
and profile files use temporary directories; the source snapshot is unchanged.
[Raw results](immutable-vet-reference-flow-2026-11-21.json) record commands,
binary/source hashes, every sample, work counts and validation results.

| Workload | Before | After |
| --- | ---: | ---: |
| Default vet, `poker_pmap` | 2.228 s | 1.712 s |
| Immutable vet, 1,000 independent locals | 0.406 s | 0.405 s |
| Normal transpilation, `poker_pmap` | 0.490 s | 0.485 s |

Times are medians of complete CLI invocations. Both poker vet passes report 15
findings. Previously four required manual review; now all 15 are automatic.
Validation drops from three checks to two because there is no manual subset to
separate. The prospective permission graph shares 22 contexts across nine roots
and 29 candidate names. The scalar batch skips this graph entirely and retains
one compiler validation. Normal builds without vet do not run it.

All generated Odin source hashes match across the old/new normal transpilation
samples. This change affects vet advice and application safety, with no generated
runtime code change. Timings cover these workloads, not a universal bound.

The protections follow actual reference paths rather than whole signatures or
call graphs. An unrelated `^testing.T`, unrelated returned storage, or conditional
helper that only reads its argument permits automatic correction. Conditional
writes, target-dependent borrowed calls, and actual result/output escapes remain
protected. Copies, aliases, deferred stores, generic helpers, and referenced union
variants have regression cases. An independent array/union copy is distinguished
from a view into the original storage; `#switch` joins retain borrowed provenance.

Validation passed 350 broad vet/contract tests and 198 slice/TLS tests, with one
Windows-SDK-dependent skip. The final affected-test rerun passed 103 tests,
including the additional `#switch` case. All 98 Rust unit tests and seven
assertion-hygiene tests passed. In a temporary copy of the real poker project,
`--apply` corrected all 15 declarations, a second vet pass was clear, and all 213
tests passed in both checked and unchecked builds. Original user sources and the
frozen Python compiler remain unchanged.
