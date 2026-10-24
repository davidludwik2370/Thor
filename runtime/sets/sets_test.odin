#+test
package thor_sets

import "core:testing"

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
