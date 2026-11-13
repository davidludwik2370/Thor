# AST storage, dependency indexes and signature layouts

Baseline: `8be7387`. This pass changes the Rust transpiler's implementation.
The Python reference, language rules and Odin backend remain unchanged.

## Project syntax cache

The compact normalized syntax cache now accepts project Thor sources as well
as shared Odin/SDK sources. A warm hit restores the normalized AST directly,
including extension features and source locations. Project syntax remains in
the project's cache; common libraries use the shared toolchain parser cache.
Entry keys, checksums, bounded decoding, atomic writes and raw-parser fallback
are the same as for SDK syntax. Exact source and overlay bytes still determine
the key. `--no-cache` bypasses both layers, and `--clear-cache` now removes both
managed raw-parser and normalized entries while leaving unrelated files alone.

## Packed AST storage

Child lists, span buckets, role tables and named node lists use contiguous,
source-owned vectors. Nodes store shared ranges into those vectors rather than
allocating each small list separately. Child and span-bucket order is preserved.
The compact cache reader constructs these vectors directly; the raw frontend
path packs them after normalization. AST protocol output is unchanged.

The range container uses ordinary reference-counted ownership and detaches a
visible range before mutation. Cloning or updating a field table cannot change
another node's table. Cleanup remains deterministic and serial. This does not
add unsafe code, a custom allocator, or process-exit leaks.

## Dependency index extension

When discovery appends files from new namespaces, the resolver extends its
syntax and symbol indexes for those files. Existing symbol IDs and lexical
scope entries remain stable. Multiple newly appended files in the same new
namespace are handled together.

Exact source-arena identity, source views and collection bindings must match
for the existing prefix. Adding a file to an existing namespace, replacing
syntax, or changing a source view forces a complete rebuild. Completed
type/name/evaluation answers are not carried into an enlarged source graph.
Conditional declarations and `using` projections are resolved against the
current graph and flags. This is reuse within a command, not a persistent
whole-project output cache.

## Signature layouts

Procedure and polymorphic-struct signatures have lazily constructed layouts
for parameter/result fields, first matching parameter names, default argument
references, variadic positions and generic constant parameter positions.
Layouts are shared with the syntax index, including dependency extensions.
Unused SDK signatures do not allocate a completed layout.

Only syntax is retained. Actual argument types, constant values, substitutions,
overload scores, purity and slice effects still use the current resolver and
call-site context. Invalid named arguments keep Odin's diagnostic and the
original Thor call location. Missing parameter lists remain distinct from
empty parameter lists.

Separate warm profiles confirm the intended index reuse:

| Project | Complete index builds before | Complete builds after | Extensions after |
| --- | ---: | ---: | ---: |
| Poker | 3 | 1 | 2 |
| UI demo | 3 | 1 | 2 |
| Othello AI | 4 | 1 | 3 |

The total resolver-construction counts remain 5, 4 and 6 respectively: semantic
work still runs where required. All parser source requests in these warm final
profiles hit normalized syntax. These profiles are for counters and attribution;
their timings are not the quiet latency comparison below.

## Measurements and validation

Seven alternating warm release samples per engine, with four workers:

| Workload | Baseline | Final | Reduction |
| --- | ---: | ---: | ---: |
| General demo | 0.680 s | 0.592 s | 13% |
| Hilo | 0.218 s | 0.193 s | 11% |
| Poker | 0.302 s | 0.253 s | 16% |
| UI demo | 0.568 s | 0.493 s | 13% |
| Circles | 0.168 s | 0.146 s | 13% |
| Othello AI | 0.694 s | 0.587 s | 15% |
| 26-file workload | 0.178 s | 0.168 s | 6% |
| 200 SDK-calling procedures | 0.121 s | 0.109 s | 10% |
| 300 scalar procedures | 0.058 s | 0.050 s | 15% |
| Long alias chain | 0.033 s | 0.032 s | 1% |

These measure the complete transpile command, including cleanup and any
compile-time helper work, but excluding the final Odin backend. Each engine
has a separate project cache; common parser caches remain warm. No competing
test/build jobs or opt-in profiling ran during the final timing pass. Synthetic
fixtures were executed on both engines with assertions, bounds checks and type
assertions disabled before timing. Real projects were copied and checked after
timing. Source hashes are included in the structured evidence.

The isolated signature-layout comparison was within timing noise on the three
larger project samples: Poker 0.2361→0.2371 s, UI 0.4969→0.4940 s, Othello AI
0.5878→0.5869 s. It removes repeated syntax flattening and named-parameter
searches; no separate end-to-end speedup is claimed for that stage on these
projects. The long-alias result is likewise effectively unchanged. The combined
gains come primarily from compact syntax storage and index reuse.

The 366 focused compiler methods passed, including 11 new methods for this
pass. Checked/debug and unchecked native builds are covered. Rust passed 74 unit
tests in both debug and release; four are new. All-target Clippy passed with
warnings denied. TLS passed 37 methods, vet passed 16, live CodeLLDB passed 43,
and assertion hygiene passed seven checks.

The copied corpus matched all 204 previous build outcomes: 190 successful
builds and the same 14 expected-invalid outcomes, with zero status changes or
timeouts. Four projects' own suites passed in Odin, renamed Thor and idiomatic
Thor (12 runs). Original-file verification reported no changes. These are
focused regression groups, not a full repository-suite claim.

Uncached compile-time computation can still take longer than these warm samples.
Previously documented contextual `#run` array-inference and unoptimized Rust
development-executable stack-depth limitations are outside this pass; see the
[previous investigation](sdk-resolver-efficiency-2026-11.md#investigation-notes).
The shipping release compiler's deep-member stress cases and generated native
debug builds, including live breakpoints, passed.

[Structured evidence](ast-index-efficiency-results.json) records raw timing
samples, source/executable hashes, intermediate checkpoints, index counters and
validation scope. The implementation checkpoint is `a42c856`.
