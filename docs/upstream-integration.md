# Upstream Odin integration

The macOS ARM64 integration run uses 22 pinned repositories. Each applicable
configuration follows the same sequence.

1. Build or execute the original Odin command.
2. Copy the project with `thor migrate` and translate its command.
3. Execute the translated command against the converted copy.
4. Run `thor vet --apply` on the configured executable and test entry points.
5. Execute the translated command against the corrected copy.

The manifest contains repository revisions, selected compilers, command lines,
defines, collection roots and entry points. Test comparisons retain every test
name and verdict where the native runner provides JSON. Interpreter fixtures
also compare stdout. TOML conformance and headless browser journeys exercise
the built executables separately.

- [Pinned manifest](../tests/integration/odin_projects.json)
- [Setup, dependencies and runner instructions](../tests/integration/README.md)
- [Migration and command translation](odin-migration.md)

Local checkouts, converted copies and complete logs are under
`integration-projects`. Per-project reports are in
`integration-projects/results/<owner>__<repository>/report.json`.

## Run results

The completed macOS ARM64 matrix contains 165 usable command configurations
across all 22 repositories. Every configuration retained its native exit status,
reported test names and verdicts after migration and after vet corrections.
There are 6,212 reported individual test outcomes per stage, including 12
native failing outcomes. There are no unexplained comparison mismatches.

| Repository | Usable command configurations | Individual test outcomes per stage |
| --- | ---: | ---: |
| NateTheGreatt/odecs | 1 | 125 |
| GuilHartt/muninn | 1 | 39 |
| atomicptr/anima | 1 | 3 |
| benjamindblock/odin-mustache | 1 | 33 |
| Up05/toml_parser | 3 | 7 |
| blob1807/odin-aseprite | 1 | 3 |
| odin-engine/ode_ecs | 6 | 348 |
| Huseynteymurzade28/hnoss | 1 | 140 |
| foldcat/oasync | 1 | Compile only |
| ariaghora/anvil | 2 | 84 |
| karl-zylinski/karl2d | 3 | Compile coverage and API verification |
| pmbanugo/tina | 2 | 802 |
| BuLEEto/Skald | 1 | 108 |
| DanielGavin/ols | 2 | 1,078 |
| kvist-lang/kvist | 6 | 1,047 |
| BuLEEto/Runa | 13 | 251 |
| Samudevv/runic | 1 | 41 |
| dresswithpockets/odin-godot | 1 | 7 |
| krig/LISP | 4 | Interpreter build and three stdout comparisons |
| hubastard/speedrun | 1 | Host build |
| odin-lang/Odin | 4 | 2,096 |
| odin-lang/examples | 109 | Native and cross-target build/check configurations |

The TOML decoder additionally matched all 583 passing conformance cases and
98 upstream skips in each stage. Speedrun's 12 headless browser journeys passed
in each stage, giving 36 journey executions. The examples matrix reproduced
109 of its 115 native configurations. Six original example configurations and
Anvil's Flux configuration have unavailable native baselines.

The 12 failing native test outcomes comprise one Aseprite round-trip case, one
Tina simulation case and ten Runa shaping cases. Known crashing karl2d and
oasync runtimes have compile-only coverage. The two macOS crash dialogs from
`baseline-0-program` and `baseline-1-program` came from the original karl2d
AppKit tests.

The successful Skald vet pass applied 3,236 corrections and peaked at about
2.6 GiB. Its generated Odin source fell from hundreds of megabytes to 3.1 MB
after the pure-schema corrections. The largest generated file fell from
123 MB to about 45 KB. The SDK vet passes applied 1,694 ordinary corrections
and 31 further speed-configuration corrections, followed by all 2,096 SDK test
outcomes matching their baselines.

Large validation workloads still deserve performance work. The SDK's ordinary
vet pass took about 42 minutes with a 4.6 GiB monitored peak. Kvist's main suite,
which invokes the compiler repeatedly from its tests, took about 12 minutes
in the final verification. These measurements include validation and test
execution and do not establish negligible build overhead.

Skald's unchanged corrected test command originally took about 159 seconds
because SDK constant resource paths disabled compile-time result reuse. After
the resource-key correction, its first cache-populating run took 172 seconds
and its repeated run took 28 seconds, with all 108 tests matching. The repeated
run prepared and compiled no evaluation helpers. Its profile still contains
substantial type/call analysis and request-key work. The final native backend
accounts for about 2.7 seconds of that repeated run. The cold and warm profiles
are saved as `integration-projects/results/verified-skald-cache-*.json`.

