# Migrating an Odin project

`thor migrate` copies an Odin project, converts its source files, and validates the
result. Run it from the directory that should contain the new project.

```sh
thor migrate /path/to/odin/project
thor migrate /path/to/odin/project --output-dir ./converted
thor migrate /path/to/odin/project --dry-run
thor help migrate
```

The default destination is `./<original-folder-name>`. `--output-dir` selects
the parent directory, so `--output-dir ./converted` creates
`./converted/<original-folder-name>`. Missing parent directories are created as
needed. An existing destination project directory is an error. Source and destination directories must be separate, with neither
inside the other. A project directory is required explicitly.

## Conversion

The command copies the current working tree, including assets, uncommitted files,
and editor settings. It skips `.git`, `.hg`, `.svn`, `.thor-cache`, `.thor-build`,
and `.thor-rust-build`. It preserves regular-file contents and permissions except
where source or supported editor configuration needs conversion. Large assets
are streamed during copying.

Every regular `.odin` source becomes `.thor`, including sources in subpackages,
tests, and platform-specific files. Directories retain their names. Filename
collisions stop the conversion.

Thor's five colliding identifier spellings are `fn`, `set`, `or_panic`, `Integer`,
and `Rational`. Migrated bindings receive a fresh suffix such as `fn_odin`, with a
number added when necessary. Their references, import aliases, named arguments,
and labels follow the rename. Comments and ordinary string contents retain their
text. Existing external SDK declarations remain in their original packages.

