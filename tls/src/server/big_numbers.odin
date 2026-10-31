// Thor's built-in numeric surface. Indexed native structs remain unchanged.
package server

import "core:odin/ast"
import path "core:path/slashpath"
import "core:strings"

thor_big_package :: proc() -> string {
    config := indexer.index.collection.config
    if config == nil { return "" }
    root, found := config.collections["thor"]
    if !found { return "" }
    return path.join({root, "big"}, context.temp_allocator)
}

thor_big_kind :: proc(symbol: Symbol) -> string {
    if .Distinct in symbol.flags { return "" }
    value, is_struct := symbol.value.(SymbolStructValue)
    if !is_struct { return "" }
    if len(value.types) == 0 { return "" }
    pkg := thor_big_package()
    if pkg == "" { return "" }
    for name in ([]string{"Integer", "Rational"}) {
        canonical, found := memory_index_lookup(&indexer.index, name, pkg)
        if !found { continue }
        declaration := canonical.value.(SymbolStructValue) or_continue
        if len(declaration.types) > 0 && declaration.types[0].pos == value.types[0].pos { return name }
    }
    return ""
}

thor_lookup_big_type :: proc(ctx: ^AstContext, name: string) -> (Symbol, bool) {
    if !thor_type_display_enabled(ctx) || name != "Integer" && name != "Rational" { return {}, false }
    pkg := thor_big_package()
    if pkg == "" { return {}, false }
    try_build_package(pkg)
    symbol, found := lookup(name, pkg, "")
    if !found { return {}, false }
    return resolve_symbol_return(ctx, symbol)
}

thor_big_property :: proc(ctx: ^AstContext, receiver: Symbol, name: string) -> (Symbol, bool) {
    if !thor_type_display_enabled(ctx) || thor_big_kind(receiver) != "Rational" ||
       name != "numerator" && name != "denominator" { return {}, false }
    symbol, found := thor_lookup_big_type(ctx, "Integer")
    if !found { return {}, false }
    symbol.type_name, symbol.type_pkg = "Integer", thor_big_package()
    symbol.name, symbol.type = name, .Field
    symbol.flags = {.Variable, .ReadOnlyBinding}
    symbol.type_expr, symbol.value_expr = nil, nil
    symbol.doc = "Read-only component. Reading this property creates an independent owned Integer; delete a named result when finished."
    if getter, ok := lookup(name, thor_big_package(), ""); ok {
        symbol.range, symbol.uri = getter.range, getter.uri
    }
    return symbol, true
}

thor_big_returns :: proc(ctx: ^AstContext, symbol: Symbol, call: ^ast.Call_Expr) -> []^ast.Expr {
    if !thor_type_display_enabled(ctx) || thor_big_kind(symbol) == "" || .Variable in symbol.flags { return nil }
    results := make([dynamic]^ast.Expr, context.temp_allocator)
    append(&results, call.expr)
    for arg in call.args {
        value := arg
        if field, ok := value.derived.(^ast.Field_Value); ok { value = field.value }
        resolved, ok := resolve_type_expression(ctx, value)
        untyped, untyped_ok := resolved.value.(SymbolUntypedValue)
        basic, basic_ok := resolved.value.(SymbolBasicValue)
        if ok && (untyped_ok && untyped.type == .String || basic_ok && basic.ident.name == "string") {
            append(&results, make_bool_ast(ctx, call.pos, call.end))
            break
        }
    }
    return results[:]
}

thor_write_big_type :: proc(sb: ^strings.Builder, ctx: ^AstContext, symbol: Symbol) -> bool {
    if !thor_type_display_enabled(ctx) { return false }
    kind := thor_big_kind(symbol)
    if kind == "" { return false }
    strings.write_string(sb, repeat("^", symbol.pointers, ctx.allocator))
    strings.write_string(sb, kind)
    return true
}
