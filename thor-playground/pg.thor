package main
import * "thor:basic"
import "core:math" / round, ceil


Person :: struct {
    name    : string ,
    age     : int    ,
    weight  : f64    ,
    married : bool   ,
}

floor :: fn(x: f64) -> int {
    return int(math.floor(x))
}

main :: proc() {
    
    people :> []Person {
        {"Tom",       22, 180.2, false},
        {"Samantha",  43, 125.3, true },
        {"Alexander", 57, 250.5, true },
    }
    
    people_table :> table(people)
    
    print(people_table)
    
    SIZE :: 1_000
    ARY_SIZE :: int(#run round(SIZE ** (1.0/7.0)))
    ary: [ARY_SIZE]int
    
    print(len(ary))
    
    F :: floor(23.7)
    print(f"I nees at least {F} feet in there")

}