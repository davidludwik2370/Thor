# Native installer validation — 2026-11-24

The implementation adds repository-root POSIX and Windows bootstraps, a native
Rust installer, inventoried per-platform release bundles, a VS Code profile
template, installed-toolchain discovery, and an optional Thor-only color preset.
No release or repository was uploaded. See [installation](../installing.md).

## Clean Ubuntu test

`tools/test_install_ubuntu.sh dist/thor-0.41.6-linux-aarch64.tar.gz` completed in
a newly created Ubuntu 24.04 ARM64 Docker container. The container had OS linker
and desktop prerequisites plus official VS Code. Rust, Cargo, Node and Python
were absent from the user's PATH; there was no global Odin installation. VS Code
and CodeLLDB used their private runtime components.

The installer created the user toolchain, private Odin SDK, command links, PATH
block and named Thor profile. Installation, repeated installation, doctor,
unchecked release execution, native test mode and vet passed. Existing default
profile settings and keybindings were preseeded, then checked byte-for-byte
after installation and editor use.

Eight checks passed inside real VS Code under Xvfb, loading the installed VSIX:

1. Extension activation from the installed location.
2. TLS hover using the private SDK.
3. Struct-field completion.
4. Dark Modern profile and Thor-only colors.
5. Optional color enable/restore commands.
6. Run from the active file's directory without a Thor PATH entry.
7. CodeLLDB breakpoint mapped to original Thor source.
8. Debug continuation and clean program exit.

The recipe uses disposable editor storage and a disposable container. Electron's
`--no-sandbox` and the fixture's Workspace Trust/ASLR test settings are confined
to this container workflow; the production installer does not change those
settings. This is a clean Linux container/editor test, not a physical-desktop VM
or hardware-driver validation.

Logs and the machine-readable GUI result are retained locally under ignored
`dist/ubuntu-validation/`. The test container is removed by the recipe.

## Ubuntu desktop VM follow-up

The same Linux ARM64 bundle was installed in the Ubuntu 24.04 Desktop Fusion VM
using its real signed-in Xorg session, the default user prefix and the named
Thor profile. Official VS Code 1.147.0 ARM64, Thor 0.41.6, the installed Thor
extension 0.23.0 and CodeLLDB 1.12.3 were used. Commands were executed through
VMware guest tools; Workspace Trust and the paused source breakpoint were also
checked in the visible desktop. Workspace Trust remained enabled, and this run
did not use the container's Electron sandbox or debugger ASLR workarounds.

The checked and unchecked release programs both printed `Thor: 42` and wrote
their execution marker. Native test mode passed its test, doctor passed, and vet
completed with the fixture's expected immutable suggestion and unverified
summary. All eight editor checks above passed, including continuing from a
breakpoint on `editor.thor` to a clean exit. The Run check now removes any old
marker before executing the editor command, so a preceding CLI run cannot make
it pass accidentally.

Repeated extension-test hosts use disposable extension state while retaining
profile settings. Their leftover generated palette was reset before the final
run, allowing enable/restore to be tested in one host that owned those changes;
this did not change the production extension or overwrite user customizations.

The initial VM failures came from its desktop provisioning: NetworkManager had
not started, an unused networkd wait-online service was blocking startup, and
the cloud-created home lacked the stock shell startup files. The clean baseline
now has working DHCP/DNS, stock shell profiles and an Xorg session for VMware
clipboard integration. A stale `/usr/bin/fwupdmgr refresh` crash report predated
Thor installation; it was archived locally without disabling crash reporting.

After validation, the pre-install snapshot was restored. The complete package
list and shell-profile hashes matched the baseline exactly; Clang, VS Code,
Thor's installation/discovery directories, VS Code settings/extensions and the
test projects were absent. The source snapshot, checked bundle and
`~/Thor/UBUNTU-SETUP.txt` remain for a manual retry. Local evidence is retained
outside the guest disk under the VM's `setup/` directory, including
`desktop-gui-result.json`, `verify-installed-thor.log` and
`verify-restored-baseline.log`.

## macOS validation

A second native ARM64 installation used an isolated home directory and default
`Library/Application Support/Thor` prefix, with only system tools on PATH.
Installation from the local bundle, cache preparation and the build/run check
passed in approximately 12.6 seconds, excluding prerequisites/network download.
The installed command symlink resolved its own versioned data rather than the
development checkout. A debug build linked the packaged compiler-rt runtime and
an intentional ASan failure reported the original `.thor` source without Python
or a developer LLVM installation on PATH.

Regression tests cover private SDK selection with global Odin blocked, symlink
discovery, spaces in toolchain paths, preservation/merging of user linker flags,
checked and unchecked ASan execution, native symbolizer protocol/mapping, archive
and checksum refusals, failure recovery, profile preservation, localhost release
downloads, PATH setup, and optional colors.

## Regression verdicts

| Check | Result |
| --- | --- |
| Maintained native installer/CLI/relocation/symbolizer/memory selection | 64 passed, 1 Windows-only skip |
| VS Code Node tests | 179 passed |
| Native installer Windows path-normalization unit | 1 passed |
| Assertion hygiene | 7 passed |
| Frozen Python reference fingerprint | Unchanged |

The 65-method native selection includes 22 installer cases and uses
`tools/test_compiler.py`'s native-only guard. No historical Python compiler
provides its program verdicts. The full language suite was not rerun for this
installation work; affected native paths and real installed applications were.

## Packaging fixes found by these tests

- Main build/test/run paths now select the private Odin installation, alongside
  informational/query commands; explicit SDK overrides retain precedence.
- macOS command symlinks are resolved before adjacent-data discovery. Windows'
  existing compiler path spelling is preserved.
- macOS compiler-rt resources accompany the reviewed LLVM-backed Odin compiler.
  Sanitizer links select them without requiring Homebrew Clang on PATH, while
  preserving explicit resource/linker choices and existing linker flag text.
  Ordinary builds receive no sanitizer flags or generated runtime changes.
- The former Python ASan adapter is native; map decoding is bounded and only
  reads the dedicated Mach-O section. Original source/function mapping remains.
- Windows installer canonical paths discard extended-path spelling before
  writing Odin-facing paths. The normalization is unit-tested locally.
- Unsupported installation-path punctuation is rejected before writes, matching
  Odin's output-path restrictions. Spaces/Unicode remain supported.

An optional native `-Wl,-dead_strip` linker flag can discard embedded standalone
ASan mapping metadata; ordinary installer/debug flags do not use it. Handling of
that explicit native linker behavior was not changed by this work. VS Code uses
its persisted source maps independently.

## Repository and release state

The obsolete upstream OLS VS Code client and nested OLS release automation were
removed (32 tracked files); the maintained client is `editors/vscode`. Existing
compiler work, fixtures, historical evidence and the frozen Python reference were
preserved. Generated bundles/targets/VSIX files remain ignored. Installation and
publishing documentation now distinguish native prerequisites from historical
Python dependencies.

Thor-owned code uses 0BSD; Odin-derived and OLS code retain original zlib/MIT
notices. Release bundles preserve SDK/native-library notices and include a Rust
dependency license inventory. LLVM runtime libraries are relocated inside the
bundle rather than depending on builder paths.

Windows launchers and installer branches are included, but this installer has
not received a native Windows end-to-end run. No Windows host was contacted in
this task. Public release hosting, release signing/notarization and GitHub upload
remain separate future steps. Until artifacts are hosted, use `--bundle FILE` or
an explicit release-base URL; the source clone cannot download a nonexistent
release.
