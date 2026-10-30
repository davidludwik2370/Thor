# Thor development process

Work through the numbered T tasks in coherent batches. Each batch includes its
compiler behavior, editor support, diagnostic quality, tests, and evaluation demo.
The [language specification](docs/language-spec.md) defines the numbered language contract.

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
executables. Validate eligibility, dependencies and configuration before reuse. Cover warm
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

## Inline generic callback defaults: compiler 0.11.0 / TLS 0.10.1

Thor declarations can now use an inline callback such as
`less := proc(i,j:E)->bool{return i<j}` when E is supplied by the enclosing
generic signature. Lift it into a named helper beside the owning declaration,
introduce the inferred helper generics at structural parameter-type positions,
and give the original parameter an explicit callback type. Existing concrete
defaults and native Odin declarations retain their previous handling.

Odin exposed two additional constraints while testing: imported generic defaults
can resolve names in the caller's scope, and an overridden default can still
specialize an invalid comparison (for example `<` on records). Remove the
lowered default and supply the helper only at statically resolved calls that
omit it. Qualify cross-package helpers through hidden imports, including when a
package-level alias exposes a procedure to a file without that import. Keep
runtime evaluation order, named/positional overrides, explicit nil and the
procedure-value signature intact. Defaults are not carried by ordinary Odin
procedure-value types; unresolved overload calls require an explicit callback.

Helpers for alternate declarations of the same procedure/parameter share a
scope-specific name across when/#if branches and package files. Odin selects the
active declaration/helper; Thor does not guess conditional values. Unused helper
bodies stay uninstantiated. Imports, selected aliases, outer compile-time bindings,
comments, nested Thor expressions and exact copied source spans remain available.
Errors in helper bodies point to their original callback tokens. Runtime captures,
generic dependencies not inferable from callback parameter types, and independently
generic literals without an explicit callback type retain precise diagnostics.
Procedure inputs and indirect calls remain outside the fn/#run pure contract.

TLS cursor lookup now collects enclosing generic parameters before resolving a
nested callback's own parameters. This restores E navigation/rename and `i: E`
hover inside defaults without admitting outer runtime-variable captures. The
original AST continues to drive hover, semantic tokens, definitions and edits.
The installed client remains 0.10.2; restarting TLS loads the rebuilt server.

The commented runnable example is examples/default_callbacks/demo.thor. Fifteen
runtime acceptance tests cover sorting, typed and independently generic defaults,
generic lengths/types, overrides that invalidate the default, nil, multiple
callbacks, conditionals across files, mixed Thor/Odin callers, selected imports,
aliases, side effects, nested scopes, other Thor expressions and purity boundaries.
Mapped diagnostics cover Unicode callback body errors, unsupported dependencies,
captures, inactive and unused code, native imports and unsaved correction. TLS
tests verify callback hover, generic definitions and rename, parameter colors,
and isolated live body errors. Actual VS Code integration compiles the inline
default after correction and confirms its body error has the original token range.

Validation: the full Python run passed 543 tests with one pre-existing expected
failure, including actual CodeLLDB tests. The final fifteen callback acceptance
tests and seven focused diagnostic tests also pass, alongside all 1,118 native
TLS tests, 78 Node tests and actual VS Code integration. The new demo runs and
checks integer/float results, custom ordering, record overrides and preservation
of the input. Existing user edits in thor.odin, demo/main.thor and basic/ are
preserved.

## Callback highlighting and rename boundaries: TLS 0.10.2 / client 0.10.3

The user's inline comparator exposed two independent highlighting paths.
Expression grammar matched anonymous `proc(...)` as a function call. Add a
procedure-expression scope with keyword coloring and nested parameter/body
handling so an inner closing parenthesis does not terminate the outer generic
parameter list. Recognize typed anonymous fn expressions while preserving
ordinary calls to an identifier named fn. TLS inferred callback parameters from
their procedure-valued defaults and colored uses as functions. Preserve parameter
or mutable-variable colors for passed/stored procedure values, tracking the
actual callee node so direct and parenthesized calls remain function-colored.
Named procedure declarations/references retain their existing presentation.

Rename rejected a cursor exactly at the right boundary of an identifier. This
made a one-letter parameter such as a look unrenameable even though its start
resolved and its references highlighted. Accept an end-boundary token as a
fallback, prefer a token containing the cursor, and resolve the selected token
from its original start. Apply the same boundary behavior to import aliases.
Original-source validation, collision checks and refusal of partial edits remain.

Validation: all 88 rename/TLS stdio tests pass with one existing expected failure,
along with all 1,118 native TLS tests and 85 Node tests. New cases cover the exact
generic slice parameter, inferred allocator, an unrelated unresolved identifier,
closed-file named arguments, Unicode and interpolation boundaries, callback value
versus call tokens, and multiline grammar/theme scopes. Actual VS Code integration
verifies the passed callback token and applies a rename with the caret immediately
after a, then checks that diagnostics remain clear. A read-only probe of the user's
basic/basic.thor confirms three rename edits and a parameter token for the
forwarded less. Source edits in basic/, demo/main.thor and thor.odin are preserved.
Rebuild TLS, package and install client 0.10.3, then reload the VS Code window to
refresh both grammar and server. Restarting TLS alone cannot refresh the grammar.

## Bundled basic collection package

Move the user's basic/basic.thor into runtime/basic/basic.thor, retaining
package basic. The driver and TLS already map the default thor collection to
runtime, so ordinary import "thor:basic" now resolves from external projects
without compiler or server changes. Ordinary imports retain the basic namespace;
existing explicit selected imports also work. The package uses the inline
generic default callback lowering for sorted and must be built through Thor.
Private helpers and internal package imports remain private to the library.

Exercise the public procedures through actual external consumers before making
the library part of the collection. Fix desc's inverted comparison and ordered's
variadic forwarding, selecting the first element and forwarding the remaining
slice. Keep its original generic variadic API: callers spread a typed slice,
as required by the current Odin backend. Preserve empty/singleton ordering.
Dispatch base=0 integer parsing to the prefixed native overloads, and use wide
range checks for narrower integer conversions. Honor read's supplied path and
free partial data on error; truncate existing output files when writing. Input
retains a partial EOF line, handles empty lines and CRLF, uses the requested
allocator, and avoids losing subsequent lines to a discarded read-ahead buffer.
Release clone's temporary allocation while retaining its shallow-copy semantics.
Write print's values and terminator to the stdout stream directly, honoring flush
without dropping output in a discarded temporary fmt buffer.

Add runtime/basic/README.md with the API, import examples and ownership rules,
and examples/basic/demo.thor with a runnable walkthrough. Tests cover default
and aliased/selected imports, mixed native Odin consumers built through Thor,
sorting different element types and custom comparators, original input storage,
order checks, numeric bases/bounds, file replacement and missing files, multiple
stdin lines and EOF, private access rejection, and allocation tracking. TLS
tests verify hover, completion, namespace tokens, original-source definitions,
and live mapped call errors that clear after correction. A JSON compiler test
also checks the exact original argument range for an invalid comparator.

Validation: all 159 tests in tests.test_basic, tests.test_defaults,
tests.test_tls and tests.test_diagnostics pass with one existing expected
failure. The ten basic acceptance tests include real Odin execution, an
allocation-tracked test run, stdin through a pipe, and the commented example.
Checking runtime/basic with -no-entry-point also passes. No extension reinstall
is required. Existing edits in demo/main.thor and thor.odin remain uncommitted.

## Spread min/max: compiler 0.12.0 / TLS 0.11.0

Export the native call ellipsis token span from the shared frontend, preserving
the original AST and all argument offsets. thorlang/extrema.py resolves genuine
builtins through lexical scope, immutable aliases and installed core:math
imports. Native Odin inputs, shadowing procedures and ordinary scalar/type-limit
calls retain their original handling. Fixed-array ** expansion returns one
element value and rejects empty arrays. Calls with a final slice spread return
(value, ok) with #optional_ok: empty input yields a typed zero and false; scalar
candidates or nonempty array segments make the result present even when the
final slice is empty. The full scalar/array/slice candidate stream folds in
source order rather than reducing segments independently.

