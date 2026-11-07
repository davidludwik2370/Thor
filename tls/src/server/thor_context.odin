// Follow expected types down original expression nodes. Thor switch adapters
// are not procedure calls: their result arms inherit the surrounding type,
// while selectors, conditions and real call arguments have their own context.
package server

import "core:odin/ast"
import "core:strconv"
import "core:strings"

// OLS resolution can leave local lookup disabled after reading a global type.
// Each independent source/type query needs its own package and lookup scope.
thor_context_type :: proc(ctx: ^AstContext, expr: ^ast.Expr) -> (Symbol, bool) {
    old_package, old_locals := ctx.current_package, ctx.use_locals
    defer { ctx.current_package, ctx.use_locals = old_package, old_locals }
    ctx.use_locals = true
    if expr != nil && expr.pos.file != "" { ctx.current_package = get_package_from_node(expr^) }
    return resolve_type_expression(ctx, expr)
}

thor_context_value :: proc(ctx: ^AstContext, expr: ^ast.Expr) -> (Symbol, bool) {
    old_package, old_locals := ctx.current_package, ctx.use_locals
    defer { ctx.current_package, ctx.use_locals = old_package, old_locals }
    ctx.use_locals = true
    if expr != nil && expr.pos.file != "" { ctx.current_package = get_package_from_node(expr^) }
    clear(&ctx.recursion_map)
    return resolve_value_type_expression(ctx, expr)
}

thor_context_switch_count :: proc(call: ^ast.Call_Expr) -> (int, bool) {
    ident, ok := call.expr.derived.(^ast.Ident)
    if !ok || ident.pos.offset != ident.end.offset { return 0, false }
    prefix := "__thor_switch_expression_"
    if !strings.has_prefix(ident.name, prefix) { return 0, false }
    count, valid := strconv.parse_int(ident.name[len(prefix):])
    return count, valid && count >= 0 && count <= len(call.args)
}

thor_context_adapter :: proc(call: ^ast.Call_Expr) -> bool {
    ident, ok := call.expr.derived.(^ast.Ident)
    if !ok || ident.pos.offset != ident.end.offset { return false }
    _, sw := thor_context_switch_count(call)
    return strings.has_prefix(ident.name,"__thor_comprehension") || sw || ident.name == "__thor_run" || ident.name == "__thor_interpolation" || ident.name == "__thor_switch_results" || ident.name == "__thor_switch_break"
}

// A nonreturning call supplies no switch result type. Use its procedure type,
// rather than its spelling, so aliases and procedure-valued parameters agree.
thor_context_diverging_arm :: proc(ctx: ^AstContext, expr: ^ast.Expr) -> bool {
    if expr == nil { return false }
    if paren, ok := expr.derived.(^ast.Paren_Expr); ok {
        return thor_context_diverging_arm(ctx, paren.expr)
    }
    call, ok := expr.derived.(^ast.Call_Expr)
    if !ok { return false }
    old_call, old_specific := ctx.call, ctx.resolve_specific_overload
    defer { ctx.call, ctx.resolve_specific_overload = old_call, old_specific }
    ctx.call, ctx.resolve_specific_overload = call, true
    symbol, resolved := thor_context_type(ctx, call.expr)
    if !resolved { return false }
    value, procedure := symbol.value.(SymbolProcedureValue)
    return procedure && value.diverging
}

thor_context_switch_value :: proc(ctx: ^AstContext, expr: ^ast.Expr) -> (Symbol, bool) {
    old_call := ctx.call
    defer ctx.call = old_call
    // Generic result types need the actual arm call's arguments, rather than
    // the virtual switch call that contains all results and case conditions.
    if call, ok := get_completed_call(expr); ok { ctx.call = call }
    return thor_context_value(ctx, expr)
}

