# Thor development process

Work through the numbered T tasks in coherent batches. Each batch includes its
compiler behavior, editor support, diagnostic quality, tests, and evaluation demo.
The specification in `thor.odin` defines the language contract.

1. Commit the working baseline. Read each task's corresponding spec sections and
   list accepted/rejected cases, ownership rules, and cross-feature interactions.
2. Implement support-library and transpiler changes using the shared frontend.
   Preserve Odin compatibility, scope, evaluation order, and source mappings.
   Include real compile/run tests, rejection fixtures, and labeled demo sections.
3. Update TLS and the VS Code grammar/client. Use the shared parser and original
   source positions. Check highlighting, relevant semantic features, and compiler
   diagnostics on unsaved buffers and imported files.
4. Review diagnostics as a language user. Explain Thor-specific failures, point
   at the actual offending expression, and suppress generated implementation
   details and redundant cascades. Preserve inactive-branch checking behavior.
5. Run the relevant checks below. Review representative failures manually, check
   the actual editor, rebuild/install changed editor components, and document the
   implemented subset and any real limitations.
6. Commit the verified batch. Keep implementation, tests, docs, examples, runtime
   support, and TLS changes in the same repository. Do not mark a task complete
   merely because its syntax parses or generated code compiles in one example.

```sh
python3 -m unittest discover -s tests -v
./thor run demo
python3 tools/build_tls.py test
odin test runtime/pure -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
odin test runtime/eval -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
cd editors/vscode
npm test
npm run package
```

Run `python3 tools/test_vscode.py` from the root for the isolated VS Code test.
Use **Thor: Restart Language Server** after rebuilding TLS. Install the local
VSIX when extension code, grammar, or settings change.

The root Git repository contains the Thor code and vendored TLS sources, so a
checkout is buildable without a private remote fork. Upstream OLS provenance and
license are retained in `tls/THOR.md` and `tls/LICENSE`. This workspace's original
TLS Git history, including checkpoint `cb009c7`, is preserved at
`.git/tls-upstream`; inspect it with
`git --git-dir=.git/tls-upstream --work-tree=tls log`. It is local history, not a
dependency required to build a fresh checkout.

T01–T23 now have implementations, editor integration and evaluation sections in
`demo/main.thor`. The earlier 0.4.1 checkpoint covered interpolation (T13), runtime and
compile-time switches (T14), Set syntax (T15), negative sequence accesses (T16),
and expression-local lowering (T17), plus native xor/pointer compatibility,
callback context handling and anonymous expression cleanup. It passes 289 Python
compiler/LSP tests, 1,099 native TLS tests, 51 grammar tests, allocation-tracked
runtime/lexer tests, and the isolated VS Code integration test. The demo passes
all three define modes.

`thorlang/expressions.py` emits named procedures with explicit lexical inputs;
their calls remain in the original expression. Helpers instantiate lazily, so
inactive native when/#switch expressions leave unused bodies unchecked. Writable
array accesses retain storage; generic assignment branches preserve native map
headers. Loop posts remain simple assignments. Native type witnesses resolve
result/element types without executing expressions. TLS shares the parser's
feature metadata and adapts only its editor AST, keeping original source spans.

`thorlang/ownership.py` tracks only anonymous allocations from known Thor
producers. Consumers release them after use; branch results carry an ownership
flag, union chains reuse fresh left tables, and loop cleanup covers every exit.
Unused expressions and blank destinations also release their owned results.
Named results and arbitrary user procedure contracts remain manual. Set elements
retain shallow ownership. Escaping temporary views require a named owner; safe
immediate reads carry the owner until the read finishes. Test new consumers with
Odin's tracking allocator, including mixed branches and early exits. Broad TLS
resolver work remains deferred to the later T tasks.

The 0.5.0 batch added declared `fn` contracts (T18), typed contents-only native
serialization (T19), and explicit `#run` helpers (T20). The 0.6.0 batch completes
T21 caching and T22 allocated results. The 0.7.0 batch completes T23 automatic
folding and specialization scheduling. Helper build/run failures use the same
diagnostic maps as final Odin checking; execution failures point to the call or #run.

