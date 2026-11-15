# Odin compatibility corrections

The five implementation defects from the November 13 audit are corrected in
both the Rust compiler and Python reference. The original checked/unchecked
JSON reports remain historical evidence, rather than expected-failure tests.

- **Native type syntax:** bracket lookahead recognizes parenthesized element
  types and `bit_field` types. Fixed arrays, slices, and typed compound literals
  retain native parsing.
- **Resources:** computed `#load`, `#load_hash`, `#load_directory`, and `#exists`
  paths resolve beside the original source. Odin still evaluates the constant
  expression, including generic constant parameters and `#config` values.
  Absolute paths and user collection mappings retain their meaning. No extra
  compilation step evaluates resource paths.
- **Source introspection:** file, directory, line, procedure, and location
  expressions describe the original lexical source even inside emitted helpers.
  Omitted caller-location and caller-expression defaults are supplied at the
  original call site. Explicit overrides, named arguments, overloads, generic
  procedures, imported procedures, and procedure-value calls retain their
  behavior. Native normal/filename/none/obfuscated modes remain effective.
- **Project identity:** file inputs use the containing directory's project
  name. Generated layouts retain the original package basename, including with
  an unrelated output directory or external dependencies. Native project-name
  constants and build tags therefore see the original identity. This does not
  change executable placement or naming.
- **Collections:** injected support imports use a collision-free private
  collection. Original user imports are remapped to their actual generated
  packages, while shared support's internal imports keep the installation
  binding. A custom `thor` collection no longer captures sequence/set/numeric
  support or requires changes to user source.

The deeper review also found a lifetime defect in the copied frontend parser:
project-name tag groups referenced a scratch descriptor array after it was
freed, and earlier groups could be invalidated by later growth. Groups now own
separate descriptor arrays. Regression coverage includes multiple comma groups,
negative names, enough names to force growth, and tagged native dependencies.

The generated directory can gain a package-name subdirectory to retain native
identity. Use the path printed by `thor transpile` rather than assuming the
entry file sits directly at the output root. Source maps, original diagnostics,
TLS overlays, standalone ASan reports, and normal executable paths remain covered.

## Verification

`tests.test_native_source_identity` compares identical Odin and Thor sources
through both compiler implementations. Every original reproduction for these
five defects runs in checked builds and with
`-o:speed -disable-assert -no-bounds-check -no-type-assert`. Additional tests cover
configuration/generic resource paths, imported native sources, metadata defaults,
anonymous procedures, source-display flags, Unicode columns, private-collection
collisions, and tag storage. Verdicts compare process output/status in Python.
`tests.test_native_source_identity_tls` covers unsaved diagnostic recovery after
metadata lowering. Existing compilation, diagnostics, library, and debug tests
exercise the changed layout and support bindings.

The 110-probe audit also remains runnable in both configurations. Naming cases
(`or_panic`, contextual `fn`/`set`, and numeric-type import aliases) remain outside
this change as requested. Negative indexing, debug allocator policy, and the
explicitly documented native vet/style restrictions remain intentional Thor
behavior. Passing this corpus is evidence for the tested constructs, not a proof
that every possible Odin source is equivalent.

The final [checked](fixed-checked.json) and [unchecked](fixed-unchecked.json)
reports agree between Rust and Python: all 21 fixed/control cases match Odin.
Across the entire historical corpus, checked builds have 83 identical outcomes,
17 retained name/policy rejections, and four retained runtime differences;
unchecked builds have the same 83/17 split and two runtime differences (the
negative-bounds cases are excluded from unchecked execution). These retained
differences belong to the separately documented naming and policy decisions.

Additional successful validation included 80 Rust unit tests, 122 TLS tests,
156 compilation/diagnostic/memory checks, the native debugger-map consumer, and
two real CodeLLDB breakpoint/frame tests. The frontend itself was also built
with assertions, bounds checks, and type assertions disabled and used to parse
the type/tag regressions. Unicode resource paths and caller-expression literals
are covered, including supplementary Unicode characters that cannot be emitted
as JSON surrogate escapes in an Odin string.

One combined native-vet validation run terminated inside Odin with SIGSEGV.
The isolated 11-test CLI rerun, including those same native-vet flags, passed;
the crash did not reproduce there. No automatic retry or suppression was added
to compilation. This observation remains separate from passing Thor assertions.

## Deferred branch locations

The deeper review found missing parser support for Odin's `#branch_location`
inside `defer`. This directive is now supported by both Thor implementations
and TLS. For example:

```odin
package main
import "core:fmt"
main :: proc() {
    defer fmt.println(#branch_location)
    return
}
```

Odin selects the exit that executes each deferred statement. Thor translates
the resulting location back to the original file, line, column, and procedure.
Lowered expression helpers carry that native location as an ordinary value.
A private read-only mapping table is emitted only for procedures using the
directive, with no additional generated files or runtime allocations. Native
`normal`, `filename`, `none`, and `obfuscated` display modes are supported.

Regression tests compare both Thor implementations directly with Odin for
explicit and implicit returns, `break`, `continue`, optional exits, case
fallthrough, nested defers, imported Odin source, Unicode positions, builtin
shadowing, and lowered indexing, switch expressions, comprehensions, and
f-strings. Checked, unchecked release, and debug runs cover execution order and
cleanup. TLS tests cover native location types, field completion, and unsaved
placement errors. Debugger tests cover internal mapping code and stepping into
the original deferred user call.

The installed Odin compiler (`dev-2026-10:8169255b9`) still has an upstream
limitation for this directive in anonymous procedure bodies. Compiling the
following native Odin program with `odin run example.odin -file -thread-count:1`
reliably triggers its `p->uses_branch_location` backend assertion at
`src/llvm_backend_expr.cpp:4643`:

```odin
package main
import "core:fmt"
main :: proc() {
    p := proc() { defer fmt.println(#branch_location) }
    p()
}
```

The same backend limitation affects Thor's emitted anonymous procedures. Named
procedures, including nested named procedures, pass the native comparisons.
The [Odin issue inventory](../../upstream/README.md) links its standalone native
reproducer and the earlier parallel code-generation investigation.

## Project migration follow-up

The [Odin migration command](../odin-migration.md) exposed two additional
compatibility cases. Configuration keys are now treated as native metadata by
both lowering implementations. All five colliding identifier spellings preserve
their original `-define` names, including a parenthesized `or_panic` key in the
shared parser. Local `using` statements and mutable using declarations also take
effect at their source position. A later projection can no longer capture an
earlier reference to a global or enclosing binding. Migration fixtures compare
the native and converted executables, and TLS tests cover configuration-key
diagnostics after unsaved edits.
