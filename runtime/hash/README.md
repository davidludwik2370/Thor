# Runtime value hashing

Use `import "thor:basic"` for the public conveniences, or import `"thor:hash"`
directly from Thor/native Odin. This implementation is ordinary library code;
it adds no keywords, compiler transformations or ownership rules.

```odin
h := basic.hash(value)                         // u64, XXH3_64, seed 0
h := basic.hash(value, algorithm=.XXH64)
h := basic.hash(value, algorithm=.FNV64a, seed=7)
h, err := basic.try_hash(value)                 // zero result on failure
```

`basic.Hash_Algorithm` has `XXH3_64`, `XXH64`, and `FNV64a`. Their native names
are `hashing.Algorithm`, `hashing.hash` and `hashing.try_hash` when importing
`hashing "thor:hash"`. These are non-cryptographic, 64-bit value hashes.

Neither API allocates or changes its input, including traversal/error state.
Strings and byte sequences use the one-shot backend. Other values stream
directly into stack-held state without constructing a serialized string or byte
buffer. Inputs and their referenced storage must remain valid and stable during
the call; this does not make concurrent mutation safe.

## Value semantics

| Value | Hashed data |
| --- | --- |
| Strings, C strings, byte arrays/slices/dynamic arrays | Contents. A top-level UTF-8 string or ordinary byte sequence matches the selected standard byte-hash algorithm. Nil and empty content agree. |
| Integers, floats, booleans, runes and enums | Values, with explicit byte-order normalization. Positive and negative floating zero agree, including in complex/quaternion values. NaNs have no equality guarantee. |
| Arrays, slices and dynamic arrays | Ordered live elements and length. Capacity, allocator and storage addresses do not participate. Fixed-capacity dynamic arrays are included. Sparse enumerated arrays skip unused index gaps. |
| Structs | Fields in declaration order, excluding padding. Field names/tags and distinct type names do not participate. |
| Tagged unions | Active variant and its value. Inactive storage does not participate. |
| `Integer`, `Rational` | Mathematical values, excluding digit capacity, allocator, flags and backing addresses. Logical Rational zero has denominator one. |
| SIMD vectors, matrices and bit fields | Logical lanes/cells/declared bits, excluding unused storage. Matrix layout does not change the hash. |
| Pointers, raw pointers and multi-pointers | Address only; no dereference. Type IDs likewise use process-local identity. |
| `any` | The contained value; nested boxes retain a structural boundary. |

Element lengths and structural markers distinguish boundaries such as
`["ab", "c"]` and `["a", "bc"]`; they do not promise collision-free hashing.
Equal supported values of the same type hash equally for the same algorithm and
seed. Different types can share a hash. Distinct wrappers retain their value
semantics unless they wrap a rejected resource type.

For FNV64a, the starting state is the standard offset basis XOR the supplied
seed. Thus `basic.hash("hello", algorithm=.FNV64a)` matches
`#hash("hello", "fnv64a")`. XXH64/XXH3 use the backend's ordinary seed meaning.
Pointer-free values are deterministic with a fixed implementation, algorithm
and seed. Composite encoding is not a versioned persistence/file format;
pointer/type-ID hashes are not stable across processes.

## Unsupported values and cycles

Maps and sets are deferred: their iteration order must not define their hash.
Raw unions, SOA layouts, procedure values and bit-field backings larger than
16 bytes are also rejected. Known file/socket, allocator, random-generator and
synchronization resource types are rejected even through named wrappers or in
empty/inactive fields. An ordinary integer used as an external handle remains
indistinguishable from ordinary numeric data. Hash an explicit ID/address or read
a resource's contents when that is the intended meaning.

Pointer cycles stop at addresses and are valid. Container cycles, including
cycles through slices or boxed `any`, return `Cyclic_Data`. Shared acyclic
storage is valid and hashes like independent equal copies. Traversal and schema
validation use a fixed 128-level limit; excessively deep data returns `Too_Deep`
instead of overflowing the traversal stack. Basic descriptor inconsistencies
return `Invalid_Value`; arbitrary invalid pointers cannot be validated safely.

`try_hash` returns `(0, error)` on failure, with no partial hash. `Hash_Error`
(`hashing.Error`) has `None`, `Unsupported_Type`, `Cyclic_Data`, `Too_Deep`,
`Invalid_Value` and `Invalid_Algorithm`. `hash` panics instead, reporting the
offending field/index path and type. Its `loc=` parameter follows normal Odin
`#caller_location` behavior: runtime panic filenames can refer to generated
`.odin` source, unlike Thor's mapped compiler diagnostics and debugger frames.

The implementation needs RTTI and is intended as a runtime helper. It does not
extend the `fn`/`#run` purity subset; `#hash` remains available for constant
strings. Existing Odin XXH3 streaming bugs are avoided by bounded updates and
one-shot short-input finalization. Regression tests compare all three backends
at buffer/stripe boundaries, with multiple seeds and chunk sizes.
