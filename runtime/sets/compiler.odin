// Compiler dispatch for generic Thor code; ordinary Odin operands retain native operations.
package thor_sets

import "base:intrinsics"
import "base:runtime"

compiler_union_value :: proc "contextless"(value: $T, other: T) -> T where !(((T == Set(type_of(key_witness(value.table)))) when intrinsics.type_is_map(type_of(value.table)) else false) when intrinsics.type_has_field(T,"table") else false) {
    return value | other
}
compiler_union :: proc{union_of,compiler_union_value}

compiler_intersection_value :: proc "contextless"(value: $T, other: T) -> T where !(((T == Set(type_of(key_witness(value.table)))) when intrinsics.type_is_map(type_of(value.table)) else false) when intrinsics.type_has_field(T,"table") else false) {
    return value & other
}
compiler_intersection :: proc{intersection,compiler_intersection_value}

compiler_difference_native :: proc "contextless"(value: $T, other: T) -> T where !(((T == Set(type_of(key_witness(value.table)))) when intrinsics.type_is_map(type_of(value.table)) else false) when intrinsics.type_has_field(T,"table") else false) {
    return value - other
}
compiler_difference :: proc{difference,compiler_difference_native}

compiler_equal_native :: proc "contextless"(value: $T, other: T) -> bool where !(((T == Set(type_of(key_witness(value.table)))) when intrinsics.type_is_map(type_of(value.table)) else false) when intrinsics.type_has_field(T,"table") else false) {
    return value == other
}
compiler_equal :: proc{equal,compiler_equal_native}

compiler_symmetric_difference :: proc(value: $T, other: T) -> T {
    when (((T == Set(type_of(key_witness(value.table)))) when intrinsics.type_is_map(type_of(value.table)) else false) when intrinsics.type_has_field(T,"table") else false) { return symmetric_difference(value,other) }
    else { #panic("Symmetric difference requires Set operands"); return {} }
}

compiler_add_set :: proc(target: ^Set($E), other: E) { insert(target,other) }
compiler_add_native :: proc "contextless"(target: ^$T, other: T) where !(((T == Set(type_of(key_witness(target^.table)))) when intrinsics.type_is_map(type_of(target^.table)) else false) when intrinsics.type_has_field(T,"table") else false) {
    target^ += other
}
compiler_add_assign :: proc{compiler_add_set,compiler_add_native}

compiler_union_set :: proc(target: ^Set($E), other: Set(E)) { union_assign(target,other) }
compiler_union_native :: proc "contextless"(target: ^$T, other: T) where !(((T == Set(type_of(key_witness(target^.table)))) when intrinsics.type_is_map(type_of(target^.table)) else false) when intrinsics.type_has_field(T,"table") else false) {
    target^ |= other
}
compiler_union_assign :: proc{compiler_union_set,compiler_union_native}

compiler_intersection_set :: proc(target: ^Set($E), other: Set(E)) { intersection_assign(target,other) }
compiler_intersection_native :: proc "contextless"(target: ^$T, other: T) where !(((T == Set(type_of(key_witness(target^.table)))) when intrinsics.type_is_map(type_of(target^.table)) else false) when intrinsics.type_has_field(T,"table") else false) {
    target^ &= other
}
compiler_intersection_assign :: proc{compiler_intersection_set,compiler_intersection_native}

compiler_xor_set :: proc(target: ^Set($E), other: Set(E)) { symmetric_difference_assign(target,other) }
compiler_xor_native :: proc "contextless"(target: ^$T, other: T) where !(((T == Set(type_of(key_witness(target^.table)))) when intrinsics.type_is_map(type_of(target^.table)) else false) when intrinsics.type_has_field(T,"table") else false) {
    #panic("Symmetric difference assignment requires Set operands")
}
compiler_xor_assign :: proc{compiler_xor_set,compiler_xor_native}

compiler_sub_one :: proc(target: ^Set($E),other:E){remove(target,other)}
compiler_sub_all :: proc(target: ^Set($E),other:Set(E)){difference_assign(target,other)}
compiler_sub_native :: proc "contextless"(target:^$T,other:T) where !(((T == Set(type_of(key_witness(target^.table)))) when intrinsics.type_is_map(type_of(target^.table)) else false) when intrinsics.type_has_field(T,"table") else false) {target^-=other}
compiler_sub_assign :: proc{compiler_sub_one,compiler_sub_all,compiler_sub_native}

compiler_iter_native :: proc "contextless"(value:$T)->T where !(((T == Set(type_of(key_witness(value.table)))) when intrinsics.type_is_map(type_of(value.table)) else false) when intrinsics.type_has_field(T,"table") else false) {return value}
compiler_iterate :: proc{entries,compiler_iter_native}

compiler_clear :: proc "contextless"(value:^$T){
    when (((T == Set(type_of(key_witness(value^.table)))) when intrinsics.type_is_map(type_of(value^.table)) else false) when intrinsics.type_has_field(T,"table") else false) {clear_set(value)}
    else {clear(value)}
}

compiler_clear_value :: proc "contextless"(value:^$T){
    when (((T == Set(type_of(key_witness(value^.table)))) when intrinsics.type_is_map(type_of(value^.table)) else false) when intrinsics.type_has_field(T,"table") else false) {clear_set(value)}
    else when intrinsics.type_is_pointer(T) {compiler_clear(value^)}
    else {clear(value^)}
}

compiler_delete :: proc(value:$T)->runtime.Allocator_Error {
    when (((T == Set(type_of(key_witness(value.table)))) when intrinsics.type_is_map(type_of(value.table)) else false) when intrinsics.type_has_field(T,"table") else false) {return delete(value.table)}
    else {return delete(value)}
}
