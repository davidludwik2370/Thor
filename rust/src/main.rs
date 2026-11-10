use serde_json::json;
use std::{env, path::PathBuf, process::ExitCode};
use thor_compiler::{
    diagnostic::{Error, Result},
    project::{absolute, print_diagnostic, Options, Project},
    VERSION,
};

fn main() -> ExitCode {
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
    match run(args) {
        Ok(status) => ExitCode::from(status.clamp(0, 255) as u8),
        Err(error) => {
            if error.exit_status == 2 {
                eprintln!("usage: thor-rust <command> <source> [options] [Odin flags] [-- arguments]\nthor: error: {}",error.message);
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
                    print_diagnostic(&error);
                }
            }
            ExitCode::from(exit_status)
        }
    }
}
fn run(mut args: Vec<String>) -> Result<i32> {
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
    if args
        .iter()
        .take_while(|arg| arg.as_str() != "--")
        .any(|arg| arg == "--version")
    {
        println!("Thor {VERSION} (Rust migration, incomplete)");
        return Ok(0);
    }
    if args.first().is_some_and(|arg| arg == "--capabilities") {
        println!(
            "{}",
            json!({"engine":"rust","complete":false,"python_fallback":false,"parallel_lowering":false,
            "supported":["shared Odin parser","arena AST","lexical symbols","type shapes","structured source maps","native Odin passthrough","selective imports","bracket arrays","sequence membership","type-list constraints","constant concatenation","literal-count string repetition","runtime numeric power","runtime sequence bounds and generic assignments","typed loop bindings","multiple-result and compile switches","named Set operations","automatic using feature","initial interpolation and f-string argument lifetimes","multiple-value or_panic","runtime collection comprehensions","runtime expanded extrema","slice privileges and contextual mutation summaries","generic inline defaults","macOS allocation tracking and ASan source locations","editor source overlays","runtime pure contracts, checked ordinary callees and scalar/sequence/record/union result ownership"],
            "evaluation":{"isolated_native_helpers":true,"generic_frames":true,"scalar_and_aggregate_results":true,"contents_cache":true,"prepared_scalar_cache":true,"verified_scalar_sites":true,"compile_time_comprehensions":true,"big_number_results":false},
            "pending":["complete immutable-slice analysis","complete symbol/type/overload resolution","remaining pure function and SDK interactions","big numbers","full interpolation ownership","complete switch/using/callback interactions","full Set temporary ownership","complete optional-result arities","wildcard imports","TLS editor-info","debugger remapping","publishing","vet","project creation"]})
        );
        return Ok(0);
    }
    if args.is_empty()
        || args
            .iter()
            .take_while(|arg| arg.as_str() != "--")
            .any(|arg| arg == "--help" || arg == "-h")
    {
        println!("Thor Rust migration compiler\n\nUsage: thor-rust <transpile|check|build|run|test|new|ast> <source> [options] [Odin flags] [-- arguments]\n\nOptions:\n  --output-dir PATH    Generated Odin (default .thor-rust-build/<source>)\n  --cache-dir PATH     Parser and evaluation cache directory\n  --no-cache           Bypass parser and evaluation caches\n  --odin PATH          Odin compiler\n  --diagnostic-format json\n  --capabilities       Machine-readable port coverage\n\nIncomplete native port; unsupported features exit 3. Python `thor` remains the default.");
        return Ok(0);
    }
    let command = args.remove(0);
    if !matches!(
        command.as_str(),
        "transpile" | "check" | "build" | "run" | "test" | "ast"
    ) {
        return Err(Error::new(format!(
            "Rust migration: command '{command}' has not been ported yet"
        )));
    }
    if args.is_empty() {
        return Err(Error::usage("expected a source file or package directory"));
    }
    let entry = PathBuf::from(args.remove(0));
    let mut options = Options {
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
        run_timeout: std::time::Duration::from_secs(5),
        clear_cache: false,
        cache_report: false,
        raw_diagnostics: false,
    };
    if let Some(root) = env::var_os("THOR_ROOT") {
        options.root = absolute(&PathBuf::from(root))?;
    }
    let mut expanded = vec![];
    for argument in args {
        if let Some((flag, value)) = argument.split_once('=') {
            if matches!(
                flag,
                "--output-dir"
                    | "--cache-dir"
                    | "--odin"
                    | "--diagnostic-format"
                    | "--overlay"
                    | "--run-timeout"
                    | "--track-allocations"
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
                let seconds = args
                    .next()
                    .and_then(|value| value.parse::<f64>().ok())
                    .filter(|value| value.is_finite() && *value > 0.0 && *value <= 300.0)
                    .ok_or_else(|| {
                        Error::usage("--run-timeout must be greater than 0 and at most 300 seconds")
                    })?;
                options.run_timeout = std::time::Duration::from_secs_f64(seconds);
            }
            "--clear-cache" => options.clear_cache = true,
            "--cache-report" => options.cache_report = true,
            "--raw-diagnostics" => options.raw_diagnostics = true,
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
            _ if argument.starts_with("--run-timeout=") => {
                let seconds = argument[14..]
                    .parse::<f64>()
                    .ok()
                    .filter(|value| value.is_finite() && *value > 0.0 && *value <= 300.0)
                    .ok_or_else(|| {
                        Error::usage("--run-timeout must be greater than 0 and at most 300 seconds")
                    })?;
                options.run_timeout = std::time::Duration::from_secs_f64(seconds);
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
    let mut project = Project::new(options)?;
    if project.options.command == "ast" {
        let sources: Vec<_> = project
            .sources
            .iter()
            .map(|source| json!({"path":source.path,"frontend":source.frontend_info()}))
            .collect();
        println!("{}", json!({"version":1,"sources":sources}));
        return Ok(0);
    }
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
    if project.options.command == "transpile" {
        println!("{}", target.display());
        Ok(0)
    } else {
        project.compile(&target)
    }
}
