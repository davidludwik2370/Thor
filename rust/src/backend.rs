//! Relax only Odin's default single-declaration control-block diagnostic.
//! Required initializers and scope cleanup stay in their original declarations.
use super::evaluationexec::Captured;
use super::*;
use crate::{
    ast::{NodeId, Span},
    emit::{MappedEdit, Origin},
};

fn unused(error: &Value) -> Option<&str> {
    if error["type"] != "error" {
        return None;
    }
    error["msgs"][0]
        .as_str()?
        .strip_prefix('\'')?
        .strip_suffix("' declared but not used")
}
fn finish(mut result: Captured, warnings: &[Value]) -> Captured {
    if !warnings.is_empty() {
        if let Ok(mut packet) = if result.stderr.is_empty() {
            Ok(json!({"errors":[]}))
        } else {
            serde_json::from_slice::<Value>(&result.stderr)
        } {
            let mut errors = warnings.to_vec();
            errors.extend(packet["errors"].as_array().into_iter().flatten().cloned());
            packet["errors"] = json!(errors);
            result.stderr = serde_json::to_vec(&packet).expect("diagnostic JSON is serializable");
        }
    }
    result
}
impl Project {
    pub(crate) fn run_backend<F>(&mut self, command: &mut Command, mut run: F) -> Result<Captured>
    where
        F: FnMut(&mut Command) -> Result<Captured>,
    {
        let strict = command.get_args().any(|arg| {
            matches!(
                arg.to_str(),
                Some("-vet" | "-vet-unused" | "-vet-unused-variables" | "-vet-unused-procedures")
            )
        });
        let warnings_as_errors = command.get_args().any(|arg| arg == "-warnings-as-errors");
        let mut warnings = vec![];
        let mut single_module = command.get_args().any(|arg| arg == "-use-single-module");
        loop {
            let mut result = run(command)?;
            if !result.stderr.is_empty() && result.status.code().is_some() {
                let flags = command.get_args().map(|arg| arg.to_string_lossy().into_owned()).collect::<Vec<_>>();
                let packet = crate::compiler_output::decode(&result.stderr, result.status.success(), crate::compiler_output::reporting(&flags));
                if !single_module && !result.status.success()
                    && crate::compiler_output::generic_module_collision(&packet) {
                    let arguments = command.get_args().filter(|arg| *arg != "-use-separate-modules")
                        .map(std::ffi::OsStr::to_os_string).collect::<Vec<_>>();
                    let mut retry = Command::new(command.get_program());
                    retry.args(arguments).arg("-use-single-module");
                    if let Some(directory) = command.get_current_dir() { retry.current_dir(directory); }
                    for (name, value) in command.get_envs() {
                        if let Some(value) = value { retry.env(name, value); }
                        else { retry.env_remove(name); }
                    }
                    *command = retry;
                    single_module = true;
                    crate::profile::count("backend.generic_module_retries", 1);
                    continue;
                }
                result.stderr = serde_json::to_vec(&packet)?;
            }
            if result.status.success()
                || result.status.code().is_none()
                || result.timed_out
                || result.oversized
                || strict
            {
                return Ok(finish(result, &warnings));
            }
            let Ok(packet) = serde_json::from_slice::<Value>(&result.stderr) else {
                return Ok(finish(result, &warnings));
            };
            let Some(errors) = packet["errors"].as_array() else {
                return Ok(finish(result, &warnings));
            };
            let mut files = BTreeMap::<PathBuf, (Source, Vec<MappedEdit>, BTreeSet<NodeId>)>::new();
            let mut current = vec![];
            let mut remaining = vec![];
            for error in errors {
                let Some(name) = unused(error) else {
                    remaining.push(error.clone());
                    continue;
                };
                let Some(file) = error["pos"]["file"]
                    .as_str()
                    .and_then(|p| absolute(Path::new(p)).ok())
                else {
                    remaining.push(error.clone());
                    continue;
                };
                let Some((_, emitted)) = self
                    .outputs
                    .get(&file)
                    .filter(|_| file.starts_with(&self.destination))
                else {
                    remaining.push(error.clone());
                    continue;
                };
                if !files.contains_key(&file) {
                    let mut frontend = self.frontend.clone();
                    frontend.cache_dir = None;
                    let native = frontend.parse_overlay(&file, false, Some(&emitted.text))?;
                    files.insert(file.clone(), (native, vec![], BTreeSet::new()));
                }
                let (native, edits, wrapped) = files.get_mut(&file).unwrap();
                let explicit_vet = native.info["file_tags"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .any(|tag| {
                        tag.trim_start_matches("#+").split_whitespace().next() == Some("vet")
                    });
                let offset = error["pos"]["offset"].as_u64().unwrap_or(u64::MAX) as usize;
                let declaration = native
                    .nodes
                    .iter()
                    .enumerate()
                    .find(|(index, node)| {
                        node.kind == "Declaration"
                            && native
                                .group(NodeId(*index), "names")
                                .iter()
                                .any(|name| native.node(*name).span.start == offset)
                    })
                    .map(|(i, _)| NodeId(i));
                let block = declaration
                    .and_then(|id| native.node(id).parent)
                    .filter(|id| native.node(*id).kind == "Block");
                let (Some(declaration), Some(block)) = (declaration, block) else {
                    remaining.push(error.clone());
                    continue;
                };
                if explicit_vet {
                    remaining.push(error.clone());
                    continue;
                }
                let Some(origin) = emitted.lookup(offset, 1) else {
                    remaining.push(error.clone());
                    continue;
                };
                let original = self
                    .sources
                    .iter()
                    .find(|source| source.path == Path::new(&origin.file));
                let Some((original, ident)) = original.and_then(|source| {
                    source
                        .nodes
                        .iter()
                        .enumerate()
                        .find(|(index, node)| {
                            node.kind == "Ident"
                                && node.span.start == origin.span.start
                                && source.text(NodeId(*index)) == name
                        })
                        .map(|(i, _)| (source, NodeId(i)))
                }) else {
                    remaining.push(error.clone());
                    continue;
                };
                if !original
                    .ancestors(ident)
                    .any(|id| original.node(id).kind == "Procedure")
                {
                    remaining.push(error.clone());
                    continue;
                }
                let span = original.node(ident).span;
                let mut translated = crate::diagnostic::Error::at(
                    original,
                    span,
                    error["msgs"][0].as_str().unwrap_or_default(),
                )
                .diagnostics
                .remove(0);
                translated["type"] = json!("warning");
                translated["stage"] = error["stage"].clone();
                if translated["stage"].is_null() {
                    translated.as_object_mut().unwrap().remove("stage");
                }
                let (end_line, end_column) = original.position(span.end);
                translated["pos"]["end_line"] = json!(end_line);
                translated["pos"]["end_column"] = json!(end_column);
                current.push(translated);
                let origin = Origin {
                    file: original.path.to_string_lossy().into_owned(),
                    span,
                    kind: "unused-local".into(),
                    exact: false,
                };
                let end = native.node(declaration).span.end;
                if !edits.iter().any(|edit| edit.span.start == end) {
                    edits.push(MappedEdit {
                        span: Span::new(end, end),
                        text: "\n_ = 0\n".into(),
                        origin: origin.clone(),
                    });
                }
                if native.raw.as_bytes().get(native.node(block).span.start) != Some(&b'{')
                    && wrapped.insert(block)
                {
                    let parent = native.node(block).parent.unwrap_or(block);
                    let token = native.info["do_tokens"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Span::from_wire)
                        .filter(|token| {
                            native.node(parent).span.start <= token.start
                                && token.start < native.node(block).span.start
                        })
                        .next_back();
                    let Some(token) = token else {
                        return Err(Error::new(
                            "cannot locate the generated control block's do token",
                        ));
                    };
                    edits.push(MappedEdit {
                        span: token,
                        text: String::new(),
                        origin: origin.clone(),
                    });
                    let span = native.node(block).span;
                    edits.push(MappedEdit {
                        span: Span::new(span.start, span.start),
                        text: "{\n".into(),
                        origin: origin.clone(),
                    });
                    edits.push(MappedEdit {
                        span: Span::new(span.end, span.end),
                        text: "\n}".into(),
                        origin,
                    });
                }
            }
            if current.is_empty() {
                return Ok(finish(result, &warnings));
            }
            warnings.extend(current);
            if warnings_as_errors || remaining.iter().any(|error| error["type"] == "error") {
                let mut remaining_packet = json!({"errors":remaining});
                if let Some(information) = packet.get("information") { remaining_packet["information"] = information.clone(); }
                result.stderr = serde_json::to_vec(&remaining_packet)?;
                return Ok(finish(result, &warnings));
            }
            for (path, (_, edits, _)) in files {
                if edits.is_empty() {
                    continue;
                }
                let (_, emitted) = self.outputs.get_mut(&path).unwrap();
                *emitted = emitted.rewrite(&edits).map_err(Error::new)?;
                atomic_write(&path, emitted.text.as_bytes())?;
                if self.options.maps
                    || self.options.command == "transpile"
                    || self.options.flags.iter().any(|flag| flag == "-debug")
                {
                    let sources = self
                        .sources
                        .iter()
                        .map(|source| {
                            (
                                source.path.to_string_lossy().into_owned(),
                                source.raw.clone(),
                            )
                        })
                        .collect::<Vec<_>>();
                    let mut map = emitted.map_json(&path, &sources);
                    map["generated_sha256"] = json!(sha256(emitted.text.as_bytes()));
                    atomic_write(
                        &PathBuf::from(format!("{}.map.json", path.display())),
                        &serde_json::to_vec(&map)?,
                    )?;
                }
            }
        }
    }
}
