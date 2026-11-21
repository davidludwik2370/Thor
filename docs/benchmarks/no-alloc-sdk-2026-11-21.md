# SDK allocation-proof audit timing, 2026-11-21

Nine paired warm transpiles and three paired optimized builds per variant on macOS ARM64, using the final release executable. Other compiler/test workers were idle. Original samples, profiles, binary hashes and emitted-source hashes are retained in the [companion JSON](no-alloc-sdk-2026-11-21.json).

| Workload | Before, no contract | After, no contract | After, `no_alloc` |
| --- | ---: | ---: | ---: |
| small | 34.6 ms | 35.0 ms | 33.6 ms |
| shared_call_graph | 138.9 ms | 140.9 ms | 191.3 ms |
| sdk_reader | 38.6 ms | 36.3 ms | 68.5 ms |
| fmt_io | 45.9 ms | 38.0 ms | 263.2 ms |
| basic_io | 142.1 ms | 138.1 ms | 395.9 ms |
| default_rng | 38.0 ms | 38.6 ms | 114.4 ms |
| seeded_rng | 38.3 ms | 38.5 ms | 123.4 ms |
| custom_rng | 36.4 ms | 37.0 ms | 70.1 ms |

All eight main native source files match across all variants after normalization of blank lines and proven-unused compiler-intrinsics imports. Full, unnormalized digests are retained too. The allocation proof introduces no runtime wrapper, allocation, ABI parameter or ownership machinery. Separate regression tests compare optimized generic arithmetic instructions with handwritten native operations and verify checked/unchecked zero-allocation paths.

The 1,000-procedure proof graph took 185.0 ms before and 191.3 ms after with the contract enabled. Fixed-buffer fmt/basic I/O proofs took 263.2/395.9 ms, compared with 260.9/389.1 ms before. Growing dynamic-array operations remain rejected.

Three build samples are a limited timing check, not a strong estimate of small compiler/linker differences. Main native code remains identical. These eight cases are not a universal compile-time bound: the large Skald integration entry still peaks around 4.3 GiB and its cold compile-time helper work costs substantially more. Full hardening and platform coverage are recorded in [Python retirement](../python-retirement.md).
