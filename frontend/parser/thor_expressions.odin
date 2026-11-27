package thor_parser

import "core:odin/ast"
import tokenizer "../tokenizer"

// A typed range header is separate from a three-clause for initializer.
// Keep annotations as Field_Value nodes so the type retains its original AST
// and source positions without extending Odin's shared AST definitions.
thor_typed_for_follows :: proc(p:^Parser)->bool {
    lexer:=p.tok
    lexer.err=nil
    token:=p.curr_tok
    depth:=0
    annotated:=false
    for {
        if token.kind==.EOF{return false}
        if depth==0 {
            if token.kind==.In{return annotated}
            if token.kind==.Eq || token.kind==.Semicolon || token.kind==.Open_Brace || token.kind==.Do{return false}
        }
        if token.kind==.Colon{annotated=true}
        #partial switch token.kind {
        case .Open_Paren,.Open_Bracket:depth+=1
        case .Close_Paren,.Close_Bracket:depth-=1
        }
        token=tokenizer.scan(&lexer)
    }
}

thor_parse_typed_for_header :: proc(p:^Parser)->^ast.Stmt {
    start:=p.curr_tok.pos
    names:=make([dynamic]^ast.Expr)
    for {
        binding_start:=p.curr_tok.pos
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
            if len(names)>0 && !grouped {
                error(p,name.pos,"An index type annotation must be parenthesized: for value, (index: T) in collection")
            }
            annotation:=parse_type(p)
            binding:=ast.new(ast.Field_Value,binding_start,annotation.end)
            binding.field,binding.value=name,annotation
            name=binding
        }else if grouped{
            error(p,name.pos,"A parenthesized loop binding requires a type: (index: T)")
        }
        if grouped {
            close:=expect_token(p,.Close_Paren)
            name.end=end_pos(close)
        }
        append(&names,name)
        if !allow_token(p,.Comma){break}
    }
    in_token:=expect_token(p,.In)
    previous:=p.allow_range
    p.allow_range=true
    expr:=parse_expr(p,false)
    p.allow_range=previous
    assignment:=ast.new(ast.Assign_Stmt,start,expr.end)
    assignment.op,assignment.lhs,assignment.rhs=in_token,names[:],thor_exprs_run(expr)
    return assignment
}

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
    if p.expr_level < 0 && !p.allow_type {return false}
    lexer:=p.tok
    lexer.insert_semicolon=false
    token:=thor_signature_token(&lexer)
    if token.kind==.String {token=thor_signature_token(&lexer)}
    if token.kind!=.Open_Paren{return false}
    if p.allow_type && p.expr_level < 0 {return true}
    depth:=1
    for depth>0 {
        token=thor_signature_token(&lexer)
        if token.kind==.EOF{return false}
        if token.kind==.Open_Paren{depth+=1}
        if token.kind==.Close_Paren{depth-=1}
    }
    token=thor_signature_token(&lexer)
    return token.kind==.Arrow_Right||token.kind==.Where||token.kind==.Open_Brace||token.kind==.Do||token.kind==.Undef
}
thor_exprs_run :: proc(expr:^ast.Expr)->[]^ast.Expr {
    args:=make([]^ast.Expr,1);args[0]=expr;return args
}

// Only a value-returning fn's own body has a tail value. Nested statement
// blocks keep their normal control-flow rules, and void fn calls stay statements.
thor_fn_has_results :: proc(p:^Parser)->bool {
    if !p.thor || p.curr_proc==nil {return false}
    signature,ok:=p.curr_proc.derived.(^ast.Proc_Type)
    return ok && signature.tok.text=="fn" && signature.results!=nil && len(signature.results.list)>0
}

thor_fn_expression_start :: proc(p:^Parser)->bool {
    if !thor_fn_has_results(p) {return false}
    #partial switch p.curr_tok.kind {
    case .Open_Bracket,.Period,.Cast,.Transmute,.Auto_Cast,.Mul_Mul,
         .Typeid,.Distinct,.Struct,.Union,.Enum,.Map,.Undef:
        return true
    case .Hash:
        name:=peek_token(p).text
        switch name {
        case "run","runtime","type","config","defined","file","directory","line",
             "procedure","caller_location","branch_location","caller_expression",
             "location","exists","load","load_directory","load_hash","hash",
             "row_major","column_major","soa","simd":return true
        }
    }
    return false
}

thor_fn_literal_at_tail :: proc(p:^Parser)->bool {
    if !thor_fn_has_results(p) {return false}
    lexer:=p.tok
    token:=p.curr_tok
    for token.kind==.Semicolon || token.kind==.Comment {token=tokenizer.scan(&lexer)}
    return token.kind==.Close_Brace
}

