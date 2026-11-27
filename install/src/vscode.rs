//! Use VS Code's CLI to create profiles/install extensions. Never edit its profile
//! registry or unrelated settings/keybindings. Native OLS adds its own setting.
use super::*;

const MINIMUM: &str = include_str!("../vscode-minimum");
fn code_command(code:&Path)->Command {
    let mut command=Command::new(code);
    // --vscode explicitly requests the native Linux editor. Its launcher must
    // not stop automated setup at the WSL suggestion to use Windows Code.
    if cfg!(target_os="linux") {command.env("DONT_PROMPT_WSL_INSTALL","1");}
    command
}
fn version_numbers(text:&str)->Option<[u32;3]> {
    let line=text.lines().find(|line|!line.trim().is_empty())?.trim();
    let mut parts=line.split('-').next()?.split('.');
    let result=[parts.next()?.parse().ok()?,parts.next()?.parse().ok()?,parts.next()?.parse().ok()?];
    if parts.next().is_some(){return None;}Some(result)
}
fn check_version(code:&Path)->Result<()> {
    let output=code_command(code).arg("--version").output().map_err(err)?;
    let version=String::from_utf8_lossy(&output.stdout);
    if !output.status.success(){return Err(format!("VS Code cannot report its version: {}",code.display()));}
    let found=version_numbers(&version).ok_or_else(||format!("VS Code returned an unrecognized version: {}",code.display()))?;
    let minimum=version_numbers(MINIMUM).ok_or("invalid bundled VS Code minimum")?;
    if found<minimum {
        return Err(format!("VS Code {}.{}.{} at {} is too old. Thor editor support requires VS Code {} or newer. Upgrade VS Code and run thor-install --vscode again; the installed compiler can be reused.",found[0],found[1],found[2],code.display(),MINIMUM.trim()));
    }
    Ok(())
}

#[cfg(test)]
mod version_tests {
    use super::*;
    #[test] fn native_linux_editor_requests_do_not_prompt_for_a_different_editor() {
        let command=code_command(Path::new("code"));
        let value=command.get_envs().find(|(key,_)|*key=="DONT_PROMPT_WSL_INSTALL").and_then(|(_,value)|value);
        assert_eq!(value,if cfg!(target_os="linux"){Some(std::ffi::OsStr::new("1"))}else{None});
    }
    #[test] fn cli_versions_use_numeric_components_and_reject_unexpected_output() {
        let minimum=version_numbers(MINIMUM).unwrap();
        let old=version_numbers("1.66.0\ncommit\narm64\n").unwrap();
        let current=version_numbers("1.147.0\ncommit\narm64\n").unwrap();
        let insiders=version_numbers("1.100.0-insider\ncommit\n").unwrap();
        let malformed=version_numbers("Warning: unknown option\n1.147.0\n");
        assert!(old<minimum);
        assert!(current>=minimum);
        assert!(insiders>=minimum);
        assert_eq!(malformed,None);
    }
}

