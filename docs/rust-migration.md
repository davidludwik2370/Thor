# Rust migration: independent native compiler

The Python compiler is still the supported default. The Rust port is **incomplete**;
this checkpoint does not complete stages 1–3 or establish whole-language parity.
Do not switch TLS or project build commands to it yet.

The reference baseline is commit `ca2df99`, also tagged
`rust-migration-baseline-2026-11-08`. The Python implementation remains in
`thorlang/` and the `thor` launcher. The independent Rust crate is in `rust/`;
`thor-rust` builds its release binary and launches it. It never runs the Python
compiler, including for unsupported syntax.

## Build and compare

Requirements: Rust 1.85+, Cargo, and the existing Odin installation. The first
Cargo build may need to download the dependencies pinned by `rust/Cargo.lock`.
Subsequent launcher builds use the local dependency cache.

```sh
cargo build --release --locked --manifest-path rust/Cargo.toml
./thor run examples/rust_migration
./thor-rust run examples/rust_migration
./thor-rust --capabilities

python3 -m unittest discover -s tests -p 'test_rust_*.py'
python3 -m unittest tests.test_compiler_comparison
cargo test --locked --manifest-path rust/Cargo.toml
cargo test --release --locked --manifest-path rust/Cargo.toml
cargo clippy --all-targets --manifest-path rust/Cargo.toml -- -D warnings
python3 -m unittest tests.test_assertion_hygiene
```

`rust/target/release/thor-rust` runs directly without Cargo's startup check. Use
that binary when measuring the compiler itself. No whole-project build cache or
parallel lowering has been added. Threads only deliver/drain the native parser's
pipes; they do not run compiler analyses concurrently.

Both compilers accept separate `--output-dir` and `--cache-dir` paths. Rust's
default generated directory is `.thor-rust-build/<source>`, so comparisons do not
replace Python's `.thor-build` files. Build executables retain the current-directory
folder name convention; use explicit `-out:` names when retaining both binaries.
Parser cache entries use a separate checksummed `rust-parse-v1` namespace.
Unmodified native runtime libraries stay in the main toolchain directory.

## Implemented infrastructure

- The existing Odin frontend is built and invoked directly from Rust. Its source
  and compiler fingerprint matches the Python bootstrap algorithm. It supplies
  the common parser, rather than introducing a second Odin grammar.
- Arena node IDs, interned names, compact syntax tags, feature metadata, lexical
  bindings, and type shapes support the initial lowering passes. Synthetic
  comprehension roles and same-span AST wrappers retain their parser identities.
  Span indexes and Euler subtree intervals avoid repeated whole-file/ancestor
  scans. Normal compilation retains one node-metadata copy; `ast` reconstructs
  the normalized parser protocol on demand.
- Structured emission fragments produce the final text and UTF-8 source-map
  segments in one traversal. There are no UUID marker strings or regex decoding.
  Version 1 maps are readable by the Python source-map consumer. This is not yet
  full debugger-region or TLS semantic-information parity.
- Independent file/package discovery, target/test parser arguments, mixed source
  packages, collections, atomic generated files, manifest ownership, native Odin
  invocation, program arguments, and original-source diagnostic locations exist.

## Initial lowering coverage

The differential tests exercise ordinary Odin, selective/blank imports, contextual
and nested bracket arrays, sequence membership, type-list constraints, constant
concatenation, literal-count string repetition, untyped strings passed to cstring
parameters, runtime numeric power, inline immutable copies and writes, automatic
using support, and simple `or_panic`.

The next checkpoint adds runtime signed/unsigned sequence bounds, generic
array/slice/map assignment dispatch, typed numeric/index loop bindings, native
multiple-result switches, assignment-only `break`, enum coverage and `-> !` arms,
lazy compile switches and native global multiple-result initialization. Named Set
literals, operations, mutation, iteration and cleanup also have differential
coverage. Anonymous Set ownership remains incomplete and detected cases are
rejected rather than silently leaking. The existing acceptance fixtures are reused
in `tests/test_rust_reference.py`; unsupported results fail that gate.

Interpolation supports the existing suffix grammar and dynamic widths, named
ownership, `f!`, and expression-scoped anonymous arguments. Tracking-allocator tests
check checked and assertion/bounds/type-assert-disabled release builds. Composite
anonymous-string ownership paths that have not been ported are rejected explicitly.
These tests do not establish parity with every existing interpolation interaction.

