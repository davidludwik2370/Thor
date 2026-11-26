# Terminal PATH, hover labels and native platform validation

## Installer PATH

`thor-install --path` configures both login and interactive startup files. Bash
uses its first existing login file and `.bashrc`; zsh uses `.zprofile` and
`.zshrc`, respecting an exported `ZDOTDIR`. A marked, deduplicating shell block
preserves existing configuration. The installer preflights both destinations,
backs up original content, and journals its edits for uninstall.

The updated native installer was run in the Ubuntu-thor Fusion VM. Fresh login
and non-login bash shells both found the installed Thor command. Native macOS
zsh was also checked in both modes on the MacBook Air. These checks exercised
the installer rather than manually editing PATH files. Process-level regressions
cover shell selection, custom startup directories, idempotence, conflicts,
legacy-block upgrades and uninstall restoration. Ubuntu login banners are kept
separate from the command/PATH verdicts.

## Hover labels

The Thor TextMate grammar now treats qualified hover-display prefixes as
namespaces. Names such as `or-else-lists.default_text` no longer color the
embedded `else` as a keyword. The rule is restricted to qualified declaration
signatures; ordinary operators, case expressions and string contents retain
their existing grammar. Thor extension 0.23.7 includes this correction.

## Native Windows fixes

The full suite runs under the PC's ordinary, non-elevated account. It exposed
filesystem assumptions that earlier checks against prepared installation caches
had missed. On NTFS, generated views now use directory junctions and file hard
links when symbolic links require unavailable privileges. Read-only and
cross-volume file aliases use independent copies. Generated source writes use
atomic replacement and preserve their authoritative originals. Runtime-view
identities include file identity and content so replaced or edited support
files cannot leave stale copied views.

Root metadata such as `runtime/README.md` stays linked to its actual source;
it is not redirected into a package-only runtime view. Installer cleanup accepts
verified support-file copies and keeps modified or unknown data.

Cancelling a Windows TLS checker now terminates that process handle directly.
Odin's graceful-termination helper can broadcast Ctrl+C to the shared console,
which interrupted the test runner. A regression runs cancellation and shutdown
checks in a separate real console and verifies that its caller receives no
interrupt. POSIX graceful cancellation remains covered separately.

Windows support-file identity/content reads now retry temporary native sharing
violations. A trace located the editor/CLI overlap failure in that fingerprint
read; a regression holds an exclusive native file handle during both ordinary
and policy-prefixed runtime snapshots. The identity and contents are captured in
one attempt, with no additional read on the successful path. Permanent failures
remain errors. The same bounded I/O helper covers native publication/startup
locks and avoids repeating successful or unrelated failed operations.

Cold frontend builds now preserve the first completed, content-addressed helper
instead of replacing an executable another compiler has begun using. A verified
unchanged Windows SDK alias is reused, preserving active readers and avoiding
unnecessary filesystem writes. A freshly linked helper can also wait briefly
for a temporary writer before its first execution. Failed process-creation
attempts have not started a child. Atomic
output publication closes its writer before replacement. On Windows it retries
Windows reader-lock responses, with a bounded wait; persistent failures
preserve the previous file and report its path. Real native handle tests cover
both transient and persistent reader locks.

Migrated directory aliases preserve their relative targets and forward references
without requiring administrator privileges. Development cleanup/reset tools also
recognize junctions and do not traverse them into unrelated directories. Reset
coverage includes all six Bash/zsh startup files managed by the installer.

These changes affect build filesystem operations and editor processes. They
introduce no instrumentation or other runtime work into compiled programs.

## Test fixtures and prerequisites

Cache regressions now inspect the bounded native store rather than obsolete
per-project JSON locations. Tests explicitly isolate their caches and verify
nonempty packets, independent reconstructed storage, corruption recovery and
safe cleanup of both current and legacy records. File-symlink-specific checks
report the account's missing capability separately from ordinary cleanup checks;
directory-alias checks use unprivileged junctions on Windows.

The Windows DLL relocation fixture builds a plain Odin C-ABI export, so it
exercises native DLL packaging without assuming an externally configured MSVC
shell. Release-bundle checks explicitly run in their writable project directory;
the scheduled task's protected Windows system directory is not a build directory.

Publishing tests reuse the release protocol executable, including its Windows
`.exe` suffix, instead of building an additional debug dependency tree. Linux
sanitizer tests require Clang's matching compiler-rt development package; icon
fixtures require Pillow. These are development test dependencies, not Thor
installation or runtime dependencies.

