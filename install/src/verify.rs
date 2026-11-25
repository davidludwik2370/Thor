//! Bounded checks of the installed artifacts, not the development checkout.
use super::*;
use std::io::{BufRead,BufReader};
use std::process::{Child,Stdio};
use std::sync::mpsc;
use std::time::{Duration,Instant};

struct Server(Child);
impl Drop for Server {fn drop(&mut self){
    #[cfg(unix)] {
        extern "C" {fn kill(pid:i32,signal:i32)->i32;}
        // spawn creates a dedicated process group. The unreaped child reserves
        // this PID, so only this probe and its descendants can belong to it.
        // Kill descendants too: an inherited stdout pipe must not defeat the
        // LSP deadline by keeping the reader thread alive after parent exit.
        unsafe {kill(-(self.0.id() as i32),9);}
    }
    #[cfg(windows)] {
        let _=Command::new("taskkill.exe").args(["/PID",&self.0.id().to_string(),"/T","/F"])
            .stdout(Stdio::null()).stderr(Stdio::null()).status();
    }
    let _=self.0.kill();let _=self.0.wait();
}}
fn uri(path:&Path)->String {
    let path=path.to_string_lossy().replace('\\',"/");
    let encoded=path.bytes().map(|b|if b.is_ascii_alphanumeric()||b"/-_.:~".contains(&b){(b as char).to_string()}else{format!("%{b:02X}")}).collect::<String>();
    if encoded.starts_with("//"){return format!("file:{encoded}");}
    format!("file://{}{}",if encoded.starts_with('/') {""}else{"/"},encoded)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn file_uris_cover_spaces_unicode_windows_drives_and_unc() {
        assert_eq!(uri(Path::new("/tmp/café project/main.thor")),"file:///tmp/caf%C3%A9%20project/main.thor");
        assert_eq!(uri(Path::new(r"C:\Thor tools\main.thor")),"file:///C:/Thor%20tools/main.thor");
        assert_eq!(uri(Path::new(r"\\server\share\main.thor")),"file://server/share/main.thor");
    }
}
fn send(writer:&mut impl Write,message:&Value)->Result<()> {
    let data=serde_json::to_vec(message).map_err(err)?;
    write!(writer,"Content-Length: {}\r\n\r\n",data.len()).map_err(err)?;
    writer.write_all(&data).map_err(err)?;writer.flush().map_err(err)
}
fn request(writer:&mut impl Write,receiver:&mpsc::Receiver<Result<Value>>,id:u64,method:&str,params:Value)->Result<Value> {
    send(writer,&json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}))?;
    let deadline=Instant::now()+Duration::from_secs(15);
    loop {
        let value=receiver.recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .map_err(|_|format!("language-server verification timed out: {method}"))??;
        if value.get("method").is_some() && value.get("id").is_some() {
            send(writer,&json!({"jsonrpc":"2.0","id":value["id"],"result":null}))?;
        }else if value["id"]==id {
            if !value["error"].is_null(){return Err(format!("language server rejected {method}: {}",value["error"]));}
            return Ok(value["result"].clone());
        }
    }
}
pub(super) fn lsp(binary:&Path,root:&Path,thor:bool)->Result<()> {
    let temp=env::temp_dir().join(format!("thor-language-check-{}",unique()));fs::create_dir(&temp).map_err(err)?;let _guard=RemoveDir(temp.clone());
    let source="package main\nRecord :: struct { number: int }\nmain :: proc() { record := Record{42}; _ = record.number }\n";
    let file=temp.join(if thor{"main.thor"}else{"main.odin"});fs::write(&file,source).map_err(err)?;
    let mut cmd=Command::new(binary);
    #[cfg(unix)] {use std::os::unix::process::CommandExt;cmd.process_group(0);}
    cmd.current_dir(&temp).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .env("OLS_BUILTIN_FOLDER",if thor{root.join("tls/builtin")}else{binary.parent().unwrap_or(root).join("builtin")});
    let mut child=Server(cmd.spawn().map_err(err)?);
    let mut input=child.0.stdin.take().ok_or("missing LSP stdin")?;
    let output=child.0.stdout.take().ok_or("missing LSP stdout")?;
    let (tx,rx)=mpsc::channel();
    let reader=std::thread::spawn(move||{
        let mut reader=BufReader::new(output);
        let result=(||->Result<()>{loop {
            let mut size=None;
            loop {let mut line=String::new();if reader.read_line(&mut line).map_err(err)?==0{return Err("language server closed its output".into());}
                if line.trim().is_empty(){break;}
                if let Some((name,value))=line.split_once(':'){if name.eq_ignore_ascii_case("content-length"){size=value.trim().parse::<usize>().ok();}}
            }
            let size=size.filter(|n|*n<=8*1024*1024).ok_or("invalid language-server message length")?;
            let mut bytes=vec![0;size];reader.read_exact(&mut bytes).map_err(err)?;
            let message=serde_json::from_slice(&bytes).map_err(err)?;
            if tx.send(Ok(message)).is_err(){return Ok(());}
        }})();if let Err(error)=result{let _=tx.send(Err(error));}
    });
    let result=(||->Result<()>{
        let params=json!({"processId":std::process::id(),"rootUri":uri(&temp),
            "workspaceFolders":[{"uri":uri(&temp),"name":"Thor installation check"}],"capabilities":{},
            "initializationOptions":{"odin_root_override":root.join("odin"),"odin_command":root.join(format!("odin/odin{}",env::consts::EXE_SUFFIX)),
                "thor_command":if thor{root.join(format!("thor{}",env::consts::EXE_SUFFIX)).to_string_lossy().into_owned()}else{String::new()},
                "enable_checker_workspace_diagnostics":false,"enable_auto_import":false,"thread_count":1,
                "collections":[{"name":"thor","path":root.join("runtime")}]}});
        let initialized=request(&mut input,&rx,1,"initialize",params)?;
        if initialized["capabilities"].is_null(){return Err("language server returned no capabilities".into());}
        send(&mut input,&json!({"jsonrpc":"2.0","method":"initialized","params":{}}))?;
        send(&mut input,&json!({"jsonrpc":"2.0","method":"textDocument/didOpen","params":{"textDocument":{"uri":uri(&file),"languageId":if thor{"thor"}else{"odin"},"version":1,"text":source}}}))?;
        let line=source.lines().nth(2).unwrap_or("");
        let hover=request(&mut input,&rx,2,"textDocument/hover",json!({"textDocument":{"uri":uri(&file)},"position":{"line":2,"character":line.find("record :=").ok_or("invalid hover fixture")?+1}}))?;
        if hover.is_null(){return Err("language server started but hover returned no type information".into());}
        let completion=request(&mut input,&rx,3,"textDocument/completion",json!({"textDocument":{"uri":uri(&file)},"position":{"line":2,"character":line.find("record.number").ok_or("invalid completion fixture")?+7},"context":{"triggerKind":1}}))?;
        if !completion.to_string().contains("number"){return Err("language server field completion is missing 'number'".into());}
        send(&mut input,&json!({"jsonrpc":"2.0","id":4,"method":"shutdown","params":null}))?;
        send(&mut input,&json!({"jsonrpc":"2.0","method":"exit","params":null}))?;
        Ok(())
    })();
    drop(input);drop(child);let _=reader.join();
    result?;println!("PASS: {} startup, type hover and field completion",if thor{"TLS"}else{"OLS"});Ok(())
}
pub(super) fn finish(record:&Value,doctor:bool)->Result<()> {
    let root=Path::new(field(record,"root")?);let bin=Path::new(field(record,"bin")?);
    if !doctor {lifecycle::verify_inventory(root)?;}
    for name in ["thor","odin","thor-install"] {
        if !bin.join(format!("{name}{}",if cfg!(windows){".cmd"}else{""})).is_file(){return Err(format!("installed command is missing: {name}"));}
    }
    println!("PASS: installed Thor/Odin/installer commands");
    if env::split_paths(&env::var_os("PATH").unwrap_or_default()).any(|p|p==bin) {println!("PASS: current process PATH");}
    else {println!("SKIP: parent shell PATH refresh; open a new terminal or reload your shell profile");}
    let state=lifecycle::state(Path::new(field(record,"prefix")?))?;
    if state["editors"].as_array().is_some_and(|v|!v.is_empty()) {
        lsp(&root.join(format!("tls/bin/tls{}",env::consts::EXE_SUFFIX)),root,true)?;
        vscode::verify_editors(record)?;
        println!("SKIP: interactive VS Code rendering and source breakpoint; verify visually in a trusted workspace");
    } else {println!("SKIP: editor checks (no VS Code setup recorded)");}
    println!("{} verification complete. All requested non-interactive checks passed; skipped checks are listed above.",if doctor{"Doctor"}else{"Installation"});Ok(())
}
