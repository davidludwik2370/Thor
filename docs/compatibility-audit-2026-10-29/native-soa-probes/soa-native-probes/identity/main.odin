package main
identity :: proc(value: $T) -> T { return value }
main :: proc() { x := [2]int{1,2}; y := [2]int{3,4}; s := soa_zip(x=x[:], y=y[:]); for v in identity(s) { _ = v.x } }
