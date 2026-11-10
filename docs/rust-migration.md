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

One additional reference correctness fix protects qualified numeric constants:
`library.VALUE` retains the same read-only mutation, address-taking and deletion
rules as `VALUE`. The previous check removed the package selector before resolving
the exported symbol, allowing invalid writes and frees of static backing. This
fix is isolated from the Rust port; the frozen reference tag is retained. The
regression also verifies that an independent runtime copy remains mutable.

A separate publishing reference fix puts the package payload in a uniquely named
staging directory. Application names `package.zip`, `package.tar.gz` and
`previous` can now be published and republished without colliding with temporary
archives or the replacement backup. All 41 publishing checks and seven assertion
hygiene checks passed; the frozen comparison tag remains unchanged.

A separate Python correction reuses the builtin namespace import when the
purity validator and generated-name hygiene both need it. The duplicate import
previously prevented otherwise valid shadowing of builtin names inside `fn`
and `#run` from compiling. All 128 reference diagnostic and assertion-hygiene
checks passed, including the cold and cached execution cases.

### Library call and permission parity corrections

Expanded original-fixture comparisons exposed gaps hidden by the earlier
feature-focused gates. The native compiler now resolves imported/overloaded
optional-result signatures, retains complete `or_panic` failure locations, and
preserves lexical context in default callbacks, field names and named arguments.
Explicitly initialized foreign-callback contexts can allocate as in Python.
Contextual array literals boxed as `any` retain their inferred array type;
nominal sequences work in extrema, and package-level switch initializers retain
contextless nested helpers. Wide native constants receive fixed-operand context
without relaxing logical/native numeric type matching.

Slice analysis now follows the reference's demand-driven parameter contracts and
recursive-caller stabilization. Scalar-only callees no longer force SDK body
inspection merely because their caller is pure. The separate storage-provenance
analysis respects approved-library boundaries. Native purity checking recognizes
I/O aliases, defers overload selection to Odin, and leaves inactive `fn` bodies
unchecked. These corrections address real timeouts; they are parity work rather
than a new optimization architecture.

The new original-fixture gates cover optional results, callback defaults, basic
helpers, hashing, pure I/O and terminal diagnostics. The initial 104-test library
gate passed 101 tests and exposed three I/O underline/stage mismatches; all ten
pure-I/O fixtures passed after that correction. Rust's 34 unit tests pass in debug
and release, and all-target Clippy passes with warnings denied. The clean
permission/editor/generic/assertion-hygiene gate passed all 232 tests, including
checked and unchecked slice-copy paths. Whole-language diagnostic comparisons
remain in progress. None of these results establishes full migration acceptance.

A uniform copied-project sweep built all 19 example projects with both compilers
and verified that their original Thor sources were unchanged. The editor Node
suite passed 141 tests. These project and editor checks complement, rather than
replace, the remaining original-language fixture comparisons.

### Native diagnostic parity

Rust now uses emission ranges and source syntax to restore original expressions
and filter dependent Odin diagnostics. Independent errors in separate operands
or f-string fields remain visible. The port also locates missing imports,
wildcard conflicts, switch results, optional failures and runtime support checks
in the original source, including unsaved buffers and Unicode. Invalid native
overload calls in `#run` retain Odin's candidate list with the original package
names.

The original diagnostic fixtures, terminal source excerpts and two capture
runtime checks passed together in a 113-test debug gate. Whole-language parity
remains under verification.

### Purity context, owned results and compile-time bindings

Purity errors now trace a checked ordinary procedure back to the declared
`fn` call and retain the original operation and function signature as related
locations. An explicit `#run` of an ordinary procedure identifies the
underlying violation at its original source location. Independent type errors
remain visible when the compiler moves a purity diagnostic to its caller.
The validator distinguishes source-location arguments to `delete` from
allocator overrides and preserves automatic cleanup in the caller's scope
when its result is discarded.

The owned-result wrapper now promotes named slots after user defers. It can
copy temporary views of local arrays while their backing is alive, including
non-nil empty slices. Isolated evaluation retains result type annotations for
local `fn` declarations and validates `type_of` operands without evaluating
their runtime initializers.

Generic evaluation uses the original concrete caller's scope when its types
are unavailable in the library. Probe iterations keep generated import names
stable while rebasing local type identities. Package constants may use Odin's
compile-time file and existence directives, with resource contents, presence
and build defines participating in cache validation. Focused gates passed
for these changes, including an assertion-disabled owned-result regression
on both engines. The combined release acceptance gate is separate.

### Constant array and slice concatenation

The resolver now uses the length of the whole concatenation for subsequent
indexing and type queries. Untyped bracket literals inherit the element type
of an established operand, including a distinct array used as an element.
Odin still evaluates a single concatenation intrinsic. Native errors map to
the original operand and preserve independent literal diagnostics. All 25
original concatenation fixtures passed, including checked debug and unchecked
release tests that verify the operation does not allocate. The follow-up
diagnostic gate passed all 123 checks. The combined purity, compile-time result
and assertion-hygiene gate passed all 131 checks on the release compiler.

### Switch results and collection permissions

Switches use the selected call's signature to distinguish `-> !` from an
ordinary void result. Imported and generic call shapes that require Odin
resolution are checked without executing a candidate arm. A switch supplying
multiple call arguments receives each parameter's expected type. Assignment
captures map headers and keys in the generic map case, and discarded results
release anonymous f-strings and Sets while retaining named storage. Inactive
`#switch` arms retain valid Odin syntax even for multi-name declarations.

Permission checking now includes map and Set comprehension keys and keeps the
scalar status results of verified collection and allocation calls independent
of their owned results. A generic map lookup returned directly from a two-result
procedure keeps Odin's optional lookup status and key semantics.

All 83 original switch, enum and cross-feature permission fixtures passed.
The discarded-result allocation test passed on both engines in checked and
unchecked configurations. The original generic map result test passed with
the Rust compiler.

### Compile-time helper cancellation and typed nil

On POSIX, `SIGTERM` during native compile-time evaluation stops the active
child, waits for it, and removes its isolated project before returning status
143. The same cancellation fixture runs against both compilers. Typed `nil`
slice arguments remain valid in `#run`. Native conversion proofs still check
the target type, and resolved invalid pure type arguments report the parameter
contract even when the procedure also uses that type in its local storage.
The Unicode diagnostic fixture now selects the same compiler as its test run.

### Constant expressions and lazy imports

Constant membership uses indexed comparisons for fixed arrays and Odin's own
bit-set membership. Inline lists preserve the typing of imported untyped
constants. Compile-time `#assert` and `#config` arguments retain constant
context, including untyped numeric power results. Fixed exponents in generic
type expressions use native constant arithmetic and type validation.

Compile-time switches retain the selected result's type and emit helpers in
the owning case. Odin validates constant labels, including enum aliases,
escaped strings and `#config` values. Wildcard imports leave inactive exports
lazy, while explicit imports still check each selected name.

All 116 checks in the combined expression, power and import gate passed.
The expanded membership and unevaluated-power regressions passed on both
engines, including debug and unchecked power builds.

### Native parameter type witnesses and checked formatting

Isolated helpers emit parameter type witnesses only for retained declarations.
Ordinary calls through `thor:basic` can use witnesses from the native runtime
package that defines the procedure. The manifest identifies the adapted file
and its source map, which retains the original Odin procedure bytes. The
build-output check allows each compiler's generated directory while verifying
the same executable location and name.

Compile-time context substitution preserves lexical bindings and record field
names. Type-name intrinsics report their source-identity restriction through
constant aliases. Checked formatting validates the resolved operand type with
the runtime's scalar and logical-number rules, including untyped scalar
arguments and records that would invoke custom formatting.

The native witness and original-byte mapping checks passed in checked and
unchecked builds. The private contextual type and unrelated `#run` regression
passed with both compilers. The native formatting, source identity and context
macro fixtures also passed.

### Anonymous producer ownership

The shared temporary algebra now follows `thorlang/ownership.py` for strings and
Sets. Known consumers retain the selected ownership flag, release intermediate
operands in reverse order, preserve lazy branches and keep view owners alive
through indexing, membership, queries, formatting and iteration. Anonymous union
chains may reuse their owned left table; named operands remain borrowed. Set
updates capture the destination before evaluating the right operand. Discarded
expressions, mixed blank destinations and loop exits release only owned values.

Ordinary procedure results remain borrowed by this layer. Passing an anonymous
Set to an arbitrary procedure keeps the reference's transfer behavior; automatic
f-string call scopes retain their existing separate contract. Escaping a view or
address into an anonymous allocation reports the original named-owner diagnostic.
Native type queries do not execute their operands. Generated type-query helpers
have separate identities for scopes that carry an f-string cleanup frame.

