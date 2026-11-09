use crate::{
    ast::Source,
    diagnostic::{Error, Result},
    digest::sha256,
    emit::Emitted,
    frontend::{atomic_write, bootstrap, Frontend},
    lower::{unquote, Lower},
    semantics::Resolver,
    VERSION,
};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    env, fs,
    path::{Component, Path, PathBuf},
    process::{Command, Stdio},
};

pub struct Options {
    pub command: String,
    pub entry: PathBuf,
    pub output: Option<PathBuf>,
    pub cache: Option<PathBuf>,
    pub odin: String,
    pub flags: Vec<String>,
    pub args: Vec<String>,
    pub no_cache: bool,
    pub json: bool,
    pub root: PathBuf,
    pub maps: bool,
}
pub struct Project {
    pub options: Options,
    pub frontend: Frontend,
    pub sources: Vec<Source>,
    pub destination: PathBuf,
    pub outputs: BTreeMap<PathBuf, (usize, Emitted)>,
    pub errors: HashMap<String, String>,
    collections: BTreeMap<String, PathBuf>,
    common: PathBuf,
    file_entry: bool,
}
pub fn absolute(path: &Path) -> Result<PathBuf> {
    let input = if path.is_absolute() {
        path.to_path_buf()
    } else {
        env::current_dir()?.join(path)
    };
    if input.exists() {
        return Ok(fs::canonicalize(input)?);
    }
    if let Some(parent) = input.parent() {
        if parent.exists() {
            return Ok(fs::canonicalize(parent)?.join(
                input
                    .file_name()
                    .ok_or_else(|| Error::new("invalid output path"))?,
            ));
        }
    }
    let mut result = PathBuf::new();
    for part in input.components() {
        match part {
            Component::CurDir => {}
            Component::ParentDir => {
                result.pop();
            }
            other => result.push(other.as_os_str()),
        }
    }
    Ok(result)
}
fn package_files(entry: &Path, test: bool) -> Result<Vec<PathBuf>> {
    if entry.is_file() {
        return Ok(vec![entry.to_path_buf()]);
    }
    let mut files = vec![];
    for item in fs::read_dir(entry)? {
        let item = item?;
        let path = item.path();
        if !item.file_type()?.is_file()
            || !path
                .extension()
                .is_some_and(|ext| ext == "thor" || ext == "odin")
        {
            continue;
        }
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        if !test && (name.ends_with("_test.thor") || name.ends_with("_test.odin")) {
            continue;
        }
        files.push(fs::canonicalize(path)?);
    }
    files.sort();
    Ok(files)
}
impl Project {
    pub fn new(mut options: Options) -> Result<Self> {
        if !options.entry.exists() {
            return Err(Error::new(format!(
                "source does not exist: {}",
                options.entry.display()
            )));
        }
        options.entry = fs::canonicalize(&options.entry)?;
        let file_entry = options.entry.is_file();
        if file_entry
            && !options
                .entry
                .extension()
                .is_some_and(|ext| ext == "thor" || ext == "odin")
        {
            return Err(Error::new(
                "a source file must have a .thor or .odin extension",
            ));
        }
        let (parser, odin) = bootstrap(&options.root, &options.odin)?;
        let cache = options
            .cache
            .clone()
            .or_else(|| env::var_os("THOR_CACHE_DIR").map(PathBuf::from))
            .unwrap_or_else(|| {
                if file_entry {
                    options.entry.parent().unwrap().join(".thor-cache")
                } else {
                    options.entry.join(".thor-cache")
                }
            });
        let test = options.command == "test"
            || options.flags.iter().any(|flag| flag == "-build-mode:test");
        let target = options
            .flags
            .iter()
            .find_map(|flag| flag.strip_prefix("-target:"))
            .and_then(|target| target.split_once('_'))
            .map(|(os, arch)| (os.into(), arch.into()));
        let frontend = Frontend {
            fingerprint: sha256(&fs::read(&parser)?),
            executable: parser,
            odin,
            cache_dir: if options.no_cache { None } else { Some(cache) },
            test,
            target,
            project_name: options
                .entry
                .file_stem()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned(),
        };
        let mut collections = BTreeMap::new();
        collections.insert("thor".into(), options.root.join("runtime"));
        for flag in &options.flags {
            if let Some(value) = flag.strip_prefix("-collection:") {
                let (name, path) = value
                    .split_once('=')
                    .ok_or_else(|| Error::new(format!("invalid collection flag: {flag}")))?;
                collections.insert(name.into(), absolute(Path::new(path))?);
            }
        }
        let base = options
            .entry
            .parent()
            .filter(|_| file_entry)
            .unwrap_or(&options.entry);
        let common = base.to_path_buf();
        let destination = absolute(&options.output.clone().unwrap_or_else(|| {
            env::current_dir()
                .unwrap_or_default()
                .join(".thor-rust-build")
                .join(&frontend.project_name)
        }))?;
        let mut project = Self {
            options,
            frontend,
            sources: vec![],
            destination,
            outputs: BTreeMap::new(),
            errors: HashMap::new(),
            collections,
            common,
            file_entry,
        };
        let entry = project.options.entry.clone();
        project.discover(&entry, &mut HashSet::new())?;
        if project.sources.is_empty() {
            return Err(Error::new(format!(
                "no .thor or .odin sources found: {}",
                entry.display()
            )));
        }
        for source in &project.sources {
            if source.shared_native {
                continue;
            }
            let parent = source.path.parent().unwrap();
            while !parent.starts_with(&project.common) {
                if !project.common.pop() {
                    return Err(Error::new("source packages have no common directory"));
                }
            }
        }
        for source in &mut project.sources {
            source.collection_roots = project.collections.clone();
        }
        Ok(project)
    }
    fn discover(&mut self, entry: &Path, visited: &mut HashSet<PathBuf>) -> Result<()> {
        let entry = fs::canonicalize(entry)?;
        if !visited.insert(entry.clone()) {
            return Ok(());
        }
        for ancestor in entry.ancestors() {
            if ancestor.join("thor-manifest.json").is_file() {
                return Err(Error::new(format!(
                    "generated Odin is not a Thor input; use the original source: {}",
                    entry.display()
                )));
            }
        }
        let files = package_files(&entry, self.frontend.test)?;
        let mut imports = vec![];
        for path in files {
            if self.sources.iter().any(|s| s.path == path) {
                continue;
            }
            let mut source = self.frontend.parse(&path, entry.is_dir())?;
            source.shared_native = path.extension().is_some_and(|ext| ext == "odin")
                && path.starts_with(self.options.root.join("runtime"))
                && path != self.options.entry
                && path.parent() != Some(self.options.entry.as_path());
            if source.excluded {
                continue;
            }
            for node in &source.nodes {
                if node.kind == "Import" {
                    let import = unquote(&node.op)?;
                    let directory = if let Some((collection, suffix)) = import.split_once(':') {
                        self.collections
                            .get(collection)
                            .map(|base| base.join(suffix))
                    } else {
                        Some(path.parent().unwrap().join(import))
                    };
                    if let Some(directory) = directory {
                        imports.push(directory);
                    }
                }
            }
            self.sources.push(source);
        }
        for import in imports {
            self.discover(&import, visited)?;
        }
        Ok(())
    }
    pub fn lower(&mut self) -> Result<PathBuf> {
        let mut resolver = Resolver::new(&self.sources);
        let mut paths = BTreeSet::new();
        let mut planned = vec![];
        for (file, source) in self.sources.iter().enumerate() {
            if source.shared_native {
                continue;
            }
            let relative = source
                .path
                .strip_prefix(&self.common)
                .map_err(|_| Error::new("source outside project root"))?
                .with_extension("odin");
            if !paths.insert(relative.clone()) {
                return Err(Error::new(format!(
                    "generated filename collision: {}",
                    relative.display()
                )));
            }
            let (emitted, errors) = Lower::new(file, &mut resolver)?.finish()?;
            self.errors.extend(errors);
            planned.push((self.destination.join(relative), (file, emitted)));
        }
        if self
            .sources
            .iter()
            .any(|source| source.path.starts_with(&self.destination))
        {
            return Err(Error::new(
                "output directory cannot contain original source files",
            ));
        }
        if self.destination.is_symlink() {
            return Err(Error::new("output directory must not be a symlink"));
        }
        let manifest_path = self.destination.join("thor-manifest.json");
        let old = if manifest_path.is_file() {
            serde_json::from_slice::<Value>(&fs::read(&manifest_path)?).map_err(|_| {
                Error::new(format!(
                    "invalid generated-output manifest: {}",
                    manifest_path.display()
                ))
            })?
        } else if self.destination.is_dir() && fs::read_dir(&self.destination)?.next().is_some() {
            return Err(Error::new(format!(
                "output directory is not empty and is not managed by Thor: {}",
                self.destination.display()
            )));
        } else {
            json!({})
        };
        self.outputs = planned.into_iter().collect();
        let raw_sources: Vec<_> = self
            .sources
            .iter()
            .map(|source| {
                (
                    source.path.to_string_lossy().into_owned(),
                    source.raw.clone(),
                )
            })
            .collect();
        let mut files: BTreeMap<PathBuf, Vec<u8>> = BTreeMap::new();
        for (path, (_, emitted)) in &self.outputs {
            files.insert(path.clone(), emitted.text.as_bytes().to_vec());
            if self.options.maps || self.options.command == "transpile" {
                let mut map = emitted.map_json(path, &raw_sources);
                map["generated_sha256"] = json!(sha256(emitted.text.as_bytes()));
                files.insert(
                    PathBuf::from(format!("{}.map.json", path.display())),
                    serde_json::to_vec(&map)?,
                );
            }
        }
        let generated: Vec<_> = files
            .keys()
            .map(|path| {
                path.strip_prefix(&self.destination)
                    .unwrap()
                    .to_string_lossy()
                    .into_owned()
            })
            .collect();
        let manifest = json!({"thor_version":VERSION,"engine":"rust","migration_complete":false,"entry":self.options.entry,"source_map_version":1,"debug_map_version":1,
            "generated":generated,"odin_flags":self.native_flags(),"odin_environment":{},"debug_roots":[],"shared_maps":[]});
        // Lower every source before publishing any of them. Ownership is
        // recorded first so an interrupted first write stays recoverable.
        fs::create_dir_all(&self.destination)?;
        for path in files.keys() {
            self.validate_output(path)?;
        }
        atomic_write(&manifest_path, &serde_json::to_vec_pretty(&manifest)?)?;
        for (path, data) in &files {
            atomic_write(path, data)?;
        }
        for relative in old["generated"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str())
        {
            let path = self.destination.join(relative);
            if files.contains_key(&path) || self.validate_output(&path).is_err() {
                continue;
            }
            if path.is_file() || path.is_symlink() {
                fs::remove_file(path)?;
            }
        }
        if self.file_entry {
            self.outputs
                .iter()
                .find(|(_, (file, _))| self.sources[*file].path == self.options.entry)
                .map(|(path, _)| path.clone())
                .ok_or_else(|| Error::new("entry file excluded by target"))
        } else {
            self.outputs
                .iter()
                .find(|(_, (file, _))| {
                    self.sources[*file].path.parent() == Some(self.options.entry.as_path())
                })
                .map(|(path, _)| path.parent().unwrap().to_path_buf())
                .ok_or_else(|| Error::new("entry package excluded by target"))
        }
    }
    fn validate_output(&self, path: &Path) -> Result<()> {
        let relative = path
            .strip_prefix(&self.destination)
            .map_err(|_| Error::new("generated output escapes destination"))?;
        if relative
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
        {
            return Err(Error::new("invalid generated output path"));
        }
        let mut parent = path.parent();
        while let Some(directory) = parent {
            if directory == self.destination {
                break;
            }
            if directory.is_symlink() {
                return Err(Error::new("generated output parent must not be a symlink"));
            }
            parent = directory.parent();
        }
        Ok(())
    }
    fn native_flags(&self) -> Vec<String> {
        let mut result = vec![];
        for flag in &self.options.flags {
            if !flag.starts_with("-collection:")
                && !matches!(flag.as_str(), "-json-errors" | "-keep-executable")
            {
                result.push(flag.clone());
            }
        }
        for (name, base) in &self.collections {
            let mapped = if base.starts_with(&self.common)
                && self
                    .sources
                    .iter()
                    .any(|s| !s.shared_native && s.path.starts_with(base))
            {
                self.destination
                    .join(base.strip_prefix(&self.common).unwrap())
            } else {
                base.clone()
            };
            result.push(format!("-collection:{name}={}", mapped.display()));
        }
        result
    }
    pub fn compile(&self, target: &Path) -> Result<i32> {
        let action = self.options.command.as_str();
        let mut command = Command::new(&self.frontend.odin);
        command
            .arg(if matches!(action, "run" | "test") {
                "build"
            } else {
                action
            })
            .arg(target);
        if self.file_entry {
            command.arg("-file");
        }
        command.args(self.native_flags()).arg("-json-errors");
        let explicit = self
            .options
            .flags
            .iter()
            .find_map(|flag| flag.strip_prefix("-out:"));
        let binary = if let Some(path) = explicit {
            absolute(Path::new(path))?
        } else if action == "build" {
            let cwd = env::current_dir()?;
            let name = cwd.file_name().unwrap_or_default().to_string_lossy();
            cwd.join(format!("{name}{}", env::consts::EXE_SUFFIX))
        } else {
            self.destination
                .join(format!("program{}", env::consts::EXE_SUFFIX))
        };
        if action == "build"
            && explicit.is_none()
            && [
                self.options.root.join("thor"),
                self.options.root.join("thor-rust"),
            ]
            .contains(&binary)
        {
            return Err(Error::new("The default output would overwrite a Thor launcher; choose another name with -out:name."));
        }
        if matches!(action, "build" | "run" | "test") && explicit.is_none() {
            command.arg(format!("-out:{}", binary.display()));
        }
        if action == "test" {
            command.arg("-build-mode:test");
        }
        let output = command.output()?;
        let mut errors = if output.stderr.is_empty() {
            vec![]
        } else {
            let packet: Value = serde_json::from_slice(&output.stderr)
                .map_err(|_| Error::new(String::from_utf8_lossy(&output.stderr).into_owned()))?;
            packet["errors"].as_array().cloned().unwrap_or_default()
        };
        for source in &self.sources {
            errors.extend(
                source.info["diagnostics"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .cloned(),
            );
        }
        if output.status.code().is_none() {
            return Err(Error::new(format!("Odin compiler terminated by {}. This is a backend compiler failure; no source location was provided.",output.status)));
        }
        self.report(&errors);
        if !output.status.success() {
            return Ok(output.status.code().unwrap_or(1));
        }
        if action == "run" || action == "test" {
            let status = Command::new(&binary)
                .args(&self.options.args)
                .stdin(Stdio::inherit())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit())
                .status();
            if !self
                .options
                .flags
                .iter()
                .any(|flag| flag == "-keep-executable")
            {
                let _ = fs::remove_file(&binary);
            }
            let status = status?;
            if let Some(code) = status.code() {
                return Ok(code);
            }
            #[cfg(unix)]
            {
                use std::os::unix::process::ExitStatusExt;
                if let Some(signal) = status.signal() {
                    return Ok(256 - signal);
                }
            }
            return Ok(1);
        }
        Ok(0)
    }
    pub fn translate(&self, errors: &[Value]) -> Vec<Value> {
        errors.iter().map(|error|{
            let mut error=error.clone();
            let mut expression_texts=vec![];
            let file=error["pos"]["file"].as_str().map(PathBuf::from);
            if let Some((index,emitted))=file.as_ref().and_then(|file|self.outputs.get(file)){
                let offset=error["pos"]["offset"].as_u64().unwrap_or(0) as usize;
                let length=error["pos"]["end_column"].as_u64().unwrap_or(0).saturating_sub(error["pos"]["column"].as_u64().unwrap_or(0)) as usize;
                if let Some(origin)=emitted.lookup(offset,length){
                    let source=&self.sources[*index];let (line,column)=source.position(origin.span.start);let (end_line,end_column)=source.position(origin.span.end);
                    if error["msgs"][0].as_str().is_some_and(|message|message.contains("'where' clause")) {
                        for (first,last,expression) in &emitted.expansions {
                            if expression.span==origin.span&&expression.file==origin.file {
                                expression_texts.push((emitted.text[*first..*last].to_string(),source.slice(origin.span).to_string()));
                            }
                        }
                    }
                    error["pos"]=json!({"file":origin.file,"offset":origin.span.start,"end_offset":origin.span.end,"line":line,"column":column,"end_line":end_line,"end_column":end_column});
                }
            }
            if let Some(messages)=error["msgs"].as_array_mut(){for message in messages{
                if let Some(text)=message.as_str(){let mut text=self.remap_references(text);for (generated,original) in &expression_texts{if text.trim()==generated.trim(){let indent=&text[..text.len()-text.trim_start().len()];text=format!("{indent}{original}");break;}}for (token,replacement) in &self.errors{if text.contains(token){text=replacement.clone();break;}}*message=json!(text);}
            }}
            error
        }).collect()
    }
    fn remap_references(&self, text: &str) -> String {
        let mut text = text.to_string();
        for (path, (file, emitted)) in &self.outputs {
            let pattern = format!("{}(", path.display());
            let mut cursor = 0;
            while let Some(found) = text[cursor..].find(&pattern) {
                let start = cursor + found;
                let coordinates = start + pattern.len();
                let Some(close) = text[coordinates..].find(')') else {
                    break;
                };
                let end = coordinates + close;
                let pair = text[coordinates..end]
                    .split_once(':')
                    .or_else(|| text[coordinates..end].split_once(','));
                let Some((line, column)) = pair.and_then(|(line, column)| {
                    Some((
                        line.trim().parse::<usize>().ok()?,
                        column.trim().parse::<usize>().ok()?,
                    ))
                }) else {
                    cursor = end + 1;
                    continue;
                };
                let offset = emitted
                    .text
                    .split_inclusive('\n')
                    .take(line.saturating_sub(1))
                    .map(str::len)
                    .sum::<usize>()
                    + column.saturating_sub(1);
                if let Some(origin) = emitted.lookup(offset, 1) {
                    let (line, column) = self.sources[*file].position(origin.span.start);
                    let replacement = format!("{}({line}:{column})", origin.file);
                    text.replace_range(start..end + 1, &replacement);
                    cursor = start + replacement.len();
                } else {
                    cursor = end + 1;
                }
            }
        }
        text
    }
    fn report(&self, errors: &[Value]) {
        let errors = self.translate(errors);
        if self.options.json {
            eprintln!(
                "{}",
                json!({"error_count":errors.iter().filter(|error|error["type"]=="error").count(),"errors":errors})
            );
        } else {
            for error in errors {
                print_diagnostic(&error);
            }
        }
    }
}
pub fn print_diagnostic(error: &Value) {
    if let Some(file) = error["pos"]["file"].as_str() {
        eprint!(
            "{}({}:{}): ",
            file, error["pos"]["line"], error["pos"]["column"]
        );
    } else {
        eprint!("thor-rust ");
    }
    eprint!(
        "{}: ",
        if error["type"] == "warning" {
            "Warning"
        } else {
            "Error"
        }
    );
    if let Some(messages) = error["msgs"].as_array() {
        for message in messages {
            eprintln!("{}", message.as_str().unwrap_or("invalid diagnostic"));
        }
    }
}
