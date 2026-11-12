//! Isolated native helpers. No Python process, user callbacks or project entry
//! points are used to evaluate a request.
use super::*;
#[path = "evaluationidentity.rs"]
mod identity;
use crate::{
    ast::{Edit, NodeId, Span},
    semantics::Ref,
};
use std::{
    io::Read,
    thread,
    time::{Duration, Instant},
};

#[cfg(unix)]
use std::sync::atomic::{AtomicUsize, Ordering};

// The low bit records SIGTERM. The remaining bits count live evaluation
// directories so the CLI can defer exit until their children and files are gone.
#[cfg(unix)]
static EVALUATION_STATE: AtomicUsize = AtomicUsize::new(0);

#[cfg(unix)]
pub fn cancel_active_evaluation() -> bool {
    EVALUATION_STATE.fetch_or(1, Ordering::Relaxed) >= 2
}

#[cfg(unix)]
pub fn evaluation_cancelled() -> bool {
    EVALUATION_STATE.load(Ordering::Relaxed) & 1 != 0
}

#[cfg(not(unix))]
fn evaluation_cancelled() -> bool {
    false
}

pub(super) fn check_cancellation() -> Result<()> {
    if evaluation_cancelled() {
        return Err(Error::new("Compile-time evaluation cancelled."));
    }
    Ok(())
}

struct ActiveEvaluation;
impl ActiveEvaluation {
    fn enter() -> Result<Self> {
        #[cfg(unix)]
        EVALUATION_STATE.fetch_add(2, Ordering::Relaxed);
        let active = Self;
        check_cancellation()?;
        Ok(active)
    }
}
impl Drop for ActiveEvaluation {
    fn drop(&mut self) {
        #[cfg(unix)]
        EVALUATION_STATE.fetch_sub(2, Ordering::Relaxed);
    }
}

struct Temporary {
    path: PathBuf,
    _active: ActiveEvaluation,
}
impl Temporary {
    fn new() -> Result<Self> {
        let active = ActiveEvaluation::enter()?;
        for sequence in 0..100 {
            let clock = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let path = env::temp_dir().join(format!(
                "thor-rust-run-{}-{clock}-{sequence}",
                std::process::id()
            ));
            let mut builder = fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            match builder.create(&path) {
                Ok(()) => {
                    let mut work = Self { path, _active: active };
                    work.path = fs::canonicalize(&work.path)?;
                    return Ok(work);
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.into()),
            }
        }
        Err(Error::new(
            "could not create an isolated compile-time helper directory",
        ))
    }
}
impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}
pub(crate) struct Captured {
    pub(crate) status: std::process::ExitStatus,
    pub(crate) stdout: Vec<u8>,
    pub(crate) stderr: Vec<u8>,
    pub(crate) timed_out: bool,
    pub(crate) oversized: bool,
}
pub(crate) struct Executed {
    pub(crate) value: Value,
    pub(crate) target: Option<String>,
}

