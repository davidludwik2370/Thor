//! Native mock executable for process/lifecycle LSP tests on Windows and Unix.
use serde_json::{json, Value};
use std::{env, fs, io::Write, path::PathBuf, process, thread, time::Duration};
fn main() {
    let exe = env::current_exe().expect("mock executable path");
    let root = exe.parent().unwrap();
    let config: Value = serde_json::from_slice(&fs::read(exe.with_extension("json")).expect("mock config")).expect("JSON config");
    let args: Vec<String> = env::args().collect();
    if config["mode"] == "linux_tools" {
        let tool=config["tool"].as_str().expect("tool name");
        let mut output=fs::OpenOptions::new().create(true).append(true).open(config["log"].as_str().unwrap()).unwrap();
        writeln!(output,"{}",serde_json::to_string(&std::iter::once(tool.to_string()).chain(args[1..].iter().cloned()).collect::<Vec<_>>()).unwrap()).unwrap();
        let settings:Value=serde_json::from_slice(&fs::read(config["settings"].as_str().unwrap()).unwrap()).unwrap();
        if tool=="readelf" {println!("{}",settings[tool][args.last().unwrap()].as_str().unwrap_or(""));}
        else if tool=="ldd" {println!("{}",settings[tool].as_str().unwrap_or(""));}
        return;
    }
    if config["mode"] == "identity" {
        match args.get(1).map(String::as_str) {
            Some("root") => println!("{}",config["sdk"].as_str().expect("SDK root")),
            Some("version") => println!("identity test compiler"),
            _ => process::exit(2),
        }
        return;
    }
    if config["mode"] == "delegate" {
        for arg in &args[1..] {println!("{arg}");}
        return;
    }
    if config["mode"] == "frontend_guard" || config["mode"] == "collision" || config["mode"] == "toolchain" {
        if let Some(log)=config["log"].as_str() {
            let mut output=fs::OpenOptions::new().create(true).append(true).open(log).expect("mock log");
            writeln!(output,"{}",serde_json::to_string(&args[1..]).unwrap()).expect("mock log write");
        }
        if args.get(1).map(String::as_str)==Some("root") {
            println!("{}",config["sdk"].as_str().expect("SDK root"));
            return;
        }
        if args.get(1).map(String::as_str)==Some("check") && config["mode"]=="toolchain" && args.get(2).is_some_and(|path|
            config["outputs"].as_array().unwrap().iter().any(|output|PathBuf::from(path).starts_with(output.as_str().unwrap()))) {
            eprintln!("native tool detail");
            process::exit(if env::var("THOR_TEST_TOOLCHAIN_MODE").as_deref()==Ok("warning"){0}else{1});
        }
        if args.get(1).map(String::as_str)==Some("build") && config["block_marker"].as_str().is_some_and(|marker|PathBuf::from(marker).exists()) &&
            args.iter().any(|arg|arg.contains("thor-run-")||arg.contains("thor-rust-run-")) {
            eprintln!("helper build blocked by test");process::exit(1);
        }
        if config["mode"] == "frontend_guard" && args.get(1).map(String::as_str)==Some("build") && args.get(2)
            .is_some_and(|path|PathBuf::from(path)==PathBuf::from(config["frontend"].as_str().unwrap())) {
            process::exit(91);
        }
        if config["mode"]=="collision" && args.get(1).map(String::as_str)==Some("build") && args.get(2)
            .is_some_and(|path|PathBuf::from(path).starts_with(config["project"].as_str().unwrap())) {
            let failure=env::var("THOR_TEST_MODULE_FAILURE").unwrap();
            if !args.iter().any(|arg|arg=="-use-single-module") || failure=="always" {
                if failure=="source" {eprintln!("{}",json!({"errors":[{"type":"error","pos":{"file":args[2],"offset":0,"line":1,"column":1},"msgs":["Source failure"]}]}));}
                eprintln!("duplicate symbol '{}'",if failure=="external"{"_external_name"}else{"_sample::helper:proc(v:int)->(:int)"});
                process::exit(1);
            }
        }
        let status=process::Command::new(config["odin"].as_str().expect("native Odin"))
            .args(&args[1..]).env("ODIN_ROOT",config["sdk"].as_str().expect("SDK root"))
            .status().expect("native Odin invocation");
        process::exit(status.code().unwrap_or(1));
    }
    if args.get(1).map(String::as_str) == Some("root") {
        fs::write(root.join("called"), "yes").unwrap();
        if config["query_sleep"].as_bool() == Some(true) { thread::sleep(Duration::from_secs(8)); }
        if let Some(text) = config["root_output"].as_str() { println!("{text}"); return; }
        process::exit(1);
    }
    let mode = config["mode"].as_str().unwrap_or("");
    let overlay: Value = args.iter().position(|a| a == "--overlay")
        .and_then(|i| args.get(i + 1)).and_then(|p| fs::read(p).ok())
        .and_then(|raw| serde_json::from_slice(&raw).ok()).unwrap_or_default();
    let source = if mode == "locationless" { PathBuf::from(&args[2]).join("main.thor") } else { root.join("main.thor") };
    // TLS may use forward slashes for overlay keys and native separators for
    // command arguments. Compare filesystem paths rather than JSON key text.
    let canonical=fs::canonicalize(&source).ok();
    let path = overlay["Replace"].as_object().and_then(|entries|entries.iter()
        .find(|(key,_)|PathBuf::from(key.as_str())==source || canonical.as_ref().is_some_and(|path|
            fs::canonicalize(key).ok().as_ref()==Some(path)))
        .and_then(|(_,value)|value.as_str())).map(PathBuf::from).unwrap_or(source);
    let text = fs::read_to_string(path).unwrap_or_default();
    if mode == "shutdown" || mode == "superseded" && text.contains("SLOW") {
        fs::write(root.join("started"), process::id().to_string()).unwrap();
        thread::sleep(Duration::from_secs(10));
    }
    if mode == "superseded" { fs::write(root.join("completed"), &text).unwrap(); }
    let errors = if mode == "locationless" && text.contains("BROKEN") {
        vec![json!({"type":"error","pos":null,"msgs":["Locationless test compiler error"]})]
    } else { vec![] };
    eprintln!("{}", json!({"error_count":errors.len(),"errors":errors}));
    if !errors.is_empty() { process::exit(1); }
}
