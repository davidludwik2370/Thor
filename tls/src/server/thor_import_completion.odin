package server

import "core:odin/ast"
import "core:path/filepath"
import "core:strings"
import lexer "thor:tokenizer"
import "src:common"

ThorImportCompletionContext :: struct {
    declaration: ^ast.Import_Decl,
    prefix: string,
    selected: map[string]bool,
    blocked: bool,
}

// The AST may recover past an empty name or trailing comma. Scan only the
// original import tail so completion also works before the list is valid.
thor_import_completion_context :: proc(document:^Document,cursor:int)->(ThorImportCompletionContext,bool) {
    if filepath.ext(document.fullpath)!=".thor" {return {},false}
    src:=string(document.text[:document.used_text])
    for declaration in document.ast.imports {
        if declaration.end.offset<=declaration.pos.offset || declaration.end.offset>cursor || declaration.is_using {continue}
        scanner:lexer.Tokenizer
        lexer.init(&scanner,src,document.fullpath,common.parser_warning_handler)
        scanner.thor=true
        scanner.flags={.Insert_Semicolon}
        lexer.seek(&scanner,declaration.end.offset)
        scanner.insert_semicolon=true // The preceding token is the quoted path.
        selected:=make(map[string]bool,context.temp_allocator)
        active,slash,want_name:bool
        prefix:string
        for {
            token:=lexer.scan(&scanner)
            start,end:=token.pos.offset,token.pos.offset+len(token.text)
            if token.kind==.Comment {
                open_ended:=strings.has_prefix(token.text,"//") || !strings.has_suffix(token.text,"*/")
                if slash && start<cursor && (cursor<end || cursor==end && open_ended) {
                    return {blocked=true},true
                }
                continue
            }
            if !slash {
                if token.kind!=.Quo || end>cursor {break}
                slash,want_name=true,true
                continue
            }
            if !active && want_name && cursor<=start {active=true}
            if want_name && token.kind==.Ident {
                if start<=cursor && cursor<=end {
                    active=true
                    prefix=src[start:cursor]
                } else {selected[token.text]=true}
                want_name=false
            } else if !want_name && token.kind==.Comma {
                want_name=true
            } else {break}
        }
        if active {return {declaration=declaration,prefix=prefix,selected=selected},true}
    }
    return {},false
}

get_thor_import_completion :: proc(document:^Document,position:common.Position,config:^common.Config)->(CompletionList,bool) {
    if filepath.ext(document.fullpath)!=".thor" {return {},false}
    cursor,valid:=common.get_absolute_position(position,document.text[:document.used_text])
    if !valid {return {},false}
    selection,selected:=thor_import_completion_context(document,cursor)
    if !selected {return {},false}
    if selection.blocked {return {},true}
    package_name:string
    for pkg in document.imports {
        if pkg.import_decl==selection.declaration {package_name=pkg.name;break}
    }
    if package_name=="" {return {},true}
    ast_context:=make_ast_context(document.ast,document.imports,document.package_name,document.uri.uri,document.fullpath)
    ast_context.current_package=package_name
    position_context:=DocumentPositionContext{
        file=document.ast,position=cursor,hint=.Completion,import_stmt=selection.declaration,
    }
    results:=make([dynamic]CompletionResult,context.temp_allocator)
    incomplete:=get_package_member_completion(&ast_context,package_name,selection.prefix,&results)
    count:=0
    for result in results {
        if !selection.selected[result.symbol.name] {results[count]=result;count+=1}
    }
    items:=convert_completion_results(&ast_context,&position_context,results[:count],.Import_Selection,nil,config)
    add_completion_text_edits(items,&position_context,.Import_Selection,position,config)
    return {items=items,isIncomplete=incomplete},true
}
