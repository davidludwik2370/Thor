# Thor language and installer review, November 27, 2026

The review used checkout `0357f22` as its baseline. The installer checks below distinguish the published 0.41.12 binaries from the candidate built during this review.

## Compiler and vet findings

| Reproduction | Correction and regression coverage |
| --- | --- |
| Removing a named but unreferenced import made a program stop printing its startup message. Odin still compiled it. | Vet follows project import chains through Thor and Odin sources and checks direct SDK packages for lifecycle declarations. Tests cover a wrapper around native init/fini hooks and an SDK image codec that registers itself. |
| Removing an import changed `intrinsics.is_package_imported(Name)` from `true` to `false`. The package had a different declared name and directory name. | Vet preserves the import graph when the inspected sources query package presence. The regression checks the actual program output in checked, assertion-disabled and unchecked builds. |
| After vet removed an import, a native deprecation warning pointed one line below the call. | Vet translates generated Odin locations into original Thor coordinates before applying edits, then moves diagnostics with the edited source. The regression compares the applied JSON diagnostic against a fresh vet run on a CRLF file containing Unicode. |
| On POSIX, a program that terminated itself with SIGTERM produced shell status 143 directly and 241 through Thor. | The native runner now uses 128 plus the signal number. The regression executes the program in three build configurations. |
| A successful suite could report a cleanup failure when two processes retired the same cache file. | Cleanup tolerates entries already removed from a disposable directory and continues to surface permission and I/O failures. |

## Installer findings

- On Windows 11, uninstalling through the installed PATH command failed halfway with OS error 5. The installed parent executable was still running while its child tried to delete it. The launcher now runs both the installer and its batch worker from a temporary directory. The native executable also detects a direct attempt to uninstall itself before modifying the installation.
- On Unix, installing through a symlinked shell profile and subsequently uninstalling could leave the PATH block behind. The installer now records the resolved target, deduplicates linked startup files, and preserves their permissions. Reinstalling after a user replaces a shell profile also updates the restoration baseline. The setup journal is written with private Unix permissions.
- An interrupted editor install could record OLS or CodeLLDB as installer-owned before either installation succeeded. Ownership is now recorded per successful extension. Tests exercise failure, an independent user installation, retry, and uninstall.
- On Linux, empty or relative XDG directories could send the native installer and the VS Code extension to different installation records. The installer, maintenance bootstrap, cache cleanup and editor now agree on the home-directory fallback. Tests change working directories between install, doctor, and uninstall.
- Both bootstraps now recognize a GitHub SSH remote written as an `ssh://` URL. The POSIX bootstrap rejects newline-containing option values before serializing arguments.

## Language checks

- The compiler ran 136 Rust unit tests, TLS ran 1,178 tests, and the VS Code extension ran 206 tests. The reference inventory matched the implementation for 62 attributes, 81 directives, 10 file-level names, eight features, 18 vet names and two analysis names.
- An 807-test selection covered lifetime and leak analysis, allocation contracts, the immutable-edit oracle, pure callbacks, implicit returns and their language-server behavior. A separate 115-test selection covered the README examples, CLI controls, vet declarations and unverified reporting. Both selections passed.
- All 379 tests in a further language selection passed. It exercised the native compiler, scoped owners, struct arithmetic, switch results, fallback lists, comprehensions, compile-time modes, parallel mapping, dependent defaults and migration.
- On disposable copies of all 30 shipped example packages, vet applied 326 edits. Every package built in checked and unchecked configurations and a second apply proposed zero edits. A separate execution pass compared stdout before applying edits, after applying them, and after an unchecked build. All 30 programs exited successfully in every run and the three outputs matched for each program. Disposable copies of `poker_pmap` and `hilo_test` also passed check, apply and both builds.
- The new regressions run startup/shutdown, package introspection and child signal behavior with assertions enabled, with `-disable-assert` alone and with the unchecked release flags. Program-output and exit-status comparisons are made by the test harness.

### Vet timing

On the same ARM64 Mac, the baseline and reviewed release binaries took turns running each workload against separate warm caches. Each cell is the median of seven runs after a warm-up.

| Workload | Baseline | Reviewed |
| --- | ---: | ---: |
| Unused-import vet, 48 aliases of one package | 119.4 ms | 121.0 ms |
| `thor check`, poker_pmap | 627.1 ms | 612.9 ms |
| `thor vet`, poker_pmap | 1.276 s | 1.284 s |

The two vet medians increased by 1.6 ms and 8.8 ms respectively. These measurements cover the example workloads and warm caches. They do not establish a worst-case cost for a project with many distinct SDK imports.

## Platform checks

| Platform | What was exercised in this review |
| --- | --- |
| macOS ARM64 | Fresh temporary home, current installer with the published binary payload, checked and unchecked compilation, interactive zsh PATH, doctor and uninstall. The host already had development tools. |
| Ubuntu 24.04 ARM64 | Clean container, public GitHub checkout and published release bundle. Automatic prerequisites and VS Code installation, ordinary and integrated terminals, TLS hover and completion, native OLS, profile colors, Run, a CodeLLDB source breakpoint, reinstall and uninstall. The default profile's settings and keybindings matched the originals byte for byte. |
| Debian 12 ARM64 | Built the candidate installer inside Linux. All 80 selected installer and bootstrap tests passed, including the new XDG case. |
| Ubuntu x64 on WSL2 | Checkout bootstrap with the published x64 binary bundle, isolated home, ordinary bash PATH, doctor and uninstall on existing development tools. |
| Windows 11 x64 | Reproduced the installed-launcher failure with the published toolchain. Built the candidate on the PC, ran 19 native installer tests and ran two isolated real installations using the candidate installer with the published compiler payload. Doctor and uninstall passed separately through the installed launcher and the checkout command. A separate native build of the reviewed compiler passed all 35 declaration, iterator and unused-procedure vet tests. The release build reported four unused-variable or unnecessary-mutability warnings in platform-conditional code. |

The Windows 10 Dell was unreachable during this review. Its earlier fresh-install results are in the [Dell validation report](windows10-dell-2026-11-26.md). Fresh Intel macOS and native BSD installation were outside this run.

## Follow-up and remaining limits

- **SDK dependencies.** The follow-up retains uncertain SDK imports and their project wrappers, reporting a manual warning without an editor fix. In seven alternating warm runs, median time for 48 local aliases changed from 126 to 120 ms. For 48 aliases of `core:thread` it changed from 147 to 82 ms because no speculative removal needed validation. Full vet on poker_pmap took 1.315 seconds before and 1.314 seconds after.
- **Fish.** The follow-up adds a reversible fish startup snippet. Tests open fresh interactive and login fish shells, source the snippet twice, reinstall, preserve intervening user edits, and uninstall.
- **Release timing.** The README already explains how to use `--from-source` for the exact checkout. The default install still selects published 0.41.12. Shipping the fixes to default binary installs requires a new release.
- **Upstream compiler observation.** During the baseline run the native Odin compiler crashed once while compiling the plain Odin typed-loop README example. The focused rerun passed, as did 24 additional checked and unchecked compilations of the same source. The crash was in Odin code generation. Its root cause remains undetermined.
