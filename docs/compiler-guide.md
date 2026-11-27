# Detailed compiler and editor guide

Start with [installation](installing.md) or [the README](../README.md). These sections retain detailed commands, diagnostics, vet policies and development notes.

## Compiler and development

`thor` runs the prebuilt Rust compiler. `thor-rust` selects the same implementation,
and `thor-python` retains a frozen historical reference. Compiler fixes and features
are maintained in Rust only. See [Python retirement and validation](../docs/python-retirement.md)
and the
[migration status and comparison commands](../docs/rust-migration.md) and the
[compiler-efficiency follow-up](../docs/compiler-efficiency-2026-11.md) and the
[cache/discovery improvements](../docs/compiler-cache-discovery-2026-11.md) and the
[dependency lookahead](../docs/compiler-dependency-lookahead-2026-11.md) and the
[shared type, summary, and parallel lowering pass](../docs/compiler-structural-3-5-2026-11.md).
The [slice-checker follow-up](../docs/slice-checker-efficiency-2026-11.md) covers
permission-root pruning, type-property caching, shared graph storage, and selective replay.
The [SDK and resolver pass](../docs/sdk-resolver-efficiency-2026-11.md) covers compact
syntax caching, semantic state transfer, dependency worklists and complete call plans.
The [AST and index pass](../docs/ast-index-efficiency-2026-11.md) extends project
syntax caching, packs AST lists, and reuses dependency indexes and signature layouts.

## Run the demo

Requirements are `odin` on `PATH` and a release compiler built with Rust 1.85+
and Cargo. Build it once after checkout and rebuild after editing Rust compiler
sources. The launcher executes the existing binary directly. The first invocation
builds and caches the small Odin frontend automatically.

```sh
cargo build --release --locked --manifest-path rust/Cargo.toml
./thor run demo
```

For compiler development, `python3 tools/build_compiler.py` builds the release
compiler and prunes obsolete development artifacts before/after the build. The
maintained test runner does the same. Plain Cargo builds remain supported.
See [storage limits and retention](../docs/storage.md).

Python 3.10+ is needed for the reference compiler and Python development tools.
The reference publisher uses Pillow for PNG/ICO icons. See
[application publishing](../docs/publishing.md).

[demo/main.thor](../demo/main.thor) has a labeled section for every completed task,
with assertions, ordinary Odin code, imported packages, and evaluation-order
examples. It finishes with `All T01-T23 demos passed.`

Select another compile-time branch with an ordinary Odin define:

```sh
./thor run demo -define:DEMO_MODE=1
./thor run demo -define:DEMO_MODE=3
```

For a more spacious, commented walkthrough in the style of Odin's language demo,
read [examples/demo/demo.thor](../examples/demo/demo.thor) and run:

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

The installed toolchain contains the compiler, TLS, frontend and shared libraries.
Projects keep their own caches and generated application code. See [INSTALLATION.md](../INSTALLATION.md).

```sh
./thor transpile demo                  # Print the generated package path.
./thor check demo
./thor build demo -out:demo-program     # Choose a name when building from this checkout.
./thor run demo
./thor test path/to/tests              # Run Odin @(test) procedures.
./thor run path/to/main.thor -- argument "two words"
./thor check demo --output-dir /tmp/thor-generated
```


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
   the hidden build directory. Imported project packages are mirrored there too.
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

The source path defaults to `.` for commands that operate on existing source:
`thor build`, `thor run -debug`, `thor vet --apply` and `thor test` use the current
working directory. Explicit file and directory paths still work, including with
options before the path. Thor does not search parent directories for a project.
`thor run -- argument1 argument2` passes those arguments to the current project's
program. Bare `thor` shows help, and `thor new` requires a project name.

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
[publishing guide](../docs/publishing.md) and [example](../examples/publish/README.md).
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

Compile-time results and parser output share a bounded per-user cache
(or the cache root selected by `--cache-dir` or `THOR_CACHE_DIR`). Records contain
typed values and dependency metadata. Dependency checks cover
called bodies, declarations, bindings, types, imports, attributes, library sources,
resources and compiler configuration. Toolchain contents are hashed once per
invocation when compile-time result reuse needs their identity. Resource reuse
checks file contents and directory membership, including same-size edits with
unchanged timestamps.