pub(super) fn find_code(o: &Options) -> Result<PathBuf> {
    if let Some(path) = &o.code {let path=absolute(path)?;check_version(&path)?;return Ok(path);}
    let mut candidates=vec![];
    for directory in env::split_paths(&env::var_os("PATH").unwrap_or_default()) {
        for name in if cfg!(windows) { vec!["code.cmd","code.exe"] } else { vec!["code"] } {
            candidates.push(directory.join(name));
        }
    }
    let paths = if cfg!(target_os="macos") {
        vec![PathBuf::from("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),home()?.join("Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")]
    } else if cfg!(windows) {
        vec![home()?.join("AppData/Local/Programs/Microsoft VS Code/bin/code.cmd"),
            env::var_os("ProgramFiles").map(PathBuf::from).unwrap_or_else(||PathBuf::from("C:/Program Files")).join("Microsoft VS Code/bin/code.cmd")]
    } else { vec![PathBuf::from("/usr/share/code/bin/code")] };
    candidates.extend(paths);
    let mut failure=None;let mut seen=BTreeSet::new();
    for path in candidates {
        if !path.is_file() || !seen.insert(path.clone()){continue;}
        match check_version(&path){Ok(())=>return Ok(path),Err(error)=>if failure.is_none(){failure=Some(error);}}
    }
    Err(failure.unwrap_or_else(||"VS Code was not found. Install it from https://code.visualstudio.com/, or specify --code PATH. Then rerun --vscode; an existing Thor installation is reusable.".into()))
}
fn data_dir(o: &Options) -> Result<PathBuf> {
    if let Some(p) = &o.code_data { return absolute(p); }
    Ok(if cfg!(target_os="macos") { home()?.join("Library/Application Support/Code") }
    else if cfg!(windows) { env::var_os("APPDATA").map(PathBuf::from).unwrap_or(home()?.join("AppData/Roaming")).join("Code") }
    else { env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).unwrap_or(home()?.join(".config")).join("Code") })
}
pub(super) fn cli(code: &Path,o: &Options,bin: &Path,root:&Path) -> Result<Command> {
    let mut cmd=code_command(code);
    // Writing a shell profile does not update this process's environment. The
    // first profile window may be launched here, before the user opens Code.
    let mut paths=vec![bin.to_path_buf()];
    paths.extend(env::split_paths(&env::var_os("PATH").unwrap_or_default()).filter(|p|p!=bin));
    cmd.env("PATH",env::join_paths(paths).map_err(err)?);
    if env::var_os("ODIN").is_none_or(|s|s.is_empty()) {
        cmd.env("ODIN",root.join(format!("odin/odin{}",env::consts::EXE_SUFFIX)));
    }
    if let Some(data)=&o.code_data {cmd.arg("--user-data-dir").arg(data);}
    if let Some(extensions)=&o.extensions {cmd.arg("--extensions-dir").arg(extensions);}
    Ok(cmd)
}
fn profile_directory(data: &Path, requested: &str) -> Result<Option<PathBuf>> {
    let path=data.join("User/globalStorage/storage.json");
    let value=match read_json(&path) {Ok(v)=>v,Err(_) if !path.exists()=>return Ok(None),Err(e)=>return Err(e)};
    let entries=match &value["userDataProfiles"] {
        Value::Null=>return Ok(None),Value::String(s)=>serde_json::from_str::<Value>(s).map_err(err)?,v=>v.clone()
    };
    let list=entries.as_array().ok_or("VS Code profile registry has an unknown format; it was left unchanged")?;
    let mut result=None;
    for item in list {
        // Built-in and future profiles can use a different location format.
        // They are unrelated to the requested profile and must remain untouched.
        if item["name"].as_str()!=Some(requested) {continue;}
        if result.is_some() {return Err("duplicate requested VS Code profile; settings were left unchanged".into());}
        let location=field(item,"location")?;
        // VS Code stores an ID relative to User/profiles. Do not follow paths or
        // URLs supplied by an unsupported future registry format.
        if !component(location) {return Err("unsupported VS Code profile location; settings were left unchanged".into());}
        result=Some(data.join("User/profiles").join(location));
    }
    Ok(result)
}
fn configure_profile(prefix:&Path,directory:&Path,template:&Value,colors:Option<bool>)->Result<()> {
    let file=directory.join("settings.json");
    let before=match fs::read_to_string(&file) {Ok(text)=>Some(text),Err(e) if e.kind()==std::io::ErrorKind::NotFound=>None,Err(e)=>return Err(err(e))};
    let mut defaults:Value=serde_json::from_str(field(template,"settings")?).map_err(err)?;
    defaults["thor.colors.recommended"]=json!(colors.unwrap_or(true));
    let mut text=before.clone().unwrap_or_else(||"{}\n".into());
    // A retained profile registration is not evidence that its defaults remain:
    // uninstall restores installer-owned settings but deliberately keeps the
    // profile. Reapply missing defaults, retaining every explicit user value.
    for (key,value) in defaults.as_object().ok_or("invalid profile defaults")? {
        text=jsonc::Document::parse(text)?.set_missing(key,value)?;
    }
    if before.as_deref()==Some(&text) {return Ok(());}
    fs::create_dir_all(directory).map_err(err)?;
    if before.is_some() {
        if fs::symlink_metadata(&file).map_err(err)?.file_type().is_symlink() {return Err("Profile settings are a symlink; preserved. Configure the profile through VS Code.".into());}
        if fs::read_to_string(&file).map_err(err)?!=before.as_deref().unwrap() {return Err("VS Code profile settings changed during setup; preserved. Rerun --vscode to finish.".into());}
        atomic_write(&file,text.as_bytes())?;
    } else {
        let mut output=fs::OpenOptions::new().create_new(true).write(true).open(&file)
            .map_err(|e|format!("Could not initialize profile settings; existing files were preserved. Rerun --vscode to finish: {e}"))?;
        output.write_all(text.as_bytes()).map_err(err)?;
    }
    lifecycle::track_file(prefix,&file,before,&text)?;
    Ok(())
}
pub(super) fn setup(o: &Options,code: &Path,root: &Path) -> Result<()> {
    let data=data_dir(o)?;
    let record=registered()?;
    let bin=PathBuf::from(field(&record,"bin")?);
    let prefix=PathBuf::from(field(&record,"prefix")?);
    if let Some(name)=&o.profile {
        let existing=profile_directory(&data,name)?;
        if existing.is_none() {
            println!("Creating profile {name}; VS Code may open a new window. Waiting up to two minutes for its first launch.");
            let mut cmd=cli(code,o,&bin,root)?;cmd.args(["--profile",name,"--new-window"]); command(cmd)?;
            let mut directory=None;
            // Cold editor startup on an HDD can take longer than ten seconds.
            // Polling returns immediately once VS Code registers the profile.
            for _ in 0..1200 {
                directory=profile_directory(&data,name)?;
                if directory.is_some(){break;}
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            directory.ok_or("VS Code did not register the requested profile within two minutes. Check that its window starts in your logged-in desktop session, then rerun the installer. Headless Linux validation needs a graphical session such as Xvfb.")?;
        }
    }
    let mut state=lifecycle::state(&prefix)?;
    let mut listing=cli(code,o,&bin,root)?;
    if let Some(profile)=&o.profile {listing.args(["--profile",profile]);}
    listing.arg("--list-extensions");
    let output=listing.output().map_err(err)?;
    if !output.status.success(){return Err(format!("could not inventory existing editor extensions: {}",String::from_utf8_lossy(&output.stderr)));}
    let existing_extensions=String::from_utf8_lossy(&output.stdout).lines().map(|s|s.to_ascii_lowercase()).collect::<BTreeSet<_>>();
    let identity=json!({"code":code,"profile":o.profile,"data":o.code_data,"extensions":o.extensions});
    let entries=state["editors"].as_array_mut().ok_or("invalid editor ownership inventory")?;
    let index=if let Some(i)=entries.iter().position(|v|v["identity"]==identity){i}else{entries.push(json!({"identity":identity,"owned":[]}));entries.len()-1};
    for id in ["thor-local.thor-language","danielgavin.ols","vadimcn.vscode-lldb"] {
        if !existing_extensions.contains(id) && !entries[index]["owned"].as_array().unwrap().iter().any(|v|v==id) {
            entries[index]["owned"].as_array_mut().unwrap().push(json!(id));
        }
    }
    lifecycle::save(&prefix,&state)?;
    {
        let directory=if let Some(name)=&o.profile {
            profile_directory(&data,name)?.ok_or("VS Code profile not found")?
        } else {data.join("User")};
        ols::configure(root,Path::new(field(&registered()?,"prefix")?),&directory.join("settings.json"))?;
        if let Some(name)=&o.profile {
            let template=read_json(&root.join("install/profiles/Thor.code-profile"))?;
            configure_profile(&prefix,&directory,&template,o.colors)?;
            println!("Profile {name}: missing Thor defaults initialized; explicit settings and keybindings preserved.");
        }
    }
    for extension in [root.join("editors/thor-language.vsix").to_string_lossy().into_owned(),"DanielGavin.ols".into(),"vadimcn.vscode-lldb".into()] {
        let mut cmd=cli(code,o,&bin,root)?;
        if let Some(profile)=&o.profile {cmd.args(["--profile",profile]);}
        cmd.arg("--install-extension").arg(&extension);
        // The bundled Thor VSIX is the tested payload even when a previous
        // installation has the same extension version. Do not force updates
        // of independently installed OLS/CodeLLDB marketplace extensions.
        if extension.ends_with(".vsix"){cmd.arg("--force");}
        command(cmd).map_err(|e|format!("Thor CLI is installed, but editor setup is incomplete. Correct the error and rerun the same installer command. {e}"))?;
    }
    println!("Thor, the standard Odin Language Server (OLS), and CodeLLDB extensions installed. Reload the profile's window to activate them, then open a new terminal for Thor's PATH. OLS owns .odin files; TLS owns .thor files. Shortcuts apply only in Thor editors.");
    if let Some(profile)=&o.profile {
        println!("Open your project in VS Code profile {profile:?}; the extensions were installed in that profile only. Restarting a window in another profile will not switch it. A .thor file should show Thor, rather than Plain Text, in the status bar.");
    }
    Ok(())
}

pub(super) fn editor_options(identity:&Value)->Options {
    Options{profile:identity["profile"].as_str().map(str::to_owned),
        code_data:identity["data"].as_str().map(PathBuf::from),extensions:identity["extensions"].as_str().map(PathBuf::from),..Options::default()}
}
pub(super) fn uninstall(state:&Value,record:&Value,dry:bool)->Result<()> {
    if let Some(editors)=state["editors"].as_array() {for editor in editors {
        let identity=&editor["identity"];let o=editor_options(identity);
        let code=Path::new(field(identity,"code")?);
        let installed=if dry {None}else{
            if !code.is_file(){println!("RETAINED: editor extensions (VS Code launcher is missing); remove them through VS Code when available");continue;}
            if let Err(error)=check_version(code){println!("RETAINED: editor extensions ({error}); remove through a compatible VS Code version");continue;}
            let mut cmd=cli(code,&o,Path::new(field(record,"bin")?),Path::new(field(record,"root")?))?;
            if let Some(profile)=&o.profile {cmd.args(["--profile",profile]);}
            cmd.arg("--list-extensions");let result=cmd.output().map_err(err)?;
            if !result.status.success(){println!("RETAINED: editor extensions (profile is unavailable); remove through VS Code if still present");continue;}
            Some(String::from_utf8_lossy(&result.stdout).lines().map(|s|s.to_ascii_lowercase()).collect::<BTreeSet<_>>())
        };
        for id in editor["owned"].as_array().ok_or("invalid owned extensions")? {
            if installed.as_ref().is_some_and(|set|!set.contains(id.as_str().unwrap_or(""))) {continue;}
            println!("Remove installer-added extension {} from {}",id,o.profile.as_deref().unwrap_or("Default"));
            if dry {continue;}
            let mut cmd=cli(code,&o,Path::new(field(record,"bin")?),Path::new(field(record,"root")?))?;
            if let Some(profile)=&o.profile {cmd.args(["--profile",profile]);}
            cmd.args(["--uninstall-extension",id.as_str().ok_or("invalid extension ID")?]);command(cmd)?;
        }
        if o.profile.is_some(){println!("RETAINED: VS Code profile registration (delete through VS Code if desired). Installer-owned settings are restored.");}
    }}Ok(())
}
pub(super) fn verify_editors(record:&Value)->Result<()> {
    let prefix=Path::new(field(record,"prefix")?);let state=lifecycle::state(prefix)?;
    for editor in state["editors"].as_array().ok_or("invalid editor inventory")? {
        let identity=&editor["identity"];let o=editor_options(identity);
        let code=Path::new(field(identity,"code")?);
        check_version(code)?;
        let mut cmd=cli(code,&o,Path::new(field(record,"bin")?),Path::new(field(record,"root")?))?;
        if let Some(profile)=&o.profile {cmd.args(["--profile",profile]);}
        cmd.arg("--list-extensions");let output=cmd.output().map_err(err)?;
        if !output.status.success(){return Err("VS Code extension verification failed".into());}
        let installed=String::from_utf8_lossy(&output.stdout).lines().map(|s|s.to_ascii_lowercase()).collect::<BTreeSet<_>>();
        for id in ["thor-local.thor-language","danielgavin.ols","vadimcn.vscode-lldb"] {
            if !installed.contains(id){return Err(format!("VS Code reports a missing extension: {id}"));}
        }
        println!("PASS: VS Code profile and Thor/OLS/CodeLLDB extension inventory");
        let mut locate=cli(code,&o,Path::new(field(record,"bin")?),Path::new(field(record,"root")?))?;
        if let Some(profile)=&o.profile {locate.args(["--profile",profile]);}
        locate.args(["--locate-extension","thor-local.thor-language"]);
        let output=locate.output().map_err(err)?;
        if !output.status.success(){return Err("could not locate the installed Thor extension".into());}
        let directory=if let Some(name)=&o.profile {profile_directory(&data_dir(&o)?,name)?.ok_or("VS Code profile disappeared")?}else{data_dir(&o)?.join("User")};
        let located=String::from_utf8_lossy(&output.stdout).trim().to_owned();
        // Some VS Code versions locate extensions only in the default profile,
        // even when --profile was supplied. Read the selected profile's own
        // inventory when that command succeeds without returning a location.
        let extension=if !located.is_empty(){PathBuf::from(located)}else{
            let inventory=read_json(&directory.join("extensions.json"))?;
            let entry=inventory.as_array().and_then(|entries|entries.iter().find(|entry|
                entry["identifier"]["id"].as_str().is_some_and(|id|id.eq_ignore_ascii_case("thor-local.thor-language"))))
                .ok_or("could not locate Thor in the selected VS Code profile")?;
            if entry["location"]["scheme"]!="file" {return Err("unsupported editor extension location".into());}
            let path=field(&entry["location"],"path")?;
            let path=if cfg!(windows)&&path.starts_with('/')&&path.as_bytes().get(2)==Some(&b':'){&path[1..]}else{path};
            PathBuf::from(path)
        };
        verify_node_package(&extension,&extension,&mut BTreeSet::new())?;
        println!("PASS: installed Thor extension entry point and JavaScript runtime dependencies");
        if let Ok(text)=fs::read_to_string(directory.join("settings.json")) {
            let doc=jsonc::Document::parse(text)?;
            if let Some(server)=doc.value["ols.server.path"].as_str().filter(|s|!s.is_empty()) {
                if Path::new(server).starts_with(prefix) {verify::lsp(Path::new(server),Path::new(field(record,"root")?),false)?;}
                else{println!("SKIP: custom OLS server is user-managed");}
            }else{println!("SKIP: OLS server uses extension-managed download; verify first-use setup in VS Code");}
        }
    }Ok(())
}

fn verify_node_package(directory:&Path,root:&Path,seen:&mut BTreeSet<PathBuf>)->Result<()> {
    if !seen.insert(directory.to_path_buf()){return Ok(());}
    if seen.len()>128 {return Err("editor dependency graph exceeds verification limit".into());}
    let package=read_json(&directory.join("package.json")).map_err(|e|format!("editor runtime dependency metadata missing at {}: {e}",directory.display()))?;
    let main=package["main"].as_str().unwrap_or("index.js");let main=main.strip_prefix("./").unwrap_or(main);
    if !safe_path(Path::new(main)) || !directory.join(main).is_file(){return Err(format!("editor runtime entry point missing: {}",directory.join(main).display()));}
    if let Some(dependencies)=package["dependencies"].as_object(){for name in dependencies.keys() {
        if !safe_path(Path::new(name)){return Err("unsafe editor dependency name".into());}
        let mut current=Some(directory);let mut found=None;
        while let Some(parent)=current {
            let path=parent.join("node_modules").join(name);
            if path.join("package.json").is_file(){found=Some(path);break;}
            if parent==root{break;}current=parent.parent();
        }
        let dependency=found.ok_or_else(||format!("editor runtime dependency missing: {name}"))?;
        verify_node_package(&dependency,root,seen)?;
    }}Ok(())
}

#[cfg(test)]
mod profile_tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize,Ordering};
    static NEXT: AtomicUsize=AtomicUsize::new(0);
    struct Fixture(PathBuf);
    impl Fixture {
        fn new(entries:Value)->Self {
            let root=env::temp_dir().join(format!("thor-profile-registry-{}-{}",std::process::id(),NEXT.fetch_add(1,Ordering::Relaxed)));
            fs::create_dir_all(root.join("User/globalStorage")).unwrap();
            let bytes=serde_json::to_vec(&json!({"userDataProfiles":entries})).unwrap();
            fs::write(root.join("User/globalStorage/storage.json"),bytes).unwrap();
            Self(root)
        }
        fn bytes(&self)->Vec<u8> {fs::read(self.0.join("User/globalStorage/storage.json")).unwrap()}
    }
    impl Drop for Fixture {fn drop(&mut self){let _=fs::remove_dir_all(&self.0);}}
    #[test] fn unrelated_builtin_and_future_profiles_are_untouched() {
        let entries=json!([{"name":"Agents","location":"builtin/agents"},{"name":"Other","location":"../../outside"},{"future":true},{"name":"Thor","location":"valid-id"}]);
        for value in [entries.clone(),json!(entries.to_string())] {
            let fixture=Fixture::new(value);let before=fixture.bytes();
            let found=profile_directory(&fixture.0,"Thor").unwrap();
            let after=fixture.bytes();
            assert_eq!(found,Some(fixture.0.join("User/profiles/valid-id")));
            assert_eq!(before,after);
        }
    }
    #[test] fn unrelated_profiles_do_not_prevent_creating_a_named_profile() {
        let fixture=Fixture::new(json!([{"name":"Agents","location":"builtin/agents"}]));
        let found=profile_directory(&fixture.0,"Thor").unwrap();
        assert_eq!(found,None);
    }
    #[test] fn requested_unsupported_locations_still_fail_without_writes() {
        for location in ["../escape","/outside","C:\\outside","file:///outside","builtin/agents",".",".."] {
            let fixture=Fixture::new(json!([{"name":"Thor","location":location}]));let before=fixture.bytes();
            let result=profile_directory(&fixture.0,"Thor");let after=fixture.bytes();
            assert!(result.is_err());assert_eq!(before,after);
        }
    }
    #[test] fn duplicate_requested_profile_is_rejected_without_writes() {
        let fixture=Fixture::new(json!([{"name":"Thor","location":"one"},{"name":"Thor","location":"two"}]));let before=fixture.bytes();
        let result=profile_directory(&fixture.0,"Thor");let after=fixture.bytes();
        assert!(result.is_err());assert_eq!(before,after);
    }
    #[test] fn unsupported_registry_shape_remains_an_error() {
        let fixture=Fixture::new(json!({"future":"format"}));
        let result=profile_directory(&fixture.0,"Thor");
        assert!(result.is_err());
    }
}