This remains checkpoint 6: logical-number producers/destructors and broader
integration are still outstanding. Python remains the supported default.

The corrected combined ownership/f-string/checked-call/compiler/numeric/generic
and assertion-hygiene gate passed all 156 tests. Rust's 29 unit tests passed in
both debug and release; Clippy passed with warnings denied. Runtime ownership
fixtures exercise allocation tracking in checked and unchecked builds.

The initial gate had two obsolete migration-gap expectations and one transient
Odin SIGSEGV while compiling the Python reference's typed numeric loop fixture.
Rust produced the expected result; four isolated comparisons and the clean full
gate passed. The extracted crash record extends the existing upstream issue
bundle at `upstream/odin-codegen-race/evidence/recurrence-2026-11-10.txt`.

### Exact logical-number contents and static backing

The native protocol now validates canonical arbitrary-precision integers and
reduced rationals with positive denominators. Numeric lexemes are parsed exactly,
including alternate bases, decimal scales and values with thousands of digits;
parsing never goes through a machine float. Exact fraction construction keeps
its normalization invariant private to the numeric contents module.

The static backing emitter matches the reference's 63-bit limbs, minimum
four-limb headers, signs, zero representations and content identities. Value
reconstruction accepts a backing callback and clones logical numbers into
independent runtime owners, including nested records, slices and unions. Existing
callers retain the ordinary protocol API. The `big_protocol` Cargo example is an
internal differential harness.

This is a foundation for checkpoint 6. Big-number language types, constructors,
operators, source constant bindings and native execution integration remain to be
connected; normal Rust commands still report those migration gaps explicitly.
Python remains the default.

The focused protocol/reconstruction/native-execution/assertion-hygiene gate
passed all 33 tests. Comparisons include 200 generated mixed packets, limb/sign
boundary cases and canonical-packet rejections. Direct Odin reconstruction ran
32 scalar/aggregate cases across checked and unchecked builds, with explicit
allocation-map and bad-free verdicts. All 29 Rust unit tests passed in debug
and release; Clippy passed with warnings denied.

### Logical type resolution

`Integer` and `Rational` now have distinct native semantic identities. The
resolver recognizes predefined names and the real runtime bridge, while user
types and local variables can still shadow those names. It retains aliases,
structural containers, native/helper return types, independent rational property
types and boolean text-constructor status slots. Native type spelling can import
the shared bridge for isolated helper metadata. Runtime lowering is still pending.

The new shape comparisons also found a general specialization mismatch: mutable
locals inherited the untyped flag of their literal initializer. That path now
defaults mutable values, matching the reference, while constant literals retain
their untyped status. Ordinary large numeric lexemes retain native scalar types;
these type changes do not infer logical numbers from native overflow expressions.

The focused logical-resolution/ordinary-resolution/generic-binding/compiler/
numeric/assertion-hygiene gate passed all 137 tests, including the eight new shape
comparisons. All 29 Rust unit tests passed in debug and release, and Clippy
passed with warnings denied. Python remains the supported default; this is a
checkpoint within the remaining logical-number port.

### Semantic context for owned literals

Lowering hints now carry the semantic expected type alongside the native Odin
type expression. Explicit declarations, assignments, results, parameter witnesses,
array/concatenation elements, set literals, comprehension builders and switch
result slots preserve that context. This metadata lets the numeric lowerer
recognize explicit logical/generic contexts without treating ordinary native
literals as allocating logical numbers. Runtime constructors and ownership
lowering are still being ported.

All 113 compiler/generic/concatenation/native-execution/assertion-hygiene checks
passed after this change. All 29 Rust unit tests passed in debug and release;
Clippy passed with warnings denied. These changes do not enable the unfinished
numeric lowerer or change the supported-default Python engine.

### Runtime logical numbers and direct ownership

Rust now lowers `Integer` and `Rational` constructors, exact contextual literals,
aliases, text status results, ordinary arithmetic/comparisons and compound
updates. Native generic instantiations retain their Odin operation. Direct
numeric copies own independent backing; aggregate copies remain shallow. Rational
components are read-only, independently owned results.

Simultaneous assignment captures destinations and snapshots inputs before
replacing owners. Map keys are evaluated once. Owning field/element contexts
cover records, arrays, map literals, dynamic append and comprehension output.
Anonymous numeric call arguments and discarded results are released, including
mixed blank destinations and multiple results. Optional selection preserves
caller return/continue/panic behavior and cleans failed owned results. Global
initialization establishes the default context; predefined and imported numeric
values do not require callers to spell an explicit runtime import.

Native argument witnesses are built inside their helper's capture scope, after
semantic ownership decisions. Variadic groups use element contexts, and named
generic record fields retain their field type. The ordinary result inference
path now also propagates types through `or_return` and loop branch expressions.

The final compiler/resolution/generic/temporary/f-string/execution/assertion-hygiene
gate passed all 160 tests, including 21 new runtime-number comparisons with
allocation tracking in checked and unchecked builds. All 29 Rust unit tests
passed in debug and release, and Clippy passed with warnings denied.

This remains checkpoint 6. Read-only constant folding/static source bindings,
numeric builtins/generic components, and logical-number pure execution and cache
reconstruction still need integration. Python remains the supported default.

### Source logical constants and lazy selection

The Rust constant lowerer now follows source bindings and exact numeric operators,
including nested numeric builtins, Rational components, conditions and lazy
compile-time switch arms. It validates exact ranges and overlap, and preserves
source diagnostics at file scope. Numeric membership constants retain the
reference's exact comparison and short-circuit behavior.

Numeric constants use shared read-only limb backing. Reading them does not
allocate; direct runtime copies remain independent owners. Mutation, address-taking
and deletion are rejected, including qualified imports. Text parsing and other
procedure-based construction still require explicit compile-time execution.
Logical constant type facts remain separate from native execution results.

Native power scheduling excludes logical-number operators and their constructor
or annotated contexts, matching the Python decision order. F-string cleanup
scopes also precede numeric constructors, so anonymous parse strings are released
without freeing named strings or changing optional result arity.

This remains checkpoint 6. Runtime numeric builtins, generic components,
selection and logical-number fn/#run/cache reconstruction are the next work.
Python remains the supported default.

The combined compiler/runtime/constants/resolution/generic/temporary/formatting/
execution/cache/reference/hygiene gate ran 211 tests: 210 passed. The remaining
Python-reference Odin compilation crashed with SIGSEGV; Rust printed the expected
`3 local 2`. Isolated debug and release comparisons both passed. The null-source
`alloc_cstring`/`lb_type_internal` stack matches the existing upstream evidence;
see `upstream/odin-codegen-race/evidence/recurrence-2026-11-10-114804.txt`. This is
recorded as an external gate failure, not a clean full-suite pass. All 29 Rust
unit tests passed in debug and release, and Clippy passed with warnings denied.

### Runtime logical selection and generic components

The runtime integration now follows numeric `abs`, `min`, `max` and `clamp`,
including array expansion, optional slice expansion, empty inputs, anonymous
operand cleanup and ordinary native generic instantiations. Selected results own
independent storage. Generic Rational component reads also return owned numeric
values while ordinary record fields retain their native behavior; write guards
reject the logical properties only for Rational receivers.

Logical membership compares numeric contents. Named collections stay borrowed;
constructed scalar operands are evaluated once and released. Generic collection
membership uses native dispatch for arrays, slices, maps and bit sets, with
logical equality only for exact numeric instantiations.

Runtime numeric switches own a stable selector snapshot and consume owned case
operands. All result slots are evaluated before destination replacement, blank
slots are destroyed, and `case: break` leaves existing destinations untouched.
Case comparisons retain source mapping and the debugger capture classification.

This remains checkpoint 6. Logical-number pure execution, native result/cache
reconstruction and remaining whole-language acceptance are still outstanding.
Python remains the supported default.

All 162 integration/constants/runtime/compiler/generic/temporary/formatting and
assertion-hygiene tests passed. The twelve new focused comparisons run with
allocation tracking in checked and unchecked builds. All 29 Rust unit tests
passed in debug and release, and Clippy passed with warnings denied.

### Logical pure execution and result-cache integration

Logical numbers now participate in pure contracts, independently owned fn/#run
results, canonical contents caches and the reference's prepared-result reuse.
Native helpers reconstruct scalar numbers and owned numeric records/slices from
contents packets; static scalar results retain read-only backing. Runtime copies
and repeated cached results own independent storage. Numeric aggregates and
numeric-containing comprehension constants retain the reference's runtime-binding
requirement, and native generic value slots reject allocated logical types.

