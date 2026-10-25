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

T01–T17 now have implementations, editor integration and evaluation sections in
`demo/main.thor`. The 0.4.0 checkpoint covers interpolation (T13), runtime and
compile-time switches (T14), Set syntax (T15), negative sequence accesses (T16),
and expression-local lowering (T17). It passes 256 Python compiler/LSP tests,
1,097 native TLS tests, 49 grammar tests, allocation-tracked runtime/lexer tests,
and the isolated VS Code integration test. The demo passes all three define modes.

`thorlang/expressions.py` emits named procedures with explicit lexical inputs;
their calls remain in the original expression. Helpers instantiate lazily, so
inactive native when/#switch expressions leave unused bodies unchecked. Writable
array accesses retain storage; generic assignment branches preserve native map
headers. Loop posts remain simple assignments. Native type witnesses resolve
result/element types without executing expressions. TLS shares the parser's
feature metadata and adapts only its editor AST, keeping original source spans.

Later tasks T18–T23 cover purity, compile-time serialization/execution, caching,
scratch allocation and automatic folding. The compiler's resolution boundary and
conservative generic `using` pointer case are documented in `README.md`; keep
those limits visible while extending the resolver in later batches.
