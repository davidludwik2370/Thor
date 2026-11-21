# Allocation-contract validation, 2026-11-20

This initial validation predates the [fatal-reporting policy and additional
tests](no-alloc-failure-audit-2026-11-20.md). Explicit panic reporting is now
excluded from the allocation contract; the original measurements below are retained.

The contract and its conservative limitations are documented in
[no-alloc.md](no-alloc.md). The implementation checks source effects; it does
not insert runtime guards, substitute allocators, change calling conventions,
or optimize the generated application.

## Coverage

`tests/test_no_alloc.py` contains 55 test methods, with additional parameterized
cases. These compare the independent Python implementation with the default
Rust implementation and exercise:

- Both attribute forms, file policy, mixed native attributes/vet tags, repeated
  headers, CRLF, excluded files, and inactive `when` branches.
- Direct calls, aliases, overload selection, type/constant specializations,
  lexical shadowing, recursion, shared callees, and bounded expanding generics.
- Omitted defaults, named arguments, caller metadata, nested procedures,
  `defer`, switches, fallback lists, `#run`, and `#runtime`.
- Unknown callbacks, foreign procedures, SDK implementations, deferred native
  callback attributes, and edited/imported callees.
- Collection growth, map writes through aliases/pointers, generated storage,
  `fn` ownership promotion, logical-number conversions and implicit cleanup,
  discarded allocating results, unbound generic storage/conversions, and
  compile-time-result materialization.
- Native stack storage, borrowed results, scalar specialization, shallow native
  copies, and ordinary mutation that does not allocate.
- Vet application/revalidation, native compatibility projection, checked and
  unchecked flags, and opt-in profiling/call-graph sharing.

The explicit-policy tests distinguish native compiler-generated trap reporting
from ordinary explicit calls. Bounds checks and type assertions stay enabled in
checked programs; explicit `panic` and allocating error paths are rejected.

`tests/test_no_alloc_tls.py` contains nine live language-server tests. They check
attribute hover/completion, unsaved edits and recovery, precise original-source
ranges, related call-path locations, and library errors reported at the user's
call. These also verify that editing a previously safe callee invalidates its
proof.

### Variadic type-inference regression

The sandbox exposed a false positive in `and(..vals[:])`: the ordinary resolver
did not recognize a variadic parameter's borrowed-slice type, so its loop
element became unknown. Specialized loop queries also lost generic element
types. Both are corrected in the shared type inference, without adding effect
exemptions or changing generated runtime behavior. The independent Python
reference also recognizes packed and named variadic arguments and binds generic
map keys/values when choosing a callee.

`tests/test_no_alloc_variadics.py` adds seven differential methods covering
Boolean reductions, empty arguments, typed and generic forwarding, indexing,
by-reference iteration, named packs, aliases, overloads, read-only callback
parameters, and generic array/slice/map iteration. Negative cases retain
rejections for actual allocation and owned logical-number copies. A tracked
runtime test combines the sandbox's reductions, immutable `soa_zip` iteration,
and struct arithmetic in checked, unchecked optimized, and debug configurations.
The new live TLS case checks valid reductions and generic iteration, an unsaved
allocating edit, and diagnostic recovery.

This regression round passed all 77 existing allocation/I/O differential
methods, 69 general compiler differential methods, seven new variadic methods
against the rebuilt release compiler, seven live TLS methods, 97 Rust unit
tests, and seven assertion-hygiene methods. The unchanged sandbox also builds
and runs through the normal `thor` launcher with identical checked and
unchecked optimized output. Its representative tracked computation makes zero
allocations in all three runtime configurations.

## Runtime evidence

The subsequent [edge-case audit](no-alloc-edge-audit-2026-11-20.md) extends the
proofs to generic expression/view types, native builtin results, type-switch
aliases, defaults, labels/control flow, and memory-copy/zero primitives. It adds
18 differential methods and two live TLS methods, including negative checks
against stale mutable-state facts and hidden fallthrough effects.

Positive runtime tests run in checked, unchecked optimized, and debug modes.
The unchecked flags are:

```text
-o:speed -disable-assert -no-bounds-check -no-type-assert
```

The allocation test tracks both `context.allocator` and
`context.temp_allocator`. An annotated computation using fixed arrays, mutable
slice views, and a runtime `fn` returning a fixed record produces the expected
result with **zero tracked allocations**. Setup and cleanup happen outside
assertions, and verdicts use explicit `if ... { panic(...) }` checks, so disabling
assertions cannot remove the verification.

The paired benchmark checks that baseline, current unannotated, and annotated
variants emit identical Odin source after removing blank lines. See the
[timing report](benchmarks/no-alloc-2026-11-20.md) for commands and raw results.

## Regression checks

Final verification passed:

| Check | Result |
| --- | --- |
| Allocation-contract suite, release Rust against Python | 51 tests passed |
| Debug Rust against Python, excluding the release-delegated compatibility test | 50 tests passed |
| Live no-alloc and parameter-contract TLS suites | 9 tests passed |
| Rust library unit suite | 97 tests passed |
| Assertion hygiene | 7 tests passed |
| Demo, checked and unchecked optimized | Both exited successfully |
| Reference inventory/link audit and whitespace checks | Passed |

The implementation also underwent the parameter-contract and semantic-efficiency
suites, native-vet checks with generated helpers in checked/unchecked modes,
Odin compatibility tests, comprehension and struct-operator tests, Rust library
unit tests, assertion hygiene, and the documentation inventory/link audit.

The broader testing exposed and corrected a Python-reference range-index bug:
the index of an ordinary struct-slice loop had inherited the element's struct
type. Checked and unchecked native-vet cases now cover the corrected inference.
The new tests also cover lexical shadowing of generic type/constant parameters
in allocation proofs.

The main verification commands, with the release compiler built first, were:

```sh
THOR_TEST_PREBUILT=1 python3 -m unittest -v tests.test_no_alloc
THOR_TEST_PREBUILT=1 python3 -m unittest -v tests.test_no_alloc_tls tests.test_parameter_contracts_tls
cargo test --locked --offline --manifest-path rust/Cargo.toml --lib -j 2
python3 -m unittest tests.test_assertion_hygiene
python3 tools/check_language_reference.py
```

Validation here ran on macOS arm64. This report does not claim new native
Linux or Windows validation for this feature, or a formal proof of all native
compiler/platform behavior. Unknown application effects fail conservatively.

## I/O extension and callback contexts

The three extension stages add namespace/type recognition, known callback and
operation-mode propagation, and reviewed SDK I/O/formatting effects. The Python
reference implements the same behavior independently. Neither implementation
inserts I/O wrappers or changes an application's allocator or ABI.

`tests/test_no_alloc_io.py` adds 22 differential test methods, with parameterized
checks covering native, generic-stream, and fixed-buffer buffered I/O; fmt/basic
output; constants and aliases; positional, named, default, and forwarded pack
arguments; and checked/unchecked/debug runtime execution. Additional cases cover:

- Callback replacement through record fields, addresses, helpers, loops and
  `using` projections; independent switch branches and allocating initializers;
  deferred construction cannot make an earlier call appear safe.
- Callback reassignment with different names/defaults, including opposite
  boolean defaults and swapped `printf` flush/newline names. Caller defaults
  come from the declared callable, while operation meanings come from the
  selected implementation. Deferred metadata cannot disappear through an alias.
- Correct `.Read_At`/`.Write_At` dispatch and buffered-flush `.Write` dispatch;
  `flush=false` avoids proving a callback that is never called.
- Opaque callbacks/formatters, formatter registration through aliases, generic
  mutation or escape of native file metadata, changed SDK source overlays,
  allocating I/O/conversion helpers, dynamic formats/widths, implicit widths,
  spread argument counts, and omitted allocating defaults.
- Opt-in gating, one SDK disk verification per request, and universal
  file-policy proofs reused at later call sites. Type/generic specializations
  retain separate keys; a signature alone never certifies a callback.

The first I/O call runs under both a tracked ordinary allocator and tracked
`context.temp_allocator`. Fixed-buffer reads and `fmt.printf` plus `basic.print`
make zero tracked allocations in checked, unchecked optimized, and debug builds.
Payloads include EOF, UTF-8, invalid UTF-8 bytes, a full buffer, and truncated input; verdicts compare
raw output bytes and use explicit checks outside assertions. The
[demo](../examples/no_alloc_io/demo.thor) also runs with both implementations.

Live TLS coverage includes a valid standard-input/print program and an unsaved
writer edit that introduces allocation. It checks the original source range,
related call path, and removal of the diagnostic after repair.

Final validation passed:

- **77 differential methods:** 55 base allocation methods and 22 I/O methods,
  including caller defaults/names, deferred metadata, writer modes, and proof
  sharing. Both compiler implementations are exercised.
- **35 production methods:** 22 I/O methods using the final release compiler,
  six live TLS methods, and seven assertion-hygiene methods.
- **97 Rust library unit tests.**

The broader compiler/parameter-contract/pmap run executed 193 methods: 192
passed; the Python side of an unrelated typed-loop panic fixture encountered an
Odin compiler SIGSEGV. Its isolated rerun passed in checked and unchecked builds.
This records that backend failure rather than describing the original run as
fully clean. No Odin compiler changes were made.

The source summaries currently cover macOS and Linux implementations at the
recorded SDK revision. Runtime measurements here were performed on macOS arm64;
Windows has no reviewed I/O boundary. Mutable formatter registries and unknown
stream targets remain conservative proof failures. Compiler-generated trap
reporting retains the original exclusion; ordinary explicit failure paths do not.

The [I/O timing report](benchmarks/no-alloc-io-2026-11-20.md) records paired
before/after timings, request counters, and emitted-code comparisons, including
the unused compiler-intrinsic import produced by additional type discovery.