`thorlang/purity.py` tracks effects and transitive borrowed provenance. Native
generic contracts check instantiated types and preserve inactive `when` behavior.
`thorlang/evaluation.py` prunes helper dependencies, verifies ordinary procedure
graphs, preserves overlays/configuration, schedules selected explicit calls, and
bounds native execution. `thorlang/values.py` validates the versioned native value
protocol and reconstructs typed constants. `runtime/pure` owns scratch lifetimes;
`runtime/eval` serializes contents without padding or process addresses.

`thorlang/cache.py` caches successful typed contents with atomic replacement and
conservative dependency/toolchain/resource fingerprints. It never caches helper
executables. Eligibility and closure construction run before lookups. Cover warm
backend skipping, corruption/concurrent publication, clear/bypass, defines,
transitive source/types/constants, imported unsaved buffers and resource changes.

Allocation-bearing procedures retain native named return behavior inside an
isolated body. A first-installed defer promotes results into saved caller storage
after user defers and while local arrays live; outer scratch cleanup follows.
The ignored native return acknowledges temporary stack views with generated
casts. Native or_return, result arity/missing-return checks and generic/variadic
signatures remain intact. Deep copies give every sequence occurrence independent
tree ownership. `pure.destroy` frees supported owned aggregates recursively;
ordinary assignment and arbitrary proc ownership remain shallow/manual.

Runtime #run materializers construct fresh buffers directly, without leaking an
allocated template. Constant strings/fixed aggregates retain borrowed native
ownership. Slice-containing constants and allocated contextless results have
explicit diagnostics. Keep zero-sized element slice lengths and nil/non-nil
descriptors intact, including through serialization and cache hits.

The previous 0.5.0 checkpoint passed 335 Python compiler/LSP tests, 1,102 native
TLS tests, 54 grammar tests and the runtime/lexer, actual VS Code and demo checks.
The 0.6.0 checkpoint passes 367 Python compiler/LSP tests, 1,105 native TLS tests,
56 grammar tests, four allocation-tracked pure-runtime tests, the native value
protocol and lexer memory checks, the actual VS Code integration test and all
three demo define modes. A warm demo run reports 12 cache hits and zero misses.
That checkpoint's local TLS executable and Thor 0.6.0 VSIX were rebuilt and installed.
Generated binaries remain ignored; their source and build commands are tracked.

T23 uses the same native helper/cache/value pipeline for declared fn calls with
known inputs and for explicit #run. Forward/dependent values are scheduled on
demand; cycles have distinct diagnostics. Native when/#switch and known lazy
conditions select work. Unknown runtime branches retain ordinary fn calls.
Direct, resolved single-result calls form the compile-time subset; native
void/multiple-result fn calls continue to work at runtime.

`thorlang/specialization.py` binds generic symbols by lexical declaration, not
spelling. It follows concrete calls, including nested forwarding and inferred
array counts. Each instance gets a native conditional substitution; unresolved
active #run paths fail rather than falling back to invented values. Helper-only
bridges let generic libraries evaluate caller-defined types without introducing
reverse imports into the actual program. Native metadata probes recover nominal
identities in their defining scopes without running user code; local source
offsets are translated through the source maps. Native test mode is retained.

Result type witnesses use native procedure_of to preserve overload selection and
private result types. Runtime materializers take the type at the original call
site, avoiding nested generic signature reflection problems. Type queries extract
types without pulling runtime initializers into helpers. Recheck these boundaries
with cross-package/private/local types, generic recursion, constant dependencies,
tracking allocators, Unicode diagnostics and unsaved buffers.

TLS explains automatic folding and ownership in hover, retains normal result
inference/completion, and checks generic-dependent constants through the compiler.
Its partial resolver still does not execute procedures for hover values or its
own branch selection. Thor checker requests allow up to 120 seconds for multiple
native helpers; each evaluation retains the compiler's separate timeout.

