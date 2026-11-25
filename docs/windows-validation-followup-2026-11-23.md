# Windows validation follow-up: 2026-11-23

**Later debugger investigation:** [one-to-one native controls](windows-debugger-pairs-2026-11-23.md)
reproduce all nine original failures in plain Odin. Two Thor adapter fixes now
resolve one original test and an additional library-stepping defect; eight
original Windows limitations remain. The earlier counts below record this
validation run before those fixes.

This continues [the initial report](windows-validation-2026-11-23.md). Its earlier
SDK-effect and file-identity capability gaps have now been implemented and tested.
The initial report remains a historical record, rather than a current checklist.

Work ran natively on Windows 11 x64 through the existing pinned SSH connection.
The installed Odin revision was `9a69916`, Rust/MSVC 1.100.0, Python 3.15.0,
Node 24.22.3, VS Code and CodeLLDB 1.12.3. No WSL substitution was used. No
account, firewall, SSH or machine-wide environment changes were needed. Compiler
builds used two Cargo workers; native compiler controls used one worker where
needed to isolate Odin's own concurrent compilation failures.

## Coverage and how failures were reconciled

The maintained compiler selection initially ran 4,825 cases. Follow-up gates
reran all initially failed IDs and the affected modules: 876 cases, then 131,
then the exact remaining CRLF/URI/dependency regressions. These are overlapping
runs, not additive counts of unique passing tests. Archived Python comparisons,
explicit platform skips and unavailable upstream baselines remain visible in
the raw results. This was not a single final clean rerun of every test.

The final relocation/runtime regression gate ran 190 Windows cases: 185 passed
and five explicit POSIX installation cases were skipped. It includes mapping,
compile-time execution, permissions, allocation contracts, comprehensions, native
installation/packaging, TLS callback support and assertion hygiene. The Windows
Rust unit suite passed all 122 cases; the Mac Rust unit suite passed all 123.
The matching Mac relocation/runtime selection passed all 184 cases.
A fresh Linux ARM64 build passed all 123 Rust units and six focused shared-fix
regressions, including checked/unchecked relocated callbacks, `#run`, runtime
callback fallback and allocation-free I/O. That follow-up used reviewed SDK
`8169255`; an initial run with older SDK `537fac56` correctly rejected the source
profile. Those failed setup records are retained. The full earlier Linux project
matrix was not rerun in this Windows follow-up. No container OOM occurred.

Native Windows release compiler, frontend, TLS, installed-toolchain relocation,
console/GUI executable resources, native DLL packaging and migration validation
were exercised. Checked builds and optimized unchecked builds were used;
unchecked verdicts use explicit checks or external output/exit comparisons.
The frozen Python source fingerprint remains unchanged.

The native TLS suite passed 1,175 tests both checked and unchecked. Live LSP
transport exercised 172 cases: its final remaining failure was a test-client
comparison of equivalent Windows drive URIs; that exact case passed after fixing
the client. All other 171 passed. All 30 rename checks passed, including unsaved
buffers and native/editor separator aliases. The full live VS Code workflow,
hover-size checks, quote/brace closing and standalone terminal/cwd workflow passed.
The extension's Node tests passed 162 with one explicit POSIX-only skip.

Three independent real VS Code/CodeLLDB workflows passed after the Windows drive-key
fix: run, save, original-source breakpoint, stepping through an interpolated
pure call, watch after the native parameter prologue, step out, restart and a
nested project's working directory. An earlier stepping timeout is retained in
the logs; these successful repeats do not prove that no intermittent timeout can
occur. The packaged extension was installed in a separate review profile.

## Compiler and TLS corrections

- Native file-identity metadata supports evaluation caching. Byte-dependent inputs
  are still hashed; file IDs/timestamps alone never certify unchanged content.
- The reviewed Windows SDK effect profile covers fixed-buffer I/O, formatting,
  RNG and selected platform boundaries, with source verification and CRLF
  normalization. Allocating operations and unknown/custom calls remain checked.
