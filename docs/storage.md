# Storage limits and development retention

Thor's source, tests and documentation are small. Persistent data is disposable
acceleration, not an expanding archive of every compiler or project revision.

## Compiler cache

The default parser, compile-time evaluation and memory-vet cache is shared across
projects:

| Platform | Default root |
| --- | --- |
| macOS | `~/Library/Caches/Thor` |
| Linux | `$XDG_CACHE_HOME/thor`, or `~/.cache/thor` |
| Windows | `%LOCALAPPDATA%/Thor/cache` |

`THOR_CACHE_DIR` changes that shared root. `--cache-dir PATH` selects a different
root for project records; SDK syntax continues to use the shared root. Explicit
roots have independent budgets, so scripts should reuse a root instead of
creating an enduring cache directory for every run. Test-only roots should live
in an automatically removed temporary directory.

Managed records have a **512 MiB aggregate budget per root**. Sixteen independent
hash buckets each hold at most 32 MiB of charged data and 256 records. Charges
round up to 4 KiB; filesystem-specific allocation and small directory/lock metadata
are additional.
Eviction reserves space before publication, including for replacements. Entries
larger than a bucket are not cached. Approximate least-recently-used eviction
refreshes old access timestamps at most once per hour. It requires no full-cache
scan or growing index. A busy independent process, unavailable directory or
abandoned writer lock can cause a write to be skipped; compilation still works.

Records live under `store-v2/<bucket>/`. Parser, evaluation, vet and historical
compiler revisions compete for the same budget. Normalized parser records are
stored once, rather than also retaining a second JSON cache. Legacy packets can
still be read; new records use bounded storage. Cache identity still includes
source contents, parser identity, paths and relevant options. Project names are
omitted from parser identities only for files without `build-project-name` tags.

`--no-cache` bypasses these records. `--clear-cache` clears managed entries in the
selected project root, including recognized legacy records, while preserving
unrelated files and directory links. Clearing an explicitly separate project
root does not clear the independent shared SDK root.

Eviction, corruption and failed publication cause recomputation. Cache storage
does not alter ownership, constant eligibility, generated code or runtime flags.

## Developing the compiler

Rust development/test profiles retain source line information but disable
incremental object histories. This affects building Thor itself, not programs
built with Thor. The release optimization profile is unchanged.

```sh
python3 tools/build_compiler.py
python3 tools/test_compiler.py --match 'tests.test_rust_.*'
python3 tools/storage.py           # Report disposable artifacts
python3 tools/storage.py --apply   # Prune them
```

The build wrapper prunes before/after building. The maintained test runner prunes
before running and again after success, failure or interruption. Test results
are written before cleanup and updated with its outcome; failed runs retain
source and diagnostic reports rather than obsolete compiler objects. An
unwritable result file does not skip cleanup or produce a successful exit.
Development histories above 1.5 GiB are cleaned through Cargo's native locking;
the active release compiler remains available. `--reset-builds` explicitly also
discards smaller debug histories. Direct Cargo commands remain supported, but
arbitrarily many manually selected profiles/toolchains are outside this policy.

Pruning is limited to enumerated generated locations. It protects tracked files,
does not traverse linked trees, retains integration reports/source evidence,
removes recognized generated integration binaries and caches, and keeps two
distribution archives per platform. Old release-copy directories and temporary
publication-install clones are disposable. Current source, VM disks, editor
settings, ordinary project executables and unrelated toolchains are preserved.
The root frontend executable cache retains recent builds and four older builds;
the standard maintenance commands discard older copies after seven days.

### Windows validation snapshots

`tools/windows_validation.py` applies the same maintenance before and after each
stage, including failed and interrupted stages. Its JSON records include the
maintenance outcome. Cleanup failures cannot replace the original test failure
or leave an overall successful stage verdict. Native executables and PDB files
under `windows-results` are disposable; source fixtures, logs and JSON reports
remain. Active release compiler binaries outside that result directory remain.

Source ZIP snapshots do not need Git metadata for maintenance: their
`snapshot-manifest.json` protects source paths, in addition to Git-tracked files
when Git metadata is present. Invalid inventories and paths outside the snapshot
abort cleanup. Tracked/source-inventoried files are protected even when they have
generated-looking names. Directory links are not traversed.

Create new candidates in temporary workspaces, copying source rather than
`target`, caches, `node_modules`, installed SDKs or prior run directories. Keep
source edits and test reports outside those temporary workspaces before their
final removal. The stage runner keeps sources in place; it does not automatically
retire whole snapshot directories. Old manually retained snapshots from before
these controls require a separate, verified source/evidence archive before
removal. Bounded caches do not retroactively remove those independent copies.
The [Windows storage validation report](validation/windows-storage-2026-11-26.md)
records a verified retirement of the older run snapshots and the regression
checks for failure/interruption cleanup.

## Installed toolchains

Successful installer upgrades retain the active toolchain and one previous
activation for rollback. Older distributions are removed only when their
inventoried files are unchanged and extra contents are recognized generated
frontends or runtime views. Modified files, unknown additions, unsafe links and
unverifiable directories are preserved with a notice. Deliberately preserving
user changes can exceed the normal installation budget.

The compiler-development checkout, required Odin/LLVM and Rust tools, current
builds and bounded cache should normally fit below 10 GiB. This is a working-set
target, not a filesystem quota on user projects or custom cache roots. A second
Docker Linux development environment is unnecessary when an existing Linux VM
is available; VM disks and software inside that VM have their own storage use.

## Integration replay workspaces

Historical converted/vetted copies, memory-vet snapshots, copied repro trees and
side-by-side integration toolchains can be compressed while keeping canonical
upstream checkouts and test reports in place:

```sh
python3 tools/integration_storage.py restore  # Before another upstream replay
# Run the integration work; close its runners/editors afterward.
python3 tools/storage.py --apply             # Remove disposable output
python3 tools/integration_storage.py archive # Verify and compress working copies
python3 tools/integration_storage.py verify
```

The single archive is `integration-projects/archived-workspaces.tar.gz`. It stores
source bytes, ordinary permissions and file timestamps, deduplicating identical
content across working copies. Restoration creates independent files, preserving
symbolic links without following them. Both creation and restoration verify
checksums before publishing/removing workspace trees. Existing destination trees,
source changes during archiving, unexpected members and unsafe archive paths
abort the operation. Access/credential directories are outside the archive scope.
Restore all archived workspaces before replacing an existing archive with an
updated snapshot. Archiving is explicit; ordinary builds do not move source trees.
The same commands with `--area runtime` archive/restore captured runtime replay
inputs and generated-source snapshots under `thor_test/artifacts`, retaining
their reports. This compresses large raw screenshots without losing evidence.
