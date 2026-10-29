# Notes for a possible Odin compiler PR

The issue can be reported independently of this patch. This is a candidate fix
for review, not a completed upstream PR or an installed compiler modification.

Suggested title: Publish cached LLVM type names atomically

## Problem and resulting behavior

LLVM code-generation workers for different modules share `Entity` objects.
`lb_get_entity_name` currently reads/writes `TypeName.ir_mangled_name` as an
ordinary `String`. Both its pointer/length header and underlying bytes need safe
publication before another worker can use the cached name.

The prototype replaces that cache with an atomic pointer to an immutable,
permanently allocated `String`. A reader uses an acquire load. A writer builds
the string privately, then publishes it with compare/exchange using acquire/release
ordering. If another writer won, the losing writer returns the published value.
The local type-declaration check uses the same atomic load.

This follows the existing atomic pointer publication pattern for Odin's local
procedure names and preserves parallel code generation.

## Patch scope

Base commit: `8169255b97b85d6a08691d99764f51fb01f92b5a`.

- `src/entity.cpp`: change `TypeName.ir_mangled_name` to `std::atomic<String *>`.
- `src/llvm_backend_general.cpp`: acquire-load cached names and publish fully
  initialized strings with compare/exchange.
- `src/llvm_backend_stmt.cpp`: use an atomic read for the existing-cache check.

The patch does not change Thor, generated program behavior, source maps, compiler
defaults or the separate procedure/variable link-name caches. Competing
initializers can retain unused allocations in the compiler's permanent arena
until compiler exit; avoiding those allocations is not part of this prototype.

To check/apply from a separate Odin checkout, substitute the absolute bundle path:

```sh
git apply --check /path/to/odin-codegen-race/type-name-cache-publication.patch
git apply /path/to/odin-codegen-race/type-name-cache-publication.patch
```

## Validation already performed

- Original C++ TSan compiler: the minimal plain Odin hello fixture reports the
  observed name-cache race. Three runs of a separate print fixture each produced
  10 reports with the first access in `lb_get_entity_name`.
- Prototype C++ TSan compiler: no such reports in the minimal fixture or those
  three repeated print cases.
- Original TSan compiler controls: both `-thread-count:1` and
  `-use-single-module` removed this race family. The earlier single-thread
  control had no reports; the fresh bundle check had one unrelated
  parser/global-file-table report. Single-module checks also left other reports.
- Prototype release compiler: 200 stress compilations, plain hello, a frozen
  Thor-generated print case and both Thor walkthrough demos passed.
- 2,402 stock-compiler stress builds did not repeat the original SIGSEGV. This
  does not negate the independently reproduced TSan finding. A later full
  regression run repeated the same SIGSEGV signature (null source, length 30);
  the failing test passed on isolated retry. See the recurrence summary.

The minimal example and runner are in this folder. Aggregate historical results
are in `evidence/summary.json`; the selected type-name access pair is in
`evidence/type-name-race.txt`. Report counts depend on the invocation/scheduling;
the runner checks presence rather than an exact count.

Fresh validation of the packaged runner is recorded separately in
`evidence/bundle-validation.json`, with a matching minimal-fixture trace in
`evidence/minimal-fixture-race.txt`. Three baseline runs reported 12, 12 and 10
first-access name-cache warnings; three prototype runs reported zero. All
experiments emitted LLVM IR. The stock compiler also passed three ordinary
compilations using the packaged runner.

## Work remaining before a PR

1. Check current upstream and related open/closed issues/PRs. Rebase or adapt the
   patch if needed; rerun the original and patched instrumented compiler on that
   same revision rather than assuming this pinned result applies to it.
2. Review all cache access sites, initialization/lifetime assumptions, naming
   semantics and atomic publication against upstream conventions. Confirm that
   competing writers compute the same canonical name and that permanent
   allocations are appropriate for this cache.
3. Decide with maintainers whether the analogous procedure/variable `link_name`
   caches should be included or handled separately. A variable/link-name race
   remains in the larger print fixture after the narrow patch. The prototype is
   not a complete audit of TSan findings.
4. Run Odin's full compiler/runtime tests and supported-platform CI, including
   ordinary debug/release compiler builds and both separate/single-module
   compilation. Those full upstream checks have not been run for this patch.
5. Add the regression coverage maintainers prefer. An ordinary compile-only test
   cannot reliably demonstrate this race is absent; a C++ TSan job or suitable
   targeted concurrency test is the meaningful regression check. The supplied
   runner is a diagnostic tool, not yet integrated with Odin's test harness.
6. Check compile-time/memory impact if maintainers request it. No performance
   equivalence or cross-platform race-free claim has been established.

No change needs to be installed in the user's compiler to prepare or submit the
report. Attach the narrow patch as experimental if filing an issue first.
