package tests

import "core:testing"
import test "src:testing"

@(test)
thor_or_panic_value_hover :: proc(t:^testing.T) {
    source:=thor_source(`package test
get::proc()->(int,bool){return 7,true}
main::proc(){value:=get() or_panic;val{*}ue}
`)
    test.expect_hover_contains(t,&source,"value: int")
}

@(test)
thor_or_panic_multiple_value_hover :: proc(t:^testing.T) {
    source:=thor_source(`package test
get::proc()->(int,string,bool){return 7,"hello",true}
main::proc(){first,second:=get() or_panic;sec{*}ond}
`)
    test.expect_hover_contains(t,&source,"second: string")
}

@(test)
thor_or_panic_keyword_hover :: proc(t:^testing.T) {
    source:=thor_source(`package test
get::proc()->(int,bool){return 7,true}
main::proc(){value:=get() or_pa{*}nic}
`)
    test.expect_hover_contains(t,&source,"Unwraps an optional result")
}

@(test)
native_or_panic_identifier_keeps_ordinary_hover :: proc(t:^testing.T) {
    source:=test.Source{main=`package test
main::proc(){or_panic:=7;or_pa{*}nic}
`}
    test.expect_hover_contains(t,&source,"or_panic: int")
}
