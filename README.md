# Thor

Thor is an experimental Odin superset that transpiles to ordinary Odin. This
milestone implements **T01–T23** from the reference specification in
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
examples. It finishes with `All T01-T23 demos passed.`

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
original source file, including in ordinary Odin dependencies. Explicit collection
resource paths and relative foreign objects/libraries use their original roots.
The manifest records the forwarded native flags, including asset collections;
assets are referenced in place, so retain the original project when compiling
generated output. `thor:` defaults to this checkout's [runtime](runtime/README.md)
collection and may be overridden with an explicit collection flag.

Other Odin flags are forwarded. Use `--odin /path/to/odin`, or set `THOR_ODIN`, to
select a compiler. Arguments after `--` are forwarded by `run` and `test`.

Generated files live under `.thor-build/`; the frontend binary is cached under
`.thor-cache/`, keyed by its source and Odin version. Source files are never
overwritten. A manifest allows stale generated sources to be removed on rebuild;
Thor refuses to reuse an unrelated nonempty output directory.
Generated trees are rejected as compiler inputs. File suffixes and build tags
follow `-target:os_arch`, including when checking a target different from the host.
`test` and `build -build-mode:test` include test-tagged files.

## Compiler diagnostics

`check`, `build`, `run`, and `test` report errors against original `.thor` files
(or original `.odin` dependencies), with source snippets and underlines. Parser,
Thor validation, and Odin type-checking errors use the same display. For example,
`values := [1, "wrong"]` underlines `"wrong"` in your Thor source, even though
the generated array declaration is much longer.

Unconditional top-level redeclarations in the same untagged file are checked by
the shared frontend before compile-time evaluation. TLS reports these immediately
while editing, even without compiler checking, with the duplicate name underlined
and the earlier declaration's line. Conditional declarations, tagged files,
cross-file conflicts and other type checks still use the backend.

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
| T07 | Caller-owned integer grouping and Unicode padding helpers in `thor:format`. |
| T08 | Map-backed `Set(T)` library in `thor:sets`, including mathematical, in-place, clone, iteration, and cleanup operations. |
| T09 | Shared Thor-owned lexer/parser and source emitter; native AST/token compatibility, comments, and byte spans retained. |
| T10 | Mixed-source/import graphs, target/tag selection, collections, resource/foreign paths, managed output and CLI flag forwarding. |
| T11 | Lexical symbols and shapes; contextual arrays through procedure aliases, overloads, generic specializations and private types. |
| T12 | `x :> expr` and `x: Type > expr`: runtime initialization once, shallow immutable storage, scoped mutation/address checks. |
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
    view[0] = value // shallow: referenced elements remain writable
}
```

Immutable records and fixed arrays also protect directly stored subobjects.
An existing pointer, slice, or dynamic-array view still permits writes to its
pointee/elements. Taking an immutable header's mutable address is rejected.
Native map entry assignment can change the map header, so it requires a mutable
header (a mutable shallow copy may still access shared entries). Ordinary Odin
copying and ownership rules apply; this feature does not add lifetime checking.
Compile-time branches and generic shapes are checked by Odin after lowering, so
inactive branches remain unchecked and reference views are distinguished from
directly stored arrays. TLS marks immutable symbols readonly and explains them
in hover; live compiler checks enforce mutations on unsaved text.

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
    text := f"{{label}} {label}: {value:'0'>12,}"
    assert(text == "{label} after: 0001,234,567")
    delete(text)
    delete(names)
}
```

Interpolation accepts quoted or raw backtick literals. `{{` and `}}` escape
literal braces. Fields contain real Thor expressions, including nested literals,
switches and interpolations. Parenthesize a ternary before a format separator.
The format grammar is `[[rune-literal]align][width][,]`: align with `<`, `>` or `^`,
use a literal decimal width, and add `,` for integer grouping. Width counts
Unicode runes. An explicit fill is a single-quoted Odin rune followed by an
alignment: `'界'^5` or `'0'>12,`. Rune escapes work too, such as `'\u754c'^5`.
Bare fills such as `界^5` and `0>12` are rejected. Omit the fill to use a space:
`^5`, `>12` or `12`. Fields execute once, left to right. The final string is owned by
the caller. Internal formatting buffers and owned nested interpolations are
released after use, including mixed branches such as
`f"{(flag ? f"value {n}" : borrowed)}"`: only the selected owned string is freed.
Named field values keep their existing ownership. A procedure that returns
allocated storage still needs an explicit lifetime chosen by the caller.

