# Native Windows validation

See the [November 23 follow-up](windows-validation-followup-2026-11-23.md) for
the latest native results, known debugger limitations and manual review steps.

`tools/windows_validation.ps1` runs explicit validation stages in an isolated
source snapshot. It imports the installed MSVC developer environment into the
current process, selects the supplied Odin/Python executables, and delegates to
`tools/windows_validation.py`. It does not install tools or change accounts,
firewall rules, SSH settings, or machine-wide environment variables.

The wrapper defaults identify the tools from the `david_pc` handoff. Override
`-Odin` and `-Python` when selecting a different installation. All executables
must be native Windows builds; WSL does not validate Windows behavior.

```powershell
.\tools\windows_validation.ps1 -Stage preflight,build,smoke
.\tools\windows_validation.ps1 -Stage rust-units,tls-units,tls-unchecked
.\tools\windows_validation.ps1 -Stage focused,tls-transport,editor
.\tools\windows_validation.ps1 -Stage debugger
```

The optional `acceptance` stage runs the complete maintained compiler gate.
`build-rust` rebuilds the native compiler/examples without replacing a running
TLS executable. `tls-hover` runs only the layout/hover transport tests; the full
`tls-transport` stage also exercises compiler diagnostics, completion and edits.
`debugger` checks original source breakpoints, stepping and values through the
installed CodeLLDB adapter, including readonly literals, `using`, and `pmap`.
`regressions` repeats the bundled demo and direct-executable source-location
checks that initially exposed Windows path/link assumptions.
`tls-regressions` repeats Windows result-query, callback, runtime-discovery,
editor-alias and unsaved-file cases, plus checked/unchecked runtime mapping.
The focused stage starts with ordinary language acceptance, native evaluation,
compile-time modes, and scoped owners. TLS transport includes hover layout
oracles compiled with the selected Odin compiler. Windows adds native AMD64
and i386 oracle profiles; the other probes use LLVM IR and require no foreign
operating-system SDK or foreign executable execution.

Snapshots include `snapshot-manifest.json`, listing every source file's SHA-256
hash and the base Git revision. Each stage verifies the file hashes, computes
a source fingerprint, and records it alongside platform/tool versions, exact
commands, logs, durations, timeouts and exit statuses in `windows-results`.
Keep the logs from failed attempts before updating or rerunning a stage.
Only an executable's expected output and successful exit establish smoke-test
success. Missing host facilities and skipped tests must remain explicit.

Every stage now runs [development storage maintenance](storage.md) before and
after execution, including failure and interruption. It preserves inventoried
source, fixtures and text/JSON reports while pruning obsolete caches, oversized
debug histories and generated native outputs in `windows-results`. The JSON
records include cleanup outcomes. Completed candidate snapshots should be
retired after preserving their unique edits and reports; create the next
candidate from source, never by copying its predecessor's build/cache trees.

Rust compilation uses two workers; Rust and Odin unit-test execution are also
bounded. Command timeouts clean up only the process tree launched by the stage.
Source snapshots exclude local build products, caches, integration clones,
private access material, and unrelated user files. The frozen Python compiler
remains unchanged; native acceptance uses the Rust executable.

Further native gates:

```powershell
.\tools\windows_validation.ps1 -Stage allocation,windows-native
.\tools\windows_validation.ps1 -Stage acceptance
.\tools\windows_validation.ps1 -Stage debugger-full
```

The Windows SDK effect profile is pinned to revision `9a69916`. It covers native
standard-file I/O, fixed-buffer formatting and the default RNG; filesystem opens
and allocating conversions retain their normal checks. Profile verification
accepts CRLF checkouts while rejecting other source edits. Changed SDKs require
another review. Its Windows-specific boundary proof does not cover cross-OS
execution. The original reviewed SDK profile remains available for Mac/Linux.

Compile-time dependency snapshots use native high-resolution Windows file IDs
through the safe `file-id` adapter. Every byte-dependent input is still rehashed
before reuse. File identity or timestamps alone never certify unchanged content;
unsupported file-identity facilities disable the optional cache safely.

Native installations can place `thor.exe` beside `frontend/`, `runtime/` and
`templates/`, or in a `bin/` subdirectory. The compiler discovers those files
relative to its executable; `THOR_ROOT` remains an explicit override. `new`,
ordinary builds, migration and root queries use the same discovery rule.
Reviewed runtime contracts use the compiler-owned support collection from that
installation, cached per resolver. A user `-collection:thor=...` override remains
distinct and does not inherit installed-library purity or permission exemptions.
