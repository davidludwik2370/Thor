# Odin superset audit: November 13, 2026

**Historical baseline:** the five transpiler defects below have since been
corrected. See [fixes and verification](fixes.md). The naming-collision decisions
and intentional Thor behavior remain separate from those corrections.

Thor is not currently a strict drop-in superset of Odin. One added word is
globally reserved, several contextual-name cases are ambiguous, and the
generated-source pipeline can change native resource lookup and observable
program values. These findings reproduce in both the Rust compiler and the
Python reference. They are not specific to the Rust migration.

This pass changes no compiler or language behavior. It records original-source
reproductions and results so compatibility decisions and corrections can be
reviewed separately.

## Method and results

The audit reviewed every `p.thor`/`t.thor` parser/tokenizer branch and the lowering
dispatch, then exercised names, scopes, imports, native operators, generic and
concrete storage, callbacks, source directives, resource directives, project
identity and relevant build policies. Both compilers received identical source
text to the Odin baseline, with only the source extension changed. Builds use
matching optimization flags and `-thread-count:1`; selected programs are also
executed. Source-extension differences alone are normalized when comparing text.

The installed toolchain is Thor **0.41.6**, Odin
**dev-2026-10:8169255b9**, on macOS arm64.

| Configuration | Odin-valid cases | Same outcome | Thor rejects | Runtime output/status differs |
| --- | ---: | ---: | ---: | ---: |
| Checked, Rust | 104 | 63 | 29 | 12 |
| Checked, Python | 104 | 63 | 29 | 12 |
| Unchecked release, Rust | 102 | 63 | 29 | 10 |
| Unchecked release, Python | 102 | 63 | 29 | 10 |

The corpus deliberately targets suspected incompatibilities; these counts are
not a failure rate for arbitrary Odin projects. Six additional probes were
rejected by Odin itself and do not count as compatibility failures. The two
negative-bounds cases are omitted from unchecked builds because executing them
without native bounds checks would invoke undefined behavior. There were no
native compiler crashes in the final runs. The existing
`tests.test_odin_compatibility` suite also passes all **12 tests**.

The exact sources are in [cases.json](cases.json). Complete outcomes and
diagnostics are in [checked.json](checked.json) and [unchecked.json](unchecked.json).

## Confirmed gaps

### A01: `or_panic` is globally reserved

The Thor tokenizer unconditionally converts the identifier to Odin's
`Or_Return` token, using its original spelling to identify the new operation.
Consequently, all of these otherwise valid Odin names fail after renaming:

```odin
or_panic := 7
read :: proc(or_panic: int) -> int { return or_panic }
Item :: struct { or_panic: int }
Kind :: enum { or_panic }
import or_panic "core:fmt"
```

Loop labels fail too. This reservation is already documented, but is a real
exception to strict source compatibility. Making it a contextual postfix
operation would preserve the spelling while allowing identifiers in their
ordinary grammatical positions.

Source: `frontend/tokenizer/tokenizer.odin`, `scan`.
Cases: `local_or_panic`, `or_panic_field`, `or_panic_parameter`,
`or_panic_enum`, `or_panic_label`, `or_panic_import`.

### A02: `fn` lookahead can reject or silently reinterpret native code

`fn` is not globally reserved. Its contextual lookahead skips newlines and
assumes a signature followed by `{`, `->`, `where` or `do` is a pure function.
It does not first establish whether `fn` names an ordinary native value.

For example, with `fn :: proc() {}`, a call followed by an independent block
is misread as a procedure literal:

```odin
fn()
{ /* independent scope */ }
```

An arrow receiver such as `value := fn()->read()` is also misread. More seriously,
this complete native program succeeds through both compilers with different
meanings:

```odin
package main
import "core:fmt"
fn :: proc() -> int { return 7 }
main :: proc() {
    x := fn()
    {}
    fmt.println(typeid_of(type_of(x)))
}
```

Odin prints **`int`**. Thor prints **`proc()`**, because it treats the empty
block as the body of a pure-function initializer. An explicit semicolon after
the call prevents that particular ambiguity.

Source: `frontend/parser/thor_expressions.odin`, `thor_fn_follows`.
Cases: `fn_call_then_block`, `fn_initializer_then_block`,
`fn_initializer_silent_type_change`, `fn_call_arrow_receiver`.

