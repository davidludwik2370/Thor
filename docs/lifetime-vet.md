# Lifetime and leak checks in Thor vet

`thor vet` includes `lifetime` and `leak`, alongside the convention checks.
Select them independently when useful:

```sh
thor vet --vet=lifetime,leak
thor build --vet=lifetime,leak
```

Vet reports definite findings as warnings; build with `--vet` treats them as
errors. Unverified findings are summarized as nonfatal information by default.
Ordinary builds do not run this analysis. These checks add
no runtime instructions, allocation tracking, destructors, or ownership changes.
`--apply` does not add deletes, change ownership, or insert acknowledgements.

## What the results mean

* **Definite lifetime**: the tracked storage is known to be invalid at this
  operation: for example a read through a freed slice, double cleanup, freeing
  borrowed storage, or exporting references after scoped cleanup.
* **Definite leak**: a known heap allocation has no cleanup or ownership path
  leaving the procedure. Allocations through an unknown `context.allocator` are
  not placed in this category: an enclosing arena may manage their lifetime.
* **Unverified lifetime / leak**: the analysis cannot establish the required
  contract. This is a review request, not a claim that a runtime bug exists.
  Examples include opaque callbacks, foreign functions, a possibly relocating
  append with a separate view, conditional cleanup/ownership, casts that hide
  provenance, unknown allocators, and an analysis budget being reached.

JSON diagnostics expose `certainty: "definite"` or `"unverified"`. The codes are
`thor/lifetime`, `thor/leak`, `thor/lifetime-unknown`, and `thor/leak-unknown`.
Messages, original-source ranges, and related storage origins are also available
in TLS when Thor vet is enabled. Ownership findings have no automatic quick fix.

## Reporting incomplete analyses

```sh
thor vet                              # Definite findings and an unverified count
thor vet --unverified                  # Show individual unverified notices
thor vet --unverified=summary          # Explicit default
thor vet --unverified=show             # Same as bare --unverified
thor vet --unverified=error            # Show notices as errors and fail
thor build --vet                      # Unverified count is nonfatal
thor build --vet --unverified          # Details remain nonfatal
thor build --vet --unverified=error    # Incomplete analysis also fails the build
```

`--unverified` applies to `vet` and to `check`, `build`, `run`, `test` and
`publish` with Thor `--vet` enabled. It changes presentation and severity, not
analysis coverage, work budgets, native checking or generated runtime code.
It also covers `thor/correction-unknown`: a convention edit whose compiler
validation did not finish. Such an edit has no automatic fix. Validated manual
convention advice remains an ordinary warning.

For example:

```text
Unverified analysis: 10 findings at 5 source locations. These are incomplete checks, not confirmed bugs.
Show details with thor vet --unverified (keep the same source and build options).
```

Counts distinguish findings from source locations: a single boundary can leave
both lifetime and cleanup unverified. No notice is printed when there are no
unverified findings. Summarizing them is not a memory-safety guarantee.

`show` prints individual notices as `Info`; `error` prints them as `Error` and
returns a nonzero status for every supported command when such findings exist.
On that rejection, `run` and `test` do not execute the program/tests, and
`publish` does not package it.
Build may have produced its executable before vet rejects it; failure is
reported through the exit status. `--apply` still never adds cleanup, removes
ownership code, or offers an unverified correction.

Ordinary vet warnings and definite memory findings retain their existing build
error policy. `-warnings-as-errors` and `-ignore-warnings` apply to ordinary
warnings, not unverified information; use `--unverified=error` to escalate it.
Strict unverified errors cannot be hidden by `-ignore-warnings`. Explicit
`vet_ignore` acknowledgements below retain their documented scope and remove
the acknowledged notices from the count. They do not prove those operations safe.
`--unverified=error` escalates only unverified notices. To also reject ordinary
warnings in standalone vet, use `thor vet -warnings-as-errors --unverified=error`;
`thor build --vet --unverified=error` already rejects both classes.
Native `-vet` remains independent; combine it with `--vet` when both are wanted.
Passing `--unverified` with native `-vet` alone reports a usage error instead of
silently enabling a new analysis or ignoring the option.

JSON always retains individual findings in `errors`, even in summary mode.
Unverified entries have `type: "info"` (`"error"` in strict mode),
`certainty: "unverified"`, an explanatory `help` message and a `reason` category.
Their original codes, ranges and related storage/call locations remain present.
The optional top-level `unverified` object contains `mode`, `count`, `locations`,
`categories` and `definite_memory_issues`; `error_count` includes strict notices.
Automation should inspect these fields rather than parse human-readable text.

