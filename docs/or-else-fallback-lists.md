# Multiple-value `or_else` fallbacks

Use a parenthesized fallback list when an optional operation has more than one
successful result:

```odin
import "core:sys/info"

_, core_count :> info.cpu_core_count() or_else (_, 1)
```

The list has one position per successful result. The operation's final boolean
or nil-comparable error is its status and has no fallback position. Each default
is checked against its corresponding successful result type, including contextual
enum names, numeric literals, and aggregate literals.

```odin
read :: proc() -> (int, string, bool) { return 0, "", false }

number, text := read() or_else (7, "fallback")
```

The operation executes exactly once. On success no default expression executes.
On failure the defaults execute in written order. Existing ownership and
allocation rules apply to the chosen values; the list itself does not allocate.
The compiler infers its result types without executing either side.

## Discarded positions

In a declaration or assignment, `_` positions must match exactly:

```odin
_, text := read() or_else (_, "fallback") // Allowed.
_, text = read() or_else (_, "another")  // Allowed.

// _, text := read() or_else (0, "fallback") // Error: use `_` in position 1.
// number, text := read() or_else (_, "fallback") // Error: position 1 is kept.
```

A discarded fallback slot is not evaluated. On success the ordinary rules for
discarding the operation's result still apply, including cleanup of directly
owned Thor big numbers. The matching rule follows result positions through
parentheses and value-producing switch expressions, and respects other RHS
values in a parallel declaration or assignment.

Returns and call arguments have no discarded assignment position. Supply every
default value in those contexts:

```odin
choose :: proc() -> (int, string) {
    return read() or_else (7, "fallback")
}

consume(read() or_else (7, "fallback"))
```

## Scope and compatibility

- This is syntax for the right-hand operand of `or_else`, not a general tuple
  value or tuple type.
- A fallback list requires at least two values. For a single successful result,
  use the existing `or_else value` or `or_else (value)` syntax.
- A trailing comma and extra enclosing parentheses are allowed.
- Existing procedure-call fallbacks remain supported. Their result slots do not
  require `_` markers when the caller discards some outputs.
- `fn` purity, `#run`, immutable slice permissions, automatic anonymous f-string
  cleanup, and big-number ownership follow their existing rules.
- TLS provides per-result hover types, contextual field/enum completion,
  immutable highlighting, and diagnostics on the original source slots.
- Breakpoints and stepping retain original Thor locations.

See [the runnable example](../examples/or-else-lists/or_else_lists.thor) and
[the regression tests](../tests/test_or_else_lists.py). An independently
reproduced Odin mixed-result fallback-call issue and Thor's packing workaround
are recorded in [the upstream bundle](../upstream/odin-or-else-multi-results/README.md).
