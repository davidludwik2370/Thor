# Scoped owner validation

`~` adds no heap allocation for registration. The runtime acceptance cases exercise checked and unchecked release builds, and tracking allocators verify shallow cleanup, final resized buffers, allocator identity, argument evaluation order, and reverse cleanup order.

Validation completed:

- 212 tests in the broad regression gate before the final allocator-branch refinements.
- 35 scoped-owner tests on the final optimized compiler, including those refinements and all four compile-time modes.
- 4 final live TLS/debugger tests; scoped TLS tests also passed with an unchecked server.
- 98 Rust library tests and an all-target Cargo check.
- 1,169 native TLS tests in each of checked and unchecked configurations.
- 152 editor tests and 7 assertion-hygiene tests.
- The Python reference remains frozen and unchanged.

## Ordinary-project transpile cost

Compared the pre-feature compiler at `5234d28` with the final optimized native compiler. Both used the current shared parser. Five timed warm samples followed two warmups, alternating compiler order. Commands used `transpile`, `--comptime=required`, and `-thread-count:1`, with separate output/cache directories. These are transpile CLI wall times, excluding the final Odin backend build. They are not a cold-parser benchmark.

| Project | Before | After | Normalized emitted Odin |
| --- | ---: | ---: | --- |
| hilo_test | 0.186 s | 0.182 s | Identical |
| poker_pmap | 0.467 s | 0.462 s | Identical |

The small timing differences are within ordinary measurement variation. Profiles confirm that scoped-owner analysis did not run for these projects. Identical emitted Odin also preserves their generated runtime code. Output-directory paths were normalized before hashing.

[Raw samples and hashes](scoped-owners-2026-11-22.json).