In VS Code, `"tls.vet": true` enables the checks and
`"tls.unverified": "summary"` is the default: one informational count per
affected file, with ordinary warnings and definite issues still visible.
Choose `show` for individual information diagnostics or `error` for errors.
Changing the resource-scoped setting restarts TLS. Other clients can pass the
equivalent flag through `checker_args`. Unsaved edits update the counts and clear
resolved notices. Unverified findings have no quick fix in any mode.

The absence of warnings does not establish that every operation was verified:
check the unverified count as well. Even a result with no findings is a bounded
static analysis result, not a memory-safe language guarantee. Unchecked casts,
foreign code, concurrency, and incomplete external ownership contracts cannot
be turned into proofs. An acknowledgement below records human review; it does
not extend the compiler's proof.

Reporting performance and regression coverage are recorded in the
[2026-11-23 validation report](benchmarks/unverified-reporting-2026-11-23.md).

### Reusing unchanged checks

Vet can reuse completed memory-check results when the same project inputs are
checked again. This includes definite findings and unverified boundaries; a
cached result never upgrades an incomplete proof to a verified one. Reporting
mode and severity are applied after retrieval, so strict mode still fails.
Native checking still runs, and convention edits are still validated separately.

The disposable `lifetime-v1` records share the [bounded per-user cache](storage.md)
with parser and compile-time records. Eviction is an ordinary cache miss.
Their identity includes the compiler/parser revisions, source graph, flags,
target and compile-time results. Dependency contents, package membership,
import symlink targets and supported `#load`/`#exists` resources are revalidated.
Unsaved/overlaid sources that differ from disk bypass reuse. Missing, corrupt,
unsupported or changed snapshots run the analysis again. `--no-cache` bypasses
this cache, and `--clear-cache` removes its entries too.

This is conservative reuse of the whole memory-check result, rather than
fine-grained reuse after editing one procedure. It avoids persisting the
analyzer's mutable reference graphs. `THOR_PROFILE` records
`vet.memory_cache_hits`, `misses`, `writes` and `bypasses`, together with key/input
validation time. Normal builds without Thor vet do none of this work.

See the [shared-analysis benchmark](benchmarks/shared-analysis-2026-11-23.md)
for cold/warm comparisons and the remaining SDK resource-snapshot limitation.

## Reference tracking

The analysis follows storage identities through local copies, aliases, slice
views, pointers, struct fields, collection elements, named/multiple results,
`using` projections, and inspectable procedure calls, including concrete callback
values and generic calls. A pointer to a local header is distinct from a copied
header. Scalar loads and inline fixed-value copies do not borrow their source's
storage. Copying a record containing a slice still shares that slice's data.

For example, this is a definite lifetime problem even though only `out` returns:

```odin
bad :: proc() -> [dynamic][]int {
    values~ := make([]int, 2)
    out := make([dynamic][]int)
    append(&out, values)
    return out // out's element refers to storage freed by values~.
}
```

Stores through output parameters and helpers are included, as are references
saved globally. An inspectable reader is distinguished from a retaining helper.
`#ro` and immutable slices prohibit writes; they do **not** promise no retention.
Unresolved callbacks and foreign procedures require manual review even when their
parameters are readonly.

Defers, inspectable native deferred-result cleanup, and scoped owners use lexical
exit and reverse registration order.
Native `when` branch braces do not create a runtime scope: their declarations,
allocator-context changes and defers belong to the enclosing scope. An explicit
nested block inside that branch still has its own lifetime.
The allocator context is captured when cleanup is registered, matching native
Odin defer behavior. The deferred expression still observes the final values of
its ordinary bindings. Changing `context.allocator` later does not change that
defer's captured allocator.
Arena reset/teardown invalidates its remaining allocations; it does not count
previously released members as double frees. Individual duplicate deletes and
uses of aliases after region teardown are still checked.
Arena identities include their field/element address, so resetting one arena in
a record or array does not invalidate a different arena in that same record or
array. A runtime choice between arenas retains the uncertainty of that choice.
Early returns, branches, loops, and switches are included; joins keep possible
alternatives. A reference used only after a conditional delete is unverified
rather than a definite use-after-free. Optional early exits with unprotected
allocations are reported as unverified cleanup paths. Loops and recursion that cannot be closed
within the budgets produce unverified findings. The current bounds per analyzed
root are 4,096 operations, 512 objects, 64 procedure invocations, 32 call and
value nesting levels, and four loop fixed-point rounds.
Expired helper-local slots without outstanding references are discarded before
joining loop states, while escaped stack addresses and deferred captures remain
tracked. Boolean short-circuiting, slice-bound calls, and the unconditional
producer effects of `or_else` follow runtime evaluation order.
Abstract external parameter shapes initially expand to three reference levels.
An access to an untouched borrowed tail can request another bounded projection:
at most 32 expansions per root, with 16-step syntactic paths and four possible
target slots. Opaque results/writes never acquire borrowed preconditions, and
querying a projection never replays a call or index expression. A
65,536-operation project budget prevents repeated complex roots from multiplying
analysis time; reaching it produces an explicit project-level unverified finding.

