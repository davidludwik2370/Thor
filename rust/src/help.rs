use std::{collections::BTreeSet, process::Command};
use thor_compiler::{
    cli,
    diagnostic::{Error, Result},
    VERSION,
};

pub const USAGE: &str = "Usage: thor <command> [source] [options] [-- arguments]";

fn commands() -> String {
    let mut text = String::from("Commands:\n");
    for command in &cli::catalog().commands {
        text.push_str(&format!("  {:17} {}\n", command.name, command.description));
    }
    text
}

pub fn overview() -> String {
    format!("Thor {VERSION}\n\n{USAGE}\n\n{}\nCommon options:\n  -debug               Enable debugging and default memory checks\n  -o:MODE              Set optimization (none, minimal, size, speed, aggressive)\n  -out:PATH            Choose the artifact path\n  --vet[=CHECKS]       Check Thor conventions and unused bindings\n  --output-dir PATH    Choose the generated-source directory\n  --no-cache           Bypass parser and compile-time result caches\n\n[source] defaults to . (the current directory). new requires a project name. migrate requires a directory or --command.\nBuild executables default to ./<working-directory-name>.\n\nUse thor help for standard options.\nUse thor help -full for all options, or thor help <command> for command help.\n", commands())
}

fn introductory(command: Option<&str>) -> String {
    let mut text = format!("Thor {VERSION}\n\n");
    match command {
        None => {
            text.push_str(USAGE);
            text.push_str("\n\n");
            text.push_str(&commands());
        }
        Some("help") => text.push_str("Usage: thor help [command] [-full]\n\nShow standard options, or every compatible option with -full.\n"),
        Some("new") => text.push_str("Usage: thor new <name> [options]\n"),
        Some("migrate") => text.push_str("Usage: thor migrate [Odin project] [options] [Odin flags]\n"),
        Some("bundle") => text.push_str("Usage: thor bundle android [directory] [options]\n"),
        Some(name) if cli::source_command(name) => {
            text.push_str(&format!("Usage: thor {name} [source] [options]"));
            if matches!(name, "run" | "test") { text.push_str(" [-- arguments]"); }
            text.push('\n');
        }
        Some(name) => text.push_str(&format!("Usage: thor {name}{}\n", if name == "report" { " [--odin PATH]" } else { "" })),
    }
    if command.is_none_or(cli::source_command) {
        text.push_str("\n[source] is a .thor/.odin file or package directory.\nIt defaults to . (the current directory).\nFile inputs are self-contained packages.\n");
    }
    if let Some(name) = command {
        if let Some(info) = cli::catalog()
            .commands
            .iter()
            .find(|info| info.name == name)
        {
            text.push_str(&format!("\n{}\n", info.description));
        }
    }
    text.push('\n');
    text
}

