# Windows 10 Dell and resumed Windows 11 installer validation

This extends the [installer compatibility round](installer-compatibility-2026-11-26.md).
It checks installation, editor services, checked/unchecked execution, upgrades,
PATH and uninstall. It does not claim a new run of the entire language suite or
resolve the upstream debugger limitations recorded in the separate platform report.

## Machines and initial state

The Dell is a Windows 10 Pro 22H2 x64 desktop (build 19045), with an Intel
Core i5-4590S, 8 GB RAM and a healthy HDD. VS Code 1.121.0 was already installed; its own updater later installed 1.148.0.
MSVC, the Windows SDK, Git, Rust and usable Python were initially absent.
The ordinary installer successfully acquired Microsoft C++ Build Tools and the
Windows SDK after the user's normal UAC approval. Native C compilation/linking
and Thor checked/unchecked execution passed. VS Code was reused, not reinstalled.
A private Python interpreter was used only to drive tests; it is not an installer
prerequisite or a global installation.

Windows 11 x64 build 26200 and its Ubuntu 24.04 x64 WSL2 desktop were retested
after the user lifted the fullscreen restriction. The first candidate passed the
complete isolated editor cycle on both: Windows in 131.59 seconds and WSL in
102.04 seconds. That includes standard OLS ownership of Odin grammar/icons,
OLS/TLS hover and completion, optional Thor colors, Run, an original-source
CodeLLDB breakpoint, clean debug exit, ordinary-shell PATH and repeated uninstall.

## Fixes found by the Dell and regression runs

- GitHub source ZIP bootstrapping no longer requires Git or adopts an unrelated
  parent checkout's remote. Validated repository metadata supplies the release
  location for an archive; an actual checkout still uses its own origin.
- An unrelated built-in VS Code `Agents` profile uses a different location format.
  Setup now reads and validates only the requested profile. Unsafe requested
  paths, duplicate requested profiles and unknown top-level formats remain errors;
  the editor's registry is never rewritten by Thor.
- A completely unconfigured Windows client defaults to refusing PowerShell files.
  The `.cmd` launcher uses process-only `RemoteSigned` only when every policy scope
  is undefined. Explicit process/user/machine policies and organization Group Policy
  remain in force; downloaded unsigned scripts remain subject to signature checks.
  The child Windows PowerShell process discovers its own native modules instead
  of inheriting incompatible PowerShell 7 module paths. No permanent policy or
  user module configuration is changed. The installed native `thor-install
  --vscode` command applies the same child-only policy selection when it invokes
  prerequisite setup, so it works independently of the initial `.cmd` bootstrap.
  Native Windows tests cover literal paths, explicit policies and downloaded scripts.
- Cold profile creation on the Dell exceeded the old ten-second timeout. Setup
  now waits up to two minutes, returning immediately when VS Code registers the
  profile. A real delayed-registration regression exceeds the original deadline.
- Linux test cleanup now recognizes Electron's reordered process arguments and
  orphan utilities only when they identify the exact disposable editor directories.
  Unrelated windows and neighboring test paths are protected by regression tests.
  The original test verdict is recorded before final temporary-directory cleanup.
- VS Code's background updater held its application-wide update mutex while the
  installer-created Welcome window remained open. New isolated launches logged
  “Code is currently being updated.” The test closed only that verified Welcome
  window using its normal close message, letting Microsoft's updater finish.
  Disposable test data now disables automatic application updates; actual user
  preferences and the normal update policy remain unchanged. No updater was
  killed and no application lock was bypassed.

The expanded local selection passed 188 tests (five native Windows cases skipped
on the Mac), plus all 15 native Rust installer tests. All five policy/argument
checks passed on the unconfigured Dell, including explicit Restricted/AllSigned
policies, downloaded unsigned scripts, quoted paths and local-checkout startup.

The Dell's cold editor Run check measured 36.535 seconds, exceeding the old
30-second test deadline. The integration harness now records individual check
timings and permits up to 90 seconds for cold operations, returning immediately
when the result arrives. It also waits for the one-shot terminal PATH probe to
finish closing before testing an interactive Run terminal, and retains shell
execution diagnostics on failure. These changes affect tests, not user builds or
runtime execution. A focused real-editor check against the installed toolchain
passed all thirteen language-service/Run/debugger checks in 74.93 seconds.

