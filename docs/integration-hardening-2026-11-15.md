# Integration hardening and compilation work reuse

This pass follows checkpoint `0a962c0` and the pinned upstream integration
matrix. It covers the five follow-up areas of warm compilation, vet validation,
cold compile-time helper preparation, platform/debugger validation, and final
regressions and reporting bundles. Windows validation is deferred at the user's
request. The retained Windows portability corrections still require a final
native Windows run.

## Work reuse

Compile-time evaluation now creates one immutable source inventory per source
set and one interface/native-support inventory per scheduler. Exact request
keys retain full source identity. Prepared value keys retain their existing
transitive dependency granularity. External resource signatures are checked on
every reuse. New, removed, replaced, modified and retargeted resource files
continue to invalidate affected results. Content hashes remain the fallback
when native metadata changes. Unchanged Unix metadata includes device, inode,
mode, size, modification time and change time. Unsupported native signatures
keep the optional fast path disabled.

Source analysis inherited by helper projects now compares effective test mode
and semantic flags separately from output selection. Changing `-out`, keeping an
executable, allowing unused defines, selecting all native packages, or spelling
an already-active test build mode no longer repeats source discovery for the
same immutable source set. Defines, target selection, debug flags, effective
test mode and changed sources continue to invalidate inherited facts.

Regression coverage includes one inventory shared by multiple requests,
explicit output flags, semantic flag changes, independent runtime edits,
restored modification times, resource replacements, conditional resource-path
aliases and cache corruption. Existing prepared/scalar records with the old
key representation safely miss after the schema change.

An experiment that cached additional nested call plans increased planning time
and was removed before the final run. Independent source parsing and file
lowering retain the existing bounded worker implementation. Compile-time helper
execution retains dependency order. Further helper batching would need shared
failure/location handling and limits for concurrent native compiler memory.

## Correctness findings

- Read-only SOA columns now retain per-column provenance through `basic.zip`,
  forwarding, identity helpers and copied SOA headers. Writable companion
  columns retain their own privileges.
- Returning newly allocated nested reference storage preserves aliases between
  multiple results, copied slices and struct fields. Independent allocations
  retain independent provenance. Return-position aggregate literals receive
  their declared result context in both implementations.
- Generic indexed multiple assignment containing a skipping switch arm keeps
  destination snapshots in their defining scope. Array, slice and map cases
  exercise both changed and skipped branches in checked and unchecked builds.
- Secondary generated bound-inference errors are suppressed alongside the
  existing normalization cascades. The primary source diagnostic remains.
- Caller arguments inserted into preserved native source now receive source-map
  annotations in the Python reference implementation. Owned checked SDK string
  results and ordinary borrowed views retain separate lifetimes.
- TLS adapts to both SDK JSON-writing APIs and preserves JSON escaping for
  quotes, control characters, Unicode and C strings. The native regression also
  runs with assertions and bounds/type assertions disabled.
- Statement stepping exits source-less native routines such as Linux libc's
  `__memset_zva64`. Explicit instruction stepping remains available. A redundant
  return-line breakpoint was removed from a stepping fixture that already
  verifies arrival at that line. A compile-time-breakpoint fixture now uses an
  executable assignment for its runtime breakpoint, since a constant-true
  assertion can emit no runtime instruction on newer SDK/compiler versions.
- The editor integration harness opens its demo workspace so indexing stays
  within the fixture. The cloned upstream repositories and generated SDK
  captures are outside that workspace. Bracket-navigation checks explicitly
  focus the active editor group. Breakpoint-verification fixtures configure
  their accepted and rejected breakpoints before starting execution, avoiding
  CodeLLDB's transient step stop during a live breakpoint reconfiguration.

Several regression fixtures also needed updated expectations for intentional
existing behavior. These include unlimited default compile-time execution,
optimization flags requiring a build action, resource-path alias caching,
package-relative generated files, source-preserving operator ranges, and work
avoidance counters that now report fewer queries.

On Unix, Rust compiler cancellation also covers frontend version/root queries,
frontend bootstrap compilation and collection-root queries through the existing managed
process runner. Cancelled commands release their subprocesses and frontend work
directories. Regression wrappers create a real child process and verify cleanup
during metadata queries, frontend compilation and compile-time helper builds.

## Portability corrections retained for later Windows validation

