//! Public command metadata and compatibility normalization.
use crate::{
    diagnostic::{Error, Result},
    project::{absolute, Options},
    VERSION,
};
use serde::Deserialize;
use std::{
    collections::BTreeSet,
    env,
    path::{Path, PathBuf},
    process::Command,
    sync::OnceLock,
};

#[derive(Deserialize)]
pub struct CommandInfo {
    pub name: String,
    pub description: String,
    pub source: bool,
}
#[derive(Deserialize)]
pub struct Section {
    pub commands: Vec<String>,
    pub text: String,
}
#[derive(Deserialize)]
pub struct NativeFlag {
    pub name: String,
    pub syntax: String,
    pub description: String,
    pub commands: Vec<String>,
    pub internal: bool,
    #[serde(default)]
    pub thor_rejection: Option<String>,
}
#[derive(Deserialize)]
pub struct Catalog {
    pub commands: Vec<CommandInfo>,
    pub sections: Vec<Section>,
    pub native_flags: Vec<NativeFlag>,
}
pub fn catalog() -> &'static Catalog {
    static CATALOG: OnceLock<Catalog> = OnceLock::new();
    CATALOG.get_or_init(|| {
        serde_json::from_str(include_str!("../../cli/help.json"))
            .expect("the installed CLI catalog is valid JSON")
    })
}
pub fn source_command(name: &str) -> bool {
    catalog()
        .commands
        .iter()
        .any(|command| command.name == name && command.source)
}

/// Native checks see generated Odin, so only audited checks can be applied to
/// Thor-containing projects. Keep native-only projects compatible with Odin.
pub fn native_check_rejection(name: &str) -> Option<&'static str> {
    if let Some(flag) = catalog().native_flags.iter().find(|flag| flag.name == name) {
        return flag.thor_rejection.as_deref();
    }
    name.starts_with("vet-").then_some(
        "This native check has not been audited against generated Thor code.",
    )
}

pub fn validate_native_checks(project: &crate::project::Project) -> Result<()> {
    if project.options.odin_compatible {
        return Ok(());
    }
    if !project.sources.iter().any(|source| source.thor && !source.excluded) {
        return Ok(());
    }
    for flag in &project.options.flags {
        let name = flag.trim_start_matches('-').split(':').next().unwrap_or_default();
        if let Some(reason) = native_check_rejection(name) {
            return Err(Error::new(format!(
                "-{name} is supported only for Odin-only projects. {reason} Remove -{name} for projects containing Thor source."
            )));
        }
    }
    if project.options.command != "ast" {
        for source in project.sources.iter().filter(|source| source.thor && !source.excluded) {
            for tag in source.info["file_tags"].as_array().into_iter().flatten().filter_map(|tag| tag.as_str()) {
                let mut words = tag.trim_start_matches("#+").split_whitespace();
                if words.next() != Some("vet") { continue; }
                let mut names = words.flat_map(|word| word.split(',')).filter(|name| !name.is_empty()).collect::<Vec<_>>();
                // A bare #+vet enables Odin's full bundle.
                if names.is_empty() { names.push(""); }
                for name in names.into_iter().filter(|name| !name.starts_with('!')) {
                    let check = if name.is_empty() { "vet".into() } else { format!("vet-{name}") };
                    if let Some(reason) = native_check_rejection(&check) {
                        let start = source.raw.match_indices(tag)
                            .map(|(start, _)| start)
                            .find(|start| !source.comments.iter().any(|comment| comment.start <= *start && *start < comment.end))
                            .unwrap_or_default();
                        let label = if name.is_empty() { "#+vet".into() } else { format!("#+vet {name}") };
                        return Err(Error::at(source, crate::ast::Span::new(start, start + tag.len()),
                            format!("{label} is supported only in Odin source files. {reason}")));
                    }
                }
            }
        }
    }
    Ok(())
}

/// File identity also covers case-insensitive paths, symlinks and Unix hardlinks.
pub(crate) fn same_file(left: &Path, right: &Path) -> bool {
    if left == right {
        return true;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if let (Ok(left), Ok(right)) = (std::fs::metadata(left), std::fs::metadata(right)) {
            return left.dev() == right.dev() && left.ino() == right.ino();
        }
        false
    }
    #[cfg(not(unix))]
    {
        match (crate::project::canonicalize(left), crate::project::canonicalize(right)) {
            (Ok(left), Ok(right)) => left
                .to_string_lossy()
                .eq_ignore_ascii_case(&right.to_string_lossy()),
            _ => false,
        }
    }
}

