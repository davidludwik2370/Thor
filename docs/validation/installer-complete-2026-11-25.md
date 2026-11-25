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
