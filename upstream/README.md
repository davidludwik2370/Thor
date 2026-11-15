# Odin compiler issues found while developing Thor

| Issue | Evidence and status |
| --- | --- |
| [Parallel LLVM type-name cache race](odin-codegen-race/README.md) | Confirmed C++ ThreadSanitizer race, minimal native Odin reproducer, intermittent stock-compiler crashes, and an experimental patch. Reporting bundle prepared. |
| [Anonymous procedure using `#branch_location`](odin-branch-location-anonymous/README.md) | Reproducible native Odin backend assertion, including with one compiler worker. Minimal reproducer and named-procedure control recorded. |
| [Generic Tensor substitution after source errors](../docs/upstream-integration.md#native-checker-crash-after-source-errors) | Reproduced on pinned Odin `537fac5` during Anvil vet validation. The checker traps in `subst_apply` after emitting source errors. Captured sources and debugger output are retained locally. |
| [Scoped native-vet parser crash](../docs/native-vet-audit.md#native-scoped-vet-parser-crash) | Three-line native Odin reproducer, intermittent SIGSEGV in `ast_file_vet_flags`, and successful single-worker control runs recorded. |

The code-generation, branch-location and scoped-vet investigations use Odin revision
`8169255b97b85d6a08691d99764f51fb01f92b5a`.

Additional native vet observations are recorded in the
[native vet audit](../docs/native-vet-audit.md).

- Odin's SDK internal tests can trigger duplicate symbols for file-private
  generic procedures after adding local generic helpers. The saved generated
  Odin input reproduces this directly. A single-module native build succeeds.
  See the native linker collision investigation in
  [the integration report](../docs/upstream-integration.md#native-file-private-generic-linker-collision).