The 0.7.0 checkpoint passes 402 Python compiler/LSP tests, 1,109 native TLS tests,
58 grammar tests, allocation-tracked pure/value-protocol/lexer tests, the real
VS Code integration test and all three demo modes. A warm demo reports 44 cache
hits and zero misses. The TLS executable and local Thor 0.7.0 VSIX are rebuilt
and installed; the workspace server has been restarted.

The compiler's resolution boundaries, conservative record schemas, raw-union
rejection, generic `using` pointer case and compile-time resolution subset
are documented in `README.md`; keep those limits visible while extending the
resolver. Do not substitute a zero result for an unresolved active #run. Unknown
native dependencies must not introduce unverified initializers into helpers.

## Post-v1 audit: 0.7.1

[AUDIT.md](AUDIT.md) records the findings, cross-feature coverage and remaining
editor boundaries. This round fixes semantic token ordering/duplication and UTF-16
lengths; precise live parser ranges; native validation lost inside folded type
queries; lazy loop posts; generic constant capture in expression helpers; and
wide unsigned sequence bounds. Superseded and closing checker processes are now
cancelled and reaped, with Thor unwinding its native helper and scratch directory.

The full Python discovery run exercised 418 tests with one documented expected
failure. The subsequently added shutdown regression also passes, bringing the
current inventory to 419 tests (418 passing, one expected failure). Native TLS
passes all 1,109 tests; the grammar suite passes 60 tests, including color metadata
checks. All four pure-runtime, one value-protocol and two lexer tests pass with
allocation tracking. The actual VS Code Extension Development Host integration
passes, including full-demo semantic token integrity and classifications. All
three demo modes pass; the warm default demo reports 44 cache hits and zero misses.
The 0.7.1 TLS executable and local VSIX are rebuilt, the extension is installed,
and workspace settings were toggled and restored to trigger a server restart.

The expected failure tracks missing hover/definition on contextual enum members
inside switch-arm arrays. Compilation and coloring work. Keep this distinct from
token-range corruption: tests must check original token identity as well as file
bounds, and editor navigation needs its own expected-type regression cases.

The 0.7.2 editor follow-up gives namespaces a Thor-specific variable-color fallback
instead of VS Code's type-colored namespace default. Interpolation now assigns
string scopes to contents/quotes, keeping prefixes and surrounding code separate.
The grammar suite checks the reported conditional nested f-string, aggregate
return braces, escaped literal braces, package names and `set`/`map` colors,
including their token/bracket metadata in the full demo context. Import regressions
exercise default `format`/`sets`/`pure` names and explicit aliases through actual
compiled programs and TLS resolution. Install updated grammars with a window
reload; a server restart cannot replace an already loaded TextMate grammar.
All 64 grammar tests, the four targeted compiler/LSP regressions (including
allocation-tracked import examples), and the actual VS Code integration test
pass. The 0.7.2 VSIX is installed locally; its grammar/theme contributions require
**Developer: Reload Window** in an already open editor.

## Quoted interpolation fills: 0.7.3

Explicit format fills now use ordinary Odin rune literals: `'界'^5`, `'0'>12,`
and `'\u754c'^5`. The shared lexer validates a single Unicode scalar and native
escapes; lowering preserves that literal in the formatter call. Bare fills are
rejected with a diagnostic against the format span. Alignment-only and width-only
formats keep their default space fill. This deliberately replaces the previous
bare-fill spelling; the specification, demo and examples use the quoted form.

TLS uses the same scanner and reports unsaved format failures at original UTF-16
ranges. The grammar colors fills as rune literals, widths as numeric literals,
and alignment/grouping punctuation as operators. Regression coverage includes
braces and outer delimiters as fills, escaped quotes/backslashes, Unicode escapes,
invalid/multiple runes, missing alignment, runtime/explicit/automatic evaluation,
source maps and unsaved error clearing.