// A failed expression attempt can grow a recording buffer and free its old
// backing allocation. Rewind its length while retaining the current allocation;
// restoring a shallow Parser snapshot alone would resurrect dangling buffers.
thor_fn_rewind_list :: proc(current:[dynamic]$T,count:int)->[dynamic]T {
    list:=current
    resize(&list,count)
    return list
}

thor_fn_rewind :: proc(p:^Parser,previous:Parser,previous_file:ast.File) {
    restored:=previous
    restored.fn_statement_switches=p.fn_statement_switches
    restored.optional_semicolons=thor_fn_rewind_list(p.optional_semicolons,len(previous.optional_semicolons))
    restored.edits=thor_fn_rewind_list(p.edits,len(previous.edits))
    restored.selections=thor_fn_rewind_list(p.selections,len(previous.selections))
    restored.immutables=thor_fn_rewind_list(p.immutables,len(previous.immutables))
    restored.scoped=thor_fn_rewind_list(p.scoped,len(previous.scoped))
    restored.mut_parameters=thor_fn_rewind_list(p.mut_parameters,len(previous.mut_parameters))
    restored.ro_parameters=thor_fn_rewind_list(p.ro_parameters,len(previous.ro_parameters))
    restored.auto_parameters=thor_fn_rewind_list(p.auto_parameters,len(previous.auto_parameters))
    restored.features=thor_fn_rewind_list(p.features,len(previous.features))
    restored.declaration_separators=thor_fn_rewind_list(p.declaration_separators,len(previous.declaration_separators))
    file:=previous_file
    file.tags=thor_fn_rewind_list(p.file.tags,len(previous_file.tags))
    file.decls=thor_fn_rewind_list(p.file.decls,len(previous_file.decls))
    file.imports=thor_fn_rewind_list(p.file.imports,len(previous_file.imports))
    file.comments=thor_fn_rewind_list(p.file.comments,len(previous_file.comments))
    p^=restored
    p.file^=file
}

// Try only the ambiguous expression starters, at the fn's own body level.
// Successful tails keep the parsed tree and recorded features: no second parse
// or new lowering path. Statement switches (including explicit returns, labels,
// initializers and type switches) fall back to the ordinary statement parser.
thor_fn_tail_expression :: proc(p:^Parser)->^ast.Expr {
    if !thor_fn_has_results(p) {return nil}
    if p.curr_tok.kind!=.Switch &&
       !(p.curr_tok.kind==.Hash && peek_token(p).text=="partial") {return nil}
    // An enclosing failed attempt can revisit nested callback bodies. Cache
    // statement forms so this cannot multiply speculative work exponentially.
    mode:=(1 if p.allow_range else 0)+(2 if p.allow_in_expr else 0)
    key:=[2]int{p.curr_tok.pos.offset,mode}
    if p.fn_statement_switches[key] {return nil}
    previous,previous_file:=p^,p.file^
    p.err,p.warn,p.tok.err=nil,nil,nil
    expr:=parse_expr(p,false)
    at_tail:=thor_fn_literal_at_tail(p)
    lexical_ok:=p.tok.error_count==previous.tok.error_count
    warnings_ok:=p.file.syntax_warning_count==previous_file.syntax_warning_count
    errors:=p.error_count-previous.error_count
    incomplete:=p.incomplete_selectors-previous.incomplete_selectors
    if at_tail && lexical_ok && warnings_ok && errors==0 {
        p.err,p.warn,p.tok.err=previous.err,previous.warn,previous.tok.err
        return expr
    }
    thor_fn_rewind(p,previous,previous_file)
    if at_tail && lexical_ok && warnings_ok && errors>0 && errors==incomplete {
        // Keep contextual completion for an unfinished enum result, and report
        // its original diagnostics exactly once using the normal handlers.
        return parse_expr(p,false)
    }
    if p.fn_statement_switches==nil {p.fn_statement_switches=make(map[[2]int]bool)}
    p.fn_statement_switches[key]=true
    return nil
}

