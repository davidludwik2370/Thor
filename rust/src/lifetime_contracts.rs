//! Shared reviewed effects, after package identity and callback verification.
//! These describe lifetime effects, not purity or allocation-free guarantees.
use super::*;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Effect {
    Math,
    Format,
    Parse,
    PathRead,
    Endian,
    RandomCollection,
    RandomScalar,
    TextRead,
}

pub(super) fn effect(lib: &str, name: &str) -> Option<Effect> {
    Some(match (lib, name) {
        (
            "core:fmt",
            "print" | "println" | "printf" | "printfln" | "eprint" | "eprintln" | "eprintf"
            | "eprintfln" | "fprint" | "fprintln" | "fprintf" | "fprintfln" | "bprint" | "bprintln"
            | "bprintf" | "bprintfln" | "assertf",
        )
        | ("thor:basic", "print") => Effect::Format,
        ("core:strconv", "parse_int" | "parse_uint" | "parse_i64" | "parse_u64") => Effect::Parse,
        ("core:os", "exists") => Effect::PathRead,
        ("core:encoding/endian", n)
            if n.strip_prefix("unchecked_get_")
                .or_else(|| n.strip_prefix("unchecked_put_"))
                .is_some_and(|s| {
                    matches!(s, "u16le" | "u32le" | "u64le" | "u16be" | "u32be" | "u64be")
                }) =>
        {
            Effect::Endian
        }
        ("core:math/rand", "choice" | "shuffle") => Effect::RandomCollection,
        (
            "core:math/rand",
            "uint32" | "uint64" | "uint128" | "int31" | "int63" | "int127" | "int31_max"
            | "int63_max" | "int127_max" | "int_max" | "uint32_max" | "uint64_max" | "uint128_max"
            | "uint_max" | "int32_range" | "int64_range" | "int128_range" | "int_range"
            | "uint32_range" | "uint64_range" | "uint128_range" | "uint_range" | "float32"
            | "float64" | "float32_range" | "float64_range",
        ) => Effect::RandomScalar,
        ("vendor:raylib", "DrawText" | "MeasureText") => Effect::TextRead,
        (
            "core:math",
            "sin" | "cos" | "tan" | "asin" | "acos" | "atan" | "atan2" | "sinh" | "cosh" | "tanh"
            | "sqrt" | "cbrt" | "exp" | "exp2" | "ln" | "log" | "log2" | "log10" | "pow" | "pow10"
            | "floor" | "ceil" | "trunc" | "round" | "round_even" | "abs" | "sign" | "clamp"
            | "min" | "max" | "lerp" | "mod" | "fmod" | "is_nan" | "is_inf" | "is_finite"
            | "copysign" | "ldexp" | "frexp" | "hypot",
        ) => Effect::Math,
        _ => return None,
    })
}

impl Analysis<'_, '_> {
    pub(super) fn parse_output_argument(
        &mut self,
        at: Ref,
        args: &[Ref],
        f: &Frame,
    ) -> Option<usize> {
        self.argument(args,"n",usize::MAX).or_else(|| {
            args.iter().enumerate().find_map(|(i,arg)| {
                if self.argument_name(*arg).is_some() {return None;}
                let typ=self.typ(*arg,f);
                matches!(base(&typ),Type::Pointer(t) if matches!(base(t),Type::Scalar{name,..} if name=="int")).then_some(i)
            })
        }).or_else(|| {
            let position=self.r.call_signature(at).and_then(|sig|self.r.fields(sig,"params").iter().position(|(n,_)|n=="n")).unwrap_or(2);
            self.argument(args,"n",position)
        })
    }
    pub(super) fn known_generator(&mut self, at: Ref, args: &[Ref], lib: &str, name: &str) -> bool {
        // Use the actual signature: numeric generators take different numbers
        // of ordinary parameters, and named arguments may reorder them.
        let position = self
            .r
            .call_signature(at)
            .and_then(|sig| {
                self.r
                    .fields(sig, "params")
                    .iter()
                    .position(|(n, _)| n == "gen")
            })
            .unwrap_or(
                if lib == "core:math/rand" && matches!(name, "choice" | "shuffle") {
                    1
                } else {
                    usize::MAX
                },
            );
        if let Some(i) = self.argument(args, "gen", position) {
            let actual = self.role(args[i], "value").unwrap_or(args[i]);
            return self.role(actual, "callee").is_some_and(|callee| {
                (crate::evaluation::library_member(self.r, callee, "base:runtime").as_deref()
                    == Some("default_random_generator")
                    && crate::privileges::verified_library(self.r, callee, "base:runtime"))
                    || (crate::evaluation::library_member(self.r, callee, "core:math/rand")
                        .as_deref()
                        == Some("default_random_generator")
                        && crate::privileges::verified_library(self.r, callee, "core:math/rand"))
            });
        }
        !self.custom_random
    }
}