Detected unported language features exit **3** with a `rust-migration` diagnostic at the
original source construct. Ordinary compiler failures exit **1**. A failed migration
check must not be interpreted as an invalid source program. Unported interactions
not covered by the early guards can also appear as mismatches in the comparison
report; successful compilation alone does not establish parity.

## Reference correction found during migration

The baseline's emission shortcut for native runtime files omitted generated
parameter-type witnesses. Calls such as `basic.to_set([3, 1, 3, 2])` could refer to
a helper in `sets` that was never emitted. Packages requiring these project-specific
witnesses now use the existing local adaptation path and retain their original
source maps. Unmodified packages continue to use shared sources.

The regression runs checked and unchecked release code and inspects the generated
file/manifest/map. All 16 basic-library tests and the allocation-tracked walkthrough
pass after the correction. This is an intentional correction to the reference,
rather than behavior the native port should reproduce.

## Remaining work

The remaining migration is organized into eight independently verified chunks:

1. Type resolution and generic specialization.
2. Complete pure-call verification and checked ordinary callees.
3. Compile-time dependency extraction, execution, reconstruction and caching.
4. Integer/Rational lowering and values.
5. Remaining language and temporary-ownership interactions.
6. Diagnostics, TLS editor information and full debugger mapping.
7. Remaining commands, including vet, project creation and publishing.
8. Whole-suite/real-project differential acceptance and performance measurement.

Each checkpoint retains Python as the default and records its actual test scope;
an implemented subset is not whole-language acceptance. Optimization and parallel
lowering follow behavioral acceptance.

- Complete symbol/type/overload resolution, imported/native type facts, generic
  specialization and conditional declarations; the initial resolver is a subset.
- Complete immutable-slice integration with pure calls, evaluated generic dimensions
  and all remaining native type/overload specialization paths. The runtime slice
  acceptance suites are now included in the differential gate.
- Purity, local mutation provenance, checked callees, scratch/result ownership,
  dependency extraction, automatic evaluation, `#run`, and evaluation caching.
- Integer/Rational lowering and compile-time values; compile-time comprehensions and reductions,
  full temporary ownership, wildcard exports and complete optional-result arities.
- Complete switch/generic-callback and promoted-field context handling, Set
  temporaries, and native access interactions beyond the selected reference cases.
- Remaining diagnostic cleanup/related notes, frontend-warning policy, the
  unused-inner-scope warning behavior, and source/resource path rewriting.
- Full debug source regions, remaining sanitizer/tracker interactions and TLS
  `--editor-info`; publishing, project creation and vet commands.
- Full existing-suite and real-project differential acceptance. Only then should
  phase-level speed comparisons, default-engine changes, or parallelization begin.

## Differential gate

```sh
python3 tools/compare_compilers.py --report /tmp/thor-engine-comparison.json
python3 tools/compare_compilers.py examples/rust_migration --command run
```

The runner checks both engines as subprocesses, compares status/output and public
diagnostic messages/locations, and verifies that original sources did not change.
It passes `-thread-count:1` to Odin for deterministic diagnostics: Odin's parallel
checker can otherwise repeat instantiation notes nondeterministically. Override
this with an explicit `--flag=-thread-count:N`. Normal compiler defaults are
unchanged, and the separate runtime acceptance tests use those defaults.
Odin's nondeterministically ordered caller/instantiation notes are compared as a
multiset, preserving duplicates and the primary message; blank separators are
ignored. The report also retains each engine's raw output.
The compiler's JSON diagnostic envelope is parsed separately from program stderr;
formatting differences in an empty envelope cannot create a mismatch. Runtime
stderr and output emitted before a failed program exits remain part of comparison.
Generated files and caches go into temporary storage. The default corpus is the
existing rejection fixtures; explicit files or package directories can be supplied.

`match`, `mismatch`, `unported`, and `timeout` remain separate outcomes. Any outcome
other than `match` makes the gate fail. `--report-only` records an unfinished-port
inventory without asserting success. Two crashed processes do not count as parity.
The tests cover the comparator itself so future migration work cannot accidentally
turn unsupported features into passing cases.