thor_fn_implicit_return :: proc(p:^Parser,expr:^ast.Expr) {
    literal,ok:=expr.derived.(^ast.Proc_Lit)
    if !ok || literal.body==nil || literal.type.results==nil || len(literal.type.results.list)==0 {return}
    body,block:=literal.body.derived.(^ast.Block_Stmt)
    if !block || len(body.stmts)==0 {return}
    index:=len(body.stmts)-1
    tail,expression:=body.stmts[index].derived.(^ast.Expr_Stmt)
    if !expression || tail.expr==nil {return}
    returned:=ast.new(ast.Return_Stmt,tail.pos,tail.end)
    returned.results=thor_exprs_run(tail.expr)
    returned.state_flags=tail.state_flags
    body.stmts[index]=returned
    // Preserve original positions for TLS and diagnostics. The native emitter
    // supplies the absent keyword only when it copies this return's source.
    append(&p.features,Feature{kind="ImplicitReturn",start=returned.pos.offset,end=returned.end.offset})
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
    partial: bool,
    container_type, loop, output, key, condition: [2]int,
    spread: bool,
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

// Only an or_else operand can introduce a fallback list. Ordinary grouping
// and commas belonging to an outer assignment/call keep native precedence.
thor_parse_fallback_paren :: proc(p:^Parser)->^ast.Expr {
    open:=expect_token(p,.Open_Paren)
    previous:=p.expr_level
    p.expr_level=max(previous,0)+1
    p.fallback_paren=-1
    if p.curr_tok.kind==.Open_Paren {p.fallback_paren=p.curr_tok.pos.offset}
    first:=parse_expr(p,false)
    p.fallback_paren=-1
    values:=make([dynamic]^ast.Expr)
    append(&values,first)
    multiple:=false
    for allow_token(p,.Comma) {
        multiple=true
        skip_possible_newline(p)
        if p.curr_tok.kind==.Close_Paren {break}
        append(&values,parse_expr(p,false))
    }
    p.expr_level=previous
    close:=expect_token(p,.Close_Paren)
    if multiple {
        result:=thor_call("__thor_fallback_list",open.pos,end_pos(close),values[:])
        append(&p.features,Feature{kind="FallbackList",start=result.pos.offset,end=result.end.offset})
        return result
    }
    grouped:=ast.new(ast.Paren_Expr,open.pos,end_pos(close))
    grouped.open,grouped.close,grouped.expr=open.pos,close.pos,first
    return grouped
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
        spans:=make([dynamic][2]int)
        append(&spans,[2]int{part.start,part.end})
        append(&spans,..part.format_exprs)
        for span,span_index in spans {
            nested := default_parser(p.flags)
            nested.thor = true
            nested.source_tools = p.source_tools
            nested.file = p.file
            nested.err, nested.warn = p.err,p.warn
            nested.allow_in_expr = true
            tokenizer.init(&nested.tok,p.file.src,p.file.fullpath,p.err)
            nested.tok.thor = true
            tokenizer.seek(&nested.tok,span[0])
            advance_token(&nested)
            if nested.curr_tok.pos.offset >= span[1] {
                error(p,nested.curr_tok.pos,"Interpolation width requires an expression" if span_index>0 else "Interpolation field requires an expression")
                continue
            }
            expr := parse_expr(&nested,false)
            if nested.curr_tok.pos.offset != span[1] {
                error(p,nested.curr_tok.pos,"Unexpected token in interpolation width expression" if span_index>0 else "Unexpected token in interpolation field; parenthesize a ternary before a format separator")
            }
            p.error_count += nested.error_count
            append(&p.edits,..nested.edits[:])
            append(&p.optional_semicolons,..nested.optional_semicolons[:])
            append(&p.features,..nested.features[:])
            p.case_comparisons = p.case_comparisons || nested.case_comparisons
            append(&args,expr)
        }
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

// Comparison prefixes are case syntax, never general unary operators.
thor_case_comparison :: proc(kind: tokenizer.Token_Kind) -> bool {
    return kind == .Lt || kind == .Lt_Eq || kind == .Gt || kind == .Gt_Eq || kind == .Not_Eq
}
thor_case_list :: proc(p: ^Parser, selector: bool = true, type_switch: bool = false) -> []^ast.Expr {
    list := make([dynamic]^ast.Expr)
    for {
        value: ^ast.Expr
        if thor_case_comparison(p.curr_tok.kind) {
            op := advance_token(p)
            p.case_comparisons = true
            if !selector || type_switch {
                error(p, op.pos, "Comparison case prefixes require a value switch selector")
            }
            operand := parse_expr(p, false)
            comparison := ast.new(ast.Unary_Expr, op.pos, operand)
            comparison.op, comparison.expr = op, operand
            value = comparison
        } else if p.curr_tok.kind == .Cmp_Eq {
            op := advance_token(p)
            error(p, op.pos, "Use 'case value:' for equality; 'case == value:' is not supported")
            value = parse_expr(p, false)
        } else { value = parse_expr(p, false) }
        append(&list, value)
        if !allow_token(p, .Comma) { break }
    }
    return list[:]
}

parse_switch_expression :: proc(p: ^Parser, partial: tokenizer.Token = {}) -> ^ast.Expr {
    token := advance_token(p)
    start := partial.pos if partial.kind == .Hash else token.pos
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
            matches = thor_case_list(p, selector != nil)
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
    call := thor_call("__thor_switch_expression",start,end_pos(close),results[:])
    kind := "CompileSwitchExpr" if token.text == "#switch" else "SwitchExpr"
    append(&p.features,Feature{kind=kind,start=call.pos.offset,end=call.end.offset,selector=thor_span(selector),cases=cases[:],partial=partial.kind==.Hash})
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
