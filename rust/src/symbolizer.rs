//! Native ASan symbolizer adapter. Maps dedicated embedded Thor tables; never
//! scans arbitrary executable bytes and never needs Python or source files.
use serde_json::Value;
use sha2::{Digest,Sha256};
use std::{collections::BTreeSet,fs::File,io::{self,BufRead,BufReader,Read,Seek,SeekFrom,Write},path::{Path,PathBuf},process::{Child,ChildStdin,ChildStdout,Command,Stdio}};
const MAGIC:&[u8]=b"THOR-MEM-MAP\0\x01";
const LIMIT:usize=32*1024*1024;
fn invalid()->io::Error {io::Error::new(io::ErrorKind::InvalidData,"invalid Thor memory map")}
fn u32_at(b:&[u8],at:usize)->Option<u32>{Some(u32::from_le_bytes(b.get(at..at+4)?.try_into().ok()?))}
fn u64_at(b:&[u8],at:usize)->Option<u64>{Some(u64::from_le_bytes(b.get(at..at+8)?.try_into().ok()?))}

fn decode(b:&[u8])->io::Result<Value> {
    let header=MAGIC.len()+40;
    if !b.starts_with(MAGIC){return Err(invalid());}
    let size=u64_at(b,MAGIC.len()).ok_or_else(invalid)?;
    if size>LIMIT as u64 {return Err(invalid());}
    let payload=b.get(header..header+size as usize).ok_or_else(invalid)?;
    if Sha256::digest(payload).as_slice()!=b.get(MAGIC.len()+8..header).ok_or_else(invalid)? {return Err(invalid());}
    let mut raw=vec![];
    flate2::read::ZlibDecoder::new(payload).take((LIMIT+1) as u64).read_to_end(&mut raw)?;
    if raw.len()>LIMIT{return Err(invalid());}
    let value:Value=serde_json::from_slice(&raw).map_err(|_|invalid())?;
    if value["version"]!=1 || !value["files"].is_array() || !value["units"].is_array(){return Err(invalid());}
    Ok(value)
}
fn read_map(path:&Path)->io::Result<Value> {
    let mut f=File::open(path)?;let mut header=[0u8;32];f.read_exact(&mut header)?;
    if u32_at(&header,0)!=Some(0xfeedfacf){return Err(invalid());}
    let count=u32_at(&header,16).ok_or_else(invalid)? as usize;
    let size=u32_at(&header,20).ok_or_else(invalid)? as usize;
    if size>LIMIT || count>65536{return Err(invalid());}
    let mut commands=vec![0;size];f.read_exact(&mut commands)?;let mut offset=0;
    for _ in 0..count {
        let kind=u32_at(&commands,offset).ok_or_else(invalid)?;
        let length=u32_at(&commands,offset+4).ok_or_else(invalid)? as usize;
        if length<8 || length>commands.len().saturating_sub(offset){return Err(invalid());}
        if kind==0x19 && length>=72 {
            let sections=u32_at(&commands,offset+64).ok_or_else(invalid)? as usize;
            for i in 0..sections.min((length-72)/80) {
                let start=offset+72+i*80;
                if commands.get(start..start+16).is_some_and(|n|n.split(|b|*b==0).next()==Some(b"__thor_map")) {
                    let length=u64_at(&commands,start+40).ok_or_else(invalid)?;
                    let position=u32_at(&commands,start+48).ok_or_else(invalid)?;
                    if length>LIMIT as u64 {return Err(invalid());}
                    f.seek(SeekFrom::Start(position as u64))?;
                    let mut bytes=vec![0;length as usize];f.read_exact(&mut bytes)?;return decode(&bytes);
                }
            }
        }
        offset+=length;
    }
    Err(invalid())
}
fn lookup(map:&Value,path:&str,line:u64,column:u64)->Option<(String,u64,u64,String)> {
    for unit in map["units"].as_array()? {
        if unit["path"].as_str()!=Some(path){continue;}
        let rows=unit["rows"].as_array()?;
        let key=|r:&Value| (r[0].as_u64().unwrap_or(0),r[1].as_u64().unwrap_or(0));
        let end=rows.partition_point(|r|key(r)<=(line,column.max(1)));
        let mut index=end.checked_sub(1);
        if index.is_none_or(|i|rows[i][0].as_u64()!=Some(line)) {
            index=Some(rows.partition_point(|r|key(r)<=(line,0)));
        }
        let r=rows.get(index?)?;
        let first=r[1].as_u64()?;let last=r[2].as_u64()?;
        if r[0].as_u64()==Some(line) && (column<=1 || first<=column && column<last) {
            let file=map["files"].get(r[3].as_u64()? as usize)?.as_str()?.to_owned();
            return Some((file,r[4].as_u64()?,r[5].as_u64()?+if r[6].as_bool()==Some(true){column.saturating_sub(first)}else{0},r[7].as_str().unwrap_or("").to_owned()));
        }
    }
    if let Some(aliases)=map["aliases"].as_object() {
        for (old,new) in aliases {if let Some(tail)=path.strip_prefix(old){return Some((format!("{}{tail}",new.as_str()?),line,column,String::new()));}}
    }
    None
}
fn module_path(command:&str)->Option<PathBuf> {
    let rest=command.trim_start().split_once(char::is_whitespace)?.1.trim_start();
    let mut word=String::new();let mut quote=None;let mut escape=false;
    for c in rest.chars() {
        if escape {word.push(c);escape=false;continue;}
        if c=='\\' && quote!=Some('\''){escape=true;continue;}
        if let Some(q)=quote {if c==q{quote=None;}else{word.push(c);}continue;}
        if c=='\'' || c=='"' {quote=Some(c);}else if c.is_whitespace(){break;}else{word.push(c);}
    }
    if quote.is_some() || word.is_empty(){return None;}
    let p=PathBuf::from(&word);
    if !p.is_file() {if let Some((name,arch))=word.rsplit_once(':'){if matches!(arch,"arm64"|"arm64e"|"x86_64"|"x86_64h"){return Some(PathBuf::from(name));}}}
    Some(p)
}
fn transform(response:&str,maps:&[Value],code:bool)->String {
    let mut lines=response.split_inclusive('\n').map(str::to_owned).collect::<Vec<_>>();
    for i in 0..lines.len() {
        let Some((prefix,last))=lines[i].trim_end_matches('\n').rsplit_once(':') else{continue};
        let Ok(last)=last.parse::<u64>() else{continue};
        let (path,line,column,has_column)=match prefix.rsplit_once(':') {
            Some((path,row)) if row.parse::<u64>().is_ok()=>(path,row.parse::<u64>().unwrap_or(0),last,true),
            _=>(prefix,last,1,false),
        };
        if let Some((file,row,column,procedure))=maps.iter().find_map(|m|lookup(m,path,line,column)) {
            lines[i]=if has_column {format!("{file}:{row}:{column}\n")}else{format!("{file}:{row}\n")};
            if code && i>0 && !procedure.is_empty() && lines[i-1].contains("__thor_"){lines[i-1]=format!("{procedure}\n");}
        }
    }
    lines.concat()
}
struct Backend {child:Child,input:Option<ChildStdin>,output:BufReader<ChildStdout>}
impl Backend {
    fn start(args:&[String],preferred:&str)->io::Result<Self>{
        let mut candidates=vec![];
        if let Some(p)=std::env::var_os("THOR_LLVM_SYMBOLIZER"){candidates.push(PathBuf::from(p));}
        if !preferred.is_empty(){candidates.push(preferred.into());}
        if let Ok(root)=crate::cli::root(){candidates.push(root.join("odin/libexec/llvm-symbolizer"));}
        for p in std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()){candidates.push(p.join("llvm-symbolizer"));}
        candidates.extend(["/opt/homebrew/opt/llvm/bin/llvm-symbolizer".into(),"/usr/local/opt/llvm/bin/llvm-symbolizer".into()]);
        let executable=candidates.into_iter().find(|p|p.is_file()).ok_or_else(||io::Error::new(io::ErrorKind::NotFound,"llvm-symbolizer not found"))?;
        let mut child=Command::new(executable).args(args).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()?;
        let input=child.stdin.take();let output=BufReader::new(child.stdout.take().ok_or_else(invalid)?);
        Ok(Self{child,input,output})
    }
    fn request(&mut self,command:&str)->io::Result<String>{
        let input=self.input.as_mut().ok_or_else(invalid)?;input.write_all(command.as_bytes())?;input.flush()?;
        let mut response=String::new();
        loop {let mut line=String::new();if self.output.read_line(&mut line)?==0{return Err(invalid());}
            let stop=line=="\n";response.push_str(&line);if response.len()>LIMIT{return Err(invalid());}if stop{break;}}
        Ok(response)
    }
}
impl Drop for Backend {fn drop(&mut self){drop(self.input.take());for _ in 0..100 {if self.child.try_wait().ok().flatten().is_some(){return;}std::thread::sleep(std::time::Duration::from_millis(20));}let _=self.child.kill();let _=self.child.wait();}}
pub fn run(args:&[String])->io::Result<()> {
    let mut maps=vec![];let mut seen=BTreeSet::new();let mut backend:Option<Backend>=None;let mut preferred=String::new();
    let stdin=io::stdin();let mut output=io::stdout().lock();
    for line in stdin.lock().lines() {
        let line=line?;let command=format!("{line}\n");
        if let Some(path)=module_path(&command){if seen.insert(path.clone()){if let Ok(map)=read_map(&path){if preferred.is_empty(){preferred=map["symbolizer"].as_str().unwrap_or("").to_owned();}maps.push(map);}}}
        if backend.is_none(){backend=Backend::start(args,&preferred).ok();}
        let response=backend.as_mut().and_then(|b|b.request(&command).ok());
        let text=if let Some(response)=response{transform(&response,&maps,line.starts_with("CODE "))}
            else {backend=None;if line.starts_with("DATA "){"??\n0 0\n??:0\n\n".into()}else{"??\n??:0:0\n\n".into()}};
        output.write_all(text.as_bytes())?;output.flush()?;
    }
    Ok(())
}
