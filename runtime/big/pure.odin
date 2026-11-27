// Keep the base pure runtime independent of the bigint backend's SDK imports.
package thor_big
import "base:runtime"
import "thor:pure"

@(private="file",init)
register_pure_numbers :: proc "contextless"() {
    pure.compiler_register_logical(0,{typeid_of(Integer),clone_integer_at,destroy_integer_at,promote_integer_at})
    pure.compiler_register_logical(1,{typeid_of(Rational),clone_rational_at,destroy_rational_at,promote_rational_at})
}
@(private="file") clone_integer_at::proc(dst,src:rawptr,allocator:runtime.Allocator){(cast(^Integer)dst)^=clone((cast(^Integer)src)^,allocator)}
@(private="file") clone_rational_at::proc(dst,src:rawptr,allocator:runtime.Allocator){(cast(^Rational)dst)^=clone((cast(^Rational)src)^,allocator)}
@(private="file") destroy_integer_at::proc(src:rawptr){value:=(cast(^Integer)src)^;destroy(&value)}
@(private="file") destroy_rational_at::proc(src:rawptr){value:=(cast(^Rational)src)^;destroy(&value)}

@(private="file")
promote_integer :: proc(value:Integer, allocator:runtime.Allocator, scratch:^pure.Scratch)->Integer {
    digits:=value._value.digit
    if digits.allocator==context.allocator &&
       pure.take_result_storage(scratch,raw_data(digits),cap(digits)*size_of(type_of(digits[0])),allocator) {
        result:=value
        result._value.digit.allocator=allocator
        return result
    }
    return clone(value,allocator)
}
@(private="file")
promote_integer_at :: proc(dst,src:rawptr, allocator:runtime.Allocator, scratch:^pure.Scratch) {
    (cast(^Integer)dst)^=promote_integer((cast(^Integer)src)^,allocator,scratch)
}
@(private="file")
promote_rational_at :: proc(dst,src:rawptr, allocator:runtime.Allocator, scratch:^pure.Scratch) {
    value:=(cast(^Rational)src)^
    (cast(^Rational)dst)^={promote_integer(value._numerator,allocator,scratch),promote_integer(value._denominator,allocator,scratch)}
}
