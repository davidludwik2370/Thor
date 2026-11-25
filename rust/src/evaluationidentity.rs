//! Resolve native local nominal identities in the original scopes, then map
//! their generated scope offsets into the actual program's emitted source.
use super::*;
use crate::{
    semantics::{scope, Type},
    specialization::{Cases, TypeCode},
};
type BindingKey = (Ref, String);
type CaseBinding = (Ref, String, BindingKey);

fn owner(typ: &Type) -> Option<Ref> {
    match typ {
        Type::Nominal(at, _)
        | Type::Struct(at)
        | Type::Record(at, _)
        | Type::Enum(at)
        | Type::Union(at, _) => Some(*at),
        Type::Array { elem, .. } | Type::Matrix{elem,..}
            | Type::Vector { elem, .. }
        | Type::Pointer(elem)
        | Type::Slice(elem)
        | Type::Dynamic(elem) => owner(elem),
        _ => None,
    }
}
fn offset(project: &Project, at: Ref) -> Option<usize> {
    let source = &project.sources[at.file];
    let position = source.node(at.node).span.start;
    project
        .outputs
        .values()
        .filter(|(file, _)| *file == at.file)
        .find_map(|(_, emitted)| {
            emitted
                .segments
                .iter()
                .find(|segment| {
                    segment.origin.file == source.path.to_string_lossy()
                        && (segment.origin.exact
                            && segment.origin.span.start <= position
                            && position < segment.origin.span.end
                            || segment.origin.kind == "evaluation-scope"
                                && segment.origin.span.start == position)
                })
                .map(|segment| segment.start + position - segment.origin.span.start + 1)
        })
}
fn scopes(name: &str, offsets: &BTreeMap<String, String>) -> String {
    let mut result = String::new();
    let mut rest = name;
    let mut quote = None;
    let mut escaped = false;
    while let Some(character) = rest.chars().next() {
        if let Some(delimiter) = quote {
            if escaped {
                escaped = false;
            } else if character == '\\' && delimiter != '`' {
                escaped = true;
            } else if character == delimiter {
                quote = None;
            }
        } else if ['"', '`'].contains(&character) && result.ends_with("$$") {
            quote = Some(character);
        } else if let Some(tail) = rest.strip_prefix("::$") {
            result.push_str("::$");
            let count = tail.bytes().take_while(u8::is_ascii_digit).count();
            let number = &tail[..count];
            result.push_str(offsets.get(number).map(String::as_str).unwrap_or(number));
            rest = &tail[count..];
            continue;
        }
        result.push(character);
        rest = &rest[character.len_utf8()..];
    }
    result
}

