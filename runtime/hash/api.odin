// Value hashing for Thor's basic package. Ordinary native Odin, with no compiler hooks.
package thor_hash

import "core:fmt"

Algorithm :: enum { XXH3_64, XXH64, FNV64a }
Error :: enum { None, Unsupported_Type, Cyclic_Data, Too_Deep, Invalid_Value, Invalid_Algorithm }

// Hash a supported value without allocating. Strings/bytes hash their contents;
// records/sequences hash logical contents, pointers hash addresses. Maps/sets,
// opaque resources and cyclic containers are rejected. Default: XXH3, 64 bits.
// Allocator metadata, padding and spare capacity never participate.
hash :: proc(value:any, algorithm:=Algorithm.XXH3_64, seed:u64=0, loc:=#caller_location) -> u64 {
    state:State
    initialize(&state,algorithm,seed)
    result:=calculate(&state,value)
    if state.failure.error!=.None {
        buffer:[2048]u8
        message:="unsupported value type (maps/sets, raw unions, SOA and opaque resources are not supported)"
        #partial switch state.failure.error {
        case .Cyclic_Data:message="cyclic container data"
        case .Too_Deep:message="value/schema nesting exceeds the 128-level hashing limit"
        case .Invalid_Value:message="invalid value descriptor"
        case .Invalid_Algorithm:message="invalid hash algorithm"
        }
        used:=len(fmt.bprintf(buffer[:],"hash: %s at value",message))
        for part in state.failure.path[:state.failure.depth] {
            if part.indexed {used+=len(fmt.bprintf(buffer[used:],"[%d]",part.index))}
            else if len(part.name)>0 {used+=len(fmt.bprintf(buffer[used:],".%s",part.name))}
        }
        if state.failure.id!=nil {used+=len(fmt.bprintf(buffer[used:]," (type %v)",state.failure.id))}
        panic(string(buffer[:used]),loc)
    }
    return result
}

// Non-panicking counterpart. On failure result is zero; no input is mutated.
try_hash :: proc(value:any, algorithm:=Algorithm.XXH3_64, seed:u64=0) -> (result:u64, err:Error) {
    state:State
    initialize(&state,algorithm,seed)
    result=calculate(&state,value)
    err=state.failure.error
    return
}
