// Compiler support for shallow, lexical cleanup. No registration allocation.
package thor_scoped

import "base:intrinsics"
import "core:mem"
import "thor:sets"
import "thor:big"

// Keep evaluation at the original argument position. The generated owner
// stores the allocator actually supplied to its constructor, exactly once.
capture_allocator :: #force_inline proc "contextless"(destination: ^mem.Allocator, value: mem.Allocator) -> mem.Allocator {
    destination^ = value
    return value
}

cleanup :: #force_inline proc(value: ^$T, allocator: mem.Allocator) {
    when intrinsics.type_is_pointer(T) {
        // Include the allocation size for allocators which require it on free.
        mem.free_with_size(rawptr(value^), size_of(value^^), allocator)
    } else when intrinsics.type_is_slice(T) || intrinsics.type_is_string(T) ||
                intrinsics.type_is_string16(T) || intrinsics.type_is_cstring(T) ||
                intrinsics.type_is_cstring16(T) {
        delete(value^, allocator)
    } else when intrinsics.type_is_dynamic_array(T) || intrinsics.type_is_map(T) {
        delete(value^)
    } else when T == big.Integer || T == big.Rational {
        big.destroy(value)
    } else when intrinsics.type_has_field(T, "table") {
        when intrinsics.type_is_map(type_of(value.table)) {
            when T == sets.Set(type_of(sets.key_witness(value.table))) {
                sets.destroy(value^)
            } else { #panic("scoped storage '~' requires a supported owning pointer or collection") }
        } else { #panic("scoped storage '~' requires a supported owning pointer or collection") }
    } else { #panic("scoped storage '~' requires a supported owning pointer or collection") }
}
