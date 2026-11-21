# Allocation-free contracts

`@(no_alloc)` asks Thor to prove that a procedure or `fn` performs no runtime
allocation during ordinary execution. Fatal diagnostic reporting is excluded,
as described below. A failure to prove the contract is a **compile error**, in ordinary
builds as well as checks, tests, and vet. The attribute takes no arguments.

```thor
@(no_alloc)
add_to_each :: proc(values: []int, amount: int) {
    for &value in values {
        value += amount
    }
}
```

The checker follows selected direct calls, constant aliases, viable overloads,
and concrete generic specializations. Callees do not need the attribute. A
procedure's implementation must be inspectable; the annotation is never a
trusted exemption for a foreign procedure or an unknown callback. Ordinary
recursion is supported and each procedure specialization is checked once per
analysis. Diagnostics show the operation that could allocate and related
locations identifying the contract and its call path.

Use the file tag before `package` to check every procedure in a file, including
unused, nested, and anonymous procedures, and the file's runtime initializers:

```thor
#+vet no_alloc
package geometry
```

TLS offers supported file policies in a completion dropdown after `#+vet` and
after spaces or commas in its option list. Suggestions include descriptions and
filter as you type. An unfinished header does not immediately report the native
vet-bundle error; completed invalid settings still produce diagnostics.

This file policy does not spread to unrelated declarations in imported packages
or other files. Calls into them still undergo transitive checking. Proven
inactive `when` branches and excluded build files do not participate. Runtime branches are checked separately unless their condition or operation mode
is proven from the current call context. Loops, aliased writes, and unknown calls
invalidate affected callback facts; the checker does not assume that the first
loop iteration describes later iterations. There is no capacity analysis.

`#+vet no_alloc, semicolon` combines Thor's allocation contract with the native
semicolon policy. Thor consumes only `no_alloc`; other native vet checks keep
their native meanings through original-source validation. See the
[native vet audit](native-vet-audit.md) for supported checking-view forms. A bare `#+vet` does not
enable `no_alloc`. There is no `!no_alloc`, command-line vet switch, or
`vet_ignore="no_alloc"` exemption. Remove the contract to stop requiring it.

## What the proof permits

- Scalar arithmetic, fixed arrays and records, stack slice literals, reads,
  writes, and copies into caller-provided storage, where types are known.
- Direct calls whose implementations satisfy the same rules, including constant
  aliases and the reviewed scalar/type-query compiler primitives. Native SIMD
  vectors and matrices retain their distinct type identities, dimensions and
  matrix layout through generic specializations.
- Variadic parameters are borrowed slices inside their procedures. Iterating,
  indexing, and forwarding them does not itself allocate; typed reductions such
  as `basic.and(..values)` and `basic.or(..values)` are supported. Generic loop
  elements retain the concrete types from each call specialization.
- Native string indexing, pointer-to-array/slice views, type assertions and
  narrowed type-switch aliases, including addressable aliases. Primitive numeric
  constructors/projections, swizzles and value packing/expansion keep their
  concrete result types through constant aliases and generic calls.
- Native `break`, `continue`, labels and `fallthrough`. Conditional expressions
  and short-circuit operators check the condition's effects and omit only proven
  unexecuted paths. A constant-false loop still checks its initializer and
  condition. Fallthrough destinations remain checked; mutable globals and
  static storage are not treated as stable branch facts.
- Native memory-copy/zero intrinsics from the installed `base:intrinsics`
  namespace, including wrappers such as `mem.copy`. This permits mutation of
  existing storage and does not permit arbitrary foreign callbacks.
- Mutation and global state access, provided they introduce no allocation.
  Allocation freedom and `fn` purity are independent contracts.
- Compile-time work that leaves a scalar or a native constant in the program.
  The compiler may allocate while performing `#run`; those compiler allocations
  are outside the runtime contract. Unevaluated native type-query operands do
  not execute at runtime.

## Other nonallocating SDK operations

The source-checked SDK adapters cover in-place sorting (including known
comparators and key callbacks), basic clocks/sleep/process queries, standard
C byte/string memory operations, atomics, and supported hardware primitives.
The checker inspects ordinary wrappers and their concrete types instead of
trusting a procedure name. Unknown foreign functions remain unverified.
Changed SDK sources, additions and overlays invalidate reviewed summaries.

Fixed-capacity `[dynamic; N]T` append and resize operations cannot grow their
backing storage and are allowed for nonallocating element types. Their return
values retain the native types. Clear, pop and removal operations reuse existing
collection storage; potentially growing operations on ordinary `[dynamic]T`
still fail. There is no runtime-capacity proof. Every evaluated argument remains
checked, including arguments to an empty sort or a failure-reporting guard.
Stack storage, including native `intrinsics.alloca`, is outside the allocator
contract, just as stack array literals are.

