# Allocation-free I/O cost, 2026-11-20

Paired warm measurements on macOS arm64, using the saved pre-I/O compiler and
final release compiler. Every variant uses the same source offsets, shared parser
cache, isolated generated output, and one Odin build thread. Order alternates.
There are seven transpile samples and three optimized builds per variant.
Benchmarks run without concurrent build/test jobs. The
[raw results](no-alloc-io-2026-11-20.json) retain binary hashes, every time and
profile, original emitted-source hashes, and normalization details.


## Transpile

Median process wall time in milliseconds:

| Workload | Before I/O change | Current, no contract | Current, contract enabled |
| --- | ---: | ---: | ---: |
| Small scalar program | 35.02 | 34.90 | 34.99 |
| 1,002-procedure shared graph | 135.86 | 135.40 | 172.05 |
| Aliased SDK math | 35.18 | 35.29 | 59.78 |
| Fixed-buffer input + fmt | 35.45 | 35.38 | 212.03 |
| basic.print | 127.37 | 127.83 | 357.18 |

## Full optimized build

Median process wall time in milliseconds:

| Workload | Before I/O change | Current, no contract | Current, contract enabled |
| --- | ---: | ---: | ---: |
| Small scalar program | 225.16 | 227.66 | 227.61 |
| 1,002-procedure shared graph | 408.00 | 405.97 | 444.04 |
| Aliased SDK math | 230.99 | 231.45 | 255.14 |
| Fixed-buffer input + fmt | 1189.67 | 1192.30 | 1386.50 |
| basic.print | 1604.03 | 1607.24 | 1828.55 |

## Interpretation

Ordinary builds/transpiles remain within the noise of these samples. Programs
without a contract do not perform allocation-effect analysis or SDK verification.
The optional I/O proof adds roughly 0.18–0.23 seconds of warm transpile work in
these examples; full-build differences include the same analysis plus Odin.
These are representative measurements, not a universal bound for every project.

SDK source verification is shared across dependency-discovery retries within one
request. Newly loaded source overlays are still checked, and the next request
starts fresh. This avoids stale cross-request proofs. The shared graph verifies
each of its 1,002 procedures once: a universal file-policy proof subsumes later
argument contexts at the same type/constant specialization. Direct calls reuse
the ordinary call-plan cache; resolved runtime callbacks bind caller names and
defaults separately from the selected implementation's parameter effects.

Allocation-analysis stage medians (including requested SDK discovery):

| Workload | Analysis ms | Procedure visits | SDK disk verifications |
| --- | ---: | ---: | ---: |
| Small scalar program | 0.13 | 2 | 0 |
| 1,002-procedure shared graph | 44.50 | 1002 | 0 |
| Aliased SDK math | 18.67 | 3 | 0 |
| Fixed-buffer input + fmt | 145.44 | 4 | 1 |
| basic.print | 199.41 | 2 | 1 |

Procedure counters can include a repeated partial pass while newly demanded SDK
facts are loaded. SDK disk verification still occurs once, rather than once per
call or discovery pass.

## Runtime behavior and source comparison

**All executable statements and generated helpers match the unannotated baseline**
in these workloads. Additional type discovery can leave an unused generated
`base:intrinsics` import. The comparison removes only blank lines and those
specific imports whose generated alias has exactly one occurrence (the import
itself). Intrinsics are a compiler namespace with no runtime initializer; other
imports and all executable code remain in the comparison. Raw hashes and the
exact ignored declaration are retained per sample, so the distinction is visible.

The feature adds no application wrapper, allocator substitution, ABI parameter,
thread, or runtime guard. Independent checked, unchecked optimized, and debug
runs track both ordinary and temporary Odin allocators around the **first** I/O
call: fixed-buffer input plus fmt/basic output produces the expected bytes with
zero tracked allocations. EOF, UTF-8, invalid UTF-8 bytes, full/truncated input,
and repaired editor callbacks are covered. See
[the validation report](../no-alloc-validation-2026-11-20.md).

The source-pinned summaries cover the reviewed SDK's macOS/Linux implementations;
these runtime measurements were made on macOS. Mutable formatter registries,
unknown targets, unreviewed SDK changes, and explicit allocating error paths stay
proof failures. Compiler-generated trap reporting retains its separate exclusion.

Reproduce with a saved pre-I/O release compiler:

```sh
python3 tools/benchmark_no_alloc.py \
  --baseline /path/to/pre-io/thor-rust \
  --include-io --runs 7 --build-runs 3 \
  --output /tmp/no-alloc-io.json
```
