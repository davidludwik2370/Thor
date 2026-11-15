#+private
package thor_graph

import "base:runtime"
import "thor:typeinfo"
import "core:os"
import "core:slice"
import "core:sync"

Task :: struct {source:rawptr, info:^runtime.Type_Info}
Task_Key :: struct {address:uintptr, id:typeid}
Region :: struct {base:uintptr, size,alignment,copy_size:int}
Group :: struct {base,end:uintptr, alignment:int, destination:rawptr}
Table :: struct {
    source,destination:runtime.Raw_Map,
    info:^runtime.Map_Info,
    key_type,value_type:^runtime.Type_Info,
    entries_start,entries_count:int,
}
Entry :: struct {table:int, key,value:uintptr, key_copy:rawptr, new_key,new_value:rawptr}
Map_Range :: struct {base,end:uintptr, table:int}
Cell :: struct {base,end:uintptr, entry:int, key:bool}
State :: struct {
    allocator,scratch:runtime.Allocator,
    loc:runtime.Source_Code_Location,
    error:Error,
    freeing:bool,
    tasks:[dynamic]Task,
    seen:map[Task_Key]bool,
    regions:[dynamic]Region,
    groups:[dynamic]Group,
    tables:[dynamic]Table,
    table_indices:map[uintptr]int,
    entries:[dynamic]Entry,
    map_ranges:[dynamic]Map_Range,
    cells:[dynamic]Cell,
    key_copies:[dynamic][]byte,
}

