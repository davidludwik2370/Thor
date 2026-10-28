#+test
package thor_sets

import "core:testing"
import "core:mem"
import "core:strings"

@(test)
sequence_conversions_keep_independent_containers :: proc(t: ^testing.T) {
    data := [4]int{3, 1, 3, 2}
    fixed := to_set(data)
    sliced := to_set(data[:])
    heap_values := make([dynamic]int)
    defer delete(heap_values)
    append(&heap_values, 3, 1, 3, 2)
    growing := to_set(heap_values)
    defer destroy(fixed); defer destroy(sliced); defer destroy(growing)
    testing.expect(t, count(fixed)==3 && equal(fixed,sliced) && equal(fixed,growing))
    data[0]=99; heap_values[0]=99
    testing.expect(t, contains(fixed,3) && !contains(fixed,99) && !contains(growing,99))
    values := to_slice(fixed)
    defer delete(values)
    testing.expect_value(t,len(values),3)
    round_trip := to_set(values)
    defer destroy(round_trip)
    testing.expect(t,equal(round_trip,fixed))
    values[0]=77
    clear_set(&fixed)
    testing.expect(t,count(fixed)==0 && count(round_trip)==3 && !contains(round_trip,77))
}

@(test)
empty_distinct_and_enum_indexed_sequences :: proc(t: ^testing.T) {
    zero: [0]int
    nil_slice: []int
    nil_dynamic: [dynamic]int
    for value in ([3]Set(int){to_set(zero),to_set(nil_slice),to_set(nil_dynamic)}) {
        testing.expect_value(t,count(value),0)
        copy := to_slice(value)
        testing.expect_value(t,len(copy),0)
        delete(copy); destroy(value)
    }
    Fixed :: distinct [3]int
    Slice :: distinct []int
    Dynamic :: distinct [dynamic]int
    data := Fixed{4,4,8}
    view := Slice(data[:])
    growing := make(Dynamic)
    defer delete(growing)
    append(&growing,4,8,4)
    a:=to_set(data); b:=to_set(view); c:=to_set(growing)
    defer destroy(a); defer destroy(b); defer destroy(c)
    testing.expect(t,count(a)==2 && equal(a,b) && equal(a,c))
    Key :: enum{First,Second}
    indexed := [Key]int{.First=9,.Second=9}
    keys := to_set(indexed)
    defer destroy(keys)
    testing.expect(t,count(keys)==1 && contains(keys,9))
}

@(test)
conversion_allocators_are_explicit_and_survive_empty_inputs :: proc(t: ^testing.T) {
    tracker: mem.Tracking_Allocator
    mem.tracking_allocator_init(&tracker,context.allocator,context.allocator)
    defer mem.tracking_allocator_destroy(&tracker)
    allocator := mem.tracking_allocator(&tracker)
    // Later insertion must retain the requested allocator, even when conversion
    // starts empty and context.allocator refers to a different allocator.
    empty := to_set([]int{},allocator)
    insert(&empty,42)
    testing.expect(t,len(tracker.allocation_map)>0)
    values := to_slice(empty,allocator)
    testing.expect(t,len(values)==1 && values[0]==42)
    destroy(empty)
    delete(values,allocator)
    testing.expect_value(t,len(tracker.allocation_map),0)
    testing.expect_value(t,len(tracker.bad_free_array),0)
    saved := context.allocator
    context.allocator = allocator
    implicit := to_set([2]int{1,2})
    implicit_values := to_slice(implicit)
    context.allocator = saved
    testing.expect(t,len(tracker.allocation_map)>0)
    destroy(implicit); delete(implicit_values,allocator)
    testing.expect_value(t,len(tracker.allocation_map),0)
    testing.expect_value(t,len(tracker.bad_free_array),0)
}

@(test)
conversion_elements_remain_shallow :: proc(t: ^testing.T) {
    text := strings.clone("shared key")
    defer delete(text)
    words := to_set([2]string{text,text})
    defer destroy(words)
    copied := to_slice(words)
    defer delete(copied)
    testing.expect(t,len(copied)==1 && raw_data(copied[0])==raw_data(text))
    number := 3
    pointers := to_set([2]^int{&number,&number})
    defer destroy(pointers)
    addresses := to_slice(pointers)
    defer delete(addresses)
    testing.expect(t,len(addresses)==1 && addresses[0]==&number)
    addresses[0]^=7
    testing.expect_value(t,number,7)
}

@(private="file")
ints :: proc(values: ..int) -> Set(int) {
    result: Set(int)
    for value in values { insert(&result, value) }
    return result
}

@(test)
zero_duplicates_remove_iteration_clear :: proc(t: ^testing.T) {
    s: Set(string)
    testing.expect_value(t, count(s), 0)
    remove(&s, "absent")
    insert(&s,"apple"); insert(&s,"apple"); insert(&s,"pear")
    testing.expect_value(t, count(s), 2)
    testing.expect(t, contains(s,"apple"))
    visited := 0
    for key in entries(s) { testing.expect(t, key == "apple" || key == "pear"); visited += 1 }
    testing.expect_value(t, visited, 2)
    remove(&s,"absent"); remove(&s,"apple")
    testing.expect_value(t, count(s), 1)
    clear_set(&s)
    testing.expect_value(t, count(s), 0)
    insert(&s,"again")
    testing.expect(t, contains(s,"again"))
    destroy(s)
}

@(test)
mathematical_operations_and_independent_tables :: proc(t: ^testing.T) {
    a := ints(1,2,3); b := ints(3,4)
    defer destroy(a); defer destroy(b)
    u := union_of(a,b); i := intersection(a,b); d := difference(a,b); x := symmetric_difference(a,b)
    defer destroy(u); defer destroy(i); defer destroy(d); defer destroy(x)
    expected_u := ints(1,2,3,4); expected_i := ints(3); expected_d := ints(1,2); expected_x := ints(1,2,4)
    defer destroy(expected_u); defer destroy(expected_i); defer destroy(expected_d); defer destroy(expected_x)
    testing.expect(t, equal(u,expected_u) && equal(i,expected_i) && equal(d,expected_d) && equal(x,expected_x))
    remove(&u,1); insert(&u,99)
    testing.expect(t, contains(a,1) && !contains(a,99))
    c := clone(a)
    testing.expect(t, equal(c,a))
    clear_set(&c)
    testing.expect_value(t, count(a), 3)
    destroy(c)
}

@(test)
self_and_aliased_operations :: proc(t: ^testing.T) {
    for operation in 0..<4 {
        s := ints(1,2,3)
        alias := s
        switch operation {
        case 0: union_assign(&s,alias)
        case 1: intersection_assign(&s,alias)
        case 2: difference_assign(&s,alias)
        case 3: symmetric_difference_assign(&s,alias)
        }
        testing.expect_value(t, count(s), operation < 2 ? 3 : 0)
        destroy(s)
    }
    a := ints(1,2,3); b := ints(2,4)
    intersection_assign(&a,b)
    testing.expect(t, count(a) == 1 && contains(a,2))
    symmetric_difference_assign(&a,b)
    testing.expect(t, count(a) == 1 && contains(a,4))
    subtract_assign(&a,4)
    testing.expect_value(t,count(a),0)
    union_assign(&a,b)
    subtract_assign(&a,b)
    testing.expect_value(t,count(a),0)
    destroy(a); destroy(b)
}
