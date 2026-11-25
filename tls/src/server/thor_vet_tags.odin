package server

import "core:fmt"
import "core:mem"
import "core:odin/ast"
import lexer "thor:tokenizer"
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

// These names acknowledge an analysis boundary. Definite lifetime/leak faults
// are deliberately absent: an acknowledgement never becomes a safety promise.
thor_vet_ignore_options := []Thor_Vet_Option{
    {"immutable", "Keep this declaration mutable; suppress its immutable advice and correction.", false},
    {"pure", "Keep this procedure as proc; suppress its fn advice and correction.", false},
    {"unused", "Suppress unused advice on this declaration or import.", false},
    {"semicolon", "Keep this declaration's optional semicolon.", false},
    {"lifetime_unknown", "Acknowledge unverified lifetime/retention effects rooted in this declaration. Definite dangling references, use-after-free and double free remain reported. This is manual assurance, not a compiler-verified lifetime contract.", false},
    {"leak_unknown", "Acknowledge unverified allocator/ownership cleanup rooted in this declaration. Definite leaks remain reported. No automatic cleanup or ownership transfer is added.", false},
}

get_thor_vet_ignore_completion :: proc(document:^Document,position:common.Position)->(CompletionList,bool) {
    if filepath.ext(document.fullpath)!=".thor" {return {},false}
    src:=document.text[:document.used_text]
    offset,ok:=common.get_absolute_position(position,src)
    if !ok {return {},false}
    State :: struct {offset:int,start,end:int,found,is_set:bool}
    state:=State{offset=int(offset)}
    visit :: proc(v:^ast.Visitor,node:^ast.Node)->^ast.Visitor {
        if node==nil {return v}
        state:=cast(^State)v.data
        attribute,ok:=node.derived.(^ast.Attribute)
        if !ok {return v}
        expr:=get_attribute_type([]^ast.Attribute{attribute},"vet_ignore")
        if expr==nil {return v}
        expr=ast.unparen_expr(expr)
        if literal,literal_ok:=expr.derived.(^ast.Basic_Lit);literal_ok && literal.tok.kind==.String {
            start,end:=literal.pos.offset+1,literal.end.offset-1
            if start<=state.offset && state.offset<=end {
                state.start=start
                state.end=end
                state.found=true
            }
        }else if set,set_ok:=expr.derived.(^ast.Comp_Lit);set_ok && set.type==nil {
            if set.open.offset<state.offset && state.offset<=set.end.offset {
                state.start=set.open.offset+1
                state.end=set.end.offset
                state.found=true
                state.is_set=true
            }
        }
        return v
    }
    visitor:=ast.Visitor{visit=visit,data=&state}
    for decl in document.ast.decls {ast.walk(&visitor,decl)}
    for declaration in document.ast.imports {ast.walk(&visitor,declaration)}
    if !state.found {
        if document.ast.syntax_error_count>0 && strings.contains(string(src),"vet_ignore") {
            return thor_vet_ignore_recovery_completion(document,int(offset))
        }
        return {},false
    }
    if state.is_set {return thor_vet_ignore_set_completion(document,state.start,int(offset))}
    start,end:=int(offset),int(offset)
    for start>state.start && src[start-1]!=',' {start-=1}
    for end<state.end && src[end]!=',' {end+=1}
    for start<int(offset) && (src[start]==' ' || src[start]=='\t') {start+=1}
    for end>int(offset) && (src[end-1]==' ' || src[end-1]=='\t') {end-=1}
    prefix:=string(src[start:int(offset)])
    selected:=make(map[string]bool,context.temp_allocator)
    segment:=state.start
    for i:=state.start;i<=state.end;i+=1 {
        if i==state.end || src[i]==',' {
            if i<=start || segment>=end && segment>int(offset) {
                name:=strings.trim_space(string(src[segment:i]))
                if name=="fn" {name="pure"}
                selected[name]=true
            }
            segment=i+1
        }
    }
    range:=common.Range{
        start=common.get_relative_token_position(start,src,0),
        end=common.get_relative_token_position(end,src,0),
    }
    items:=make([dynamic]CompletionItem,context.temp_allocator)
    for option in thor_vet_ignore_options {
        if selected[option.name] || !strings.has_prefix(option.name,prefix) {continue}
        append(&items,CompletionItem{
            label=option.name,kind=.Value,detail="Thor vet acknowledgement",
            documentation=MarkupContent{kind="markdown",value=option.description},
            textEdit=TextEdit{range=range,newText=option.name},sortText=option.name,
        })
    }
    return {items=items[:],isIncomplete=true},true
}

