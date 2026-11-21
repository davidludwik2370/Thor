package server

import "core:fmt"
import "core:mem"
import "core:path/filepath"
import "core:strings"
import "src:common"

Thor_Vet_Option :: struct {
    name, description: string,
    native: bool,
}

// Native file policies are checked against the installed Odin inventory in live
// tests. Tag-only names are included; command-only vet-packages is not a tag.
thor_vet_options := []Thor_Vet_Option{
    {"no_alloc", "Prove allocation-free ordinary execution for every procedure and runtime initializer in this file, including reachable calls. Unknown effects are compile errors. Fatal panic/trap reporting is excluded; eager successful-path arguments remain checked.", false},
    {"unused", "Report unused local variables and imports. Uses Odin's native policy.", true},
    {"unused-variables", "Report unused local variables. Uses Odin's native policy.", true},
    {"unused-imports", "Report unused imports. Uses Odin's native policy.", true},
    {"unused-procedures", "Report unused private procedures. Uses Odin's native policy.", true},
    {"shadowing", "Report local declarations that shadow an existing name. Uses Odin's native policy.", true},
    {"using-stmt", "Require explicit opt-in for using statements. Uses Odin's native policy.", true},
    {"using-param", "Report using procedure parameters. Uses Odin's native policy.", true},
    {"style", "Check trailing commas and deprecated syntax. Uses Odin's native policy.", true},
    {"semicolon", "Report unnecessary semicolons. Uses Odin's native policy.", true},
    {"deprecated", "Report uses of deprecated declarations. Uses Odin's native policy.", true},
    {"cast", "Report redundant casts. Uses Odin's native policy.", true},
    {"tabs", "Require tabs for indentation. Uses Odin's native policy.", true},
    {"explicit-allocators", "Require allocator arguments to be supplied explicitly. Uses Odin's native policy.", true},
    {"when-shadowing", "Report declarations in global when blocks that shadow existing names. Uses Odin's native policy.", true},
    {"nil-deref", "Report statically detected nil pointer dereferences. Uses Odin's native policy.", true},
    {"uninitialized", "Report reads of uninitialized values. Uses Odin's native policy.", true},
    {"atomic-access", "Report non-atomic access to storage also accessed atomically. Uses Odin's native policy.", true},
}

thor_vet_separator :: proc(ch:u8)->bool {
    return ch==' ' || ch=='\t' || ch=='\r' || ch=='\n' || ch==','
}

thor_vet_tail :: proc(text:string)->(int,bool) {
    if !strings.has_prefix(text,"#+vet") {return 0,false}
    if len(text)>5 && (!thor_vet_separator(text[5]) || text[5]==',') {return 0,false}
    return 5,true
}

thor_vet_option_matches :: proc(name,prefix:string,native:bool)->bool {
    if strings.has_prefix(prefix,"!") {
        return native && strings.has_prefix(name,prefix[1:])
    }
    return strings.has_prefix(name,prefix)
}

// Recognize only bare headers and a final unfinished *supported* option. A
// completed unsupported option (or a typo) must keep its compiler diagnostic.
thor_vet_tag_incomplete :: proc(source:string)->bool {
    text:=strings.trim_space(source)
    start,ok:=thor_vet_tail(text)
    if !ok {return false}
    for start<len(text) {
        for start<len(text) && thor_vet_separator(text[start]) {start+=1}
        if start==len(text) {return true}
        end:=start
        for end<len(text) && !thor_vet_separator(text[end]) {end+=1}
        word:=text[start:end]
        complete,partial:bool
        for option in thor_vet_options {
            if word==option.name || option.native && strings.has_prefix(word,"!") && word[1:]==option.name {complete=true}
            if thor_vet_option_matches(option.name,word,option.native) {partial=true}
        }
        if !complete {return end==len(text) && partial}
        start=end
    }
    return len(text)==5
}

// Keep the immutable checker snapshot's header locations: the worker must not
// inspect the mutable document AST, nor suppress errors from another revision.
thor_pending_vet_lines :: proc(document:^Document,allocator:mem.Allocator)->[dynamic]int {
    if filepath.ext(document.fullpath)!=".thor" {return nil}
    lines:=make([dynamic]int,allocator)
    for tag in document.ast.tags {
        if thor_vet_tag_incomplete(tag.text) {append(&lines,tag.pos.line)}
    }
    return lines
}

get_thor_vet_completion :: proc(document:^Document,position:common.Position)->(CompletionList,bool) {
    if filepath.ext(document.fullpath)!=".thor" {return {},false}
    src:=document.text[:document.used_text]
    cursor,valid:=common.get_absolute_position(position,src)
    if !valid {return {},false}
    for tag in document.ast.tags {
        tail,ok:=thor_vet_tail(tag.text)
        if !ok || cursor<tag.pos.offset+tail || cursor>tag.pos.offset+len(tag.text) {continue}
        relative:=cursor-tag.pos.offset
        start,end:=relative,relative
        for start>tail && !thor_vet_separator(tag.text[start-1]) {start-=1}
        for end<len(tag.text) && !thor_vet_separator(tag.text[end]) {end+=1}
        prefix:=tag.text[start:relative]
        selected:=make(map[string]bool,context.temp_allocator)
        i:=tail
        for i<len(tag.text) {
            for i<len(tag.text) && thor_vet_separator(tag.text[i]) {i+=1}
            j:=i
            for j<len(tag.text) && !thor_vet_separator(tag.text[j]) {j+=1}
            if i!=j && !(i==start && j==end) {selected[strings.trim_prefix(tag.text[i:j],"!")]=true}
            i=j
        }
        items:=make([dynamic]CompletionItem,context.temp_allocator)
        range:=common.Range{
            start=common.get_relative_token_position(tag.pos.offset+start,src,0),
            end=common.get_relative_token_position(tag.pos.offset+end,src,0),
        }
        for option in thor_vet_options {
            if selected[option.name] || !thor_vet_option_matches(option.name,prefix,option.native) {continue}
            name:=option.name
            if strings.has_prefix(prefix,"!") {name=fmt.tprintf("!%s",name)}
            text:=name
            if start==tail {text=fmt.tprintf(" %s",text)}
            description:=option.description
            if option.name=="no_alloc" {description=attribute_docs["no_alloc"]}
            if strings.has_prefix(prefix,"!") {description=fmt.tprintf("Disable this native file vet policy.\n\n%s",description)}
            append(&items,CompletionItem{
                label=name,kind=.Keyword,detail="File vet policy",
                documentation=MarkupContent{kind="markdown",value=description},
                textEdit=TextEdit{range=range,newText=text},sortText=name,
            })
        }
        return {items=items[:],isIncomplete=true},true
    }
    return {},false
}