The expression/diagnostic/TLS regression run exercised 153 tests with the existing
contextual-enum navigation expected failure. All 65 grammar tests, 1,109 native
TLS tests, two allocation-tracked lexer tests, the demo and actual VS Code
integration pass. Install the 0.7.3 VSIX and run **Developer: Reload Window** to
load its grammar changes in an existing editor window.

The 0.7.4 editor update styles `#if`, `#else`, `#switch` and `#run` like existing
directives. The `if` following `#else` shares that scope, across whitespace and
comments; conditions and subsequent runtime keywords retain their own scopes.
All 67 grammar tests pass, including actual color checks, comments/strings,
multiline chains and runtime-keyword boundaries. This changes editor styling only.

## Early redeclarations: compiler/TLS 0.7.5

Previously, an otherwise parseable redeclaration waited until the final native
check, after compile-time evaluations. A copied cold demo took about 55 seconds
to report a duplicated `allowed_size`; the early check rejects it in about 0.2
seconds before evaluation. `frontend/parser/declarations.odin` supplies the same
conservative check to the CLI and TLS. It covers unconditional top-level names
in one untagged file, ignores blank/synthetic names, and leaves branches, tagged
files, locals and cross-file conflicts to native checking.

TLS publishes a separate immediate `Declaration` diagnostic with an exact UTF-16
identifier range and prior declaration line. Compiler checking is not required.
Fixing or closing a buffer clears it, and background checks avoid duplicating it.
Regression cases include the reported generic constraint, an infinite pure
initializer that must never run before the error, Unicode, valid conditional
definitions, scoped shadowing and blank names.

Validation used an isolated snapshot of the working implementation with the
committed demo/spec, preserving the user's experimental edits in this checkout.
The CLI/scope/interaction/diagnostic/LSP run exercised 240 tests with the existing
contextual-enum navigation expected failure. All 1,109 native TLS tests and the
actual VS Code integration test pass, including early redeclaration clearing.
The compiler and TLS advance to 0.7.5; the editor grammar/client remain 0.7.4.

## Interval membership: 0.8.0

`in`/`not_in` now accept half-open and inclusive intervals at runtime and in
constant/generic contexts. The shared parser consumes arithmetic bounds without
changing ordinary container precedence or native iteration. A second explicit
semicolon identifies a three-part loop's membership condition; condition-only
loops retain native iteration unless the boolean membership is parenthesized.

Runtime lowering evaluates value, low and high once in that order, including
out-of-range values, and releases anonymous owned strings/views afterward. Native
type witnesses preserve literal coercions, enum contexts, private/generic types
and ordered comparisons. Constant contexts retain checked comparisons. Inclusive
bounds do not increment the upper endpoint, avoiding integer overflow. Query
diagnostics use `range-type` mappings and suppress redundant inference cascades.

TLS retains boolean inference and original token ranges, folds integer interval
conditions (including unary signs), and reports generic failures against unsaved
source. Its existing partial scalar/branch evaluator remains a documented limit.
The spec and demo include interval rules, constraints, boundaries and order checks.
Power remains a design discussion; the existing prefix `**` expansion is unchanged.

The full discovery run exercised 443 tests with one existing expected failure.
It exposed an invalid new loop fixture and a demo dependency on a commented user
example; both were corrected. All 70 affected range/demo/expression regressions
then passed. All 1,111 native TLS tests, 69 grammar tests, actual VS Code integration
and all three demo modes pass. The 0.8.0 compiler/server and local VSIX are built
and installed; scratch edits outside the feature changes remain uncommitted.

## Numeric power: 0.9.0

Infix `**` now associates to the right above prefix signs and multiplication;
native prefix expansion is unchanged. A contextless numeric support routine uses
integer squaring and native floating-point pow. Checked common-type witnesses
preserve literal coercion and reject mixed typed operands, without overflowing
while inferring types. Expression-local helpers evaluate operands once in order.
Negative integer exponents fail when evaluated at compile time and panic at
runtime even with assertions disabled. Untyped integers remain exact through
128 bits; typed runtime multiplication retains native width behavior.