The analysis shares lowering's decisions for static immutable literals and
automatic expression-lifetime f-string cleanup. Named f-strings still need
ordinary cleanup. `f!` suppresses automatic argument cleanup and transfers that
responsibility to the receiving code. Pure-function scratch storage and result
promotion follow the existing managed pure-call rules. Comprehensions retain
their normal manually owned allocation semantics.

## Leak and allocator boundaries

Returned allocations and allocations transferred to output storage, globals,
or another escaping owner are not lost. An interior view alone does not prove that
the original allocation can be correctly freed; that transfer remains unverified. Unreachable allocation cycles are lost
just like other unreachable graphs. Shallow cleanup of an outer collection does
not free owned allocations referenced by its elements.

An explicit `runtime.heap_allocator()` is known heap storage. The lifetime of an
arbitrary allocator is unverified; the analyzer never assumes that an allocator
callback behaves like the heap. Known arena allocations are discharged by the
corresponding arena teardown. `basic.scoped_arena()` has its existing implicit
scope cleanup. This does not prove the absence of unbounded but reachable caches
or identify application-specific ownership hidden in integer handles.

Reviewed models cover native constructors, shallow cloning, collection stores
and cleanup; direct formatting and OS buffer I/O; and the documented arena APIs.
Allocated `fmt.aprint*`/`fmt.caprint*` results have independent owned storage;
formatting still checks reads of their inputs. `strings.fields`, `split`,
`split_n`, and `split_lines` own their outer slice and borrow the input text for
their elements. Freeing that slice never establishes ownership of its text.
Other standard-library/resource APIs are **not** guessed from their names: they
produce unverified boundary findings when reference ownership is involved.

Additional reviewed models distinguish these common safe patterns:

* `strings.builder_from_bytes` / `builder_from_slice` borrow their supplied
  buffer. Writes through that builder, `fmt.sbprint*`, and string/C-string views
  keep the buffer's lifetime. They do not invent an owned string allocation or
  an ownership transfer. An arbitrary builder with unknown backing still needs
  review. Reading a view after releasing its buffer, or returning a view of a
  local array, remains an error.
* Growing `strings.Builder` values own their byte buffer. The constructor's
  allocator is preserved across first growth; empty constructors and empty
  writes do not invent allocations. `to_string` borrows that buffer rather
  than allocating an independent string. It can transfer the buffer to a
  returning caller when the local header has only reviewed monotonic writes.
  Destroying the builder invalidates its views. A separate live view/header
  blocks a relocation proof; an abstract caller-provided allocator/buffer and
  custom formatters remain review boundaries. Destroying a fixed-buffer builder
  clears its header without freeing its borrowed buffer.
* `strings.fields_iterator` / `split_iterator` update the remaining string
  header and return borrowed tokens. Procedure-style iterators are invoked on
  each analyzed iteration, so repeated releases and retained tokens are checked.
* `utf8.runes_to_string` creates independent owned output, with the selected
  allocator. Primitive numeric `basic.sum` reads elements and returns a scalar;
  it does not borrow the input or take responsibility for freeing it.
* `slice.sort_by` rearranges the existing elements. An inspectable comparator
  is analyzed with their provenance; an opaque comparator or changes to shared
  reference state still require review.
* `basic.pmap` owns a new outer result slice, separate from its jobs. Known
  callbacks, including generics and aliases, are inspected. Reference-valued
  results preserve their source lifetimes, and shallow outer cleanup leaves
  nested allocation obligations intact. Opaque callbacks and shared reference
  changes remain unverified. This model does not prove race freedom.
* `pop` carries an element's references into its result. Discarding an owned
  nested element still leaves an undischarged allocation; passing it to another
  collection does not itself imply a leak. Collection summaries can still need
  review when per-element cleanup depends on runtime lengths or loop budgets.
* Fixed-capacity append and `copy` copy element values, rather than the address
  of the source buffer. Inline records can therefore outlive that buffer;
  pointer/slice fields inside copied elements still borrow their original
  storage. `remove_range` releases neither the outer buffer nor owned nested
  elements. Removing an element never discharges its cleanup obligation.
