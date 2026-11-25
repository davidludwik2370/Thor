# Lifetime precision and memory analysis expansion: 2026-11-22

This follows the [previous memory-check round](lifetime-memory-checks-2026-11-22.md).
The maintained Rust compiler implements the four precision improvements in order,
then the additional memory models. The Python reference remains frozen.

## Precision improvements

1. Cached field-specific summaries read only the record projections actually
   needed by a helper. Nested records, generic specializations, aliases and
   named forwarding retain their requirements. Copying an unused pointer field
   does not read its pointee; used references, encoded addresses and escaping
   stores retain their provenance. Failed proofs use ordinary interpretation.
2. Loop ownership states can forget completed helper-local slots after checking
   remaining aliases, caller temporaries, deferred captures, allocator identities
   and queued work. Inline copies do not acquire borrowed ownership by accident.
3. Bounded branch facts distinguish private failed allocations returned as nil
   from successful live owners. A verified, exclusive two-buffer swap followed
   by an empty resize can normalize analysis identities. External aliases,
   different allocators, retained references and fake swap implementations block
   that shortcut. No runtime swap or allocation is rewritten.
4. Deep reference projections expand only untouched synthetic caller-parameter
   tails on demand. They cannot revive released storage, invent a contract for
   an opaque result or repeat an index/call's side effects.

Projection summaries allow 16-field paths and 32 projections per parameter.
Demand expansion allows 32 extensions per root, at most four candidate slots
and 16 syntactic projection steps. Existing interpretation budgets remain in
force. Exhaustion remains an unverified boundary, not a proof of safety.

## Additional memory checks

* Supported primitive and fixed-array layouts provide byte extents and alignment
  requirements for raw typed access, pointer offsets and reviewed buffer APIs.
  Packed/custom record ABI layouts and unknown offsets are not guessed.
* Known non-zeroed heap allocations track up to 64 uninitialized intervals.
  Partial typed writes, byte copies, zero/fill operations and reviewed input
  operations update those facts. Unknown writes discard certainty rather than
  establishing that an entire buffer was initialized.
* Reviewed allocation, slice/view, byte-copy and size-aware cleanup contracts
  share the existing provenance model. A known nonzero cleanup size must match
  its allocation; zero retains Odin's unsized-free convention.
