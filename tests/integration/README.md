# Pinned upstream integration runs

This opt-in suite covers 22 repositories. The manifest records their revisions,
native compiler selections, build and test commands, defines, collection roots,
and vet entry points. The ordinary compiler regression suite runs independently.

```sh
python3 tests/integration/setup_odin_projects.py
python3 tests/integration/run_odin_projects.py --projects 'muninn|odecs'
python3 tests/integration/run_odin_projects.py --stage baseline --fresh
python3 tests/integration/run_odin_projects.py --stage migrated
python3 tests/integration/run_odin_projects.py --stage vetted
python3 tests/integration/run_odin_projects.py --stage verify
```

Sources, copied projects, corrected projects, artifacts, and detailed JSON/log
results live under `integration-projects`. Each repository has a process lock.
The `verify` stage repeats the translated commands against existing vetted copies
and retains their vet validation records.
Compiler commands share two execution slots across runner instances. The runner monitors
the combined memory of each command and its descendants, recording its peak
and stopping it above 3 GiB. On macOS this includes compressed memory through
the kernel's physical-footprint metric. `THOR_INTEGRATION_MAX_RSS_MB` configures that
limit for a different host. The SDK migration and larger vet configurations
have explicit 8–12 GiB limits in the manifest. `THOR_INTEGRATION_JOBS` configures
the slot count.
Memory-limit stops retain their logs and status.
The runner preserves existing working-tree edits and verifies the pinned HEAD.
Use separate working directories when changing a revision or preparation patch.

## Comparison

The baseline uses Odin directly. Migration invokes Thor's project copier and
command translator, then runs the translated commands. Vet runs with `--apply` and each entry point's compile configuration, followed
by the same translated commands against the corrected copy. Test entry points
use test build mode to validate declarations without executing tests during
vet's edit selection.

Each successful baseline must retain its exit status and test count. Where the
native runner supports JSON reports, comparison also includes every package,
test name, and success verdict. Selected interpreter commands additionally
require identical stdout. Explicitly marked native test failures are compared
case by case, retaining their original failure verdicts. Crashes and unavailable
baseline configurations remain recorded as unavailable.

The large Odin repository and examples matrix use one initial command to
validate the copied project, followed by individually translated commands for
the full matrix. This preserves per-command failure evidence.

Baseline, migrated and vetted builds use the same explicit compiler flags.
The intervening Thor style pass permits warnings while applying supported edits.
Its recipes omit `-warnings-as-errors`, allowing unused-parameter findings to be
reported. The subsequent build retains the original command's warning policy.
Native compilation uses one
compiler worker to avoid the separately documented upstream compiler races.
Odin's test runner uses one worker and a fixed random seed. Timing includes the
test execution and any upstream subprocesses.

## Native toolchains and preparation

The manifest targets macOS ARM64 for this run. The default compiler comes from
PATH. Some projects use official Odin monthly releases under
`integration-projects/toolchains`. Download those releases from the Odin GitHub
release page and retain their original distribution directory names. The
manifest currently references dev-2026-08 and dev-2026-10. The cloned current
Odin revision is also built for projects using its newer standard library.

Thor's parser builder defaults to the installed Odin compiler. Set
`THOR_FRONTEND_ODIN` to a current compiler when selecting an older backend.
The selected backend continues to supply the SDK and final compilation.

The native current Odin build used Homebrew LLVM 22 with the following command
from the pinned Odin clone.

```sh
LLVM_CONFIG=/opt/homebrew/opt/llvm/bin/llvm-config \
CXX=/opt/homebrew/opt/llvm/bin/clang++ ./build_odin.sh release
```

Additional upstream dependencies used during this run follow.

- Runic uses Homebrew `libyaml` and `llvm@19`, matching its pinned libclang
  bindings. The manifest supplies the LLVM 19 linker search path.
- Odin's vendor tests require Git LFS assets for OpenEXRCore and kb_text_shape.
  The stb libraries were rebuilt with the upstream stb build script and copied
  into its Darwin library directory for the current linker.
- Runa's fonts come from its `tools/ci/fetch_test_fonts.sh`. Its full shaping
  suite has font-version-sensitive expectations. The manifest also includes
  its version-robust CI shaping selections.
- odin-godot requires its pinned submodules and generated templates from
  `make templates`. The standalone names test package is covered. The complete
  bindgen command has a native macOS platform gap.
- Speedrun uses its pinned submodules and upstream `build.sh` with
  `SPEEDRUN_NO_ANDROID=1 SPEEDRUN_NO_IOS=1 SPEEDRUN_VERSION=integration`.
  Its browser journeys run against its local fixture server on port 8781.
- TOML conformance uses upstream `toml-test` v2.2.0 and exactly the skip list
  in the parser's `run-tests.bash`. The decoder receives 583 passing cases
  across the three stages, with 98 upstream skips.

Preparation patches are kept in `patches` and applied idempotently before the
native baseline. They enable Anvil's existing `using` statements, make Aseprite
fixture paths relative, and update LISP's renamed OS import and stream adapter.
These patches preserve test assertions and expected fixture contents.

## Runtime compiler calls and source fixtures

Some projects exercise Odin tooling at runtime. Their fixture dependencies
remain explicit in the manifest.

- The SDK's OS file-clone, POSIX filename-matching and native structure-layout
  tests explicitly consume their original Odin source fixtures. Per-build overlays
  replace `#file`/`#directory` in those four test files with the original fixture
  locations. Comments, string literals and test assertions retain their contents.
- OLS uses the original builtin documentation directory through
  `OLS_BUILTIN_FOLDER`.
- Runic's code-generator test reads the original Odin fixtures and compares
  their original C golden files. The compiled test runner uses converted code.
- karl2d's API verifier reads the original Odin documentation source and invokes
  its original documentation builder from that fixture working directory.
- Kvist uses the original shipped Kvist package root. Its test programs also
  invoke hard-coded `odin build` commands against their own source directory.
  For converted stages, the runner supplies `odin_runtime_proxy.py` on PATH.
  The proxy sends compilation commands to Thor with the selected Odin backend.
  Native introspection commands continue to reach Odin. This adapter applies
  only to explicitly marked integration projects.

Arbitrary runtime command strings retain their source spelling during project
migration. These adapters preserve the test programs' fixture contracts.

```sh
python3 tests/integration/run_extra_checks.py toml
python3 tests/integration/run_extra_checks.py browsers
```

Vetting uses migration compatibility mode and each command's target and define
configuration. Example programs use their check/build configuration. Test
packages use test build mode. Known native AppKit/scheduler crashes are retained
as baseline evidence and skipped by subsequent runtime runs, with compile-only
coverage recorded separately.

The shared RTTI consumers can also be checked against a second native SDK.

```sh
THOR_TEST_SECOND_ODIN=/path/to/other/odin \
  python3 -m unittest tests.test_runtime_sdk_compatibility
```
