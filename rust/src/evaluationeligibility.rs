//! Shared reviewed-operation identities for extraction and readiness checks.
//! A collection override or same-named local never inherits an SDK contract.
use super::*;

pub(crate) fn library_origin(r: &Resolver<'_>, callee: Ref) -> Option<(String, String)> {
    fn visit(r: &Resolver<'_>, callee: Ref, seen: &mut HashSet<Ref>) -> Option<(String, String)> {
        if !seen.insert(callee) {
            return None;
        }
        let source = &r.sources[callee.file];
        let node = source.node(callee.node);
        if node.kind == "Paren" {
            return visit(
                r,
                Ref {
                    file: callee.file,
                    node: source.role(callee.node, "expr")?,
                },
                seen,
            );
        }
        if node.kind == "Selector" {
            let base = source.role(callee.node, "expr")?;
            let field = source.role(callee.node, "field")?;
            if source.node(base).kind == "Ident"
                && r.lookup(
                    Ref {
                        file: callee.file,
                        node: base,
                    },
                    source.text(base),
                )
                .is_none()
            {
                for import in source.nodes.iter().filter(|node| node.kind == "Import") {
                    let path = unquote(&import.op).ok()?;
                    let alias = source.names.get(import.name);
                    let alias = if alias.is_empty() {
                        path.rsplit(['/', ':']).next()?
                    } else {
                        alias
                    };
                    if alias == source.text(base) {
                        return Some((path, source.text(field).into()));
                    }
                }
            }
        }
        if node.kind == "Ident" && r.lookup(callee, source.text(callee.node)).is_none() {
            if let Some(selection) = source.selections.iter().find(|selection| {
                selection
                    .names
                    .iter()
                    .any(|name| name == source.text(callee.node))
            }) {
                return Some((
                    unquote(&selection.path).ok()?,
                    source.text(callee.node).into(),
                ));
            }
        }
        let symbol = &r.symbols[r.symbol(callee)?];
        if !symbol.constant || symbol.immutable {
            return None;
        }
        let value = Ref {
            file: symbol.reference.file,
            node: symbol.value?,
        };
        matches!(
            r.sources[value.file].node(value.node).kind.as_str(),
            "Ident" | "Selector" | "Paren"
        )
        .then(|| visit(r, value, seen))
        .flatten()
    }
    visit(r, callee, &mut HashSet::new())
}

pub(crate) fn library_member(r: &Resolver<'_>, callee: Ref, path: &str) -> Option<String> {
    if !path.starts_with("thor:") {
        return r.library_alias_member(callee, path);
    }
    library_seen(r, callee, path, &mut HashSet::new())
}
fn library_seen(
    r: &Resolver<'_>,
    callee: Ref,
    path: &str,
    seen: &mut HashSet<Ref>,
) -> Option<String> {
    if !seen.insert(callee) {
        return None;
    }
    let source = &r.sources[callee.file];
    let suffix = path.strip_prefix("thor:")?;
    let expected = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()?
        .join("runtime")
        .join(suffix);
    let actual = source.collection_roots.get("thor")?.join(suffix);
    if r.canonical_path(&actual)? != r.canonical_path(&expected)? {
        return None;
    }
    let node = source.node(callee.node);
    if node.kind == "Selector" {
        let base = source.role(callee.node, "expr")?;
        let field = source.role(callee.node, "field")?;
        if source.node(base).kind == "Ident"
            && r.lookup(
                Ref {
                    file: callee.file,
                    node: base,
                },
                source.text(base),
            )
            .is_none()
            && r.namespace_imports(callee.file, source.text(base)).iter().any(|import| import == path)
        {
            return Some(source.text(field).into());
        }
    }
    if node.kind == "Paren" {
        return library_seen(
            r,
            Ref {
                file: callee.file,
                node: source.role(callee.node, "expr")?,
            },
            path,
            seen,
        );
    }
    if node.kind == "Ident"
        && r.lookup(callee, source.text(callee.node)).is_none()
        && source.selections.iter().any(|selection| {
            unquote(&selection.path).ok().as_deref() == Some(path)
                && (selection.wildcard
                    || selection
                        .names
                        .iter()
                        .any(|name| name == source.text(callee.node)))
        })
    {
        return Some(source.text(callee.node).into());
    }
    let symbol = &r.symbols[r.symbol(callee)?];
    if !symbol.constant || symbol.immutable {
        return None;
    }
    let owner = &r.sources[symbol.reference.file];
    if owner.path.parent() == Some(expected.as_path()) {
        return Some(symbol.name.clone());
    }
    let value = Ref {
        file: symbol.reference.file,
        node: symbol.value?,
    };
    matches!(
        owner.node(value.node).kind.as_str(),
        "Ident" | "Selector" | "Paren"
    )
    .then(|| library_seen(r, value, path, seen))
    .flatten()
}
const PURE_FORMAT_PROCEDURES: &[&str] = &[
    "format", "format_plain", "format_options", "format_spec", "group_integer",
];

pub(crate) fn approved_format(r: &Resolver<'_>, callee: Ref) -> bool {
    library_member(r, callee, "thor:format")
        .is_some_and(|name| PURE_FORMAT_PROCEDURES.contains(&name.as_str()))
}

