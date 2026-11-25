//! Use VS Code's CLI to create profiles/install extensions. Never edit its profile
//! registry or unrelated settings/keybindings. Native OLS adds its own setting.
use super::*;

pub(super) fn find_code(o: &Options) -> Result<PathBuf> {
    if let Some(path) = &o.code { return absolute(path); }
    for directory in env::split_paths(&env::var_os("PATH").unwrap_or_default()) {
        for name in if cfg!(windows) { vec!["code.cmd","code.exe"] } else { vec!["code"] } {
            let path = directory.join(name); if path.is_file() { return Ok(path); }
        }
    }
    let paths = if cfg!(target_os="macos") {
        vec![PathBuf::from("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),home()?.join("Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")]
    } else if cfg!(windows) {
        vec![home()?.join("AppData/Local/Programs/Microsoft VS Code/bin/code.cmd")]
    } else { vec![PathBuf::from("/usr/share/code/bin/code")] };
    paths.into_iter().find(|p| p.is_file()).ok_or_else(|| "VS Code was not found. Install it from https://code.visualstudio.com/, or specify --code PATH. Then rerun --vscode; an existing Thor installation is reusable.".into())
}
fn data_dir(o: &Options) -> Result<PathBuf> {
    if let Some(p) = &o.code_data { return absolute(p); }
    Ok(if cfg!(target_os="macos") { home()?.join("Library/Application Support/Code") }
    else if cfg!(windows) { env::var_os("APPDATA").map(PathBuf::from).unwrap_or(home()?.join("AppData/Roaming")).join("Code") }
    else { env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).unwrap_or(home()?.join(".config")).join("Code") })
}
fn cli(code: &Path,o: &Options,bin: &Path,root:&Path) -> Result<Command> {
    let mut cmd=Command::new(code);
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
fn profiles(data: &Path) -> Result<Vec<(String,PathBuf)>> {
    let path=data.join("User/globalStorage/storage.json");
    let value=match read_json(&path) {Ok(v)=>v,Err(_) if !path.exists()=>return Ok(vec![]),Err(e)=>return Err(e)};
    let entries=match &value["userDataProfiles"] {
        Value::Null=>return Ok(vec![]),Value::String(s)=>serde_json::from_str::<Value>(s).map_err(err)?,v=>v.clone()
    };
    let list=entries.as_array().ok_or("VS Code profile registry has an unknown format; it was left unchanged")?;
    let mut result=vec![];
    for item in list {
        let name=field(item,"name")?;
        let location=field(item,"location")?;
        // VS Code stores an ID relative to User/profiles. Do not follow paths or
        // URLs supplied by an unsupported future registry format.
        if !component(location) {return Err("unsupported VS Code profile location; settings were left unchanged".into());}
        result.push((name.to_owned(),data.join("User/profiles").join(location)));
    }
    Ok(result)
}
pub(super) fn setup(o: &Options,code: &Path,root: &Path) -> Result<()> {
    let data=data_dir(o)?;
    let bin=PathBuf::from(field(&registered()?,"bin")?);
    if let Some(name)=&o.profile {
        let existing=profiles(&data)?.into_iter().find(|(n,_)|n==name);
        if existing.is_none() {
            println!("Creating profile {name}; VS Code may open a new window.");
            let mut cmd=cli(code,o,&bin,root)?;cmd.args(["--profile",name,"--new-window"]); command(cmd)?;
            let mut directory=None;
            for _ in 0..100 {
                directory=profiles(&data)?.into_iter().find(|(n,_)|n==name).map(|(_,p)|p);
                if directory.is_some(){break;}
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            let directory=directory.ok_or("VS Code did not create the requested profile. On headless Ubuntu use a graphical session (Xvfb is suitable for validation); then rerun the installer.")?;
            let template=read_json(&root.join("install/profiles/Thor.code-profile"))?;
            let mut settings:Value=serde_json::from_str(field(&template,"settings")?).map_err(err)?;
            settings["thor.colors.recommended"]=json!(o.colors.unwrap_or(true));
            fs::create_dir_all(&directory).map_err(err)?;
            let file=directory.join("settings.json");
            // A setting written during profile creation wins, even on first run.
            // create_new makes this safe against concurrent edits by the editor.
            match fs::OpenOptions::new().create_new(true).write(true).open(&file) {
                Ok(mut f)=>f.write_all(&serde_json::to_vec_pretty(&settings).map_err(err)?).map_err(err)?,
                Err(e) if e.kind()==std::io::ErrorKind::AlreadyExists=>println!("Profile settings already exist and were preserved. Enable Thor colors with the extension command if desired."),
                Err(e)=>return Err(err(e)),
            }
        } else { println!("Existing profile {name}: settings and keybindings preserved."); }
    }
    if cfg!(all(target_os="linux",target_arch="aarch64")) {
        let directory=if let Some(name)=&o.profile {
            profiles(&data)?.into_iter().find(|(n,_)|n==name).map(|(_,p)|p).ok_or("VS Code profile not found")?
        } else {data.join("User")};
        ols::configure(root,Path::new(field(&registered()?,"prefix")?),&directory.join("settings.json"))?;
    }
    for extension in [root.join("editors/thor-language.vsix").to_string_lossy().into_owned(),"DanielGavin.ols".into(),"vadimcn.vscode-lldb".into()] {
        let mut cmd=cli(code,o,&bin,root)?;
        if let Some(profile)=&o.profile {cmd.args(["--profile",profile]);}
        cmd.arg("--install-extension").arg(extension);
        command(cmd).map_err(|e|format!("Thor CLI is installed, but editor setup is incomplete. Correct the error and rerun the same installer command. {e}"))?;
    }
    println!("Thor, the standard Odin Language Server (OLS), and CodeLLDB extensions installed. Reload the profile's window to activate them, then open a new terminal for Thor's PATH. OLS owns .odin files; TLS owns .thor files. Shortcuts apply only in Thor editors.");
    if !cfg!(all(target_os="linux",target_arch="aarch64")) {println!("On first use, OLS may ask to download its server and create an ols.json configuration.");}
    Ok(())
}
