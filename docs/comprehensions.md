# Collection comprehensions

Comprehensions construct a collection with one loop and an optional filter:

```odin
SQUARES :: [x*x for x in 0..<10 if x%2==0] // [5]int in static storage
squares :> [x*x for x in values if x>0]   // allocated []int
small   := []i8{i8(x) for x in values if x>0}
growing := [dynamic]int{x*x for x in values}
unique  := set[int]{x*x for x in values}
lookup  := map[int]int{x=x*x for x in values}
```

The ordinary expression is evaluated once for each accepted item. The source
(or each range bound) is evaluated once; the filter runs before the output.
For maps the key runs before the value, including for duplicate keys. Loop
bindings are local to the comprehension, including in the output before `for`.
Outer locals remain visible. `for x in x` reads the outer `x` as its source.

Normal collection/range iteration applies: values and optional indices for
sequences, key/value for maps, members for sets, runes/byte offsets for strings,
and enum values. Iterator procedures returning multiple values are not supported
in this first version. Typed numeric ranges (`for x:f64 in 0..<10`) and checked sequence
indices (`for value,(index:i8) in values`) follow the existing typed-loop rules.
An `&value` binding has native Odin reference semantics and requires an
addressable iterable; use `&value` to pass its address to a mutating procedure.

A bare runtime comprehension returns a slice. An explicit constructor chooses
slice, dynamic array, set or map; fixed-array constructors are not supported.
The explicit element type supplies normal literal/record/enum context. An
expected slice type also supplies context to a bare runtime comprehension.
Typed values retain Odin's usual conversion requirements.

## Flattening and merging

Use a leading `..` for all unpacking in a comprehension:

```odin
rows := [][]int{{1,2},{3,4}}
flat := [..row for row in rows]                  // []int{1,2,3,4}
flat_growing := [dynamic]int{..row for row in rows}
unique := set[int]{..row for row in rows}
merged := map[string]int{..part for part in maps}
```

Sequence destinations accept arrays, slices, dynamic arrays and fixed-capacity
dynamic arrays. Set destinations additionally accept sets. Map unpacking accepts
maps and contributes complete key/value entries. Strings are not unpacked.
Unpacking is one level, in the source's normal iteration order. Slices and
dynamic arrays preserve duplicates; sets keep one of each value. Maps keep the
last value for a repeated key. Map/set iteration itself has no stable order.

Multiple `for` clauses and syntactically nested comprehensions are rejected.
Bind an intermediate result separately and free it according to its ownership.
Only one `if` is accepted; combine conditions with `&&`. This does not prohibit
nested ordinary array literals or calls to procedures that build collections.

## Ownership and allocation failures

Runtime results allocate through `context.allocator`. Delete every owned result
once, including one passed anonymously into a procedure. Comprehensions do not
introduce automatic argument cleanup. `:>` keeps its existing shallow rules;
it does not make the underlying slice/map/set storage deeply readonly.

Elements use ordinary shallow aggregate ownership. Copying a slice element
copies its header; it does not duplicate its backing storage. Fresh allocated
strings produced as elements are retained, not freed by the compiler's internal
insertion calls. Delete those strings as well as the outer result:

```odin
texts := [f"item {i}" for i in 0..<3]
for text in texts {delete(text)}
delete(texts)
```

Independent `Integer`/`Rational` output expressions follow Thor's existing direct
copy rules. Reference-containing elements remain borrowed for purity checks.

Allocation failure panics with `Comprehension allocation failed` at the Thor
construction site, including with assertions disabled. The compiler's outer
construction buffer is freed before that panic. Element ownership is not rolled
back recursively: manually owned values already produced by user expressions
retain the ordinary ownership contract. A user expression that panics has the
normal Odin panic behavior; comprehension construction does not add unwinding.
Slice construction uses a temporary dynamic buffer and returns an exact-length
slice, freeing the buffer. A dynamic result retains that buffer directly. Set
and map construction use checked entry insertion, including growth failures.
Empty constructions need no allocation. As with ordinary Odin globals, a
package-level runtime initializer uses the runtime's default context before
`main`. If `main` switches allocators (including debug tracking), free its global
slice/string allocations using their original allocator.

Avoid overwriting manually owned map values through duplicate keys unless you
retain another owner that can release them. Last-value-wins assignment is
shallow and does not deep-free the replaced value.

## Compile-time arrays

A bare comprehension in a `::` constant must have compile-time-known iteration
inputs, and its output/filter/calls must obey the normal transitive `fn` rules.
Ordinary eligible `proc` calls are allowed. Thor evaluates in scratch storage,
finds the filtered/flattened length, and emits a native fixed-array constant.
The result requires no runtime allocation or deletion. Required evaluations use
the existing project-local compile-time cache.

```odin
square :: proc(x:int)->int {return x*x}
EVENS :: [square(x) for x in 0..<10 if x%2==0]
#assert(type_of(EVENS)==[5]int)
```

Explicit allocated constructors require a runtime binding. Constants cannot
contain owned slice/dynamic-array elements or logical-number aggregates beyond
the existing compile-time serialization boundaries. Ordinary supported fixed
arrays, strings and records work. An explicit array annotation can provide enum/record context for a constant
comprehension. `#run` on an eligible function returning a
runtime comprehension materializes an independently owned result as usual.
A runtime comprehension remains allocated even when all its inputs are constant.

## Editor and diagnostics

TLS uses the same parser and original identifiers for hover, completion,
readonly loop highlighting, navigation and rename. Constant extents that can
be folded safely are shown as fixed-array lengths; otherwise TLS uses `?` for
that extent instead of executing user code in the editor. The compiler remains
authoritative for exact types, lengths and purity. Diagnostics and debug maps
retain the original source locations, including inside output/filter calls.
