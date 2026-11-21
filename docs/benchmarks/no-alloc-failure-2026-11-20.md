# Fatal-reporting proof cost, 2026-11-20

Paired warm-process measurements compare the saved pre-change compiler with the
final release compiler. Each lowering median uses seven samples, alternating
engine order, with a shared parser cache and isolated output directories. There
were no concurrent compiler/test workloads during timing. Raw samples, compiler
hashes, and profiles are in [the JSON report](no-alloc-failure-2026-11-20.json).

| Case | Ordinary lowering before | Ordinary lowering after | Existing proof before | Proof after |
| --- | ---: | ---: | ---: | ---: |
| Small program | 39.23 ms | 38.95 ms | 39.43 ms | 39.43 ms |
| 1,000 procedures sharing a leaf | 135.39 ms | 135.53 ms | 168.89 ms | 173.14 ms |
| SDK scalar call | 39.18 ms | 39.43 ms | 62.00 ms | 62.00 ms |
| Fixed-buffer fmt I/O | 39.38 ms | 39.66 ms | 203.60 ms | 203.68 ms |
| basic.print | 129.00 ms | 129.71 ms | 343.50 ms | 342.88 ms |

An initial version repeated failure-reporter identity searches for ordinary
calls. Reusing known procedure definitions and bypassing unrelated builtins
reduced its extra cost on the 1,000-procedure proof from about 18 ms to about 4 ms.
Neither version performs failure-effect analysis when the contract is absent.

Two additional cases check the touched lowering and newly supported summaries:

- Ordinary returning f-string arguments: 49.40 ms before, 49.30 ms after.
- 100 `fmt.ensuref` calls: ordinary lowering 43.69 ms before and 43.86 ms after;
  contract-enabled lowering 77.45 ms. The profile records exactly 100 summarized
  failure calls. The before compiler rejected these summaries, so there is no
  before-enabled timing. This case supplies a constant source-location argument
  explicitly to keep caller metadata equal across discovery modes.

The emitted native code matches in every comparison, after removing blank lines
and proven-unused generated `base:intrinsics` imports. The namespace has no
runtime initializer; the main cases retain both original and normalized digests.
The successful runtime path gains no instrumentation or allocation. Separately,
nonreturning f-string argument helpers now preserve `-> !` and omit a defer that
could never execute; ordinary returning helpers retain their cleanup.

End-to-end build samples are also retained in the JSON. They include Odin and
linker variation and use only two samples per variant; they are not a statistical
claim about small backend timing differences. Runtime allocation behavior is
checked by the checked/unchecked allocator-count regressions, rather than inferred
from wall-clock noise.

Reproduce the main cases with:

```sh
python3 tools/benchmark_no_alloc.py \
  --baseline /path/to/pre-change/thor-rust \
  --include-io --compare-enabled --runs 7 --build-runs 2 \
  --output /tmp/no-alloc-failure.json
```