Lower single-container calls directly to contextless sequence helpers. Mixed
calls use existing expression helpers, extended with optional boolean returns.
Bind each expression once in original order before reducing; fixed arrays are
snapshots and slices retain borrowed storage. Scalar annotations use the
sequence element type, preserving untyped literal conversion and rejecting typed
mismatches. No concatenated array or heap allocation is needed. Scope helpers,
captures, debug metadata and original argument maps use the existing machinery.
Runtime helpers preserve native numeric min/max, including floating-point NaN
and signed-zero behavior. This Odin backend misses the string comparison runtime
dependency in imported generic min/max; equivalent explicit string comparisons
register that dependency and retain the native equal-value selection rule.

Integrate required constant reductions with the existing native evaluator and
dependency graph. Pure function bodies, direct #run reductions, generic constant
instantiations, compile-time branches and unevaluated type queries are covered.
The evaluator retains its single-result contract; code needing both value and
status at compile time returns a record from a function. Keep the existing
native-library purity whitelist: only resolved builtin reductions gain this
approval, rather than every selected native symbol with a builtin-like name.

TLS infers the element type plus optional bool from the original call, including
qualified calls and builtin aliases. Alias lookup can disable locals in the
resolution context; resolve container arguments using a temporary context that
restores local lookup. Native buffers retain their prior handling. Compiler
errors identify the actual invalid container or scalar argument, and ordered
element constraint failures use a public Thor diagnostic. No grammar change or
extension reinstall is required; restart TLS to load the rebuilt server.

The commented example is examples/extrema/demo.thor. Fifteen acceptance tests
cover result/status rules, fallbacks, mixed expansions, evaluation order, array
snapshots, aliases/shadowing, exact types and literal conversion, strings/enums,
generic and contextless calls, zero allocation counts, pure/#run results,
required and generic constants, compile-time branches, floating-point edge
values, invalid arguments and the retained native purity boundary. Additional
stdio, JSON diagnostics, actual VS Code and CodeLLDB tests verify hover,
semantic tokens, argument error ranges, unsaved correction, stepping and locals.
Existing playground moves, table work, demo edits and specification edits are
outside this feature's commit.

Validation: the full Python regression run passed 579 tests with one existing
expected failure, including the real debugger tests. The final fifteen extrema
tests and focused updated diagnostic test also pass. All 1,118 native TLS tests,
85 Node tests and actual VS Code integration pass. Rebuild the local TLS server
with version 0.11.0; the client remains 0.10.3. The new demo runs successfully.

## Wildcard and namespace-free imports: compiler 0.13.0 / TLS 0.12.0

Parse `import * "package"` as a Thor wildcard import, retaining the default
package namespace. Enumerate public declarations from the original package
sources before constructing the resolver. Reuse selected-name resolution to
rewrite unqualified references into qualified package accesses; this preserves
live mutable globals, types, overload groups, generic functions, default
callbacks and pure compile-time calls without copying values or re-exporting
imports. Native core/base/vendor export discovery uses the Odin root reported
by the frontend built with the selected compiler. Build-tag exclusions and
file/declaration privacy are read from native parser metadata.

Do not eagerly instantiate wildcard exports: a name in an inactive conditional
branch need not exist unless used. Name reservation is conservative across
conditional declarations; overlapping potential public names from different
wildcard imports conflict. Report collisions at the second wildcard marker or
the importing file's own declaration, and defer conditional local collisions
to the native active-branch checker. Procedure locals may shadow imported names.

Allow `import _ "package" / first, second` by emitting a hygienic internal
package alias. Only the selected names are exposed; the default package name
is absent. Each blank selection has an independent alias, even when selecting
from the same package more than once. Existing explicit-selection validation
still checks unused missing/private names and retains its mutable-variable
restriction. Wildcard access to mutable public variables has no such restriction.

TLS reuses the AST's namespace-using lookup for wildcard imports and its existing
file-private synthetic aliases for explicit selections. Blank selections use
hidden namespaces, omitted from namespace completion and hover presentation.
Original-source hover, definitions, semantic tokens, completion, unused-import
dimming and public-symbol renaming are exercised over stdio. Wildcard package
namespace renaming is rejected because this syntax retains the default name;
public declarations reached through either import form can still be renamed.
Compiler JSON diagnostics and live unsaved corrections preserve original ranges.

Calling the user's newly copied table helper exposed a native iterator lowering
bug: opaque native iterator calls were wrapped as ordinary sequence values.
Leave those calls to Odin's native range checker so repeated `(values..., ok)`
iteration retains its protocol. Also add the missing UTF-8 import required by
that helper. The user's table body, playground moves and existing demo changes
remain outside this feature's commit.

The runnable walkthrough is examples/imports/demo.thor, with a second file
demonstrating independent blank selections. The VS Code client and grammar need
no update; restart TLS to load the rebuilt server.

Validation: the full Python run exercised 597 tests, with one existing expected
failure and one unexpected native helper-build failure in the existing warm-cache
test. Odin exited without a diagnostic during that test's first compilation.
All eight cache tests, including that case, passed on rerun; the cause was not
reproduced. A final 15-test import/rename/completion run also passes, including
additional excluded-file, conditional-collision and hidden-namespace checks.
All 1,118 native TLS tests, 86 Node tests and actual VS Code integration pass.
The new import walkthrough, main T01-T23 demo and user's playground program run
successfully. A direct call to the user's table helper through a wildcard import
also prints the expected aligned table.

## Build artifact in the invoking directory: compiler 0.13.1

Default `thor build` output to `program` (`program.exe` on Windows) in the
command's working directory, independently of the input path or generated Odin
output directory. Retain `-out:` overrides and leave `run`/`test` temporary
executables in their generated trees. Debug manifests record the new default
artifact path normally. Generated Odin, source maps and manifests still live
under `.thor-build/<source>` or the explicitly selected `--output-dir`.

Isolate acceptance-test build working directories so their artifacts remain in
temporary folders. Cover `build .`, single-file builds from another directory,
explicit output names, debug manifest identity, and the retained run/test output
behavior. Update the basic input and test-mode build consumers to use the new
artifact location. The user's playground builds in place and prints `hello`.

Validation: all 100 focused CLI, basic-library, project-template and test-mode
build regressions pass. An additional run check confirms that a pre-existing
`program` in the working directory survives temporary executable cleanup.

## Default artifact name follows the working folder: compiler 0.13.2

Name the default `build` artifact after the working directory, preserving the
entire folder name (including spaces and dots) and appending `.exe` on Windows.
For example, `thor build .` in thor-playground produces ./thor-playground.
Explicit `-out:` overrides, generated output locations, debug manifest identity
and temporary `run`/`test` executable handling retain their existing behavior.

The checkout's folder and launcher are both named thor. Reject a default output
that would overwrite that launcher, and show an explicit output name in the
checkout's build example. An isolated launcher fixture exercises this guard
without putting the real compiler at risk. All 14 focused build, run/test,
basic-input and project-template regressions pass; the playground executable
also builds and prints `hello` under its new name.

## Inspect standard-library bodies for #run: compiler 0.14.0

Replace the installed-package allowlist with on-demand source inspection from
the selected Odin compiler's reported SDK root. NativeSources keeps parsed
base/core/vendor metadata separate from the ordinary project emission. Resolve
overloads, generics, immutable aliases and transitive constant/type/procedure
dependencies through the same resolver used for project code. Custom collection
overrides remain ordinary inspectable project packages.

Build isolated helper copies containing only required declarations. Redirect
native dependency imports into those copies, rename copied package identities
to coexist with the runtime support's installed-library imports, and expose
selected private implementations through public procedure-group bridges. Check
API privacy before adding bridges. Unused initializers, impure overloads and
runtime globals cannot enter evaluation merely because their package is used.
The user's final build still imports the installed Odin libraries normally.

Eligible ordinary procedures may have non-Odin calling conventions when their
bodies are available. Bodyless foreign calls and indirect calls remain excluded.
Retain the existing input/result/borrow/ownership contracts, including no owned
string/slice result from procedures without an Odin context. Permit scalar
numeric/boolean/enum/bit-set transmute, with native generic structural guards;
continue rejecting pointer and aggregate representation casts. Deterministic
compiler primitives and known SDK LLVM math declarations are explicit leaves,
separate from inspected source bodies. Source/type identity queries and metadata
APIs exposing pointer/procedure values are not approved leaves. Declared fn
procedures retain their stricter declared-fn/approved-helper call rule.

