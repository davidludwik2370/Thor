package thor_hash

import "base:runtime"
import hashes "core:hash"
import "core:hash/xxhash"
import "core:mem"
import "core:net"
import "core:os"
import "core:strings"
import "core:sync"
import "core:testing"
import "core:fmt"
import "thor:big"

check :: proc(value:bool,message:string,loc:=#caller_location) {if !value {panic(message,loc)}}

@(test)
raw_content_algorithms_seeds_and_empty_inputs :: proc(t:^testing.T) {
    text:="hello 😀\x00world"
    bytes:=transmute([]u8)text
    copy:=strings.clone(text);defer delete(copy)
    for algorithm in Algorithm {for seed in ([]u64{0,7,max(u64)}) {
        expected:u64
        switch algorithm {
        case .XXH3_64:expected=xxhash.XXH3_64(bytes,seed)
        case .XXH64:expected=xxhash.XXH64(bytes,seed)
        case .FNV64a:expected=hashes.fnv64a(bytes,0xcbf29ce484222325~seed)
        }
        a:=hash(text,algorithm,seed);b:=hash(bytes,algorithm,seed);c:=hash(copy,algorithm,seed)
        if a!=expected||a!=b||b!=c {fmt.println(algorithm,seed,expected,a,b,c)}
        check(a==expected&&a==b&&b==c,"raw content/seed compatibility")
    }}
    a:=hash("",.FNV64a);b:=hash([]u8{},.FNV64a)
    check(a==#hash("","fnv64a")&&a==b,"empty FNV offset")
    c:=hash("hello",.FNV64a)
    check(c==#hash("hello","fnv64a"),"compile-time string counterpart")
    empty:=hash("");expected:=xxhash.XXH3_64(bytes[:0])
    check(empty==expected,"default/empty XXH3")
    large:=make([]u8,4097);defer delete(large)
    for &v,i in large {v=u8(i)}
    actual:=hash(large);large_expected:=xxhash.XXH3_64(large)
    check(actual==large_expected,"long input XXH3")
}

@(test)
streaming_boundaries_match_standard_one_shot_algorithms :: proc(t:^testing.T) {
    input:=make([]u8,8193);defer delete(input)
    for &v,i in input {v=u8((i*71+13)%256)}
    lengths:=[?]int{0,1,3,4,8,9,16,17,127,128,129,239,240,241,255,256,257,511,512,513,1023,1024,1025,4097,8193}
    chunks:=[?]int{1,7,63,256,1000}
    seeds:=[?]u64{0,7,max(u64)}
    state:State
    for algorithm in Algorithm {for seed in seeds {for count in lengths {
        bytes:=input[:count]
        expected:u64
        switch algorithm {
        case .XXH3_64:expected=xxhash.XXH3_64(bytes,seed)
        case .XXH64:expected=xxhash.XXH64(bytes,seed)
        case .FNV64a:expected=hashes.fnv64a(bytes,0xcbf29ce484222325~seed)
        }
        for chunk in chunks {
            state={};initialize(&state,algorithm,seed)
            for start:=0;start<count;start+=chunk {emit(&state,bytes[start:min(start+chunk,count)])}
            actual:=digest(&state)
            if actual!=expected {fmt.println("stream mismatch",algorithm,seed,count,chunk,actual,expected)}
            check(actual==expected,"streamed algorithm compatibility")
        }
    }}}
}

@(test)
xxh3_matches_independent_reference_vectors :: proc(t:^testing.T) {
    // Generated with the independent C xxHash 0.8.3 implementation using
    // XXH3_64bits_withSeed(bytes,n,seed); bytes[i]=(i*71+13)%256.
    lengths:=[?]int{0,1,16,17,128,240,241,256,257,512,1024,4097,8193}
    expected:=[?][2]u64{
        {3244421341483603138,10464923555624353464},{9953473643821969856,5537363548130527910},
        {2033886439536079094,5827847141914596897},{5898667862595129916,15001531913526218144},
        {11878113601518629991,3620216360877840050},{3732761695330538769,11549371823633391795},
        {7752600724941955109,2039321129981798936},{12878215139641646079,9783885646769628877},
        {5099820357841090122,10344360901250576499},{5912494355189686657,4342927048120945864},
        {4315693564039754760,7560987755819371117},{16828948687099672806,6018516976552419933},
        {13771552098318763704,5274876594732264464},
    }
    bytes:=make([]u8,8193);defer delete(bytes)
    for &v,i in bytes {v=u8((i*71+13)%256)}
    state:State
    for count,i in lengths {for seed,j in ([2]u64{0,7}) {
        actual:=hash(bytes[:count],.XXH3_64,seed)
        check(actual==expected[i][j],"reference one-shot vector")
        state={};initialize(&state,.XXH3_64,seed)
        for start:=0;start<count;start+=777 {emit(&state,bytes[start:min(start+777,count)])}
        streamed:=digest(&state)
        check(streamed==expected[i][j],"reference streaming vector")
    }}
}

@(test)
numbers_endianness_zero_and_distinct_types :: proc(t:^testing.T) {
    Count::distinct i64
    a:i64=-123456789;b:=i64be(a);c:=i64le(a);d:Count=Count(a)
    ha,hb,hc,hd:=hash(a),hash(b),hash(c),hash(d)
    check(ha==hb&&ha==hc&&ha==hd,"integer value/endianness")
    f:f64=1.25;g:=f64be(f);h:=f64le(f)
    hf,hg,hh:=hash(f),hash(g),hash(h)
    check(hf==hg&&hf==hh,"float endianness")
    positive:f64=0;negative:=transmute(f64)u64(1<<63)
    hp,hn:=hash(positive),hash(negative)
    check(hp==hn,"negative zero")
    cp,cn:=complex(positive,positive),complex(negative,negative)
    hcp,hcn:=hash(cp),hash(cn)
    check(hcp==hcn,"complex negative zero")
    qp:=quaternion(real=positive,imag=positive,jmag=positive,kmag=positive)
    qn:=quaternion(real=negative,imag=negative,jmag=negative,kmag=negative)
    hqp,hqn:=hash(qp),hash(qn)
    check(hqp==hqn,"quaternion negative zero")
    b1,b2:=bool(true),b32(true)
    h1,h2:=hash(b1),hash(b2)
    check(h1==h2,"boolean widths")
    for value in ([]u128{0,1,1<<100,max(u128)}) {_,err:=try_hash(value);check(err==.None,"u128")}
}

@(test)
matrices_vectors_bit_fields_and_encoded_strings :: proc(t:^testing.T) {
    column:=matrix[2,3]f64{1,2,3,4,5,6}
    row:#row_major matrix[2,3]f64={1,2,3,4,5,6}
    hc,hr:=hash(column),hash(row)
    check(hc==hr,"matrix logical layout")
    Bits::bit_field u32{a:u8|3,b:u16|5}
    a,b:Bits
    runtime.memset(&a,0x11,size_of(a));runtime.memset(&b,0x77,size_of(b))
    a.a,b.a=3,3;a.b,b.b=7,7
    ha,hb:=hash(a),hash(b);check(ha==hb,"unused bit-field bits")
    text:string16="hello"
    _,text_error:=try_hash(text);check(text_error==.None,"UTF16")
    plain:="hello";cstr:cstring="hello"
    hp,hcstring:=hash(plain),hash(cstr);check(hp==hcstring,"cstring contents")
    vector:=#simd[4]f32{1,2,3,4}
    _,vector_error:=try_hash(vector);check(vector_error==.None,"SIMD vector")
    Key::enum{A,B,C}
    flags:bit_set[Key;u32]={.A,.C}
    big_flags:bit_set[Key;u32be]={.A,.C}
    hflags,hbig:=hash(flags),hash(big_flags)
    check(hflags==hbig,"bit-set byte order")
}

@(test)
padding_alignment_and_nested_contents :: proc(t:^testing.T) {
    Record::struct{flag:u8,number:i64,text:string}
    a,b:Record
    runtime.memset(&a,0x11,size_of(a));runtime.memset(&b,0x77,size_of(b))
    a.flag,b.flag=1,1;a.number,b.number=17,17
    a.text="same";b.text=strings.clone(a.text);defer delete(b.text)
    ha,hb:=hash(a),hash(b)
    check(ha==hb,"padding/independent strings")
    Packed::struct #packed{prefix:u8,number:f64,text:string,values:[]i64}
    values:=[]i64{7,13}
    p,q:=Packed{1,0,a.text,values},Packed{1,transmute(f64)u64(1<<63),b.text,values}
    hp,hq:=hash(p),hash(q)
    check(hp==hq,"packed unaligned descriptors/scalars")
}

@(test)
sequence_headers_capacity_boundaries_and_shared_storage :: proc(t:^testing.T) {
    array:=[3]int{1,2,3}
    a,b:=make([dynamic]int,0,3),make([dynamic]int,0,100)
    defer delete(a);defer delete(b)
    append(&a,..array[:]);append(&b,..array[:])
    fixed:[dynamic;8]int;append(&fixed,..array[:])
    ha,hb,hc,hd,he:=hash(array),hash(array[:]),hash(a),hash(b),hash(fixed)
    check(ha==hb&&ha==hc&&ha==hd&&ha==he,"sequence capacities/container kinds")
    shared:=struct{first,second:[]int}{array[:],array[:]}
    copy:=[3]int{1,2,3}
    independent:=struct{first,second:[]int}{array[:],copy[:]}
    hs,hi:=hash(shared),hash(independent)
    check(hs==hi,"shared storage is not a cycle")
    x,y:=[]string{"ab","c"},[]string{"a","bc"}
    hx,hy:=hash(x),hash(y)
    check(hx!=hy,"string element boundaries")
    rows1,rows2:=[][]int{array[:1],array[1:]},[][]int{array[:2],array[2:]}
    hr1,hr2:=hash(rows1),hash(rows2)
    check(hr1!=hr2,"nested sequence boundaries")
    Empty::struct{}
    empty:[3]Empty
    _,err:=try_hash(empty)
    check(err==.None,"zero sized elements")
    Key::enum{A,B,C}
    indexed:=[Key]int{.A=1,.B=2,.C=3}
    he2:=hash(indexed);check(he2==ha,"enumerated array elements")
    Sparse::enum{A=0,B=2,C=4}
    sparse_a,sparse_b:#sparse [Sparse]int
    runtime.memset(&sparse_a,0x11,size_of(sparse_a));runtime.memset(&sparse_b,0x77,size_of(sparse_b))
    sparse_a[.A],sparse_a[.B],sparse_a[.C]=1,2,3
    sparse_b[.A],sparse_b[.B],sparse_b[.C]=1,2,3
    hsa,hsb:=hash(sparse_a),hash(sparse_b)
    check(hsa==hsb&&hsa==ha,"sparse enumerated arrays ignore gaps")
}

@(test)
pointer_cycles_any_values_and_actual_container_cycles :: proc(t:^testing.T) {
    Node::struct{value:int,next:^Node}
    node:=Node{value=7};node.next=&node
    _,pointer_error:=try_hash(node)
    check(pointer_error==.None,"pointer cycles stop at addresses")
    hp1,hp2:=hash(&node),hash(node.next)
    check(hp1==hp2,"pointer identity")
    Tree::struct{children:[]Tree}
    nodes:=make([]Tree,1);defer delete(nodes)
    nodes[0].children=nodes
    result,err:=try_hash(nodes[0])
    check(result==0&&err==.Cyclic_Data,"slice cycle")
    items:=make([]any,1);defer delete(items)
    items[0]=items
    _,any_error:=try_hash(items)
    check(any_error==.Cyclic_Data,"any/slice cycle")
    direct:any=13;record:=struct{value:any}{direct}
    _,ok_error:=try_hash(record)
    check(ok_error==.None,"acyclic boxed value")
    empty:any
    _,empty_error:=try_hash(empty)
    check(empty_error==.None,"nil any")
}

@(test)
tagged_unions_nil_and_inactive_storage :: proc(t:^testing.T) {
    Value::union{int,string}
    a,b:Value
    runtime.memset(&a,0x11,size_of(a));runtime.memset(&b,0x77,size_of(b))
    a=int(17);b=int(17)
    ha,hb:=hash(a),hash(b);check(ha==hb,"inactive union storage")
    a="17";hc:=hash(a);check(hc!=ha,"active variant")
    nil_value:Value
    _,err:=try_hash(nil_value);check(err==.None,"nil union")
    No_Nil::union #no_nil{int,string}
    definite:No_Nil=int(17)
    _,no_nil_error:=try_hash(definite);check(no_nil_error==.None,"no-nil union")
    Maybe::union{^int}
    absent:Maybe;number:=7;present:Maybe=&number
    hnil,hpresent:=hash(absent),hash(present)
    check(hnil!=hpresent,"pointer-only union")
}

@(test)
logical_numbers_ignore_ownership_storage_and_zero_representation :: proc(t:^testing.T) {
    a:=big.from_integer(u128(123456789)<<90);defer big.destroy(&a)
    b:=big.clone(a);defer big.destroy(&b)
    ha,hb:=hash(a),hash(b);check(ha==hb,"independent Integer storage")
    x:=big.fraction(2,4);defer big.destroy(&x)
    y:=big.fraction(-7,-14);defer big.destroy(&y)
    hx,hy:=hash(x),hash(y);check(hx==hy,"normalized Rational")
    zero:big.Rational
    z:=big.fraction(0,7);defer big.destroy(&z)
    hz0,hz1:=hash(zero),hash(z);check(hz0==hz1,"zero Rational")
    Data::struct{value:big.Integer,fraction:big.Rational}
    one,two:=Data{a,x},Data{b,y}
    h1,h2:=hash(one),hash(two);check(h1==h2,"nested logical values")
}

expect_unsupported :: proc($T:typeid) {
    value:T
    result,err:=try_hash(value)
    check(result==0&&err==.Unsupported_Type,"unsupported schema")
}
@(test)
opaque_resources_maps_and_inactive_unsupported_schemas :: proc(t:^testing.T) {
    expect_unsupported(os.File);expect_unsupported(net.Socket)
    expect_unsupported(sync.Mutex);expect_unsupported(sync.Futex)
    expect_unsupported(sync.Sema);expect_unsupported(runtime.Allocator)
    expect_unsupported(runtime.Random_Generator);expect_unsupported(proc())
    expect_unsupported(map[string]int)
    expect_unsupported(struct{values:[]struct{lock:sync.Mutex}})
    expect_unsupported(union{int,proc()})
    expect_unsupported(struct #raw_union{a:int,b:f64})
    // Raw addresses explicitly request identity, even for opaque pointees.
    file:os.File
    _,err:=try_hash(&file);check(err==.None,"explicit resource address")
}

@(test)
bounded_depth_invalid_algorithms_and_invalid_descriptors :: proc(t:^testing.T) {
    Tree::struct{children:[]Tree}
    nodes:=make([]Tree,140);defer delete(nodes)
    for &node,i in nodes[:139] {node.children=nodes[i+1:i+2]}
    _,depth_error:=try_hash(nodes[0]);check(depth_error==.Too_Deep,"nesting limit")
    _,algorithm_error:=try_hash(7,Algorithm(99));check(algorithm_error==.Invalid_Algorithm,"invalid algorithm")
    bad:=transmute([]int)runtime.Raw_Slice{nil,1}
    _,bad_error:=try_hash(bad);check(bad_error==.Invalid_Value,"invalid slice")
}

@(test)
successful_hashing_does_not_need_an_allocator :: proc(t:^testing.T) {
    data:=struct{values:[3]int,text:string}{[3]int{1,2,3},"text"}
    expected:=hash(data)
    context.allocator=mem.nil_allocator();context.temp_allocator=mem.nil_allocator()
    actual:=hash(data)
    check(actual==expected,"allocation-free hash")
    _,err:=try_hash(map[string]int{})
    check(err==.Unsupported_Type,"allocation-free error return")
}
