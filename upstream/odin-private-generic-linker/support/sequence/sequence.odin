package thor_sequence

import "base:intrinsics"
import "base:runtime"

validate_loop_index :: proc "contextless"($A:typeid,$I:typeid) {
    #assert(intrinsics.type_is_integer(I), "Typed loop indices require an integer type.")
    when intrinsics.type_is_pointer(A) {validate_loop_index(intrinsics.type_elem_type(A),I)}
    else {
        #assert((intrinsics.type_is_array(A) && !intrinsics.type_is_enumerated_array(A)) || intrinsics.type_is_slice(A) ||
            intrinsics.type_is_dynamic_array(A) || intrinsics.type_is_fixed_capacity_dynamic_array(A),
            "Typed index bindings are supported only for arrays and slices.")
        when intrinsics.type_is_integer(I) && intrinsics.type_is_array(A) && !intrinsics.type_is_enumerated_array(A) {
            when len(A)>0 {
                #assert(u128(len(A)-1)<=u128(max(I)), "The array's indices do not fit in the declared loop index type.")
            }
        }
    }
}
checked_loop_index :: proc "contextless"(index:int,$I:typeid,location:=#caller_location)->I {
    when intrinsics.type_is_integer(I) {
        if index<0 || u128(index)>u128(max(I)) {
            runtime.panic_contextless("Typed loop index is out of range for its declared type.",location)
        }
        return I(index)
    }else{return {}}
}

// Widen signed arithmetic before adding the length. Unsigned values retain
// their original type and never take a negative path or undergo narrowing.
normalize_signed :: proc "contextless"(length: int, index: $I, location:=#caller_location, checked:=true) -> i128 where intrinsics.type_is_integer(I) && !intrinsics.type_is_unsigned(I) {
    value := i128(index)
    result := value < 0 ? i128(length)+value : value
    // Odin's machine index conversion can truncate very wide integers. Check
    // the wide result first; the final access distinguishes index < len from
    // the inclusive upper bound allowed by slicing.
    if checked { assert_contextless(result >= 0 && result <= i128(length), "Bounds check failed: normalized sequence bound is out of range",location) }
    return result
}
normalize_unsigned :: proc "contextless"(length: int, index: $I, location:=#caller_location, checked:=true) -> I where intrinsics.type_is_integer(I) && intrinsics.type_is_unsigned(I) {
    if checked { assert_contextless(u128(index)<=u128(length), "Bounds check failed: sequence bound is out of range",location) }
    return index
}
normalize_enum :: proc "contextless"(length: int, index: $I, location:=#caller_location, checked:=true) -> I where intrinsics.type_is_enum(I) {
    return index
}
normalize :: proc{normalize_signed,normalize_unsigned,normalize_enum}

capture_array_reference :: proc "contextless"(value: ^$T) -> []intrinsics.type_elem_type(T) where intrinsics.type_is_array(T) && !intrinsics.type_is_enumerated_array(T) || intrinsics.type_is_fixed_capacity_dynamic_array(T) { return value^[:] }
capture_header_reference :: proc "contextless"(value: ^$T) -> T where intrinsics.type_is_slice(T) || intrinsics.type_is_dynamic_array(T) || intrinsics.type_is_pointer(T) || intrinsics.type_is_multi_pointer(T) || intrinsics.type_is_string(T) { return value^ }
capture_inline_reference :: proc "contextless"(value: ^$T) -> ^T where !(intrinsics.type_is_array(T) && !intrinsics.type_is_enumerated_array(T) || intrinsics.type_is_fixed_capacity_dynamic_array(T) || intrinsics.type_is_slice(T) || intrinsics.type_is_dynamic_array(T) || intrinsics.type_is_pointer(T) || intrinsics.type_is_multi_pointer(T) || intrinsics.type_is_string(T)) { return value }
capture_reference :: proc{capture_array_reference,capture_header_reference,capture_inline_reference}
capture_field_pointer :: proc "contextless"(value: ^$T) -> T
where intrinsics.type_is_pointer(T) || intrinsics.type_is_multi_pointer(T) {
    return value^
}
capture_field_inline :: proc "contextless"(value: ^$T) -> ^T
where !intrinsics.type_is_pointer(T) && !intrinsics.type_is_multi_pointer(T) {
    return value
}
capture_field_reference :: proc{capture_field_pointer, capture_field_inline}
validate_assignment_field :: proc "contextless"($T: typeid, $Writable: bool) {
    #assert(Writable || intrinsics.type_is_pointer(T) || intrinsics.type_is_multi_pointer(T),
        "Cannot assign to a field without writable storage.")
}

validate_assignment_base :: proc "contextless"($T:typeid,$Writable:bool) {
    when intrinsics.type_is_array(T) || intrinsics.type_is_fixed_capacity_dynamic_array(T) || intrinsics.type_is_map(T) {
        #assert(Writable, "Cannot assign through a collection value without writable storage.")
    }
}

