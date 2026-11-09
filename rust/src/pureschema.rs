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
        let mut parts=vec![
            format!("when Depth>24{{#panic(\"Pure functions do not support recursive reference-containing types or value nesting deeper than 24 levels\"+Site)}}"),
            format!("else when Allow_Refs && {meta}.type_is_pointer(T) && T!=rawptr{{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}}"),
            format!("else when Allow_Refs && {meta}.type_is_fixed_capacity_dynamic_array(T){{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}}"),
            format!("else when {meta}.type_is_dynamic_array(T){{when Result && !Allow_Owned{{#panic(\"Contextless compile-time procedures cannot return dynamic arrays; use fn or a contextful proc\"+Site)}}else when Result || Allow_Refs{{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}}else{{{module}.validate(T,false,Depth,Site)}}}}"),
            format!("else when {meta}.type_is_struct(T){{\nwhen {meta}.type_struct_field_count(T)!=({}){{#panic(\"Pure record fields could not be fully resolved\"+Site)}}",field_count(&names,&meta)),
        ];
        for field in names {
            let predicate = direct_field(&field, &meta);
            let field = json!(field);
            parts.push(format!("when {predicate}{{{name}({meta}.type_field_type(T,{field}),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}}"));
        }
        parts.extend([
            "}".into(),
            format!("else when {meta}.type_is_array(T)||{meta}.type_is_enumerated_array(T)||{meta}.type_is_slice(T){{when Result && !Allow_Owned && {meta}.type_is_slice(T){{#panic(\"Contextless compile-time procedures cannot return strings/slices; use fn or a contextful proc\"+Site)}}else{{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}}}}"),
            format!("else when {meta}.type_is_union(T){{{name}_union(T,Result,Allow_Owned,Allow_Refs,Depth,0,Site)}}"),
            format!("else{{{module}.validate(T,Result && !Allow_Owned,Depth,Site)}}"),
        ]);
        let code=format!("\n@(private=\"file\")\n{name}::proc \"contextless\"($T:typeid,$Result:bool,$Allow_Owned:bool,$Allow_Refs:bool,$Depth:int,$Site:string){{\n{}\n}}\n@(private=\"file\")\n{name}_union::proc \"contextless\"($T:typeid,$Result:bool,$Allow_Owned:bool,$Allow_Refs:bool,$Depth:int,$Index:int,$Site:string){{when Index<{meta}.type_union_variant_count(T){{{name}({meta}.type_variant_type_of(T,Index),Result,Allow_Owned,Allow_Refs,Depth+1,Site);{name}_union(T,Result,Allow_Owned,Allow_Refs,Depth,Index+1,Site)}}}}\n",parts.join("\n"));
        self.helpers.insert(key, self.g(code, site));
        name
    }
}
