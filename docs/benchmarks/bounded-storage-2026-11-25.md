# Bounded storage validation — 2026-11-25

The native compiler now uses a shared, bounded disk cache. Generated development
history and duplicate integration workspaces were reduced without changing Odin
generation, language semantics, ownership rules or user-program build flags.

## Measured storage

Before cleanup the checkout's parser caches occupied approximately 190 GiB,
Rust outputs 64 GiB, and Docker's virtual disk 49 GiB. An additional 14 GiB of
integration replay copies, historical toolchains and benchmark output remained.

The measured development stack after cleanup was **8.77 GiB**, including this
checkout, its compressed replay sources/evidence, shared cache, current Odin
checkout, required LLVM installation, Rust and Cargo dependencies. The checkout
itself is approximately **4.8 GiB**. Docker has no containers, images, volumes or
build-cache records remaining; its sparse disk is approximately **343 MiB**.
The Fusion VM and unrelated installed toolchains were preserved.

Available space rose from approximately **73 GiB to 441 GiB**. These are observed
APFS volume measurements; directory totals and volume changes are not exact
equivalents because of filesystem accounting and concurrent system activity.

Persistent controls:

- 512 MiB of charged managed cache records per root, shared by default across
  projects; bounded entry count and automatic eviction of older records.
- A single normalized parser record instead of also writing a JSON duplicate.
- Shared SDK identities omit project names only when build-project-name tags
  cannot depend on them.
- Development/test Rust builds disable incremental object histories, retaining
  line tables. Standard build/test maintenance prunes obsolete artifacts.
- Successful installer upgrades retain active/previous unmodified toolchains;
  user edits, unknown additions and unsafe links prevent deletion.
- Inactive replay workspaces and raw screenshots are compressed with verified
  restoration. Source archives are explicit, not an automatic source-tree move.
- Optional offline Rust HTML documentation was removed locally. The minimal
  Rustup profile avoids reinstalling it for new toolchains; Rustdoc, Rust source,
  formatting/lint tools and complete redistribution notices remain available.
- The unused LLVM 19 installation from the earlier integration work was removed
  after checking installed dependents and active tool paths. Archived Odin
  toolchains use bundled LLVM libraries; the active Odin and LLVM stayed intact.

See [storage controls](../storage.md) for paths and archive/restore commands.

## Validation

| Check | Result |
| --- | --- |
| Final Mac Rust library units | 130 passed |
| Final Linux Rust library units | 130 passed |
| Linux cache/default-root/ownership/vet-cache integration tests | 53 passed |
| Linux native installer tests, including ARM64 OLS setup | 35 passed |
| Mac installer and cleanup tests | 39 run, 7 Linux-only cases skipped |
| Cache, CLI and assertion-hygiene group | 79 passed |
| Final native cache, CLI, installation and TLS group | 71 passed |
| Final broader compiler/request/lifetime/hygiene/tool regression group | 118 passed |
| Additional developer-tool/shared-analysis/default-cache group | 20 passed |
| VS Code Node tests | 186 passed |
| Archive/cleanup tests, ordinary and Python `-O` | 11 passed in each mode |
| Final assertion hygiene | 7 passed |
| Frozen Python source identity | Unchanged |
| Distribution license-notice preparation after removing HTML docs | Passed |

Groups overlap; their counts are not a unique-test total. Windows was not
contacted for this storage round. Mac and Linux compiler/cache sources matched
before retiring the Linux Docker builder.

The actual replay archive was restored and re-archived: **71,162 integration
entries** and **5,836 runtime replay entries** passed complete inventory/content
verification. Unit cases also cover changed sources, malformed archives,
existing destinations, links, executable permissions, failed verification and
independent restored files after content deduplication.

`poker_pmap/poker.thor` and `hilo_test/hilo_test.thor` built successfully in checked
mode and with `-o:speed -disable-assert -no-bounds-check -no-type-assert`. Native
runtime fixtures use explicit checks that remain active when assertions are
disabled, including cached results, scoped allocations and comprehensions.

## Performance and emitted code

The baseline is the immutable compiler extracted from the published v0.41.6
macOS distribution. The final Vulkan measurements used immutable candidate copies. The smaller
workloads measured the same storage implementation before the final Windows
LRU-handle/help-text adjustments; neither adjustment changes Odin generation.

| Workload | Before | After |
| --- | ---: | ---: |
| Small warm transpile | 0.037 s | 0.036 s |
| 1,000-procedure call graph, warm transpile | 0.164 s | 0.163 s |
| Math SDK reader, warm transpile | 0.047 s | 0.046 s |
| Formatting/I/O, warm transpile | 0.047 s | 0.047 s |
| Basic I/O, warm transpile | 0.168 s | 0.170 s |
| Vulkan SDK, warm transpile | 0.165 s | 0.163 s |

Controlled alternating runs with genuinely empty SDK caches gave Vulkan medians
of **0.658 → 0.634 s cold** and **0.175 → 0.176 s warm**. Warm runs spawned no
parser processes. This removes the apparent first-run difference in a preliminary
sample taken while other validation/cleanup was active.

The Vulkan generated Odin tree was byte-identical across both binaries and all
runs. The five smaller workloads also retained identical generated code under
the benchmark's existing blank-line/proven-unused-intrinsics normalization,
including no_alloc variants. No user-program optimization settings changed.
The storage work therefore adds no runtime instructions or runtime allocation.

Build samples and profiles are summarized in the
[machine-readable results](bounded-storage-2026-11-25.json). Build-time samples
are small and should be treated as a regression check, not a speedup claim.

Docker reclamation followed its [documented disk-space procedure](https://docs.docker.com/desktop/troubleshoot-and-support/faqs/macfaqs/).
Only the Thor validation containers/images and their identified build-cache
records were removed; unrelated Docker/VM settings were preserved.