The November 15 follow-up records further work reuse, correctness fixes, local
platform validation and updated timings. [Integration hardening report](integration-hardening-2026-11-15.md).

Compiler regression verification includes 86 Rust unit tests, 94 cache checks,
assertion-hygiene checks and the 155-case compiler/slice hardening run with one
SDK-dependent skip. Targeted pure-call, vet, diagnostic and language-server
regressions also pass. The cache summary is saved as
`integration-projects/results/final-corrected-cache-summary.json`, with its
additional Python interface-fingerprint check in
`integration-projects/results/final-python-excluded-headers.log`.

## Compiler corrections

The run exposed and corrected the following interactions.

- Native vet and style checks now examine a source-preserving Odin projection
  before the ordinary Thor compiler pass. This preserves native checks after
  vet introduces `fn`, `:>` and `#mut`. Original source ranges remain attached
  to diagnostics. Migration metadata preserves file-local native vet policies.
- Generic indexed assignments preserve the enclosing procedure's `or_return`,
  left-to-right destination evaluation, once-only operands, map descriptors and
  array backing storage. Packed fields and vector components retain native
  assignment behavior. Index-expression evaluation follows collection evaluation.
- Generated helpers preserve lexical `#no_bounds_check` and `#no_type_assert`
  settings. SOA column multipointers, SOA row addresses, enumerated arrays and
  addressable union assertions retain their native shapes.
- Native members named `Integer` and `Rational` no longer activate big-number
  lowering. Native swizzles and implicit map keys retain their contextual types.
- Slice analysis resolves the implicit `context` against the selected SDK's
  `Context` definition. Immutable allocator copies therefore retain their native
  reference-free shape. Native `#type` aliases, procedure types, packed fields and
  bit sets retain their type information during permission checks.
- Recursive permission analysis uses deferred parameter-relative references and
  finite symbolic paths while retaining conservative alias checks. This avoids
  expanding every unopened branch of cyclic record graphs.
- Extracted compile-time helpers tolerate defines consumed by the complete
  project. The final project build retains its ordinary unused-define checks.
- Inactive platform packages retain their generated directory and build-tagged
  source. Copied SDK tests continue using the selected backend's SDK collections.
- Native policy probes default to allocation tracking being off. Cross-target
  checking therefore avoids adding host-specific memory runtime dependencies.
- A separately selected parser-builder compiler preserves the backend's SDK and
  target configuration. Vet validation retains explicit compatibility mode.
- Source errors emitted before a native compiler crash retain their mapped Thor
  locations. Vet can reject an invalid proposed correction using those errors.
  Native failures without source diagnostics continue to abort validation.
- Vet rejects invalid immutable corrections together when mapped readonly-seed
  diagnostics identify their declarations. Other invalid batches retain the
  incremental validation fallback. Reference-bearing locals in generic procedure
  bodies retain their mutable declarations so other entry points can instantiate
  branches that require writable storage.
- Shared native imports alone no longer activate Thor's call-effect discovery
  pass. Ordinary Odin-shaped code avoids this analysis until a feature needs it.

Regression fixtures exercise these paths in checked builds and with
`-o:speed -disable-assert -no-bounds-check -no-type-assert`. Assertion hygiene
checks review native predicates and verify assertion-disabled setup behavior.

## Baseline limitations

Native baseline failures remain recorded in the reports. The runner compares
case outcomes for explicitly selected failing suites and skips configurations
whose native build or runtime is unavailable.

- karl2d's graphical test runners call AppKit menu APIs from a worker thread and
  abort in the native baseline. Their test executables are also compiled without
  launching them. The API verifier uses its original Odin documentation fixtures.
- oasync's native scheduler tests exhibited memory corruption and inconsistent
  runtime crashes. A compile-only configuration verifies migration and vetting
  without repeatedly launching that failing native runtime.
- Aseprite's marshal round-trip case and Runa's font-sensitive shaping cases have
  native failures. Their individual outcomes remain part of the comparison.
- Anvil's Flux example uses incompatible native APIs. The standalone odin-godot
  names tests are covered, while its complete macOS binding generator has a
  native platform gap. Several examples also fail their original native checks.

