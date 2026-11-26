# Windows and Linux on the PC: installation validation

Validation used the public GitHub checkout on the Windows PC and a new Ubuntu
24.04 x64 WSL2 virtual machine on that PC. Linux ran its own native compiler,
VS Code, TLS, OLS and CodeLLDB, rather than forwarding editor requests to Windows.
The VM has a 16 GB sparse disk limit. No Windows firewall, login or remote-access
configuration was changed.

## Checks

The actual installer removed its previous toolchain, owned extensions and managed
PATH entries, then installed the toolchain and the named Thor VS Code profile.
Tests exercised the installed VSIX in a real VS Code extension host. A separate
temporary editor user-data directory avoided touching existing windows/projects.
On Linux, Xvfb and a session D-Bus supplied a native desktop test session.

The checked and unchecked optimized builds of the parallel example produced the
same expected output. Unchecked builds used `-o:speed -disable-assert
-no-bounds-check -no-type-assert`; the Python harness compared output explicitly.

Live editor checks covered Thor type hover, field completion, semantic tokens,
hover within the unbuilt GitHub checkout, ordinary Odin hover/completion and
language icons, a fresh terminal resolving Thor, Run, and immediate Run after
closing its terminal. Debugger checks stopped on the original `.thor` source,
read `person.age` as `42`, stepped over a source line, and resumed to exit zero.
These are install smoke tests, not a claim that every upstream LLDB limitation
or the previously documented Windows debugger cases has been resolved.

## Issues found and corrected

The old published 0.41.8 bundle included extension 0.23.3. Syntax highlighting
worked, but an unbuilt source checkout could shadow the installed TLS binary.
The next distribution includes the existing discovery and closed-terminal fixes.
Distribution packaging now rejects a stale VSIX whose version differs from the
editor sources, with a regression test.

A fresh marketplace CodeLLDB package can initially contain only JavaScript.
CodeLLDB downloads its native platform package asynchronously on activation.
Thor now activates CodeLLDB when necessary and waits for its completion marker
and adapter before spawning it. It rechecks the extension location because that
download can replace the package. Cancellation and download-failure messages are
covered by unit tests; an already complete package needs no wait. See the
[upstream setup description](https://github.com/vadimcn/codelldb/wiki/Setup).

The Linux desktop harness initially used a runtime directory that logind removed
between sessions. A private, persistent test runtime directory corrected the
harness. Native Code on WSL also uses its documented `DONT_PROMPT_WSL_INSTALL=1`
setting for this deliberately native-Linux test. Neither adjustment is required
for an ordinary Ubuntu desktop installation.

## Evidence and regression coverage

Transient JSON results, installer logs and archive checksums are under ignored
`dist/windows-linux-install-2026-11-25/`. Source/packaging and assertion-hygiene
checks passed (42 tests); editor unit checks passed (200 tests). No Python
compiler implementation was changed. The Mac ARM64 release candidate also passed
checked, unchecked optimized and unchecked debug build/run tests.

## Published 0.41.9 results (completed November 26)

The final cycle downloaded the ordinary public GitHub release, without
`--from-source`, `--bundle`, or maintainer export flags:

| Host | Full uninstall and reinstall | Additional runtime/editor verification |
| --- | ---: | ---: |
| Windows 11 x64 | 43.35 seconds | 22.90 seconds; all 14 editor checks passed |
| Ubuntu 24.04 x64 on WSL2 | 48.81 seconds | 22.70 seconds; all 14 editor checks passed |

These timings reuse the development tools and VS Code installed in the initial
cycle. Ubuntu's initial fresh-VM setup also exercised prerequisite installation
and installation of native Linux VS Code. Windows reused its existing VS Code
and development tools, as intended by uninstall's preservation policy.

Both final cycles passed Doctor and checked/unchecked execution. The Linux editor
started with no native CodeLLDB adapter installed and completed its first-use
setup, original-source breakpoint, watch, step and clean exit successfully.
Windows uninstall restored the exact original user PATH. Hashes confirmed that
the default Windows VS Code settings and keybindings were unchanged.

Uninstall intentionally retains VS Code profile registrations. For the optional
color check, missing Dark Modern/Thor color preferences were restored only in the
test-created Thor profile after reinstall; existing preferences were not
replaced. This is separate from the installer checks of TLS, OLS and the debugger.

All six published archive/sidecar assets match their local SHA-256 digests. The
Windows bundle-verification workflow now packages the current editor sources
rather than reusing a potentially stale VSIX from its input artifact.

Temporary test editor directories and source/export bundles on the PC were
removed. Installed Thor 0.41.9, the public checkout and the Thor profile remain
available. The Ubuntu distro is named `ThorInstallTest`, defaults to user `david`,
and was stopped after verification to release its memory. Its filesystem used
approximately 4.6 GB after cleanup, with a 16 GB virtual disk limit. Enter it with
`wsl -d ThorInstallTest`; it runs native Linux tools, with WSLg available for GUI
use. The automated GUI checks used a virtual X display and do not assess visual
spacing or color appearance on a physical monitor.