thor_context_dependent_switch :: proc(ctx: ^AstContext, expr: ^ast.Expr, depth := 0) -> bool {
    if expr == nil || depth > 64 { return false }
    if paren, ok := expr.derived.(^ast.Paren_Expr); ok { return thor_context_dependent_switch(ctx, paren.expr, depth+1) }
    arms: []^ast.Expr
    branches: [2]^ast.Expr
    #partial switch node in expr.derived {
    case ^ast.Call_Expr:
        count, sw := thor_context_switch_count(node)
        if !sw { return false }
        arms = node.args[:count]
    case ^ast.Ternary_When_Expr:
        if !strings.has_suffix(ctx.file.fullpath, ".thor") { return false }
        branches = {node.x, node.y}; arms = branches[:]
    case ^ast.Ternary_If_Expr:
        if !strings.has_suffix(ctx.file.fullpath, ".thor") { return false }
        branches = {node.x, node.y}; arms = branches[:]
    case: return false
    }
    for arm in arms {
        if thor_context_diverging_arm(ctx, arm) { continue }
        candidate, ok := thor_context_switch_value(ctx, arm)
        if ok && thor_context_concrete(ctx, candidate) { return false }
    }
    return true
}

thor_context_argument :: proc(ctx: ^AstContext, pc: ^DocumentPositionContext,
    call: ^ast.Call_Expr) -> (result: Symbol, ok: bool) {
    old_call, old_specific := ctx.call, ctx.resolve_specific_overload
    old_package, old_locals := ctx.current_package, ctx.use_locals
    defer {
        ctx.call, ctx.resolve_specific_overload = old_call, old_specific
        ctx.current_package, ctx.use_locals = old_package, old_locals
    }
    ctx.call, ctx.resolve_specific_overload = call, true
    ctx.current_package, ctx.use_locals = ctx.document_package, true
    if len(call.args)==1 && thor_switch_root(call.args[0]) && thor_switch_arity(call.args[0],ctx)>1{
        symbol:=resolve_type_expression(ctx,call) or_return
        value:=symbol.value.(SymbolProcedureValue) or_return
        slot:=thor_switch_result_index(call.args[0],pc.position)
        field:=get_proc_arg_type_from_index(value,slot) or_return
        return thor_context_type(ctx,field.type if field.type!=nil else field.default_value)
    }
    type := get_call_argument_type(ctx, pc, call) or_return
    return thor_context_type(ctx, type)
}

// An inferred bracket array may contain type_of(.Red), which still needs an
// expected type. Do not choose that unresolved arm over a genuinely typed one.
thor_context_concrete :: proc(ctx: ^AstContext, symbol: Symbol, depth := 0) -> bool {
    if depth > 64 || symbol.value == nil { return false }
    elem: ^ast.Expr
    #partial switch value in symbol.value {
    case SymbolFixedArrayValue: elem = value.expr
    case SymbolSliceValue: elem = value.expr
    case SymbolDynamicArrayValue: elem = value.expr
    case SymbolPolyTypeValue: return false
    }
    if elem != nil {
        child, ok := thor_context_type(ctx, elem)
        return ok && thor_context_concrete(ctx, child, depth+1)
    }
    return true
}

thor_context_element :: proc(ctx: ^AstContext, pc: ^DocumentPositionContext,
    literal: ^ast.Comp_Lit, elem: ^ast.Expr, symbol: Symbol) -> (result: Symbol, ok: bool) {
    set_ast_package_set_scoped(ctx, symbol.pkg)
    value := elem
    field, named := elem.derived.(^ast.Field_Value)
    if named { value = field.value }
    type: ^ast.Expr
    #partial switch container in symbol.value {
    case SymbolFixedArrayValue:
        type = container.expr
        if named && position_in_node(field.field, pc.position) { type = container.len }
    case SymbolSliceValue: type = container.expr
    case SymbolDynamicArrayValue: type = container.expr
    case SymbolBitSetValue: type = container.expr
    case SymbolMapValue:
        type = container.key
        if named && position_in_node(value, pc.position) { type = container.value }
    case SymbolStructValue:
        name := ""
        if named { name, _ = get_field_value_name(field) }
        type = get_struct_comp_lit_type(pc, literal, container, name)
    case SymbolBitFieldValue:
        for item, i in literal.elems {
            if item == elem && i < len(container.types) { type = container.types[i] }
        }
        if named {
            name, _ := get_field_value_name(field)
            for field_name, i in container.names {
                if name == field_name { type = container.types[i]; break }
            }
        }
    }
    return thor_context_type(ctx, type)
}