Power participates in the existing lazy evaluator, specialization, contents cache
and result mapping. Folded untyped results retain literal coercion, huge untyped
exponents of zero/one remain valid, and type queries never execute powers.
Fixed type-level generic exponents through 128 use native constant arithmetic;
other type-level powers without a resolvable call site have an explicit limit.
TLS shares the AST and precedence, infers numeric types and folds representable
integer powers. Diagnostics collapse inference cascades into the original mixed
operation, unsupported operand, or unrepresentable-literal error.

The broad discovery run exercised 458 tests with the existing enum-navigation
expected failure. One warm-cache assertion failed while compiler source identity
was changing and passed on a stable rerun. The final 16 power regressions and
focused diagnostic/LSP checks pass, covering generic signatures/records, constants,
negative and fractional exponents, IEEE values, cache invalidation, evaluation
order, lazy paths, native expansion, and UTF-16 unsaved errors and clearing.
All 1,113 native TLS tests, 72 grammar tests, actual VS Code integration and all
three demo modes pass. Compiler/TLS 0.9.0 is built and its local VSIX installed.
The spec and main demo include the new rules; user scratch edits stay separate.

## Record value highlighting: TLS 0.9.1

The aggregate-alias heuristic cleared the value marker from every record-valued
call and identifier. `PURE_REPORT :: #run pure_report(7)` therefore looked like a
type. TLS now distinguishes actual generic type construction from value-producing
calls, retains value aliases and annotated constants, and normalizes type_of
results as types. Local/global generic aliases keep type colors. Constant hover
prints source `#run` syntax instead of the synthetic call wrapper.

The stdio LSP suite passes 47 tests with the existing contextual-enum expected
failure, covering declarations/uses, readonly flags, fn/#run records, value/type
aliases, typed constants, field hover and full-demo token identity. The actual
VS Code integration asserts PURE_REPORT is a variable and Owned_Report a struct.
All 1,114 native TLS tests pass in the serial runner. An imported-selector
completion test failed once in the parallel runner and passed alone and in that
complete serial run. The workspace server is rebuilt/restarted as TLS 0.9.1;
the compiler and editor client/grammar remain 0.9.0. Scratch edits are preserved.

## Thor type presentation: TLS 0.9.2

Hover, signature help and completion labels now share a temporary presentation
view of type ASTs. The actual thor:sets.Set declaration is identified through the
configured collection and declaration identity, rather than an import alias or
any record named Set. Variables, pointers, containers, generic arguments and
procedure parameters/returns display set[T], including inferred clone results.
Indexed/original ASTs and source ranges remain unchanged. Distinct aliases and
unrelated Set types retain identity; explicit type annotations preserve their
names instead of reconstructing them from underlying generic metadata.

Thor hover snippets now use the Thor language fence, giving set/fn/directives the
workspace grammar. Native Odin buffers keep native spelling and highlighting.
Regression coverage checks renamed/default imports, nested callbacks, cross-package
signatures, generic record arguments, distinct aliases, field hover/definition,
semantic token identity and signature help. The actual VS Code test checks both
reported variable forms, clone inference, insertion signatures and Thor fences.

The 50-test stdio LSP suite passes with the existing contextual-enum expected
failure. All 1,115 native TLS tests pass with allocation tracking in the serial
runner, and actual VS Code integration passes. TLS 0.9.2 is rebuilt and restarted;
the compiler/client/grammar remain 0.9.0. User scratch edits remain separate.

## Commented language walkthrough

`examples/demo/demo.thor` is a separate, spacious language tour modeled on the
organization of Odin's examples/demo/demo.odin. Fourteen topic procedures cover
T01–T23, interval membership and power, with original examples, explanatory
comments, ownership guidance and commented rejection cases. A mixed Odin/Thor
guide package and source-relative resource exercise project handling. The native
test wrapper runs the entire tour under Odin's allocation tracker; the root
acceptance suite now includes it. The earlier demo remains independently covered.

