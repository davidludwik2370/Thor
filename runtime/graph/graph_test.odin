#+test
package thor_graph

import "base:runtime"
import "core:mem"
import "core:strings"
import "core:sync"
import "core:testing"
import big "thor:big"
import sets "thor:sets"

// These checks remain active in the unchecked/release test configuration.
check :: proc(condition:bool, message:string, loc:=#caller_location) {
    if !condition {panic(message,loc)}
}

@(test)
cycles_stack_targets_and_embedded_references :: proc(t:^testing.T) {
    Node :: struct {value:int,next,other:^Node,selected:^int}
    a:=Node{value=7};b:=Node{value=12}
    a.next=&b;b.next=&a;a.other=&b;b.other=&b
    a.selected=&b.value;b.selected=&a.value
    copy,err:=deepclone(&a)
    check(err==.None&&copy!=nil,"clone cycle")
    check(copy!=&a&&copy.next!=&b&&copy.next.next==copy,"cycle identity")
    check(copy.other==copy.next&&copy.next.other==copy.next,"shared object")
    check(copy.selected==&copy.next.value&&copy.next.selected==&copy.value,"embedded pointers")
    copy.selected^=99
    check(copy.next.value==99&&b.value==12,"source independent")
    free_err:=deepfree(copy)
    check(free_err==.None,"free cycle")
}

@(test)
views_preserve_overlaps_regardless_of_field_order :: proc(t:^testing.T) {
    Data :: struct {selected:^int,tail,head,full:[]int,bytes:string,alias:string}
    values:=[6]int{10,20,30,40,50,60}
    text:="abcdef"
    source:=Data{&values[2],values[2:],values[:4],values[:],text[2:],text}
    copy,err:=deepclone(&source)
    check(err==.None,"clone overlapping views")
    check(copy.selected==&copy.full[2]&&&copy.tail[0]==&copy.full[2]&&&copy.head[0]==&copy.full[0],"overlap relationship")
    check(uintptr(raw_data(copy.bytes))==uintptr(raw_data(copy.alias))+2,"overlapping strings")
    copy.selected^=81
    check(copy.tail[0]==81&&copy.head[2]==81&&values[2]==30,"mutate alias")
    free_err:=deepfree(copy)
    check(free_err==.None,"free overlapping views")
}

@(test)
dynamic_capacity_empty_views_and_zero_sized_values :: proc(t:^testing.T) {
    Empty :: struct {}
    Data :: struct {view:[]string,values:[dynamic]string,empty:[]int,zst:[]Empty,zero:^Empty,nil_slice:[]int}
    original:=make([dynamic]string,2,16)
    defer delete(original)
    original[0]="one";original[1]="two"
    backing:=[1]int{3};zst:=[3]Empty{};empty:Empty
    source:=Data{original[:],original,backing[:0],zst[:],&empty,nil}
    copy,err:=deepclone(&source)
    check(err==.None&&cap(copy.values)==16&&len(copy.values)==2,"dynamic capacity")
    check(raw_data(copy.view)==raw_data(copy.values)&&copy.view[1]=="two","dynamic view")
    check(raw_data(copy.empty)!=nil&&len(copy.empty)==0&&raw_data(copy.nil_slice)==nil,"empty descriptor identity")
    check(len(copy.zst)==3&&copy.zero!=nil,"zero size")
    free_err:=deepfree(copy)
    check(free_err==.None,"free dynamic and empty")
    nil_root:^Data
    absent,nil_err:=deepclone(nil_root)
    nil_free:=deepfree(nil_root)
    check(absent==nil&&nil_err==.None&&nil_free==.None,"nil root")
    root,empty_err:=deepclone(&empty)
    root_free:=deepfree(root)
    check(empty_err==.None&&root!=nil&&root_free==.None,"empty root")
}