#[cfg(all(test, unix))]
mod output_guard_tests {
    use super::*;
    #[test]
    fn launcher_identity_survives_aliases() {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = env::temp_dir().join(format!(
            "thor-launcher-guard-{}-{nonce}",
            std::process::id()
        ));
        std::fs::create_dir(&directory).unwrap();
        let launcher = directory.join("thor");
        let alias = directory.join("compiler-alias");
        let symlink = directory.join("compiler-symlink");
        std::fs::write(&launcher, b"launcher must survive").unwrap();
        std::fs::hard_link(&launcher, &alias).unwrap();
        std::os::unix::fs::symlink(&launcher, &symlink).unwrap();
        let hardlink_matches = same_file(&launcher, &alias);
        let symlink_matches = same_file(&launcher, &symlink);
        let different_matches = same_file(&launcher, &directory.join("missing"));
        std::fs::remove_dir_all(directory).unwrap();
        assert!(hardlink_matches);
        assert!(symlink_matches);
        assert!(!different_matches);
    }
}
pub fn root() -> Result<PathBuf> {
    absolute(
        &env::var_os("THOR_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap()
                    .to_path_buf()
            }),
    )
}
pub fn value_option(flag: &str) -> bool {
    matches!(
        flag,
        "--output-dir"
            | "--command"
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
    )
}
pub fn odin_option(args: &[String]) -> String {
    args.windows(2)
        .find(|pair| pair[0] == "--odin")
        .map(|pair| pair[1].clone())
        .or_else(|| {
            args.iter()
                .find_map(|arg| arg.strip_prefix("--odin=").map(str::to_string))
        })
        .or_else(|| env::var("THOR_ODIN").ok())
        .unwrap_or_else(|| "odin".into())
}
pub fn early(args: &[String]) -> Result<Option<i32>> {
    let Some(command) = args.first().map(String::as_str) else {
        return Ok(None);
    };
    match command {
        "version" => {
            if args.len() != 1 {
                return Err(Error::usage("thor version takes no arguments"));
            }
            println!("Thor {VERSION}");
        }
        "root" => {
            if args.len() != 1 {
                return Err(Error::usage("thor root takes no arguments"));
            }
            print!(
                "{}{sep}",
                root()?
                    .to_string_lossy()
                    .trim_end_matches(std::path::MAIN_SEPARATOR),
                sep = std::path::MAIN_SEPARATOR
            );
        }
        "report" => {
            let odin = odin_option(args);
            let mut remaining = args[1..].iter();
            while let Some(arg) = remaining.next() {
                if arg == "--odin" {
                    remaining
                        .next()
                        .ok_or_else(|| Error::usage("--odin requires a path"))?;
                } else if !arg.starts_with("--odin=") {
                    return Err(Error::usage(format!("unexpected report argument '{arg}'")));
                }
            }
            println!(
                "Thor: {VERSION}\nThor root: {}\nHost: {}_{}",
                root()?.display(),
                env::consts::OS,
                env::consts::ARCH
            );
            match crate::frontend::executable(&odin) {
                Ok(path) => {
                    println!("Native compiler: {}", path.display());
                    for action in ["version", "root", "report"] {
                        let output = Command::new(&path).arg(action).output()?;
                        let text = String::from_utf8_lossy(&output.stdout);
                        if action == "report" {
                            if let Some((_, details)) =
                                text.split_once("Useful information to add to a bug report:")
                            {
                                println!("{}", details.trim());
                            } else {
                                print!("{text}");
                            }
                        } else {
                            println!("{action}: {}", text.trim());
                        }
                    }
                }
                Err(error) => println!("Native compiler unavailable: {}", error.message),
            }
        }
        "bundle" => {
            if args.get(1).is_none_or(|arg| arg != "android") {
                return Err(Error::usage(
                    "use thor bundle android [directory] [options]",
                ));
            }
            let mut native = vec!["bundle".to_string(), "android".to_string()];
            let mut rest = args[2..].iter();
            let mut directory = None;
            while let Some(arg) = rest.next() {
                if arg == "--odin" {
                    rest.next()
                        .ok_or_else(|| Error::usage("--odin requires a path"))?;
                } else if arg.starts_with("--odin=") {
                } else if !arg.starts_with('-') && directory.is_none() {
                    directory = Some(arg.clone());
                } else {
                    native.push(arg.clone());
                }
            }
            native.insert(2, directory.unwrap_or_else(|| ".".into()));
            let executable = crate::frontend::executable(&odin_option(args))?;
            return Ok(Some(
                Command::new(executable)
                    .args(native)
                    .status()?
                    .code()
                    .unwrap_or(1),
            ));
        }
        _ => return Ok(None),
    }
    Ok(Some(0))
}