Direct numeric copies reset borrowed storage provenance; shallow aggregate fields
remain borrowed. Native generic ownership predicates distinguish numeric copies
from ordinary reference headers. Exact private-storage guards preserve ordinary
user fields while rejecting Integer/Rational internals in checked code. Pure
formatting accepts logical values, and contextless results retain their allocation
restriction. Isolated boolean selection uses constant rendering, matching Python.

This integration also fixed two shared helper bugs: capture collection now includes
a root identifier, and storage guards placed around a deferred operation no longer
cause its operation to execute immediately. Numeric `or_panic` follows the reference's
single optional lowerer, preserving capture scope and independent selected owners.

The two integration batches passed all 462 tests (167 and 295), covering the new
numeric cases plus ordinary purity, storage, scheduling, native execution, cache,
compiler, generic, temporary, formatting and assertion-hygiene paths. Runtime
ownership cases exercise checked and unchecked builds. All 29 Rust unit tests
passed in debug and release, and Clippy passed with warnings denied.

A reference limitation remains pinned: direct text-constructor `#run` has a result
count diagnostic; a pure wrapper that consumes the optional status executes and
reconstructs correctly. No Python fallback or Odin compiler changes were added.

Checkpoint 6 still needs the all-source constant-type prepass: an additional probe
found that a selected imported compile-time switch arm can keep an inactive scalar
arm's type until its owning source is lowered. Python performs an earlier exact
constant pass. This is being ported before moving to command/editor integration.
Python remains the supported default.

### Constant types before ownership lowering

The native resolver now primes exact logical constants across all source files
before scheduling native execution or lowering runtime copies. This follows the
reference's numeric-constant prepass. A constant selected from a compile-time
switch retains the selected `Integer` or `Rational` type even when its first,
inactive arm is a native scalar literal. Imported and forward-declared constants
therefore receive the same independent-copy, update and deletion behavior as
constants declared earlier in the same file. Final lowering still emits static
backing and reports invalid constant expressions at their original source sites.

All 97 focused constant, numeric execution/purity/cache, scheduling, type-parameter
and assertion-hygiene checks passed. The new cases include imported constants,
forward declarations and selected constants passed into `#run`, in checked and
unchecked builds. All 29 Rust unit tests passed in debug and release; Clippy
passed with warnings denied. This closes the known constant-type prepass gap;
remaining integration and whole-language acceptance work continues below.

### Native project creation

The Rust CLI now handles `new` directly, without requiring Python or Odin. It
creates the same named source file and minimal VS Code task/launch configuration
as the reference, including custom UTF-8 template bytes, optional `.gitignore`,
explicit Odin paths, filename-filtering warnings and optional editor opening.
Existing directories and dangling symlinks are preserved. Generated editor tasks
continue to use the supported `thor` launcher; creating a project does not switch
its compiler to the incomplete Rust engine.

All seven new integration tests passed, including creating and running a project
with both engines and creating projects with no tools on `PATH`. Fourteen earlier
factory/CLI comparisons also passed. All 29 Rust debug unit tests and Clippy with
warnings denied passed. This is part of checkpoint 7; publishing, vet, complete
TLS/debugger integration and whole-language acceptance are still outstanding.

### TLS permissions and debugger map integration

The native CLI now supports the existing hidden `--editor-info` protocol. It
returns per-file UTF-8 snapshot hashes, explicit/inferred slice mutation
parameters and readonly member-access offsets with mapped JSON diagnostics.
Editors can request contracts for unused and generic declarations; ordinary
builds retain the existing demand-driven analysis. Unsaved overlays use their
own hashes, and a later saved-source check restores the saved snapshot metadata.

Source maps now retain expression-helper, capture, source-call-target and checked
procedure regions. Generated identifier replacements retain their AST kinds so
the existing debug adapter can translate helper conditions and variable names.
Debug manifests include SDK/runtime roots and the successful executable's hash;
a subsequent check invalidates that executable identity. Generated editor tasks
and TLS still use the supported Python compiler by default.

The combined gate passed all 118 tests, covering the real Node debug-map
consumer, metadata comparisons, compiler and pure-call diagnostics, checked and
unchecked numeric ownership and assertion hygiene. Five further generic,
variadic and signature-only metadata comparisons passed. All 30 Rust unit tests
passed in debug and release; Clippy passed with warnings denied.

Live CodeLLDB stepping could not be validated in this environment: both the
native build and an unchanged Python-reference test timed out while waiting for
`configurationDone`. The initial sandbox loopback restriction was removed for
that comparison, and stalled test processes were cleaned up. The source-map
consumer checks pass, but they are not a substitute for completing live DAP
acceptance. This remains part of checkpoint 7.

### Native invocation, warning recovery and CLI controls

The native build path now follows the reference's unused-inner-local policy.
Only Odin's default single-declaration control-block rejection becomes a warning;
explicit unused-variable/procedure vetting remains strict. Recovery keeps the
declaration, initializer and deferred cleanup intact, inserts an inert statement,
and rewrites source segments, expansion/capture regions and persisted map hashes
before the next native check. Ordinary successful compilations still use one
backend invocation. Isolated compile-time helpers use the same recovery path.

The CLI now supports `--run-timeout`, `--clear-cache`, `--cache-report` and
`--raw-diagnostics`, plus equals-form value options. Cache clearing preserves
unmanaged files and symlinks. Usage errors have status 2. Compiler-looking options
after `--` reach the program. Warning suppression/promotion and deduplication
match the reference. Plain-text native-tool output and backend signals retain
toolchain diagnostics, signal names, native detail and failure status.

All 98 combined invocation/compiler/debug-map/assertion-hygiene tests passed.
The focused 19-test run and two controlled toolchain-failure comparisons also
passed. Runtime fixtures check required side effects and deferred work in both
checked and unchecked builds; map-consumer tests verify that unused-`do` recovery
keeps the final executable identity and generated-source hashes consistent.
All 32 Rust unit tests passed in debug and release; Clippy passed with warnings
denied. Native `vet` and publishing ports and whole-language acceptance remain.

### Native convention vetting and verified corrections

The Rust CLI now supports `vet`, `--vet[=immutable,pure]`, `--apply` and `--ask`.
Findings use the existing warning codes, UTF-8 byte edits and snapshot hashes.
Candidate filtering retains explicit ownership/aliasing decisions, excludes
generic/attributed/inactive declarations and rejects known purity violations.
The complete native compiler validates batches and bisects rejected batches
before a correction is reported. Validation invokes only the independent Rust
compiler and reuses the project cache; Python is not a fallback.

Corrections preserve comments, CRLF, Unicode and file modes. Application stages
updates and backups, checks the original bytes again before replacement, rolls
back partial replacements when possible and preserves concurrent edits. Dry
vetting never prompts or edits. `--ask` defaults to no, and corrections reject
unsaved overlays. Vet options before the source path and large valid batches
retain their reference behavior. Build-time findings obey ordinary warning flags.

All 17 focused native vet tests passed, including exact reference fix-payload
comparison and an 80-correction batch. The combined vet/CLI/TLS/unused-local/
debug-map/assertion-hygiene gate passed all 54 tests. All 34 Rust unit tests passed
in debug and release; Clippy passed with warnings denied. Transaction tests cover
replacement failure, rollback, concurrent changes, permissions and cleanup.
Publishing, remaining migration guards and whole-language acceptance continue.

### Native application publishing

Rust now implements publishing policy, TOML configuration, additive project
resources, PNG/ICO normalization, platform icon containers and native dependency
relocation. macOS packages preserve framework links and sign copied dependencies;
Windows packages inspect regular/delayed PE imports; Linux packages retain their
relocatable launcher, library paths and explicit desktop installer. Managed
replacement uses an isolated payload directory and preserves prior output on
compilation/package failures. Console launching remains an explicit app choice.

Publishing shares native lowering, compile-time helpers, source diagnostics,
tracking and debug-symbol packaging. Diagnostic warnings and later errors produce
one JSON packet. The normal build path does not read publishing configuration.
`thor:app` uses the source directory in development and the packaged layout when
published. Foreign import assets use per-source collection aliases, matching the
reference and preserving library paths containing spaces without editing inputs.

The combined publishing/reference/compiler/CLI/TLS-metadata/debug-map/scaffolding/
vet/assertion-hygiene gate passed 197 tests with two existing opt-in Finder-launch
checks skipped. Portable comparisons cover 36 policy/flag cases, twelve icon
pixel/container cases, eighteen dependency cases and sixteen package/replacement
cases. Real macOS tests move applications away from sources, preserve arguments,
load libraries/plugins/frameworks and run tracked debug and unchecked release
builds. All 34 Rust unit tests passed in debug and release; Clippy with warnings
denied passed. Windows/Linux host execution and live DAP acceptance remain outstanding; format
and controlled-tool comparisons do not substitute for those platform checks.
Three final CLI checks also passed for toolchain-first validation, publishing
TLS metadata, combined warning/error packets and relocated #run applications.
The native path never invokes Python; the Python engine remains the default.