Verified scalar `#run` sites can reuse results before constructing temporary
helpers. Adding an unrelated local variable or moving a call does not change its
calculation. Native type witnesses still check the original call in generated
Odin, preserving overload, privacy and nominal-type errors. Generic, aggregate,
conditional, `using` and overlay cases retain the conservative helper path when
these early checks cannot establish reuse. Dynamic resource paths bypass result
caching when their inputs cannot be established safely.

The cache lives in `~/Library/Caches/Thor` on macOS, `$XDG_CACHE_HOME/thor`
(or `~/.cache/thor`) on Linux, and `%LOCALAPPDATA%/Thor/cache` on Windows.
Its managed records have a combined **512 MiB** budget, shared across projects,
with automatic eviction. Parser records are keyed by source contents, parser binary
and relevant parsing options.
Editing one file reuses parsing of unchanged imports; each consumer constructs
fresh syntax objects and performs fresh analysis. Editor overlays participate
using their actual text. Errors are not cached.

`--cache-report` prints `thor_cache` evaluation counters and `thor_prepared_hits`
for results reused before helper construction; one early hit can cover nested
calculations too. `--no-cache` bypasses result, parser and memory-vet caches.
`--clear-cache` clears their entries before compiling. Memory vet can reuse
unchanged checks after validating dependency contents, flags and resources;
see [memory-check reuse](../docs/lifetime-vet.md#reusing-unchanged-checks). The former
`--no-transpile-cache` option was removed with the whole-project cache.

Thor's own options use long names, such as `--output-dir`, `--odin`,
`--run-timeout`, `--no-cache`, and `--cache-report`. Run `./thor` for a short
overview and common options, `./thor help` for the usual Thor options grouped by
command, or `./thor help build` for one command’s options. Add `-full` to include
all compatible native compiler options: `./thor help -full` or
`./thor help build -full`. Odin’s `-help` spelling remains accepted; `-h` and
`--help` have been removed. [The CLI guide](../docs/cli.md) describes command
compatibility, source-facing tools, and reporting.
`--odin /path/to/odin` or `THOR_ODIN` selects the Odin executable.
Compile-time helper execution is unlimited by default. `--run-timeout 0`
explicitly selects unlimited execution. A positive value, such as
`--run-timeout 10`, sets a deadline for each compile-time execution.

Arguments after `--` are passed to the executable by `run` or `test`:

```sh
./thor run path/to/my_file.thor -o:speed -- first_argument "two words"
```


## VS Code and TLS

The local [TLS fork](../tls/THOR.md) and [Thor VS Code extension](../editors/vscode/README.md)
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

The VS Code test uses a temporary editor profile. See [TLS's notes](../tls/THOR.md)
for the pinned upstream revision, semantic-resolution limits, and configuration.
For the startup sequence, local installation model, LSP communication and the two
highlighting layers, see [how the VS Code extension works](../editors/vscode/README.md#how-vs-code-launches-tls).


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
[templates/main.thor](../templates/main.thor), initially copied from your Desktop
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
behavior. See [memory debugging](../docs/memory-debugging.md) for ownership,
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
workspace. See the [debugger guide](../editors/vscode/DEBUGGING.md) for setup,
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
generated output. `thor:` defaults to this checkout's [runtime](../runtime/README.md)
collection and may be overridden with an explicit collection flag.

Thor additions such as `:>` belong in `.thor` files. `.odin` files retain ordinary
Odin syntax, including when compiled through `thor`. When converting a project,
rename its Thor source files to `.thor` and update any editor tasks that still
invoke `odin build` to use Thor (see the [debugger setup](../editors/vscode/DEBUGGING.md)).

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
thor build . --vet                 # Require Thor convention checks to pass
thor vet . --vet=unused            # Select checks (comma-separated)
thor vet . -warnings-as-errors     # Require corrections for a successful check
thor vet . --unverified            # Show individual incomplete-analysis notices
thor build . --vet --unverified=error # Also fail on unverified findings
```

The `immutable` check suggests `:>` for initialized bindings whose conversion
passes the compiler in the current configuration. Grouped declarations qualify
together. Global bindings and changes with uncertain API or configuration effects
produce manual-review warnings that `--apply` leaves unchanged. See
[immutable declaration advice](../docs/immutable-vet.md), including alias batches
and editor actions. The `pure` check
suggests `fn` for eligible procedures, including their transitive calls and
signature requirements. Findings have stable `thor/prefer-immutable` and
`thor/prefer-fn` diagnostic codes. The `unused` check reports unused local
variables, immutable bindings, local constants, loop/type-switch/comprehension
bindings, and procedure parameters with the `thor/unused` code. It also reports
unused imports (`thor/unused-import`) and named data types and type aliases
(`thor/unused-type`), including package-level types. Unused named `proc`/`fn`
declarations, constant procedure aliases and overload groups use
`thor/unused-procedure`. Entry points, lifecycle hooks, exported/required
procedures and native test procedures are treated as used. Calls, callbacks,
aliases and compile-time references count as uses; self-recursion alone does not.
All four checks
are enabled by default for `thor vet` and `--vet`. `_` and explicit `_ = value`
uses indicate intentional discards. Member names, named argument labels, and
shadowed bindings are distinguished from actual uses. Named return values and
parameter names in procedure-type declarations are part of their signatures.
Other package-level values and struct fields are outside this local-binding check.
Type references in annotations, aliases, other project files, f-strings and
compile-time code count as uses; a recursive type's self-reference alone does not.
Generic type specializations and aliases into Odin libraries are checked too.
The latter load declaration facts on demand during vetting; ordinary builds
without `--vet` keep their existing SDK loading behavior.
Assignments alone do not count as uses, including `x += 1` and simple arithmetic
self-updates such as `x = x + 1`. Passing a value to any function counts as use,
even when that function ignores its parameter; unused parameters are checked in
their own bodies. Writing through pointer or slice parameters counts as use
because it can change the caller's data, as do writes through `for &value` and
reference type-switch bindings. Conditions, returns, address-taking and explicit
`_ = value` discards also count. This is a conservative local check:
it does not trace unused values through other bindings or report overwritten
initializers when the binding is subsequently used.
Unused regular variables, parameters, types and procedures have no removal edit:
initializer effects, allocations and public declarations require review.
`--apply` and `--ask` preserve their declarations. Unused `for` iterator bindings
have compiler-validated fixes: a first or sole binding becomes `_`, and an unused
second binding is removed. Both unused bindings become a single `_`.

```odin
for hand, i in hands { use(i) }    // --apply: for _, i in hands
for hand, i in hands { use(hand) } // --apply: for hand in hands
for i in 0..<trials { run() }     // --apply: for _ in 0..<trials
```

Reference markers on discarded bindings are removed. Typed indices keep their
checked conversion as `(_:Type)`; comment layouts requiring a second binding
also keep `_`. The iteration expression and body are preserved. TLS offers the
same fixes. Union-switch and comprehension bindings remain manual.
Pure-function advice is independent of usage:
an eligible unused procedure receives both `thor/unused-procedure` and
`thor/prefer-fn` warnings. `--apply` converts its `proc` keyword to `fn`, and
the unused warning remains. An explicit `_ = procedure_name` use suppresses
the unused warning while retaining applicable pure-function advice.
`@(unused)` explicitly marks a variable, constant, type, `proc`, or `fn` as
intentionally unavailable. It suppresses that declaration's unused warning;
referencing it is a compile error until the attribute is removed:

```odin
@(unused)
debug_dump :: proc(value: int) { _ = value }

main :: proc() {
    @(unused) future_limit :: 100
    // debug_dump(1)      // Error: debug_dump is marked @(unused).
    // _ = future_limit  // Error: even an explicit discard is a reference.
}
```

The attribute takes no value and applies to every name in a grouped declaration.
Reads, writes, addresses, type queries, aliases, and compile-time references all
count as uses. Checks follow declarations through package imports and distinguish
shadowed bindings and field/argument labels. Statically excluded files and known
inactive `when` arms are ignored, including conditions resolved by compile-time
execution. References in potentially active procedure definitions are checked even
if the procedure itself is unused. Entry points and declarations automatically
used through `@(init)`, `@(fini)`, `@(test)`, `@(export)`, or `@(require)` cannot be
marked `@(unused)`.

The attribute does not suppress warnings about parameters or locals inside a
procedure, or other vet advice. `--apply` preserves it, including when converting
an eligible `proc` to `fn`. It does not change initializer or ownership behavior.
For unused parameters, use `_` or `_ = parameter`; this declaration attribute is
not a parameter annotation. TLS reports forbidden uses at the reference with a
link to the tagged declaration.

Unused imports have compiler-validated removal edits. Whole selective or wildcard
imports can be removed when unused; if any selected member or the namespace is
used, the declaration remains. A standalone unused import's entire line,
including indentation and its newline, is removed without creating a blank line.
Comments and neighboring same-line statements are preserved, as are existing
intentional blank lines and the file's newline style.
Explicit blank imports (`import _ "package"`) remain intentional, including
imports needed for initialization. Imports used only in inactive debug/platform
branches are retained so applying corrections does not break other configurations.
In test mode with `-all-packages`, imports are test-discovery roots and are also
retained, even if their namespace is otherwise unused.
Unused bindings take priority over immutable suggestions. A declaration with an
unused name receives no immutable suggestion or conversion, including when a
group contains both used and unused names. After those names are used or removed,
the remaining declaration can qualify for an immutable suggestion. This also
applies when selecting only the `immutable` check.
Bindings used only in explicit `_ = name` discards receive no immutable-binding
suggestions, conversions, or related TLS quick fixes. Any other use of the
binding makes it eligible for immutable advice again, regardless of the
discard's position. Expressions such as `_ = name + 1` count as ordinary uses.
The `semicolon` check reports unnecessary statement terminators with the
`thor/unnecessary-semicolon` code. A semicolon at the end of a line or before a
closing brace can be removed. Semicolons separating statements on one line,
loop/control-header clauses, and fixed-capacity type syntax remain required.
The parser identifies optional terminators, including inside f-string expressions.
`--vet=semicolon` selects this check alone. `--apply`, `--ask`, and TLS quick fixes
remove the flagged token while preserving comments and line endings.
`-ignore-warnings` hides warnings from standalone `vet` and editor checks.
`thor build --vet` treats its convention findings as errors, so this flag does
not suppress them. Incomplete memory analyses and unfinished correction proofs
are summarized as nonfatal counts by default. `--unverified` shows details as
information; `--unverified=error` shows them as errors and fails the command,
including with `-ignore-warnings`. JSON retains all details. See
[unverified reporting](../docs/lifetime-vet.md#reporting-incomplete-analyses).
Native vet flags and `#+vet` tags work on Odin-compatible `.thor` source.
Source-sensitive checks inspect a separate source-preserving view before normal
Thor compilation, retaining native diagnostics, package scopes and opt-outs.
That view also supports `fn`, immutable declarations, parameter contracts,
f-strings and comprehensions, preserving user reads, calls and loop bindings.
Original formatting is checked before projection; generated helpers do not create
native-vet findings. See [the policy details](../docs/native-vet-audit.md). Native
`-vet-when-shadowing` and `-vet-semicolon` support the full Thor lowering.
See [CLI compatibility](../docs/cli.md) for details.

Vetting examines project `.thor` files beneath the selected package directory,
using the active target and build flags. Unused warnings use original source
bindings so generated helper arguments and ownership cleanup cannot hide unused
values. Known inactive `when` arms and excluded files are skipped. For unresolved
generic/native compile-time conditions, potential uses are retained and warnings
inside uncertain arms are omitted. Immutable advice includes active conditional
declarations and native declaration attributes; uninitialized bindings are skipped.
Procedure conversions conservatively skip conditional declarations, generic
procedures, attributed procedures and the entry point. Procedures returning owned or
reference-bearing data also require manual review, since `fn` can change result
ownership and allocation behavior. Converting a procedure to `fn` enables its
normal automatic compile-time evaluation with known arguments.

Immutable corrections preserve ownership choices across API results and
reference-capable output parameters. Compiler-accepted locals whose readonly
references actually escape receive manual-review warnings. Unrelated output
parameters and independent value copies support ordinary immutable corrections,
even when the procedure also calls conditional SDK implementations. Conditional
writes through the affected value remain protected.

Reviewed library argument contracts also bound vet's portability analysis.
Printing a value, including a slice or a zipped row, permits immutable advice
without inspecting platform-specific printing internals. Formatting buffer
destinations still require writable access. These contracts preserve package
identity through aliases and selected imports. Procedure results are cached per
analysis mode within each vet invocation. Unreviewed conditional implementations
retain conservative handling.

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

In a terminal, diagnostic labels and source underlines are red for errors and
yellow for warnings. Redirected output and JSON diagnostics remain plain text.
Native point or partial-word diagnostics underline the complete keyword or
identifier in the original source. Expression diagnostics use their mapped
expression boundaries, and editor positions account for UTF-16 columns.
Set `NO_COLOR=1`, `CLICOLOR=0`, or `TERM=dumb` to disable terminal colors.

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
builtin namespace when needed. See the [diagnostic review](../docs/diagnostic-review-2026-11-01.md)
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

TLS uses this same compiler pipeline on open/change/save, including unsaved
dependencies. It translates ranges to LSP coordinates, preserves the editor's URI
for symlinked files, clears resolved errors, and discards superseded checks.
Program/test stdout and stderr remain separate from compiler diagnostics.
The [Thor debug adapter](../editors/vscode/DEBUGGING.md) also maps debugger stack
frames and stepping. Stack traces printed directly by the native runtime still
use its own locations.

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


## Tests and rejection examples

```sh
THOR_TEST_PREBUILT=1 python3 tools/test_compiler.py
```

For parallel test workers, prebuild the compiler and protocol fixtures once.

```sh
cargo build --release --manifest-path rust/Cargo.toml --bin thor-rust --examples -j 2
```

Set `THOR_TEST_PREBUILT=1` for every worker. This keeps Cargo setup from replacing
the shared executable during another worker's launch. A missing prebuilt compiler
is an error. Ordinary sequential test setup continues to build with Cargo.

Linux address-sanitizer tests also require the compiler-rt development package
matching Clang (for example, `libclang-rt-18-dev` with Ubuntu 24.04's Clang 18).
Icon regression tests use Python's Pillow package (`python3-pil` on Ubuntu).
These are test dependencies; ordinary Thor installation and builds do not need
them.


The opt-in [upstream integration run](../docs/upstream-integration.md) compares
pinned Odin projects, migrated Thor copies and vet-corrected copies. Its
[runner instructions](../tests/integration/README.md) document dependencies and
resource limits.

The suite compiles and runs actual generated programs with Odin. It checks
accepted programs, rejection behavior, evaluation counts/order, import privacy
and shadowing, native syntax, generic constraints, contextual/empty/nested arrays,
mixed packages, command behavior, and the main demo. Each test uses an isolated
temporary source and output directory.
The suite also builds TLS and runs stdio LSP tests against the actual executable.
The [post-v1 audit](../AUDIT.md) records cross-feature fixes, coverage and concrete
remaining editor limitations. Contextual enum navigation, completion and rename
through switch-arm arrays have dedicated regression coverage.
The [November hardening report](../docs/hardening-2026-11.md) inventories the
features added since the purity audit, their documentation, interaction tests,
and remaining conservative boundaries.

Readable rejection examples are under [tests/fixtures](../tests/fixtures). Each
directory includes `main.thor` and the expected diagnostic fragment:

```sh
./thor check tests/fixtures/generic_constraint
```


## Implementation and current boundary

- [frontend/parser](../frontend/parser) adapts Odin's real tooling parser and retains
  its original license. [frontend/main.odin](../frontend/main.odin) exports AST spans
  and roles as JSON. [frontend/tokenizer](../frontend/tokenizer) owns scanning while
  retaining installed Odin token/AST types for TLS and formatter compatibility.
- [rust/src/lower.rs](../rust/src/lower.rs) performs source-preserving lowering;
  [rust/src/semantics.rs](../rust/src/semantics.rs) and the contract passes resolve
  the type shapes, call targets, effects and storage permissions needed by Thor.
- [rust/src/project.rs](../rust/src/project.rs),
  [rust/src/frontend.rs](../rust/src/frontend.rs) and
  [rust/src/backend.rs](../rust/src/backend.rs) manage source discovery, the shared
  frontend, generated output and native Odin invocation.
- [rust/src/evaluation.rs](../rust/src/evaluation.rs) coordinates compile-time
  execution with the evaluator, scheduler and persistent cache modules.
- [rust/src/emit.rs](../rust/src/emit.rs) and
  [rust/src/diagnostic.rs](../rust/src/diagnostic.rs) retain composable source maps
  and translate backend diagnostics to original source locations.
- [tls](../tls) adapts OLS for Thor syntax and semantic information. The
  [VS Code extension](../editors/vscode) hosts its language client, source-aware
  debugger integration and editor commands.
- [thorlang](../thorlang) is the frozen Python reference, retained for historical
  comparisons. New compiler changes belong in Rust.

The language reference was checked against Odin `dev-2026-10:8169255b9`; see
[reference validation](../tools/check_language_reference.py) and the
[platform validation reports](../docs/validation).
An Odin AST/token API change may require refreshing the shared frontend.
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

Automatic constant evaluation includes per-instance generic scheduling,
persistent caching and independently owned allocated results.
`fn` and `set` remain valid ordinary Odin identifiers. The
[language specification](../docs/language-spec.md) preserves the numbered reference;
`demo/main.thor` is the evaluation program. Follow
[DEVELOPMENT.md](../DEVELOPMENT.md) for each compiler → TLS → diagnostics batch.
