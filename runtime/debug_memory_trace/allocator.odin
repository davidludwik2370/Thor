package debug_memory_trace

import "base:runtime"
import libc "core:c/libc"
import trace "core:debug/trace"
import "core:fmt"
import "core:sync"
import memory "../debug_memory"

@(private)
state: struct {
    tracker: trace.Tracking_Allocator,
    backing: runtime.Allocator,
    mapper: memory.Mapper,
    started: bool,
}

begin :: proc(mapper: memory.Mapper) -> runtime.Allocator {
    if !state.started {
        state.backing = context.allocator
        state.mapper = mapper
        trace.tracking_allocator_init(&state.tracker, state.backing, state.backing)
        state.tracker.bad_free_callback = bad_free
        state.started = true
        if libc.atexit(report) != 0 { panic("Could not register Thor allocation reporting") }
    }
    return trace.tracking_allocator(&state.tracker)
}

@(private, no_sanitize_address)
bad_free :: proc(_: ^trace.Tracking_Allocator, _: rawptr, _: trace.Capture_Const, loc: runtime.Source_Code_Location) {
    context.allocator = state.backing
    panic("Thor allocation tracker: invalid or double free", loc=state.mapper(loc))
}

@(private)
report :: proc "c" () {
    context = runtime.default_context()
    context.allocator = state.backing
    sync.mutex_lock(&state.tracker.mutex)
    defer sync.mutex_unlock(&state.tracker.mutex)
    if len(state.tracker.allocation_map) > 0 {
        fmt.eprintfln("Thor allocation tracker: %d allocation(s), %d byte(s) still allocated (with call stacks)",
            len(state.tracker.allocation_map), state.tracker.current_memory_allocated)
        for _, entry in state.tracker.allocation_map {
            memory.print_allocation(state.mapper(entry.location), entry.size)
            locations, err := trace.resolve(entry.backtrace, allocator=state.backing, temp_allocator=state.backing)
            if err == nil {
                for loc in locations {
                    mapped := state.mapper(loc)
                    fmt.eprintfln("    %s:%d:%d: %s", mapped.file_path, mapped.line, mapped.column, mapped.procedure)
                }
                trace.locations_destroy(locations, state.backing)
            }
        }
    }
    trace.tracking_allocator_destroy(&state.tracker)
}
