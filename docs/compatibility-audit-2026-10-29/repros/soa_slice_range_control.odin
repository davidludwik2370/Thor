package main
main :: proc() { Point :: struct{x,y:int}; values: #soa[2]Point; s := values[:]; for v in s { _ = v.x } }