thor_context_membership :: proc(ctx: ^AstContext, binary: ^ast.Binary_Expr) -> (container: Symbol, element: Symbol) {
    if binary.left == nil || binary.right == nil { return }
    right := binary.right
    for {
        paren, wrapped := right.derived.(^ast.Paren_Expr)
        if !wrapped { break }
        right = paren.expr
    }
    literal, inline_array := right.derived.(^ast.Comp_Lit)
    inline_array = inline_array && literal.type == nil && thor_is_array_literal(ctx.file, literal)
    if inline_array {
        needle, resolved := thor_context_switch_value(ctx, binary.left)
        _, untyped := needle.value.(SymbolUntypedValue)
        array: ^ast.Array_Type
        if resolved && !untyped {
            query := ast.new(ast.Call_Expr, binary.left.pos, binary.left.end)
            query.expr = thor_ident("type_of", binary.left.pos)
            query.args = thor_exprs(binary.left)
            array = thor_array_type(literal, query)
        } else { array = thor_array_type(literal) }
        if array != nil {
            context.allocator = ctx.allocator
            container = make_symbol_array_from_ast(ctx, array^, ctx.field_name)
        }
    } else { container, _ = thor_context_value(ctx, binary.right) }
    type: ^ast.Expr
    #partial switch value in container.value {
    case SymbolFixedArrayValue: type = value.expr
    case SymbolSliceValue: type = value.expr
    case SymbolDynamicArrayValue: type = value.expr
    case SymbolMapValue: type = value.key
    case SymbolBitSetValue: type = value.expr
    }
    if type != nil { element, _ = thor_context_type(ctx, type) }
    return
}

