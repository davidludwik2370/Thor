// Compiler-validated callbacks use independent worker scratch lifetimes. The
// ordinary pmap loops and their ownership contract remain in workers.odin.
package parallel_native

import "base:runtime"
import "base:intrinsics"
import pure "thor:pure"

map_values_checked :: proc(jobs:[]$J,calculate:$F,$R:typeid,requested:int,
    allocator,worker_allocator:runtime.Allocator,loc:runtime.Source_Code_Location,
)->[]R {
    workers:=worker_count(len(jobs),requested,loc)
    result,err:=make([]R,len(jobs),allocator)
    if err!=nil {panic("pmap: could not allocate the output slice",loc)}
    if workers==0 {return result}
    local:pure.Scratch
    parent:=pure.scratch_parent(allocator)
    standalone:=parent==nil
    if standalone {pure.scratch_init(&local);parent=&local}
    Work::struct{jobs:[]J,result:[]R,calculate:F,start,end:int,scratch:^pure.Scratch}
    run::proc(work:^Work){
        context.allocator=pure.scratch_allocator(work.scratch)
        context.temp_allocator=context.allocator
        for i in work.start..<work.end {
            when intrinsics.type_struct_field_count(J)==0 {work.result[i]=compress_values(work.calculate())}
            else {work.result[i]=compress_values(work.calculate(**work.jobs[i]))}
        }
    }
    work,work_error:=make([]Work,workers,allocator)
    if work_error!=nil {
        one:=[1]Work{{jobs,result,calculate,0,len(jobs),pure.scratch_child(rawptr(parent))}}
        run_chunks(one[:],run,allocator,worker_allocator)
    } else {
        start:=0
        base,extra:=len(jobs)/workers,len(jobs)%workers
        for &item,i in work {
            end:=start+base+int(i<extra)
            item={jobs,result,calculate,start,end,pure.scratch_child(rawptr(parent))}
            start=end
        }
        run_chunks(work,run,allocator,worker_allocator)
        delete(work,allocator)
    }
    if standalone {
        // A direct #run has no surrounding fn scratch. Materialize independent
        // storage before releasing its worker arenas.
        copied:=pure.clone(result,allocator)
        delete(result,allocator)
        pure.scratch_destroy(&local)
        return copied
    }
    return result
}

map_actions_checked :: proc(jobs:[]$J,calculate:$F,requested:int,
    allocator,worker_allocator:runtime.Allocator,loc:runtime.Source_Code_Location,
) {
    workers:=worker_count(len(jobs),requested,loc)
    if workers==0 {return}
    local:pure.Scratch
    parent:=pure.scratch_parent(allocator)
    standalone:=parent==nil
    if standalone {pure.scratch_init(&local);parent=&local}
    Work::struct{jobs:[]J,calculate:F,start,end:int,scratch:^pure.Scratch}
    run::proc(work:^Work){
        context.allocator=pure.scratch_allocator(work.scratch)
        context.temp_allocator=context.allocator
        for i in work.start..<work.end {
            when intrinsics.type_struct_field_count(J)==0 {work.calculate()}
            else {work.calculate(**work.jobs[i])}
        }
    }
    work,err:=make([]Work,workers,allocator)
    if err!=nil {
        one:=[1]Work{{jobs,calculate,0,len(jobs),pure.scratch_child(rawptr(parent))}}
        run_chunks(one[:],run,allocator,worker_allocator)
    } else {
        start:=0
        base,extra:=len(jobs)/workers,len(jobs)%workers
        for &item,i in work {
            end:=start+base+int(i<extra)
            item={jobs,calculate,start,end,pure.scratch_child(rawptr(parent))}
            start=end
        }
        run_chunks(work,run,allocator,worker_allocator)
        delete(work,allocator)
    }
    if standalone {pure.scratch_destroy(&local)}
}
