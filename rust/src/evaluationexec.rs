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

struct Temporary(PathBuf);
impl Temporary {
    fn new() -> Result<Self> {
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
                Ok(()) => return Ok(Self(fs::canonicalize(path)?)),
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
        let _ = fs::remove_dir_all(&self.0);
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
pub(crate) fn capture(command: &mut Command, timeout: Duration) -> Result<Captured> {
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
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
        if start.elapsed() >= timeout {
            timed_out = true;
            let _ = child.kill();
            break child.wait()?;
        }
        thread::sleep(Duration::from_millis(10));
    };
    let (stdout, large_out) = output
        .join()
        .map_err(|_| Error::new("helper stdout reader failed"))??;
    let (stderr, large_err) = errors
        .join()
        .map_err(|_| Error::new("helper stderr reader failed"))??;
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
        let mut resolver = Resolver::with_flags(&self.sources, &self.options.flags);
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
            source.edits.extend(
                source.info["selection_tags"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Span::from_wire)
                    .map(|span| Edit {
                        span,
                        text: String::new(),
                    }),
            );
            let view = plan.files.get_mut(&source.path).unwrap();
            for (index, node) in source.nodes.iter().enumerate() {
                let id = Ref {
                    file,
                    node: NodeId(index),
                };
                let name = if node.kind == "Ident" {
                    Some(source.text(NodeId(index)).to_string())
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
                    if resolver.symbol(id).is_none() {
                        view.values.insert(NodeId(index), value);
                    }
                }
            }
        }
        Ok(Self {
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
            },
            frontend: self.frontend.clone(),
            sources,
            destination,
            outputs: BTreeMap::new(),
            errors: HashMap::new(),
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
        let work = Temporary::new()?;
        let mut helper =
            self.evaluation_helper_with_context(expression, work.0.join("source"), context, mode)?;
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
        if let Some(value) = cache.as_mut().and_then(|cache| {
            cache.get(
                key.as_deref()
                    .filter(|key| !context.cache_misses.contains(*key)),
            )
        }) {
            return Ok(Executed { value, target: key });
        }
        let executable = work.0.join(format!("helper{}", env::consts::EXE_SUFFIX));
        let mut command = Command::new(&helper.frontend.odin);
        command
            .envs(&helper.native_environment)
            .arg("build")
            .arg(&target)
            .args(&flags)
            .arg(format!("-out:{}", executable.display()))
            .arg("-json-errors");
        let built =
            capture(&mut command, timeout.max(Duration::from_secs(30))).map_err(|error| {
                fail(format!(
                    "{label} could not start the helper compiler: {error}"
                ))
            })?;
        if built.timed_out {
            return Err(fail(format!("{label} helper compilation timed out.")));
        }
        if !built.status.success() {
            let errors = serde_json::from_slice::<Value>(&built.stderr)
                .ok()
                .and_then(|packet| packet["errors"].as_array().cloned())
                .unwrap_or_default();
            let mut diagnostics = helper.translate(&errors);
            for diagnostic in &mut diagnostics {
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
            let detail = detail
                .find(") ")
                .map(|end| &detail[end + 2..])
                .unwrap_or(&detail);
            let mut error = fail(format!(
                "{label} execution failed ({}). {}",
                result.status,
                if detail.is_empty() {
                    "The helper terminated without a runtime error message; run this call in a debug build to investigate.".into()
                } else {
                    detail.chars().take(300).collect::<String>()
                }
            ));
            if let Some(origin) = helper.evaluation_failure_origin(&stderr) {
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
        crate::values::validate(&value)
            .map_err(|error| fail(error.to_string()))?;
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
        }
        Ok(Executed { value, target: key })
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
