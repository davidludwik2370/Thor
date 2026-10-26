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
