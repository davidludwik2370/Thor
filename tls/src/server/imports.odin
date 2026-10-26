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

find_unused_imports :: proc(document: ^Document, allocator := context.temp_allocator) -> []Package {
	spall.trace(#procedure, document.fullpath)

	if document.ast.syntax_error_count > 0 {
		return nil
	}

	symbols := resolve_entire_file(document)

	// Selective imports add synthetic alias declarations to the server AST.
	// Resolving those declarations alone does not mean the source uses them.
	synthetic_positions := make(map[int]bool, context.temp_allocator)
	selections := make(map[string]string, context.temp_allocator)
	for decl in document.ast.decls {
		if value, ok := decl.derived.(^ast.Value_Decl); ok && thor_is_import_alias(value) {
			synthetic_positions[decl.pos.offset] = true
			selector := value.values[0].derived.(^ast.Selector_Expr)
			namespace := selector.expr.derived.(^ast.Ident)
			selections[selector.field.name] = namespace.name
		}
	}

	pkgs := make(map[string]struct{}, context.temp_allocator)
	used_names := make(map[string]bool, context.temp_allocator)
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
			if namespace, selected := selections[ident.name]; selected {
				for imp in document.imports {
					// A local that shadows a selected name is not an import use.
					if imp.base == namespace && imp.name == v.symbol.pkg {
						used_names[namespace] = true
					}
				}
			}
		}
	}

	unused := make([dynamic]Package, allocator)
	for imp in document.imports {
		// Zero-length imports are internal adapters (e.g. for set[T]), with no
		// source declaration to dim or remove. Blank imports are intentional.
		used := used_names[imp.base]
		if imp.import_decl.is_using { used = imp.name in pkgs }
		if imp.base != "_" && imp.import_decl.pos.offset < imp.import_decl.end.offset && !used {
			append(&unused, imp)
		}
	}

	return unused[:]
}
