//! Build unmodified upstream OLS for platforms its extension cannot download.
use super::*;

pub(super) const REVISION:&str="0c9438faeba846232caaa22fe9f22b30c302dba2";

fn copy_tree(from:&Path,to:&Path)->Result<()> {
    fs::create_dir_all(to).map_err(err)?;
    for entry in fs::read_dir(from).map_err(err)? {
        let entry=entry.map_err(err)?;let kind=entry.file_type().map_err(err)?;
        if kind.is_dir() {copy_tree(&entry.path(),&to.join(entry.file_name()))?;}
        else if kind.is_file() {fs::copy(entry.path(),to.join(entry.file_name())).map_err(err)?;}
        else {return Err("OLS source contains unsupported links/special files".into());}
    }Ok(())
}

pub(super) fn prepare(root:&Path,prefix:&Path)->Result<PathBuf> {
    let upstream=read_json(&root.join("ols/UPSTREAM.json")).map_err(|_|"This bundle lacks pinned upstream OLS source. Use an updated native bundle; the Thor CLI remains installed.".to_string())?;
    if upstream["revision"]!=REVISION {return Err("unsupported upstream OLS source revision".into());}
    let manifest=read_json(&root.join(MANIFEST))?;
    let inputs:serde_json::Map<String,Value>=manifest["files"].as_object().ok_or("missing bundle inventory")?.iter()
        .filter(|(k,_)|k.starts_with("ols/")||k.starts_with("odin/")).map(|(k,v)|(k.clone(),v.clone())).collect();
    let key=hex(Sha256::digest(serde_json::to_vec(&json!({"inputs":inputs,"root":root,"build":"checked-speed-threads2-v1"})).map_err(err)?));
    let cache=prefix.join("editor/ols");fs::create_dir_all(&cache).map_err(err)?;
    let directory=cache.join(&key[..16]);
    if directory.exists() {
        let state=read_json(&directory.join("THOR-OLS.json"))?;
        if state["key"]!=key {return Err("OLS build cache does not match its inputs; it was preserved".into());}
        for (name,hash) in state["files"].as_object().ok_or("invalid OLS cache inventory")? {
            if !safe_path(Path::new(name)) || digest(&directory.join(name))?!=hash.as_str().unwrap_or("") {
                return Err(format!("OLS cache was modified: {name}; it was preserved"));
            }
        }
        println!("Reusing native upstream OLS build.");return Ok(directory.join(format!("ols{}",env::consts::EXE_SUFFIX)));
    }
    let lock=cache.join(".build-lock");fs::create_dir(&lock).map_err(|e|format!("Could not lock native OLS build: {e}. Another installer may be running; retry when it finishes."))?;
    let _lock=RemoveDir(lock);
    let stage=cache.join(format!(".stage-{}",unique()));fs::create_dir(&stage).map_err(err)?;let _stage=RemoveDir(stage.clone());
    println!("Building native upstream OLS for this host (two compiler threads)...");
    let mut cmd=Command::new(root.join(format!("odin/odin{}",env::consts::EXE_SUFFIX)));
    cmd.arg("build").arg(root.join("ols/src")).arg(format!("-collection:src={}",root.join("ols/src").display()))
        .args(["-o:speed","-thread-count:2"]).arg("-define:VERSION=upstream-0c9438f")
        .arg(format!("-out:{}",stage.join(format!("ols{}",env::consts::EXE_SUFFIX)).display())).env_remove("ODIN_ROOT");
    command(cmd).map_err(|e|format!("Native OLS build failed. Install the platform's Clang/linker prerequisites, then rerun --vscode. The Thor CLI is available. {e}"))?;
    // This upstream revision does not implement --version: it starts its LSP
    // read loop instead. Never launch it as a synchronous installer probe.
    if !stage.join(format!("ols{}",env::consts::EXE_SUFFIX)).is_file() || fs::metadata(stage.join(format!("ols{}",env::consts::EXE_SUFFIX))).map_err(err)?.len()==0 {
        return Err("Odin did not produce a native OLS executable; rerun --vscode".into());
    }
    copy_tree(&root.join("ols/builtin"),&stage.join("builtin"))?;
    fs::copy(root.join("ols/LICENSE"),stage.join("LICENSE")).map_err(err)?;
    let config=json!({"enable_semantic_tokens":true,"enable_hover":true,"enable_document_symbols":true,"enable_snippets":true,"enable_references":true,
        "odin_command":root.join(format!("odin/odin{}",env::consts::EXE_SUFFIX)),"odin_root_override":root.join("odin")});
    atomic_write(&stage.join("ols.json"),&serde_json::to_vec_pretty(&config).map_err(err)?)?;
    let mut files=serde_json::Map::new();
    fn inventory(root:&Path,dir:&Path,files:&mut serde_json::Map<String,Value>)->Result<()> {
        for e in fs::read_dir(dir).map_err(err)? {let e=e.map_err(err)?;let p=e.path();
            if e.file_type().map_err(err)?.is_dir() {inventory(root,&p,files)?;}
            else {files.insert(p.strip_prefix(root).map_err(err)?.to_string_lossy().replace('\\',"/"),json!(digest(&p)?));}
        }Ok(())
    }
    inventory(&stage,&stage,&mut files)?;
    atomic_write(&stage.join("THOR-OLS.json"),&serde_json::to_vec_pretty(&json!({"key":key,"files":files})).map_err(err)?)?;
    fs::rename(&stage,&directory).map_err(err)?;Ok(directory.join(format!("ols{}",env::consts::EXE_SUFFIX)))
}

