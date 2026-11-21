# Python compiler retirement

The maintained compiler is Rust. `thor` and `thor-rust` use the native release
executable and never fall back to Python. `thor-python` and `thorlang/` remain
available for historical inspection, with no ongoing feature or bug-fix work.
Python test runners and development tools remain maintained.

## Preserved reference

Commit `f4b0d2c` preserves the pre-retirement toolchain, including the existing
uncommitted compiler, runtime, TLS, test and documentation work. The annotated
tag `python-reference-2026-11-21` identifies this snapshot. The full tag retains
the matching shared frontend as well as the compiler. Source hashes are recorded
in `python-reference-frozen.json`; `thorlang/AGENTS.md` records the freeze policy.

```sh
python3 tools/check_python_reference.py
```

The frozen reference has known false positives and unsupported cases that the
maintained compiler has already corrected. A historical mismatch is evidence to
investigate, rather than a reason to restore incorrect behavior. Normal compiler
diagnostics no longer recommend switching to the frozen implementation.

## Maintained test gate

```sh
cargo build --release --locked --offline --manifest-path rust/Cargo.toml --bin thor-rust --examples -j 2
THOR_TEST_PREBUILT=1 python3 tools/test_compiler.py --output native-test-results.json
```

The gate uses explicit expected values, diagnostics, source ranges, allocation
counts, original-source checks and native Odin oracles. An omitted stdout oracle
means silence; nonempty output must have an explicit expected result. Tests
cannot silently succeed because both compilers reject a program. Native mode
actively forbids execution of `thor-python`, Python project compilation, or
Python source lowering.

Historical implementation/API tests and explicit Python variants are inventoried
in `tests/fixtures/python-reference-tests.json`. They remain available, separately
from the native verdict gate:

```sh
python3 tools/test_compiler.py --list
THOR_TEST_PREBUILT=1 python3 tools/test_compiler.py --reference --match PATTERN
```

Raw unittest discovery still exposes historical implementation tests. Use the
maintained gate for normal development. Pure helper data/decoders and the shared
native-parser bridge may be used by tests; the frozen Python compiler never
supplies a mandatory compiled-program verdict. New regressions belong in native
acceptance tests and should not require updating the frozen implementation.

## Production entry points

`tools/build_runtime.py` prepares native runtime views by invoking Rust. It does
not import Python compiler classes or regenerate Python-specific shared basic
library output. Normal installation, TLS diagnostics, project creation,
compilation, vet, publishing and debugging use the native compiler.

The capabilities protocol identifies Rust as maintained and the Python reference
as frozen. Its conservative `complete: false` field remains: that is not a Python
fallback or a promise to accept every unverified source pattern. Correctness
limits remain explicit and separately documented in the guard audit.

## Validation record

The SDK corpus contains 219 native-valid probes: 218 apply on macOS and all 219
on Linux. Each platform includes nine deliberately allocating controls that must
be rejected. It covers fixed-buffer I/O, formatting, RNG, collections, clocks,
atomics, SIMD, matrices, type queries, descriptor I/O and cryptography. Checked
and unchecked runtime tests verify zero allocations and independent results.
Runtime-dependent growing operations and unknown callbacks remain conservative.
This is an audited corpus, not a proof about every possible SDK invocation. See
[SDK timing](benchmarks/no-alloc-sdk-2026-11-21.md).

The mandatory native inventory contains 4,054 methods; 304 historical tests
are available only with `--reference`. Full initial Mac/Linux runs were followed
by affected-class and cross-feature replays as defects were corrected:

| Coverage of current methods across those runs | Passed | Platform/environment skips | Unresolved |
| --- | ---: | ---: | ---: |
| macOS ARM64 | 4,051 | 3 | 0 |
| Linux ARM64 | 4,004 | 50 | 0 |

These counts describe distinct current methods across the full runs and later
replays, **not one fresh all-green full-suite invocation**. The initial runs
exposed native-only harness assumptions as well as compiler defects. The
affected-class replays covered 1,095 methods on Mac and 1,028 on Linux; the last
185-method release follow-up passed independently on both platforms. The
[machine-readable record](validation/python-retirement-2026-11-21.json) retains
run counts, source/result fingerprints, skip reasons and coverage accounting.

Hardening corrected optional folding of replaceable callback defaults, native
matrix/SIMD and sized-Boolean facts, generated diagnostic cascades, exponential
nested arithmetic emission, and source-dependent caller-metadata scalar cache
reuse. Regression checks cover ordinary/generic calls, runtime fallbacks,
`#run`, caching, read-only storage and live debugger source locations. The
arithmetic fix preserves operand evaluation order and optimized native scalar
instructions. Its former Linux memory-failure repro now passes checked and
unchecked with about 0.5 GiB child-inclusive peak RSS and no new OOM kills.

Both platforms passed 98 Rust unit tests and 1,169 TLS unit tests. Editor checks
passed 149 Node tests and all 71 isolated VS Code integration checks. The TLS
runtime-discovery tests also passed with assertions and bounds/type assertions
disabled. Seven assertion-hygiene tests and eight checked/unchecked demo and
sandbox runs passed. The frozen-source integrity check passes on both hosts.

Representative upstream re-verification covered odecs (125 tests), muninn (39),
toml_parser (7 plus two build entries), anvil tensor/nn (71/13), and Skald (108).
Their usable entries matched the recorded/native baselines on both hosts. Anvil
models/flux still fails its native Odin baseline and is explicitly excluded
from Thor equivalence claims. The broader existing integration matrix was not
freshly rerun in its entirety. The large Skald compile/test entry still needs
roughly 4.3 GiB peak process-tree memory; Linux validation used a 5 GiB test
guard inside the unchanged 6 GiB VM limit. That existing large-project cost is
separate from the corrected exponential arithmetic regression.

The paired timing report checks compile-time cost and emitted-source equality.
The allocation proof adds no runtime wrapper, allocator, ABI parameter or
ownership machinery. Ordinary runtime-dependent growth and unknown callback
paths remain rejected instead of receiving an unsafe exemption.

Windows validation remains deferred by the user. This work does not contact the
Windows computer.