state_init :: proc(s:^State,allocator,scratch:runtime.Allocator,loc:runtime.Source_Code_Location) {
    s.allocator=allocator;s.scratch=scratch;s.loc=loc
    s.tasks.allocator=scratch;s.regions.allocator=scratch;s.groups.allocator=scratch
    s.tables.allocator=scratch;s.entries.allocator=scratch;s.map_ranges.allocator=scratch
    s.cells.allocator=scratch;s.key_copies.allocator=scratch
    s.seen.allocator=scratch;s.table_indices.allocator=scratch
    if allocator.procedure==nil || scratch.procedure==nil {s.error=.Invalid_Allocator}
}
state_destroy :: proc(s:^State) {
    for data in s.key_copies {runtime.mem_free_bytes(data,s.scratch,s.loc)}
    delete(s.key_copies);delete(s.cells);delete(s.map_ranges);delete(s.entries)
    delete(s.table_indices);delete(s.tables);delete(s.groups);delete(s.regions)
    delete(s.seen);delete(s.tasks)
}
fail :: proc(s:^State,error:Error)->bool {if s.error==.None {s.error=error};return false}
push :: proc(s:^State,items:^[dynamic]$T,value:T)->bool {
    if s.error!=.None {return false}
    if append(items,value)!=1 {return fail(s,.Allocation_Failed)}
    return true
}
put :: proc(s:^State,table:^map[$K]$V,key:K,value:V)->bool {
    if s.error!=.None {return false}
    key,value:=key,value
    _,err:=runtime.__dynamic_map_set_without_hash(cast(^runtime.Raw_Map)table,runtime.map_info(type_of(table^)),&key,&value,s.loc)
    if err!=nil {return fail(s,.Allocation_Failed)}
    return true
}
offset :: proc "contextless"(p:rawptr,n:int)->rawptr {return rawptr(uintptr(p)+uintptr(n))}
// Packed fields can place a descriptor at an unaligned address. Do not load or
// store such fields through a naturally aligned ^Raw_* / ^rawptr cast.
load :: proc(p:rawptr,$T:typeid)->(value:T) {
    runtime.mem_copy_non_overlapping(&value,p,size_of(T))
    return
}
store :: proc(p:rawptr,value:$T) {
    value:=value
    runtime.mem_copy_non_overlapping(p,&value,size_of(T))
}
span_size :: proc(s:^State,count,size:int)->int {
    if count<0||size<0||size>0&&count>max(int)/size {fail(s,.Size_Overflow);return 0}
    return count*size
}
add_region :: proc(s:^State,p:rawptr,size,alignment,copy_size:int) {
    if s.error!=.None {return}
    if p==nil || size<0 || copy_size<0 || copy_size>size || alignment<1 {fail(s,.Invalid_Data);return}
    if uintptr(size)>max(uintptr)-uintptr(p) {fail(s,.Size_Overflow);return}
    push(s,&s.regions,Region{uintptr(p),size,alignment,copy_size})
}
leaf :: proc(info:^runtime.Type_Info)->bool {
    // Zero-sized arrays/records have no live reference slots. In particular,
    // map values of these types have no addressable value cells to relocate.
    if info.size==0 {return true}
    #partial switch item in runtime.type_info_base(info).variant {
    case typeinfo.Type_Info_Boolean,typeinfo.Type_Info_Integer,typeinfo.Type_Info_Rune,typeinfo.Type_Info_Float,
         typeinfo.Type_Info_Complex,typeinfo.Type_Info_Quaternion,typeinfo.Type_Info_Enum,typeinfo.Type_Info_Bit_Set,
         typeinfo.Type_Info_Type_Id,typeinfo.Type_Info_Simd_Vector,typeinfo.Type_Info_Matrix,typeinfo.Type_Info_Bit_Field:
        return true
    case typeinfo.Type_Info_Struct:return item.field_count==0
    }
    return false
}
unsupported_resource :: proc(info:^runtime.Type_Info)->bool {
    return info.id==typeid_of(runtime.Allocator)||info.id==typeid_of(runtime.Random_Generator)||
           info.id==typeid_of(os.File)||info.id==typeid_of(sync.Mutex)||info.id==typeid_of(sync.RW_Mutex)||
           info.id==typeid_of(sync.Recursive_Mutex)||info.id==typeid_of(sync.Atomic_Mutex)||
           info.id==typeid_of(sync.Atomic_RW_Mutex)||info.id==typeid_of(sync.Atomic_Recursive_Mutex)||
           info.id==typeid_of(sync.Barrier)||info.id==typeid_of(sync.Ticket_Mutex)||
           info.id==typeid_of(sync.Cond)||info.id==typeid_of(sync.Sema)||info.id==typeid_of(sync.Futex)||
           info.id==typeid_of(sync.Atomic_Cond)||info.id==typeid_of(sync.Atomic_Sema)||
           info.id==typeid_of(sync.Wait_Group)||info.id==typeid_of(sync.Auto_Reset_Event)||
           info.id==typeid_of(sync.Benaphore)||info.id==typeid_of(sync.Recursive_Benaphore)||
           info.id==typeid_of(sync.Once)||info.id==typeid_of(sync.Parker)||info.id==typeid_of(sync.One_Shot_Event)
}
validate_schema :: proc(s:^State,root:^runtime.Type_Info)->bool {
    work:[dynamic]^runtime.Type_Info;work.allocator=s.scratch
    seen:map[typeid]bool;seen.allocator=s.scratch
    defer delete(work);defer delete(seen)
    if !push(s,&work,root) {return false}
    for index:=0;index<len(work)&&s.error==.None;index+=1 {
        info:=work[index]
        if info==nil {return fail(s,.Unsupported_Type)}
        if info.id in seen {continue}
        if !put(s,&seen,info.id,true) {return false}
        if unsupported_resource(info) {return fail(s,.Unsupported_Type)}
        #partial switch item in info.variant {
        case typeinfo.Type_Info_Named:push(s,&work,item.base)
        case typeinfo.Type_Info_Boolean,typeinfo.Type_Info_Integer,typeinfo.Type_Info_Rune,typeinfo.Type_Info_Float,
             typeinfo.Type_Info_Complex,typeinfo.Type_Info_Quaternion,typeinfo.Type_Info_Enum,typeinfo.Type_Info_Bit_Set,
             typeinfo.Type_Info_Type_Id,typeinfo.Type_Info_Simd_Vector,typeinfo.Type_Info_Matrix,typeinfo.Type_Info_Bit_Field:
        case typeinfo.Type_Info_Pointer:
            if item.elem==nil {return fail(s,.Unsupported_Type)}
            push(s,&work,item.elem)
        case typeinfo.Type_Info_String:
            if item.is_cstring {return fail(s,.Unsupported_Type)}
        case typeinfo.Type_Info_Slice:push(s,&work,item.elem)
        case typeinfo.Type_Info_Dynamic_Array:push(s,&work,item.elem)
        case typeinfo.Type_Info_Fixed_Capacity_Dynamic_Array:push(s,&work,item.elem)
        case typeinfo.Type_Info_Array:push(s,&work,item.elem)
        case typeinfo.Type_Info_Enumerated_Array:push(s,&work,item.elem)
        case typeinfo.Type_Info_Struct:
            if .raw_union in item.flags || item.soa_kind!=.None {return fail(s,.Unsupported_Type)}
            for i in 0..<int(item.field_count) {push(s,&work,item.types[i])}
        case typeinfo.Type_Info_Union:
            for variant in item.variants {push(s,&work,variant)}
        case typeinfo.Type_Info_Map:
            push(s,&work,item.key);push(s,&work,item.value)
        case:return fail(s,.Unsupported_Type)
        }
    }
    return s.error==.None
}
enqueue :: proc(s:^State,p:rawptr,info:^runtime.Type_Info) {
    if s.error!=.None || leaf(info) {return}
    if p==nil {fail(s,.Invalid_Data);return}
    key:=Task_Key{uintptr(p),info.id}
    if key in s.seen {return}
    if put(s,&s.seen,key,true) {push(s,&s.tasks,Task{p,info})}
}
sequence :: proc(s:^State,p:rawptr,count,stride:int,elem:^runtime.Type_Info) {
    if count<0 || stride<0 {fail(s,.Invalid_Data);return}
    span_size(s,count,stride)
    if s.error!=.None || leaf(elem) {return}
    for i in 0..<count {enqueue(s,offset(p,i*stride),elem);if s.error!=.None {return}}
}
active_variant :: proc(s:^State,p:rawptr,info:typeinfo.Type_Info_Union)->^runtime.Type_Info {
    if len(info.variants)==0 {return nil}
    // A single pointer variant is represented by the pointer alone. Some Odin
    // versions still populate tag_type for this representation.
    pure_maybe:=false
    if len(info.variants)==1 {
        _,pure_maybe=runtime.type_info_base(info.variants[0]).variant.(typeinfo.Type_Info_Pointer)
    }
    if pure_maybe {
        if load(p,rawptr)==nil {return nil}
        return info.variants[0]
    }
    if info.tag_type==nil {fail(s,.Unsupported_Type);return nil}
    tag:u128
    address:=offset(p,int(info.tag_offset))
    switch info.tag_type.size {
    case 1:value:u8;runtime.mem_copy_non_overlapping(&value,address,1);tag=u128(value)
    case 2:value:u16;runtime.mem_copy_non_overlapping(&value,address,2);tag=u128(value)
    case 4:value:u32;runtime.mem_copy_non_overlapping(&value,address,4);tag=u128(value)
    case 8:value:u64;runtime.mem_copy_non_overlapping(&value,address,8);tag=u128(value)
    case 16:runtime.mem_copy_non_overlapping(&tag,address,16)
    case:fail(s,.Unsupported_Type);return nil
    }
    if !info.no_nil {if tag==0 {return nil};tag-=1}
    if tag>=u128(len(info.variants)) {fail(s,.Invalid_Data);return nil}
    return info.variants[int(tag)]
}
discover :: proc(s:^State) {
    for index:=0;index<len(s.tasks)&&s.error==.None;index+=1 {
        task:=s.tasks[index];p:=task.source
        #partial switch item in runtime.type_info_base(task.info).variant {
        case typeinfo.Type_Info_Pointer:
            target:=load(p,rawptr)
            if target!=nil {add_region(s,target,item.elem.size,item.elem.align,item.elem.size);enqueue(s,target,item.elem)}
        case typeinfo.Type_Info_String:
            value:=load(p,runtime.Raw_Slice)
            width:=2 if item.encoding==.UTF_16 else 1
            size:=span_size(s,value.len,width)
            if value.data!=nil {add_region(s,value.data,size,width,size)} else if value.len!=0 {fail(s,.Invalid_Data)}
        case typeinfo.Type_Info_Slice:
            value:=load(p,runtime.Raw_Slice)
            size:=span_size(s,value.len,item.elem_size)
            if value.data!=nil {add_region(s,value.data,size,item.elem.align,size);sequence(s,value.data,value.len,item.elem_size,item.elem)}
            else if value.len!=0&&item.elem_size!=0 {fail(s,.Invalid_Data)}
        case typeinfo.Type_Info_Dynamic_Array:
            value:=load(p,runtime.Raw_Dynamic_Array)
            if value.len<0 || value.cap<value.len {fail(s,.Invalid_Data);continue}
            size:=span_size(s,value.cap,item.elem_size)
            used:=span_size(s,value.len,item.elem_size)
            if s.freeing&&value.data!=nil&&(value.allocator.procedure!=s.allocator.procedure||value.allocator.data!=s.allocator.data) {fail(s,.Invalid_Allocator);continue}
            if value.data!=nil {add_region(s,value.data,size,item.elem.align,used);sequence(s,value.data,value.len,item.elem_size,item.elem)}
            else if value.cap!=0&&item.elem_size!=0 {fail(s,.Invalid_Data)}
        case typeinfo.Type_Info_Array:sequence(s,p,item.count,item.elem_size,item.elem)
        case typeinfo.Type_Info_Enumerated_Array:sequence(s,p,item.count,item.elem_size,item.elem)
        case typeinfo.Type_Info_Fixed_Capacity_Dynamic_Array:
            count:=load(offset(p,int(item.len_offset)),int)
            if count<0||count>item.capacity {fail(s,.Invalid_Data);continue}
            sequence(s,p,count,item.elem_size,item.elem)
        case typeinfo.Type_Info_Struct:
            for i in 0..<int(item.field_count) {enqueue(s,offset(p,int(item.offsets[i])),item.types[i])}
        case typeinfo.Type_Info_Union:
            variant:=active_variant(s,p,item)
            if variant!=nil {enqueue(s,p,variant)}
        case typeinfo.Type_Info_Map:discover_map(s,p,item)
        }
    }
}

