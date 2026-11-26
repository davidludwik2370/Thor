# Installer compatibility validation — November 26, 2026

This round validates the one-command toolchain/editor setup, prerequisite
acquisition, ordinary terminal PATH, upgrades and uninstall. It supplements the
[full language-suite and platform results](path-and-platform-suite-2026-11-26.md);
it does not replace them or resolve their documented upstream debugger limits.

## Corrections

- Portable Linux bundles now target updated Ubuntu 22.04: glibc 2.35,
  GLIBCXX 3.4.30 and CXXABI 1.3.13. Release inspection reads the actual ELF
  requirements, rejects unknown requirements and checks every native host binary
  and shared library, while distinguishing cross-target SDK inputs.
- Portable SDK compilers use generic CPU targets. An initial ARM64 candidate
  built with `release-native` used instructions unavailable on an older host.
  The replacement ran successfully on that host, in addition to the CI hosts.
- Native Raylib and Raygui libraries are rebuilt against the release baseline;
  checked and unchecked static/shared FFI smoke programs verify them. Private,
  checksum-pinned CMake is acquired when the system version is too old.
- Portable Mac TLS explicitly declares macOS 13.0. Environment deployment settings
  alone had left TLS requiring macOS 15; the release gate caught this. Every
  native Mach-O file in the replacement ARM64 bundle has a floor at or below 13.
- Clean source setup acquires a compatible LLVM binary package, private Git LFS
  and reviewed binary build tools without a large package-manager source build.
  Packaging rejects unresolved LFS placeholders. Git configuration stays local
  to the temporary SDK checkout.
- Native Linux VS Code commands under WSL acknowledge the deliberately selected
  Linux editor, avoiding the launcher's interactive Windows-editor suggestion.
  This environment setting is scoped to installer child processes.
- Fedora 45 needs its maintained OpenSSL 3 compatibility runtime for VS Code's
  extension signature verifier. Setup installs the missing `libssl.so.3` provider,
  including when reusing an existing or explicitly selected editor. OpenSSL 4
  and signature verification remain enabled. Toolchain-only setup skips this.
- Installer-only refreshes reuse independently verified compiler/TLS/SDK inputs.
  Linux repackaging resolves dependencies from the original SDK executable, so
  its relative library search paths still resolve. Draft-only publication guards
  prevent replacing published assets.

## Tests and preservation checks

The local installer/source/prerequisite/release regression selection passed
168 tests. A subsequent 12-test desktop-cleanup/assertion-hygiene selection
passed, including two added process-ownership regressions. Native installer
regressions also run on Linux x64/ARM64, macOS Intel/ARM64 and Windows x64.
[Five-host CI](https://github.com/davidludwik2370/Thor/actions/runs/45832248302)
passed with the final process-ownership cleanup regressions.

The real desktop harness installs and reinstalls into temporary state, runs
Doctor, tests checked and unchecked execution, verifies fresh ordinary-shell
PATH, loads the installed extensions in actual VS Code, checks OLS and TLS hover
and completion, tests optional colors, Run, an original Thor source breakpoint
and clean debugger exit, then uninstalls twice. Default profile settings and
keybindings stay byte-identical; project sources survive uninstall. On Mac the
real HOME is used for native editor/Keychain services; the temporary installation
discovery record is backed up and restored, including on test failure. Cleanup
closes only owned test editor/debugger process trees.

Unchecked programs use `-o:speed -disable-assert -no-bounds-check
-no-type-assert`; verdicts use external comparisons or explicit failure checks.
No required setup, cleanup or runtime operation is hidden inside an assertion.

| Existing desktop | Result | Cycle time |
| --- | --- | ---: |
| macOS 15.6 ARM64, primary Mac | All desktop checks passed | 98.04 s |
| macOS 13.5.1 ARM64, M1 MacBook Air | All public-install desktop checks passed | 123.64 s |
| Ubuntu 24.04 ARM64, Fusion | All desktop checks passed | 105.82 s |
| Windows 11 x64, build 26200 | All desktop checks passed | 146.11 s |
| Ubuntu 24.04 x64, WSL2 | All desktop checks passed | 109.65 s |

Windows and WSL desktop checks finished before the user's fullscreen restriction.
Further physical-PC GUI runs are deferred until the user lifts that restriction.
These are isolated checks on provisioned desktops, not clean OS installations.

The Air’s repeat debugger check exposed a test-orchestration issue: direct SSH
children ran in the `Background` launchd/audit session. macOS rejected debugging
as non-interactive even though VS Code could open windows. Starting the same
validator through a temporary LaunchAgent in the logged-in user’s `gui/<uid>`
domain produced an `Aqua` session and passed the source breakpoint and clean
exit checks. This required no Thor code or OS security-policy change. The
public-release cycle also verified record restoration, default-profile
preservation and repeated uninstall. The temporary job was removed afterward.

A separate real upgrade installed 0.41.10, verified it, installed 0.41.11 into the
same prefix and verified it, then uninstalled twice. The project survived and
managed PATH blocks were removed. That complete isolated cycle took 54 seconds.

## Fresh operating systems and public releases

The compatibility workflow uses fresh Ubuntu 22.04, Ubuntu 24.04, Debian 12 and
Fedora containers on both x64 and ARM64. They initially lack VS Code and the
compiler's C toolchain. Setup acquires missing prerequisites/editor packages;
real editor and debugger tests run under Xvfb. Default settings/keybindings,
ordinary terminal PATH, repeated install/uninstall and source preservation are
checked. Candidate runs additionally exercise a Unicode prefix with spaces.
Rootless test execution uses a sandbox override only inside the disposable
container's test launcher; installed user settings and signature checks are not
weakened.

All eight [final candidate cases](https://github.com/davidludwik2370/Thor/actions/runs/45829839121)
passed, including Fedora on both architectures. After publication, all eight
[public-install cases](https://github.com/davidludwik2370/Thor/actions/runs/45831669854)
also passed the exact GitHub clone/bootstrap path with no local bundle override.
Each installed Doctor log was checked for version 0.41.11, and each native
editor/debugger result was independently checked for success. Candidate/public
matrix results and archive digests are recorded in the
[companion results](installer-compatibility-2026-11-26-results.json).

A fresh public GitHub clone on macOS installed the published 0.41.11 release
without a bundle override. Fresh login and interactive shells passed PATH and
Doctor checks; repeated uninstall restored PATH and preserved the checkout.
This separate toolchain-only cycle took 36.47 seconds.

Only current release archives and small verification logs are retained locally.
Nine obsolete archives were removed after matching their published digests
(1,426 MiB reclaimed), along with the completed disposable container image and
private Fusion validation checkout. User installations/projects were preserved.
Physical-PC staging is retained until cleanup can occur without interfering with
the user’s fullscreen application.

## Remaining scope

Windows 10 was initially deferred; the user has now identified a Dell test PC,
and access/setup is pending. First-time MSVC installation on a clean Windows 11
VM remains explicitly deferred. macOS Intel and BSD source-install paths have
less direct desktop coverage than the configurations above. Container results
verify real native application APIs; they do not assess display scaling, visual
spacing or every desktop compositor. No universal claim about arbitrary OS,
CPU, editor or driver versions follows from this matrix.
