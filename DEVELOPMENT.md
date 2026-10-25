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

T01–T20 now have implementations, editor integration and evaluation sections in
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

The 0.5.0 batch adds declared `fn` contracts (T18), typed contents-only native
serialization (T19), and explicit `#run` helpers (T20). It pulls forward the
necessary T22 scratch lifetime support and takes the authorized result fallback:
string/slice inputs and internal allocation work, but allocation-bearing returns
are rejected recursively. T21 persistent caching and T23 automatic folding and
specialization scheduling remain future work. Helper build/run failures use the
same diagnostic maps as final Odin checking; execution failures point to #run.

`thorlang/purity.py` tracks effects and transitive borrowed provenance. Native
generic contracts check instantiated types and preserve inactive `when` behavior.
`thorlang/evaluation.py` prunes helper dependencies, verifies ordinary procedure
graphs, preserves overlays/configuration, schedules selected explicit calls, and
bounds native execution. `thorlang/values.py` validates the versioned native value
protocol and reconstructs typed constants. `runtime/pure` owns scratch lifetimes;
`runtime/eval` serializes contents without padding or process addresses.

The verified batch passes 335 Python compiler/LSP tests, 1,102 native TLS tests,
54 grammar tests, allocation-tracked runtime/lexer checks, the actual VS Code
integration test and the demo in all three define modes. The local TLS executable
and Thor 0.5.0 VSIX are rebuilt and installed. Generated binaries remain ignored;
their source and build/test commands are version-controlled.

The compiler's resolution boundaries, conservative record schemas, raw-union
rejection, generic `using` pointer case and specialization-dependent nested #run
are documented in `README.md`; keep those limits visible while extending the
resolver. Do not substitute a zero result for an unresolved active #run. Unknown
native dependencies must not introduce unverified initializers into helpers.
