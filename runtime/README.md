# Thor support collection

The driver supplies `thor:` automatically unless explicitly overridden with
`-collection:thor=...`. Libraries written in `.odin` can also be used with native
Odin by supplying `-collection:thor=/path/to/thor/runtime`. The `basic` package is
written in Thor and uses its inline generic default callbacks.

## Basic helpers

`import "thor:basic"` provides `basic.print`, `basic.sorted`, order checks, parsing,
file/line I/O, and small collection/string helpers. It resolves automatically from
any Thor project. For unqualified names, use the existing selection syntax, such
as `import "thor:basic" / print, sorted`.

The source is [basic/basic.thor](basic/basic.thor), moved from the checkout's
former top-level `basic/` directory. See the [API and ownership notes](basic/README.md)
and [runnable example](../examples/basic/demo.thor). Allocated results remain
caller-owned; `clone` retains shallow value-copy semantics.

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

T13 interpolation uses these helpers and frees its own formatting buffers.
Its final result is caller-owned; borrowed/user-returned field values retain
their normal ownership in ordinary procedures. Pure functions use the separate
compiler-managed scratch policy described below.
The compiler releases anonymous nested interpolation results after formatting,
including the selected allocated arm of a mixed owned/borrowed branch.

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
memory tracking enabled. T15 adds `set[T]`, literals, operators, and builtin
integration over this same support type. `compiler.odin` contains generic dispatch
that preserves native operations for non-Set instantiations, including contextless
native arithmetic and iteration. These dispatch functions are compiler support.
Thor operators release their anonymous owned inputs after use and may reuse a
fresh left union result, so `a | b | c` needs only one result table. Named inputs
and caller-visible results retain explicit ownership. Element allocations remain
shallow and require their own lifetime; the runtime APIs themselves do not infer
ownership or automatically delete operands.

## Sequence bounds (T16)

`thor:sequence` supplies contextless normalization and element type witnesses.
Signed arithmetic widens to i128 before adding length, and checks the wide result
before native machine-index conversion. Unsigned values retain their original
type. The emitter applies this only to ordinary sequences, preserves storage for
writes/addresses, and leaves map/pointer/enumerated-array indexing native.

The same support package implements min/max reductions used by Thor's spread
forms. Slice helpers return `(value, ok)` with optional status; an empty slice
without an initial candidate returns the element type's zero value and false.
Fixed-array helpers require a positive length and return one element value.
Seeded helpers carry the running candidate and presence flag across original
arguments without concatenating containers. Comparisons use native min/max for
numeric types and equivalent explicit comparisons for strings, avoiding an Odin
backend dependency-registration bug in imported generic string min/max calls.
They use no allocator or mutable global state. These helpers are compiler support;
the public syntax and examples are documented in the root README.

## Pure scratch storage and value protocol (T18–T20)

`thor:pure` and `thor:eval` are compiler support, not general-purpose purity
annotations for arbitrary user procedures. Generated contracts validate actual
instantiated input/result types. The frontend supplies record field schemas,
while Odin resolves their exact field types. Unsupported/reference-bearing
results fail at the original function declaration.

Every checked function saves its caller allocator in a tracking scratch allocator.
Its ordinary and temporary allocator slots both refer to that scratch allocator.
Explicit frees remove tracked allocations; every remaining allocation is freed
on return. Nested calls have separate trackers backed by the outer scratch
allocator. No allocated result may escape this initial implementation: strings,
slices and nested allocation-bearing results are rejected. Ordinary `proc`
interpolation remains allocated with the current allocator and caller-managed.

The native helper serializes a versioned JSON value tree before its storage
expires. Integers use decimal strings; floats carry raw bytes and endianness;
strings carry byte hex; aggregates carry individual fields/elements and union
variants. Nil and nonnil empty slice descriptors are distinct in the protocol.
Padding and addresses are excluded. Reconstruction validates the packet and emits
typed native constants. The current result policy intentionally exposes only
scalar and pointer-free fixed aggregate results. See the root README for native
execution limits and conservative schema boundaries.

## Numeric power

The compiler uses `thor:numeric` for infix `**`. Its contextless `power` procedure
implements integer exponentiation by squaring and delegates floating-point powers
to `core:math.pow`. Typed callers supply matching operands. Negative integer
exponents panic; zero exponents return one. No allocator is used.
