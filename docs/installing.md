# Installing Thor

`thor-install` installs a versioned, per-user native toolchain. Users do not need
Rust, Python, Node or npm. From a cloned repository, choose one:

```sh
./thor-install
./thor-install --vscode
./thor-install --vscode --profile Thor --path
```

Windows uses `.\thor-install.cmd` or `thor-install.ps1`. The toolchain includes
Thor, runtime libraries, parser sources, templates, TLS/builtin definitions and a
tested private Odin compiler/SDK. Existing Odin installations are not replaced.
Explicit `--odin`/`THOR_ODIN` selections retain precedence.

**Before GitHub releases are published**, supply a local native bundle:

```sh
./thor-install --bundle /path/to/thor-0.41.6-linux-aarch64.tar.gz --vscode --profile Thor
```

Keep its adjacent `.tar.gz.sha256` sidecar, or supply a trusted `--sha256 HEX`.
The repository contains the installer launchers, not generated binaries.
`--release-base https://host/path` (or `THOR_RELEASE_BASE`) selects an artifact
directory; `--version VERSION` selects a version. Otherwise the bootstrap derives
`https://github.com/OWNER/REPO/releases/download/vVERSION` from the clone's GitHub
origin. No command uploads or publishes anything.

## Prerequisites

- macOS: Xcode Command Line Tools (`xcode-select --install`).
- Ubuntu/Linux: Clang, system development/linker files, CA certificates and curl.
  The initial clean-container validation targets Ubuntu 24.04 ARM64. Native
  bundles must match OS, architecture and Linux ABI.
- Windows x64: MSVC and Windows SDK (Desktop development with C++), PowerShell
  and Windows' `tar.exe`. The bootstrap does not change execution policy.
- `--vscode`: an installed VS Code desktop application and network access for
  Odin highlighting and CodeLLDB. `--code PATH` selects the CLI. Missing system/editor prerequisites
  produce instructions; the installer does not silently install privileged OS
  software.

A named profile is created through VS Code's launcher, which can open a window.
This needs a graphical session; Xvfb is suitable for container validation.
Toolchain-only installation works without a desktop.

For example, install the Ubuntu command-line prerequisites with:

```sh
sudo apt update
sudo apt install clang build-essential curl ca-certificates
```

Install VS Code from its official distribution before using `--vscode`, and run
profile setup from a terminal inside the signed-in desktop session.

## Paths and discovery

| Platform | Default prefix | Discovery record |
| --- | --- | --- |
| Linux | `$XDG_DATA_HOME/thor` or `~/.local/share/thor` | `$XDG_CONFIG_HOME/thor/installation.json` or `~/.config/thor/installation.json` |
| macOS | `~/Library/Application Support/Thor` | `installation.json` under that prefix |
| Windows | `%LOCALAPPDATA%\Thor` | `installation.json` under that prefix |

`--prefix DIR` selects an empty or installer-owned directory. Spaces and Unicode
are supported. Quotes and shell punctuation rejected by Odin output-path rules
are rejected before installation; choose another prefix when needed. Versions live in
`versions/VERSION-CONTENTID/`, with stable commands under `bin/`. User-writable
directories accommodate prepared parser caches and shared runtime output. The
discovery record lets VS Code use the installation without environment variables
or per-project settings. Explicit editor paths take precedence.

`--path` adds a marked block to `.profile` (bash/sh) or `.zprofile` (zsh), backing
up existing content and avoiding duplicates. Windows adds only the new directory
to user PATH; fish receives a `fish_add_path` instruction. Open a new terminal
afterward. Without `--path`, use the installed absolute command or configure PATH
yourself. Updating a startup file does not refresh an already running desktop
session or VS Code process. In an existing bash terminal, run `. ~/.profile`;
otherwise sign out and back in (or restart VS Code from a refreshed shell).

The installer also gives the VS Code process it launches the updated PATH.
Thor's extension adds the registered command directory to new integrated
terminals through VS Code's environment API, including after shell startup.
This does not edit `terminal.integrated.env.*`, shell files, project settings or
other extensions' environment changes. Existing terminals may need relaunching;
their running shells are not silently sent commands. The editor Run command
uses the registered compiler directly even before the terminal is refreshed.

## Editor profiles and optional colors