@(test)
arrays_distinct_values_and_active_unions :: proc(t:^testing.T) {
    Key :: enum {A,B}
    Maybe :: union {^int}
    Value :: union {int,string,[]string}
    No_Nil :: union #no_nil {int,string}
    Data :: struct {indexed:[Key]string,values:[3]Value,maybe:Maybe,missing:Maybe,definite:No_Nil,fixed:[dynamic;4]string}
    number:=32;words:=[2]string{"alpha","beta"}
    source:=Data{indexed={.A="a",.B="b"},values={17,"text",words[:]},maybe=&number,definite="non-nil"}
    append(&source.fixed,"inline","buffer")
    copy,err:=deepclone(&source)
    check(err==.None,"union clone")
    check(copy.values[0].(int)==17&&copy.values[1].(string)=="text"&&copy.values[2].([]string)[1]=="beta","variants")
    check(copy.maybe.(^int)!=&number&&copy.maybe.(^int)^==32&&copy.missing==nil,"maybe pointer union")
    check(copy.definite.(string)=="non-nil"&&copy.indexed[.B]=="b"&&copy.fixed[1]=="buffer","inline data")
    free_err:=deepfree(copy)
    check(free_err==.None,"union free")
}

@(test)
maps_rehash_cloned_keys_and_preserve_table_and_cell_aliases :: proc(t:^testing.T) {
    Node :: struct {name:string,next:^Node}
    Root :: struct {key:^Node,table,alias:map[^Node]^Node,words:map[string][]int,cell:^int,cell_map:map[int]int,empty:map[int]string}
    a:=Node{name="a"};b:=Node{name="b"};a.next=&b;b.next=&a
    table:=make(map[^Node]^Node);defer delete(table)
    table[&a]=&b;table[&b]=&a
    values:=[3]int{4,5,6}
    words:=make(map[string][]int);defer delete(words)
    words["same"]=values[:];words["overlap"]=values[1:]
    cell_map:=make(map[int]int);defer delete(cell_map)
    cell_map[9]=42
    // Odin permits address_of_map_cell for use until the table is mutated.
    cell:=&cell_map[9]
    source:=Root{&a,table,table,words,cell,cell_map,nil}
    copy,err:=deepclone(&source)
    check(err==.None,"map clone")
    check(copy.key!=&a&&copy.table[copy.key]==copy.key.next&&copy.table[copy.key.next]==copy.key,"rehash pointer keys")
    check(runtime.map_data(transmute(runtime.Raw_Map)copy.table)==runtime.map_data(transmute(runtime.Raw_Map)copy.alias),"map aliases")
    check(&copy.words["same"][1]==&copy.words["overlap"][0],"map values share buffer")
    check(copy.cell==&copy.cell_map[9]&&copy.cell^==42,"map cell pointer")
    copy.cell^=77
    check(copy.cell_map[9]==77&&cell_map[9]==42,"map cell independent")
    check(len(copy.empty)==0,"empty map")
    free_err:=deepfree(copy)
    check(free_err==.None,"map free")
}

@(test)
sets_and_big_values_clone_internal_storage_without_compiler_hooks :: proc(t:^testing.T) {
    Data :: struct {names:sets.Set(string),integer:big.Integer,rational:big.Rational,alias:big.Integer}
    names:=sets.to_set([2]string{"apple","pear"});defer sets.destroy(names)
    integer:=big.integer(123456789);defer big.destroy(&integer)
    rational:=big.fraction(22,13);defer big.destroy(&rational)
    source:=Data{names,integer,rational,integer}
    copy,err:=deepclone(&source)
    check(err==.None,"set and big clone")
    check(sets.contains(copy.names,"pear")&&big.equal(copy.integer,integer)&&big.equal(copy.rational,rational),"set/big values")
    free_err:=deepfree(copy)
    check(free_err==.None&&sets.contains(names,"apple")&&big.equal(source.integer,integer),"set/big free")
}

@(test)
long_graph_has_no_recursion_depth_limit :: proc(t:^testing.T) {
    Node :: struct {next:^Node,value:int}
    source:=make([]Node,5000);defer delete(source)
    for &item,i in source {item.value=i;if i+1<len(source) {item.next=&source[i+1]}}
    copy,err:=deepclone(&source)
    check(err==.None&&copy^[4999].value==4999&&copy^[0].next==&copy^[1],"large graph")
    free_err:=deepfree(copy)
    check(free_err==.None,"large graph free")
}

