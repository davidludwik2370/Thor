mod help;

use serde_json::json;
use std::{env, path::PathBuf, process::ExitCode};
use thor_compiler::{
    diagnostic::{Error, Result},
    project::{absolute, Options, Project},
    VERSION,
};

#[cfg(unix)]
extern "C" fn terminate(_: libc::c_int) {
    if !thor_compiler::project::cancel_active_evaluation() {
        // Outside an isolated evaluation, preserve immediate termination.
        // SAFETY: _exit is async-signal-safe and never returns.
        unsafe { libc::_exit(128 + libc::SIGTERM) }
    }
}

fn main() -> ExitCode {
    let _profile = thor_compiler::profile::Session::from_env();
    let _command = thor_compiler::profile::span("command");
    #[cfg(unix)]
    // SAFETY: terminate has the C signal-handler ABI and only touches a
    // lock-free atomic or calls _exit.
    unsafe {
        libc::signal(libc::SIGTERM, terminate as *const () as libc::sighandler_t);
    }
    let args: Vec<String> = env::args().skip(1).collect();
    let compiler_args = args
        .iter()
        .take_while(|arg| arg.as_str() != "--")
        .cloned()
        .collect::<Vec<_>>();
    let json_output = compiler_args
        .iter()
        .any(|arg| matches!(arg.as_str(), "-json-errors" | "--diagnostic-format=json"))
        || compiler_args
            .windows(2)
            .any(|args| args == ["--diagnostic-format", "json"]);
    let result = run(args);
    #[cfg(unix)]
    if thor_compiler::project::evaluation_cancelled() {
        return ExitCode::from((128 + libc::SIGTERM) as u8);
    }
    match result {
        Ok(status) => ExitCode::from(status.clamp(0, 255) as u8),
        Err(error) => {
            if error.exit_status == 2 {
                eprintln!("{}\nthor: error: {}\nRun thor help for help.", help::USAGE, error.message);
                return ExitCode::from(2);
            }
            let exit_status = if error.unsupported {
                3
            } else {
                error.exit_status
            };
            let errors = if error.diagnostics.is_empty() {
                vec![json!({"type":"error","stage":"thor-rust","pos":null,"msgs":[error.message]})]
            } else {
                error.diagnostics
            };
            if json_output {
                eprintln!(
                    "{}",
                    json!({"error_count":errors.iter().filter(|error|error["type"]=="error").count(),"errors":errors})
                );
            } else {
                for error in errors {
                    let overlay=compiler_args.iter().find_map(|arg|arg.strip_prefix("--overlay="))
                        .or_else(||compiler_args.windows(2).find(|args|args[0]=="--overlay").map(|args|args[1].as_str()));
                    let raw=overlay.and_then(|path|thor_compiler::diagnostic::overlay_source(&error,std::path::Path::new(path)));
                    eprintln!("{}",thor_compiler::diagnostic::text(&error,raw.as_deref(),compiler_args.iter().any(|arg|arg=="-terse-errors"),compiler_args.iter().any(|arg|arg=="-error-pos-style:unix")));
                }
            }
            ExitCode::from(exit_status)
        }
    }
}
fn parse_run_timeout(value: &str) -> Result<std::time::Duration> {
    let seconds = value
        .parse::<f64>()
        .ok()
        .filter(|seconds| seconds.is_finite() && *seconds >= 0.0)
        .ok_or_else(|| Error::usage("--run-timeout requires a finite, nonnegative number of seconds; 0 means unlimited"))?;
    if seconds == 0.0 {
        return Ok(std::time::Duration::ZERO);
    }
    let duration = std::time::Duration::try_from_secs_f64(seconds)
        .map_err(|_| Error::usage("--run-timeout is too large to represent as a duration"))?;
    // A positive sub-nanosecond limit must stay finite after conversion.
    Ok(duration.max(std::time::Duration::from_nanos(1)))
}