### A03: `set` lookahead conflicts with native indexing

`set` is also contextual rather than globally reserved. Ordinary `set[0]` works
in some positions but becomes a Thor type/literal in others:

```odin
set :: [2]int{3, 4}
values: [set[0]]int // Valid Odin; rejected by Thor.
```

With a Boolean array named `set`, `if set[0] { ... }` and
`switch set[0] { ... }` fail too. Parenthesizing the native expression works:
`if (set[0])` and `[(set)[0]]int`.

Source: `frontend/parser/thor_expressions.odin`, `thor_set_type_follows`.
Cases: `set_array_length`, `set_bool_condition`, `set_switch_condition`.

### A04: `Integer` and `Rational` import aliases are misclassified

Ordinary local variables, user-defined types, fields and parameters can shadow
these predefined names. Import aliases do not reliably do so:

```odin
import Integer "core:fmt"
// Inside main:
Integer.println("ok")
```

Odin accepts this. Thor treats `Integer` as its numeric type and reports that
the type has no `println` field. `Rational` behaves the same way. The predefined
type fallback must honor namespace bindings as well as ordinary symbols.

Source: `rust/src/semantics.rs`, `logical_type`, and the corresponding Python
logical-type resolution.
Cases: `Integer_import_alias`, `Rational_import_alias`.

### A05: relative resource expressions resolve from the generated directory

Literal relative paths are rewritten correctly. A native constant or expression
with the same value is not:

```odin
PATH :: "payload.txt"
DATA :: #load(PATH, string)
// Also fails: #load("payload" + ".txt", string)
```

The native source file has a neighboring `payload.txt`; Thor looks beside the
generated `.odin` file and fails. The same issue affects `#load_hash` and
`#load_directory` with relative constant paths. `#exists(PATH)` is worse: it
silently changes from **true** to **false**. Paths derived from `#directory`
fail as well. An absolute constant path works.

The resource lowering currently rewrites a path only when the argument AST node
is a string literal. Correct native lookup must also cover constant expressions
and respect each source file's directory. Moving unrelated project assets into
the generated directory is not necessary to define the correct semantics.

Source: `rust/src/lower.rs`, `resources`, and Python resource lowering.
Cases: `load_constant`, `load_concatenation`, `load_directory`,
`load_hash_constant`, `load_directory_constant`, `exists_constant`.

### A06: native source introspection can observe generated helpers

Compiler/TLS diagnostic remapping does not restore values exposed to the program:

- `#file`, `#directory` and `#caller_location` can identify generated files.
- `#line` after an ordinary signed-index access changed from **7** to **13**.
- `#caller_expression(value)` changed from **`values[index]`** to a generated
  `__thor_...access...` call.
- `#procedure` inside a lowered native index refers to the helper procedure.

The last issue can change results, not just printed metadata:

```odin
values := [2]int{3, 7}
name := #procedure
fmt.println(values[int(#procedure != name)])
```

Odin prints **3**. Thor prints **7**. Source-sensitive expressions must retain
their original lexical procedure/location even when lowering moves them.
Location and caller-expression defaults also need to describe original calls.

Cases: `caller_location`, `source_directives`, `source_line_after_native_access`,
`source_procedure_inside_native_index`, `caller_expression_native_access`.

### A07: logical project identity follows generated paths

`ODIN_BUILD_PROJECT_NAME` can become the generated directory's basename instead
of the original package directory's basename. Both explicit file and directory
entry points reproduce this with `--output-dir`.

Valid `#+build-project-name <original-directory-name>` files are also rejected:
file-entry frontend selection uses the file stem, while the final Odin compile
can use a different generated-package directory name. The two compilers must
agree on the original logical project identity throughout selection and emission.

Sources: `rust/src/project.rs`, frontend construction and final native invocation;
`thorlang/driver.py`, `Project.discover` and emission layout.
Cases: `compiler_root_identity`, `compiler_project_name_directory`,
`build_project_tag_file`, `build_project_tag_directory`.

### A08: user `thor` collections collide with injected compiler support

An Odin project may define its own `-collection:thor=/some/directory`. Thor also
accepts that flag for trivial code, but an ordinary signed array index introduces
an internal `thor:sequence` import. The build then fails if the user's collection
has no such package. Compiler-injected dependencies should use a hygienic private
collection binding if existing user collection names are to remain compatible.

