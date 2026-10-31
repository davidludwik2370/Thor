// Read-only compiler data. Headers are allocation-free views, never owners.
package thor_big

import "base:runtime"
import backend "core:math/big"

compiler_digit :: backend.DIGIT
compiler_digit_bits :: backend._DIGIT_BITS
#assert(backend._MIN_DIGIT_COUNT<=4)

compiler_static_integer :: proc "contextless"(digits: []compiler_digit, negative: bool) -> Integer {
    if len(digits)==0 {return {}}
    header:=runtime.Raw_Dynamic_Array{data=rawptr(raw_data(digits)),len=len(digits),cap=len(digits)}
    used:=len(digits)
    for used>0 && digits[used-1]==0 {used-=1}
    return Integer{_value=backend.Int{digit=transmute([dynamic]backend.DIGIT)header,
        used=used,sign=.Negative if negative else .Zero_or_Positive}}
}
compiler_static_rational :: proc "contextless"(n,d:Integer)->Rational {
    if is_zero(n){return {}}
    return Rational{_numerator=n,_denominator=d}
}