The new examples exposed two compiler issues. Evaluation closure construction
retained a selective import even when a parameter/local shadowed its name, then
validated that alias against a pruned helper package. It now uses lexical lookup
before retaining an alias. Pure mutable-reference loop bindings were replaced by
error tokens which Odin treated as legal declarations, hiding the intended
restriction behind undeclared-name errors in the body. The rejection now targets
the entire loop and suggests supported indexed writes to fresh local storage.
Mutable-reference iteration inside pure code remains a conservative limitation.

The tour passes all three TOUR_MODE values, directory and single-file invocation,
allocation tracking, and uncached evaluation. A stable warm run reuses all 21
evaluations with no misses. Validation also passes 44 pure-function tests, 88
automatic-evaluation/source-map tests, three focused diagnostic/LSP checks, both
demo acceptance tests, and 73 grammar tests. The new LSP checks cover original
semantic-token identity across the full tour, set hover spelling, and unsaved
pure-loop errors/recovery. Grammar checks inspect nested strings, Unicode fills,
switch interpolation and Set colors in the complete new file.

## Thor 0.10: source debugging and mkthor

The native debug-adapter prototype demonstrated usable Thor breakpoints and
stepping with ordinary Odin and the installed CodeLLDB. The implementation keeps
that bridge rather than requiring an Odin fork or rewriting DWARF. The VS Code
extension now registers `thor-lldb`, Thor breakpoint support and Cmd+Shift+R via
**Thor: Debug Project**. TLS remains the editing server and does not implement
debugging. Its native parser/server version is unchanged.

Debug source maps retain helper and capture regions in additive version-1 fields.
Breakpoint inversion excludes generated type witnesses and capture arguments,
and rejects native locations moved onto another original statement. Frame mapping
collapses helpers, merges their user scopes, adapts captured-name conditions and
keeps watches/edits available in the caller. Source stepping skips generated and
library housekeeping while preserving real breakpoint/exception stops. Original
editor path aliases and Unicode scalar/UTF-16 column conversions are retained.

Debug manifests have a separate version and record original snapshots, generated
source hashes and a successfully built executable's hash. Failed/check-only builds
cannot certify an old executable against new maps. VS Code restart uses a fresh
adapter and pre-launch rebuild; an integration test changes source before restart.
The backend connects over an authenticated local stream and shuts down through
CodeLLDB. Advanced mapped step targets, hardware data breakpoints, jump-to-location
and restart-frame capabilities remain disabled. Watches use CodeLLDB expression
syntax; native runtime printed traces and direct LLDB use remain native locations.

With `-debug`, optional runtime `fn` calls remain runtime calls, including literal
arguments. Required constants, type/signature work, constant generic arguments and
explicit #run still evaluate during compilation. Non-debug folding is unchanged.
`mkthor` creates a project under ~/Code/Odin with main.thor, build/launch/TLS
settings and a CodeLLDB recommendation, then opens VS Code. It rejects existing
folders/symlinks and uses main.thor to avoid platform-suffix file selection traps.
The root workspace also has a debug configuration for the commented walkthrough.

Validation passes 166 pure/evaluation/cache/source-map/project regressions, 59
expression-lowering regressions, ten
real CodeLLDB tests, four project/build-identity tests and the focused CLI checks.
All 78 Node grammar/protocol/watcher tests pass. Both demo acceptance tests pass, and the
commented walkthrough also runs with -debug -o:none. Actual VS Code language and
debug integration suites pass, including a newly generated project, the shortcut
command, pre-launch task, breakpoint, Step Over/Into/Out, locals/watch and restart
after a saved edit. Compiler/client 0.10.0 is packaged and installed locally.
The VSIX packaging command preserves local relative documentation links because
this checkout has no published repository URL. See editors/vscode/DEBUGGING.md.
The client watcher also excludes generated debug/build trees, including custom
output roots identified by their manifest, to keep those symbols out of TLS.

## Unused import dimming: TLS 0.9.3 / client 0.10.1

