package main
import sets "thor:sets"
main :: proc() { x := [2]int{1,2}; y := [2]int{3,4}; s := soa_zip(x=x[:], y=y[:]); for v in sets.compiler_iterate(s) { _ = v.x } }
