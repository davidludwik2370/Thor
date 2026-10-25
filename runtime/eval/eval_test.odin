#+test
package thor_eval

import "core:strings"
import "core:testing"

@(test)
contents_and_descriptor_protocol_releases_builder_storage :: proc(t:^testing.T) {
    builder:strings.Builder
    strings.builder_init(&builder)
    defer strings.builder_destroy(&builder)
    Record::struct{text:string,values:[2]i128}
    write(&builder,Record{"\x00\xff\"\\",{-(i128(1)<<126),17}},0)
    text:=strings.to_string(builder)
    testing.expect(t,strings.contains(text,`"bytes":"00ff225c"`))
    testing.expect(t,strings.contains(text,"-85070591730234615865843651857942052864"))
    strings.builder_reset(&builder)
    data:=[1]int{7}
    write(&builder,data[:0],0)
    testing.expect(t,strings.contains(strings.to_string(builder),`"nil":false`))
    strings.builder_reset(&builder)
    empty:[]int
    write(&builder,empty,0)
    testing.expect(t,strings.contains(strings.to_string(builder),`"nil":true`))
    strings.builder_reset(&builder)
    Choice::union{int,string}
    write(&builder,Choice("\x00\xff"),0)
    testing.expect(t,strings.contains(strings.to_string(builder),`"bytes":"00ff"`))
}