- Windows path normalization follows native `..` semantics before traversing
  reparse points; source spelling remains available for diagnostics.
- Native executable suffixes are used consistently for generated helpers,
  migration probes, warning checks and publishing inspection tools.
- The compiler's own stack reserve supports deep type analysis; generated
  applications do not receive a changed stack setting.
- TLS merges check diagnostics under a consistent drive URI, preserves unsaved
  editor aliases and resolves rename paths with either separator.
- The debugger adapter compares source-map drive letters consistently. It also
  keeps valid backend disassembly/source references intact.
- A moved compiler now identifies its runtime through the compiler-owned support
  collection, rather than a Cargo build-machine path. The cached identity is
  shared by callback specialization, purity, read-only contracts, logical number
  types, sets and compile-time specialization. User collection overrides do not
  acquire those contracts. Relocation regressions cover known/polymorphic/runtime
  callbacks, `#run`, big numbers, sets, read-only views and override rejection.
- Portable test mocks and fixtures now preserve exact source bytes, native
  executable naming, actual host ABI expectations and logical drive URI identity.
  These fixture changes do not weaken the runtime result checks.

These corrections add no per-operation runtime instrumentation to user programs.

## Upstream integration

The 22 pinned repositories supplied 136 usable native command baselines and 2,097
baseline test cases. 135 commands matched after migration and after vet rewriting.
All 98 usable Odin examples matched. A failed or unavailable native baseline is
not counted as Thor success.

One default-configuration mismatch remains in Tina: its Windows VEH recovery test
passes in the original default build and fails in the migrated build (369 of 370
pass). Native disassembly shows different inlining of its trap-save wrapper.
Both versions fail at `-o:none` and pass at `-o:speed`. An independent plain-Odin
assembly-wrapper reproduction is in
[`upstream/odin-trap-wrapper-inline`](../upstream/odin-trap-wrapper-inline/README.md).
This identifies an inlining-sensitive native boundary, not a fully resolved
compiler-parity result; the exact reason for the inliner threshold difference
has not been isolated. Neither Odin nor the upstream project was patched.

LISP's four ordinary commands matched. Its forced test-mode vet command fails
with the same empty-test-runner removed-section error in plain Odin, so this is
not claimed as a successful vet rewrite. The selected Odin's static-library
linking control also fails natively for missing CRT/startup symbols; the migration
fixture compares the native result instead of calling that a Thor pass.

## Build and runtime measurements

These are five-sample medians on the Windows x64 host, with one native compiler
worker and no other Windows validation job running. Frontend installation warm-up
is excluded. Build and runtime output/exit checks passed for each configuration.
These small, controlled workloads do not establish a general large-project bound.

| Workload | Native Odin build | Thor build | Thor transpile |
| --- | --- | --- | --- |
| plain, checked | 1.180 s | 1.214 s | 38.8 ms |
| hilo, checked | 1.209 s | 1.257 s | 40.7 ms |
| plain, unchecked | 1.051 s | 1.103 s | 38.6 ms |
| hilo, unchecked | 1.069 s | 1.125 s | 40.9 ms |

Mapping measurements use four workers and the same deterministic simulation
kernel, with validation and result deletion outside the timer. Result allocation
is inside the timer. Each job's output and final checksums match. Values below
are milliseconds per mapping call; the raw records also include pools and reuse.

| Workload | Odin manual threads | Thor static pmap | Thor runtime callback | Thor manual threads |
| --- | --- | --- | --- | --- |
| hilo-balanced, checked | 187.2199 | 187.5285 | 187.3949 | 187.0160 |
| cheap-startup, checked | 0.0858 | 0.0819 | 0.0794 | 0.0811 |
| cheap-many, checked | 0.5693 | 0.5343 | 0.7724 | 0.8083 |
| hilo-balanced, unchecked | 186.0555 | 168.5083 | 168.2278 | 170.6840 |
| cheap-startup, unchecked | 0.0851 | 0.0837 | 0.0819 | 0.0827 |
| cheap-many, unchecked | 0.4424 | 0.4376 | 0.5810 | 0.5470 |