assignment_bound_signed :: proc "contextless"(value:$T,index:$I,location:=#caller_location,checked:=true) -> i128
where (intrinsics.type_is_array(T) && !intrinsics.type_is_enumerated_array(T) || intrinsics.type_is_slice(T) || intrinsics.type_is_dynamic_array(T) || intrinsics.type_is_fixed_capacity_dynamic_array(T)) && intrinsics.type_is_integer(I) && !intrinsics.type_is_unsigned(I) {
    return normalize_signed(len(value),index,location,checked)
}
assignment_bound_unsigned :: proc "contextless"(value:$T,index:$I,location:=#caller_location,checked:=true) -> I
where (intrinsics.type_is_array(T) && !intrinsics.type_is_enumerated_array(T) || intrinsics.type_is_slice(T) || intrinsics.type_is_dynamic_array(T) || intrinsics.type_is_fixed_capacity_dynamic_array(T)) && intrinsics.type_is_integer(I) && intrinsics.type_is_unsigned(I) {
    return normalize_unsigned(len(value),index,location,checked)
}
assignment_bound_native :: proc "contextless"(value:$T,index:$I,location:=#caller_location,checked:=true) -> I
where !(intrinsics.type_is_array(T) && !intrinsics.type_is_enumerated_array(T) || intrinsics.type_is_slice(T) || intrinsics.type_is_dynamic_array(T) || intrinsics.type_is_fixed_capacity_dynamic_array(T)) || !intrinsics.type_is_integer(I) {
    return index
}
assignment_bound :: proc{assignment_bound_signed,assignment_bound_unsigned,assignment_bound_native}

array_element :: proc "contextless"(value: $A/[$N]$E) -> E { return {} }
slice_element :: proc "contextless"(value: $A/[]$E) -> E { return {} }
dynamic_element :: proc "contextless"(value: $A/[dynamic]$E) -> E { return {} }
fixed_element :: proc "contextless"(value: $A/[dynamic; $N]$E) -> E { return {} }
map_element :: proc "contextless"(value: $A/map[$K]$V) -> V { return {} }
map_key_value :: proc "contextless"(value: $A/map[$K]$V) -> K { return {} }
map_key_pointer :: proc "contextless"(value: ^$A/map[$K]$V) -> K { return {} }
map_key :: proc{map_key_value,map_key_pointer}
string_element :: proc "contextless"(value: $S/string) -> u8 { return 0 }
multi_element :: proc "contextless"(value: [^]$E) -> E { return {} }
pointer_array_element :: proc "contextless"(value: ^[$N]$E) -> E { return {} }
pointer_slice_element :: proc "contextless"(value: ^$A/[]$E) -> E { return {} }
pointer_dynamic_element :: proc "contextless"(value: ^$A/[dynamic]$E) -> E { return {} }
pointer_fixed_element :: proc "contextless"(value: ^$A/[dynamic; $N]$E) -> E { return {} }
pointer_map_element :: proc "contextless"(value: ^$A/map[$K]$V) -> V { return {} }
pointer_string_element :: proc "contextless"(value: ^$S/string) -> u8 { return 0 }
soa_slice_element :: proc "contextless"(value: $A/#soa[]$E) -> E { return {} }
soa_array_element :: proc "contextless"(value: $A/#soa[$N]$E) -> E { return {} }
soa_dynamic_element :: proc "contextless"(value: $A/#soa[dynamic]$E) -> E { return {} }
enumerated_element :: proc "contextless"(value: $A) -> intrinsics.type_elem_type(A) where intrinsics.type_is_enumerated_array(A) { return {} }
element :: proc{array_element,slice_element,dynamic_element,fixed_element,map_element,string_element,multi_element,pointer_array_element,pointer_slice_element,pointer_dynamic_element,pointer_fixed_element,pointer_map_element,pointer_string_element,soa_slice_element,soa_array_element,soa_dynamic_element,enumerated_element}

// Reductions use native scalar operations, including their floating-point rules.
// A supplied initial value is an actual candidate, not an identity/sentinel.
// Explicit string comparisons also register the runtime comparison dependency:
// this Odin release misses it when min/max are instantiated in imported generics.
min_value :: proc "contextless" (a, b: $T) -> T {
    when intrinsics.type_is_string(T) { return a < b ? a : b }
    else { return min(a, b) }
}
max_value :: proc "contextless" (a, b: $T) -> T {
    when intrinsics.type_is_string(T) { return a > b ? a : b }
    else { return max(a, b) }
}
min_slice_seeded :: proc "contextless" (values: $S/[]$T, initial: T, has_initial: bool) -> (value: T, ok: bool)
where intrinsics.type_is_ordered(T) #optional_ok {
    value, ok = initial, has_initial
    for item in values {
        if ok { value = min_value(value, item) } else { value, ok = item, true }
    }
    return
}
max_slice_seeded :: proc "contextless" (values: $S/[]$T, initial: T, has_initial: bool) -> (value: T, ok: bool)
where intrinsics.type_is_ordered(T) #optional_ok {
    value, ok = initial, has_initial
    for item in values {
        if ok { value = max_value(value, item) } else { value, ok = item, true }
    }
    return
}
min_array_seeded :: proc "contextless" (values: $A/[$N]$T, initial: T, has_initial: bool) -> T
where intrinsics.type_is_ordered(T) {
    #assert(N > 0, "An empty fixed array cannot be expanded into min/max.")
    value, ok := initial, has_initial
    for item in values {
        if ok { value = min_value(value, item) } else { value, ok = item, true }
    }
    return value
}
max_array_seeded :: proc "contextless" (values: $A/[$N]$T, initial: T, has_initial: bool) -> T
where intrinsics.type_is_ordered(T) {
    #assert(N > 0, "An empty fixed array cannot be expanded into min/max.")
    value, ok := initial, has_initial
    for item in values {
        if ok { value = max_value(value, item) } else { value, ok = item, true }
    }
    return value
}

min_slice :: proc "contextless" (values: $S/[]$T) -> (value: T, ok: bool) #optional_ok {
    return min_slice_seeded(values, T{}, false)
}
max_slice :: proc "contextless" (values: $S/[]$T) -> (value: T, ok: bool) #optional_ok {
    return max_slice_seeded(values, T{}, false)
}
min_array :: proc "contextless" (values: $A/[$N]$T) -> T { return min_array_seeded(values, T{}, false) }
max_array :: proc "contextless" (values: $A/[$N]$T) -> T { return max_array_seeded(values, T{}, false) }
