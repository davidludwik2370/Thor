# Thor Language for VS Code

Local Thor T01-T23 syntax highlighting and a client for the TLS fork in this
repository. `.thor` files have their own language identity; existing Odin support
continues to own `.odin` files.

Thor supplies a yellow T file icon with a transparent background through the
same language-icon contribution Odin uses. Explorer rows and editor tabs keep
the editor's background visible around the glyph. Dark themes use bright yellow;
light themes use a darker gold from the same palette. No icon-theme switch is
required. Themes with their own explicit Thor mapping can override the language
icon. The extension listing uses the white T on gold app emblem.

The final brand exports and editable SVG masters live in
[`assets/branding`](../../assets/branding/README.md). Reload the VS Code window
after installing an extension update to refresh icons; restarting TLS does not
reload extension contributions.

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

The client discovers the toolchain in `THOR_ROOT`, the current toolchain checkout,
or `~/Thor`. Ordinary projects use that shared TLS installation without a local
server copy. Set `tls.server.path` to select another executable explicitly.
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

## Running and debugging

The extension provides **Thor: Run Project** on **Cmd+R**. It saves edits, shows
the active integrated terminal, and sends `thor run .` followed by Enter. It
uses that terminal's current directory and shell environment, without scanning
or launching tasks. If no live terminal exists, it creates a regular terminal
in the project folder or the saved file's directory. Repeated runs reuse it.
It also provides **Thor: Debug Project**, bound to **Cmd+Shift+R** in
Thor editors on macOS, and a `thor-lldb` debug adapter using ordinary CodeLLDB.
Run `thor new my_project` to create a local folder containing `my_project.thor`
and only VS Code task/launch files. `mkthor my_project` creates the same layout
in `~/Code/Thor` and opens VS Code. TLS needs no generated project settings.
Breakpoints, stepping and stack locations map back to the original Thor source.
This uses DAP separately from TLS's LSP connection. See the
[debugger guide](DEBUGGING.md) for setup, architecture and supported behavior.

User keyboard bindings take precedence over extension defaults. If another
extension such as Code Runner already owns Cmd+R in your user keybindings,
append this Thor-only override to **Preferences: Open Keyboard Shortcuts (JSON)**:

```json
{
    "key": "cmd+r",
    "command": "thor.run",
    "when": "editorLangId == thor && !inDebugMode"
}
```

Other languages keep their existing run shortcut. Reload VS Code after installing
an extension update; restarting TLS does not reload client commands.

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

Comments are explicitly excluded from bracket pairing/colorization, including
nested block comments and comments inside interpolated expressions. Code brackets
remain active when the surrounding code is uncommented. After an extension
update, use **Developer: Reload Window** to refresh this grammar metadata;
restarting TLS alone does not reload it.

If brackets occasionally retain their code colors after Cmd+/ comments a block,
use **Thor: Refresh Bracket Highlighting**. This rebuilds VS Code's bracket cache
without reloading the window, restarting TLS, editing the buffer, or changing
your settings. The extension also requests this refresh automatically after
comment-delimiter edits settle. Ordinary typing does not request a refresh.
This is a defensive workaround for intermittent stale editor state; the exact
trigger has not yet been reproduced in a clean editor profile.

Literal string/rune brackets are also explicitly excluded. In
`print(f"fib ({i}): {a:,}")`, the parentheses around `{i}` are orange string text;
the interpolation braces and the actual procedure-call parentheses remain code
brackets. This applies to quoted/raw strings, nested interpolation, and `f!`.

Typed numeric ranges (`for number: f64 in 1..=COUNT`) and grouped array/slice
indices (`for value, (index: i8) in values`) have original-source hover,
definition, rename, readonly semantic tokens, and compiler diagnostics. The
element's inferred type and explicit `&value` mutability are preserved.
The `fort` and `forindex` snippets insert these forms. Debugging retains the
visible binding names/types and hides the internal native index counter.

### Reversible constant-color preview

The original theme colors remain the default. To try a different color for Thor
compile-time value constants, use **Thor: Preview Compile-Time Constant Color**
in the Command Palette. Press Enter to try the blue-violet `#8582DE`, or enter
another `#RRGGBB` color. Run the same command to tweak it again.

**Thor: Restore Previous Constant Color** restores the pre-preview rule. The
baseline persists across editor reloads and repeated previews, so it returns to
the color from before the first preview, rather than the last tweak. Other
changes to your settings made during the preview are retained.

This changes only the user-level semantic-color selector
`variable.thorCompileTime:thor`. The existing theme and mutable/runtime-immutable
variable colors are preserved, as are parameter, type, procedure, enum-member,
keyword, string and number-literal colors. The preview applies across Thor
projects in the current VS Code profile. With no explicit previous rule,
restoring removes the override and follows the theme again. Changes take effect
immediately; after installing the updated extension, reload the window once.

TLS supplies the additive `thorCompileTime` semantic modifier for actual
compile-time value declarations and references, including imported values,
aliases and `#run` results. Runtime `:>` declarations and loop bindings retain
their runtime classification. Ordinary procedure/function parameters (including
pointer parameters) and plain `for value, index in container` bindings use the
same readonly blue as immutable variables, on declarations and references.
`for &value, index in container` keeps `value` light blue and `index` readonly;
ordinary mutable loop counters and named return variables remain light blue.
Type-switch bindings follow the same convention: `switch value in union_value`
uses readonly blue, while `switch &value in union_value` uses mutable blue.
Each case retains its narrowed type and corresponding struct-member mutability;
invalid assignments keep the binding's readonly color as well as their diagnostic.
The color follows the binding, not the mutability of pointed-to data. Other
themes can style the modifier directly;
the preview is optional and does not replace a theme.

