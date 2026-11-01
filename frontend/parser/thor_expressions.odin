package thor_parser

import "core:odin/ast"
import tokenizer "../tokenizer"

thor_for_condition_has_separator :: proc(p:^Parser)->bool {
    lexer:=p.tok
    lexer.err=nil
    token:=p.curr_tok
    depth:=0
    for {
        if token.kind==.EOF {return false}
        if depth==0 {
            if token.kind==.Semicolon {return token.text==";"}
            if token.kind==.Open_Brace || token.kind==.Do {return false}
        }
        #partial switch token.kind {
        case .Open_Paren,.Open_Bracket,.Open_Brace:depth+=1
        case .Close_Paren,.Close_Bracket,.Close_Brace:depth-=1
        }
        token=tokenizer.scan(&lexer)
    }
}

// Look ahead only through the arithmetic part of a membership RHS. Native in
// precedence stays unchanged unless an interval is actually present.
thor_membership_range_follows :: proc(p:^Parser)->bool {
    lexer:=p.tok
    lexer.err=nil
    token:=p.curr_tok
    depth:=0
    for {
        if token.kind==.EOF {return false}
        if depth==0 {
            #partial switch token.kind {
            case .Range_Half,.Range_Full,.Ellipsis:return true
            case .Cmp_And,.Cmp_Or,.Cmp_Eq,.Not_Eq,.Lt,.Gt,.Lt_Eq,.Gt_Eq,
                 .Question,.If,.When,.Or_Else,.In,.Not_In,.Comma,.Colon,
                 .Semicolon,.Close_Paren,.Close_Bracket,.Close_Brace,.Open_Brace:
                return false
            }
        }
        #partial switch token.kind {
        case .Open_Paren,.Open_Bracket,.Open_Brace:depth+=1
        case .Close_Paren,.Close_Bracket,.Close_Brace:depth-=1
        }
        token=tokenizer.scan(&lexer)
    }
}

thor_parse_membership_rhs :: proc(p:^Parser,prec:int)->^ast.Expr {
    if thor_membership_range_follows(p) {
        low:=parse_binary_expr(p,false,6,true)
        interval:=advance_token(p)
        high:=parse_binary_expr(p,false,6,true)
        range:=ast.new(ast.Binary_Expr,low.pos,high.end)
        range.left,range.op,range.right=low,interval,high
        return range
    }
    // Parenthesized intervals are also permitted as membership operands.
    previous:=p.allow_range
    defer p.allow_range=previous
    p.allow_range=true
    return parse_binary_expr(p,false,prec)
}

thor_signature_token :: proc(lexer:^tokenizer.Tokenizer)->tokenizer.Token {
    for {
        token:=tokenizer.scan(lexer)
        if token.kind!=.Comment && !(token.kind==.Semicolon && tokenizer.is_newline(token)) {return token}
    }
}

// Keep fn usable as an ordinary identifier/callee. A procedure signature must
// be followed by a return type, where clause or body, outside a control header.
thor_fn_follows :: proc(p:^Parser)->bool {
    if p.expr_level < 0 {return false}
    lexer:=p.tok
    lexer.insert_semicolon=false
    token:=thor_signature_token(&lexer)
    if token.kind==.String {token=thor_signature_token(&lexer)}
    if token.kind!=.Open_Paren{return false}
    depth:=1
    for depth>0 {
        token=thor_signature_token(&lexer)
        if token.kind==.EOF{return false}
        if token.kind==.Open_Paren{depth+=1}
        if token.kind==.Close_Paren{depth-=1}
    }
    token=thor_signature_token(&lexer)
    return token.kind==.Arrow_Right||token.kind==.Where||token.kind==.Open_Brace||token.kind==.Do
}
thor_exprs_run :: proc(expr:^ast.Expr)->[]^ast.Expr {
    args:=make([]^ast.Expr,1);args[0]=expr;return args
}

Feature_Case :: struct {
    start, end: int,
    matches: [][2]int,
    result: [2]int,
    statements: [][2]int,
}
Feature :: struct {
    kind: string,
    start, end: int,
    selector: [2]int,
    cases: []Feature_Case,
    parts: []tokenizer.Interpolation_Part,
    manual_lifetime: bool,
}
thor_span :: proc(node: ^ast.Node) -> [2]int {
    return [2]int{-1,-1} if node == nil else [2]int{node.pos.offset,node.end.offset}
}
thor_spans :: proc(nodes: []^ast.Expr) -> [][2]int {
    result := make([][2]int,len(nodes))
    for n,i in nodes { result[i] = thor_span(n) }
    return result
}
thor_call :: proc(name: string, start, end: tokenizer.Pos, args: []^ast.Expr) -> ^ast.Call_Expr {
    call := ast.new(ast.Call_Expr,start,end)
    ident := ast.new(ast.Ident,start,start)
    ident.name = name
    call.expr = ident
    call.args = args
    return call
}

