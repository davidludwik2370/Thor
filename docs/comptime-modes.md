# Compile-time execution modes

`thor build --comptime=MODE` selects how much work Thor executes while
transpiling. The default is `normal`. Both `--comptime=MODE` and
`--comptime MODE` work with compilation commands, including `check`, `transpile`,
`run`, `test`, `vet`, and `publish`. This flag is independent of Odin's
optimization setting (`-o:speed`, for example). The modes are implemented in
the maintained Rust compiler; the frozen Python reference is unchanged.

| Mode | Execution policy |
| --- | --- |
| `required` | Execute explicit `#run` requests and computations needed for constants/types. Leave optional calls at runtime. |
| `normal` | Existing behavior: automatically evaluate eligible declared `fn` calls with known inputs. |
| `aggressive` | Also evaluate eligible ordinary `proc` calls with known inputs. Respect `#runtime`. |
| `override` | Aggressive execution, additionally ignoring `#runtime` markers, including transitive markers. |

```odin
square :: proc(x: int) -> int { return x*x }
square_fn :: fn(x: int) -> int { return x*x }

main :: proc() {
    a := square(7)             // Runtime in normal/required; folded in aggressive/override.
    b := square_fn(7)          // Folded except in required (or normal -debug).
    c := #run square(7)        // Explicit execution in all four modes.
    d := #runtime square_fn(7) // Runtime except in override.
    _ = a; _ = b; _ = c; _ = d
}
```

`required` is not a way to disable all compile-time work. Explicit `#run` still
executes, and `SIZE :: square_fn(7)`, `[SIZE]int`, `when`, and constant parameter
defaults still require their compile-time values. The mode does not make an
ordinary call such as `SIZE :: square(7)` valid without `#run`; aggressive and
override can accept that call if it meets their additional eligibility rules.
Native constant arithmetic and native compiler optimization remain enabled in
every mode.

## Eligibility and runtime behavior

Ordinary automatic calls must resolve statically to one procedure, have known
arguments, pass the existing transitive purity checks, and be provably
allocation-free for all runtime inputs in that specialization. They must have
one self-contained value result: supported scalar/enum values, fixed arrays,
and supported structs/unions containing only such values. Fixed-capacity arrays
are also supported. Type inference, overload resolution, defaults, aliases and
polymorphic specialization use the existing compiler machinery.

Pointers, borrowed strings/slices, dynamic collections, procedure values, opaque
resources, logical arbitrary-precision numbers and aggregates containing them
are excluded from ordinary automatic results. Merely knowing a slice's contents
does not make copying it an ownership-preserving replacement for returning its
original backing storage. Similarly, a scalar-returning procedure that allocates
and frees a temporary still calls the caller's allocator; automatic folding must
not silently remove those observable allocator effects.

These calls retain their original runtime implementation and result ownership.
There are no new runtime wrappers or worker threads. Explicit `#run` and declared
`fn` calls retain their existing scratch-allocation and result-materialization
contracts, including supported allocated results; use those contracts when you
intend execution in the compiler's scratch environment.

I/O, randomness, mutable globals, unverified foreign calls and escaping mutation
remain ineligible. `override` only overrides `#runtime`; it does not bypass these
checks. Runtime callback variables remain runtime calls. Unsupported or
unresolved optional ordinary calls stay at runtime, including calls that cannot
pass the helper's checks before execution. A required constant cannot use that
fallback and receives a diagnostic instead.

An immutable runtime local (`input :> 7`) is not automatically treated as a
constant input. The scheduler does not perform general optimizer-style dataflow
propagation. Only selected, known executable paths are evaluated: type queries,
dead branches and lazy operands must not execute the calls they inspect or skip.
Void and nonoptional multiple-result calls retain their existing runtime behavior;
use an eligible one-result wrapper with `#run` when needed.

## Failures, targets and caching

Once compile-time execution starts, a panic, crash, malformed result or timeout
is a compilation error. There is no silent retry at runtime. Execution is
unlimited by default; `--run-timeout SEC` supplies a deadline and `0` means
unlimited. Use a positive deadline for potentially expensive automatic work.

Execution supports native targets only. It does not simulate a cross-target
machine. Existing type/constant/execution restrictions still apply; `override`
cannot bypass them. In debug builds, `normal` retains the existing suppression
of optional `fn` evaluation. Explicit `aggressive` or `override` requests are
honored even with `-debug`.

The policy is part of the evaluation request, prepared-helper and scalar cache
identity. Per-file migration markers and native sequence bounds selected by `--odin-compatible` also participate
in cache identity. A cached from-the-end access cannot bypass native bounds
checking, and a cached override result cannot bypass a retained runtime barrier
in another mode. Normal caching and `--no-cache` remain available. Eligible
independent scalar calls can share bounded helper batches; verification is
cached by procedure specialization and argument shape, including captured generic
constants/types. Result ownership classifications are refreshed when entering or
leaving a generic context. Normal builds do not perform the additional ordinary-procedure purity/allocation analysis.

## TLS

VS Code's resource-scoped `tls.comptime` setting accepts the same four names
and defaults to `normal`. Changing it restarts TLS. For another LSP client, pass
`checker_args: "--comptime=aggressive"` in initialization options. Live compiler
checks use that policy, including unsaved source, source-mapped failures and
timeouts. Hover and completion retain original source types; the TLS resolver
does not execute calls on its own.

Use the matching CLI flag in build/run/debug tasks. The editor diagnostic setting
does not rewrite your terminal commands or change the run shortcuts' defaults.

An executable walkthrough is in
[examples/comptime_modes/demo.thor](../examples/comptime_modes/demo.thor).
