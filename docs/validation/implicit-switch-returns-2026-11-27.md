# Implicit switch returns — 2026-11-27

Value-returning `fn` bodies now accept a final switch expression directly:

```odin
f4 :: fn(x: int) -> int {
    switch x {
    case < 10: 0
    case < 20: 1
    case < 30: 2
    case:      3
    }
}
```

This uses the same return/switch rules as `return switch`, including runtime
and compile-time switches, `#partial switch`, nested switches, multiple results,
contextual literals, aliases, generic callbacks and all four compile-time modes.
Missing defaults/results and assignment-only `break` arms retain their errors.
Statement switches with explicit returns, mutations, initializers, labels or
type-switch bindings keep their existing behavior. Nested statement blocks,
`if`/`when` statements, loops, ordinary procedures and void functions do not gain
implicit branch returns. The existing restriction against `do` bodies in `fn`
is unchanged.

The related review corrected the source span for trailing `#partial` compound
literals. Conditional expressions, comprehensions, fstrings, membership,
`or_else` and expression directives are covered by regression tests.

A pre-existing ownership issue also affected explicit `return switch`: an
intermediate helper could try to return stack-backed slice literals before the
owning function promoted them. Owned switch returns now keep their branches in
the function's return scope. Tests cover input views, nested/multiple results,
records, Integer/Rational values, compile-time results, diverging arms and user
defers, including unchecked builds and allocation tracking. Implicit returns
introduce no runtime tracking or cleanup mechanism.

## Validation

On macOS 15.6 ARM64:

| Suite | Result |
| --- | --- |
| Broader switch, purity, callback, ownership, no_alloc and compile-time regressions | 355 passed |
| Final focused implicit-return and TLS suite | 32 passed |
| Rust compiler unit tests | 136 passed |
| TLS native unit tests | 1,178 passed |
| Assertion hygiene | 7 passed |

The focused rerun overlaps the broader suite and includes the finished parser
cache changes and an additional owned-record/numeric case. No selected tests
were skipped. The maintained broader runner excluded one frozen-reference
comparison. The example passed checked and `-o:speed -disable-assert
-no-bounds-check -no-type-assert` runs with output `20 24 28`.

TLS checks include hover, contextual enum completion, navigation, original
semantic-token positions, diagnostics, and unsaved error recovery. An editor
test changes a statement switch into an incomplete expression at the same
source offset to verify that parse decisions do not survive a document change.
An unchecked frontend regression exercises twenty nested callback/switch levels;
failed expression attempts are cached per file and parser mode. Rewinds retain
grown recording buffers safely while removing speculative metadata.

## Parser timing

Compared the finished frontend with Git revision `ef6828f`, using identical
unchecked optimized Odin build flags. Each workload used two warmups and nine
alternating measurements, including process startup and AST JSON output.

| Existing source sample | Baseline median | Candidate median |
| --- | ---: | ---: |
| `demo/main.thor` | 20.12 ms | 20.33 ms |
| 200 scalar functions | 16.67 ms | 16.79 ms |
| 100 statement switches with callbacks | 21.23 ms | 21.80 ms |

Parsed ASTs and metadata were identical for all three existing-code samples.
The new value-switch sample also completed successfully; its AST intentionally
differs because it now contains returns. These small parser-only measurements
do not establish an end-to-end build-time or runtime speedup.

Raw results, logs and timing samples are retained locally under
`dist/implicit-switch-returns-2026-11-27`. Benchmark builds and their temporary
source copies were automatically removed.