### Native-selected conditional mutability

Conditional declarations now carry the reference's compile-time immutable marker
rather than an unconditional migration rejection. Native `when` and compile-switch
selection decides whether storage and slice-access guards apply. Imported bindings
use generated module access; conditional reference-view errors retain original
source positions and related declarations. The marker and its checks add no
runtime work. Existing native intervals and backing aliasing rules are unchanged.

The focused comparisons cover scalar assignment/address-taking, fixed-array
copies, slice writes/aliases/addresses/#mut calls, imported bindings, independent
clones, generic and non-braced branches, and pure/#run execution. All 127 combined
storage/using/resolution/editor/borrow-model/local-ownership/assertion-hygiene tests
passed, plus the additional Unicode TLS diagnostic-location check. All 34 Rust
unit tests passed in debug and release; Clippy passed with warnings denied.
Remaining migration guards and project acceptance continue before optimization.

### Native intervals and membership

Numeric loop intervals bypass generic Set dispatch, including parenthesized
intervals and defaulted/generic bounds. Native Odin retains its bound evaluation
frequency and loop control behavior. Range membership now follows the reference's
constant predicate and runtime ownership algorithm: operands are checked in one
common native type, evaluated once in source order, and temporary owners are
released after comparison. `not_in`, inclusive/exclusive bounds, enum contexts,
formatting and fn/#run calls retain their existing behavior.

All 110 interval/compiler/conditional-binding/typed-loop/assertion-hygiene tests
passed. A corrected case-sensitive diagnostic expectation also passed separately;
no compiler fix was needed for that fixture. All 34 Rust unit tests passed in
both debug and release, and Clippy passed with warnings denied. These changes
address the interval mismatches found in project-copy acceptance; helper capture
addressability and fixed-capacity storage cases are the next work.

### Capture addressability and fixed-capacity data

Generated helper captures now follow projected storage owners, including pointer
paths, value parameters, range bindings and type-switch bindings. Runtime indexed
reads can copy readonly inline values while writes preserve real pointer-backed
storage. Reference type switches request an address for their indexed selector;
value switches retain readonly behavior. Fixed-capacity dynamic-array element
access follows the reference's header/data distinction while ordinary immutable
slices retain their data restrictions.

All six new focused comparisons and all 283 combined reference/storage/using/
editor/debug-map/conditional-binding/assertion-hygiene tests passed. Checked and
unchecked runtime cases verify original storage, independent copies and rejected
writes. All 34 Rust unit tests passed in debug and release; Clippy passed with
warnings denied. The copied-project recheck now builds 15_puzzle, Ascending Primes,
Othello, Poker, Sketch, Tetris and Triangle Solver. Sudoku's further union-copy
permission mismatch remains under investigation. Original project sources were
verified unchanged. Broader acceptance and remaining guards continue.

### Union value copies retain payload permissions

The native permission checker now follows the reference's distinction between
copying a union value and creating a writable slice alias. Copies and stores of
inline union payloads remain independent values. A copied union that contains a
readonly slice retains that payload's permissions when narrowed, including
copies from readonly containers and calls through ordinary helpers.

All 336 combined union/reference/slice/borrow-model/local-ownership/editor/
assertion-hygiene tests passed. All 34 Rust unit tests passed in debug and release;
Clippy passed with warnings denied. Sudoku's copied project now builds with zero
diagnostics, clearing the final mismatch from the initial 19-project sweep.
A final uniform project/suite acceptance run remains required after the further
array-constant context mismatch is corrected. No original project sources were
changed.

### Constant array context

Array hints now preserve the reference's distinction between a native array
literal and a conversion expression. Nested arrays, record fields/defaults and
explicitly typed constants remain compile-time constants; nested distinct array
elements retain their compound-literal context. Runtime distinct conversions and
boxing into `any` keep their existing behavior, and invalid slice conversions or
length mismatches remain errors.

All 160 array/compiler/resolution/concatenation/numeric/generic/assertion-hygiene
checks passed, including checked and unchecked runtime comparisons. All 34 Rust
unit tests passed in debug and release; Clippy passed with warnings denied.
The three former compile-time slice exclusions also pass and are now included
in the permanent reference acceptance suite. The full native suite, expanded
original language fixtures and uniform project-copy sweep continue; this is not
yet a claim of whole-language acceptance.

### Exact constant arithmetic foundation

`bigconstantvalue` now performs logical-number arithmetic directly on canonical
arbitrary-precision contents, including truncating division/remainder, floor
modulus, signed bitwise operations, comparisons, shifts and integral powers.
It preserves the reference's domain and result-size checks without a machine
floating-point conversion. The internal `big_protocol` harness exposes these
operations for differential checks. Source constant bindings are not connected
yet; normal Rust commands still report that migration gap.

The four permanent arithmetic tests compare 4,892 cases against the actual Python
constant evaluator. The combined arithmetic, exact-value protocol, reconstruction
and assertion-hygiene gate passed all 26 tests. Debug arithmetic comparisons and
Clippy also passed. An isolated optimized prototype passed the same arithmetic
boundary corpus with debug assertions and overflow checks disabled.

### Parity-first resolution work

The expression-coercion predicate now follows the reference's syntax-sensitive
rules, including untyped comparison results, shift counts, both arithmetic
operands, conditional arms and constant container lengths. Array membership
uses that predicate when selecting its element type. The focused resolution,
compiler, numeric, membership and assertion-hygiene gate passed all 117 tests;
debug and release unit tests and Clippy also passed. This supplies the same
coercion decisions to the remaining logical-number lowerer.

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

### Pure projections

Resolved record projections now participate in pure-body checks. This includes
`using` parameters, standalone statements and local declarations, with the same
borrowed storage and owned-result behavior as explicit selectors. Helpers may
mutate or replace headers in records created by the surrounding `fn`; assigning
a borrowed slice to a promoted field does not make it owned. Returned borrowed
views are copied by the existing pure-result promotion path.

The resolver also retains the declared specialization of a constrained type
slot (`$T/Record`) before call instantiation, following Python's `type_node`.
An unconstrained pure `using` parameter remains a source error with the reference
message, rather than an unported-feature exit. This is another portion of
checkpoint 3; native-selected conditional effects still need completion. The
pure-projection, generic-branch, record, storage and resolution gate passed all
83 tests, including checked and unchecked tracked runtime cases. Clippy passes
with warnings denied.

### Conditional pure effects

Generic `when` statements and expressions retain conditional purity failures in
the native checked body. Inactive branches do not reject an otherwise valid
instantiation. A transitive failure is attached to the call or constant reference
inside that branch, with original Thor locations and related pure call paths.
Ordinary procedure bodies keep their original behavior. Call-analysis memoization
includes the conditional use so a first visit in one branch cannot suppress a
failure in a later branch or an unconditional call.

The remaining generic ownership gap is separate: unresolved storage provenance
still rejects cases such as a generic fixed-array copy followed by an element
write. Completing the reference's symbolic permission propagation is the next
part of checkpoint 3. Compile-time helper execution has not begun.

One explicit reference exception is preserved by
`test_unused_default_branch_records_a_reference_false_positive`: Python combines
the default-use requirements of `helper(value)` and an inactive `helper()` call
inside the same generic function. It then incorrectly rejects the supplied-value
call if the unused default reads runtime state. Rust accepts that supplied-value
path and rejects the selected omitted-default path. The frozen Python snapshot
has not been edited to hide this discrepancy.

The conditional/branch/native-compiler regression gate passed 87 tests, followed
by 15 transitive-call and explicit-type checks. Clippy passes with warnings denied.

### Symbolic ownership primitives

`permissions.rs` ports Python's `Access` and `Permission` operations, including
canonical joins, conjunction, caller substitution, type traits and bounded path
projection. `borrowflow.rs` ports the reference `Flow` primitives using arena IDs
for cyclic metadata: member/item/dereference access, header copies, joins, writes,
and stack-owner inspection. These modules are not wired into lowering yet and
do not remove the remaining generic ownership migration errors.

The permission protocol harness compares 753 generated and boundary expressions
directly against Python's algebra. All 22 Rust unit tests pass in debug and
release, the eight-test differential/assertion-hygiene group passes, and Clippy
passes for all targets with warnings denied. The next work is the AST flow
evaluator, binding/store fixed point and checked-call ownership arguments.

