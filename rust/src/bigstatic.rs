//! Read-only numeric limbs. Runtime reconstruction clones these borrowed views.
use crate::{bigvalues, digest};
use num_bigint::BigInt;
use num_traits::{Signed, ToPrimitive, Zero};
use serde_json::Value;
use std::collections::BTreeMap;

pub struct Backing {
    prefix: String,
    module: String,
    pub declarations: BTreeMap<String, String>,
}
impl Backing {
    pub fn new(prefix: &str, module: &str) -> Self {
        Self {
            prefix: prefix.into(),
            module: module.into(),
            declarations: BTreeMap::new(),
        }
    }
    pub fn view(&mut self, value: &Value) -> bigvalues::Result<String> {
        let number = bigvalues::number(value)?;
        if value["kind"] == "big_rational" {
            if number.numerator().is_zero() {
                return Ok(format!("{}.Rational{{}}", self.module));
            }
            let numerator = self.integer(number.numerator());
            let denominator = self.integer(number.denominator());
            return Ok(format!(
                "{}.compiler_static_rational({numerator},{denominator})",
                self.module
            ));
        }
        Ok(self.integer(number.numerator()))
    }
    fn integer(&mut self, number: &BigInt) -> String {
        if number.is_zero() {
            return format!("{}.Integer{{}}", self.module);
        }
        let mut magnitude = number.abs();
        let mask = (BigInt::from(1u8) << 63) - 1;
        let mut digits = vec![];
        while !magnitude.is_zero() {
            let digit: BigInt = &magnitude & &mask;
            digits.push(digit.to_u64().expect("masked 63-bit digit"));
            magnitude >>= 63;
        }
        digits.resize(digits.len().max(4), 0);
        let canonical = format!("{{\"kind\": \"big_integer\", \"value\": \"{number}\"}}");
        let hash = digest::sha256(canonical.as_bytes());
        let name = format!("{}big_data_{}", self.prefix, &hash[..20]);
        self.declarations.entry(name.clone()).or_insert_with(||{
            let contents=digits.iter().map(u64::to_string).collect::<Vec<_>>().join(",");
            format!("\n@(private=\"file\",rodata)\n{name}:=[{}]{}.compiler_digit{{{contents}}}\n#assert({}.compiler_digit_bits==63,\"Unsupported bigint limb layout for constant data\")\n",digits.len(),self.module,self.module)
        });
        format!(
            "{}.compiler_static_integer({name}[:],{})",
            self.module,
            number.is_negative()
        )
    }
}
