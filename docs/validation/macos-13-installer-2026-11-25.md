# macOS 13 installation failure — 2026-11-25

The supplied M1 MacBook Air / macOS 13.5.1 log reached the source-install
prerequisite stage after a release download returned HTTP 404. Its initial
Command Line Tools issue had already been resolved before Thor setup started.
Homebrew and Python installation completed; the fatal error was the shell
command at `install/prerequisites.sh:119`.

Apple's Bash 3.2 `/bin/sh` misparses a `case` pattern inside the quoted command
substitution used to select a Homebrew formula. The resulting shell text was
passed to `brew install` as a formula name. The same expression was reproduced
on the development Mac; `sh -n` alone does not detect this execution-time bug.
Formula selection now happens in an ordinary `case` statement before invoking
Homebrew.

Two adjacent source-install issues were also corrected:

- An existing Apple Silicon Homebrew is found even if its login-shell PATH
  setup has not yet taken effect.
- When no supported LLVM is installed, setup requests `llvm@22`, rather than
  accepting an installed unsupported major or following the moving unversioned
  formula. The reviewed Odin supports LLVM 17–22.

## Binary compatibility

The published v0.41.8 Mac ARM64 archive was inspected with `vtool -show-build`:

| Component | Mach-O minimum macOS |
| --- | --- |
| Thor and native installer | 11.0 |
| TLS, Odin and llvm-symbolizer | 15.0 |
| Bundled LLVM, z3 and zstd libraries | 15.0 |

Consequently, the default bootstrap now uses a native source build on Macs
older than the official bundle's `release/macos-minimum`, currently 15.0. This
decision happens before downloading the incompatible archive. Custom bundles
and release bases retain their own compatibility requirements. Maintenance and
explicit release selection do not silently switch to another source version.
Downloads also print the actual requested release URL to aid future diagnosis.

## Validation and limits

The installer, source-install, prerequisite, OLS and assertion-hygiene selection
passed 84 tests. An additional regression for explicitly selected release
versions passed separately. The native installer passed eight Rust tests, and
the native C compile/link prerequisite check passed.

Process regressions execute missing Node/npm/Git branches using the real Apple
`/bin/sh` and `/bin/bash` 3.2, with package-manager operations replaced by
recording fixtures. They cover npm's mapping to Node, installing Node only once,
reusing existing tools, unsupported LLVM versions and Homebrew outside PATH.
Bootstrap tests simulate macOS 13.5.1, version boundaries including numeric
15.9/15.10 ordering, inert dry-runs, maintenance and custom release locations.

These checks ran on macOS 15.6, not a macOS 13 guest. A complete native macOS
13 install remains unverified. Older macOS Homebrew source builds can take much
longer than installing the published binary and remain subject to upstream
package and Command Line Tools support.

Retry from the existing checkout with `git pull --ff-only`, followed by the
original `./thor-install --vscode --profile Thor --path` command. Already
installed prerequisites are reused.

## Dependency-chain follow-up

A second installation log exposed a separate issue: installing Node through
Homebrew on macOS 13 pulled in source builds of LLVM 23, Rust and many other
packages. This happened before source setup could use its existing official
Node binary acquisition. The earlier `llvm@22` correction did not prevent that
transitive dependency chain. The source-install dependency path was revised:

- macOS no longer installs Homebrew packages. Apple's Python 3.9 can run the
  source installer and distribution builder, avoiding a new Python build.
- Missing Node/npm and Rust/Cargo use verified private binaries on macOS,
  Linux and Windows. Compatible existing tools are reused.
- Missing Mac LLVM uses pinned official LLVM 18.1.8 ARM64 or 19.1.7 Intel
  packages. Precompiled components are indexed into the archive Odin expects.
  Only the small zstd C library is compiled, with two jobs; LLVM is not compiled.
- Optional native VSCE signing/keychain dependencies and npm install scripts
  are omitted. npm and Node compile caches stay in the disposable workspace.
- Reusing Odin avoids SDK-only dependency acquisition. Native C/linker checks
  remain, as do normal checked/unchecked health checks and license inventories.

Official archive SHA-256 values were verified. The Intel LLVM package's actual
Mach-O minimum was inspected and recorded as **macOS 13.7**, not 13.0. Apple
Silicon's package targets macOS 11.0; the reported macOS 13.5.1 M1 therefore
passes the package selection check. This does not replace testing on macOS 13.

Local Mac validation completed a full native source build/install with Apple's
Python 3.9.6 and a private Rust 1.89.0 toolchain. It took **341.44 seconds**,
excluding preparing the LLVM inputs. LLVM extraction, zstd build and archive
indexing took **61.55 seconds**, plus approximately 15 seconds for downloading
the 800 MiB LLVM package on this connection. Node 22.14.0/npm 10.9.2 binary
acquisition was tested separately; the complete install reused an existing Node.
These measurements are specific to the development Mac and network.

The installation's checked and unchecked compile/run health checks passed.
Additional installed-product tests passed for parallel `pmap` callbacks with
multiple inputs and named results, in checked and unchecked builds, and SDK JSON
compatibility. The isolated Mac installation and temporary build inputs were
removed after verification.

The installer/OLS/source-tool/assertion-hygiene selection passed **99 tests**,
and the native installer passed **eight Rust tests**. A Linux source lifecycle
run exposed Node's default compile cache outside the workspace. After explicitly
placing that cache inside the workspace, the repeated source install completed
in **319.23 seconds**; the full build, doctor, SDK JSON, checked/unchecked tests,
uninstall and repeated uninstall took **328.26 seconds**. No temporary build
files remained. The VM's original Thor 0.41.6 installation was unchanged; the
isolated test workspace was removed and the VM returned to its powered-off state.

The final native installer receives the caller's original environment rather
than private build-tool variables. This prevents VS Code and its terminals from
inheriting Cargo, Rustup or npm paths that are removed after source installation.
Regression tests cover preserving the caller's paths and cache settings.

The GitHub-hosted Windows source installation, doctor and uninstall passed in
[run 45644060355](https://github.com/davidludwik2370/Thor/actions/runs/45644060355).
The installer regression matrix also passed on Windows, both Ubuntu architectures
and Intel macOS. Its hosted ARM macOS job was cancelled before running; the local
ARM macOS validation above supplies native coverage for that host.
