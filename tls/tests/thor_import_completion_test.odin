package tests

import "core:testing"
import test "src:testing"

import_completion_source :: proc(source:string)->test.Source {
    files:=make([]test.File,1,context.temp_allocator)
    files[0]={"main.thor",source}
    packages:=make([]test.Package,1,context.temp_allocator)
    packages[0]={pkg="library",source=`package library
// Alpha accepts a number.
alpha::proc(value:int)->int{return value}
alpine::proc(){}
alias::alpha
Record::struct{value:int}
ANSWER::42
@(private) hidden::proc(){}
@(private="file") file_hidden::proc(){}
`}
    return {
        files=files,packages=packages,
    }
}

@(test)
thor_import_completion_empty_and_comma_lists :: proc(t:^testing.T) {
    for source in ([]string{
        "package test\nimport \"library\" /{*}\nmain::proc(){}\n",
        "package test\nimport lib \"library\" / {*}",
        "package test\nimport _ \"library\" / alpha, // continue\n {*}",
    }) {
        src:=import_completion_source(source)
        test.expect_completion_labels(t,&src,"/",{"alpine","alias","Record","ANSWER"},{"hidden","file_hidden","main","proc"})
    }
}

@(test)
thor_import_completion_filters_selected_names_and_prefix :: proc(t:^testing.T) {
    src:=import_completion_source("package test\nimport \"library\" / alpha, al{*}, Record\nmain::proc(){}\n")
    test.expect_completion_labels(t,&src,",",{"alpine","alias"},{"alpha","Record","ANSWER"})
}

@(test)
thor_import_completion_procedures_insert_names_without_calls :: proc(t:^testing.T) {
    src:=import_completion_source("package test\nimport \"library\" / alp{*}\n")
    src.config.enable_snippets=true
    src.config.enable_procedure_snippet=true
    test.expect_completion_edit_text(t,&src,"/","alpha","alpha")
}

@(test)
thor_import_completion_keeps_comments_and_runtime_commas_quiet :: proc(t:^testing.T) {
    for source in ([]string{
        "package test\nimport \"library\" / alpha // , {*}",
        "package test\nimport \"library\" / /* , {*} */",
        "package test\nimport \"library\"\nmain::proc(){alpha(1, {*})}",
    }) {
        src:=import_completion_source(source)
        test.expect_completion_labels(t,&src,",",nil)
    }
    for source in ([]string{
        "package test\nimport \"library\" / alpha {*}\nmain::proc(){}",
        "package test\nimport \"library\"\nmain::proc(){value:=1; {*}}",
    }) {
        src:=import_completion_source(source)
        test.expect_completion_labels(t,&src," ",nil)
    }
}
