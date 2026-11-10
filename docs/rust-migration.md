# Rust migration: independent native compiler

The Python compiler is still the supported default. The Rust port is **incomplete**;
this checkpoint does not complete stages 1–3 or establish whole-language parity.
Do not switch TLS or project build commands to it yet.

The reference baseline is commit `ca2df99`, also tagged
`rust-migration-baseline-2026-11-08`. The Python implementation remains in
`thorlang/` and the `thor` launcher. The independent Rust crate is in `rust/`;
`thor-rust` builds its release binary and launches it. It never runs the Python
compiler, including for unsupported syntax.

## Active parity-first plan

The accepted comparison snapshot for the parity-first migration is `825ee0a`.
Both Python and Rust are preserved there; the older `ca2df99` baseline remains
available for history. The Python snapshot includes the explicitly recorded
correctness fixes made during the initial migration. Further reference fixes
must remain separate, documented commits rather than silently changing the target.

The [module inventory](rust-parity-inventory.json) maps every Python module to
its current Rust counterparts and the checkpoint responsible for remaining work.
A mapped module or a test count is not a declaration of complete parity.

1. **Reference/inventory:** preserve the snapshot and map all modules (complete).
2. **Resolution:** finish symbols, types, overloads and generic/conditional lookup.
3. **Permissions/purity:** follow reference permission propagation and call checks.
4. **Execution:** port compile-time extraction, scheduling and helper execution.
5. **Results/cache:** install reconstructed results and port result cache policy.
6. **Lowering:** complete logical numbers and remaining expression/ownership gaps.
7. **Integration:** finish commands, TLS, diagnostics and debugger metadata.
8. **Acceptance:** run whole-suite/project comparisons, then profile performance.

Checkpoints 2–5 complete the previously discussed type/purity/compile-time scope.
Retain existing Rust parser, node IDs, emission and tested lowerers. For unfinished
or divergent semantic paths, follow the Python functions and decision order.
Use focused comparisons while implementing and broader gates at integration
boundaries. Further architectural optimization and parallel lowering wait for
behavioral acceptance. The historical sections below record earlier checkpoints.

### Parity-first resolution work

The resolver now follows the reference's `using` lookup and storage-protection
decisions. Promoted names retain their containing record's type and storage
origin, including nested `using`, parameter projections, local initializer
shadowing, and cross-file immutable storage. Existing pointer paths remain
shallow. A generic value receiver may require an implicit mutable address for
an arrow call; pointer receivers and by-value receivers keep Odin's behavior.

Storage checks retain native type predicates until generic instantiation. They
are rendered in the current helper capture environment rather than embedding
out-of-scope local names. Type queries do not execute index/call operands.
Mixed `.odin` dependencies receive the same immutable-storage checks as the
reference, while direct value iteration over an immutable array slice remains
valid. Direct storage errors take precedence over dependent slice-alias errors;
independent errors at one source position have separate message tokens.

The focused acceptance files are `test_rust_storage_parity.py` and
`test_rust_using_parity.py`, alongside the existing resolution and explicit
type-parameter suites. Runtime checks exercise checked and unchecked release
builds with explicit panic-based verdicts. The combined resolution/storage/using,
type-parameter and assertion-hygiene run passed 95 tests; the additional standalone
parameter-addressability test passed separately. All 15 Rust unit tests pass in
debug and release, and Clippy passes with warnings denied. Pure `using` projection handling is
the next permission/purity gap; compile-time execution remains unported.

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

The active eight-checkpoint plan and full module mapping are above. Earlier
references to “stages 1–3” mean type resolution, purity and compile-time execution;
they are not completion claims for checkpoints in the active plan.

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

## Deterministic native intrinsics

Checked calls can use the reference compiler's approved scalar and structural
operations from the installed `base:intrinsics` package, including constant
aliases and selective imports. Argument expressions still receive normal effect
checking. Clock/memory operations and type-name identity queries remain
ineligible. Canonical package identity is checked before names receive native
intrinsic treatment; an overridden package cannot acquire that trust by name.

The installed SDK's approved scalar LLVM declarations are recognized by their
foreign calling convention and exact operation/float-width link names. Their
ordinary library wrappers still require SDK adaptation. For example, a pure
`math.sin` call reaches the explicit adaptation gap rather than being rejected
as an invalid foreign call. User-defined foreign declarations retain normal
inspection requirements. Namespace `using` retains the Python reference's
purity restriction.

