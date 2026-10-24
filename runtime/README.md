# Thor support collection

The driver supplies `thor:` automatically unless explicitly overridden with
`-collection:thor=...`. These libraries use ordinary Odin syntax and can also be
used with native Odin by supplying `-collection:thor=/path/to/thor/runtime`.

## Formatting (T07)

Import `f "thor:format"`. `f.group_integer(value)` groups decimal integer digits,
including signed minima and 128-bit values. `f.format(value, width=0,
align=.Default, fill=' ')` formats ordinary values; add `grouping=true` for integer
grouping. Unsupported grouping types fail compilation. The option is a generic
constant and must be known at compile time.

Width counts Unicode runes, not UTF-8 bytes or terminal display cells. Alignment
is `.Left`, `.Right`, `.Center`, or `.Default` (numeric right, other values left).
Odd center padding puts the extra rune on the right. Values are never truncated;
fill must be a Unicode scalar and width nonnegative. Formatting returns a fresh
caller-owned string: `delete(result, allocator)`, using the selected allocator
(default `context.allocator`). Intermediate formatting storage is released.

Interpolation syntax and format-spec parsing are T13. These helpers do not yet
make claims about pure-function execution or compile-time allocation.

## Sets (T08)

Import `sets "thor:sets"`. `Set(T)` accepts native map key types. A zero value
is empty and ready for insertion. All element ownership is shallow.

| Operation | API |
| --- | --- |
| Insert / remove one | `insert(&s, value)`, `remove(&s, value)` |
| Query | `contains(s, value)`, `count(s)`, `equal(a, b)` |
| Iterate without allocation | `for key in entries(s) { ... }` |
| Clear / free table | `clear_set(&s)`, `destroy(s)` |
| Independent table copy | `clone(s, allocator)` |
| Independent mathematical result | `union_of(a,b)`, `intersection(a,b)`, `difference(a,b)`, `symmetric_difference(a,b)` |
| Mutate target | `union_assign(&a,b)`, `intersection_assign(&a,b)`, `difference_assign(&a,b)`, `symmetric_difference_assign(&a,b)` |
| Subtract element or set | `subtract_assign(&s, value_or_set)` |

Result-producing operations accept an optional allocator; destroy each owned
table once. They copy keys shallowly. Removing/clearing/destroying a set does not
free allocations referenced by keys. `entries` is a borrowed native map view;
do not mutate while iterating it. In-place operations handle self/aliased inputs
by snapshotting keys where necessary and release those snapshots before returning.

Ordinary assignment copies a map header and aliases its backing storage. Aliases
are not independent sets or synchronized headers: resizing, clearing, or deleting
through one alias has ordinary Odin consequences for the others. Use `clone` for
an independent table and avoid duplicate destruction. Runtime tests cover
mathematical results, duplicates/missing keys, and self/aliased operations with
memory tracking enabled. Set syntax (`set[T]`, operators, builtin integration)
belongs to T15.
