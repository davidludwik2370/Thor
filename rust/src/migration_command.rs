//! Translate literal compiler invocations without reading or executing source.
use crate::diagnostic::{Error, Result};
use std::path::{Component, Path, PathBuf};

pub(super) struct Mapping {
    pub source: PathBuf,
    pub alias: Option<PathBuf>,
    pub destination: PathBuf,
    pub cwd: PathBuf,
}

#[derive(Clone)]
pub(super) struct Invocation {
    pub action: String,
    pub source: Option<usize>,
    pub arguments: Vec<String>,
    pub odin: Option<String>,
    pub compatible: bool,
}

// Shell syntax is limited to literal arguments. Quoted punctuation and empty
// arguments remain literal, while shell evaluation needs a script-level tool.
fn words(text: &str) -> Result<Vec<String>> {
    let mut words = vec![];
    let mut word = String::new();
    let mut quote = None;
    let mut started = false;
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if quote == Some('\'') {
            if ch == '\'' {
                quote = None;
            } else {
                word.push(ch);
            }
            continue;
        }
        if ch == '\\' {
            // An absolute Windows drive path is literal on either host. Do not
            // turn C:\work or -out:C:\work into C:work during command-only
            // translation. POSIX escaping remains available for other words.
            let drive_path=word.as_bytes().windows(2).enumerate().any(|(i,pair)|
                pair[0].is_ascii_alphabetic() && pair[1]==b':'
                    && (i==0 || matches!(word.as_bytes()[i-1],b':'|b'='))
                    && (i+2==word.len() || matches!(word.as_bytes()[i+2],b'\\'|b'/')));
            let unc_start=cfg!(windows) && word.is_empty() && chars.peek()==Some(&'\\');
            if drive_path || cfg!(windows) && word.starts_with("\\\\") || unc_start {
                word.push(ch);
                if unc_start {word.push(chars.next().unwrap());}
                started=true;
                continue;
            }
            let next = chars
                .next()
                .ok_or_else(|| Error::usage("unfinished escape in --command"))?;
            if quote == Some('"') && !matches!(next, '$' | '`' | '"' | '\\' | '\n') {
                word.push('\\');
            }
            if next != '\n' {
                word.push(next);
                started = true;
            }
            continue;
        }
        if quote == Some('"') {
            match ch {
                '"' => quote = None,
                '$' | '`' => return Err(Error::usage("--command expects literal arguments; shell expansion requires manual conversion")),
                _ => word.push(ch),
            }
            continue;
        }
        match ch {
            '\'' | '"' => {
                quote = Some(ch);
                started = true;
            }
            '\n' | '\r' => {
                return Err(Error::usage(
                    "use repeated --command options for separate invocations",
                ))
            }
            '?' if !word.ends_with(':') => {
                return Err(Error::usage(
                    "quote literal wildcard characters in --command",
                ))
            }
            ch if ch.is_whitespace() => {
                if started {
                    words.push(std::mem::take(&mut word));
                    started = false;
                }
            }
            ';' | '&' | '|' | '<' | '>' | '(' | ')' | '$' | '`' | '*' | '[' | ']' => {
                return Err(Error::usage("--command accepts one literal Odin invocation; use repeated --command options for multiple invocations"));
            }
            '#' if !started => {
                return Err(Error::usage(
                    "shell comments in --command require manual conversion",
                ))
            }
            '~' if !started => return Err(Error::usage(
                "expand the home-directory path before passing --command, or quote a literal path",
            )),
            _ => {
                word.push(ch);
                started = true;
            }
        }
    }
    if quote.is_some() {
        return Err(Error::usage("unclosed quote in --command"));
    }
    if started {
        words.push(word);
    }
    Ok(words)
}

pub(super) fn parse(text: &str) -> Result<Invocation> {
    let mut tokens = words(text)?;
    if tokens.is_empty() {
        return Err(Error::usage("--command requires an Odin invocation"));
    }
    let executable = tokens.remove(0);
    let name = executable.rsplit(['/', '\\']).next().unwrap_or_default();
    if name != "odin" && !name.eq_ignore_ascii_case("odin.exe") {
        return Err(Error::usage(
            "--command must start with odin or a path to the Odin executable",
        ));
    }
    if tokens.is_empty() {
        return Err(Error::usage(
            "--command requires an Odin command such as build or test",
        ));
    }
    let action = tokens.remove(0);
    if !matches!(
        action.as_str(),
        "build"
            | "run"
            | "test"
            | "check"
            | "doc"
            | "strip-semicolon"
            | "help"
            | "version"
            | "root"
            | "report"
            | "bundle"
    ) {
        return Err(Error::usage(format!(
            "Odin command '{action}' has no Thor command mapping"
        )));
    }
    let takes_source = matches!(
        action.as_str(),
        "build" | "run" | "test" | "check" | "doc" | "strip-semicolon"
    );
    let mut source = None;
    let mut boundary = false;
    for (index, token) in tokens.iter().enumerate() {
        if boundary {
            continue;
        }
        if token == "--" {
            boundary = true;
            continue;
        }
        if token.starts_with("--") {
            return Err(Error::usage(format!(
                "'{token}' is not a native Odin compiler flag"
            )));
        }
        if !token.starts_with('-') && takes_source {
            if source.replace(index).is_some() {
                return Err(Error::usage(
                    "unexpected positional argument in --command; program arguments follow --",
                ));
            }
        }
    }
    let query = tokens
        .iter()
        .any(|token| token == "-help" || token.ends_with(":?"));
    if takes_source && source.is_none() && !query {
        return Err(Error::usage(
            "the Odin invocation in --command requires its source argument",
        ));
    }
    if boundary && !matches!(action.as_str(), "run" | "test") {
        return Err(Error::usage(
            "arguments after -- require an Odin run or test command",
        ));
    }
    Ok(Invocation {
        action,
        source,
        arguments: tokens,
        odin: (executable != "odin").then_some(executable),
        compatible: takes_source && !query,
    })
}

