package thor_format

import "base:runtime"
import "core:strings"

// Initialize in place: a live Buffer contains pointers to its own storage and
// must not be copied. Borrowed views last only until the next write/destroy.
// Only an exact-sized, separately allocated string can escape this buffer.
Buffer :: struct #align(16) {
    storage:[512]byte,
    builder:strings.Builder,
    scratch:Scratch_Allocator,
}

@(private)
Scratch_Allocator :: struct {
    storage:[]byte,
    backing:runtime.Allocator,
    live:bool,
    failed:bool,
    size,alignment:int,
}

@(private)
scratch_allocate :: proc(data:rawptr,mode:runtime.Allocator_Mode,size,alignment:int,
                         old_memory:rawptr,old_size:int,loc:=#caller_location)->([]byte,runtime.Allocator_Error) {
    state:=cast(^Scratch_Allocator)data
    stack:=raw_data(state.storage)
    #partial switch mode {
    case .Alloc,.Alloc_Non_Zeroed:
        if size==0 {return nil,nil}
        if !state.live && size<=len(state.storage) && alignment>0 && uintptr(stack)%uintptr(alignment)==0 {
            state.live=true;state.size=size;state.alignment=alignment
            if mode==.Alloc {runtime.mem_zero(stack,size)}
            return state.storage[:size],nil
        }
    case .Resize,.Resize_Non_Zeroed:
        if old_memory==stack && state.live {
            if size<=len(state.storage) {
                if mode==.Resize && size>old_size {runtime.mem_zero(raw_data(state.storage[old_size:]),size-old_size)}
                state.size=size
                return state.storage[:size],nil
            }
            alloc_mode:=runtime.Allocator_Mode.Alloc if mode==.Resize else runtime.Allocator_Mode.Alloc_Non_Zeroed
            result,err:=state.backing.procedure(state.backing.data,alloc_mode,size,alignment,nil,0,loc)
            if err!=nil {
                if err!=.Mode_Not_Implemented {state.failed=true}
                return nil,err
            }
            copy(result,state.storage[:min(old_size,size)])
            state.live=false
            return result,nil
        }
    case .Free:
        if old_memory==stack {state.live=false;return nil,nil}
    case .Query_Info:
        info:=cast(^runtime.Allocator_Query_Info)old_memory
        if info!=nil && info.pointer==stack && state.live {
            info.size=state.size;info.alignment=state.alignment;info.pointer=nil
            return nil,nil
        }
    case:
    }
    result,err:=state.backing.procedure(state.backing.data,mode,size,alignment,old_memory,old_size,loc)
    if err!=nil && err!=.Mode_Not_Implemented &&
       (mode==.Alloc || mode==.Alloc_Non_Zeroed || mode==.Resize || mode==.Resize_Non_Zeroed) {state.failed=true}
    return result,err
}

buffer_init :: proc(buffer:^Buffer,allocator:=context.allocator) {
    buffer.scratch={storage=buffer.storage[:],backing=allocator}
    strings.builder_init(&buffer.builder,runtime.Allocator{procedure=scratch_allocate,data=&buffer.scratch})
}

buffer_destroy :: proc(buffer:^Buffer) {
    strings.builder_destroy(&buffer.builder)
    buffer.scratch={}
}

buffer_write_string :: proc(buffer:^Buffer,text:string) {
    // Writes must execute even when the application uses -disable-assert.
    if strings.write_string(&buffer.builder,text)!=len(text) {panic("format allocation failed")}
}

// A string carries no capacity or allocator. Keep this final exact-sized copy
// even on the heap fallback, so ordinary delete(string) works with sized frees.
buffer_owned_text :: proc(buffer:^Buffer,allocator:=context.allocator,loc:=#caller_location)->string {
    if buffer.scratch.failed {panic("format allocation failed")}
    text:=strings.clone(strings.to_string(buffer.builder),allocator,loc)
    if len(text)!=len(buffer.builder.buf) {panic("format allocation failed")}
    return text
}