The VS Code client now enables unused-import hints on open/change/save. TLS
publishes `Hint` diagnostics tagged `Unnecessary`, letting VS Code apply its
normal unused-code opacity without changing semantic token colors. Hints update
against unsaved text, clear during incomplete syntax and on close, and do not
require the compiler check to finish.

Import usage follows source namespace aliases and resolved selected identifiers,
including shadowing and multiple aliases of one package. Synthetic selected
declarations do not themselves count as uses; the hidden set[T] adapter does not
keep an unused explicit thor:sets import alive. Explicit blank imports remain
intentional. Native using imports now populate the resolver's using-package
context. Full diagnostic ranges include Thor's multiline selections, stored
separately from the native import AST ranges used for navigation.

Seven stdio regressions cover live edits/clearing, Unicode ranges, selective
imports/shadowing, nested interpolation and switches, duplicate package aliases,
set adapters, blank imports and using imports. All 59 stdio tests pass (with the
pre-existing contextual-enum expected failure), along with 1,115 native TLS tests
and 78 Node tests. Actual VS Code integration verifies the diagnostic tag and
clearing on unsaved use. A read-only check of basic.thor confirms math/big is
marked unused. Existing user demo/spec/basic source edits are preserved.

## Startup without an active editor: client 0.10.2

The user's real extension log exposed a startup exception: workspace selection
passed an undefined URI to VS Code when activation/restart ran before an editor
was active. The server never launched, so hover and unused-import diagnostics
were unavailable despite the updated extension being installed. Guard the editor
URI and fall back to the first workspace folder in both TLS startup and the
debug command. Restore the package declaration's native namespace scope (green
in Dark+), keeping imported namespaces blue.

The actual VS Code integration now activates the extension before opening any
editor, checks package hover and unused hints, and restarts TLS with all editors
closed before reopening source. It passes alongside all 78 Node tests, including
the updated themed declaration/reference colors. Reloading the VS Code window
loads the new client/grammar; restarting only TLS cannot replace client code.

## Generic value classification: TLS 0.9.4

Generic loop values inherited the element type's PolyType flag. Their declaration
could be blue while uses inside the body were green, and hover could incorrectly
name the value `$v`. Clear the inherited flag for value bindings, then restore it
only for an explicitly polymorphic binding. Generic type aliases retain their
type classification. Hover/completion print generic element types as T/^T rather
than introducing a new $T; two upstream display expectations are updated for
that corrected presentation.

Two stdio regressions cover the asc/desc variadic loops and generic slice, fixed
array, dynamic array and map elements, alongside ordinary/immutable locals and
type aliases. All 61 stdio tests pass (one pre-existing expected failure), along
with 1,115 native TLS tests and 78 Node tests. The VS Code integration verifies
all six asc/desc declaration/use tokens and the `v: T` hover. A read-only check
also confirms all eight v tokens in the user's basic/basic.thor are variables.
This update rebuilds tls/bin/tls; the installed client remains 0.10.2 and the
Thor: Restart Language Server command loads the rebuilt executable.

## Locationless compiler error routing: TLS 0.9.5

The user's sorted default comparator in basic/basic.thor triggers Odin's
`Unspecialized polymorphic procedure ''` error with no source filename or range.
TLS previously assigned any such error to buffers[0], so a valid demo/main.thor
could display another package's error on its first line. Keep the input paths
with each compiler process/result. The fallback now selects a source only from
that check's inputs, preserves editor URI spelling, and explicitly notes when
the compiler supplied no source location. A timeout also uses source fallback
instead of creating a diagnostic URI for a directory.

The screenshot also exposed a phantom sets package hover at byte zero: the
internal Set import has an empty source span. Exclude empty imports from cursor
lookup, and use the actual import range for import hovers rather than the
package declaration's range.

Three native tests cover package isolation, symlink/unsaved-buffer matching and
refusal to select an unrelated buffer. Two stdio regressions reproduce the
native locationless error with a second package open, its unsaved correction,
and hidden-import hover/range behavior. All 63 stdio tests pass (one pre-existing
expected failure), all 1,118 native tests and 78 Node tests pass, and actual VS
Code integration verifies that the error cannot land on the open demo or create
a package hover on its first comment. A fresh CLI check of demo succeeds. User
edits to Library.odin, demo/main.thor, thor.odin and basic/ remain untouched.

