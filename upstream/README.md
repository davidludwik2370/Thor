# Odin compiler issues found while developing Thor

| Issue | Evidence and status |
| --- | --- |
| [Parallel LLVM type-name cache race](odin-codegen-race/README.md) | Confirmed C++ ThreadSanitizer race, minimal native Odin reproducer, intermittent stock-compiler crashes, and an experimental patch. Reporting bundle prepared. |
| [Anonymous procedure using `#branch_location`](odin-branch-location-anonymous/README.md) | Reproducible native Odin backend assertion, including with one compiler worker. Minimal reproducer and named-procedure control recorded. |
| [Generic Tensor substitution after source errors](odin-tensor-substitution/README.md) | Reproduced on pinned Odin `537fac5` during Anvil vet validation. The checker traps in `subst_apply` after emitting source errors. Standalone 86-line native reproducer and single-worker debugger output prepared. |
| [Scoped native-vet parser crash](odin-scoped-vet-parser/README.md) | Three-line native Odin reproducer, intermittent SIGSEGV in `ast_file_vet_flags`, and successful single-worker control runs recorded. |
| [Test-runner JSON report argument mismatch](odin-testing-json-report/README.md) | Reproduced in the official Linux ARM64 `9a69916` nightly. A one-test package fails inside the SDK when JSON reporting is enabled. Native controls pass without the define and on pinned revision `537fac5`. |
| [Stack recovery through an uninlined wrapper](odin-trap-wrapper-inline/README.md) | Native Windows x64 reproduction: a `#force_inline` capture wrapper remains out of line at `-o:none`; an alias control passes. Tina also exposes a default-optimization inlining sensitivity after lowering. |
| [Mixed-type multi-result `or_else` fallback](odin-or-else-multi-results/README.md) | Native Odin `8169255b9` drops values from a direct fallback call. Minimal source and allocation-free packing workaround recorded. |

The code-generation, branch-location and scoped-vet investigations use Odin revision
`8169255b97b85d6a08691d99764f51fb01f92b5a`.

Additional native vet observations are recorded in the
[native vet audit](../docs/native-vet-audit.md).

- Odin's SDK internal tests can trigger duplicate symbols for file-private
  generic procedures after adding local generic helpers. The saved generated
  Odin input reproduces this directly. A single-module native build succeeds.
  See the native linker collision investigation in
  [the portable reporting bundle](odin-private-generic-linker/README.md).
