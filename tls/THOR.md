# TLS: the local Thor fork of OLS

Upstream: https://github.com/DanielGavin/ols

Pinned base: `0c9438faeba846232caaa22fe9f22b30c302dba2` (2026-10-10).
The upstream MIT license is retained. TLS sources are now vendored in Thor's
root Git repository so compiler/editor batches can be committed together. The
original local branch/history is preserved in this workspace at `.git/tls-upstream`
(see `DEVELOPMENT.md`). No remote GitHub fork was created.

This base builds with the installed Odin `dev-2026-10:8169255b9`. More recent
upstream commits require `core:odin/tool-format`, absent from this installation.
Refreshing upstream is a deliberate future maintenance step.

## Build and test

From the Thor repository root:

```sh
python3 tools/build_tls.py
python3 tools/build_tls.py check
python3 tools/build_tls.py test
python3 -m unittest discover -s tests -p test_tls.py -v
```

The executable is `tls/bin/tls` (`tls.exe` on Windows). `THOR_ODIN` or `--odin`
selects the compiler. Additional Odin flags are forwarded. TLS uses the same
parser and owned lexer as the transpiler through `-collection:thor=frontend`; it does not
have a second independently modified parser. Server symbols and diagnostics use
the original source text, including unsaved document edits.

The native suite retains upstream's regression tests and adds Thor hover,
completion, privacy, inference, conditional-compilation, and incomplete-code
tests. `tests/test_tls.py` also launches the real executable and checks stdio LSP
messages, file discovery, semantic token ranges, unsaved changes, and diagnostics.

## Current support

- Parse `fn` and `#run` through the shared frontend. Hover retains the `fn`
  spelling and explains purity; call results, including concrete generic results,
  retain type inference and completion through `#run`. Live compiler checks
  enforce purity/evaluation rules against unsaved source and dependencies.
- Automatic calls retain normal return-type inference, completion and semantic
  token positions. Hover explains known-argument folding and constant/runtime
  ownership. Live compiler checks schedule automatic constants and generic-dependent
  evaluations, map failures against unsaved/imported text, and clear after fixes.
- Infer allocated string/slice and record results through `#run`; pure-function
  hover documents independent result ownership and `pure.destroy` cleanup.
  Unsaved imported contents invalidate cached evaluations. Slice-containing
  constant results report their runtime-binding requirement at the original
  `#run` expression and clear after correction.
- Discover mixed `.thor`/`.odin` packages and dependencies.
- Parse T13–T17 interpolation, switch expressions, compile-time switches and Set
  syntax. Infer interpolation's string type, runtime switch result types and
  instantiated Set types; preserve definitions and semantic tokens inside fields
  and arms. Display Set types as `set[T]`. Compile-time switch adaptation reuses
  OLS's native `when` scope and constant-selection machinery. Negative indices
  use the original native AST and normal element-type inference.
- Preserve native `~`/`~=` xor and pointer assignments/calls through the shared
  parser. Parse direct Set literals in range headers. Live compiler checks also
  report temporary-view lifetime errors at original unsaved source ranges.
- Parse selective imports, multiline ternaries, `#if` / `#else if` / `#else`,
  bracket array literals, and membership in generic constraints.
- Present the actual `thor:sets.Set` as `set[T]` throughout hover, signature
  help and completion type labels, including pointers/containers, nested generic
  arguments and imported procedures. Temporary display ASTs preserve indexed
  metadata and navigation. User-defined Set types and distinct aliases retain
  identity; declared type annotations keep their names. Thor hover code blocks
  use the Thor grammar, while native Odin buffers keep native presentation.
- Distinguish record-valued constants and value aliases from type aliases,
  including explicit #run and automatic fn results. Generic type construction
  and type_of aliases retain type classification. Constant hover prints the
  source #run syntax rather than its synthetic parser wrapper.
- Keep generic loop elements and other value bindings variable-colored rather
  than inheriting their type parameter's semantic classification. Hover and
  completion describe an element as `v: T`, preserving actual `$T` parameters
  and generic type aliases as types.