Expression switches currently require a default arm and exactly one expression
per arm. They support comma-separated cases and native ranges, check duplicate
constant cases, evaluate the selector once, and execute only the first matching
arm. Expected destination/argument/return types supply result context.
`#switch` has the same selector forms in statement and expression positions;
it uses native `when` semantics for constant selection, declaration visibility,
control flow, and unchecked inactive arms. Later conditions, including those in
a comma-separated case, stay unchecked after a match. Explicit `#run` follows
selected compile-time branches and known lazy conditions.

Sets use `|`, `&`, `-`, `~`, `==` and `!=`, with `|=`, `&=`, `-=`, `~=`
for in-place updates; `+=` inserts one element, while `-=` accepts an element
or another Set. Binary mathematical results own independent tables. Assignment
copies a shallow header. Anonymous Thor intermediates are cleaned up automatically:
`result := a | b | c` reuses one result table; only `result` needs deletion, and
named operands remain untouched. `clear(s)` keeps the table allocation;
`delete(s)` releases it. Keys retain their ordinary shallow
ownership. Native operations on other types keep their Odin meaning.

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
remaining editor limitations, including contextual enum navigation in switch arms.

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
`fn` and `set` remain valid ordinary Odin identifiers. The root `thor.odin` is the
reference specification; `demo/main.thor` is the evaluation program. Follow
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

The scheduler follows selected `when`/`#if`, both `#switch` forms and known lazy
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
`any`, procedure values, globals, ambient context, foreign/indirect calls, raw
addresses, unsafe casts, uninitialized storage and unapproved operations are rejected. Checks inside
inactive native `when` branches remain inactive.

The initial deterministic whitelist includes numeric conversions/operations,
length/type/size queries, `make` for local slice/dynamic buffers, `copy`, `delete`,
assertions, `core:strings.clone`, `core:slice.clone`, and Thor's scalar/string
formatters. A declared `fn` otherwise calls declared `fn` targets only. Explicit
allocator overrides and custom record formatters are rejected. Each checked
function uses private tracking scratch storage, including for interpolation;
all outstanding internal allocations are released on every return. Ordinary
`proc` allocation and f-string ownership retain their existing manual rules.

String/slice results own independent backing storage, including input views and
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

`pure.destroy` recursively frees owned strings/slices and their supported
aggregate members. `pure.clone(value)` creates another deep owned copy. Both
accept an explicit allocator outside pure code. Ordinary assignments remain
shallow: destroy an owner once; free an owned field before overwriting it. Do not
use recursive destruction on borrowed literals or shallow native containers.
Arbitrary user procedure results and anonymous arguments remain manual Odin
ownership. Eligible `proc "contextless"` targets cannot promote allocated returns;
use `fn` or a contextful `proc` for those results.

Runtime `x := #run work()` and automatically folded calls materialize fresh owned storage on every execution,
including loop iterations and cache hits. In constant contexts, strings and fixed
aggregates containing strings become ordinary borrowed Odin constants. A
slice-containing result needs a runtime binding (`:=`), or a pure consumer that
returns a scalar constant. Native slice constants are not implemented.

The value protocol preserves arbitrary string bytes, nil/non-nil empty slices,
zero-sized slice elements, fields, active union variants and exact float bits. It
serializes contents without padding or process addresses.

Each ready evaluation is compiled in a temporary native helper containing only
its required declarations/imports. User entry points, unrelated runtime globals
and package initializers are excluded; referenced runtime storage is rejected.
Opaque native imports are restricted to `base:intrinsics`, `core:math`,
`core:strings` and `core:slice`; user collections are inspected as project source.
Native value reads are limited to explicitly approved `core:math` constants;
an import name or a collection override does not confer purity.
Helpers use the same native defines/configuration as the final build. Cross-target
evaluation is rejected. Compilation errors map to original source; execution failures
point to the original call or `#run` expression. Helpers are deleted after use. Execution defaults
to five seconds per helper; set `--run-timeout 10` to change it (maximum 300).
Independent-call batching remains future work.

Successful helper results are cached in `.thor-cache/eval-v1` beside the entry
package. Keys include emitted transitive code/types/constants/arguments, native
flags/defines, compiler binary/version, Thor/frontend/runtime and installed
base/core source contents, plus static resource contents/existence. Keys are
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
on stderr. Eligibility checks still run before a cache lookup.

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
Source-location-dependent `#file`, `#directory` and `#line` values, including
constants derived from them, are rejected in pure execution until their values
can be kept identical across generated runtime code and helpers. Implicit deferred
procedure callbacks are also rejected; make eligible calls explicit in the body.
TLS infers call result types but does not execute procedures for hover values or
its own branch selection; live compiler checks perform the actual evaluation.
