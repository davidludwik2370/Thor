# Thor installation

See [the installation guide](docs/installing.md) for the per-user installer,
VS Code profiles/colors, prerequisites, release packaging and clean Ubuntu tests.

From a clone with published release artifacts:

```sh
./thor-install --vscode --profile Thor --path
```

Before publishing, provide `--bundle FILE`.
Cloning provides installer scripts, not generated executables or a hosted release.
Windows uses `thor-install.cmd` or `thor-install.ps1`.

## Development checkout

Build `rust/Cargo.toml` and `install/Cargo.toml` with `cargo build --release
--locked --manifest-path PATH -j 2`. Prepare runtime output with
`python3 tools/build_runtime.py` and TLS with `python3 tools/build_tls.py`.
The root `thor`/`thor-rust` launchers execute the prebuilt compiler. Native
installations ship the executable directly as `thor`. Neither falls back to
Python. `thor-python` and `thorlang/` are frozen historical material; maintained
build scripts and the regression harness still use Python.

Projects own `.thor-cache/`, `.thor-rust-build/`, executables and persisted debug
maps. Toolchain parser caches/shared runtime output belong to the installation.
Ordinary builds keep source mappings in memory; `-debug` and explicit
transpilation persist debugger/inspection maps.

`thor new name` creates source and task/launch files without local TLS settings.
The personal `mkthor` wrapper uses `~/Code/Thor`; that directory preference is not
installed for other users.