A partial callback summary is not an exact runtime value: record contents,
reinterpreted pointer layouts, multiple results and numeric truncation cannot
be used to hide an allocating branch. Analysis budgets fall back to unknown.

## Input and output

Fixed-buffer input and nonallocating output are supported on the reviewed macOS
and Linux SDK implementations:

```thor
#+vet no_alloc
package main
import "core:os"
import "core:fmt"
import * "thor:basic"

main :: proc() {
    buffer: [128]byte
    count, err := os.read(os.stdin, buffer[:])
    if err != nil {
        fmt.eprintln(err)
        return
    }
    fmt.printf("Read %d bytes: %s\n", count, string(buffer[:count]))
    print("Done", true)
}
```

- `os.read`, `read_at`, `write`, `write_at`, `write_string`, `write_strings`,
  and `flush` work with proven native standard files. Converting a file to an
  `io.Stream` does not itself establish that an arbitrary file callback is safe.
- `io` read/write wrappers follow a statically known `Stream.procedure`, through
  constant aliases, local callback values, record fields, and procedure arguments.
  Each used operation mode is checked independently. A writer with an allocating
  `.Read` branch can still be used for `.Write` when that branch is unreachable.
  An unknown writer or a callback replacement cannot inherit this proof.
- `bufio.reader_init_with_buf` and `writer_init_with_buf` use caller-provided
  storage. Buffer reads, delimiter-slice reads, writes, and flushes are supported.
  A buffered writer's flush checks the underlying **write** operation. Allocating
  constructors, `reader_read_bytes`/`reader_read_string`, and destruction through
  an unverified allocator remain rejected. Borrowed input slices retain Odin's
  normal buffer lifetime rules.
- The `fmt` print/println/printf/printfln families, including their `e`, `f`, and
  `w` variants, and `basic.print` are supported with proven output targets.
  `flush=false`, named arguments, defaults, aliases, and concrete forwarded
  variadic arguments retain their native behavior. A callback call binds names
  and defaults from its declared callable type, then checks the selected body
  using those argument slots; reassignment does not change the caller defaults.
- `printf` accepts runtime format strings, `*` widths/precisions, and concrete
  argument packs. Invalid formats, excessive widths, or the SDK's checked-argument
  limit can still trigger native failures; their diagnostic reporting is excluded
  from the contract. This does not make an invalid format safe, especially when
  native checks are disabled. Argument construction and writer callbacks remain checked.
- `basic.print` must establish that each argument cannot be a directly owned
  `Integer`/`Rational` requiring allocating conversion. Known native values and
  concrete variadic forwarding are supported; an opaque `any` is insufficient.

These are **source-verified effect summaries**, not name-based exemptions. The
compiler validates the SDK snapshot and inspects reachable program code for
replacement of standard-file callbacks or modification of the public formatter
registry. A familiar function in a collection override does not gain native I/O
privileges. This first version rejects configured user-formatter registries,
including registrations outside the checked procedure; safe custom `io.Writer`
callbacks can be checked directly instead. Unknown callbacks and ordinary
allocating paths still fail compilation.

The reviewed source fingerprints are in
[`rust/data/noalloc-io-sdk.json`](../rust/data/noalloc-io-sdk.json), for Odin SDK
revision `8169255b97b85d6a08691d99764f51fb01f92b5a`. SDK edits or upgrades fail closed
with a message identifying the mismatched file; updating fingerprints requires
reviewing the effect summaries against the changed implementation. Only code
using these I/O or RNG summaries pays their SDK verification cost, once per analysis.
Windows has no reviewed native I/O summary yet.

These source checks do not prove memory safety of unchecked code outside the
region. Unsafe aliasing or foreign code that changes SDK globals invalidates the
native-target assumptions. Typed writes, aliases, address escapes and native-file
`using` projections are conservatively rejected rather than trusted. Slice
read/write permissions are still checked independently of allocation freedom.

The contract covers Odin allocator calls caused by the checked path, including
`context.temp_allocator`. OS kernel storage and internal allocations in foreign
platform facilities remain outside the application allocator contract. Reading
zero bytes, EOF, a full buffer, or a non-ASCII byte sequence does not change the
allocation rule. No runtime wrapper, buffering replacement, allocator, or ABI
parameter is added by the checker.

## Random-number generators