pub fn render(command: Option<&str>, full: bool, odin: &str) -> String {
    let mut text = introductory(command);
    let mut documented = BTreeSet::new();
    for section in &cli::catalog().sections {
        if command.is_none_or(|name| section.commands.iter().any(|c| c == name)) {
            text.push_str(&section.text);
            text.push('\n');
            for line in section.text.lines() {
                if let Some(flag) = line.trim_start().strip_prefix('-') {
                    documented.insert(
                        flag.split([':', ' ', '['])
                            .next()
                            .unwrap_or(flag)
                            .to_string(),
                    );
                }
            }
        }
    }
    if full {
        text.push_str("Compatible compiler options:\n  Diagnostics refer to original project files.\n  Native vet/style policies retain native diagnostics on original source scopes.\n  Thor --vet findings are errors for build and warnings otherwise.\n\n");
        let mut known = BTreeSet::new();
        for flag in &cli::catalog().native_flags {
            if command.is_some_and(|name| !flag.commands.iter().any(|c| c == if name == "migrate" { "build" } else { name }))
                || command == Some("migrate") && matches!(flag.name.as_str(), "file" | "out" | "build-mode" | "export-dependencies" | "export-dependencies-file" | "doc-format" | "keep-temp-files" | "export-defineables" | "export-timings" | "export-timings-file" | "export-linked-libs-file")
                || documented.contains(&flag.name)
            {
                continue;
            }
            known.insert(flag.name.clone());
            text.push_str(&format!("  {}", flag.syntax));
            if command.is_none() {
                text.push_str(&format!("  [{}]", flag.commands.join(", ")));
            }
            if flag.internal {
                text.push_str("  [compiler development]");
            }
            text.push('\n');
            for line in flag.description.lines() {
                text.push_str("      ");
                text.push_str(line.trim_end());
                text.push('\n');
            }

            text.push('\n');
        }
        // Query only while rendering exhaustive help. Normal builds use the
        // installed catalog and do not start a help-discovery subprocess.
        if let Ok(executable) = thor_compiler::frontend::executable(odin) {
            let action = command.map(|name| {
                if name == "vet" {
                    "check"
                } else if matches!(name, "publish" | "migrate") {
                    "build"
                } else {
                    name
                }
            });
            let actions = action.map_or_else(
                || vec!["build", "check", "test", "doc", "bundle"],
                |name| vec![name],
            );
            for action in actions {
                if let Ok(output) = Command::new(&executable).args(["help", action]).output() {
                    let help = String::from_utf8_lossy(&output.stdout);
                    let mut include = false;
                    for line in help.lines() {
                        if line.starts_with("\t-") {
                            let name = line
                                .trim_start_matches('\t')
                                .trim_start_matches('-')
                                .split([':', ' ', '\t'])
                                .next()
                                .unwrap_or_default();
                            include = name != "help"
                                && !(command == Some("migrate") && matches!(name, "file" | "out" | "build-mode" | "export-dependencies" | "export-dependencies-file" | "doc-format" | "keep-temp-files" | "export-defineables" | "export-timings" | "export-timings-file" | "export-linked-libs-file"))
                                && !documented.contains(name)
                                && known.insert(name.to_string());

                        }
                        if include {
                            text.push_str(line);
                            text.push('\n');
                        }
                    }
                }
            }
        }
        if command.is_none_or(|name| {
            matches!(
                name,
                "build"
                    | "run"
                    | "check"
                    | "test"
                    | "transpile"
                    | "vet"
                    | "publish"
                    | "doc"
                    | "strip-semicolon"
            )
        }) {
            text.push_str("\nAdvanced toolchain and editor options:\n  --editor-info                Include editor metadata in JSON diagnostics\n  --odin-compatible            Preserve Odin command defaults, sequence bounds, and native policy checks\n  --capabilities               Print native compiler capabilities as JSON\n\n");
        }
    }
    text.push_str("Help and version:\n  thor help [command] [-full]   Show standard or exhaustive help\n  -help                        Compatible command-help spelling\n  --version                    Same as thor version\n");
    if !full {
        text.push_str(&format!(
            "\nUse thor help{} -full for all {}options.\n",
            command.map_or(String::new(), |name| format!(" {name}")),
            if command.is_some() { "command " } else { "" }
        ));
    }
    if command.is_none() {
        text.push_str("\nExamples:\n  thor new my_project --open\n  thor run\n  thor build -o:speed\n  thor run -debug\n  thor vet --ask\n  thor publish --app-type console\n  thor run -- first_argument\n");
    }
    text
}

pub fn dispatch(args: &[String]) -> Result<Option<i32>> {
    let mut tokens = vec![];
    let mut skip = false;
    for argument in args.iter().take_while(|arg| arg.as_str() != "--") {
        if skip {
            skip = false;
            continue;
        }
        if cli::value_option(argument) {
            skip = true;
            continue;
        }
        tokens.push(argument.as_str());
    }
    if tokens.iter().any(|arg| matches!(*arg, "-h" | "--help")) {
        return Err(Error::usage(
            "use thor help or thor help <command>; add -full for every option",
        ));
    }
    let request = args.first().is_some_and(|arg| arg == "help");
    let compatible = tokens.contains(&"-help");
    if !request && !compatible {
        if tokens.contains(&"-full") {
            return Err(Error::usage("-full is a thor help option"));
        }
        return Ok(None);
    }
    let full = tokens.contains(&"-full");
    let mut selected = None;
    if request {
        let mut rest = args[1..].iter();
        while let Some(argument) = rest.next() {
            if argument == "-full" {
                continue;
            }
            if argument == "--odin" {
                rest.next()
                    .ok_or_else(|| Error::usage("--odin requires a path"))?;
                continue;
            }
            if argument.starts_with("--odin=") {
                continue;
            }
            if selected.is_some()
                || !cli::catalog()
                    .commands
                    .iter()
                    .any(|info| info.name == *argument)
            {
                return Err(Error::usage(format!(
                    "unknown help topic or extra argument '{argument}'"
                )));
            }
            selected = Some(argument.as_str());
        }
    } else if let Some(name) = args
        .first()
        .map(String::as_str)
        .filter(|name| *name != "-help")
    {
        if !cli::catalog().commands.iter().any(|info| info.name == name) {
            return Err(Error::usage(format!("unknown command '{name}'")));
        }
        selected = Some(name);
    }
    print!("{}", render(selected, full, &cli::odin_option(args)));
    Ok(Some(0))
}