Single-value #run calls accept a procedure's optional second return value under
the native ignored-status convention. Root calls to approved allocation/format
helpers retain input validation, even without an enclosing checked procedure.
Actual allocator overrides remain excluded; scalar parameters merely named
allocator work normally. Preserve ODIN_TEST and ODIN_BUILD_MODE in copied code,
respect lexical shadowing, and retain originally selected test-tagged files in
the executable helper. Source-dependent project names remain excluded alongside
file/line/type identity and #defined-dependent constants. Preserve import/foreign
attribute spans so pruning removes attributes with their declarations.

SDK compiler errors attach to the original #run with the native source location
in a note. Overload failures highlight the original callee, use public package
names and omit the resulting invalid-type cascade. Normalize copied SDK package
names in compiler-managed generic identity records, including cache hits. Cache
identity includes installed vendor code, loaded native overlays and referenced
resource contents. Cached results do not bypass eligibility checks.

The commented walkthrough is examples/native_run/demo.thor with ordinary Odin
bodies in eligible.odin. The original thor-playground/pg.thor now runs unchanged:
int(#run round(SIZE ** .33333)) yields a usable constant array length. JSON,
stdio TLS and actual VS Code tests check invalid native calls, exact original
ranges, public diagnostic names and clearing after unsaved correction. No TLS
syntax/client update or extension reinstall is required.

Validation: the full Python regression run passed 632 tests with one existing
expected failure. After the final build-context refinements, all 29 native #run
tests and four focused compiler/TLS diagnostic cases pass. All 1,118 native TLS
tests, 86 Node tests and actual VS Code integration pass. The new walkthrough
and the user's playground execute successfully. A final 74-test purity,
allocation, cache and test-mode regression run also passes. User playground moves, table
work, demo edits and specification deletion remain outside this feature commit.

## Reuse complete transpilation artifacts: compiler 0.15.0 (historical)

This generated-output cache was removed in 0.16.0; the following section records
the earlier implementation and measurements. See the current cache design below.

Profiled the user's playground at 5.4-6.0 seconds per unchanged build. Both
compile-time operations hit the evaluation cache, but its lookup occurred after
reconstructing helper projects and emitting their sources/maps. The build still
launched 212 parser processes for 154 distinct source files. Parsing and Python
AST construction dominated; final Odin compilation/linking took about 0.3 seconds.

Add a complete-transpilation cache before frontend selection, project discovery
and evaluation. Validate source contents, package membership, static resources,
collection/path bindings, native flags/test mode, toolchain identities and the
generated artifacts. Reuse the existing target, mapped native flags, source maps
and diagnostic bookkeeping on a hit. Odin still runs for every check/build.
Load source-map/diagnostic objects only when needed to translate a diagnostic.
Retain the original prefixes, immutable guards, public namespace names, source
snapshots and operation spans. Debug builds refresh the executable identity
without invalidating unchanged generated source artifacts.

Track project and inspected SDK sources/resources by content. Check broad
compiler/runtime/SDK inventories through file identities, sizes and nanosecond
modification/change times, including directory changes for additions/removals.
Record input state before evaluation and revalidate before publication; a source
snapshot differing from current disk contents cannot be published. Keep cache
files atomic, checksummed and optional. Changed/damaged generated files or maps
fall back to normal compilation. Preserve generated-output ownership checks.
Editor overlays bypass complete reuse and replace generated artifacts normally;
the next disk build rejects artifacts from different unsaved contents. Dynamic
or unresolved resource paths bypass complete reuse.

Separately cache successful frontend JSON by source bytes, path/configuration and
frontend binary identity. Deserialize fresh ASTs for each consumer so helper
pruning, feature annotations and overlays cannot mutate a shared cached AST.
Do not cache parser failures. Replace fallback full-tree symbol scans with a
lazy lexical index. Avoid resolving ordinary calls as spread min/max operations
and avoid traversing installed Odin bodies for Thor generic specialization; their
explicit #run dependency inspection remains unchanged. Helper emission may use
metadata from already inspected SDK packages, including rejected runtime globals,
without discovering new SDK packages through ordinary runtime-support calls.

Complete artifacts live under the selected cache root's transpile-v1 directory;
frontend JSON lives under the checkout's .thor-cache/parse-v1. --cache-report
distinguishes evaluation and transpilation counters. --no-transpile-cache keeps
the parser/evaluation layers while bypassing complete reuse. --no-cache bypasses
all three; --clear-cache clears their owned records. Existing evaluation-cache
tests explicitly disable complete reuse to keep exercising that layer.

Acceptance tests cover skipping the frontend/evaluator/emitter on a warm build,
same-size edits with preserved modification times, imported and entry-file
additions/deletions, defines/test mode, resource bytes/existence/directory contents,
retargeted collection symlinks, compiler changes, corrupted output/maps/manifests
and cache packets, unavailable cache storage, overlay recovery, cached Unicode
diagnostics/support contracts, debug manifest identity and parser configuration.

Validation: the full Python regression run passed 659 tests with one existing
expected failure, including real CodeLLDB/debugger tests. A final 53-test native
#run/parser/transpilation-cache run passes after publication and SDK-discovery
refinements; the two affected runtime-storage diagnostic cases also pass.
All 86 Node tests and actual VS Code integration pass. The original playground
source is unchanged and its executable prints the table and array length 10.

Five final alternating warm Thor/native Odin builds of the playground measured
Thor at 0.243-0.253 seconds (median 0.249) and native Odin at 0.166-0.177 seconds
(median 0.177), approximately 72 ms additional overhead. Every warm Thor sample
reports one complete-transpilation hit and no evaluation-cache lookups. These
are unchanged-build measurements; changed inputs still require transpilation,
using the parser and evaluation caches where possible. Existing playground moves,
table work, demo edits and specification deletion remain outside this commit.

## Reuse scalar evaluation across runtime edits: compiler 0.15.1

The 0.15.0 fast path covered unchanged inputs. Editing an unrelated runtime
variable invalidated that path, and evaluation-cache hits still came after
helper reconstruction/emission. The user's playground therefore still spent
roughly three seconds rebuilding after small edits. Treat the edit-and-build
case as a separate acceptance criterion from repeating an identical build.

Add dependency-keyed prepared-result records before helper reconstruction.
For verified straight-line primitive scalar #run sites, check a saved dependency
record before loading SDK ASTs. Retain file-level source syntax with only
top-level procedure bodies removed, then separately validate selected bodies,
local bindings, SDK source/package membership and static resources. Preserve
attributes, privacy, foreign defaults and conditional declarations. Body-local
offset changes do not change the calculation's identity. Cached roots cover
their nested calculations, which need not be scheduled again.

Keep native type witnesses around the original call on early hits. Do not turn
cached scalar values into unchecked primitive casts: the native checker must
still validate privacy, overload resolution, parameter conversions and nominal
return types. Keep generic/aggregate execution, nested #run arguments, unsaved
overlays and unsupported lexical contexts on conservative paths. Resource,
body, type, purity and binding edits invalidate early reuse. Corrupted result
packets fall back without double-counting the same failed lookup.

Cache the toolchain content fingerprint against the already captured metadata
inventory. Source/generated files tracked by content tolerate identical-byte
rewrites. Skip automatic-fn and generic scheduling where no relevant fn/generic
compile-time work exists. Avoid discovering unrelated SDK packages just to
classify ordinary native variadic calls as builtin min/max candidates.

New tests edit runtime variables before and after #run while forbidding helper
project construction. Additional cases cover changed bodies/constants/types,
runtime shadowing, nominal and shadowed primitive types, global/local conditional
declarations, new library privacy, no-op saves and modified native overlays.
The original playground source remains unchanged by the performance work.

Validation: the full regression run passed 669 tests with one existing expected
failure, including real debugger coverage. After final native-type/privacy
hardening, 148 focused edited-build/native-run/extrema/power/cache/diagnostic
tests pass. Nine additional cache tests pass, including clearing early records
when the result directory is missing. Actual VS Code integration also passes.

Five final alternating edited/unchanged/native builds of a temporary copy of
the playground measured runtime edits at 0.662-0.676 seconds (median 0.666),
unchanged builds at 0.276-0.284 seconds (median 0.280), and native Odin at
0.198-0.216 seconds (median 0.207). All edited samples report one early result
hit and zero evaluation misses; all unchanged samples report complete reuse.
The initially empty evaluation/transpilation cache took 2.855 seconds to warm.
These figures include the final Odin build. Changed builds still pay for Thor
parsing, analysis and source-map emission; that remaining overhead is material
and should not be described as negligible. The copied program prints the table
and array length 10, and its original source hash is unchanged.

## Keep result and per-file parser caches: compiler 0.16.0

Remove the complete-project/generated-output cache. The normal workflow edits
source before building, so the unchanged-project shortcut adds artifact,
manifest and diagnostic snapshot validation for little benefit in that workflow.
Delete TranspilationCache, CachedProject and LazyDiagnostics, and remove their
hooks from project discovery and inspected native-source loading.

Every invocation discovers the current files, reconstructs syntax objects,
performs Thor analysis/evaluation, emits Odin and source maps, and invokes Odin.
Retain successful per-file parser JSON keyed by contents/parser/options. Retain
typed compile-time result packets and their dependency records, including early
scalar #run reuse across unrelated runtime edits. No semantic analysis or
generated output is reused across invocations.

Move shared file/package/resource checks to thorlang/dependencies.py. Validate
the compile-time toolchain identity lazily inside EvaluationCache.identity;
there is no SDK inventory scan for builds that need no compile-time execution.
This metadata supports result invalidation and does not retain project analysis.

Remove --no-transpile-cache and thor_transpile_cache reporting. --no-cache
bypasses parser/results; --clear-cache clears parser/results and their owned
dependency records. --cache-report exposes evaluation counters and early hits.
Old transpile-v1 records are ignored and no new ones are written.

Replace whole-project cache tests with fresh-compilation tests. Cover unchanged
builds rerunning analysis/emission, single-file edits reusing parsed imports,
bypass reparsing all files, stale legacy records being ignored, generated-file
repair, overlays, resources, flags, fresh Unicode diagnostics, collection symlinks
and refreshed debug maps/executable identities. Keep compile-time invalidation
and helper-skipping tests; they no longer need a separate transpilation bypass.

Validation: 37 focused compiler/cache/parser checks and two additional
single-file/legacy-record checks pass. Actual VS Code integration passes. The
673-test full run included one existing expected TLS navigation failure and
found two failures: an obsolete assertion that rejected toolchain metadata after
a failed calculation (updated to reject only result/reuse records, then passed),
and an Odin subprocess crash while compiling the math.ln helper (exit -11).
That native-math test passed its isolated rerun and five consecutive final
reruns. The corrected cache test also passed in the final six-test recheck.
All other cases in the full run passed.

Final measurements use a temporary copy of the original playground, with five
alternating runtime edits, unchanged builds and native Odin builds. Edited
builds took 0.640-0.671 seconds (median 0.644); unchanged builds took
0.637-0.683 seconds (median 0.646); native Odin took 0.211-0.227 seconds (median
0.215). Both Thor paths report one early result hit and zero evaluation misses.
The initially empty evaluation cache took 3.509 seconds to warm. The copied
program prints its table and array length 10, and the original source hash
remains unchanged. Removing complete reuse intentionally gives unchanged builds
the same analysis/emission path as edited builds.

## Transitive ordinary calls from fn: compiler 0.17.0

Allow fn bodies to call inspectable Odin/Thor procedures when the relevant
transitive graph satisfies the same effect, type and ownership rules. This works
for runtime fn calls, automatic known-argument evaluation and explicit #run.
Ordinary direct calls retain their original behavior and do not automatically
evaluate merely because some fn also calls them.

The existing native evaluation closure already checks ordinary bodies. Remove
the declared-fn-only restriction and retain its resolved native call targets in
helper metadata, including overload implementations whose original group was
pruned from the helper. Runtime calls need separate checked versions: merely
allowing the ordinary call would let borrowed return views bypass provenance
tracking and scratch cleanup. thorlang/purecalls.py emits checked procedures
beside their original declarations and rewrites only checked call paths.

Keep native generic/where/default/overload semantics, conditional declarations,
recursion, variadic forwarding, named arguments, void/multiple runtime results,
calling conventions and #procedure names. A defaulted hidden argument with an
inferred type delays native body checks until use, including after variadics;
its generic when guard preserves inactive branches. Original procedure types,
ordinary calls and ownership remain intact. Preserve require_results/deprecation
attributes and native privacy. The frontend exposes private_file file tags, and
the resolver prefers the current file's private declarations when names repeat.
Reject inaccessible originals before a generated public name can bypass privacy.

Checked SDK procedures are added to generated copies of their original packages.
The backend selects a local SDK root for both ordinary and checked calls, keeping
public/private nominal type identities unified. Untouched SDK collections and
toolchain directories are referenced through managed links; originals are never
written. thorlang/sdkmirror.py preserves resources and supplies identity maps for
untouched copied Odin files. Record ODIN_ROOT in manifest odin_environment for
manual backend builds. This is generated input, not a whole-project cache.

Decouple compiler scratch/deep-copy support from core:mem and core:reflect to
avoid cycles when those libraries or their dependencies call compiler support.
Use the base runtime allocator/type-info APIs, a per-call allocation map backed
by the saved caller allocator, and direct union-tag inspection. Retain independent
result promotion, nil/non-nil empty descriptors and allocator failure behavior.
Keep compiler support's native test files out of imported program dependencies;
run those tests through their own native package test commands.

Source maps retain original procedure body locations and record checked frame
names. VS Code extension 0.10.4 presents checked procedures as their original
frames, with their own locals and breakpoints. TLS needs no new syntax; the live
compiler check reports an unsaved transitive impurity at the original leaf and
clears it after correction. Map checked callee names back to their original
source spelling and hide synthetic arguments in signatures and quoted calls,
including ordinary return-type errors involving an SDK call.

Add examples/pure_calls/demo.thor and acceptance tests covering runtime/automatic/
explicit execution, nested allocations/borrowed views, ordinary alias behavior,
recursion, overloads/generics/defaults, inactive branches, private types/access,
file-private duplicate names, SDK math/nominal types/test mode, calling conventions,
procedure names, result requirements, variadics, multiple results, Unicode source
locations, transitive cache invalidation and actual LSP/debugger behavior.

Validation: the full run passed 696 tests with one existing expected TLS enum
navigation failure. A final two-test privacy run covers the late file-private
resolver refinement. All six allocation-tracked native pure-runtime tests and
all 86 Node tests pass, as does actual VS Code integration. The new demo runs
successfully. A prior focused 119-test run encountered an intermittent Odin
compiler array assertion; the exact rerun and subsequent full run both pass.
The final 94-test review also exercises return-type diagnostic cleanup. Its
compiler, source-map and TLS cases pass; the debugger case passes separately
with local loopback access, which the sandbox denies.

## SDK overlay performance: compiler 0.17.1

Profile the current playground including its fn wrapper around math.floor and
both automatic and explicit compile-time calls. Compiler 0.17.0 repeatedly emits
2,710 managed entries (64,488,211 bytes) after evaluation results are already
cached. It renders whole inspected SDK packages and reads/parses unrelated SDK
files just to preserve their resources and identity maps. Warm builds reproduce
4.317-4.467 seconds (median 4.360).

Replace that snapshot with a sparse SDK overlay. Generate only source files
containing checked versions, preserve ordinary native text verbatim, and insert
the checked declarations at their original lexical scopes. Keep one ODIN_ROOT
for ordinary/checked nominal identities. Link untouched source files, packages,
assets and toolchain directories directly to their installed inputs. Relative
resources retain their layout without parsing unrelated files. Emit source maps
for generated files and SDK editor replacements; translate diagnostic references
through linked paths back to installed SDK paths.

The overlay layout name fingerprints its generated/replaced file paths. A new
layout therefore cannot write through an older directory link. Unlink output
symlinks before replacing files, and clean only manifest-owned entries. Cover
layout changes and overlay removal while checking installed source bytes remain
unchanged. Unsaved native files and relative resources supplied by an editor
remain part of the selected overlay, including newly added SDK files.

Avoid argument/type-query analysis for ordinary automatic-evaluation candidates
before verifying that a fn target exists. Preserve native conditional fn selection,
aliases, overloads, extrema and genuine type-query behavior. Keep fresh project
analysis/emission and the existing compile-time result and parser caches; no
whole-project cache or persistent generated-SDK cache is introduced.

Measurements use a temporary copy of the current playground, with five alternating
runtime edits, unchanged builds and direct native Odin builds. Runtime edits take
1.005-1.040 seconds (median 1.023); unchanged builds take 1.007-1.038 seconds
(median 1.020); native Odin takes 0.216-0.227 seconds (median 0.221). Each warm
Thor build reports two early result hits and zero evaluation misses. An initially
empty result cache takes 4.684 seconds to compile/evaluate the three helpers.
Generated output falls to 75 entries, including 59 links, with 1,858,085 written
bytes. The program prints its table, array length 3 and compiled floor value 23;
the original playground source hash remains unchanged. Direct warm builds in the
original playground additionally measure 0.942-0.946 seconds.

Validation: 66 pure-function/ownership/protocol cases, 38 compilation/result-cache
cases, 132 generic/native-evaluation/diagnostic/parser-cache cases, two live TLS
checks and two real CodeLLDB breakpoint/stepping checks pass. New regressions
cover sparse native emission, stable type identity, layout cleanup, unchanged SDK
inputs, unsaved SDK files/resources, linked overload-note paths, and result reuse
after runtime edits with transitive dependency invalidation.

## Shared installation and output simplification: compiler 0.18.0

Move the complete checkout, including Git history and existing user work, to
/Users/davidludwik/Thor. Keep the former /Users/davidludwik/Code/Odin/thor path as
a compatibility symlink, and point the shell PATH at ~/Thor. Installation-owned
compiler/frontend/TLS binaries, runtime sources and common library translations
live there. Application parsing data, compile-time results, adapted code and
generated output belong to the application project. Document the layout and
setup in INSTALLATION.md. Protect the launcher when the default executable name
matches it on a case-insensitive filesystem; retain the normal working-directory
executable name and explicit -out: behavior.

Publish versioned common runtime views atomically under lib/. Native packages
link to their original sources without importing their native test files. The
current single-file basic package has an isolated generic Thor-to-Odin
translation shared by callers. Keep native when/generic specialization in Odin.
Library overlays, configuration-dependent Thor syntax, compile-time execution
and checked procedure variants retain project-local output. If any file needs
adaptation, emit the whole package together. Do not share partially specialized
or caller-dependent code. Custom collections retain their existing semantics.

Keep compact mappings in memory for compiler diagnostics on every build. Persist
project source-map sidecars only for -debug and explicit transpile commands;
shared library maps live once beside their common translation. Merge adjacent
exact spans, omit identity-only expansion records and skip debug-only frame
metadata during ordinary compilation. The debug manifest references common maps
without copying them into projects. VS Code extension 0.11.0 reads these maps and
discovers the shared toolchain through THOR_ROOT, a toolchain workspace or ~/Thor.
Explicit settings still override discovery. Rebuild and install the local TLS
and extension after relocation; existing editor windows require a reload.

Plan evaluator output in memory, check the typed result cache against that code,
and only materialize a temporary project on a cache miss. Native compiler
support passes through without re-rendering when unchanged. Parser packets now
use the application's cache directory, including unsaved new-file overlays;
clearing one project's cache does not clear another project's data. There is no
whole-project cache. Unnecessary older frontend/parser and manifest-owned output
artifacts were removed during migration, reclaiming about 197 MB while preserving
project evaluation results and all source files.

Defer independent-call batching, nested evaluation fusion and a faster cache
index. They require additional semantic/dependency work; the shared library,
map persistence and evaluator write changes already remove repeated work without
changing which computations execute or which source inputs invalidate results.

Final measurements use a temporary copy of the current playground, with five
runtime edits, unchanged builds and direct native builds. Runtime edits take
0.671-0.715 seconds (median 0.687); unchanged builds take 0.669-0.693 seconds
(median 0.677); native Odin takes 0.173-0.183 seconds (median 0.177). Every warm
Thor build reports two early evaluation-result hits and zero misses. An empty
project result cache takes 4.339 seconds. Normal output contains two generated
Odin files (98,857 bytes) and 59 SDK links, plus its ownership manifest; there
are no project map sidecars or common runtime copies. The program's table,
array length 3 and floor result 23 are unchanged, and the original playground
source hash remains unchanged.

Validation: the final complete Python run passes 712 tests in 721.754 seconds,
with the existing expected TLS contextual enum-navigation failure. All 1,118
native TLS tests, 13 allocation-tracked native runtime tests and 89 Node tests
pass. Actual VS Code integration passes hover, rename, highlighting, unsaved
mapped diagnostics, imports, compile-time execution and canonical type displays.
Its first attempt encountered an intermittent native Odin helper compiler crash
(exit -11); the complete rerun passed without adding production retries.
End-to-end VS Code debugging passes mkthor scaffolding, Cmd+Shift+R, the build
task, breakpoints, stepping, locals, watches, restart/rebuild and clean exit.
A real CodeLLDB regression also stops in the common basic library through its
installed source map. Fresh projects build/run from the new installation, and
building a source from another working directory still uses that directory's
executable location/name. The final 55-test focused run covers package fallback,
project cache isolation, unsaved files, diagnostics and ordinary pure calls.

## TLS expected-type propagation and parser isolation

Replace the expected failure for contextual enum members in Thor switch-arm
arrays with a normal passing test. Follow expected types through runtime switch
adapters, compile-time switch/ternary branches, bracket arrays and nested
aggregates. Resolve the nearest real procedure argument independently of its
surrounding result type, preserving named/grouped/generic/imported parameter
types. Treat unresolved contextual switch arguments like implicit literals
while other arguments select an overload. Scope package/local lookup for each
query so reading a global type does not hide the switch's local selector.

Share this resolution between hover, definitions, aggregate completion and
enum completion. Completion can reparse a partial member, so match its original
source position as well as its AST identity. Arrays permit repeated values;
bitsets and explicit keys continue filtering duplicates, without excluding the
member currently being edited. Strip inherited variable type-display metadata
when constructing an enum-member hover so its member signature remains visible.
Give whole-file/reference resolution the same source file and enclosing-call
context as cursor queries. Rename and Find References now resolve contextual
members completely while preserving same-named members of other enums. Truly
unresolved uses still cancel rename rather than produce partial edits.

Expanded parallel native testing exposed a separate parser race: current_errors
was process-global while parsers on independent workers replaced/appended to it
using different scratch allocators. Make the callback collector thread-local.
A two-worker regression repeatedly parses broken documents and verifies every
diagnostic retains its own document path. An isolated temporary copy with the
thread-local annotation removed fails this regression; repository sources and
the installed server are not modified by that negative check. The fixed suite
also passes repeated runs with the seed that previously exposed crashes.

Validation: 106 complete stdio LSP/rename tests pass with no expected failures.
The context matrix covers 27 expression cases plus imported parameter types,
selector enums, incomplete literals, Unicode diagnostic ranges, unsaved fixes,
rename and references. All 1,126 allocation-tracked native TLS tests pass with
the recorded failure seed; two final complete replays also pass. The 89 Node
extension tests pass. Actual VS Code integration passes after rebuilding TLS,
including contextual enum hover, Go to Definition, repeated-value completion
and rename that leaves another enum's Red untouched. The former expected
failure is closed; complex semantic cases still retain the documented partial
OLS-resolver boundaries. Restart TLS to load the rebuilt server; no grammar or
extension reinstall is required for this update.

## Allocating Set/sequence conversions

Add public to_set and to_slice procedures to thor:sets and re-export their
identities from thor:basic. to_set accepts fixed arrays, slices and dynamic
arrays, including distinct sequences and enum-indexed fixed arrays. Its
independent table collapses duplicates and preserves the selected allocator,
including for insertion after an empty conversion. to_slice exposes the former
private snapshot operation as a public owned slice with unspecified key order;
Set mutation operations reuse it for their temporary snapshots. Both operations
copy elements shallowly and default to context.allocator. Existing pure-code
restrictions on Sets remain in effect.

Keep basic's exports as aliases, with one implementation in runtime/sets, and
retain the user's unrelated in-progress basic library additions. Document
ownership, custom allocator cleanup and empty inputs in README.md; extend the
commented examples/basic demo with conversion, deduplication and explicit sort.

Validation: all 81 basic/installation/expression regression tests, 82 complete
stdio TLS tests and seven allocation-tracked native Set tests pass. Cover every
input shape, native Odin consumers, wildcard/selected imports, invalid input
rejections, empty/nil inputs, allocator retention and shallow string/pointer
ownership. TLS infers set[int], set[string], set[f64] and corresponding slices,
finds the exports and offers both names in namespace completion. The shared
runtime view is prepared successfully and the updated commented demo runs.

## Debug memory checks: compiler 0.19.0 / client 0.12.0

Native macOS executable `-debug` builds select basic allocation tracking and
AddressSanitizer by default. Support `--track-allocations=off|basic|trace` and
`--no-sanitize`, preserving explicit Odin sanitizer/module flags. Other-platform
debug builds keep their ordinary debugger/source-map behavior. Native tests keep
Odin's per-test allocator. Normal builds and compile-time helper/probe executables
do not receive these defaults.

Tracking uses stable state and reports after main defers and Odin finalizers.
Allocation reports use generated-to-original location tables. ASan resolves
addresses through `llvm-symbolizer-thor`, using a compact map embedded in a
Mach-O section, including direct execution and moved executable/dSYM pairs.
Generated entry markers keep sanitizer setup from exposing uninitialized
parameter homes during source stepping. The adapter skips their synthetic spans.
Custom allocators and ASan hooks retain their explicit ownership/control rules.
See [the memory guide](docs/memory-debugging.md) for commands, deployment needs,
platform scope and allocation/unwinding boundaries.

## Compiler, library and editor hardening: compiler 0.19.1

Review the post-v1 changes, document their current contracts and inspect the new
filter/sum/range/Unicode/random/table helpers in basic. Preserve unrelated
playground edits and source-file moves. Recover the numbered specification in
`docs/language-spec.md`, and expand the basic API guide and commented demo.

[The audit report](docs/hardening-2026-10.md) inventories the reviewed features,
documentation, coverage, eleven corrected defects and remaining limitations.
Fix local overload extraction, relative SDK dependencies, owned `do` returns,
variadic forwarding, shared native `when` expressions, optional map status,
conditional main tracking, excluded ASan hooks and missing random imports.
Return exact-size input strings for sized allocators, and identify fatal Odin
signals as toolchain errors rather than silent unusual exit codes.

One native Odin code-generation SIGSEGV was recorded during the long test run;
it was not reproduced in isolation, 100 executable runs or ten fresh normal/debug
builds. Keep this distinct from the corrected Thor defects. Conditional ASan
hook selection also remains conservative; the documented explicit-symbolizer
workaround has a real runtime test. Pure library eligibility retains the current
implementation/effect rules, including the UTF-8 decoder's mutable-table limit.

Verification commands for this pass:

```sh
python3 -m unittest discover -s tests -v
python3 tools/build_tls.py test
odin test runtime/format -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
odin test runtime/sets -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
odin test runtime/pure -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
odin test runtime/eval -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
cd editors/vscode && npm test
# From the repository root, in a desktop session:
python3 tools/test_vscode.py
python3 tools/test_vscode_debug.py
```

Validation: a clean full run passes 752 Python compiler/runtime/cache/stdio-TLS
tests. Four final added cases pass in focused revalidation (generic optional map
lookup, its unsaved TLS diagnostic, sized input frees and input allocation
failure); all 13 real CodeLLDB/DAP tests pass. This covers all 769 available
Python cases in this workspace, including the pre-existing local Odin playground
table wrapper. The maintained suite has no expected failures. A separate 187-test
run revalidates the final compiler/library changes and related diagnostics.

All 1,126 allocation-tracked native TLS tests, 89 Node grammar/client/adapter
tests, and 17 native runtime tests pass. Both isolated real VS Code workflows
pass, including a new mkthor project's shortcut, breakpoints, stepping, watches
and restart. The expanded basic demo and full language walkthrough pass in normal
and debug builds with empty stderr and no leak reports. Shared runtime reuse and
the normal-build absence of memory instrumentation retain their tests. The local
TLS/client implementation and versions are unchanged; no extension reinstall is
needed for these compiler/library fixes.

## Odin backend crash investigation

Follow the earlier SIGSEGV into Odin's cached LLVM type names. The crash registers
show a null string pointer with length 26 while copying a name; parallel module
workers access the same non-atomic two-word cache. A C++ ThreadSanitizer compiler
reproduces those accesses when compiling a plain `fmt.println("hello")`, without
Thor. A temporary atomic-pointer publication patch removes this type-name race
while retaining parallel builds. The original release compiler does not repeat
the SIGSEGV in 2,402 stress compilations; race detection is the stronger evidence.

The candidate passes 200 ordinary stress compilations and plain/generated print,
basic and full walkthrough examples. Other sanitizer reports are outside this
narrow fix; an analogous variable/link-name race remains in a two-print example.
The installed compiler, its source checkout and Thor's defaults stay unchanged.
Document optional single-module/single-thread controls, and preserve the selected
trace, test fixture, results and three-file patch under
`upstream/odin-codegen-race`. See [the report](docs/odin-codegen-race.md).


## Application publishing (0.20.0)

Add `thor publish` as a separate command using the existing compiler/evaluator
pipeline, then package in a temporary staging directory. Normal commands do not
load the publish module, read thor.toml, convert images or inspect dependencies.
Publish-only app name/ID/version/type/icon/resource/library/output options are
consumed by Thor. Existing Odin optimization/defines/debug options retain their
meaning; package-only output/resource/linker settings reach only the final native
build. Generated sources and caches retain the existing ownership/layout rules.

An optional [publish] table supplies defaults. Include assets/ recursively and
find assets/icon.png or assets/icon.ico; ambiguous automatic icons require an
explicit selection. PNG/ICO conversion uses optional Pillow, including extracting
the largest ICO image and generating ICNS/ICO/PNG for the selected platform.
Resources preserve project-relative names and empty directories, follow only
project-local noncyclic symlinks, and reject missing/special inputs. CLI resources
add to configured entries. Publishing protects unrelated outputs and replaces an
existing managed package only after staging/validation succeeds.

macOS copies transitive dylibs/versioned frameworks, rewrites load paths/rpaths,
and ad-hoc signs/verifies the copies and bundle. GUI mode launches its native
executable directly; explicit console mode uses a native launcher and a resource
command file to open Terminal. Windows ZIPs embed icon/version resources, set the
GUI/console subsystem and recursively inspect normal/delayed PE imports. Linux
archives use a relocatable launcher, library runpaths and an explicit desktop-entry
installer. Odin's temporary linked-library export helps locate adjacent runtime
libraries without exporting any extra persistent project files. Explicit plugins
are included even when absent from the link graph. OS libraries remain system
dependencies; unresolved libraries/collisions stop publication.

Add the native thor:app library: resource_path returns an owned absolute string,
using the original entry directory in development and executable-relative layout
in published apps. It preserves CWD and ordinary I/O. Runtime flags are scoped to
projects that import the library; no original project resource root is defined in
published code. Native library files remain common installation source. TLS hover,
definition and source-mapped argument errors work without a server/client change.
The publishing guide, installation/README entries and examples/publish cover the
new workflow, platform/dependency boundaries and allocator ownership.

Validation: the full discovery run exercised 804 Python cases in 669.896 seconds.
Its only failures were 13 LLDB cases blocked by sandbox attachment (each also
failed during teardown), plus the existing demo encountering the already diagnosed
Odin SIGSEGV while compiling a compile-time helper. All 13 debugger cases pass
outside the sandbox; the isolated demo retry passes. The later crash record has
the identical alloc_cstring/type-generation stack and a null string pointer with
length 30; preserve it in the prepared upstream reporting bundle. No compiler
installation or default compiler flags were changed to conceal that failure.

The final publishing suite covers 41 cases (40 pass in the sandbox, one conditional
Finder-launch case passes separately outside it), including real moved apps,
PNG/ICO conversions, transitive libraries, read-only library inputs, versioned
frameworks, dynamically loaded plugins, debug allocation tracking, clean rebuilds,
JSON/source-mapped failures, resource-path ownership and Windows/Linux packaging
contracts. The new real stdio TLS helper test passes. Together the full run and
focused/failed-case reruns cover all 811 available Python cases in this workspace.
Native Windows/Linux execution remains unverified on this macOS host. The normal
and published demo both run; Launch Services launches a real GUI package with
argument forwarding and no Terminal wrapper. Existing user playground/basic edits
are preserved. Logs are under /tmp/thor-publish-*.log.


## Icon normalization (0.20.1)

Accept rectangular and larger source PNG/ICO images instead of requiring an
already-square icon with a 4096-pixel input limit. Decode RGBA, choose a square
crop centered on the canvas, and shift it over transparent padding when all
visible content can fit. Detect visibility from the alpha channel (including
palette/tRNS PNGs), so hidden RGB at alpha=0 does not cause a warning and any
nonzero alpha does. Reduce oversized squares to 1024 pixels before the existing
platform conversion. Preserve unmodified square Windows ICO size variants;
regenerate an ICO when its largest image was normalized. Original assets remain
unchanged, including the original copy in the resources directory.

Publish preflight warnings use the existing diagnostic reporter: text and JSON
remain coherent, -ignore-warnings suppresses them, and -warnings-as-errors stops
before compilation or modifying a package. Add coverage for both aspect ratios,
off-center content, transparent/palette padding, nearly invisible edge pixels,
odd sizes, source images above the previous size limit, ICO normalization and
preservation, and the full warning/JSON policies.

Validation: the publishing suite ran 51 cases in 9.299 seconds: 50 pass and the
optional Finder/Launch Services test is skipped inside the sandbox. The actual
react_time project, whose 1024x500 palette icon has 500x499 visible artwork,
publishes successfully with empty stderr using temporary generated/cache/output
directories. Its artwork fits a 500x500 crop and emits no warning; the source icon
hash is unchanged. No editor changes or restart are required.


## Publish optimization default (0.20.2)

Default the final native `thor publish` build to -o:speed when no explicit -o:
option is present. Apply it in Publisher.native_flags, leaving the project's
input flags, compile-time helpers and ordinary build/run/test behavior unchanged.
Explicit optimization always wins, including -o:none for a debug package.
Document the default in publish help, README and the publishing guide.

Validation: the publishing suite runs 53 cases in 13.231 seconds, with 52 passing
and the optional sandbox-restricted Finder-launch case skipped. New coverage
checks default/explicit optimization on all three platform flag paths and
captures real final compiler invocations for default publish, explicit -o:none
publish and ordinary build. Existing packaging, icons, libraries, resource and
debug tracking tests also pass with the new default.


## Immutable declarations and Odin-project conversion (0.20.3)

Investigate Dijkstra's `x, y :> int(mousePos.x)/GRID_SIZE,
int(mousePos.y)/GRID_SIZE`. The multi-expression declaration already works in
Thor; the reported file was still Dijkstra.odin and the editor task invoked
odin build directly. Preserve native .odin parsing, improve the shared parser's
immutable-syntax diagnostic to explicitly require .thor, and document multi-name
initialization/independent inference. Add regression cases for evaluation order,
typed and inferred expressions, the exact coordinate calculation, all immutable
bindings, the .odin diagnostic location, and both TLS hovers plus a mapped write
error on the second binding.

Converting the real program also exposes two sequence-lowering bugs: a reference
range over board.cells[row] receives an array value instead of its original
storage, and nested reads of array fields in readonly value parameters incorrectly
try to take a mutable address. Infer addressability through selectors/indexes,
preserve explicit reference-range lvalues, and use native generic type predicates
for record/pointer and array/reference-container distinctions. Compute those
predicates inside the helper's capture scope. Render reference and value branches
separately so an inactive address-taking branch cannot contaminate readonly
nested reads. Temporary arrays remain readable, and readonly parameter storage
cannot become writable. Tests cover concrete/generic/pointer records, nested
arrays and slice headers, temporary record results, negative indices and reference
iteration. The sequence specification now explicitly preserves these properties.

The user renamed the live file to Dijkstra.thor during the investigation; leave
that current source untouched. Apply staged, backed-up VS Code task/launch/settings
changes using the mkthor configuration, and ignore generated build/cache data.
Dijkstra's actual build-thor-debug task succeeds with its original source content,
producing the executable and source maps under .vscode/bin. No GUI was launched
from that project and no existing user changes were staged in its Git repository.

Validation: all 819 compiler/runtime/cache/stdio-TLS cases pass in 684.078 seconds
(the optional Finder case is run separately outside the sandbox); all 13 real
LLDB tests and all 1,126 native TLS tests pass. Together those cover all 832 Python
cases available in the workspace, with the Finder launch checked separately.
The focused 56-case T07–T12 suite and both immutable TLS cases also pass. The
converted source and real debug task compile cleanly; logs are under
/tmp/thor-dijkstra-*.log and /tmp/thor-multi-immutable-*.log. No installed Odin
compiler or normal compiler flags were changed.


## Odin superset compatibility (0.20.4)

Convert temporary copies of all projects under Code/Odin and Code/Odin_Projects
to .thor and compare their builds with native Odin. The 102 cases include 82
source directories and 20 additional individual/reference versions. The initial
audit finds 17 conversion failures among 79 Odin-successful cases. Resolve every
one: the final corpus has 79 successes with both compilers, 23 shared baseline
failures, and no timeout. Preserve the source/resource/configuration files in
the original projects; all 416 before/after file hashes match.

Preserve native range and enum/type operands, SDK aliases, contextual enum
indices, multi-result iterator protocols, and SOA iteration. Select generic Set
iteration with a compile-time branch around the original loop instead of an
overload call. This avoids an independently reproduced Odin checker stall when
matching a Set parameter candidate against an inferred zipped SOA type. No
installed Odin change, threading workaround, or syntax change is required.

Correct capture/addressability distinctions for writable named results, readonly
value parameters and using projections, value versus pointer type switches,
and fixed-capacity dynamic array inline storage. Resolve runtime locals in source
order, retaining the outer binding in a shadowing initializer. Emit helpers
outside case-match scope and deferred statements. The frontend now exposes
deferred statements and fixed-capacity metadata explicitly. Generic len dispatch
retains native constant expressions; its type witness also accepts untyped string
constants from compile-time switches without evaluating them.

Add identical-source Odin/Thor regression fixtures, runtime storage/aliasing and
evaluation-order checks, parenthesized operands, readonly rejection, Unicode
diagnostic ranges, unsaved TLS error recovery and parameter/local rename checks.
Keep the original audit, all final build commands/logs, and the per-issue fixes
in docs/compatibility-audit-2026-10-29, with the current outcome in RESOLUTION.md.

Validation: both clean Python groups pass (361 and 475 cases, with the optional
Finder test skipped in the sandbox). All 13 real CodeLLDB cases and the separate
Finder launch pass, covering all 849 distinct Python cases in the workspace.
All 1,126 native TLS tests and 89 Node/editor tests pass. Both Thor demos run
successfully, including -o:speed, and the final 79-case corpus recheck passes
without a timeout or conversion failure. The initial full run caught an untyped
compile-switch string length regression during this work; the typed witness,
new regression and clean rerun verify its correction. Logs and compiler hashes
are retained in the audit's fixed directory. Unrelated user edits are preserved.


## Readonly iteration over immutable array slices (0.20.5)

Dijkstra changes searchDirs from native rodata to `:>` storage, exposing the
existing rejection of `searchDirs[:g.diagonal ? 8 : 4]`. That slice normally
provides a writable alias, so the storage checker rejects it and Odin emits
secondary errors for the missing iteration binding. Value iteration itself can
safely read the slice: Odin's loop values are readonly and non-addressable.

Permit an explicit slice used directly as the operand of a value range loop,
including parentheses and runtime bounds. Preserve the original array storage;
introduce neither an array copy nor an allocation. Keep named, passed, returned,
and reference-iterated views under the existing immutable-slice checks. This is
a narrow immediate-use rule, not a general readonly-slice type or borrow checker.
Document the distinction between Thor's immutable-storage enforcement and Odin's
rodata placement attribute.

Validation: 210 immutable/expression/compatibility/ownership/purity tests pass,
including globals, locals, imported arrays, generic values, record fields,
negative bounds, single evaluation of a bound, deferred loops, runtime fn calls
and #run. Rejections cover escaping views, reference loops and mutation/address
attempts on loop values. A real stdio TLS test verifies the original slice error
location and diagnostic clearing after an unsaved conversion to value iteration.
The unchanged current Dijkstra source checks cleanly and its equivalent debug
build succeeds using temporary inputs/outputs; the project's source hash matches
the initial snapshot. No GUI is launched and no project files are modified.


## Reversible compile-time constant colors (TLS/editor 0.13.0)

Add a separate `thorCompileTime` semantic-token modifier for compile-time value
names, including local, imported and evaluated constants. Preserve the existing
readonly classification and colors for runtime immutable storage, parameters and
loop values. Types, procedures, enum members and namespaces retain their existing
token kinds and colors. The modifier is additive and does not change language
semantics or install a new theme.

Provide `Thor: Preview Compile-Time Constant Color` and `Thor: Restore Previous
Constant Color`. The preview defaults to blue-violet `#9B8FE8` and changes only
the user-level `variable.thorCompileTime:thor` rule. Save the original rule in
persistent extension state before applying the preview; preserve it across
repeated tweaks and editor reloads. Restore just that rule, retaining unrelated
preferences and edits made during the preview. Without an active preview, the
original theme remains the default. Document the controls in the editor README.

Validation: all 1,126 native TLS tests, 90 stdio LSP tests and 95 Node/editor tests
pass. New cases distinguish constants from mutable/runtime immutable bindings,
parameters, loop values, types and callable values, including imported constants.
Preference tests cover cancellation, repeated tweaks, extension reloads, existing
styles and exact restoration without overwriting unrelated settings. The real
isolated VS Code extension test applies two colors and restores its original
preference. Build and install extension 0.13.0; preserve the prior 0.12.0 VSIX in
`.thor-cache/editor-backups/` and apply the preview through its normal command.


## Readonly binding colors and cooler purple (TLS/editor 0.13.1)

Refine the optional constant-color preview to `#8582DE`, slightly darker and
bluer, while retaining the first preview's saved baseline. Install the updated
extension and apply the new color through the existing preview command.

Mark ordinary proc/fn parameters and value range-loop bindings readonly in both
their declarations and uses. Include pointer parameters and pointer-valued loop
elements: the binding is readonly even when its target can be changed. An
explicit `&value` iteration remains mutable; its separate index/key stays
readonly. Preserve mutable counters, named results, callback-call colors and
compile-time value classification. Use a separate internal ReadOnlyBinding flag
so this presentation change does not add the :> storage annotation to ordinary
parameter/loop hovers or change resolution behavior. Clear inherited binding
flags when resolving another local, preserving mutable copies and shadowing.

Give parameter.readonly the same theme fallback as immutable variables, scoped
to Thor. Resolve string rune/byte-offset pairs and canonical Set elements so
their uses receive the same color as their declarations. Preserve the original
source positions for reference-loop names rather than coloring the & operator.

Validation: all 1,126 native TLS tests, 92 stdio LSP tests and 95 Node/editor tests
pass. New checks cover default/nested callback and pure-function parameters,
pointers and mutable copies, arrays/slices/dynamic arrays/maps/Sets/bit sets,
enums, string literals and typed strings, ranges/unrolled ranges, generics,
shadowing and unsaved value-to-reference loop edits. Named results and ordinary
mutable counters are checked explicitly. The real isolated VS Code integration
test verifies readonly declarations/uses, mutable reference bindings, theme
mapping and reversible color controls. No compiler or project-source changes.


## Rename global immutable declarations (TLS 0.13.2)

Fix Rename Symbol on a global `:>` declaration, including an enum-indexed array
of records such as `colors :> [NodeType]rl.Color {...}`. The internal immutable
attribute has a zero-width range at the declaration's first character. The
top-level cursor lookup used an inclusive range check and selected that metadata
before visiting the real identifier. Rename normalizes its cursor to the start
of the identifier, so it failed even when invoked in the middle or at the end
of the word. Renaming from a use already worked; local declarations did not take
the same early-return path.

Ignore zero-width attributes when choosing the top-level source declaration.
Preserve lookup for real source attributes and keep rename's original-source
validation, collision checks and scope rules. This repairs the shared cursor
lookup rather than bypassing it for immutable variables.

Validation: reproduce five prepareRename failures before the fix; all 28 rename
tests pass afterward, including new declaration/use/caret-boundary cases,
same-name local shadowing, comments/strings, mixed Thor/Odin references, Unicode
and unsaved :>-to-:=-to-:> edits. The renamed enum-array project checks with Thor.
All 1,126 native TLS tests and 92 stdio TLS tests pass. The isolated real VS Code
integration test applies a rename to a fresh global immutable enum-array fixture.
Rebuild the shared TLS binary; the installed client needs only a language-server
restart. User project files remain unchanged.


## Compile-time string repetition (0.21.0, TLS 0.14.0)

Implement string * integer and integer * string with ordinary multiplication
precedence, retaining string + concatenation. Both operands must be compile-time
constants, as requested; :> runtime storage and ordinary procedure parameters
are not constants. Zero yields an empty string, negative and noninteger counts
are errors, and results use borrowed constant storage without a runtime allocator
or delete. Keep exact native byte/escape/UTF-8 behavior and distinct string types.
Support imported/forward constants, native constant arithmetic and #config,
fn/#run results, constant procedure parameters and selected compile-time branches.

Emit private, source-mapped native constant concatenations in a doubling chain.
Generated code grows with the count's bit length rather than its magnitude;
unknown native constant counts use bounded 128-bit templates. Repetition itself
needs no native execution helper or evaluation cache entry. Apply the existing
16 MiB compile-time value budget before forming oversized strings. Keep a short
inline form for fixed counts through 128 in generic signatures/type expressions;
variable-count generic repetition belongs in the procedure body. Document this
boundary beside the existing generic-power boundary in the README.

Order nested helper constants before their consumers. Preserve native when and
compile-switch result-arm selection around hoisted constants, so unused invalid
repetitions remain unchecked. Recognize runtime operands before lowering them,
producing one source-level error rather than cascaded helper-name diagnostics.
Translate generated assertions into direct integer/count/size messages at the
original operation. Ordinary numeric multiplication and .odin files pass through.

TLS infers either operand order as a string without changing semantic-token
ranges or constant hover style. Extend partial branch selection for string +
and bounded repetition. Test unsaved errors and correction, source positions
after Unicode, original-source rename, and the real VS Code hover/error path.
No TextMate or extension client change is needed; restart the shared TLS binary.

Validation: 601 existing compiler regression cases pass, along with 15 dedicated
repetition tests. Coverage includes precedence/chaining, zero/one, empty strings
with huge counts, Unicode/raw/escaped/invalid-UTF-8 bytes, typed counts and distinct
strings, record-field constants, imports/shadowing, generic bodies/constraints,
default values, fn/#run, #if/#switch, arrays, sets, negative indexing, interpolation,
debug tracking and logarithmic generated size with zero execution-cache writes.
All 122 stdio TLS/rename cases, 1,126 native TLS cases and 95 Node/editor cases pass;
the isolated real VS Code integration passes with its new repetition check. Run
the new commented examples/string-repetition/main.thor with default/configured
counts and -debug, without modifying existing user demo/library/playground edits.


## Preserve string-repetition literal coercion (0.21.1)

Fix PAD :: " " * 30 losing its untyped constant-string status. The explicitly
typed empty seed in the repetition chain forced its concatenations to string,
so PAD + TITLE + " (animation off)" could not be passed to cstring. Use a native
constant empty slice to preserve literal versus typed string behavior. Restore
the original type for distinct strings, using a native type comparison rather
than trusting a partial frontend shape. Apply the same rule to the inline
generic-signature form, including zero repetitions.

Validation covers the exact 30-space padding argument, direct cstring variables
and arguments, both operand orders, typed counts, zero/one/empty/nested results,
and concatenated constants. Explicit string types still require explicit cstring
conversion. Conditional and concatenated inputs retain their native string or
distinct-string type even for zero repetitions. All 18 repetition cases pass;
the related compiler regression run passes and the real VS Code integration
passes with a concatenated repeated string passed to cstring. A real stdio TLS
case verifies the old cstring argument error's Unicode-aware source range and
clearing after an unsaved change to an untyped repetition. No TLS/client code or
allocation behavior changes, and unrelated user files remain untouched.

## Readonly parameter color follow-up

Checked the running editor's TLS protocol response for Dijkstra's `calcPath`,
including pointer and defaulted boolean parameters: declarations and references
have semantic kind `parameter` and the `readonly` modifier. The issue did not
require another compiler or server change. Set the current VS Code profile's
`parameter.readonly:thor` color explicitly to Dark Modern's immutable blue,
`#4FC1FF`, preserving the compile-time constant preview and other settings.
Document the optional preference and how to remove it in the editor README.

Validation: all 95 extension tests and the three targeted stdio TLS tests for
parameter/value-loop readonly markers, shadowing/unsaved edits, and compile-time
versus runtime bindings pass. Temporary protocol tracing was removed afterward;
the parameter color is the only persistent change to the user's settings.
