# Thor development process

The maintained compiler is Rust. The Python compiler is frozen at
`python-reference-2026-11-21` and must not be updated for new compiler work.
[Retirement and validation](docs/python-retirement.md) explains the independent
native gate and opt-in historical tests. Python remains a supported language for
test runners and development tools.

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

Keep the public README organized around named user-facing extensions. Add each
new extension to its linked index and provide a minimal example and an idiomatic
Odin comparison where one exists. Explain Thor-only guarantees and compile-time
facilities explicitly. Detailed contracts and implementation history belong in
the linked reference guides.

README code fences marked `readme-example` are the actual inputs to
`tests.test_readme_examples`; `tests/fixtures/readme-examples.json` supplies only
wrapper context, expected output or expected rejection, and mode selection. Do
not keep a second copy of the snippets in fixtures. Update the manifest when
adding or changing examples, show each runnable Thor example's printed results
in an `// Output:` comment block, and run the maintained example suite in checked
and unchecked builds. Its structure checks protect displayed output, index
order, local links, legacy incoming anchors and coverage of every displayed
Thor/Odin code fence. Introduce Thor syntax before using it in later examples;
use `values ~:=` / `values ~:>` as the conventional scoped-owner spellings while
retaining tests of all accepted spacing forms.

```sh
THOR_TEST_PREBUILT=1 python3 tools/test_compiler.py
./thor run demo
python3 tools/build_tls.py test
odin test runtime/pure -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
odin test runtime/eval -collection:thor=runtime -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
cd editors/vscode
npm test
npm run package
```

Run `python3 tools/test_vscode.py` from the root for the isolated VS Code test.
The automated completion tests apply real provider edits without depending on
background-window keyboard focus. Add `--interactive-typing` to exercise the
suggestion widget in a foreground test window.
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
Named results and arbitrary user procedure contracts remain manual. Anonymous
f-string call arguments additionally live through the entire enclosing expression;
`f!` opts out. `thorlang/fstrings.py` records their headers/allocators in a stack
scope and threads that scope through generated helpers without hoisting argument
evaluation or adding heap allocations. User procedure literals establish separate
invocation scopes. Named f/f! bindings and ordinary factory results remain manual.
Set elements
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

Explicit format fills now use ordinary Odin rune literals: `'界'^5`, `'0'12,`
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

The 0.7.4 editor checkpoint standardized compile-time directive styling.
The conditional-compilation aliases from that checkpoint were subsequently
removed; native `when` and `else when` use normal control-flow scopes.
`#switch`, `#run`, `#assert` and `#partial` retain directive styling.

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
when/config branches, unused and specialized enclosing bodies, valid concrete
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
scope-specific name across when branches and package files. Odin selects the
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
default values, fn/#run, when/#switch, arrays, sets, negative indexing, interpolation,
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

## Readonly struct-member access colors (TLS 0.14.1)

Field uses now carry `property.readonly` when their value storage is readonly:
ordinary value parameters, `:>` records, nested record fields, fixed-array
members and plain value-loop bindings. This is access-path metadata added to
file resolution's symbol copy; field types, declarations, hover, definitions,
rename and compiler behavior retain their existing rules.

Stop propagation at explicit or implicit pointers, including promoted
`using ^Record` fields, slice/dynamic-array elements and reference-loop bindings.
A readonly pointer field stays readonly itself while its pointee's fields remain
mutable. Explicit `:>` map storage retains Thor's existing entry protection;
ordinary parameter map headers do not make their contents readonly. Reset local
lookup for each path component because resolving a global field type can disable
it in the resolver context.

The audit also found inherited `Immutable` flags on newly declared mutable copies
of immutable values. Clear initializer binding qualifiers before restoring each
local/global declaration's own flags. Both the copy and its fields now use mutable
colors. Preserve the user's other preferences and add the Thor-only
`property.readonly:thor` override matching the existing readonly parameter blue.

Validation: 1126 native TLS tests, 96 stdio LSP tests, 95 extension tests and the
real isolated VS Code integration pass. New coverage includes nested and imported
generic records, pointers/dereferences, promoted fields, fixed arrays, slices,
maps, value/reference loops, shadowing, local/global copies of immutable structs,
invalid writes at original source locations, hover/definition and unsaved edits
that change a value parameter to a pointer and clear its diagnostic.

## Exclude comment brackets from pairing (VS Code extension 0.13.2)

Declare `unbalancedBracketScopes: ["comment"]` for the Thor grammar. Comments
already had comment scopes and green foregrounds, but inherited the default
balanced-bracket metadata flag. Explicit exclusion prevents their brackets from
participating in pairing/colorization, including nested block comments and
comments inside interpolation, without suppressing actual interpolation brackets.
The grammar tests now use the manifest's bracket configuration rather than a
hard-coded wildcard, so they exercise the settings installed into VS Code.

Validation: all 97 extension tests and the real isolated VS Code integration
pass. Regressions cover the reported commented procedure/array/call pattern,
nested comments and interpolation metadata. The editor integration invokes the
actual comment-toggle command after matching an active array bracket, verifies
that navigation ignores the commented bracket and that TLS removes its semantic
tokens, then uncomments the line and verifies bracket matching returns. This is
an extension metadata update; load it with Developer: Reload Window rather than
the TLS restart command.

## Readonly type-switch bindings (TLS 0.14.2)

Type-switch value aliases now carry readonly metadata in their declarations and
case bodies, matching ordinary value iteration. Explicit `&value` aliases remain
mutable. Apply the flag both to the initial union/any binding and each narrowed
case binding, including default/multiple-type/nil cases and indexed switch inputs.
A single indexed switch source must not expand into an optional index-status pair.
Assignments now resolve their left-hand identifiers normally, preserving readonly
colors on invalid writes instead of always emitting a mutable-variable token.

File-wide semantic resolution now starts with the original switch binding and
adds narrowed types in each case's own scope. This prevents the final case's type
from leaking into other cases, restores semantic field tokens for struct variants,
and preserves the existing readonly-value versus mutable-pointee field rules.
Nested ordinary switches cannot narrow the enclosing type-switch alias. Hover
and semantic resolution share the narrowed-binding helper; rename remains tied
to the original alias declaration.

Validation: all 1126 native TLS tests, 127 stdio LSP/rename tests and 97 extension
tests pass. New cases cover the Sudoku-style indexed union switch, readonly
invalid writes and precise diagnostic ranges, unsaved value/reference edits and
recovery, struct/pointer variants, any values, grouped/default/nil cases, nested
ordinary switches, shadowing, mutable copies, narrowed hovers and alias rename.
The real VS Code integration also checks declaration/case/assignment token colors
and readonly versus reference struct-member colors. No language, compiler, theme
or user project source changes are needed; rebuild/restart TLS to load the fix.

## Independent purple labels (TLS 0.15.0 / VS Code extension 0.14.0)

Append `label` to the semantic legend without changing existing token indices.
Visit label declarations on loops, conditionals, blocks and switches, ordinary
break/continue targets, and or_break/or_continue targets. Labels have readonly
metadata in their own category, separate from variables and compile-time values;
regular variables sharing a label's name keep their normal classification.

Give the grammar's same-line and separate-line label declarations a consistent
`entity.name.label.thor` scope, preserving punctuation and requiring a keyword
boundary so declarations such as `value: for_values` remain type annotations.
Map semantic labels to the same scope. Apply the requested `#8582DE` purple in the
local profile using separate semantic and TextMate label preferences, leaving
constant/variable preferences intact. Document how to change/remove those rules
independently; constant-color preview/restore also preserves the label preference.

Validation: 1126 native TLS tests, 128 stdio LSP/rename tests, 98 extension tests
and the real isolated VS Code integration pass. New regressions cover declaration
layouts, branches and optional branches, nested control flow, Unicode ranges,
comments/strings, same-named variables, label hover/rename and unsaved layout
changes. Both grammar fallback color and resolved semantic classification are
checked. No compiler or user project source changes are required.

## Built-in panic as a switch result (Thor 0.21.2 / TLS 0.15.1)

Permit an unshadowed built-in panic call, including parenthesized calls, as a
switch expression arm. Emit the call as a terminating statement rather than
`return panic(...)`, and omit it from the common-result type query. Retain native
checking of the call's arguments, every other result and case, and evaluate the
selector and selected arm exactly once. Ordinary void calls, aliases of panic,
user declarations named panic and other procedures declared `-> !` receive no
special treatment. All-panic runtime switches need an expected result type.

Use the same terminating-statement rule in ownership selection so anonymous
string and Set consumers never assign a panic call into a generated temporary.
Keep compile-time switches lazy: typed selected panic arms call an expression
helper, while inactive ones remain unchecked. A selected compile-time switch
panic has no inferred result type and needs a destination/argument/return type.
Allow the built-in panic in pure code, like assert; reaching it during automatic
evaluation or #run produces a compilation failure at the original evaluation
site. No special case is added for arbitrary non-returning procedures.

TLS ignores builtin panic arms when inferring results and contextual literals.
Resolve each value-producing arm's value type, including procedure results,
rather than mistaking a procedure's own symbol for its return type. Preserve
normal inference for user procedures named panic. Regression tests cover both
selector forms, panic position and parentheses, contextual records/named arrays/
enums/generics, execution order, selected failures, pure evaluation, ownership,
shadowing/imports/aliases, Unicode diagnostic locations, and unsaved TLS edits
and diagnostic recovery. Check the user's puzzle from a temporary source copy;
the original project is never changed by this verification.

Validation: 208 compiler/diagnostic/ownership/purity tests, 130 stdio LSP/rename
tests, 1126 native TLS tests, 98 extension tests, and the isolated real VS Code
integration pass. A temporary copy of 15_puzzle.thor passes thor check. The local
TLS process is restarted with the rebuilt binary; no extension reload is needed.

## Contextual membership literals (Thor 0.21.3 / TLS 0.15.2)

Use a typed needle as the expected element type of a directly written membership
array, including parenthesized literals: `tile: i8 = 4; tile in [4, 8]` lowers to
an i8 array rather than eagerly defaulting the first element to int. Render every
element with that context, including the first elided record/nested array. Keep
named and explicitly typed containers fixed, and use native assignment checking
to reject typed element mismatches, fractional integer values and overflow.
Literal needles still receive the element/key type from an existing container.

Determine whether a needle has a fixed type without materializing its value.
Follow untyped constants/aliases and arithmetic operands; shifts keep the left
operand's type. Preserve native untyped boolean comparisons and constant-query
results such as len(fixed_array), len(constant_string), size_of and abs. Both
untyped sides retain normal first-element defaulting and homogeneous-array
validation. The membership AST supplies context even during independent type
queries/ownership lowering; this does not depend on the order of rendering.
Type queries never execute operands. Existing eager element evaluation, needle
before container order, surrounding short circuiting and allocation ownership
remain in force. Type lists, ranges, native maps/bit sets and Sets retain their
existing membership semantics.

TLS follows membership context into inline array/record/enum literals, in both
directions, using original source nodes for definitions, completion and tokens.
Handle membership directly in if/loop conditions as well as calls, declarations
and returns. Compiler diagnostics continue to point at the actual rejected
literal or typed element, including after unsaved Unicode edits and corrections.

Tests cover signed/unsigned widths through 128 bits, distinct types, floats,
untyped constants and expressions, imported constants, stored containers,
arrays/slices/dynamic/fixed arrays/maps/bit sets/Sets, enums/runes/strings/nil,
nested arrays/records, arithmetic/conditional/call/field/index/pointer needles,
generic type/value constraints, when/#switch, pure/#run/automatic execution,
inactive branches, empty lists, type lists, ownership and execution order. Add
real VS Code checks for contextual field hovers and precise unsaved overflow
diagnostics, with stdio tests for enum completion/navigation in both directions.

Opaque SDK constants such as math.PI retain their untyped status through the
existing on-demand native-source index. Ordinary variable needles require no
SDK inspection, and native libraries are never copied merely for this check.

Validation: 300 broad compiler/diagnostic/ownership/range tests, 72 related
library/purity/string tests, 19 focused membership tests, 132 stdio LSP/rename
tests, 1126 native TLS tests, 98 extension tests, and the isolated real VS Code
integration pass. A temporary copy of the current 15_puzzle.thor passes thor
check. The local TLS process is restarted with 0.15.2; no window reload is needed.

## Automatic using-stmt opt-in (Thor 0.21.4)

Enable gated using forms in .thor files by emitting a per-file
`#+feature using-stmt` header. Detect standalone Using AST nodes and Using
fields in ProcedureType.params, including aliases, callback signatures and
anonymous procedures. Embedded struct fields and using value declarations are
ungated Odin syntax and receive no automatic header. Detecting syntax rather
than searching text avoids comments/string false positives and leaves ordinary
.odin dependencies under native feature rules.

Export actual header file-tag tokens from the CLI frontend, without inserting
anything into its parser input. Preserve explicit feature settings, including
multi-feature tags and `!using-stmt`, rather than adding duplicate/overriding
tags. Comments that merely mention a feature flag do not configure it. Existing
compiler syntax/type restrictions, purity checks and immutable-storage rules
still apply to using; enabling the feature only removes its opt-in requirement.

Insert the generated header as a mapped support fragment at the root's byte
offset 0 (or after a UTF-8 BOM), retaining CRLF where present. All original
fragments retain their exact byte mappings. Imported Thor files, checked/pure
helpers and compile-time evaluation use the same path, with no user-file edits.
Source-derived flags refresh on cache hits when edits add/remove using syntax.

Tests cover gated/ungated forms, pointer/package/multiple statements, generic
parameters, nested procedure types, explicit tags/opt-out, comments/strings,
per-file imports, pure/#run/automatic evaluation, BOM/CRLF exact mappings, and
runtime/debug compiler-error locations. Stdio LSP checks hovers, semantic ranges,
rename and unsaved diagnostic recovery. Real CodeLLDB and VS Code tests exercise
original-line breakpoints, procedure prologues/steps, arguments/locals, and the
mkthor Cmd+Shift+R task/launch/restart path with the generated header present.

Validation: 380 compiler/cache/purity/import tests, 133 stdio LSP/rename tests,
1126 native TLS tests, 14 real CodeLLDB tests, 98 extension tests, and both real
VS Code language/debugger integrations pass. A temporary copy of 15_puzzle.thor
also passes thor check after removing its manual using-stmt tag. User source
files remain unchanged, and the local TLS process is refreshed for diagnostics.

## Promoted-field expression context (Thor 0.21.5)

Correct Source.type_hint for using-promoted fields. Resolver projection symbols
intentionally retain their owner's declaration for storage protection. Looking
at that declaration's annotation supplied ^Triangle rather than Kind for
`kind = switch {...}` inside `proc(using tri: ^Triangle)`. Aggregate fields
already used a value-type witness, but scalar/enum fields fell through to the
owner annotation. Query the actual promoted expression with type_of before
that fallback, preserving its lexical scope, nominal identity and private types.
Do not replace the projection's shared storage declaration or mutability flags.

Regression coverage includes the triangle example's boolean switch and ternary
enum arms, selector switches, standalone/declaration/parameter using, pointer
promotion through embedded records, generics, scalar/distinct/string/array/record
fields, private imported enum types, local shadowing, readonly rejection, and
pure/#run/#switch paths. Wrong branch values identify Kind and highlight their
original source spans. TLS checks enum hover/definition and unsaved diagnostics;
the real VS Code integration exercises the promoted switch destination too.

Validation: 326 compiler/diagnostic/using/switch/compatibility tests, 34 focused
using/purity tests, 134 stdio LSP/rename tests, 98 extension tests and the real
VS Code integration pass. A temporary copy of the user's triangle_solver.thor
runs successfully and classifies the 3-3-3 example as equilateral. The original
project is unchanged; unused-import vetting also accepts the generated code.

## Multiple switch results and assignment skipping (Thor 0.22.0 / TLS 0.16.0)