The reviewed native default RNG is supported, including its lazy entropy seed,
fixed-buffer refills, explicit state, and reseeding. Distribution helpers such as
`rand.int_max`, `rand.int_range`, and `rand.float64`, borrowed-buffer `rand.read`,
and in-place `rand.shuffle` keep their normal implementations. Helpers that
allocate, such as `rand.perm`, remain errors.

```thor
@(no_alloc)
sample :: proc() -> u64 {
    state := rand.create(u64(42))
    generator := rand.default_random_generator(&state)
    return rand.uint64(generator)
}
```

Explicit custom generators work when their callback target is known. The checker
follows constant aliases, local record copies, and generator parameters, and
checks the callback body in each requested `.Read`, `.Reset`, or `.Query_Info`
mode. A callback that allocates only while resetting can be used for reading;
resetting it still fails. Its data pointer can refer to caller-owned state.
Pointer escapes, field writes, `using` writes, loops, and callback replacement
invalidate affected facts rather than preserving an old target.

Calls that omit the generator use `context.random_generator`. The checker
inspects reachable source procedures, initializers, exported entry points, and
file-policy roots for replacements of that mutable context field. Known
replacement callbacks must also satisfy the requested operation's contract.
Opaque replacements, escaped context addresses, and unknown replacement
callbacks prevent proving the ambient generator; diagnostics identify the
offending context operation. Passing an explicit known generator avoids this
ambient-state dependency. A local value copy of `context` is independent. Odin
also keeps ordinary procedure/callback context assignments local to that call;
an unrelated opaque callback outside the contract is not itself evidence that
the caller's context was replaced. Unknown calls inside the checked region are
still rejected. The source scan conservatively includes known replacements in
reachable procedures even when a more precise calling-context analysis could
exclude them; an explicit generator avoids that conservatism.

This remains a conservative source proof. Arbitrary generator factories,
unknown runtime callback storage, unsafe memory corruption, and foreign code
that bypasses Odin's context/alias rules cannot establish a safe callback.
The native default implementation and entropy boundary share the reviewed SDK
snapshot used for I/O. No RNG wrapper, allocator substitution, or runtime
instrumentation is generated. Missing declarations in a partially loaded SDK
package are discovered by a cached lexical file index, then parsed and checked;
the index itself supplies no semantic or allocation guarantees. Ordinary
compilation without an allocation contract does not build this index.

## Conservative rejections

- Allocation and potential growth: `make`, `new`, `reserve`, ordinary dynamic-array
  `append`/`resize`, map insertion, dynamic-array/map/set literals, and allocating
  set operations. An ordinary dynamic-array append is rejected even when a human
  can establish sufficient capacity. Fixed-capacity operations are described above.
- Runtime f-strings, comprehensions, `pmap`, owned
  `Integer`/`Rational` operations/copies, and other potentially allocating Thor
  helpers. A more optimized individual lowering may use less storage, but this
  first version does not make its contract depend on such optimizations. Fatal
  message construction is an exception; `or_panic` checks its producer normally
  and allows its generated failure diagnostic.
- Logical-number parameters/results, implicit numeric initializers and literal
  elements are checked too. An unused initialized `Integer` local or a literal
  coerced to an `Integer` parameter must not bypass the allocation contract.
  Discarding a numeric builtin result does not avoid constructing it: selecting
  `min`/`max` from an `Integer` slice still acquires owned result storage.
- Returning strings, slices, or nested owned data from `fn`: Thor promotes those
  results into independently owned storage. An ordinary `proc` can return a
  borrowed slice without that promotion, if its implementation is allocation
  free.
- Materializing owned compile-time results in runtime bindings, including
  strings. `TEXT :: #run text()` can become a native string constant;
  `text := #run text()` constructs an independently owned runtime result.
- Unknown runtime procedure variables/parameters, unresolved overloads, uninspectable
  foreign functions, unreviewed intrinsics, inline assembly, and implicit
  callbacks whose targets cannot be established safely. A procedure pointer's signature
  alone cannot prove its implementation allocation free.
  This includes callbacks selected from mutable collection storage. A bounded
  return-identity analysis can establish simple callback/writer factories,
  including aliases, forwarding and named results; every factory body still
  undergoes ordinary effect checking. Native `deferred_*` attributes check their
  cleanup procedures too, with captured mutable state treated as unknown.
- Allocator callbacks, including deallocation: an arbitrary allocator procedure
  can itself allocate even when called to free memory. `delete` is therefore
  conservatively rejected too.
