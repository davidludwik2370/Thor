# Shared Thor installation

The canonical installation and development checkout on this machine is
`/Users/davidludwik/Thor`. `/Users/davidludwik/Code/Odin/thor` is a compatibility
symlink so existing terminal commands and project configurations continue to
resolve. Git history, source changes, examples and playgrounds move with the
checkout; there is one physical copy.

## Installation and projects

The installation owns `thor`, `thor-rust`, `thor-python`, `mkthor`, `thorlang/`, `frontend/`, `tls/`, library
sources under `runtime/`, the editable starter under `templates/`, and common library output under `lib/`. The frontend
binary and the toolchain's own parsing data live in its `.thor-cache/`.

`thor` and `thor-rust` launch the release executable in
`rust/target/release/thor-rust`. Build it after checkout and rebuild after editing
Rust compiler sources.

```sh
cargo build --release --locked --manifest-path rust/Cargo.toml
```

The launchers run the prebuilt executable directly. `thor-python` selects the
Python reference compiler. The current native acceptance corpus is verified on
macOS arm64. See [migration status](docs/rust-migration.md).

Each project owns its `.thor-cache/` (parser packets, compile-time results and
dependency records), `.thor-rust-build/` (native generated application code), executable,
and debug maps. `--cache-dir` and `THOR_CACHE_DIR` can override the project cache
root explicitly. Ordinary project caching does not write into the toolchain's
parser cache. Python uses `.thor-build/` for generated application code.

The shell PATH includes `~/Thor`. New project setup is:

```sh
thor new example --parent ~/Code/Thor
cd ~/Code/Thor/example
thor build .
./example
```

Without `-out:`, `thor build` always writes the executable to the directory the
command runs in and uses that directory's name. `--output-dir` and a source
argument pointing elsewhere do not change this rule. Windows adds `.exe`.
An explicit `-out:` still selects another location/name. Building in the
toolchain root requires an explicit output name to protect the `thor` launcher,
including on a case-insensitive filesystem where the folder is named `Thor`.

`thor publish .` creates a local application package instead of a loose
executable: `.app` on macOS, `.zip` on Windows and `.tar.gz` on Linux. It discovers
`assets/icon.png` or `assets/icon.ico`, includes `assets/`, and accepts optional
`[publish]` settings in `thor.toml`. GUI launch mode is the default; use
`--app-type console` to request a terminal. PNG/ICO conversion uses the optional
Pillow package (`python3 -m pip install Pillow`) when using `thor-python`.
That reference compiler additionally needs `tomli` on Python 3.10 to read TOML.
The Rust publisher reads TOML and converts icons directly. See [publishing](docs/publishing.md) for native
tool requirements, extra resources, dependencies and resource lookup.

## Common libraries

Native Odin runtime packages are linked directly to their installed sources;
they are not copied or regenerated per project. Imported runtime test files are
excluded from the common view; the runtime's own native test commands still run
them normally.

The Python reference prepares a project-independent generic translation of
`basic.thor` in
a versioned `lib/runtime-…-basic/` directory and reuses the same output across
projects. The version derives from compiler/frontend/library inputs, not user
project sources. Ordinary native `when` conditions and generic procedures remain
for Odin to specialize. The translation's common source map lives beside it.

The native compiler links unchanged Odin runtime packages through a shared
`lib/runtime-rust-…/` view. Imported Thor library sources and required checked SDK
adaptations are lowered with the project into its generated tree.

Thor evaluation, configuration-dependent source selection, edited library
overlays and checked procedure variants are not treated as common output.
Necessary adapted packages live in the project's `.thor-runtime/` inside its
generated directory; other support packages remain shared. SDK checked variants
similarly remain in the project's sparse SDK overlay.

Prepare installation output after compiler/library changes with:

```sh
python3 tools/build_runtime.py
python3 tools/build_tls.py
```

Development builds can prepare missing common output lazily. Published library
directories are versioned and atomically installed, so different project builds
do not overwrite each other's common code. No complete project cache is used.

## Diagnostics, debugging and editor setup

All builds keep compact source mappings in memory for Thor compiler errors and
TLS checks. Ordinary `build`, `check`, `run` and `test` do not write map sidecars.
`-debug` persists project maps and refers to shared library maps through the
manifest. `thor transpile` also persists maps for explicit inspection. Temporary
compile-time helpers and type probes do not need disk maps, and a cached helper
result is checked before writing its temporary project.

VS Code discovers TLS from `THOR_ROOT`, a toolchain workspace, or `~/Thor`.
Explicit `tls.server.path` and `tls.compiler.path` settings remain supported.
`thor new name` creates `./name/name.thor` and only `.vscode/tasks.json` and
`.vscode/launch.json`. `mkthor name` delegates to `thor new` with parent
`~/Code/Thor` and `--open`; `--no-open` disables opening. The shared editable
starter is `templates/main.thor`; `--template` overrides it and `--gitignore`
adds optional build/cache ignore rules. Per-project TLS settings are unnecessary.
Cmd+R sends `thor run .` to the active integrated terminal, using its current
directory and shell environment, without a VS Code task. Cmd+Shift+R starts CodeLLDB
through the Thor debug adapter. Both save edits before compiling.
After an extension update, run **Developer: Reload Window**. Reopen the relocated
checkout at its canonical path when working on the toolchain itself.
