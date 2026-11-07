// Original comprehension expressions live in a synthetic native range scope.
package server

import "core:odin/ast"
import "core:strings"
import "core:fmt"

thor_comprehension :: proc(call:^ast.Call_Expr)->(body:^ast.Proc_Lit,loop:^ast.Range_Stmt,output:^ast.Return_Stmt,ok:bool) {
    if call==nil||len(call.args)==0{return}
    ident,found:=call.expr.derived.(^ast.Ident)
    if !found||ident.pos.offset!=ident.end.offset||!strings.has_prefix(ident.name,"__thor_comprehension"){return}
    body=call.args[0].derived.(^ast.Proc_Lit) or_return
    block:=body.body.derived.(^ast.Block_Stmt) or_return
    if len(block.stmts)!=1{return}
    loop=block.stmts[0].derived.(^ast.Range_Stmt) or_return
    block=loop.body.derived.(^ast.Block_Stmt) or_return
    if len(block.stmts)!=1{return}
    if conditional,found:=block.stmts[0].derived.(^ast.If_Stmt);found {
        block=conditional.body.derived.(^ast.Block_Stmt) or_return
    }
    if len(block.stmts)!=1{return}
    output=block.stmts[0].derived.(^ast.Return_Stmt) or_return
    ok=len(output.results)>0
    return
}

thor_comprehension_locals :: proc(ctx:^AstContext,loop:^ast.Range_Stmt,position:int) {
    // Resolving a source such as `for x in x` must see the enclosing x.
    if position_in_node(loop.expr,position){return}
    pc:=DocumentPositionContext{position=position}
    get_locals_range_vals(ctx.file,loop.vals,loop.expr,loop.body,ctx,&pc)
    // The output precedes its binding text, but belongs to that binding's scope.
    group:=get_local_group(ctx)
    for binding in loop.vals {
        value:=binding
        if typed,found:=value.derived.(^ast.Field_Value);found {value=typed.field}
        if name,found:=unwrap_ident(value);found {
            stack:=&group[name.name]
            if stack!=nil&&len(stack^)>0 {
                local:=&stack^[len(stack^)-1]
                local.offset=loop.pos.offset
                if local.type_expr==nil {
                    // Preserve a named element type (e.g. Item), rather than
                    // reconstructing the resolved record as an empty struct.
                    if _,range_expr:=local.rhs.derived.(^ast.Binary_Expr);!range_expr {local.type_expr=local.rhs}
                }
            }
        }
    }
}

thor_get_comprehension_locals :: proc(ctx:^AstContext,position:int) {
    State::struct{ctx:^AstContext,position:int}
    state:=State{ctx,position}
    visit::proc(visitor:^ast.Visitor,node:^ast.Node)->^ast.Visitor {
        if node==nil{return nil}
        state:=cast(^State)visitor.data
        if !position_in_node(node,state.position){return nil}
        if call,found:=node.derived.(^ast.Call_Expr);found {
            _,loop,_,comp:=thor_comprehension(call)
            if comp {thor_comprehension_locals(state.ctx,loop,state.position);return nil}
        }
        return visitor
    }
    visitor:=ast.Visitor{visit=visit,data=&state}
    for declaration in ctx.file.decls {ast.walk(&visitor,declaration)}
}