/// Only capability-listing queries bypass source discovery and evaluation.
pub fn query(options: &Options) -> Result<Option<i32>> {
    if !options.flags.iter().any(|flag| {
        matches!(
            flag.as_str(),
            "-target:?" | "-microarch:?" | "-target-features:?"
        )
    }) {
        return Ok(None);
    }
    let executable = crate::frontend::executable(&options.odin)?;
    let mut command = Command::new(executable);
    command.args(["build", "."]);
    command.args(options.flags.iter().filter(|flag| {
        [
            "-target:",
            "-microarch:",
            "-target-features:",
            "-subtarget:",
        ]
        .iter()
        .any(|prefix| flag.starts_with(prefix))
    }));
    let output = command.output()?;
    use std::io::Write;
    std::io::stdout().write_all(&output.stdout)?;
    // Native listing queries sometimes use status 1 and stderr for their list.
    let combined = String::from_utf8_lossy(&output.stdout).into_owned()
        + &String::from_utf8_lossy(&output.stderr);
    let listing = [
        "All supported targets:",
        "Possible -microarch values",
        "Possible -target-features",
    ]
    .iter()
    .any(|prefix| combined.starts_with(prefix));
    if listing {
        std::io::stdout().write_all(&output.stderr)?;
    } else {
        std::io::stderr().write_all(&output.stderr)?;
    }
    Ok(Some(if listing {
        0
    } else {
        output.status.code().unwrap_or(1)
    }))
}

pub fn normalize(options: &mut Options) -> Result<()> {
    if options.raw_diagnostics && matches!(options.command.as_str(), "doc" | "strip-semicolon") {
        return Err(Error::usage(
            "source tools require mapped output without --raw-diagnostics",
        ));
    }
    if options.flags.iter().any(|flag| flag == "-file") {
        if !options.entry.is_file() && !options.overlays.contains_key(&absolute(&options.entry)?) {
            return Err(Error::usage("-file requires a .thor or .odin file input"));
        }
        options.flags.retain(|flag| flag != "-file");
    }
    for prefix in ["-build-mode:", "-out:", "-thread-count:"] {
        if options
            .flags
            .iter()
            .filter(|flag| flag.starts_with(prefix))
            .count()
            > 1
        {
            return Err(Error::usage(format!(
                "{} may only be supplied once",
                prefix.trim_end_matches(':')
            )));
        }
    }
    if let Some(mode) = options
        .flags
        .iter()
        .find_map(|flag| flag.strip_prefix("-build-mode:"))
        .map(str::to_string)
    {
        if options.command == "run" && mode != "exe" {
            return Err(Error::usage(
                "thor run requires an executable; use thor build for other build modes",
            ));
        }
        if options.command == "test" && mode != "test" {
            return Err(Error::usage("thor test requires -build-mode:test"));
        }
        if options.command == "vet" && mode != "test" {
            return Err(Error::usage("thor vet only supports -build-mode:test"));
        }
        if !matches!(
            options.command.as_str(),
            "build" | "run" | "test" | "publish" | "vet"
        ) {
            return Err(Error::usage(
                "-build-mode is only supported by build, run, test, publish and vet",
            ));
        }
        if options.command == "test"
            || options.command == "run"
            || options.command == "publish" && mode == "exe"
        {
            options
                .flags
                .retain(|flag| !flag.starts_with("-build-mode:"));
        }
    }
    if options.command == "transpile"
        && options
            .flags
            .iter()
            .any(|flag| flag.starts_with("-vet") || flag.starts_with("-strict-style"))
    {
        return Err(Error::usage("source checks require thor check or thor vet"));
    }
    if options.command == "strip-semicolon" && !options.overlays.is_empty() {
        return Err(Error::usage(
            "strip-semicolon requires saved source files without --overlay",
        ));
    }
    for flag in &options.flags {
        let name = flag
            .trim_start_matches('-')
            .split(':')
            .next()
            .unwrap_or_default();
        if let Some(info) = catalog().native_flags.iter().find(|info| info.name == name) {
            let parser_setting =
                options.command == "ast" && matches!(name, "target" | "collection");
            let helper_setting = options.command == "transpile"
                && !matches!(
                    name,
                    "out"
                        | "build-mode"
                        | "all-packages"
                        | "print-linker-flags"
                        | "keep-executable"
                        | "export-dependencies"
                        | "export-dependencies-file"
                );
            if !options.odin_compatible && !parser_setting && !helper_setting && !info.commands.contains(&options.command) {
                return Err(Error::usage(format!("-{name} is not supported by thor {}; use thor help {} -full for applicable options", options.command, options.command)));
            }
        }
        if let Some(value) = flag.strip_prefix("-thread-count:") {
            let count = value
                .parse::<usize>()
                .ok()
                .filter(|count| *count > 0)
                .ok_or_else(|| Error::usage("-thread-count requires a positive integer"))?;
            crate::parallel::set_workers(count);
        }
    }
    Ok(())
}

