package server

import "core:fmt"
import "core:mem"
import "core:strings"

import "base:runtime"

import "core:odin/ast"
import path "core:path/slashpath"

import "src:common"
import "src:spall"

find_used_not_imported :: proc(
	document: ^Document,
	config: ^common.Config,
	allocator := context.temp_allocator,
) -> []Package {
	arena: runtime.Arena

	_ = runtime.arena_init(&arena, mem.Megabyte * 40, context.allocator)
	defer runtime.arena_destroy(&arena)

	context.allocator = runtime.arena_allocator(&arena)

	symbols_and_nodes := resolve_entire_file(document)

	already_imported := make(map[string]struct{})

	for imp in document.imports {
		already_imported[imp.base] = {}
	}

	unresolved := make(map[string]string)

	for _, v in symbols_and_nodes {
		if v.is_unresolved && v.is_selector_expression_unresolved {
			if selector_expr, ok := v.node.derived.(^ast.Selector_Expr); ok {
				if ident, ok := selector_expr.expr.derived.(^ast.Ident); ok {
					if ident.name in already_imported {
						continue
					}

					if selector_expr.field != nil && ident.name not_in unresolved {
						unresolved[ident.name] = selector_expr.field.name
					}
				}
			}
		}
	}


	missing := make([dynamic]Package, allocator)

	for collection, pkgs in build_cache.pkg_aliases {
		for pkg in pkgs {
			pkg_base := path.base(pkg)
			field_name, is_candidate := unresolved[pkg_base]

			if !is_candidate {
				continue
			}

			fullpath := path.join({config.collections[collection], pkg})

			already := false

			for doc_pkg in document.imports {
				if fullpath == doc_pkg.name {
					already = true
					break
				}
			}

			if already {
				continue
			}

			try_build_package(fullpath)

			if _, ok := lookup(field_name, fullpath, document.fullpath); ok {
				append(
					&missing,
					Package {
						name = strings.clone(fullpath, allocator),
						base = strings.clone(pkg_base, allocator),
						original = fmt.aprintf("%v:%v", collection, pkg, allocator = allocator),
					},
				)
			}
		}
	}

	return missing[:]
}

ImportUsage :: struct {
	unused_packages: []Package,
	unused_selections: []^ast.Ident,
}

find_unused_imports :: proc(document: ^Document, allocator := context.temp_allocator) -> []Package {
	usage := find_import_usage(document, allocator)
	delete(usage.unused_selections, allocator)
	return usage.unused_packages
}

find_import_usage :: proc(document: ^Document, allocator := context.temp_allocator) -> ImportUsage {
	spall.trace(#procedure, document.fullpath)

	if document.ast.syntax_error_count > 0 {
		return {}
	}

	symbols := resolve_entire_file(document)

	// Selective imports add synthetic alias declarations to the server AST.
	// Resolving those declarations alone does not mean the source uses them.
	synthetic_positions := make(map[int]bool, context.temp_allocator)
	selections := make(map[string]string, context.temp_allocator)
	selected_names := make([dynamic]^ast.Ident, context.temp_allocator)
	for decl in document.ast.decls {
		if value, ok := decl.derived.(^ast.Value_Decl); ok && thor_is_import_alias(value) {
			synthetic_positions[decl.pos.offset] = true
			selector := value.values[0].derived.(^ast.Selector_Expr)
			namespace := selector.expr.derived.(^ast.Ident)
			selections[selector.field.name] = namespace.name
			append(&selected_names, selector.field)
		}
	}
	// A qualified access uses the namespace, not a same-named selection.
	selector_fields := make(map[uintptr]bool, context.temp_allocator)
	for _, v in symbols {
		if v.node == nil { continue }
		if selector, ok := v.node.derived.(^ast.Selector_Expr); ok && selector.field != nil {
			selector_fields[cast(uintptr)selector.field] = true
		}
	}

	pkgs := make(map[string]struct{}, context.temp_allocator)
	used_names := make(map[string]bool, context.temp_allocator)
	used_selections := make(map[string]bool, context.temp_allocator)
	for _, v in symbols {
		if v.node == nil || synthetic_positions[v.node.pos.offset] { continue }
		pkgs[v.symbol.pkg] = {}
		if ident, ok := v.node.derived.(^ast.Ident); ok {
			// Track source namespaces, not just package paths: two aliases of
			// one package can have different usage. The hidden set[T] adapter
			// must not make an explicit, unused import of thor:sets look used.
			if v.symbol.type == .Package && ident.end.offset > ident.pos.offset {
				used_names[ident.name] = true
			}
		}
	}
	if len(selected_names) > 0 {
		// Usage is a reference to the imported declaration, not a successful
		// overload/result-type query. Incomplete calls can still use an import;
		// exported aliases can resolve to a type owned by a different package.
		targets := make(map[string]Symbol, context.temp_allocator)
		for name in selected_names {
			for imp in document.imports {
				if imp.base == selections[name.name] {
					if symbol, ok := lookup(name.name, imp.name, document.fullpath); ok { targets[name.name] = symbol }
				}
			}
		}
		references := resolve_entire_file_for_references(document, context.temp_allocator, .Identifier, "", capacity=max(32,len(symbols)), import_usage=true)
		for _, reference in references {
			if reference.node == nil || synthetic_positions[reference.node.pos.offset] { continue }
			ident, named := reference.node.derived.(^ast.Ident)
			if !named || selector_fields[cast(uintptr)ident] { continue }
			target, selected := targets[ident.name]
			if !selected { continue }
			// Exact declaration identity excludes locals, parameters, named
			// argument keys and record fields, even when their types come from
			// the imported package or they have the same spelling.
			if reference.symbol.uri == target.uri && reference.symbol.range == target.range {
				used_names[selections[ident.name]] = true
				used_selections[ident.name] = true
			}
		}
	}

	unused := make([dynamic]Package, allocator)
	unused_selections := make([dynamic]^ast.Ident, allocator)
	for imp in document.imports {
		required := false
		for attribute in imp.import_decl.attributes {
			for element in attribute.elems {
				if name, _, ok := unwrap_attr_elem(element); ok && name.name == "require" {
					required = true
				}
			}
		}
		if required {continue}
		// Zero-length imports are internal adapters (e.g. for set[T]), with no
		// source declaration to dim or remove. Blank imports are intentional.
		used := used_names[imp.base]
		if imp.import_decl.is_using { used = imp.name in pkgs }
		if imp.base != "_" && imp.import_decl.pos.offset < imp.import_decl.end.offset && !used {
			append(&unused, imp)
			continue // The whole declaration already includes its selected names.
		}
		for name in selected_names {
			if selections[name.name] == imp.base && !used_selections[name.name] {
				append(&unused_selections, name)
			}
		}
	}

	return {unused_packages=unused[:], unused_selections=unused_selections[:]}
}
