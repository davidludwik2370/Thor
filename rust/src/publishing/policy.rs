//! Shared configuration/resource policy for native publishing.
use crate::diagnostic::{Error, Result};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
#[derive(Clone, Default, Debug)]
pub struct Settings {
    pub text: BTreeMap<String, String>,
    pub resources: Vec<String>,
    pub libraries: Vec<String>,
}
pub fn relative_path(value: &str, description: &str) -> Result<PathBuf> {
    if value.is_empty() || value.contains('\0') {
        return Err(Error::new(format!(
            "{description} must be a nonempty relative path."
        )));
    }
    let normalized = value.replace('\\', "/");
    let parts = normalized
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect::<Vec<_>>();
    if normalized.starts_with('/')
        || value.chars().nth(1) == Some(':')
        || parts.contains(&"..")
        || parts.is_empty()
    {
        return Err(Error::new(format!(
            "{description} must stay inside the project: {value:?}"
        )));
    }
    Ok(parts.iter().collect())
}
pub fn validate_name(value: &str, description: &str) -> Result<String> {
    if value.trim().is_empty()
        || matches!(value, "." | "..")
        || value.chars().any(|c| c < ' ' || "/\\<>:\"|?*".contains(c))
        || value.ends_with([' ', '.'])
    {
        return Err(Error::new(format!(
            "{description} must be a valid file name: {value:?}"
        )));
    }
    let first = value.split('.').next().unwrap_or_default().to_uppercase();
    if ["CON", "PRN", "AUX", "NUL"].contains(&first.as_str())
        || (1..=9).any(|i| first == format!("COM{i}") || first == format!("LPT{i}"))
    {
        return Err(Error::new(format!(
            "{description} is reserved on Windows: {value:?}"
        )));
    }
    Ok(value.into())
}
pub fn read_config(root: &Path) -> Result<Settings> {
    let path = root.join("thor.toml");
    if !path.exists() {
        return Ok(Settings::default());
    }
    let text = fs::read_to_string(&path)
        .map_err(|error| Error::new(format!("Cannot read thor.toml: {error}")))?;
    let config: toml::Value = toml::from_str(&text)
        .map_err(|error| Error::new(format!("Cannot read thor.toml: {error}")))?;
    let Some(config) = config.get("publish") else {
        return Ok(Settings::default());
    };
    let config = config
        .as_table()
        .ok_or_else(|| Error::new("thor.toml [publish] must be a table."))?;
    let allowed = [
        "name",
        "id",
        "version",
        "icon",
        "resources",
        "libraries",
        "type",
    ];
    let unknown = config
        .keys()
        .filter(|key| !allowed.contains(&key.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !unknown.is_empty() {
        return Err(Error::new(format!(
            "Unknown [publish] setting: {}",
            unknown.join(", ")
        )));
    }
    let mut settings = Settings::default();
    for key in ["name", "id", "version", "icon", "type"] {
        if let Some(value) = config.get(key) {
            settings.text.insert(
                key.into(),
                value
                    .as_str()
                    .ok_or_else(|| Error::new(format!("[publish].{key} must be a string.")))?
                    .into(),
            );
        }
    }
    for key in ["resources", "libraries"] {
        if let Some(value) = config.get(key) {
            let values = value
                .as_array()
                .filter(|values| {
                    values
                        .iter()
                        .all(|value| value.as_str().is_some_and(|value| !value.is_empty()))
                })
                .ok_or_else(|| {
                    Error::new(format!(
                        "[publish].{key} must be an array of nonempty paths."
                    ))
                })?;
            let values = values
                .iter()
                .map(|value| value.as_str().unwrap().to_string())
                .collect();
            if key == "resources" {
                settings.resources = values;
            } else {
                settings.libraries = values;
            }
        }
    }
    Ok(settings)
}
#[derive(Default, Debug)]
pub struct Resources {
    pub files: BTreeMap<PathBuf, PathBuf>,
    pub directories: BTreeSet<PathBuf>,
}
pub fn collect_resources(root: &Path, entries: &[String]) -> Result<Resources> {
    fn visit(
        root: &Path,
        path: &Path,
        relative: &Path,
        active: &mut BTreeSet<PathBuf>,
        out: &mut Resources,
    ) -> Result<()> {
        if !path.exists() {
            return Err(Error::new(format!(
                "Resource does not exist: {}",
                relative.display()
            )));
        }
        let resolved = fs::canonicalize(path)?;
        if !resolved.starts_with(root) {
            return Err(Error::new(format!(
                "Resource symlink leaves the project: {}",
                relative.display()
            )));
        }
        if path.is_dir() {
            if !active.insert(resolved.clone()) {
                return Err(Error::new(format!(
                    "Resource directory contains a symlink cycle: {}",
                    relative.display()
                )));
            }
            out.directories.insert(relative.to_path_buf());
            let mut children = fs::read_dir(path)?
                .map(|entry| entry.map(|entry| entry.path()))
                .collect::<std::io::Result<Vec<_>>>()?;
            children.sort();
            for child in children {
                visit(
                    root,
                    &child,
                    &relative.join(child.file_name().unwrap()),
                    active,
                    out,
                )?;
            }
            active.remove(&resolved);
        } else if path.is_file() {
            out.files.insert(relative.to_path_buf(), path.to_path_buf());
        } else {
            return Err(Error::new(format!(
                "Resource is not an ordinary file or directory: {}",
                relative.display()
            )));
        }
        Ok(())
    }
    let mut resources = Resources::default();
    for value in entries {
        let relative = relative_path(value, "Resource")?;
        visit(
            root,
            &root.join(&relative),
            &relative,
            &mut BTreeSet::new(),
            &mut resources,
        )?;
    }
    Ok(resources)
}
pub fn shell_quote(value: &str) -> String {
    if !value.is_empty()
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c))
    {
        value.into()
    } else {
        format!("'{}'", value.replace('\'', "'\"'\"'"))
    }
}
pub fn copy_file(source: &Path, destination: &Path) -> Result<()> {
    fs::copy(source, destination)?;
    let metadata = fs::metadata(source)?;
    filetime::set_file_times(
        destination,
        filetime::FileTime::from_last_access_time(&metadata),
        filetime::FileTime::from_last_modification_time(&metadata),
    )?;
    Ok(())
}
pub fn absolute(path: &Path) -> Result<PathBuf> {
    fn resolve(path: &Path, links: &mut BTreeSet<PathBuf>) -> Result<PathBuf> {
        if path.exists() {
            return Ok(fs::canonicalize(path)?);
        }
        if fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_symlink()) {
            if !links.insert(path.to_path_buf()) {
                return Err(Error::new("Path contains a symlink cycle."));
            }
            let target = fs::read_link(path)?;
            let target = if target.is_absolute() {
                target
            } else {
                path.parent().unwrap().join(target)
            };
            return resolve(&target, links);
        }
        if matches!(
            path.components().next_back(),
            Some(std::path::Component::ParentDir)
        ) {
            let parent = resolve(path.parent().unwrap(), links)?;
            return Ok(parent.parent().unwrap_or(&parent).to_path_buf());
        }
        let Some(name) = path.file_name() else {
            return Ok(path.to_path_buf());
        };
        let parent = path
            .parent()
            .ok_or_else(|| Error::new("Path has no parent."))?;
        Ok(resolve(parent, links)?.join(name))
    }
    let path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    resolve(&path, &mut BTreeSet::new())
}