The preparation patches and runtime fixture adapters are listed in the runner
instructions. Original assertions and golden fixture contents are preserved.

## Native checker crash after source errors

Anvil validation exposed an Odin checker assertion on pinned revision `537fac5`.
The checker first emits source errors and then traps in
`src/check_type.cpp:2625` with `subst_apply: no instantiation found for 'Tensor($T)'`.
The original Anvil commands succeed. The failing input contains proposed vet
corrections that Thor must reject.

The local capture is under `integration-projects/results/repros/anvil-vet`.
It includes generated native sources, the backend command and debugger output.
A standalone 86-line native reproducer, license and single-worker backtrace
are now in [`upstream/odin-tensor-substitution`](../upstream/odin-tensor-substitution/README.md). Thor preserves
and maps the preceding source errors so the failed proposal can be rejected.

Generic unions retain their instantiated arguments during type/effect analysis,
including Odin's implicit `Maybe(T)` builtin in `core_builtin.odin`. Value-only union records stay
independent copies. Slice-containing variants retain read-only provenance.
Anonymous procedure expressions are analyzed in their own procedure scope.
Native pointer handles defer pointee analysis until a checked operation needs
it, avoiding eager loading of every namespace reachable through an SDK context.
Opaque returned slices preserve normal Odin permissions when there is no
potentially read-only slice storage in the project. Existing Thor views retain
the conservative callback-return check. Regression tests exercise checked and
unchecked builds.

### Native file-private generic linker collision

The SDK internal tests triggered duplicate native symbols for file-private
`not_const` specializations after sequence lowering introduced local generic
helpers. The saved generated Odin input also fails when built directly with
Odin revision `537fac56cfee40cd51967d509ef089aa0a71444b`. Its ordinary multi-module
build fails, while `-use-single-module` succeeds. The reduced capture and
per-hunk investigation are in `integration-projects/results/repros/sdk-internal`.
A portable copied-SDK reproduction and successful single-module control are
recorded in [`upstream/odin-private-generic-linker`](../upstream/odin-private-generic-linker/README.md).
Thor retries this specific unlocated generic-procedure duplicate-symbol failure
once with a single native module. Located source errors and ordinary duplicate
external symbols retain their normal failure. Parser/source/debug maps are
unchanged by this backend retry.

Default false/zero syntax flags and irrelevant empty calling conventions are
stored implicitly, including when older normalized syntax caches are loaded.
The complete wire representation remains available for AST output and cache
round trips. This reduces memory across compilation, vetting and migration.

Vetting conservatively preserves procedure declarations and reference-bearing
locals when their transitive source dependencies contain conditional compilation
or platform-selected files. A successful check for one target or `#config`
value cannot establish every inactive implementation. Scalar immutable fixes
remain available. The regression suite checks both define values and inspects
cross-platform wrapper declarations.

The shared `thor:typeinfo` runtime adapter selects value or pointer RTTI payload
aliases from the active SDK. Pure-value cloning, compile-time serialization,
deep cloning/freeing, and hashing use those aliases. Their native tests run in
checked and unchecked configurations against the installed SDK and an optional
second revision using `THOR_TEST_SECOND_ODIN`.

Native range indices from a nonnegative constant start in an exclusive range
stay native when their type fits the target index representation. Negative and
wide runtime index handling retains its normalization checks. This avoids
unnecessary generated helpers in common indexed loops.

Vet validation retains the native warning policy, including
`-warnings-as-errors`. A proposed edit must compile with the requested policy.
The internal dependency discovery pass preloads immutable-binding shapes and
follows additional call/type dependencies on demand, avoiding a scan of every
unrelated project expression during each validation.

Vetting also keeps reference-bearing local declarations mutable in procedures
that return reference-bearing data. Changing those locals to read-only can
change the returned access contract for clients outside the current executable.
That API choice requires an explicit source edit. Callback-return analysis
includes immutable inline arrays and record fields whose storage can be sliced.

The integration runner matches vet entry points to their native source, target,
defines, collection roots and file/test mode. A successful host build cannot
make an unavailable Windows or WebAssembly baseline eligible. Under
`-warnings-as-errors`, vet can apply its validated corrections and still return
failure for remaining unused parameters or locals. Those findings remain in the
logs, and the corrected build/test commands are compared independently.

