//! Caller-local native types stay in their concrete caller's lexical scope.
//! Only isolated helpers gain the reverse import needed to call this bridge.
use super::*;
use crate::specialization::Specializations;

fn template(source: &crate::ast::Source, node: NodeId) -> String {
    if source.node(node).kind == "PolyType" {
        return source
            .role(node, "specialization")
            .map(|node| template(source, node))
            .unwrap_or_else(|| source.names.get(source.node(node).name).to_string());
    }
    let mut code = source.text(node).to_string();
    for child in source.node(node).children.iter().rev() {
        let span = source.node(*child).span;
        code.replace_range(
            span.start - source.node(node).span.start..span.end - source.node(node).span.start,
            &template(source, *child),
        );
    }
    code
}

impl Plan {
    pub(super) fn configure_request(
        &mut self,
        r: &mut Resolver<'_>,
        expression: Ref,
        context: &Context<'_>,
        mode: Mode,
        locals: BTreeMap<usize, BTreeMap<NodeId, Vec<NodeId>>>,
    ) -> Result<()> {
        let source = &r.sources[expression.file];
        let mut types = TypeCode::new(expression.file, Some(expression));
        types.allow_imports = true;
        let direct = Specializations::declarations(context.bindings, &mut types, r);
        let mut request = Request {
            expression: expression.node,
            locals: locals.get(&expression.file).cloned().unwrap_or_default(),
            mode,
            prelude: direct.as_ref().cloned().unwrap_or_default(),
            bridge: None,
        };
        self.entry_file = expression.file;
        if direct.is_err() {
            let mut selected = None;
            for (origin, bindings) in context.frames.iter().rev() {
                let mut types = TypeCode::new(origin.file, Some(*origin));
                types.allow_imports = true;
                if context
                    .bindings
                    .values()
                    .all(|binding| types.render(&binding.typ, r).is_ok())
                {
                    selected = Some((*origin, bindings, types));
                    break;
                }
            }
            let Some((origin, origin_bindings, mut origin_types)) = selected else {
                return Err(Error::at(
                    source,
                    source.node(expression.node).span,
                    "The generic specialization has no concrete call site.",
                ));
            };
            let name = format!(
                "{}specialized_evaluation_{}",
                source.prefix,
                source.node(expression.node).span.start
            );
            let mut arguments = vec![];
            let mut parameters = vec![];
            let mut type_names = vec![];
            for ((_, slot), binding) in context.bindings {
                let typ = origin_types
                    .render(&binding.typ, r)
                    .map_err(|error| Error::new(error.to_string()))?;
                let argument = if let Some(value) = &binding.value {
                    let symbol = &r.symbols[binding.symbol];
                    let annotation = symbol
                        .annotation
                        .map(|node| {
                            let owner = &r.sources[symbol.reference.file];
                            template(owner, node)
                        })
                        .unwrap_or_else(|| typ.clone());
                    parameters.push(format!("${slot}:{annotation}"));
                    crate::values::expression(
                        value,
                        &typ,
                        &format!("{}meta", r.sources[origin.file].prefix),
                    )
                    .map_err(|error| Error::new(error.to_string()))?
                } else {
                    parameters.push(format!("${slot}:typeid"));
                    type_names.push(slot.clone());
                    typ
                };
                arguments.push(argument);
            }
            request.bridge = Some(RequestBridge {
                name: name.clone(),
                parameters: parameters.join(","),
                type_names,
            });
            let caller = &r.sources[origin.file];
            let alias = if source.path.parent() == caller.path.parent() {
                String::new()
            } else {
                let alias = format!("{}evaluation_bridge_{}", caller.prefix, expression.file);
                self.files.get_mut(&caller.path).unwrap().imports.insert(
                    alias.clone(),
                    dependency_path(caller, source.path.parent().unwrap()),
                );
                alias + "."
            };
            let prelude = Specializations::declarations(origin_bindings, &mut origin_types, r)
                .map_err(|error| {
                    Error::at(caller, caller.node(origin.node).span, error.to_string())
                })?;
            self.files.get_mut(&caller.path).unwrap().entry = Some(EntryRequest {
                origin: origin.node,
                locals: locals.get(&origin.file).cloned().unwrap_or_default(),
                prelude,
                call: format!("{alias}{name}({})", arguments.join(",")),
            });
            for (alias, directory) in origin_types.imports {
                self.files
                    .get_mut(&caller.path)
                    .unwrap()
                    .imports
                    .insert(alias, dependency_path(caller, &directory));
            }
            self.entry_file = origin.file;
        } else {
            for (alias, directory) in types.imports {
                self.files
                    .get_mut(&source.path)
                    .unwrap()
                    .imports
                    .insert(alias, dependency_path(source, &directory));
            }
        }
        self.files.get_mut(&source.path).unwrap().request = Some(request);
        Ok(())
    }
}