The initial checkpoint passed 57 Python differential/harness/assertion-hygiene
tests, seven Rust unit tests in both profiles, and all eight default comparison
fixtures. Reference validation included a 200-test language/borrowing/interpolation
group, 23 basic-library/assertion-hygiene tests, and a final 74-test native
execution/compilation/arena/TLS group. Broad native reference runs used a temporary
single-thread Odin wrapper to avoid the existing intermittent backend crash;
the compiler's defaults were not changed. These are checkpoint results, not
whole-language Rust acceptance.

The expanded checkpoint passed 125 unique checks: 63 native differential tests,
43 reused reference acceptance tests, 12 comparator checks and seven assertion
hygiene checks. Eight Rust unit tests pass in both debug and release profiles.
All eight default rejection fixtures and the updated native migration demo match.
The large combined run contained 124 tests; the final comparator determinism
check was subsequently verified with its complete 19-test comparator/hygiene group.

## Scoped lowering measurement

`python3 tools/benchmark_lowering.py --report /tmp/lowering.json` generates 200
procedures using interpolation and power, validates each engine by compiling and
running the program, warms parser caches, and alternates five timed `transpile`
runs. The timed phase includes compiler startup, cached AST decoding, lowering,
and generated-file writes; it excludes Odin compilation and Cargo startup.

On this machine on 2026-11-09, the median times were:

| Engine | Warm transpile |
| --- | ---: |
| Python reference | 0.703 s |
| Initial Rust checkpoint (`6987b76`) | 0.129 s |
| Expanded Rust checkpoint | 0.087 s |

The [raw measurements](benchmarks/rust-migration-checkpoint.json) retain all
samples and the generated source hash. These describe one supported synthetic
case. They do not estimate real-project performance or establish whole-language
parity; the remaining semantic work must precede those comparisons.

## Runtime borrowing and construction checkpoint

The native engine now has procedure summaries for parameter-relative writes,
returned aliases and out-parameter stores. They preserve nested record/array
copy boundaries, shallow slice copies, named/default/variadic arguments,
conditional type specializations, union narrowing and SoA columns. Ordinary
inline scalar/record/array copies stay writable; retaining a reference to a
read-only element keeps its access restriction. Native SDK type inspection uses
shared parser entries rather than copying SDK sources into each project.

Runtime one-clause comprehensions support slices, dynamic arrays, sets and maps,
filters, flattening, optional fallback outputs and distinct container types.
Expanded `min`/`max` calls preserve evaluation order and optional status for
possibly empty slices. Generic inline callback defaults are lifted to named
native generic helpers. These are independent Rust implementations.

macOS native builds support the debug allocation tracker and ASan defaults,
tracking overrides and original Thor report locations, including standalone
executables moved away from their generated sources. Ordinary builds emit no
tracking support. The source-map lookup deliberately disables optimization for
that diagnostic helper: optimizing its constant tables increased one unchecked
tracking build from roughly 3 seconds to 70 seconds. The program itself retains
its requested optimization level. Full debugger/DWARF and TLS editor-information
parity remain outstanding.

The persistent acceptance gate now reuses 114 runtime slice fixtures, seven
runtime comprehension fixtures, nine expanded-extrema fixtures and 13 standalone
memory-debug fixtures in addition to the earlier native and comparison tests.
The three slice fixtures requiring compile-time evaluation remain
explicit migration gaps; they are not counted as passing native acceptance.

This checkpoint passed all 267 tests in the combined native differential,
reference acceptance, memory-report, comparator and assertion-hygiene gate.
All eight Rust unit tests also pass in debug and release profiles, and Clippy
passes with warnings denied. This is expanded runtime coverage, not completion
of migration stages 1–3.

## Initial native purity contracts

Runtime `fn` checking now follows local ordinary calls, rejects external storage,
ambient context, source identity, I/O, pointer identity and unsupported local
storage, and protects borrowed inputs. Rejection tests reuse the Python purity
inventory, including reordered builtin arguments and transitive source locations.
Scalar-returning functions release local allocations through the shared scratch
runtime. String, slice and dynamic-array results receive independent storage;
named/multiple returns retain user-defer ordering, and nested fn results use the
enclosing allocation lifetime. Existing ordinary runtime call behavior is retained.

