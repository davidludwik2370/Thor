//! Narrow settings edits: preserve comments, whitespace and unrelated values.
use super::*;

struct Token {start:usize,end:usize,kind:u8}
pub(super) struct Document {pub value:Value,text:String,tokens:Vec<Token>}

impl Document {
    pub fn set_missing(&self,key:&str,value:&Value)->Result<String> {
        if self.value.get(key).is_some() {return Ok(self.text.clone());}
        self.edit(key,Some(value))
    }
    fn edit(&self,key:&str,value:Option<&Value>)->Result<String> {
        let mut depth=0;let mut found=Vec::new();
        for (i,t) in self.tokens.iter().enumerate() {
            if depth==1 && t.kind==b'"' && self.tokens.get(i+1).is_some_and(|t|t.kind==b':') &&
                serde_json::from_str::<String>(&self.text[t.start..t.end]).map_err(err)?==key {
                let mut j=i+2;let mut nested=0;
                while j<self.tokens.len() {
                    let k=self.tokens[j].kind;
                    if nested==0 && (k==b','||k==b'}'){break;}
                    match k {b'{'|b'['=>nested+=1,b'}'|b']'=>nested-=1,_=>{}}j+=1;
                }
                if j<=i+2 {return Err("missing setting value".into());}
                found.push((i,j));
            }
            match t.kind {b'{'|b'['=>depth+=1,b'}'|b']'=>depth-=1,_=>{}}
        }
        if found.len()>1 {return Err("duplicate settings; preserved".into());}
        if let Some((i,j))=found.first().copied() {
            let (start,end,replacement)=if let Some(v)=value {
                (self.tokens[i+2].start,self.tokens[j-1].end,serde_json::to_string(v).map_err(err)?)
            } else {
                let next=self.tokens.get(j).is_some_and(|t|t.kind==b',');
                let start=if !next && i>0 && self.tokens[i-1].kind==b',' {self.tokens[i-1].start}else{self.tokens[i].start};
                (start,if next {self.tokens[j].end}else{self.tokens[j-1].end},String::new())
            };
            return Ok(format!("{}{}{}",&self.text[..start],replacement,&self.text[end..]));
        }
        if let Some(v)=value {
            let end=self.tokens.last().ok_or("missing object")?.start;
            let comma=if self.value.as_object().unwrap().is_empty()||self.tokens[self.tokens.len()-2].kind==b','{""}else{","};
            return Ok(format!("{}{comma}\n{}:{}\n{}",&self.text[..end],serde_json::to_string(key).map_err(err)?,serde_json::to_string(v).map_err(err)?,&self.text[end..]));
        }Ok(self.text.clone())
    }
    pub fn parse(text:String)->Result<Self> {
        if text.len()>4*1024*1024 {return Err("editor settings exceed 4 MiB; left unchanged".into());}
        let b=text.as_bytes();let mut i=0;let mut tokens=Vec::new();let mut clean=String::new();
        if b.starts_with(&[239,187,191]) {i=3;}
        while i<b.len() {
            if b[i].is_ascii_whitespace() {clean.push(b[i] as char);i+=1;continue;}
            if b[i..].starts_with(b"//") {
                i+=2;while i<b.len() && b[i]!=b'\n' {i+=1;}clean.push(' ');continue;
            }
            if b[i..].starts_with(b"/*") {
                i+=2;while i+1<b.len() && !b[i..].starts_with(b"*/") {i+=1;}
                if i+1>=b.len() {return Err("unterminated settings comment; left unchanged".into());}
                i+=2;clean.push(' ');continue;
            }
            let start=i;let kind=b[i];i+=1;
            if kind==b'"' {
                let mut closed=false;
                while i<b.len() {
                    if b[i]==b'\\' {i+=2;continue;}
                    if b[i]==b'"' {i+=1;closed=true;break;}i+=1;
                }
                if !closed {return Err("unterminated settings string; left unchanged".into());}
            } else if !b"{}[],:".contains(&kind) {
                while i<b.len() && !b[i].is_ascii_whitespace() && !b"{}[],:/".contains(&b[i]) {i+=1;}
            }
            tokens.push(Token{start,end:i,kind});
            clean.push_str(&text[start..i]);
        }
        // JSONC permits a trailing comma in either an object or an array.
        // Rebuild from tokens, retaining a separating space across comments.
        clean.clear();
        for (index,token) in tokens.iter().enumerate() {
            if token.kind==b',' && tokens.get(index+1).is_some_and(|t|b"}]".contains(&t.kind)) {continue;}
            clean.push_str(&text[token.start..token.end]);clean.push(' ');
        }
        let value:Value=serde_json::from_str(&clean).map_err(|e|format!("invalid editor settings; left unchanged: {e}"))?;
        if !value.is_object() {return Err("editor settings must be an object; left unchanged".into());}
        Ok(Self{value,text,tokens})
    }
    pub fn set_string(&self,key:&str,value:&str)->Result<String> {
        let encoded=serde_json::to_string(value).map_err(err)?;
        if self.value.get(key).is_none() {
            let end=self.tokens.last().ok_or("missing settings object")?.start;
            let comma=if self.value.as_object().unwrap().is_empty() || self.tokens[self.tokens.len()-2].kind==b',' {""}else{","};
            return Ok(format!("{}{comma}\n  {}: {encoded}\n{}",&self.text[..end],serde_json::to_string(key).map_err(err)?,&self.text[end..]));
        }
        let mut depth=0;let mut matches=Vec::new();
        for (i,t) in self.tokens.iter().enumerate() {
            if depth==1 && t.kind==b'"' && self.tokens.get(i+1).is_some_and(|t|t.kind==b':') &&
                serde_json::from_str::<String>(&self.text[t.start..t.end]).map_err(err)?==key {
                let start=self.tokens.get(i+2).ok_or("missing setting value")?;
                if start.kind!=b'"' && &self.text[start.start..start.end]!="null" {return Err("unsupported OLS setting; left unchanged".into());}
                matches.push((start.start,start.end));
            }
            match t.kind {b'{'|b'['=>depth+=1,b'}'|b']'=>depth-=1,_=>{}}
        }
        if matches.len()!=1 {return Err("duplicate OLS settings; left unchanged".into());}
        let (start,end)=matches[0];Ok(format!("{}{encoded}{}",&self.text[..start],&self.text[end..]))
    }
}

