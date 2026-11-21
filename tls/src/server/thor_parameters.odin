// Parser-only parameter contracts retained on TLS fields, never emitted source.
package server
import "core:odin/ast"
import "core:strings"
import parser "thor:parser"

THOR_PARAMETER_TAG :: "__thor_tls_parameters:"

thor_parameter_code :: proc(field:^ast.Field,index:int)->u8 {
    if field==nil || !strings.has_prefix(field.tag.text,THOR_PARAMETER_TAG) {return '-'}
    at:=len(THOR_PARAMETER_TAG)+index
    if at<0 || at>=len(field.tag.text) {return '-'}
    return field.tag.text[at]
}
thor_parameter_auto :: proc(field:^ast.Field)->bool {return thor_parameter_code(field,0)=='a'}

thor_mark_parameters :: proc(p:^parser.Parser,file:^ast.File) {
    if len(p.ro_parameters)==0 && len(p.auto_parameters)==0 {return}
    State::struct{codes:map[int]u8}
    state:=State{codes=make(map[int]u8)}
    for row in p.ro_parameters {state.codes[row[0]]='r'}
    for row in p.auto_parameters {state.codes[row[0]]='a'}
    visit::proc(visitor:^ast.Visitor,node:^ast.Node)->^ast.Visitor {
        if node==nil {return visitor}
        field,ok:=node.derived.(^ast.Field)
        if !ok {return visitor}
        state:=cast(^State)visitor.data
        builder:=strings.builder_make()
        strings.write_string(&builder,THOR_PARAMETER_TAG)
        marked:=false
        for name in field.names {
            code,found:=state.codes[name.pos.offset]
            if !found {code='-'}else{marked=true}
            strings.write_byte(&builder,code)
        }
        if marked {field.tag.text=strings.to_string(builder)}else{strings.builder_destroy(&builder)}
        return visitor
    }
    visitor:=ast.Visitor{visit=visit,data=&state}
    for declaration in file.decls {ast.walk(&visitor,declaration)}
    delete(state.codes)
}
