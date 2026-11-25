# Odin highlighting and terminal PATH installation — 2026-11-24

`thor-install --vscode` now installs the Thor VSIX, Marketplace extension
`aesl.odin` (Odin syntax highlighting/snippets) and CodeLLDB in the requested
profile. Odin files retain their own language identity and do not need a second
language server merely for syntax highlighting.

Writing `.profile` does not change the environment of the installer, an existing
editor or the desktop session. The installer itself can be the first process to
launch VS Code, because profile creation opens a window. It now prepends the
installed stable command directory to its Code child processes' PATH while
preserving other entries.

The Thor extension also registers that directory through VS Code's persistent
terminal environment collection. It uses the installer record, not a project's
compiler override. The change applies at terminal creation and shell integration,
including when the editor was launched from a desktop with an old PATH. It does
not write terminal settings or send commands to running shells. Reopening an
existing terminal may be necessary. Startup activation enables this before a
Thor file opens; TLS starts only for open Thor files, with queued starts
coalesced so opening several documents does not repeatedly restart the server.

## Regression checks

- 24 installer process tests passed, including exact extension/profile arguments,
  child PATH propagation/deduplication, download-failure recovery and preservation
  of existing shell/editor configuration.
- All 185 VS Code Node tests passed. New coverage includes Windows command-wrapper
  paths/separators, missing/invalid/uninstalled registrations, cached PATH cleanup,
  repeated activation, prefix changes and deferred/coalesced TLS startup.
- All 7 assertion-hygiene checks passed; `git diff --check` passed.
- A fresh Ubuntu 24.04 ARM64 container passed install, repeat install, doctor,
  checked health execution, unchecked release execution, native test mode and vet.
  The default profile's preseeded settings/keybindings remained byte-identical.

The real installed VSIX passed ten editor checks:

1. Installed extension activation.
2. `.odin` language recognition and grammar registration.
3. A new terminal executing `thor version` without sourcing a profile and with
   Thor absent from the test editor process's inherited PATH.
4. TLS hover using the private SDK.
5. TLS struct-field completion.
6. Named profile theme and Thor-only semantic colors.
7. Optional color enable/restore.
8. Run using the registered compiler independently of inherited shell PATH.
9. CodeLLDB source breakpoint on the original Thor file.
10. Debug continuation and clean exit.

The disposable color fixture accounts for colors applied by the installer-created
window before the extension-test host starts; the two hosts have separate
mementos. It clears only the known generated preset in its disposable Thor
profile before testing ownership-aware enable/restore within one host. It does
not change the production color preservation rules.

## Actual Ubuntu desktop

The user's existing Fusion VM was upgraded to Thor extension 0.23.1 and Odin
highlighting 1.2.2, keeping CodeLLDB 1.12.3. Its `.profile` and existing profile
settings/keybindings were compared before and after installation and were
byte-identical. After a normal editor reload, `library.odin` was visibly colored
with language mode Odin, and a newly created integrated bash terminal printed
`Thor 0.41.6` without a profile-source command. No project source was edited.

The local Linux and macOS bundles were refreshed. Manifest comparisons confirm
only `thor-install` and `editors/thor-language.vsix` changed: the compiler,
runtime and SDK payloads are unchanged. Previous bundles remain under ignored
`dist/before-editor-update/`; current test results are in
`dist/editor-update/` and `dist/ubuntu-validation/`. The VM retains its previous
bundle under `~/Thor/dist/before-editor-update/`, its updated source/guide and
the current bundle at the original default path.

Windows separator/wrapper behavior is unit-tested, but this change did not
contact or run a native Windows machine. No artifacts were published.

## Follow-up: standard OLS for Odin

The temporary 0.23.2 approach that connected Odin to TLS was withdrawn in 0.23.3.
The Thor extension again contributes only Thor's language, grammar, icons and
colors, and its client selector accepts only Thor documents. Odin-only windows
retain terminal PATH support without starting TLS.

`--vscode` now installs the official `DanielGavin.ols` extension instead of the
standalone `aesl.odin` grammar extension. OLS supplies its own Odin icon, grammar
and language services, with its normal server/configuration prompts. Existing
extensions and OLS configuration are not removed or overwritten by the installer.

All 24 native-installer process checks passed, covering extension/profile
arguments, repeat installation, preservation and recovery after an OLS extension
installation failure. All 186 editor Node checks passed, including regressions
that Thor never contributes Odin styling or starts TLS for Odin-only windows.
The native installer Rust checks also passed.

