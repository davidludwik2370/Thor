//! Explicit ownership records for reversible user configuration and uninstall.
use super::*;

pub(super) fn prerequisites(o:&Options)->Result<()> {
    if o.no_deps {println!("SKIP: automatic prerequisite installation (--no-install-deps); build/run checks remain enabled");return Ok(());}
    let temp=env::temp_dir().join(format!("thor-prerequisites-{}",unique()));fs::create_dir(&temp).map_err(err)?;
    let _guard=RemoveDir(temp.clone());
    let mut cmd;
    if cfg!(windows) {
        let file=temp.join("prerequisites.ps1");fs::write(&file,include_str!("../prerequisites.ps1")).map_err(err)?;
        cmd=Command::new("powershell.exe");cmd.args(["-NoProfile","-File"]).arg(file);
        if o.vscode && o.code.is_none() {cmd.arg("-VSCode");}
    } else {
        let file=temp.join("prerequisites.sh");fs::write(&file,include_str!("../prerequisites.sh")).map_err(err)?;
        cmd=Command::new("sh");cmd.arg(file);
        if o.vscode && o.code.is_none() {cmd.arg("--vscode");}
    }
    command(cmd)
}

fn state_path(prefix:&Path)->PathBuf {prefix.join("setup-state.json")}
pub(super) fn state(prefix:&Path)->Result<Value> {
    let path=state_path(prefix);
    if !path.exists() {return Ok(json!({"schema":1,"managed_by":"thor-install","files":{},"editors":[]}));}
    let value=read_json(&path)?;
    if value["schema"]!=1 || value["managed_by"]!="thor-install" {return Err("unrecognized setup ownership record; preserved".into());}
    Ok(value)
}
pub(super) fn save(prefix:&Path,value:&Value)->Result<()> {atomic_write(&state_path(prefix),&serde_json::to_vec_pretty(value).map_err(err)?)}
pub(super) fn track_artifact(prefix:&Path,path:&Path)->Result<()> {
    let mut value=state(prefix)?;
    if value["artifacts"].is_null(){value["artifacts"]=json!({});}
    value["artifacts"][path.to_str().ok_or("non-UTF-8 backup path")?]=json!(digest(path)?);
    save(prefix,&value)
}
pub(super) fn track_file(prefix:&Path,path:&Path,before:Option<String>,after:&str)->Result<()> {
    if before.as_deref()==Some(after) {return Ok(());}
    let mut value=state(prefix)?;let key=path.to_str().ok_or("non-UTF-8 settings path")?;
    let entry=&mut value["files"][key];
    if entry.is_null() {*entry=json!({"before":before});}
    else if path.file_name().is_some_and(|s|s=="settings.json") && before.as_deref()!=entry["after"].as_str() {
        // An upgrade must not adopt intervening user settings as installer-owned.
        // Reverse the prior managed edits in the CURRENT pre-edit document to
        // obtain a new restoration baseline, retaining user keys and comments.
        let baseline=jsonc::restore_managed(before.as_deref().unwrap_or("{}"),
            entry["before"].as_str().unwrap_or("{}"),field(entry,"after")?)?;
        entry["before"]=json!(baseline);
    }
    entry["after"]=json!(after);save(prefix,&value)
}
pub(super) fn verify_inventory(root:&Path)->Result<()> {
    let manifest=read_json(&root.join(MANIFEST))?;validate_manifest(&manifest)?;
    for (name,record) in manifest["files"].as_object().ok_or("invalid file inventory")? {
        let path=root.join(name);
        if path.is_symlink() || digest(&path)?!=field(record,"sha256")? {return Err(format!("installed file failed verification: {}",path.display()));}
    }
    println!("PASS: installed file inventory and checksums");Ok(())
}
fn restore_files(state:&Value,dry:bool)->Result<()> {
    if let Some(files)=state["files"].as_object() {
        for (name,record) in files {
            let path=Path::new(name);
            if path.is_symlink() {println!("RETAINED: settings symlink {}",path.display());continue;}
            let Some(current)=fs::read_to_string(path).ok() else {continue;};
            let before=record["before"].as_str();let after=field(record,"after")?;
            if current==after {
                println!("Remove installer changes: {}",path.display());
                if !dry {if let Some(before)=before {atomic_write(path,before.as_bytes())?;}else{fs::remove_file(path).map_err(err)?;}}
            } else if path.file_name().is_some_and(|s|s=="settings.json") {
                let restored=super::jsonc::restore_managed(&current,before.unwrap_or("{}"),after);
                match restored {
                    Ok(text) if text!=current=>{println!("Restore managed settings, retain user edits: {}",path.display());if !dry{atomic_write(path,text.as_bytes())?;}},
                    _=>println!("RETAINED: user-modified settings {}",path.display()),
                }
            } else {
                // Shell profiles are additive: remove ONLY our exact marked block.
                let lines:Vec<_>=after.lines().collect();
                let start=lines.iter().position(|s|*s=="# >>> thor-install PATH >>>");
                let end=lines.iter().position(|s|*s=="# <<< thor-install PATH <<<");
                if let (Some(a),Some(b))=(start,end) {
                    let block=lines[a..=b].join("\n");
                    if current.matches(&block).count()==1 {
                        println!("Remove managed PATH block, retain user edits: {}",path.display());
                        if !dry {atomic_write(path,current.replacen(&block,"",1).as_bytes())?;}continue;
                    }
                }
                println!("RETAINED: user-modified file {}",path.display());
            }
        }
    }Ok(())
}

