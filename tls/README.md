# Thor Language Server

TLS is Thor's maintained Odin-language fork of
[OLS](https://github.com/DanielGavin/ols). It uses Thor's shared frontend and the
native compiler for diagnostics. The upstream MIT license is preserved in
[`LICENSE`](LICENSE); the fork's revision and capabilities are documented in
[`THOR.md`](THOR.md).

For a user installation, use the repository-root `thor-install --vscode` bootstrap;
see the [installation guide](../docs/installing.md). A native release includes the
server and its `builtin/` data, so no Odin or Python build step is necessary.

To build from a development checkout:

```sh
python3 tools/build_tls.py
```

Run this from the repository root. The maintained VS Code client lives in
[`../editors/vscode`](../editors/vscode/README.md). The inherited OLS VS Code
client and its obsolete release automation have been removed; they installed a
different extension and were not part of Thor's supported editor path.

Other editor integrations can launch `tls/bin/tls` with `OLS_BUILTIN_FOLDER`
pointing to `tls/builtin`. See [THOR.md](THOR.md) for LSP initialization options.