pub(super) fn lexical(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                result.pop();
            }
            other => result.push(other.as_os_str()),
        }
    }
    result
}

pub(super) fn collection_entry(text: &str, flags: &[String], cwd: &Path) -> Option<PathBuf> {
    let (name, relative) = text.split_once(':')?;
    let base = flags
        .iter()
        .take_while(|argument| argument.as_str() != "--")
        .filter_map(|flag| flag.strip_prefix("-collection:"))
        .filter_map(|value| value.split_once('='))
        .find_map(|(collection, path)| (collection == name).then_some(path))?;
    Some(lexical(&cwd.join(base).join(relative)))
}

fn project_relative<'a>(absolute: &'a Path, mapping: &Mapping) -> Option<&'a Path> {
    absolute.strip_prefix(&mapping.source).ok().or_else(|| {
        mapping
            .alias
            .as_ref()
            .and_then(|alias| absolute.strip_prefix(alias).ok())
    })
}

fn path_text(text: &str, mapping: Option<&Mapping>, source_file: bool) -> String {
    let path = Path::new(text);
    let mut output = path.to_path_buf();
    let mut convert_source = source_file && mapping.is_none();
    if let Some(mapping) = mapping {
        let absolute = lexical(&mapping.cwd.join(path));
        if let Some(relative) = project_relative(&absolute, mapping) {
            convert_source = source_file;
            let destination = if relative.as_os_str().is_empty() {
                mapping.destination.clone()
            } else {
                mapping.destination.join(relative)
            };
            output = if path.is_absolute() {
                destination
            } else {
                super::relative_link(&mapping.cwd, &destination)
            };
        }
    }
    if convert_source
        && output
            .extension()
            .is_some_and(|extension| extension == "odin")
    {
        output.set_extension("thor");
    }
    let mut result = output.to_string_lossy().into_owned();
    if text.starts_with("./") && !result.starts_with('.') && !output.is_absolute() {
        result.insert_str(0, "./");
    }
    result
}

pub(super) fn path_flag(flag: &str, mapping: Option<&Mapping>) -> String {
    let Some((name, value)) = flag.split_once(':') else {
        return flag.into();
    };
    if name == "-collection" {
        if let Some((collection, path)) = value.split_once('=') {
            return format!("{name}:{collection}={}", path_text(path, mapping, false));
        }
    }
    if matches!(
        name,
        "-out"
            | "-export-defineables"
            | "-export-timings-file"
            | "-export-dependencies-file"
            | "-export-linked-libs-file"
    ) {
        return format!("{name}:{}", path_text(value, mapping, false));
    }
    flag.into()
}

pub(super) fn shell_quote(value: &str) -> String {
    if !value.is_empty()
        && value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || "_@%+=:,./-".contains(ch))
    {
        return value.into();
    }
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}

impl Invocation {
    pub fn translated(&self, mapping: Option<&Mapping>, odin: Option<&str>) -> String {
        let mut result = vec!["thor".to_string(), self.action.clone()];
        let mut boundary = false;
        let mut compiler_options = vec![];
        if self.compatible {
            compiler_options.push("--odin-compatible".into());
        }
        if let Some(odin) = odin.or(self.odin.as_deref()) {
            compiler_options.extend(["--odin".into(), odin.into()]);
        }
        for (index, argument) in self.arguments.iter().enumerate() {
            if argument == "--" && !boundary {
                result.append(&mut compiler_options);
                boundary = true;
            }
            result.push(if boundary {
                argument.clone()
            } else if self.source == Some(index) {
                let collection_path = mapping.and_then(|mapping| {
                    collection_entry(argument, &self.arguments, &mapping.cwd)
                        .filter(|path| project_relative(path, mapping).is_some())
                });
                let source = collection_path
                    .as_ref()
                    .map(|path| path.to_string_lossy())
                    .unwrap_or_else(|| argument.as_str().into());
                path_text(
                    &source,
                    mapping,
                    self.arguments
                        .iter()
                        .take_while(|arg| arg.as_str() != "--")
                        .any(|arg| arg == "-file"),
                )
            } else {
                path_flag(argument, mapping)
            });
        }
        result.extend(compiler_options);
        result
            .iter()
            .map(|argument| shell_quote(argument))
            .collect::<Vec<_>>()
            .join(" ")
    }
}
