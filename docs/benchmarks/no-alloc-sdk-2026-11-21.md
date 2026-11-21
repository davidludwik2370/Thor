# SDK allocation-proof audit timing, 2026-11-21

Nine paired warm transpiles and three paired optimized builds per variant, on macOS ARM64. Other compiler/test workers were idle during this run. Original samples, profiles, binary hashes and emitted-source hashes are retained in the companion JSON.

| Workload | Before, no contract | After, no contract | After, `no_alloc` |
| --- | ---: | ---: | ---: |
| small | 40.5 ms | 40.1 ms | 39.5 ms |
| shared_call_graph | 147.7 ms | 145.4 ms | 193.2 ms |
| sdk_reader | 39.4 ms | 38.0 ms | 71.0 ms |
| fmt_io | 40.7 ms | 41.1 ms | 249.6 ms |
| basic_io | 153.0 ms | 148.4 ms | 402.4 ms |
| default_rng | 42.0 ms | 42.3 ms | 114.2 ms |
| seeded_rng | 42.8 ms | 40.7 ms | 119.5 ms |
| custom_rng | 42.3 ms | 42.9 ms | 81.5 ms |

All eight workloads emitted identical native code across all variants after the documented normalization of blank lines and proven-unused compiler-intrinsics imports. Full, unnormalized digests are retained too. No runtime instructions, allocations, ABI changes or ownership machinery were introduced by the proof analysis.

The 1,000-procedure proof graph took 193.2 ms both before and after with the contract enabled. Fixed-buffer fmt/basic I/O proof times were 249.6/402.4 ms, versus 248.5/404.2 ms before. Default/seeded/custom RNG proof cases took 114.2/119.5/81.5 ms. Growing dynamic-array operations remain rejected.

Three build samples are a limited timing check, not a strong estimate of small compiler/linker differences. For example the unannotated shared-call-graph build median changed from 403.9 to 426.7 ms while its transpile median improved; native code was identical. No timing result here is a universal bound for arbitrary projects.
