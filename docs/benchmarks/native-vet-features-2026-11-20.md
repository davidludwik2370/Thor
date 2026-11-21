# Native vet expression support: compile-time validation

Measured 2026-11-20 on macOS arm64. Seven alternating warm subprocess samples
per compiler and command, with a shared parse cache and one native compiler
worker. The baseline is the release executable saved before this change.

| Fixture | Command | Before | After | Change |
| --- | --- | ---: | ---: | ---: |
| small | `transpile` | 41.02 ms | 40.68 ms | -0.8% |
| small | `check` | 58.45 ms | 59.41 ms | +1.6% |
| features | `transpile` | 86.52 ms | 85.72 ms | -0.9% |
| features | `check` | 178.09 ms | 177.04 ms | -0.6% |

These samples show no meaningful ordinary compile-time regression. Profiles
confirm that ordinary builds and `no_alloc` alone do not invoke
`backend.native_source_checks`. Generated Odin is byte-identical between the
saved compiler and the updated compiler for both fixtures, without normalization.

The feature fixture imports `fmt` and `reflect` and uses a comprehension and an
f-string. Enabling `-vet-cast` takes **317.89 ms**
(total `check` command median, five warm samples), including the separate native
checking view. That additional validation is opt-in and does not enter the
application's generated code.

[Raw timings, profiles and generated-source hashes](native-vet-features-2026-11-20.json)
contain the full measurement data. These are two representative local fixtures,
not a worst-case bound for all projects.

Runtime fixtures independently compare generated Odin with/without native vet
and run both checked and unchecked optimized builds using explicit verdicts.
The checking executable is never run or linked into the application.
