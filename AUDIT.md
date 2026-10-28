# Post-v1 correctness and TLS audit

Audit date: 2026-10-25. Baseline: `dd73068` (0.7.0). Fixes: 0.7.1.
This is a review of the implemented T01–T23 features, their existing tests and
selected cross-feature interactions. It is not a proof over every combination
or every Odin platform/toolchain.

## Findings and fixes

### Semantic tokens could recolor unrelated source

This caused the demo screenshots: fragments of strings, comments and names were
given semantic colors. The TextMate grammar already recognized these examples
correctly. TLS encoded LSP relative positions while walking the adapted AST;
switch arms/conditions can be visited out of source order, and synthetic nodes
can revisit the same source. A backwards/duplicate visit corrupted later deltas.
Lengths also used UTF-8 bytes where LSP requires UTF-16 code units.

TLS now collects absolute ranges, sorts them, removes overlapping duplicates,
rejects synthetic identifiers that do not match original text, and then encodes
UTF-16 deltas/lengths. Full-demo tests require semantic tokens to coincide with
actual original identifiers, rather than merely lie somewhere inside the file.
They assert the function/variable/enum classifications from the reported cases.
Other regressions cover unsaved edits, CRLF, emoji and non-ASCII identifiers.

The grammar tests now check representative rendered color metadata as well as
scopes: nested `inner ` text and ordinary quoted strings are orange; interpolation
expressions retain ordinary function/number colors. The actual VS Code test checks
the complete demo's semantic token response and selected classifications.

`f"{{fields}} {mark(1)} {mark(2)} {1234567:0>12,}"` deliberately outputs literal
`{fields}`, then evaluates each call once, left to right. Its assertion tests
escaped braces, formatting and evaluation order. An ordinary quoted assertion
string does not interpolate its braces.

### Folded type queries could suppress native errors

For example, `N :: id(size_of(cast(i32)"bad"))`, with `id` declared as an integer
identity `fn`, previously succeeded. Type-query substitution erased the operand
before Odin could reject its invalid cast. Constant out-of-bounds array accesses
inside the query had the same problem.

Native type witnesses now retain the original queries. Local `::` declarations
whose queries reference runtime storage need a separate unevaluated query check:
Odin cannot reference that storage directly inside a native constant declaration.
This preserves native validation without executing the queried expression or
pulling runtime initializers into a compile-time helper. Regression cases cover
global/local constants, folded runtime calls, valid local storage, invalid casts
and bounds, Unicode source maps, unsaved TLS diagnostics and clearing after a fix.

### An unentered loop could evaluate its post expression

`for i:=0; i<0; i=explode() {}` could execute a known-argument `fn` at compile time
even though the loop never entered. Lazy-path analysis now includes the post,
and inactive placeholder expressions are parenthesized for Odin's loop grammar.
Tests use failing functions in both a known comparison and a literal-false loop.

### Expression helpers could lose generic constant parameters

A `$N:int` parameter used inside a compile-time switch nested in an interpolated
string could be captured as a runtime helper argument. This made a valid constant
selector nonconstant. Native constant parameters now remain lexical constants.
The regression instantiates both arms of a generic `fn` containing nested strings,
a `#switch` and a call to another `fn`.

### Very wide unsigned sequence indices could truncate

With `a:=[7,8]; index:u128=1<<100`, `a[index]` could read the first element because
the backend narrowed the index. Thor's existing wide signed guard did not cover
explicit unsigned indices. Wide unsigned runtime indices now enter the sequence
normalizer, which checks the bound before narrowing. Reads, writes and both slice
bounds reject the oversized value. Existing valid unsigned, map-key, enum-array
and pointer semantics retain their separate regression coverage.

### Parser diagnostics highlighted whole lines

TLS now derives a token range from the shared lexer at the original parser error
offset and converts it to UTF-16. Tests check an exact punctuation range after an
emoji, clearing after a fix and a valid zero-width range at EOF.

### Superseded and closing checks could leave work running