Cases: `custom_thor_collection_simple`, `custom_thor_collection_index`.

### A09: inferred-literal lookahead steals valid native type syntax

The lookahead deciding whether `[ ... ]` begins an inferred Thor literal does
not include all valid native element-type starters. In particular, these
ordinary type aliases are accepted by Odin and rejected by Thor:

```odin
Array :: [2](int)
Slice :: [](int)
Bits  :: [2]bit_field u8 { value: u8 | 1 }
```

The native typed literal `[2](int){1, 2}` is rejected too. `Open_Paren` and
`Bit_Field` are missing from the `type_follows` lookahead in
`frontend/parser/parser.odin`. Thor interprets these as inferred array values
and then tries to call them or parse the remaining type as a statement.
Preserving native element-type syntax should take precedence over the new
literal fallback.

Cases: `array_parenthesized_element`, `slice_parenthesized_element`,
`array_bitfield_element`, `array_parenthesized_literal`.

## Intentional differences requiring an explicit compatibility contract

- **Negative indexing/slicing:** a runtime negative bound traps under checked
  Odin but indexes from the end under Thor. This is the accepted Thor feature,
  and necessarily changes the meaning of some compiling native programs.
- **Debug builds:** Thor changes the default allocator to the tracking allocator
  and enables supported address sanitization. A comparison against
  `runtime.default_allocator_proc` changes from true to false. This follows the
  accepted debug-build policy.
- **Native vet/style policies:** the recently added restrictions reject flags
  such as `-vet`/`-strict-style` and source tags such as `#+vet unused` for Thor
  source. Those restrictions were deliberate, but a clean native program with
  such a source tag cannot simply be renamed. See the
  [native-vet audit](../native-vet-audit.md).

## Checks that did retain native behavior

The audit found no additional globally reserved word beyond `or_panic`.
`and`, `or`, `all`, `mut`, `run` and `f` remain ordinary identifiers. `not_in`
was already an Odin keyword. New infix `++` and `**` probes were rejected by
Odin itself; native prefix `**` expansion retained its behavior.

The tested native slice writes and addresses, concrete/generic promoted
pointers, independent array copies, map-header copies, dynamic-array pointers,
static locals, rodata copies, reference iteration, deferred calls, contextless
indexing, native bitsets/maps/matrices/enumerated arrays, optional results,
native iterators, scalar builtin shadowing and untyped string-to-cstring
arguments all retained their outcomes. Normal native callback defaults worked;
two suspected generic-default forms were already invalid in Odin. Separate
checks of grouped `#any_int`/`#no_alias` and generic promoted-pointer writes
with two distinct wrapper shapes also passed through all three compilers.

## Suggested correction order

1. Preserve original source-sensitive values and resource lookup (A05–A06).
   These can silently change correct program behavior.
2. Fix contextual `fn`/`set` disambiguation, native array-type lookahead and
   predefined-type namespace lookup (A02–A04, A09), including the silently
   changed initializer type.
3. Preserve logical project identity/build tags and isolate injected support
   collection names (A07–A08).
4. Decide whether `or_panic` remains a documented reservation or becomes
   contextual, and document the intentional negative-bound/debug/vet exceptions.

Fixes should promote the relevant reproductions into acceptance tests requiring
the native outcome; the differing outcomes here are observations, not approved
expected failures. A finite audit on one host cannot establish equivalence for
every program, target, ABI, toolchain revision or flag combination.

## Reproduction

The runner builds scratch copies and leaves user projects untouched:

```sh
python3 docs/odin-superset-audit-2026-11-13/audit.py --output /tmp/superset-checked.json
python3 docs/odin-superset-audit-2026-11-13/audit.py --unchecked --output /tmp/superset-unchecked.json
python3 docs/odin-superset-audit-2026-11-13/audit.py --case fn_initializer_silent_type_change --output /tmp/fn.json
python3 -m unittest tests.test_odin_compatibility -v
```

`--engine rust` or `--engine python` selects one Thor implementation.
`--work-dir PATH` chooses the scratch tree for inspecting generated source.
The runner records disagreements and exits normally after writing its audit;
it is not a passing regression-test verdict for the incompatible cases.
