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
- The existing `test_pure_calls`, `test_pure_io`, `test_native_run`,
  `test_t18_t20`, `test_t21_t22`, `test_t23` and diagnostic suites.

## Rejection categories

| Category | Restriction and diagnostic location |
| --- | --- |
| Missing implementation | `fn` declarations need an inspectable body. Foreign/bodyless prototypes use `proc`. Highlight `fn`; do not emit a Python traceback. |
| Calling convention | Declared `fn` uses Odin's calling convention. An inspectable ordinary contextless/C procedure can still qualify when its operations and value types qualify. |
| Input/result types | Reject raw/typed/multiple pointers, runtime type identities, `any`, procedure values, maps/sets, dynamic arrays and unsupported nested members. Highlight the specific parameter or result type and name the rejected instantiated type. |
| String representation | Support bounded UTF-8 `string`; reject `cstring`, `cstring16` and `string16` as pure inputs/results. C strings are pointer storage, rather than ordinary bounded value strings. |
| Numeric representation | Integer/Rational are logical values. Pure code uses their numeric operators and public properties; inspecting their implementation headers is rejected, including through unconstrained generics. Unrelated user fields with the same names remain valid. |
| Record schemas | Validate all fields and union variants recursively. Reject raw unions, unresolved schemas and recursive/excessively deep value shapes. A public raw union no longer causes an unrelated normal record with the same field names to fail. |
| Local storage | Reject pointer/`any` boxing, procedure values, maps/sets and raw unions introduced through annotations, inference or aggregates. Fresh supported buffers remain allowed. |
| Nonlocal state | Reject reads/writes of runtime globals and immutable runtime bindings outside the function. Imported storage follows the same rule. Genuine permitted constants remain usable. |
| Ambient state | Reject explicit context queries, allocator callbacks and unverified stateful native operations. Library violations highlight the user's call and link the offending library implementation. |
| I/O | Reject console/file/stream I/O, including constant aliases and transitive helpers. In-memory supported formatting remains separate. |
| Calls | Reject indirect runtime procedure calls, foreign/bodyless calls and unavailable implementations. Keep native undeclared-name and type errors when they are the actual underlying problem. |
| Compiler intrinsics | Only verified deterministic intrinsics are eligible. Stateful compiler intrinsics get an explicit ineligible-operation error. |
| Defaults | Impure defaults fail at the offending default expression. Preserve the signature's syntax/type inference so that a context-dependent default does not turn into a misleading missing-parameter error. Checked call contracts also cover the inspected callee's defaults. |
| Implicit callbacks | Reject initializer/finalizer and deferred callback contracts that cannot be treated as explicit eligible calls. Ordinary proc usage is unchanged. |
| Borrowed mutation | Inputs and nested references remain read-only through aliases, slices, aggregates, copies, loops, union projections and conversions. This includes shared Integer/Rational member headers in shallow aggregate copies. Direct numeric assignments retain Thor's independent cloning semantics. Highlight the write/copy/delete operation. |
| Named arguments | Bind `copy` source/destination by parameter name. Reordering arguments cannot hide a write to an input or lose the borrowed provenance of nested elements. Parenthesized constant builtin/clone aliases retain these rules. A `delete` location argument is distinct from a prohibited allocator override. |
| Reference bindings | Reject mutable-reference iteration and type-switch bindings in pure code. Diagnose the binding without undeclared-name cascades from its body. Indexed writes into a fresh local buffer remain allowed. |
| Addresses and representations | Reject raw address extraction, pointer conversion, aggregate/pointer transmutation, assembly and uninitialized storage. Supported scalar transmutation remains eligible. |
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