pub fn checks_requested(flags: &[String]) -> bool {
    flags
        .iter()
        .any(|flag| flag.starts_with("-vet") || flag.starts_with("-strict-style"))
}
pub fn check_scopes(project: &crate::project::Project, flags: &mut Vec<String>) {
    let mut packages = BTreeSet::new();
    let root = if project.options.entry.is_file() {
        project.options.entry.parent().unwrap()
    } else {
        &project.options.entry
    };
    for source in &project.sources {
        if source.path.starts_with(root) && !source.excluded {
            packages.insert(
                source
                    .names
                    .get(source.node(crate::ast::NodeId(0)).name)
                    .to_string(),
            );
        }
    }
    let packages = packages.into_iter().collect::<Vec<_>>().join(",");
    if flags.iter().any(|flag| flag.starts_with("-vet"))
        && !flags.iter().any(|flag| flag.starts_with("-vet-packages:"))
    {
        flags.push(format!("-vet-packages:{packages}"));
    }
    if flags.iter().any(|flag| flag == "-strict-style")
        && !project.options.native_policy_probe
        && !flags
            .iter()
            .any(|flag| flag.starts_with("-strict-style-packages:"))
    {
        flags.push(format!("-strict-style-packages:{packages}"));
    }
}

/// Reports and artifact policies belong to the final compilation, not isolated
/// compile-time helpers or type probes.
pub(crate) fn helper_flag(flag: &str) -> bool {
    ![
        "-show-",
        "-export-",
        "-vet",
        "-strict-style",
        "-build-diagnostics",
        "-print-linker-flags",
        "-short",
        "-in-source-order",
        "-doc-format",
        "-all-packages",
        "-keep-temp-files",
    ]
    .iter()
    .any(|prefix| flag.starts_with(prefix))
}

pub fn resolve_entry(options: &mut Options) -> Result<()> {
    let entry = options.entry.to_string_lossy();
    if options.entry.exists()
        || !entry.contains(':')
        || cfg!(windows) && entry.as_bytes().get(1) == Some(&b':')
    {
        return Ok(());
    }
    let (collection, relative) = entry.split_once(':').unwrap();
    if collection.is_empty() || relative.is_empty() {
        return Ok(());
    }
    let custom = options
        .flags
        .iter()
        .filter_map(|flag| flag.strip_prefix("-collection:"))
        .find_map(|flag| {
            flag.split_once('=')
                .filter(|(name, _)| *name == collection)
                .map(|(_, path)| PathBuf::from(path))
        });
    let base = if let Some(path) = custom {
        path
    } else if collection == "thor" {
        options.root.join("runtime")
    } else if matches!(collection, "core" | "base" | "vendor") {
        let executable = crate::frontend::executable(&options.odin)?;
        let output = crate::project::capture_command(
            Command::new(executable).arg("root"), std::time::Duration::ZERO,
        )?;
        if !output.status.success() || output.oversized {
            return Err(Error::new("could not query the native compiler root"));
        }
        PathBuf::from(String::from_utf8_lossy(&output.stdout).trim()).join(collection)
    } else {
        return Err(Error::usage(format!(
            "unknown library collection '{collection}'"
        )));
    };
    options.entry = absolute(&base.join(relative))?;
    Ok(())
}
