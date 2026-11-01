package thor_format

import "base:intrinsics"
import "thor:big"

// Normalize before formatting so wide/unsigned indices cannot silently wrap.
// The caller location belongs to the source width expression, not the value.
normalize_width :: proc(value:$T,loc:=#caller_location)->int {
    #assert(intrinsics.type_is_integer(T),"Interpolation width must have an integer type.")
    when intrinsics.type_is_integer(T) {
        if value<0 {panic("Interpolation width must be nonnegative.",loc)}
        if u128(value)>u128(max(int)) {panic("Interpolation width exceeds the native int range.",loc)}
        return int(value)
    } else {return 0}
}

// A later width call may overwrite the original Integer/Rational and release
// its old digits automatically. Preserve borrowed scalar values before that
// call; fresh owned field results already have independent storage.
compiler_capture_width_value :: proc(value:$T,owned:bool)->T {
    when T==big.Integer || T==big.Rational {
        if !owned {return big.clone(value)}
    }
    return value
}
compiler_release_width_value :: proc(value:$T,owned:bool) {
    when T==big.Integer || T==big.Rational {
        if !owned {copy:=value;big.destroy(&copy)}
    }
}