@(test)
unsupported_schemas_fail_before_copying_even_when_nil_or_inactive :: proc(t:^testing.T) {
    Raw :: struct {data:rawptr}
    Callback :: struct {f:proc()}
    Value :: union {int,rawptr}
    Mutex :: struct {lock:sync.Mutex}
    a:Raw;b:Callback;c:Value=13;d:Mutex
    ca,ea:=deepclone(&a);cb,eb:=deepclone(&b);cc,ec:=deepclone(&c);cd,ed:=deepclone(&d)
    check(ca==nil&&cb==nil&&cc==nil&&cd==nil,"unsupported roots nil")
    check(ea==.Unsupported_Type&&eb==.Unsupported_Type&&ec==.Unsupported_Type&&ed==.Unsupported_Type,"unsupported schemas")
    text:cstring="unbounded"
    ctext,etext:=deepclone(&text)
    check(ctext==nil&&etext==.Unsupported_Type,"cstring")
}

Failing :: struct {backing:runtime.Allocator,remaining:int,failed:bool}
fail_allocate :: proc(data:rawptr,mode:runtime.Allocator_Mode,size,alignment:int,old:rawptr,old_size:int,loc:=#caller_location)->([]byte,runtime.Allocator_Error) {
    state:=cast(^Failing)data
    if size>0&&(mode==.Alloc||mode==.Alloc_Non_Zeroed||mode==.Resize||mode==.Resize_Non_Zeroed) {
        if state.remaining==0 {state.failed=true;return nil,.Out_Of_Memory}
        state.remaining-=1
    }
    return state.backing.procedure(state.backing.data,mode,size,alignment,old,old_size,loc)
}

@(test)
allocation_failure_sweep_rolls_back_output_and_temporary_storage :: proc(t:^testing.T) {
    tracker:mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    backing:=mem.tracking_allocator(&tracker)
    values:=[2]string{"first","second"}
    source:=make(map[string][]string);defer delete(source)
    source["words"]=values[:]
    // Destination and scratch failures each cover every allocation boundary.
    for scratch_failure in ([2]bool{false,true}) {
        completed:=false
        for limit in 0..<256 {
            state:=Failing{backing,limit,false}
            allocator:=runtime.Allocator{fail_allocate,&state}
            output:=allocator;temporary:=backing
            if scratch_failure {output=backing;temporary=allocator}
            copy,err:=deepclone(&source,output,temporary)
            if err==.None {
                free_err:=deepfree(copy,output,backing)
                check(free_err==.None,"free failure sweep success")
                completed=true
            }else {check(err==.Allocation_Failed&&copy==nil,"failed clone result")}
            check(len(tracker.allocation_map)==0&&len(tracker.bad_free_array)==0,"failure rollback leaks")
            if completed {break}
        }
        check(completed,"failure sweep bounded")
    }
}

Strict :: struct {
    backing:runtime.Allocator,
    sizes:map[rawptr]int,
    mismatch:bool,
}
strict_allocate :: proc(data:rawptr,mode:runtime.Allocator_Mode,size,alignment:int,old:rawptr,old_size:int,loc:=#caller_location)->([]byte,runtime.Allocator_Error) {
    s:=cast(^Strict)data
    if old!=nil&&(mode==.Free||mode==.Resize||mode==.Resize_Non_Zeroed) {
        recorded,found:=s.sizes[old]
        if !found||recorded!=old_size {s.mismatch=true;return nil,.Invalid_Argument}
    }
    result,err:=s.backing.procedure(s.backing.data,mode,size,alignment,old,old_size,loc)
    if err==nil {
        #partial switch mode {
        case .Alloc,.Alloc_Non_Zeroed:s.sizes[raw_data(result)]=size
        case .Resize,.Resize_Non_Zeroed:delete_key(&s.sizes,old);if raw_data(result)!=nil {s.sizes[raw_data(result)]=size}
        case .Free:delete_key(&s.sizes,old)
        }
    }
    return result,err
}