## Results

[Machine-readable results](path-and-platform-suite-2026-11-26-results.json)
record the baseline/corrective counts, final native debugger failures, release
digests, and GitHub installer PATH checks.

The maintained native-only harness ran the complete suite on both machines.
Historical Python compiler implementation checks remain explicitly archived.
These are full baseline runs followed by targeted corrective runs; the baseline
records retain their original failures.

| Full baseline | Tests run | Time | Reported failures | Errors | Skips |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native Windows x64 | 5,004 | 74.2 min | 19 | 30 | 184 |
| Ubuntu 24.04 x64 in WSL2 | 5,001 | 62.4 min | 22 | 3 | 63 |

Linux failures other than the two native debugger limitations below were
corrected and rechecked with a 376-test run (23 platform skips), a 39-test
portability run (2 skips), and migration checks. All those corrective runs
passed. A final 101-test platform run also passed (1 skip), followed by 10
integration-storage tests (2 Windows-only skips). Comparing the final test
inventory against the baseline and follow-up selections left no uncovered
maintained test IDs on Linux.

The final Windows corrective run covered 683 tests in 686 seconds: 9 native
debugger failures, no errors, and 74 platform/capability skips. There were no
unexpected failures. The subsequent 206-test editor run passed. All 5,014
maintained test IDs in the final Windows and Linux inventories are covered by
their baseline or corrective selections; renamed fixtures are counted under
their final names.

Both platforms also ran native Rust and installer tests, 1,178 TLS tests in each
of checked and unchecked configurations, and the editor's Node tests. The final
editor regression suite contains 206 tests. Assertion hygiene passed all 7 tests.
The parallel example has explicit output comparisons in checked, optimized
unchecked, and debug/optimized unchecked release-bundle builds.

The [0.41.10 release](https://github.com/davidludwik2370/Thor/releases/tag/v0.41.10)
was built from `5895ca1a1ef7e27bba58d8a1d338edf9da957979`.
Final native compiler unit runs passed 141 tests on Windows, 135 on Linux and
136 on macOS. The final Windows platform run passed all 101 tests with 3
capability/platform skips. These runs include the exclusive-file sharing
regressions, directory alias resources, rename, runtime-view snapshots, native
DLL relocation, and cache corruption/recovery checks.

Fresh isolated installations of the new native bundle passed `--path`, both
login/interactive shell discovery, `--doctor`, and uninstall restoration in
Ubuntu and on the macOS 13.5.1 MacBook Air. The refreshed Apple Silicon bundle
uses the tested static LLVM 18 SDK and has a macOS 13.0 floor; all 12 macOS
Mach-O binaries/libraries were checked, including the private symbol tools and
sanitizer runtimes.

After publishing, a separate check on both machines cloned the public GitHub
repository and ran **`./thor-install --path`** with no local bundle override or
manual PATH edits. It downloaded the published 0.41.10 release. Fresh login and
interactive shells each found Thor exactly once and passed `thor-install
--doctor`. Uninstall restored the original startup files; a second uninstall
also succeeded. These were clean test homes on provisioned operating systems.
The installer cannot change the environment of its parent terminal; users
should open a new terminal after installation.

Large temporary validation checkouts, build products and private test toolchains
were removed after saving the results. The ordinary Windows/Linux VS Code
workspaces, installations and VM were preserved.

### Native debugger limitations

Strict debugger tests remain enabled and retain their failing expectations.
The eight previously documented Windows limitations still reproduce in matching
plain Odin controls; see the [paired comparison report](../windows-debugger-pairs-2026-11-23.md).

One additional Windows check could not display caller metadata and slice
arguments at the first procedure statement. Direct plain Odin, plain Odin
through an identity bridge, and Thor all reported the same unavailable watches;
all three returned the correct result of 10. The
[standalone matching sources](../../tests/support/debugger_metadata/README.md)
preserve the original line positions and native metadata defaults.

Ubuntu has two independently reproduced native limitations: no verified
breakpoint on the deferred return line, and stepping into a slice argument
annotated `#mut` remaining at the caller. Direct plain Odin, identity-bridge
Odin, and Thor behave alike. These are debugger/debug-info limits in the tested
Odin/CodeLLDB configuration, not failed program result checks.