### Direct generic ownership and helper flow

The reference flow evaluator now guards direct generic `fn` storage operations.
Array and inline-record copies own their copied storage; slice headers retain
borrowed backing storage, including reference-containing elements. Native type
predicates are emitted in the active capture environment and selected native
`when` arm. Header/do-body checks and nested pure bodies retain their lexical
scope. Fixed-capacity dynamic types also retain their capacity through ordinary
inference, including addressed arguments used for overload resolution.

`borrowbind.rs` and `borrowprogram.rs` port helper argument binding, stored aliases,
stack escapes, transitive requirements and the fixed-point driver. They are
independently compared with Python using actual parsed ASTs, but their helper
profiles are **not yet connected to lowering** at this checkpoint. A variadic
alias case reaches the same nonconvergence rejection in both analyses; the test
records that rejection rather than claiming successful handling.

The 33-test focused ownership/permissions/assertion-hygiene gate passes, as does
the expanded ten-case helper-model comparison. All 24 Rust units pass in debug
and release, and Clippy passes for all targets with warnings denied. A broader
517-test native run found only two stale expectations of an unported generic
copy error; their corrected eight-test module passed separately. That broader
run is not recorded as a clean full-suite acceptance run. Checkpoint 3 continues
with checked-helper ownership arguments; execution and result caching remain
unported.

### Checked helper ownership profiles

`ownershiplower.rs` connects the reference symbolic requirements to checked
procedure variants using zero-sized native type metadata. The original proc
signatures and ordinary shallow ownership stay intact. Checked calls carry their
caller's permissions; stored aliases, array/record copies, pointer redirection,
value/reference iteration, type-switch views, append/pop and copy operations keep
their reference provenance. Helpers that modify caller-owned storage use the
surrounding scratch lifetime rather than freeing their output allocations on exit.

Checked variants remain lazy, including empty parameter lists and mixed fn/proc
overload groups. Metadata is supplied to every checked member of such a group.
Ported pure paths use the reference native guards instead of eager concrete-summary
rejection. Their diagnostics retain Thor source locations and identify the fn call
that imposed purity, with the implementation as a related location.

Variadic binding preserves the distinction between individual arguments, packed
slice arguments and `**` array/record spreads. The reviewed installed
`core:slice.clone` and `core:strings.clone` contracts follow the reference, including
constant aliases and selected imports. Slice cloning owns the outer buffer while
reference-containing elements keep their borrowed provenance. Same-named local
procedures receive no SDK trust; explicit allocator selection remains ineligible.

A second explicit reference exception is recorded by
`test_constant_string_clone_records_a_reference_evaluator_false_positive`.
Python automatic evaluation incorrectly adds `checked_call_ownership=struct{}{}`
to a reviewed `strings.clone(input)` call without that native parameter. Rust
accepts the valid call. Runtime-argument clone calls are also compared normally
in checked and unchecked builds. The Python snapshot remains unchanged.

The 67-test checked-call/purity/helper/branch gate and the separate 41-test
resolution/type-parameter/variadic gate pass. The 17-case real-AST helper-model
comparison passes, including the reference nonconvergence rejection. The graph
edge tests include all 24 append/pop combinations and ten stack-escape forms.
The full native run is still in progress at this checkpoint; it found the mixed
overload regression before the fix, which the 67-test gate subsequently verifies.
This is not yet whole-suite acceptance. Remaining checkpoint 3 work includes SDK
helper profiles, comprehension bindings and incomplete diagnostic selection among
multiple fn callers. Compile-time execution/result caching remain unported.

### SDK graphs, comprehensions and source errors

The ownership graph now inspects the reference's full helper closure, including
installed SDK bodies and inactive source branches, while native checked-call
selection still determines which contracts are required. Reviewed clone and I/O
contracts retain their special reference treatment. `#assert` and `#config` are
recognized as directives rather than unresolved calls. Comprehension iteration
bindings and flattened results preserve the difference between inline copies and
borrowed reference elements.

Definite missing-callee and argument errors remain native source errors instead
of migration failures. An anonymous procedure call gets the reference purity
error. A parenthesized named helper is inspectable and receives a checked variant.
This last case is a third explicit reference exception: the frozen Python
procedure-candidate lookup does not unwrap that initializer and rejects the valid
call. `test_rust_pure_call_errors.py` records both outcomes without changing Python.

Permission diagnostics select the failing fn caller using native instantiation
notes when a shared helper also has valid callers. Odin's textual note columns
count Unicode scalars; Rust converts them to UTF-8 map offsets before producing
the original source's UTF-16 editor coordinates. Both ASCII and emoji-prefixed
caller tests cover this path. Type-schema diagnostics retain their existing
conservative caller notes until the integration checkpoint.

The expanded real-AST ownership-model comparison passes all 22 cases, including
SDK inspection. One resolved `slice.clone` input needs a constant type predicate
where Python leaves an equivalent native sequence predicate; that exact precision
difference is checked explicitly. The ten graph-edge tests, 66-test conversion/
checked-call/conditional gate, four source-error tests and two caller-diagnostic
tests pass. Checked and unchecked tracked runtime configurations are included.
All 24 Rust units pass in both profiles. A fresh broad native run is still in
progress, so this is not recorded as whole-suite acceptance. Checkpoint 3 continues
with nonconvergence handling and remaining eligibility gaps; execution is unported.

### Native eligibility and graph-failure contracts

Nonconverging provenance now installs the reference's failing checked-body
contract. Unused generic functions remain lazy and ordinary proc calls remain
unchanged. Generic overload groups retain native candidate selection; effects
from an unselected implementation do not invalidate the selected implementation.
Generic formatting and transmute predicates are checked inside the selected
native helper. Definite invalid overload arguments keep native candidate errors.
Unknown signature types and unsupported SoA schemas reach the reference's native
type checks rather than an unrelated migration error.

Checked helpers can reach bodies through transitive public re-exports without
requiring a direct user import. Valid single-statement scopes such as
`if flag do return value` retain owned-result promotion. Checked-variant edits
are isolated from original proc bodies; invalid whole-procedure `do` syntax
remains an Odin syntax error. Unresolved `auto_cast` targets use the reference
source-contract message.

The broad native comparison run passed 576 tests. A subsequent 127-test focused
gate passes for checked calls, conditional effects, ownership, conversions,
auto-casts, source errors and real-AST helper models. The corrected transitive
re-export test and two final procedure-syntax/result-lifetime tests pass separately.
Rust's 24 units pass in debug and release, Clippy passes with warnings denied,
and the seven assertion-hygiene tests pass. The broad run predates the final
eligibility changes; these results do not replace checkpoint 8 acceptance.
Evaluation-mode signatures and specialization scheduling are the next porting
work. Big-number integration and complete diagnostic/editor parity retain their
later checkpoints.

### Execution foundations (checkpoint 4 in progress)

`specialization.rs` ports lexical bindings, relevant-binding selection, stable
fingerprints, saved caller frames, helper type spelling, reconstructed binding
declarations and specialization predicates. Local types require their original
lexical scope. Cross-package types use helper-only imports and preserve nominal
identity; generated import identifier spelling is normalized in comparisons.

`evaluation.rs` now extracts declaration/procedure dependency closures with the
reference decision order. Selected overloads retain their implementation rather
than unused overload bodies. Type queries retain their type definitions without
copying runtime operands; runtime globals and unrelated package init/fini bodies
are excluded. Selected aliases, conditional definitions, local constants, reviewed
clone contracts and invalid deferred/using contracts have differential fixtures.
Explicit `#run` requests also trigger SDK body inspection, including transitively
reached packages. The purity analyzer has an entry point for an explicit retained
root set, so later helper compilation can exclude unrelated fn declarations.

The 26-test execution-foundation gate passes, including installed `math.sqrt`
extraction, wildcard import handling, lexical specialization comparisons, and
the existing no-fallback/unsupported-feature checks. All 24 Rust units pass in
both profiles, and Clippy passes with warnings denied. These analysis modules
are not a completed evaluator: helper generation, bounded execution, generic
scheduling and result installation/cache integration are still pending. The
normal Rust command continues to reject unported `#run` programs explicitly;
Python remains the supported default.

### Isolated native execution (checkpoint 4 in progress)

`evaluationlower.rs` renders a pruned helper using the original AST identities
and source fragments. Local declarations return to their original lexical
scopes; selected targets and private re-exports get compiler-only access paths.
`evaluationexec.rs` constructs the helper from the selected source snapshot,
compiles it with a bounded subprocess, executes it with a timeout, validates the
contents-only result packet and removes the temporary project. It does not
rediscover unrelated files in a file-entry build. Scratch allocation and result
promotion follow the reference, and entry arguments remain read-only.