@(test)
sized_allocator_checks_every_group_and_map_free :: proc(t:^testing.T) {
    strict:=Strict{backing=context.allocator};defer delete(strict.sizes)
    allocator:=runtime.Allocator{strict_allocate,&strict}
    Data :: struct {one_past:[]byte,tail,full:[]byte,growing:[dynamic]string,table:map[string][]byte}
    bytes:=[8]byte{1,2,3,4,5,6,7,8}
    growing:=make([dynamic]string,1,27);defer delete(growing);growing[0]="some words"
    table:=make(map[string][]byte,32);defer delete(table);table["key"]=bytes[3:]
    source:=Data{bytes[8:],bytes[2:],bytes[:],growing,table}
    copy,err:=deepclone(&source,allocator,allocator)
    check(err==.None,"strict clone")
    check(uintptr(raw_data(copy.one_past))==uintptr(raw_data(copy.full))+8,"one-past empty view")
    free_err:=deepfree(copy,allocator,allocator)
    check(free_err==.None&&!strict.mismatch&&len(strict.sizes)==0,"sized cleanup")
}

@(test)
failed_free_discovery_leaves_graph_untouched_and_retryable :: proc(t:^testing.T) {
    tracker:mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    allocator:=mem.tracking_allocator(&tracker)
    values:=make([dynamic]string,1,8);defer delete(values);values[0]="preserved"
    copy,err:=deepclone(&values,allocator)
    check(err==.None,"clone for failed free")
    count:=len(tracker.allocation_map)
    wrong:=deepfree(copy)
    check(wrong==.Invalid_Allocator&&len(tracker.allocation_map)==count,"wrong allocator frees nothing")
    failing:=Failing{context.allocator,0,false}
    scratch:=runtime.Allocator{fail_allocate,&failing}
    failed:=deepfree(copy,allocator,scratch)
    check(failed==.Allocation_Failed&&copy^[0]=="preserved"&&len(tracker.allocation_map)==count,"scratch failure frees nothing")
    free_err:=deepfree(copy,allocator)
    check(free_err==.None&&len(tracker.allocation_map)==0,"retry free")
}

@(test)
shared_nil_unions_and_distinct_pointer_slice_types :: proc(t:^testing.T) {
    Pointer :: distinct ^int
    Slice :: distinct []int
    Value :: union #shared_nil {^int,^string}
    Data :: struct {a:Value,b:Value,p:Pointer,s:Slice,id:typeid}
    n:=8;text:="word";values:=[2]int{3,4}
    source:=Data{&n,&text,Pointer(&n),Slice(values[:]),Data}
    copy,err:=deepclone(&source)
    check(err==.None&&copy.a.(^int)==cast(^int)copy.p&&copy.b.(^string)^=="word"&&copy.s[1]==4&&copy.id==Data,"distinct/shared nil clone")
    free_err:=deepfree(copy)
    check(free_err==.None,"distinct/shared nil free")
}

@(test)
invalid_descriptors_and_allocator_are_reported_before_allocating_output :: proc(t:^testing.T) {
    number:=7
    copy,err:=deepclone(&number,allocator=runtime.Allocator{})
    check(copy==nil&&err==.Invalid_Allocator,"invalid allocator")
    storage:=[1]int{3}
    raw:=runtime.Raw_Slice{raw_data(storage[:]),max(int)}
    bad:=transmute([]int)raw
    invalid,size_err:=deepclone(&bad)
    check(invalid==nil&&size_err==.Size_Overflow,"size overflow")
    raw={nil,3};bad=transmute([]int)raw
    invalid_again,data_err:=deepclone(&bad)
    check(invalid_again==nil&&data_err==.Invalid_Data,"nil descriptor")
}

@(test)
map_cell_keys_are_rejected_with_rollback_instead_of_dangling_references :: proc(t:^testing.T) {
    table:=make(map[int]int);defer delete(table);table[1]=8
    keys:=make(map[^int]int);defer delete(keys);keys[&table[1]]=9
    source:=struct{table:map[int]int,keys:map[^int]int}{table,keys}
    copy,err:=deepclone(&source)
    check(copy==nil&&err==.Unsupported_Reference&&keys[&table[1]]==9,"map cell key restriction")
}

