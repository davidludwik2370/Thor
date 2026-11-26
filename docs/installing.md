# Installing Thor

`thor-install` installs a versioned, per-user native toolchain. Binary installation
does not need Rust, Python, Node or npm. From a cloned repository, choose one:

```sh
./thor-install
./thor-install --vscode
./thor-install --vscode --profile Thor --path
```

Windows uses `.\thor-install.cmd` or `thor-install.ps1`. The toolchain includes
Thor, runtime libraries, parser sources, templates, TLS/builtin definitions and a
tested private Odin compiler/SDK. Existing Odin installations are not replaced.
Explicit `--odin`/`THOR_ODIN` selections retain precedence.

For an offline or locally prepared installation, supply a native bundle:

```sh
./thor-install --bundle /path/to/thor-0.41.7-linux-aarch64.tar.gz --vscode --profile Thor
```

Keep its adjacent `.tar.gz.sha256` sidecar, or supply a trusted `--sha256 HEX`.
The repository contains the installer launchers, not generated binaries.
`--release-base https://host/path` (or `THOR_RELEASE_BASE`) selects an artifact
directory; `--version VERSION` selects a version. Otherwise the bootstrap derives
`https://github.com/OWNER/REPO/releases/download/vVERSION` from the clone's GitHub
origin. No command uploads or publishes anything.

The current published macOS bundle requires **macOS 13 or newer** because of
its bundled Odin/LLVM libraries. This is distinct from Thor's CPU support: M1
is supported. On older macOS versions, the default repository command selects a
native source build before downloading that incompatible bundle. A custom
`--bundle` or `--release-base` must supply software compatible with that Mac.
The official bundle's minimum is recorded in `release/macos-minimum`.

## Automatic prerequisites and supported hosts

The portable Linux release baseline is **Ubuntu 22.04 with current updates**:
glibc 2.35 and libstdc++ ABI GLIBCXX 3.4.30. Release packaging checks the actual
native executables and shared libraries, including native vendor libraries.
Linux support here means glibc-based systems; Alpine/musl is a separate,
unvalidated configuration. The macOS ARM64 baseline is macOS 13 (tested on
13.5.1); Windows desktop validation currently covers Windows 11 x64. Windows 10
validation is pending on an identified test machine. See `release/linux-compatibility.json` and
`release/macos-minimum` for the enforced release bounds.


On Ubuntu/Debian, setup waits up to five minutes for package-manager locks held
by automatic updates or another package operation. Package-list lock contention
is retried within the same limit; other package-manager errors are reported.

The installer reuses working tools and installs missing prerequisites. On macOS
it opens Apple's Command Line Tools installer and waits for its completion;
Apple's license/installation dialog still needs approval. On Linux it uses the
distribution package manager (apt, dnf, pacman or zypper). Windows uses Microsoft's
signed Visual Studio Build Tools installer for MSVC and the Windows SDK. System
package installation can require sudo/doas or UAC; a required restart is reported
without rebooting the computer. Rerun the same command afterward.

`--vscode` also installs VS Code when missing: an official signed application in
`~/Applications` on macOS, the official Debian/RPM package on supported Linux
systems, or Microsoft's signed per-user installer on Windows. Existing editor
installations are reused; `--code PATH` selects one explicitly. BSD editor
availability is separate from compiler support: FreeBSD uses its `vscode` port;
NetBSD/OpenBSD require an available compatible editor and `--code PATH`.

Use `--no-install-deps` on managed systems to disable system/editor prerequisite
installation; verification still runs and reports missing tools. Source builds
can still acquire pinned Odin, private binary build tools and Cargo/npm packages; use a local
bundle without `--vscode` for an offline binary installation. `--dry-run`, `--doctor` and
`--uninstall` never install prerequisites. The bootstrap itself needs a downloader;
POSIX bootstrap acquisition can install curl/CA certificates using the package
manager. Windows uses built-in PowerShell HTTPS downloads.