The native-port example now exercises generic scalar/owned results, recursive
record ownership, read-only inputs and a direct deterministic intrinsic. Both
engines produced matching output with empty tracker reports in debug and
unchecked release builds.

All 88 intrinsic/generic/purity/record/resolution/hygiene interaction checks
passed, as did a clean rerun of all 274 reference/compiler/comparison checks.
Eight Rust unit tests passed in both profiles, and Clippy passed with warnings
denied. The first reference run encountered one stock Odin compiler SIGSEGV;
the isolated retry and full clean rerun passed. Its extracted null-source,
nonzero-length stack signature is recorded in the existing
[`upstream` evidence bundle](../upstream/odin-codegen-race/evidence/recurrence-2026-11-09.txt).

## Sparse native SDK checked calls

Verified SDK wrappers now receive checked copies beside their original Odin
declarations. The generated project contains a sparse mirror with a layout hash:
only adapted files are generated, while untouched packages, files and resources
link to their original locations. One native SDK root keeps ordinary and checked
calls in the same nominal type universe. Ordinary declarations retain their
shallow ownership; checked calls recursively promote owned results. Odin source
keeps native indexing semantics inside the checked copies.

Imports used only in reachable SDK bodies are inspected before effect analysis,
including constant callee aliases and procedure groups. Missing body metadata
must not become a false mutation effect. Unsaved native source replacements are
inputs to the mirror; the installed SDK is not rewritten. Generated maps retain
the original native file locations. Layout changes, obsolete-link cleanup and
symlink-parent rejection have dedicated regressions.

Compiler support libraries use an installation-owned native view under
`lib/runtime-rust-<layout>`. It links editable sources while excluding their own
unit-test files, as Python's shared runtime does. This prevents imported support
tests from entering a project's test run or creating SDK dependency cycles.
Project-specific checked SDK copies and execution artifacts remain local.

The accepted subset covers scalar LLVM/math wrappers, native nominal durations,
generic slice reversal on local buffers, native string result ownership and
ordinary borrowed string views. Generic conditional ownership, promoted using
scopes and unverified/indirect callbacks still have migration or purity limits;
this does not complete chunks 1–3 or all SDK adaptation.

The SDK regressions also found two Python-reference issues. Addressed compound
literals now have stable abstract storage identities, preventing endlessly
growing pointer alias sets during provenance analysis. Record literals also
retain their local stack ownership, so their addresses cannot escape through
an input. Native size/alignment/offset/length/capacity queries have their known
integer result shape, avoiding runtime pointer guards in compile-time conditions.
Runtime, explicit compile-time and unsaved TLS transitions cover these fixes.

A separate repeatable LLVM AArch64 crash exposed a Rust-port mapping-table bug.
The emitted IR passed LLVM verification, but standalone `llc` crashed selecting
the generated memory-location mapper's huge aggregate stores. Slice constants
had materialized thousands of mapping rows on each call. Mapping tables now use
read-only global storage, retaining Python's storage lifetime, which removes that stack work and the crash
trigger. No installed compiler, backend defaults or normal-build instrumentation
changed. This is distinct from the earlier intermittent Odin cached-name race.

The clean checkpoint passed all 386 native/differential, memory-report,
comparison and assertion-hygiene checks; all 201 Python purity/borrowing/default
checks; and 17 TLS checks. Eight Rust unit tests passed in debug and release
profiles, and Clippy passed with warnings denied. The expanded example produced
matching output and empty allocation reports in checked debug and unchecked
release configurations. The focused SDK suite also verifies that test mode runs
exactly the project's own test, and that stale layouts never modify installed
SDK files.

## Explicit type parameters and scoped substitutions

Runtime lowering now supports inspectable functions and helpers with explicit
Odin type parameters, such as `identity :: fn($T: typeid, value: T) -> T`.
Type arguments remain distinct from integer/value arguments during overload
selection. Public generic results retain the existing independent ownership
contract; the source procedure signatures receive no hidden permission arguments.
Runtime `typeid` data is still ineligible as a pure input.

Substitutions now follow the resolved parameter symbol rather than a matching
name alone. Previously, an ordinary helper's local constant, or a global in an
unrelated callee, could be mistaken for an outer compile-time parameter. That
could make Rust skip an active impure `when` arm. The Python default already
rejected those programs. Procedure and record parameter scopes are now indexed,
and temporary enum-alias values use a separate scoped namespace.