// Scan the original attribute value rather than depending on recovery spans for
// an unfinished implicit selector. Comments and strings cannot become rule names.
thor_vet_ignore_set_completion :: proc(document:^Document,first,cursor:int)->(CompletionList,bool) {
    src:=string(document.text[:document.used_text])
    scanner:lexer.Tokenizer
    lexer.init(&scanner,src,document.fullpath,common.parser_warning_handler)
    lexer.seek(&scanner,first)
    selected:=make(map[string]bool,context.temp_allocator)
    want_entry,period:=true,false
    active,needs_dot:bool
    word_start,word_end:=cursor,cursor
    previous_end:=first
    prefix:string
    for {
        token:=lexer.scan(&scanner)
        start,end:=token.pos.offset,token.pos.offset+len(token.text)
        if token.kind==.Comment {
            open_ended:=strings.has_prefix(token.text,"//") || !strings.has_suffix(token.text,"*/")
            if start<cursor && (cursor<end || cursor==end && open_ended) {return {},true}
            continue
        }
        if token.kind==.Close_Brace || token.kind==.EOF {
            if cursor>start {return {},false}
            if !active && want_entry && previous_end<=cursor && cursor<=start {
                active=true
                needs_dot=!period
            }
            break
        }
        if token.kind==.Period && want_entry && !period {
            period=true
        }else if token.kind==.Ident && want_entry && period {
            if start<=cursor && cursor<=end {
                active=true
                word_start,word_end=start,end
                prefix=src[start:cursor]
            }else{
                name:=token.text
                if name=="fn" {name="pure"}
                selected[name]=true
            }
            want_entry,period=false,false
        }else if token.kind==.Comma {
            if want_entry {
                if previous_end<=cursor && cursor<=start {
                    active=true
                    needs_dot=!period
                    word_start,word_end=cursor,cursor
                }else{return {},true}
            }
            want_entry,period=true,false
        }else{return {},true}
        previous_end=end
    }
    if !active {return {},true}
    range:=common.Range{
        start=common.get_relative_token_position(word_start,document.text[:document.used_text],0),
        end=common.get_relative_token_position(word_end,document.text[:document.used_text],0),
    }
    items:=make([dynamic]CompletionItem,context.temp_allocator)
    for option in thor_vet_ignore_options {
        if selected[option.name] || !strings.has_prefix(option.name,prefix) {continue}
        text:=option.name
        if needs_dot {text=fmt.tprintf(".%s",text)}
        append(&items,CompletionItem{
            label=option.name,kind=.EnumMember,detail="Thor vet rule",
            documentation=MarkupContent{kind="markdown",value=option.description},
            textEdit=TextEdit{range=range,newText=text},sortText=option.name,
        })
    }
    return {items=items[:],isIncomplete=true},true
}

// An empty `.` can make the native parser discard the surrounding declaration.
// In that editing state only, recover an actual @(..., vet_ignore={...}) token
// context. Never infer an attribute from text inside comments/strings or calls.
thor_vet_ignore_recovery_completion :: proc(document:^Document,cursor:int)->(CompletionList,bool) {
    src:=string(document.text[:document.used_text])
    scanner:lexer.Tokenizer
    lexer.init(&scanner,src,document.fullpath,common.parser_warning_handler)
    at_pending,want_key,rule_value_pending:bool
    parens,braces,brackets:int
    key:string
    for {
        token:=lexer.scan(&scanner)
        if token.kind==.EOF || token.pos.offset>cursor {break}
        if token.kind==.Comment {continue}
        if at_pending {
            at_pending=false
            if token.kind==.Open_Paren {parens,braces,brackets=1,0,0;want_key=true;key="";rule_value_pending=false}
            continue
        }
        if token.kind==.At {at_pending=true;continue}
        if parens==0 {continue}
        if rule_value_pending {
            if token.kind==.Open_Brace {
                if result,handled:=thor_vet_ignore_set_completion(document,token.pos.offset+1,cursor);handled {return result,true}
            }
            rule_value_pending=token.kind==.Open_Paren
        }
        top:=parens==1 && braces==0 && brackets==0
        if top {
            if token.kind==.Ident && want_key {key=token.text;want_key=false}
            else if token.kind==.Eq {rule_value_pending=key=="vet_ignore"}
            else if token.kind==.Comma {want_key=true;key=""}
        }
        #partial switch token.kind {
        case .Open_Paren:parens+=1
        case .Close_Paren:parens-=1
        case .Open_Brace:braces+=1
        case .Close_Brace:braces=max(0,braces-1)
        case .Open_Bracket:brackets+=1
        case .Close_Bracket:brackets=max(0,brackets-1)
        }
    }
    return {},false
}
