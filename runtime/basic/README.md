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
| `hash(value, algorithm=.XXH3_64, seed=0)` | Return an allocation-free `u64` content hash; panic with a field path for unsupported/cyclic data. |
| `try_hash(value, algorithm=.XXH3_64, seed=0)` | Return `(u64, Hash_Error)`; failed results are zero. `Hash_Algorithm` also supports `.XXH64` and `.FNV64a`. |
| `sorted(data, less=..., allocator=...)` | Allocate a sorted copy of a slice; the default comparison is `<`. Pass a comparator for records or custom ordering. |
| `_int`, `_uint`, `_i8`/`16`/`32`/`64`/`128`, `_u8`/`16`/`32`/`64` | Parse a string, returning `(value, ok)`. `base=0` infers prefixes; another base parses digits in that base. Narrow conversions check bounds. |
| `_f32`, `_f64`, `_bool` | Parse a float or boolean string, returning `(value, ok)`. Their retained `base` argument has no effect. |
| `_string(value, allocator=...)` | Return independent text and an optional `runtime.Allocator_Error`. Supports all scalar numeric types, `Integer`, `Rational`, bools, enums, runes, strings/cstrings, and byte/rune sequences. |
| `input(message="", allocator=...)` | Read one stdin line; remove LF or CRLF. Return `(text, io.Error)`, preserving a final partial line with `.EOF`. |
| `read(file_path, allocator=...)` | Read the whole file into `(text, ok)`. |
| `write(file_path, text)` | Create or truncate a file and return `(bytes_written, os.Error)`. |
| `clone(value, allocator=...)` | Shallow value copy; references still share storage, as with assignment. Its temporary allocation is freed internally. |
| `deepclone(source: ^T, allocator=..., scratch_allocator=...)` | Return a heap root and `Deep_Error`; copy supported reachable data while preserving cycles, shared storage, overlapping views and interior pointers. |
| `deepfree(root: ^T, allocator=..., scratch_allocator=...)` | Free a clone-owned graph once, returning `Deep_Error`. Use the same destination allocator and preserve ownership boundaries/reachability until cleanup. |
| `fmap(values, callback, allocator=...)` | Allocate an element-wise mapped slice with the same element type. |
| `pmap(jobs, callback, result_type=..., workers=0, allocator=..., worker_allocator=...)` | Map struct jobs with bounded parallel workers, preserving order. [Results and ownership](../parallel/README.md). |
| `split(text, sep="\n", allocator=...)` | Allocate a slice of borrowed string views into `text`. |
| `join(values, sep="", allocator=...)` | Allocate a joined string. |
| `to_set(values, allocator=...)`, `to_slice(set, allocator=...)` | Allocate independent containers; element copies are shallow and set order is unspecified. Also exported by `thor:sets`. |
| `zip(a=first, b=second, ...)` | Alias for `soa_zip`. Return an allocation-free structure-of-arrays view of named slices, with the shortest input's length. Columns share their inputs' storage and slice permissions. |
| `filter(values, predicate, allocator=...)` | Allocate a slice of matching elements in input order. The predicate runs once per element; copies are shallow. Empty/no-match inputs yield an empty slice. |
| `and(a, b, ...)`, `or(a, b, ...)` | Reduce boolean arguments, stopping at the first false/true respectively. Empty calls return true/false. Spread a slice with `and(..flags)` or `or(..flags)`; no allocation. |
| `sum(values)` | Sum a numeric slice with Odin's arithmetic/overflow rules; empty input returns the element type's zero value. Supports distinct numeric, complex and quaternion types. |
| `range(end)`, `range(start, end)`, `range(start, end, step)` | Allocate a half-open `[]int`, with a default start of zero and step of one. Descending ranges need a negative step. Zero step, equal bounds and the wrong direction yield an empty slice. Bounds/count arithmetic is widened before allocation. |
| `chr(code_point)` | Return one Unicode scalar as a `rune`, without allocating. Negative, surrogate and out-of-range values assert. |
| `ord(value)` | Return a single-rune/single-code-point string's Unicode value, or the byte from a one-element `[]u8`. Empty/multiple/invalid input asserts. A combining sequence such as `"e\u0301"` has two code points. |
| `random(T, lo=..., hi=..., gen=...)` | Sample integer/float values with both bounds inclusive. Omitted bounds are `min(T)`/`max(T)`; explicit zero stays zero. Requires ordered bounds and finite floats. Uses `context.random_generator` unless `gen` is supplied. |
| `table(rows, max_col_width=0, float_format="%.2f")` | Allocate an ASCII-bordered table from a slice of structs. Field tags override headings; numeric values align right, other values and headings left. |

`and` and `or` accept boolean values, with no Python-style truthiness. Their
loops return as soon as the result is known. As with ordinary procedure calls,
all argument expressions are evaluated before entering the procedure. Use
`&&` and `||` when evaluating the expressions themselves must short-circuit.

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

## Converting values to strings

```odin
letters := []rune{'a', '界', '😀'}
text, err := basic._string(letters)
if err != nil { panic("could not allocate text") }
defer delete(text)

// The allocation error is optional, just like Odin's string cloning helpers.
number := basic._string(12345) or_panic
defer delete(number)
unchecked := basic._string(true) // Discard the allocation error explicitly by omission.
defer delete(unchecked)
```

Numeric/Boolean/enum output matches a plain `f"{value}"` field: native integer,
float, complex and quaternion types (including distinct/endian variants), bool
and `b8`/`b16`/`b32`/`b64`, and enum names. `Integer` emits exact decimal digits;
`Rational` emits a normalized exact fraction, including `"0/1"` for zero. Use an
f-string when precision, a decimal Rational expansion, padding or grouping is
needed.

A rune becomes its UTF-8 character. Byte/rune slices, fixed arrays, dynamic
arrays and fixed-capacity dynamic arrays become text without list punctuation.
Rune sequences are UTF-8 encoded; negative, surrogate and out-of-range code
points become U+FFFD. Byte sequences are copied verbatim, including embedded
zero bytes and invalid UTF-8. Strings are copied without quotes; `cstring` is
read up to its first zero byte (nil becomes empty). General structs, pointers,
maps, arbitrary numeric sequences, `any`, and UTF-16 string types are unsupported
and fail at compile time. These remain available for inspection with f-strings.

Every nonempty successful result owns an exact-sized allocation, even when the
input is already a string. Inputs are borrowed and never freed or modified.
Delete the result with the same allocator; empty output needs no allocation.
Every internal allocation uses the requested allocator. Failed conversions
release intermediates and return `("", err)`, never truncated/partial output.
Ignoring the optional error therefore produces an empty string on failure.
Custom native scalar formatters follow their normal callback behavior; the
helper detects destination allocation failures, but cannot recover a callback
that explicitly panics or undo its own unrelated side effects.

This is an ordinary allocating procedure: `_string` results passed directly to
another procedure do not receive anonymous f-string cleanup. Keep the result in
a variable or use a scoped arena. The implementation is shared with the native
`thor:format` package's `try_string` procedure; no new language syntax or purity
exception is introduced.

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

Runtime hashes read strings, ordered containers and record fields by value;
pointers hash their addresses. Padding, spare capacity and allocator metadata
are ignored. `Integer`/`Rational` use logical numeric values. Maps/sets and known
opaque resources are unsupported in this first version, and recursive
containers fail clearly. See the [hashing contract](../hash/README.md) for seeds,
supported types, error handling, depth limits and portability.

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
