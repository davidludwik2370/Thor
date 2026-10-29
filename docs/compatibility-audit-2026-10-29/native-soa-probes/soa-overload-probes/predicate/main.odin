package main
import "base:intrinsics"
Set :: struct($T:typeid) {table: map[T]struct{}}
key_witness :: proc(value: map[$T]struct{})->T { return {} }
iterate :: proc(value:$T)->T where !(((T == Set(type_of(key_witness(value.table)))) when intrinsics.type_is_map(type_of(value.table)) else false) when intrinsics.type_has_field(T,"table") else false) {return value}
main :: proc() { x := [2]int{1,2}; y := [2]int{3,4}; s := soa_zip(x=x[:], y=y[:]); for v in iterate(s) { _ = v.x } }
