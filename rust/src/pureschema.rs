//! Native exact-type contracts for the record layouts inspected by the resolver.
use super::*;
use std::collections::BTreeSet;

fn direct_field(name: &str, meta: &str) -> String {
    // type_has_field also sees promoted `using` members. Shared-field checks
    // compare physical struct fields, so aliases cannot inflate schema coverage.
    let quoted = json!(name);
    format!("({meta}.type_has_shared_fields(T,struct{{{name}:{meta}.type_field_type(T,{quoted})}}) when {meta}.type_has_field(T,{quoted}) else false)")
}
fn field_count(names: &[String], meta: &str) -> String {
    match names.len() {
        0 => "0".into(),
        1 => format!("(1 when {} else 0)", direct_field(&names[0], meta)),
        _ => {
            let middle = names.len() / 2;
            format!(
                "({}+{})",
                field_count(&names[..middle], meta),
                field_count(&names[middle..], meta)
            )
        }
    }
}

fn overlap_count(names: &[String], meta: &str, builtin: &str) -> String {
    // Ordinary struct fields with positive size cannot share byte offset zero.
    // A raw union can. Field-name equality alone would also reject unrelated
    // ordinary records; zero-sized fields carry no overlapping bytes.
    match names.len() {
        0 => "0".into(),
        1 => {
            let name = &names[0];
            let quoted = json!(name);
            format!("((1 when {builtin}.offset_of(T,{name})==0 && {builtin}.size_of({meta}.type_field_type(T,{quoted}))>0 else 0) when {} else 0)",direct_field(name,meta))
        }
        _ => {
            let middle = names.len() / 2;
            format!(
                "({}+{})",
                overlap_count(&names[..middle], meta, builtin),
                overlap_count(&names[middle..], meta, builtin)
            )
        }
    }
}