// Never follow source/version links, and remove only files whose hashes still
// match the inventory. Unknown files and edits remain at their original paths.
fn remove_inventory(root:&Path,manifest:&Value,dry:bool)->Result<()> {
    if root.is_symlink() {println!("RETAINED: version symlink {}",root.display());return Ok(());}
    if !dry {
        let cache=root.join(".thor-cache");
        if generated_cache(&cache) {let _=fs::remove_dir_all(cache);}
        let views=root.join("lib");
        if generated_views(root,&views) {let _=fs::remove_dir_all(views);}
    }
    for (name,record) in manifest["files"].as_object().ok_or("invalid inventory")? {
        if !safe_path(Path::new(name)) {return Err("unsafe uninstall inventory".into());}
        let path=root.join(name);
        let mut ancestor=path.parent();let mut linked=false;
        while let Some(p)=ancestor {if p==root{break;}if p.is_symlink(){linked=true;break;}ancestor=p.parent();}
        if !linked && !path.is_symlink() && digest(&path).ok().as_deref()==record["sha256"].as_str() {
            if !dry {fs::remove_file(&path).map_err(err)?;}
        } else if path.exists() || path.is_symlink() {println!("RETAINED: modified/unmanaged file {}",path.display());}
    }
    if !dry {
        let _=fs::remove_file(root.join(MANIFEST));remove_empty(root);
    }
    Ok(())
}
fn remove_empty(root:&Path) {
    if root.is_symlink(){return;}
    if let Ok(entries)=fs::read_dir(root) {for entry in entries.flatten() {if entry.file_type().is_ok_and(|t|t.is_dir()){remove_empty(&entry.path());}}}
    let _=fs::remove_dir(root);
}

