# Basic helpers

`basic.thor` is the canonical source for the bundled `thor:basic` package. It was
moved here from the checkout's `basic/basic.thor` directory. Thor and TLS find
this collection automatically; projects do not need to copy the file or add a
collection flag.

```odin
package main

import "thor:basic"

main :: proc() {
    data := []int{3, 1, 2}
    result := basic.sorted(data)
    defer delete(result)
    basic.print(result) // [1, 2, 3]; data is unchanged.
}
```

Run it with `thor run .` from the project's directory. This package uses Thor's
inline generic default callbacks, so build consumers with Thor. The other
support packages written entirely in Odin remain usable directly with Odin.

A plain import keeps names under `basic`. Select unqualified names explicitly,
import every public name, or choose a different namespace:

```odin
import "thor:basic" / sorted, print
// or: import * "thor:basic"
// or: import _ "thor:basic" / sorted, print
// or: import b "thor:basic"
```

Selected and wildcard imports retain their namespace as well. The `_` form
exposes only the selected names and does not introduce `basic`. Each import's
unqualified names are visible only in that file and are not re-exported. Local
bindings may shadow them; conflicting file-scope declarations are errors.
Wildcard imports also reject overlapping public names from different packages,
including names in conditional branches. Internal imports such as `fmt`
and the private `_parse_int` helper are not exported. Leading underscores alone
do not mark names private, so `_int` and the other conversion helpers are public.

Compile-time array/slice concatenation uses the language's `A ++ B` operator.

| Helper | Behavior |
| --- | --- |
| `print(values, sep=" ", flush=true, end="\n")` | Print values with a separator and terminator. |
| `type(value)`, `size(value)` | Return the value's type ID or its type's size in bytes. |
| `scoped_arena()` | Return a growing arena allocator with automatic cleanup at the end of the calling scope, including early returns, breaks and continues. |
| `sorted(data, less=..., allocator=...)` | Allocate a sorted copy of a slice; the default comparison is `<`. Pass a comparator for records or custom ordering. |
| `_int`, `_uint`, `_i8`/`16`/`32`/`64`/`128`, `_u8`/`16`/`32`/`64` | Parse a string, returning `(value, ok)`. `base=0` infers prefixes; another base parses digits in that base. Narrow conversions check bounds. |
| `_f32`, `_f64`, `_bool` | Parse a float or boolean string, returning `(value, ok)`. Their retained `base` argument has no effect. |
| `input(message="", allocator=...)` | Read one stdin line; remove LF or CRLF. Return `(text, io.Error)`, preserving a final partial line with `.EOF`. |
| `read(file_path, allocator=...)` | Read the whole file into `(text, ok)`. |
| `write(file_path, text)` | Create or truncate a file and return `(bytes_written, os.Error)`. |
| `clone(value, allocator=...)` | Shallow value copy; references still share storage, as with assignment. Its temporary allocation is freed internally. |
| `deepclone(source: ^T, allocator=..., scratch_allocator=...)` | Return a heap root and `Deep_Error`; copy supported reachable data while preserving cycles, shared storage, overlapping views and interior pointers. |
| `deepfree(root: ^T, allocator=..., scratch_allocator=...)` | Free a clone-owned graph once, returning `Deep_Error`. Use the same destination allocator and preserve ownership boundaries/reachability until cleanup. |
| `fmap(values, callback, allocator=...)` | Allocate an element-wise mapped slice with the same element type. |
| `split(text, sep="\n", allocator=...)` | Allocate a slice of borrowed string views into `text`. |
| `join(values, sep="", allocator=...)` | Allocate a joined string. |
| `to_set(values, allocator=...)`, `to_slice(set, allocator=...)` | Allocate independent containers; element copies are shallow and set order is unspecified. Also exported by `thor:sets`. |
| `filter(values, predicate, allocator=...)` | Allocate a slice of matching elements in input order. The predicate runs once per element; copies are shallow. Empty/no-match inputs yield an empty slice. |
| `sum(values)` | Sum a numeric slice with Odin's arithmetic/overflow rules; empty input returns the element type's zero value. Supports distinct numeric, complex and quaternion types. |
| `range(end)`, `range(start, end)`, `range(start, end, step)` | Allocate a half-open `[]int`, with a default start of zero and step of one. Descending ranges need a negative step. Zero step, equal bounds and the wrong direction yield an empty slice. Bounds/count arithmetic is widened before allocation. |
| `chr(code_point)` | Return one Unicode scalar as a `rune`, without allocating. Negative, surrogate and out-of-range values assert. |
| `ord(value)` | Return a single-rune/single-code-point string's Unicode value, or the byte from a one-element `[]u8`. Empty/multiple/invalid input asserts. A combining sequence such as `"e\u0301"` has two code points. |
| `random(T, lo=..., hi=..., gen=...)` | Sample integer/float values with both bounds inclusive. Omitted bounds are `min(T)`/`max(T)`; explicit zero stays zero. Requires ordered bounds and finite floats. Uses `context.random_generator` unless `gen` is supplied. |
| `table(rows, max_col_width=0, float_format="%.2f")` | Allocate an ASCII-bordered table from a slice of structs. Field tags override headings; numeric values align right, other values and headings left. |