The ARM64 Ubuntu VM has the official Marketplace extension 0.1.47. Upstream does
not publish an automatic Linux ARM64 server download in that extension, so this
VM uses an unmodified native build of upstream commit
`0c9438faeba846232caaa22fe9f22b30c302dba2`, compatible with the reviewed Odin SDK.
The newer upstream head requires SDK modules absent from this Odin revision.
Its separate server/configuration is under `~/.local/share/ols/0c9438f`, with
`enable_semantic_tokens=true` and the installed Odin compiler/root configured.
The profile's `ols.server.path` points to it; its previous settings were backed
up, and other settings/keys were preserved. Thor's TLS binary is not used as OLS.

The installed-extension regression is
`tools/install-ubuntu/verification-extension/ols-test.js`. It exercises the
standard OLS grammar/icon registration, generic hover, field completion and
semantic tokens, followed by a Thor hover through the separate TLS client.
All five checks passed in an isolated Ubuntu VS Code profile using the installed
extensions. All seven assertion-hygiene checks passed as well.
The regular installer GUI suite verifies the official OLS extension's grammar
and icons without assuming an unattended upstream server download can occur.

The VM's installer/source and both local native bundles were refreshed. Inventory
comparisons show changes only to `thor-install` and `editors/thor-language.vsix`;
the compiler, TLS, runtime and reviewed SDK are unchanged. Prior bundles remain
under `dist/before-standard-ols-update/`. The existing VM window needs Developer:
Reload Window to load extension 0.23.3 and its OLS configuration.

## Automatic ARM64 Linux setup and VM idle settings

`--vscode` now builds unmodified upstream OLS from inventoried, hash-verified
source included in ARM64 Linux bundles. The source is pinned to upstream
`0c9438faeba846232caaa22fe9f22b30c302dba2`; its license and source inventory ship
with the bundle. Release builders can acquire the official archive automatically
or use a matching checkout. All required source files are checked against the
committed hashes before staging. End-user setup needs neither Git nor a second
Odin/Rust/Python toolchain. Native builds use two compiler threads and are cached
per installed toolchain; repeat setup verifies/reuses the binary and builtins.

The installer adds `ols.server.path` through a narrow JSONC edit, preserving
comments, spacing and unrelated settings and keeping a backup. Custom server
choices remain authoritative; upgrades change only the installer's own previous
choice. Source/build/cache failures leave the CLI usable, stage directories are
cleaned on failure, and edits made during compilation cause a retry instead of
being overwritten. Installed commands now also expose the bundled `odin`.

The initial clean-install test exposed that this OLS revision ignores
`--version` and enters its LSP loop. That synchronous probe was removed; a
regression verifies that installation never launches the server as a version
probe. Real LSP/editor tests validate its functionality instead.

Validation:

- 34 installer/source cases pass on native ARM64 Linux; macOS passes with the
  seven Linux-specific cases skipped. Four Rust checks pass, including JSONC
  comments, Unicode, trailing commas and invalid/ambiguous settings.
- All seven assertion-hygiene checks pass.
- A fresh Ubuntu ARM64 container, with no Cargo, Rust, Python or Node on PATH,
  passes installation, repeat installation/cache reuse, doctor, vet, native
  test mode and an unchecked release run. Its eleven real-editor checks include
  official OLS hover/semantic tokens, Thor TLS hover/completion, terminal PATH,
  optional colors, Run and a source breakpoint/continuation through CodeLLDB.
  The unrelated default profile remains byte-identical.
- The unmodified upstream OLS passes hover and semantic-token checks both in a
  checked build and with `-disable-assert -no-bounds-check -no-type-assert`.
- Both local bundles are refreshed. Only the native installer changes on macOS;
  Linux also adds the 74 inventoried upstream source/provenance/license files.
  Compiler/runtime/SDK/TLS and the editor VSIX remain unchanged. Prior bundles
  are preserved under `dist/before-ols-auto-install/`.

Separately, the existing Fusion VM's GNOME idle timeout is zero, screen locking
is disabled, and automatic suspend is disabled on AC and battery. The previous
values are saved in `~/.config/thor-validation/vm-idle-settings-before.json`.
Authentication settings were not changed. These preferences are not part of
`thor-install` and are not applied on other people's computers.

The native build/configuration path was also exercised with a new isolated
profile in the actual Fusion VM: the installer built OLS and selected its server
without a manual setting edit. An additional named-profile editor-test launch
exited without running its test entry point, so editor functionality is verified
by the successful fresh-container suite rather than claimed from that launcher.
Its disposable editor processes were closed; the user's profile was preserved.