## Generic default callback source diagnostics: compiler 0.10.1

The resolver now recognizes an anonymous procedure used directly as a parameter
default whose signature contains its own generic parameters or direct structural
references to generics owned by the hosting parameter list. It distinguishes
those bindings from concrete/shadowing/imported types and generic bindings from
an enclosing body that may already be specialized. Computed type/length
expressions are left to Odin: mentioning N inside `[N when false else 2]int`
does not prove a polymorphic signature.

Lower that unsupported default to a source-mapped compile-time assertion at the
same default-expression site. Odin therefore retains native active-branch and
enclosing-body checking behavior, while its error has a reliable original
callback-signature span. The public message names the parameter and generic
dependencies, and suggests a named generic default or an explicit callback.
Multiple invalid callbacks report separately. No backend location is guessed;
other genuinely locationless failures keep the labeled package fallback.

Regression coverage includes Unicode ranges, parenthesized defaults, explicit
generic and typed defaults, generic array lengths, multiple callbacks, inactive
when/#if/config branches, unused and specialized enclosing bodies, valid concrete
callbacks, type/member shadowing, named generic defaults, native imported sources
and unsaved correction. The locationless fallback regression now uses a test
compiler to keep testing unknown failures independently from this recovered
case. Actual TLS and VS Code tests assert the callback signature range and
clearing, rather than accepting a package-header diagnostic.

Validation: the full Python regression run passed 507 tests with one existing
expected failure, including the real CodeLLDB cases. The seven final focused
diagnostic tests (with additional computed-expression and enclosing-scope cases)
also pass, along with all 78 Node tests and the final actual VS Code integration.
A direct check of the user's basic package now points to the comparator
signature on line 45 rather than the package declaration. User source files
remain unchanged; the installed client/server can use the updated compiler on
the next check or Thor: Restart Language Server.

## Original-source Rename Symbol: TLS 0.10.0

Rename was explicitly disabled despite references/highlights being enabled.
Advertise rename with prepare support and route Thor requests through a dedicated
original-source edit builder. Resolve declarations and uses by source identity,
validate identifier spans, deduplicate adapted selective-import aliases, and
refresh the package index from all valid unsaved open buffers before searching.
Workspace scans include Thor and native Odin sources, skip generated directories,
and retain open text in preference to disk. Local bindings avoid workspace scans;
procedure parameters also search named arguments across files. Import namespaces
are renamed only in their source file, inserting an alias for implicit imports
without modifying import paths. SDK declarations outside editable sources and
package declarations are not rename targets.

Reject invalid names, detected collisions, related syntax errors and unresolved
same-name uses instead of returning known partial edits. The existing contextual
enum navigation limitation therefore cancels an affected rename. Fix the shared
named-argument resolver to measure its declaration range in the declaration's
Unicode source, including an unsaved imported buffer, rather than in the caller.
Ordinary Odin requests retain their inherited rename path.

Twenty real stdio rename tests cover local/immutable/generic bindings, shadowing,
types and overload groups, record/enum members, compile-time conditionals,
cross-file selected and qualified imports, mixed closed Thor/Odin sources,
named arguments, unsaved imported declarations, Unicode edits, namespace aliases,
collisions, unresolved cases and generated-file exclusion. Representative edited
projects are compiled with Thor. All 64 existing TLS stdio tests also pass (one
pre-existing expected failure), alongside 1,118 native TLS tests and 78 Node
tests. Actual VS Code integration verifies provider registration, applies a
rename to unsaved Unicode/interpolation source, and checks diagnostics clear.
The installed client remains 0.10.2: Thor: Restart Language Server loads the new
server capability without reinstalling the extension. Existing user source edits
in Library.odin, demo/main.thor, thor.odin and basic/ remain untouched.