thor_comprehension_type :: proc(ctx:^AstContext,call:^ast.Call_Expr)->(Symbol,bool) {
    _,loop,returned,valid:=thor_comprehension(call)
    if !valid{return {},false}
    if len(call.args)>1 {return internal_resolve_type_expression_value(ctx,call.args[1])}
    add_local_group(ctx)
    defer pop_local_group(ctx)
    old:=ctx.non_mutable_only
    ctx.non_mutable_only=false
    defer ctx.non_mutable_only=old
    thor_comprehension_locals(ctx,loop,returned.pos.offset)
    expression:=returned.results[0]
    element,found:=thor_concatenation_value(ctx,expression)
    if !found{return {},false}
    marker:=call.expr.derived.(^ast.Ident)
    spread:=strings.contains(marker.name,"_unpack")
    if spread {
        #partial switch value in element.value {
        case SymbolSliceValue:element,found=thor_concatenation_value(ctx,value.expr)
        case SymbolFixedArrayValue:element,found=thor_concatenation_value(ctx,value.expr)
        case SymbolDynamicArrayValue:element,found=thor_concatenation_value(ctx,value.expr)
        case:return {},false
        }
    }
    if !found{return {},false}
    typ:=symbol_to_expr(element,call.pos.file,ctx.allocator)
    if literal,untyped:=element.value.(SymbolUntypedValue);untyped {
        name:="int"
        #partial switch literal.type {
        case .Float:name="f64"
        case .String:name="string"
        case .Bool:name="bool"
        case .Rune:name="rune"
        }
        typ=thor_ident(name,expression.pos)
    } else if element.type_expr!=nil {typ=element.type_expr}
    if typ==nil{return {},false}
    // Inferred fixed-array literals store an element as type_of(Item{...}).
    // Keep the nominal type in presentation and later member resolution.
    if query,is_query:=typ.derived.(^ast.Call_Expr);is_query&&len(query.args)==1 {
        if name,is_name:=query.expr.derived.(^ast.Ident);is_name&&name.name=="type_of" {
            if literal,is_literal:=query.args[0].derived.(^ast.Comp_Lit);is_literal&&literal.type!=nil {typ=literal.type}
        }
    }
    result:=element
    result.value=SymbolSliceValue{expr=typ}
    if strings.has_suffix(marker.name,"_constant") {
        size:="?"
        if count,known:=thor_comprehension_length(ctx,loop,spread);known {size=fmt.aprintf("%d",count)}
        length:=ast.new(ast.Basic_Lit,call.pos,call.pos)
        length.tok={kind=.Integer,text=size,pos=call.pos}
        result.value=SymbolFixedArrayValue{expr=typ,len=length}
    }
    result.type=.Variable
    result.type_name,result.type_pkg="",""
    result.pointers=0
    result.flags-={.Distinct}
    return result,true
}

// Avoid resetting the caller's recursion guard during a synthetic type query.
internal_resolve_type_expression_value :: proc(ctx:^AstContext,expr:^ast.Expr)->(Symbol,bool) {
    result:Symbol
    ok:=internal_resolve_type_expression(ctx,expr,&result)
    return result,ok
}

// Only bounded, side-effect-free static arithmetic is folded by the editor.
// Arbitrary eligible calls are executed by Thor, not by the language server.
thor_comprehension_length :: proc(ctx:^AstContext,loop:^ast.Range_Stmt,spread:bool)->(int,bool) {
    if spread||len(loop.vals)!=1{return 0,false}
    interval,valid:=ast.unparen_expr(loop.expr).derived.(^ast.Binary_Expr)
    if !valid||!(interval.op.kind==.Ellipsis||interval.op.kind==.Range_Half||interval.op.kind==.Range_Full){return 0,false}
    known:=make_when_expr_map()
    register_when_consts_from_globals(&known,ctx.globals)
    lo,lo_ok:=resolve_when_expr(known,interval.left)
    hi,hi_ok:=resolve_when_expr(known,interval.right)
    low,low_int:=lo.(int);high,high_int:=hi.(int)
    if !lo_ok||!hi_ok||!low_int||!high_int||low<0||high<low||high-low>4096||high==max(int){return 0,false}
    if interval.op.kind==.Range_Full {high+=1}
    block,block_ok:=loop.body.derived.(^ast.Block_Stmt)
    if !block_ok{return 0,false}
    condition:^ast.Expr
    if len(block.stmts)>0 {if filtered,found:=block.stmts[0].derived.(^ast.If_Stmt);found{condition=filtered.cond}}
    binding:=loop.vals[0]
    if typed,found:=binding.derived.(^ast.Field_Value);found{binding=typed.field}
    name,found:=unwrap_ident(binding)
    if !found{return 0,false}
    count:=0
    for i in low..<high {
        known[name.name]=i
        if condition!=nil {
            value,valid:=resolve_when_expr(known,condition)
            included,boolean:=value.(bool)
            if !valid||!boolean{return 0,false}
            if !included {continue}
        }
        count+=1
    }
    return count,true
}
