package tests

import "core:testing"
import test "src:testing"

@(test)
thor_concatenation_hover_combined_length :: proc(t:^testing.T) {
    source:=thor_source(`package test
A::[1,2]
B::[3]
main::proc(){result:=A ++ B ++ [4];res{*}ult}
`)
    test.expect_hover_contains(t,&source,"result: [4]int")
}

@(test)
thor_concatenation_hover_empty_leading_literal :: proc(t:^testing.T) {
    source:=thor_source(`package test
A::[1,2]
main::proc(){result:=[] ++ A;res{*}ult}
`)
    test.expect_hover_contains(t,&source,"result: [2]int")
}

@(test)
thor_concatenation_hover_slices :: proc(t:^testing.T) {
    source:=thor_source(`package test
A::[]int{1}
B::[]int{2}
main::proc(){result:=A ++ B;res{*}ult}
`)
    test.expect_hover_contains(t,&source,"result: []int")
}