- Parse right-associated power above unary signs and multiplication, preserving
  native prefix expansion. Infer numeric results with literal coercion; fold
  representable integer powers for branch selection. Compiler errors retain
  original operation spans, including unsaved mixed-type expressions.
- Parse interval membership with half-open/inclusive bounds, parentheses and
  arithmetic bounds, preserving native iteration and container precedence. Infer
  boolean results; fold integer interval conditions (including signed bounds)
  in the server's partial branch selector. Compiler checks handle the complete
  ordered-type rules and generic-constraint failures against unsaved buffers.
- Parse inferred/typed immutable runtime bindings. Hover explains immutable
  storage, semantic tokens mark declarations/uses readonly, and shadowed mutable
  bindings retain their ordinary behavior. Compiler checks enforce shallow
  storage protection, including generic instantiations and implicit addresses.
- Discover `thor:format` and `thor:sets` by default beside the compiler launcher.
  Explicit collection configuration takes precedence.
- Resolve selected import aliases with file privacy and local shadowing. Hover
  retains the imported declaration's signature; definitions follow the origin.
- Report unused source imports as `Hint` diagnostics tagged `Unnecessary`, so
  VS Code dims them. The client enables reporting on open/change/save, including
  unsaved edits. Usage follows the actual namespace or selected name, distinguishing
  aliases of the same package and local shadowing. Synthetic selected aliases and
  implicit `set[T]` imports do not count as explicit source uses. Blank imports are
  intentional; syntax-incomplete buffers and closed documents clear stale hints.
- Infer common bracket-array types (including nested arrays), indexed element
  types, and membership's boolean type. Explicit declaration types continue to
  supply contextual information through OLS.
- Select `#if`/native `when` branches using the server's scalar constant folder,
  including integer comparisons, inline-list membership, and `#config` defaults.
- Provide OLS completion, hover, definitions, references, document symbols,
  signature help, and semantic highlighting where the underlying resolver can
  determine the symbol/type. Serve parser diagnostics at Thor source locations.

Like OLS, this server has its own partial semantic resolver, not Odin's complete
type checker. Complex overloads, generic specialization, and some nested expected
type contexts remain limited. The conditional evaluator is also a subset of
Odin's constant evaluator; it does not execute arbitrary procedures. It does not
execute procedures to populate hover values or choose `#run`-dependent branches;
the compiler checker performs explicit and automatic compile-time execution.
One confirmed expected-type gap is `.Red` inside a contextual switch-arm array
such as `choice:[2]Color=switch x{case 1:[.Red,.Blue];case:[.Blue,.Red]}`:
highlighting and compilation work, but hover/definition do not resolve. The stdio
suite records this as an expected failure. See [the audit](../AUDIT.md).

Semantic tokens are collected in absolute original-source ranges, sorted and
deduplicated before encoding UTF-16 LSP deltas. Adapted switch traversal order and
synthetic nodes cannot shift later tokens onto strings, comments or partial names.

For Thor packages, the checker invokes `thor check -json-errors` with snapshots
of all open buffers. Thor lowers those buffers, checks the generated Odin, and
maps diagnostics back to original files and UTF-16 source ranges. Checks run on
open/change/save; superseded checks are cancelled and resolved diagnostics are
cleared. Generated support errors point back to their Thor operations. The live
parser continues to provide immediate syntax diagnostics without duplicating
compiler parser errors for open documents. Parser errors use exact token ranges,
including after Unicode text and at EOF. Closing the server terminates its active
check before removing the overlay; Thor cancellation also unwinds native helpers.