impl Project {
    fn evaluation_snapshot(
        &self,
        destination: PathBuf,
        results: &BTreeMap<Ref, Value>,
        skips: &BTreeMap<Ref, bool>,
        cases: &Cases,
    ) -> Result<Self> {
        let mut options = self.options.clone();
        options.output = None;
        options
            .flags
            .retain(|flag| !flag.starts_with("-out:") && flag != "-keep-executable" && crate::cli::helper_flag(flag));
        Ok(Self {
            timings: BTreeMap::new(),
            publish_layout: None,
            native_override: None,
            collected_diagnostics: None,
            options,
            frontend: self.frontend.clone(),
            sources: self.sources.clone(),
            destination,
            outputs: BTreeMap::new(),
            errors: HashMap::new(),
            editor_info: vec![],
            evaluated: results.clone(),
            evaluation_skips: skips.clone(),
            evaluation_witnesses: self.evaluation_witnesses.clone(),
            specialized: cases.clone(),
            evaluation_scheduled: true,
            related: HashMap::new(),
            type_sites: HashMap::new(),
            pure_error_sites: HashMap::new(),
            pure_call_paths: BTreeMap::new(),
            collections: self.collections.clone(),
            common: self.common.clone(),
            file_entry: self.file_entry,
            memory: self.memory.clone(),
            native_environment: BTreeMap::new(),
            backend_runtime: PathBuf::new(),
            evaluation: self.evaluation.clone(),
            type_probes: BTreeMap::new(),
            type_probe_imports: BTreeMap::new(),
            source_facts: self.source_facts.clone(),
            resolver_index: self.resolver_index.clone(),
            resolver_state: std::cell::RefCell::new(None),
            permission_cache: std::cell::RefCell::default(),
            parsed_imports: HashMap::new(),
        })
    }
    pub(crate) fn resolve_evaluation_identities(
        &self,
        results: &BTreeMap<Ref, Value>,
        skips: &BTreeMap<Ref, bool>,
        cases: &mut Cases,
    ) -> Result<()> {
        if cases.is_empty() {
            return Ok(());
        }
        let _phase = crate::profile::span("evaluation.nominal_identities");
        let mut resolver = self.resolver();
        resolver.set_evaluations(results.clone());
        resolver.prepare_contexts();
        let mut pending: BTreeMap<(Ref, String, String), Vec<CaseBinding>> = BTreeMap::new();
        let mut imports: BTreeMap<usize, BTreeMap<String, String>> = BTreeMap::new();
        for (request, instances) in cases.iter() {
            for (key, case) in instances.iter().filter(|(_, case)| case.skipped != Some(true)) {
                for (slot, binding) in &case.bindings {
                    if binding.value.is_some() || binding.canonical.is_empty() {
                        continue;
                    }
                    let Some(at) = owner(&binding.typ) else {
                        continue;
                    };
                    let source = &self.sources[at.file];
                    if !source
                        .node(at.node)
                        .parent
                        .is_some_and(|node| source.node(node).kind == "Declaration")
                    {
                        continue;
                    }
                    let mut context = TypeCode::new(at.file, Some(at));
                    context.allow_imports = true;
                    let code = context.render(&binding.typ, &resolver).map_err(|_| {
                        Error::at(
                            source,
                            source.node(at.node).span,
                            "Cannot name the nominal type in its defining scope.",
                        )
                    })?;
                    for (alias, directory) in context.imports {
                        imports.entry(at.file).or_default().insert(
                            alias,
                            crate::evaluation::dependency_path(source, &directory),
                        );
                    }
                    pending
                        .entry((at, binding.condition.clone(), code))
                        .or_default()
                        .push((*request, key.clone(), slot.clone()));
                }
            }
        }
        if pending.is_empty() {
            return Ok(());
        }
        let work = Temporary::new()?;
        let mut probe = self.evaluation_snapshot(work.path.join("probe"), results, skips, cases)?;
        probe.type_probe_imports = imports;
        let mut markers = vec![];
        for (index, ((at, condition, code), _)) in pending.iter().enumerate() {
            let source = &self.sources[at.file];
            let declaration = source.node(at.node).parent.unwrap();
            let marker = format!("THOR_TYPE_ID_{index}:");
            let code = format!(
                "#panic({}+{}meta.type_canonical_name({code}))",
                json!(marker),
                source.prefix
            );
            let code = if condition.is_empty() {
                code
            } else {
                format!("when {condition}{{{code}}}")
            };
            probe
                .type_probes
                .entry(Ref {
                    file: at.file,
                    node: scope(source, declaration),
                })
                .or_default()
                .push(code);
            markers.push(marker);
        }
        let target = probe.lower()?;
        let mut command = Command::new(&probe.frontend.odin);
        command
            .envs(&probe.native_environment)
            // `check` rejects build-only flags such as -o:speed. Tagged
            // #panic statements stop this build in the native checker; the
            // resulting program is never executed.
            .arg("build")
            .arg(target)
            .args(probe.native_flags())
            .arg(format!("-out:{}", crate::native_compat::artifact_path(&work.path,"type-probe",&probe.native_flags()).display()))
            .arg("-json-errors")
            .arg("-max-error-count:10000");
        if probe.file_entry {
            command.arg("-file");
        }
        if probe.frontend.test
            && !self
                .options
                .flags
                .iter()
                .any(|flag| flag.starts_with("-build-mode:"))
        {
            command.arg("-build-mode:test");
        }
        let checked = capture(&mut command, Duration::from_secs(30))?;
        let first = pending.keys().next().unwrap().0;
        let source = &self.sources[first.file];
        let fail = |message: &str| Error::at(source, source.node(first.node).span, message);
        if checked.timed_out {
            return Err(fail("Generic type identity checking timed out."));
        }
        let packet = serde_json::from_slice::<Value>(&checked.stderr).unwrap_or_default();
        let mut originals = BTreeMap::new();
        for (marker, (_, group)) in markers.iter().zip(pending.iter()) {
            let names = packet["errors"]
                .as_array()
                .into_iter()
                .flatten()
                .flat_map(|error| error["msgs"].as_array().into_iter().flatten())
                .filter_map(Value::as_str)
                .filter_map(|message| {
                    message
                        .split_once(marker)
                        .map(|(_, name)| name.trim().to_string())
                })
                .collect::<BTreeSet<_>>();
            if names.len() != 1 {
                return Err(fail(
                    "Cannot resolve the nominal type for this compile-time specialization.",
                ));
            }
            for key in group {
                originals.insert(key.clone(), names.first().unwrap().clone());
            }
        }
        for (key, canonical) in &originals {
            cases
                .get_mut(&key.0)
                .unwrap()
                .get_mut(&key.1)
                .unwrap()
                .bindings
                .get_mut(&key.2)
                .unwrap()
                .canonical = canonical.clone();
        }
        let mut actual = self.evaluation_snapshot(work.path.join("actual"), results, skips, cases)?;
        for _ in 0..12 {
            actual.specialized = cases.clone();
            // Lowering adds generated collections. Reuse the original
            // collection set so their aliases stay the same across passes.
            actual.collections.clone_from(&self.collections);
            actual.lower()?;
            let mut changed = false;
            for ((at, _, _), group) in &pending {
                let mut offsets = BTreeMap::new();
                for scope in self.sources[at.file].ancestors(at.node).filter(|node| {
                    crate::semantics::is_scope(&self.sources[at.file], *node)
                        && !["Procedure", "File"]
                            .contains(&self.sources[at.file].node(*node).kind.as_str())
                }) {
                    let scope = Ref {
                        file: at.file,
                        node: scope,
                    };
                    if let (Some(before), Some(after)) =
                        (offset(&probe, scope), offset(&actual, scope))
                    {
                        offsets.insert(before.to_string(), after.to_string());
                    }
                }
                for key in group {
                    let canonical = scopes(&originals[key], &offsets);
                    let binding = cases
                        .get_mut(&key.0)
                        .unwrap()
                        .get_mut(&key.1)
                        .unwrap()
                        .bindings
                        .get_mut(&key.2)
                        .unwrap();
                    changed |= binding.canonical != canonical;
                    binding.canonical = canonical;
                }
            }
            if !changed {
                return Ok(());
            }
        }
        Err(fail("Local type identity scheduling did not converge."))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn scope_offsets_preserve_constant_parameter_contents() {
        let offsets = BTreeMap::from([("12".into(), "123".into()), ("61".into(), "75".into())]);
        for (name, expected) in [
            (
                "main::outer::$12::Local::$61",
                "main::outer::$123::Local::$75",
            ),
            (
                r#"main::outer(S:$$"::$12")::Local::$61"#,
                r#"main::outer(S:$$"::$12")::Local::$75"#,
            ),
            (
                r#"main::outer(S:$$"a\"::$61")::Local::$61"#,
                r#"main::outer(S:$$"a\"::$61")::Local::$75"#,
            ),
            (
                "main::outer(S:$$`::$61`)::Local::$61",
                "main::outer(S:$$`::$61`)::Local::$75",
            ),
            (
                "main::someone's_file.odin::Local::$61",
                "main::someone's_file.odin::Local::$75",
            ),
            (
                "main::quoted\"file.odin::Local::$61",
                "main::quoted\"file.odin::Local::$75",
            ),
            (
                "主::outer(S:$$\"é🙂::$61\")::型::$61",
                "主::outer(S:$$\"é🙂::$61\")::型::$75",
            ),
        ] {
            let actual = scopes(name, &offsets);
            assert_eq!(actual, expected);
        }
    }
}