parse_interpolation :: proc(p: ^Parser, token: tokenizer.Token) -> ^ast.Expr {
    lexer: tokenizer.Tokenizer
    tokenizer.init(&lexer,p.file.src,p.file.fullpath,p.err)
    lexer.thor = true
    tokenizer.seek(&lexer,token.pos.offset)
    _, parts := tokenizer.scan_interpolated(&lexer)
    args := make([dynamic]^ast.Expr)
    for part in parts {
        if part.literal { continue }
        nested := default_parser()
        nested.thor = true
        nested.file = p.file
        nested.err, nested.warn = p.err,p.warn
        nested.allow_in_expr = true
        tokenizer.init(&nested.tok,p.file.src,p.file.fullpath,p.err)
        nested.tok.thor = true
        tokenizer.seek(&nested.tok,part.start)
        advance_token(&nested)
        if nested.curr_tok.pos.offset >= part.end {
            error(p,nested.curr_tok.pos,"Interpolation field requires an expression")
            continue
        }
        expr := parse_expr(&nested,false)
        if nested.curr_tok.pos.offset != part.end {
            error(p,nested.curr_tok.pos,"Unexpected token in interpolation field; parenthesize a ternary before a format separator")
        }
        p.error_count += nested.error_count
        append(&p.edits,..nested.edits[:])
        append(&p.features,..nested.features[:])
        append(&args,expr)
    }
    call := thor_call("__thor_interpolation",token.pos,end_pos(token),args[:])
    append(&p.features,Feature{kind="Interpolation",start=call.pos.offset,end=call.end.offset,parts=parts,
        manual_lifetime=len(token.text)>1 && token.text[1]=='!'})
    return call
}

thor_set_type_follows :: proc(p: ^Parser) -> bool {
    lexer := p.tok
    if tokenizer.scan(&lexer).kind != .Open_Bracket { return false }
    if p.allow_type { return true }
    depth := 1
    for depth > 0 {
        token := tokenizer.scan(&lexer)
        if token.kind == .EOF { return false }
        if token.kind == .Open_Bracket { depth += 1 }
        if token.kind == .Close_Bracket { depth -= 1 }
    }
    return tokenizer.scan(&lexer).kind == .Open_Brace
}
parse_set_type :: proc(p: ^Parser) -> ^ast.Expr {
    start := advance_token(p)
    expect_token(p,.Open_Bracket)
    elem := parse_type(p)
    close := expect_token(p,.Close_Bracket)
    args := make([]^ast.Expr,1)
    args[0] = elem
    call := thor_call("__thor_set_type",start.pos,end_pos(close),args)
    append(&p.features,Feature{kind="SetType",start=call.pos.offset,end=call.end.offset})
    return call
}

parse_switch_expression :: proc(p: ^Parser) -> ^ast.Expr {
    token := advance_token(p)
    selector: ^ast.Expr
    if p.curr_tok.kind != .Open_Brace {
        old := p.expr_level
        p.expr_level = -1
        selector = parse_expr(p,false)
        p.expr_level = old
    }
    expect_token(p,.Open_Brace)
    cases := make([dynamic]Feature_Case)
    args := make([dynamic]^ast.Expr)
    results := make([dynamic]^ast.Expr)
    if selector != nil { append(&args,selector) }
    for p.curr_tok.kind == .Case {
        start := advance_token(p)
        matches: []^ast.Expr
        if p.curr_tok.kind != .Colon {
            old_range,old_in := p.allow_range,p.allow_in_expr
            p.allow_range,p.allow_in_expr = true,true
            matches = parse_rhs_expr_list(p)
            p.allow_range,p.allow_in_expr = old_range,old_in
        }
        expect_token(p,.Colon)
        old := p.expr_level
        p.expr_level = 0
        result: ^ast.Expr
        if p.curr_tok.kind == .Break {
            stop := advance_token(p)
            result = thor_call("__thor_switch_break",stop.pos,end_pos(stop),nil)
            append(&p.features,Feature{kind="SwitchBreak",start=result.pos.offset,end=result.end.offset})
        } else {
            values := parse_rhs_expr_list(p)
            if len(values) == 1 { result = values[0] } else {
                result = thor_call("__thor_switch_results",values[0].pos,values[len(values)-1].end,values)
                append(&p.features,Feature{kind="SwitchResults",start=result.pos.offset,end=result.end.offset})
            }
        }
        p.expr_level = old
        expect_semicolon(p,result)
        append(&args,..matches)
        append(&results,result)
        append(&cases,Feature_Case{start=start.pos.offset,end=result.end.offset,matches=thor_spans(matches),result=thor_span(result)})
        if p.curr_tok.kind != .Case && p.curr_tok.kind != .Close_Brace {
            error(p,p.curr_tok.pos,"A switch expression arm contains only result expressions or break")
            fix_advance_to_next_stmt(p)
            break
        }
    }
    close := expect_token(p,.Close_Brace)
    append(&results,..args[:])
    call := thor_call("__thor_switch_expression",token.pos,end_pos(close),results[:])
    kind := "CompileSwitchExpr" if token.text == "#switch" else "SwitchExpr"
    append(&p.features,Feature{kind=kind,start=call.pos.offset,end=call.end.offset,selector=thor_span(selector),cases=cases[:]})
    return call
}

parse_compile_switch :: proc(p: ^Parser) -> ^ast.Stmt {
    statement := parse_switch_stmt(p)
    if sw,ok := statement.derived.(^ast.Switch_Stmt); ok {
        if sw.init != nil { error(p,sw.init.pos,"#switch does not support an initializer") }
        cases := make([dynamic]Feature_Case)
        body := sw.body.derived.(^ast.Block_Stmt)
        for stmt in body.stmts {
            clause := stmt.derived.(^ast.Case_Clause) or_continue
            statements := make([][2]int,len(clause.body))
            for child,i in clause.body { statements[i] = thor_span(child) }
            append(&cases,Feature_Case{start=clause.pos.offset,end=clause.end.offset,matches=thor_spans(clause.list),statements=statements})
        }
        append(&p.features,Feature{kind="CompileSwitch",start=sw.pos.offset,end=sw.end.offset,selector=thor_span(sw.cond),cases=cases[:]})
    } else { error(p,statement.pos,"#switch does not support type switches") }
    return statement
}
