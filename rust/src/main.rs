use serde_json::json;
use std::{env, path::PathBuf, process::ExitCode};
use thor_compiler::{
    diagnostic::{Error, Result},
    project::{absolute, print_diagnostic, Options, Project},
    VERSION,
};

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    let json_output = args
        .iter()
        .any(|arg| matches!(arg.as_str(), "-json-errors" | "--diagnostic-format=json"))
        || args
            .windows(2)
            .any(|args| args == ["--diagnostic-format", "json"]);
    match run(args) {
        Ok(status) => ExitCode::from(status.clamp(0, 255) as u8),
        Err(error) => {
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
            ExitCode::from(if error.unsupported { 3 } else { 1 })
        }
    }
}
fn run(mut args: Vec<String>) -> Result<i32> {
    if args.iter().any(|arg| arg == "--version") {
        println!("Thor {VERSION} (Rust migration, incomplete)");
        return Ok(0);
    }
    if args.first().is_some_and(|arg| arg == "--capabilities") {
        println!(
            "{}",
            json!({"engine":"rust","complete":false,"python_fallback":false,"parallel_lowering":false,
            "supported":["shared Odin parser","arena AST","lexical symbols","type shapes","structured source maps","native Odin passthrough","selective imports","bracket arrays","sequence membership","type-list constraints","constant concatenation","literal-count string repetition","runtime numeric power","runtime sequence bounds and generic assignments","typed loop bindings","multiple-result and compile switches","named Set operations","automatic using feature","initial interpolation and f-string argument lifetimes","multiple-value or_panic","runtime collection comprehensions","runtime expanded extrema","slice privileges and contextual mutation summaries","generic inline defaults","macOS allocation tracking and ASan source locations","editor source overlays"],
            "pending":["complete immutable-slice analysis","complete symbol/type/overload resolution","pure functions","compile-time execution","big numbers","full interpolation ownership","complete switch/using/callback interactions","compile-time comprehensions","full Set temporary ownership","complete optional-result arities","wildcard imports","TLS editor-info","debugger remapping","publishing","vet","project creation"]})
        );
        return Ok(0);
    }
    if args.is_empty() || args.iter().any(|arg| arg == "--help" || arg == "-h") {
        println!("Thor Rust migration compiler\n\nUsage: thor-rust <transpile|check|build|run|test|ast> <source> [options] [Odin flags] [-- arguments]\n\nOptions:\n  --output-dir PATH    Generated Odin (default .thor-rust-build/<source>)\n  --cache-dir PATH     Parser cache directory\n  --no-cache           Bypass parser cache\n  --odin PATH          Odin compiler\n  --diagnostic-format json\n  --capabilities       Machine-readable port coverage\n\nIncomplete native port; unsupported features exit 3. Python `thor` remains the default.");
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
        return Err(Error::new("expected a source file or package directory"));
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
    };
    if let Some(root) = env::var_os("THOR_ROOT") {
        options.root = absolute(&PathBuf::from(root))?;
    }
    let mut args = args.into_iter();
    while let Some(argument) = args.next() {
        match argument.as_str() {
            "--" => {
                options.args = args.collect();
                break;
            }
            "--output-dir" => {
                options.output = Some(PathBuf::from(
                    args.next()
                        .ok_or_else(|| Error::new("--output-dir requires a path"))?,
                ))
            }
            "--cache-dir" => {
                options.cache = Some(PathBuf::from(
                    args.next()
                        .ok_or_else(|| Error::new("--cache-dir requires a path"))?,
                ))
            }
            "--odin" => {
                options.odin = args
                    .next()
                    .ok_or_else(|| Error::new("--odin requires a path"))?
            }
            "--no-cache" => options.no_cache = true,
            "--diagnostic-format" => {
                let format = args
                    .next()
                    .ok_or_else(|| Error::new("--diagnostic-format requires text or json"))?;
                if format != "text" && format != "json" {
                    return Err(Error::new("invalid diagnostic format"));
                }
                options.json = format == "json";
            }
            "--diagnostic-format=json" | "-json-errors" => options.json = true,
            "--source-maps" => options.maps = true,
            "--track-allocations" => {
                options.tracking = args
                    .next()
                    .ok_or_else(|| Error::new("--track-allocations requires a mode"))?
            }
            "--no-sanitize" => options.no_sanitize = true,
            "--overlay" => {
                let path = args
                    .next()
                    .ok_or_else(|| Error::new("--overlay requires a file"))?;
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
            _ if argument.starts_with("--track-allocations=") => {
                options.tracking = argument[20..].into()
            }
            _ if argument.starts_with("--") => {
                return Err(Error::new(format!(
                    "Rust migration: option '{argument}' has not been ported yet"
                )))
            }
            _ => options.flags.push(argument),
        }
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
    let target = project.lower()?;
    if project.options.command == "transpile" {
        println!("{}", target.display());
        Ok(0)
    } else {
        project.compile(&target)
    }
}
