# Pure-function and compile-time diagnostics

Thor checks the same purity contract for a declared `fn`, the ordinary
procedures called from it, and procedures forced with `#run`. Ordinary `proc`
calls outside those paths keep Odin's behavior. A procedure's name or calling
convention alone is not proof of purity.

This audit is covered by the shared CLI/TLS inventory in
[`purity_cases.py`](../tests/purity_cases.py), with additional transitive,
generic, ownership, helper-process and editor tests in:

- [`test_purity_hardening.py`](../tests/test_purity_hardening.py)
- [`test_purity_hardening_tls.py`](../tests/test_purity_hardening_tls.py)
- [`test_evaluation_hardening.py`](../tests/test_evaluation_hardening.py)
- [`test_pure_mutation.py`](../tests/test_pure_mutation.py) and
  [`test_pure_mutation_tls.py`](../tests/test_pure_mutation_tls.py)
- [`test_pure_call_context.py`](../tests/test_pure_call_context.py) and
  [`test_pure_call_context_tls.py`](../tests/test_pure_call_context_tls.py)
- The existing `test_pure_calls`, `test_pure_io`, `test_native_run`,
  `test_t18_t20`, `test_t21_t22`, `test_t23` and diagnostic suites.

Pure callback value boundaries are checked through assignments, aliases, generic
specializations, nested storage and casts before native type erasure. See
[pure callback types](pure-callbacks.md).

## Rejection categories

| Category | Restriction and diagnostic location |
| --- | --- |
| Missing implementation | `fn` definitions need an inspectable body; `fn(...) -> ...` callback types describe a checked effect contract. Foreign/bodyless prototypes use `proc`. Highlight `fn`; do not emit a Python traceback. |
| Calling convention | Declared `fn` uses Odin's calling convention. An inspectable ordinary contextless/C procedure can still qualify when its operations and value types qualify. |
| Input/result types | Public fn inputs and checked results reject raw/typed/multiple pointers, runtime type identities, `any`, ordinary `proc` values, maps/sets and unsupported nested members. Dynamic-array results and internal helper parameters are supported, including nested owned values. Public dynamic-array inputs remain unsupported; pass a slice view instead. Internal ordinary helper parameters may contain typed pointers and fixed-capacity arrays of supported storage. Public signature failures highlight the parameter/result type; helper failures link that annotation from the responsible fn call. Name the rejected instantiated type. |
| String representation | Support bounded UTF-8 `string`; reject `cstring`, `cstring16` and `string16` as pure inputs/results. C strings are pointer storage, rather than ordinary bounded value strings. |
| Numeric representation | Integer/Rational are logical values. Pure code uses their numeric operators and public properties; inspecting their implementation headers is rejected, including through unconstrained generics. Unrelated user fields with the same names remain valid. |
| Record schemas | Validate all fields and union variants recursively. Reject raw unions, unresolved schemas and recursive/excessively deep value shapes. A public raw union no longer causes an unrelated normal record with the same field names to fail. |
| Local storage | Typed local pointers may address supported storage. Reject raw pointers, `any` boxing, ordinary `proc` values, maps/sets and raw unions introduced through annotations, inference or aggregates. Fresh supported buffers remain allowed. |
| Nonlocal state | Reject reads/writes of runtime globals and immutable runtime bindings outside the function. Imported storage follows the same rule. Genuine permitted constants remain usable. |
| Ambient state | Reject explicit context queries, allocator callbacks and unverified stateful native operations. Library violations highlight the user's call and link the offending library implementation. |
| I/O | Reject console/file/stream I/O, including constant aliases and transitive helpers. In-memory supported formatting remains separate. |
| Calls | Accept runtime calls through `fn` callback types; reject indirect plain `proc` calls, foreign/bodyless calls and unavailable implementations. A `#run` request still needs a known callback identity. Keep native undeclared-name and type errors when they are the actual underlying problem. |
| Compiler intrinsics | Only verified deterministic intrinsics are eligible. Stateful compiler intrinsics get an explicit ineligible-operation error. |
| Defaults | Impure defaults fail at the offending default expression. Preserve the signature's syntax/type inference so that a context-dependent default does not turn into a misleading missing-parameter error. Checked call contracts also cover the inspected callee's defaults. |
| Implicit callbacks | Reject initializer/finalizer and deferred callback contracts that cannot be treated as explicit eligible calls. Ordinary proc usage is unchanged. |
| Borrowed result | A `fn` must explicitly allocate or clone each returned reference, or forward an owned helper result. A diagnostic on the result type links to the input or view that supplies the borrow. This includes nested fields, concrete generic specializations and SOA rows and columns. Use `core:slice.clone` for inline elements or `thor:pure.clone` for nested owned data. |
| Borrowed mutation | Public inputs and nested references remain read-only through aliases, slices, aggregates, copies, loops, union projections and conversions. Ordinary helpers may mutate enclosing-invocation-owned storage, including through pointer arguments and helper chains. This includes protecting shared Integer/Rational member headers in shallow aggregate copies. Direct numeric assignments retain Thor's independent cloning semantics. A write/copy/delete in the fn is highlighted directly; a helper violation links that operation from the fn's call. |
| Named arguments | Bind `copy` source/destination by parameter name. Reordering arguments cannot hide a write to an input or lose the borrowed provenance of nested elements. Parenthesized constant builtin/clone aliases retain these rules. A `delete` location argument is distinct from a prohibited allocator override. |
| Reference bindings | Reference iteration and type-switch aliases inherit the selected storage's permission. Writes to owned storage are allowed; writes to borrowed contents highlight the actual write. |
| Addresses and representations | Allow typed internal addresses, dereferences and nil checks. Reject pointer identity comparisons (including inside aggregate comparisons, membership or switches), numeric address conversions, aggregate/pointer transmutation, assembly and uninitialized storage. Supported scalar transmutation remains eligible. |
| Escaping local storage | Reject helper-local stack pointers, array views and slice literals stored through output parameters, including transitive stores and shallow `copy` of reference headers. Heap buffers allocated in a mutating helper share the enclosing invocation's scratch lifetime and may be assigned through output parameters. |
| Source identity | Reject file/directory/line/caller identity, project-name dependencies and unsupported source-dependent directives. Do not silently substitute a helper's identity. |
| Formatting | Reject custom/record formatting in pure paths. The error belongs to the interpolation field or format call, rather than `runtime/format`. Ordinary procs may still format such values. |
| Contextless ownership | Ordinary contextless compile-time targets cannot return allocation-bearing results requiring a caller allocator. Use `fn` or a contextful eligible proc. |
| Constant ownership | Slice-containing results need runtime bindings (`:=`), rather than an allocated value disguised as an Odin `::` constant. Existing Integer/Rational constant/result rules also apply. |

