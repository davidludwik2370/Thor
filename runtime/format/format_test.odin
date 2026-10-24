#+test
package thor_format

import "core:testing"

@(test)
decimal_grouping :: proc(t: ^testing.T) {
    cases := []struct{value:i64, expected:string}{
        {0,"0"}, {1,"1"}, {-1,"-1"}, {999,"999"}, {1000,"1,000"},
        {-1234567,"-1,234,567"}, {-9223372036854775808,"-9,223,372,036,854,775,808"},
        {9223372036854775807,"9,223,372,036,854,775,807"},
    }
    for c in cases {
        actual := group_integer(c.value)
        testing.expect_value(t, actual, c.expected)
        delete(actual)
    }
    big := group_integer(u128(340282366920938463463374607431768211455))
    testing.expect_value(t, big, "340,282,366,920,938,463,463,374,607,431,768,211,455")
    delete(big)
    small := group_integer(i128(-170141183460469231731687303715884105728))
    testing.expect_value(t, small, "-170,141,183,460,469,231,731,687,303,715,884,105,728")
    delete(small)
}

@(test)
alignment_and_runes :: proc(t: ^testing.T) {
    a := format(-1234, grouping=true, width=10)
    b := format("é😀", width=5)
    c := format("x", width=6, align=.Center, fill='界')
    d := format(12, width=5, fill='0')
    e := format("already wider", width=2)
    f := format("", width=3, align=.Right, fill='😀')
    testing.expect_value(t, a, "    -1,234")
    testing.expect_value(t, b, "é😀   ")
    testing.expect_value(t, c, "界界x界界界")
    testing.expect_value(t, d, "00012")
    testing.expect_value(t, e, "already wider")
    testing.expect_value(t, f, "😀😀😀")
    delete(a); delete(b); delete(c); delete(d); delete(e); delete(f)
}

@(test)
independent_results_and_borrowed_input :: proc(t: ^testing.T) {
    input := "borrowed"
    a := format(input)
    b := format(input)
    testing.expect(t, raw_data(a) != raw_data(b))
    testing.expect_value(t, input, "borrowed")
    delete(a)
    testing.expect_value(t, b, "borrowed")
    delete(b)
}