This is still partial purity coverage. Generic/record schemas, ordinary checked
callee variants with owned results, SDK specialization and all compile-time
execution were unfinished at this initial checkpoint. The checked-callee
checkpoint below extends that runtime coverage; detected unported cases remain
explicit migration failures.

## Constant specialization and reference corrections

Record specialization now distinguishes integer/boolean/text arguments by value.
Enum arguments use their ordinal, including aliases. Array/fixed-capacity generic
counts remain constants in contextual type facts. Unresolved native branch
specializations fail explicitly instead of proving a branch from incomplete facts.
The shared frontend exposes enum members without scanning source-text braces.

The Python reference had a related false positive: it discarded record constant
values and included unrelated caller type variables in record identity. Its
constant record arguments now retain their values and relevant lexical captures.
Both resolvers also leave a parameter name out of scope in its own annotation
and default, preserving an enclosing type of the same name. Regression cases
cover integer, boolean and aliased enum arguments, explicit/inferred counts,
method receivers and substituted library bodies. A missing compiler-support
package now reports a source diagnostic instead of raising `StopIteration`.

The expanded native gate passed 285 checks. The subsequent purity storage/cast
hardening passed its complete 13-test focused group (one new test, for 286 unique
checks across the checkpoint). The Python reference compatibility run passed 284
checks covering borrowing, defaults, basic, binding/TLS, pure calls and native
execution. Rust unit tests pass in both profiles; Clippy denies warnings.
This remains an incomplete migration; the pending list above still applies.

## Call resolution and contextual types checkpoint

The native resolver now shares call-site plans between type inference, purity
checking and slice-effect analysis. Plans arrange named arguments and defaults,
filter incompatible element types, dimensions and nominal record identities,
and distinguish concrete from generic overloads. Non-selected overloads no longer
contribute mutation or I/O effects. Native Odin still checks final assignability
and ambiguous calls; incomplete native specialization is not treated as proof.

Generic result types retain their substitutions through nested calls. Local
record types preserve the generic parameters used from their lexical scope.
Constant parameters include enum ordinals, aliases, flags and contextual enum
comparisons. Conditional lookup continues across files after an inactive
declaration, and multiple wildcard imports are searched for the requested name.

Contextual bracket arrays use parameter witnesses emitted beside the selected
declaration, including private types in imported Thor and local Odin packages.
Generic type witnesses use the argument expressions under type queries, preserving
lexical shadowing and evaluation frequency. Their generated code retains original
source mappings. SDK sources remain shared; complete native SDK contextual
adaptation and evaluated dimensions still require later migration work.

Four additional Python-reference corrections were discovered and fixed:

- Promoted `using` fields now follow their real nested storage path during slice
  provenance analysis; promotion cannot bypass a read-only view.
- Enum compile-time parameters and their forwarded aliases select slice effects
  by ordinal rather than being treated as unresolved or compared with field text.
- Single-result procedure calls retain an expression witness for contextual
  generic inference, including when the original type name is shadowed locally.
- Generic record patterns propagate their type/count arguments into procedure
  results. A resolved integer dimension takes precedence over the source generic
  parameter's approximate type classification during negative-index lowering.

The persistent native gate includes `tests.test_rust_resolution` and twelve
additional existing binding/context fixtures. It checks selected mutation
rejections as well as valid readers and exercises runtime result typing and
contextual witnesses with assertions, bounds checks and type assertions disabled.
This is a resolution checkpoint; checked ordinary-call variants, complete generic
purity schemas and native compile-time execution remain unfinished.

The final checkpoint gate passed 320 native/differential, memory, comparator and
assertion-hygiene checks. All 198 Python borrowing, binding and default-callback
checks passed in the final reference run. Eight Rust unit tests passed in both
debug and release profiles; Clippy passed with warnings denied. An earlier
reference run hit an Odin backend SIGSEGV compiling the native format tests;
the targeted retry and the final complete reference run both passed.

## Checked ordinary-call runtime checkpoint

Calls from verified functions now use separately emitted checked ordinary bodies.
Ordinary calls retain their shallow aliasing behavior. Checked helpers use scratch
storage and recursively promote owned results into the enclosing allocation
lifetime. Helpers that mutate local out parameters retain that enclosing scratch
allocator, so returned allocations do not disappear at the helper's return.

