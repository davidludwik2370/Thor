package main
Set :: struct($T:typeid) {table: map[T]struct{}}
entries :: proc(value: Set($T)) -> map[T]struct{} {return value.table}
identity :: proc(value:$T)->T {return value}
iterate :: proc{entries,identity}
main :: proc() { x := [2]int{1,2}; y := [2]int{3,4}; s := soa_zip(x=x[:], y=y[:]); for v in iterate(s) { _ = v.x } }