thor_context_expression :: proc(ctx: ^AstContext, pc: ^DocumentPositionContext,
    expr: ^ast.Expr, expected: Symbol, wanted: ^ast.Node, depth := 0) -> (result: Symbol, ok: bool) {
    if expr == nil || depth > 64 || !position_in_node(expr, pc.position) { return {}, false }
    expected := expected
    if literal, ok := expr.derived.(^ast.Comp_Lit); ok && literal.type != nil {
        expected = thor_context_type(ctx, literal.type) or_return
    }
    if expr == wanted { return expected, expected.value != nil }
    if _, implicit := expr.derived.(^ast.Implicit_Selector_Expr); implicit {
        // Completion may reparse a partial `.R` into a fresh node. Match its
        // original start rather than requiring the same AST allocation.
        if wanted == nil || expr.pos.offset == wanted.pos.offset {
            return expected, expected.value != nil
        }
    }
    #partial switch node in expr.derived {
    case ^ast.Paren_Expr:
        return thor_context_expression(ctx, pc, node.expr, expected, wanted, depth+1)
    case ^ast.Field_Value:
        return thor_context_expression(ctx, pc, node.value, expected, wanted, depth+1)
    case ^ast.Comp_Lit:
        if expected.value == nil { expected, _ = thor_context_value(ctx, expr) }
        for elem in node.elems {
            if !position_in_node(elem, pc.position) { continue }
            child := thor_context_element(ctx, pc, node, elem, expected) or_return
            if field, named := elem.derived.(^ast.Field_Value); named {
                value := field.value
                if position_in_node(field.field, pc.position) { value = field.field }
                return thor_context_expression(ctx, pc, value, child, wanted, depth+1)
            }
            return thor_context_expression(ctx, pc, elem, child, wanted, depth+1)
        }
    case ^ast.Call_Expr:
        if _,loop,output,comp:=thor_comprehension(node);comp {
            if len(node.args)>1 {expected,_=thor_context_type(ctx,node.args[1])}
            for value,index in output.results {
                if !position_in_node(value,pc.position){continue}
                element:^ast.Expr
                #partial switch container in expected.value {
                case SymbolSliceValue:element=container.expr
                case SymbolDynamicArrayValue:element=container.expr
                case SymbolFixedArrayValue:element=container.expr
                case SymbolMapValue:element=container.key if index==0&&len(output.results)>1 else container.value
                case SymbolStructValue:if thor_is_set_symbol(expected)&&len(container.args)==1 {element=container.args[0]}
                }
                child:Symbol
                marker:=node.expr.derived.(^ast.Ident)
                if strings.contains(marker.name,"_unpack"){child=expected}
                else if element!=nil {child,_=thor_context_type(ctx,element)}
                return thor_context_expression(ctx,pc,value,child,wanted,depth+1)
            }
            if position_in_node(loop.expr,pc.position){return thor_context_expression(ctx,pc,loop.expr,{},wanted,depth+1)}
            return {},false
        }
        if group,ok:=thor_switch_group(node);ok {
            for arg in group.args {
                if position_in_node(arg,pc.position){return thor_context_expression(ctx,pc,arg,expected,wanted,depth+1)}
            }
            return {},false
        }
        if count, sw := thor_context_switch_count(node); sw {
            if thor_switch_arity(node,ctx)>1 && !thor_context_concrete(ctx,expected){
                slot:=thor_switch_result_index(node,pc.position)
                expected,_=thor_context_switch_value(ctx,thor_switch_projection(node,slot))
            }
            if !thor_context_concrete(ctx, expected) {
                for arm in node.args[:count] {
                    if thor_context_diverging_arm(ctx, arm) { continue }
                    candidate, ok := thor_context_switch_value(ctx, arm)
                    if ok && thor_context_concrete(ctx, candidate) { expected = candidate; break }
                }
            }
            for arg, i in node.args {
                if !position_in_node(arg, pc.position) { continue }
                if i >= count {
                    // The parser stores results first, then selector/matches.
                    // No result type may leak into a switch case condition.
                    expected = {}
                    if len(node.args) > count { expected, _ = thor_context_value(ctx, node.args[count]) }
                }
                return thor_context_expression(ctx, pc, arg, expected, wanted, depth+1)
            }
        } else if !thor_context_adapter(node) {
            child := thor_context_argument(ctx, pc, node) or_return
            for arg in node.args {
                if position_in_node(arg, pc.position) {
                    return thor_context_expression(ctx, pc, arg, child, wanted, depth+1)
                }
            }
        }
    case ^ast.Ternary_If_Expr:
        if position_in_node(node.cond, pc.position) {
            return thor_context_expression(ctx, pc, node.cond, {}, wanted, depth+1)
        }
        for branch in ([2]^ast.Expr{node.x, node.y}) {
            if position_in_node(branch, pc.position) {
                if !thor_context_concrete(ctx, expected) {
                    other := node.y if branch == node.x else node.x
                    expected, _ = thor_context_value(ctx, other)
                }
                return thor_context_expression(ctx, pc, branch, expected, wanted, depth+1)
            }
        }
    case ^ast.Ternary_When_Expr:
        if position_in_node(node.cond, pc.position) {
            return thor_context_expression(ctx, pc, node.cond, {}, wanted, depth+1)
        }
        for branch in ([2]^ast.Expr{node.x, node.y}) {
            if position_in_node(branch, pc.position) {
                if !thor_context_concrete(ctx, expected) {
                    other := node.y if branch == node.x else node.x
                    expected, _ = thor_context_value(ctx, other)
                }
                return thor_context_expression(ctx, pc, branch, expected, wanted, depth+1)
            }
        }
    case ^ast.Binary_Expr:
        if node.op.kind == .In || node.op.kind == .Not_In {
            container, element := thor_context_membership(ctx, node)
            if position_in_node(node.right, pc.position) {
                return thor_context_expression(ctx, pc, node.right, container, wanted, depth+1)
            }
            return thor_context_expression(ctx, pc, node.left, element, wanted, depth+1)
        }
        for branch in ([2]^ast.Expr{node.left, node.right}) {
            if !position_in_node(branch, pc.position) { continue }
            other := node.right if branch == node.left else node.left
            if node.op.kind == .Cmp_Eq || node.op.kind == .Not_Eq || node.op.kind == .Lt || node.op.kind == .Gt || node.op.kind == .Lt_Eq || node.op.kind == .Gt_Eq {
                expected, _ = thor_context_value(ctx, other)
            }
            if node.op.kind == .Increment && !thor_context_concrete(ctx, expected) {
                expected, _ = thor_context_switch_value(ctx, other)
            }
            return thor_context_expression(ctx, pc, branch, expected, wanted, depth+1)
        }
    }
    return {}, false
}

