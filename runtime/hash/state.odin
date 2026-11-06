#+private
package thor_hash

import "base:runtime"
import "core:hash"
import "core:hash/xxhash"

MAX_DEPTH :: 128
Path :: struct {name:string,index:int,indexed:bool}
Frame :: struct {address:rawptr,id:typeid}
Failure :: struct {error:Error,id:typeid,path:[MAX_DEPTH]Path,depth:int}
State :: struct {
    algorithm:Algorithm,
    seed:u64,
    direct:bool,
    direct_hash:u64,
    h3:xxhash.XXH3_state,
    h64:xxhash.XXH64_state,
    fnv:u64,
    failure:Failure,
    path:[MAX_DEPTH]Path,
    frames:[MAX_DEPTH]Frame,
    schemas:[MAX_DEPTH]typeid,
    depth,frame_count,schema_count:int,
}
Tag :: enum u8 {Boolean,Integer,Float,Complex,Quaternion,Enum,Bit_Set,Pointer,Type_Id,
    String,Sequence,Bytes,Struct,Union,Any,Big_Integer,Big_Rational,Vector,Matrix,Bit_Field}

fail :: proc(s:^State,error:Error,info:^runtime.Type_Info=nil) {
    if s.failure.error!=.None {return}
    s.failure.error=error
    if info!=nil {s.failure.id=info.id}
    s.failure.path=s.path;s.failure.depth=s.depth
}
initialize :: proc(s:^State,algorithm:Algorithm,seed:u64) {
    s.algorithm=algorithm
    s.seed=seed
    switch algorithm {
    case .XXH3_64:
        err:=xxhash.XXH3_64_reset_with_seed(&s.h3,seed)
        if err!=.None {fail(s,.Invalid_Algorithm)}
    case .XXH64:
        err:=xxhash.XXH64_reset_state(&s.h64,seed)
        if err!=.None {fail(s,.Invalid_Algorithm)}
    case .FNV64a:s.fnv=0xcbf29ce484222325 ~ seed
    case:fail(s,.Invalid_Algorithm)
    }
}
emit :: proc(s:^State,bytes:[]u8) {
    if s.failure.error!=.None || len(bytes)==0 {return}
    switch s.algorithm {
    case .XXH3_64:
        // Odin's large-update path saves the wrong last stripe. Feeding at
        // most one internal buffer avoids that path while preserving the
        // standard stream and its existing SIMD accumulation routines.
        for start:=0;start<len(bytes); {
            end:=start+min(xxhash.XXH3_INTERNAL_BUFFER_SIZE,len(bytes)-start)
            err:=xxhash.XXH3_64_update(&s.h3,bytes[start:end])
            if err!=.None {fail(s,.Invalid_Value);return}
            start=end
        }
    case .XXH64:
        err:=xxhash.XXH64_update(&s.h64,bytes)
        if err!=.None {fail(s,.Invalid_Value)}
    case .FNV64a:s.fnv=hash.fnv64a(bytes,s.fnv)
    }
}
direct_bytes :: proc(s:^State,bytes:[]u8) {
    bytes:=bytes
    if len(bytes)==0 {bytes=s.h3.buffer[:0]}
    s.direct=true
    switch s.algorithm {
    case .XXH3_64:s.direct_hash=xxhash.XXH3_64(bytes,s.seed)
    case .XXH64:s.direct_hash=xxhash.XXH64(bytes,s.seed)
    case .FNV64a:s.direct_hash=hash.fnv64a(bytes,0xcbf29ce484222325~s.seed)
    }
}
number :: proc(s:^State,value:u128,width:int=8) {
    bytes:[16]u8
    for i in 0..<width {bytes[i]=u8(value>>uint(i*8))}
    emit(s,bytes[:width])
}
tag :: proc(s:^State,value:Tag) {number(s,u128(value),1)}
load :: proc(p:rawptr,$T:typeid)->(value:T) {
    // Packed records can put even strings, pointers and arrays at unaligned offsets.
    runtime.mem_copy_non_overlapping(&value,p,size_of(T))
    return
}
offset :: proc(p:rawptr,n:int)->rawptr {return rawptr(uintptr(p)+uintptr(n))}
unsigned :: proc(p:rawptr,width:int,endian:=runtime.Platform_Endianness.Platform)->u128 {
    bytes:=([^]u8)(p)
    value:u128
    little:=endian==.Little || endian==.Platform && ODIN_ENDIAN==.Little
    for i in 0..<width {
        index:=i if little else width-1-i
        value|=u128(bytes[index])<<uint(i*8)
    }
    return value
}
span :: proc(s:^State,p:rawptr,count,stride:int,info:^runtime.Type_Info)->bool {
    if count<0 || stride<0 || stride>0 && count>max(int)/stride || count>0 && stride>0 && p==nil {
        fail(s,.Invalid_Value,info);return false
    }
    if uintptr(count*stride)>max(uintptr)-uintptr(p) {fail(s,.Invalid_Value,info);return false}
    return true
}
push_path :: proc(s:^State,part:Path)->bool {
    if s.depth>=MAX_DEPTH {fail(s,.Too_Deep);return false}
    s.path[s.depth]=part;s.depth+=1
    return true
}
digest :: proc(s:^State)->u64 {
    if s.failure.error!=.None {return 0}
    if s.direct {return s.direct_hash}
    switch s.algorithm {
    case .XXH3_64:
        // Odin's current short-input streaming finalizer has its seed test
        // reversed. The buffered one-shot path is exact for both zero/nonzero
        // seeds, and remains valid after the upstream finalizer is corrected.
        if s.h3.total_length<=xxhash.XXH3_MIDSIZE_MAX {
            return xxhash.XXH3_64(s.h3.buffer[:s.h3.total_length],s.h3.seed)
        }
        return xxhash.XXH3_64_digest(&s.h3)
    case .XXH64:return xxhash.XXH64_digest(&s.h64)
    case .FNV64a:return s.fnv
    }
    return 0
}