- Unbound generic value storage, conversions, copies, and arithmetic when their
  future type could introduce owned storage. For example, an annotated generic identity function cannot
  promise this for every future `$T`; a generic slice-length reader can. A known
  scalar specialization of an unannotated generic helper can be verified from
  an annotated caller. Unused generic locals and discarded numeric results are
  included; compile-time type arguments do not create runtime value storage.
  There is no unchecked assertion of generic effects.

Defaults are checked at the call when omitted; explicitly supplied arguments
replace those defaults. The contract does not execute or replace user assertions
and stays active under `-disable-assert` and unchecked release flags.

The proof concerns application code and Thor-generated runtime storage reached
from the checked region. It does not promise zero process startup allocations,
zero stack usage, or zero internal storage in operating-system, sanitizer,
debugger, profiling, and test-harness machinery. It adds no runtime counter,
allocator substitution, ABI parameter, or optimization rule. Existing runtime
code generation stays unchanged.

## Fatal diagnostic reporting

Compiler-generated trap reporting and explicit panic reporting are outside the
contract. Failed bounds/type checks, `panic`, `panic_contextless`, `unimplemented`,
and their contextless variants can invoke a nonreturning error handler that
allocates. The reviewed `fmt.panicf`, `log.panic`, and `log.panicf` implementations
are supported too, including constant aliases and known procedure variables.
`panic(f"bad value {x}")` and `panic(fmt.aprintf("%v", x))` may allocate their
messages because the entire invocation is fatal reporting. There is no built-in
Odin/Thor panic recovery; a custom handler must not return.

Assertions and ensures, including the formatted `fmt`/`log` variants, check all
caller-written arguments that execute on a successful path. Their internal
failure-message formatting and handler calls may allocate. For example:

```thor
@(no_alloc)
checked :: proc(ok: bool, value: int) {
    fmt.ensuref(ok, "bad value: %d", value) // Formatting only happens on failure.
    if !ok { panic(f"bad value {value}") } // Fatal reporting may allocate.
    // ensure(ok, f"bad value {value}")    // Error: the caller formats eagerly.
}
```

An always-false compile-time condition also makes the check's message arguments
fatal-only. Mutable local facts are not sufficient for this exemption. Assertions
disabled by `-disable-assert` do not evaluate their arguments when called directly
or through constant aliases. Runtime procedure-variable calls still evaluate
their arguments; the flag does not remove those calls. Ensures stay active.
`or_panic` permits only the generated failure report: its wrapped operation must
still satisfy `no_alloc`. Allocating operations before a panic remain checked;
merely ending a branch or procedure with `panic` does not exempt earlier work.

A `-> !` return type alone grants no exemption. An arbitrary nonreturning
procedure or callback can allocate while looping. Such callees are checked by
the ordinary rules. Similarly, `log.fatal` is checked normally because it can
return. Source-verified failure summaries reject modified SDK implementations
and collection overrides instead of trusting procedure names.

## Cost and validation

Projects without the feature bypass effect analysis and additional SDK discovery.
Annotated code reuses existing symbol/call/type facts and inspects only reachable
implementations. SDK bodies load on demand. Proofs and recursive call paths use
bounded work queues; common callees share analysis rather than being walked once
per caller. There is no persistent cache that could hide an edited callee.

Run [the demo](../examples/no_alloc/demo.thor) with `thor run`. Focused differential
tests are in `tests/test_no_alloc.py`, `tests/test_no_alloc_io.py`,
`tests/test_no_alloc_variadics.py`, and `tests/test_no_alloc_edges.py`; live TLS tests are in
`tests/test_no_alloc_tls.py`. Measurement details and checked/unchecked runtime
evidence are recorded in the [validation report](no-alloc-validation-2026-11-20.md)
and [timing report](benchmarks/no-alloc-2026-11-20.md).

### Native instruction and type-information queries

Native `typeid_of` and `type_info_of` queries do not allocate. A runtime type-id
producer is still an ordinary evaluated expression and must satisfy the
contract. `any.data` and `any.id` describe the boxed header; their proof facts
must not be confused with fields of the boxed payload. Implicit boxing and
`#by_ptr` can expose caller storage, so later callback targets are invalidated
when an ordinary call could change that storage.

Source-pinned CPU instruction leaves used by SIMD and fixed-buffer cryptography
are checked against `rust/data/noalloc-instructions.json`. Its LLVM metadata is
generated offline by `tools/generate_no_alloc_instruction_catalog.py`, and is
never regenerated as part of a project build. The finite catalog excludes TLS,
coroutines, managed runtime operations and unreviewed side-effect intrinsics.
These leaves provide no exemption for an allocating argument or wrapper body.
