# Shared Thor installation

The canonical installation and development checkout on this machine is
`/Users/davidludwik/Thor`. `/Users/davidludwik/Code/Odin/thor` is a compatibility
symlink so existing terminal commands and project configurations continue to
resolve. Git history, source changes, examples and playgrounds move with the
checkout; there is one physical copy.

## Installation and projects

The installation owns `thor`, `mkthor`, `thorlang/`, `frontend/`, `tls/`, library
sources under `runtime/`, and common library output under `lib/`. The frontend
binary and the toolchain's own parsing data live in its `.thor-cache/`.

Each project owns its `.thor-cache/` (parser packets, compile-time results and
dependency records), `.thor-build/` (generated application code), executable,
and debug maps. `--cache-dir` and `THOR_CACHE_DIR` can override the project cache
root explicitly. Ordinary project caching does not write into the toolchain's
parser cache.

The shell PATH includes `~/Thor`. New project setup is:

```sh
mkthor example --parent ~/Code/Thor --no-open
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

## Common libraries

Native Odin runtime packages are linked directly to their installed sources;
they are not copied or regenerated per project. Imported runtime test files are
excluded from the common view; the runtime's own native test commands still run
them normally.

`basic.thor` has a project-independent generic translation. Thor prepares it in
a versioned `lib/runtime-…-basic/` directory and reuses the same output across
projects. The version derives from compiler/frontend/library inputs, not user
project sources. Ordinary native `when` conditions and generic procedures remain
for Odin to specialize. The translation's common source map lives beside it.

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
`mkthor` writes build, launch and TLS settings pointing at the current physical
installation. Cmd+Shift+R starts CodeLLDB through the Thor debug adapter.
After an extension update, run **Developer: Reload Window**. Reopen the relocated
checkout at its canonical path when working on the toolchain itself.
