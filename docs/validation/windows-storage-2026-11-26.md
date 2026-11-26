# Windows 11 validation storage cleanup — November 26, 2026

The Windows 11 PC retained a November 23 validation run under
`C:\Users\david\Code\thor-validation\runs\20261123-480b053b`.
It occupied about 69.3 GiB of unique allocated file storage. The largest areas
were duplicated legacy parser caches and native compiler/build products across
eight source snapshots. These snapshots predated the November 25 cache/profile
limits; that earlier storage cleanup had not contacted this Windows PC.

The removal recovered **69.53 GiB** by the volume free-space measurement. C:
had **126.10 GiB free** afterward (56.57 GiB immediately before removal). The
earlier audit had measured 50.32 GiB free; other system activity changed free
space between that audit and the removal, so the entire difference is not
attributed to Thor. The retained archive is already included in these totals.

## Preservation

Before removing the run, its source files, original snapshot manifests,
additional probes/patches, fixtures and text/JSON test evidence were archived.
The preservation inventory records every omitted generated cache/native artifact
and every directory link without traversing its target. All eight source
inventories matched their saved hashes, and additional files were preserved too.

- Retained regular files: **125,015**.
- Retained link records: **458**.
- Omitted generated files: **112,700**.
- Compressed archive size: **1,240,487,207 bytes** (about 1.16 GiB).
- Archive SHA-256:
  `24afe7ac82376f8b0d908288363be1a03e31882f75ec8aa34a7bc3b6043b5326`.

The archive is `retired-run-evidence/20261123-480b053b.tar.gz` beside the Windows
validation checkout, with a second copy in the Mac checkout's ignored
`dist/windows-linux-install-2026-11-25/retired-run-evidence/` directory. It uses
standard tar/gzip; `THOR-RETIRED-RUN.json` contains the source/evidence checksums,
link targets and omitted-artifact inventory. Identical file contents are
deduplicated with tar hard links. Both archive copies and all archive members
were verified; the original files were rechecked before removal.
For editable recovery of multiple snapshots, restore deduplicated members as
independent files so editing one copy does not change another snapshot.

The archive is private local evidence, not part of the Git repository. The
installed Windows toolchain, current public source checkout, Linux VM and
unrelated applications are outside the removal scope.

## Persistent fixes

The maintained compiler-suite runner now prunes after failures and interruptions
as well as success. Results are saved before cleanup and include its outcome.
An unwritable report does not skip cleanup; cleanup failure cannot report overall
success or replace an original interruption.

The Windows stage runner now applies maintenance before and after each stage,
including failure/interruption. Source ZIP snapshots use their source manifest
for deletion protection without requiring Git. Generated Windows executables
and PDBs in the result directory are disposable; reports and fixtures remain,
including text that quotes a native binary signature. Cleanup does not traverse
directory links. Cargo cleanup explicitly selects the inspected target directory
instead of inheriting an unrelated `CARGO_TARGET_DIR`.

The existing shared compiler cache remains bounded at 512 MiB per root;
development/test profiles avoid incremental histories, oversized debug histories
are pruned through Cargo's locking, and installer upgrades retain the active and
previous unmodified toolchains. Candidate workspaces must copy source rather than
build/cache histories. These controls do not impose a quota on explicitly saved
source archives, user projects or unrelated toolchains.

## Regression checks

- Windows 11: **26** targeted cleanup/runner checks passed in normal Python and
  with `-O`; their disposable fixture workspace was removed.
- macOS: the same **26** checks passed in both modes. The additional archive
  group brought the ordinary run to **36 tests**, with two Windows-only cases
  skipped on macOS.
- The maintained native test runner exercised the cleanup cases successfully.
- Assertion hygiene: **7 passed**.
- Post-cleanup Doctor passed installed-file checksums, checked compile-time and
  runtime execution, unchecked release execution, PATH, TLS hover/completion,
  OLS hover/completion, and the recorded editor extension inventory.

Doctor ran in the logged-in normal-user desktop context, matching VS Code and
the per-user installation. An initial elevated SSH probe could read the runtime
sources directly but failed to traverse their junctions with Windows error 448.
The normal-user probe passed with the same files and links. This matches
Windows' [junction trust protection](https://www.microsoft.com/en-us/msrc/blog/2025/06/redirectionguard-mitigating-unsafe-junction-traversal-in-windows);
no security settings or runtime sources were changed. Interactive rendering and
live source breakpoints were not rerun for this storage-only task.

The regression cases exercise failure, cancellation, full-disk/report errors,
cleanup errors, source protection, invalid inventories, native path separators,
directory aliases, preserved reports and Cargo target-directory overrides.
No compiler/runtime implementation or user-program code generation changed.
