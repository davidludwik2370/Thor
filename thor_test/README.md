Local Odin → Thor compatibility corpus

The originals in `/Users/davidludwik/Code/Odin` and `/Users/davidludwik/Code/Odin_Projects` are untouched. The request repeated the latter path; both established project roots were included.

The local copies are:

- `odin/`: Odin reference sources, assets, and foreign libraries.
- `thor/`: the same sources with every `.odin` renamed to `.thor`.
- `idiomatic/`: Thor conventions and selected Thor features applied to those copies.
- `artifacts/`: build logs, generated source, binaries, caches, runtime fixtures, rendered screenshots, and detailed measurements.
- `patches/`: reviewable differences between the renamed and idiomatic sources.
- `evidence/`: compact results and source-preparation records.

Copies, binaries, and detailed artifacts remain local and are Git-ignored. Harnesses, evidence, and patches can be versioned without committing three copies of the projects' assets and generated output.

Coverage

The snapshot contains 292 source/configuration/resource files across 72 top-level project folders. It has 82 Odin package directories and 102 build cases, including individual archived versions. The existing Thor-only `Odin/my_project` is checked separately. The `Odin/thor` symlink back to the compiler is excluded to prevent recursive copying.

The untouched-source baseline built 80 cases with Odin and 91 after extension-only conversion to Thor, with no Odin-success/Thor-failure case. Older examples also needed current Odin API names/error conventions, a missing print alias/test printer, and explicit Odin `using-stmt` tags. Those repairs are confined to the copies and recorded in `evidence/test-preparation.json`. After those preparations, 95 cases build in all three forms.

Seven directory/file entries are not standalone programs: five aggregate archive folders contain duplicate declarations/entry points, and two `steps.odin` files explicitly say `#+build ignore`. Individual usable archived versions are checked separately.

The user authorized smaller test workloads. The same bounds apply to all three copies: poker 10,000 trials, HiLo and pi 1,000,000, factorial 1,000, prime examples up to 1,000,000, and quicksort 20 sorts of 10,000 elements. Quicksort's former million-sort outer loop would otherwise perform ten billion element-level inputs before sorting costs.

Idiomatic rewrites

The final rewrites are preserved as exact patches and have been reapplied to a fresh prepared copy to verify reproduction. The authoring helpers `rewrite_vet.py`, `plan_refactors.py`, and `extra_idioms.py` remain available for inspection; they are not needed to replay the final changes.

The copies use immutable bindings, eligible pure functions, selected imports, inferred arrays, literal membership, value switches (including multiple assignment), negative indexing, string repetition, and compile-time concatenation where those patterns actually occur. Additional focused examples use `Integer` and `Rational` arithmetic in Fibonacci/big_ref, and slice/dynamic comprehensions in filter, script_pg, brc, and both Circles projects. Allocating comprehensions and big values have explicit cleanup. C bindings are left as bindings; features are not inserted where they would obscure the code or change its contract.

`ord_bytes` deliberately remains a `proc`: its tests intercept invalid inputs at runtime. Changing it to a `fn` would move a known literal failure to compilation. Generic library helpers with recoverable allocator behavior also retain their existing contracts.

Runtime verification

Console fixtures seed random generators, reduce worker counts to one for reproducible simulation comparisons, provide deterministic input files, and drive interactive input one prompt at a time. Output comparisons preserve values; explicit normalizations cover executable paths, pointer addresses, elapsed times, unordered map output, and concurrent scheduling traces. Huffman codes may vary with equal-frequency symbol order, so the saved bitstream is decoded with that run's dictionary and checked against the input rather than requiring identical compressed bytes.

GUI fixtures run real raylib or SDL/TTF rendering. They replace wall-clock/input queries in throwaway copies, seed RNGs, and stop after 64 frames (8 for the glyph-heavy SDL demo). Six captured frames are compared by pixel hash at startup and during a scripted keyboard/mouse sequence, with repeat runs to check determinism. These are bounded behavior checks, not exhaustive gameplay tests. Screenshots are under `artifacts/runtime-source/{startup,input}/CASE/{odin,thor,idiomatic}/audit-frame-*`.

`Odin/test_area/test.odin` contains `free(x)` followed by `x^`. AddressSanitizer identifies the same heap-use-after-free in all three variants. Its unspecified output is not counted as an equivalence pass. Foreign declaration packages are compiled independently and exercised by their parent examples. The MD5 sample prints each digest byte with `%x`, omitting leading zeroes. CLI boundary tests preserve that original display convention and verify it against independently computed digest bytes.

The projects' own filter, poker, console tic-tac-toe, and GUI tic-tac-toe test suites are also run in each variant.

Compiler fixes found during this audit

- A cast multi-pointer sliced with a runtime bound could acquire the wrong helper result type. Native type recognition now includes multi-pointers; generic slice type queries use a bounded empty slice. Regression coverage checks casts, aliases, generic calls, shared storage, and once-only bounds in debug and unchecked release configurations.
- Readonly array captures were copied into unnecessary helper locals. This both slowed tight loops and changed native Odin aliasing when a bound expression updated the original array. Array reads now use the native captured storage; slice headers, explicit copies, and effectful bases retain their required snapshots. Tests compare directly with Odin in debug and unchecked builds, and cover compile-time execution and a warm result cache.
- Allocation-free pure calls still invoked the generic scratch-map destructor. Empty-capacity scratch storage now has a fast path; maps with retained capacity still take the full cleanup path. Checked and unchecked runtime tests verify both paths.
- The idiomatic SDL/microui example exposed repeated slice-analysis SDK metadata resolution. Metadata and lexical symbol queries are cached only within a fixed-input analysis pass, and dependency discovery restarts with fresh state. Tests cover callback-containing records, generic bindings, readonly failures, dependency loading, and SDK behavior.

Reproduction

Use the matching installed Odin SDK and a built release Rust compiler. All commands below run from the compiler root. Start from a fresh `thor_test` snapshot directory before `prepare`; existing copies are intentionally not replaced.

```sh
python3 thor_test/audit.py prepare
python3 thor_test/audit.py build --tag baseline
python3 thor_test/prepare_sources.py
python3 thor_test/apply_rewrites.py
python3 thor_test/audit.py build --modes odin,thor,idiomatic --tag prepared
python3 thor_test/project_tests.py
python3 thor_test/benchmark.py --repeats 3
python3 thor_test/report.py
python3 thor_test/audit.py verify
```

`runtime.py --ids ID,ID` runs console cases; add `--gui` for rendering and `--interactions` for scripted GUI input. GUI execution needs macOS WindowServer access. Run a single writer per report file. `--modes idiomatic` can update one variant against existing reference observations. Runtime fixture instrumentation is separate from the source copies used in build timing.

Matched build flags are `-o:speed` for Odin and Thor. The full timing sweep (855 builds) was collected before the final array-capture and scratch-cleanup fixes. A separate final sample checks the principal outliers and CPU workloads after those fixes. Timing runs build sequentially, rotate mode order, use three samples, and report medians with warm compile-time-result caches. They do not reuse whole-project build results. Full per-case commands, outputs, and samples are retained in artifacts; the compact table is `evidence/cases.csv`.