Migration distinguishes official SDK collection references from copied project
packages even when the source project is the Odin repository itself. Qualified
procedure names and named arguments continue to match the selected SDK ABI.
Nested indexed addresses infer their element type without taking an address in
the type witness. Copying a slice or pointer header from a value parameter
preserves access to its referenced storage.

Discarded `_` results never introduce read-only storage. Treating their unknown
type as a possible view previously caused unrelated callback results to become
read-only and forced vet into repeated subset validation.

When no read-only storage can escape and call arguments carry no qualified
references, slice analysis leaves a reference-shaped return value unqualified.
It avoids expanding unrelated parser and allocator object graphs. Pure calls,
qualified arguments and generic bodies with local immutable declarations retain
their checks. A regression fixture bounds the summaries for a 40-procedure
factory chain. OLS vet validation completed in about 20 seconds with a 1.5 GiB
resident peak after this correction, followed by all 1,078 server tests passing.

SOA array row access retains native indexed-lvalue syntax when its binding came
from `for &row`. Runtime and negative indices, writes, addresses and f-string
captures are covered in checked and unchecked builds.

Native arithmetic division retains the typed operand during generic overload
selection. Type-specialization `/` syntax continues to resolve its constraint.
Compile-time evaluation uses a caller-file bridge when a generic binding names
a type private to that file. Contextual literal hints also respect file privacy.

Runtime-indexed SDK namespace arrays request the declarations needed to determine
their storage and element type. This avoids querying a namespace as a runtime
value while preserving global array storage identity.

Four SDK tests explicitly use Odin source filenames or compile native
structure-layout fixture programs. Their integration build overlays retain the
original fixture paths for `#file` and `#directory`. The migrated test bodies and
all assertions still execute unchanged. These source-dependent fixture contracts
are recorded separately from compiler source/debug locations.

Pure-type validation emits a small generic dispatcher and a separate record
schema validator. Odin copies a generic procedure body during specialization,
including inactive branches. Embedding all discovered record fields in that
dispatcher caused scalar checks at distinct diagnostic sites to duplicate the
entire schema and exhaust memory during Skald vet validation. Only actual record
checks now instantiate the field scanner. Field coverage, overlapping raw-union
rejection, recursive type checks and original diagnostic sites remain enforced.
The regression runs many scalar contracts alongside an unrelated large record
and verifies both generated code shape and checked/unchecked behavior in Python
and Rust.

Resolved contracts collect field names only from their actual record, union and
element types. Unresolved generic contracts retain the complete discovered
schema, with native field-count checks covering every instantiation. Checked
procedure variants share their source file's validator. This prevents unrelated
SDK schemas and repeated checked variants from multiplying generated source.
Vet's test-mode validation also preserves `-all-packages`, including entry files
whose tests are all declared in imported packages.

Unqualified complex and quaternion primitive names use the same primitive-type
lookup as qualified names. Their library return values retain reference-free
numeric types during overload and slice-permission analysis. Freezing one of
these values therefore permits ordinary copies and value arguments, including
boxing into `any`. Checked and unchecked regressions cover complex powers and
linear-algebra quaternion results.

Vet uses mapped pure call-chain root notes to reject the responsible `proc` to
`fn` proposal directly. Multiple roots in one diagnostic packet are rejected
together before validating the remaining batch. This avoids repeated subset
builds around procedures that mutate their callers' slice storage. A regression
covers shifted UTF-8 offsets, several invalid wrappers and an eligible procedure
that mutates its own local array through the same helper.
Direct writes through reference-bearing parameters are excluded before proposing
`fn` conversions. Address-taking stays distinct from mutation, preserving
eligible procedures that pass a read-only input to a reading helper.

Resource dependency tracking recognizes bounded, source-local string constants,
aliases, concatenation and conditional expressions. Both branches of a
conditional contribute resource paths, so platform-selected SDK checks such as
`#exists(LIB)` can retain compile-time result caching. File contents, missing
paths, directory membership and symlink bindings remain validated. Unknown,
cyclic, shadowed runtime and excessively large path expressions bypass this
optional cache. The regression suite checks resource invalidation and cache
reuse in checked and assertion-disabled builds.

Interface fingerprints retain the path and current exclusion decision for
parsed inactive files. Editing their inactive bodies therefore preserves reuse.
Discovery reparses their selection tags before lookup, so activating a file
restores its complete interface and invalidates the result. Unparsed native
package members remain content-tracked. A regression removes `#+ignore` and
activates a native package member.