* Builtin `free`, including constant aliases, releases its target. Same-named
  user procedures receive no builtin contract. The formatting reader models
  include `printfln`, `eprint*`, `fprintfln` and `bprintfln`; their inputs and
  registered custom formatter boundaries remain checked. `fmt.assertf` follows
  native argument elision under `-disable-assert`, and a known successful
  assertion does not format its message.
* The installed `core:encoding/endian` unchecked 16/32/64-bit little/big-endian
  getters and setters read/write supplied buffers without retaining ownership.
  Released buffer targets still produce lifetime findings.
* Standalone root-level `resize`/`reserve` can be verified when the dynamic
  buffer has one tracked owner and no other live view or deferred capture.
  References held by a caller or an enclosing expression remain a proof
  boundary; the analysis does not assume those temporary views disappear.

Known reference-free records, arrays and scalar results do not acquire invented
reference provenance merely because abstract parameter expansion reaches its
depth limit. Actual tracked address provenance, including addresses converted
to integers, is retained. Lifetime vet loads missing native result declarations
on demand, including imported namespaces exposed through `using`; it does not
assign ownership to a foreign scalar return. Foreign reference results and
unreviewed foreign effects still need review. This discovery runs only for
lifetime/leak vet, after native checking.

`strconv.parse_int`/`parse_uint` and the reviewed 64-bit variants read their
string and check optional output-pointer lifetimes. `os.exists` reads its path.
Reviewed `rand.choice`/`shuffle` preserve references within elements: choosing
does not clone a nested allocation and shuffling does not discard its ownership.
An explicitly opaque generator or exposed/replaced ambient generator retains
an unverified callback boundary.

The interpreter and cached proofs share the reviewed formatting, scalar math,
parsing, path-reader, endian and random-generation effect inventory. A helper
does not lose its proof merely because it calls one of these reviewed operations.
Argument evaluation, named argument order, default expressions and caller
reference provenance still apply. Borrowed formatting results cannot become
inline-value results. Custom formatters, replaced standard streams and exposed
file-stream dispatch keep their callback boundaries. Buffer-only formatting
does not acquire a dependency on standard output.
Pointer formatting retains the interpreter's verb-dependent consumption rules;
it is not upgraded to a definite pointee read by a cached print helper. Parser
count outputs overwrite their old integer provenance. Endian stores/loads
preserve encoded address provenance through the written byte positions, including
reordered named arguments. Formatting into a buffer containing tracked addresses
cannot count its overwritten bytes as a proven ownership-transfer path.

Native static slots persist across calls and are distinct per generic
instantiation. References retained in them are checked like other escaping
storage. A nil branch at a join is a real alternative, not an empty accumulator:
discarding an owner on one path must not be mistaken for unconditional transfer.
Bounded proofs cover local empty dynamic-array headers that are only appended
to and returned unchanged on every return path. Header replacement, opaque
address escape, different returned headers, and nested owned elements do not
receive that monotonic-header shortcut. Promotion skips edge-free values
without clearing existing uncertainty or explicit integer address provenance.

## Reviewed foreign contracts

The installed Odin `vendor:raylib` 6.x binding has narrow text/window contracts.
`DrawText` and `MeasureText` read their text argument during the call. Freed
text remains a lifetime issue. `InitWindow` and `SetWindowTitle` retain the title
pointer; replacement or `CloseWindow` releases that tracked borrow. An empty
`InitWindow` title can reuse the previously retained pointer, so its lifetime is
still checked. A locally scoped buffer cannot be exported as a persistent title.
Registered foreign callbacks keep an explicit review boundary. Other foreign
functions, substituted collections and different binding major versions do not
receive these contracts by name alone.