Compiler-generated expression helpers have distinct identities for ordinary and
checked pure procedure variants. Sharing an unchecked interpolation, switch or
index helper with a checked variant could otherwise bypass its contracts.

Generic direct Integer/Rational copies, including local aliases and public
Rational components, retain independent ownership. Copying an aggregate remains
shallow. Native type-dependent checks distinguish numeric copies from borrowed
user fields with names such as `numerator` and `denominator`; those names do not
grant permission to free shared slices or other input storage.

## Purity imposed by a caller

An ordinary helper is not intrinsically invalid because a pure caller reaches
it. A transitive purity failure highlights the responsible call **inside the
`fn`**, names that function and shows the call chain. Related locations retain
the actual violating operation, intermediate helper calls and the fn signature.
Native instantiation locations select the active generic/when path; an ordinary
caller or an inactive eligible specialization is not blamed for another caller.
Public fn signature errors remain on the fn's own parameter/result annotation.

For example, `reset_shuffled(&deck)` in `simulate_game::fn` is the primary
location when `reset_shuffled::proc` calls `rand.shuffle(deck[:])`. Its pointer
parameter is permitted for a locally created fixed-capacity deck. The failure
is the shuffle's default ambient generator; linked notes show the shuffle call,
the standard-library context access and `simulate_game`'s fn contract. Changing
the caller to `proc`, or making the helper deterministic, clears the error.

Internal helper parameters accept dynamic/fixed-capacity arrays recursively,
including through typed pointers. Dynamic-array results are promoted into the
caller allocator; public dynamic-array inputs still require slice views.
`append` and pop-family operations preserve borrowed
element provenance, including native/Thor spreads and named arguments; putting
a borrowed slice in a local container does not grant permission to mutate it.
Generated inference cascades are compared with the underlying cause location
even after its primary diagnostic moves to the fn call.

## Compilation and execution failures

`#run` requires a resolved eligible call, known arguments/types and exactly one
supported result value. Native optional status/allocator-error results retain
their normal one-value behavior. Runtime arguments, dependency cycles,
unresolved specializations, unsupported result contexts and cross-target
execution produce source-located errors. Use ordinary runtime calls when inputs
are runtime values. Valid recursive call graphs are distinct from invalid
constant-dependency cycles.

Panics, assertions and bounds failures are execution failures, rather than
purity violations. The primary diagnostic points to the `#run` or automatically
evaluated call. When Odin supplies the failing location, a related diagnostic
links the original function line. Generated sequence bounds checks forward their
caller location for this purpose. CLI JSON preserves related notes through the
outer diagnostic adapter, so TLS receives the same information and can clear it
after an unsaved correction.

Helper compilation/execution is bounded. Timeouts, failure to launch, backend
compiler signals, executable signals and invalid result packets have explicit
messages attached to the evaluation request. Temporary generated paths and
compiler implementation names are omitted from normal diagnostics. A signal
without a runtime message does **not** reliably identify the inner cause; Thor
reports the signal and suggests reproducing the call in a runtime debug build.

Structured checks reserve a bounded native error budget of 1,000 before mapping
and deduplication. Odin counts repeated helper errors before merging them; its
smaller default can otherwise stop on generated cascades before reaching another
independent source error. Explicit `-max-error-count` settings keep their native
meaning. Raw diagnostics are unchanged. This requires no extra compilation pass.

Purity checks remain compile-time checks with `-disable-assert` and unchecked
release flags. User assertions still follow Odin's assertion-elision semantics;
they are not repurposed for compiler bookkeeping or required cleanup. Failed
evaluation never becomes a cached successful result. Changes to inspected
dependencies invalidate previously successful compile-time results.

## Limits of the contract

Generic bodies and native `when` branches are checked when instantiated/selected.
Inactive branches and unevaluated type queries must not execute or reject an
otherwise unused ordinary effect. A bodyless `fn` is an invalid declaration
form, rather than an executable branch to defer checking.

The checker remains deliberately conservative for inaccessible record schemas,
raw unions and unsupported reference graphs. Deeply nested validation is bounded
at 24 levels. It does not infer resource identity from a plain integer being
used as an operating-system handle, duplicate external resources, or permit
arbitrary uninspectable callbacks. A genuinely locationless native compiler
failure cannot be assigned an invented inner source location.

Storage permissions are compiler metadata with no runtime ownership table.
Joins across branches/reassignments are conservative: replacing an earlier
borrowed header with fresh storage does not necessarily erase the old provenance.
Internal typed pointer inputs do not broaden the supported result serialization
or public fn input types. Ordinary procedures outside a checked pure call graph
retain their original signatures, allocation and mutation behavior. Procedure
type reflection keeps the original source signature in evaluation helpers too.
