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

| Helper | Behavior |
| --- | --- |
| `print(values, sep=" ", flush=true, end="\n")` | Print values with a separator and terminator. |
| `type(value)`, `size(value)` | Return the value's type ID or its type's size in bytes. |
| `asc(first, ..rest)`, `desc(first, ..rest)` | Check nondecreasing or nonincreasing order; equal neighbors are allowed. |
| `ordered(..values)` | Check either direction. Empty and single-element typed slices are ordered. Pass a typed slice with `..values`, following Odin's generic variadic rules. |
| `sorted(data, less=..., allocator=...)` | Allocate a sorted copy of a slice; the default comparison is `<`. Pass a comparator for records or custom ordering. |
| `_int`, `_uint`, `_i8`/`16`/`32`/`64`/`128`, `_u8`/`16`/`32`/`64` | Parse a string, returning `(value, ok)`. `base=0` infers prefixes; another base parses digits in that base. Narrow conversions check bounds. |
| `_f32`, `_f64`, `_bool` | Parse a float or boolean string, returning `(value, ok)`. Their retained `base` argument has no effect. |
| `CONCAT` | Alias of `intrinsics.concatenate` for constant array/slice concatenation. |
| `input(message="", allocator=...)` | Read one stdin line; remove LF or CRLF. Return `(text, io.Error)`, preserving a final partial line with `.EOF`. |
| `read(file_path, allocator=...)` | Read the whole file into `(text, ok)`. |
| `write(file_path, text)` | Create or truncate a file and return `(bytes_written, os.Error)`. |
| `clone(value, allocator=...)` | Shallow value copy; references still share storage, as with assignment. Its temporary allocation is freed internally. |
| `fmap(values, callback, allocator=...)` | Allocate an element-wise mapped slice with the same element type. |
| `split(text, sep="\n", allocator=...)` | Allocate a slice of borrowed string views into `text`. |
| `join(values, sep="", allocator=...)` | Allocate a joined string. |

Allocating helpers default to `context.allocator`. Delete each owned result once
using its allocator. For `split`, delete the outer slice and keep the input text
alive while its views are used. `clone` preserves ordinary shallow copy behavior;
it does not make independent backing storage for slices, strings, maps or sets.
Use the corresponding collection/string clone operation when that is needed.
The allocation-error tuples on `clone`, `fmap`, `split` and `join` keep their
original optional-error behavior.

For a runnable walkthrough, see [the example](../../examples/basic/demo.thor):

```sh
thor run /path/to/thor/examples/basic
```
