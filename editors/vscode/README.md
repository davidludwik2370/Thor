# Thor Language for VS Code

Local Thor T01-T12 syntax highlighting and a client for the TLS fork in this
repository. `.thor` files have their own language identity; existing Odin support
continues to own `.odin` files.

From the repository root:

```sh
python3 tools/build_tls.py
cd editors/vscode
npm ci
npm test
npm run package
```

Install `thor-language.vsix` with VS Code's **Extensions: Install from VSIX**
command, then reload the window if requested. The grammar works independently
of TLS. No Marketplace account or publication is needed.

The client defaults to `tls/bin/tls` inside the workspace. To use this fork from
another workspace, configure `tls.server.path` with the absolute executable path.
`tls.odinRoot` can point to an Odin installation when it cannot be discovered.
The compiler launcher defaults to `thor` beside the TLS checkout. Set
`tls.compiler.path` to its absolute path if your compiler lives elsewhere.
Use **Thor: Restart Language Server** after rebuilding the executable.

TLS analyzes original source positions and supports the Thor parser extensions,
file-local selected import aliases, inferred-array types, and sequence membership.
Live compiler diagnostics use source maps to report errors against original Thor
files, including unsaved edits and imported buffers. Errors clear after a fix;
generated helper names are replaced with messages about the Thor operation.
Formatting, automatic import edits, and rename are disabled until their edits can
preserve Thor syntax. See `tls/THOR.md` at the repository root for details.