These effects were reviewed against Raylib C revision
[`f5994221`](https://github.com/raysan5/raylib/tree/f5994221b4a1b57cae9e2f8871503cd309e12f14/src):
[`rtext.c`](https://github.com/raysan5/raylib/blob/f5994221b4a1b57cae9e2f8871503cd309e12f14/src/rtext.c)
and [`rcore.c`](https://github.com/raysan5/raylib/blob/f5994221b4a1b57cae9e2f8871503cd309e12f14/src/rcore.c),
including GLFW, SDL and RGFW title setters. They are lifetime contracts, not
claims that the graphics functions are allocation-free or pure.

`LoadImageFromTexture` creates an owned pixel-buffer obligation;
`UnloadImage` releases it. `ExportImage` reads the image and filename during the
call. `ImageFlipVertical` can allocate replacement pixels and free the previous
buffer: it is modeled as one current buffer only when there are no independently
retained pixel views, image copies, enclosing expression temporaries or opaque
callbacks. Otherwise the replacement remains unverified. An image copied by
value still shares its pixels. A normal memory `free` is not a substitute for
the matching resource unload. Image effects were reviewed in
[`rtextures.c`](https://github.com/raysan5/raylib/blob/f5994221b4a1b57cae9e2f8871503cd309e12f14/src/rtextures.c)
at the same pinned revision.

The installed `core:os` file APIs on Darwin, Linux and Windows have explicit
ownership contracts: `open`/`create` return an owned file on success; `close`
releases it through the native stream implementation. Standard streams are
borrowed native resources. Aliases preserve the same resource identity;
returning a file transfers its cleanup obligation. Closed owned-file use, duplicate
close and memory-free substitutes remain findings. Custom or copied `File`
records, changes to `impl`/`stream`, and other targets retain review boundaries.
Bounded success/error and standard-stream branch correlations are tracked.
More complicated resource choices can still require correlation the graph has
not proved; the model does not discard those warnings by assuming the open
succeeded. Qualified runtime globals are followed
through their real declarations; their native metadata is loaded only when
actually referenced by lifetime vet.
Closing a standard descriptor does not free its static `File` record; later I/O
can fail without being a memory use-after-free. `os.args` and its argument text
are borrowed process-lifetime storage owned by the standard library. Explicit
user reassignment of these globals still follows the newly stored references.

Contextually typed record literals and by-reference range bindings preserve
the same reference edges as explicitly typed records and direct assignments.
Expired scalar helper slots can be discarded without forgetting addresses held
by aliases, deferred work, or temporarily evaluated arguments. Reusing an
allocation site never revives an earlier freed allocation with a surviving
alias. If an analysis budget is exhausted, the diagnostic identifies that proof
boundary; skipped downstream cleanup is not reported as an invented leak.
Custom registered formatters also make formatting effects unverified. The
separate arbitrary-precision runtime and expression switches with statement
prefixes can also require manual review where no complete ownership model is
available. Growing
containers with outstanding views are not assumed to preserve their addresses.

## Intentional unverified cases

Use the existing declaration attribute, with an explanatory comment:

```odin
work :: proc(callback: proc(values: []int)) {
    // Reviewed: callback completes synchronously, never retains or frees values.
    @(vet_ignore={.lifetime_unknown})
    values~ := make([]int, 100)
    callback(values)
}

scratch_work :: proc() {
    // Caller installs an arena and resets it after this work.
    @(vet_ignore={.leak_unknown})
    scratch := make([]int, 128)
    _ = scratch
}
```

Combine them with each other or convention rules:

```odin
@(vet_ignore={.lifetime_unknown, .leak_unknown, .immutable})
values := make([]int, 128)
```

The acknowledgement follows the originating declaration through tracked aliases
and views. It does not silence other allocations. A procedure annotation can
acknowledge unknown effects rooted in its parameters; it does not silence local
allocation findings with tracked storage origins throughout its body. A procedure annotation can also acknowledge
originless proof boundaries, such as a whole-procedure analysis budget.
Annotation does not grant callees a
trusted no-retain/no-free contract, and it never changes the graph analysis.
Definite lifetime and leak findings remain visible. `vet_ignore={.lifetime}` or
`vet_ignore={.leak}` is an error, as are misspellings and empty entries. Compiler errors and
ordinary scoped-owner errors cannot be suppressed. TLS offers supported rules
after `.` inside the rule set, filters prefixes, and omits already selected
rules. Braces are required even for a single rule. Existing constant-string
annotations remain compatible.

Normal builds and generated runtime code are unaffected. Vet's graph is local to
the request; type-independent library models and existing resolver facts are
reused, with explicit budgets instead of unbounded analysis.

## Cached procedure proofs

Each vet request caches verified procedure summaries by declaration and generic
substitutions. Summaries cover reference-free value operations, non-retaining
readers and writers of pointers/slices whose elements are reference-free, and
verified scalar-global effects. Every global access has a constant/zero
initialization proof and a call-time check that its tracked value carries no
reference provenance. Global initializer calls are never skipped on a signature
alone; reference-bearing stores and disguised integer addresses fall back to
full analysis.
It verifies bodies, defaults and the complete dependency component before
publishing a summary, including recursive components. A signature, `fn` marker,
or readonly parameter alone is not a retention/cleanup proof. Failed proofs are
cached too, and use the existing graph interpreter.

Read masks propagate through helpers, named argument reordering, slicing and
`using` projections. Every summarized call still checks the lifetime of storage
it reads; header-only operations such as `len` do not pretend to read backing
storage. Values carrying tracked references cannot enter an inline-value shortcut.
Mutable runtime callback targets, deferred effects, allocation, reference-state
mutation, unreviewed foreign calls and reference-valued results retain
full analysis or explicit unverified boundaries. Unbound generic bodies are
checked at concrete call sites, rather than diagnosed as opaque external data.
Reads and writes propagate through recursive dependency components before any
member is published. Native assertion elision, known short circuits and
statically selected branches retain their normal evaluation semantics.

Summary construction is bounded to 16,384 AST visits per request, 1,024 per
dependency proof, 64 procedures per component, and the existing nesting limit.
Exceeding a summary budget falls back to ordinary analysis; exceeding the graph
analysis budget remains an explicit unverified warning. Cached summaries contain
contracts and read masks, never caller allocation identities. They add no runtime
tracking and are discarded after the request.

### Exclusive buffer replacement

A bounded proof recognizes a local slice/dynamic-array owner that is deleted
and then replaced with a fresh allocation in the same block. Inline elements,
non-retaining operations and exclusive ownership allow old/new allocation
generations to share one abstract current-buffer slot across loop joins. The
proof never revives a separately retained old view. Global stores, escaped
element addresses, resets inside a live reference range, reference-bearing
elements and opaque calls prevent this equivalence. Call-time graph checks
also protect integer-carried addresses, captured cleanups and inherited aliases.
Missing or conditional cleanup and overwritten owners still produce findings.

Proofs and failures are cached by declaration and generic bindings. A scan is
limited to 4,096 nodes, with 16,384 nodes shared across the request; fresh-result
helper chains have an additional depth limit. The existing owning-header proof
also permits dead helper-local header slots to be discarded after builtin-only
append use has proved that their addresses cannot escape.

### Bounded recursive record summaries

Closed pointer/record graphs can use cached field-traversal programs. They cover
readers and writers of inline scalar fields, including mutually recursive
helpers, named argument forwarding and cycles. Programs follow only the fields
the procedure actually traverses. Every concrete call checks the referenced
storage again; scalar fields carrying tracked addresses prevent an inline-result
shortcut. Symbolic incoming parameter tails remain borrowed preconditions,
separate from unverified pointers returned by opaque code. Mutated caller state
does not inherit those preconditions.

The domain is limited to 16 record shapes, 128 program states and 128 visited
place/state pairs per invocation, within the existing summary construction
budgets. Oversized or unverified cases use ordinary analysis and keep explicit
review warnings. Reference-field mutation, pointer permutations, allocation,
cleanup, opaque effects, unsupported nested containers and reference-valued
results do not receive this recursive shortcut.

See the [local project audit](lifetime-project-audit-2026-11-22.md) for alias and
scoped-value regression coverage, remaining proof boundaries, and timings.
The [model and summary follow-up](benchmarks/lifetime-models-summaries-2026-11-22.md)
records the six additional models, 15 Puzzle cleanup validation, cached proofs,
and paired performance measurements.
The [static-storage and effect follow-up](benchmarks/static-lifetime-effects-2026-11-22.md)
records attributed immutability fixes, growing builders, owning-result proofs,
reviewed foreign contracts, broader cached effects and the new project sweep.
The [contract and recursion follow-up](benchmarks/lifetime-contracts-recursion-2026-11-22.md)
records shared library proofs, native file/image ownership, exclusive loop
replacement, bounded recursive programs, negative regressions and paired timings.

## Scratch storage, branch facts and task lifetimes

Cached proofs can include local fixed buffers, their non-escaping views, and
inline-element `new`/`make` storage whose cleanup is registered immediately with
`defer` or `~`. The proof still checks every use and every called procedure.
Header reassignment, escaping references, pointer/integer conversions, opaque
callbacks and unprotected early exits reject the shortcut. Fixed-capacity
append/pop/clear operations on inline elements can also participate; growing or
reference-bearing collections keep their full ownership analysis.

A bounded native-file predicate domain relates an open result to its error and
relates a selected handle to borrowed standard streams. Conditional absence is
recorded across branches for up to eight native file origins. It is not a general
path solver. Unrelated files still need their own cleanup, overwritten status
values cannot prove success, and opaque mutation discards the facts.

Reference-depth limits count pointer/collection edges rather than inline record
wrapping. At completed loop back-edges, expired addressed locals in the current
frame can be discarded only after checking live references, exported storage
and captured cleanup arguments. Caller-frame expression temporaries and all
surviving aliases remain protected.

The verified installed `core:thread` pool model covers `pool_init`, `pool_start`,
`pool_add_task`, `pool_finish` and `pool_destroy`. It tracks at most eight queued
call sites. Known callback bodies are inspected and rechecked at completion;
only callbacks without shared reference/cleanup effects receive the inline-effect
shortcut, with closed reference-free payloads. Runtime branch facts from the time
of scheduling cannot establish that contract; both runtime branches are checked.
Nil payloads require a callback whose parameter use proves that its data is not
accessed. Queued data must remain alive until completion. A payload leaving its
stack scope early, use after release, or destruction before finishing workers
remains a finding. Custom hooks, opaque callbacks, direct pool-record mutation,
copied headers and unverified lifecycle combinations retain review boundaries.
This is a lifetime model, not a race-freedom proof.

## Allocator and byte-buffer checks

These checks belong to `lifetime`; they use the same definite/unverified codes
and never add runtime instrumentation or automatic source fixes.

* A release through two proven, different native allocator identities is a
  mismatch. Supported identities include the native heap and specific local
  arena instances. Pointer/slice/string releases use their selected allocator;
  dynamic arrays and maps use the allocator stored in their header. Scoped and
  deferred releases use the context captured at registration. Unknown allocator
  callbacks are not guessed to be distinct or interchangeable.
* Known byte storage has bounded extent/content metadata. Reviewed operations
  include `raw_data`, `mem.alloc`, `mem.copy`, `mem.copy_non_overlapping`,
  `mem.zero`, `mem.set`, byte indexing and unchecked endian reads/writes.
  Definite overruns, negative counts, nonempty operations on literal nil buffers
  and proven overlap in a non-overlapping copy are findings. Zero-byte operations
  do not require readable backing storage. Unknown lengths do not create a new
  definite buffer finding.
* `len(cstring)`, conversion of a C-string to a string, and the reviewed Raylib
  C-string consumers check known termination. Formatting also reads C-strings:
  native `fmt_cstring` converts to a string before dispatching on the format verb,
  including `%p`. A definite finding requires every
  possible tracked buffer to have no zero byte before the physical allocation
  ends. The physical storage can extend beyond a slice's logical end. Unknown
  contents, opaque writes, unsupported layouts and unresolved negative slice
  offsets do not become assertions that a terminator is missing.
* Byte contents use a uniform default plus at most 64 overrides. Branch joins
  retain only common facts. Copies preserve bounded known contents; formatters,
  I/O, summarized writers and asynchronous writers invalidate affected content
  facts. Potential growth invalidates stale extent facts. Encoded addresses keep
  their lifetime edges; partial raw byte copies cannot establish a verified
  ownership transfer merely by copying address fragments.

Cached writers invalidate scalar/branch facts as well as byte contents. The
effect follows forwarded fields and slice views back to their original storage,
including globals and every traversed node in a recursive writer. Readonly calls
and writes to inline array copies preserve the source's facts. A cached writer
cannot leave an old Boolean value that would hide a freeing branch.
Parallel `pmap` callbacks also check runtime branch alternatives rather than
assuming scheduling-time scalar facts hold across repeated callbacks. Comparing
their shared ownership effects ignores scalar-content changes, so writing an
integer alone does not become a shared-reference warning.

Cached summaries carry buffer-size and termination requirements to their actual
callers, including aliases and fixed-offset forwarding. Dependency propagation
has a 128-round limit and a bounded requirement size; unsupported cases use the
ordinary interpreter. These checks are deliberately incomplete for arbitrary
pointer arithmetic, non-byte layouts, external code and data races. Absence of a
finding is not a general memory-safety guarantee.

Call models distinguish named arguments from positional field expressions.
Passing `holder.pointer`, `holder.loc` or `holder.allocator` does not turn the
expression into a cleanup, location or allocator parameter annotation.

The [precision and memory-check validation report](benchmarks/lifetime-memory-checks-2026-11-22.md)
records regression coverage, remaining findings, measured vet costs and the
generated-code comparison for these checks.

## Bounded precision and additional memory checks

Field-only procedure summaries record the scalar fields actually read from a
by-value record, including nested fields and forwarding through aliases, named
arguments and generic specializations. An unused pointer field can be copied
without reading its pointee. Used pointer fields, address bits encoded in
integers, output stores and global retention retain their ordinary provenance.
Summary paths are limited to 16 fields and 32 projections per parameter. Failed
proofs use the interpreter rather than publishing a partial contract.

The separate convention preflight for prospective immutable reference bindings
is bounded to 100,000 abstract value slots, 4,096 procedure contexts and 200,000
proof checkpoints. Exhausting it keeps those reference edits manual; it never
turns a partial configuration or escape proof into an automatic correction.
Current-build validation still gates the advice. Correction validation also has
its own bounded proof. If it cannot finish, vet reports `thor/correction-unknown`
with no automatic edit rather than claiming that the proposed change compiles.
Already validated corrections remain available. These limits do not apply to
ordinary compilation or replace the lifetime/leak analysis and its diagnostics.

Empty slice literals have no backing-allocation lifetime, matching Odin. Empty
views of existing storage still retain their original references.

Loop joins discard completed helper-local slots only after checking remaining
aliases, pending caller expressions, deferred captures, allocator identities and
asynchronous borrows. A narrow two-buffer proof recognizes a verified header
swap followed by `resize(&second, 0)`. It canonicalizes analysis identities when
the buffers have distinct exclusive owners and the same allocator. It never
rewrites runtime swaps or assumes that a procedure named `swap` is trustworthy.
External views or ownership effects block that proof. A private allocation
released on a failure branch and returned as nil is distinguished from a live
successful result; returning or retaining the freed pointer still warns.

The physical-memory model extends the byte checks with:

* Target-aware sizes for supported primitive and fixed-array storage, typed
  accesses through raw pointers, element-scaled pointer offsets, and definite
  alignment violations. Custom/packed record layouts are not guessed. Unknown
  raw sizes, offsets or layouts remain unverified. Array/slice negative indices
  follow Thor's relative-index semantics rather than becoming negative raw
  offsets.
* Reviewed `mem.alloc`, `alloc_bytes`, `alloc_bytes_non_zeroed`, runtime allocation
  equivalents, `slice_ptr`, `byte_slice`, `ptr_to_bytes`, byte copy/fill/zero and
  `free_with_size` contracts. Borrowed headers retain the original allocation;
  a known nonzero cleanup size must match its original byte size. Zero is the
  native unsized-free convention. Unknown sizes are not guessed.
* Up to 64 definitely uninitialized intervals in known non-zeroed heap storage.
  Typed writes, partial callee writes, zero/fill operations and copies update
  those intervals. Byte copies transport known uninitialized ranges without
  treating the transport itself as a typed value read; later interpretation
  still checks initialization. Unknown
  writes discard the affected certainty; they do not prove a particular value
  initialized. This is deliberately separate from Odin's local `---` check.
* Integer overflow/narrowing facts used only when the resulting value feeds an
  allocation or reviewed buffer-size argument. These are unverified intended-size
  warnings, since wrapping can be intentional. Ordinary bit arithmetic elsewhere
  is not a new lint. Unknown runtime sizes do not become definite overruns.

Temporary `fmt.tprint*` and `fmt.ctprint*` results use the current temporary
allocator, including an explicitly substituted allocator and copied contexts.
Resetting that allocator ends the old allocation generations. New formatting
does not revive aliases to earlier strings; an independently cloned string
survives. The default per-thread temporary allocator is runtime-managed and does
not create an individual-delete obligation. Custom formatter hooks remain a
review boundary. Deferred allocator contexts and lexical/callee restoration
also include the temporary allocator.

In addition to pools, reviewed `thread.create`, `start`, `create_and_start*`,
`run*`, `join`, `join_multiple` and `destroy` retain borrowed payloads until
completion. Single and polymorphic payloads, aliases and known callbacks share
the existing bounded callback interpreter. `destroy` waits for completion;
`is_done` alone does not prove it. Detached/self-cleaning handles cannot be safely
joined or destroyed, and detached borrows can outlive a local scope. Releasing
an allocation or resetting its arena while a worker may still access it warns.
Caller-provided contexts, unknown callbacks and shared ownership effects remain
unverified. This analysis does not establish race freedom or model arbitrary
foreign callback registration APIs. At most eight native worker identities are
modeled per root; further cases use the conservative boundary.

### Reuse Odin's existing memory diagnostics

Thor forwards these native checks through its original-source projection:

```sh
thor vet -vet-nil-deref -vet-uninitialized -vet-atomic-access
```

They retain Odin's error policy and installed-SDK availability. Native stack
escape analysis, checked bounds/type assertions and sanitizer options also keep
their existing behavior. Thor does not implement a second local nil, `---`,
atomic-access or runtime bounds checker. In particular Odin regards passing a
local's address as initialization; Thor's additional heap/range checks address
only the separately tracked operations above. These facilities remain bounded
diagnostics, not a guarantee about unchecked foreign code or every heap read.

The existing intrinsic catalog supplies the list of atomic operations. Thor
models their reads/writes and retained pointer values for lifetime purposes;
Odin continues to check atomic ordering and ordinary versus atomic access.
Atomic scalar helpers therefore need no opaque-retention warning. Pointer
stores keep their possible escaping references, and atomic accesses to freed or
definitely uninitialized heap storage are still checked.

The [expanded precision and memory-analysis report](benchmarks/lifetime-memory-expansion-2026-11-22.md)
records this round’s regression, upstream and performance validation.