`--vscode` installs Thor, the standard [Odin Language Server extension](https://marketplace.visualstudio.com/items?itemName=DanielGavin.ols)
(`DanielGavin.ols`) and CodeLLDB in the default profile. Unrelated settings are preserved.
OLS supplies Odin's grammar, file icon, highlighting and language services; TLS
serves only `.thor`. The separate `aesl.odin` grammar extension is no longer
installed or required. Existing extensions are not automatically uninstalled.
`--profile Thor` instead creates/uses that named
profile. New profiles use the
[starter template](../install/profiles/Thor.code-profile): Dark Modern plus the
Thor-only color preset. `--no-colors` disables the preset for a new profile;
`--colors` explicitly selects it. Existing themes and keybindings are always
preserved, including with `--colors`; ARM64 Linux adds only its managed OLS server setting. The installer never edits VS Code's profile
registry directly.

Profiles apply to windows/workspaces, not automatically to `.thor` extensions.
Open a project with `code /path/to/project --profile Thor`; VS Code can remember
that association. Cmd+R/Cmd+Shift+R on macOS (Ctrl+R/Ctrl+Shift+R elsewhere) are
provided by the extension and apply only in Thor editors. Existing user bindings
retain precedence. Projects' `.vscode` files are not rewritten.

Colors can be used in any profile: **Thor: Apply Recommended Colors** enables
`thor.colors.recommended`; **Thor: Restore Previous Colors** removes only unchanged
rules added by Thor. Rules are scoped to `:thor`, adapt to light/dark themes,
preserve explicit customizations and leave the selected theme/other languages
alone. Rules edited afterward become user-owned. A new profile's Dark Modern
selection applies to its entire window, with extra colors only in Thor code.

On ARM64 Linux, `--vscode` automatically builds unmodified, pinned upstream OLS
using the bundled Odin compiler and the system Clang/linker prerequisites. No
Git, Python, Cargo, separate Odin installation or manual server build is needed.
The release includes hash-verified upstream source, builtins, provenance and its
license. The release builder acquires source; the end-user installer does not
fetch or build an unpinned upstream checkout.

The build uses two compiler threads and lives in an installer cache under
`PREFIX/editor/ols`. Reinstalling the same toolchain reuses the verified build.
A failed build leaves Thor usable and can be retried with `thor-install --vscode`.
The selected VS Code profile gets `ols.server.path` and a backup of its prior
settings. JSONC comments, formatting, unrelated values, themes and keybindings
are preserved. Existing custom OLS server paths are retained; only a path
previously managed by the installer is updated during an upgrade.

Native OLS has semantic highlighting enabled and points to the bundled Odin SDK.
Project `ols.json` files retain their usual precedence. On other platforms, the
standard OLS extension continues using its normal server-download/configuration
prompts. The installed command directory also exposes `odin` for OLS and plain
Odin workflows. Code processes launched by the installer receive the bundled
`ODIN` path when one has not already been explicitly selected.

`--code-user-data DIR` and `--extensions-dir DIR` support isolated testing.
Reload the window after installing/updating the extension. Workspaces must be
trusted before TLS/execution/debugging activate; installation does not disable
Workspace Trust or trust arbitrary projects.
For your own newly created project, choose **Yes, I trust the authors** when
VS Code first opens the folder. If it is already in Restricted Mode, use
**Manage Workspace Trust** to review that folder.

The extension registers terminal PATH support after editor startup, even before
a Thor file is opened. TLS itself starts only when a Thor file is open. Removing
the installation clears its cached terminal PATH entry on the next activation.

## Verification and recovery

Downloads use HTTPS (localhost HTTP is allowed for integration tests). The
bootstrap checks SHA-256 before executing the native installer. That installer
validates every inventoried file, rejecting links, traversal, duplicate/colliding
paths, missing/extra files and wrong-target bundles. Checksums detect corruption;
their authenticity depends on the trusted release origin or an independently
supplied expected hash.

New versions are staged and must pass a compile-time/runtime build/run check
before activation. Failed checks leave previous commands/records active.
Unmanaged directories/commands and modified installed files are preserved with
an error. Old versions remain available; `previous-installation.json` records
the previous activation. Reinstall its bundle to select it again.

`--dry-run` verifies the bundle and prints the plan without installing/changing
settings. `thor-install --doctor` checks the registered toolchain. The installed
native installer can add editor support with `thor-install --vscode --profile Thor`
without downloading the bundle again, and accepts local bundle updates. Use the
repository bootstrap for downloads. If editor setup fails, the CLI remains available; correct the reported
problem and rerun editor setup. Interrupted installers may leave `.install-lock`;
after ensuring no installer is running, remove that directory and retry.

V1 does not destructively uninstall or prune old versions. To remove an
installation, close the editor and remove its owned command links, matching
discovery record and version directories. Remove only its marked PATH block if
added. Remove extensions/profiles through VS Code's usual controls. Projects and
unrelated settings are never installation-owned.

## Maintainer release builds

Maintainers need Rust/Cargo, Python 3.11+, Node/npm and the reviewed Odin SDK:

```sh
cargo build --release --locked --manifest-path rust/Cargo.toml --bin thor-rust -j 2
cargo build --release --locked --manifest-path install/Cargo.toml -j 2
python3 tools/build_tls.py
npm --prefix editors/vscode ci
npm --prefix editors/vscode test
npm --prefix editors/vscode run package
python3 tools/build_distribution.py --odin /path/to/reviewed/odin
```

Build natively on each target. `--compiler`, `--installer`, `--tls`, `--vsix` and
`--output` select prebuilt inputs/output. The builder checks compiler version and
reviewed SDK hashes and creates an inventoried tarball/checksum in ignored
`dist/`. It copies an explicit component allowlist, excluding source compiler
checkouts (except the pinned OLS source needed for ARM64 Linux setup), Python reference, tests, developer tools, local projects, caches and
benchmark logs. Archive entries omit machine ownership and source timestamps.
Original notices and a Rust dependency license inventory accompany each bundle.

Linux builders need `patchelf` and Debian copyright metadata to relocate Odin's
LLVM/support libraries. macOS builders need LLVM's `llvm-symbolizer` and compiler-rt resources, `otool`,
`install_name_tool` and `codesign`. Relocated copies are ad-hoc signed; public
release signing/notarization is a separate step. Windows uses a tested native
Odin distribution and its adjacent DLLs. Do not mix revisions' SDKs/DLLs.

## Clean Ubuntu validation

[`tools/test_install_ubuntu.sh`](../tools/test_install_ubuntu.sh) creates a fresh
Ubuntu 24.04 container with only OS/editor prerequisites, then exercises the
prebuilt installation with Rust, Cargo, Node and Python absent from user PATH.
VS Code's private Node and CodeLLDB's private Python are their own components.
Xvfb enables editor testing; this container check does not replace a hardware or
full desktop VM test.
