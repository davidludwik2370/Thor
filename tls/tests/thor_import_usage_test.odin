package tests

import "core:testing"
import "core:fmt"
import test "src:testing"

import_usage_source :: proc(body:string)->test.Source {
    files:=make([]test.File,1,context.temp_allocator)
    files[0]={"main.thor",body}
    packages:=make([]test.Package,1,context.temp_allocator)
    packages[0]={pkg="library",source=`package library
small::proc(value:f32)->f32{return value}
large::proc(value:f64)->f64{return value}
chosen::proc{small,large}
other::proc(chosen:int){}
KEY::7
unused::proc(){}
`}
    return {
        files=files,packages=packages,
    }
}

@(test)
thor_import_usage_does_not_require_a_resolved_overload :: proc(t:^testing.T) {
    for body in ([]string{
        "use::proc(n:f32){value:>chosen(n);_=value}",
        "use::proc(n:$T){value:>chosen(n);_=value}",
        "use::proc(n:string){value:>chosen(n);_=value}",
        "use::proc(){alias::chosen;_=alias}",
    }) {
        source:=import_usage_source(fmt.tprintf("package test\nimport \"library\" / chosen, unused\n%s",body))
        test.expect_import_usage(t,&source,0,{"unused"})
    }
}

@(test)
thor_import_usage_distinguishes_bindings_keys_and_members :: proc(t:^testing.T) {
    for build in ([]string{
        "build::proc()->map[int]int{return map[int]int{KEY=7}}",
        "build::proc($T:typeid)->T{return T{KEY=7}}",
    }) {
        source:=import_usage_source(fmt.tprintf("%s\n%s",`package test
import "library" / chosen, other, KEY
main::proc(){
    other(chosen=7)
    record:=struct{chosen:int}{chosen=7}
    _=record.chosen
}`,build))
        test.expect_import_usage(t,&source,0,{"chosen"})
    }
}

@(test)
thor_import_usage_qualified_access_and_local_alias_do_not_use_selection :: proc(t:^testing.T) {
    source:=import_usage_source(`package test
import "library" / chosen, other
main::proc(){
    chosen:=library.chosen
    _=chosen(f32(1))
    library.other(7)
}
`)
    test.expect_import_usage(t,&source,0,{"chosen","other"})
}