Inspected SDK packages use isolated package names, preserving the evaluator
runtime's original SDK dependencies. Compiler-owned base namespaces stay
external. Original test/build context and file selection are frozen before the
helper is made executable; test sources get normal executable-source filenames.
Compile errors and runtime failures map to original sources, with underlying
failure notes. Generated scratch defers no longer invalidate a diverging panic.
Qualified builtin aliases retain their native purity/type-query identity, and
literal coercibility no longer changes native typeid comparisons.

The 53-test extraction/specialization/execution gate passes, covering checked
and unchecked execution, SDK calls, generic branches, owned results, local and
cross-package declarations, optional status, build context, panics, bounds
errors and timeouts. A separate 66-test purity/ownership/assertion-hygiene gate
passes; its run predates the final typeid normalization. Rust's 24 units pass in
both profiles and Clippy passes with warnings denied. The independent execution
harness is not command integration: known/active and generic scheduling, nested
requests, result installation and cache policy remain. Normal Rust commands
still explicitly reject unported `#run` rather than invoking Python.

### Concrete scheduling and result reconstruction

`evaluationschedule.rs` follows the reference's known-input and active-branch
decisions for concrete requests. It distinguishes constants from runtime and
immutable storage, leaves optional debug calls native, executes required calls,
handles nested requests and preserves lazy boolean, conditional and switch
selection. An inactive request inside a retained dependency is also carried to
the helper. Compile-time comprehensions check their complete bodies and become
fixed native arrays, including filtered, flattened and empty results.

`evaluationresult.rs` installs contents using the original native result-type
witness. Runtime strings/slices/dynamic arrays receive independent storage;
constant results do not own allocated buffers. Local runtime storage used only
by a type query stays out of the constant witness, while a separate unevaluated
check retains the original query's validation. Evaluated array lengths are
available to subsequent inference. These paths are exercised by compiling and
running the installed output in checked and unchecked release configurations.

One additional frozen-reference exception is recorded by
`test_dependency_constant_request_is_evaluated_first`: Python adds a helper-only
generic parameter to `leaf` while reusing an already evaluated `VALUE::#run
leaf()`. That invalidates the original signature witness in the next helper.
Rust retains the original signature and produces both expected results. The
Python source remains unchanged.

The clean combined scheduling, execution, extraction and specialization gate
passes all 80 tests. It includes compiled checked/unchecked scalar and slice
results, constant comprehensions, downstream generic array-length inference,
local type-query validation and inactive nested requests. All 24 Rust units
pass in debug and release, and Clippy passes with warnings denied.

Generic caller frames, per-specialization substitutions and original nominal
identity probes are still pending. The scheduling/reconstruction harness does
not enable evaluation in the normal Rust command. Contents caching, logical
numbers and final command/editor integration retain their later checkpoints.

The next generic-frame foundation connects `Specializations` to the resolver's
declaration-keyed type and constant environments. Entering/restoring a binding
environment clears dependent type/context facts; an empty environment retains
the ordinary lookup path. Local record types capture the generic types and
dimensions they use, so those facts survive after the active frame is cleared.
The 37-test lexical-binding/specialization/type-parameter/generic-branch gate
passes, including nested shadowing and boolean, UTF-8 string and enum constants.
Clippy passes with warnings denied. The scheduler has not yet adopted these
environments or emitted per-specialization result cases.

### Native generic evaluation frames

The scheduling harness now traverses user generic procedures in declaration-keyed
lexical frames. Explicit type and value parameters, inferred array dimensions,
named arguments, nested and recursive generic calls, and captured outer constant
parameters retain separate result and inactive-branch records. Entering a child
frame restores the caller's type and constant facts on exit. Installed Odin SDK
procedures remain outside this user-body traversal and continue through isolated
helper dependency extraction.

The emitted program selects a specialization's reconstructed result with native
compile-time predicates. Uncalled generic procedures and inactive branches stay
lazy. Required unresolved specializations receive the reference source-error
fallback; optional runtime calls retain their native fallback. Reconstruction
helpers for different owned result contents have distinct identities, so two
specializations cannot accidentally materialize the first one's slice contents.
The original procedure signatures remain unchanged.

One explicit frozen-reference gap is covered by the enum-constant regression:
Python's known-value test silently skips `generic(Kind.one)` and
`generic(Kind.two)`, leaving their inner requests unevaluated. Rust recognizes
the qualified native enum constants, evaluates the two requests independently,
and compiles and runs the result as `1 2`. The test requires the reference's
empty specialization registry explicitly and checks both Rust packets and the
native runtime output. Python has not been changed.

This completes the directly nameable generic-frame portion of checkpoint 4.
The combined execution, extraction and scheduling gate ran 88 checks: 87 passed,
and the enum test initially expected Python's missing results. Its explicit
reference-gap expectation then passed separately, including native compilation
and execution. The adjacent resolver/lexical-binding/type-parameter group passed
48 checks. All 24 Rust units pass in debug and release, Clippy passes with warnings
denied, and assertion hygiene passes seven checks.

Caller-local nominal types still need isolated generic bridges and original-scope
identity probes. Remaining eligibility details, command adoption and the contents
cache are also pending. Python remains the default and frozen reference, and
normal Rust build commands still reject unported evaluation requests explicitly.

### Caller-local native type identities

Isolated evaluation can now use a caller-local nominal type even when the
request belongs to a different package. The helper reintroduces the original
local declarations at a concrete caller, invokes a generated generic bridge,
and serializes the result with native type-name metadata. Reverse imports exist
only inside the helper. The original program retains its package dependencies
and procedure signatures.

The scheduler probes named type identities in their original defining scopes.
It maps the native scope positions through exact source-map segments and settles
result-predicate widths before final emission. Local records that capture an
outer generic constant carry the matching condition into their probe. Equal
shapes do not merge distinct nominal types. Probe builds consume only tagged
compile-time diagnostics and never execute the program; using the build command
also preserves support for build-only flags such as `-o:speed`.

Three additional frozen-reference gaps are explicit in the local-type tests.
Python's helper fails to name a caller-local `distinct` type; it reuses the first
result for a local record that depends on different enclosing constant values;
and its identity probe forwards `-o:speed` to Odin's `check` command, which rejects
that flag. The tests pin those reference failures/partial registries and verify
the Rust packets, compiled runtime output and unchecked builds independently.
Python production code remains unchanged.

The integrated execution/extraction/scheduling/local-type run passed 139 test
runs, including a repeated scheduling group discovered through an imported test
class. The harness import has been corrected: the local module now discovers
its nine tests once. All 24 Rust units pass in debug and release, Clippy passes
with warnings denied, and the seven assertion-hygiene checks pass.

At this checkpoint, composing caller-local results into another helper still
needed helper-relative identities. That work is recorded below. Contents-cache
policy remains checkpoint 5, and normal Rust commands do not enable the scheduler.

### Replaying results inside nested helpers

Each helper now installs only result cases whose declarations and bound types
are present in its extracted program. This prevents unused materializers or
specializations from requiring pruned definitions. Retained caller-local cases
receive identities from that helper's own scopes, independently of earlier
helpers and the final program. Synthetic lexical wrappers carry scope origins
so scope-position changes, including changes in digit width, settle correctly.
Compiler-owned base types remain available through their original SDK sources.

Inactive native `when` branches keep the reference's lazy source-error fallback
without creating a specialization result. Inactive runtime branches retain their
ordinary result type; a local type can be probed even when execution never
supplied an initial identity. Probe snapshots preserve the original build mode.
Tests cover nested scalar and owned-record results, independent materialized
buffers, unevaluated runtime type queries, unused global-type specializations,
large source positions, active/inactive conditions and object-build context.

Two more reference discrepancies are explicit: Python can reuse a local identity
from the wrong generated helper, and a condition helper with a local type
parameter can leak NUL mapping markers into its emitted Odin. The regression
fixtures require those reference failures and independently verify the Rust
packets and native runtime output. Python production code remains unchanged.

The combined helper-replay/local-type/scheduling/execution/extraction gate passes
all 106 tests. All 24 Rust units pass in debug and release, Clippy passes with
warnings denied, and all seven assertion-hygiene checks pass. Execution-eligibility
and native-protocol details remain before scheduler/command adoption; this is
still a partial checkpoint 4, rather than whole-language acceptance.

The scope-offset remapper also preserves quoted constant-parameter contents.
Odin marks these string values with `$$`; text such as `"::$12"` inside a value
is not a scope address. Direct native metadata probes confirmed that spelling.
The regression covers escaped quotes, raw values, Unicode and quotes in filenames.
All 25 Rust units pass in debug and release, the three neighboring native scope
regressions pass, and assertion hygiene passes seven checks.