The parser represents a result list as a virtual SwitchResults call, retaining
every original result expression and span. A standalone break arm has its own
virtual SwitchBreak node. Native single-result switches retain their existing
lowering. Multiple results use generic Odin procedures with one result type
parameter per position; declaration, assignment, return and expanded argument
contexts supply independent type witnesses. Direct multi-result calls and
nested switches use the same native multiple-value contract.

An assignment whose complete RHS is a switch may use break in any arm. Capture
destination addresses/indices in source order before selecting the switch,
then perform a native multiple assignment only for a value arm. Map elements
need their header's address and a typed key, rather than an element address.
Generic sequence/map dispatch uses separate helper names for each branch;
sharing those names could instantiate a sequence address operation for a map.
Post-statement assignments retain Odin's required assignment syntax. Default
arms are still mandatory; break is rejected in other value contexts.

Whole-statement #switch selection uses native when blocks, preserving constant
declarations, visibility and unchecked inactive arms. Header contexts use a
lazy generic helper instead. Arity errors remain semantic and branch-local,
including native syntax's stricter constant-declaration expression counts.
Native Odin disallows global multiple-value initializers; runtime Thor globals
stage results once in a private value record and initialize the real names
from its fields. This adds no heap allocation and preserves global dependency
order. Global helpers use a contextless convention, following Odin's normal
global initializer restriction.

Purity tracks borrowed origins separately for each result. Anonymous Thor
allocations discarded into blanks are released after all RHS work; named
values remain borrowed. Explicit result values and arity/type witnesses keep
their original mappings, so errors identify the appropriate arm/result.

TLS queries each result through a projected expression without rewriting the
document AST. This retains original semantic tokens and rename/reference
spans, while giving each local/global binding its own type and each contextual
enum/record value its corresponding destination, return or argument type.
Both selector forms, runtime/compile-time switches, generics, private/promoted
fields, aliasing, map/sequence destinations, readonly rejection, skipped
assignments, ownership, diagnostics and unsaved recovery have regression tests.
The separate commented walkthrough is examples/switch-results/demo.thor.

Validation: the 832-test compiler/integration sweep (one platform skip) found a
nested-#run context regression while adding native global initializers. Narrow
contextless helper generation to the new global-switch path, rather than
changing the evaluation helpers' conventions. The exact failing test and 172
affected allocation/cache/purity/default/native-call/repetition/switch tests
pass after that correction; 128 focused switch/diagnostic/using/compatibility
checks pass too. Also pass: 138 stdio LSP/rename tests, 1126 native TLS tests,
15 real CodeLLDB tests, 98 extension tests, and the isolated real VS Code
integration including multiple-result hovers and mapped unsaved errors.
The main demo and new walkthrough run successfully (the walkthrough also in
debug with clean allocation tracking). An unchanged temporary copy of the
triangle solver runs; five further temporary input variants exercise its
multiple-result branches. External project source and unrelated user edits
are preserved, and the rebuilt local TLS is refreshed.

## Minimal project creation and run shortcut (Thor 0.23.0 / extension 0.15.0)

`thor new NAME` dispatches to project_template before compiler/bootstrap work.
It creates NAME/NAME.thor and only .vscode/tasks.json and launch.json. The
editable shared starter lives at templates/main.thor, copied from the user's
Desktop/new.thor. `--template` reads another UTF-8 source verbatim, retaining
bytes/newlines; `--gitignore` adds optional build/cache ignores. Validate names,
template reads and existing/dangling-symlink destinations before writing.
Creation works without Odin. No caches, native binaries or library copies are
created until a build/run. Project-named files retain native filename filtering;
warn for platform/architecture/test suffixes rather than overriding that rule.

The mkthor launcher delegates to the same `thor new` dispatch with
~/Code/Thor as its default parent and --open enabled. --parent, --no-open,
--template and --odin remain available. Opening uses the VS Code CLI when
present, otherwise the existing macOS open behavior. An editor launch failure
does not undo the successfully created project. No shell profile change is
needed because both launchers already live on PATH.

The extension contributes thor.run (Cmd+R / Ctrl+R) alongside the existing
thor.debug (Cmd+Shift+R / Ctrl+Shift+R). Both shortcuts remain scoped to Thor
editors. Run saves changes and prefers the current workspace's run-thor task;
existing projects without one get a process task. The whole package runs, so
sibling source files work. Tasks use process arguments, an interactive terminal,
and one concurrent instance. No debugger or debug flags are added by normal run.
Debug retains its original pre-launch build, source maps, breakpoints and steps.
TLS still discovers the shared installation without a per-project settings file.

VS Code silently declines process tasks in windows without any workspace
folder. For a saved standalone file, launch the command in an integrated terminal
with explicit POSIX-sh or PowerShell quoting, source-directory cwd and persistent
visible output. Each run gets its own terminal so a new command cannot become
stdin for an already running program. Untrusted workspaces and failed saves do
not start execution.

Tests cover the exact three-file layout, default/custom template bytes, missing
and invalid templates, no-Odin creation, optional flags, spaced paths, name and
symlink rejection, editor opening, wrapper delegation, and build/debug identity.
Extension tests cover configured tasks, process/standalone fallbacks, shell
quoting, trust/save failures and shortcut registration. The real VS Code debugger
fixture now lives separately under tests/support/project_debug.thor, keeping
the actual user starter small. Integration tests run an unsaved project with a
sibling source, verify the configured and fallback tasks, then check breakpoints,
step in/over/out, watches and restart. A separate empty-window integration runs
a spaced standalone filename. The standard language integration also passes.

Validation: 45 Python creation/build/installation tests, the rejection-fixture
check, 106 Node extension tests, and all three real VS Code integrations pass.
The tested VSIX is installed locally at extension version 0.15.0; its run-command
source matches the checkout. Reload VS Code once to activate the new client
command in an already-open window. User source, Desktop template and unrelated
working-tree changes are preserved.

The user's global Cmd+R binding to code-runner.run would override the extension
default. Append a Thor-only thor.run binding in the user's keybindings.json,
preserving the Code Runner rule for other languages. Document the same scoped
override in the extension guide for installations with existing run shortcuts.

## Direct terminal run command (extension 0.15.1)

Replace Cmd+R's task enumeration/execution with the literal `thor run .` sent
to the active integrated terminal after saving edits. Preserve that terminal's
cwd and shell environment. If no live terminal exists, create a regular terminal
using the user's default profile in the workspace or saved file's directory.
Reuse it on repeated runs. No task providers, compiler/TLS path lookup, explicit
shell, path quoting or standalone-file special command is needed. Normal run
now follows exactly the terminal command's package and environment semantics.
New projects emit only the debug build task; existing run tasks are left alone
and no longer consulted by the shortcut. --odin on thor new affects debugging;
normal execution uses the terminal's PATH/THOR_ODIN. Debug launch stays unchanged.

Verification: 106 Node tests and 11 project-creation tests pass. Tests reject
any task enumeration/execution or toolchain lookup in the run handler, check
terminal reuse and exited-terminal replacement, and preserve save/trust checks.
Real VS Code checks repeat the command in one terminal, execute sibling sources,
assert that no run task starts, and retain source breakpoints, stepping, watches
and restart. Dispatch measured 32 ms initially and 1 ms on reuse in that test;
these timings exclude compiler/program execution. The empty-window terminal
test passes as well. The local VSIX is updated; reload the editor to load the
new client code. No compiler changes or cache invalidation are required.

## Selected import coloring and unused ranges (TLS 0.16.1)

Selected-import aliases were omitted from semantic tokens entirely, leaving
names such as print with the grammar's generic variable color. Emit only the
real selected identifier using its resolved symbol kind, preserving function,
overload-group, type, constant and mutable-variable colors. Keep the overlapping
synthetic namespace and declaration out of the token stream.

An exclusive selected-list end at a newline was also converted using columns
from the following line, clipping the unused hint. Convert its original byte
offset directly to an LSP position, including Unicode, CRLF and EOF. Within a
used import, fade unused selected names individually. Namespace-qualified calls
use the namespace, while local shadowing and qualified field identifiers do
not count as selected-name uses. Wholly unused imports retain one full-range
hint; the import organizer continues to consider only wholly unused packages.

Verification: 141 stdio LSP/rename tests, all 1126 native TLS tests with memory
tracking, and 106 extension tests pass. New regressions cover unused procedures
and overloads, types/constants/variables, multiline selections, qualified and
shadowed names, live edits, namespace-free imports, Unicode, CRLF and EOF.
Real VS Code integration verifies selected function tokens, individual unused
hints, full-declaration hint coverage and unsaved use/clearing. A read-only
check of the user's sandbox.thor confirms the print token and complete unused
range. No compiler, scaffold, theme or production extension changes are needed;
restart TLS once to load the rebuilt server.

## Hover on selected imports (TLS 0.16.2)

Treat a selected identifier in an import list as a normal imported-symbol
reference during position lookup. The synthetic alias's attributes and fake
namespace previously intercepted hover and produced no tooltip. The normal
hover renderer now supplies the same signature/type and documentation as code
references, including for unused imports, multiline lists, namespace-free
imports, Unicode identifiers, generics and overload groups. Navigation from
the list also follows the original declaration.

Keep imported symbols' original initializer/type and mutability when resolving
the adapter. Previously a selected constant could display the internal
__thor_import_N.NAME expression, and a mutable imported variable could inherit
the adapter's constant flags. The adapter is presentation/scoping metadata,
not another user-visible constant declaration.

Regression tests compare import-list hovers directly with function-value and
call hovers, check every character in print and its exact original range, and
remove uses through unsaved edits. Mixed lists cover record types, constants,
mutable variables, generic procedures, overload groups and Unicode names.
Actual VS Code integration checks signature/docs equality and tooltip ranges
before and after use/removal. All 143 stdio LSP/rename tests and all 1126 native
TLS tests pass, with memory tracking enabled for the native suite. The local
TLS binary is rebuilt; restart the language server once to load the change.

## Final Thor brand and transparent file icons (extension 0.15.2)

Finalize the straight T with its wider cap and small foot, the selected double
diagonal H, the original outlined O, and the R's sharp joined leg. The three
brand exports are assets/branding/logo-slim.png, emblem.ico and sourcefile.ico;
editable SVG masters live alongside them. The source-file glyph is yellow on
transparency, with no document sheet or colored tile. The app emblem retains
white on the rounded gold gradient. ICO exports include 16 through 256 pixels.

Register the transparent T with contributes.languages[].icon, using the same
mechanism as OLS's Odin file icon. The dark-theme glyph uses the palette's
bright yellow, with darker gold on light themes. This works with the current
language-icon-aware theme without changing user settings or Odin associations.
The extension listing now has the app emblem. Loading these new manifest
contributions requires Developer: Reload Window after installing the update.

tools/build_branding.py reproduces the PNG/ICO exports from SVG with isolated
Chrome renderers and ImageMagick. It closes its renderers once the PNG is
complete, including macOS Chrome versions which otherwise remain running.
These authoring tools do not run during compilation, TLS startup or extension
activation. Extension tests check the icon paths, glyph-only transparent SVGs,
light/dark palette and supported PNG listing image. Export verification checks
the source icon's transparent margins and opaque yellow pixels in all 7 sizes.

All 108 extension tests and the real isolated VS Code integration suite pass.
The packaged VSIX includes the PNG emblem and both glyph-only SVGs, and the
local extension is installed. No compiler/TLS rebuild or user theme changes
are needed.

## Arbitrary precision: stage 1 runtime foundation

Checkpoint `238dd59` preserves the pre-feature library/playground edits and table
tests. The accepted Integer/Rational syntax, direct numeric ownership compromise,
aggregate/native boundaries, constants, and five implementation stages are in
`docs/arbitrary-precision.md`. Keep its distinction between implemented runtime
APIs and pending compiler features explicit.

`runtime/big` contains native Integer/Rational storage and constructors, strict
integer/finite/repeating-decimal parsing, canonical fractions, independent
cloning/getters, atomic replacement, error handling, and decimal text output.
Empty values represent zero without allocating; an empty rational's logical
denominator is one. Nonzero rationals are reduced with positive denominators.
Text conversion uses integer arithmetic throughout, without a float intermediate.

Stage 1 uses ordinary native ownership. Language-level predefined types, long
unquoted literals, automatic direct copies/replacement, dot properties, operators,
fn/#run support, and their TLS adaptation remain future work. No new parser,
semantic-token, extension, or debugger protocol is introduced in this batch.
The driver excludes the new package's native test files during dependency
discovery, just as for the other runtime support packages. Shared-runtime
publication already discovers native packages automatically.

Backing-library workarounds are local to Thor. Validate complete numeric text;
the backend accepts empty/partially terminated inputs. Normalize denominator
signs before arithmetic; the installed rational setter loses a negative
denominator's sign. Destroy stored digit arrays through their saved allocators
and reset the real caller value; backend destruction uses the current allocator
and does not reset that value. Format magnitudes and remove conservative padding
before adding signs; some large negative backend outputs otherwise gain a zero.
Use a limb-based output bound instead of backend radix_size: its integer-log
path calls a power helper with a scratch leak on allocation failure. Do not
modify the installed Odin checkout for these workarounds.

Runtime Error distinguishes Invalid_Text from Invalid_Allocator. The backend's
Invalid_Argument also covers allocator errors and cannot safely stand for both:
only a genuine text syntax error may yield ok=false. Other errors panic through
location-aware wrappers or return from try_ APIs. Failed constructors return
empty values, partial allocations are freed, and failed replacement preserves
the original target. Exponent magnitude overflow is checked before constructing
powers; available memory still limits huge valid values.

Validation: the selected Python compiler/library suite passed 130 tests; the
additional constructor-rejection regression and the final seven-test big runtime
integration suite pass. The 12 native big-number tests pass with allocation
tracking and in a debug AddressSanitizer build. They sweep successive allocation
failures across 11 operation paths, including resizing a 3,600-digit number,
normalization, output, cloning, and replacement. Independent Python int/Fraction
oracles cover 106 integer cases (through 6,000 digits) and 155 rational cases
(including positive/negative decimal exponents of 4,096). Existing sets, format,
pure, and eval native suites pass all 17 tests with allocation tracking.
Fresh-project imports, wildcard/selected imports, malformed input, incompatible
constructor types, and isolated allocation/zero-denominator panics are covered.
Both the main demo and commented walkthrough pass in the compiler regression
suite. The new commented example runs with allocation tracking:

```sh
./thor run examples/big_runtime --track-allocations=basic
python3 -m unittest tests.test_big -v
odin test runtime/big -define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true
```
## Intermittent comment bracket colors (VS Code extension 0.16.1)

The user reported brackets retaining rainbow colors inside line-commented code
after Cmd+/, cleared by reloading the window. The existing grammar excludes
comment scopes from bracket pairing. Both static TextMate metadata and a clean
VS Code renderer correctly excluded the reported loop/call brackets through
comment toggles, undo/redo and semantic-token updates. The persistent bad state
itself has not been reproduced, so a specific upstream defect is not established.

Add a bounded defensive recovery in `editors/vscode/bracket-refresh.js`: after
comment-delimiter edits settle for 100 ms, register an empty Thor language
configuration overlay. VS Code's language-configuration notification rebuilds
its separate bracket tree from current tokens. The empty overlay preserves all
installed language rules; previous overlays are disposed. Ordinary code/text
edits do not request this refresh. Source snapshots are editor-local and released
on document close. This does not affect compilation, diagnostics or source maps.
The command `Thor: Refresh Bracket Highlighting` requests the same recovery
immediately, including for an already affected document. No user settings change,
source edit, language-ID switch or TLS restart is involved.

Unit coverage checks delimiter insertion/removal (including single slash/star
edits), batching, ordinary typing, document lifecycle and disposal. Real editor
coverage exercises multi-line Cmd+/, undo/redo and manual refresh while checking
bracket navigation, unchanged buffers, and continued TLS hover. This is a
workaround with a tested mechanism, not a claim that the intermittent trigger
has been isolated.

