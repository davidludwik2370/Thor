# Thor

Thor is an experimental Odin superset that transpiles to ordinary Odin. This
milestone implements **T01–T06** from the reference specification in
[thor.odin](thor.odin). It includes the parser and project support needed to make
those features usable together.

## Run the demo

Requirements: Python 3.10+ and `odin` on `PATH`. The first invocation builds and
caches the small Odin frontend automatically; no Python packages are required.

```sh
./thor run demo
```

[demo/main.thor](demo/main.thor) has a labeled section for every completed task,
with assertions, ordinary Odin code, imported packages, and evaluation-order
examples. It finishes with `All T01-T06 demos passed.`

Select another compile-time branch with an ordinary Odin define:

```sh
./thor run demo -define:DEMO_MODE=1
./thor run demo -define:DEMO_MODE=3
```

## Commands

```sh
./thor transpile demo                  # Print the generated package path.
./thor check demo
./thor build demo                      # Produce .thor-build/demo/program.
./thor run demo
./thor test path/to/tests              # Run Odin @(test) procedures.
./thor run path/to/main.thor -- argument "two words"
./thor check demo --output-dir /tmp/thor-generated
```

## VS Code and TLS

The local [TLS fork](tls/THOR.md) and [Thor VS Code extension](editors/vscode/README.md)
support the current T01-T06 syntax. Build the server and extension from this repository:

```sh
python3 tools/build_tls.py
cd editors/vscode
npm ci
npm test
npm run package
```

In VS Code, run **Extensions: Install from VSIX** and select
`editors/vscode/thor-language.vsix`. Reopen a `.thor` file or reload the window.
The supplied `.vscode/settings.json` points to the local TLS executable. Use
**Thor: Restart Language Server** after rebuilding it. From another workspace,
set `tls.server.path` to the absolute path of `tls/bin/tls`.

The extension provides a Thor grammar, snippets, brackets/comments, and a TLS
client. TLS provides completion, hover, definitions, semantic highlighting,
and live parser/compiler diagnostics on original, unsaved Thor text. The client
finds the `thor` compiler launcher alongside the local TLS checkout; use
`tls.compiler.path` for another location. Rename, formatting, automatic imports,
and Thor code actions are disabled for this first editor milestone. Existing
Odin support continues to handle `.odin` files.

Additional editor checks:

```sh
python3 tools/build_tls.py test
python3 tools/test_vscode.py
```

