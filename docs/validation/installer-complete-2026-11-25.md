# Complete installer validation — 2026-11-25

The installer adds automatic OS prerequisites and VS Code acquisition, explicit
source builds with pinned SDKs and locked Rust packages, confirmed-404 source
fallback, reversible ownership records, uninstall and bounded final verification.
The compiler/runtime semantics are unchanged; Thor's release version is 0.41.7.

## Local native validation

- macOS ARM64: complete disposable source build using the reviewed existing Odin;
  compile-time/runtime smoke in checked and unchecked release configurations.
- Ubuntu 24.04 ARM64 Fusion VM: complete source acquisition/build of the pinned
  Odin revision, Rust compiler/installer, TLS, VSIX and distribution; native
  installation, PATH setup and doctor. Intermediate build directories are removed.
- Both hosts: real VS Code setup in separate validation data/profiles/extensions;
  TLS and pinned, unmodified native OLS initialize and return actual type hover and
  struct field completion over LSP. Thor/OLS/CodeLLDB extension inventory passes.
- Fresh Ubuntu 24.04 ARM64 container: the shared prerequisite routine installed
  missing curl/CA certificates, Clang/C development files and the official VS Code
  Debian package. Native C compile/link/run passed. The container and image were
  removed after preserving the log; no Docker build cache is retained.
- Installer Rust unit tests cover JSONC restoration, Windows path normalization
  and URI encoding. Process regressions cover ownership, retries, dry run,
  source fallback policy, checksums, preservation, checked/unchecked execution,
  malformed/terminated language servers and uninstall. Editor unit tests run
  with real installation discovery isolated where PATH fallback is the subject.
- Assertion-hygiene checks pass. Required setup operations are outside assertions;
  the installed unchecked smoke verdict uses explicit `if ... { panic(...) }`.

The clean source builds exposed and corrected packaging outside a Git checkout,
new-profile extension inventory ordering, and official Node archive top-directory
handling. Review also corrected settings ownership across upgrades: intervening
user keys/comments do not become installer-owned and survive uninstall.

Final packaging validation also caught missing VSIX JavaScript dependencies and
the Windows SDK's omitted `bin` helpers (including `radlink.exe`). Both are now
included. Packaging rejects an incomplete language-client payload; installation
checks the actual installed extension entry point and its runtime dependency
graph. Named-profile verification handles VS Code versions whose extension
location command only searches the default profile.

The macOS installed-VSIX integration test also passes activation, standard Odin
grammar/icons, terminal PATH, TLS hover/completion, color enable/restore, Run,
an original Thor source breakpoint and clean debugger exit. It uses a separate
editor data directory and extensions directory, leaving the user's editor alone.

The same installed-VSIX test passes in the Ubuntu ARM64 Fusion desktop, including
native OLS, TLS, Run, a mapped source breakpoint and debugger exit. Local final
regressions pass: 67 installer/source/OLS cases, 8 Rust installer tests, 186 editor
unit tests and 7 assertion-hygiene tests. The Windows x64 complete pinned-source
build, checked/unchecked execution, doctor and uninstall pass in GitHub Actions.

Windows editor verification additionally exposed Odin's changed JSON-writer API
in pinned OLS. A narrow adapter is applied only to a disposable Windows build
copy, with exact-input guards and checked/unchecked JSON escaping and I/O-error
tests. Bundled upstream sources remain unchanged, and OLS retains its standard
Odin language behavior. Windows downloads suppress expensive PowerShell progress
rendering without changing the caller's preference.

The [Windows release/editor run](https://github.com/davidludwik2370/Thor/actions/runs/45606409300)
passes automatic official VS Code installation, checked/unchecked JSON adapter
tests, checked/unchecked Thor execution, standard native OLS compilation, actual
TLS/OLS hover and completion, installed JavaScript dependency checks, doctor and
two uninstall attempts. This is non-interactive editor verification, not a claim
that Windows GUI rendering or every debugger scenario was tested here.
The [five-host regression matrix](https://github.com/davidludwik2370/Thor/actions/runs/45606038615)
also passes on Windows x64, Intel/ARM64 macOS and x64/ARM64 Linux.

## Scope and limits

Normal setup verifies actual checked/unchecked compilation and execution, file
integrity, installed commands, current-process PATH, TLS/OLS services and requested
editor extension inventory. It prints explicit SKIP messages for parent-shell
refresh, custom OLS services and interactive rendering/source breakpoints. These
are not presented as completed tests or as a full compiler regression run.

Uninstall retains VS Code, development tools, projects, modified/unrecognized
files and profile registrations (VS Code has no supported profile-deletion CLI).
It removes only added extensions, unchanged inventory files, known caches and
owned settings/PATH changes. No project source is installation-owned.

Host selection follows Odin's native hosts: Windows x64; macOS/Linux x64/ARM64;
FreeBSD/NetBSD x64/ARM64; OpenBSD x64. BSD source/package-manager paths are
implemented and have platform-selection coverage, but native BSD runs remain
unverified. Windows ARM64 and 32-bit x86 are not claimed as native Odin hosts.
GitHub CI covers Windows x64, Intel/ARM64 macOS and x64/ARM64 Linux. Full source
installations are available through the workflow's explicit source-build run.

Detailed transient logs and checksum-verified release bundles are kept under
ignored `dist/install-v2-validation/`; no binaries or build caches are committed.