pub(super) fn uninstall(o:&Options)->Result<()> {
    let record=match registered() {Ok(v)=>v,Err(_) if !registry()?.exists()=>{println!("Thor is not installed.");return Ok(());},Err(e)=>return Err(e)};
    let prefix=absolute(Path::new(field(&record,"prefix")?))?;
    if o.prefix.as_ref().is_some_and(|p|absolute(p).ok().as_ref()!=Some(&prefix)) {return Err("--prefix does not match the registered installation; nothing removed".into());}
    if prefix.is_symlink() || read_json(&prefix.join(MARKER))?["managed_by"]!="thor-install" {return Err("installation owner cannot be verified; nothing removed".into());}
    let lock=prefix.join(".install-lock");fs::create_dir(&lock).map_err(|e|format!("could not lock installation: {e}"))?;let guard=RemoveDir(lock);
    let state=state(&prefix)?;
    println!("{}Uninstall Thor from {}",if o.dry{"Dry run: "}else{""},prefix.display());
    // Restore editor settings before removing the OLS server they reference.
    vscode::uninstall(&state,&record,o.dry)?;
    restore_files(&state,o.dry)?;
    #[cfg(unix)]
    for name in [".profile",".zprofile"] {
        let file=home()?.join(name);
        if state["files"].get(file.to_string_lossy().as_ref()).is_some() || file.is_symlink(){continue;}
        if let Ok(text)=fs::read_to_string(&file) {
            let block=format!("# >>> thor-install PATH >>>\nexport PATH={}:\"$PATH\"\n# <<< thor-install PATH <<<",shell_quote(Path::new(field(&record,"bin")?)));
            if text.matches(&block).count()==1 {
                println!("Remove matching legacy managed PATH block: {}",file.display());
                if !o.dry {atomic_write(&file,text.replacen(&block,"",1).as_bytes())?;}
            }
        }
    }
    if let Some(artifacts)=state["artifacts"].as_object(){for (name,hash) in artifacts {
        let path=Path::new(name);
        if !path.is_symlink() && digest(path).ok().as_deref()==hash.as_str() {
            if !o.dry {fs::remove_file(path).map_err(err)?;}
        } else if path.exists(){println!("RETAINED: user-modified backup {}",path.display());}
    }}
    // Native OLS cache files have their own independently checked inventory.
    let editor=prefix.join("editor/ols");
    if !editor.is_symlink() {if let Ok(entries)=fs::read_dir(&editor) {for entry in entries.flatten() {
        let root=entry.path();if !entry.file_type().is_ok_and(|t|t.is_dir()){continue;}
        if let Ok(record)=read_json(&root.join("THOR-OLS.json")) {
            if let Some(files)=record["files"].as_object() {
                let files=files.iter().map(|(k,v)|(k.clone(),json!({"sha256":v}))).collect::<serde_json::Map<_,_>>();
                remove_inventory(&root,&json!({"files":files}),o.dry)?;
                if !o.dry {let _=fs::remove_file(root.join("THOR-OLS.json"));remove_empty(&root);}
            }
        }
    }}}
    #[cfg(windows)]
    if (state["windows_path_added"]==true || !state_path(&prefix).exists()) && !o.dry {
        let bin=field(&record,"bin")?.replace('\'',"''");
        let script=format!("$p=[Environment]::GetEnvironmentVariable('Path','User'); $b='{bin}'; [Environment]::SetEnvironmentVariable('Path',(($p -split ';' | Where-Object {{ $_ -ne $b }}) -join ';'),'User')");
        let mut cmd=Command::new("powershell.exe");cmd.args(["-NoProfile","-NonInteractive","-Command",&script]);command(cmd)?;
    }
    let versions=prefix.join("versions");
    if versions.is_symlink() {return Err("version directory is a symlink; preserved".into());}
    if let Ok(entries)=fs::read_dir(&versions) {for entry in entries {
        let entry=entry.map_err(err)?;let root=entry.path();
        if !entry.file_type().map_err(err)?.is_dir(){continue;}
        let manifest=match read_json(&root.join(MANIFEST)){Ok(v)=>v,Err(_)=>continue};
        validate_manifest(&manifest)?;
        let hash=hex(Sha256::digest(serde_json::to_vec(&manifest).map_err(err)?));
        if entry.file_name().to_str()!=Some(&format!("{}-{}",field(&manifest,"version")?,&hash[..12])){continue;}
        println!("Remove inventoried toolchain: {}",root.display());remove_inventory(&root,&manifest,o.dry)?;
    }}
    for name in ["thor","thor-install","odin"] {
        let path=prefix.join("bin").join(format!("{name}{}",if cfg!(windows){".cmd"}else{""}));
        #[cfg(unix)] let owned=fs::read_link(&path).is_ok_and(|target|target.starts_with(&versions));
        #[cfg(windows)] let owned=fs::read_to_string(&path).is_ok_and(|s|s.starts_with("@rem Managed by thor-install\r\n"));
        if owned && !o.dry {fs::remove_file(path).map_err(err)?;}
    }
    if !o.dry {
        let editor=prefix.join("editor");
        if let Ok(entries)=fs::read_dir(&editor){for entry in entries.flatten(){
            let name=entry.file_name().to_string_lossy().into_owned();
            if name.starts_with("ols-profile-") && name.ends_with(".json") && entry.file_type().is_ok_and(|t|t.is_file()) {
                if read_json(&entry.path()).is_ok_and(|v|v["server"].as_str().is_some_and(|s|Path::new(s).starts_with(&prefix))) {let _=fs::remove_file(entry.path());}
            }
        }}
        clean_shared_cache(o.dry)?;
        for name in ["setup-state.json","previous-installation.json",MARKER] {let _=fs::remove_file(prefix.join(name));}
        fs::remove_file(registry()?).map_err(err)?;
        drop(guard);remove_empty(&prefix);
    }
    println!("{}Thor uninstall complete. VS Code, developer tools, projects, source checkouts and user-modified files are retained.",if o.dry{"Dry run: "}else{""});Ok(())
}

fn clean_shared_cache(dry:bool)->Result<()> {
    // Never act on THOR_CACHE_DIR: it may point into a project or another tool.
    let root=if cfg!(windows){env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or(home()?.join("AppData/Local")).join("Thor/cache")}
        else if cfg!(target_os="macos"){home()?.join("Library/Caches/Thor")}
        else{env::var_os("XDG_CACHE_HOME").map(PathBuf::from).unwrap_or(home()?.join(".cache")).join("thor")};
    if root.is_symlink()||root.join("store-v2").is_symlink(){return Ok(());}
    for digit in "0123456789abcdef".chars() {
        let bucket=root.join("store-v2").join(digit.to_string());
        if bucket.is_symlink(){continue;}
        if let Ok(entries)=fs::read_dir(&bucket){for entry in entries.flatten(){
            let name=entry.file_name().to_string_lossy().into_owned();
            let owned=name.strip_suffix(".bin").and_then(|s|s.rsplit_once('-')).is_some_and(|(namespace,key)|
                !namespace.is_empty()&&namespace.len()<=96&&namespace.bytes().all(|b|b.is_ascii_alphanumeric()||b"_-".contains(&b))&&
                key.len()==64&&key.bytes().all(|b|b.is_ascii_hexdigit()&&!b.is_ascii_uppercase()));
            if owned&&entry.file_type().is_ok_and(|t|t.is_file())&&!dry {fs::remove_file(entry.path()).map_err(err)?;}
        }}
    }
    if !dry{remove_empty(&root);}Ok(())
}
