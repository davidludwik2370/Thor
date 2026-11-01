# F-string argument lifetimes

Implemented in Thor 0.28.0, TLS 0.21.0, and the local VS Code extension 0.16.0.

```odin
print(f"value {n}")                  // Temporary, freed after the expression.
print(identity(f"value {n}"))        // Survives both calls.
save_for_later(f!"value {n}")        // Receiving API manages the allocation.
append(&messages, f!"value {n}")     // The container retains this string header.

text := f"value {n}"
defer delete(text)                  // Named owners remain explicit.
other := f!"value {n}"
defer delete(other)                 // f! is legal and redundant when named.
```

Both prefixes allocate with the current allocator and support quoted or raw
backtick strings. `f!` changes temporary cleanup, not the resulting string type.
No borrowing attribute, new reserved word, or print-specific behavior is involved.

## Lifetime and ownership

An anonymous default f-string supplied as an argument lives through the whole
enclosing expression. Nested calls, conditionals, switches, lazy operators, named
arguments and variadic expansion preserve native evaluation order. Only executed
producers are registered; borrowed/literal branches are never deleted. Loops
create a new scope on each evaluation, and deferred calls get their scope when
they execute. Cleanup also covers optional early returns and loop exits.

Named bindings, returned values, and strings inside named containers keep their
existing explicit ownership. An anonymous container passed to a call can itself
contain temporary f-string arguments; those element strings share the expression
lifetime. Procedure literals have their own invocation scopes, so callbacks may
run after the expression that created them has finished.

Use `f!` when an API retains or deletes the original string, including container
APIs such as `append`. It suppresses automatic temporary deletion in existing
format/query consumers as well. The receiving API's ownership contract still
applies; `f!` does not prove that the value will eventually be freed. Returning or
storing a reference to a default argument beyond its expression would dangle.

Ordinary string-returning procedure results are not inferred to be disposable.
`print(make_string())` retains that API's existing manual contract. Pure function
scratch cleanup and independent return promotion also retain their existing
semantics. `f!` does not disable a pure function's scratch cleanup.

## Implementation

The shared lexer/parser records the optional `!` without changing source byte
positions. The compiler records temporary headers and their allocators in a
bounded stack array and releases actual registrations in reverse order. There is
no additional heap allocation or string clone for ownership bookkeeping.

The scope is passed explicitly through generated expression helpers. Type
witnesses remain unevaluated; native optional-result tags and allocator-error
result types are preserved. Existing formatting/constructor cleanup recognizes
scope-managed values to avoid double deletion. User procedure bodies never
capture a surrounding expression's scope. Diagnostics and debug maps continue to
refer to original Thor expressions.

## Validation

The allocation-checking tests cover Fibonacci printing, nested borrowed returns,
mixed automatic/manual/literal branches, escaping manual values, retaining
containers, anonymous aggregate arguments, parentheses, generic/indirect/method
calls, multiple/optional results, loop/defer/early-exit behavior, pure computation
and cached compile-time results. Existing compiler/library, native SDK, diagnostic,
TLS, grammar and debugger regression suites were also run. The main commented
tour includes runnable lifetime examples and passes allocation tracking/ASan.

The broad compiler/library/native-SDK run passed 384 tests. The final call/ownership
subset passed 76, including all 21 dedicated lifetime tests; source diagnostics
and editor checks passed 88. Native checks passed 3 lexer tests and 1,126 TLS
tests, and the extension passed 112 Node tests. The full 20-test CodeLLDB suite
also passed.

Real VS Code checks verify manual-string hovers, original field-error ranges,
rename and unsaved corrections. The local VSIX has been installed; reload the
VS Code window to refresh its grammar, then restart TLS if needed.
