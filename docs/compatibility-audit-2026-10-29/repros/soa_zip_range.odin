package main
main :: proc() { x := [2]int{1, 2}; y := [2]int{3, 4}; s := soa_zip(x=x[:], y=y[:]); for v in s { _ = v.x } }
