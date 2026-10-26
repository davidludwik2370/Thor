package tests

import "core:odin/ast"
import parser "thor:parser"
import "core:odin/tokenizer"
import "core:os"
import "core:testing"
import "src:server"
import test "src:testing"

thor_source :: proc(source: string) -> test.Source {
    files := make([]test.File, 1, context.temp_allocator)
    files[0] = {"main.thor", source}
    return {files=files}
}

@(test)
thor_hover_pure_function :: proc(t:^testing.T) {
    source:=thor_source(`package test
process::fn(value:int)->int{return value*value}
main::proc(){pro{*}cess(3)}
`)
    test.expect_hover_contains(t,&source,"fn(value: int) -> int")
}

@(test)
thor_hover_run_result :: proc(t:^testing.T) {
    source:=thor_source(`package test
square::fn(value:int)->int{return value*value}
main::proc(){result:=#run square(3);res{*}ult}
`)
    test.expect_hover_contains(t,&source,"result: int")
}

@(test)
thor_hover_automatic_result :: proc(t:^testing.T) {
    source:=thor_source(`package test
square::fn(value:int)->int{return value*value}
SIZE::square(3)
main::proc(){res{*}ult:=square(SIZE)}
`)
    test.expect_hover_contains(t,&source,"result: int")
}

@(test)
thor_hover_automatic_contract :: proc(t:^testing.T) {
    source:=thor_source(`package test
square::fn(value:int)->int{return value*value}
main::proc(){squ{*}are(3)}
`)
    test.expect_hover_contains(t,&source,"Known-argument calls")
}

@(test)
thor_automatic_record_completion :: proc(t:^testing.T) {
    source:=thor_source(`package test
Report::struct{value:int,title:string}
report::fn(n:int)->Report{return {n,"ok"}}
main::proc(){result:=report(3);result.{*}}
`)
    test.expect_completion_labels(t,&source,".",{"value","title"})
}

@(test)
thor_generic_dependent_run_inference :: proc(t:^testing.T) {
    source:=thor_source(`package test
square::fn(value:int)->int{return value*value}
work::proc($N:int)->int{SIZE::#run square(N);res{*}ult:=square(SIZE);return result}
main::proc(){_=work(3)}
`)
    test.expect_hover_contains(t,&source,"result: int")
}

@(test)
thor_hover_allocated_pure_result :: proc(t:^testing.T) {
    source:=thor_source(`package test
names::fn()->[]string{return nil}
main::proc(){result:=#run names();res{*}ult}
`)
    test.expect_hover_contains(t,&source,"result: []string")
}

@(test)
thor_hover_pure_ownership_contract :: proc(t:^testing.T) {
    source:=thor_source(`package test
names::fn()->[]string{return nil}
main::proc(){na{*}mes()}
`)
    test.expect_hover_contains(t,&source,"pure.destroy")
}

@(test)
thor_allocated_record_completion :: proc(t:^testing.T) {
    source:=thor_source(`package test
Report::struct{title:string,rows:[]string}
report::fn()->Report{return {}}
main::proc(){result:=#run report();result.{*}}
`)
    test.expect_completion_labels(t,&source,".",{"title","rows"})
}

@(test)
thor_pure_generic_completion :: proc(t:^testing.T) {
    source:=thor_source(`package test
Record::struct{value:int}
identity::fn(value:$T)->T{return value}
main::proc(){result:=#run identity(Record{7});result.{*}}
`)
    test.expect_completion_labels(t,&source,".",{"value"})
}

@(test)
thor_parser_native_pointer_and_set_xor :: proc(t: ^testing.T) {
    context.allocator = context.temp_allocator
    source := `package test
identity::proc(value:int)->int{return value}
last::proc "c"(a:[]int,i:int)->int{return a[i]}
main::proc(){
 value:=1;p:=&value;p^=3;p^~=1
 callable:=identity;pointer:=&callable;_=pointer^(4)
 a:=set[int]{1};b:=set[int]{2};a~=b;_=a~(b)&a
 outer:for item in set[int]{1,2}{_=item;break outer}
 _=f"{(true ? f"inner {7}" : "borrowed")}"
}
`
    file := ast.new(ast.File, tokenizer.Pos{}, tokenizer.Pos{})
    file.src=source;file.fullpath="main.thor"
    p:=parser.default_parser()
    testing.expect(t,server.parse_file(&p,file))
    testing.expect_value(t,file.syntax_error_count,0)
}

