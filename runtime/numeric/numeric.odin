package thor_numeric

import "base:intrinsics"
import "base:runtime"
import "core:math"

// The compiler supplies the shared native operand type before calling this.
// No integer/float or width conversions are performed between typed operands.
power :: proc "contextless"(base, exponent: $T) -> T where intrinsics.type_is_integer(T) || intrinsics.type_is_float(T) {
    when intrinsics.type_is_float(T) {
        when size_of(T) == 2 {
            return T(math.pow(f16(base), f16(exponent)))
        } else when size_of(T) == 4 {
            return T(math.pow(f32(base), f32(exponent)))
        } else {
            return T(math.pow(f64(base), f64(exponent)))
        }
    } else {
        if exponent < 0 {
            runtime.panic_contextless("Integer power requires a nonnegative exponent; use a floating-point base and exponent for negative powers.")
        }
        result := T(1)
        factor, remaining := base, exponent
        for remaining != 0 {
            if remaining & 1 != 0 { result *= factor }
            remaining >>= 1
            if remaining != 0 { factor *= factor }
        }
        return result
    }
}
