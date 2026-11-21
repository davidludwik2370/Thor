//! Native deferred metadata remains a checked implicit call. Captured mutable
//! storage is unknown at scope exit; no initial pointee/callback facts survive.
use super::*;
impl Check<'_, '_> {
    pub(super) fn deferred_calls(
        &mut self,
        w: &Work,
        declaration: Ref,
        signature: Ref,
    ) -> Result<()> {
        let attributes = self.r.attributes(declaration);
        for (name, callback) in attributes {
            if !name.starts_with("deferred_") {
                continue;
            }
            let mode = name.trim_end_matches("_by_ptr");
            if ![
                "deferred_none",
                "deferred_in",
                "deferred_out",
                "deferred_in_out",
            ]
            .contains(&mode)
            {
                return Err(self.error(
                    w,
                    declaration,
                    "unrecognized deferred attribute has unverified implicit callback effects",
                ));
            }
            let Some(callback) = callback else {
                return Err(self.error(
                    w,
                    declaration,
                    "deferred attribute has no inspectable callback",
                ));
            };
            let candidates = self.r.candidates(callback);
            let Some(target) = candidates
                .first()
                .copied()
                .filter(|_| candidates.len() == 1)
            else {
                return Err(self.error(
                    w,
                    callback,
                    "deferred attribute has unverified implicit callback effects",
                ));
            };
            let Some(target_signature) = self.r.sources[target.file].role(target.node, "type")
            else {
                return Err(self.error(
                    w,
                    callback,
                    "deferred callback has no inspectable procedure signature",
                ));
            };
            let mut types = vec![];
            for role in ["params", "results"] {
                let include = match mode {
                    "deferred_in" => role == "params",
                    "deferred_out" => role == "results",
                    "deferred_in_out" => true,
                    _ => false,
                };
                if !include {
                    continue;
                }
                for (_, annotation) in self.r.fields(signature, role) {
                    let Some(annotation) = annotation else {
                        return Err(self.error(
                            w,
                            callback,
                            "deferred callback arguments have unresolved types",
                        ));
                    };
                    let typ = self.r.specialized_type(annotation, &w.bindings);
                    types.push(if name.ends_with("_by_ptr") {
                        Type::Pointer(Box::new(typ))
                    } else {
                        typ
                    });
                }
            }
            let target_signature = Ref {
                node: target_signature,
                ..target
            };
            let mut bindings = w.bindings.clone();
            bindings.extend(self.r.parameter_bindings(target_signature, &types));
            // Native signature validation remains Odin's responsibility. The
            // analysis checks any default expressions the synthesized call uses.
            let fields =
                crate::caller_metadata::params(&self.r.sources[target.file], target_signature.node);
            for (_, field) in fields.iter().skip(types.len()) {
                if let Some(node) = self.r.sources[target.file].role(*field, "default") {
                    self.walk(
                        &Work {
                            bindings: bindings.clone(),
                            ..w.clone()
                        },
                        Ref { node, ..target },
                    )?;
                }
            }
            let path = self.traces.len();
            self.traces.push((callback, w.path));
            self.queue.push_back(Work {
                at: target,
                bindings,
                arguments: vec![Fact::Unknown; fields.len()],
                root: w.root,
                path: Some(path),
                procedure: true,
            });
        }
        Ok(())
    }
}