discover_map :: proc(s:^State,p:rawptr,info:typeinfo.Type_Info_Map) {
    value:=load(p,runtime.Raw_Map)
    base:=runtime.map_data(value)
    if base==0 {if value.len!=0 {fail(s,.Invalid_Data)};return}
    if s.freeing&&(value.allocator.procedure!=s.allocator.procedure||value.allocator.data!=s.allocator.data) {fail(s,.Invalid_Allocator);return}
    if index,found:=s.table_indices[base];found {
        if s.tables[index].info!=info.map_info {fail(s,.Unsupported_Reference)}
        return
    }
    capacity:=runtime.map_cap(value)
    per_entry:=info.map_info.ks.size_of_type+info.map_info.vs.size_of_type+64
    if capacity>max(int)/max(1,int(per_entry)) {fail(s,.Size_Overflow);return}
    size:=runtime.map_total_allocation_size(uintptr(capacity),info.map_info)
    if size>uintptr(max(int)) || size>max(uintptr)-base || value.len>uintptr(capacity) {fail(s,.Invalid_Data);return}
    index:=len(s.tables)
    if !put(s,&s.table_indices,base,index) {return}
    push(s,&s.tables,Table{source=value,info=info.map_info,key_type=info.key,value_type=info.value,entries_start=len(s.entries)})
    push(s,&s.map_ranges,Map_Range{base,base+size,index})
    ks,vs,hs,_,_:=runtime.map_kvh_data_dynamic(value,info.map_info)
    for i in 0..<capacity {
        if !runtime.map_hash_is_valid(hs[i]) {continue}
        key:=runtime.map_cell_index_dynamic(ks,info.map_info.ks,uintptr(i))
        data:=runtime.map_cell_index_dynamic(vs,info.map_info.vs,uintptr(i))
        entry:=len(s.entries)
        push(s,&s.entries,Entry{table=index,key=key,value=data})
        if info.key.size>0 {push(s,&s.cells,Cell{key,key+uintptr(info.key.size),entry,true})}
        if info.value.size>0 {push(s,&s.cells,Cell{data,data+uintptr(info.value.size),entry,false})}
        enqueue(s,rawptr(key),info.key);enqueue(s,rawptr(data),info.value)
        if s.error!=.None {return}
    }
    s.tables[index].entries_count=len(s.entries)-s.tables[index].entries_start
}

