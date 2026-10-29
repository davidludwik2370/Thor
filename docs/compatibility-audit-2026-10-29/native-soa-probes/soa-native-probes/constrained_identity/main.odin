package main
import "base:intrinsics"
identity :: proc(value:$T)->T where !intrinsics.type_has_field(T,"table") { return value }
main :: proc() { x := [2]int{1,2}; y := [2]int{3,4}; s := soa_zip(x=x[:], y=y[:]); for v in identity(s) { _ = v.x } }
