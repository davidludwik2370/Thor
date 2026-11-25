//! Defer by address: cleanup reads the final owner, including a resized header.
use super::*;

impl Lower<'_, '_> {
    pub(super) fn scoped_declaration(
        &mut self,
        id: NodeId,
        code: Code,
        captures: Vec<Code>,
    ) -> Result<Code> {
        let names = crate::scoped::names(self.source, id);
        if names.is_empty() || self.querying != 0 {
            return Ok(code);
        }
        let module = self.module("scoped");
        let mut parts = captures;
        parts.push(code);
        for name in names {
            let allocator = format!("{}scope_allocator_{}", self.prefix, name.0);
            parts.push(self.g(
                format!(
                    "; defer {{ {module}.cleanup(&{}, {allocator}^) }}",
                    self.source.text(name)
                ),
                name,
            ));
        }
        Ok(Code::concat(parts))
    }

    pub(super) fn scoped_captures(
        &mut self,
        id: NodeId,
    ) -> Result<(Vec<Code>, Vec<(NodeId, Option<Vec<String>>)>)> {
        if self.querying != 0 {
            return Ok((vec![], vec![]));
        }
        let names = crate::scoped::names(self.source, id);
        let mut parts = vec![];
        let mut replacements = vec![];
        for name in names {
            let allocator = format!("{}scope_allocator_{}", self.prefix, name.0);
            // An explicit constructor allocator must be evaluated exactly once,
            // before initialization, even if context.allocator changes later.
            let values = self.source.group(id, "values");
            let value = values
                .get(
                    self.source
                        .group(id, "names")
                        .iter()
                        .position(|n| *n == name)
                        .unwrap_or(0),
                )
                .or_else(|| values.first().filter(|_| values.len() == 1))
                .copied();
            let index = self
                .source
                .group(id, "names")
                .iter()
                .position(|n| *n == name)
                .unwrap_or(0);
            let result_index = if values.len() == 1 { index } else { 0 };
            let branches = value
                .map(|node| {
                    crate::scoped::initializations(
                        self.resolver,
                        Ref {
                            file: self.file,
                            node,
                        },
                        result_index,
                    )
                })
                .unwrap_or_default();
            let typ = self.typ(name);
            let arguments = if crate::scoped::stores_allocator(&typ) {
                vec![]
            } else {
                branches
                    .into_iter()
                    .map(|branch| crate::scoped::allocator_argument(self.resolver, branch))
                    .collect::<Vec<_>>()
            };
            if arguments.iter().any(Option::is_some) && arguments.iter().any(Option::is_none) {
                return Err(Error::at(self.source,self.source.node(name).span,"Scoped pointer/slice/string branches must all specify an explicit allocator when any branch does; pass allocator explicitly on each allocating branch, or use explicit defer cleanup."));
            }
            for arg in arguments.into_iter().flatten() {
                let arg = arg.node;
                let previous = self.scoped_arguments.get(&arg).cloned();
                let mut captures = previous.clone().unwrap_or_default();
                captures.push(allocator.clone());
                self.scoped_arguments.insert(arg, captures);
                replacements.push((arg, previous));
            }
            parts.push(self.g(
                format!(
                    "{allocator}_value := context.allocator; {allocator} := &{allocator}_value; "
                ),
                name,
            ));
        }
        Ok((parts, replacements))
    }
}
