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
