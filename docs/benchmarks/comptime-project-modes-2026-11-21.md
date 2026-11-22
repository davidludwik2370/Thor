# Compile-time modes on poker_pmap and hilo_test

`aggressive` and `override` do not precompute any additional calls in these two projects. All four modes produce byte-for-byte identical executables for each project. `required` substantially reduces poker’s cold build time by avoiding optional evaluation in test-only files.

## Build and runtime measurements

All times are seconds. Builds use the original source contents. Runtime uses the reduced, seeded workloads described below. Values are medians of three cold builds, five warm builds, and eight runtime runs per mode.

| Project | Mode | Cold build | Warm build | Runtime |
| --- | --- | ---: | ---: | ---: |
| poker_pmap | `required` | 1.811 | 1.703 | 0.877 |
| poker_pmap | `normal` | 7.315 | 1.807 | 0.876 |
| poker_pmap | `aggressive` | 7.464 | 1.994 | 0.905 |
| poker_pmap | `override` | 7.455 | 1.983 | 0.882 |
| hilo_test | `required` | 1.844 | 1.805 | 0.562 |
| hilo_test | `normal` | 1.807 | 1.794 | 0.555 |
| hilo_test | `aggressive` | 1.807 | 1.797 | 0.564 |
| hilo_test | `override` | 1.821 | 1.798 | 0.570 |

The runtime spread is not evidence of a mode-dependent code-generation slowdown: the entire executable hashes match across all four modes, including for the untouched build inputs. The seeded runtime executables also match byte for byte and produce exactly the same complete output. OS scheduling, CPU frequency, startup and ordinary measurement variation can still change elapsed times when running the same binary.

## Work performed during compilation

“Folded sites” counts source calls replaced with results. Eligibility proofs are compiler checks of ordinary procedures, not executions of those procedures. Profiles are separate, representative invocations; phase times are not additional timing samples.

| Project | Mode | Folded sites | Cold helper compilations | Warm helper compilations | Extra ordinary-call proofs | Warm evaluation/scheduling (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| poker_pmap | `required` | 0 | 0 | 0 | 0 | 61.2 |
| poker_pmap | `normal` | 6 | 3 | 0 | 0 | 134.0 |
| poker_pmap | `aggressive` | 6 | 3 | 0 | 45 | 311.7 |
| poker_pmap | `override` | 6 | 3 | 0 | 45 | 297.7 |
| hilo_test | `required` | 0 | 0 | 0 | 0 | 14.7 |
| hilo_test | `normal` | 0 | 0 | 0 | 0 | 3.8 |
| hilo_test | `aggressive` | 0 | 0 | 0 | 5 | 9.0 |
| hilo_test | `override` | 0 | 0 | 0 | 5 | 8.5 |

Poker’s six folded calls all construct the same fixed `[52]Card` deck with [`test_full_deck()`](/Users/davidludwik/Code/Thor/poker_pmap/poker_test.thor:687). They occur twice each in `judge_test.thor`, `simulation_test.thor`, and `poker_test.thor`; the JSON records all six original source lines. On an empty project cache this requires three helper compiler invocations. In the normal cold profile, helper compilation alone takes about 4.8 seconds; the whole evaluation/scheduling phase takes about 5.7 seconds. Warm normal builds reuse six request-cache hits and compile no helpers.

These optional evaluations happen while Thor processes the test files, even during an ordinary build. Odin then excludes the generated `_test.odin` files from that executable. That explains why paying this cold-build cost does not improve the production program at all. Skipping optional evaluation in test-only files during a regular build is a concrete follow-up optimization opportunity; no compiler changes were made for this benchmark.

For poker, aggressive/override add 45 ordinary-call eligibility proofs but no additional folded calls. The representative warm scheduling phase rises from 134 ms with normal to 312/298 ms, and median warm builds rise from 1.807 s to 1.994/1.983 s. For HiLo they add five proofs; representative warm scheduling rises from 3.8 ms to 9.0/8.5 ms, with essentially unchanged complete build times.

The hot simulation inputs and ambient RNG state are runtime values. The purity and known-input rules prevent precomputing those Monte Carlo loops. Neither application has relevant `#runtime` barriers to override, so override does the same work as aggressive here.

## Method and reproducibility

- Machine: Apple M1 Pro, 10 logical CPUs; `macOS-15.6-arm64-arm-64bit`.
- Native toolchain: `odin version dev-2026-10:8169255b9`. Thor compiler source commit: `4a6c97e`; exact compiler executable hash is in the JSON.
- Flags for every build: `-o:speed -disable-assert -no-bounds-check -no-type-assert`. The native compiler keeps its normal parallel settings.
- Original source hashes were checked before and after the experiment. The user projects were not edited. Builds use private source copies, including the poker test files.
- `poker_pmap` is built as a package. `hilo_test/hilo_test.thor` is built as an explicit file because an Odin-style package build excludes a filename ending in `_test`.
- Cold means a fresh project cache for each sample, with existing shared SDK/parser/runtime and system caches retained. It does not mean a rebooted/cold machine. Warm means Thor caches are populated; Odin still performs a full compilation and link.
- One build or benchmark process runs at a time. Mode order rotates across rounds. Timed build commands use cache counters but no `THOR_PROFILE`; detailed profiles are collected separately.
- Backend output paths are identical across modes. Otherwise directory-name lengths can move diagnostic strings and change address relocations despite identical instructions.
- Runtime measures the complete executable after an untimed warmup. Original `poker_pmap` currently uses explicit Odin threads; `hilo_test` uses `basic.pmap`. Both retain their existing worker-count logic.
- Runtime trial counts were calibrated using a pilot run, then held fixed across modes. The target duration is approximate; measured durations are shown above.
- Poker runtime workload: **330,000 trials for each player count 2–10**, or **2,970,000 deals total** per run, instead of 1,000,000 per player count.
- HiLo runtime workload: **4,270,000 trials per run**, instead of 1,000,000,000.

For reproducibility, the private poker copy resets the RNG once on entry to `run_trials` with `u64(0x243f6a8885a308d3) + u64(player_count)*97 + u64(trials)`. The private HiLo copy resets once on entry to `simulate_batch` with `u64(0x243f6a8885a308d3) + u64(trials)`. Equal-sized workers consequently use identical streams. This is a controlled performance workload, not an independent-stream probability experiment. The simulation algorithms, callback signatures and threading implementations are otherwise unchanged. Runtime patches, binaries, lowered-source differences and reference outputs are retained under the artifact directory named in the JSON.

One native Odin build failed with SIGTRAP and `src/array.cpp` bounds assertions during a warm normal HiLo build. The failed invocation is recorded separately in `native_failures` and excluded from successful-build timing distributions; its elapsed time was not retained by the initial runner. The benchmark resumed the missing samples with unchanged inputs and flags. This establishes an intermittent native backend failure, not its exact root cause; the assertion alone does not establish that it is the same race as the previously investigated type-name cache issue. Runtime runs had no failures or output mismatches.

To repeat with new empty artifacts (the pilot may select different runtime trial counts):

```sh
python3 tools/benchmark_comptime_modes.py \
  --artifacts /tmp/thor-comptime-modes-new \
  --report /tmp/thor-comptime-modes-new.json
```

The runner can resume a matching saved report with `--resume`. It records recognized native backend assertions separately, retries with unchanged flags, and preserves an empty cache for cold retries. It never retries source errors, application failures or output mismatches.

[Raw samples, profiles, hashes and call sites](comptime-project-modes-2026-11-21.json) · [Benchmark runner](../../tools/benchmark_comptime_modes.py)
