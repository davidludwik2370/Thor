//! PATH setup for login shells and fresh interactive terminal windows.
use super::*;

pub(super) fn managed_blocks(bin:&Path)->[String;2] {
    let quoted=shell_quote(bin);
    [format!("# >>> thor-install PATH >>>\ncase \":${{PATH-}}:\" in\n    *:{quoted}:*) ;;\n    *) export PATH={quoted}${{PATH:+\":$PATH\"}} ;;\nesac\n# <<< thor-install PATH <<<"),
     format!("# >>> thor-install PATH >>>\nexport PATH={quoted}:\"$PATH\"\n# <<< thor-install PATH <<<")]
}
fn zsh_home()->Result<PathBuf> {
    env::var_os("ZDOTDIR").filter(|s|!s.is_empty()).map(|p|absolute(Path::new(&p))).unwrap_or_else(home)
}
pub(super) fn legacy_candidates()->Result<Vec<PathBuf>> {
    let base=home()?;let mut files=vec![];
    for name in [".profile",".bash_profile",".bash_login",".bashrc",".zprofile",".zshrc"] {files.push(base.join(name));}
    for name in [".zprofile",".zshrc"] {let file=zsh_home()?.join(name);if !files.contains(&file){files.push(file);}}
    Ok(files)
}
fn startup_files(shell:&str)->Result<Vec<PathBuf>> {
    let base=home()?;
    if shell.ends_with("zsh") {let base=zsh_home()?;return Ok(vec![base.join(".zprofile"),base.join(".zshrc")]);}
    if shell.ends_with("bash") {
        // Bash reads the first existing login file, whereas desktop terminals
        // normally start a non-login interactive shell and read only .bashrc.
        let login=[".bash_profile",".bash_login",".profile"].into_iter().map(|n|base.join(n)).find(|p|p.exists()).unwrap_or_else(||base.join(".profile"));
        return Ok(vec![login,base.join(".bashrc")]);
    }
    Ok(vec![base.join(".profile")])
}
pub(super) fn add(bin:&Path)->Result<()> {
    let shell=env::var("SHELL").unwrap_or_default();
    if shell.ends_with("fish") {return Err(format!("PATH was not edited. In fish, run: fish_add_path {}",shell_quote(bin)));}
    let [block,legacy]=managed_blocks(bin);let mut edits=vec![];
    // Validate every destination before changing any of the startup files.
    for file in startup_files(&shell)? {
        let existed=file.exists();
        let text=match fs::read_to_string(&file){Ok(s)=>s,Err(e) if e.kind()==std::io::ErrorKind::NotFound=>String::new(),Err(e)=>return Err(err(e))};
        let after=if text.contains("# >>> thor-install PATH >>>") {
            if text.matches(&block).count()==1 {continue;}
            if text.matches(&legacy).count()!=1 {return Err(format!("existing managed PATH block preserved in {}; update it manually for this prefix",file.display()));}
            text.replacen(&legacy,&block,1)
        } else {format!("{text}\n{block}\n")};
        edits.push((file,existed,text,after));
    }
    let prefix=PathBuf::from(field(&registered()?,"prefix")?);
    for (file,existed,before,after) in edits {
        if existed {
            let backup=file.with_file_name(format!("{}.thor-backup-{}",file.file_name().unwrap().to_string_lossy(),unique()));
            fs::copy(&file,&backup).map_err(err)?;lifecycle::track_artifact(&prefix,&backup)?;
        }
        fs::write(&file,&after).map_err(err)?;
        lifecycle::track_file(&prefix,&file,if existed{Some(before)}else{None},&after)?;
        println!("PATH configured in {} (existing content retained).",file.display());
    }
    Ok(())
}