A blocking pipe read could prevent the checker from observing new snapshots or
its deadline while a compiler was silent. The reader now polls pipe readiness.
Superseded checks are terminated and reaped before checking the newest snapshot;
timeouts produce a diagnostic instead of silently clearing errors. Thor handles
termination by unwinding its native subprocess and temporary work directory.

Closing TLS also previously left its active compiler running. Shutdown now
invalidates queued/active generations and joins the worker before destroying
overlays, documents and configuration. Real stdio tests exercise a silent compiler,
24 rapid edits and closing during a check. A real Thor cancellation test runs an
infinite compile-time helper and verifies graceful exit and scratch cleanup.

## Coverage reviewed

| Features | Existing and extended checks |
| --- | --- |
| T01–T06 | CLI modes; ordinary Odin; selective-import privacy/shadowing; lazy ternaries; native `when`; selected `#if`; contextual/nested/empty arrays; membership and generic constraints; native rejections and source maps. |
| T07–T12 | Unicode formatting; Set ownership; shared lexer; mixed packages, build tags and resources; overload/private/generic contexts; shallow immutable storage, aliases, addresses and generic guards; editor resolution and mapped errors. |
| T13–T17 | Nested/raw interpolation; escaping/formatting/order; both runtime and compile-time switch forms; lazy arms; Set operations/aliases/temporary cleanup; negative reads/writes/slices; signed and unsigned widths; loops, callbacks and contextless helpers. |
| T18–T20 | Purity/borrow rejection; recursive/generic/ordinary eligible calls; native value protocol and exact bits; selected/lazy execution; runtime/global dependency isolation; timeout/panic/cycle errors and imported unsaved sources. |
| T21–T23 | Cache corruption/concurrency/invalidation/resources; independent allocated results, defers and allocation failure; forward/dependent constants; per-instance generic scheduling; lazy runtime paths; native type witnesses and validation. |
| Diagnostics | Exact original ranges, Unicode/CRLF, repeated/reordered fragments, generated helpers and call notes, imported/mixed/unsaved sources, warnings, text/JSON/unix output and deduplication. |
| TLS/editor | Actual stdio and VS Code providers; hover/definition/completion; readonly tokens; whole-demo token integrity; unsaved checks and clearing; symlink URIs; new buffers; rapid edits; shutdown; grammar scope and color assertions. |

The new compiler interaction cases are in `tests/test_interactions.py`. An
allocation-tracked combined test uses switch-arm arrays, negative indexing,
nested interpolation and anonymous Set unions in both selected branches.

## Contextual enum follow-up

The previously recorded switch-arm array gap is resolved. The former expected
failure is now `test_contextual_enum_navigation_in_switch_array_arm`, a regular
passing regression. Expected types flow through switch results and nested
aggregates in declarations, assignments, returns and procedure arguments.
Coverage includes hover, definition, completion, rename, incomplete edits and
mapped diagnostics. Switch selectors and inner calls keep their own enum types.

## Remaining boundaries

1. **TLS still has a partial resolver.** Complex overload/generic/expected-type
   contexts can lack editor information even when the native checker accepts the
   code. TLS does not execute procedures for hover values or its own selection
   of `#run`-dependent branches. Rename validates original-source edits and rejects
   unresolved related uses. Formatting and automatic import edits remain disabled.
   These are existing boundaries, separate from token integrity.
2. **Compiler limits agreed in the specification remain.** Examples include
   conservative implicit `using` pointer promotion through unconstrained
   generics, slice-containing compile-time results requiring runtime bindings,
   conservative pure-value schemas and unsupported raw unions. F-strings use
   the current allocator; named final allocated results require user cleanup.

Validation uses the installed macOS Odin toolchain. Cross-target source selection
is tested; native runtime/editor behavior on Windows and Linux is not established
by this run. POSIX-specific executable/cancellation tests are skipped on Windows.

## Verification

See the 0.7.1 checkpoint in `DEVELOPMENT.md` for final test totals and installed
editor artifacts. Temporary audit logs live outside the repository; the regression
tests and this report are the durable record.
