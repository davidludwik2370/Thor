package thor_sequence

import "base:intrinsics"

// Widen signed arithmetic before adding the length. Unsigned values retain
// their original type and never take a negative path or undergo narrowing.
normalize_signed :: proc "contextless"(length: int, index: $I) -> i128 where intrinsics.type_is_integer(I) && !intrinsics.type_is_unsigned(I) {
    value := i128(index)
    result := value < 0 ? i128(length)+value : value
    // Odin's machine index conversion can truncate very wide integers. Check
    // the wide result first; the final access distinguishes index < len from
    // the inclusive upper bound allowed by slicing.
    assert_contextless(result >= 0 && result <= i128(length), "Bounds check failed: normalized sequence bound is out of range")
    return result
}
normalize_unsigned :: proc "contextless"(length: int, index: $I) -> I where intrinsics.type_is_integer(I) && intrinsics.type_is_unsigned(I) {
    assert_contextless(u128(index)<=u128(length), "Bounds check failed: sequence bound is out of range")
    return index
}
normalize :: proc{normalize_signed,normalize_unsigned}

array_element :: proc "contextless"(value: $A/[$N]$E) -> E { return {} }
slice_element :: proc "contextless"(value: $A/[]$E) -> E { return {} }
dynamic_element :: proc "contextless"(value: $A/[dynamic]$E) -> E { return {} }
fixed_element :: proc "contextless"(value: $A/[dynamic; $N]$E) -> E { return {} }
map_element :: proc "contextless"(value: $A/map[$K]$V) -> V { return {} }
string_element :: proc "contextless"(value: $S/string) -> u8 { return 0 }
multi_element :: proc "contextless"(value: [^]$E) -> E { return {} }
pointer_array_element :: proc "contextless"(value: ^[$N]$E) -> E { return {} }
element :: proc{array_element,slice_element,dynamic_element,fixed_element,map_element,string_element,multi_element,pointer_array_element}

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