The VS Code test uses a temporary editor profile. See [TLS's notes](tls/THOR.md)
for the pinned upstream revision, semantic-resolution limits, and configuration.

## Project inputs and output

Inputs can be a single `.thor`/`.odin` file or a package directory. Directory
inputs can mix both extensions. Relative imported packages and explicitly passed
`-collection:name=path` collections are mirrored into the generated tree. Odin's
native file suffixes and build tags select active native sources. Resource paths
in `#load`, `#load_hash`, `#load_directory`, and `#exists` remain relative to the
original source file, including in ordinary Odin dependencies.

Other Odin flags are forwarded. Use `--odin /path/to/odin`, or set `THOR_ODIN`, to
select a compiler. Arguments after `--` are forwarded by `run` and `test`.

Generated files live under `.thor-build/`; the frontend binary is cached under
`.thor-cache/`, keyed by its source and Odin version. Source files are never
overwritten. A manifest allows stale generated sources to be removed on rebuild;
Thor refuses to reuse an unrelated nonempty output directory.

## Compiler diagnostics

`check`, `build`, `run`, and `test` report errors against original `.thor` files
(or original `.odin` dependencies), with source snippets and underlines. Parser,
Thor validation, and Odin type-checking errors use the same display. For example,
`values := [1, "wrong"]` underlines `"wrong"` in your Thor source, even though
the generated array declaration is much longer.

Each generated `.odin` file has a `.odin.map.json` sidecar. These versioned maps
record exact copied byte ranges, source ranges for generated constructs, and
original source snapshots. Nested, reordered, and duplicated expressions retain
their origins. Compiler messages also translate embedded declaration/caller
locations and quoted lowered expressions. Errors inside membership helpers point
back to the operation that instantiated them. Empty-array and membership failures
have Thor-specific explanations; redundant inference errors are suppressed when
the underlying expression already has a diagnostic.

Useful options:

```sh
./thor check demo
./thor check tests/fixtures/array_type_mismatch --diagnostic-format=json
./thor check tests/fixtures/array_type_mismatch -terse-errors -error-pos-style:unix
./thor check tests/fixtures/array_type_mismatch --raw-diagnostics
```

JSON diagnostics go to stderr and use one-based UTF-16 line/column positions,
UTF-8 byte offsets, severity, stage, and message lines. `-json-errors` is an alias
for mapped JSON output. `--raw-diagnostics` exposes the backend output for
investigating transpiler problems. `--overlay <json>` accepts the editor overlay
format `{"Replace": {"original path": "buffer file path"}}`; checking uses those
buffers without saving or modifying source files.

TLS uses this same compiler pipeline on open/change/save, including unsaved
dependencies. It translates ranges to LSP coordinates, preserves the editor's URI
for symlinked files, clears resolved errors, and discards superseded checks.
Program/test stdout and stderr remain separate from compiler diagnostics.
Debugger mappings and runtime stack traces are outside this implementation.

## Implemented syntax

| Task | Behavior |
| --- | --- |
| T01 | CLI, source preservation, generated output, and compile/run/rejection harness. |
| T02 | `import "core:math" / sin, cos`; multiline lists, retained package namespace, file-local names. |
| T03 | Ternaries may continue before `:` without a backslash, including nesting and comments. |
| T04 | `#if`, `#else if`, and `#else` lower to `when`; native `when` remains supported. |
| T05 | `[1, 2, 3]` is an inferred fixed array; contextual types, empty typed arrays, nested arrays, arguments, returns, and assignments are supported. |
| T06 | `in`/`not_in` for arrays, slices, dynamic arrays, native maps and bit sets; constant type/value membership in generic constraints. |

```odin
import "core:math" / sin,
    cos

numeric :: proc(value: $T) -> T where T in [int, f64] {
    return value
}

MODE :: 2
#if MODE in [1, 2] {
    ENABLED :: true
} #else {
    ENABLED :: false
}

main :: proc() {
    values := [1, 2, 3]
    typed: [2]f64 = [1, 2]
    empty: [0]int = []
    result := ENABLED ? 27
                      : 31
    assert(result == 27 && 2 in values && 9 not_in typed)
    assert(len(empty) == 0 && sin(f64(0)) == 0 && cos(f64(0)) == 1)
}
```

Import selections have no trailing comma. Selected names may be procedures,
procedure groups, types, or constants; mutable package-variable selections are
rejected, including when unused. Local variables/parameters can shadow a selected
name. The implementation resolves these aliases to qualified references, and
validates selections with local aliases in a private initializer: Odin does not
allow privacy attributes on every kind of alias declaration.

Array literals infer their element type from context, otherwise from the first
element with Odin's normal literal defaulting. A type-only helper is used inside
`type_of` when necessary; it does not evaluate the element an extra time. Every
literal element executes once. An empty literal needs context, except that an
empty membership list can obtain its element type from the needle.
The inferred element type also supplies context to later nested array/record
literals. Procedure defaults and assignments through indexing or pointers are
covered by the tests.

Runtime membership evaluates the needle first and the container second, once
each. All literal elements execute before searching, even when the first element
matches. Enclosing `&&`, `||`, and ternaries retain their lazy behavior. Generated
membership helpers are contextless and allocate no storage. Map membership tests
keys, and string substring membership is intentionally unsupported.

`T in [int, f64]` compares exact type identity; aliases match, unlisted distinct
types do not. Generic constraints and other constant contexts use constant
comparisons rather than runtime search. Parentheses, `not_in`, duplicate entries,
and empty type lists are supported. Named constant arrays can also be used for
value membership when their length is statically determined by the frontend.

## Tests and rejection examples

```sh
python3 -m unittest discover -s tests -v
```

The suite compiles and runs actual generated programs with Odin. It checks
accepted programs, rejection behavior, evaluation counts/order, import privacy
and shadowing, native syntax, generic constraints, contextual/empty/nested arrays,
mixed packages, command behavior, and the main demo. Each test uses an isolated
temporary source and output directory.
The suite also builds TLS and runs stdio LSP tests against the actual executable.

Readable rejection examples are under [tests/fixtures](tests/fixtures). Each
directory includes `main.thor` and the expected diagnostic fragment:

```sh
./thor check tests/fixtures/generic_constraint
```

## Implementation and current boundary

- [frontend/parser](frontend/parser) adapts Odin's real tooling parser and retains
  its original license. [frontend/main.odin](frontend/main.odin) exports AST spans
  and roles as JSON, using the installed Odin AST and tokenizer packages.
- [thorlang/compiler.py](thorlang/compiler.py) performs AST-based,
  source-preserving lowering. It uses Odin type intrinsics for contextual array
  types rather than copying private callee type names into the caller.
- [thorlang/driver.py](thorlang/driver.py) manages the CLI, frontend build cache,
  source/import graph, generated output, and Odin invocation.
- [thorlang/sourcemap.py](thorlang/sourcemap.py) records composable emission
  mappings; [thorlang/diagnostics.py](thorlang/diagnostics.py) translates backend
  locations, ranges, and messages to original source.

The implementation was verified with Odin `dev-2026-10:8169255b9` and Python
3.11.6. An Odin AST/tokenizer API change may require refreshing the adapted parser.
Full project/toolchain handling remains a later task: cross-target compilation,
assembly/foreign-library asset mirroring, and the complete set of unusual Odin
build flags are not claimed by this milestone. Toolchain/linker failures without
source locations are reported as compiler-wide errors; unfamiliar backend wording
may still need a dedicated Thor explanation.

Expected-type discovery covers directly resolved procedures, annotated procedure
variables, record fields, returns, and assignments. It does not yet resolve every
overload or generic specialization. At those call sites, a literal that relies
solely on contextual typing (such as `[.Red, .Blue]`) can require explicit element
types or ordinary Odin array syntax. Full target resolution belongs to T11.

Later syntax in the full spec—`fn`/`#run`, `#switch` and runtime switch expressions,
immutable declarations, sets, f-strings, and negative-index normalization—is not
implemented yet. Native Odin maps/bit sets work in T06; Thor sets arrive with
T08/T15. The original root `thor.odin` remains the planning/reference file; the
evaluation program is `demo/main.thor`.