Compiler results retain the package/file check that produced them. If the native
compiler supplies no source location, TLS attaches the diagnostic to an open
source file in that checked package (or a source file discovered there), with a
note explaining the missing location. It never selects an unrelated open buffer
as the fallback. Internal zero-length Set imports are excluded from source
position lookup, so they cannot create package hovers on the first comment.
Thor supports inline default callbacks whose generic dependencies can be inferred
from their parameter types. The compiler lifts the callback into a named generic
helper, gives the parameter an explicit procedure type, and supplies omitted
callbacks at resolved calls, including across imported packages. An explicit
override does not specialize the default. TLS continues to use the original AST:
hover shows the original inline signature, navigation/rename retain the outer
generic bindings, and callback parameters retain value coloring. Cursor lookup
collects enclosing generic parameters even when a callback is nested in a
signature rather than in the outer body.

Errors inside generated callback helpers map to the original body tokens,
including unsaved/imported Unicode sources. Unsupported result/body-only generic
dependencies and runtime captures have mapped diagnostics at their original
signature or capture. Native `.odin` declarations retain their unsupported-default
guard. Native conditional compilation and enclosing generic specialization still
control whether guards and helper bodies are checked. This uses the compiler's
checker rather than adding a second TLS type checker.

Unconditional top-level redeclarations in an untagged file also have an immediate
`Declaration` diagnostic, independent of native checking. The shared frontend
performs the same check before evaluating `fn`/`#run` calls in the CLI. Duplicate
backend reports are suppressed for open buffers, and fixing or closing the buffer
clears the early diagnostic. Conditional declarations, tagged files, local scopes
and cross-file conflicts remain with the backend checker.

Set `thor_command` in `tls.json` or initialization options to the absolute Thor
launcher path. The VS Code extension supplies this automatically from the local
checkout, or from `tls.compiler.path`; `odin_command` selects the backend. TLS
stores generated sources, source maps, and overlay files in its temporary checker
directory. Source files are never saved by checking.

### Rename Symbol

TLS advertises rename with `prepareRename` support. Thor rename resolves the
selected identifier to its declaration, then validates every edit against the
original source. It uses unsaved open buffers in both the package index and
reference search. Local variables, immutable bindings, generic parameters,
procedure parameters (including named arguments), and resolvable record/enum
members are supported. Global names are searched across `.thor` and `.odin`
sources in the configured workspace, including selective import lists. Strings,
comments and generated output directories are excluded.

Import namespaces are file-local: renaming `fmt` from `import "core:fmt"` adds
an explicit alias and updates its uses, leaving the path and other files alone.
Declarations outside the editable workspace, such as SDK procedures, cannot be
renamed from an imported use. Renaming a package declaration itself is not
supported.

Rename rejects invalid identifiers, detected name collisions, and unresolved
same-name uses in related files rather than returning known partial edits. This
includes the contextual `.Red` in switch-arm arrays mentioned above: that member
rename is refused until the resolver can locate every use. Resolve related syntax
errors before a global rename. The search is limited to workspace/open sources;
references in other projects are outside its scope. References/highlights retain
their existing OLS limitations independently of rename.

Formatting, automatic imports, and Thor code actions remain disabled because
their edits still need to preserve Thor syntax.

TLS reads `tls.json` instead of `ols.json`, using the existing OLS configuration
schema. `OLS_BUILTIN_FOLDER` remains supported for the upstream builtin index.
The extension sets this automatically to `tls/builtin`. Profiles and collection
configuration can be provided in `tls.json` when needed.

## VS Code

The extension's [startup and communication guide](../editors/vscode/README.md#how-vs-code-launches-tls)
explains manifest activation, the local server process, LSP over standard streams,
grammar versus semantic highlighting, compiler checks, and installation updates.

The separate extension lives in `../editors/vscode`. Build its local VSIX, install
it, and open a `.thor` file. The supplied workspace settings point to this local
binary. A **Thor: Restart Language Server** command reloads a rebuilt executable.
Existing Odin extensions continue to handle `.odin` files. TLS still indexes those
files when they belong to a Thor package or an imported package.

`python3 tools/test_vscode.py` runs an isolated Extension Development Host with a
temporary profile. It checks language registration, actual server hover and
definitions, live compiler errors on unsaved text, Unicode ranges, error clearing,
restart, and preservation of the Odin file association. It requires a desktop
VS Code installation; `--code` selects its CLI.
