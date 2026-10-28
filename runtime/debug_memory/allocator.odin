package debug_memory

import "base:runtime"
import libc "core:c/libc"
import "core:fmt"
import "core:mem"
import "core:sync"

State :: struct {
    tracker: mem.Tracking_Allocator,
    backing: runtime.Allocator,
    mapper: Mapper,
    started: bool,
}

@(private)
state: State

// Stable process-lifetime storage permits cleanup after main returns. The C
// shutdown callback runs after Odin's normal @(fini) procedures and main defers.
begin :: proc(mapper: Mapper) -> runtime.Allocator {
    if !state.started {
        state.backing = context.allocator
        state.mapper = mapper
        mem.tracking_allocator_init(&state.tracker, state.backing, state.backing)
        state.tracker.bad_free_callback = bad_free
        state.started = true
        if libc.atexit(report) != 0 { panic("Could not register Thor allocation reporting") }
    }
    return mem.tracking_allocator(&state.tracker)
}

@(private, no_sanitize_address)
bad_free :: proc(_: ^mem.Tracking_Allocator, _: rawptr, loc: runtime.Source_Code_Location) {
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
        fmt.eprintfln("Thor allocation tracker: %d allocation(s), %d byte(s) still allocated",
            len(state.tracker.allocation_map), state.tracker.current_memory_allocated)
        for _, entry in state.tracker.allocation_map {
            print_allocation(state.mapper(entry.location), entry.size)
        }
    }
    mem.tracking_allocator_destroy(&state.tracker)
}

print_allocation :: proc(loc: runtime.Source_Code_Location, size: int) {
    fmt.eprintfln("  %s:%d:%d: %d bytes allocated in %s", loc.file_path, loc.line, loc.column, size, loc.procedure)
}