@(test)
thor_hover_set_native_xor :: proc(t: ^testing.T) {
    source:=thor_source(`package test
main::proc(){a:=set[int]{1};b:=set[int]{2};result:=a~b;res{*}ult}
`)
    // The native test harness supplies indexed packages in memory; the stdio
    // integration tests index the real runtime collection from disk.
    source.collections=make(map[string]string)
    source.collections["thor"]="test"
    source.packages={{pkg="sets",source=`package sets
Set::struct($T:typeid){table:map[T]struct{}}
`}}
    test.expect_hover_contains(t,&source,"set[int]")
}

@(test)
thor_hover_interpolated_string :: proc(t:^testing.T){
    source:=thor_source(`package test
main::proc(){name:="world";message:=f"hello {name}";mess{*}age}
`)
    test.expect_hover_contains(t,&source,"message: string")
}

@(test)
thor_hover_runtime_switch_expression :: proc(t:^testing.T){
    source:=thor_source(`package test
main::proc(){choice:=switch 1{case 1:2;case:3};cho{*}ice}
`)
    test.expect_hover_contains(t,&source,"choice: int")
}

@(test)
thor_hover_compile_switch_selected_expression :: proc(t:^testing.T){
    source:=thor_source(`package test
VALUE::#switch 2{case 1:"wrong";case 2:7;case:false}
main::proc(){VAL{*}UE}
`)
    test.expect_hover_contains(t,&source,":: 7")
}

@(test)
thor_hover_compile_switch_declaration :: proc(t:^testing.T){
    source:=thor_source(`package test
#switch 2{case 1:VALUE::"wrong";case 2:VALUE::7;case:VALUE::false}
main::proc(){VAL{*}UE}
`)
    test.expect_hover_contains(t,&source,":: 7")
}

@(test)
thor_hover_negative_array_index :: proc(t:^testing.T){
    source:=thor_source(`package test
main::proc(){data:=[1,2];last:=data[-1];la{*}st}
`)
    test.expect_hover_contains(t,&source,"last: int")
}

@(test)
thor_parser_main_demo :: proc(t: ^testing.T) {
    context.allocator = context.temp_allocator
    data, err := os.read_entire_file("../demo/main.thor", context.temp_allocator)
    testing.expect(t, err == nil)
    file := ast.new(ast.File, tokenizer.Pos{}, tokenizer.Pos{})
    file.src = string(data)
    file.fullpath = "main.thor"
    p := parser.default_parser()
    testing.expect(t, server.parse_file(&p, file))
    testing.expect_value(t, file.syntax_error_count, 0)
    testing.expect_value(t, len(p.selections), 3)
}

@(test)
thor_hover_inferred_array :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { values := [1,2,3]; value{*}s }
`)
    test.expect_hover_contains(t, &source, "[3]int")
}

@(test)
thor_hover_immutable_runtime_storage :: proc(t: ^testing.T) {
    source := thor_source(`package test
make_value :: proc()->int {return 7}
main :: proc(){ value :> make_value(); val{*}ue }
`)
    test.expect_hover_contains(t, &source, "immutable storage")
}

@(test)
thor_hover_typed_immutable_array :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc(){ values: [2]int > [1,2]; val{*}ues }
`)
    test.expect_hover_contains(t, &source, "[2]int")
}

@(test)
thor_immutable_shadowing_is_scoped :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc(){ value :> 1; {value := "mutable"; val{*}ue} }
`)
    test.expect_hover_contains(t, &source, "value: string")
}

@(test)
thor_hover_typed_array :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { values: [2]f64 = [1,2]; value{*}s }
`)
    test.expect_hover_contains(t, &source, "[2]f64")
}

@(test)
thor_hover_nested_array :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { values := [[1,2],[3,4]]; value{*}s }
`)
    test.expect_hover_contains(t, &source, "[2][2]int")
}

@(test)
thor_hover_array_element :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { values := [1,2]; first := values[0]; fir{*}st }
`)
    test.expect_hover_contains(t, &source, "first: int")
}

@(test)
thor_hover_membership :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { found := 2 in [1,2]; fou{*}nd }
`)
    test.expect_hover_contains(t, &source, "found: bool")
}

@(test)
thor_hover_range_membership :: proc(t:^testing.T){
    source:=thor_source(`package test
allowed::proc($N:int)->int where N in 0..<10 {return N}
main::proc(){value:=3;found:=value in 0..=9;fou{*}nd}
`)
    test.expect_hover_contains(t,&source,"found: bool")
}

@(test)
thor_if_range_membership_condition :: proc(t:^testing.T){
    source:=thor_source(`package test
