# Allocation-checker audit cost, 2026-11-20

Paired warm process wall times on macOS arm64 compare the compiler before this
audit with the final release compiler, both with and without allocation
contracts. Each variant has seven transpile samples and three optimized builds.
The parser/evaluation cache is shared, output directories are isolated, order
alternates, and native compilation uses one thread. No test/build jobs ran
concurrently. [Raw results](no-alloc-edge-audit-2026-11-20.json) retain every
measurement/profile, binary hashes, and source-comparison details.

## Transpile

Median milliseconds:

| Workload | Before, no contract | After, no contract | Before, contract | After, contract |
| --- | ---: | ---: | ---: | ---: |
| Small scalar program | 39.01 | 38.98 | 39.27 | 39.05 |
| 1,002-procedure graph | 131.64 | 132.98 | 165.65 | 167.68 |
| Aliased SDK math | 39.19 | 39.35 | 61.75 | 62.17 |
| Fixed-buffer input + fmt | 39.16 | 39.52 | 201.30 | 202.70 |
| basic.print | 124.57 | 128.50 | 335.93 | 344.63 |

## Full optimized build

Median milliseconds:

| Workload | Before, no contract | After, no contract | Before, contract | After, contract |
| --- | ---: | ---: | ---: | ---: |
| Small scalar program | 215.46 | 213.49 | 214.04 | 213.19 |
| 1,002-procedure graph | 384.68 | 377.63 | 408.85 | 417.65 |
| Aliased SDK math | 217.32 | 216.26 | 239.55 | 239.91 |
| Fixed-buffer input + fmt | 1097.47 | 1096.39 | 1273.46 | 1275.06 |
| basic.print | 1470.01 | 1470.27 | 1691.96 | 1697.97 |

## Interpretation

The 1,002-procedure workload originally exposed repeated builtin identity
queries for ordinary procedure results. Skipping that irrelevant classification
removed about nine milliseconds of avoidable work. The final comparison above
places ordinary transpilation within about four milliseconds of the pre-audit
compiler in these workloads; contract-enabled transpilation differs by at most
about nine milliseconds. These are representative paired measurements, rather
any universal timing bound.

Every workload emits identical executable Odin statements and helpers across
all four variants. Comparison removes blank lines and only proven-unused
generated `base:intrinsics` imports; original hashes and the exact ignored
declarations remain in the raw report. No other imports or executable code are
ignored. The changes add no runtime guard, wrapper, thread, allocator call, or
ABI parameter. Independent checked, unchecked optimized, and debug tests also
confirm zero tracked allocations in the newly accepted operations.

## Reproduction

Keep the saved pre-change binary outside the checkout, build the current
release compiler, then run:

```sh
python3 tools/benchmark_no_alloc.py --baseline /path/to/pre-audit-thor-rust \
  --include-io --compare-enabled --runs 7 --build-runs 3 \
  --output docs/benchmarks/no-alloc-edge-audit-2026-11-20.json
```

See [the audit report](../no-alloc-edge-audit-2026-11-20.md) for corrected cases,
negative tests, and remaining conservative limitations.