Generated helpers and imports have separate namespaces for ordinary and checked
bodies, including nested procedures. Checked calls retain overload selection,
named/default/variadic arguments, anonymous f-string cleanup, original
`#procedure` names and required-result attributes. Redirecting a call cannot
bypass the original procedure's package/file privacy.

Both implementations had two reference inconsistencies that this work corrects:
checked helper results own independent storage and must therefore be writable
even when their inputs are read-only; and the parser's synthetic `_` names for
unnamed result slots must not be read as the first character of their type.
Unnamed results still require explicit return values. The native resolver also
tracks each slot of a multiple result with its own concrete/generic type, and
generated contracts handle ignored input parameters without reading `_`.

The permanent comparison suite now reuses existing borrowed-view, variadic,
privacy, contextless-call and type-query fixtures. Allocation-tracked native
fixtures exercise both debug and assertion/bounds/type-assert-disabled release
builds. This checkpoint does not complete chunk 2: generic public-function and
record schemas, native SDK adaptation and compile-time execution remain pending.

The checkpoint passed 336 broad native/differential, memory, comparator and
assertion-hygiene checks, plus seven reused acceptance fixtures and two additional
multiple-result/ignored-parameter regressions. All 112 Python purity, mutation,
call-context, dynamic-result and I/O checks passed. Rust unit tests pass in debug
and release profiles, and Clippy passes with warnings denied.

## Record contracts and checked storage provenance

Native pure-call contracts now validate inspected record and union layouts,
including generic record dimensions. Results recursively own each occurrence of
a string, slice or dynamic buffer, including repeated aliases inside records and
union variants. Contextless helpers retain the scalar-only result contract.
Pointer-containing records remain unavailable as public pure values; internal
helpers may operate on locally created pointer graphs.

Permission requirements and actual storage changes are tracked separately through
ordinary call chains. Creating an alias or requesting `#mut` alone does not make
a procedure impure. A subsequent write or deletion takes priority over that
permission request, and explicit immutable-slice restrictions remain enforced.

Record validation now uses Odin's physical shared-field metadata to distinguish
real fields from promoted `using` names. The earlier schema-counting algorithm
could falsely reject valid promoted records in both engines. Fixing it exposed a
Python purity-provenance gap: promoted fields inside copied/generic arguments
could lose their original storage path. Typed flow projections now retain that
path, including through aliases and pointer-containing internal helper arguments.
These are reference corrections; the port must not reproduce those bugs.

Native generic type-contract failures carry a source-site token through Odin's
checker and are retargeted to the original Thor parameter/result annotation.
The user-facing messages identify the original procedure and rejected type,
without exposing the generated validator's location or instantiation names.
Public generic branches, promoted `using` parameter scopes, native SDK checked
adaptation and compile-time execution still require later migration work.

This checkpoint passed 357 native/differential, memory, comparison and
assertion-hygiene checks; 325 Python purity, borrowing, binding and default checks;
44 base purity/evaluation checks; and 13 TLS checks. Focused regressions also
exercise promoted fields through copied aliases and both automatic and explicit
compile-time readers. Eight Rust unit tests passed in debug and release profiles;
Clippy passed with warnings denied.

## Initial public generic runtime contracts

Unbound implicit type names now retain generic identity instead of becoming
unknown types. Public generic functions with fully verified, type-independent
effects can validate concrete native instantiations and recursively copy owned
results. Coverage includes numeric/complex/quaternion operations, generic fixed
array dimensions, read-only slice inputs, local private record types, nested
slice construction and concrete function-pointer assignments. Their source
procedure signatures have no added permission parameters.

Abstract generic function bodies are also inspected even when a direct call does
not instantiate them in the resolver. This prevents an indirect instantiation
from bypassing borrowing checks. Cases that require a native conditional
ownership proof remain migration failures, including a generic copy that could
be either an inline array or a shared slice header. Generic native branches and
explicit type-parameter schemas remain incomplete; this is not full generic
purity support or completion of chunk 2.

The eight new generic checks passed, including debug and unchecked allocation
tracking. All 70 purity/checked-call/record/resolution/hygiene interaction checks
and all 195 reused reference fixtures passed. Eight Rust unit tests passed in
both profiles, and Clippy passed with warnings denied.
