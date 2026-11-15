# Native vet compatibility audit

The audit covers all 17 `-vet*` options advertised by the installed Odin
compiler, plus `-strict-style`. Both Thor implementations use the policy in
`cli/help.json`. Diagnostics from supported checks refer to original source.

| Native policy | Thor-containing projects | Reason |
| --- | --- | --- |
| `-vet-semicolon` | Supported | Retains native terminator errors. Thor's `--vet=semicolon` additionally provides source edits. |
| `-vet-when-shadowing` | Supported | Retains checks for global `when` declarations shadowing builtins. |
| `-vet-packages:...` | Supported | Selects packages for supported native checks. |
| `-vet` | Rejected | Bundles several incompatible checks below. |
| `-vet-unused`, `-vet-unused-variables`, `-vet-unused-procedures`, `-vet-unused-imports` | Rejected | Generated declarations, query helpers, imports and aliases change native usage analysis. |
| `-vet-cast` | Rejected | A redundant cast in an f-string can become part of a generic helper, bypassing native checking. |
| `-vet-shadowing` | Rejected | Generated comprehension bindings can hide the original lexical shadowing relationship. |
| `-vet-style`, `-vet-tabs`, `-strict-style` | Rejected | These inspect generated formatting and constraints, producing false source errors. |
| `-vet-nil-deref`, `-vet-uninitialized`, `-vet-atomic-access` | Rejected | Passing source values into generated captures changes the native flow analysis. Original reads can be missed. |
| `-vet-using-stmt`, `-vet-using-param` | Rejected | Thor's automatic `using-stmt` feature tag overrides these native prohibitions. |

Every option remains available for Odin-only projects. A mixed project or a
native entry point importing Thor source is a Thor-containing project.
Excluded platform/test files do not count. Package selectors do not waive the
restriction. New unaudited `-vet-*` options are also restricted to Odin-only
projects. Enabling an incompatible check with `#+vet` in a Thor file produces
a diagnostic at that tag; disabling checks with `!` remains supported.

`--vet` operates on original Thor bindings and conventions. It is not an
equivalent implementation of Odin's native checks: unused import/procedure,
cast, shadowing, nil-flow and atomic-access policies remain unavailable for
Thor source. Implementing those requires checks on original Thor semantics,
rather than filtering diagnostics after native checking.

`thor build --vet` reports convention findings as errors and exits unsuccessfully.
`-ignore-warnings` does not suppress these errors. `thor vet`, `check --vet`
(including TLS), and other commands retain warnings, with existing
`-warnings-as-errors` and warning suppression options. Validation of proposed
corrections filters native flags by its actual action (`check`, or a test
`build`): artifact options such as `-o:speed` must not be forwarded to `check`.

Regression coverage is in `tests/test_native_vet.py`, with CLI, warning-policy,
correction and live TLS coverage in the existing vet suites. The matrix checks
every catalog option against valid native-only code and actual native
violations, comparing errors with Odin. It also covers original Thor errors,
generated helpers, f-strings, SOA aliases, comprehensions, typed loops, big
numbers, compile-time execution, mixed/imported/excluded files, overlays,
source tags, package selectors, selected checks, optimization, object output,
debugging flags, and unchecked release flags. TLS checks retain warning
severity, original UTF-16 ranges, and quick fixes for the same findings that
fail a build.

During the audit the installed Odin compiler intermittently crashed while
reporting vet errors with parallel checking, including on native `.odin`
input invoked directly with `odin check`. One failure reported concurrent
out-of-bounds accesses in Odin's `src/array.cpp`. Serial error-oracle tests
use `-thread-count:1`; no retry or change to Thor's default worker count hides
these failures. If this upstream crash occurs, `-thread-count:1` is an available
workaround, including for `thor vet`.

### Native scoped-vet parser crash

Migration validation isolated an additional native-only reproducer on the same
installed Odin revision. Save the following as `main.odin` in a directory.

```odin
package main
import "core:fmt"
main::proc(){fmt.println(7)}
```

```sh
odin check /path/to/directory -vet-when-shadowing -vet-packages:main -json-errors
odin check /path/to/directory -vet-when-shadowing -vet-packages:main -json-errors -thread-count:1
```

On macOS arm64, the default-worker command produced 2 SIGSEGV failures in 40
runs. All 40 single-worker runs passed. A generated native input produced 3
failures in 40 default-worker runs and passed all 40 single-worker runs. Recent
crash reports faulted in `ast_file_vet_flags(AstFile*)` while parsing imported
files. This identifies a native parsing failure independently of Thor and of the
previous LLVM type-name-cache issue. The underlying concurrency cause remains
unconfirmed. Scoped-vet regression fixtures use one native worker.

## Translated Odin commands

`thor migrate --command` adds `--odin-compatible`. This explicit mode retains
native vet/style options through a source-preserving checking pass. It supports
`fn`, immutable declarations and `#mut` parameters added during vet corrections.
The ordinary Thor pass also validates and compiles the program. Unsupported
checking-view features produce a source error identifying the feature.
Migrated `#+vet` metadata also restores per-file native policies and exemptions
in this checking view. Both `// thor-migrate #+vet ...` and the earlier
`// Odin vet ...` spelling are recognized before the package declaration.
The restrictions above continue to describe ordinary Thor compilation.
Command translation itself requires neither source inspection nor an Odin installation.
