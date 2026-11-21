# RNG allocation-proof audit, 2026-11-21

The sandbox's `rand.int_max` path now satisfies `#+vet no_alloc`. The installed
Rust compiler and Python reference both support the native default generator,
explicit seeded state, and inspectable custom callbacks. The
[contract reference](no-alloc.md#random-number-generators) describes supported
operations and conservative boundaries.

## Corrections

- Missing symbols in a partially loaded native package now trigger targeted
  declaration discovery. Previously, loading `runtime.Context` from `core.odin`
  could prevent the checker from finding `random_generator_read_ptr` in another
  file of the same package. A lexical index selects parser inputs, while parsed
  ASTs and the existing effect checker establish the actual proof. Comments,
  strings, grouped names, Unicode names, overlays, and target exclusions retain
  their handling. Package indexes and parser results are reused.
- `size_of` and `align_of` have untyped integer results, including through
  aliases; `offset_of` has a typed `uintptr` result. This matches native Odin and
  avoids rejecting valid unsigned parameters in SDK helper calls.
- Native entropy acquisition and the default generator's lazy initialization,
  fixed-buffer refill, and reset use reviewed SDK source summaries. They share
  the existing I/O snapshot verification, once per source analysis. SDK changes,
  additions, overlays, or collection overrides cannot inherit that proof.
- Explicit generators retain callback identities through aliases, local record
  copies, and procedure arguments. The checker follows the callback body in the
  requested mode and checks ordinary calls and data mutations normally. An
  allocating reset branch does not disqualify a read-only operation; an actual
  reset still rejects that callback.
- Address escapes remain tracked after reassignment. Stores through pointers,
  record fields, and `using` invalidate affected callback facts. Read-only casts
  and inspection do not erase facts, and unrelated private by-value headers are
  preserved through operations such as shuffling a separate slice.
- The ambient generator is checked against source-visible context replacements.
  Unknown replacements and escaped context addresses produce a diagnostic at
  the RNG use, with the offending context operation as related information.
  Ordinary Odin call-local context assignments do not rewrite the caller's
  context: a native probe confirmed that both a normal call and a slice-sorting
  callback preserve the caller's `context.user_index`. Unrelated opaque calls
  outside the contract therefore do not alone poison the RNG proof. Unknown
  calls within the contract remain errors.

The ambient source scan deliberately overapproximates known replacements in
reachable procedures. It can require an explicit known generator when a more
precise incoming-context analysis could distinguish unrelated replacements.
Unsafe foreign code that bypasses the language's context and alias rules is
outside these source assumptions. No callback is trusted just for having the
right signature or a familiar name.

## Validation

`tests/test_no_alloc_random.py` contains 25 methods with additional subcases.
They cover default, seeded and custom generators; generator aliases and
forwarding; lazy initialization, refills and reset; borrowed reads, shuffle and
choice; actual allocation in `rand.perm`; mode-sensitive allocation; callback
reassignment, address escape, data mutation, loops, and `using`; context changes
in initializers and imported native packages; inactive code; SDK overlays and
added files; targeted discovery; and shared-proof counters.

The runtime fixture tracks both `context.allocator` and
`context.temp_allocator`. It checks deterministic equal-seed sequences, reset,
callback state mutation, and zero application allocator calls. Verdicts use
explicit conditionals and Python comparisons, so they remain active under
`-o:speed -disable-assert -no-bounds-check -no-type-assert`. Both compiler
implementations execute checked and unchecked variants. Linux ARM64 target
checking also exercises its selected native RNG/entropy sources; the runtime
allocator-count measurements in this round are native macOS runs.

Live TLS tests cover valid default/seeded/custom calls, unsaved allocating
callback edits and recovery, user-call paths, whole-expression ranges, and
related context-write locations. Surrounding allocation, I/O, variadic,
control-flow, type-parameter, call-plan, and builtin editor tests provide the
regression coverage. The actual sandbox builds in both checked and unchecked
release modes without changing its source.

Completed runs:

- Broader allocation/I/O/variadic/failure/editor regression run: 151 tests passed.
- Final release RNG/editor/using-mutation run: 38 tests passed, including all 25
  RNG methods and 12 live TLS methods.
- Type-parameter, call-plan-cache, and builtin editor regression run: 32 passed.
- Rust library unit tests: 98 passed. Assertion hygiene: 7 passed.
- Documentation inventory/local links and whitespace checks passed.

Timing samples and native-output comparisons are recorded in the
[benchmark report](benchmarks/no-alloc-rng-2026-11-21.md). The allocation proof adds
no runtime wrapper, instrumentation, allocator change, or ABI parameter.
