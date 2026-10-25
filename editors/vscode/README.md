# Thor Language for VS Code

Local Thor T01-T23 syntax highlighting and a client for the TLS fork in this
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
command, then run **Developer: Reload Window** after an update to load the new
grammar and theme mappings. Restarting TLS only reloads the server. The grammar
works independently of TLS. No Marketplace account or publication is needed.

The client defaults to `tls/bin/tls` inside the workspace. To use this fork from
another workspace, configure `tls.server.path` with the absolute executable path.
`tls.odinRoot` can point to an Odin installation when it cannot be discovered.
The compiler launcher defaults to `thor` beside the TLS checkout. Set
`tls.compiler.path` to its absolute path if your compiler lives elsewhere.
Use **Thor: Restart Language Server** after rebuilding the executable.

TLS analyzes original source positions and supports the Thor parser extensions,
file-local selected import aliases, inferred-array types, and sequence membership.
It also parses quoted/raw interpolation, runtime/compile-time switches and Set
types/literals; fields and arms retain completion/hover/definition positions.
The grammar highlights embedded expressions independently from string text,
including nested quotes and braces. Negative indices use native expression scopes.
Explicit interpolation fills use Odin rune literals, e.g. `{"é😀":'界'^5}` or
`{1234567:'0'>12,}`. Rune fills and numeric widths keep their ordinary literal
colors; alignment and grouping punctuation stay neutral. Bare fills are rejected.
TLS normalizes semantic token ranges before publishing them, preserving string
and comment colors even around adapted switches and nested interpolation.
Package names use variable-blue theme scopes, while `set[T]` uses the same keyword
scope family as `map`. String scopes cover literal text and quotes; interpolation
expressions, their prefixes and surrounding aggregate braces retain code metadata
for bracket matching and coloring. Unaliased `import "thor:sets"` exposes `sets`;
the analogous defaults are `format` and `pure`. Explicit aliases remain supported.
Some contextual expected-type navigation remains incomplete: `.Red` in a
switch-arm array can highlight correctly while lacking hover/definition.
`fn` declarations and `#run` calls have highlighting and snippets. TLS hover
explains declared purity and infers call results without executing procedures;
the live compiler evaluates explicit and known-argument calls, including generic-dependent constants, and maps purity/evaluation failures.
Live compiler diagnostics use source maps to report errors against original Thor
files, including unsaved edits and imported buffers. Errors clear after a fix;
generated helper names are replaced with messages about the Thor operation.
Formatting, automatic import edits, and rename are disabled until their edits can
preserve Thor syntax. See `tls/THOR.md` at the repository root for details.
