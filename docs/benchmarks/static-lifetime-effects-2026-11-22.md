# Static immutability and lifetime effects: 2026-11-22

This follows the [model and summary audit](lifetime-models-summaries-2026-11-22.md).
Both release compilers use identical application sources and flags. No project
edits, warning suppressions or ownership auto-fixes were made in this round.

## Static storage correctness

The parser records immutable declarations at the first declared name. An
attributed declaration's exported span starts at its attribute instead. Rust's
AST normalization incorrectly used that span start to recover immutability,
so adding an attribute could erase `:>` permissions. Normalization now matches
the first declared name, including grouped and generic declarations. The AST
cache revision includes this change.

The actual `sandbox.thor` example now fails at `x += y` and `&x`, both checked
and with `-o:speed -disable-assert -no-bounds-check -no-type-assert`. Its source
is intentionally unchanged. `@static x := 10` remains mutable and persistent;
`@static x :> 10` remains immutable. Attributes never grant write permissions.
TLS receives the same corrected metadata and reports the original source ranges.

Related fixes and tests cover:

* Mutable static state prevents `fn` eligibility and explicit `#run`. All four
  comptime modes preserve an ordinary procedure's persistent counter, including
  calls through an alias. Immutable static values remain usable in pure code.
* Native `@(static, rodata)` tables remain eligible for direct pure reads and
  value copies, including `math.pow10`'s tables. Mutation and writable access
  do not receive that read-only exception.
* Lifetime vet keeps local static slots across calls, separately for each generic
  type/constant instantiation. Retained pointers are checked after their referents
  are released, rather than reset to an invented fresh slot on every call.
* A nil branch is a real graph alternative. Dropping an allocated header on
  one branch must not be mistaken for unconditional ownership transfer merely
  because nil was visited first.

## Completed analysis improvements

| Area | Behavior and retained boundaries |
| --- | --- |
| Growing string builders | Models constructors, buffer growth, writes, views, formatting, reset and destroy. Empty construction/writes do not invent allocations; allocator arguments work by position and name. Fixed borrowed buffers are not freed by a builder with a nil allocator. Outstanding views, opaque allocators and custom formatters remain protected. |
| Scalar library effects | Reviewed `strconv.parse_int`, `parse_uint` and 64-bit variants read strings and check optional output-pointer lifetimes. `os.exists` reads its path. `rand.choice` and `shuffle` preserve nested reference provenance; opaque/replaced generators retain a callback boundary. |
| Owning dynamic-array returns | A bounded cached proof recognizes an empty local or named result header that is only appended to and returned unchanged on every return. Header replacement, cleanup, escaping addresses, different returned headers and reference-bearing elements do not receive this shortcut. |
| Edge-free promotion | Promotion avoids a graph walk for values with no tracked edges. It preserves existing uncertainty and addresses carried by integers. |
| Reviewed foreign contracts | Raylib 6.x `DrawText` and `MeasureText` consume their text argument during the call. `InitWindow` and `SetWindowTitle` retain it; replacement or `CloseWindow` releases the tracked borrow. An empty `InitWindow` title can reuse the old pointer. Registered callbacks and unreviewed functions retain review boundaries. |
| Broader cached effects | Verified summaries now cover inline-value pointer/slice writers, by-reference range writes and scalar-global effects. Read/write/global masks propagate through complete recursive components. Every invocation rechecks tracked caller/global provenance; integer-carried addresses, reference mutation, static state, allocations and opaque calls retain full analysis. |

Foreign contracts follow the verified installed collection and binding version,
not just a function name. Aliases work; a user procedure with the same name does
not gain a contract. The Raylib C source revision and exact contract scope are
documented in the [lifetime reference](../lifetime-vet.md#reviewed-foreign-contracts).
These contracts do not grant purity or `no_alloc` exemptions.

Summary construction still has explicit request/component budgets. Native
assertion elision, selected compile-time branches and known short circuits are
respected. Disabled and inactive code cannot introduce invented effects.

## Validation

* **437** release compiler/lifetime/scoped-owner/cache/purity/immutability
  regressions passed. The additional static-counter test passed in all four
  comptime modes and verified that vet does not suggest `fn` for it.
* **35** live TLS tests, **103** `no_alloc` regressions, **104** Rust unit tests
  and **7** assertion-hygiene tests passed. The frozen Python reference matches
  `python-reference-2026-11-21` unchanged.
* Three portable native runtime fixtures passed checked and unchecked release
  configurations. They verify growing-builder ownership with a tracking
  allocator, writer/global behavior, and independent generic static slots.
  Required work and verdicts remain active when assertions are disabled.
* Positive/negative pairs cover aliases, defaults, named arguments, generics,
  recursion, empty buffers, fixed buffers, views surviving growth, freed parser
  outputs, nested owners, discarded nil-branch owners, integer-address stores,
  retained foreign titles, opaque callbacks and inactive callback registrations.
* All **20** valid project checks and both explicit Poker test-mode checks pass
  with each compiler. All **62** generated Odin files are byte-identical. The
  deliberately invalid sandbox is tested separately and correctly rejected.
  The analysis changes add no runtime instructions.
* One earlier debug-run native check hit an internal Odin array assertion. Its
  isolated release rerun and the full final 437-test release suite passed. No
  retry, suppression or change to Odin was added.

## Findings and timings

Unverified findings fall from **294 to 227**; neither project sweep has definite
findings. No ignore annotations were inserted. Remaining warnings include 102
recursive-summary boundaries, 56 statement/project-budget boundaries, 16 loop
convergence boundaries and 53 other uncertain effects/provenance/ownership cases.
These remain warnings, not claims that the programs are free of lifetime bugs.

Sequential paired warm medians of three runs on this Mac, with two backend
threads, `--comptime=required` and `--vet=lifetime,leak`:

| Project | Findings before → after | Graph before → after (ms) | Total vet before → after (s) |
| --- | ---: | ---: | ---: |
| hilo_test | 3 → 1 | 19.7 → 18.7 | 0.256 → 0.262 |
| poker_pmap | 15 → 13 | 283.3 → 281.5 | 0.810 → 0.818 |
| sudoku | 11 → 2 | 195.7 → 192.4 | 0.394 → 0.388 |
| life | 6 → 3 | 36.1 → 36.8 | 0.234 → 0.228 |
| 15_puzzle | 127 → 112 | 175.4 → 171.4 | 1.084 → 1.063 |
| md5 | 5 → 3 | 3.6 → 3.8 | 0.208 → 0.207 |

The vet-only native-result metadata phase remains included in total time:
roughly 2.7–2.9 ms for the small samples, 14 ms for Sudoku/15 Puzzle, and 27 ms
for Poker. The graph remains bounded; the broader cache does not increase its
budgets to suppress unknowns.

| Normal transpile | Before (s) | After (s) |
| --- | ---: | ---: |
| hilo_test | 0.152 | 0.152 |
| poker_pmap | 0.409 | 0.422 |
| sudoku | 0.112 | 0.113 |
| life | 0.118 | 0.119 |
| 15_puzzle | 0.385 | 0.369 |
| md5 | 0.141 | 0.144 |

Normal-transpile profiles contain neither lifetime-vet phase. These small warm
samples show similar compilation/vet costs, not a uniform speedup claim. The
new writer summaries reduce repeated graph interpretation where proven; opaque
reference effects still incur ordinary analysis or an explicit review warning.

[Raw diagnostics, samples, counters, source/compiler hashes, sandbox errors and validation results](static-lifetime-effects-2026-11-22.json).
