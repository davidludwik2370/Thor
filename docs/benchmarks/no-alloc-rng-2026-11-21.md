# RNG allocation-proof cost, 2026-11-21

Paired warm-process measurements compare the saved compiler from immediately
before the RNG work with the final release compiler. Eleven transpile samples
per variant alternate engine order, using a shared parser cache and isolated
output directories. Timing is performed without concurrent compiler/test jobs.
The [raw JSON](no-alloc-rng-2026-11-21.json) retains samples, profiles, compiler
hashes, and original and normalized native-source digests.

The allocation proof is opt-in. Ordinary lowering does not construct the new
SDK declaration index, verify RNG effects, or inspect ambient replacements.
Existing proof costs are compared with the before compiler; RNG proofs have no
before-enabled timing because that compiler rejected them.

Medians in milliseconds:

| Case | Ordinary before | Ordinary after | Existing proof before | Proof after |
| --- | ---: | ---: | ---: | ---: |
| Small program | 35.42 | 33.30 | 32.73 | 33.26 |
| 1,000 procedures sharing a leaf | 131.43 | 129.72 | 167.66 | 172.82 |
| SDK scalar call | 34.50 | 34.62 | 57.45 | 58.40 |
| Fixed-buffer fmt I/O | 34.70 | 35.52 | 199.50 | 218.41 |
| basic.print | 130.93 | 123.15 | 348.57 | 357.68 |
| 100 default RNG calls | 34.91 | 35.05 | Unsupported | 97.33 |
| 100 seeded RNG calls | 36.77 | 36.68 | Unsupported | 102.73 |
| 100 custom RNG calls | 35.56 | 34.37 | Unsupported | 66.92 |

Ordinary lowering remains within the observed process/IO variation. Existing
proofs add approximately 1–19 ms in these samples; this includes new bounded
callback facts and native layout-query corrections. The RNG cases add roughly
33–66 ms over their unannotated variants. Profiles confirm that 100 default
calls share one ambient-context graph, one SDK snapshot verification, and one
requested native helper file. Seeded calls load two requested helper files and
share one SDK snapshot. The custom-generator case needs no default-generator
summary or SDK snapshot at all.

All compared native output is identical after excluding blank lines and
proven-unused generated `base:intrinsics` imports. Original digests are also
retained. There is no runtime instrumentation, allocator substitution, or new
RNG wrapper. The native allocator-count regressions independently check lazy
first use, several buffer refills, equal seeds, reset, and custom callback state
in checked and unchecked builds.

End-to-end build samples include Odin/linker costs and are retained in the JSON.
Two build samples per variant are useful context, rather than a statistical
claim about small backend timing differences.

Reproduce with:

```sh
python3 tools/benchmark_no_alloc.py \
  --baseline /path/to/pre-RNG/thor-rust \
  --include-io --include-rng --compare-enabled --runs 11 --build-runs 2 \
  --output /tmp/no-alloc-rng.json
```
