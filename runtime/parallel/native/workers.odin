// Native loops avoid adding Thor lowering machinery to each mapped element.
package parallel_native

import "base:runtime"
import "core:thread"
import "core:os"

@(private="file", thread_local)
depth: int

@(private)
worker_count :: proc(count, requested: int, loc: runtime.Source_Code_Location) -> int {
    if requested<0 {panic("pmap: workers must be nonnegative",loc)}
    if count==0 {return 0}
    when !thread.IS_SUPPORTED {return 1}
    if depth>0 {return 1}
    workers:=requested
    if workers==0 {workers=os.get_processor_core_count()}
    return min(count,max(1,workers))
}

@(private)
run_chunks :: proc(work: []$W, run: proc(^W), allocator, worker_allocator: runtime.Allocator) {
    if len(work)==0 {return}
    saved:=context.allocator
    context.allocator=worker_allocator
    defer context.allocator=saved
    depth+=1
    defer depth-=1
    when !thread.IS_SUPPORTED {
        for &item in work {run(&item)}
        return
    } else {
        Task :: struct {
            work: ^W,
            run: proc(^W),
            caller_context: runtime.Context,
            allocator: runtime.Allocator,
            thread: ^thread.Thread,
        }
        entry :: proc(t: ^thread.Thread) {
            task:=(^Task)(t.data)
            inherited:=task.caller_context
            inherited.allocator=task.allocator
            // Keep the fresh temporary allocator owned by this native thread.
            inherited.temp_allocator=context.temp_allocator
            context=inherited
            depth+=1
            defer depth-=1
            task.run(task.work)
        }
        if len(work)==1 {run(&work[0]);return}
        tasks,err:=make([]Task,len(work)-1,allocator)
        if err!=nil {
            // Scheduling storage is optional; mapping still works serially.
            for &item in work {run(&item)}
            return
        }
        defer delete(tasks,allocator)
        for &task,i in tasks {
            task={work=&work[i+1],run=run,caller_context=context,allocator=worker_allocator}
            // Thread management allocations belong to the caller's allocator.
            context.allocator=saved
            task.thread=thread.create(entry)
            context.allocator=worker_allocator
            if task.thread!=nil {task.thread.data=&task;thread.start(task.thread)}
        }
        run(&work[0])
        for &task in tasks {
            if task.thread==nil {run(task.work)}
        }
        for &task in tasks {
            if task.thread!=nil {
                thread.join(task.thread)
                context.allocator=saved
                thread.destroy(task.thread)
                context.allocator=worker_allocator
            }
        }
    }
}

map_values :: proc(jobs: []$J, calculate: $F, $R: typeid, requested: int,
    allocator, worker_allocator: runtime.Allocator, loc: runtime.Source_Code_Location, $spread:bool,
) -> []R {
    return _map_values(jobs,calculate,R,requested,allocator,worker_allocator,loc,spread,false,dynamic_callback)
}

map_values_static :: proc(jobs: []$J, $calculate: $F, $R: typeid, requested: int,
    allocator, worker_allocator: runtime.Allocator, loc: runtime.Source_Code_Location, $spread:bool,
) -> []R {
    return _map_values(jobs,calculate,R,requested,allocator,worker_allocator,loc,spread,true,calculate)
}

@(private)
_map_values :: proc(jobs: []$J, calculate: $F, $R: typeid, requested: int,
    allocator, worker_allocator: runtime.Allocator, loc: runtime.Source_Code_Location, $spread:bool,$is_static:bool,$known:$C,
) -> []R {
    workers:=worker_count(len(jobs),requested,loc)
    result,err:=make([]R,len(jobs),allocator)
    if err!=nil {panic("pmap: could not allocate the output slice",loc)}
    if workers==0 {return result}
    Work :: struct {jobs: []J, result: []R, calculate: F, start,end: int}
    run :: proc(work: ^Work) {
        for i in work.start..<work.end {
            work.result[i]=call_value(work.calculate,work.jobs[i],R,spread,is_static,known)
        }
    }
    work,work_error:=make([]Work,workers,allocator)
    if work_error!=nil {
        saved:=context.allocator;context.allocator=worker_allocator
        depth+=1
        for job,i in jobs {
            result[i]=call_value(calculate,job,R,spread,is_static,known)
        }
        depth-=1;context.allocator=saved
        return result
    }
    defer delete(work,allocator)
    start:=0
    base,extra:=len(jobs)/workers,len(jobs)%workers
    for &item,i in work {
        end:=start+base+int(i<extra)
        item={jobs,result,calculate,start,end}
        start=end
    }
    run_chunks(work,run,allocator,worker_allocator)
    return result
}

map_actions :: proc(jobs: []$J, calculate: $F, requested: int,
    allocator, worker_allocator: runtime.Allocator, loc: runtime.Source_Code_Location, $spread:bool,
) {
    _map_actions(jobs,calculate,requested,allocator,worker_allocator,loc,spread,false,dynamic_callback)
}

map_actions_static :: proc(jobs: []$J, $calculate: $F, requested: int,
    allocator, worker_allocator: runtime.Allocator, loc: runtime.Source_Code_Location, $spread:bool,
) {
    _map_actions(jobs,calculate,requested,allocator,worker_allocator,loc,spread,true,calculate)
}

@(private)
_map_actions :: proc(jobs: []$J, calculate: $F, requested: int,
    allocator, worker_allocator: runtime.Allocator, loc: runtime.Source_Code_Location, $spread:bool,$is_static:bool,$known:$C,
) {
    workers:=worker_count(len(jobs),requested,loc)
    if workers==0 {return}
    Work :: struct {jobs: []J, calculate: F, start,end: int}
    run :: proc(work: ^Work) {for i in work.start..<work.end {
        call_action(work.calculate,work.jobs[i],spread,is_static,known)
    }}
    work,err:=make([]Work,workers,allocator)
    if err!=nil {
        saved:=context.allocator;context.allocator=worker_allocator
        depth+=1
        for job in jobs {
            call_action(calculate,job,spread,is_static,known)
        }
        depth-=1;context.allocator=saved
        return
    }
    defer delete(work,allocator)
    start:=0
    base,extra:=len(jobs)/workers,len(jobs)%workers
    for &item,i in work {
        end:=start+base+int(i<extra)
        item={jobs,calculate,start,end}
        start=end
    }
    run_chunks(work,run,allocator,worker_allocator)
}
