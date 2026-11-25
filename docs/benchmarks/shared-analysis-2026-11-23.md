# Shared analysis and vet performance: 2026-11-23

This follows [unverified reporting](unverified-reporting-2026-11-23.md).
The maintained Rust compiler reuses more completed semantic queries, avoids
unnecessary metadata discovery and can reuse unchanged memory-check results.
Neither language rules nor generated runtime operations were changed.

## Changes

1. Fully resolved nested call/type queries can be retained when no recursion
   guard or missing import interrupted the query. The previous requirement for
   an entirely empty inference stack discarded many completed answers.
2. An additional bounded memo stores generic, ambiguous and empty call answers
   for the exact substitutions in one fixed source graph. Demand-tracking mode
   is part of its identity. These answers never enter the cache used across
   namespace discovery. Binding, evaluation and context changes invalidate them.
3. Completed slice-permission facts can survive independent native namespace
   additions. Source/namespace changes, flags, bindings, contexts, helper views
   and unfinished discovery retain the existing fallback. The SDK still needs
   three permission passes; required checks were not removed.
4. Vet's return-type discovery skips inactive compile-time branches, removes
   resolved calls from its retry worklist and stops revisiting void calls.
5. An optional memory-check cache reuses complete findings for unchanged inputs.
   It retains definite issues, uncertainty, related locations and original
   ranges. Reporting mode is applied afterward. It restores any imported
   metadata needed by subsequent convention checks.

The persistent cache deliberately stores the whole memory-check result rather
than serializing mutable ownership graphs or attempting fine-grained reuse
after an edit. Compiler/parser revisions, source contents, flags, targets,
compile-time values, package membership, import bindings and supported resources
guard reuse. Unsupported snapshots, overlays that differ from disk and corrupt
records fall back to fresh analysis. Native checking and convention correction
validation still run. `--no-cache` bypasses reuse; `--clear-cache` clears it.

The implementation revision is embedded at build time, covering Rust sources,
manifest/lockfile and build configuration. This avoids hashing the executable
on every command and prevents a running process from mistaking a replacement
executable on disk for its own implementation. A regression exercises atomic
replacement while the older compiler process is alive. Dependency versions
were not upgraded.

## Results

Measurements use macOS ARM64, sequential commands, one native worker,
`--comptime=required` and separate project caches. Main timing samples have no
profiler; profiles were collected separately. A fresh memory check removes only
its result memo, retaining parser caches. No applications or SDK tests are run
by the benchmark.

All **20 local projects** retain identical complete memory diagnostic packets
before, after a fresh check and after reuse: **99 findings at 74 locations**.
Full convention vet also retains identical complete diagnostic packets for all
six representative projects, including suggested corrections. All **62 local
generated Odin files** are byte-identical. User source hashes are unchanged.

Warm wall-time medians, three samples per configuration:

| Project | Build before | Build after | Vet before | Vet after, fresh memory check | Vet after, reused memory check |
| --- | ---: | ---: | ---: | ---: | ---: |
| hilo_test | 0.785 s | 0.808 s | 0.304 s | 0.306 s | 0.271 s |
| poker_pmap | 0.985 s | 0.934 s | 1.098 s | 0.995 s | 0.498 s |
| sudoku | 0.511 s | 0.512 s | 0.547 s | 0.525 s | 0.226 s |
| 15_puzzle | 0.909 s | 0.916 s | 0.601 s | 0.631 s | 0.489 s |
| bin_tree | 0.687 s | 0.689 s | 0.343 s | 0.374 s | 0.314 s |
| sketch | 0.650 s | 0.672 s | 0.320 s | 0.304 s | 0.270 s |

Vet here selects lifetime/leak checks. Full default vet includes convention
analysis and validation of proposed edits. Its separate single-sample runs were
1.800 → 1.039 seconds for poker_pmap and 0.857 → 0.478 seconds for sudoku when
reusing memory checks; those are illustrative observations, not medians.

Fresh checks do not universally improve: cache publication and additional memo
bookkeeping can add work on small projects. Five additional alternating pairs
measured fresh bin_tree at 0.334 → 0.343 seconds and 15_puzzle at
0.623 → 0.654 seconds. This tradeoff is recorded rather than hidden by reporting
only warm results. Ordinary local build times are largely similar in these
small samples.

## SDK-heavy case

The same pinned SDK core test tree uses `-file -all-packages -build-mode:test
--odin-compatible -thread-count:1`. Before/after/after/before order supplies two
samples per compiler and command. The test executable is built, not executed.

| Command | Before median | After median |
| --- | ---: | ---: |
| Build | 31.020 s | 28.341 s |
| Vet, fresh memory check | 31.908 s | 28.226 s |
| Vet, second run | 31.908 s | 28.290 s |

The small sample count and native compiler variation limit precision: these
results support a shared-work improvement, not an assertion that vet always
takes less time than building.

The SDK's memory-result cache conservatively bypasses reuse. Its source contains
resource expressions such as `#load(#file)` and `#load_directory(#directory)`
that the current resource inventory cannot enumerate. Both after profiles show
`vet.memory_cache_bypasses: 1`; the SDK speedup comes from shared analysis and
metadata improvements, not skipped memory checking.

Separate profiles measured build's slice-permission phase at **14.259 → 11.916
seconds**, and vet's extra return metadata at **2.277 → 0.863 seconds**. The
lifetime walk itself remains approximately **0.45 seconds**. It retains all
**67,054 operations**, **3,718 objects**, 83 summary proofs and 566 summary hits.
Fully closed call-plan hits increased from 3,820 to 15,645, with 31,925 additional
fixed-graph memo hits. Three permission passes and 4,247 body passes remain.

The SDK's **469 findings at 364 locations** have identical complete JSON packets
before and after. Its **154 generated Odin files** are also byte-identical,
bringing total compared output to **216 files**. There is no new runtime
instrumentation or cleanup in that output.

## Validation

Runs overlap; these counts are not added together:

* The 71-module broad run planned 1,256 cases. It executed 1,229 with no failed
  test assertions and one skip; two example harness setup failures prevented
  27 cases from running. The missing default `unverified` field was corrected
  in eight existing Rust examples. The affected 11 cached-execution and 16
  prepared-execution cases then passed. All release examples pass `cargo check`.
* Focused final cache/policy/query coverage passed **35/35**. Additional harness
  compilation and hygiene coverage passed **13/13**. An additional optional-cache
  I/O failure regression passed separately. These include three cases added
  after the broad suite was loaded. Combined coverage is 1,259 distinct cases,
  with one intentional skip, after the setup repairs. A separate native storage
  protocol comparison also passed as part of a **25/25** harness rerun.
* **113/113 Rust unit tests** and **65/65 live TLS tests** passed. TLS includes
  immutable highlighting, f-strings, parameter contracts, pmap and diagnostic
  recovery. The existing TLS binary needs no protocol change for this work.
* Invalidation cases cover changed source with restored mtime, native dependency
  edits, added files, flags/assertion elision, resource contents, symlink targets,
  overlays, corrupt/missing/cleared records and compiler executable replacement.
  Definite errors and unverified boundaries survive reuse and strict reporting.
* Checked and `-o:speed -disable-assert -no-bounds-check -no-type-assert` execution
  oracles use explicit checks. Unsafe fixtures are checked without execution.
  Assertion hygiene, frozen Python-reference verification and `git diff --check`
  pass. No Windows or Linux validation is claimed in this round.

[Raw samples, profiles, diagnostic packets and source hashes](shared-analysis-2026-11-23.json)