### Exact untyped integer evaluation

The independent scheduler now folds untyped integer powers with exact host
integers and applies the reference's signed/unsigned 128-bit result boundary.
The result remains an untyped literal until its native use supplies a type;
typed integer powers and floating-point powers still execute in the native
helper. Negative exponents retain the reference diagnostic. Trivial bases and
zero exponents accept large input constants without narrowing them to machine
integers. Constant-value generic parameters likewise preserve full `u128`
literal contents. The host implementation uses the locked `num-bigint` and
`num-traits` crates; it does not introduce implicit Thor `Integer` conversions.

Native unevaluated guards retain operand checking without forcing a wide
untyped result through a machine-integer helper. Tests cover signed boundaries,
overflow, aliases, right associativity, inactive branches, typed wraparound and
checked/unchecked runtime reconstruction. This extends the independent execution
harness; ordinary Rust commands still do not enable the scheduler.

The combined numeric/scheduling/specialization/lexical-binding run passed 64 of
65 tests; Odin terminated with SIGSEGV while compiling one typed-power helper.
That test passed separately, all nine numeric tests passed on the clean rerun,
and five further isolated repetitions passed. The crash was not reproduced and
is recorded as a backend failure, not a clean combined gate. All 27 Rust units
pass in debug and release, Clippy passes with warnings denied, and all seven
assertion-hygiene checks pass.

### Compile-time call eligibility

Extraction, argument readiness and checked calls now share reviewed library
identities and allocator-selection rules. The shared Thor formatter and big-number
library identities are tied to their installed source directories; native
libraries remain tied to their genuine SDK collection. Constant aliases preserve
that identity, while collection overrides and local look-alikes do not acquire a
reviewed contract. LLVM primitive recognition is shared with purity checking.

Explicit `#run` entries reject I/O before result-count or argument-readiness
errors, identify disallowed compiler intrinsics and imported bodyless calls, and
reject indirect runtime procedure values. Ordinary scalar/enum parameters named
`allocator` remain ordinary parameters. Real allocator selection retains the
reference scratch-allocator diagnostic. Nested known arguments and source-location
dependent compound types follow the same readiness rules as the reference.

The scheduler also follows Python's source-work and generic-traversal gates.
Projects without automatic pure-function work visit only sources containing
evaluation requests, unless a request requires traversing user generic bodies.
An isolated formatter test exposed the omitted gate in debug Rust; the same case
now completes in checked debug and release harnesses. This is the existing
reference scheduling policy, not a new evaluation or ownership rule.

The combined eligibility/scheduling/execution/extraction gate passed 109 of 110
tests. One new collection-override fixture omitted required support packages;
the corrected fixture and the clean 22-test eligibility group passed separately.
All 36 neighboring purity/ownership checks pass. All 27 Rust units pass in debug
and release, Clippy passes with warnings denied, and assertion hygiene passes
seven checks.
The final five-case indirection/primitive gate also passes, including two added
tests that distinguish a mutable primitive alias and an indirect nested argument
from their eligible direct-call counterparts.

### Expanded extrema constants

Expanded `min`/`max` calls now use one spread plan and builtin-identity classifier
in runtime lowering, purity checking and automatic evaluation. A required
constant is evaluated with the existing native reduction and reconstructed with
its sequence element type. Mixed scalar/array/slice reductions use that direct
element-type witness, avoiding a contextful generated call in a global type
query. Ordinary scalar builtins and runtime optional-status calls stay native.

The focused regressions compare fixed arrays, optional slice primary values,
empty slices, literal coercion, string/float elements, qualified imports and
aliases, nested `#run`, generic constants, unevaluated type queries and invalid
spreads. Checked and unchecked builds also verify source-order evaluation and
array snapshots through runtime output. The same-named procedure in a user
namespace remains an ordinary procedure.

The combined extrema/numeric/entry-eligibility/intrinsic gate passes all 58 tests.
All 27 Rust units pass in debug and release, Clippy passes with warnings denied,
and assertion hygiene passes seven checks. The independent execution harness is
ready for the contents-cache port and normal command adoption; Python remains
the default, and this does not declare whole-language acceptance.

### Contents-cache storage primitives

`evaluationcache.rs` ports the optional contents store, checksummed metadata
records, prepared-target links, atomic publication, counters and selective
clearing policy. Native packets can be read by Python and vice versa. Invalid
values, partial/corrupt/oversized records and unavailable storage are misses or
optional write failures. Clearing preserves unrelated names, directories and
symlinks. Concurrent writers publish whole packets through separate temporary
files. Native value validation currently uses the existing reconstruction API;
logical big-number packets will join it with the big-number backing port.

Canonical JSON keys preserve sorted object keys, ASCII Unicode escapes and
Python's finite-float notation. Generated float tests exposed the JSON parser's
default approximate decimal conversion; the locked `serde_json` dependency now
enables its existing `float_roundtrip` feature without adding a dependency.
The tests compare 212 finite float values as well as Unicode/control characters,
checksums, native packet interoperability and cache metadata behavior.

This is the storage foundation of checkpoint 5. Dependency/toolchain keys,
prepared and scalar-site reuse, scheduler integration and command adoption remain
to be connected. Ordinary Rust commands still do not consult this contents store.

All 14 storage tests pass against debug and release Rust harnesses, including
the generated canonical encodings and Python/Rust packet interoperability.
All seven neighboring value-protocol tests pass, including native checked and
unchecked reconstruction round trips. All 29 Rust units pass in debug and release,
Clippy passes with warnings denied, and all seven assertion-hygiene checks pass.

### Dependency snapshots for cached results

`evaluationinputs.rs` ports file-content/stat observations, package membership,
directory/tree snapshots, resource loads and path bindings. Identical-byte
rewrites preserve content-tracked inputs; metadata-only inputs retain exact stat
checks. Conflicting observations and unsaved source/content mismatches disable
reuse. Missing files and immediate directory-resource members are tracked without
including nested resources or generated cache/build folders.

Path bindings resolve existing symlinks even when the final file is missing.
Retargeting a link invalidates the snapshot, and cyclic links conservatively
disable reuse. On systems without the native stat fields used by this fast path,
the snapshot declines reuse rather than inventing a weaker signature. That only
affects the optional cache path. Toolchain identity, final dependency keys and
scalar/prepared scheduler reuse remain separate follow-up work in checkpoint 5.

The combined dependency/storage gate passes all 32 tests; the dependency tests
also pass through the debug Rust harness. All 29 Rust units pass in debug and
release, Clippy passes with warnings denied, and assertion hygiene passes seven
checks. A mutable reference snapshot in the initial differential-test harness
was corrected to copy its input, so one engine cannot update the other engine's
starting observations.

### Toolchain and emitted-result keys

`evaluationcachekey.rs` ports lazy toolchain identities, validated identity
records, resource hashes and keys for emitted Odin dependencies. Helper output
directories are normalized; target, flags, emitted Odin contents and resource
contents remain part of the key. Dynamic/unresolvable resources, disabled caches
and failed toolchain queries produce optional bypasses. The existing bounded
command capture is shared with identity queries; helper execution behavior is
unchanged.

Rust identities use an engine tag and the running compiler binary, plus the
frontend, runtime, Rust source and installed SDK. Editable `.thor` library sources
are included alongside `.odin` support sources. This deliberately covers the
plain-text basic library and keeps identities specific to the compiler engine.
Unrelated file extensions can change the metadata stamp without changing the
content identity. Metadata-record corruption recomputes the identity.

These keys remain independently tested APIs. The scheduler does not yet read or
publish result packets through them; scalar/prepared reuse and normal command
adoption are the next parts of checkpoint 5.

The combined key/dependency/storage gate passes all 46 tests. The two neighboring
native helper checks for successful execution and timeout handling also pass.
The 14 key tests pass through the debug Rust harness; resource hashes are
compared directly with Python, while engine-specific identity keys are checked
for stable reuse and required invalidation.
All 29 Rust units pass in debug and release, Clippy passes with warnings denied,
and all seven assertion-hygiene checks pass.

### Native contents-cache integration

The independent scheduler now passes its optional contents cache into helper
execution. Helpers retain their extraction, purity and source checks before key
lookup. Valid hits return decoded contents before launching the helper compiler;
cold successful executions publish only validated value packets. A resource-key
change during execution retains the reference stable-input diagnostic. Failed
evaluations never publish a value, and unavailable/bypassed storage preserves
ordinary compilation behavior.

