//! Exact request aliases avoid constructing helpers on unchanged snapshots.
//! Dependency-granular prepared/scalar keys remain the edit-friendly path.
use super::*;
use crate::semantics::Ref;
use crate::{
    evaluationcache::{canonical, EvaluationCache},
    evaluationinputs::Inputs,
};

fn bindings(bindings: &crate::specialization::Bindings) -> Value {
    json!(bindings
        .iter()
        .map(|((site, name), binding)| {
            // Type constants may exceed JSON's integer range. Debug spelling is
            // deterministic within the compiler identity and preserves all bits.
            json!([
                site,
                name,
                format!("{:?}", binding.typ),
                binding.value,
                binding.canonical,
                binding.condition
            ])
        })
        .collect::<Vec<_>>())
}

impl Project {
    pub(super) fn evaluation_request_key(
        &self,
        expression: Ref,
        context: &crate::evaluation::Context<'_>,
        mode: crate::evaluation::Mode,
    ) -> Option<String> {
        let _phase = crate::profile::span("cache.request_key");
        let cache = context.cache?;
        // Nested helper views have additional lexical retention/bridge state.
        // Keep their full emitted-code validation rather than approximate it.
        if self.evaluation.is_some() || !context.batch.is_empty() || !cache.borrow().enabled {
            return None;
        }
        let identity = cache
            .borrow_mut()
            .identity(&self.frontend.odin, &self.options.root)?;
        let sources = cache.borrow_mut().source_key(&self.sources)?;
        let cases = context
            .cases
            .iter()
            .map(|(site, cases)| {
                json!([
                    site,
                    cases
                        .iter()
                        .map(|(name, case)| json!([
                            name,
                            bindings(&case.bindings),
                            case.value,
                            case.skipped
                        ]))
                        .collect::<Vec<_>>()
                ])
            })
            .collect::<Vec<_>>();
        let frames = context
            .frames
            .iter()
            .map(|(site, frame)| json!([site, bindings(frame)]))
            .collect::<Vec<_>>();
        let mode = match mode {
            crate::evaluation::Mode::Value => json!(["value"]),
            crate::evaluation::Mode::Condition => json!(["condition"]),
            crate::evaluation::Mode::Case(index) => json!(["case", index]),
        };
        let flags = self
            .options
            .flags
            .iter()
            .filter(|flag| !flag.starts_with("-out:") && flag.as_str() != "-keep-executable" && crate::cli::helper_flag(flag))
            .collect::<Vec<_>>();
        Some(sha256(&canonical(
            &json!({"schema":2,"identity":identity,
                "entry":self.options.entry,"file_entry":self.file_entry,"flags":flags,
                "test":self.frontend.test,"target":self.frontend.target,"overlays":self.options.overlays,
                "collections":self.collections,"sources":sources,"expression":expression,"mode":mode,"comptime":self.options.comptime.name(),
                "results":context.results.iter().collect::<Vec<_>>(),"skips":context.skips.iter().collect::<Vec<_>>(),
                "bindings":bindings(context.bindings),"cases":cases,"frames":frames,
                "witnesses":self.evaluation_witnesses.iter().collect::<Vec<_>>()
            }),
        )))
    }

    pub(super) fn evaluation_request_inputs(&self, helper: &Self) -> Inputs {
        let mut inputs = Inputs::default();
        let known = self
            .sources
            .iter()
            .map(|source| source.path.as_path())
            .collect::<HashSet<_>>();
        let mut additional_packages = BTreeSet::new();
        for source in &helper.sources {
            // Current project syntax/overlays already belong to the request
            // key. Also cover resources and any helper-only discoveries.
            inputs.source(source, !known.contains(source.path.as_path()));
            if !known.contains(source.path.as_path()) {
                inputs.binding(&source.path);
                if let Some(directory) = source.path.parent() {
                    additional_packages.insert(directory.to_path_buf());
                }
            }
        }
        for directory in additional_packages {
            let suffixes = vec!["odin".into(), "thor".into()];
            match crate::evaluationinputs::members(&directory, &suffixes) {
                Ok(names) => inputs.package(
                    &directory,
                    &names
                        .into_iter()
                        .map(|name| directory.join(name))
                        .collect::<Vec<_>>(),
                    &suffixes,
                ),
                Err(_) => inputs.usable = false,
            }
        }
        inputs
    }

    pub(super) fn remember_evaluation_request(
        cache: &EvaluationCache,
        request: Option<&str>,
        target: Option<&str>,
        inputs: &Inputs,
    ) {
        if let Some(target) = target.filter(|_| inputs.valid()) {
            cache.write_record(
                "request-v1",
                request,
                &json!({"target":target,"inputs":inputs.data}),
            );
        }
    }
}
