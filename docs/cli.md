# Thor commands and help

Thor uses one command interface for its source features and the compatible native
compiler operations. A source argument defaults to `.` for compilation commands.
Files ending in `.thor` or `.odin` are detected automatically; an explicit native
`-file` remains accepted with a file input. Collection paths such as `thor:basic`
and `core:fmt` can also be used as package inputs.

```sh
thor                         # Short command overview and common options
thor help                    # Normal Thor options, grouped by command
thor help build              # Normal options for one command
thor help -full               # Every compatible compiler option
thor help build -full         # Every option applicable to build
```

`-full` is a help option, not a build flag. The compatible `thor build -help`
spelling shows build help without loading a project. `-h` and `--help` are no
longer compiler options. Arguments after `--` still reach the program unchanged,
including arguments named `--help`.

The common help catalog ships with Thor and works without an installed native
compiler. Exhaustive help also incorporates additional options advertised by the
selected toolchain. Normal builds do not run help-discovery subprocesses. Both
the Rust compiler and Python reference use the same command/option catalog.

## Commands

| Command | Behavior |
| --- | --- |
| `new NAME` | Create a project with the starter source and VS Code configuration. NAME is required. |
| `migrate [DIRECTORY]` | Copy and validate an Odin project, or translate repeated `--command` invocations. File migration requires DIRECTORY. `--output-dir` selects the parent receiving the project folder. `--dry-run` validates and discards the copy. |
| `build [source]` | Compile an executable or another native artifact. The default executable is `./<working-directory-name>`. |
| `run [source]` | Build and launch an executable; pass its arguments after `--`. |
| `check [source]` | Parse and type-check without producing a native artifact. |
| `test [source]` | Build and execute the package’s tests; runner arguments follow `--`. |
| `vet [source]` | Check Thor conventions. `--apply` applies verified corrections; `--ask` previews them and asks first. |
| `publish [source]` | Build and package an application using the existing publish settings. |
| `doc [source]` | Type-check, then display public documentation with original Thor signatures and filenames. |
| `strip-semicolon [source]` | Type-check, then remove unnecessary semicolons from original project files. |
| `transpile [source]` | Emit intermediate Odin source and print its path. |
| `ast [source]` | Print the original parser/AST data as JSON. |
| `bundle android [directory]` | Run the native Android bundler on an already prepared output directory (default `.`). |
| `version` | Print the Thor version (`--version` is also accepted). |
| `root` | Print the Thor installation directory. |
| `report` | Print Thor, native compiler, and system information for investigations. |
| `help [command] [-full]` | Show the standard or exhaustive option list. |

Information and help commands work in an empty directory. `report` remains useful
when the native compiler is unavailable. `--odin PATH` / `THOR_ODIN` selects the
native compiler for compilation, reports, full-help discovery, and bundling.

See [Odin project migration](odin-migration.md) for identifier renaming, external
dependencies, assets, editor configuration, validation, and review notices.

## Native flags

Compatible native spellings remain accepted, including `-define:NAME=VALUE`,
`-collection:NAME=PATH`, `-target:OS_ARCH`, `-o:MODE`, `-out:PATH`, sanitizer,
linker, and diagnostic options. The full help lists applicable commands and
finite value choices. Unknown newer native flags are forwarded for the selected
compiler to validate. Platform restrictions still come from that compiler.

Intermediate output preserves the original package directory name, including
when `--output-dir` has a different name. Consequently the generated entry may
be nested inside that directory; `thor transpile` prints the actual entry path.
This keeps `ODIN_BUILD_PROJECT_NAME` and `#+build-project-name` compatible with
Odin. Executable placement and naming still follow the working directory.
User collection mappings, including `-collection:thor=PATH`, remain available
to source imports and resources; compiler support uses a separate private binding.

`build -build-mode:test` builds a test executable without running it. `test`
builds and runs tests, and also accepts an explicit `-build-mode:test`.
Default executable output is rejected if it would replace a Thor launcher,
including filesystem aliases. `run` accepts only executable mode; library, object, assembly and LLVM IR modes
belong to `build`. LLVM IR uses an output directory. Object and assembly modes
can emit multiple units; `-use-single-module` selects a single unit. `publish` also requires an executable. `-all-packages` retains
the native test/documentation meaning. `-thread-count:N` limits native workers
and the Rust frontend’s independent parsing work (the frontend caps at 32).

Thor’s `--vet` and native `-vet-*` policies are distinct:

- `--vet[=immutable,pure,unused,semicolon]` checks original Thor conventions.
  Findings are errors for `thor build --vet`, including with `-ignore-warnings`.
  Other commands, including TLS `check` and standalone `vet`, report warnings;
  `-warnings-as-errors` can make those strict too.
