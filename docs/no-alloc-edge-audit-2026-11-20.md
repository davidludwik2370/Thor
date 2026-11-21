# Allocation-checker edge-case audit, 2026-11-20

The later [fatal-reporting audit](no-alloc-failure-audit-2026-11-20.md) supersedes
the original explicit-panic restrictions recorded below. Ordinary allocations
remain checked; fatal diagnostic reporting may allocate.

The sandbox's variadic false positive prompted a wider comparison of ordinary
native builds and allocation-contract checks. The fixes improve source/type
proofs; they do not add application wrappers, allocator substitutions, runtime
checks, or ABI parameters.

## Corrected false positives

- Concrete generic types now survive unary operators, reslicing, type
  assertions, and conditional expressions. Pointer-to-array/slice views retain
  their element type. Multipointers to arrays remain distinct from pointers
  that implicitly dereference a collection.
- Native type-switch aliases retain each case's type. `switch &value` makes the
  alias addressable; its expression type remains the case type. `using` keeps
  its existing reference/projection rules.
- Inferred default parameters retain their native value type. String indexing
  has a byte result. These corrections make source inspection of string
  trimming and numeric parsing work without exempting those library names.
- Builtin result shapes cover numeric constructors/projections, min/max/abs/
  clamp, swizzles, `expand_values`, `compress_values`, `soa_unzip`, and borrowed
  raw pointers. Aliases preserve builtin identity; user declarations with the
  same names do not gain an exemption. Typed operands guide numeric overload
  selection. Multi-result inputs to `compress_values` remain multi-result.
- Large homogeneous value expansion is represented by an element type and
  count, rather than allocating one compiler fact per element. Heterogeneous
  metadata remains bounded by source size.
- Native branch statements and labels are not mistaken for unknown runtime
  expressions. Short-circuit operators and ternaries check evaluated conditions
  and skip only proven unexecuted operands. Constant-false loops retain checks
  on their initializer and condition.
- Standard memory-copy/zero intrinsics operate on existing storage. Source
  identity is checked; a locally defined or aliased `mem_copy` that allocates
  remains rejected.
- Python retains already-inspected SDK facts during lowering without loading
  additional packages. A combined scalar expression had generated about 44 MB
  of fallback code after losing those facts; it now emits about 1.5 KB. A
  pre-compilation size check protects this regression test from repeating that
  expensive native compilation.

## Checks against weakening the contract

Known short-circuit facts cannot hide an allocating condition or evaluated
operand. An unknown condition still checks both possible paths. Mutable globals
and static variables do not become stable facts across calls. A selected switch
case with `fallthrough` cannot hide an allocating destination. Known safe and
unsafe specializations do not share an incorrect universal proof.

Negative cases also cover owned logical-number copies after generic reslicing,
pointer/multipointer indexing and contextual literals, builtin-name shadowing,
unknown callbacks, potential growth, and explicit allocating failures.

Some conservative rejections remain intentional: capacity-dependent operations,
opaque callback results/collection elements, arbitrary allocator callbacks and
foreign code, and library routines with explicit unproven panic/assertion paths.
For example, `strconv.itoa` is rejected for its explicit failure path, rather
than an unresolved scalar type. This audit does not add capacity/range analysis
or trust user-provided allocation promises.

## Regression coverage

`tests/test_no_alloc_edges.py` adds 18 differential test methods with positive
and negative subcases. Its combined runtime test exercises pointer views,
string trimming/parsing, native memory copies, value packing, complex values,
swizzles, generic unary operations, type switches, and native loop control.
Both the ordinary and temporary Odin allocators are tracked around the first
call, with explicit verdicts outside assertions. Checked, unchecked optimized,
and debug configurations must all produce the expected value with zero tracked
allocations.

Two additional live TLS tests cover these native/generic paths and unsaved
condition changes, including diagnostic recovery. See the
[main validation report](no-alloc-validation-2026-11-20.md) for the earlier
contract/I/O coverage and the completed audit results below.

Completed validation on macOS arm64:

- 102 allocation-contract, I/O, variadic and edge-case methods passed against the
  rebuilt release compiler and independent Python reference.
- All nine live allocation-contract TLS methods passed with the release compiler.
- 153 development-compiler allocation/general-compiler methods passed during the
  audit; 73 parameter-contract, `using`, and semantic-efficiency interaction
  methods passed as well.
- 97 Rust unit tests and seven assertion-hygiene methods passed.
- The unchanged user sandbox runs successfully through the normal `thor`
  launcher. Documentation inventory/link and whitespace checks pass.

This validation is local to macOS. It does not claim a new Linux or Windows run.

The final ordinary-call query fast path passed a further 47 edge/I/O/variadic
methods, all nine live TLS methods, and the Rust unit suite. Assertion-hygiene
coverage now includes labels in both checked and unchecked frontend ASTs.

The [paired timing report](benchmarks/no-alloc-edge-audit-2026-11-20.md) compares
pre-audit and final binaries both with and without contracts. Representative
ordinary transpile times differ by about 0–4 ms; opted-in analysis changes the
measured transpile times by about 0–9 ms. All five workloads emit identical
executable Odin code across the four variants. An initial repeated-query cost
on the wide call graph was removed before the final measurement.
