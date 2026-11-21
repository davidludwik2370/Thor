//! Complete requested declarations in partially inspected native packages.
//! The catalog selects parser inputs only; the AST/type/effect checker supplies
//! every semantic fact. Ordinary compilation never constructs this catalog.
use super::*;
use crate::semantics::ImportDemand;

#[derive(Default)]
pub(super) struct Discovery {
    packages: BTreeMap<PathBuf, Vec<(PathBuf, HashSet<String>)>>,
    attempted: HashSet<PathBuf>,
}
impl Discovery {
    pub fn load(&mut self, p: &mut Project, demands: &[ImportDemand]) -> Result<()> {
        let mut requests = BTreeMap::<PathBuf, HashSet<String>>::new();
        for demand in demands {
            let source = &p.sources[demand.file];
            let directory = if let Some((collection, path)) = demand.path.split_once(':') {
                let Some(root) = source.collection_roots.get(collection) else {
                    continue;
                };
                root.join(path)
            } else {
                source.path.parent().unwrap().join(&demand.path)
            };
            let Ok(directory) = canonicalize(directory) else {
                continue;
            };
            if !source.info["odin_root"]
                .as_str()
                .is_some_and(|root| directory.starts_with(root))
                && !p
                    .sources
                    .iter()
                    .any(|s| s.shared_native && s.path.parent() == Some(directory.as_path()))
            {
                continue;
            }
            requests
                .entry(directory)
                .or_default()
                .insert(demand.name.clone());
        }
        let mut files = BTreeSet::new();
        for (directory, names) in requests {
            if !self.packages.contains_key(&directory) {
                let mut catalog = vec![];
                for path in package_files(&directory, false, &p.options.overlays)? {
                    let raw = if let Some(raw) = p.options.overlays.get(&path) {
                        raw.clone()
                    } else {
                        fs::read_to_string(&path)?
                    };
                    catalog.push((path, declared_names(&raw)));
                }
                self.packages.insert(directory.clone(), catalog);
                crate::profile::count("no_alloc.discovery_catalogs", 1);
            }
            for (path, declared) in &self.packages[&directory] {
                if !self.attempted.contains(path)
                    && !p.sources.iter().any(|s| s.path == *path)
                    && names.iter().any(|name| declared.contains(name))
                {
                    files.insert(path.clone());
                }
            }
        }
        let mut frontend = p.frontend.clone();
        frontend.normalized_cache = true;
        if frontend.cache_dir.is_some() {
            frontend.cache_dir = Some(p.options.root.join(".thor-cache/sdk-parser"));
        }
        let mut roots = p.collections.clone();
        if let Some(root) = p.sources.iter().find_map(|s| s.info["odin_root"].as_str()) {
            for name in ["base", "core", "vendor"] {
                roots
                    .entry(name.into())
                    .or_insert_with(|| Path::new(root).join(name));
            }
        }
        let files = files.into_iter().collect::<Vec<_>>();
        let parsed = crate::parallel::map(&files, |path| {
            frontend.parse_overlay(path, true, p.options.overlays.get(path).map(String::as_str))
        });
        for (path, source) in files.into_iter().zip(parsed) {
            self.attempted.insert(path);
            let mut source = source?;
            if source.excluded {
                continue;
            }
            source.shared_native = true;
            source.collection_roots = roots.clone();
            p.sources.push(source);
            crate::profile::count("no_alloc.discovery_files", 1);
        }
        Ok(())
    }
}
// A deliberately permissive lexical index: local/field names can select an
// extra file, but comments and strings cannot. Multiline and grouped names are
// included. No name from this catalog is treated as a declaration or an effect.
fn declared_names(raw: &str) -> HashSet<String> {
    let bytes = raw.as_bytes();
    let mut i = 0;
    let mut names = HashSet::new();
    let mut pending = None;
    while i < bytes.len() {
        if bytes[i..].starts_with(b"//") {
            while i < bytes.len() && bytes[i] != b'\n' {
                i += 1;
            }
            continue;
        }
        if bytes[i..].starts_with(b"/*") {
            i += 2;
            let mut depth = 1;
            while i < bytes.len() && depth > 0 {
                if bytes[i..].starts_with(b"/*") {
                    depth += 1;
                    i += 2;
                } else if bytes[i..].starts_with(b"*/") {
                    depth -= 1;
                    i += 2;
                } else {
                    i += 1;
                }
            }
            continue;
        }
        if matches!(bytes[i], b'"' | b'\'' | b'`') {
            pending = None;
            let quote = bytes[i];
            i += 1;
            while i < bytes.len() {
                let b = bytes[i];
                i += 1;
                if b == quote {
                    break;
                }
                if b == b'\\' && quote != b'`' && i < bytes.len() {
                    i += 1;
                }
            }
            continue;
        }
        let ch = raw[i..].chars().next().unwrap();
        if ch == '_' || unicode_ident::is_xid_start(ch) {
            let start = i;
            i += ch.len_utf8();
            while i < bytes.len() {
                let ch = raw[i..].chars().next().unwrap();
                if !unicode_ident::is_xid_continue(ch) {
                    break;
                }
                i += ch.len_utf8();
            }
            pending = Some(&raw[start..i]);
        } else {
            if matches!(ch, ':' | ',') {
                if let Some(name) = pending {
                    names.insert(name.into());
                }
            }
            if !ch.is_whitespace() {
                pending = None;
            }
            i += ch.len_utf8();
        }
    }
    names
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn catalog_ignores_comments_and_strings_and_keeps_grouped_names() {
        let names=declared_names("/* fake :: proc(){} /* nested */ */\ntext::`wrong: int`\na,\n β: int\nreal /* gap */ :: proc(){} // nope:int\n");
        assert!(
            names.contains("text")
                && names.contains("a")
                && names.contains("β")
                && names.contains("real")
        );
        assert!(!names.contains("fake") && !names.contains("wrong") && !names.contains("nope"));
    }
}
