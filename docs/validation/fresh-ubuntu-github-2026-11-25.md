# Fresh Ubuntu install and uninstall from GitHub — 2026-11-25

## Environment and acquisition

Validation used a disposable Fusion linked clone of the clean, pre-Thor Ubuntu
24.04.5 ARM64 desktop snapshot, with two CPUs and 4 GiB RAM. The existing Ubuntu
VM was not reset. Baseline inspection confirmed no Thor, Odin, Clang/GCC,
VS Code, Rust/Cargo, Node/npm, installation records, editor data or toolchain
caches. Stock Git, curl and Python were present. The snapshot's old uninstalled
source copy was removed before cloning the public repository.

The actual setup used:

```sh
git clone https://github.com/davidludwik2370/Thor.git
cd Thor
./thor-install --vscode --profile Thor --path
```

No local bundle, SDK, compiler, VSIX, custom prefix or dependency-skipping option
was supplied. The checkout was `395269a8581cbefd76784d7a9cf3aaca0ad12a38`, and
the bootstrap downloaded published release **v0.41.8** from GitHub. Temporary
askpass automation supplied the test account's normal sudo authentication
because guest-tools execution has no terminal; sudo policy was unchanged.

## Issue found and corrected

The first clean attempt with v0.41.7 failed because Ubuntu's first-boot automatic
updates held the dpkg frontend lock. Release v0.41.8 waits up to 300 seconds for
that lock and retries package-list lock contention within a 300-second bound.
Other repository/package-manager failures remain failures.

Tests held Ubuntu's real frontend lock for 12 seconds: a one-second timeout
failed after 1.04 seconds, and the production timeout waited successfully for
11.76 seconds. A separate real package-list lock test also completed prerequisite
provisioning after the lock was released. Process regressions cover argument
propagation, retrying list locks and preserving non-lock failures.

The final run used a newly reset clean clone, without the prerequisites installed
by those focused tests. All six release archive/checksum assets had GitHub
SHA-256 digests matching their local files before publication.

## Results

| Operation | Result | Time on this VM/network |
| --- | --- | --- |
| Public Git clone | Passed | 2.19 s |
| Full setup, including Clang, VS Code, OLS and extensions | Passed | 100.91 s |
| Doctor after setup | Passed | 8.28 s |

Additional CLI checks passed: project creation, checked and unchecked optimized
execution, direct executable build/run, vet, and native test mode in checked and
unchecked configurations. Verdicts for unchecked programs use explicit failure
branches rather than assertions. A new login shell found Thor through the managed
PATH entry.

The installed-extension GUI test passed eleven checks: installed VSIX activation;
standard Odin grammar/icons; automatically built ARM64 OLS hover/semantic tokens;
integrated-terminal PATH; TLS hover and field completion; profile theme/colors;
color enable/restore; Run; an original Thor source breakpoint; and clean debugger
continuation/exit. No developer toolchain checkout supplied those services.

Normal editor onboarding was completed without an account, and the generated
test folder was trusted through the normal Workspace Trust UI. The instrumented
extension-test host starts separately in Restricted Mode, so its launch alone used
`--disable-workspace-trust`; no persisted Workspace Trust setting was disabled.
The VM used the normal `thor new` debugger configuration, without the container
ASLR or Electron sandbox workarounds. Unrelated default-profile settings remained
byte-identical during editor testing.

## Uninstall and preservation

Uninstall dry-run left settings, PATH and registration unchanged. Actual
uninstall removed all installed toolchain files, command links, registration,
managed OLS builds and the three installer-added extensions. Checks found no
remaining installed files under the toolchain prefix.

The following survived uninstall:

- An editor preference and comment added after installation.
- Original shell-profile content and a newly added export, with Thor's marked
  PATH block removed. Conservative block removal can leave blank separator lines.
- The Git clone and test project, including unchanged source contents.
- VS Code and the system C/C++ developer tools, as intended.

Reinstall through the same GitHub command and a subsequent doctor passed while
preserving the added preferences. A second uninstall and another repeated
uninstall also passed. Rust, Cargo, Node and npm remained absent throughout the
binary-install path.

The local regression selection passed **77 tests**, including assertion hygiene;
the installer passed **8 Rust tests**. The five-host installer matrix and the
rebuilt Windows v0.41.8 editor/install/doctor/uninstall workflow also passed.
Detailed local logs and machine-readable results are retained under ignored
`dist/fresh-ubuntu-github-2026-11-25/`.

After evidence export, the disposable VM was powered off and deleted. Temporary
Linux release-build files were removed from the existing VM; its original Thor
0.41.6 installation was verified intact, freed space was trimmed, and the VM was
returned to its original powered-off state.
