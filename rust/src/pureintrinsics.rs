//! Compiler-known operations retain their native implementation. Names are
//! trusted only through the installed base:intrinsics package and constant aliases.
use super::*;

impl Purity<'_, '_> {
    pub(super) fn llvm_primitive(&self, candidate: Ref) -> bool {
        let source = &self.r.sources[candidate.file];
        let Some(root) = source.info["odin_root"].as_str() else {
            return false;
        };
        let root = std::path::Path::new(root);
        if !["base", "core", "vendor"]
            .iter()
            .any(|dir| source.path.starts_with(root.join(dir)))
        {
            return false;
        }
        let Some(foreign) = source
            .ancestors(candidate.node)
            .find(|id| source.node(*id).kind == "ForeignBlock")
        else {
            return false;
        };
        if source.node(foreign).data["calling_convention"] != "none" {
            return false;
        }
        let Some(declaration) = source
            .node(candidate.node)
            .parent
            .filter(|id| source.node(*id).kind == "Declaration")
        else {
            return false;
        };
        self.r
            .attributes(Ref {
                file: candidate.file,
                node: declaration,
            })
            .iter()
            .any(|(name, value)| {
                if name != "link_name" {
                    return false;
                }
                let Some(link) =
                    value.and_then(|value| crate::lower::unquote(self.text(value)).ok())
                else {
                    return false;
                };
                let Some((operation, float)) = link
                    .strip_prefix("llvm.")
                    .and_then(|name| name.rsplit_once('.'))
                else {
                    return false;
                };
                [
                    "sin",
                    "cos",
                    "pow",
                    "exp",
                    "exp2",
                    "log",
                    "log2",
                    "log10",
                    "fmuladd",
                    "fma",
                    "sqrt",
                    "floor",
                    "ceil",
                    "trunc",
                    "round",
                    "roundeven",
                ]
                .contains(&operation)
                    && ["f16", "f32", "f64"].contains(&float)
            })
    }
    pub(super) fn intrinsic_name(&self, callee: Ref, seen: &mut HashSet<Ref>) -> Option<String> {
        if !seen.insert(callee) {
            return None;
        }
        if let Some(name) = self.r.library_member(callee, "base:intrinsics") {
            return Some(name);
        }
        if self.kind(callee) == "Paren" {
            return self
                .role(callee, "expr")
                .and_then(|r| self.intrinsic_name(r, seen));
        }
        let symbol = &self.r.symbols[self.r.symbol(callee)?];
        if !symbol.constant || symbol.immutable {
            return None;
        }
        let value = Ref {
            file: symbol.reference.file,
            node: symbol.value?,
        };
        matches!(self.kind(value), "Ident" | "Selector" | "Paren")
            .then(|| self.intrinsic_name(value, seen))
            .flatten()
    }
    pub(super) fn intrinsic_allowed(name: &str) -> bool {
        SCALAR.contains(&name)
            || STRUCTURAL.contains(&name)
            || name.starts_with("type_is_")
            || name.starts_with("type_has_")
    }
}

const SCALAR: &[&str] = &[
    "sqrt",
    "fused_mul_add",
    "count_ones",
    "count_zeros",
    "count_leading_zeros",
    "count_trailing_zeros",
    "count_leading_ones",
    "count_trailing_ones",
    "reverse_bits",
    "byte_swap",
    "saturating_add",
    "saturating_sub",
    "constant_log2",
    "expect",
    "likely",
    "unlikely",
];
const STRUCTURAL: &[&str] = &[
    "type_base_type",
    "type_core_type",
    "type_elem_type",
    "type_union_tag_type",
    "type_union_tag_offset",
    "type_union_base_tag_value",
    "type_union_variant_count",
    "type_variant_type_of",
    "type_variant_index_of",
    "type_bit_set_elem_type",
    "type_bit_set_underlying_type",
    "type_field_type",
    "type_field_bit_size",
    "type_field_bit_offset",
    "type_proc_parameter_count",
    "type_proc_return_count",
    "type_proc_parameter_type",
    "type_proc_return_type",
    "type_proc_calling_convention",
    "type_struct_field_count",
    "type_struct_has_implicit_padding",
    "type_polymorphic_record_parameter_count",
    "type_field_index_of",
    "type_fixed_capacity_dynamic_array_len_offset",
    "type_enum_is_contiguous",
    "type_convert_variants_to_pointers",
    "type_merge",
    "type_integer_to_unsigned",
    "type_integer_to_signed",
];