@(test)
packed_descriptors_and_aligned_objects_preserve_layout :: proc(t:^testing.T) {
    Packed :: struct #packed {prefix:u8,text:string,values:[]string,p:^int}
    Aligned :: struct #align(64) {value:int,text:string}
    words:=[2]string{"first","second"}
    n:=13
    source:=Packed{1,"packed",words[:],&n}
    copy,err:=deepclone(&source)
    check(err==.None&&copy.prefix==1&&copy.text=="packed"&&copy.values[1]=="second"&&copy.p^==13,"packed headers")
    free_err:=deepfree(copy)
    check(free_err==.None,"packed free")
    aligned:=Aligned{value=7,text="aligned"}
    aligned_copy,aligned_err:=deepclone(&aligned)
    check(aligned_err==.None&&uintptr(aligned_copy)%64==0&&aligned_copy.text=="aligned","aligned root")
    aligned_free:=deepfree(aligned_copy)
    check(aligned_free==.None,"aligned free")
}

expect_resource_rejection :: proc($T:typeid) {
    value:T
    copy,err:=deepclone(&value)
    check(copy==nil&&err==.Unsupported_Type,"resource schema")
}
@(test)
resource_wrappers_are_not_ordinary_numeric_data :: proc(t:^testing.T) {
    expect_resource_rejection(sync.Mutex)
    expect_resource_rejection(sync.Sema)
    expect_resource_rejection(sync.Futex)
    expect_resource_rejection(sync.Atomic_Cond)
    expect_resource_rejection(sync.Wait_Group)
    expect_resource_rejection(runtime.Allocator)
    expect_resource_rejection(runtime.Random_Generator)
}

@(test)
empty_view_between_disjoint_extents_is_rejected_instead_of_guessing :: proc(t:^testing.T) {
    values:=[4]int{1,2,3,4}
    source:=struct{a,b,empty:[]int}{values[:2],values[2:],values[2:2]}
    copy,err:=deepclone(&source)
    check(copy==nil&&err==.Unsupported_Reference,"ambiguous empty view")
}

@(test)
allocated_empty_map_and_new_owned_children_are_freed :: proc(t:^testing.T) {
    source:=make(map[int]string,32);defer delete(source)
    copy,err:=deepclone(&source)
    check(err==.None&&cap(copy^)==cap(source)&&len(copy^)==0,"reserved empty map")
    // Added owned children are allowed when boundaries and reachability remain.
    text:=strings.clone("owned")
    copy^[7]=text
    free_err:=deepfree(copy)
    check(free_err==.None,"new child cleanup")
}

@(test)
zero_sized_aggregate_map_values_need_no_relocation :: proc(t:^testing.T) {
    Empty :: struct {words:[0]string}
    source:=make(map[string]Empty);defer delete(source)
    source["a"]={};source["b"]={}
    copy,err:=deepclone(&source)
    check(err==.None&&len(copy^)==2&&"b" in copy^,"zero sized aggregate map values")
    free_err:=deepfree(copy)
    check(free_err==.None,"zero sized aggregate map free")
}

@(test)
map_rehash_relocates_all_interior_pointers_after_every_insertion :: proc(t:^testing.T) {
    Cell :: struct {value:int,next:^Cell}
    N :: 300
    source:=make(map[int]Cell,N);defer delete(source)
    for i in 0..<N {source[i]={value=i}}
    refs:[N]^Cell
    for i in 0..<N {refs[i]=&source[i];refs[i].next=&source[(i+1)%N]}
    root:=struct{refs:[]^Cell,table:map[int]Cell}{refs[:],source}
    copy,err:=deepclone(&root)
    check(err==.None,"many cell references clone")
    for i in 0..<N {
        check(copy.refs[i]==&copy.table[i]&&copy.refs[i].next==copy.refs[(i+1)%N]&&copy.refs[i].value==i,"rehash retained cell identity")
    }
    copy.refs[99].value=400
    check(copy.table[99].value==400&&source[99].value==99,"many cells independent")
    free_err:=deepfree(copy)
    check(free_err==.None,"many cells free")
}