pub(crate) fn approved_library(r: &Resolver<'_>, callee: Ref) -> Option<(String, String)> {
    for (path, names) in [
        ("core:slice", &["clone"][..]),
        ("core:strings", &["clone"][..]),
        ("thor:format", PURE_FORMAT_PROCEDURES),
        (
            "thor:big",
            &[
                "from_integer",
                "rational_from_integer",
                "fraction",
                "parse_integer",
                "parse_rational",
                "parse_fraction",
                "clone",
                "numerator",
                "denominator",
                "to_string",
                "equal",
                "is_zero",
                "sum",
                "ascending",
                "descending",
            ][..],
        ),
    ] {
        if let Some(name) =
            library_member(r, callee, path).filter(|name| names.contains(&name.as_str()))
        {
            return Some((path.into(), name));
        }
    }
    None
}
pub(crate) fn primitive(r: &mut Resolver<'_>, callee: Ref) -> bool {
    if r.symbol(callee).is_some_and(|index| {
        let symbol = &r.symbols[index];
        !symbol.constant || symbol.immutable || symbol.parameter || symbol.result
    }) {
        return false;
    }
    if let Some(name) = r.library_alias_member(callee, "base:intrinsics") {
        return crate::purity::intrinsic_allowed(&name);
    }
    r.candidates(callee)
        .iter()
        .any(|candidate| crate::purity::llvm_primitive(r, *candidate))
}

pub(super) fn source_location_dependent(
    r: &Resolver<'_>,
    node: Ref,
    seen: &mut HashSet<Ref>,
) -> bool {
    if !seen.insert(node) {
        return false;
    }
    let source = &r.sources[node.file];
    let ast = source.node(node.node);
    if matches!(ast.kind.as_str(), "Procedure" | "ProcedureGroup") {
        return false;
    }
    if ast.kind == "Directive"
        && ["file", "directory", "line", "caller_location", "defined"]
            .contains(&source.names.get(ast.name))
    {
        return true;
    }
    if ast.kind == "Ident"
        && source.text(node.node) == "ODIN_BUILD_PROJECT_NAME"
        && r.symbol(node).is_none()
    {
        return true;
    }
    if library_member(r, node, "base:builtin").as_deref() == Some("ODIN_BUILD_PROJECT_NAME")
        || library_member(r, node, "base:intrinsics")
            .is_some_and(|name| ["type_canonical_name", "type_name_of"].contains(&name.as_str()))
    {
        return true;
    }
    if matches!(ast.kind.as_str(), "Ident" | "Selector") {
        if let Some(symbol) = r
            .symbol(node)
            .map(|index| &r.symbols[index])
            .filter(|symbol| symbol.constant)
        {
            if let Some(value) = symbol.value {
                if source_location_dependent(
                    r,
                    Ref {
                        file: symbol.reference.file,
                        node: value,
                    },
                    seen,
                ) {
                    return true;
                }
            }
        }
    }
    ast.children.iter().any(|child| {
        source_location_dependent(
            r,
            Ref {
                file: node.file,
                node: *child,
            },
            seen,
        )
    })
}

pub(crate) fn explicit_allocator(r: &mut Resolver<'_>, call: Ref) -> bool {
    let source = &r.sources[call.file];
    let args = source
        .group(call.node, "args")
        .iter()
        .map(|node| Ref {
            file: call.file,
            node: *node,
        })
        .collect::<Vec<_>>();
    let library = source.role(call.node, "callee").and_then(|node| {
        approved_library(
            r,
            Ref {
                file: call.file,
                node,
            },
        )
    });
    let plans = r.call_plans(call, &crate::semantics::Substitutions::new());
    let plan = if let [plan] = plans.as_slice() {
        Some(plan)
    } else {
        None
    };
    let fields = plan
        .map(|plan| r.fields(plan.signature, "params"))
        .unwrap_or_default();
    let source = &r.sources[call.file];
    let named = args.iter().any(|arg| {
        source.node(arg.node).kind == "FieldValue"
            && source
                .role(arg.node, "field")
                .is_some_and(|field| source.text(field) == "allocator")
    });
    if named {
        let actual = fields
            .iter()
            .find(|(name, _)| name == "allocator")
            .and_then(|(_, typ)| *typ)
            .zip(plan)
            .map(|(typ, plan)| r.specialized_type(typ, &plan.bindings));
        let mut actual = actual.as_ref();
        while let Some(Type::Nominal(_, inner)) = actual {
            actual = Some(inner);
        }
        return !matches!(actual,Some(Type::Scalar{name,..}) if !["any","rawptr","typeid","cstring"].contains(&name.as_str()))
            && !matches!(actual, Some(Type::Enum(_) | Type::Set(_)));
    }
    let count = args
        .iter()
        .filter(|arg| source.node(arg.node).kind != "FieldValue")
        .count();
    if library.is_some() && !fields.is_empty() {
        return fields
            .iter()
            .take(count)
            .any(|(name, _)| name == "allocator");
    }
    match library
        .as_ref()
        .map(|(path, name)| (path.as_str(), name.as_str()))
    {
        Some(("core:strings" | "core:slice", "clone") | ("thor:format", "group_integer")) => {
            count > 1
        }
        Some(("thor:format", name)) => {
            count
                > if ["format_options", "format_spec"].contains(&name) {
                    5
                } else {
                    4
                }
        }
        _ => false,
    }
}