struct HelperProcess {
    child: std::process::Child,
    stopped: bool,
}
impl HelperProcess {
    fn spawn(command: &mut Command) -> std::io::Result<Self> {
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        Ok(Self {
            child: command.spawn()?,
            stopped: false,
        })
    }
    fn stop(&mut self) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        #[cfg(unix)]
        {
            // Compiler descendants inherit the helper's isolated group and pipes.
            // Terminate them before joining the output readers, including when
            // the group leader has already exited. Keep the library safe Rust.
            let _ = Command::new("/bin/kill")
                .args(["-KILL", "--", &format!("-{}", self.child.id())])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
impl Drop for HelperProcess {
    fn drop(&mut self) {
        self.stop();
    }
}

fn drain(mut pipe: impl Read, limit: usize) -> std::io::Result<(Vec<u8>, bool)> {
    let mut bytes = vec![];
    let mut chunk = [0; 8192];
    let mut oversized = false;
    loop {
        let count = pipe.read(&mut chunk)?;
        if count == 0 {
            break;
        }
        let available = limit.saturating_sub(bytes.len());
        oversized |= count > available;
        bytes.extend_from_slice(&chunk[..count.min(available)]);
    }
    Ok((bytes, oversized))
}

#[cfg(all(test, unix))]
mod process_tests {
    use super::*;

    #[test]
    fn inherited_pipes_do_not_extend_a_helper_deadline_or_keep_children_alive() {
        for script in ["sleep 2 & wait", "sleep 2 & exit 0"] {
            let started = Instant::now();
            let result = capture(
                Command::new("/bin/sh").args(["-c", script]),
                Duration::from_millis(100),
            )
            .expect("capture helper process");
            let elapsed = started.elapsed();
            if elapsed >= Duration::from_millis(900) {
                panic!("inherited helper pipes delayed cleanup for {elapsed:?}: {script}");
            }
            if script.ends_with("wait") != result.timed_out {
                panic!("helper deadline changed its result: {script}");
            }
        }
    }
}

pub(crate) fn capture(command: &mut Command, timeout: Duration) -> Result<Captured> {
    // Toolchain identity queries can run before a helper directory is created.
    // Their children need the same signal-driven cleanup as evaluation itself.
    let _active = ActiveEvaluation::enter()?;
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut process = HelperProcess::spawn(command)?;
    let child = &mut process.child;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| Error::new("helper stdout unavailable"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| Error::new("helper stderr unavailable"))?;
    let output = thread::spawn(move || drain(stdout, 16 * 1024 * 1024));
    let errors = thread::spawn(move || drain(stderr, 16 * 1024 * 1024));
    let start = Instant::now();
    let mut timed_out = false;
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        let cancelled = evaluation_cancelled();
        if cancelled || start.elapsed() >= timeout {
            timed_out = !cancelled;
            let _ = child.kill();
            break child.wait()?;
        }
        thread::sleep(Duration::from_millis(10));
    };
    process.stop();
    let (stdout, large_out) = output
        .join()
        .map_err(|_| Error::new("helper stdout reader failed"))??;
    let (stderr, large_err) = errors
        .join()
        .map_err(|_| Error::new("helper stderr reader failed"))??;
    check_cancellation()?;
    Ok(Captured {
        status,
        stdout,
        stderr,
        timed_out,
        oversized: large_out || large_err,
    })
}
impl Project {
    /// Construct a helper from the already selected source snapshot. In
    /// particular, a file-entry request never rediscovers unrelated files.
    pub fn evaluation_helper(&self, expression: Ref, destination: PathBuf) -> Result<Self> {
        self.evaluation_helper_with_context(
            expression,
            destination,
            &crate::evaluation::Context::default(),
            crate::evaluation::Mode::Value,
        )
    }
    pub(crate) fn evaluation_helper_with_context(
        &self,
        expression: Ref,
        destination: PathBuf,
        context: &crate::evaluation::Context<'_>,
        mode: crate::evaluation::Mode,
    ) -> Result<Self> {
        let mut resolver = self.resolver();
        resolver.evaluated = context.results.clone();
        resolver.prepare_contexts();
        crate::specialization::Specializations::apply_bindings(context.bindings, &mut resolver);
        let mut plan =
            crate::evaluation::Plan::with_context(&mut resolver, expression, context, mode)?;
        let entry = self.sources[plan.entry_file]
            .path
            .parent()
            .ok_or_else(|| Error::new("evaluation source has no package"))?
            .to_path_buf();
        let flags = self
            .options
            .flags
            .iter()
            .filter(|flag| {
                !["-out:", "-build-mode:", "-vet", "-target:"]
                    .iter()
                    .any(|prefix| flag.starts_with(prefix))
                    && !matches!(flag.as_str(), "-keep-executable" | "-no-entry-point")
            })
            .cloned()
            .collect();
        let mut sources = self.sources.clone();
        let mode = self
            .options
            .flags
            .iter()
            .find_map(|flag| flag.strip_prefix("-build-mode:"))
            .unwrap_or("exe");
        let mode = match mode {
            "dll" | "shared" | "dynamic" => "Dynamic",
            "static" | "lib" => "Static",
            "obj" | "object" => "Object",
            "asm" | "assembly" | "assembler" => "Assembly",
            "llvm" | "llvm-ir" => "LLVM_IR",
            _ => "Executable",
        };
        for (file, source) in sources.iter_mut().enumerate() {
            if source.shared_native
                && !source.path.starts_with(self.options.root.join("runtime"))
                && !crate::evaluation::compiler_support(source)
            {
                if let Some(span) = source.node(NodeId(0)).spans.get("package").copied() {
                    let name = format!(
                        "thor_eval_{}",
                        &sha256(source.path.parent().unwrap().to_string_lossy().as_bytes())[..12]
                    );
                    source.edits.push(Edit { span, text: name });
                }
            }
            if source.path.parent() == Some(entry.as_path()) {
                if let Some(span) = source.node(NodeId(0)).spans.get("package").copied() {
                    source.edits.push(Edit {
                        span,
                        text: "main".into(),
                    });
                }
            }
            let selection_edits =
                source.info["selection_tags"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Span::from_wire)
                    .map(|span| Edit {
                        span,
                        text: String::new(),
                    }).collect::<Vec<_>>();
            source.edits.extend(selection_edits);
            let view = plan.files.get_mut(&source.path).unwrap();
            for (index, node) in source.nodes.iter().enumerate() {
                let id = Ref {
                    file,
                    node: NodeId(index),
                };
                let name = if node.kind == "Ident" {
                    let name = source.text(id.node);
                    if !matches!(name, "ODIN_TEST" | "ODIN_BUILD_MODE") {
                        continue;
                    }
                    let binding = node.parent.is_some_and(|parent| {
                        let kind = source.node(parent).kind.as_str();
                        matches!(kind, "PolyType" | "EnumType" | "ImplicitSelector")
                            || matches!(kind, "Declaration" | "Field" | "Range" | "TypeSwitch")
                                && source.group(parent, "names").contains(&id.node)
                            || matches!(kind, "Selector" | "FieldValue")
                                && source.role(parent, "field") == Some(id.node)
                    });
                    (!binding && resolver.native_builtin(id, name)).then(|| name.to_owned())
                } else if node.kind == "Selector" {
                    if !source.role(id.node, "field").is_some_and(|field| {
                        matches!(source.text(field), "ODIN_TEST" | "ODIN_BUILD_MODE")
                    }) {
                        continue;
                    }
                    crate::evaluation::library_member(&resolver, id, "base:builtin")
                } else {
                    None
                };
                // A shadowing declaration always wins over builtin context.
                let value = match name.as_deref() {
                    Some("ODIN_TEST") => Some(self.frontend.test.to_string()),
                    Some("ODIN_BUILD_MODE") => Some(format!("type_of(ODIN_BUILD_MODE).{mode}")),
                    _ => None,
                };
                if let Some(value) = value {
                    view.values.insert(id.node, value);
                }
            }
        }
        Ok(Self {
            publish_layout: None,
            native_override: None,
            collected_diagnostics: None,
            options: Options {
                command: "build".into(),
                entry,
                output: None,
                cache: None,
                odin: self.options.odin.clone(),
                flags,
                args: vec![],
                no_cache: self.options.no_cache,
                json: true,
                root: self.options.root.clone(),
                maps: false,
                tracking: "off".into(),
                no_sanitize: true,
                overlays: self.options.overlays.clone(),
                editor_info: false,
                run_timeout: std::time::Duration::from_secs(5),
                clear_cache: false,
                cache_report: false,
                raw_diagnostics: false,
                vet: None,
                vet_apply: false,
                vet_ask: false,
            },
            frontend: self.frontend.clone(),
            sources,
            destination,
            outputs: BTreeMap::new(),
            errors: HashMap::new(),
            editor_info: vec![],
            evaluated: context.results.clone(),
            specialized: context.cases.clone(),
            evaluation_scheduled: true,
            evaluation_skips: context.skips.clone(),
            evaluation_witnesses: self.evaluation_witnesses.clone(),
            related: HashMap::new(),
            type_sites: HashMap::new(),
            pure_error_sites: HashMap::new(),
            pure_call_paths: BTreeMap::new(),
            collections: self.collections.clone(),
            common: self.common.clone(),
            file_entry: false,
            memory: crate::memory::Memory::select("build", &[], "off", true)?,
            native_environment: BTreeMap::new(),
            backend_runtime: PathBuf::new(),
            evaluation: Some(plan),
            type_probes: BTreeMap::new(),
            type_probe_imports: BTreeMap::new(),
            source_facts: self.source_facts.clone(),
            resolver_index: self.resolver_index.clone(),
            permission_cache: std::cell::RefCell::default(),
            parsed_imports: HashMap::new(),
        })
    }
    pub fn execute_evaluation(&self, expression: Ref, timeout: Duration) -> Result<Value> {
        self.execute_evaluation_with_context(
            expression,
            timeout,
            &crate::evaluation::Context::default(),
            crate::evaluation::Mode::Value,
        )
        .map(|executed| executed.value)
    }
    pub(crate) fn execute_evaluation_with_context(
        &self,
        expression: Ref,
        timeout: Duration,
        context: &crate::evaluation::Context<'_>,
        mode: crate::evaluation::Mode,
    ) -> Result<Executed> {
        let _phase = crate::profile::span("evaluation.native_helper");
        check_cancellation()?;
        let source = &self.sources[expression.file];
        let site = source
            .node(expression.node)
            .parent
            .filter(|id| source.node(*id).kind == "Run")
            .unwrap_or(expression.node);
        let label = if source.node(site).kind == "Run" {
            "#run"
        } else {
            "Compile-time"
        };
        let fail = |message: String| Error::at(source, source.node(site).span, message);
        if let Some((os, arch)) = &self.frontend.target {
            let host_os = if env::consts::OS == "macos" {
                "darwin"
            } else {
                env::consts::OS
            };
            let host_arch = match env::consts::ARCH {
                "x86_64" => "amd64",
                "aarch64" => "arm64",
                arch => arch,
            };
            if os != host_os || arch != host_arch {
                return Err(fail("Compile-time execution supports native targets only; host execution cannot evaluate a cross-target program.".into()));
            }
        }
        let request_key = self.evaluation_request_key(expression, context, mode);
        if let Some(cache) = context.cache {
            let record = cache.borrow().read_record("request-v1", request_key.as_deref());
            if let Some(record) = record {
                if let Some(target) = record["target"].as_str().filter(|target| !context.cache_misses.contains(*target)) {
                    if record.get("inputs").is_some_and(|inputs|
                        crate::evaluationinputs::Inputs::new(Some(inputs.clone())).valid()) {
                        let value = cache.borrow_mut().get(Some(target));
                        if let Some(value) = value {
                            check_cancellation()?;
                            cache.borrow_mut().prepared_hits += 1;
                            crate::profile::count("evaluation.request_hits", 1);
                            return Ok(Executed { value, target: Some(target.into()) });
                        }
                    }
                }
            }
        }
        crate::profile::count("evaluation.helper_preparations", 1);
        let work = Temporary::new()?;
        let mut helper =
            self.evaluation_helper_with_context(expression, work.path.join("source"), context, mode)?;
        if let Some(plan) = &helper.evaluation {
            helper.specialized.retain(|node, cases| {
                if !plan.emits(&helper.sources[node.file], node.node) {
                    return false;
                }
                cases.retain(|_, case| {
                    case.bindings
                        .values()
                        .all(|binding| plan.emits_type(&binding.typ, &helper.sources))
                });
                !cases.is_empty()
            });
        }
        // Nominal scope positions belong to this generated helper, rather
        // than an earlier helper or the eventual user executable.
        let mut cases = std::mem::take(&mut helper.specialized);
        helper.resolve_evaluation_identities(
            &helper.evaluated,
            &helper.evaluation_skips,
            &mut cases,
        )?;
        helper.specialized = cases;
        let target = helper.lower()?;
        let flags = helper.native_flags();
        let mut cache = context.cache.map(|cache| cache.borrow_mut());
        let generated = if cache.as_ref().is_some_and(|cache| cache.enabled) {
            helper
                .outputs
                .iter()
                .map(|(path, (_, emitted))| (path.clone(), emitted.text.as_bytes().to_vec()))
                .collect()
        } else {
            BTreeMap::new()
        };
        let key = cache.as_mut().and_then(|cache| {
            cache.key(
                &self.sources,
                crate::evaluationcache::HelperCode {
                    destination: &helper.destination,
                    flags: &flags,
                    target: &target,
                    generated: &generated,
                },
                &helper.frontend.odin,
                &self.options.root,
            )
        });
        let request_inputs = request_key.as_ref().map(|_| self.evaluation_request_inputs(&helper));
        if let Some(value) = cache.as_mut().and_then(|cache| {
            cache.get(
                key.as_deref()
                    .filter(|key| !context.cache_misses.contains(*key)),
            )
        }) {
            if let (Some(cache), Some(inputs)) = (&cache, &request_inputs) {
                Self::remember_evaluation_request(cache, request_key.as_deref(), key.as_deref(), inputs);
            }
            return Ok(Executed { value, target: key });
        }
        let executable = work.path.join(format!("helper{}", env::consts::EXE_SUFFIX));
        let mut command = Command::new(&helper.frontend.odin);
        command
            .envs(&helper.native_environment)
            .arg("build")
            .arg(&target)
            .args(&flags)
            .arg(format!("-out:{}", executable.display()))
            .arg("-json-errors");
        if !flags
            .iter()
            .any(|flag| flag.starts_with("-max-error-count:"))
        {
            command.arg("-max-error-count:1000");
        }
        crate::profile::count("evaluation.helper_compilations", 1);
        let _backend = crate::profile::span("evaluation.helper_backend");
        let built = helper
            .run_backend(&mut command, |command| {
                capture(command, timeout.max(Duration::from_secs(30)))
            })
            .map_err(|error| {
                fail(format!(
                    "{label} could not start the helper compiler: {error}"
                ))
            })?;
        drop(_backend);
        if built.timed_out {
            return Err(fail(format!("{label} helper compilation timed out.")));
        }
        if !built.status.success() {
            let errors = serde_json::from_slice::<Value>(&built.stderr)
                .ok()
                .and_then(|packet| packet["errors"].as_array().cloned())
                .unwrap_or_default();
            let mut diagnostics = helper.translate(&errors);
            let package_names = helper.sources.iter().filter_map(|source| {
                let span = *source.node(NodeId(0)).spans.get("package")?;
                let name = source.edits.iter().find(|edit| edit.span == span && edit.text.starts_with("thor_eval_"))?;
                Some((format!("{}.", name.text), format!("{}.", source.slice(span))))
            }).collect::<BTreeMap<_, _>>();
            for diagnostic in &mut diagnostics {
                if let Some(messages) = diagnostic["msgs"].as_array_mut() {
                    for message in messages {
                        if let Some(text) = message.as_str() {
                            let restored = package_names.iter().fold(text.to_owned(), |text, (internal, original)| {
                                text.replace(internal, original)
                            });
                            *message = json!(restored);
                        }
                    }
                }
                if diagnostic["pos"].is_null() {
                    diagnostic["pos"] = fail(String::new()).diagnostics[0]["pos"].clone();
                }
                if diagnostic["pos"]["file"].as_str().is_some_and(|file| {
                    helper
                        .sources
                        .iter()
                        .any(|unit| unit.shared_native && unit.path == Path::new(file))
                }) {
                    let note = json!({"pos":diagnostic["pos"],"msgs":["Purity violation in standard-library source."]});
                    diagnostic["pos"] = fail(String::new()).diagnostics[0]["pos"].clone();
                    diagnostic["related"]
                        .as_array_mut()
                        .map(|notes| notes.push(note.clone()))
                        .unwrap_or_else(|| {
                            diagnostic["related"] = json!([note]);
                        });
                }
            }
            if !diagnostics.is_empty() {
                return Err(Error {
                    message: "Compile-time helper compilation failed.".into(),
                    diagnostics,
                    unsupported: false,
                    exit_status: 1,
                });
            }
            let detail = String::from_utf8_lossy(if built.stderr.is_empty() {
                &built.stdout
            } else {
                &built.stderr
            })
            .chars()
            .take(1000)
            .collect::<String>();
            return Err(fail(format!(
                "{label} helper compilation failed ({}). {detail}",
                built.status
            )));
        }
        let result = capture(&mut Command::new(executable), timeout).map_err(|error| {
            fail(format!(
                "{label} could not start the evaluation helper: {error}"
            ))
        })?;
        if result.timed_out {
            return Err(fail(format!(
                "{label} execution exceeded the {} second timeout.",
                timeout.as_secs_f64()
            )));
        }
        if !result.status.success() {
            let stderr = String::from_utf8_lossy(&result.stderr);
            let detail = stderr
                .lines()
                .find(|line| {
                    [
                        "panic",
                        "assertion",
                        "runtime error",
                        "out of range",
                        "division by zero",
                    ]
                    .iter()
                    .any(|word| line.to_lowercase().contains(word))
                })
                .unwrap_or("");
            let detail = helper.remap_references(detail);
            let detail = detail.find("panic:").or_else(||detail.find("assertion failure:"))
                .map(|start| &detail[start..]).unwrap_or(&detail);
            let mut error = fail(format!(
                "{label} execution failed ({}). {}",
                result.status,
                if detail.is_empty() {
                    "The helper terminated without a runtime error message; run this call in a debug build to investigate.".into()
                } else {
                    detail.chars().take(300).collect::<String>()
                }
            ));
            if detail.contains("panic: unexpected failure") && stderr.lines().any(|line|line.starts_with("  expression: ")) {
                if let Some(messages)=error.diagnostics[0]["msgs"].as_array_mut() {
                    messages.extend(stderr.lines().filter(|line|["  expression: ","  procedure: ","  error: "].iter().any(|prefix|line.starts_with(prefix)))
                        .map(|line|json!(line.trim().chars().take(2000).collect::<String>())));
                }
                if let Some(origin)=self.original_optional_failure_origin(&stderr) {
                    let unit=self.sources.iter().find(|unit|unit.path==Path::new(&origin.file)).unwrap();
                    let (line,column)=unit.position(origin.span.start);
                    error.diagnostics[0]["related"]=json!([{"pos":{"file":origin.file,"offset":origin.span.start,"end_offset":origin.span.end,"line":line,"column":column},"message":"or_panic failed here."}]);
                }
            }
            let origin=error.diagnostics[0]["related"].is_null().then(||helper.evaluation_failure_origin(&stderr)).flatten();
            if let Some(origin) = origin {
                let unit = helper
                    .sources
                    .iter()
                    .find(|unit| unit.path == Path::new(&origin.file))
                    .unwrap();
                let (line, column) = unit.position(origin.span.start);
                error.diagnostics[0]["related"] = json!([{"pos":{"file":origin.file,"offset":origin.span.start,"end_offset":origin.span.end,"line":line,"column":column},
                    "msgs":["Compile-time execution failed here."]}]);
            }
            error.diagnostics[0]["stage"] = json!("evaluation");
            return Err(error);
        }
        if result.oversized {
            return Err(fail("Compile-time result exceeds 16 MiB.".into()));
        }
        let value =
            crate::values::decode(&result.stdout).map_err(|error| fail(error.to_string()))?;
        crate::values::validate(&value).map_err(|error| fail(error.to_string()))?;
        if let Some(cache) = &mut cache {
            if key.is_some() {
                let current = cache.key(
                    &self.sources,
                    crate::evaluationcache::HelperCode {
                        destination: &helper.destination,
                        flags: &flags,
                        target: &target,
                        generated: &generated,
                    },
                    &helper.frontend.odin,
                    &self.options.root,
                );
                if current != key {
                    return Err(fail("#run resource inputs changed during evaluation; compile again with stable inputs.".into()));
                }
            }
            cache.put(key.as_deref(), &value);
            if let Some(inputs) = &request_inputs {
                Self::remember_evaluation_request(cache, request_key.as_deref(), key.as_deref(), inputs);
            }
        }
        Ok(Executed { value, target: key })
    }
    fn original_optional_failure_origin(&self, stderr: &str) -> Option<crate::emit::Origin> {
        for line in stderr.lines().filter(|line|line.contains("panic: unexpected failure")) {
            for source in &self.sources {
                let path=source.path.to_string_lossy();
                let Some(start)=line.find(path.as_ref()) else {continue;};
                let tail=&line[start+path.len()..];
                let coordinates=tail.strip_prefix('(').and_then(|tail|tail.split(')').next())
                    .or_else(||tail.strip_prefix(':').map(|tail|tail.split(": ").next().unwrap_or(tail)));
                let Some((row,column))=coordinates.and_then(|pair|pair.split_once(':')) else {continue;};
                let (Ok(row),Ok(column))=(row.parse::<usize>(),column.parse::<usize>()) else {continue;};
                if row==0||column==0 {continue;}
                let Some(line)=source.raw.split_inclusive('\n').nth(row-1) else {continue;};
                let base=source.raw.split_inclusive('\n').take(row-1).map(str::len).sum::<usize>();
                let offset=base+line.chars().take(column-1).map(char::len_utf8).sum::<usize>();
                let end=source.nodes.iter().find(|node|node.kind=="OrPanic"&&node.span.start==offset)
                    .map(|node|node.span.end).unwrap_or((offset+1).min(source.raw.len()));
                return Some(crate::emit::Origin{file:path.into_owned(),span:Span::new(offset,end),kind:"or-panic".into(),exact:false});
            }
        }
        None
    }
    fn evaluation_failure_origin(&self, stderr: &str) -> Option<crate::emit::Origin> {
        for line in stderr.lines() {
            for source in &self.sources {
                let pattern = format!("{}(", source.path.display());
                let Some(start) = line.find(&pattern) else {
                    continue;
                };
                let Some(pair) = line[start + pattern.len()..]
                    .split(')')
                    .next()
                    .and_then(|value| value.split_once(':').or_else(|| value.split_once(',')))
                else {
                    continue;
                };
                let (Ok(row), Ok(column)) = (
                    pair.0.trim().parse::<usize>(),
                    pair.1.trim().parse::<usize>(),
                ) else {
                    continue;
                };
                if row == 0 || column == 0 {
                    continue;
                }
                let base = source
                    .raw
                    .split_inclusive('\n')
                    .take(row - 1)
                    .map(str::len)
                    .sum::<usize>();
                let offset = source
                    .raw
                    .get(base..)?
                    .chars()
                    .take(column - 1)
                    .map(char::len_utf8)
                    .sum::<usize>();
                if !source.path.starts_with(self.options.root.join("runtime")) {
                    return Some(crate::emit::Origin {
                        file: source.path.to_string_lossy().into_owned(),
                        span: Span::new(base + offset, (base + offset + 1).min(source.raw.len())),
                        kind: "evaluation".into(),
                        exact: false,
                    });
                }
            }
            for (path, (_, emitted)) in &self.outputs {
                let pattern = format!("{}(", path.display());
                let Some(start) = line.find(&pattern) else {
                    continue;
                };
                let pair = line[start + pattern.len()..]
                    .split(')')
                    .next()?
                    .split_once(':')?;
                let (Ok(row), Ok(column)) = (pair.0.parse::<usize>(), pair.1.parse::<usize>())
                else {
                    continue;
                };
                if row == 0 || column == 0 {
                    continue;
                }
                let base = emitted
                    .text
                    .split_inclusive('\n')
                    .take(row - 1)
                    .map(str::len)
                    .sum::<usize>();
                let offset = emitted
                    .text
                    .get(base..)?
                    .chars()
                    .take(column - 1)
                    .map(char::len_utf8)
                    .sum::<usize>();
                if let Some(origin) = emitted.lookup(base + offset, 1) {
                    if !Path::new(&origin.file).starts_with(self.options.root.join("runtime")) {
                        return Some(origin);
                    }
                }
            }
        }
        None
    }
}
