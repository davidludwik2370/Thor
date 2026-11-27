# Source review and regression validation — 2026-11-27

Reviewed the pending pure callback types, implicit final expressions in `fn`,
editor integration, retained-profile initialization, Omarchy prerequisites, and
their documentation before pushing to GitHub. Validation below ran locally on
macOS 15.6 build `24G84`, ARM64, using the release Rust compiler and rebuilt TLS.

## Compiler and language

| Check | Result |
| --- | --- |
| Rust compiler unit tests | 136 passed |
| Native TLS unit tests | 1,178 passed |
| Feature and compatibility regressions | 603 passed, no skips |
| Additional pure-function and ownership regressions | 222 passed, no skips |

The 603-test selection covers pure callbacks, implicit returns, defaults, caller
metadata, parameter contracts, pmap, native and automatic compile-time execution,
all compile-time modes, no_alloc, struct arithmetic, and existing pure-result
ownership. It includes the new live TLS tests. Historical frozen-Python
implementation comparisons remain excluded by the maintained runner's policy;
they are not substitutes for native acceptance tests.

The additional 222 tests cover existing pure conversions, automatic casts, call
contexts, dynamic results, local mutation, helper/local ownership, native
evaluation, intrinsics, projections, and records. Together the two native
regression selections passed 825 tests; 53 frozen-reference comparisons were
excluded by policy. This was a targeted compatibility review, not a rerun of
every compiler test on every operating system.

Changed frontend and runtime paths are exercised in checked builds and with
`-o:speed -disable-assert -no-bounds-check -no-type-assert`. Their verdicts use
explicit output comparisons or conditional failures that remain active in those
builds. The callback ABI test compares optimized LLVM IR against equivalent
ordinary procedure callbacks, including runtime dispatch and known-callback
specialization. Recursive cleanup tests include callbacks alongside owned data.

## Installer and desktop integration

| Check | Result |
| --- | --- |
| Installer Rust unit tests | 16 passed |
| Installer, prerequisites, assertion hygiene | 101 passed, no skips |

The installer cases cover repeated uninstall/reinstall, retained profile
registrations, editor-created settings, JSONC comments, explicit themes/colors,
and preservation of user changes. Omarchy cases cover package availability,
existing installations, package-manager failure, minimum editor versions, and
toolchain-only installation.

The separate [fresh macOS and Omarchy report](macos-omarchy-installer-2026-11-27.md)
records actual desktop installation, hover/completion, Run, source debugging, and
uninstall/reinstall checks. The Windows reports retain their original evidence
and explicitly distinguish the published retained-profile defect from the
candidate fix.

## Reproduction and scope

```sh
python3 tools/build_compiler.py
python3 tools/build_tls.py
cargo test --locked --offline --manifest-path rust/Cargo.toml -j 2
cargo test --locked --offline --manifest-path install/Cargo.toml -j 2
python3 tools/build_tls.py test -thread-count:2
THOR_TEST_PREBUILT=1 python3 tools/test_compiler.py \
  --match 'tests.test_(pure_callbacks|implicit_returns|defaults|caller_metadata|parameter_contracts|pure_calls|pure_io|native_run|no_alloc|comptime|fn|pmap|struct_arithmetic|t18_t20|t21_t22|t23)'
THOR_TEST_PREBUILT=1 python3 tools/test_compiler.py \
  --match 'tests.test_(pure_(auto_casts|call_context|conversions|conversion_tls|dynamic_results|mutation)|rust_(borrow_model|helper_ownership|local_ownership|native_evaluation|pure))'
python3 -m unittest tests.test_installer tests.test_installer_prerequisites tests.test_assertion_hygiene
```

`git diff --check`, JavaScript syntax checking, POSIX shell syntax checking, and
Python/JSON parsing also passed. Raw logs and machine-readable compiler results
are retained locally under `dist/prepush-review-2026-11-27`; generated binaries,
caches, and VM disks are excluded from Git. The maintained compiler runner prunes
obsolete development output before and after each suite.

This is a source push, not a binary release. Published Thor `0.41.12` remains
unchanged: its retained-profile defaults defect still needs a new installer
binary, and the language changes require a current source build or a future
release. The Omarchy prerequisite change belongs to the repository bootstrap.