## Release verification

Release 0.41.12 is rebuilt from the verified 0.41.11 SDK and TLS. A source guard
requires unchanged compiler implementation, runtime, SDK pins and dependencies;
the compiler package version was rebuilt once, and subsequent installer-only
refreshes retain that exact compiler binary. The refresh guard requires the same
version, unchanged compiler/SDK/dependency inputs and the current source commit's
unpublished draft; it refuses published releases and another source's draft. Every archive's
outer checksum and every inventoried file are checked, and SDK/runtime/editor
payloads are compared with the already exercised candidate. Linux ABI inspection
checks the Ubuntu 22.04 compatibility floor; macOS inspection checks macOS 13.

The [final results](windows10-dell-2026-11-26-results.json) record archive digests,
individual verdicts and timings. The [five-host native regression matrix](https://github.com/davidludwik2370/Thor/actions/runs/45861122535)
passed, including 17 Rust tests on Windows and 15 on each other host. The final
[installer-only refresh](https://github.com/davidludwik2370/Thor/actions/runs/45860289969)
passed native upgrade, checked/unchecked execution, Doctor, ABI and repeated
uninstall checks on all four binary-release architectures. Only installer files
differ from the previously validated candidate; compiler/SDK/TLS/editor files are
byte-identical. The release tag includes subsequent test-only corrections.

[Release 0.41.12](https://github.com/davidludwik2370/Thor/releases/tag/v0.41.12) is
published. All eight [fresh public Linux cases](https://github.com/davidludwik2370/Thor/actions/runs/45864993493)
passed: Ubuntu 22.04, Ubuntu 24.04, Debian 12 and Fedora on x64 and ARM64. Each
saved host/editor verdict was independently checked, and every Doctor log
confirmed version 0.41.12. Earlier failures are retained as failure evidence,
not counted as successful installation cycles.

| Desktop | Isolated install/editor/uninstall cycle | Public setup/verification with cached prerequisites |
| --- | ---: | ---: |
| Windows 10 Dell | 428.27 s, all checks passed | 79.65 s, passed |
| Windows 11 PC | 133.36 s, all checks passed | 61.07 s, passed |
| Ubuntu 24.04 WSL2 on PC | 101.88 s, all checks passed | 52.62 s, passed |

The Dell also passed the complete actual default-prefix cycle: installer,
standalone native editor setup under the unconfigured client's normal policy,
private editor/CLI lifecycle, default uninstall twice, original PATH restoration,
pre-existing extension/default-preference preservation, reinstall, Doctor and a
fresh ordinary command prompt. This broader HDD cycle took 862.46 seconds; it
includes multiple installations and is not the time for one cached install.

The final public desktop checks used the repository bootstrap without a local
bundle or release-base override. Windows checks additionally compared the
parallel example's complete output in checked and unchecked release builds,
covering polymorphic/runtime callbacks, compile-time evaluation, pointer inputs,
zero-input jobs and multiple results. Windows' permanent execution-policy lists
were identical before and after setup.

## Preservation and remaining scope

Tests use separate editor data and prefixes. They check default settings and
keybindings, restoration of the original user PATH, preservation of pre-existing
extensions and project sources, and repeated uninstall. Checked and unchecked
verdicts use explicit comparisons; required operations are outside assertions.
The final public installations are left available. Eight test tasks were removed
from each Windows PC, along with the Dell's private interpreter, copied bundles,
private source checkouts and disposable GUI state. The cleanup first verified
that removing a junction leaves its target intact. Final Doctor checks passed
again on Windows 10, Windows 11 and WSL, and no owned scheduled tasks remained.
Original source projects, VS Code and developer tools were preserved. About
1,487.8 MiB of superseded local staging archives were also removed; only the
current native release archives and small verification evidence remain.

First-time development-tool acquisition on a clean Windows 11 VM remains deferred
at the user's request. The Dell supplies real Windows 10 and first-time MSVC/SDK
coverage, but it is not a pristine OS image. Automated editor API and debugger
checks do not measure visual spacing, scaling or every compositor/driver.
