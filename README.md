# Thor

Both the Python `thor` launcher and the independent experimental `thor-rust`
launcher are available. The Rust implementation passes the current macOS
reference corpus. See the [migration status and comparison commands](docs/rust-migration.md).
The [readiness audit and optimization plan](docs/rust-migration.md#readiness-audit-and-optimization-plan-2026-11-10)
records the native installation checks and remaining performance work before cutover.
The [structural optimization results](docs/rust-migration.md#structural-optimization-pass-2026-11-10)
record the first five performance steps and the remaining one-second target.

![Thor](assets/branding/logo-slim.png)

Thor is an experimental Odin superset that transpiles to ordinary Odin. This
milestone implements **T01–T23** from the reference specification in
[the language specification](docs/language-spec.md). It includes the parser and project support needed to make
those features usable together.

Arbitrary-precision stages 1–5 implement predefined `Integer`/`Rational` types,
exact unquoted literals, constructors, read-only components, direct-copy ownership,
runtime operators, read-only constants, fn/#run, formatting and shared-library
integration. See [the introductory demo](examples/big_numbers/demo.thor),
[the compile-time demo](examples/big_comptime/demo.thor),
[the integration demo](examples/big_integration/demo.thor),
[the runtime API](runtime/big/README.md), and
[the language reference](docs/arbitrary-precision.md).

Odin compatibility is checked against real projects as well as focused tests.
The [October compatibility audit](docs/compatibility-audit-2026-10-29/README.md)
records the original failures and their regression cases; the compiler preserves
native range/type operands, enum indices, SOA iteration and storage rules while
adding Thor's syntax.

Collection comprehensions construct slices, dynamic arrays, sets and maps,
with filtered compile-time fixed arrays and one-level `..` unpacking. See the
[comprehension reference](docs/comprehensions.md) and the executable
[commented examples](examples/comprehensions/demo.thor).

## Run the demo

Requirements: Python 3.10+ and `odin` on `PATH`. The first invocation builds and
caches the small Odin frontend automatically; no Python packages are required.
Publishing PNG/ICO application icons additionally uses Pillow; see
[application publishing](docs/publishing.md).

```sh
./thor run demo
```

[demo/main.thor](demo/main.thor) has a labeled section for every completed task,
with assertions, ordinary Odin code, imported packages, and evaluation-order
examples. It finishes with `All T01-T23 demos passed.`

Select another compile-time branch with an ordinary Odin define:

```sh
./thor run demo -define:DEMO_MODE=1
./thor run demo -define:DEMO_MODE=3
```

For a more spacious, commented walkthrough in the style of Odin's language demo,
read [examples/demo/demo.thor](examples/demo/demo.thor) and run:

```sh
./thor run examples/demo
./thor test examples/demo -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
```

It uses fresh examples, with a procedure per topic, ownership explanations,
commented invalid cases, a mixed Odin/Thor helper package, and five sections on
Integer/Rational construction, ownership, operators, compile-time results and
library/formatting interactions. Its compile-time
branch selector is `-define:TOUR_MODE=1`, `2` (default), or `3`.

## Commands

The canonical local toolchain is `/Users/davidludwik/Thor`. Its compiler, TLS,
frontend and shared libraries live there; projects keep their own caches and
generated application code. See [INSTALLATION.md](INSTALLATION.md).

```sh
./thor transpile demo                  # Print the generated package path.
./thor check demo
./thor build demo -out:demo-program     # Choose a name when building from this checkout.
./thor run demo
./thor test path/to/tests              # Run Odin @(test) procedures.
./thor run path/to/main.thor -- argument "two words"
./thor check demo --output-dir /tmp/thor-generated
```

## Basic library

The bundled [`thor:basic` package](runtime/basic/README.md) works from any Thor
project without additional collection flags. Its canonical source is
[runtime/basic/basic.thor](runtime/basic/basic.thor):

```odin
import "thor:basic"

// Inside a procedure:
result := basic.sorted([]int{3, 1, 2})
defer delete(result)
basic.print(result)
```

Use `import "thor:basic" / sorted, print` for selected unqualified names.
Use these forms to expose every public name, or select names without also
introducing the package namespace:

```odin
import * "thor:basic"                // All public names; basic.name also works.
// Alternatively:
import _ "thor:basic" / sorted, print // Only sorted and print; no basic namespace.
```

The unqualified names are local to the importing file. They are not re-exported,
and private declarations and internal imports stay hidden. Wildcard imports
preserve live access to exported variables, including assignments. Local
variables may shadow imported names; conflicting file-scope declarations and
imports are errors. Public names in conditional branches reserve their names
even when inactive, so two wildcard imports with overlapping potential exports
are rejected. An unused conditional export is not forced to exist.

Run the [import walkthrough](examples/imports/demo.thor) with
`thor run /path/to/thor/examples/imports`.
Run the [commented example](examples/basic/demo.thor) with
`thor run /path/to/thor/examples/basic`.

`basic.deepclone(&source)` returns an independently allocated graph root and an
error, preserving cycles, shared storage and interior references.
`basic.deepfree(root)` releases it once. See the [ownership contract](docs/deepclone.md)
and [graph-copy demo](examples/deepclone/demo.thor); these are library procedures
and require no changes to the Thor/Odin compilers.

For allocations that all share a block's lifetime:

```odin
{
    context.allocator = basic.scoped_arena()
    values := make([]int, 100)
    text := f"Created {len(values)} values"
    basic.print(text)
}
// The arena is destroyed and the enclosing allocator restored.
```

The arena uses Odin's native caller-scope deferred cleanup, including early
returns. Keep its allocations within that scope; explicit allocator choices and
`context.temp_allocator` retain their usual behavior. See the
[scoped arena contract](runtime/basic/README.md#scoped-arena-allocations).

`basic.hash(value)` returns a runtime `u64` value hash without allocating.
Select `.XXH64` or `.FNV64a` with `algorithm=`, or use the default `.XXH3_64`.
`basic.try_hash(value)` returns `(hash, Hash_Error)` for recoverable failures.
Strings/ordered containers hash contents, structs ignore padding, and pointers
hash addresses. See the [hashing contract](runtime/hash/README.md).

`basic._string(value, allocator=...)` returns owned text and an optional
`runtime.Allocator_Error`. It supports numeric values including `Integer` and
`Rational`, bools, enums, runes, strings/cstrings, and byte/rune sequences:

```odin
text := basic._string([]rune{'a', '界', '😀'}) or_panic
defer delete(text)
```

Strings/bytes are copied; rune sequences are UTF-8 encoded. Scalar formatting
matches plain interpolation. Failures return empty text and release temporary
allocations. See the [conversion contract](runtime/basic/README.md#converting-values-to-strings).

## Optional results with `or_panic`

Use postfix `or_panic` when an optional result must succeed:

```odin
value := strconv.parse_int("42") or_panic
entry := records[key] or_panic
node := item.(^Node) or_panic
first, second := get_pair() or_panic // Drops only the final status.
minimum := min(..values) or_panic
```

Like `or_else panic(...)`, it unwraps optional lookups/assertions and procedure
results whose final value is a success boolean or a nil-comparable error.
Booleans succeed when true; error values succeed when nil. The operand executes
exactly once, at its original expression site; short-circuited expressions stay
unevaluated. Ordinary type checking and ownership rules still apply. The
operator does not free the successful result or introduce automatic cleanup of
user-owned values.

On failure the ordinary panic handler receives the original `.thor` file, line,
column, and procedure, plus a message containing the original expression and
returned error/status. The default handler writes to stderr, for example:

```text
/path/main.thor(27:13) panic: unexpected failure
  expression: strconv.parse_int(text)
  procedure: main
  error: false
```

The location remains present with `-disable-assert`; panic is never compiled out.
The status check itself allocates nothing and formats no text; result allocation
and numeric-copy ownership retain their usual rules. Only the failure path
formats the returned error, using the temporary allocator.
Contextless procedures use a contextless panic; ordinary procedures retain the
current custom panic handler. `fn` and `#run` use the same unwrap semantics:
failed evaluations report both the evaluation request and the original failing
unwrap. `#run` still requires a direct call: put the unwrap inside the called
procedure rather than writing `#run (operation() or_panic)`. Native `#load`
retains its own constant-expression restrictions.

TLS supports result types, immutable highlighting, keyword help/completion,
references/rename, and diagnostics. Original-source debug mapping applies as
usual. `or_panic` is reserved in Thor; native `.odin` files remain unchanged.

## Spread minimum and maximum

Thor extends Odin's builtins with fixed-array expansion and slice spreading:

```odin
a := [?]int{1, 2, 3}
maximum := max(**a)         // int: 3
minimum, ok := min(..a[:]) // int: 1, bool: true
lowest := min(..a[:])      // The presence flag is optional.

empty: []int
missing, found := min(..empty) // 0, false
fallback, valid := max(7, ..empty) // 7, true
```

Every call containing a slice spread returns `(value, ok)` with Odin's
`#optional_ok` behavior. `ok` reports whether the whole call has at least one
candidate, including scalar starting values. Empty slices without starting values
return the element type's zero value and false. `or_else` and `or_return` work
with the status. A fixed-array expansion produces a single value; expanding a
zero-length array is a compile error.

Arguments execute once in source order. Fixed-array expansion snapshots its
elements; a slice borrows its backing storage. Reductions allocate no heap
storage or mutate their inputs. Results preserve the element type. Untyped scalar
literals can adapt to it; typed mismatches are errors. Ordered numbers, strings,
enums and their distinct aliases retain native comparison semantics. Selected
strings remain borrowed from the input.

Scalar calls such as `min(x,y)` and type limits such as `max(int)` retain their
native behavior. User-defined procedures named min/max remain ordinary calls.
Immutable aliases of the actual builtins and `core:math.min/max` also support the
new forms. Declared `fn`/`#run` bodies can reduce sequences, and required constants
with known inputs are evaluated. The existing single-result compile-time rule
still applies: return a record containing both value and status from a function
when both must be retained at compile time.

Run the [commented example](examples/extrema/demo.thor) with
`./thor run examples/extrema`.

## Compilation and Odin flags

Thor uses Odin as its native backend. For a single-file build:

```sh
./thor build path/to/my_file.thor -o:speed -debug -out:my_program
```

1. Thor parses the original files with its shared Odin/Thor frontend and checks
   Thor-specific rules.
2. Required compile-time work, including `#run` and eligible known-argument `fn`
   calls, is evaluated. This can compile and run temporary native Odin helpers;
   successful results can be reused from the evaluation cache.
3. Thor writes generated `.odin` files, source maps and a manifest into
   `.thor-build/my_file/`. Imported project packages are mirrored there too.
4. Thor invokes Odin on the generated file, automatically supplying `-file` for
   a single-file input. Odin performs native checking, code generation and linking.
5. Thor translates compiler diagnostics back to the original source locations.
   A successful build leaves the executable at `my_program` in this example.

Generated files retain the original relative layout inside the output tree;
`my_file.odin` is not written over or beside the original `my_file.thor`. Use
`--output-dir /tmp/generated` to choose the generated tree's location. Without
`-out:...`, `thor build` names the executable after the working directory and
places it there (`.exe` is appended on Windows). For example, running
`thor build .` inside `thor-playground` produces `./thor-playground`. This also
applies when building a source elsewhere or using `--output-dir`. Set `-out:path`
to choose the artifact's name and location. Building from the Thor checkout
requires a different output name, such as `-out:demo-program`, to protect the
existing `thor` launcher.
`run` and `test` keep their temporary executables under the generated output tree.
Odin's other native build modes can be selected with `-build-mode:...`.

| Thor command | Work after parsing and compile-time evaluation |
| --- | --- |
| `transpile` | Write the generated Odin tree and print its entry path; skip final Odin checking/building. |
| `check` | Ask Odin to check the generated source. |
| `build` | Ask Odin to build the requested artifact; an executable by default. |
| `run` | Build an executable, then run it. |
| `test` | Build with Odin's `-build-mode:test`, then run the test executable. |
| `publish` | Build and package a local macOS app, Windows ZIP or Linux archive with resources, icon and native dependencies. |

For distributable applications use `thor publish .`. It includes `assets/`
automatically, discovers `assets/icon.png` or `assets/icon.ico`, and reads optional
`[publish]` settings from `thor.toml`. GUI mode is the default: macOS launches the
executable directly, Windows selects the GUI subsystem, and Linux desktop metadata
disables the terminal. Use `--app-type console` when a terminal is wanted. See the
[publishing guide](docs/publishing.md) and [example](examples/publish/README.md).
Published executables default to `-o:speed`; an explicit `-o:...` flag overrides
that setting. Ordinary build/run/test optimization defaults are unchanged.

`run` and `test` remove their executable afterward unless `-keep-executable` is
passed. Generated sources remain available for inspection. Source-map files are
saved for `-debug` builds and the explicit `transpile` command; ordinary compiler
diagnostics use mappings held in memory.

Thor forwards flags it does not handle itself to the final Odin invocation.
Use Odin's usual `-flag:value` spelling, including `-o:speed`, `-debug`,
`-define:NAME=value`, `-out:path`, and `-target:os_arch`. Values containing spaces
must remain part of the same quoted argument. Thor does not reproduce Odin's
complete flag catalog; Odin validates forwarded flags, and compatibility with
every option is not guaranteed. Thor exposes the commands above rather
than forwarding arbitrary Odin subcommands.

A few options receive additional handling:

- `-collection:name=path` is rewritten to the corresponding generated package
  location. Source-relative assets keep their original resource paths.
- Diagnostics normally use Odin's JSON output internally, followed by Thor's
  source mapping. `-json-errors` requests mapped JSON; `--raw-diagnostics`
  exposes the unmodified backend diagnostics.
- Defines and relevant native options also reach compile-time helpers. Options
  for the final artifact, such as `-out:` and `-build-mode:`, are omitted from
  helper builds. Helpers execute natively; a cross-target build requiring Thor
  compile-time execution currently reports an unsupported-target error.

Thor caches compile-time execution results and per-file parser output. Every
invocation discovers the current source files, performs fresh Thor analysis,
regenerates project Odin and in-memory source maps, and runs the requested Odin check or build.
There is no whole-project or generated-output cache.
Common native libraries are read from the installation. Project-independent Thor
library translations live under the installation's `lib/`; checked or
configuration-dependent versions remain in the project.

Compile-time results live under `.thor-cache/eval-v1` beside the entry package
(or the cache root selected by `--cache-dir` or `THOR_CACHE_DIR`). Records contain
values and dependency metadata, not executable helpers. Dependency checks cover
called bodies, declarations, bindings, types, imports, attributes, library sources,
resources and compiler configuration. A compiler/SDK identity record avoids
rehashing the toolchain when its file metadata is unchanged; it is checked lazily
when compile-time result reuse is needed.

Verified scalar `#run` sites can reuse results before constructing temporary
helpers. Adding an unrelated local variable or moving a call does not change its
calculation. Native type witnesses still check the original call in generated
Odin, preserving overload, privacy and nominal-type errors. Generic, aggregate,
conditional, `using` and overlay cases retain the conservative helper path when
these early checks cannot establish reuse. Dynamic resource paths bypass result
caching when their inputs cannot be established safely.

The parser cache lives in the project's `.thor-cache/parse-v1` (or selected cache root). Successful
frontend JSON is keyed by source contents, parser binary and parsing options.
Editing one file reuses parsing of unchanged imports; each consumer constructs
fresh syntax objects and performs fresh analysis. Editor overlays participate
using their actual text. Errors are not cached.

`--cache-report` prints `thor_cache` evaluation counters and `thor_prepared_hits`
for results reused before helper construction; one early hit can cover nested
calculations too. `--no-cache` bypasses both result and parser caches.
`--clear-cache` clears their entries before compiling. The former
`--no-transpile-cache` option was removed with the whole-project cache.

Thor's own options use long names, such as `--output-dir`, `--odin`,
`--run-timeout`, `--no-cache`, and `--cache-report`. Run `./thor --help` for the
complete list. `--odin /path/to/odin` or `THOR_ODIN` selects the Odin executable.
The `--run-timeout` option bounds compile-time helper execution, not your final
program's runtime.

Arguments after `--` are passed to the executable by `run` or `test`:

```sh
./thor run path/to/my_file.thor -o:speed -- first_argument "two words"
```

## VS Code and TLS

The local [TLS fork](tls/THOR.md) and [Thor VS Code extension](editors/vscode/README.md)
support the current T01-T23 features. Build the server and extension from this repository:

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
symbol renaming, and live parser/compiler diagnostics on original, unsaved Thor
text. The client
finds the `thor` compiler launcher alongside the local TLS checkout; use
`tls.compiler.path` for another location. Use **Rename Symbol** in the context menu
or **F2** to rename a resolved source identifier. Formatting, automatic imports,
and Thor code actions remain disabled. Existing
Odin support continues to handle `.odin` files.

Additional editor checks:

```sh
python3 tools/build_tls.py test
python3 tools/test_vscode.py
```

The VS Code test uses a temporary editor profile. See [TLS's notes](tls/THOR.md)
for the pinned upstream revision, semantic-resolution limits, and configuration.
For the startup sequence, local installation model, LSP communication and the two
highlighting layers, see [how the VS Code extension works](editors/vscode/README.md#how-vs-code-launches-tls).

## Create, run and debug a project

With this checkout on PATH and the local Thor Language extension installed:

```sh
thor new my_project          # Create ./my_project
thor new my_project --open   # Also open VS Code
mkthor my_project            # Create ~/Code/Thor/my_project and open VS Code
```

Creation writes just `my_project.thor`, `.vscode/tasks.json`, and
`.vscode/launch.json`. No package manifest, README, local library copy or TLS
settings file is needed. The default source comes from the editable shared
[templates/main.thor](templates/main.thor), initially copied from your Desktop
`new.thor`. Project creation does not invoke Odin or build caches.

**Cmd+R** runs the project in VS Code's integrated terminal; **Cmd+Shift+R**
builds with debugging and starts CodeLLDB. Use **Ctrl+R / Ctrl+Shift+R** elsewhere.
Both commands save edits first. **F5** also starts debugging. The shortcuts apply
to Thor editors and leave existing Odin shortcuts alone. Both shortcuts target
the active file's containing directory, including projects nested inside a
larger workspace. Cmd+R reuses the active integrated terminal, changes directory
if necessary, and runs `thor run .` with its existing shell environment. If the
terminal already reports that directory, only `thor run .` is sent; otherwise
a shell condition avoids a redundant `cd`. If no live terminal exists, it opens
one directly in that directory. Normal runs include sibling source files and
do not launch VS Code tasks. Cmd+Shift+R builds and launches the debugger in that
same source directory, using matching local executable and source-map paths.

Install **CodeLLDB** (`vadimcn.vscode-lldb`) as well as the locally built Thor
extension. Rebuild/install the VSIX and reload VS Code after updating from an
older extension. `mkthor` itself is an executable beside `thor`, so the same PATH
entry makes both commands available. Other creation options include:

```sh
thor new my_project --parent /path/to/projects
thor new my_project --template /path/to/starter.thor
thor new my_project --gitignore # Optional fourth file for build output/caches
mkthor my_project --parent /path/to/projects
mkthor my_project --no-open
mkthor my_project --odin /path/to/odin
```

The generated task runs `thor build . -debug -o:none`, keeps the executable in
`.vscode/bin/debug`, and writes the matching generated sources/maps beneath
`.vscode/bin/generated/`. Tasks record the Thor installation's launcher path;
TLS discovers the shared installation. The optional `--odin` selects an Odin
executable for the debug build task; normal runs use the terminal's environment.
Existing folders and symlinks are never
overwritten. Platform/test filename suffixes retain Odin's native filtering;
the creator warns about names such as `example_linux` instead of silently
changing the requested filename. Use a neutral name for a portable starter.

On native macOS, debug executable builds also enable allocation tracking and
AddressSanitizer, with runtime reports mapped to the original Thor source even
when the executable is launched directly. Use `--track-allocations=off` and/or
`--no-sanitize` to disable them; `--track-allocations=trace` adds allocation call
stacks. Normal builds and debugging on other platforms retain their existing
behavior. See [memory debugging](docs/memory-debugging.md) for ownership,
configuration and tool dependencies.

The `thor-lldb` debug adapter reads those maps and uses ordinary Odin and CodeLLDB.
Breakpoints, stack frames, Step Over/Into/Out, watches and local inspection refer
to original Thor files; generated expression helpers are presented as their
owning procedure. A step can enter a user procedure called from inside an fstring
or switch arm. Compiler/runtime housekeeping is skipped during source stepping.
Mixed imported Odin files retain normal source locations. Conditional breakpoints
and variable editing also work through expression captures.

With `-debug`, optional automatic `fn` evaluation in runtime expressions is
retained as runtime calls for stepping. Required constants, type/signature work,
generic constant arguments and explicit `#run` still evaluate during compilation.
Compile-time-only code and inactive branches have no runtime breakpoint location.
Watch/condition expressions use CodeLLDB's expression syntax; they are not a full
Thor interpreter. Save before debugging; the pre-launch task rebuilds, and the
adapter rejects stale source snapshots or incompatible maps.

This repository also includes **Debug Thor Walkthrough (LLDB)**, which builds
`examples/demo` when the debug shortcut is used from a Thor document in this
workspace. See the [debugger guide](editors/vscode/DEBUGGING.md) for setup,
architecture, supported behavior and tests.

## Project inputs and output

Inputs can be a single `.thor`/`.odin` file or a package directory. Directory
inputs can mix both extensions. Relative imported packages and explicitly passed
`-collection:name=path` collections are mirrored into the generated tree. Odin's
native file suffixes and build tags select active native sources. Resource paths
in `#load`, `#load_hash`, `#load_directory`, and `#exists` remain relative to the
original source file, including in ordinary Odin dependencies. Explicit collection
resource paths and relative foreign objects/libraries use their original roots.
The manifest records the forwarded native flags, including asset collections;
assets are referenced in place, so retain the original project when compiling
generated output. `thor:` defaults to this checkout's [runtime](runtime/README.md)
collection and may be overridden with an explicit collection flag.

Thor additions such as `:>` belong in `.thor` files. `.odin` files retain ordinary
Odin syntax, including when compiled through `thor`. When converting a project,
rename its Thor source files to `.thor` and update any editor tasks that still
invoke `odin build` to use Thor (see the [debugger setup](editors/vscode/DEBUGGING.md)).

See [Compilation and Odin flags](#compilation-and-odin-flags) for flag forwarding,
native helper builds, output locations and runtime arguments.

Generated files live under `.thor-build/`; the frontend binary is cached under
`.thor-cache/`, keyed by its source and Odin version. Source files are never
overwritten. A manifest allows stale generated sources to be removed on rebuild;
Thor refuses to reuse an unrelated nonempty output directory.
Generated trees are rejected as compiler inputs. File suffixes and build tags
follow `-target:os_arch`, including when checking a target different from the host.
`test` and `build -build-mode:test` include test-tagged files.

## Thor vetting

`thor vet` checks Thor conventions and reports warnings. Source changes are
explicitly requested with `--apply` or confirmed through `--ask`.

```sh
thor vet .                         # Report findings
thor vet . --apply                 # Apply verified corrections
thor vet . --ask                   # Preview corrections and ask once [y/N]
thor build . --vet                 # Build with convention warnings
thor vet . --vet=immutable,pure    # Select checks
thor vet . -warnings-as-errors     # Require corrections for a successful check
```

The `immutable` check suggests `:>` for initialized local bindings whose uses
permit immutability. Grouped declarations qualify together. The `pure` check
suggests `fn` for eligible procedures, including their transitive calls and
signature requirements. Findings have stable `thor/prefer-immutable` and
`thor/prefer-fn` diagnostic codes. `-ignore-warnings` hides the warnings.
Odin's single-dash `-vet` flags select its native checks.

Vetting examines project `.thor` files beneath the selected package directory,
using the active target and build flags. The initial checks conservatively skip
conditional declarations, uninitialized locals, generic procedure conversions,
attributed procedures and the entry point. Procedures returning owned or
reference-bearing data also require manual review, since `fn` can change result
ownership and allocation behavior. Converting a procedure to `fn` enables its
normal automatic compile-time evaluation with known arguments.

Proposed corrections are validated together through normal Thor checking using
temporary source overlays. Additional analysis runs for `thor vet` and `--vet`.
Procedures with definitely unsupported public `fn` parameter/result types or
direct I/O are discarded before tracing their helper graphs. The remaining
suggestions still pass complete compiler validation; vet normally performs the
original check and one proposed-edit check. Mixed valid/invalid edits can require
additional checks to isolate the safe corrections.
Applying corrections preserves comments, formatting, line endings and file modes.
Changed source snapshots are rejected, and write failures roll back completed
updates. `--ask` accepts `y` or `yes`. An empty answer or end of input declines.

Enable `"tls.vet": true` in VS Code to see the same warnings and quick fixes on
unsaved text. TLS offers a correction for the document snapshot that was checked.

## Compiler diagnostics

`check`, `build`, `run`, and `test` report errors against original `.thor` files
(or original `.odin` dependencies), with source snippets and underlines. Parser,
Thor validation, and Odin type-checking errors use the same display. For example,
`values := [1, "wrong"]` underlines `"wrong"` in your Thor source, even though
the generated array declaration is much longer.

A declaration that stands alone in an inner control-flow block, such as
`if condition { x := 10 }`, produces an unused-local warning. Builds continue
normally, and its initializer executes normally. TLS highlights the original
variable name with warning severity. `-ignore-warnings` hides the warning and
`-warnings-as-errors` makes it an error. Explicit Odin vet modes and
`--raw-diagnostics` use Odin's native policy. An affected build may need an extra
native checking pass. Already successful checks use a single invocation.

If an array type is accidentally used as an array value (for example,
`Rows :: [3]int; Rows[i] = 1`), Thor explains the distinction and suggests a
variable declaration such as `Rows: [3]int`. A related note points to the type's
declaration. Notes appear with source excerpts in the terminal and as clickable
related locations in VS Code, including original imported/unsaved source. JSON
diagnostics expose these as optional `related` entries containing `pos` and
`message`; notes do not increase the error count.

Indexing errors identify invalid index/lower-bound/upper-bound types at the
original bound, and proven generated cascades are removed when the real error is
an undeclared name or missing record field. Result-type errors quote the original
lowered expression (such as `values[i]`) while preserving its actual type.
Inline membership-list conversion errors explain the comparison context and link
to the value being tested. User bindings named `type_of`, `len` or other native
builtins keep their normal meaning; generated operations use Odin's qualified
builtin namespace when needed. See the [diagnostic review](docs/diagnostic-review-2026-11-01.md)
for examples and implementation boundaries.

Unconditional top-level redeclarations in the same untagged file are checked by
the shared frontend before compile-time evaluation. TLS reports these immediately
while editing, even without compiler checking, with the duplicate name underlined
and the earlier declaration's line. Conditional declarations, tagged files,
cross-file conflicts and other type checks still use the backend.

With `-debug` or `thor transpile`, generated `.odin` files have `.odin.map.json`
sidecars. Ordinary builds retain mappings in memory without writing these files.
Shared translated libraries keep their common maps in the installation. These versioned maps
record exact copied byte ranges, source ranges for generated constructs, and
original source snapshots. Nested, reordered, and duplicated expressions retain
their origins. Compiler messages also translate embedded declaration/caller
locations and quoted lowered expressions. Errors inside membership helpers point
back to the operation that instantiated them. Empty-array and membership failures
have Thor-specific explanations; redundant inference errors are suppressed when
the underlying expression already has a diagnostic.

Power operand mismatches underline the original `**` expression and name both
types. For example, `HOUSE_ODDS ** i` with `f64` and `int` operands reports the
type mismatch and asks for an explicit conversion. A failed generated result
type can also cause a secondary return error; that error is suppressed when
the original expression already has an error. Genuine user return-type errors
remain visible.

Thor supports inline generic callback defaults in named procedures:

```odin
sorted :: proc(data: $T/[]$E,
               less := proc(i, j: E) -> bool { return i < j }) -> []E {
    ret := make([]E, len(data))
    copy(ret, data)
    slice.sort_by(ret, less)
    return ret
}
```

Import `core:slice` for this example. Calls can omit `less` or supply their own
comparator, including a runtime procedure value. The returned slice belongs to
the caller; delete it when finished. See the runnable, commented example in
[examples/default_callbacks/demo.thor](examples/default_callbacks/demo.thor).

Thor emits a named generic helper, an explicit callback parameter type, and the
omitted argument at a statically resolved call. This works across Thor/Odin files,
package imports and selective aliases. Supplying a comparator skips specialization
of the default, so records can use a custom comparator even though `<` is not
defined for records. Explicit `nil` stays `nil`. Ordinary Odin procedure-value
types do not retain default arguments; indirect calls still supply the callback.
Overloaded calls that Thor cannot resolve also need an explicit callback.

Generic dependencies must be inferable from the callback's parameter types, such
as `E` in `proc(a,b:E)` or `N` in `proc(a:[N]int)`. A dependency appearing only in
the result/body is currently rejected with a mapped signature diagnostic.
Callbacks cannot capture enclosing runtime parameters. An independently generic
literal such as `proc(a,b:$U)` needs an explicit callback parameter type.
Procedure-valued parameters remain outside the `fn`/`#run` pure-input contract.

This extension applies to `.thor` declarations; native `.odin` declarations retain
their source-mapped unsupported-default check. Named generic defaults, concrete
callbacks and callbacks using already-bound outer types retain native behavior.
Odin still selects active branches and instantiates enclosing bodies before
checking guards or helper bodies. Computed type expressions that cannot be
classified confidently stay with Odin's checker.
Unrecoverable locationless errors retain an explicitly labeled package fallback
in TLS rather than being assigned to an unrelated open file.

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
The [Thor debug adapter](editors/vscode/DEBUGGING.md) also maps debugger stack
frames and stepping. Stack traces printed directly by the native runtime still
use its own locations.

## Implemented syntax

| Task | Behavior |
| --- | --- |
| T01 | CLI, source preservation, generated output, and compile/run/rejection harness. |
| T02 | `import "core:math" / sin, cos`; multiline lists, retained package namespace, file-local names. |
| T03 | Ternaries may continue before `:` without a backslash, including nesting and comments. |
| T04 | Native `when` / `else when` / `else`, including nesting, selected declarations and lazy compile-time execution. |
| T05 | `[1, 2, 3]` is an inferred fixed array; contextual types, empty typed arrays, nested arrays, arguments, returns, and assignments are supported. |
| T06 | `in`/`not_in` for arrays, slices, dynamic arrays, native maps and bit sets; interval membership; constant type/value membership in generic constraints. |
| T07 | Caller-owned integer grouping and Unicode padding helpers in `thor:format`. |
| T08 | Map-backed `Set(T)` library in `thor:sets`, including mathematical, in-place, clone, iteration, and cleanup operations. |
| T09 | Shared Thor-owned lexer/parser and source emitter; native AST/token compatibility, comments, and byte spans retained. |
| T10 | Mixed-source/import graphs, target/tag selection, collections, resource/foreign paths, managed output and CLI flag forwarding. |
| T11 | Lexical symbols and shapes; contextual arrays through procedure aliases, overloads, generic specializations and private types. |
| T12 | `x :> expr` and `x: Type > expr`: runtime initialization once, immutable storage and read-only slice views; inferred/per-name `#mut` procedure contracts. |
| T13 | `f"hello {name}"` and raw f-strings; nested expressions, brace escaping, integer grouping, Unicode padding and owned output. |
| T14 | Runtime switch expressions and compile-time `#switch` statements/expressions, with optional selectors, lazy matching and contextual results. |
| T15 | `set[T]`, literals, mathematical/in-place operators, logical equality, membership, len, iteration, clear and delete. |
| T16 | Negative signed sequence indices and slice bounds, including writable/addressable access; native map, enum-array and pointer behavior retained. |
| T17 | Expression-local helpers preserve lazy branches, operand order, nested arguments, loop reevaluation and assignment storage. |
| T18 | Declared `fn` purity, transitive read-only inputs, verified direct calls, recursive/generic functions, and internal scratch allocation. |
| T19 | Versioned contents-only value serialization and typed reconstruction, including wide integers, exact floats, enums, arrays, records and unions. |
| T20 | Explicit native `#run` execution, verified dependency closures, dependent constants, selected/lazy paths, mapped failures and bounded execution. |
| T21 | Persistent contents-only result cache, transitive/configuration/toolchain/resource invalidation, atomic publication, corruption recovery and cache controls. |
| T22 | Scratch cleanup and independently owned string/slice results, deep aggregate promotion, recursive cleanup and fresh runtime `#run` materialization. |
| T23 | Automatic known-argument `fn` folding, forward/dependent constants, generic specialization scheduling, selected/lazy paths and fresh folded runtime results. |

Conditional compilation uses Odin's `when`, `else when`, and `else` directly.
Use ordinary `if` / `else if` for runtime branching. The `#switch` and `#run`
directives retain their existing behavior.

```odin
import "core:math" / sin,
    cos

numeric :: proc(value: $T) -> T where T in [int, f64] {
    return value
}

MODE :: 2
when MODE in [1, 2] {
    ENABLED :: true
} else {
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
An inline membership list also receives context from an already typed needle:
`tile: i8 = 4; found := tile in [4, 8]` uses an `[2]i8` list. Untyped constants
and arithmetic expressions in the list receive the same context. Conversely,
`4 in existing_i8_array` coerces the untyped `4` to the array's element type.
Named arrays keep their established types: `values := [4, 8]` remains `[2]int`,
so an `i8` needle does not implicitly convert to match it. Explicitly typed list
elements must match too; out-of-range literals are errors rather than narrowing
casts. `not_in`, compile-time conditions and generic value constraints follow
the same rules. With untyped operands on both sides, normal first-element
defaulting remains in effect, so `1.5 in [1, 2]` is an error.
The inferred element type also supplies context to later nested array/record
literals. Procedure defaults and assignments through indexing or pointers are
covered by the tests.

Runtime membership evaluates the needle first and the container second, once
each. All literal elements execute before searching, even when the first element
matches. Enclosing `&&`, `||`, and ternaries retain their lazy behavior. Generated
membership helpers are contextless and allocate no storage. Map membership tests
keys, and string substring membership is intentionally unsupported.

Intervals also support membership: `value in low..<high`, `value in low..=high`
and `value not_in (low..<high)`. The half-open form excludes `high`; inclusive
membership compares directly without incrementing the bound. Reversed intervals
are empty. Compatible numeric values, runes, enums and strings use Odin's ordered
comparisons; string intervals are lexicographic.

Runtime operands execute once in value/low/high order, including the high bound
when the value is outside the interval. Anonymous owned strings/views are freed
after comparison. Constants, `when`, `#switch`, `fn`/`#run` and constraints work too:

```odin
allowed_size_range :: proc($N: int) -> int where N in 0..<10 {
    return N
}
```

Arithmetic bounds work directly, as in `value in low+1..<high-1 && value != 5`.
Use `for (value in low..<high)` for a condition-only loop; native `for value in
low..<high` retains iteration. Three-part loop headers accept membership as their
condition, including an empty post statement. Intervals here are syntax for
membership, rather than first-class range values.

String repetition uses `*` with a constant string and a nonnegative integer
constant, in either order. It has ordinary multiplication precedence:

```odin
X :: "a" * 3          // "aaa"
Y :: "a" * 3 + "b"    // "aaab"
Z :: 4 * X + "z"      // "aaaaaaaaaaaaz"
PHRASE :: ("a" + "b") * 2 // "abab"
EMPTY :: "anything" * 0   // ""
```

Like Odin's string `+`, both operands must be compile-time constants; runtime
immutable bindings (`:>`) are not compile-time constants. Named/imported
constants, `#config` values, `#run` results and constant parameters inside generic
procedure bodies work. The result is constant storage: assigning it with `:=`
does not allocate, and it must not be deleted. Untyped literal/constant inputs
retain native coercions such as a `cstring`
procedure argument, including after concatenation. Explicitly typed string
inputs retain their type, including for zero repetitions.
Strings repeat their exact bytes, including UTF-8, escapes and raw strings.
The result is limited to 16 MiB, matching the existing compile-time value budget.
Thor emits a doubling chain of
native constant concatenations, with no repetition execution helper or new cache
files. See [the runnable example](examples/string-repetition/main.thor).
In generic signatures and type expressions, a repetition depending on generic
parameters currently needs a fixed integer count from 0 through 128. A count
that varies with a generic parameter works inside the procedure body.

Compile-time sequence concatenation uses `++`:

```odin
FIRST :: [1, 2]
SECOND :: [3, 4, 5]
ALL :: FIRST ++ SECOND ++ [6] // [6]int
WORDS :: []string{"hello"} ++ []string{"Thor"} // []string
ROWS :: [[1, 2]] ++ [[3, 4]] // [2][2]int; rows stay nested

SMALL :: [1]i8{1}
MORE :: SMALL ++ [2] // New literals inherit the established element type.
EMPTY:[0]i8: [] ++ [] // Empty literals need an element type from context.
```

Operands must be native compile-time values. Runtime variables, including `:>`
bindings and ordinary procedure parameters, do not qualify; `$` constant
parameters do. Arrays may have different lengths but must have matching element
types. Slices must have the same slice type. Arrays and slices cannot be mixed
implicitly. An array result is a plain fixed array of the combined length; a
slice result retains its slice type. Concatenation is shallow and combines only
the outer sequence. Constant backing storage follows Odin's usual materialization
rules, including for nested slices; no independent heap clone is made.

`++` has the same precedence as `+` and associates left to right. Native array
`+` keeps its element-wise meaning, and strings keep their existing constant
`+`. There is no `++=` or increment operation. Chains lower to one
`intrinsics.concatenate` call, with no evaluation helper, heap allocation or
cache entries. A runtime binding may copy the resulting fixed array normally;
constant slice backing must not be deleted. Odin's intrinsic restrictions still
apply: positional constant literals are supported; named/indexed literal entries
and runtime array arithmetic are not silently evaluated as constants.

See `constant_concatenation` in [the commented tour](examples/demo/demo.thor).
The old `basic.CONCAT` alias has been removed; use `A ++ B` instead.

Power uses infix `**`; native prefix `**values` expansion remains available.
It associates to the right and binds above multiplication and unary signs:
`2 ** 3 ** 2 == 512`, `-2 ** 2 == -4`, and `(-2) ** 2 == 4`.
Both operands must have the same integer or floating-point type. Untyped numeric
constants follow native coercions, so `2.0 ** -3 == 0.125` and `f32_value ** 3`
work; typed `int_value ** f64_value` requires an explicit conversion.

Numeric loops can specify their value type, and array/slice loops can specify
only the index type using a parenthesized binding:

```odin
ROUNDS :: 10
for i: f64 in 1..=ROUNDS { probability := 0.5 ** i; _ = probability }
for value, (i: i8) in values { _ = value; _ = i }
for &value, (i: u8) in values { value += int(i) }
```

Range annotations supply context for untyped bounds; already-typed bounds must
match or be explicitly converted. Range values and indices remain immutable;
`&value` keeps its existing reference behavior. Element types are inferred, and
the ambiguous `for value, i: i8 in values` spelling is rejected. Index types must
be integers. Fixed arrays must fit their complete index range at compile time;
slices and dynamic arrays panic if a visited index cannot be represented, even
with `-disable-assert`. The internal counter retains Odin's normal type, so a
narrow index cannot wrap. Annotations allocate no memory and preserve native
range-bound evaluation, labels, and loop control flow. See the
[commented examples](examples/demo/demo.thor).
Floating-point ranges keep Odin's unit stepping and precision limits, including
the inability to advance by one at sufficiently large magnitudes.

Floating-point powers use `core:math.pow`, including fractional exponents.
Integer powers require a nonnegative exponent (a compile error when known, a
panic otherwise). `0 ** 0` is `1`; runtime integer overflow follows ordinary
Odin multiplication. Untyped integer constants retain exact values through 128
bits. Operands execute once in base/exponent order and retain lazy surrounding
control flow. Powers work in constants, array sizes, `when`/`#switch`, specialized
procedure generics and `fn`/`#run`, using the existing native evaluation cache.
Type-level generic expressions without a resolvable call site currently require
a fixed nonnegative integer exponent through 128; other generic constant powers
need a resolvable procedure specialization.

`T in [int, f64]` compares exact type identity; aliases match, unlisted distinct
types do not. Generic constraints and other constant contexts use constant
comparisons rather than runtime search. Parentheses, `not_in`, duplicate entries,
and empty type lists are supported. Named constant arrays can also be used for
value membership when their length is statically determined by the frontend.

```odin
import f "thor:format"
import sets "thor:sets"

main :: proc() {
    text := f.format(-1234567, grouping=true, width=14, align=.Center, fill='界')
    defer delete(text) // returned formatting strings belong to the caller
    names: sets.Set(string)
    defer sets.destroy(names) // destroy each table once, including its aliases
    sets.insert(&names, "apple")
    for name in sets.entries(names) { assert(name == "apple") }

    value :> len(text) // runtime value; direct writes and &value are rejected
    data := [1, 2]
    view :> data[:]
    data[0] = value // writable aliases can change what the read-only view sees
    // view[0] = value // rejected
}
```

Immutable records and fixed arrays also protect directly stored subobjects.
Slices reached through an immutable binding become read-only views, including
nested slice references. Existing writable aliases can still modify the shared
storage. Pointer targets and dynamic arrays retain their existing behavior.
Taking an immutable header's mutable address is rejected.
Native map entry assignment can change the map header, so it requires a mutable
header (a mutable shallow copy may still access shared entries). Ordinary Odin
copying and ownership rules apply; this feature does not add lifetime checking.
Compile-time branches and generic shapes are checked by Odin after lowering, so
inactive branches remain unchecked and reference views are distinguished from
directly stored arrays. TLS marks immutable symbols readonly and explains them
in hover; live compiler checks enforce mutations on unsaved text.

An immutable array can be sliced directly for value iteration:

```odin
values :> [10, 20, 30]
for value in values[:2] { fmt.println(value) }
```

This reads the original storage without allocating or copying the whole array.
Named immutable views and passing them to readers are supported. Writable aliases
and `for &value in values[:]` remain rejected. Thor infers per-parameter writable
access requirements; explicit `#mut` applies to the next name only:

```odin
copy_values :: proc(source, #mut destination: []int) {
    copy(destination, source)
}
```

TLS displays inferred `#mut` in grey and includes requirements in hover text.
See [the complete slice access contract](docs/slice-access.md) and the commented
[slice access demo](examples/slice_access/demo.thor).

`:>` enforces storage immutability in Thor; Odin's `@(rodata)` is a storage
placement attribute and does not provide the same compile-time checks.

## Using without a feature header

Thor enables standalone `using value` statements and `using` procedure
parameters by default. Write them directly in a `.thor` file; the transpiler
adds `#+feature using-stmt` to that file's generated Odin when needed.
This includes `using` parameters in procedure types and anonymous procedures.

Embedded fields (`struct { using base: Base }`) and declarations such as
`using value := Record{}` already work in Odin without the feature, so those
forms do not cause a tag to be added. Comments and strings are ignored.
Existing feature tags are preserved without duplicates; an explicit
`#+feature !using-stmt` retains the restriction for that file. Native `.odin`
dependencies keep Odin's feature rules.

Original `.thor` files are never edited. Compiler diagnostics, TLS locations,
breakpoints and stepping use the normal source maps to account for the
generated header line.
Promoted fields keep their own value types when supplying expression context.
For example, assigning `kind = switch { case condition: .A; case: .B }` through
`using record: ^Record` uses the type of `record.kind`, including enum and
generic fields.

## Interpolation, switches, Sets and negative indices

```odin
main :: proc() {
    names := set[string]{"apple", "pear", "apple"}
    names += "plum"
    assert(len(names) == 3 && "pear" in names)
    for name in names { assert(name in names) }

    data := [10, 20, 30]
    data[-1] += 1
    tail := data[-2:]
    label := switch data[-1] {
        case 30: "before"
        case 31: "after"
        case: "other"
    }
    value := switch { case len(tail) == 2: 1234567; case: 0 }
    text := f"{{label}} {label}: {value:'0'12,}"
    assert(text == "{label} after: 0001,234,567")
    delete(text)
    delete(names)
}
```

Interpolation accepts quoted or raw backtick literals. `{{` and `}}` escape
literal braces. Fields contain real Thor expressions, including nested literals,
switches and interpolations. Parenthesize a ternary before a format separator.
The [format grammar](docs/interpolation-formatting.md) is
`[quoted-fill][flags][width][,][.precision][verb]`, following Odin's `fmt` verbs.
For example, `.2f`, `12.2f`, `-12.2f`, `#08x`, `012,.2f`, and `.2%`.
Use nested braces for a runtime width: `f"{value:{width}.2f}"`.
Alignment defaults to right for every type; `-` means left and `^` means center.
`<` and `>` are removed. Unicode fills use quoted runes (`'界'^12s`);
width counts runes. `f/F` defaults to three decimal places, `e/E` to six,
and native `g/G` to shortest round-trip text. Big-number decimals are exact;
Rational `g/G` requires explicit precision (`.6g`). String `.10s` truncates at
rune boundaries. Large precisions use sized storage. Fields execute once, left to right. The final string is owned by
the caller. Internal formatting buffers and owned nested interpolations are
released after use, including mixed branches such as
`f"{(flag ? f"value {n}" : borrowed)}"`: only the selected owned string is freed.
Named field values keep their existing ownership.

Formatting streams into bounded stack scratch, with a heap fallback for large
output. Common short native f-strings allocate only the final owned string.
See [formatting performance and benchmarks](docs/fstring-performance.md).

Anonymous f-string arguments are temporary by default. Thor releases them when
the whole enclosing expression finishes, including nested calls and selected
conditional/switch branches. This applies to every function, including native
Odin functions; printing has no special compiler rule.

```odin
print(f"value {n}")                  // Automatically freed after printing.
print(identity(f"value {n}"))        // Lives through both calls.
save_for_later(f!"value {n}")        // Opt out: the receiving API owns cleanup.
append(&messages, f!"value {n}")     // Containers retain string headers too.

text := f"value {n}"                 // Named owners still require deletion.
defer delete(text)
other := f!"value {n}"               // Legal; same ownership as a named f string.
defer delete(other)
```

The `f!` spelling also supports raw backticks. It disables automatic temporary
cleanup; use it when the receiving API retains or deletes the argument. This is
a caller-controlled lifetime rule, not a checked borrowing contract. Returning
or storing a reference to a default anonymous argument beyond its expression
would leave a dangling reference. Named strings, literals and arbitrary
string-returning procedures are not automatically deleted at call sites.
Ordinary factory results still need the lifetime specified by their API.
Native type conversions preserve ownership: `text := string(f"value {n}")`
owns its string and needs deletion. In `print(string(f"value {n}"))`, the
enclosing `print` call still controls temporary cleanup.
See [the lifetime implementation and validation notes](docs/fstring-lifetimes.md).

Expression switches require a default arm unless a runtime enum switch covers
every named value. Odin checks that coverage; missing enum cases are errors.
`#partial switch` also works in expression position, with a default supplying
the omitted cases' result (or `panic(...)`). An unmatched enum value produced
by an explicit cast panics rather than returning a fabricated zero value.
Arms may supply one result or a
comma-separated list of results. They support comma-separated cases and native ranges, check duplicate
constant cases, evaluate the selector once, and execute only the first matching
arm. Expected destination/argument/return types supply result context.
Each result receives its own destination type, including distinct types, enum
members and inferred record/array literals. Multiple results use ordinary Odin
multiple-value rules; swaps evaluate every selected RHS before storing results.
An arm can also call a procedure returning the required number of values.

```odin
a, angle, b = switch {
    case rotate: b, other_angle, a
    case:        break
}

NUMBER, TEXT :: #switch {
    case DEBUG: 1, "debug"
    case:       2, "release"
}
```

`break` skips the entire assignment and leaves all destinations unchanged. It is
allowed only when the switch is the entire RHS of an ordinary `=` assignment
(parentheses are fine), in any arm. It does not break an enclosing loop. It is
rejected in declarations, returns, arguments, nested arithmetic and compound
assignments. Destination locations/indices are evaluated once before switch
selection, as in native assignment; skipping performs no stores. The same
default/enum-coverage rule applies. `#switch` supports the same result lists and assignment-only
skip; compile-time constants and unchecked inactive arms retain native `when`
behavior. Anonymous Thor allocations discarded into `_` are cleaned up; named
values remain borrowed. Run the [commented examples](examples/switch-results/demo.thor)
with `thor run examples/switch-results`.
A switch result arm may call any procedure declared `-> !`, such as `panic`,
`unreachable`, or a custom/imported nonreturning procedure. It terminates instead
of supplying a value, so it does not contribute to result-type inference.
For example, `direction: Coord > switch { case valid: {-1, 0}; case: panic("invalid move") }`
is valid. Aliases, procedure-valued parameters and generic specializations follow
the same rule. Ordinary void calls are not result arms. A switch with only
nonreturning arms needs an expected result type, or expected types for multiple
results. Normal argument checking, calling conventions and purity rules apply.
`#switch` has the same selector forms in statement and expression positions;
it uses native `when` semantics for constant selection, declaration visibility,
control flow, and unchecked inactive arms. Later conditions, including those in
a comma-separated case, stay unchecked after a match. Explicit `#run` follows
selected compile-time branches and known lazy conditions.
A selected nonreturning arm in a `#switch` expression also needs an expected
result type; inactive arms are unchecked. `fn` may call `panic`, and a panic
reached during `#run` or automatic compile-time execution fails the compilation.

Sets use `|`, `&`, `-`, `~`, `==` and `!=`, with `|=`, `&=`, `-=`, `~=`
for in-place updates; `+=` inserts one element, while `-=` accepts an element
or another Set. Binary mathematical results own independent tables. Assignment
copies a shallow header. Anonymous Thor intermediates are cleaned up automatically:
`result := a | b | c` reuses one result table; only `result` needs deletion, and
named operands remain untouched. `clear(s)` keeps the table allocation;
`delete(s)` releases it. Keys retain their ordinary shallow
ownership. Native operations on other types keep their Odin meaning.

`to_set` and `to_slice` are available from both `thor:sets` and `thor:basic`:

```odin
import * "thor:basic"

unique := to_set([1, 2, 2, 3])
defer delete(unique)
values := to_slice(unique)
defer delete(values)
```

`to_set` accepts fixed arrays, slices and dynamic arrays, including distinct
sequence types. Duplicates become one key. `to_slice` returns an allocated `[]T`
with one copy of each key in unspecified order; sort it separately if needed.
Both produce independent container storage and accept `allocator=` (defaulting
to `context.allocator`). Element copies are shallow: separately owned strings
and pointees retain their existing lifetimes. Empty inputs are supported.
Sets retain their allocator for later insertion and deletion; delete slices
with the allocator that created them when supplying a custom allocator.

Automatic cleanup covers Thor-owned expressions consumed by operators, queries,
formatting, indexing, iteration, unused expressions and blank destinations. Loop
temporaries survive through the loop and are released on exit. Assigning or returning an owned result
leaves cleanup to the caller. Arbitrary procedure arguments/results, aggregate
fields and separately allocated Set keys retain Odin's manual ownership rules;
this does not introduce a general ownership checker. Use named keys when their
allocations need independent cleanup.

A view that must survive its expression needs a named owner:

```odin
owner := f"value {n}"
defer delete(owner)
view := owner[1:]
```

Thor diagnoses `view := f"value {n}"[1:]` and equivalent temporary pointer/field
views. Immediate reads such as `len(f"value {n}"[1:])` keep the owner alive until
the read completes. Borrowed views through mixed branches are conservatively
rejected when the owner cannot be carried safely.

Negative signed bounds normalize to `len(sequence) + bound`; unsigned bounds
keep their type. Strings index UTF-8 bytes. Bounds are checked after normalization
without clamping; excessively wide signed bounds are checked before machine-index
conversion. The base and supplied bounds execute once in source order. Map keys,
enumerated arrays, pointers and multi-pointers retain their native interpretation.

The emitter uses named procedures with explicit lexical inputs, rather than
closures (which Odin does not provide). Calls stay at the original expression
site; declarations do no work. The same source maps cover helper bodies and their
instantiations, so errors point back to fields, arms, elements and accesses.
Nonallocating helpers also work inside native C/system/contextless procedures;
allocating features require a context, which such callbacks can assign explicitly.

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
The [post-v1 audit](AUDIT.md) records cross-feature fixes, coverage and concrete
remaining editor limitations. Contextual enum navigation, completion and rename
through switch-arm arrays have dedicated regression coverage.
The [November hardening report](docs/hardening-2026-11.md) inventories the
features added since the purity audit, their documentation, interaction tests,
and remaining conservative boundaries.

Readable rejection examples are under [tests/fixtures](tests/fixtures). Each
directory includes `main.thor` and the expected diagnostic fragment:

```sh
./thor check tests/fixtures/generic_constraint
```

## Implementation and current boundary

- [frontend/parser](frontend/parser) adapts Odin's real tooling parser and retains
  its original license. [frontend/main.odin](frontend/main.odin) exports AST spans
  and roles as JSON. [frontend/tokenizer](frontend/tokenizer) owns scanning while
  retaining installed Odin token/AST types for TLS and formatter compatibility.
- [thorlang/compiler.py](thorlang/compiler.py) performs AST-based,
  source-preserving lowering. It uses Odin type intrinsics for contextual array
  types rather than copying private callee type names into the caller.
- [thorlang/driver.py](thorlang/driver.py) manages the CLI, frontend build cache,
  source/import graph, generated output, and Odin invocation.
- [thorlang/semantics.py](thorlang/semantics.py) tracks lexical bindings, required
  type shapes, call targets and storage protection. Native type witnesses resolve
  exact contextual types in the declaration's scope, including private types.
- [thorlang/expressions.py](thorlang/expressions.py) lowers interpolation,
  switches, Set operations and negative accesses using expression-local helpers.
- [thorlang/sourcemap.py](thorlang/sourcemap.py) records composable emission
  mappings; [thorlang/diagnostics.py](thorlang/diagnostics.py) translates backend
  locations, ranges, and messages to original source.

The implementation was verified with Odin `dev-2026-10:8169255b9` and Python
3.11.6. An Odin AST/token API change may require refreshing the shared frontend.
Cross-target checking is covered; linking a foreign target still requires the
ordinary Odin toolchain. Linker/toolchain failures without locations are reported
as compiler-wide errors. The generated manifest records a rebuild command's
native flags; it is not a standalone packaged SDK or asset bundle.

Thor resolves the type/procedure information its additions require and delegates
ordinary assignability, overload validity, constraints, and code generation to
Odin. It preserves unresolved/ambiguous calls for native checking rather than
choosing arbitrarily. A call with insufficient information to determine a
contextual literal's element type still needs an explicit type. TLS retains
OLS's partial semantic resolver; compiler diagnostics use the complete backend.
For an unconstrained generic receiver, immutable-field checks conservatively
protect an implicitly promoted `using` field. Write the existing pointer member
explicitly (`value.pointer.field`) to retain shallow mutation in that case;
concrete and shape-constrained records resolve pointer promotion directly.

T01–T23 are implemented, including automatic constant evaluation, per-instance
generic scheduling, persistent caching and independently owned allocated results.
`fn` and `set` remain valid ordinary Odin identifiers. The
[language specification](docs/language-spec.md) preserves the numbered reference;
`demo/main.thor` is the evaluation program. Follow
[DEVELOPMENT.md](DEVELOPMENT.md) for each compiler → TLS → diagnostics batch.

## Pure functions and compile-time execution

```odin
square :: fn(value: $T) -> T { return value * value }
SIZE :: square(4) // automatically evaluated: a genuine integer constant
buffer: [SIZE]int
NEXT :: #run eligible_proc(SIZE) // ordinary proc execution still needs #run
```

`fn` is a contextual procedure spelling. It lowers to an Odin procedure with a
purity contract. Calls with known values/types are evaluated automatically;
runtime inputs remain runtime calls. Literals, named constants, supported constant
expressions, type queries and evaluated results qualify. Mutable variables and
immutable runtime bindings (`:>`) do not become constants through propagation.
`#run` also accepts an ordinary `proc` when its relevant transitive call graph
passes the same checks. Recursion, concrete generic calls, local constants,
overloads, defaults, named arguments and forward/dependent results are supported.
Explicit `#run` requires known inputs and reports a runtime argument as an error.

A `fn` may directly call an ordinary Odin or Thor `proc` when that procedure
and its transitive callees satisfy the pure rules. This applies to runtime calls
as well as compile-time evaluation:

```odin
square :: proc(n: int) -> int { return n*n }
adjusted :: proc(n: int) -> int { return square(n)+1 }
calculate :: fn(n: int) -> int { return adjusted(n) }
VALUE :: calculate(3) // 10
```

Calls from `fn` use checked versions of ordinary bodies, including the same
scratch cleanup and independent returned storage as `fn`. An ordinary helper may
mutate storage created within the enclosing `fn`, recursively through other
helpers. Typed pointers may be used internally to address that storage:

```odin
set_y :: proc(out: ^int) { out^ = 7 }
calculate :: fn(x: int) -> int {
    y: int
    set_y(&y)
    return x + y
}
```

Numeric casts, including `auto_cast`, keep Odin's type and overload inference.
Pure code cannot use casts to manufacture pointers or expose pointer identities;
that restriction also covers pointer elements inside converted arrays/slices.
Native type queries may still inspect such types without executing a conversion.

The permission follows the affected storage. A local record/header containing an
input slice still borrows that slice; forwarding it through a pointer, field,
subslicing, reference loop or another helper does not permit writing its elements.
Fresh local arrays/buffers and scalar fields may be changed. Helpers may allocate
buffers through output parameters; their storage lives until the enclosing pure
invocation finishes. Helpers cannot store references to their own stack locals
or slice literals through output parameters; use `make` for surviving buffers.
Pointer-bearing results and public `fn` inputs remain
outside the supported value subset; pointer identity comparisons and numeric
address conversions remain forbidden. Provenance joins are conservative across
branches and reassignments, rather than a full flow-sensitive borrow checker.

Direct ordinary calls
retain their original behavior and ownership; their known arguments alone do
not trigger automatic compile-time evaluation. See
[the transitive-call demo](examples/pure_calls/demo.thor).

Printing, file/stream I/O, and the basic library's `print`, `input`, `read` and
`write` are excluded from pure call chains. Calls such as `fmt.print(...)` in a
`fn` report a purity error on the original call, including in an unused
non-generic `fn`. Use `proc` for I/O. Aliases and transitive calls retain the same
rule; inactive compile-time branches and unevaluated type queries remain unchecked.
Formatting into a value is separate from observable I/O and remains subject to
the usual pure formatting, allocation and input-type rules.
See [pure-function and evaluation diagnostics](docs/purity-diagnostics.md) for
the rejection categories, CLI/TLS source locations and execution-failure limits.

Generic `$T`/`$N` bindings are scheduled separately for each resolved instantiation,
including forwarding through imported procedures and inferred fixed-array counts.
A normal parameter is still runtime storage inside a generic body. For example:

```odin
work :: proc($N:int, value:$T) -> int {
    COUNT :: square(N)
    WIDTH :: #run bytes(T)
    data: [COUNT]int
    return len(data) + WIDTH
}
bytes :: fn($T:typeid) -> int { return size_of(T) }
```

The scheduler follows selected `when`, both `#switch` forms and known lazy
conditions. Calls under unknown runtime branches/loop bodies remain runtime;
this avoids executing a failing call on a path that may never be taken. Explicit
`#run` remains a compile-time directive even in runtime code. Type queries inspect
types without executing the queried expression. Invalid dependency cycles are
reported separately from valid native recursion. Unresolved generic `#run` calls
receive a source diagnostic rather than a fabricated result.

Inputs may contain scalar values, enums, strings, slices, fixed arrays, records,
bit sets and tagged unions. Borrowed contents are protected through aliases,
fields, slices, shallow copies, loops, union matches/assertions and calls. Local fixed-array
copies and fresh buffers can be mutated. Pointers, maps, dynamic-array inputs,
`any`, procedure values, runtime global storage, ambient context, uninspectable
foreign/indirect calls, pointer identity observation, pointer/aggregate bit casts, uninitialized
storage and unapproved operations are rejected. Numeric, boolean, enum and bit-set
scalar bit casts are eligible. Checks inside
inactive native `when` branches remain inactive.

Compiler-controlled deterministic operations include numeric conversions/operations,
length/type/size queries, `make` for local slice/dynamic buffers, `append`, `clear`,
pop-family operations, `copy`, `delete`,
assertions, `core:strings.clone`, `core:slice.clone`, and Thor's scalar/string
formatters. Other direct calls require inspectable, transitively eligible bodies. Explicit
allocator overrides and custom record formatters are rejected. Each checked
invocation uses private tracking scratch storage, including for interpolation;
mutating ordinary helpers share the enclosing invocation's scratch lifetime;
all outstanding internal allocations are released on every return. Ordinary
`proc` results and named f-string owners retain explicit cleanup. Anonymous
f-string arguments follow the expression lifetime rule described above.

String/slice/dynamic-array results own independent backing storage, including input views and
repeated aliases inside arrays, records and tagged unions. Promotion uses the
saved caller allocator after user defers and before local/scratch storage expires.
Nested calls allocate their returned copies in the receiving function's scratch.
Temporary work is always released; visible runtime results are caller-managed:

```odin
import pure "thor:pure"
message :: fn(n:int)->string { return f"value {n}" }
text := message(7)
defer delete(text)
// For nested allocated fields/elements, call pure.destroy(result) once instead.
```

Dynamic-array results retain their length and capacity and use the caller's
allocator, so later `append` and ordinary `delete` work normally. For example:

```odin
load_points :: fn() -> [dynamic]Pos {
    points: [dynamic]Pos
    for point in STARTING_POINTS { append(&points, point) }
    return points
}
points := load_points() // May fold automatically when all inputs are constants.
defer delete(points)
compiled := #run load_points()
defer delete(compiled)
```

`pure.destroy` recursively frees owned strings/slices/dynamic arrays and their supported
aggregate members. `pure.clone(value)` creates another deep owned copy. Both
accept an explicit allocator outside pure code. Ordinary assignments remain
shallow: destroy an owner once; free an owned field before overwriting it. Do not
use recursive destruction on borrowed literals or shallow native containers.
Arbitrary user procedure results and anonymous arguments remain manual Odin
ownership. Procedures without an Odin context, including `proc "contextless"`
and C-convention bodies, cannot promote allocated returns;
use `fn` or a contextful `proc` for those results.

Runtime `x := #run work()` and automatically folded calls materialize fresh owned storage on every execution,
including loop iterations and cache hits. In constant contexts, strings and fixed
aggregates containing strings become ordinary borrowed Odin constants. A
slice- or dynamic-array-containing result needs a runtime binding (`:=`), or a
pure consumer that returns a scalar constant. Native slice/dynamic-array constants
are not implemented. Public pure inputs still accept slice views instead of
dynamic-array headers.

The value protocol preserves arbitrary string bytes, nil/non-nil empty slices,
zero-sized sequence elements, dynamic-array capacities, fields, active union variants and exact float bits. It
serializes contents without padding or process addresses.

Explicit `#run` can execute an eligible ordinary Odin or Thor procedure,
including procedures in the installed `base`, `core` and `vendor` collections.
Thor discovers their source on demand and checks the implementation and required
dependencies under the same rules as project code. Library names do not confer
purity or require an allowlist entry. For example:

```odin
import "core:math" / round

SIZE :: 1_000
ARY_SIZE :: int(#run round(SIZE ** .33333))
values: [ARY_SIZE]int
```

Eligible overloads, generics, immutable procedure aliases and selected/wildcard
imports use the same path. Immutable library constants are inspected as
dependencies; mutable runtime state remains forbidden. Compiler intrinsics are
separate leaf operations: deterministic numeric/structural queries and known LLVM
math operations are supported, while clock/cycle counters, memory operations and
source identity queries such as `type_canonical_name` are not eligible user calls.
A procedure with an inspectable Odin body can use a non-Odin calling convention;
an external declaration without a body remains uninspectable. Existing input,
result and ownership constraints still apply. An optional second return value is
ignored by a single-value `#run`; non-optional multiple results need a consumer
that returns one supported value.

Each ready evaluation is compiled in a temporary native helper containing only
its required declarations/imports. User entry points, unrelated runtime globals
and package initializers are excluded; referenced runtime storage is rejected.
Only selected native overload implementations enter the helper, with private
implementations kept behind public evaluation bridges. Native source failures
highlight the original `#run` and include their library source location. Public
names and overload notes retain their original spelling.

For runtime checked calls into installed Odin libraries, Thor writes a sparse
SDK overlay under generated output and selects it for the backend compilation.
Only files containing checked procedures are augmented; untouched packages,
source files and resources link directly to the installed SDK. Unsaved SDK
replacements supplied by an editor are emitted into the overlay as well.
Ordinary and checked library calls therefore share the same nominal types;
the installed SDK is unchanged. The manifest's `odin_environment` records the
required `ODIN_ROOT`; use it alongside `odin_flags` when compiling generated Odin
manually. Ordinary project-only call graphs do not need this SDK overlay.

Run the [commented native-call walkthrough](examples/native_run/demo.thor) with
`./thor run examples/native_run`.
Helpers preserve the original defines, `ODIN_TEST` and `ODIN_BUILD_MODE`, including
dependencies in test-tagged files. Cross-target
evaluation is rejected. Compilation errors map to original source; execution failures
point to the original call or `#run` expression. Helpers are deleted after use. Execution defaults
to five seconds per helper; set `--run-timeout 10` to change it (maximum 300).
Independent-call batching remains future work.

Successful helper results are cached in `.thor-cache/eval-v1` beside the entry
package. Keys include emitted transitive code/types/constants/arguments, native
flags/defines, compiler binary/version, Thor/frontend/runtime and installed
base/core/vendor source contents, plus static resource contents/existence. Keys are
conservative: equivalent calls at different sites or irrelevant source edits may
miss. Dynamic resource paths bypass caching. Partial, corrupt or incompatible
entries recompute; publication is atomic. Cache storage failures do not prevent
compilation. Cached entries contain typed contents, never executables or addresses.

```sh
./thor run demo --cache-report # repeat to see warm hits
./thor run demo --no-cache
./thor run demo --clear-cache
./thor check demo --cache-dir /tmp/thor-evaluations
```

`THOR_CACHE_DIR` supplies a default cache root; `--cache-dir` overrides it.
`--cache-report` prints opt-in JSON counters on stdout, leaving compiler diagnostics
on stderr. Evaluation-cache lookups follow eligibility checks. Project analysis
and final Odin checking still run on every invocation. Late result-cache hits
hash generated evaluator code in memory and skip writing or compiling the
temporary evaluator project.

Current conservative boundaries: the native compiler cannot reflect record
fields at compile time, so Thor supplies checked source field schemas. Unknown
external record schemas, some `using`-promoted record shapes and raw unions are
rejected; a record sharing a known raw union's field-name shape may also be
rejected. Provenance joins can reject writes to a fresh buffer after it has shared
a binding with borrowed storage. Compile-time evaluation currently supports direct,
resolved calls with one result; void/multiple-result `fn` calls retain their native
runtime behavior. The scheduler uses Thor's partial resolver, so unsupported or
ambiguous specializations may still need an explicit type or simpler wrapper.
Enum-indexed array constants support declared keys and zeroed
unnamed slots; nonzero unnamed slots fail rather than being changed on output.
Source-location-dependent `#file`, `#directory`, `#line`, `#caller_location`,
`ODIN_BUILD_PROJECT_NAME` and source/type identity queries, including
constants derived from them, are rejected in pure execution until their values
can be kept identical across generated runtime code and helpers. `#defined`
depends on the declarations in scope and is also excluded. Implicit deferred
procedure callbacks are also rejected; make eligible calls explicit in the body.
TLS infers call result types but does not execute procedures for hover values or
its own branch selection; live compiler checks perform the actual evaluation.