/// Reverse only keys still equal to the installer's value; preserve subsequent
/// user edits, comments, formatting, and unrelated nested settings.
pub(super) fn restore_managed(current:&str,before:&str,after:&str)->Result<String> {
    let before=Document::parse(before.into())?;let after=Document::parse(after.into())?;
    let mut result=current.to_owned();
    let keys:BTreeSet<_>=before.value.as_object().unwrap().keys().chain(after.value.as_object().unwrap().keys()).collect();
    for key in keys {
        if before.value.get(key)==after.value.get(key){continue;}
        let doc=Document::parse(result.clone())?;
        if doc.value.get(key)==after.value.get(key){result=doc.edit(key,before.value.get(key))?;}
    }Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn missing_defaults_keep_explicit_values_comments_and_other_settings() {
        let text="\u{feff}{ // keep\n\"workbench.colorTheme\":\"café\",\"thor.colors.recommended\":false,\"explicitNull\":null,\"nested\":{\"keep\":1},}";
        let doc=Document::parse(text.into()).unwrap();
        for key in ["workbench.colorTheme","thor.colors.recommended","explicitNull"] {
            let unchanged=doc.set_missing(key,&json!("default")).unwrap();
            assert_eq!(unchanged,text);
        }
        let edited=doc.set_missing("missing",&json!(true)).unwrap();
        let value=Document::parse(edited.clone()).unwrap().value;
        let mut expected=doc.value;expected["missing"]=json!(true);
        assert_eq!(value,expected);
        assert!(edited.contains("// keep"));
        assert!(edited.starts_with('\u{feff}'));
    }
    #[test] fn restores_only_unchanged_managed_keys_and_keeps_comments() {
        let before="{\"theme\":\"mine\",\"old\":{\"nested\":[1,2]}}";
        let after="{\"theme\":\"Thor\",\"old\":{\"nested\":[3]},\"thor\":true}";
        let current="{ // user\n\"theme\":\"Thor\",\"old\":{\"nested\":[9]},\"thor\":true,\"new\":1,}";
        let text=restore_managed(current,before,after).unwrap();
        let value=Document::parse(text.clone()).unwrap().value;
        assert_eq!(value,json!({"theme":"mine","old":{"nested":[9]},"new":1}));
        assert!(text.contains("// user"));
    }
    #[test] fn removing_first_last_and_only_key_retains_valid_jsonc() {
        for current in ["{\"a\":1}","{\"a\":1,\"keep\":2}","{\"keep\":2,\"a\":1}","{\"keep\":2,\"a\":1,}"] {
            let result=restore_managed(current,"{}","{\"a\":1}").unwrap();
            let value=Document::parse(result).unwrap().value;
            assert!(value.get("a").is_none());
        }
    }
    #[test] fn preserves_jsonc_and_unicode() {
        for text in ["{}","\u{feff}{ // hi\n \"theme\": \"café\", /*keep*/\n}","{\"nested\":{\"ols.server.path\":\"keep\",},\"list\":[1,],}"] {
            let doc=Document::parse(text.into()).unwrap();let edit=doc.set_string("ols.server.path","/a café/ols").unwrap();
            let changed=Document::parse(edit).unwrap();let mut expected=doc.value;
            expected["ols.server.path"]=json!("/a café/ols");assert_eq!(changed.value,expected);
        }
    }
    #[test] fn updates_only_managed_value() {
        let text="{\"ols.server.path\": /*keep*/ \"/old\",\"other\":\"same\"}//end";
        let doc=Document::parse(text.into()).unwrap();let changed=doc.set_string("ols.server.path","/new").unwrap();
        let expected=text.replace("/old","/new");assert_eq!(changed,expected);
    }
    #[test] fn invalid_or_ambiguous_settings_are_preserved() {
        for text in ["{broken}","{/*","[]","{\"x\":tr/*bad*/ue}","{\"x\":\"unterminated}"] {
            let parsed=Document::parse(text.into());assert!(parsed.is_err());
        }
        let doc=Document::parse("{\"ols.server.path\":\"a\",\"ols.server.path\":\"b\"}".into()).unwrap();
        let edited=doc.set_string("ols.server.path","c");assert!(edited.is_err());
    }
}