Struct, enum and bit-field member spellings, including `or_panic`, remain intact.
This preserves their reflection names and generic SDK
interoperability. Unqualified promoted `fn`, `set`, and `or_panic` references receive
parentheses where needed to disambiguate Thor syntax. A reference to a renamed
type or constant in a member declaration follows that binding.
Foreign and exported declarations retain their original native link names.
A type declaration renamed during migration can still expose its new name
through `typeid_of` and formatting. See the [compatibility contract and example](extension-contracts.md#odin-compatibility-and-migration).
Configuration keys and package headers retain their original spellings, including
parenthesized reserved configuration names, so existing `-define` and package
policy switches continue to select their values.

Known source-file paths in `#load`, `#load_hash`, and `#exists` are updated when
they refer to renamed files inside the copy. This includes constant paths,
concatenated paths, and paths passed into generic procedures. Absolute paths to
those files are rebased through `#directory`. Arbitrary runtime filename strings
and external asset paths keep their existing meaning and may need manual updates.
Embedding the contents of converted source naturally observes the converted text.

Internal symlinks point into the copied tree. A symlink used as an `.odin` source
is materialized as an independent converted file, with a report entry. Other
external symlinks continue to reference their external targets. A project that
uses external package directories still requires those dependencies.

Collection-qualified imports targeting packages inside the project
become relative imports into the copy. External package imports retain their
existing collection paths. A collection-qualified command entry is also rebased
when it selects a copied package, including collections rooted above the project.

## Compiler command translation

Translate one or more literal Odin invocations using repeated `--command` options.

```sh
thor migrate --command 'odin build ./game -o:speed'
thor migrate --command 'odin build . -o:speed' --command 'odin test .'
```

The first command prints `thor build ./game -o:speed --odin-compatible`. Command-only translation
uses command text and the installed flag definitions. It works with missing
source paths and an unavailable Odin compiler. It preserves defines, quoted
arguments, empty arguments, and program arguments following `--`. File inputs selected with `-file` and ending in `.odin` become `.thor`. Directory
names retain their original extensions. Explicit paths to the Odin executable are
preserved through Thor's `--odin` option.

Combine copying and translation by providing the source project explicitly.

```sh
thor migrate ./game --output-dir ./converted \
    --command 'odin build ./game -o:speed' \
    --command 'odin test ./game -all-packages'
```

This creates `./converted/game` and prints commands targeting that copy. Project
paths, collection paths, and recognized artifact/report paths inside the source
project are rebased. Each command is interpreted from the directory where
`migrate` is invoked. The emitted commands are intended to run from that same
directory. Text inside defines and runtime arguments retains its original value.
Opaque linker and assembler flag strings also retain their contents.
Commands targeting source files outside the copied project retain their native
`.odin` paths and continue to use those external files.

Translated commands go to stdout. Migration progress and review notices go to
stderr. An invalid command anywhere in a batch produces an error before printing
translated commands or copying the project. `--command` accepts POSIX-style
literal argument quoting. Shell expansion, pipelines, redirection, and compound
scripts receive a conversion error. Single-quoted strings may contain literal
shell punctuation.
Absolute Windows drive paths retain their backslashes, including inside flags
such as `-out:C:\build\game.exe`; native Windows commands also accept UNC paths.
Quote paths containing spaces. Printed commands continue to use literal
argument quoting and do not execute a shell.

### Native policy compatibility

Translated compilation commands include `--odin-compatible`. This compiler
option preserves Odin's default output naming, native debug defaults and native
sequence indexing. Signed indices and slice endpoints go directly to Odin,
including in generic code and compile-time calls. For example, a runtime
index of `-1` in a checked build triggers Odin's bounds check. Running the
same converted file with a plain `thor run` uses Thor's from-the-end indexing.
Native vet/style policies and source restrictions such as `-bedrock` are checked
against a source-preserving Odin view. The ordinary Thor lowering then checks
and compiles the program with Thor's purity and immutable-slice rules.

```sh
thor migrate --command 'odin build . -vet -o:speed'
# thor build . -vet -o:speed --odin-compatible
```

This allows the translated command to keep working after `thor vet --apply`
introduces `fn` and immutable declarations. Native diagnostics retain their
original Thor file, line and column. The checking view also supports `#mut`
parameters. A Thor feature that this view cannot represent produces a source
error identifying the unsupported feature.

Thor-aware `--vet` remains available when adopting Thor-only syntax. The explicit compatibility
option suppresses the automatic tracking allocator and added ASan defaults.
Those can still be selected explicitly with Thor options.

Use the test build mode to vet and correct test declarations and their imported
packages. This builds the tests for validation and applies the verified edits.
The test command executes the corrected suite.

```sh
thor vet ./tests -build-mode:test -all-packages --apply
thor test ./tests -all-packages
```

Both the Python and Rust launchers support these migration commands. The Python
launcher delegates migration and explicit `--odin-compatible` builds to the
installed Rust executable. Ordinary Python compilation remains independent.

## Validation and configuration

Migration compiles the original and converted projects into scratch directories
before publishing the copy. Linking is included. Test executables are built
without running their tests. Projects containing only test files are supported.
When original test code compiles successfully, default migration also validates
the corresponding converted test build. Original test compilation failures are
reported while allowing a successful ordinary executable migration.

Commands supplied with `--command` select their source entries, defines, targets,
collections, build modes, and test configuration for validation. `run` invocations
are validated through compilation. `check` invocations use checking. Command-only
translation performs text translation directly.

A failed original or converted build leaves the destination unpublished. Source
bytes, asset metadata, permissions, and link targets are checked for concurrent
changes before publication. `--dry-run` performs validation and discards the
staging copy, while printing the planned translated commands.

Select a configuration directly when migrating a project without command text.

```sh
thor migrate /path/to/project --output-dir ./converted \
    -define:MODE=2 -collection:local=packages
```

These direct collection paths resolve from the original project. Collections
inside the project are rebased into the copy. External collections remain
external. When combined with `--command`, direct compiler flags apply to every
supplied command and are included in the emitted commands. `--odin PATH` or
`THOR_ODIN` selects the native compiler. Compiler
artifact/report options belong inside `--command`. Their validation outputs are
redirected to scratch storage so original project files are preserved.

Inactive and archived source files are also parsed for renames. A file that
cannot be parsed is copied with its original contents and reported for review.
Other target/define combinations require their normal build and test commands.
Native `#+vet` tags become migration metadata comments with a notice. For
example, `#+vet !shadowing` becomes `// thor-migrate #+vet !shadowing`.
Thor restores these file policies in its source-preserving native checking
view, including file-local exceptions to command-line vet policies. Existing
copies using the earlier `// Odin vet ...` spelling remain supported. Ordinary
comments after the package declaration do not activate file policies.

`THOR_FRONTEND_ODIN` can select the Odin compiler used to build Thor's parser
when a project requires an older backend compiler. `--odin` continues to select
the standard library, target definitions and final native compilation.

External Odin declarations named `or_panic` can be accessed through member
selectors and named arguments. Owned bindings continue to be renamed. Exported
multi-name declarations are split into individual declarations with their
original link names. Initializers keep their source order and execute once.

## VS Code and review

Structured `.vscode/tasks.json` tasks invoking Odin are switched to Thor and their
source-file arguments are updated. Native checking policies, explicit compiler
paths, and program arguments following `--` are preserved, using the same
compatibility option. Absolute paths inside the original project
are rebased to `${workspaceFolder}`. Matching CodeLLDB debug configurations use
`thor-lldb` and its source-map manifest. Simple compound shell commands, including
`mkdir ... && odin build ...`, are supported.

JSON with comments, complex shell scripts, and custom task working directories
receive review notices. Their configurations retain their contents for manual
adjustment. Other build scripts and references to source filenames also need
review. The destination preserves the project folder name and its default executable
name and `ODIN_BUILD_PROJECT_NAME`.

## Regression coverage

`tests/test_migrate.py` checks original-tree preservation, all audited keyword
collisions, scopes and shadowing, cross-package imports, SDK members, generic
procedures, configuration keys, resources, inactive files, symlinks, large assets,
FFI linking, editor configuration, concurrent migration, and publication failures.
`tests/test_migrate_commands.py` checks literal quoting, batches, source-independent
translation, native policy success/failure behavior, imported-package tests,
compiler modes, source preservation for reports, and execution of emitted commands.
Path-rebasing fixtures remove the original project before executing the emitted
command and keep external collection dependencies available.
The debugger suite also migrates and executes a native debug command, verifies
a breakpoint in the converted source, steps into a procedure, and inspects its
argument through CodeLLDB.
Fixtures build with both Odin and Thor and compare executable output. Ownership
and FFI paths also run with assertions, bounds checks, and type assertions disabled.
The native frontend's migration metadata is tested in an unchecked release build.

The macOS arm64 compatibility run additionally migrated and built 29 existing
projects from the local Odin project collections. Direct Odin builds of those
same projects passed. Two additional inputs failed their original Odin checks
and were left for source correction. GUI interaction was outside that build run.