The source-install host matrix follows Odin: Windows x64; macOS and Linux x64 or
ARM64; FreeBSD and NetBSD x64 or ARM64; OpenBSD x64. Windows ARM64 and 32-bit x86
are not native Odin hosts. macOS/Linux ARM64 have been validated locally; other
hosts need native validation before equivalent support can be claimed. The
installer CI covers Windows x64, macOS Intel/ARM64 and Linux x64/ARM64. BSD paths
are implemented and covered by platform-selection tests, but have not yet been
exercised on native BSD machines. Distribution/ABI and upstream Rust/editor
availability can impose further requirements on older OS versions.
BSD source installs retain their system LLVM runtime dependency; standalone BSD
binary releases need separate loader/redistribution validation before publication.

On Fedora, editor setup also checks for the OpenSSL 3 runtime needed by VS Code's
extension signature verifier. Fedora 45 provides this alongside OpenSSL 4 as a
[maintained compatibility package](https://packages.fedoraproject.org/pkgs/openssl3/openssl3-libs/).
The installer adds its `libssl.so.3` provider when missing, including when Code is
already installed. This addresses the upstream
[extension verification failure](https://github.com/microsoft/vscode/issues/338194)
while retaining signature verification and the system's existing OpenSSL.

A named profile is created through VS Code's launcher, which can open a window.
Run profile setup in a graphical session. Toolchain-only installation works
without a desktop.

## Building from source

```sh
./thor-install --from-source --vscode --profile Thor --path
# Reuse a matching reviewed Odin instead of building Odin:
./thor-install --from-source --odin /absolute/path/to/odin
```

The default is a tested prebuilt release. A confirmed HTTP 404 for that host's
bundle falls back to building the checked-out source. Network, TLS, checksum,
server and installation failures do not trigger a silent source fallback. A
source fallback cannot substitute another version: check out the desired release
first. Windows uses the same options through `thor-install.cmd`/`.ps1`.

Thor-owned source and tests are in the checkout. Source installation additionally
uses Python 3.9+, Rust/Cargo, Node/npm, Git, LLVM development tools, and the
pinned reviewed Odin revision. Rust packages use committed Cargo lockfiles; SDK
files are checked against the reviewed source hashes. A compatible existing Odin
can be selected explicitly. BSD builds use system Rust packages. Windows source
prerequisite acquisition uses winget for missing Python/Git; if App Installer
is unavailable, it reports which tool to install. Binary setup does not require
winget, Rust, Python or Node. Windows Rust executables link their C runtime
statically so the installer can start before developer tools are installed.

SDK source acquisition also uses a checksum-pinned private Git LFS binary when
needed. Its filters apply only to the disposable SDK checkout, and no Git hooks
or global Git configuration are installed. Packaging rejects unresolved large-
file pointer files rather than silently shipping unusable vendor libraries.
SDK downloads also use a checkout-local LFS object store, so global Git cache
settings cannot retain temporary build objects elsewhere.

Compatible existing Rust/Cargo and Node/npm are reused. Otherwise macOS, Linux
and Windows source builds download verified binary tools into their temporary
workspace: Node 22.14.0 and a minimal Rust 1.89.0 toolchain. Rust uses private
Cargo/Rustup homes; Node and Rust setup does not change global tools, shell
profiles or PATH. Existing older system Node/Rust installations are retained.

macOS source setup uses Apple's Command Line Tools Python 3.9 and built-in Bash
3.2. It does **not install Homebrew or any Homebrew packages**. If no usable
LLVM is installed, it downloads a pinned official package: LLVM 18.1.8 for Apple
Silicon (macOS 11+) or LLVM 19.1.7 for Intel (macOS 13.7+). The installer combines
precompiled LLVM objects into the archive Odin expects; LLVM is never compiled
from source. It builds only a small pinned zstd C library with two jobs to satisfy
those packages' compression dependency, retaining the LLVM and zstd notices.
Intel Macs older than 13.7 need a compatible installed `LLVM_CONFIG` or an OS
update; incompatible downloads are rejected before acquisition. Version pins,
checksums and upstream references are recorded in `install/source-tools.json`.
macOS distributions also include LLVM's matching `dsymutil`. Thor selects it
only for debug builds using that bundled SDK, without changing the user's PATH
or external Odin installations. Older Apple symbol tools can otherwise discard
DWARF 5 information and prevent source breakpoints from resolving.

Maintainers build portable Linux releases on Ubuntu 22.04 using
`--from-source --portable-bundle`. This refreshes native Raylib/Raygui libraries
against the same ABI baseline and tests both static/shared linking in checked
and unchecked builds. A checksum-pinned private CMake binary is acquired when
the system CMake is too old; ordinary binary installs do not need CMake or the
graphics development packages. Portable macOS releases use the reviewed LLVM
binary toolchain and refresh native Raygui with the declared deployment target.
The release builder rejects native libraries requiring newer OS versions. Portable
SDK compilers use generic CPU targets rather than the build machine’s native
instruction set, so a newer ARM64 build host does not require SVE on older CPUs.

The `Portable release build` GitHub workflow produces native draft assets; the
`Installer OS compatibility` workflow exercises clean Ubuntu 22.04/24.04,
Debian 12 and current Fedora containers on x64 and ARM64. These tests install
missing prerequisites and VS Code, run actual editor language services and a
source debugger breakpoint under Xvfb, check ordinary terminal PATH setup,
reinstall and uninstall, and preserve default editor preferences. Their small
logs are retained temporarily; containers and their new images are removed.
For an existing desktop, maintainers can run `tools/test_install_desktop.py`
with `--code PATH --output DIR` and optionally `--bundle ARCHIVE`. This uses an
isolated installer state/editor profile and closes only its own test windows.
On macOS, GUI processes use the real account home for Keychain access; the
original installation discovery record is backed up and restored after the
isolated editor check. On Windows, run it in the logged-in desktop session,
rather than directly inside SSH.

Linux setup no longer installs Node/npm through the OS package manager. BSD
continues to use native binary packages for Node, Rust and LLVM. Reusing Odin
with `--odin PATH` skips SDK-only C++/LLVM acquisition, and on Windows also skips
Git acquisition. The required native C compiler/linker/SDK is still checked.
Extra cross-target linkers such as `wasm-ld` remain optional prerequisites for
those targets; native Linux source setup no longer installs LLD unconditionally.

Build targets, downloaded build tools, Odin source and npm cache/dependencies
live in a temporary directory and are removed on success or failure. VSIX
packaging omits optional native keychain/signing packages, disables npm lifecycle
scripts, and runs VSCE without an additional network installation. Cargo builds
use two jobs and release optimizations. Tools that were already installed remain
untouched. A source install takes longer and uses more temporary space than a
binary install; it does not add a runtime layer to generated programs.
The final installer/editor starts with the caller's original environment, so
VS Code terminals do not retain temporary Cargo, Rustup, npm or build-cache paths.

The reduced dependency path has been exercised on current macOS with Apple's
Python 3.9 and through uninstall/reinstall on an M1 Mac running macOS 13.5.1.

The installation includes representative health checks, not the compiler's full
regression suite. A clone already contains that suite; maintainers can run
`python3 tools/test_compiler.py` with the development dependencies installed.

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

`--path` configures login and interactive startup files for zsh and bash. Zsh
uses `.zprofile` and `.zshrc` (including an exported `ZDOTDIR`). Bash uses its
first existing login file (`.bash_profile`, `.bash_login`, or `.profile`) and
`.bashrc`; other POSIX shells use `.profile`. The marked block adds Thor only
when its command directory is absent from PATH. Existing contents are retained
and backed up; uninstall restores the owned edits. Windows adds only the new
directory to user PATH; fish receives a `fish_add_path` instruction. Open a new
terminal afterward. Without `--path`, use the installed absolute command or
configure PATH yourself. Existing desktop sessions and editor processes may
need restarting to inherit the updated environment.

Windows compilation works without enabling Developer Mode or running Thor as
administrator. Generated library views use directory junctions and file hard
links when symbolic links are unavailable, with independent copies for
read-only or different-volume files. Generated writes replace files atomically
and preserve the original sources. These are build-time filesystem operations;
they add no overhead to the compiled program.

The installer also gives the VS Code process it launches the updated PATH.
Thor's extension adds the registered command directory to new integrated
terminals through VS Code's environment API, including after shell startup.
This does not edit `terminal.integrated.env.*`, shell files, project settings or
other extensions' environment changes. Existing terminals may need relaunching;
their running shells are not silently sent commands. The editor Run command
uses the registered compiler directly even before the terminal is refreshed.

## Editor profiles and optional colors

Thor's extension requires **VS Code 1.96.0 or newer**. Setup checks the editor's
version, upgrades an obsolete standard installation when necessary, and reuses
a compatible editor. Automatic editor acquisition runs before source compilation.
An explicit `--code PATH` is validated and remains user-managed.

Save work and quit an obsolete VS Code before upgrading it; run setup from a
separate terminal. The installer does not close editor windows. macOS setup
verifies the downloaded application signature and its minimum OS version, stages
the replacement, and restores the previous application if relocation checks fail.
The replacement preserves VS Code's separate settings and extension storage.
Older Macs use reviewed VS Code 1.147.0 from `install/vscode-macos-version`
instead of an unconstrained future `latest` download. Microsoft's
[platform support FAQ](https://code.visualstudio.com/docs/supporting/faq#can-i-run-vs-code-on-old-macos-versions)
describes the Ventura/Sonoma support transition.

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
preserved, including with `--colors`; native OLS adds only its managed server setting. The installer never edits VS Code's profile
registry directly.

Profiles apply to windows/workspaces, not automatically to `.thor` extensions.
Open a project with `code /path/to/project --profile Thor`; VS Code can remember
that association. Restarting a window that uses a different profile does not
switch its profile. If a `.thor` file shows **Plain Text** in the status bar,
first open the project explicitly in the profile selected during installation.
On macOS, if the `code` shell command is unavailable, use the application launcher:

```bash
"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" --new-window --profile Thor /path/to/project
```

An application installed in `~/Applications` has the same launcher path under
that directory. Extensions installed with `--profile Thor` are available in Thor
profile windows; this does not install them into the default profile. Compiler
reinstallation is unnecessary when the issue is the active VS Code profile.

Cmd+R/Cmd+Shift+R on macOS (Ctrl+R/Ctrl+Shift+R elsewhere) are
provided by the extension and apply only in Thor editors. Existing user bindings
retain precedence. Projects' `.vscode` files are not rewritten.

TLS discovery requires an actual built server executable. Opening the cloned
Thor repository uses the registered installed toolchain if the checkout contains
only TLS source files. Building TLS in the checkout is optional; a built development
checkout and explicit server-path settings remain supported. Compiler-only Run
and Debug discovery does not require the TLS executable.

Colors can be used in any profile: **Thor: Apply Recommended Colors** enables
`thor.colors.recommended`; **Thor: Restore Previous Colors** removes only unchanged
rules added by Thor. Rules are scoped to `:thor`, adapt to light/dark themes,
preserve explicit customizations and leave the selected theme/other languages
alone. Rules edited afterward become user-owned. A new profile's Dark Modern
selection applies to its entire window, with extra colors only in Thor code.

`--vscode` automatically builds pinned upstream OLS
using the bundled Odin compiler and the system Clang/linker prerequisites. No
Git, Python, Cargo, separate Odin installation or manual server build is needed.
The release includes hash-verified upstream source, builtins, provenance and its
license. The release builder acquires source; the end-user installer does not
fetch or build an unpinned upstream checkout.

For the reviewed Windows SDK, a small JSON-writer compatibility adapter is
applied to a disposable build copy. It accommodates Odin's changed JSON escaping
API; Odin syntax, language services and highlighting remain standard OLS. The
bundled upstream files stay hash-identical. The adapter is tested in checked and
unchecked builds, including escaping and I/O error propagation.

The build uses two compiler threads and lives in an installer cache under
`PREFIX/editor/ols`. Reinstalling the same toolchain reuses the verified build.
A failed build leaves Thor usable and can be retried with `thor-install --vscode`.
The selected VS Code profile gets `ols.server.path` and a backup of its prior
settings. JSONC comments, formatting, unrelated values, themes and keybindings
are preserved. Existing custom OLS server paths are retained; only a path
previously managed by the installer is updated during an upgrade.

Native OLS has semantic highlighting enabled and points to the bundled Odin SDK.
Project `ols.json` files retain their usual precedence. All installer targets configure this native upstream OLS server, avoiding a
first-use server download. An explicitly configured custom OLS server is retained
and reported as user-managed during verification. The installed command directory also exposes `odin` for OLS and plain
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

`--dry-run` verifies the bundle and prints the plan without installing software
or changing user settings. `./thor-install --doctor` and the installed
`thor-install --doctor` verify the registered installation without downloading a
bundle. Maintenance also finds installations with a custom prefix through their
discovery record. The installed native installer can add editor support with
`thor-install --vscode --profile Thor` without downloading the bundle again.

Every successful setup ends with an explicit PASS/SKIP report:

- Archive/file integrity, installed command discovery and Odin startup.
- Thor compile-time evaluation, allocation/cleanup and runtime execution in both
  checked and unchecked optimized builds (checks use explicit failure branches).
- Current-process PATH visibility; parent-shell refresh is reported separately.
- With recorded editor setup: TLS and native OLS initialization, real type hover
  and field completion over their LSP transports, plus VS Code's extension
  inventory for the selected profile.

Verification failure exits nonzero. Editor setup failure leaves the working CLI
available and can be retried. Non-interactive verification does not claim to test
editor rendering, workspace trust or actual source breakpoints: these are marked
SKIP and remain visual checks. The dedicated GUI integration suite exercises them
in a disposable profile. A custom user-managed OLS server is likewise reported
separately rather than silently replaced.

Interrupted installers may leave `.install-lock`; after ensuring no installer is
running, remove that directory and retry. Unmodified old toolchains are retired,
keeping the active version and one rollback. Modified files are preserved.

## Uninstall

For installations older than 0.41.7, rerun setup first to install the maintenance
commands. The repository bootstrap delegates maintenance to the installed
installer and does not download or install software during removal.

```sh
./thor-install --uninstall --dry-run
./thor-install --uninstall
# Also available through the installed thor-install command.
```

Uninstall does not download software or require Rust/Python. It removes
inventoried, unchanged toolchain files, command links, installation records,
managed OLS builds, recognized shared-cache files, and installer-owned PATH and
editor settings. It restores only settings still equal to the installer's values;
subsequent edits, comments, unrelated settings, unknown files and symlink targets
are preserved. Installer-created backups are removed only when unchanged.

Extensions are inventoried before setup. Uninstall removes only extensions added
by this installer in the recorded profile; pre-existing OLS/CodeLLDB and unrelated
extensions remain. VS Code has no supported profile-deletion CLI, so profile
registrations remain and can be deleted through VS Code after uninstall. The
installer never rewrites VS Code's private profile registry.

VS Code itself, system/developer tools (including Rust/LLVM), source checkouts,
user projects and their local build output remain. Retained customizations are
reported. A second uninstall is safe. On Windows a temporary installer copy
allows its installed executable to be removed after it is no longer running.

## Maintainer release builds

A successful source install can retain its already verified native bundle with
`./thor-install --from-source --export-bundle /path/to/releases`. This is a
maintainer option; normal source installation removes the temporary bundle and
build directories. The separate Installer bundle verification workflow can reuse
a Windows source-build artifact to check a newer installer and editor setup
without compiling the transpiler again.

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
checkouts (except the pinned OLS source needed for native editor setup), Python reference, tests, developer tools, local projects, caches and
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