Borrowing uses the common call planner's concrete type/value bindings while
retaining more precise value-flow information, including constructed SOA rows.
Coverage includes scalar and owned results, multiple type/result slots, named
value arguments, fixed-array contexts, local mutation and borrowed-input
rejection, overloads, enum aliases and nested record parameters. Generic native
branches and conditional ownership proofs remain incomplete; this checkpoint
does not finish the migration.

The clean gate passed all 400 native/differential, memory, comparison and
assertion-hygiene checks, including 14 new type-parameter regressions. Eight
Rust unit tests passed in both profiles, and Clippy passed with warnings denied.
The expanded example matched Python in checked debug and unchecked release
builds, with empty allocation reports.

## Captured generic type proofs

Both implementations now check conversions and local storage whose concrete
type comes from an enclosing generic procedure. Such a type is not necessarily
covered by a nested `fn`'s parameter/result checks. Previously, a captured pointer
type or raw-union layout could bypass the ordinary purity checks and modify
outside storage.

The additional checks are native type proofs beneath `type_of`. They preserve
constant expressions, evaluate the actual value once, and add no runtime calls.
Pure comparisons/conversion operands use the same approach; the Python
reference's previous runtime wrappers could invalidate generic constant `when`
conditions. Unevaluated source type queries remain unevaluated. Local pointers
derived from owned storage are still eligible.

Record schemas distinguish generic instantiations during traversal. Native
checks reject overlapping raw-union fields, retain exact checks for accessible
raw-union types, and do not reject ordinary records merely because an inaccessible
raw union has the same field names. Zero-sized fields do not count as overlapping
storage. Unresolved local schemas use the existing native schema depth limit.

The new proofs skip ordinary helper defaults when checked callers provide those
arguments. Omitted defaults and declared `fn` defaults remain checked. Runtime,
automatic compile-time and explicit `#run` reference cases cover these paths.
Native diagnostic metadata is collected after rendering, so type-proof errors
refer to the original conversion, operand or annotation. Unsaved TLS changes
cover error appearance and recovery.

The lifetime regressions also found a shared reference/native bug: native type
conversions such as `string(f"...")` were treated as borrowing procedure calls.
That could release the string before assignment or pure-result promotion.
Conversions now retain the operand's ownership, while an enclosing real call
still owns its normal temporary cleanup. Logical big-number constructors retain
their separate parsing/argument contract in the Python implementation. Native
borrowing uses the common type resolver for explicit type parameters and aliases;
checked generic conversions retain provenance until their exact target proof.

This is a correctness checkpoint within the type/purity port. Compile-time
execution/caching and other migration gaps remain; Python stays the default.

The clean acceptance runs passed 420 native/differential, memory-report,
comparison and assertion-hygiene checks and 223 reference checks, including
purity, defaults, compile-time execution and f-string lifetimes. The expanded
example matched between engines in checked debug and unchecked release builds,
with empty allocation reports. An earlier full run hit a stock Odin array-bounds
assertion during a reference-only check; its isolated retry, neighboring checks
and the clean full rerun passed. That diagnostic alone does not identify its
cause as the previously recorded cached-name race.

## Contextual and aggregate conversion proofs

Pure casts now validate the complete converted representation, including array
and slice elements. A scalar-to-pointer `auto_cast`, or an element-wise cast from
an integer array to a pointer array, must not bypass the ordinary pointer
conversion restriction. Conversions from pointer representations to numeric
representations are checked as well. Locally owned references and ordinary
`proc` pointer casts retain their existing behavior.

The contextual checks retain Odin's original `auto_cast`; they do not replace it
with an explicit target conversion. Source types still drive generic inference
and exact overload matches. Where necessary, an unevaluated `procedure_of` query
obtains the selected procedure's concrete parameter type. Compile-time parameters
are excluded from the runtime parameter index, named arguments retain their
mapping, and variadic arguments use the element type. No new shared SDK helper
files are needed for these proofs.