impl Lower<'_, '_> {
    pub(super) fn pure_type_site(&mut self, site: NodeId, proc: NodeId, role: &str) -> String {
        let token = format!(
            " [{}pure_type_site_{}_{}]",
            self.source.prefix, proc.0, site.0
        );
        let name = serde_json::from_str::<String>(&self.checked_procedure_literal(proc))
            .unwrap_or_else(|_| "anonymous procedure".into());
        let description = format!(
            "{role} in pure call '{name}'. Rejected type: {}.",
            self.source.text(site)
        );
        self.type_sites
            .insert(token.clone(), (self.reference(site), description));
        json!(token).to_string()
    }
    pub(super) fn pure_validator(&mut self, site: NodeId) -> String {
        let name = format!("{}pure_validate", self.prefix);
        let key = (NodeId(0), self.prefix.clone(), name.clone());
        if self.helpers.contains_key(&key) {
            return name;
        }
        let public = name;
        let name = format!("{public}_mode");
        let names = self
            .resolver
            .sources
            .iter()
            .flat_map(|s| {
                s.nodes
                    .iter()
                    .enumerate()
                    .filter(|(_, node)| node.kind == "StructType")
                    .filter_map(|(index, _)| s.role(NodeId(index), "fields"))
                    .flat_map(|fields| s.node(fields).children.iter())
                    .filter(|field| s.node(**field).kind == "Field")
                    .flat_map(|field| s.group(*field, "names"))
                    .map(|field| s.text(*field).to_string())
            })
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        self.meta = true;
        let meta = format!("{}meta", self.prefix);
        let module = self.module("pure");
        self.builtin = true;
        let builtin = format!("{}builtin", self.prefix);
        let mut parts=vec![
            format!("when Mode!=0 && ({meta}.type_is_pointer(T)||{meta}.type_is_multi_pointer(T)||{meta}.type_is_proc(T)||{meta}.type_is_map(T)||{meta}.type_is_cstring(T)||{meta}.type_is_cstring16(T)||{meta}.type_is_string16(T)||T==rawptr||T==any||T==typeid){{#panic((\"Pure code cannot convert values to pointers, procedure values, any or runtime type identities.\" when Mode==1 else \"Pure code cannot observe pointer identities or convert pointers to numeric values.\")+Site)}}"),
            format!("else when Depth>24{{#panic(\"Pure functions do not support recursive reference-containing types or value nesting deeper than 24 levels\"+Site)}}"),
            format!("else when Allow_Refs && {meta}.type_is_pointer(T) && T!=rawptr{{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site,Mode)}}"),
            format!("else when Allow_Refs && {meta}.type_is_fixed_capacity_dynamic_array(T){{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site,Mode)}}"),
            format!("else when {meta}.type_is_dynamic_array(T){{when Result && !Allow_Owned{{#panic(\"Contextless compile-time procedures cannot return dynamic arrays; use fn or a contextful proc\"+Site)}}else when Result || Allow_Refs{{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site,Mode)}}else{{{module}.validate(T,false,Depth,Site)}}}}"),
            format!("else when {meta}.type_is_struct(T){{\nwhen {meta}.type_struct_field_count(T)!=({}){{#panic(\"Pure record fields could not be fully resolved\"+Site)}}",field_count(&names,&meta)),
        ];
        if self.big_enabled(){
            let big=self.module("big");
            parts.insert(2,format!("else when T=={big}.Integer || T=={big}.Rational{{when Result && !Allow_Owned{{#panic(\"Contextless compile-time procedures cannot return allocated numbers; use fn or a contextful proc\"+Site)}}}}"));
        }
        parts.push(format!("when {}>1{{#panic(\"Raw-union storage cannot be proved pure: overlapping fields in a raw-union\"+Site)}}else{{",overlap_count(&names,&meta,&builtin)));
        for (file, owner) in self.resolver.sources.iter().enumerate() {
            if owner.path.parent() != self.source.path.parent() {
                continue;
            }
            for (index, record) in owner
                .nodes
                .iter()
                .enumerate()
                .filter(|(_, node)| node.kind == "StructType" && node.op == "raw_union")
            {
                if owner.role(NodeId(index), "params").is_some() {
                    continue;
                }
                let Some(declaration) = record.parent.filter(|decl| {
                    owner.node(*decl).kind == "Declaration"
                        && owner.node(*decl).parent == Some(NodeId(0))
                }) else {
                    continue;
                };
                let raw_names = owner.group(declaration, "names");
                if raw_names.len() != 1 {
                    continue;
                }
                let field_private = self
                    .resolver
                    .attributes(Ref {
                        file,
                        node: declaration,
                    })
                    .iter()
                    .any(|(name, value)| {
                        name == "private"
                            && value.is_some_and(|value| {
                                self.resolver.sources[value.file].text(value.node) == "\"file\""
                            })
                    })
                    || owner.info["file_tags"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(|tag| tag.as_str())
                        .any(|tag| {
                            tag.contains("private")
                                && tag.split_whitespace().any(|word| word == "file")
                        });
                if field_private && owner.path != self.source.path {
                    continue;
                }
                parts.push(format!(
                    "when T=={}{{#panic(\"Raw-union storage cannot be proved pure\"+Site)}}",
                    owner.text(raw_names[0])
                ));
            }
        }
        for field in names {
            let predicate = direct_field(&field, &meta);
            let field = json!(field);
            parts.push(format!("when {predicate}{{{name}({meta}.type_field_type(T,{field}),Result,Allow_Owned,Allow_Refs,Depth+1,Site,Mode)}}"));
        }
        parts.extend([
            "}".into(),
            "}".into(),
            format!("else when {meta}.type_is_array(T)||{meta}.type_is_enumerated_array(T)||{meta}.type_is_slice(T){{when Result && !Allow_Owned && {meta}.type_is_slice(T){{#panic(\"Contextless compile-time procedures cannot return strings/slices; use fn or a contextful proc\"+Site)}}else{{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site,Mode)}}}}"),
            format!("else when {meta}.type_is_union(T){{{name}_union(T,Result,Allow_Owned,Allow_Refs,Depth,0,Site,Mode)}}"),
            format!("else{{{module}.validate(T,Result && !Allow_Owned,Depth,Site)}}"),
        ]);
        let code=format!("\n@(private=\"file\")\n{name}::proc \"contextless\"($T:typeid,$Result:bool,$Allow_Owned:bool,$Allow_Refs:bool,$Depth:int,$Site:string,$Mode:int){{\n{}\n}}\n@(private=\"file\")\n{name}_union::proc \"contextless\"($T:typeid,$Result:bool,$Allow_Owned:bool,$Allow_Refs:bool,$Depth:int,$Index:int,$Site:string,$Mode:int){{when Index<{meta}.type_union_variant_count(T){{{name}({meta}.type_variant_type_of(T,Index),Result,Allow_Owned,Allow_Refs,Depth+1,Site,Mode);{name}_union(T,Result,Allow_Owned,Allow_Refs,Depth,Index+1,Site,Mode)}}}}\n",parts.join("\n"));
        let code = code + &format!("\n@(private=\"file\")\n{public}::proc \"contextless\"($T:typeid,$Result:bool,$Allow_Owned:bool,$Allow_Refs:bool,$Depth:int,$Site:string){{{name}(T,Result,Allow_Owned,Allow_Refs,Depth,Site,0)}}\n");
        self.helpers.insert(key, self.g(code, site));
        public
    }
}
