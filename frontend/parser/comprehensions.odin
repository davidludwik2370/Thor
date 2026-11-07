// Comprehensions use an Odin-compatible synthetic scope, keeping every user
// expression/identifier at its original byte position for the frontend and TLS.
package thor_parser

import "core:odin/ast"
import tokenizer "../tokenizer"

thor_comprehension_follows :: proc(p:^Parser,close:tokenizer.Token_Kind)->bool {
    lexer:=p.tok
    lexer.err=nil
    token:=p.curr_tok
    depth:=0
    for {
        if token.kind==.EOF {return false}
        if depth==0 {
            if token.kind==close {return false}
            if token.kind==.For {return true}
        }
        #partial switch token.kind {
        case .Open_Paren,.Open_Bracket,.Open_Brace:depth+=1
        case .Close_Paren,.Close_Bracket,.Close_Brace:
            if depth==0 {return false}
            depth-=1
        }
        token=tokenizer.scan(&lexer)
    }
}

parse_comprehension :: proc(p:^Parser,open:tokenizer.Token,type:^ast.Expr,closing:tokenizer.Token_Kind)->^ast.Expr {
    if p.comprehension_depth>0 {error(p,open.pos,"Nested comprehensions are not supported; bind the inner result separately")}
    p.comprehension_depth+=1
    defer p.comprehension_depth-=1
    previous_level:=p.expr_level
    p.expr_level=0
    defer p.expr_level=previous_level
    spread:=allow_token(p,.Ellipsis)
    if p.curr_tok.kind==.Mul_Mul {
        error(p,p.curr_tok.pos,"Use '..' to unpack a collection in a comprehension")
        advance_token(p)
        spread=true
    }
    output:=parse_expr(p,false)
    key:^ast.Expr
    if allow_token(p,.Eq) {key=output;output=parse_expr(p,false)}
    if p.curr_tok.kind==.Comma {
        error(p,p.curr_tok.pos,"A comprehension has one output expression; maps use 'key = value'")
        for p.curr_tok.kind!=.For && p.curr_tok.kind!=closing && p.curr_tok.kind!=.EOF {advance_token(p)}
    }
    skip_possible_newline(p)
    for_token:=expect_token(p,.For)
    names:=make([dynamic]^ast.Expr)
    for {
        start:=p.curr_tok.pos
        grouped:=allow_token(p,.Open_Paren)
        reference:=p.curr_tok
        by_reference:=allow_token(p,.And)
        name:^ast.Expr=parse_ident(p)
        if by_reference {
            unary:=ast.new(ast.Unary_Expr,reference.pos,name.end)
            unary.op,unary.expr=reference,name
            name=unary
        }
        if allow_token(p,.Colon) {
            if len(names)>0 && !grouped {error(p,start,"An index type annotation must be parenthesized: (index: T)")}
            annotation:=parse_type(p)
            field:=ast.new(ast.Field_Value,start,annotation.end)
            field.field,field.value=name,annotation
            name=field
        }
        if grouped {expect_token(p,.Close_Paren)}
        append(&names,name)
        if !allow_token(p,.Comma) {break}
    }
    if len(names)>2 {error(p,for_token.pos,"A comprehension supports at most two iteration bindings")}
    in_token:=expect_token(p,.In)
    old_range,old_in:=p.allow_range,p.allow_in_expr
    old_filter,old_filter_level:=p.comprehension_filter,p.comprehension_level
    p.allow_range,p.allow_in_expr=true,true
    p.comprehension_filter,p.comprehension_level=true,p.expr_level
    source:=parse_expr(p,false)
    skip_possible_newline(p)
    condition:^ast.Expr
    if allow_token(p,.If) {condition=parse_expr(p,false)}
    p.allow_range,p.allow_in_expr=old_range,old_in
    p.comprehension_filter,p.comprehension_level=old_filter,old_filter_level
    skip_possible_newline(p)
    if p.curr_tok.kind==.For || p.curr_tok.kind==.If {
        error(p,p.curr_tok.pos,"A comprehension supports one 'for' and one optional 'if'; combine conditions with '&&'")
        for p.curr_tok.kind!=closing && p.curr_tok.kind!=.EOF {advance_token(p)}
    }
    close:=expect_token(p,closing)
    start:=type.pos if type!=nil else open.pos
    stop:=end_pos(close)
    returned:=ast.new(ast.Return_Stmt,output.pos,end_pos(for_token))
    returned.results=thor_exprs_run(output)
    if key!=nil {returned.results=make([]^ast.Expr,2);returned.results[0],returned.results[1]=key,output}
    body:=ast.new(ast.Block_Stmt,open.pos,stop)
    body.stmts=make([]^ast.Stmt,1)
    body.stmts[0]=returned
    if condition!=nil {
        conditional:=ast.new(ast.If_Stmt,open.pos,stop)
        conditional.cond,conditional.body=condition,body
        wrapper:=ast.new(ast.Block_Stmt,open.pos,stop)
        wrapper.stmts=make([]^ast.Stmt,1);wrapper.stmts[0]=conditional
        body=wrapper
    }
    loop:=ast.new(ast.Range_Stmt,open.pos,stop)
    loop.for_pos,loop.in_pos=for_token.pos,in_token.pos
    loop.vals,loop.expr,loop.body=names[:],source,body
    block:=ast.new(ast.Block_Stmt,open.pos,stop)
    block.stmts=make([]^ast.Stmt,1);block.stmts[0]=loop
    signature:=ast.new(ast.Proc_Type,open.pos,open.pos)
    signature.tok={kind=.Proc,text="__thor_comprehension_body",pos=open.pos}
    signature.params=ast.new(ast.Field_List,open.pos,open.pos)
    procedure:=ast.new(ast.Proc_Lit,open.pos,stop)
    procedure.type,procedure.body=signature,block
    args:=thor_exprs_run(procedure)
    if type!=nil {args=make([]^ast.Expr,2);args[0],args[1]=procedure,type}
    helper:="__thor_comprehension_unpack" if spread else "__thor_comprehension"
    call:=thor_call(helper,start,stop,args)
    append(&p.features,Feature{kind="ComprehensionBody",start=procedure.pos.offset,end=procedure.end.offset})
    append(&p.features,Feature{kind="Comprehension",start=call.pos.offset,end=call.end.offset,
        container_type=thor_span(type),loop=thor_span(loop),output=thor_span(output),key=thor_span(key),
        condition=thor_span(condition),selector=thor_span(source),spread=spread})
    return call
}