map_range :: proc(s:^State,address:uintptr)->int {
    lo,hi:=0,len(s.map_ranges)
    for lo<hi {mid:=(lo+hi)/2;if s.map_ranges[mid].base<=address {lo=mid+1}else{hi=mid}}
    if lo>0&&address<s.map_ranges[lo-1].end {return lo-1}
    return -1
}
cell_range :: proc(s:^State,address:uintptr)->int {
    lo,hi:=0,len(s.cells)
    for lo<hi {mid:=(lo+hi)/2;if s.cells[mid].base<=address {lo=mid+1}else{hi=mid}}
    if lo>0&&address<s.cells[lo-1].end {return lo-1}
    return -1
}
organize :: proc(s:^State) {
    if s.error!=.None {return}
    slice.sort_by(s.map_ranges[:],proc(a,b:Map_Range)->bool{return a.base<b.base})
    slice.sort_by(s.cells[:],proc(a,b:Cell)->bool{return a.base<b.base})
    slice.sort_by(s.regions[:],proc(a,b:Region)->bool{return a.base<b.base || a.base==b.base&&a.size>b.size})
    map_cursor:=0
    for region in s.regions {
        end:=region.base+uintptr(region.size)
        map_index:=map_range(s,region.base)
        if map_index>=0 {
            cell:=cell_range(s,region.base)
            if cell<0 || end>s.cells[cell].end {fail(s,.Unsupported_Reference);return}
            continue
        }
        // A range cannot straddle map bookkeeping/cells or arbitrary opaque storage.
        for map_cursor<len(s.map_ranges)&&s.map_ranges[map_cursor].end<=region.base {map_cursor+=1}
        if map_cursor<len(s.map_ranges)&&s.map_ranges[map_cursor].base<end {fail(s,.Unsupported_Reference);return}
        if len(s.groups)>0 {
            last:=&s.groups[len(s.groups)-1]
            if region.base<last.end || region.base==last.base || region.size==0&&region.base==last.end {
                last.end=max(last.end,end);last.alignment=max(last.alignment,region.alignment)
                continue
            }
        }
        push(s,&s.groups,Group{region.base,end,region.alignment,nil})
        if s.error!=.None {return}
    }
    // Natural typed layouts admit one consistent alignment-preserving translation.
    for group in s.groups {if group.base%uintptr(group.alignment)!=0 {fail(s,.Unsupported_Reference);return}}
    // An empty view exactly between two disjoint extents could belong to either.
    // Their original allocation identity is unavailable; do not guess an owner.
    for region in s.regions {
        if region.size!=0 {continue}
        lo,hi:=0,len(s.groups)
        for lo<hi {mid:=(lo+hi)/2;if s.groups[mid].base<=region.base {lo=mid+1}else{hi=mid}}
        if lo>1&&s.groups[lo-1].base==region.base&&s.groups[lo-2].end==region.base {
            fail(s,.Unsupported_Reference);return
        }
    }
}
translate :: proc(s:^State,address:uintptr,size:int,allow_cells:bool)->rawptr {
    if address==0 {return nil}
    cell_index:=cell_range(s,address)
    if cell_index>=0 {
        cell:=s.cells[cell_index]
        if !allow_cells||uintptr(size)>cell.end-address {fail(s,.Unsupported_Reference);return nil}
        entry:=s.entries[cell.entry]
        target:=entry.new_key if cell.key else entry.new_value
        if target==nil {fail(s,.Unsupported_Reference);return nil}
        return offset(target,int(address-cell.base))
    }
    lo,hi:=0,len(s.groups)
    for lo<hi {mid:=(lo+hi)/2;if s.groups[mid].base<=address {lo=mid+1}else{hi=mid}}
    if lo>0 {
        group:=s.groups[lo-1]
        if address<=group.end && uintptr(size)<=group.end-address {return offset(group.destination,int(address-group.base))}
    }
    fail(s,.Unsupported_Reference)
    return nil
}
allocate_groups :: proc(s:^State) {
    if s.error!=.None {return}
    for &group in s.groups {
        size:=max(1,int(group.end-group.base))
        data,err:=runtime.mem_alloc_bytes(size,group.alignment,s.allocator,s.loc)
        if err!=nil||len(data)<size||raw_data(data)==nil {fail(s,.Allocation_Failed);return}
        group.destination=raw_data(data)
    }
    for region in s.regions {
        if region.copy_size==0 || map_range(s,region.base)>=0 {continue}
        dst:=translate(s,region.base,region.size,false)
        if s.error!=.None {return}
        runtime.mem_copy_non_overlapping(dst,rawptr(region.base),region.copy_size)
    }
}

