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