#if -2 in (-3..=0) && 5 not_in 0..<5 {VALUE::42} #else {VALUE::false}
main::proc(){VAL{*}UE}
`)
    test.expect_hover_contains(t,&source,"42")
}

@(test)
thor_hover_not_in :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() { missing := 3 not_in [1,2]; miss{*}ing }
`)
    test.expect_hover_contains(t, &source, "missing: bool")
}

@(test)
thor_hover_multiline_ternary :: proc(t: ^testing.T) {
    source := thor_source(`package test
main :: proc() {
    chosen := true ? 1
                   : 2
    cho{*}sen
}
`)
    test.expect_hover_contains(t, &source, "chosen: int")
}

@(test)
thor_hover_selected_procedure :: proc(t: ^testing.T) {
    source := thor_source(`package test
import "library" / selected
main :: proc() { sele{*}cted(2) }
`)
    source.packages = {{pkg="library", source=`package library
selected :: proc(value: int) -> int { return value }
`}}
    test.expect_hover_contains(t, &source, "proc(value: int) -> int")
}

@(test)
thor_hover_selected_type :: proc(t: ^testing.T) {
    source := thor_source(`package test
import "library" / Record
main :: proc() { r: Rec{*}ord }
`)
    source.packages = {{pkg="library", source="package library\nRecord :: struct {value:int}\n"}}
    test.expect_hover_contains(t, &source, "struct")
}

@(test)
thor_selected_import_shadowing :: proc(t: ^testing.T) {
    source := thor_source(`package test
import "library" / selected
main :: proc() { selected := 42; sele{*}cted }
`)
    source.packages = {{pkg="library", source="package library\nselected :: proc() {}\n"}}
    test.expect_hover_contains(t, &source, "selected: int")
}

@(test)
thor_selected_import_completion :: proc(t: ^testing.T) {
    source := thor_source(`package test
import "library" /
    selected,
    Record
main :: proc() { sel{*} }
`)
    source.packages = {{pkg="library", source="package library\nselected :: proc() {}\nRecord :: struct {}\n"}}
    test.expect_completion_labels(t, &source, "", {"selected"})
}

@(test)
thor_selected_type_completion :: proc(t: ^testing.T) {
    source := thor_source("package test\nimport \"library\" / Record\nmain :: proc() { Rec{*} }\n")
    source.packages = {{pkg="library", source="package library\nRecord :: struct {}\n"}}
    test.expect_completion_labels(t, &source, "", {"Record"})
}

@(test)
thor_selected_import_file_privacy :: proc(t: ^testing.T) {
    source := thor_source("package test\nmain :: proc() { {*} }\n")
    source.files = {{"main.thor", source.files[0].source},
                    {"other.thor", "package test\nimport \"library\" / selected\n"}}
    source.packages = {{pkg="library", source="package library\nselected :: proc() {}\n"}}
    test.expect_completion_labels(t, &source, "", {"main"}, {"selected"})
}

@(test)
thor_if_integer_condition :: proc(t: ^testing.T) {
    source := thor_source(`package test
MODE :: 2
#if MODE == 1 { VALUE :: "wrong" } #else if MODE == 2 { VALUE :: 42 } #else { VALUE :: false }
main :: proc() { VAL{*}UE }
`)
    test.expect_hover_contains(t, &source, "42")
}

@(test)
thor_if_membership_condition :: proc(t: ^testing.T) {
    source := thor_source(`package test
#if 2 in [1,2] { VALUE :: 42 } #else { VALUE :: false }
main :: proc() { VAL{*}UE }
`)
    test.expect_hover_contains(t, &source, "42")
}

@(test)
thor_if_config_default :: proc(t: ^testing.T) {
    source := thor_source(`package test
MODE :: #config(MODE, 2)
#if MODE == 2 { VALUE :: 42 } #else { VALUE :: false }
main :: proc() { VAL{*}UE }
`)
    test.expect_hover_contains(t, &source, "42")
}

@(test)
thor_generic_constraint_signature :: proc(t: ^testing.T) {
    source := thor_source(`package test
numeric :: proc(x: $T) -> T where T in [int, f64] { return x }
main :: proc() { num{*}eric(2) }
`)
    test.expect_hover_contains(t, &source, "proc(x: $T) -> T")
}

@(test)
thor_incomplete_array_retains_other_symbols :: proc(t: ^testing.T) {
    source := thor_source(`package test
Record :: struct {value:int}
main :: proc() { r: Record; r.{*}
unfinished := [1,
`)
    test.expect_completion_labels(t, &source, ".", {"value"})
}
