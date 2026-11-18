package main

import "thor:basic"
import ti "thor:intrinsics"

Job :: struct { left, right: int }
calculate :: proc(a, b: $T) -> (sum, product: T) { return a+b, a*b }
Result :: ti.result_type_of(calculate(**Job{}))
Named :: struct { added, multiplied: int }
Action_Job :: struct { value: int, destination: ^int }
store :: proc(value: $T, destination: ^T) { destination^=value }
twice :: proc(value: $T) -> T { return value*2 }
whole :: proc(job: Job) -> int { return job.left+job.right }
increment :: proc(destination: ^int) { destination^+=1 }
constant :: proc() -> int { return 7 }

main :: proc() {
    jobs :> []Job{{2,3}, {4,5}, {6,7}}
    rows := basic.pmap(jobs, calculate, workers=2)
    defer delete(rows)
    for row in rows { basic.print(row.sum, row.product) }

    precomputed := #run basic.pmap([]Job{{2,3}, {4,5}}, calculate, workers=2)
    defer delete(precomputed)
    basic.print("compile-time:", precomputed[0].sum, precomputed[1].product)

    renamed := basic.pmap(jobs, calculate, result_type=Named)
    defer delete(renamed)
    basic.print(renamed[0].added, renamed[0].multiplied)

    sample: Result = {5,6}
    basic.print(sample)
    destinations: [3]int
    actions := []Action_Job{{10,&destinations[0]}, {20,&destinations[1]}, {30,&destinations[2]}}
    basic.pmap(actions, store)
    basic.print(destinations)

    doubled := basic.pmap([]int{2,3,4}, twice)
    defer delete(doubled)
    basic.print("single inputs:", doubled)

    // A known procedure such as `twice` above is specialized automatically.
    // A procedure held in runtime storage keeps dynamic callback dispatch.
    callback: proc(int)->int = twice
    selected := basic.pmap([]int{2,3,4}, callback)
    defer delete(selected)
    basic.print("runtime callback:", selected)

    totals := basic.pmap(jobs, whole)
    defer delete(totals)
    basic.print("whole structs:", totals)

    basic.pmap([]^int{&destinations[0],&destinations[1]}, increment)
    basic.print("pointer inputs:", destinations)

    empty_jobs: [3]struct{}
    constants := basic.pmap(empty_jobs[:], constant)
    defer delete(constants)
    basic.print("zero inputs:", constants)
}
