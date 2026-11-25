# Matching native debugger controls

These nine pairs correspond to the nine Windows failures from the November 23
validation. `main.thor` stays synchronized with the original live regression in
`tests/test_debugger.py`, with explicit runtime guards in place of elidable
assertions. `cases.json` records the exact test names. The `.odin` counterparts
are handwritten native source, not Thor-transpiled output.

| Pair | Native counterpart | Observation |
| --- | --- | --- |
| allocated | [`allocated/main.odin`](allocated/main.odin) | Allocating string-returning procedure; inspect `n` at the first body statement. |
| using | [`using/main.odin`](using/main.odin) | Same `using` parameter/statement, with Odin's required file opt-in on the original comment line. |
| basic | [`basic/main.odin`](basic/main.odin), [`helper`](basic/helper/basic.odin) | Exact installed `print` procedure body in a separate native package; inspect variadic arguments and step over. |
| deferred | [`deferred/main.odin`](deferred/main.odin) | Byte-identical native defer/`#branch_location`; breakpoint on return line 11. |
| fallback | [`fallback/main.odin`](fallback/main.odin) | Explicit native success/fallback branch calling the same scalar procedure. |
| pmap | [`pmap/main.odin`](pmap/main.odin) | Same generic callback, jobs and named results through the native Odin scheduler. |
| static | [`static/main.odin`](static/main.odin) | Explicit `@(static, rodata)` slice, matching Thor's compile-time-known literal storage. |
| switch | [`switch/main.odin`](switch/main.odin) | Native switch statement and equivalent last-element indexing, calling the same procedure. |
| unicode | [`unicode/main.odin`](unicode/main.odin) | Byte-identical Unicode line; request UTF-16 column 13. |

The `basic` formatting module and `pmap` scheduler are existing `.odin` sources
under `runtime/`. Native controls compile these directly with Odin; they never
run the Thor frontend, lowering or compile-time executor.

From the repository root, a native control can be built independently:

```sh
odin build tests/support/debugger_pairs/allocated -debug -o:none -thread-count:1
odin build tests/support/debugger_pairs/pmap -debug -o:none -thread-count:1 -collection:thor=runtime
```

Run the maintained comparison tool for all pairs. Select a fresh output directory
so previous evidence is preserved:

```sh
python3 tools/compare_windows_debugger.py --output /path/to/new-results --repeat 2
```

The tool creates separate package directories for Odin and Thor. It runs the
native program through direct CodeLLDB and through an identity source-map bridge,
then runs Thor through the normal adapter. It saves all DAP requests/responses,
stops, watch results, instruction listings, compiler commands and hashes.

Checked and optimized unchecked builds must pass explicit runtime guards and
produce identical output. Native debug-info failures remain recorded as failed
checks. A zero comparison exit status means there are no extra Thor/bridge
failures or setup errors; it does **not** mean every native debugger check passed.
The comparison checks repeats separately and permits improvements over native.
The original live debugger tests remain strict and still fail for unresolved
native limitations.

See [the case-by-case report](../../../docs/windows-debugger-pairs-2026-11-23.md).