Canonical filesystem identities now use native compiler-compatible Windows
path spelling. Ordinary drive and UNC paths retain Unicode components while
avoiding the verbatim prefix added by filesystem canonicalization. Generated
collection imports use forward slashes. The launcher preserves the complete
native process exit status. Local fixture paths select `.exe` on Windows and
allocation-tracker-specific assertions follow supported hosts.

## Upstream reporting bundles

The reporting index is [upstream/README.md](../upstream/README.md).

- The Tensor substitution checker assertion has a standalone 86-line native
  reproducer, upstream license and single-worker LLDB backtrace. Source errors
  in the reduced input are intentional triggers. The compiler should report
  them and exit normally with failure.
- The file-private generic linker collision has a copied-SDK reproduction
  script, one changed native file, support sources, license, original linker
  output and a verified successful single-module control. It only builds the
  test executables.
- The scoped native-vet parsing failure has its three-line native input,
  original recurrence counts, serial control and debugger capture command.
  Its underlying concurrency cause remains unconfirmed.

The existing type-name cache race and anonymous `#branch_location` bundles
remain available. External issue submission is deferred.

## Validation record

The complete Mac Python sweep ran 3,632 tests across 213 modules with four
platform/SDK skips. It exposed the early cancellation cleanup defect described
above and one recurrence of the already-recorded native Odin code-generation
race. The affected 24-test compile-time eligibility module passed on rerun.
Six additional fresh checked/unchecked comparisons also passed. The crash
summary and register signature are retained in the upstream race bundle.
The final 62-test targeted pass succeeded with one Windows-only skip, covering
the cancellation correction, cache reuse, CLI, installation, live TLS and
assertion hygiene. All 45 Mac native debugger tests also passed after fixture
corrections. The two additional cancellation fixtures bring the current full
Python inventory to 3,634 tests.

The Mac Rust unit suite passed 87 tests. Native TLS passed 1,151 tests on the
installed October SDK. The JSON escaping regression also passed with assertions,
bounds checks and type assertions disabled. The editor unit suite passed 142
tests and the isolated VS Code integration suite passed 71 checks.

Local Ubuntu ARM64 validation used Odin revision `9a69916`, CodeLLDB 1.12.3,
Rust 1.100 and Node 22.14.0. Its final core/lifecycle selection completed
159 tests successfully, including 15 host-specific allocation-tracker skips. All 45 native debugger tests,
87 Rust unit tests, 1,151 native TLS tests and 142 editor unit tests passed.
The JSON escaping regression also passed in the unchecked configuration.
Three fresh Linux breakpoint-verification controls passed after correcting
fixture setup. Node 18's older mock-timer API and missing editor dependencies
were resolved by using the pinned Node toolchain and the lockfile install.

Windows testing remains deferred. The earlier incomplete Windows runs remain
separate evidence and establish no final Windows validation result.

## Timing record and remaining cost

The Skald test build uses the same corrected source, native flags and 108 test
cases as the original cache benchmark. The final executable was rebuilt after
the cancellation correction. The three warm samples took 20.260, 20.446 and
20.148 seconds. Every sample matched all baseline test names and outcomes.

| Workload | Recorded baseline | This pass |
| --- | --- | --- |
| Cold corrected Skald test build | 171.735 s | 104.225 s |
| Warm corrected Skald test build | 28.337 s | 20.260 s median |
| Source scans across cold helper preparation | 32,256 | 384 |
| Reused helper source-analysis snapshots | 0 | 83 |
| First sampled Skald vet validation | 511.727 s | 337.230 s |
| Repeated sampled Skald vet validation | — | 91.764 s |

The cold build still prepares 83 helper requests and compiles 52 native helpers.
The warm builds prepare and compile zero helpers. Each evaluation session builds
one source inventory. Its warm inventory receives 82 reuse hits. The cold pass
performs one source inspection, retaining semantic and resource invalidation.

Vet samples precede the final metadata-query cancellation correction. They use
three correction-validation passes. The complete quiet build profiles and
per-test comparisons are in `integration-projects/results/hardening-skald-*`.
The earlier comparison is in `verified-skald-cache-summary.json`.

The large project's warm profile spends about 9.8 seconds resolving call plans,
1.6 seconds constructing analysis contexts, and 2.6 seconds in final native
compilation. Further work toward sub-second lowering requires reducing those
analysis costs and repeated context processing. Cold helper batching also
requires preserving dependency order, failure attribution and bounded memory.
The existing upstream native compiler issues remain tracked in their bundles.