* Byte transport preserves uninitialized ranges without interpreting them as
  values, consistent with [LLVM raw-copy semantics](https://www.llvm.org/docs/LangRef.html#byte-type).
  Later typed reads retain the initialization check. Bounds, overlap and released
  source checks also remain active.
* Temporary allocator results belong to allocation generations. Resetting the
  current default or substituted temporary allocator invalidates earlier views;
  independent owned copies and new generations remain distinct.
* Integer narrowing/wrapping facts warn only when used as allocation/buffer sizes.
  They remain unverified intended-size warnings because wrapping can be deliberate.
* Native thread creation/start/run/join/destroy model retained single and
  polymorphic payloads and known callback effects. Borrowed payloads must survive
  completion. Detached workers, opaque callbacks and shared ownership effects
  remain conservative boundaries. This is not a race-freedom proof.

Odin supplies its existing local nil, `---` initialization, atomic access,
stack-escape and checked bounds/type diagnostics. Thor forwards them through
original-source projection instead of maintaining duplicate checkers. Additional
heap, alias, allocator, generation and asynchronous lifetime checks are opt-in.

## Issues found during the upstream audit

* Native bitset literals and procedure defaults retain their native constant
  contexts. An explicit struct context still selects field-wise operators.
* Generic `for` initializers no longer receive an extra declaration inside the
  loop header. Immutable update errors identify the actual source binding.
* Native `when` branch bodies share the enclosing runtime scope, including
  local lifetimes, deferred cleanup and allocator restoration. Explicit nested
  blocks still have their own scope.
* Destroyed caller containers no longer export their dead contents. Surviving
  aliases, returned/global references and missing nested cleanup still warn.
* Empty slice literals have a nil header and no backing stack allocation.
  Zero-length views of existing storage retain their original provenance.
* Fixed-input permission queries cache complete negative view/body scans even
  when unrelated imports remain pending. Incomplete discovery remains uncached.
* Convention vetting recognizes writes in the declaration's own compile-time
  branch and maps rejected immutable proposals back to their declarations.
* Required imports survive unused-import corrections and editor actions. Test
  correction validation includes imported test packages. Matrix element writes
  and mutable element addresses prevent unsafe immutable edits.
* Native `#row_major` / `#column_major` matrix literals retain compound-literal
  parsing, without broadening the separate `#type` helper grammar.
* Native tabs-policy probes exempt compiler-owned native runtime support from
  indentation checks. User Thor/native sources and SDK policies remain checked;
  ordinary builds keep the existing symlinked runtime cache.
* Compile-time enum arguments retain their parameter's expected enum type when
  specializing generic procedures. An unrelated `fn` no longer makes a valid
  implicit enum argument fail, or causes convention validation to bisect every
  unrelated proposed edit. Explicit and imported enum aliases are covered.
* Prospective reference-edit proofs and correction validation have finite
  budgets. An unfinished validation emits `thor/correction-unknown` without a
  source edit; it does not claim that the proposed change compiles. Ordinary
  compilation has no such added limit, and lifetime diagnostics are independent.

A controlled OLS full-vet run completed in 45 seconds with a measured 1,780 MiB
process-tree peak. The earlier unbounded convention proof exceeded 12 GiB.
OLS's lifetime phase itself took 0.645 seconds; result-fact discovery took
3.190 seconds and prospective reference safety took 3.935 seconds. These are
loaded audit measurements, not isolated performance benchmarks. Its 187
unverified convention corrections remain manual rather than being applied from
an incomplete proof.

## Validation

These runs overlap; their counts are not added together:

* Memory/scoped-owner/struct interaction suite: **515/515 passed**.
* Convention, immutable-oracle, storage and precision suite: **135/135 passed**.
  The final immutable/vet rerun after the integration fixes passed **88/88**.
* Raw-memory, buffer and native-check suite: **72/72 passed**.
  Allocation-free I/O/random compatibility: **103/103 passed**.
* Broad live TLS run: **67/67 passed**. Final precision, required-import,
  matrix/enum hover, source-range and unsaved-recovery tests: **12/12 passed**.
* Compile-time-mode and native matrix suites: **37/37 passed**, plus **6/6**
  focused enum/import/mutable-address correction regressions.
* Rust unit tests: **109/109 passed**. Integration harness and assertion hygiene:
  **23/23 passed**. Native vet/CLI compatibility: **103 passed, 1 skipped**.
  Frozen-reference verification and `git diff --check` passed.
* The earlier broad release run in this round passed **731/731**, with a further
  **178/178** permission/purity/ownership interaction run after the cache fix.
* Native executable oracles use checked builds and
  `-o:speed -disable-assert -no-bounds-check -no-type-assert`. Verdicts use explicit
  `if`/`panic` checks, including byte initialization, joined workers, allocator
  context, empty slices and scope/defer behavior.

## Pinned upstream corpus

All **22 repositories / 160 vet configurations** were audited, including test
packages and target/define variants. **153 configurations passed**; seven have
no usable native baseline. Successful vet commands can contain warnings.

The **175 original build/test commands** have **165 runnable native-baseline
matches** and ten unavailable cases. The matching runs include the same twelve
pre-existing native test failures: ten Runa shaping cases, one Aseprite case and
one Tina simulation case. Seven commands have unavailable native build baselines;
three previously crashing/unavailable native runtime configurations were not
re-executed. Their separate compile coverage remains recorded. This round ran
on macOS ARM64; it makes no new Windows or Linux validation claim.

The converted fixtures needed two kinds of source repair. Twenty-two unsafe
immutable edits left by an older vet run were restored to mutable declarations
in six SDK crypto files and the OpenGL example. Three cleanup helpers (two Runa
shape-test bundles and the SDL GPU renderer) now clear freed headers in their
caller-owned records. The native original and migrated baseline trees were
preserved. Hashes and reasons are recorded in the raw report. Regression tests
keep required imports and mutable pointer-exposing inputs from being auto-fixed
back into invalid source.

The remaining **one definite finding** is OdeEcs's intentional bad-free test,
which passes a stack address to its tracking allocator. It remains flagged.
No definite leak remains in these audited configurations. Unverified foreign
calls, opaque reference results, casts, asynchronous ownership and bounded
analysis still require review; this is not a memory-safety guarantee.

Across configurations there are **7,402 unverified lifetime**, **2,258 unverified
leak** and **9,719 unverified convention-correction** findings. Repeated source
under different configurations contributes to those totals. The raw report also
records distinct locations/messages and their categories. Convention validation
that exceeds its proof budget emits a manual finding without a fix; these edits
are not presented as proven immutable/`fn` conversions. Memory diagnostics stay
independent of that convention budget.

After the enum fix, SDK normal/speed vet completed in **69.306 / 65.848 seconds**,
each with **one** correction-validation build. The earlier normal run spent over
13 minutes bisecting edits after the unrelated enum inference failure. The final
native test comparisons passed **1,407 normal** and **359 speed** test cases;
internal (**265**) and vendor (**65**) suites also match their native baselines.
Large upstream timings include native compilation, test execution or convention
validation as appropriate; they are not isolated runtime benchmarks.

## Local timing and runtime-code audit

Twenty valid local projects were checked with the same updated inputs in both
engines. Sandbox's deliberately invalid experiments were excluded. Unverified
memory findings decreased from **115 to 99** (88→72 lifetime, 27→27 leak), without
adding acknowledgements. Source hashes confirm the local projects were unchanged.

Three sequential paired warm samples were taken per engine/project for ten
representative projects after the heavier jobs stopped. `vet` below selects only
`lifetime,leak` and uses required-only compile-time work, to isolate these checks.
It includes the native checking process. All timings are wall seconds.

| Project | Vet before | Vet after | Normal transpile before | Normal transpile after |
|---|---:|---:|---:|---:|
| hilo_test | 0.253 | 0.256 | 0.153 | 0.143 |
| poker_pmap | 0.990 | 0.994 | 0.536 | 0.519 |
| sudoku | 0.448 | 0.543 | 0.112 | 0.110 |
| life | 0.215 | 0.216 | 0.118 | 0.120 |
| 15_puzzle | 0.547 | 0.575 | 0.348 | 0.348 |
| md5 | 0.200 | 0.203 | 0.139 | 0.141 |
| circles_on_dots | 0.500 | 0.492 | 0.280 | 0.269 |
| bin_tree | 0.321 | 0.325 | 0.148 | 0.146 |
| hilo | 0.186 | 0.184 | 0.107 | 0.107 |
| sketch | 0.254 | 0.249 | 0.153 | 0.153 |

Most vet medians stayed within a few milliseconds. The additional checks cost
about **95 ms** in Sudoku and **28 ms** in 15 Puzzle; the largest measured warm
vet median was **0.994 s** for poker_pmap. Normal transpilation remained effectively
stable, and neither normal nor required-only transpilation entered a lifetime
analysis phase. Poker and poker_pmap were checked separately in test build mode.
These samples cover this corpus, not a universal time bound.

Of **62 generated Odin files**, **60 were byte-identical**. The other two
(15 Puzzle and circles_on_dots) now emit native scalar/array operators directly
where their expected type is known, omitting compile-time struct fallback paths;
internal diagnostic token numbering also differs. For both programs, optimized
LLVM output was built in checked and unchecked modes. Every user-procedure and
entry-point opcode/direct-call count matched. Full LLVM text differs in compiler
symbol numbering, ordering and metadata, so this is not claimed as bitwise
binary equivalence or a dedicated runtime benchmark.

The new memory machinery runs only in vet. It introduces no runtime checks,
tracking tables, cleanup calls or ownership changes. Odin's existing local
checks remain the implementation for the native cases listed above.

[Machine-readable report](lifetime-memory-expansion-2026-11-22.json) contains
paired timings, source/compiler hashes, generated-file comparisons, native LLVM
comparison counts and flags, per-repository configurations, baseline verdicts,
warning categories, budgets/counters and converted-fixture repairs. Corpus
results were retained throughout this round, with affected configurations rerun
after later fixes; exact compiler hashes are recorded per repository.
