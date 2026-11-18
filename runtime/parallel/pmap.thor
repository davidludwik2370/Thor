// Mapping policy lives here; result_type_of supplies only a dependent type.
package parallel

import ti "thor:intrinsics"
import engine "thor:parallel/native"
import "base:runtime"
import "base:intrinsics"

// One fixed parameter receives the whole element; other callbacks receive
// struct fields. The compiler supplies the static input mode. Results retain
// input order. The outer slice belongs to allocator; elements are shallow.
pmap :: proc { _pmap_values, _pmap_actions }

// Compiler entry after callback purity and input-ownership proofs. Its native
// signature matches pmap; callers continue to use the public pmap procedure.
pmap_checked :: proc { _pmap_values_checked, _pmap_actions_checked }

// The compiler supplies __thor_pmap_spread after resolving callback overloads.
// It is a required constant parameter so native worker loops use `when` and
// require neither runtime reflection nor an extra callback adapter per element.

@(private="file")
_pmap_values_checked :: proc(jobs:[]$Job,calculate:$F,
    $result_type:typeid=ti.result_type_of(calculate(Job{})),
    workers:=0,allocator:=context.allocator,
    worker_allocator:runtime.Allocator={runtime.heap_allocator_proc,nil},loc:=#caller_location,$__thor_pmap_spread:bool,
)->[]result_type {
    return engine.map_values_checked(jobs,calculate,result_type,workers,allocator,worker_allocator,loc,__thor_pmap_spread)
}

@(private="file")
_pmap_actions_checked :: proc(jobs:[]$Job,calculate:$F,
    workers:=0,allocator:=context.allocator,
    worker_allocator:runtime.Allocator={runtime.heap_allocator_proc,nil},loc:=#caller_location,$__thor_pmap_spread:bool,
) where intrinsics.type_proc_return_count(F)==0 {
    engine.map_actions_checked(jobs,calculate,workers,allocator,worker_allocator,loc,__thor_pmap_spread)
}

@(private="file")
_pmap_values :: proc(jobs: []$Job, calculate: $F,
    $result_type: typeid = ti.result_type_of(calculate(Job{})),
    workers := 0, allocator := context.allocator,
    worker_allocator: runtime.Allocator = {runtime.heap_allocator_proc, nil}, loc := #caller_location, $__thor_pmap_spread: bool,
) -> []result_type {
    return engine.map_values(jobs, calculate, result_type, workers, allocator, worker_allocator, loc, __thor_pmap_spread)
}

@(private="file")
_pmap_actions :: proc(jobs: []$Job, calculate: $F,
    workers := 0, allocator := context.allocator,
    worker_allocator: runtime.Allocator = {runtime.heap_allocator_proc, nil}, loc := #caller_location, $__thor_pmap_spread: bool,
) where intrinsics.type_proc_return_count(F)==0 {
    engine.map_actions(jobs, calculate, workers, allocator, worker_allocator, loc, __thor_pmap_spread)
}