Allocating helpers default to `context.allocator`. Delete each owned result once
using its allocator. For `split`, delete the outer slice and keep the input text
alive while its views are used. `clone` preserves ordinary shallow copy behavior;
it does not make independent backing storage for slices, strings, maps or sets.
Use the corresponding collection/string clone operation when that is needed.
For full graph copies, see the [deepclone/deepfree contract](../../docs/deepclone.md)
and [commented demo](../../examples/deepclone/demo.thor). These are ordinary
library procedures, with runtime rejection of recursively unsupported schemas;
they do not change assignment or add automatic ownership management.
The allocation-error tuples on `clone`, `fmap`, `split` and `join` keep their
original optional-error behavior.

## Scoped arena allocations

```odin
{
    context.allocator = basic.scoped_arena()
    values := make([]int, 100)
    text := f"Created {len(values)} values"
    basic.print(text)
    // Arena storage is released together; no individual deletes are needed.
}
// The enclosing context.allocator is restored by Odin's block-scoping rules.
```

The returned allocator owns a growing `core:mem/virtual` arena. Odin's native
`@(deferred_out=...)` attribute registers its destruction in the calling scope.
A small, stable management record comes from the parent allocator, which is
saved for cleanup; all arena blocks are released before that record is freed.
Initialization failure panics, including in unchecked release builds. Nested
calls create independent arenas and clean up from inner to outer scope.

Creation does not modify context by itself. To use an explicit allocator, write
`arena := basic.scoped_arena()` and pass `arena` to allocation procedures. The
factory's result must be handled. The allocator and its allocations must not
escape the calling scope. Copy any required results into a longer-lived
allocator before exit. Manual early destruction of the arena is unsupported.

Only allocations using this allocator belong to the arena. Explicit allocator
arguments and existing maps/dynamic arrays keep their own allocator choices.
`context.temp_allocator` is unchanged. File/socket/resource cleanup remains
separate. Individual frees generally do not reclaim arena memory; it remains
reserved until scope exit or an explicit `free_all(arena)` reset. Such a reset
invalidates previous arena allocations, but keeps the scoped allocator usable.

Create the allocator directly in its owning scope. Calls with automatic scope
cleanup are rejected inside Thor expressions lowered to an internal procedure
(for example a switch-result arm or interpolation field), since that would
destroy the arena before the surrounding code could use it. Bind the allocator
first, then reference the named value inside those expressions. The helper is a
runtime procedure and is not eligible for `fn`/`#run` execution.

## Additional allocation and value rules

`input` retains the capacity of its temporary read buffer until cleanup and
returns an exact-size string, so trimming LF/CRLF does not invalidate a sized
allocator's free operation. Allocation failure returns `io.Error.Unknown`, since
its I/O error type has no allocator-error variant. Partial lines at EOF remain
caller-owned and accompany `.EOF`.

`filter` and `range` results follow the same deletion rule. `filter` shrinks its
dynamic buffer before returning a slice, or copies to an exact-size allocation
if shrinking is unsupported. This makes deleting the slice valid for allocators
that check the size supplied to `delete`.

`range` produces machine-sized integers. Its `#any_int` arguments follow Odin's
integer conversion rules; use representable bounds/steps. The maximum result
length is checked against the allocation's byte size. A very large valid range
can still exhaust memory; it is not a lazy iterator.

`table` uses the current allocator for its final string and all internal work;
delete its final string once. It releases its temporary strings and arrays.
Widths count terminal display cells, including wide Unicode and intact grapheme
clusters. Each column has one space on either side; continuation lines indent by
up to two spaces, reduced for narrow columns. Newlines in headings/cells are
preserved, tabs become four spaces and ASCII controls are escaped. A positive
`max_col_width` must fit the widest displayed character; zero uses measured
widths. A table with no rows still prints headings; a struct with no fields
produces `""`. The returned table includes its final newline.

Pure eligibility depends on the actual implementation. `sum`, `chr` and
`ord(rune)` can be used from `fn`/`#run`. `ord(string)` currently calls Odin's
UTF-8 decoder, whose mutable lookup tables are excluded by the purity contract.
The allocation/context/callback/reflection requirements of `range`, `filter`,
`random` and `table` also keep those implementations outside that subset.
Use them as ordinary runtime procedures; no library import bypasses purity rules.

For a runnable walkthrough, see [the example](../../examples/basic/demo.thor):

```sh
thor run /path/to/thor/examples/basic
```