## Exclude literal string brackets (VS Code extension 0.16.2)

The reported `print(f"fib ({i}): {a:,}", end="\n\n")` had orange
string tokens, but its literal parentheses still inherited the balanced-bracket
metadata flag. Add explicit bracket exclusions for ordinary string/rune scopes,
escapes, and interpolated literal text. Interpolation needs a leaf-only
`meta.interpolation.literal.thor` scope: excluding the entire outer string scope
would also disable genuine brackets inside `{...}`, including nested calls and
nested fstrings. Quoted/raw and f/f! forms share the correction; escaped `{{` and
`}}` remain literal, and literal brackets never affect code nesting.

The new grammar regression fails on the original manifest at the exact opening
parenthesis in the reported example. It checks color, token type, and bracket
metadata across eleven cases: quoted/raw/manual/nested interpolation, conditional
fields, strings inside fields, ordinary strings, runes, escaped braces and rune
format fills. Real VS Code tests verify that call/field bracket matching remains
active and literal parentheses do not match, across seven ordinary/interpolated
forms and unsaved replacements. All 118 extension tests and the full isolated
VS Code integration suite pass.

## Power diagnostic cascades through numeric dispatch

Reproduced the user's `double_bet` case using a copy of its source. An immutable
loop binding initialized by `HOUSE_ODDS ** i`, with `thor:basic` imported, emitted
a generated numeric-dispatch return failure before the actual `f64`/`int` power
mismatch. The existing filter handled `power-type` query failures, but this
additional failure mapped to the outer Binary operation instead.

Recognize failed returns of unresolved compiler-generated `$...Result` type
parameters before sanitizing generated names. Mark those failures as cascades;
the existing range-aware filter drops one only when a primary error belongs to
the same source expression. Actual typed returns and user generic parameters
retain their errors, and an isolated failure is never silently discarded.
Power mismatch messages also request an explicit conversion and explain that
only untyped numeric constants are coerced. The fix applies to both CLI output
and TLS compiler diagnostics; it requires no TLS or extension rebuild.

Validation: 103 diagnostic/source-map and power tests pass. Regressions cover
the original enum-table/constant/immutable-loop interaction, a simultaneous
unrelated undeclared name, and a genuine return-type error. Both affected TLS
tests pass, including the original operation range and unsaved `f64(i)` recovery.
A copied version of the user's full file now emits one readable power error;
adding the explicit cast checks successfully. The original file was untouched.

## Typed loop bindings (Thor 0.29.0, TLS 0.22.0, extension 0.17.0)

Add `for number: f64 in low..=high` and
`for value, (index: i8) in values`, including reference elements. Value
annotations are restricted to native numeric intervals; grouped index annotations
are restricted to arrays/slices and their native dynamic/pointer forms. Existing
Odin loop spellings and inference retain their behavior. A range annotation gives
untyped bounds context without silently converting already-typed values.

The shared parser stores an annotated binding as a Field_Value containing the
original name/type nodes. Frontend output exports normalized binding names,
annotation roles, original binding spans, and native initializer roles. TLS
retains those ASTs for type hovers, alias definitions and readonly semantic
tokens. Position/reference traversal treats them as declarations, avoiding the
named-argument interpretation that initially prevented rename at a header.

`thorlang/typedloops.py` lowers annotations while retaining the native loop.
Range-bound helpers take an explicit type parameter before their typed argument,
so Odin supplies ordinary literal coercions and rejects typed mismatches. Helper
arguments stay at the original endpoints: this intentionally preserves native
evaluation order/frequency (an upper bound may be evaluated on each iteration).
Floating-point iteration retains native unit stepping and precision limits.

Typed array/slice indices use a hidden native counter and a checked conversion
at each visited iteration. Runtime support is in the existing sequence package;
no new SDK copies, output artifacts, allocator use, or reserved terms are added.
Fixed arrays validate their complete index range at compile time. Dynamic-length
inputs panic before an unrepresentable visited index, including optimized builds
with assertions and bounds checks disabled. Early exit need not visit every
possible dynamic index. Native enum indices and iterator procedures keep their
unannotated forms. Contract diagnostics point at the original annotation.

The readonly exposed index lives outside the original body block, preserving
legal body shadowing. Thor's storage checks reject writes and mutable addresses.
Native break/continue/return/labels and defer semantics remain in place. `do`
bodies and deferred loops are supported. A lifted loop initializer occupies a
parent synthetic block: Odin collects a when condition before variables in its
own block, so this extra scope is necessary for annotations such as
`type_of(prototype)` that refer to initializer bindings.

Source mappings cover checks, bound helpers and exposed bindings. The debug
adapter skips generated checks/helpers and hides the internal counter. Real
CodeLLDB tests verify header step-in, body breakpoints, user binding types/values,
and hidden-temporary filtering for both annotation forms. Main and commented
tour demos include examples and invalid-case explanations. The spec, README and
editor README record the semantics; `fort`/`forindex` snippets insert the syntax.

Validation: 162 compiler regressions passed, followed by a focused run of all
19 typed-loop tests and targeted final boundary/diagnostic checks. Another 89
ordinary-language compatibility/TLS tests passed. All 1,126 native TLS tests,
119 extension/grammar tests, the full isolated VS Code integration, and 23 real
debugger tests passed (22 in the full run plus the additional index step-in
regression). Both demos run with -debug, and the commented tour passes its
allocation-checked test wrapper. Coverage includes aliases/generic types,
fn/#run/cache invalidation, empty/max-size arrays, dynamic overflow, references,
shadowing, control flow, initializers, C callbacks, and unsaved editor recovery.

## Type operands in value indexing: 0.29.1

Known type operands now bypass generated value-index/assignment helpers before
native checking. `Resolver.known_type_operand` deliberately excludes ambiguous
conditional declarations, runtime typeid variables and polymorphic formals.
Native checking still decides whether the use is active; no eager Thor rejection
breaks inactive when branches or uninstantiated generic bodies. Valid array type
aliases, conversions and value expressions retain their existing behavior.

Diagnostics translate the native type-as-value rejection at the original operand
and add an original-declaration note. Only contextual implicit-compound failures
from that same invalid assignment are filtered; explicitly typed nested values
and independent errors remain. Terminal rendering and JSON `related` notes share
positions. TLS 0.22.1 carries the notes through LSP `relatedInformation`, preserves
editor path spelling, and owns/releases the copied URI/message storage.

The focused review and five further proposals are recorded in
[the diagnostic review](docs/diagnostic-review-2026-11-01.md). Their implementation
is described in the 0.29.2 section below.

Validation: all 181 compiler diagnostic/ordinary-language regressions passed,
as did 113 diagnostic/typed-loop regressions and the additional nested-expression
and independent-error tests. The final complete diagnostic suite passed all 95
tests after those refinements. All 1,127 native TLS tests passed with memory
tracking, including note storage ownership and both removal paths. Two real TLS
transport tests cover Unicode use/declaration ranges, imported unsaved notes and
repeated error recovery. The full isolated VS Code integration confirms the
related note reaches the editor; two CodeLLDB smoke tests preserve breakpoints,
source stepping and helper-local inspection. The live TLS binary is rebuilt;
no grammar or extension runtime update is needed.

## Contextual diagnostics and generated builtin names: 0.29.2

The five follow-ups in the diagnostic review are implemented. Sequence dispatch,
element type queries and normalized bounds have distinct emission origins.
Diagnostics use these origins and the original Index/Slice nodes to suppress
inference/normalization cascades only when a real unresolved-name/field diagnostic
exists in the same operation. Noninteger bounds use Odin's concrete argument
types, including generic instantiations, and point to the original index or
lower/upper bound. Native map/enum rules and integer representation remain intact.

Expression expansion records now include lowered calls, indices, slices,
interpolation and switches. Diagnostics restore original expressions from those
records even when Odin pretty-prints whitespace differently. Literal contents
and identifier boundaries are retained. Contextual switch-arm errors remain
specific to the actual wrong arm. Membership conversion diagnostics add context
and a related needle location, without relabeling nested explicit value errors.

`thorlang/hygiene.py` protects generated references through `base:builtin` when
the source's package or lexical bindings can shadow a native callable operation.
Only synthetic native tokens are qualified; exact source, literals/comments,
selectors and explicitly marked renamed user call targets retain their meaning.
Files without relevant shadowing skip the lexical pass/import. Qualifier
insertions shift all generated map coordinates, expression expansions and debug
regions; original UTF-8 offsets are unchanged. This adds no frontend invocation,
subprocess, new output artifact or reserved word.

Native query detection respects actual binding identity, including aliases and
selected imports. The debug adapter recognizes source call-target regions, so
user `type_of` procedures keep evaluated arguments and real breakpoint locations.
Sequence-only generated fragments remain hidden during stepping. Extension
0.17.1 includes that adapter update; TLS 0.22.2 uses the shared compiler messages
and existing related-location transport. New live TLS tests cover all five
changes, generic imported errors, original UTF-16 ranges, hover/rename and unsaved
recovery. Runtime tests cover maps, enum arrays, wide/distinct integer indices,
scope/import shadows, allocation cleanup and cold/cached pure `#run`.

The qualification pass only targets native callable references. Generated
parameter/value references and compile-time context bindings such as a user's
`ODIN_TEST` remain untouched. Expression restoration similarly excludes ordinary
folded literals and real type names; a regression ensures emitted `8` cannot
rewrite a native diagnostic's `i8` type name.

Validation: the final clean run passed all 466 compiler regressions across
diagnostics, ordinary compatibility, typed loops, power/range/membership/extrema,
interpolation ownership, defaults, pure/native compile-time calls and big-number
integration. The final four live TLS tests and all 24 CodeLLDB tests passed,
including user-call argument breakpoints after qualifier insertion. All 1,127
native TLS tests and 119 Node extension tests passed. The full isolated VS Code
suite confirms all five changes through unsaved diagnostics, hover/rename and
related locations. Its comment undo/redo assertions now wait for the actual
asynchronous document update instead of assuming command completion immediately
updates the extension host's document snapshot. The local extension is packaged
and installed; reload VS Code once for the adapter update.

An earlier broad run hit one Odin backend SIGSEGV compiling the extrema demo.
The same example passed direct and regression reruns, and the final clean suite
passed without a backend crash. This remains an observed intermittent backend
issue, not a retry or suppression policy added to Thor. The earlier context-value
qualification regression was fixed and its original regression test passes.


## Odin-style interpolation formatting: 0.30.0

The current format contract is [docs/interpolation-formatting.md](docs/interpolation-formatting.md),
which supersedes the original T07/T13 padding-only grammar above. `<` and `>`
are removed; default alignment is right for every type, with `-` for left and
Thor `^` for center. Quoted fill remains independent of alignment. Odin verbs,
flags, width/precision, grouping of decimal integer portions, and Thor `%`
percentage output are supported. Rational g/G requires explicit precision.

`thorlang/formatting.py` validates literal suffixes and emits static options for
`thor:format.format_spec`. Existing format/format_options/format_plain APIs keep
their signatures. `runtime/format/render.odin` performs conversion, using Odin's
formatter for native/custom values and sized strconv float buffers. Integer
precision avoids the native integer formatter's fixed buffer too. Aggregate
precision is rejected rather than forwarding unsafe sizes recursively into fmt;
format individual fields. `runtime/big/formatting.odin` implements exact decimal
scaling, ties-even division, significant digits, exponent carries, and radix
conversion. Both formatter layers release scratch and return one owned result.
The f/f! lifetime policy and pure-function capability checks are unchanged.

The source map still wraps each expression field, while format punctuation is
nonexecutable metadata. Shared compiler diagnostics remap type/verb contracts to
the original field, including unsaved TLS documents. TLS 0.23.0 and extension
0.18.0 carry the updated grammar: rune fills, numeric widths/precisions, neutral
flags/operators, and blue verbs. No debug adapter translation change is needed.

Regression coverage includes a Decimal-based rounding oracle, native scalar and
aggregate/custom formats, bigint bases, Rational ties/carries/negative zero,
1,000-place runtime/fn/#run output, grouping/prefix padding, Unicode/byte
truncation, nesting, ownership tracking, inactive branches, mapped diagnostics,
unsaved TLS recovery, TextMate bracket/color metadata, actual VS Code providers,
and original-source CodeLLDB breakpoints/step-in. Both evaluation demos include
new formats; the spaced walkthrough explains ownership and exact-number choices.

The broad regression pass also found and fixed an older SDK-overlay diagnostic
initialization gap: native overlay source snapshots now carry the empty
`type_operand_errors` and `sequence_bounds` maps expected by the mapper. The
existing unsaved-native-file/resource regression reproduces and verifies it.

Final verification, including the broad compiler run's investigated failures and
fresh passing reruns, is recorded in
[docs/interpolation-formatting-verification.md](docs/interpolation-formatting-verification.md).
One intermittent stock Odin SIGSEGV repeated the earlier code-generation stack
signature; its anonymized observation is retained in the upstream report bundle.
No automatic build retry, sanitizer bypass, or upstream compiler patch was added.


## Root errors ahead of generated inference cascades: 0.30.1

An unknown name in `(1-p_loss)*100` inside a formatted interpolation could
produce five diagnostics: the undeclared name, invalid generic/type queries,
and a conversion of `100` to an unresolved compiler result type. Their ranges
overlapped, so VS Code could show the generated conversions without showing the
actual cause, depending on the hovered character.

The shared diagnostic mapper now recognizes failed generated type queries,
result-placeholder conversions (including ones mapped to copied source), and
invalid generated operator dispatch. It follows value inference through the
source AST's arithmetic, parentheses, unary operations, and other value nodes.
Known cascade messages are suppressed only when an independent concrete error
exists in an expression they depend on. Call arguments, separate interpolation
fields and statements bound the search. Original user generic/type arguments,
independent argument/type errors, and useful instantiation notes are retained.
If no primary error was reported, the inference diagnostic remains visible.

The original example now has one diagnostic, `Undeclared name: p_loss`, on the
identifier alone. `100` no longer has a false error underline. This changes
presentation only: Odin still checks the same program and unsuccessful checks
still fail. Source generation and debugger mappings are unchanged. TLS 0.23.1
uses the same compiler output for unsaved buffers; no extension reinstall is
needed. Restart TLS or edit/save the document to refresh existing diagnostics.

Tests cover literal and parenthesized operands, unary/arithmetic chains, power,
selected imports, absent members, multiple root errors, imported generics,
inactive/uninstantiated code, and independent native type arguments and
conversion errors. A live TLS regression checks the exact UTF-16 identifier
range, the absence of an error at `100`, and repeated unsaved typo/fix cycles.

Validation: all 154 diagnostics/hygiene/formatting/typed-loop compiler tests,
all nine live diagnostic/typed-loop/f-string TLS tests, and the complete isolated
VS Code integration suite pass. The editor regression verifies one error exactly
on `p_loss`, no error range at `100`, and clearing after changing the reference to
`prob_loss`. Its existing undo/redo test now explicitly focuses the text editor
before issuing focus-routed history commands; no extension runtime change was
needed. A read-only copy of the reported double_bet source reproduces the same
single primary error. The original project files were not modified.


## Dynamic interpolation widths: 0.31.0

Use `f"{value:{width}}"`, with existing literal flags before the nested expression
and grouping/precision/verb after it. Bare identifiers are diagnosed with the
nested-brace syntax. The shared lexer balances the width expression using normal
Thor tokens, including nested literals and interpolation; the parser appends its
AST after the field value. Source byte positions remain unchanged. The suffix
parser substitutes parsed spans rather than re-parsing arbitrary expressions in
Python. Runtime `normalize_width` checks native integer types, nonnegative values,
and overflow before conversion to int. Dynamic precision remains unsupported.

