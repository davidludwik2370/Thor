// A distinct map-backed Set with ordinary Odin shallow element ownership.
package thor_sets

import "base:intrinsics"

Set :: struct($T: typeid) where intrinsics.type_is_valid_map_key(T) {
    table: map[T]struct{},
}

// A zero-initialized Set is ready for insertion; Odin's map allocates lazily.
insert :: proc(set: ^Set($T), value: T) { set.table[value] = {} }
remove :: proc(set: ^Set($T), value: T) { delete_key(&set.table, value) }
contains :: proc "contextless"(set: Set($T), value: T) -> bool { return value in set.table }
count :: proc "contextless"(set: Set($T)) -> int { return len(set.table) }

// entries is a borrowed map view for ordinary `for key in entries(set)` loops.
// It allocates nothing. Do not mutate a set while iterating that view.
entries :: proc "contextless"(set: Set($T)) -> map[T]struct{} { return set.table }
clear_set :: proc "contextless"(set: ^Set($T)) { clear(&set.table) }

// Destroy only the table. Keys retain Odin's shallow value-copy ownership.
// Copies of a Set alias its table; destroy that table only once.
destroy :: proc(set: Set($T)) { delete(set.table) }

clone :: proc(set: Set($T), allocator := context.allocator) -> Set(T) {
    result := Set(T){table=make(map[T]struct{}, len(set.table), allocator)}
    for value in set.table { result.table[value] = {} }
    return result
}

equal :: proc "contextless"(a, b: Set($T)) -> bool {
    if len(a.table) != len(b.table) { return false }
    for value in a.table { if value not_in b.table { return false } }
    return true
}

union_assign :: proc(target: ^Set($T), other: Set(T)) {
    // Aliasing is safe: every iterated key already exists in the same table.
    for value in other.table { target.table[value] = {} }
}

@(private="file")
snapshot :: proc(set: Set($T), allocator := context.allocator) -> []T {
    values := make([]T, len(set.table), allocator)
    assert(len(values) == len(set.table), "set snapshot allocation failed")
    i := 0
    for value in set.table { values[i] = value; i += 1 }
    return values
}

intersection_assign :: proc(target: ^Set($T), other: Set(T)) {
    values := snapshot(target^)
    defer delete(values)
    for value in values { if value not_in other.table { delete_key(&target.table, value) } }
}

difference_assign :: proc(target: ^Set($T), other: Set(T)) {
    values := snapshot(other)
    defer delete(values)
    for value in values { delete_key(&target.table, value) }
}

symmetric_difference_assign :: proc(target: ^Set($T), other: Set(T)) {
    values := snapshot(other)
    defer delete(values)
    for value in values {
        if value in target.table { delete_key(&target.table, value) }
        else { target.table[value] = {} }
    }
}

union_of :: proc(a, b: Set($T), allocator := context.allocator) -> Set(T) {
    result := clone(a, allocator)
    union_assign(&result, b)
    return result
}
intersection :: proc(a, b: Set($T), allocator := context.allocator) -> Set(T) {
    result := Set(T){table=make(map[T]struct{}, min(len(a.table), len(b.table)), allocator)}
    for value in a.table { if value in b.table { result.table[value] = {} } }
    return result
}
difference :: proc(a, b: Set($T), allocator := context.allocator) -> Set(T) {
    result := Set(T){table=make(map[T]struct{}, len(a.table), allocator)}
    for value in a.table { if value not_in b.table { result.table[value] = {} } }
    return result
}
symmetric_difference :: proc(a, b: Set($T), allocator := context.allocator) -> Set(T) {
    result := clone(a, allocator)
    // The newly cloned result is independent from b, even if a and b alias.
    for value in b.table {
        if value in result.table { delete_key(&result.table, value) }
        else { result.table[value] = {} }
    }
    return result
}

subtract_assign :: proc{remove, difference_assign}

// Type witnesses are only used inside type_of by the compiler.
element_witness :: proc "contextless"(set: Set($T)) -> T { return {} }
key_witness :: proc "contextless"(table: $M/map[$K]$V) -> K { return {} }

from_values :: proc($T: typeid, values: [$N]T, allocator := context.allocator) -> Set(T) {
    result: Set(T)
    if N > 0 { result.table = make(map[T]struct{},N,allocator) }
    for value in values { insert(&result,value) }
    return result
}

length :: proc "contextless"(value: $T) -> int {
    when (((T == Set(type_of(key_witness(value.table)))) when intrinsics.type_is_map(type_of(value.table)) else false) when intrinsics.type_has_field(T,"table") else false) {
        return count(value)
    } else { return len(value) }
}