- `-vet-when-shadowing` and `-vet-semicolon`
  work with Thor source and retain native error behavior. They can be combined.
- Native vet/style checks default to project package names, avoiding unrelated
  reports in installed support libraries. Explicit `-vet-packages:` and
  `-strict-style-packages:` override that scope.

The remaining native policies require an Odin-only project: `-vet`, `-vet-cast`,
`-vet-shadowing`,
`-vet-unused`, `-vet-unused-variables`, `-vet-unused-procedures`,
`-vet-unused-imports`, `-vet-style`, `-vet-tabs`, `-vet-nil-deref`,
`-vet-uninitialized`, `-vet-atomic-access`, `-vet-using-stmt`,
`-vet-using-param`, and `-strict-style`. Thor rejects these explicitly if any
active project or dependency source is `.thor`, even with a package selector.
Native unused checks can flag generated helpers; native style checks inspect
generated formatting; native flow analysis can miss reads hidden in captures;
Thor's automatic using feature tag overrides the native using policies.
Use `--vet=unused` for original Thor bindings, imports, data types and procedures, and
`--vet=semicolon` for original Thor terminators. Unused imports can be removed
with `thor vet --apply`; unused values, types and procedures remain warning-only findings
without removal edits. These are not replacements for native procedure, cast,
shadowing, pointer-flow, or atomic-access checks. Generic
formatting helpers can hide redundant casts, and generated comprehension
bindings do not retain the original lexical shadowing relationship.

The same restriction applies to enabling these checks with `#+vet` in Thor
files. Explicit opt-outs such as `#+vet !unused` remain valid. Native `.odin`
file tags retain Odin semantics. New unaudited native `-vet-*` options are
restricted to Odin-only projects until audited. `thor help build -full` marks
restricted flags and explains the reason for each.
The [native vet audit](native-vet-audit.md) records the checks and regression coverage.

Source checks require `check`, `vet`, or another command that invokes native
checking; `transpile -vet` does not silently skip the checks.

Compiler reports such as `-show-import-graph`, `-show-unused-with-location`,
`-show-defineables`, and `-print-linker-flags` retain their output. Source paths
and available locations refer back to original files. `-show-timings` adds Thor
preparation/lowering/native durations before the native timing breakdown.
Informational stderr output is separate from warnings: it is not promoted by
`-warnings-as-errors`, and JSON diagnostics place it in `information` alongside
`errors`. Exports of dependencies and other text reports also restore source
paths. `-target:?`, `-microarch:?`, and `-target-features:?` show supported values
without loading a project. Compile-time helpers do not inherit final-artifact
report, documentation, or vet/style flags.

Text errors and warnings put the source location on its own line, followed by
the severity, message, source excerpt, and underline:

```text
/path/project/main.thor(13:1):
Warning: Type 'Strategy' declared but not used
    Strategy :: enum {
    ^^^^^^^^
```

This layout also applies with `-terse-errors` and Unix-style positions. JSON
diagnostics and language-server diagnostics retain their structured format.

## Source tools

`doc` accepts one entry package per invocation; use `-all-packages` for its
dependencies, or run separate commands for unrelated packages. It supports `-short`, `-in-source-order`, `-all-packages`, and native
`-doc-format -out:PATH` interchange exports. Text documentation preserves Thor
signatures such as `fn` and `#mut` and the comments from the checked native
interface. Interchange exports preserve native type/entity indexes while
restoring original file paths, declaration locations and initializer text.
Thor currently understands Odin’s 0.3.2 interchange layout; a different layout
produces an explicit compatibility error. The interchange type system remains
Odin’s, so consumers that want Thor-specific syntax should use the restored
initializer/signature text.

`strip-semicolon` edits project sources after successful checking. It preserves
strings, comments, required same-line separators, and control-header separators,
as well as file permissions and line endings. Edits use the same staged-write
and concurrent-change checks as vet corrections. `-keep-temp-files` retains
originals beside the sources as `<filename>~backup.thor-temp`; existing backups
are never overwritten. Imported packages outside the source directory are not
edited. Cleanup requires saved files and does not accept editor overlays.
Source tools require mapped output, so `--raw-diagnostics` is rejected for them.

`bundle android` is the compatible native Android packaging command. It expects
the native bundler’s Android directory layout, SDK/tools, and signing setup;
it does not cross-compile a Thor program or extend `publish` to Android.
`thor help bundle -full` lists signing options.

Explicit `--odin-compatible` builds preserve native command defaults and allow
native vet/style policies through the Odin-compatible source path. Migration
adds this option to translated compilation commands. The [migration guide](odin-migration.md) describes
command-only translation, validation, quoting, and native compatibility.
