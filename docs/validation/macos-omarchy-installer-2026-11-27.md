# Fresh macOS and Omarchy installer validation — 2026-11-27

## Omarchy ARM64

Tested a new official Try Omarchy 1.4.1 image on an Apple M1 Pro host. The guest
reported Omarchy `4.0.0.r7969.g459b90c`, Linux `7.3.0-72-omarchy`, and `aarch64`.
Created the requested disposable local account. The initial image had no Thor,
Odin, VS Code, Clang, or Cargo; GCC, Git, and Python were already provided by the
distribution. No existing developer installation was copied into the guest.

Source: public GitHub main `60f088a2d61e67b54373f9f879405fb1c259dc78`.
Binary toolchain: published Thor `0.41.12` Linux ARM64 bundle.

The unmodified public bootstrap installed Clang and its dependencies, then
stopped because VS Code acquisition handled Debian/RPM systems but not pacman.
The candidate change adds Omarchy's configured `visual-studio-code-bin` package.
It queries availability before installation, uses the existing privilege/package
manager path, and retains final editor-version verification. It neither installs
an AUR helper nor builds VS Code from source. After copying only that bootstrap
script change into the guest checkout, this command completed successfully:

```sh
./thor-install --vscode --profile Thor --path
```

It installed Microsoft VS Code `1.146.0-1`, built upstream native OLS
automatically, installed TLS/OLS/CodeLLDB, and passed the built-in doctor checks.
Both ordinary interactive and login Bash shells found Thor without VS Code's
terminal environment assistance.

`tools/test_install_desktop.py` subsequently passed in 97.08 seconds in the real
Wayland desktop session, with a disposable installation and editor profile:

- Install, repeated install, and doctor verification.
- Login and interactive shell PATH.
- Checked execution and unchecked optimized execution, using an explicit output
  file verdict that remains enabled when assertions are disabled.
- Thor extension activation, hover, and field completion.
- Native Odin grammar/icons and upstream OLS hover/completion/semantic tokens.
- Profile theme, Thor-only colors, and optional color enable/restore.
- Integrated terminal PATH without a shell profile, and Run without shell PATH.
- CodeLLDB breakpoint mapped to original Thor source and clean program exit.
- Existing default-profile preferences retained.
- Repeated uninstall with the test project retained.

The harness removed its private installation/editor directories. Raw logs and
machine-readable verdicts were retained under
`dist/macos-omarchy-vms-2026-11-27/omarchy-results` on the host.

## macOS ARM64

Created a new UTM Apple Virtualization VM with four CPUs, 8 GiB RAM, and an 80 GiB
sparse disk. Restored macOS Sequoia 15.6 build `24G84` from Apple's signed restore
image, verified against SHA-256
`8fbeaca047b66bcbc8ea557f2b3f95bcb84cd0f60cbfdadb52fc23ecfe671317`.
Completed normal first-run setup with a local account, without an Apple account
or migrated data. The user approved accepting the displayed macOS agreement.

Source: public GitHub main `60f088a2d61e67b54373f9f879405fb1c259dc78`.
Binary toolchain: published Thor `0.41.12` macOS ARM64 bundle.
The macOS test used the public bootstrap without a candidate code change.

The baseline confirmed that both Command Line Tools and VS Code were absent.
The bootstrap downloaded and verified the release, opened Apple's Command Line
Tools installer, waited for its completion, then downloaded Microsoft VS Code
`1.148.0` into the guest account's `~/Applications`. Apple Clang `17.0.0` and
`/Library/Developer/CommandLineTools` were selected. Native upstream OLS was built
automatically. The installed Thor extension was `0.23.8`, standard OLS extension
`0.1.47`, and CodeLLDB `1.12.3`.

The complete command succeeded:

```sh
./thor-install --vscode --profile Thor --path
```

Both ordinary interactive and login zsh shells selected the installed Thor
command. Doctor passed integrity, checked/unchecked execution, TLS/OLS hover and
field completion, editor-profile inventory and extension runtime dependencies.

The first real debugger launch displayed macOS's Developer Tools Access prompt.
That launch failed while waiting for first-use authorization. After approval in
the disposable guest, the full desktop harness passed on retry in 125.9 seconds:

- Install, reinstall and doctor, with an isolated toolchain/editor profile.
- Ordinary interactive/login zsh PATH, checked and unchecked optimized execution.
- Thor hover and completion; native Odin highlighting/icons and OLS services.
- Profile theme, Thor-only recommended colors and enable/restore behavior.
- Terminal PATH without a shell profile and Run without shell PATH.
- Breakpoint mapped to original Thor source and clean debugger exit.
- Original installation discovery record restored; default preferences retained.
- Repeated uninstall with the test project retained and temporary test files
  cleaned up.

The normal (non-isolated) installation was then uninstalled twice. A new login
shell no longer found Thor. VS Code, Command Line Tools and the source checkout
remained present. Re-running the same public install command succeeded, reusing
the existing editor and developer tools; final doctor passed. The regular Thor
installation remains available in the VM.

The final visual check of that normal retained profile caught a separate release
limitation: public `0.41.12` skips profile defaults when the profile registration
already exists, so uninstall/reinstall leaves the theme and recommended colors
absent. The isolated fresh-profile harness does not detect that retained-profile
case. The current working tree already contains the targeted fix in
`install/src/vscode.rs`: initialize only missing settings while retaining
explicit choices. Running that locally built candidate installer against the
VM's real retained profile restored `Dark Modern`, enabled the recommended
colors and passed doctor. The visible parallel example then showed the expected
Thor colors. Six focused profile/default/preservation regression tests passed.
This fix still needs to be included in a published installer; the release test
must not be read as a pass for retained-profile colors in public `0.41.12`.

Raw baseline, installation, first-authorization failure, successful desktop
verdicts and normal uninstall/reinstall logs are retained under
`dist/macos-omarchy-vms-2026-11-27/macos-transfer` on the host. Compact verdicts are
also recorded in `macos-installer-2026-11-27-results.json` and
`omarchy-installer-2026-11-27-results.json` beside this report.

## Scope and retained resources

These checks cover a fresh ARM64 installation on the tested macOS and Omarchy
versions. They do not establish support for every older release or every Arch
repository configuration. Omarchy requires its configured official VS Code
binary package; unavailable repositories get an actionable error.

The Omarchy prerequisite fix passed all 27 prerequisite tests and all seven
assertion-hygiene checks. No compiler or runtime behavior was changed in this
round. The Omarchy guest was shut down after testing to release its RAM. Both
VM disks are retained for reuse. The 16.8 GB Apple restore image was deleted
after restoration, and the Omarchy installation image was unmounted. Shared
files in the macOS guest are limited to the dedicated test scripts/logs folder.