patch_header :: proc(s:^State,dst,src:rawptr,info:^runtime.Type_Info,allow_cells:bool) {
    #partial switch item in runtime.type_info_base(info).variant {
    case typeinfo.Type_Info_Pointer:
        original:=load(src,rawptr)
        store(dst,translate(s,uintptr(original),item.elem.size,allow_cells))
    case typeinfo.Type_Info_String:
        value:=load(src,runtime.Raw_Slice)
        width:=2 if item.encoding==.UTF_16 else 1
        value.data=translate(s,uintptr(value.data),span_size(s,value.len,width),allow_cells)
        store(dst,value)
    case typeinfo.Type_Info_Slice:
        value:=load(src,runtime.Raw_Slice)
        value.data=translate(s,uintptr(value.data),span_size(s,value.len,item.elem_size),allow_cells)
        store(dst,value)
    case typeinfo.Type_Info_Dynamic_Array:
        value:=load(src,runtime.Raw_Dynamic_Array)
        value.data=translate(s,uintptr(value.data),span_size(s,value.cap,item.elem_size),allow_cells)
        value.allocator=s.allocator
        store(dst,value)
    case typeinfo.Type_Info_Map:
        value:=load(src,runtime.Raw_Map)
        if base:=runtime.map_data(value);base!=0 {
            table:=s.tables[s.table_indices[base]]
            result:=table.destination;result.len=value.len
            store(dst,result)
        }else {store(dst,runtime.Raw_Map{allocator=s.allocator})}
    }
}
// Keys are copied into temporary cells before hashing. References into map cells
// cannot be used as keys: placement would depend on the address being hashed.
patch_key :: proc(s:^State,dst,src:rawptr,info:^runtime.Type_Info) {
    work:[dynamic]Task;work.allocator=s.scratch;defer delete(work)
    if !push(s,&work,Task{src,info}) {return}
    for i:=0;i<len(work)&&s.error==.None;i+=1 {
        task:=work[i]
        out:=offset(dst,int(uintptr(task.source)-uintptr(src)))
        #partial switch item in runtime.type_info_base(task.info).variant {
        case typeinfo.Type_Info_Struct:
            for j in 0..<int(item.field_count) {push(s,&work,Task{offset(task.source,int(item.offsets[j])),item.types[j]})}
        case typeinfo.Type_Info_Array:
            for j in 0..<item.count {push(s,&work,Task{offset(task.source,j*item.elem_size),item.elem})}
        case typeinfo.Type_Info_Enumerated_Array:
            for j in 0..<item.count {push(s,&work,Task{offset(task.source,j*item.elem_size),item.elem})}
        case typeinfo.Type_Info_Union:
            variant:=active_variant(s,task.source,item)
            if variant!=nil {push(s,&work,Task{task.source,variant})}
        case:patch_header(s,out,task.source,task.info,false)
        }
    }
}
build_maps :: proc(s:^State) {
    if s.error!=.None {return}
    for &table in s.tables {
        capacity:=runtime.map_cap(table.source)
        log2:=uintptr(0)
        for (uintptr(1)<<log2)<uintptr(capacity) {log2+=1}
        result,err:=runtime.map_alloc_dynamic(table.info,log2,s.allocator,s.loc)
        table.destination=result
        if err!=nil {fail(s,.Allocation_Failed);return}
    }
    for &entry in s.entries {
        table:=&s.tables[entry.table]
        key_size:=max(1,table.key_type.size)
        data,err:=runtime.mem_alloc_bytes(key_size,table.key_type.align,s.scratch,s.loc)
        if err!=nil||len(data)<key_size||raw_data(data)==nil {fail(s,.Allocation_Failed);return}
        if !push(s,&s.key_copies,data) {runtime.mem_free_bytes(data,s.scratch,s.loc);return}
        entry.key_copy=raw_data(data)
        runtime.mem_copy_non_overlapping(entry.key_copy,rawptr(entry.key),table.key_type.size)
        patch_key(s,entry.key_copy,rawptr(entry.key),table.key_type)
        if s.error!=.None {return}
        // Reserve source capacity up front; no entry pointer is retained until
        // every insertion has finished (Robin Hood insertion can move entries).
        _,set_error:=runtime.__dynamic_map_set_without_hash(&table.destination,table.info,entry.key_copy,rawptr(entry.value),s.loc)
        if set_error!=nil {fail(s,.Allocation_Failed);return}
    }
    for &entry in s.entries {
        table:=s.tables[entry.table]
        index,found:=runtime.map_lookup_dynamic(table.destination,table.info,uintptr(entry.key_copy))
        if !found {fail(s,.Invalid_Data);return}
        ks,vs,_,_,_:=runtime.map_kvh_data_dynamic(table.destination,table.info)
        entry.new_key=rawptr(runtime.map_cell_index_dynamic(ks,table.info.ks,index))
        entry.new_value=rawptr(runtime.map_cell_index_dynamic(vs,table.info.vs,index))
    }
}
patch_graph :: proc(s:^State) {
    if s.error!=.None {return}
    for task in s.tasks {
        dst:=translate(s,uintptr(task.source),task.info.size,true)
        if s.error!=.None {return}
        patch_header(s,dst,task.source,task.info,true)
        if s.error!=.None {return}
    }
}
rollback :: proc(s:^State) {
    for group in s.groups {if group.destination!=nil {runtime.mem_free_with_size(group.destination,max(1,int(group.end-group.base)),s.allocator,s.loc)}}
    for table in s.tables {if table.destination.data!=0 {_ = runtime.map_free_dynamic(table.destination,table.info,s.loc)}}
}
release_graph :: proc(s:^State)->Error {
    error:=Error.None
    for group in s.groups {
        if runtime.mem_free_with_size(rawptr(group.base),max(1,int(group.end-group.base)),s.allocator,s.loc)!=nil {error=.Free_Failed}
    }
    for table in s.tables {
        if runtime.map_free_dynamic(table.source,table.info,s.loc)!=nil {error=.Free_Failed}
    }
    return error
}