pub(super) fn configure(root:&Path,prefix:&Path,settings:&Path)->Result<()> {
    let text=match fs::read_to_string(settings) {Ok(t)=>t,Err(e) if e.kind()==std::io::ErrorKind::NotFound=>"{}\n".into(),Err(e)=>return Err(err(e))};
    let doc=jsonc::Document::parse(text.clone())?;
    let state_path=prefix.join("editor").join(format!("ols-profile-{}.json",&hex(Sha256::digest(settings.to_string_lossy().as_bytes()))[..16]));
    let old=if state_path.exists(){read_json(&state_path)?}else{Value::Null};
    if let Some(value)=doc.value.get("ols.server.path") {
        if !value.is_null() && value.as_str()!=Some("") && (old["server"].is_null() || value!=&old["server"]) {
            println!("Existing custom ols.server.path preserved; OLS uses that server.");return Ok(());
        }
    }
    let server=prepare(root,prefix)?;let server=server.to_str().ok_or("non-UTF-8 OLS server path")?;
    let edited=doc.set_string("ols.server.path",server)?;
    if edited!=text {
        let current=match fs::read_to_string(settings) {Ok(t)=>t,Err(e) if e.kind()==std::io::ErrorKind::NotFound=>"{}\n".into(),Err(e)=>return Err(err(e))};
        if current!=text {return Err("VS Code settings changed during the OLS build; preserved. Rerun --vscode to finish setup using the cached build.".into());}
        if settings.exists() {
            if fs::symlink_metadata(settings).map_err(err)?.file_type().is_symlink() {return Err("OLS settings file is a symlink; preserved. Configure ols.server.path through VS Code.".into());}
            let backup=settings.with_file_name(format!("settings.json.thor-ols-backup-{}",unique()));fs::copy(settings,&backup).map_err(err)?;
            lifecycle::track_artifact(prefix,&backup)?;
        }
        let before=if settings.exists(){Some(text.clone())}else{None};
        atomic_write(settings,edited.as_bytes())?;
        lifecycle::track_file(prefix,settings,before,&edited)?;
    }
    atomic_write(&state_path,&serde_json::to_vec_pretty(&json!({"server":server,"settings":settings})).map_err(err)?)?;
    println!("Configured native OLS for {} (other settings preserved).",settings.display());Ok(())
}
