// Run: thor run examples/deepclone -debug
// These helpers live in the basic library; they do not change assignment or
// introduce compiler ownership rules. Every check below also runs in release.
package main

import "thor:basic" / deepclone, deepfree, print

Node :: struct {
    name: string,
    next: ^Node,
}

Graph :: struct {
    // The selected pointer and both views must continue to refer to the same
    // elements after cloning, including when the pointer is encountered first.
    selected: ^int,
    tail, values: []int,
    first, second: ^Node,
    lookup: map[string]^Node,
}

main :: proc() {
    // Source data may live on the stack, in static strings, or in heap storage.
    // It must stay valid and unchanged while deepclone examines it.
    values := [10, 20, 30, 40]
    a := Node{name="first"}
    b := Node{name="second"}
    a.next = &b
    b.next = &a

    lookup := make(map[string]^Node)
    defer delete(lookup)
    lookup["first"] = &a
    lookup["second"] = &b

    original := Graph{
        selected=&values[2], tail=values[2:], values=values[:],
        first=&a, second=&b, lookup=lookup,
    }

    // A heap root gives self-references a stable address. Both results matter:
    // failure returns nil and releases any partially allocated destination.
    copy, err := deepclone(&original)
    if err != .None { panic("deepclone failed") }

    if copy.first.next != copy.second || copy.second.next != copy.first {
        panic("cycle was not preserved")
    }
    if copy.lookup["first"] != copy.first {
        panic("shared reference was not preserved")
    }
    if copy.selected != &copy.values[2] || &copy.tail[0] != copy.selected {
        panic("interior reference was not preserved")
    }

    // Editing data is fine. The source remains independent; relationships
    // inside the copy still work normally.
    copy.selected^ = 99
    if copy.tail[0] != 99 || original.values[2] != 30 {
        panic("copy was not independent")
    }
    print(copy.first.name, copy.second.name, copy.values)

    // Free once through the stable root. Do not individually delete strings,
    // nodes, map storage or slices that belong to this graph. Do not detach an
    // owner or replace it with borrowed storage before deepfree.
    // allocator= may be supplied to both operations; it must be the same one.
    free_err := deepfree(copy)
    if free_err != .None { panic("deepfree failed") }
    copy = nil

    // Recursive unsupported fields are rejected at runtime even if currently
    // nil. Raw pointers have no target size; an FD/mutex is not clonable data.
    Unsupported :: struct { opaque: rawptr }
    unsupported: Unsupported
    bad_copy, bad_err := deepclone(&unsupported)
    if bad_copy != nil || bad_err != .Unsupported_Type {
        panic("unsupported schema was accepted")
    }

    print("deepclone demo passed")
}
