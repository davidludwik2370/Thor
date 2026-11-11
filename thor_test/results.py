#!/usr/bin/env python3
"""Render the compact audit results from the recorded evidence."""
import json,statistics
from pathlib import Path
from audit import CORPUS,ART
summary=json.loads((CORPUS/'evidence/summary.json').read_text())
full=json.loads((ART/'build-benchmarks.json').read_text());final=json.loads((ART/'build-benchmarks-final-samples.json').read_text());runtime=json.loads((ART/'runtime-benchmarks.json').read_text())
text='''Results — 2026-11-11

All 292 original source/configuration/resource files are unchanged. The local Odin, renamed Thor, and idiomatic Thor trees are under this folder. The final compiler fixes are committed as `2d9f155` and `ec273d3`.

- 95 valid build cases pass in all three forms, including individual archived versions and two foreign-declaration packages.
- Seven aggregate/ignored entries fail equivalently and are excluded from timing.
- 101 source files were rewritten; replaying the saved patches reproduces them exactly.
- 58 console cases match their checked results, and 34 GUI cases match six captured startup frames and six scripted-input frames. Representative GUI cases were rechecked after the late compiler fixes.
- The four existing project test suites pass in all three forms, including the 213-test poker suite. Additional compiler checks passed: the initial 242-check sweep, 35 editor/using/capture checks, a 109-check final sweep, and six direct Odin-comparison/cache regressions. These sweeps overlap; their counts are not added together.
- Nine runtime ownership tests pass in checked and assertion-disabled release builds. Assertion hygiene and Clippy pass.

Build timings

The full sweep used three samples, rotating order, equal `-o:speed` flags, sequential builds, and warm compile-time-result caches: 855 successful timed builds. It predates the final array-capture and scratch-cleanup fixes; the following final sample reran the principal outliers after those fixes.

Across the full sweep, the median added time was **34 ms** for renamed source and **297 ms** for idiomatic source. The respective maxima were **409 ms** and **1.90 s**. Idiomatic rewrites therefore do not have uniformly negligible build overhead; the UI and large-demo cases still spend more time in analysis and generated Odin checking.

| Final sample | Odin | Renamed Thor | Idiomatic Thor |
|---|---:|---:|---:|
'''
for row in final:
 text+='| '+row['label']+' | '+' | '.join(f"{row[mode]['median_seconds']:.3f} s" for mode in ['odin','thor','idiomatic'])+' |\n'
text+='''
CPU workload timings

Five runs per variant, rotating order, with the same seeded workload and verified output. Times include executable startup. These checks found and removed unnecessary array copies and empty scratch-map cleanup. Small differences remain; the table records them rather than treating them as zero.

| Workload | Odin | Renamed Thor | Idiomatic Thor | Idiomatic difference |
|---|---:|---:|---:|---:|
'''
for row in runtime:
 text+='| '+row['label']+' | '+' | '.join(f"{row[mode]['median_seconds']:.4f} s"for mode in ['odin','thor','idiomatic'])+f" | {(row['idiomatic']['ratio_to_odin']-1)*100:+.2f}% |\n"
text+='''
Findings

The audit fixed a native multi-pointer slicing/type-query bug, repeated permission-analysis metadata resolution, incorrect extra copies of readonly array captures, and unnecessary cleanup work for allocation-free pure calls. The array regression was observable: native Odin produced `9` after an aliased update, while the old Thor helper produced a copied `1`. Tests now compare this behavior directly with Odin and retain explicit-copy/slice-header behavior.

`Odin/test_area/test.odin` contains a preexisting `free(x)` followed by `x^`. AddressSanitizer confirms that use-after-free in all three forms, so its unspecified output is excluded from equivalence passes. The MD5 sample prints bytes with `%x`, omitting leading zeroes; boundary tests preserve that convention and independently check the digest bytes. The general demo's thread-pool cancellation trace is nondeterministic in native Odin too; deterministic sections are compared, with complete raw traces retained.

See [cases.csv](evidence/cases.csv) for every case, [summary.json](evidence/summary.json) for compact structured evidence, [patches](patches) for source rewrites, and [README.md](README.md) for methods and reproduction. Detailed logs, binaries, frame captures, and timing samples remain in `artifacts/`.
'''
(CORPUS/'RESULTS.md').write_text(text)
print('Wrote',CORPUS/'RESULTS.md')
