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
