# Deep-copying data graphs

`deepclone` and `deepfree` are ordinary library procedures, exported by
`thor:basic`. Their implementation lives in `runtime/graph`; it is also usable
from ordinary Odin with the Thor collection configured. No compiler changes,
new keywords, implicit destruction, or persistent ownership registry are involved.

```odin
import "thor:basic"

Node :: struct {value: int, next: ^Node}

main :: proc() {
    source := Node{value=7}
    source.next = &source

    copy, err := basic.deepclone(&source)
    if err != .None {panic("cannot clone graph")}
    // copy.next == copy, and neither points into source.
    copy.value = 9

    free_err := basic.deepfree(copy)
    if free_err != .None {panic("cannot free graph")}
    copy = nil
}
```

The API takes a pointer to a source value and returns a **heap-allocated root**:

```odin
deepclone(source: ^T, allocator=context.allocator,
          scratch_allocator=context.allocator) -> (^T, Deep_Error)
deepfree(root: ^T, allocator=context.allocator,
         scratch_allocator=context.allocator) -> Deep_Error
```

Both also accept `loc=#caller_location`. `Deep_Error` is the basic alias of
`graph.Error`. Selected imports and `import * "thor:basic"` work normally.
A nil source/root succeeds without allocating. The heap root keeps self-pointers
stable; copying `copy^` into a stack variable does not transfer ownership of the
root allocation. Call `deepfree(copy)`, including for scalar or slice roots.

## What copying preserves

The source can reference stack, static, and heap data. It must be valid and
unchanged throughout the call. All supported reachable data becomes independently
owned heap storage, allocated with `allocator`:

- Typed pointers, shared objects, cycles, and pointers to embedded fields.
- Structs, fixed/enum-indexed arrays, distinct types, and active tagged unions.
- Strings, slices, dynamic arrays (including capacity), and fixed-capacity arrays.
- Overlapping string/slice views and pointers to their elements. For example,
  `selected == &values[2]` continues to hold in the copy.
- Maps, shared map backing tables, and references into map value cells. Keys are
  rebuilt using cloned data and new hashes rather than copying a hash table's
  bytes. Existing Odin rules about pointers becoming invalid on map mutation
  still apply.
- Thor sets, `Integer`, and `Rational`, through their ordinary data layouts.
  Shared big-number storage remains shared inside the new graph.

Strings/slices copy only their represented extents; they cannot discover hidden
parts of a source allocation. Dynamic arrays retain the represented capacity,
copy initialized elements, and zero unused capacity. Empty/nil descriptors retain
that distinction. Traversal uses a work queue rather than a recursion-depth limit.

## Ownership and editing before deepfree

Free exactly once through the returned root, using the same destination allocator.
All owned storage must remain reachable, with its complete allocation boundaries
represented by the graph. There is no hidden list that can recover lost owners.

Editing scalar values or buffer elements is fine. Reconnecting references is
fine if every owned allocation stays reachable and no borrowed allocation is
introduced. If several aliases share storage, treat them as aliases: independently
deleting/replacing one owner can invalidate the others. This includes Thor's
normal replacement/freeing rules for big numbers.

Do not, before `deepfree`:

- Remove the last reference to an owned allocation or advance/shrink its only
  owning slice/string view.
- Replace owned storage with a string literal, stack pointer, or another borrowed
  allocation and then ask `deepfree` to free that borrowed storage.
- Independently delete shared child objects or free the root with ordinary
  `delete` while its children remain allocated.
- Retain live references to the graph after freeing it.

New allocations attached to the graph must use the same allocator and satisfy
these rules. Normal container mutations can invalidate interior pointers, just
as they can in ordinary Odin; update/remove those aliases appropriately.
`deepfree` is intended for clone-owned graphs, not arbitrary borrowed source data.
It cannot prove that an arbitrary pointer points to a heap allocation. An invalid
pointer is not a recoverable library error.

`deepfree` discovers and validates the graph before releasing any allocation.
It then frees each distinct allocation once without following already-freed
objects. A validation or scratch-allocation failure frees nothing; fix the issue
and retry. If the allocator itself fails during freeing, cleanup continues for
other allocations and the result is `.Free_Failed`; do not blindly retry a
partially freed graph.

## Unsupported data and errors

A complete schema check runs before cloning, including nil fields and inactive
union variants. It rejects raw/unbounded pointers, `cstring`, `any`, procedure
values, raw unions, SOA containers, and recognized resource types such as
`os.File`, synchronization primitives, explicit `runtime.Allocator` and random
generator fields. Container allocator metadata is handled by the library.

Raw pointers and `cstring` do not provide target extents. Copying mutexes or file
handles does not duplicate their resources. An ordinary integer used as a file
descriptor is indistinguishable from another integer: the caller must pass data,
not resources disguised as supported types. This is not a general resource copier.

Obvious unsupported root types (`rawptr`, `any`, multi-pointers and procedure
values) get a library generic compile error. Recursive struct-field checks are
runtime checks in v1; no special compiler support has been added.

V1 rejects references into map cells **when used as map keys**, since rehashing
can move those cells and change address-based hashes. Ordinary map values may
reference cells. Views spanning map bookkeeping/multiple relocated cells are
unsupported. Reinterpreted overlapping storage with incompatible types/layouts
is outside the contract; ordinary typed fields/elements and views are supported.
An empty view exactly between two otherwise disjoint extents is also rejected:
the descriptors do not reveal which original allocation owned that address.
Overlapping/full views that establish one common extent remove that ambiguity.

| Error | Meaning |
| --- | --- |
| `.None` | Success. |
| `.Unsupported_Type` | The recursive schema contains an unsupported type/resource. |
| `.Unsupported_Reference` | The represented aliasing/layout cannot be relocated safely. |
| `.Invalid_Data` | An examined descriptor/tag has invalid bounds or representation. This does not make dangling pointers safe. |
| `.Invalid_Allocator` | A required allocator is absent, or a dynamic array/map being freed records a different allocator. Ordinary pointer/string allocator identity cannot be checked. |
| `.Allocation_Failed` | A destination or temporary allocation failed. A failed clone returns nil and rolls back its partial allocations. |
| `.Size_Overflow` | A represented byte size cannot fit. |
| `.Free_Failed` | The allocator rejected at least one free; other frees were attempted. |

Destination and scratch allocators must support individual allocation/free
operations. Rollback assumes frees succeed. Bookkeeping always disappears before
return; no scratch allocations escape. Do not reset/free the destination allocator
before the graph is released. Each call is independent; do not call `deepfree`
separately on roots sharing one cloned allocation.

See [the commented demo](../examples/deepclone/demo.thor) and
[the native tests](../runtime/graph/graph_test.odin) for examples of the relationships
and failure paths covered.