The checked Hilo pmap result is within 0.2% of native manual threads. The unchecked
run favors Thor, including its manual control; the cause of that difference was
not isolated, so it is not attributed to the relocation fix. Static pmap is also
competitive on the cheap-work controls. Runtime callbacks cost more for trivial
jobs, consistent with the remaining indirect callback calls.

The Thor-transpiled manual loop is measurably slower in the million-cheap-job
control (about 42% checked, 24% unchecked). Its indexing/lowering differs from
the native loop; the exact performance cause was not isolated. These results
therefore do not support a blanket zero-overhead claim for all Thor-generated
loops. No such slowdown was observed in the static pmap path measured here.
A persistent pool is much cheaper when amortizing startup over tiny jobs; pmap
continues to create/join its workers according to its existing API contract.

## Remaining debugger limitations

Nine cases in the 52-case full DAP selection remain failures. They concern input
values at the first source statement (scalar, aggregate, generic, switch/helper,
variadic and fallback paths), an unavailable static rodata local, a deferred
return breakpoint without a native line location, and missing PDB column precision.
Independent plain-Odin controls reproduce those categories. Runtime result checks
pass. The adapter does not invent watch values, source columns or executable
lines, and the compiler does not insert runtime work to conceal them.

Windows ARM64 was not available. Native x64 validation does not establish ARM64
support. Automatic allocation tracking/ASan remains a deliberate unsupported
capability on Windows/Linux; their ordinary allocation and `no_alloc` checks are
separate and were tested.

## Manual and visual review

On the PC desktop, open **Thor Windows Review.cmd** and read **Thor Windows
Review.txt**. The launcher selects the tested compiler/TLS and the isolated
`review-profile` / `review-extensions` under the validation run. Trust the provided
workspace if VS Code prompts. The feature example already compiles and runs.
The normal VS Code profile and extensions remain unchanged.

1. Check Ctrl+R and Ctrl+Shift+R with real keyboard focus, unsaved edits and a
   terminal reused across project folders. Their command workflows passed
   automatically; physical key conflicts and focus still need a human check.
2. Inspect hover spacing, byte-size labels, read-only colors (including `using`
   fields inside fstrings), completion menus, whole-token squiggles and automatic
   braces/quotes with the normal theme, font scale and monitor DPI.
3. Inspect actual GUI windows, icons, taskbar behavior, focus, input and resize
   in representative applications. PE subsystems, resources, packaged executables
   and DLL loading passed; visual appearance is not an exit-code assertion.
4. Try the debugger interactively in the supplied project, bearing in mind the
   native limitations above. The nine failures need toolchain/debug-info work,
   rather than being tasks a manual click-through can certify as fixed.

## Evidence and reproduction

The active native installation is below:

```text
C:\Users\david\Code\thor-validation\runs\20261123-480b053b\candidate6
```

The reproducible stage runner is documented in
[Windows validation](windows-validation.md). Per-stage JSON records contain
source fingerprints, exact commands, durations and log paths. Failed attempts
remain alongside their corrected reruns. The final metadata and log archive
are retained under `integration-projects/results/windows-2026-11-23` on the Mac
and the Windows validation run. This report is written after the source snapshot;
its own prose and the later documentation-link updates are not part of that
tested-source fingerprint. Compiler, runtime, editor and test sources still match
the verified manifest.

Final tested-source identity: `dd7466a86b5ceb780eaf5a91a025c155ed34c9105c655822f0871f0104b38363` (1913 manifest entries).
Native compiler SHA-256: `af54d4523b57ea1432929af6d589beea6767a030db497c601a5b1921eb8f5f1b`.
The collected archive contains 322 individually verified files; archive SHA-256:
`ae08cab1823eb5b6dd7e7ff511f857bc1272a832831368cb7faecd13bb9fb33a`.
