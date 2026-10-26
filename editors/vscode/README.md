# Thor Language for VS Code

Local Thor T01-T23 syntax highlighting and a client for the TLS fork in this
repository. `.thor` files have their own language identity; existing Odin support
continues to own `.odin` files.

## Local installation and updates

The setup in this repository uses a locally built VSIX and a local TLS executable.
The extension is installed into your VS Code installation; its source and the
server's source are version-controlled here. Someone with another checkout can
build and install both on their own computer. This installation does not depend
on a Marketplace listing or a remotely hosted language server.

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

## How VS Code launches TLS

TLS means Thor Language Server. It is an Odin program forked from OLS, the Odin
Language Server. The VS Code extension is a separate JavaScript client which
starts that program and connects its language features to the editor UI.

The startup sequence is registered by the installed extension:

1. [package.json](package.json) declares `.thor` in `contributes.languages`,
   associating that extension with the language ID `thor`. It also registers the
   grammar, snippets and editor configuration.
2. Its `activationEvents` includes `onLanguage:thor`, and `main` points to
   `./extension.js`. Opening the first Thor document therefore causes VS Code to
   load the extension and call its `activate()` function.
3. [extension.js](extension.js) selects a workspace folder and reads the `tls.*`
   settings. In a trusted workspace, with TLS enabled and the executable present,
   it creates a `LanguageClient` using `tls.server.path`. Its document selector
   is `{ scheme: 'file', language: 'thor' }`.
4. The `vscode-languageclient` library launches the TLS executable as a separate
   local process, connects its standard input/output, and performs the Language
   Server Protocol (LSP) initialization handshake. It manages message framing,
   requests, responses and editor events for the extension.

LSP messages are JSON-RPC messages. For a hover, VS Code sends the document URI
and cursor position; TLS resolves the symbol and responds with its signature or
type, which VS Code displays in a popup. Completions, definitions, signature help
and semantic tokens follow the same request/response model. Diagnostics arrive
as notifications and become squiggles and entries in the Problems panel.

The extension maintains one language client and one TLS process per VS Code
window, shared by its open Thor documents. It does not start a new server for
each file. Closing a document leaves the server running. Deactivating the
extension or closing the window stops it; the restart command replaces it.
Changes to `tls.*` configuration also trigger a restart. The current client
selects one workspace folder at startup rather than creating a server per folder
in a multi-root workspace.

Your workspace's `.vscode/settings.json` supplies settings such as the executable
location; the installed extension's manifest and startup code supply the launch
behavior. The server communicates through local process streams, with no network
port or remote service required. If TLS cannot start, check the **Thor Language
Server** channel in VS Code's Output panel.

## Debugging

The extension also provides **Thor: Debug Project**, bound to **Cmd+Shift+R** in
Thor editors on macOS, and a `thor-lldb` debug adapter using ordinary CodeLLDB.
Run `mkthor my_project` to create a project with matching build and launch files.
Breakpoints, stepping and stack locations map back to the original Thor source.
This uses DAP separately from TLS's LSP connection. See the
[debugger guide](DEBUGGING.md) for setup, architecture and supported behavior.

## Highlighting and compiler diagnostics

There are two highlighting layers:

- The extension's TextMate grammar recognizes strings, comments, numbers,
  keywords and punctuation. It works without the TLS process.
- TLS supplies semantic tokens for resolved variables, types, procedures and
  packages. VS Code combines these with the grammar and theme; semantic colors
  can override the grammar's initial colors.

The popup text, completion labels and symbol classification come from TLS. For
example, showing a Set as `set[string]` in a hover is a server presentation rule.
Recognizing string text inside nested fstrings also requires correct grammar
rules. A change may therefore belong in the server, the grammar, or both.

VS Code sends open/change/save events and unsaved document contents to TLS. TLS
and the transpiler share the Thor parser, keeping their accepted syntax aligned.
TLS uses its own partial semantic resolver for interactive features; the Thor
compiler and Odin perform the complete checks.

TLS marks unused imports with an LSP `Unnecessary` hint. VS Code dims the full
declaration using the current theme's unused-code styling (`editor.showUnused`
must be enabled, as it is by default). These hints update on open, unsaved edits
and save, independently of compiler checks. Explicit aliases, selective imports,
local shadowing and imports used inside interpolation are recognized. Intentional
blank imports (`import _ "..."`) are kept. Internal imports added for `set[T]`
never appear as unused source declarations.

Syntax errors and some obvious redeclarations can be reported immediately. For
deeper checks, TLS invokes `thor check -json-errors` using snapshots of the open
buffers. Thor performs compile-time evaluation, lowers the source, checks it with
Odin, and maps diagnostics back to the original Thor files. Those checks can take
longer than interactive hover or syntax analysis. Superseded checks are cancelled
and resolved errors are cleared. Unsaved buffers are checked without saving them
over your source files. TLS itself does not execute functions for hover values;
compile-time execution belongs to the compiler checker.

After a server change, rebuild TLS and run **Thor: Restart Language Server**.
After an extension or grammar change, rebuild/reinstall the VSIX and run
**Developer: Reload Window**. Restarting only TLS does not reload the installed
grammar. The compiler stages and flag handling are documented in the root
[README](../../README.md#compilation-and-odin-flags).

## Language support

TLS analyzes original source positions and supports the Thor parser extensions,
file-local selected import aliases, inferred-array types, and sequence membership.
It also parses quoted/raw interpolation, runtime/compile-time switches and Set
types/literals; fields and arms retain completion/hover/definition positions.
The grammar highlights embedded expressions independently from string text,
including nested quotes and braces. Negative indices use native expression scopes.
Explicit interpolation fills use Odin rune literals, e.g. `{"é😀":'界'^5}` or
`{1234567:'0'>12,}`. Rune fills and numeric widths keep their ordinary literal
colors; alignment and grouping punctuation stay neutral. Bare fills are rejected.
Compile-time `#if`, `#else`, `#switch` and `#run` use directive scopes, matching
`#assert` and `#partial`. The entire `#else if` branch introducer has that styling,
including when comments or line breaks separate the words. Ordinary runtime
`if`, `else` and `switch` retain their normal control-flow scopes.
Range membership (`value in low..<high`, inclusive `..=`, and `not_in`) uses the
shared parser, retains boolean inference and original token positions, and is
checked in generic constraints by the compiler. TLS's limited branch folder also
recognizes integer interval conditions; its existing scalar-evaluation limits
remain for other constant expressions.
Infix `**` uses the shared power precedence and numeric result inference; prefix
`**` retains expansion. Integer constant powers participate in TLS branch
selection, with overflow left unresolved. Compiler checks provide matching-type
and negative-exponent diagnostics against unsaved source.
Hover, signature help, and completion type labels display Thor's Set as `set[T]`,
including `^set[$T]` parameters, nested containers, return types, renamed imports,
and inferred library results. Detection follows the actual `thor:sets.Set` type;
unrelated `Set` types and distinct aliases retain their own identity. Declared
aliases are kept when available. Hover snippets in Thor files use Thor highlighting;
Odin files retain native type spelling and highlighting.
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
preserve Thor syntax. See [the TLS implementation notes](../../tls/THOR.md) for
details and current resolver limitations.
