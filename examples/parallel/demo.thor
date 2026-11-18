package main

import "thor:basic"
import ti "thor:intrinsics"

Job :: struct { left, right: int }
calculate :: proc(a, b: $T) -> (sum, product: T) { return a+b, a*b }
Result :: ti.result_type_of(calculate(**Job{}))
Named :: struct { added, multiplied: int }
Action_Job :: struct { value: int, destination: ^int }
store :: proc(value: $T, destination: ^T) { destination^=value }

main :: proc() {
    jobs :> []Job{{2,3}, {4,5}, {6,7}}
    rows := basic.pmap(jobs, calculate, workers=2)
    defer delete(rows)
    for row in rows { basic.print(row.sum, row.product) }

    renamed := basic.pmap(jobs, calculate, result_type=Named)
    defer delete(renamed)
    basic.print(renamed[0].added, renamed[0].multiplied)

    sample: Result = {5,6}
    basic.print(sample)
    destinations: [3]int
    actions := []Action_Job{{10,&destinations[0]}, {20,&destinations[1]}, {30,&destinations[2]}}
    basic.pmap(actions, store)
    basic.print(destinations)
}
