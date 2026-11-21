//! Fatal reporting is outside the ordinary-execution allocation contract.
//! Arguments to conditional checks still execute on their successful path.
use super::*;
use std::{collections::BTreeMap, path::PathBuf, sync::OnceLock};

#[derive(Clone, Copy)]
enum Kind {
    Panic,
    Guard,
    Check { disabled: bool },
}
#[derive(Default)]
pub(super) struct State {
    verified: HashSet<PathBuf>,
}

fn builtin(name: &str) -> Option<Kind> {
    Some(match name {
        "panic" | "panic_contextless" | "unimplemented" | "unimplemented_contextless" => {
            Kind::Panic
        }
        "assert" | "assert_contextless" => Kind::Check { disabled: true },
        "ensure" | "ensure_contextless" => Kind::Check { disabled: false },
        _ => return None,
    })
}
fn identity(r: &Resolver<'_>, callee: Ref) -> Option<(&'static str, String, Kind)> {
    for package in ["base:runtime", "core:fmt", "core:log"] {
        let name = if r.sources[callee.file].node(callee.node).kind == "Procedure" {
            let source = &r.sources[callee.file];
            let root = source.info["odin_root"].as_str()?;
            if source.path.parent()
                != Some(
                    std::path::Path::new(root)
                        .join(package.replace(':', "/"))
                        .as_path(),
                )
            {
                continue;
            }
            let declaration = source.node(callee.node).parent?;
            source
                .group(declaration, "names")
                .first()
                .map(|name| source.text(*name).to_owned())?
        } else {
            if !r.standard_library(callee, package) {
                continue;
            }
            let Some(name) = r.library_alias_member(callee, package) else {
                continue;
            };
            name
        };
        let kind = if package == "base:runtime" {
            builtin(&name).or_else(||["bounds_check_error_contextless","bounds_check_error_with_context","bounds_check_error_loc",
                "multi_pointer_slice_expr_error_contextless","multi_pointer_slice_expr_error_with_context",
                "slice_expr_error_hi_contextless","slice_expr_error_hi_with_context","slice_expr_error_hi_loc",
                "slice_expr_error_lo_hi_contextless","slice_expr_error_lo_hi_with_context","slice_expr_error_lo_hi_loc",
                "matrix_bounds_check_error_contextless","matrix_bounds_check_error_with_context",
                "make_slice_error_loc","make_dynamic_array_error_loc","make_map_expr_error_loc"].contains(&name.as_str()).then_some(Kind::Guard))
        } else {
            match name.as_str() {
                "panic" | "panicf" => Some(Kind::Panic),
                "assert" | "assertf" => Some(Kind::Check { disabled: true }),
                "ensure" | "ensuref" => Some(Kind::Check { disabled: false }),
                _ => None,
            }
        }?;
        return Some((package, name, kind));
    }
    None
}
fn expected(relative: &str) -> Option<&'static str> {
    static DATA: OnceLock<BTreeMap<String, String>> = OnceLock::new();
    DATA.get_or_init(|| {
        serde_json::from_str::<serde_json::Value>(include_str!("../data/noalloc-io-sdk.json"))
            .expect("reviewed SDK effect profile")["sdk"]
            .as_object()
            .expect("SDK profile entries")
            .iter()
            .map(|(key, value)| (key.clone(), value.as_str().expect("source hash").into()))
            .collect()
    })
    .get(relative)
    .map(String::as_str)
}
impl Check<'_, '_> {
    fn failure_source(&mut self, w: &Work, site: Ref, relative: &str) -> Result<()> {
        let Some(root) = self.r.sources[site.file].info["odin_root"].as_str() else {
            return Err(self.error(
                w,
                site,
                "cannot locate the SDK for fatal-reporting verification",
            ));
        };
        let path = PathBuf::from(root).join(relative);
        if self.failure.verified.contains(&path) {
            return Ok(());
        }
        let bytes = if let Some(overlay) = self.overlays.get(&path) {
            overlay.as_bytes().to_vec()
        } else if let Some(source) = self.r.sources.iter().find(|source| source.path == path) {
            source.raw.as_bytes().to_vec()
        } else {
            std::fs::read(&path).map_err(|error| {
                self.error(
                    w,
                    site,
                    &format!("cannot inspect fatal-reporting SDK source: {error}"),
                )
            })?
        };
        if expected(relative) != Some(crate::digest::sha256(&bytes).as_str()) {
            return Err(self.error(w,site,&format!("fatal-reporting SDK profile does not match {}; the changed implementation requires review",path.display())));
        }
        self.failure.verified.insert(path);
        Ok(())
    }
    pub(super) fn failure_call(
        &mut self,
        w: &Work,
        at: Ref,
        callee: Ref,
        native: Option<&str>,
    ) -> Result<bool> {
        let direct = native.and_then(builtin);
        if native.is_some() && direct.is_none() {
            return Ok(false);
        }
        // Most checked calls name an ordinary local procedure directly. Its
        // definition supplies identity without three library-alias searches or
        // a second callback-fact query on every call in a large graph.
        let definition = self.r.symbol(callee).and_then(|index| {
            let symbol = &self.r.symbols[index];
            let node = symbol.value.filter(|node| {
                symbol.constant
                    && self.r.sources[symbol.reference.file].node(*node).kind == "Procedure"
            })?;
            Some(Ref {
                node,
                file: symbol.reference.file,
            })
        });
        let library = if direct.is_none() {
            if let Some(definition) = definition {
                identity(self.r, definition)
            } else {
                identity(self.r, callee).or_else(|| {
                    if let Fact::Function(target) = self.flow.fact(self.r, callee, &w.bindings) {
                        identity(self.r, target)
                    } else {
                        None
                    }
                })
            }
        } else {
            None
        };
        let Some(kind) = direct.or_else(|| library.as_ref().map(|(_, _, kind)| *kind)) else {
            return Ok(false);
        };
        // Explicit SDK summaries verify the implementation, rather than trusting
        // an arbitrary function named assertf/panicf or a collection override.
        let relative = match library.as_ref().map(|(package, _, _)| *package) {
            Some("base:runtime") if matches!(kind,Kind::Guard)=>Some("base/runtime/error_checks.odin"),
            Some("core:fmt") => Some("core/fmt/fmt.odin"),
            Some("core:log") => Some("core/log/log.odin"),
            _ => Some("base/runtime/core_builtin.odin"),
        };
        if let Some(relative) = relative {
            self.failure_source(w, at, relative)?;
        }
        // Both explicit and conditional reporters rely on the handler ABI.
        self.failure_source(w, at, "base/runtime/core.odin")?;
        if matches!(kind,Kind::Guard){
            for node in self.r.sources[at.file].group(at.node,"args").to_vec(){self.walk(w,Ref{node,..at})?;}
            crate::profile::count("no_alloc.failure_calls",1);
            return Ok(true);
        }
        let Kind::Check { disabled } = kind else {
            self.flow.terminated = true;
            crate::profile::count("no_alloc.failure_calls", 1);
            return Ok(true);
        };
        if disabled
            && (direct.is_some() || crate::parallelcompile::known_callback(self.r, callee))
            && self.r.flags.iter().any(|flag| flag == "-disable-assert")
        {
            return Ok(true);
        }
        // A compile-time false condition makes every message argument part of
        // a fatal diagnostic. Mutable call-context facts are insufficient here:
        // named arguments evaluated earlier could change a condition variable.
        let supplied = self.r.sources[at.file]
            .group(at.node, "args")
            .iter()
            .map(|node| Ref {
                node: self.r.sources[at.file]
                    .role(*node, "value")
                    .unwrap_or(*node),
                ..at
            })
            .collect::<HashSet<_>>();
        let layout = if library.is_some() && !crate::parallelcompile::known_callback(self.r, callee)
        {
            let typ = self.r.specialized_type(callee, &w.bindings);
            let Type::Procedure(signature) = base_type(&typ) else {
                return Err(self.error(
                    w,
                    at,
                    "failure-check callback argument layout is unresolved",
                ));
            };
            let args = self.r.sources[at.file]
                .group(at.node, "args")
                .iter()
                .map(|node| Ref { node: *node, ..at })
                .collect::<Vec<_>>();
            Some(self.r.arrange_arguments(*signature, &args).ok_or_else(|| {
                self.error(
                    w,
                    at,
                    "failure-check callback argument layout is unresolved",
                )
            })?)
        } else {
            None
        };
        let condition = if let Some(layout) = &layout {
            layout.first().and_then(|group| group.first()).copied()
        } else {
            crate::borroweval::Evaluator {
                resolver: self.r,
                arena: &mut crate::borrowflow::Arena::default(),
            }
            .argument(at, "condition", 0)
        };
        let fatal = condition
            .is_some_and(|cond| self.r.context_condition(cond, &w.bindings) == Some(false));
        let args = self.r.sources[at.file].group(at.node, "args").to_vec();
        for node in args {
            let value = self.r.sources[at.file].role(node, "value").unwrap_or(node);
            if !fatal || condition == Some(Ref { node: value, ..at }) {
                self.walk(w, Ref { node, ..at })?;
            }
        }
        for arg in layout
            .iter()
            .flatten()
            .flatten()
            .filter(|arg| !supplied.contains(arg))
        {
            if !fatal || condition == Some(*arg) {
                self.walk(w, *arg)?;
            }
        }
        if fatal {
            self.flow.terminated = true;
        }
        crate::profile::count("no_alloc.failure_calls", 1);
        Ok(true)
    }
}
