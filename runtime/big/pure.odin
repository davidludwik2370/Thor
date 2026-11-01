// Keep the base pure runtime independent of the bigint backend's SDK imports.
package thor_big
import "base:runtime"
import "thor:pure"

@(private="file",init)
register_pure_numbers :: proc "contextless"() {
    pure.compiler_register_logical(0,{typeid_of(Integer),clone_integer_at,destroy_integer_at})
    pure.compiler_register_logical(1,{typeid_of(Rational),clone_rational_at,destroy_rational_at})
}
@(private="file") clone_integer_at::proc(dst,src:rawptr,allocator:runtime.Allocator){(cast(^Integer)dst)^=clone((cast(^Integer)src)^,allocator)}
@(private="file") clone_rational_at::proc(dst,src:rawptr,allocator:runtime.Allocator){(cast(^Rational)dst)^=clone((cast(^Rational)src)^,allocator)}
@(private="file") destroy_integer_at::proc(src:rawptr){value:=(cast(^Integer)src)^;destroy(&value)}
@(private="file") destroy_rational_at::proc(src:rawptr){value:=(cast(^Rational)src)^;destroy(&value)}