The scheduling harness exposes cache counters for comparisons. Native tests
verify warm hits, changed bodies and flags, corruption recovery, new purity
violations, nested requests, caller-local type specializations and independent
owned reconstruction. Checked/unchecked output checks and allocation tracking
validate replayed storage; a compiler wrapper verifies that a warm hit launches
no helper compiler. The full key/dependency/storage/cached-execution group passes
all 57 tests. Normal Rust commands still do not enable the scheduler; prepared
and scalar-site reuse and command adoption remain outstanding.

### Local types in enclosing generic instances

Specialization fingerprints now retain the lexical condition of local nominal
types, including sequence element types. Odin gives a local struct, enum, union
or distinct type a different identity in each enclosing generic instance even
when the outer constant is unused by its fields. Merging those instances left
the second call without a generated evaluation case. Rust now keeps both cases.
The frozen reference still merges them, or fails its nominal identity probe;
the differential tests pin those reference outcomes explicitly.

The 20-test local-type/replay run passed all native output checks and found three
incorrect expectations about the reference's partially scheduled results in
unchecked builds. After pinning those results, the complete eight-case new
struct/enum/union/distinct matrix passed separately, in checked and unchecked
builds. The structural-sequence test also passed. Debug units, assertion hygiene
and Clippy with warnings denied pass.

### Prepared scalar result keys

The scheduler now ports the reference's early scalar lookup after dependency
extraction. Keys retain file-level syntax and declaration headers, selected
dependency bodies, prior nested results, query witnesses, native/support source
contents, enclosing signatures, flags, toolchain identity and resource inputs.
Unrelated runtime procedure bodies may change without regenerating a helper.
Generic, aggregate and source-procedure-dependent requests retain emitted keys.
Corrupt metadata or missing contents fall back to ordinary verified execution.

All 12 prepared-cache tests pass, including checked/unchecked replay, body/header
and resource edits, new impurity, imported dependencies and cache bypass. The
neighboring 11 contents-cache tests pass. A first resource fixture put `#load`
inside a pure body, which is ineligible under the reference rules; the corrected
fixture tests conservative resource invalidation without changing those rules.
Debug units, assertion hygiene and Clippy pass. Scalar-site reuse and normal
command adoption are still pending; Python remains the supported default.

### Verified scalar sites

`evaluationscalar.rs` ports straight-line scalar-site eligibility and replay.
Records validate source interfaces, lexical bindings, selected procedure bodies,
resource observations, native package files and raw collection-path bindings
before loading their contents target. Runtime shadowing, changed transitive
bodies and new impurity invalidate replay. Generic, conditional, `using` and
owned results keep the established verified paths. Missing targets flow through
the prepared and final lookups without repeatedly probing the same failed key.

Rust retains selected platform ASTs rather than every native file. Its scalar
snapshot therefore observes the full native package membership and contents of
unparsed members as well, avoiding immediately invalid partial snapshots.
The 16-test scalar-site group passes; the neighboring 12 prepared and 11 contents
checks pass. Two initial collection fixtures tried to override Odin's reserved
`core` collection, which Odin rejects; the corrected custom-collection tests
pass. Debug/release units, assertion hygiene and Clippy pass. Reuse is currently
connected through the independent scheduler after source discovery. Normal
command adoption, early SDK discovery avoidance and result-type witness
integration remain outstanding.

### Native coercion of floating constant parameters

Host integer folding no longer treats an integer source packet as the final
value of a floating/complex constant parameter. Those values and conditions
remain native, so Odin decides their formal coercion before branch selection.
Previously a rounded `f64` argument could suppress a required `#run` case.
Odin's compile-time `f32` behavior differs at the corresponding precision
boundary; tests preserve that native behavior rather than simulating a cast.

All 13 numeric scheduling tests pass, including positive/negative boundaries,
checked/unchecked builds and an inactive impure arm. A distinct floating alias
is valid native code but the frozen reference fails its helper compilation;
the differential test records that reference gap explicitly. The Rust output
matches the ordinary Odin program. No Python or Odin compiler source changed.

### Normal command evaluation pipeline

Ordinary Rust `check`, `transpile`, `build`, `run` and `test` now schedule native
compile-time requests after source inspection and install scalar/aggregate
results, generic cases, inactive-branch markers and verified scalar witnesses.
Helper/identity snapshots are marked scheduled and cannot recursively start
another scheduler. Restored scalar witnesses retain native result-type queries
without re-extracting their dependencies. Python remains the supported default;
there is no Python fallback in the Rust pipeline.

The 67 existing compiler regression tests and all 11 dedicated command checks
pass after adoption. The command checks cover explicit/automatic/nested requests, generic frames, SDK
calls, fixed-array comprehensions, owned reconstruction and original Thor error
locations. A first owned-result fixture returned stack-backed slice storage;
both compilers correctly rejected it. Its allocated-buffer replacement passes.
The capability report exposes the connected evaluator and cache components while
retaining `complete: false` and the remaining migration gaps. Source inspection
still precedes verified-site reuse; avoiding redundant SDK discovery is a
separate remaining integration/performance task, not a claimed speedup here.
All 29 Rust units pass in debug and release, Clippy passes with warnings denied,
and all seven assertion-hygiene checks pass.

### Constant concatenation and literal contexts

Concatenation now follows the reference for unresolved generic operands,
distinct arrays/slices, parenthesized empty literals and established element
contexts. Runtime-operand checks recognize compile-time parameters, constant
record fields, native builtins and named type conversions; runtime values still
fail before constant declarations are emitted.

Context witnesses retain whether the instantiated destination is an ordinary
array. Such bracket arguments remain native compound literals: even an identity
cast makes an otherwise valid array invalid as an Odin constant parameter.
The frozen reference has the same cast gap for an unconstrained generic's second
bare bracket argument. A dedicated test pins its rejection and verifies Rust's
accepted output against an ordinary Odin program; Python remains unchanged.

The 113-test concatenation/resolution/type-parameter/compiler gate passes.
Additional tests verify the reference cast gap and generic calls in checked and
unchecked builds. Debug units, assertion hygiene and Clippy pass. Native
constant-expression string counts are the next lowering gap.

### Native constant string repetition

`repetition.rs` now follows the reference's native doubling-chain renderer.
Counts may be native constant expressions, configuration values, converted
integers, record fields or generic constant parameters. Integer/nonnegative/size
checks remain compile-time checks in unchecked builds. Repetitions preserve
typed/untyped and distinct string identity, including zero/empty results and
`cstring` coercion. Nested helpers precede their consumers; hoisted helpers retain
lazy `when` and compile-switch selection. Generic signatures use the reference's
fixed-count inline form and explicit variable-count boundary.

Ordinary type inference now retains distinct type wrappers, matching the
specialized resolver; previously a named string operation could be mistaken for
native numeric multiplication. A generic-record test records the frozen
reference's invalid inline type query and verifies Rust's valid result against
ordinary Odin code. The reference source remains unchanged.

All 153 repetition/concatenation/type/overload/compiler/reference/assertion checks
pass. The group includes checked/unchecked programs, UTF-8 and arbitrary string
bytes, clear invalid-count diagnostics and a million-character result whose
generated source stays below 30,000 characters without execution-cache writes.
All 29 Rust units pass in debug and release, and Clippy passes with warnings
denied. Remaining arbitrary-precision and temporary-ownership paths are still
explicit migration gaps.

### F-string tuple and optional-result lifetimes

Anonymous f-string call scopes now preserve multiple return values and Odin's
optional-result tags. Boolean and allocator-error status slots retain their
native fixed types, while the other result slots use native signature queries.
SDK signature inspection is enabled for interpolation-bearing sources as well,
so ordinary imported calls such as `strings.clone` retain their optional error.
Overloaded and indirect calls use the selected tuple signature.

Lifetime discovery also crosses the native statement/block nodes inside switch
value arms. An inner call returning a borrowed string therefore keeps the
anonymous input alive until the enclosing expression finishes. Named strings
and `f!` arguments retain their existing ownership contracts; type queries and
lazy branches do not evaluate their arguments.

All 135 focused tuple/checked-call/type-parameter/command/compiler/assertion
checks pass. An additional pure owned-string tuple check passes separately.
The native memory checks run in debug and unchecked release with empty tracking
reports, including early return/continue, SDK optional errors and switch borrows.
One initial fixture incorrectly applied `size_of` to a two-valued call; both
compilers rejected it. The corrected single-result query test passes. All 29
Rust units pass in debug and release, and Clippy passes with warnings denied.
Other temporary-producer and arbitrary-precision lowering paths remain pending.

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
parallel lowering has been added. Threads only deliver/drain native subprocess
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