thor_resolve_expected_type :: proc(ctx: ^AstContext, pc: ^DocumentPositionContext,
    wanted: ^ast.Node) -> (result: Symbol, ok: bool) {
    if wanted == nil && !pc.implicit || !strings.has_suffix(pc.file.fullpath, ".thor") { return {}, false }
    old_package, old_locals := ctx.current_package, ctx.use_locals
    defer { ctx.current_package, ctx.use_locals = old_package, old_locals }
    // The closest real call supplies its argument's type, even when that call
    // is nested in an aggregate or in an interpolation with a different type.
    for i := len(pc.calls)-1; i >= 0; i -= 1 {
        call := pc.calls[i]
        if thor_context_adapter(call) || !position_in_exprs(call.args, pc.position) { continue }
        if symbol, ok := thor_context_expression(ctx, pc, call, {}, wanted); ok { return symbol, true }
    }
    if binary := pc.parent_binary; binary != nil {
        if symbol, ok := thor_context_expression(ctx, pc, binary, {}, wanted); ok { return symbol, true }
    }
    if literal := pc.parent_comp_lit; literal != nil && literal.type != nil {
        if symbol, ok := thor_context_expression(ctx, pc, literal, {}, wanted); ok { return symbol, true }
    }
    if assignment := pc.assign; assignment != nil && len(assignment.rhs)==1 && len(assignment.lhs)>1 && thor_switch_root(assignment.rhs[0]) {
        slot:=thor_switch_result_index(assignment.rhs[0],pc.position)
        if slot<len(assignment.lhs){
            expected:=thor_context_type(ctx,assignment.lhs[slot]) or_return
            return thor_context_expression(ctx,pc,assignment.rhs[0],expected,wanted)
        }
    }
    if assignment := pc.assign; assignment != nil && len(assignment.lhs) == len(assignment.rhs) {
        for rhs, i in assignment.rhs {
            if !position_in_node(rhs, pc.position) { continue }
            expected := thor_context_type(ctx, assignment.lhs[i]) or_return
            return thor_context_expression(ctx, pc, rhs, expected, wanted)
        }
    }
    if returns := pc.returns; returns != nil && pc.function != nil && pc.function.type != nil && pc.function.type.results != nil {
        if len(returns.results)==1 && thor_switch_root(returns.results[0]) && thor_switch_arity(returns.results[0],ctx)>1{
            slot:=thor_switch_result_index(returns.results[0],pc.position)
            current:=0
            for field in pc.function.type.results.list{for _ in 0..<max(1,len(field.names)){
                if current==slot{
                    expected:=thor_context_type(ctx,field.type) or_return
                    return thor_context_expression(ctx,pc,returns.results[0],expected,wanted)
                }
                current+=1
            }}
        }
        index := 0
        for field in pc.function.type.results.list {
            for _ in 0..<max(1, len(field.names)) {
                if index < len(returns.results) && position_in_node(returns.results[index], pc.position) {
                    expected := thor_context_type(ctx, field.type) or_return
                    return thor_context_expression(ctx, pc, returns.results[index], expected, wanted)
                }
                index += 1
            }
        }
    }
    if decl := pc.value_decl; decl != nil {
        expected: Symbol
        if decl.type != nil { expected, _ = thor_context_type(ctx, decl.type) }
        for value in decl.values {
            if position_in_node(value, pc.position) { return thor_context_expression(ctx, pc, value, expected, wanted) }
        }
    }
    return {}, false
}
