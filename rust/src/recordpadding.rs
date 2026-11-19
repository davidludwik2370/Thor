//! Prove explicit padding using native type witnesses, without reading values.
//! Reconstructing the complete base layout keeps unknown named fields and raw
//! unions from being mistaken for padding, including in generic specializations.
use super::*;
use std::collections::BTreeSet;

pub(super) type Layouts = BTreeSet<(String, Vec<(String, String, String)>)>;

fn catalog(sources: &[Source]) -> Layouts {
    sources
        .iter()
        .flat_map(|source| {
            source
                .nodes
                .iter()
                .enumerate()
                .filter(|(_, node)| node.kind == "StructType" && node.op != "raw_union")
                .filter_map(move |(index, node)| {
                    let fields = source.role(NodeId(index), "fields")?;
                    let fields = source
                        .node(fields)
                        .children
                        .iter()
                        .filter(|field| source.node(**field).kind == "Field")
                        .flat_map(|field| {
                            source.group(*field, "names").iter().map(move |name| {
                                let flags = match (
                                    source.node(*field).data["using_field"] == true,
                                    source.node(*field).data["subtype_field"] == true,
                                ) {
                                    (true, true) => "using #subtype ",
                                    (true, false) => "using ",
                                    (false, true) => "#subtype ",
                                    _ => "",
                                };
                                (
                                    source.text(*name).to_string(),
                                    flags.to_string(),
                                    source.node(*field).data["field_tag"]
                                        .as_str()
                                        .unwrap_or("")
                                        .to_string(),
                                )
                            })
                        })
                        .collect::<Vec<_>>();
                    let named = fields
                        .iter()
                        .filter(|(name, _, _)| name != "_")
                        .map(|(name, _, _)| name)
                        .collect::<BTreeSet<_>>();
                    if named.len() != fields.iter().filter(|(name, _, _)| name != "_").count() {
                        // An invalid declaration can live in an inactive when.
                        // Never instantiate an invalid trial schema from it.
                        return None;
                    }
                    fields.iter().any(|(name, _, _)| name == "_").then(|| {
                        (
                            node.data["struct_flags"].as_str().unwrap_or("").to_string(),
                            fields,
                        )
                    })
                })
        })
        .collect()
}

impl Lower<'_, '_> {
    pub(super) fn record_padding_count(
        &mut self,
        meta: &str,
        builtin: &str,
        prefix: &str,
    ) -> (String, String, Vec<String>) {
        let sources = self.resolver.sources;
        let layouts = self.padding_layouts.get_or_insert_with(|| catalog(sources));
        count(layouts, meta, builtin, prefix)
    }
}

fn count(
    layouts: &Layouts,
    meta: &str,
    builtin: &str,
    prefix: &str,
) -> (String, String, Vec<String>) {
    if layouts.is_empty() {
        return (String::new(), "0".into(), vec![]);
    }
    let tuple = format!("{prefix}padding_types");
    let count = format!("{prefix}padding_count");
    let total = format!("{meta}.type_struct_field_count(T)");
    // `any` cannot be a native type alias. Store reflected types as fields of
    // compiler-only wrappers so even opaque padding types remain admissible.
    let mut code = format!("\n{tuple}::struct{{value:({builtin}.type_of({builtin}.compress_values({builtin}.expand_values(T{{}}))) when {total}>0 else struct{{}})}}\n");
    let tuple_type = format!("{meta}.type_field_type({tuple},\"value\")");
    let max_fields = layouts
        .iter()
        .map(|(_, fields)| fields.len())
        .max()
        .unwrap_or(0);
    let using_indices = layouts
        .iter()
        .flat_map(|(_, fields)| {
            fields
                .iter()
                .enumerate()
                .filter(|(_, (_, flags, _))| !flags.is_empty())
                .map(|(index, _)| index)
        })
        .collect::<BTreeSet<_>>();
    for index in 0..max_fields {
        let typ = format!("{prefix}padding_type_{index}");
        code.push_str(&format!("{typ}::struct{{value:(({tuple_type} when {total}==1 else ({meta}.type_elem_type({tuple_type}) when {meta}.type_is_array({tuple_type}) else {meta}.type_field_type({tuple_type},\"v{index}\"))) when {total}>{index} else struct{{}})}}\n"));
        if using_indices.contains(&index) {
            let deref = format!("{typ}_base");
            let actual = format!("{meta}.type_field_type({typ},\"value\")");
            code.push_str(&format!("{deref}::struct{{value:({meta}.type_elem_type({actual}) when {actual}!=rawptr && {meta}.type_is_pointer({actual}) else {actual})}}\n"));
            let base = format!("{meta}.type_field_type({deref},\"value\")");
            code.push_str(&format!("{typ}_using::struct{{value:({actual} when {meta}.type_is_struct({base}) || {meta}.type_is_bit_field({base}) || ({builtin}.len({base})<=4 when {meta}.type_is_array({base}) else false) else struct{{}})}}\n"));
        }
    }
    let cases = layouts.iter().map(|(flags, fields)| {
        let padding = fields.iter().filter(|(name, _, _)| name == "_").count();
        let schema = fields.iter().enumerate().map(|(index, (name, flags, tag))| {
            // Scalar fields in unrelated layouts must not cause invalid
            // `using`/`#subtype` trial schemas. Their safe substitute cannot
            // match the complete base type, so the proof remains exact.
            let typ = format!("{meta}.type_field_type({prefix}padding_type_{index}{},\"value\")", if flags.is_empty(){""}else{"_using"});
            format!("{flags}{name}:{typ} {tag}")
        }).collect::<Vec<_>>().join(",");
        let align = if flags.contains("#packed") { String::new() } else { format!(" #align({builtin}.align_of(T))") };
        format!("({padding} when ({meta}.type_base_type(T)==struct{flags}{align}{{{schema}}} when {total}=={} else false) else 0)", fields.len())
    }).collect::<Vec<_>>();
    fn maximum(cases: &[String], builtin: &str) -> String {
        match cases.len() {
            0 => "0".into(),
            1 => cases[0].clone(),
            n => format!(
                "{builtin}.max({},{})",
                maximum(&cases[..n / 2], builtin),
                maximum(&cases[n / 2..], builtin)
            ),
        }
    }
    // Different tag spellings can decode to the same native layout. Matching
    // it more than once must not count its padding twice.
    code.push_str(&format!("{count}::{}\n", maximum(&cases, builtin)));
    let fields = (0..max_fields)
        .map(|index| format!("{meta}.type_field_type({prefix}padding_type_{index},\"value\")"))
        .collect();
    (code, count, fields)
}