fn run(mut args: Vec<String>) -> Result<i32> {
    let setup = thor_compiler::profile::span("command.setup");
    if args
        .iter()
        .take_while(|arg| arg.as_str() != "--")
        .any(|arg| arg == "--version")
    {
        println!("Thor {VERSION}");
        return Ok(0);
    }
    if args.first().is_some_and(|arg| arg == "--capabilities") {
        println!(
            "{}",
            json!({
                "engine": "rust",
                "status": "experimental",
                "complete": false,
                "python_fallback": false,
                "reference_command": "thor-python",
                "parallel_lowering": false,
                "parallel_work": {
                    "source_parsing": true,
                    "source_map_serialization": true,
                    "semantic_lowering": false,
                    "default_worker_limit": 4,
                    "maximum_workers": 32,
                    "worker_environment": "THOR_JOBS"
                },
                "supported": [
                    "shared Odin parser and arena AST",
                    "lexical symbols, native type facts, overloads and generic bindings",
                    "structured source maps, original diagnostics and live debugger metadata",
                    "native Odin passthrough and selective/wildcard imports",
                    "contextual bracket arrays, membership and type-list constraints",
                    "constant array concatenation and string repetition",
                    "numeric power, sequence bounds and typed loop bindings",
                    "multiple-result switches, compile switches and nonreturning arms",
                    "Set operations and temporary ownership",
                    "automatic using feature and generic inline defaults",
                    "f-string formatting, dynamic widths and expression-scoped ownership",
                    "optional results and multiple-value or_panic",
                    "runtime and compile-time collection comprehensions and expanded extrema",
                    "slice privileges, contextual mutation summaries and TLS metadata",
                    "Integer/Rational constants, runtime ownership and compile-time results",
                    "pure functions, checked ordinary callees and owned results",
                    "native project creation and verified vet corrections",
                    "application publishing, icons and native dependency relocation",
                    "macOS allocation tracking, ASan locations and source overlays"
                ],
                "evaluation": {
                    "isolated_native_helpers": true,
                    "generic_frames": true,
                    "scalar_and_aggregate_results": true,
                    "contents_cache": true,
                    "prepared_scalar_cache": true,
                    "verified_scalar_sites": true,
                    "compile_time_comprehensions": true,
                    "big_number_results": true
                },
                "pending": [
                    "native-host verification on Windows and Linux",
                    "guarded specialization and ownership paths outside the acceptance corpus"
                ],
                "optimization_opportunities": [
                    "further permission-analysis and type-discovery optimization",
                    "parallel semantic lowering where dependencies permit"
                ]
            })
        );
        return Ok(0);
    }
    if args.is_empty() {
        print!("{}", help::overview());
        return Ok(0);
    }
    if let Some(status) = help::dispatch(&args)? { return Ok(status); }
    if let Some(status) = thor_compiler::cli::early(&args)? { return Ok(status); }
    if args.first().is_some_and(|arg| arg == "migrate") {
        return thor_compiler::migrate::main(&args[1..], &thor_compiler::cli::root()?);
    }
    if args.first().is_some_and(|arg| arg == "new") {
        let root = env::var_os("THOR_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap()
                    .to_path_buf()
            });
        return thor_compiler::project_template::main(&args[1..], &absolute(&root)?);
    }
    let command = args.remove(0);
    if !thor_compiler::cli::source_command(&command) {
        return Err(Error::usage(format!("unknown command '{command}'")));
    }
    let mut skip = false;
    let mut source = None;
    for (index, argument) in args.iter().enumerate() {
        if skip {
            skip = false;
            continue;
        }
        if argument == "--" {
            break;
        }
        if matches!(
            argument.as_str(),
            "--output-dir"
                | "--cache-dir"
                | "--odin"
                | "--diagnostic-format"
                | "--overlay"
                | "--run-timeout"
                | "--track-allocations"
                | "--app-name"
                | "--app-id"
                | "--app-version"
                | "--app-icon"
                | "--app-type"
                | "--app-resource"
                | "--app-library"
                | "--publish-dir"
        ) {
            skip = true;
            continue;
        }
        if !argument.starts_with('-') {
            source = Some(index);
            break;
        }
    }
    let entry = source.map_or_else(|| PathBuf::from("."), |index| PathBuf::from(args.remove(index)));
    let mut options = Options {
        odin_compatible: false,
        native_policy_probe: false,
        command,
        entry,
        output: None,
        cache: None,
        odin: env::var("THOR_ODIN").unwrap_or_else(|_| "odin".into()),
        flags: vec![],
        args: vec![],
        no_cache: false,
        json: false,
        root: PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_path_buf(),
        maps: false,
        tracking: "auto".into(),
        no_sanitize: false,
        overlays: Default::default(),
        editor_info: false,
        run_timeout: std::time::Duration::ZERO,
        clear_cache: false,
        cache_report: false,
        raw_diagnostics: false,
        vet: None,
        vet_apply: false,
        vet_ask: false,
    };
    if let Some(root) = env::var_os("THOR_ROOT") {
        options.root = absolute(&PathBuf::from(root))?;
    }
    let mut publishing = thor_compiler::publishing::Options::default();
    let mut packaging_options = false;
    let mut expanded = vec![];
    let mut runtime_arguments = false;
    for argument in args {
        runtime_arguments |= argument == "--";
        if let Some((flag, value)) = argument.split_once('=').filter(|_| !runtime_arguments) {
            if matches!(
                flag,
                "--output-dir"
                    | "--cache-dir"
                    | "--odin"
                    | "--diagnostic-format"
                    | "--overlay"
                    | "--run-timeout"
                    | "--track-allocations"
                    | "--app-name"
                    | "--app-id"
                    | "--app-version"
                    | "--app-icon"
                    | "--app-type"
                    | "--app-resource"
                    | "--app-library"
                    | "--publish-dir"
            ) {
                expanded.extend([flag.to_string(), value.to_string()]);
                continue;
            }
        }
        expanded.push(argument);
    }
    let mut args = expanded.into_iter();
    while let Some(argument) = args.next() {
        match argument.as_str() {
            "--app-name" | "--app-id" | "--app-version" | "--app-icon" | "--app-type"
            | "--app-resource" | "--app-library" | "--publish-dir" => {
                let value = args
                    .next()
                    .ok_or_else(|| Error::usage(format!("{argument} requires a value")))?;
                packaging_options = true;
                match argument.as_str() {
                    "--app-name" => publishing.name = Some(value),
                    "--app-id" => publishing.identifier = Some(value),
                    "--app-version" => publishing.version = Some(value),
                    "--app-icon" => publishing.icon = Some(value),
                    "--app-type" => {
                        if !matches!(value.as_str(), "gui" | "console") {
                            return Err(Error::usage("--app-type: choose gui or console"));
                        }
                        publishing.app_type = Some(value);
                    }
                    "--app-resource" => publishing.resources.push(value),
                    "--app-library" => publishing.libraries.push(value),
                    "--publish-dir" => publishing.directory = Some(PathBuf::from(value)),
                    _ => unreachable!(),
                }
            }
            "--" => {
                options.args = args.collect();
                break;
            }
            "--output-dir" => {
                options.output =
                    Some(PathBuf::from(args.next().ok_or_else(|| {
                        Error::usage("--output-dir requires a path")
                    })?))
            }
            "--cache-dir" => {
                options.cache =
                    Some(PathBuf::from(args.next().ok_or_else(|| {
                        Error::usage("--cache-dir requires a path")
                    })?))
            }
            "--odin" => {
                options.odin = args
                    .next()
                    .ok_or_else(|| Error::usage("--odin requires a path"))?
            }
            "--no-cache" => options.no_cache = true,
            "--odin-compatible" => options.odin_compatible = true,
            "--diagnostic-format" => {
                let format = args
                    .next()
                    .ok_or_else(|| Error::usage("--diagnostic-format requires text or json"))?;
                if format != "text" && format != "json" {
                    return Err(Error::usage("invalid diagnostic format"));
                }
                options.json = format == "json";
            }
            "--diagnostic-format=json" | "-json-errors" => options.json = true,
            "--run-timeout" => {
                let value = args.next().ok_or_else(|| Error::usage("--run-timeout requires seconds"))?;
                options.run_timeout = parse_run_timeout(&value)?;
            }
            "--clear-cache" => options.clear_cache = true,
            "--cache-report" => options.cache_report = true,
            "--raw-diagnostics" => options.raw_diagnostics = true,
            "--vet" => options.vet = Some(thor_compiler::vet::CHECKS.iter().map(|check| (*check).into()).collect()),
            "--apply" => options.vet_apply = true,
            "--ask" => options.vet_ask = true,
            "--source-maps" => options.maps = true,
            "--editor-info" => options.editor_info = true,
            "--track-allocations" => {
                options.tracking = args
                    .next()
                    .ok_or_else(|| Error::usage("--track-allocations requires a mode"))?
            }
            "--no-sanitize" => options.no_sanitize = true,
            "--overlay" => {
                let path = args
                    .next()
                    .ok_or_else(|| Error::usage("--overlay requires a file"))?;
                let packet: serde_json::Value = serde_json::from_slice(&std::fs::read(&path)?)?;
                let entries = packet["Replace"]
                    .as_object()
                    .ok_or_else(|| Error::new("overlay requires a Replace object"))?;
                for (original, replacement) in entries {
                    let replacement = replacement
                        .as_str()
                        .ok_or_else(|| Error::new("overlay replacement must be a path"))?;
                    let original = absolute(std::path::Path::new(original))?;
                    options
                        .overlays
                        .insert(original, std::fs::read_to_string(replacement)?);
                }
            }
            _ if argument.starts_with("--vet=") => {
                options.vet = Some(argument[6..].split(',').map(str::to_string).collect())
            }
            _ if argument.starts_with("--run-timeout=") => {
                options.run_timeout = parse_run_timeout(&argument[14..])?;
            }
            _ if argument.starts_with("--track-allocations=") => {
                options.tracking = argument[20..].into()
            }
            _ => options.flags.push(argument),
        }
    }
    if !matches!(
        options.tracking.as_str(),
        "auto" | "off" | "basic" | "trace"
    ) {
        return Err(Error::usage(
            "--track-allocations: choose auto,off,basic,trace",
        ));
    }
    if options.flags.iter().any(|flag| !flag.starts_with('-')) {
        return Err(Error::usage(
            "unexpected argument; runtime arguments must follow --",
        ));
    }
    if !options.args.is_empty() && !matches!(options.command.as_str(), "run" | "test") {
        return Err(Error::usage(
            "arguments after -- are only supported by run and test",
        ));
    }
    if options.vet_apply && options.vet_ask {
        return Err(Error::usage("--apply and --ask are mutually exclusive"));
    }
    if (options.vet_apply || options.vet_ask) && options.command != "vet" {
        return Err(Error::usage(
            "--apply and --ask are only supported by thor vet",
        ));
    }
    if (options.vet_apply || options.vet_ask) && !options.overlays.is_empty() {
        return Err(Error::usage(
            "vet corrections require saved files without --overlay",
        ));
    }
    if let Some(checks) = &options.vet {
        if checks.is_empty()
            || checks
                .iter()
                .any(|check| !thor_compiler::vet::CHECKS.contains(&check.as_str()))
        {
            return Err(Error::usage(
                format!("Thor vet checks must be selected from {}", thor_compiler::vet::CHECKS.join(",")),
            ));
        }
        if options.command == "transpile" {
            return Err(Error::usage(
                "--vet requires native checking. Run thor vet.",
            ));
        }
    }
    if options.raw_diagnostics && (options.command == "vet" || options.vet.is_some()) {
        return Err(Error::usage(
            "Thor vet requires mapped diagnostics without --raw-diagnostics",
        ));
    }
    if packaging_options && options.command != "publish" {
        return Err(Error::usage(
            "application packaging options are only supported by thor publish",
        ));
    }
    thor_compiler::cli::normalize(&mut options)?;
    if let Some(status) = thor_compiler::cli::query(&options)? { return Ok(status); }
    thor_compiler::cli::resolve_entry(&mut options)?;
    drop(setup);
    if options.command == "publish" {
        return thor_compiler::publishing::run(options, publishing);
    }
    let preparing = std::time::Instant::now();
    let mut project = thor_compiler::profile::Owned::with_cleanup("project.teardown", Project::new(options)?, Project::release);
    project.timings.insert("preparation", preparing.elapsed().as_secs_f64());
    if project.options.command == "ast" {
        let sources: Vec<_> = project
            .sources
            .iter()
            .map(|source| json!({"path":source.path,"frontend":source.frontend_info()}))
            .collect();
        println!("{}", json!({"version":1,"sources":sources}));
        return Ok(0);
    }
    let lowering = std::time::Instant::now();
    let target = match project.lower() {
        Ok(target) => target,
        Err(error) if project.options.editor_info && project.options.json => {
            let unsupported = error.unsupported;
            let errors = if error.diagnostics.is_empty() {
                vec![json!({"type":"error","stage":"thor-rust","pos":null,"msgs":[error.message]})]
            } else {
                error.diagnostics
            };
            eprintln!(
                "{}",
                json!({"error_count":errors.iter().filter(|error|error["type"]=="error").count(),"errors":errors,"slice_access":project.editor_info})
            );
            return Ok(if unsupported { 3 } else { 1 });
        }
        Err(error) => return Err(error),
    };
    project.timings.insert("lowering", lowering.elapsed().as_secs_f64());
    if project.options.command == "transpile" {
        println!("{}", target.display());
        Ok(0)
    } else {
        project.compile(&target)
    }
}