### Label colors

Labels use their own `label` semantic token category and
`entity.name.label.thor` grammar scope. Declarations on the same line as a loop
or on the preceding line match their `break`, `continue`, `or_break` and
`or_continue` references. The colon stays ordinary punctuation. Labeled blocks,
conditionals and switches follow the same rule.

The local purple preview uses these user settings (merge with existing rules):

```json
"editor.semanticTokenColorCustomizations": {
  "rules": {
    "label:thor": "#8582DE"
  }
},
"editor.tokenColorCustomizations": {
  "textMateRules": [
    {
      "name": "Thor labels (purple preview)",
      "scope": "entity.name.label.thor",
      "settings": { "foreground": "#8582DE" }
    }
  ]
}
```

The semantic rule colors resolved labels; the TextMate rule keeps the syntax
fallback consistent while TLS starts or is disabled. Change/remove these two
label-specific rules to adjust/restore labels independently of compile-time
constants and variables. The constant-color preview commands do not change them.

### Readonly binding colors

If readonly parameters still have the mutable-variable color in your theme,
you can set their color explicitly in VS Code's user settings. For Dark Modern,
`#4FC1FF` matches its immutable-variable blue:

```json
"editor.semanticTokenColorCustomizations": {
  "rules": {
    "parameter.readonly:thor": "#4FC1FF"
  }
}
```

Merge this selector into any existing `rules` object, preserving other rules
such as the compile-time constant preview. It applies to parameter declarations
and references, including pointer parameters and defaulted arguments, across
Thor projects in the current profile. Named argument labels are separate tokens;
this setting does not change them. Settings changes apply immediately. Remove
the selector to return to the theme's fallback, or adjust it when changing themes.

Struct member accesses also carry `property.readonly` when they refer to
immutable value storage. This includes value parameters, `:>` structs, nested
structs, fixed-array elements, and value-loop bindings. A pointer field itself
can be readonly while fields of its pointee remain mutable. Slice/dynamic-array
elements and reference-loop bindings likewise retain mutable member colors.
Struct field declarations stay neutral: the same type can have both mutable and
immutable instances. A mutable copy (`copy := frozen`) and its fields use the
mutable color even when the original instance is immutable.
To explicitly match Dark Modern's immutable blue, add
`"property.readonly:thor": "#4FC1FF"` alongside the parameter rule above.

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

Selected names retain their resolved symbol colors, including when unused:
`print` in `import "core:fmt" / print` is function-colored and faded. If only
some selected names are unused, those names fade individually while the used
names stay bright. A qualified call such as `fmt.print()` uses the namespace,
so it does not keep an unused selected `print` bright. Multiline selections and
Unicode names use the original source ranges, including at newline/EOF.
Hover over a selected name in the import list to see the same signature, type
and documentation as a reference in code, even when that import is unused.

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
`f!"..."` and raw `f!` backtick literals use the same string/embedded-expression
scopes as ordinary f-strings. Their `!` is part of the lifetime prefix. Default
anonymous f-string arguments are freed after the enclosing expression; f! opts
out, while named bindings retain explicit cleanup.
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
Inline `proc` literals use the same keyword color as procedure declarations.
Callback parameters and mutable procedure values use value colors when passed
or stored; direct calls use function colors, including parenthesized callees.
The declared name in `package main` uses the usual namespace declaration scope
(green in Dark+). Imported namespace names and references use variable-blue theme
scopes, while `set[T]` uses the same keyword
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
Invalid sequence indices and lower/upper slice bounds identify their original
expressions and concrete types. Unresolved index operands keep their primary
name/field error without generated inference cascades. Result mismatches quote
original expressions such as `values[i]`. Type-as-array errors link to the type
declaration; inline membership errors link to the needle and explain why its
type constrains list elements. These notes are clickable related locations in
the Problems panel and diagnostic popup, including imported/unsaved files.
User procedures named `type_of`, `len`, and other native names retain hover and
rename support; generated builtin queries use an isolated native namespace when
needed. Extension 0.17.1 also keeps user `type_of` calls' runtime argument
breakpoints distinct from unevaluated native type queries. Reload VS Code after
installing that adapter update; subsequent compiler/TLS updates need only
**Thor: Restart Language Server**.
Use **Rename Symbol** in the context menu or **F2** on an identifier. TLS supports
local/global declarations, generic and procedure parameters, resolvable members,
selective imports, and file-local import aliases. It returns edits to original
Thor/Odin workspace sources, including unsaved buffers. Import paths, comments,
string text, and generated sources are preserved. Ambiguous or unresolved uses
and detected name conflicts cancel the rename. References outside the workspace
are not searched; SDK declarations and package declarations cannot be renamed.
The caret may be inside an identifier or immediately after it, including
one-letter generic parameters and identifiers after Unicode text.
After rebuilding TLS, run **Thor: Restart Language Server** to refresh the
advertised capabilities; an extension reinstall is unnecessary for this change.

Formatting and automatic import edits remain disabled until their edits can
preserve Thor syntax. See [the TLS implementation notes](../../tls/THOR.md#rename-symbol)
for details and current resolver limitations.
