//! Compiler primitive result shapes. These facts confer no effect exemption.
use super::*;
const SIMD_SAME: &[&str] = &[
    "simd_add",
    "simd_sub",
    "simd_mul",
    "simd_div",
    "simd_saturating_add",
    "simd_saturating_sub",
    "simd_shl",
    "simd_shr",
    "simd_shl_masked",
    "simd_shr_masked",
    "simd_bit_and",
    "simd_bit_or",
    "simd_bit_xor",
    "simd_bit_and_not",
    "simd_neg",
    "simd_abs",
    "simd_min",
    "simd_max",
    "simd_clamp",
    "simd_replace",
    "simd_ceil",
    "simd_floor",
    "simd_trunc",
    "simd_nearest",
    "simd_lanes_reverse",
    "simd_lanes_rotate_left",
    "simd_lanes_rotate_right",
    "simd_odd_even",
    "simd_pairwise_add",
    "simd_pairwise_sub",
];
const SIMD_COMPARE: &[&str] = &[
    "simd_lanes_eq",
    "simd_lanes_ne",
    "simd_lanes_lt",
    "simd_lanes_le",
    "simd_lanes_gt",
    "simd_lanes_ge",
];
const SIMD_REDUCE: &[&str] = &[
    "simd_extract",
    "simd_reduce_add_bisect",
    "simd_reduce_mul_bisect",
    "simd_reduce_add_ordered",
    "simd_reduce_mul_ordered",
    "simd_reduce_add_pairs",
    "simd_reduce_mul_pairs",
    "simd_reduce_min",
    "simd_reduce_max",
    "simd_reduce_and",
    "simd_reduce_or",
    "simd_reduce_xor",
    "simd_reduce_any",
    "simd_reduce_all",
];
const ATOMIC: &[&str] = &[
    "atomic_type_is_lock_free",
    "atomic_thread_fence",
    "atomic_signal_fence",
    "atomic_store",
    "atomic_store_explicit",
    "atomic_load",
    "atomic_load_explicit",
    "atomic_add",
    "atomic_add_explicit",
    "atomic_sub",
    "atomic_sub_explicit",
    "atomic_and",
    "atomic_and_explicit",
    "atomic_nand",
    "atomic_nand_explicit",
    "atomic_or",
    "atomic_or_explicit",
    "atomic_xor",
    "atomic_xor_explicit",
    "atomic_exchange",
    "atomic_exchange_explicit",
    "atomic_compare_exchange_strong",
    "atomic_compare_exchange_strong_explicit",
    "atomic_compare_exchange_weak",
    "atomic_compare_exchange_weak_explicit",
];
const QUERIES: &[&str] = &["has_target_feature", "is_package_imported"];
pub(crate) fn atomic_intrinsic(name:&str)->bool{ATOMIC.contains(&name)}
const MATRIX: &[&str] = &[
    "transpose",
    "outer_product",
    "hadamard_product",
    "matrix_flatten",
];
const MACHINE: &[&str] = &[
    "debug_trap",
    "trap",
    "cpu_relax",
    "read_cycle_counter",
    "read_cycle_counter_frequency",
    "return_address",
    "frame_address",
    "stack_pointer",
    "address_of_return_address",
    "alloca",
    "prefetch_read_instruction",
    "prefetch_read_data",
    "prefetch_write_instruction",
    "prefetch_write_data",
    "overflow_add",
    "overflow_sub",
    "overflow_mul",
    "fixed_point_mul",
    "fixed_point_div",
    "fixed_point_mul_sat",
    "fixed_point_div_sat",
];
pub(crate) fn allocation_free_intrinsic(name: &str) -> bool {
    QUERIES.contains(&name)
        || MATRIX.contains(&name)
        || MACHINE.contains(&name)
        || ATOMIC.contains(&name)
        || SIMD_SAME.contains(&name)
        || SIMD_COMPARE.contains(&name)
        || SIMD_REDUCE.contains(&name)
        || [
            "simd_extract_lsbs",
            "simd_extract_msbs",
            "simd_shuffle",
            "simd_select",
            "simd_to_bits",
            "simd_to_bits_signed",
            "simd_indices",
        ]
        .contains(&name)
}
impl Resolver<'_> {
    pub(super) fn intrinsic_result_type(
        &mut self,
        call: Ref,
        index: usize,
        context: &Substitutions,
    ) -> Option<Type> {
        let source = &self.sources[call.file];
        let callee = Ref {
            node: source.role(call.node, "callee")?,
            ..call
        };
        // Most calls are ordinary definitions. Avoid namespace/alias walks for
        // those, and never classify mutable callbacks from their initializers.
        if let Some(i) = self.symbol(callee) {
            let symbol = &self.symbols[i];
            if symbol.parameter || symbol.result || !symbol.constant || symbol.immutable {
                return None;
            }
            if let Some(node) = symbol.value {
                if !matches!(
                    self.sources[symbol.reference.file].node(node).kind.as_str(),
                    "Ident" | "Selector" | "Paren"
                ) {
                    return None;
                }
            }
        }
        if !self.standard_library(callee, "base:intrinsics") {
            return None;
        }
        let name = self.library_alias_member(callee, "base:intrinsics")?;
        let args = self.sources[call.file]
            .group(call.node, "args")
            .iter()
            .map(|node| Ref {
                node: self.sources[call.file]
                    .role(*node, "value")
                    .unwrap_or(*node),
                ..call
            })
            .collect::<Vec<_>>();
        if QUERIES.contains(&name.as_str()) {
            return (index == 0).then(|| Type::scalar("bool"));
        }
        if index == 0 {
            match name.as_str() {
                "read_cycle_counter" | "read_cycle_counter_frequency" => {
                    return Some(Type::scalar("i64"))
                }
                "return_address" | "frame_address" | "stack_pointer" => {
                    return Some(Type::scalar("rawptr"))
                }
                "address_of_return_address" => {
                    return Some(Type::Pointer(Box::new(Type::scalar("rawptr"))))
                }
                "alloca" => return Some(Type::Pointer(Box::new(Type::scalar("u8")))),
                _ => {}
            }
        }
        let first = *args.first()?;
        let typ = self.specialized_type(first, context);
        let mut base = &typ;
        while let Type::Nominal(_, inner) = base {
            base = inner;
        }
        if MATRIX.contains(&name.as_str()) {
            if index != 0 {
                return None;
            }
            return Some(match name.as_str() {
                "transpose" => {
                    if let Type::Matrix {
                        elem,
                        rows,
                        columns,
                        row_major,
                    } = base
                    {
                        Type::Matrix {
                            elem: elem.clone(),
                            rows: *columns,
                            columns: *rows,
                            row_major: *row_major,
                        }
                    } else {
                        Type::Unknown
                    }
                }
                "hadamard_product" => typ,
                "matrix_flatten" => {
                    if let Type::Matrix {
                        elem,
                        rows: Some(rows),
                        columns: Some(columns),
                        ..
                    } = base
                    {
                        Type::Array {
                            elem: elem.clone(),
                            length: rows.checked_mul(*columns),
                        }
                    } else {
                        Type::Unknown
                    }
                }
                "outer_product" => {
                    let other = args
                        .get(1)
                        .map(|arg| self.specialized_type(*arg, context))
                        .unwrap_or(Type::Unknown);
                    let mut second = &other;
                    while let Type::Nominal(_, inner) = second {
                        second = inner;
                    }
                    if let (Type::Array { elem, length: a }, Type::Array { length: b, .. }) =
                        (base, second)
                    {
                        Type::Matrix {
                            elem: elem.clone(),
                            rows: *a,
                            columns: *b,
                            row_major: false,
                        }
                    } else {
                        Type::Unknown
                    }
                }
                _ => Type::Unknown,
            });
        }
        if ATOMIC.contains(&name.as_str()) {
            if name == "atomic_type_is_lock_free" {
                return (index == 0).then(|| Type::scalar("bool"));
            }
            if name.starts_with("atomic_compare_exchange_") {
                return match index {
                    0 => base.element().cloned(),
                    1 => Some(Type::scalar("bool")),
                    _ => None,
                };
            }
            if name.starts_with("atomic_load")
                || [
                    "atomic_add",
                    "atomic_sub",
                    "atomic_and",
                    "atomic_nand",
                    "atomic_or",
                    "atomic_xor",
                    "atomic_exchange",
                ]
                .iter()
                .any(|n| name == *n || name == format!("{n}_explicit"))
            {
                return (index == 0).then(|| base.element().cloned().unwrap_or(Type::Unknown));
            }
            return None;
        }
        if ["overflow_add", "overflow_sub", "overflow_mul"].contains(&name.as_str()) {
            return match index {
                0 => Some(typ),
                1 => Some(Type::scalar("bool")),
                _ => None,
            };
        }
        if index != 0 {
            return None;
        }
        if name == "simd_indices" {
            return Some(typ);
        }
        if SIMD_SAME.contains(&name.as_str()) {
            return Some(typ);
        }
        if SIMD_REDUCE.contains(&name.as_str()) {
            return Some(base.element().cloned().unwrap_or(Type::Unknown));
        }
        if SIMD_COMPARE.contains(&name.as_str())
            || ["simd_to_bits", "simd_to_bits_signed"].contains(&name.as_str())
        {
            let Type::Vector { elem, length } = base else {
                return Some(Type::Unknown);
            };
            let width = self.simple_size(elem)?.checked_mul(8)?;
            if ![8, 16, 32, 64, 128].contains(&width) {
                return Some(Type::Unknown);
            }
            let signed = name == "simd_to_bits_signed";
            return Some(Type::Vector {
                elem: Box::new(Type::scalar(&format!(
                    "{}{width}",
                    if signed { "i" } else { "u" }
                ))),
                length: *length,
            });
        }
        if ["simd_extract_lsbs", "simd_extract_msbs"].contains(&name.as_str()) {
            return Some(Type::scalar("bit_set"));
        }
        if name == "simd_shuffle" {
            if let Type::Vector { elem, .. } = base {
                return Some(Type::Vector {
                    elem: elem.clone(),
                    length: Some(args.len().saturating_sub(2) as i128),
                });
            }
        }
        if name == "simd_select" {
            return Some(
                args.get(1)
                    .map(|arg| self.specialized_type(*arg, context))
                    .unwrap_or(Type::Unknown),
            );
        }
        Some(match name.as_str() {
            "unaligned_load" | "unaligned_store" | "volatile_load" | "non_temporal_load" => {
                base.element().cloned().unwrap_or(Type::Unknown)
            }
            "count_ones"
            | "count_zeros"
            | "count_leading_zeros"
            | "count_trailing_zeros"
            | "count_leading_ones"
            | "count_trailing_ones"
            | "reverse_bits"
            | "byte_swap"
            | "saturating_add"
            | "saturating_sub"
            | "sqrt"
            | "fused_mul_add"
            | "fixed_point_mul"
            | "fixed_point_div"
            | "fixed_point_mul_sat"
            | "fixed_point_div_sat"
            | "expect"
            | "likely"
            | "unlikely" => typ,
            "ptr_offset" => typ,
            "ptr_sub" => Type::scalar("int"),
            _ => return None,
        })
    }
}