Lowering evaluates the field value and width once, in that order. One discovered
ownership interaction required preserving borrowed Integer/Rational values:
a width function could otherwise replace the original owner and automatically
release the digits held by the staged field header. ASan reproduced that use
after free. Compiler capture/release helpers now snapshot borrowed big scalars
only; owned field results and ordinary native values avoid the extra copy.
Tests include both Integer and Rational replacement through pointer parameters,
with tracking and ASan, plus nested temporary allocations used to compute widths.

Width expressions are ordinary AST children for purity, fn/#run dependencies,
semantic tokens, hover, references, and rename. The source-map tag
`interpolation-width` attaches type errors to the width rather than expanding
them to the enclosing len/call. Native lexer/TLS tests, live TLS tests, grammar
metadata tests, and a CodeLLDB step-in test cover the new syntax. TLS is 0.24.0;
extension 0.19.0 adds embedded-code/bracket scopes for nested format widths.
See [the format reference](docs/interpolation-formatting.md#variable-minimum-widths).

Validation: 209 compiler/formatting/diagnostic tests, 1,128 native TLS tests,
121 Node extension tests, four native tokenizer tests, eight live TLS tests,
26 debugger tests, and the isolated VS Code integration suite pass. Both the
T01–T23 evaluation demo and the commented walkthrough run successfully with
their new variable-width examples. Distinct integer widths are also covered.
The installed local extension is 0.19.0; reload the VS Code window to refresh
its TextMate grammar along with TLS.

## Invalid format-name highlighting: extension 0.19.1

An unsupported bare width such as `f"{value:round_pad}"` remains a compiler
error, but the grammar previously colored individual letters of `round_pad`
as formatting verbs. Format verbs now require a token boundary, and unknown
names are consumed as whole neutral tokens. Valid verbs, numeric widths,
nested width expressions, quoted fills, and following string/code scopes
retain their existing colors. No compiler or TLS protocol change is needed.

Grammar regressions cover quoted/raw strings, identifier suffixes with digits
and Unicode, literal brackets after the field, and the following code line.
The VS Code integration regression keeps the nested-braces diagnostic and
surrounding hover/semantic tokens through an unsaved invalid-to-valid edit.
All 122 Node extension tests and the isolated VS Code integration suite pass.

## Indexed constants retain value colors: TLS 0.24.1

`HOUSE_ODDS :: HOUSE_ODDS_TABLE[GAME]` could inherit the indexed element's type
classification, causing the constant to appear green. TLS's declaration
classifier now recognizes indexed, matrix-indexed and sliced values, including
expressions wrapped in parentheses. Their declaration and reference tokens
retain the new binding's compile-time or runtime value modifiers. Actual type
aliases, including `type_of(...)` and generic record instantiations, retain
their type classification.

Regression coverage checks plain/enum-indexed arrays, nested arrays, strings,
slices, matrix elements/columns, value aliases, mutable and immutable bindings,
actual type aliases, hover, rename, and unsaved value-to-type edits. A read-only
copy of the reported double_bet source confirms both HOUSE_ODDS occurrences use
the compile-time variable token. The native TLS suite and editor integration
also cover the constant modifier used by the purple theme preference.
Validation: all 1,129 native TLS tests, eight targeted live TLS tests, 122 Node
extension tests, and the isolated VS Code integration suite pass. The local
TLS binary is rebuilt; restart the language server to refresh open editors.

## Selective-import member completion: TLS 0.25.0

`import "core:fmt" / ` and `import "core:fmt" / print, ` now offer public package
members with the same fuzzy lookup, visibility and documentation as `fmt.`.
The server recognizes a selected-name context directly from the original token
stream, so an empty selection, a trailing comma, and multiline/commented lists
work before the source parses cleanly. Relative imports, collection imports,
explicit aliases and namespace-free selections use the resolved source package.
Already-selected names on either side of the cursor are omitted. Edits retain
original UTF-16 ranges and insert names alone, without call snippets or imports.

The package-member query is shared with ordinary selector completion. Comma
and space are additional LSP triggers; both return empty results outside the
selected-import context. Space is necessary because VS Code otherwise closes
the slash/comma popup when the usual following space is typed. No client or
grammar update is needed: restarting TLS advertises the new triggers.

Tests cover native and real stdio requests, public/private members, overloaded
groups, documentation, prefix matching, Unicode edit ranges, incomplete lists,
comments, multiline continuation, existing selections, import-path edits,
core/Thor libraries and unrelated code. The VS Code integration test types the
separators and spaces, accepts suggestions from the actual automatic popup,
and filters to a procedure name while checking that no call syntax is inserted.
Validation: all 1,133 native TLS tests, ten live import/regression tests, 122
Node extension tests and the isolated VS Code integration suite pass. The local
TLS 0.25.0 executable is rebuilt; use **Thor: Restart Language Server** to load
the new completion triggers.

## Streaming interpolation storage: Thor 0.32.0

Interpolation now writes fields directly into a stack-backed output buffer,
with checked heap growth for larger output. Native strings can be borrowed
during formatting; native scalar formatting, grouping and padding avoid owned
intermediate field strings. Complex and big-number conversion fallbacks retain
their existing algorithms. No syntax or ownership change is involved.

`runtime/format/buffer.odin` supplies 512 bytes of scratch and a private allocator
that grows without replaying a custom formatter. A live buffer has pointers to
its own storage, so initialize it in place and never copy it. The result remains
an exact-sized clone: strings do not carry builder capacity, and sized custom
allocators need correct sizes on deletion. Real allocation failures are checked
even when Odin's formatter ignores writer errors; unsupported resize modes still
allow Odin's allocator fallback. Borrowed views that alias a destination are
protected before growing it. The final clone forwards the Thor caller location
so leak reports continue to identify the original interpolation.

The lowering keeps the same field source-map wrappers and evaluation/cleanup
order, replacing per-field `format_spec` results with `write_spec` calls.
The public owned `format_spec` API is unchanged. TLS and the editor extension
require no protocol or grammar changes.

Validation: 359 broader compiler/formatting/lifetime/pure/big-number/diagnostic
tests pass, as do the dedicated nine buffer regression tests, five native runtime
tests with ASan and memory tracking, 45 debugger/memory-policy tests, eight live
TLS tests, 122 Node extension tests, and the isolated VS Code integration suite.
Both evaluation demos run in optimized and debug modes. The benchmark tool
compares identical owned results against Odin `aprintf`, outside terminal I/O,
and measures allocations separately. See
[performance details and measured results](docs/fstring-performance.md).

One overlapping walkthrough invocation returned status 1 without a retained
failure diagnostic, although its log contained the completed assertions. A
subsequent normal invocation passed; a rebuild into isolated output then passed
ten consecutive debug/ASan runs with status 0 and no memory reports. No
reproducible formatter failure was found.

## Assertion-disabled formatting: Thor 0.32.1

The `double_bet` executable built with `tbss` crashed because that alias includes
`-disable-assert`. The 0.32.0 formatter had placed some required buffer writes,
reserves and resizes inside `assert(...)`; Odin removes the expressions entirely
under that flag. This was a Thor regression, reproducible with the unchanged
user source, rather than an optimizer or Odin compiler failure.

Buffer operations now use ordinary conditionals with explicit failure panics.
Allocation-result and formatter-writer failure checks remain active even with
assertions disabled. The remaining runtime assertions have no required side
effects; the rest of the Thor runtime and generated-code templates were also
reviewed for this pattern.

A behavioral regression first failed under three flag configurations and now
checks actual output under `-disable-assert`, `-o:speed -disable-assert`, and the
full `tbss` flags (`-o:speed -microarch:native -no-bounds-check -no-type-assert
-disable-assert`). It covers literal writes, native numbers, percent/signs,
enum precision, dynamic widths, nested f-strings, big-number conversion, heap
growth and balanced allocation counts. Its checks use ordinary conditionals
and Python output comparisons so disabling assertions cannot disable the test.
Both final-result and custom-formatter allocation-failure tests also run with
assertions disabled. Future runtime optimizations must keep required operations
outside assertions and include the assertion-disabled release configuration.

An unchanged temporary copy of the reported `double_bet` source reproduces the
crash before the fix and passes five consecutive runs after rebuilding with the
exact alias flags. Both evaluation demos also pass under those flags. Native
formatter tests pass with ASan and memory tracking both with and without
assertions; all 45 debugger/memory-policy tests and eight live TLS tests pass.
The benchmark retains one tracked allocation for each common short case and
two for the large-string case, with matching frees and zero live allocations.
The full affected compiler regression run passes all 362 tests, including the
ten buffer tests and the new assertion-disabled output/allocation-failure cases.

## Assertion side-effect audit and maintenance rule

Audit all implementation assertions across the Python compiler, native frontend,
runtime/basic, TLS and editor tooling after the 0.32.1 formatter fix. No further
required side effects were found in implementation assertion predicates or
messages. Two editor test helpers had confirmation-file writes inside assertions;
these now execute first and only assert on the error result. Hoist counter-updating
demo expressions and random sampling before their result checks as well.

`AGENTS.md` records the repository rule. Six new assertion-hygiene tests inspect
native ASTs and Python assertions, require review of native predicate callees,
cover multiline/nested/message operations and contextless assertions, execute
the editor fixtures with assertions disabled, compare regular/unchecked frontend
ASTs and errors, and exercise the compiler itself under Python `-O`.

All 1,133 native TLS tests pass with assertions/bounds/type checks disabled, as do
the four native tokenizer tests and both actual VS Code run/debug integration
suites. See [the audit scope, findings and guard limitations](docs/assertion-audit.md).

## Native conditional compilation only: Thor 0.33.0 / TLS 0.26.0

Remove the redundant `#if` and `#else` language aliases. The shared parser no
longer rewrites hash-prefixed keywords into native conditional tokens, nor
accepts `else if` as a continuation of `when`. Both Thor and Odin sources use
`when` / `else when` / `else` with Odin's normal behavior. Runtime `if` / `else if`
and the Thor `#switch` / `#run` directives retain their existing behavior.

Convert the evaluation demo, commented walkthrough, fixtures, compiler tests,
TLS/rename/debugger tests and language references to native conditional syntax.
Keep T04 as coverage of native compile-time branching, declaration visibility,
configuration, laziness and interaction with Thor expressions. Rejection tests
cover removed spellings at package/procedure scope, whitespace, inactive arms,
original Unicode-aware diagnostic positions and live unsaved correction. Plain
string/comment contents remain data.

The editor 0.20.0 grammar removes conditional-directive state and the special
color for `if` after a hash-prefixed else. Its conditional snippet now inserts
`when`. Native conditional keywords use ordinary control-flow scopes. Rebuild
TLS and install the updated VSIX, then reload VS Code to refresh both layers.

Validation: 200 core/compiler/default/diagnostic tests and 380 cross-feature
tests pass, along with the extended native-conditional rejection cases, six
assertion-hygiene tests, 149 live TLS/rename tests, 26 real debugger tests, 1,134
native TLS tests, 123 Node grammar/extension tests and the isolated VS Code
integration suite. Both demos pass checked runs and full unchecked release
runs, including `-disable-assert`. The packaged and locally installed extension
is verified as 0.20.0 and the rebuilt TLS executable reports 0.26.0.

## Library-only graph copying

Add `basic.deepclone`, `basic.deepfree` and `basic.Deep_Error`, backed by ordinary
Odin procedures in `runtime/graph`. The Thor/Odin compilers, TLS implementation,
syntax and implicit ownership rules are unchanged. A clone returns a heap root
and an error; freeing is explicit, with no persistent allocation registry.

Validate recursive schemas before copying. Discover supported reachable objects
iteratively, coalesce overlapping ordinary spans, rebuild maps with cloned keys,
then relocate descriptors/pointers after all map insertions. This preserves
cycles, shared objects, buffer/field aliases and pointers into map value cells.
Use unaligned-safe descriptor loads/stores for packed records. Dynamic capacity
is retained, unused slots zeroed, and only active tagged-union payloads traversed;
single-pointer union representations are handled without reading a nonexistent
tag. Sets and Integer/Rational work through their regular runtime layouts.

Discover the whole graph before deepfree releases anything. Check allocation
failures in traversal bookkeeping as well as destinations; roll back partial
clones, use exact free sizes, and reject known resource/unbounded schemas.
Map-cell references used as map keys and ambiguous empty views between disjoint
extents return Unsupported_Reference. Document caller obligations around retaining
owners/extents, shared mutations, allocator lifetime and borrowed storage in
[the graph contract](docs/deepclone.md). Add a separate commented runnable demo.

Validation covers 20 native tests in checked ASan and unchecked optimized ASan
builds, including 5,000 nodes, 300 mutually linked map cells, aliasing, strings,
slices, packed/aligned records, unions, zero-sized aggregates, custom sized
allocators, allocation-failure sweeps and retryable failed-free discovery. Six
Thor integration tests cover imports, big numbers/sets, original-source errors,
the commented demo and checked/unchecked tracking. Live TLS verifies hover,
completion, navigation and unsaved diagnostic recovery; real CodeLLDB verifies
original-source breakpoints/stepping across both calls. Assertion hygiene passes.

The wider basic/installation regression run has four existing failures because
the working tree's pre-existing edits removed asc, desc and ordered. Those edits
are preserved separately from this feature; their old tests/demo still reference
the removed names.

## Remove basic ordering predicates

Confirm the removal of basic.asc, basic.desc and basic.ordered. Delete their
API-specific tests and documentation, and remove their uses from the bundled
demos. Keep unrelated import, native-consumer, big-number and project-local
purity-adaptation coverage by using the remaining basic helpers. Standalone TLS
fixtures that declare their own comparison procedures still test generic loop
classification and do not depend on the removed library API.

All 58 affected basic/import/installation/big-integration/assertion-hygiene tests
pass, resolving the four baseline failures recorded above. The basic, imports
and big-integration demos also pass optimized unchecked builds with allocation
tracking. The full commented language walkthrough passes a checked debug run.

## Partial expressions and exhaustive enum switches: Thor 0.33.1 / TLS 0.26.1

Accept #partial before runtime switch expressions in the shared parser. Keep the
complete directive/switch source span and carry the partial flag into lowering;
native #partial switch statements and enumerated-array literals remain unchanged.

Allow runtime enum expressions without a default when Odin verifies coverage of
all enum values. Native checking retains grouped/range cases, aliases, imported
and generic enum types, duplicates and operand errors. Non-enum selectors,
compile-time #switch expressions and #partial expressions still need a default.
A partial value switch cannot silently leave a result uninitialized.

Centralize native switch emission across scalar results, multiple results,
assignment cancellation and temporary ownership selection. Keep exhaustive enum
checking separate from a mandatory panic on unmatched enum casts; adding a
synthetic default would incorrectly hide missing members. Assignment/ownership
paths track whether an arm ran before their continuation. Generated panics use
the contextless form where necessary, and native-name hygiene preserves shadowed
user panic/type_of procedures. Scalar global switch helpers now use the same
contextless convention as staged global multi-result initializers.

Add focused tests for all four reported Othello variants, laziness/selector
counts, inferred/contextual strings and records, generic/imported/sparse enums,
multiple/global results, global initialization order, skip assignments, owned
f-strings/sets/big numbers, pure/#run evaluation, native partial syntax and
unmatched-cast failures with assertions/bounds/type checks disabled. Stdio TLS
tests verify original tokens, hover, enum navigation, rename excluding #partial,
Unicode-aware missing-case/default errors and unsaved recovery. Real CodeLLDB
checks original-line stepping and locals across both switch forms.

Validation: all 124 focused switch/parser/TLS/assertion-hygiene tests pass, as do
326 broader compiler/diagnostic/ownership regressions, 1,134 native TLS tests,
123 editor tests and two real debugger tests. Both evaluation demos pass checked
debug runs. All four variants also build in isolated copies of the full Othello
project; the original external file is unchanged. The checked and unchecked
frontend AST comparison includes both updated demos. Rebuild TLS and restart
Thor: Restart Language Server to load the new parser; no client/grammar update
is needed. Preserve the user's one-line basic.swap definition as requested.

## Source-located I/O purity errors: Thor 0.33.2

Reproduce the Hilo SIGBUS with an unused fn that calls fmt.print/println. The
purity adapter was following the printing implementation and generating checked
copies of its variadic, callback, reflection and OS dependencies instead of
rejecting the observable I/O at the call. Native checking/code generation could
crash before returning a source diagnostic.

Recognize the canonical fmt output procedures and basic print/input/read/write
by library identity, including selected imports, aliases and transitive calls.
Emit purity errors at the original callee span and name the declared fn when
available. Stop traversing those implementations in both checked call graphs
and compile-time evaluation closures. Preserve generic overload candidates until
native instantiation; an I/O candidate gets a lazy failing checked body, so a
different eligible overload is still usable. Keep inactive when arms and
unevaluated type queries lazy. Ordinary procs retain printing behavior; unrelated
user procedures and custom collections named print/basic are not rejected by
spelling alone. In-memory formatting retains its existing purity rules.

The CLI's existing mapped JSON errors supply TLS with precise UTF-16 ranges;
no TLS/client changes are needed. Live tests cover unsaved fn-to-proc fixes,
restoring errors, transitive alias locations and generic branch activation.
Run `Thor: Restart Language Server` to clear an existing stale crash report.
Unclassified native backend crashes still use the honest locationless fallback;
this change prevents the invalid I/O call chain from reaching that backend path.

Validation: 318 compiler, native-evaluation, diagnostic, basic-library, live TLS
and assertion-hygiene tests, plus two real CodeLLDB tests. Rejection and valid
formatting/ordinary printing are checked in debug and optimized unchecked builds,
including -disable-assert. The unchanged external Hilo source reports its two
fmt.print calls and fmt.println at lines 78, 80 and 83. An isolated full-project
copy builds with -debug after changing only print_board from fn to proc; no
external project source was edited.

## Pure contracts and compile-time diagnostic hardening: Thor 0.33.3

Audit declared fn bodies, transitively checked ordinary procedures, automatic
folding and explicit #run together. The shared rejection inventory exercises
54 source patterns through runtime fn checking, automatic evaluation and #run,
with matching ordinary-procedure controls. Stdio TLS tests apply each violation
and its correction to unsaved buffers and verify original UTF-16 ranges.
The detailed contract and remaining conservative boundaries are documented in
[docs/purity-diagnostics.md](docs/purity-diagnostics.md).

Fix named/reordered copy arguments, nested borrowed-element propagation and
parenthesized builtin/library aliases. Keep effectful defaults syntactically
valid and reject them through lazy body contracts, avoiding false missing-argument
and invalid-type errors. Reject bodyless fn declarations explicitly. Distinguish
foreign calls, indirect runtime calls and ineligible intrinsics from genuine
native undeclared-name errors. Preserve inactive when branches, native optional
status/allocator-error results, ordinary proc effects, type queries, valid panic
bodies, delete location arguments and owned or_return results.

Give checked expression helpers separate identities from ordinary helpers; an
unchecked cached interpolation/switch/index body must not bypass transitive
purity contracts. Map formatting errors to the original interpolation field.
Use per-type source markers that survive truncated native instantiation stacks,
including zero-width typeid frontend nodes. Standard-library effect errors point
to the user's call and retain the library location as related information.
Use nominal raw-union identities where visible instead of rejecting unrelated
records merely because their field names match. Reject unsupported C/UTF-16
string representations and report the bounded recursive schema check clearly.

A real ASan-confirmed ownership error involved Integer/Rational member headers
in shallow aggregate copies: updating such a member could free the caller's
backing allocation. Keep these member aliases read-only under purity checking.
Direct numeric copies remain independent, including fields and local aliases
whose exact type is supplied by an unconstrained generic. Preserve ordinary
scalar records and borrowed slice provenance. Avoid cloning already-owned
Rational component results twice; doing so leaked their first allocations.
Type-dependent ownership checks preserve valid independent numeric cleanup while
rejecting borrowed user fields named numerator/denominator. Hide numeric backing
storage from checked generic code without reserving those names in user records.

Execution failures remain distinct from purity violations. Check explicit #run
result arity before generating a helper. Report compiler/executable launch
failures, bounded compile/run timeouts, native signals and malformed serialized
results at the evaluation request. Panics/assertions/bounds errors retain an
original failing-function location when the native runtime supplies one; forward
sequence-check caller locations and preserve related notes through the outer CLI
adapter into TLS. Silent native traps identify the signal and evaluation site,
without inventing an inner cause or source location. Changed dependencies cannot
reuse successful compile-time results to hide a new purity error.

An earlier apparently intermittent independent-error test was reproducible under
repetition: Odin's default error budget was exhausted by repeated helper errors
before checking another indexed assignment. The native counter advances before
deduplication, and checker ordering determines which source errors are visited.
Reserve 1,000 native errors for structured final/helper checks, preserve explicit
user limits and raw-diagnostic mode, then apply the existing source-level cleanup.
This adds no compilation pass and changes no successful program behavior. Real
CLI and TLS tests retain twelve independent undeclared indexing errors and clear
them after unsaved fixes. The original flaky case passes 40 consecutive repeats.
The larger budget exposed additional f-string ownership/type-query cascades;
classify only generated query/condition failures and suppress them only when
the same value operation contains a real primary error. Preserve independent
arguments, interpolation fields, statements and genuine native conditions.

No editor grammar/client change is required: TLS uses the shared compiler and
source maps. Rebuild TLS and use Thor: Restart Language Server to replace stale
crash/error reports. Keep inaccessible/raw schemas conservative, bound recursive
validation at 24 levels, and do not infer resource ownership from ordinary
integer values. Assertions remain read-only checks; release validation uses
explicit panic conditions or Python verdicts with native assertions disabled.

Validation: 542 Python compiler, live TLS and assertion-hygiene tests across the
46-test hardening inventory, 299 broader purity/evaluation/diagnostic tests,
130 expression/basic/typed-loop interactions and 67 big-number regressions.
The final ownership/purity rerun also passes all 179 tests in its overlapping
scope. The 54-pattern unchecked release matrix passes separately. Native checks
pass 1,134 TLS tests, six pure-runtime tests and one serialization-runtime test.
Five real CodeLLDB cases cover checked proc frames/locals, allocated fn bodies,
user calls inside generated expressions, exhaustive/partial switches and numeric
property breakpoints. Both bundled demos pass: checked debug and optimized
unchecked release with allocation tracking. Hilo now reports its three I/O calls
at original lines 78, 80 and 83 instead of a backend crash. External project
sources remain unchanged.

After the final native-budget/cascade correction, all 143 diagnostic, evaluation
and assertion-hygiene tests and all 29 purity/live-TLS tests pass again. The
forty-run diagnostic repeat and the twelve-error CLI/TLS cases also pass.

## Confined mutation in pure call graphs (Thor 0.34.0)

An ordinary proc called from fn may write through its inputs when the affected
storage belongs to the enclosing pure invocation. This applies recursively to
Thor and inspected Odin procedures, named/default arguments, overloads and
generic helpers. Public fn inputs remain transitively read-only. A fresh scalar,
array or allocated buffer may be passed to a mutator; copying an input slice
header or placing it in a local record does not grant ownership of its elements.
Reference loops and reference type-switch bindings follow the selected storage's
permission. Odin's type-switch tag is a binding, not a write to its alias.

`pureborrow.py` supplies static symbolic field/element/pointee permissions and
reference-store summaries. Checked ordinary variants accept zero-sized type
metadata describing the actual arguments. Native compile-time guards retain
generic/when laziness; no runtime ownership table or reference count is added.
Profile fields distinguish overload targets. Calls outside checked paths retain
their original signatures and behavior. Type reflection, hover, rename, backend
overload messages and debugger frames expose the source declarations.

Reference stores through output parameters and shallow `copy` update caller provenance. A helper
cannot redirect a local header to borrowed input and then hide a later write,
or export its own stack address/view or slice literal through another helper. Caller-local pointer
redirection remains valid. Mutating helpers share the enclosing scratch allocator
so buffers they allocate through output parameters outlive the helper call.
Supported returned values still receive independent promotion and visible results
retain caller-managed cleanup. This distinction is exercised with allocation
tracking, ASan debug builds and unchecked release builds.

Internal typed pointers support addressing and nil checks, while pointer identity
comparisons, membership/switch identity tests, numeric address conversions and
pointer/aggregate bit casts remain ineligible. Integer/Rational implementation
storage remains opaque through typed/generic helper pointers. Public pointer-bearing
inputs/results, unsupported containers and recursive reference schemas retain
their existing restrictions. Provenance joins across branches, reassignments and
array elements are deliberately conservative; this is not a general borrow checker.

The regression pass also preserves nested ordinary #run entries, approved library
helpers, native enum shorthand and original procedure type reflection. Updated old
reference-binding rejection fixtures now reject actual borrowed writes and accept
mutation of fresh storage. New acceptance/editor tests live in
`test_pure_mutation.py` and `test_pure_mutation_tls.py`; both demos and the dedicated
`examples/pure_calls` walkthrough demonstrate the rule. Real CodeLLDB coverage
stops inside a mutator and observes the updated local in its caller afterward.

Validation across focused regression runs: 497 distinct Python compiler, live TLS
and assertion-hygiene tests, including 29 mutation tests and the shared effect/type
inventory; 1,134 native TLS tests; six allocation-tracked pure-runtime tests and
one serialization-runtime test. Four real CodeLLDB checks cover mutator writes,
caller locals, checked procedure frames, allocated fn frames and stepping through
generated expressions. The main demo and commented walkthrough pass checked debug
and optimized unchecked release runs with allocation tracking. Output-buffer and
numeric-mutator acceptance cases also run with assertions, bounds checks and type
assertions disabled, using explicit panic/Python verdicts. Slice-literal escapes
are covered with explicit, elided, temporary-array, nested-record, parenthesized
and named-argument forms, along with shallow-copy escapes and safe owned copies.

## Imported builtin result highlighting (TLS 0.26.2)

An inferred `guess_dist :> math.abs(diff)` had no semantic token or hover because
OLS lost builtin identity through the reexport and attempted to resolve its
placeholder result type `T` as an ordinary declaration. Preserve builtin identity
through reexports/renamed aliases, and resolve result-dependent arguments in the
caller's lexical scope, including casts and nested calls. This restores
the actual result type and lets the existing immutable-binding rules apply; it
does not infer mutability from the initializer. Ordinary user functions with the
same name remain ordinary functions. Live editor tests cover fn/loop scope,
declarations/references, hover, selective imports, chained aliases, int/f64 results,
mutable controls, unsaved binding changes and mapped invalid-write diagnostics.

Validation: all 122 live TLS tests, 1,134 native TLS tests in both checked and
optimized unchecked/assertion-disabled builds, and six assertion-hygiene tests
pass. A read-only check of the actual Hilo document confirms the three `:>`
occurrences are readonly and the three `:=` occurrences remain mutable. Rebuild
TLS and use Thor: Restart Language Server; no grammar/extension reinstall is needed.

## Binding inference and resilient immutable colors (TLS 0.26.3)

Hilo's `drawn_card :> pop(&deck)` exposed a second inference gap: fixed-capacity
dynamic arrays lost their capacity when converted back to an AST type, and the
generic resolver did not match or substitute their element/capacity parameters.
Preserve `[dynamic; N]E` throughout conversion, generic calls, result types and
generic record fields. Overload matching must check both capacity and element
type. Instantiation now retains the original procedure's calling convention,
builtin/pure identity, attributes and other metadata. Explicit `base:runtime`
imports can find their actual `@builtin` declarations in the shared index,
without exposing unrelated compiler builtins such as `len` as runtime exports.

Semantic highlighting no longer requires successful type inference to describe
a known value binding. The file resolver records lexical binding flags for
unresolved local/global values, parameters and iteration variables. Unresolved
loop containers receive unknown-type lexical entries before element inference;
concrete results replace them when available. This preserves `:>`/parameter/value
iteration colors, `&` iteration mutability, scope/shadowing and constant colors
while code is incomplete. These entries belong only to the semantic-token map;
they do not fabricate a hover type, select an overload, resolve an undeclared
name or suppress compiler diagnostics.

The related builtin audit covers fixed-capacity `raw_data`, numeric constructors,
component extraction and conjugates. Complex/quaternion construction no longer
renames an operand's shared type AST. Cast/nested arguments and real-valued
magnitudes preserve f16/f32/f64 precision through aliases and repeated requests.

`tests/test_binding_inference_tls.py` checks real LSP requests, checked compiler
fixtures, builtin/native/imported/aliased generic calls, tuple results, mutable
controls, all numeric precisions, unknown initializers/types, scoped shadowing,
unsaved recovery and source-mapped invalid writes. Native regressions live in
`tls/tests/fixed_capacity_inference_test.odin`. A read-only audit of the actual
Hilo document verifies all 11 named immutable declarations and all `drawn_card`
references; its hover reports `int // immutable storage`.

Validation: 133 live editor/purity-interaction tests, 1,140 native TLS tests in
both checked and optimized assertion-/bounds-/type-check-disabled builds, and
six assertion-hygiene tests pass. The server is rebuilt locally as TLS 0.26.3;
use Thor: Restart Language Server to load it. No extension or grammar reinstall
is required.

## Purity call-chain diagnostics and local fixed-capacity helpers (Thor 0.34.1)

`simulate_game::fn` calling `reset_shuffled(&deck)` with a local fixed-capacity
array exposed a false type-contract error: internal typed pointers were allowed,
but validation then rejected their `[dynamic; N]E` pointee. Internal helper
validation now handles that container recursively. Public fn input/result
restrictions remain in place, and the existing provenance rules still protect
borrowed elements inside locally created headers.

`PureCalls.chains` retains actual caller edges. `purediagnostics.py` combines
those edges with native instantiation locations to report transitive purity
failures at the responsible call inside the declared fn. Related notes keep
the underlying operation, intermediate calls, fn signature and any inspected
standard-library implementation. Ordinary proc uses and inactive generic paths
remain valid. The same adapter handles CLI JSON/text and TLS unsaved overlays,
including no-argument functions and explicit/automatic compile-time evaluation.
Cascade filtering keeps the original cause position after moving the primary
underline, so failed generated index/type inference does not obscure the reason.

The supporting mutation audit tracks borrowed element provenance through
`append` and the pop-family operations. Native `..` spreads, Thor array/record
`**` spreads, generic record spreads and named variadic arguments retain that
provenance. Writing through a popped borrowed slice remains forbidden. Exporting
a helper's stack view through an appended output element is rejected; an allocated
output element retains its enclosing fn scratch lifetime. Checked and unchecked
release fixtures validate results with explicit conditionals rather than relying
on enabled assertions.

New coverage is in `test_pure_call_context.py` and
`test_pure_call_context_tls.py`; earlier transitive diagnostic tests now require
both the fn call location and the preserved cause note. A copy of the complete
Hilo program reports exactly one ambient-generator error at
`reset_shuffled(&deck)`. Removing the shuffle, or making `simulate_game` an
ordinary proc, checks successfully. The user's original Hilo file is not edited.

Validation: 264 compiler, evaluation, diagnostic and assertion-hygiene tests;
151 live editor tests; and six native pure-runtime tests pass. Runtime coverage
includes checked debug builds and optimized builds with assertions, bounds
checks and type assertions disabled. The actual Hilo document is also checked
through TLS using temporary compiler output/cache directories, preserving the
user's source and project files. No TLS binary or extension update is required;
save the file or use Thor: Restart Language Server to refresh diagnostics.

## Nonreturning switch result arms (Thor 0.35.0, TLS 0.27.0)

Switch expressions now accept any call with a `-> !` procedure signature,
replacing the earlier builtin-panic-only rule. The frontend exports the parser's
`diverging` flag; the resolver follows signatures through aliases, imports,
overloads, typed callbacks, fields and anonymous procedures. Standard-library
signatures are inspected on demand without changing their purity eligibility.
Builtin identity still respects lexical shadowing. Ordinary void procedures
remain invalid value arms, including procedures named `panic` or `unreachable`.

Nonreturning arms contribute neither a value nor result arity/type evidence.
The scalar, multi-result, assignment-skip and ownership paths emit them as calls
rather than fabricated return values. A switch with only nonreturning arms needs
expected result types. Compile-time switches keep inactive arms unchecked.
Unconstrained callbacks and conditional `when` signatures use native compile-time
result-count selection; a zero-result specialization is additionally checked
inside a `-> !` helper, so a void callback cannot masquerade as a nonreturning
one. Queries do not execute user calls. Invalid void specializations get an
original-source call diagnostic rather than a generated-helper error.

TLS uses `SymbolProcedureValue.diverging` when selecting switch result types.
The live editor regressions cover hover types, overloads, aliases, imports,
callbacks, Unicode source ranges and unsaved signature/error recovery. Compiler
coverage includes runtime and compile-time selection, purity checks, ownership,
multiple results, shadowing, conditional signatures and checked/unchecked builds.
A real CodeLLDB test verifies original Thor breakpoints, stepping and locals.
The original Othello program checks successfully without changing its source.

Validation: 300 distinct compiler, switch, evaluation, diagnostic and assertion
hygiene tests; 128 live editor tests; and 1,140 native TLS tests in both checked
and optimized assertion-/bounds-/type-check-disabled configurations pass. Two
CodeLLDB regressions cover ordinary and generic nonreturning arms. Both updated
demos also run in optimized unchecked builds. Restart the language server to
load TLS 0.27.0; no extension reinstall is needed.

## Owned dynamic pure results (Thor 0.36.0)

`loadPoints::fn()->[dynamic]Pos` was blocked by the pure result-type restriction,
even though its local append loop already satisfied purity. Contextful checked
results now accept dynamic arrays recursively, and internal checked helpers may
receive locally owned dynamic headers/pointers. Public fn dynamic-array inputs
still require slice views; pointer/resource elements and contextless allocated
returns retain their existing restrictions and source-mapped diagnostics.

Result promotion treats a dynamic header as owned storage. It preserves length,
capacity, nil backing and live elements, recursively copies nested owned values,
and installs the caller allocator instead of letting scratch metadata escape.
`pure.destroy` follows a dynamic header's stored allocator for recursive cleanup.
Empty/nil and zero-sized elements remain supported. Allocation/descriptor guards
use ordinary conditionals and remain active with assertions disabled.

The contents-only evaluation protocol adds dynamic sequence storage and capacity;
runtime materialization reconstructs fresh buffers directly, including on cache
hits. Such results require runtime bindings (:=), while pure scalar consumers
can still yield constants. Generic, named/multiple, deferred and nested results,
borrowed element protection, strings, big numbers and later appends are covered
by `test_pure_dynamic_results.py`. The old rejected dynamic-result inventory case
now checks an unsupported dynamic array of pointers instead.

Validation: 339 distinct compiler/evaluation/cache/purity/diagnostic/hygiene tests,
12 live editor tests, ten native pure/evaluation tests in both checked and
optimized assertion-/bounds-/type-check-disabled modes, and a real CodeLLDB test
are verified. Initial fixture errors (slicing a native constant and expecting a
typedef spelling in hover) were corrected and rerun. The guided demo runs in
unchecked release mode. The actual circles_on_dots source checks and builds in
debug and unchecked release modes without edits or launching its GUI.

One existing intrinsic regression hit an intermittent stock Odin SIGSEGV. Its
leading faulting frames match the previously captured code-generation crash;
the isolated retry and three further repeats passed. Details are retained in
`upstream/odin-codegen-race/evidence/recurrence-2026-11-04.txt`. No Odin compiler
changes or TLS engine changes were needed; saving/rechecking refreshes the new
compiler diagnostics.

## Selected-import usage follows declarations (TLS 0.27.1)

`to_radians` in a selected `core:math` import could be dimmed despite a source
call. Unused-import analysis had depended on successful result/overload type
inference and the inferred symbol's package. That also missed re-exported aliases
whose underlying procedure belongs to a different package.

Selected names now use the existing scoped reference resolver and compare the
imported declaration's URI/range identity. This retains uses with unresolved
generic arguments or invalid calls, while excluding local/parameter shadows,
qualified member access, named argument keys and record fields. Namespace and
wildcard handling keeps its existing rules. Indexed literal keys are resolved
as expressions, preserving map/array key references and import usage. If a
generic literal's type is unresolved, unused-import analysis conservatively
retains a possible key reference; that fallback does not affect rename edits.
The extra reference pass runs only for selected imports and sizes its temporary
map from the existing semantic map instead of reserving 10,000 entries.

Coverage is in `test_selected_import_usage_tls.py` and
`tls/tests/thor_import_usage_test.odin`, including unsaved edits, re-exports,
overloads, generic/invalid calls, initializer scope, namespace shadowing and keys.
A read-only check of the actual circles_on_dots document confirms `to_radians`
receives no unused diagnostic. No source or extension changes are needed;
restart the language server to load TLS 0.27.1.

Validation: all 126 stdio editor tests and 1,143 native TLS tests pass. The native
suite also passes with assertions, bounds checks and type assertions disabled;
all six assertion-hygiene tests pass.

## Constant sequence concatenation (Thor 0.37.0, TLS 0.28.0)

`++` concatenates compile-time fixed arrays or constant slices, with additive
precedence and left associativity. The shared parser reuses the previously
unsupported increment token only in Thor mode; the lexer permits continuation
after `++`. Ordinary Odin parsing, array arithmetic, string `+`, and unsupported
increment/compound-concatenation syntax retain their existing behavior.

Chains, including grouped chains, lower to one native `intrinsics.concatenate`
call. Operand mappings retain original diagnostic locations. Element-only hints
give new/empty bracket literals context without forcing the result length or
container kind onto an operand. Typed nested compound literals preserve native
constant eligibility where casts would turn arrays into runtime values. This
also fixes nested inferred constant arrays when basic/big ownership support is
enabled. Large literals are not repeated in each operand's element-type query.
No runtime concatenation routine or evaluation cache is added.

The original operand ASTs remain available to TLS. Result shapes include summed
array lengths and established operand element types, with normal source-based
enum completion, semantic tokens, references and rename. Nested queries retain
the resolver's recursion guard so cyclic/incomplete constant edits recover.
Backend errors name `++` and the offending source operand; literal type errors
take precedence over dependent constant-eligibility failures.

`runtime/basic/basic.thor` no longer exports `CONCAT`. The README and both main
demos use `++`. The operator intentionally follows the intrinsic's existing
constant-value boundaries, including positional entries and native constant
types; runtime array arithmetic and aggregates of allocated logical big numbers
are not silently evaluated. Pure functions can return concatenated constant
slices through their usual independent-storage promotion.

Coverage lives in `test_concatenation.py`, `test_concatenation_tls.py`, the native
TLS/lexer tests and the CodeLLDB source-breakpoint regression. Node grammar tests
cover `++` in ordinary code and interpolation fields.

A broad editor run exposed a tooling rebuild race: the linker temporarily
removed `tls/bin/tls` while another client tried to start it. `build_tls.py`
now builds in a private directory and atomically replaces the published server
only on success. `test_build_tls.py` verifies availability during a deliberately
paused build and preservation of the old executable after a failed build.
The broader compiler run also found an old inline-default callback test still
expecting purity's former generic error text. It now checks the existing precise
indirect-call diagnostic and the callback-parameter context; the rejection rule
has not changed.

Validation: 25 concatenation compiler cases, all 179 editor regressions, 126
diagnostic/hygiene cases, and the real CodeLLDB breakpoint/step test pass. The
428-case compiler regression run passed except for that stale callback-message
assertion; the corrected default-callback suite and atomic-builder test then
passed all 16 cases. All 1,146 native TLS tests and five lexer tests pass in both
checked and assertion/bounds/type-check-disabled modes. All 125 Node tests pass.
Both main demos run successfully in unchecked optimized builds; checked and
allocation-tracked walkthrough coverage is included in the compiler regressions.
The common runtime was prepared and the installed local extension grammar was
updated. Reload the VS Code window to load TLS 0.28.0 and the new grammar.

## Optional-result panic operator (Thor 0.38.0 / TLS 0.29.0)

`or_panic` is a Thor-only postfix operator. The shared lexer reuses the native
postfix AST while preserving the original token text; the frontend exports a
distinct `OrPanic` node for lowering, type/provenance analysis, and ownership.
Native Odin files keep `or_panic` available as an ordinary identifier.

Expression-local helpers evaluate the operand once, save all successful values
and the final status, and return those values after a boolean/nil status check.
SDK signatures are inspected on demand to preserve multiple successful results
such as `os.pipe` and `strconv.parse_f64_prefix`. Native optional promotion and
overload selection remain authoritative through unevaluated type queries. The
`#load` case retains native constant handling. Direct logical numeric results
keep Thor's established independent-ownership rule; ordinary aggregates remain
shallow. F-string lifetimes and short-circuiting use the existing expression
machinery.

The internal `runtime/optional` package allocates/formats only on failure and
invokes the normal/contextless panic machinery with explicitly embedded original
source coordinates. Release builds preserve those coordinates even though
Odin's default handler ordinarily omits its location prefix with disabled
assertions. Compile-time failures retain the expression/procedure/error details
and a related location for the failing unwrap, including Unix position style
and unsaved TLS overlays. Generated-query cascades and native `or_else` token
names are translated back to the source operation. Parser diagnostics likewise
retain `or_panic`, rather than the reused AST token's `or_return` spelling.

TLS supports ordinary/immutable and multiple-result inference, semantic tokens,
source references and rename, keyword hover and completion, and compile-time
error details. Thor-only help is kept out of the native keyword table so native
Odin identifiers retain their full hover/type behavior. The VS Code grammar and
both executable demos document the new operator. The common runtime and local
TLS executable are rebuilt; reload the VS Code window for the installed grammar.

Validation includes 24 operator compiler cases, 95 diagnostic regressions,
29 pure-mutation and 21 f-string lifetime cases, 20 TLS integration cases,
1,150 native TLS cases in checked/unchecked configurations, six lexer cases
checked/unchecked, 127 Node cases, assertion hygiene, and a real CodeLLDB
breakpoint/step/result-inspection check. Other compiler regressions covering
purity, switch panic arms, big numbers, and concatenation also pass. Both main
demos run in optimized builds with assertions, bounds, and type checks disabled.

## Scoped arena library helper (Thor 0.38.1)

`basic.scoped_arena()` returns a growing `core:mem/virtual` arena allocator.
Odin's native `deferred_out` callback destroys it at the end of the calling
scope; assigning it to `context.allocator` uses ordinary scoped context
restoration. Its management record has a stable address, comes from the saved
parent allocator, and is freed with its original size after releasing the arena
blocks. A nil allocation result and initialization errors panic explicitly,
including with assertions disabled. Nested arenas, explicit allocator arguments,
and `free_all` resets retain the same ownership rules. The helper does not change
the temporary allocator or manage non-memory resources.

The implementation exposed a lowering interaction: moving a native deferred
callback into an expression helper shortens its lifetime. Calls to known
procedures with deferred attributes now receive a mapped diagnostic in those
helpers, asking for a direct binding in the owning scope. Native constant-index
lowering, unevaluated type queries, nested user procedure scopes, and inactive
`when` branches retain their normal behavior. Ownership inference also leaves
callback-managed results in the caller scope rather than wrapping explicitly
discarded opaque results in an unnecessary temporary scope. There is no new
language syntax or general escape analysis; allocations must still remain
within their owning scope.

The main README, basic-library reference, and all three relevant demos include
examples and the allocation lifetime contract. Seventeen compiler/runtime test
cases cover management/block cleanup order, sized parent frees, nil allocation,
context replacement/restoration, nesting, early returns/breaks/continues,
explicit and immutable bindings, multiple factories, resets, imports/aliases,
native Odin consumers, and interaction with strings, sets and logical numbers.
Runtime fixtures run checked and in optimized builds with assertions, bounds
and type checks disabled; a debug/ASan case reports no outstanding allocations.
Two live TLS cases verify hover, completion, definition, mapped diagnostics and
recovery after an unsaved edit. All 19 cases and 95 related library, lifetime,
optional-result, switch and assertion-hygiene regressions pass. Both main demos
and the basic-library demo pass in unchecked optimized builds; the basic demo
also passes with debug allocation tracking and ASan. The common installed
runtime has been rebuilt.

## Active-file run/debug directories (extension 0.20.1)

Cmd+R now selects the directory containing the active saved source file before
running `thor run .`, including nested projects in a larger workspace. A live
terminal is reused. When shell integration already reports that directory, the
extension sends only the run command. Otherwise a shell condition checks the
current directory before changing it; a failed change prevents running the
wrong package. Terminal creation starts directly in the selected directory.
No task discovery, compiler-setting lookup, shell-integration polling or new
compiler work is added to normal run dispatch. POSIX, fish, PowerShell and cmd
receive their appropriate directory syntax; paths cmd cannot represent safely
use a fresh terminal's cwd option instead of interpolating them into a command.

Cmd+Shift+R remains the CodeLLDB shortcut. Existing project-root configurations
keep their custom build task/settings. Nested source directories and roots with
no launch configuration get a provided `thor-debug-build` task, with build cwd,
executable, source-map manifest and launch cwd all rooted in the selected source
directory. The prelaunch task is retained for restart. The existing workspace
requirement for debugging is unchanged, while normal standalone-file running
still works without a workspace. Failed saves and untrusted workspaces do not
launch commands or debug builds.

All 139 Node tests and six assertion-hygiene tests pass. Shell regressions execute
the POSIX command in real shells with spaces, apostrophes and command-looking
path text, check that an already-correct cwd skips `cd`, and ensure a failed cd
does not run Thor. Live VS Code tests verify a wrong-directory terminal, switching
between standalone source directories, terminal reuse, unsaved edits and spaced
paths. CodeLLDB integration verifies both the original project and a nested
package, including mapped breakpoints, stepping, watches and restart/rebuild.
Normal run dispatch measured 33 ms initially and 1 ms on reuse in that test,
excluding compilation and execution.

The quoted-path integration probe also confirmed a separate existing Odin
restriction: `-out` validation rejects apostrophes in output paths. The shortcut
correctly changes into such a directory, but plain `thor run .` has the same
compiler rejection there. That compiler limitation is unchanged by this client
update. Real compiler integration uses spaced paths; shell quoting is tested
independently. Reinstall the VSIX and reload the window to load the client code;
restarting TLS alone does not reload VS Code command handlers.

## Runtime value hashing (Thor 0.38.2)

`thor:basic` exports `hash`, `try_hash`, `Hash_Algorithm` and `Hash_Error` from the
native `thor:hash` package. The default is XXH3_64, with XXH64/FNV64a and explicit
seeds available. Strings and ordinary byte sequences use one-shot hashing;
composite values stream field/element encodings into stack-held state. The
implementation allocates no heap or temporary-allocator storage and borrows its
inputs. Record padding, spare capacity, allocator metadata and inactive union
storage are excluded. Numeric endianness and signed floating zero are normalized;
Integer/Rational values ignore their ownership/storage details. Pointer/type-ID
hashes retain process-local identity.

Both schema and value traversal are bounded at 128 levels. Active-path address
and type pairs detect container cycles while allowing shared acyclic storage;
pointers are not followed. Known resource wrappers, maps/sets, raw unions and SOA
are rejected. Sparse enum arrays skip unused cells. Packed fields are loaded
through byte copies rather than unaligned typed dereferences. Failure discards
the partial hash: `try_hash` returns an error enum and zero, while `hash` panics
with a field/index path and type. This retains native `#caller_location`
behavior; runtime panic filenames can still name generated Odin source. Compiler
and TLS diagnostics retain their existing source mapping. The library does not
extend `fn`/`#run` eligibility or promise a versioned composite persistence format.

Tests uncovered two defects in the installed Odin XXH3 streaming implementation:
the short-input finalizer reverses its seed test, and large updates preserve the
wrong stripe for short tails. Bounded updates avoid the latter path, and short
finalization uses the standard buffered one-shot function. No Odin fork or new
hash algorithm implementation is introduced. Tests compare 1,125 combinations
of algorithms, seeds, lengths and chunk sizes against one-shot backends, plus
26 independent XXH3 vectors from the C xxHash 0.8.3 reference implementation.

Hashing literal arrays also exposed an existing contextual-typing problem.
An `any` target now boxes the inferred whole array instead of supplying `any`
as its element type. Explicit `[N]any` contexts still permit heterogeneous
elements. An unconstrained `$T` parameter likewise supplies no contextual
element type for the argument from which T is inferred; omitting its circular
type witness also removes an undeclared-helper diagnostic. The fix applies to
all callers rather than introducing a hash-specific lowering rule. Tests cover
calls, named arguments, procedure variables, generic wrappers, nested literals,
single evaluation, empty/heterogeneous rejection and explicit arrays of any.

Validation: all 18 Python/TLS/contextual-array acceptance cases pass. All 13 native
hash cases pass with Odin's allocation checker in checked and assertion/bounds/
type-check-disabled release builds, and with debug/AddressSanitizer. The broader
runs pass 93 compiler/assertion-hygiene cases and 68 related basic, arena and
logical-number regressions. TLS coverage checks API hover/type inference,
completion/definition and an unsaved diagnostic repair. The basic demo passes
with automatic debug tracking/ASan; both main walkthroughs pass optimized with
assertions, bounds and type checks disabled. References and all three demos
document the new API and limits. The common installed runtime is rebuilt.

## Owned string conversions (Thor 0.38.3)

`basic._string(value, allocator=...)` returns `(string, runtime.Allocator_Error)`
with `#optional_allocator_error`. Scalar numbers, Integer/Rational, bools and
enums retain plain interpolation output. Runes and rune sequences are UTF-8
encoded; strings/cstrings and byte sequences are copied into exact-sized owned
storage. Arrays, slices, dynamic arrays and fixed-capacity dynamic arrays are
supported for bytes/runes. Empty text needs no allocation. Unsupported values
produce a public support-contract diagnostic at the call, including in TLS.

The library implementation is `thor:format.try_string`; the native float writer
now has a fallible internal entry point shared with interpolation. No syntax,
automatic cleanup or purity exception is added. Inputs remain borrowed and all
work uses the requested allocator. Intermediates are freed on failure, with
empty output and the original allocator error returned. The allocator adapter
also detects `core:mem.nil_allocator`'s silent `(nil, nil)` allocation failures;
unsupported resize can still use Odin's ordinary allocate/copy/free fallback.
Big-number errors preserve their underlying allocation cause. A size that cannot
be represented maps to Out_Of_Memory. Invalid rune scalars normalize to U+FFFD
before encoding, avoiding the installed Odin encoder's stale-width bug for
negative/above-range code points. UTF-16 inputs remain unsupported explicitly.

All 11 conversion/TLS acceptance cases pass, including primitive/endian/distinct
types, exact logical-number round trips, char containers, aliases/immutables,
selected/wildcard imports, anonymous f-string inputs, supplied allocators,
scoped arenas, optional-error propagation, long enum names, custom formatter
growth, mapped unsupported-type errors and unsaved repair. The 10 native
formatter tests pass checked, assertion/bounds/type-check-disabled release and
ASan. Failure injection covers every allocation stage of large Integer/Rational
text, all allocator error codes, silent nil allocators, resize fallback and
cleanup without partial results. Related runs pass 130 basic/diagnostic/hygiene
cases and 53 formatting/lifetime/hygiene cases. The documented basic demo runs
cleanly under debug tracking/ASan and unchecked release; the shared runtime is
rebuilt. See runtime/basic/README.md for ownership and custom-formatter limits.

## Collection comprehensions (Thor 0.39.0)

Implemented the agreed single-for, optional-if forms: inferred runtime slices,
typed slices/dynamic arrays/sets/maps, compile-time fixed arrays, and one-level
`..` unpacking for every supported container. Duplicate set members collapse;
map duplicates keep the last value in iteration order. Nested comprehensions and
multiple for/filter clauses produce direct grammar diagnostics. Runtime results
retain manual ownership and existing shallow :> semantics.

The shared parser represents a comprehension using a tagged native procedure /
range scope while preserving every original identifier and expression span.
The compiler treats that procedure as transparent, captures enclosing locals,
and emits one native loop with checked construction primitives. Output bindings
are in scope before their textual for declaration; source expressions retain
outer lookup, including `for x in x`. Helpers are declared outside the synthetic
AST blocks. Typed range/index bindings, enum indices, reference iteration,
contextual literals, distinct destination types and generic specializations
preserve native types. Package runtime initializers use Odin's default context;
allocator switches still require the original allocator when freeing global
slice/string storage.

Required constant construction uses existing fn eligibility and project-local
result caching. The filtered length becomes a native fixed-array type; empty
constants retain their element type. Generated construction does not weaken fn
rules: borrowed element storage remains borrowed and anonymous map/set
construction cannot bypass the existing local-storage restrictions. Existing
serialization boundaries on owned nested slices and logical-number aggregates
remain explicit. Multiple-result iterator procedures are rejected with a source
contract diagnostic; collection/range sources are evaluated once.

Allocation failure is an ordinary panic path, including unchecked builds.
Native map assignment ignores growth errors, so construction uses map_entry's
explicit error result. Slice/dynamic/map/set builders release outer buffers
before failure. Allocator adapters normalize silent nil allocation refusals and
are removed from returned headers, avoiding stack-backed callback state.
Manually owned element values keep ordinary shallow ownership, including when a
map duplicate overwrites a value. Fresh f-string elements remain alive. Testing
also found and fixed Rational constructor arguments rendered before capture
aliases were installed; constructors now render again in their actual helper
scope.

TLS resolves the same range scopes for hover, completion, readonly highlighting,
definition and rename, including output-before-binding and shadowed sources.
Explicit/contextual enum outputs receive element context. Simple constant
extents are folded with bounded static arithmetic; other constant extents show
`?` instead of running user code in the server. Synthetic signature tokens are
hidden; incomplete edits and repair do not retain stale scope/tokens. Compiler
errors, unsaved TLS checks and CodeLLDB break/step locations retain Thor spans.
No extension reinstall or new grammar keywords are needed; restart TLS to load
the rebuilt server.

Validation includes 35 comprehension compiler cases, seven real-stdio TLS
cases, a real CodeLLDB breakpoint/step/variable test, and the assertion-hygiene
suite. Coverage includes side-effect order, filtering before output/unpacking,
empty/zero-sized values, duplicate keys, enum/map/string/set iteration, distinct
and generic types, pure and impure calls, shallow borrowed elements, logical
number ownership, anonymous owners and all construction failure stages. Failure
injection verifies zero live construction bytes before panic in checked and
assertion/bounds/type-check-disabled builds, including silent nil allocators.
Related runs exercised the compiler/purity/loop/lifetime regressions, 30 TLS
regressions, logical-number language/operator tests and all 139 editor Node tests.
The dedicated commented demo, the main walkthrough and the original task demo
are exercised with debug tracking/ASan and unchecked release flags. References
are docs/comprehensions.md and examples/comprehensions/demo.thor; common runtime
output and TLS are rebuilt.

## Optional-value comprehension outputs (Thor 0.39.1)

`[my_proc(a) or_else 2 for a in values]` now preserves its value/status pair
until native `or_else` handles it. The type-only comprehension query previously
replaced every resolved call with its first result, prematurely dropping that
status. A contextless, unevaluated pair witness now retains both types when a
call is actually the operand of an optional unwrap, including through grouping.
Fallback expressions remain single-value contexts. Runtime calls and fallbacks
keep native exact-once/lazy behavior; no tuple elements or additional iterator
bindings are introduced.

Nested power/switch/optional helpers also receive distinct type-query identities.
Without that separation, a helper body built from zero-value type witnesses
could be reused for the real runtime expression, losing calls and their effects.
Package-level query helpers have a contextless entry and an internal default
context for type checking; they are never executed. Query state is restored even
if binding inference raises. TLS now resolves an or_else call to the specialized
value result rather than exposing its entire procedure signature, while keeping
native builtin/load/map-lookup handling.

Validation passes 43 comprehension compiler cases plus six assertion-hygiene
cases, 15 comprehension/or_panic TLS cases, 59 related optional/logical-number
cases, and a CodeLLDB breakpoint/step/variable test with optional output nested
inside power. New checks cover bool, nil-error-union and pointer-error status,
generics, optional flags, grouped calls, lazy side-effectful fallbacks, filters,
typed destinations, map keys/lookups, flattening, library parsing, pure/#run and
constant construction, nested expression helpers, and mapped invalid fallbacks
with unsaved editor recovery. Checked/debug/ASan and assertion/bounds/type-check-
disabled builds pass; the commented demo and specification include the example.
Shared runtime output and the local TLS server are rebuilt.


## Read-only slice views and per-name access contracts (Thor 0.40.0)

The accepted contract is in [docs/slice-access.md](docs/slice-access.md). `:>`
now protects slice element access, including nested slices, sliced views and
returned aliases. Existing writable aliases still observe and modify the same
backing allocation. Inline value copies and pointer targets retain their existing
behavior. This does not add a runtime type, ownership tracker or ABI parameter.

`thorlang/sliceaccess.py` infers per-parameter requirements separately from purity.
It follows aliases, returned values, helper calls, named and spread arguments,
container stores, loops, conditional declarations and concrete generic shapes.
Writable escapes and unknown/opaque callees are conservative. Explicit `#mut`
belongs only to the immediately following name and is erased by the shared parser.
Reviewed core/basic APIs have source-identity-checked contracts.

TLS consumes compiler metadata tied to the exact UTF-8 source hash. It shows an
inferred grey `#mut ` before each affected name, preserves explicit annotations
in hover/signature help, refreshes hints and semantic tokens after accepted
checks, and colors protected element fields read-only. JSON-RPC replies to the
server's refresh requests are consumed without treating them as editor requests.
Source diagnostics include the argument/operation, view origin and mutation chain.
Original source maps continue to drive CodeLLDB breakpoints and stepping.

The audit also repaired an existing conditional `#run` entry-target gap: all
retained conditional alternatives now keep the external entry contract. Pure
record schema field-count expressions are balanced, avoiding a deterministic
Odin checker stack overflow after SDK inspection expands the schema inventory.
A stale nested-f-string lifetime expectation was updated to reflect the existing
streaming formatter's elimination of the inner allocation. Evaluation helper
access checks examine their retained closure and do not schedule new evaluations
while inspecting native source facts.

Coverage includes the compiler matrix in `test_slice_access.py`, live LSP cases
in `test_slice_access_tls.py`, VS Code inlay/hover/error/refresh checks, a real
CodeLLDB source breakpoint and step test, native parser/server suites, and the
commented `examples/slice_access/demo.thor`. Checked debug and assertion-disabled,
unchecked optimized builds exercise allocation ownership with verdicts that stay
enabled. The broader regression suite checks the interactions with comprehensions,
f-strings, purity, `#run`, generics, native `when`, negative indexing and imports.


Validation for this batch: the full Python matrix ran 1,664 cases, including
CodeLLDB breakpoint/step tests. Four cases encountered native Odin SIGSEGV or
SIGTRAP failures; each passed independently with a temporary `-thread-count:1`
compiler wrapper, without changing Thor's compiler flags or installed Odin.
The new feature matrix was rerun after the final analysis fixes. Native TLS's
1,150 cases pass in checked and unchecked/assertion-disabled configurations;
all 140 Node extension tests pass. Both large demos pass, and the slice-access
demo passes with debug tracking and optimized unchecked flags without leaks.

`python3 tools/test_vscode.py --scope slice-access` passes the actual VS Code
inlay, hover, original-source diagnostic, related-location and unsaved-edit
checks. The full UI harness remains limited by its existing simulated typing
step for selective-import suggestions: the typing command leaves the document
unchanged in this environment. Programmatic completion and all live LSP import
coverage pass. This is recorded separately from the focused editor result.
The background typing limitation is resolved in the 0.40.1 test harness below;
real keyboard/widget simulation remains an explicit foreground option.
TLS was rebuilt and the local VSIX was packaged/installed as version 0.21.0.


The final reference-escape audit also covers mutating map-literal key expressions,
reference-bearing map/Set keys, nested references forwarded by helpers, mutable
callback reassignment, overridden default callbacks, and opaque returned slices.
Unknown callback/foreign slice results require a read-only binding and an explicit
clone before writable use; fixed immutable aliases of inspectable procedures
retain their exact contract. The editable basic library's own view declarations
are checked even when the containing helper is not called.


Final acceptance after the callback/reference audit: all 109 focused compiler,
live TLS, assertion-hygiene and basic-library cases pass. Another 88 default
callback, comprehension, graph-clone and pure-call interaction cases pass. The
focused VS Code suite passes with TLS refresh responses enabled. No upstream
compiler binary or ordinary Thor build flag defaults were changed.

## General hardening (Thor 0.40.1)

Review all changes since the previous general purity audit (`d012351`, 0.33.3),
including confined local mutation, dynamic pure results, nonreturning switches,
selected-import inference, concatenation, `or_panic`, arenas, run/debug directories,
hashing, string conversions, comprehensions and read-only slice access. The
[hardening report](docs/hardening-2026-11.md) maps each feature to its public
contract and test coverage, and records the remaining conservative boundaries.

Fix access-analysis traversal through runtime switch selectors/case matches,
if initializers and map-comprehension keys. Apply the existing map/Set key
reference boundary to comprehensions. Propagate each multiple result through
parentheses, forwarding, optional unwrapping, sole-call argument expansion and
opaque callbacks. Keep lookup/assertion Boolean statuses and allocation/helper
error statuses independent from the first result's slice permissions. Retain
trusted runtime allocator-error enum facts, including namespace/type aliases,
without opening the compiler runtime as general SDK source or trusting unrelated
same-named user types. Statically selected `when`/`#switch` arms supply their own
provenance; unresolved native conditions retain conservative joins without
scheduling new compile-time evaluation.

Anchor access-check tokens to the actual original operation before diagnostic
deduplication. Generated type-query/runtime repetitions now produce one precise
argument/write underline. Live TLS checks verify UTF-16 positions after Unicode,
related mutation notes, status hovers and recovery without saving. Real CodeLLDB
cases step into map-comprehension key calls and parenthesized optional tuples,
then verify original source lines and scalar locals.

Move required editor edits/run commands out of assertion predicates and extend
the maintenance guard. Repair the full editor test's background-window typing
dependency: its default completion path uses the real client provider and edit
ranges, while `--interactive-typing` retains an optional foreground widget test.
The full VS Code suite now completes, covering comment-toggle bracket repair,
rename, inlays, completion, hovers, unsaved errors and source mapping. No editor
product change or extension reinstall is needed for these harness changes.

The new checked/unchecked runtime integration combines confined pure mutation,
independent dynamic `#run` results, `++`, constant/runtime comprehensions,
read-only views, optional scalar statuses, arenas, hash/string conversions,
big numbers and f-string formatting. It checks output and tracker/ASan reports
with assertions disabled as well as in checked debug. Extend the slice-access
demo with multiple-result views, status copies and inactive-arm behavior.

Validation: both large demos and the extended slice-access demo pass checked
`-debug` and optimized `-disable-assert -no-bounds-check -no-type-assert` builds
with allocation tracking and no tracker/ASan reports. The slice demo also passes
with the ordinary installed Odin invocation. Native TLS passes all 1,150 tests
in each of checked and unchecked configurations; all 140 Node tests pass. The
full isolated VS Code suite and end-to-end VS Code run/debug suite pass. The
Finder launch integration, normally opt-in, passes separately.

The complete Python matrix ran all 1,696 discovered cases in three groups
(526, 593 and 577), including 39 CodeLLDB/DAP cases. The 526- and 577-case groups
passed cleanly. The 593-case group found the SDK-overlay metadata regression
described below and skipped the opt-in Finder check. After the correction, all
60 compilation, diagnostic-hardening and slice-access live-TLS cases passed on
rerun, including that overlay test. Finder passed separately. There are no
retained failing or expected-failure cases from this audit.

Both full matrix runs used a temporary Odin wrapper adding `-thread-count:1`
to native compiler invocations, isolating the previously documented upstream
parallel-codegen race. The installed compiler and Thor's ordinary flag defaults
were not changed. The original 1,675-case baseline also passed (one opt-in skip).
This batch adds 21 test methods, with additional accepted/rejected subcases.

The full regression run caught a compatibility error in this new diagnostic
anchoring: native SDK overlay source records intentionally omit Thor-only
blocked-expression tables. Use an empty table for those metadata-only records;
the existing unsaved SDK/source-resource integration test exercises that path.

## Unused local warnings (Thor 0.40.2)

Relax Odin's default error for a declaration that is the sole statement in an
inner control-flow block. `thorlang/backend.py` recognizes the native diagnostic
and verifies its original local identifier and emitted declaration. Add an inert
second statement to the emitted block and retry native checking. Initializers
and deferred cleanup retain their execution and scope. Explicit vet modes use
Odin's native policy. Successful native checks return after one invocation.

Reuse this path for compile-time helper compilation. Keep logical helper cache
keys based on initial emission so diagnostic recovery does not appear to change
resource inputs.

`SourceMap.rewrite` retains copied byte ranges and adjusts expansion/debug regions
for emitted changes, including wrapping a native `do` body. Update generated
hashes and managed output together with the maps. TLS receives warning severity
and the original identifier range. Regressions cover inner blocks, grouped names,
initializer/deferred work, independent errors, warning controls, source positions,
imported Odin source, compile-time calls, unsaved TLS fixes and debugger stepping.
Runtime verdicts use explicit panic checks and Python comparisons, including in
optimized assertion-disabled builds.

Validation passes 143 compilation/edited-build/diagnostic/assertion-hygiene cases,
13 dedicated compiler/TLS cases and the focused CodeLLDB regression. Checked and
unchecked smoke builds also pass with the ordinary installed Odin invocation.

## Opt-in Thor vetting (Thor 0.41.0, client 0.22.0)

Add `thor vet`, build/check/run/test/publish `--vet[=immutable,pure]`, and explicit
`thor vet --apply` / `--ask` correction modes. Findings are warnings and support
`-warnings-as-errors`, `-ignore-warnings`, mapped JSON and original source ranges.
Plain vet reports findings. Ask mode previews corrections and reads one answer,
with empty input/EOF declining. Apply and ask are mutually exclusive and operate
on saved files. Native Odin vet flags continue to select the native policy.

`thorlang/vet.py` finds initialized local-binding and concrete named-procedure
candidates. Reuse resolved bindings and the existing purity source analysis to
exclude known mutations, mutable addresses, ABI attributes and purity violations.
Validate candidate batches through the normal compiler using temporary overlays
and subdivide rejected batches while preserving already accepted edits. Existing
slice contracts, transitive pure helpers, type checks, compile-time execution and
active build flags supply the final validation. Source text is edited at AST
separator/keyword byte spans, preserving surrounding bytes. Source snapshots are
verified before staged replacements, and completed writes roll back on failure.

The initial scope favors proven conversions. Conditional declarations,
uninitialized locals, unresolved generic procedures, attributed procedures and
main are excluded. Reference-bearing/owned results require manual conversion:
pure result promotion can change aliases and allocation ownership. Vetting uses
the active target and flags. Additional analysis is opt-in.

TLS preserves vet diagnostic codes and verified edit metadata. Snapshot hashes
invalidate stale quick fixes after unsaved edits. Heap-owned diagnostic strings
are cloned/deleted with the diagnostic store; text-edit slices use allocated
storage. Thor quick fixes bypass the existing native import-organizer restriction
without enabling native organizer edits for Thor imports. VS Code exposes the
resource-scoped, default-off `tls.vet` setting through checker arguments.

Acceptance covers report/apply/ask and EOF, rule selection and strict warnings,
checked/unchecked runtime behavior, grouped/typed declarations, comments/Unicode/
CRLF, mutations and aliases, transitive purity and confined local mutation,
conservative ownership/ABI/generic/conditional boundaries, large accepted batches,
invalid inputs, concurrent edits and rollback, live TLS positions and edits,
stale-fix rejection and debugger stepping after corrections.

Validation passes the 130-case diagnostic/acceptance group and the 149-case
compiler/edited-build/evaluation/slice-access group. The focused CodeLLDB test
passes with corrected immutable locals and a corrected fn. All 1,150 native TLS
tests pass in both checked and assertion-disabled configurations, and all 141
Node tests pass. The local TLS server was rebuilt, and client 0.22.0 was packaged
and installed. Native regression runs use the temporary single-worker wrapper
from the previous audits. Runtime fixtures also exercise optimized assertion-disabled
code, and live vet quick fixes pass with an assertion-disabled TLS server.
The ordinary installed Odin invocation also passes vet/apply/run smoke checks
in debug and optimized assertion-disabled configurations.

## Early code-action crash (Thor/TLS 0.41.1)

The 0.41.0 vet quick-fix handler assumed that the compiler diagnostic map already
contained the requested document. VS Code also requests code actions immediately
on opening or selecting code, including with vetting disabled. Iterating the
missing map value dereferenced a null entry and terminated TLS. All 19 recent
macOS crash reports from the affected editor session stopped in this handler;
the subsequent symbol, token and hover failures followed the server exit.

Look up the diagnostic entry under its existing mutex and return when absent
before reading its array or hashing the source. Keep the same locked iteration
for populated and empty entries. Live regressions request actions before compiler
results, verify subsequent symbols/tokens, and cover a completed empty check
followed by an unsaved edit that produces a valid correction. The VS Code test
requests code actions immediately after opening its first Thor document, then
verifies symbols, hover and semantic tokens.

Validation passes the focused TLS/assertion checks, the actual Dijkstra file's
compiler check and repeated action/symbol/token requests, all four live quick-fix
cases with assertions disabled, and the full isolated VS Code suite. Rebuild the
local server with its ordinary checked configuration after release validation.
An editor that exhausted its five automatic restart attempts needs the Thor
Restart Language Server command or a window reload to reconnect.

## Vector component access through immutable bindings (Thor/TLS 0.41.2)

The circles-on-dots project exposed two missing source facts in the slice-access
analysis. Array components such as `.x` and `.y` were modeled as unknown record
fields, and names exposed by a namespace `using rl` were unresolved. Freezing an
unknown result conservatively created a slice permission; converting its numeric
component to `int` then falsely looked like erasing that permission.

Resolve namespace members to their original exported symbols and preserve their
package identities for reviewed reader contracts, without granting those contracts
to same-named local helpers. Model Odin array components and swizzles using their
element types and shared element provenance. Numeric copies are independent;
component writes through read-only slices and nested slice aliases still require
writable permission. Native checking retains responsibility for selector validity
and exact nominal types. These are source-analysis changes, with no new runtime
storage or lowering machinery.

Regressions cover qualified and namespace-imported Raylib results, both component
alphabets, swizzle copies/writes, generic readers, nested slices, transitive
namespace reader/writer/returned-view contracts, reviewed readers and local
shadowing. The live TLS regression checks inferred hints, source locations and
unsaved error recovery. Numeric fixtures run checked and with assertions, bounds
checks and type assertions disabled. The original project passes check and builds
in debug and optimized unchecked configurations without source edits.

Validation passes 112 slice-access/TLS/hardening cases, 40 using/context/power
cases, and the final 66-case focused/purity/native-evaluation/assertion group.
The final group includes all new regressions after the namespace-contract fix.

## Project build audit and scalar/formatter access facts (Thor/TLS 0.41.3)

Build all 19 Thor projects under Code/Thor in default and debug configurations,
redirecting every artifact to temporary storage. The initial sweep found a false
`hilo` access error and timeouts in `15_puzzle` and `sketch`. Preserve all result
types on the reference-free call fast path, including optional-result unwrapping;
previously a later numeric result became unknown and an immutable binding gained
spurious slice permission. Resolve qualified primitive names in the genuine
base:builtin namespace so the core:c aliases used by Raylib remain numeric.

The native fmt buffer formatters now use reviewed argument contracts: buffer
writes require writable access, formatting arguments are read-only readers, and
the result is a string. Use the normal argument binder for named and expanded
arguments. Avoid traversing the format engine's large recursive reference graph
without relaxing destination checks. There are no runtime representation changes.

All 18 valid projects now build in default and debug modes; sandbox's remaining
failure is an undeclared `x` after its declaration was commented out. No project
sources were changed by the audit. All 213 poker tests pass. New native/compiler
and live TLS regressions cover these cases, destination hints and unsaved recovery;
buffer runtime fixtures also check optimized assertion-disabled code.
The 167-case compiler/TLS/native/assertion group passes 166 cases; correct an
explicit numeric cast in the remaining new fixture and rerun all four new
compiler cases successfully. All new live TLS cases pass in the full group.
The circles-on-dots project still has a separate performance concern: an isolated
warm build succeeds but takes 47.26 seconds. Record this unresolved timing issue
in the audit without claiming that all compile-time overhead is resolved.
See [the complete project audit](docs/project-build-audit-2026-11-07.md).

## Copy boundaries in slice access analysis (Thor/TLS 0.41.4)

Detach inline value-copy paths from their caller storage while preserving nested
borrowed references. Model native conditional numeric aliases, union narrowing,
defaulted/generic parameters, per-result builtin and procedure copies, and
column-specific SoA access. Distinguish array and slice overloads. Preserve alias
relationships across summary results and separate detached result snapshots from
later local-header rebindings. Recognize independent local-header replacement
without erasing previous aliases or potentially skipped/deferred writes. Compare
final permission graphs when those replacements require non-monotone local facts.

Keep inferred TLS `#mut` tied to actual slice-storage access: inline generic
element copies and writes through existing pointer targets do not imply a slice
write. Normal package discovery/native SDK inspection exclude test-only sources,
preventing irrelevant diagnostics and expensive test-graph traversal in builds.
No runtime representation, ownership or source-line changes are introduced.

The final compiler/TLS acceptance group passes 149 tests, including 111 compiler
programs in the new copy matrix and four new live TLS cases. The final native,
compatibility, runtime and assertion-hygiene group passes 110 tests; an additional
86-case purity/conditional/power/using interaction run passes. Runtime fixtures
use checked debug and optimized assertion-disabled unchecked configurations.
All 19 current Code/Thor projects build in normal and debug modes with unchanged
source bytes. See [the coverage and remaining boundaries](docs/slice-copy-hardening-2026-11-07.md).

## Reject impossible vet purity candidates before graph inference (Thor/TLS 0.41.5)

Poker vetting spent minutes preparing transitive borrowing/store graphs for
procedures whose signatures already made conversion to `fn` impossible. Reject
definitely unsupported public parameter/result shapes, including nested fields
and inferred defaults, before calling the expensive purity analysis. Reuse
purity's direct-I/O recognition as another early rejection. Unknown types still
go through full checking; eligible callers can still mutate locally created
storage through pointer-input ordinary helpers.

Keep original-program and proposed-edit validation, batching, diagnostic maps,
TLS quick fixes and explicit apply/ask behavior. The current poker source now
vets in 5.35–5.46 seconds in the recorded local sequence, versus the reported
24 minutes 40 seconds, with the same 15 warnings. The adjacent build took
2.49 seconds. Ordinary builds without vet are unaffected. Add regressions that
check which candidates reach graph inference rather than relying on fragile
wall-clock limits. See [the profiling and validation report](docs/vet-performance-2026-11-08.md).

Validation passes 28 vet/TLS/assertion-hygiene tests and 47 purity/slice-copy
interaction tests. The additional full poker test run times out before emission;
an untouched-source control reaches the same slow recursive slice inference
stage. Record that separate performance observation in the report without
claiming a completed poker-suite run.

## Restore full-test slice-inference performance (Thor/TLS 0.41.6)

Replace the global recursive-summary replay loop with dependency-driven
propagation. Local header replacement can set transient change flags while
leaving the completed graph identical; compare final graphs before rescheduling
their callers. Preserve a conservative requirement at the iteration bound.
Separate ordinary body checking from on-demand symbolic parameter-contract
inference. Editors still request all contracts for unused-declaration hints.

Reuse resolved symbol/type facts within fixed source-analysis phases, invalidate
on source discovery and bypass active generic bindings and unresolved queries.
Memoize static lexical scopes and import directories with collection identity.
This is in-memory analysis reuse, not a persistent project/build-output cache.
Generated program behavior, source mappings and default test flags are unchanged.

Poker now completes all 213 tests in 6.98–7.85 seconds with default checks, and
also passes assertion-disabled unchecked and speed-optimized runs. All 387 tests
in the three compiler/TLS/native/interaction groups pass. Add seven dedicated
performance/correctness regressions, including cached/uncached and eager/on-demand
diagnostic comparisons, recursive aliases and unused-body checks. Four affected
real projects build without source edits. See [the complete report](docs/test-performance-2026-11-08.md).


## Compile-time execution policy (2026-11-21)

Add `--comptime=required|normal|aggressive|override` to the maintained Rust CLI.
Normal remains the default and retains the existing scheduler's early exits.
Required suppresses optional execution while retaining explicit requests,
constant/type contexts, native branch conditions and constant parameter defaults.
Aggressive considers ordinary calls; override additionally removes `#runtime`
barriers, including in transitive helper bodies. Vet validation and live TLS
checks inherit the selected policy. VS Code client 0.22.2 adds `tls.comptime`.
The frozen Python compiler remains unchanged.

Ordinary automatic results must be self-contained fixed-size values. Verify
purity and allocation freedom before optional execution so that borrowed result
aliases, cleanup responsibilities and ordinary custom-allocator effects remain
unchanged. A source-level helper rejection can retain an optional ordinary call
at runtime, but execution failures/timeouts remain compilation errors. Explicit
`#run` and declared `fn` retain their existing scratch/materialization contracts.

Reuse proofs by target, type/constant specialization, argument shape and captured
lexical bindings. Refresh result-type classifications across generic contexts;
a local record's nominal identity need not describe its captured field types.
Request, prepared and scalar cache identities include the policy. Scalar batching
also accepts eligible ordinary calls, with the existing bounds and failure
isolation. No new runtime wrappers, library ABI or native compiler changes.

Validation covers 32 native mode tests, five live TLS tests, 428 existing
regression cases plus one Windows-only skip, 98 Rust unit tests, 151 editor tests,
1,169 native TLS tests in both checked and unchecked builds, all Cargo targets,
and seven assertion-hygiene checks. The direct Options initializers in the Rust
harnesses were updated; all 42 scheduling cases pass after that correction.

Seven alternating warm transpile samples against pre-change `c4de374` show
49/49 ms (plain), 142/143 ms (features), and 494/491 ms (poker_pmap).
Generated Odin matches after output-path normalization, including explicit
normal versus the default. Normal performs zero ordinary-policy verifications.
See [the raw validation/timing record](docs/validation/comptime-modes-2026-11-21.json),
[the contract](docs/comptime-modes.md), and
[the executable walkthrough](examples/comptime_modes/demo.thor).