The generated proofs use native type queries and compile-time `when` selection,
without runtime wrapper calls or additional operand evaluations. Known scalar
conversions skip the extra representation proof. Unused ordinary defaults and
unevaluated source type queries retain their contracts. Regression coverage
includes checked and unchecked runtime builds, overloads and generic inference,
private types, SDK calls, compound literals, aggregate conversions, defaults,
reference compile-time calls and unsaved TLS diagnostic recovery. This remains a
checkpoint within the type/purity migration; it does not complete native
compile-time execution or the other outstanding feature ports.

The integrated focused run passed 29 new/reference/native/TLS checks. Neighboring
reference runs passed 48 purity/conversion checks and 31 big-number checks. All
seven assertion-hygiene checks passed, as did the Rust unit tests in debug and
release and Clippy with warnings denied. The earlier captured-type checkpoint
also completed 30 TLS checks after its commit. The broader native acceptance
suite is rerun at the next combined checkpoint.

## Generic conditional branches and pure call context

Unresolved generic `when` conditions can now remain native when every possible
arm can be verified as eligible. This covers public generic functions and checked
ordinary helpers, type and constant-value conditions, conditional expressions,
type-specific inactive code and independent owned results. Type comparisons
against pointer *types* do not observe runtime pointer identities. Native exact
parameter/result proofs validate unresolved generic type slots.

An unresolved condition with a potentially impure arm still produces an explicit
migration gap. The compiler must first know which arm is selected before it can
report that arm as a real source violation. Runtime state accessed by the
condition itself remains an actual purity error. Conditional generic mutation
and ownership cases that need more precise provenance also remain unported.

Native type-proof diagnostics now retain the checked source call paths. With a
single path into an ordinary helper, the primary error points at the requiring
`fn` call, with the helper's underlying operation and pure declaration as related
locations. Multiple callers/specializations retain the underlying primary
location and identify the possible checked paths in notes, rather than assigning
the error to a possibly unrelated specialization.

The combined checkpoint passed all 224 reference checks and 21 TLS checks. The
native/differential, memory-report, comparison and assertion-hygiene gate ran
443 checks: 442 passed; one new diagnostic fixture incorrectly used an Odin-invalid
`int`-to-pointer cast and therefore received two errors. Correcting its input to
`uintptr` isolated the intended purity violation, and that test passed separately.
The additional multiple-caller diagnostic test also passed. No production-code
change was needed for that fixture correction. All eight existing Rust unit tests
passed in debug and release, Clippy passed with warnings denied, and the updated
example matched both engines in checked debug and unchecked release builds with
empty allocation reports.

## Native compile-time value reconstruction foundation

`rust/src/values.rs` now implements the existing contents-only value packet and
Odin reconstruction API for native scalar and aggregate values. It preserves
integer bounds, float bit patterns and byte order, arbitrary string bytes, enum
arrays, bit sets, records, unions, nil/zero-sized sequences and dynamic capacity.
Runtime reconstruction allocates independent returned storage directly, using
the shared pure runtime; it does not allocate temporary object templates.
Unicode field identifiers use the already-locked `unicode-ident` crate. Packet
versions must be integer `1`; Boolean/float lookalikes are rejected as malformed
headers. Big-number static backing remains part of the separate big-number port.

The specialization envelope now forwards ownership and dynamic-storage queries
to its payload in both implementations. The reference evaluator normally unwraps
that envelope before reconstruction, but the shared protocol API must remain
correct when it receives one directly. Native tests cover wrapped and unwrapped
actual Odin serializer output, including checked and unchecked release builds
and allocation-map/bad-free checks. Differential tests compare scalar/aggregate
packets, malformed input and 200 generated portable values with the reference.
The Cargo example `value_protocol` is an internal differential-test harness.

This component is not yet connected to native `#run`: dependency extraction,
execution scheduling, result installation and result caching remain to be
ported. Python remains the default, and native execution gaps remain explicit.
The read-only reference regression check also passed all 19 projects currently
under `/Users/davidludwik/Code/Thor`, with their source files unchanged.

Validation passed 52 value/compile-time/big-number regressions, seven final native
protocol integration tests, and seven assertion-hygiene checks. The integration
suite includes the 200 generated packet comparisons and actual Odin round trips
for plain and specialization-wrapped results in both checked and unchecked
release builds, with no tracked allocations or bad frees left over. All 15 Rust
unit tests passed in debug and release, and Clippy passed with warnings denied.
The decoder transfers the decoded payload rather than cloning its JSON tree;
byte-string reconstruction appends hex escapes directly without a heap
allocation for each byte.
