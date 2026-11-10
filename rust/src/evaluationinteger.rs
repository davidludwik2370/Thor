//! Python's exact untyped integer folding, bounded only at the result boundary.
//! Native typed arithmetic keeps its normal helper execution path.
use super::*;
use num_bigint::BigInt;
use num_traits::{One, Signed, ToPrimitive, Zero};
use serde_json::json;

impl Scheduler<'_> {
    pub(super) fn constant_integer(&self, node: Ref) -> Option<BigInt> {
        self.constant_integer_seen(node, &mut BTreeSet::new())
    }
    fn constant_integer_seen(&self, node: Ref, seen: &mut BTreeSet<Ref>) -> Option<BigInt> {
        if !seen.insert(node) {
            return None;
        }
        if let Some(value) = self.value(node).filter(|value| value["kind"] == "integer") {
            return value["value"].as_str()?.parse().ok();
        }
        if let Some(index) = self.resolver.symbol(node) {
            let symbol = &self.resolver.symbols[index];
            if let Some(value) = self
                .specializations
                .bindings
                .get(&(symbol.reference, symbol.name.clone()))
                .filter(|binding| !binding.typ.native_float_constant())
                .and_then(|binding| binding.value.as_ref())
                .filter(|value| value["kind"] == "integer")
            {
                return value["value"].as_str()?.parse().ok();
            }
        }
        if let Some(value) = self.resolver.constant_int(node) {
            return Some(value.into());
        }
        let source = self.source(node);
        let ast = source.node(node.node);
        match ast.kind.as_str() {
            "Literal" if ast.op == "integer" => {
                let text = source.text(node.node).replace('_', "");
                let (radix, digits) = [("0x", 16), ("0b", 2), ("0o", 8), ("0d", 10)]
                    .iter()
                    .find_map(|(prefix, radix)| {
                        text.strip_prefix(prefix).map(|digits| (*radix, digits))
                    })
                    .unwrap_or((10, &text));
                BigInt::parse_bytes(digits.as_bytes(), radix)
            }
            "Paren" | "Unary" => {
                let value = self.constant_integer_seen(self.role(node, "expr")?, seen)?;
                match (ast.kind.as_str(), ast.op.as_str()) {
                    ("Paren", _) | ("Unary", "+") => Some(value),
                    ("Unary", "-") => Some(-value),
                    _ => None,
                }
            }
            "Ident" | "Selector" => {
                let symbol = &self.resolver.symbols[self.resolver.symbol(node)?];
                if !symbol.constant || symbol.immutable {
                    return None;
                }
                self.constant_integer_seen(
                    Ref {
                        file: symbol.reference.file,
                        node: symbol.value?,
                    },
                    seen,
                )
            }
            "Binary" => {
                let left =
                    self.constant_integer_seen(self.role(node, "left")?, &mut seen.clone())?;
                let right =
                    self.constant_integer_seen(self.role(node, "right")?, &mut seen.clone())?;
                match ast.op.as_str() {
                    "+" => Some(left + right),
                    "-" => Some(left - right),
                    "*" => Some(left * right),
                    _ => None,
                }
            }
            _ => None,
        }
    }
    pub(super) fn integer_power(&mut self, node: Ref) -> Result<Option<Value>> {
        if self.kind(node) != "Binary"
            || self.source(node).node(node.node).op != "**"
            || !self.resolver.type_of(node).untyped()
        {
            return Ok(None);
        }
        let (Some(left), Some(right)) = (
            self.role(node, "left")
                .and_then(|arg| self.constant_integer(arg)),
            self.role(node, "right")
                .and_then(|arg| self.constant_integer(arg)),
        ) else {
            return Ok(None);
        };
        let value = power(&left, &right).map_err(|message| self.fail(node, message))?;
        Ok(Some(json!({"kind":"integer","value":value.to_string()})))
    }
}
fn power(left: &BigInt, right: &BigInt) -> std::result::Result<BigInt, &'static str> {
    if right.is_negative() {
        return Err("Integer power requires a nonnegative exponent; use floating-point operands for negative powers.");
    }
    let one = BigInt::one();
    if right.is_zero() {
        return Ok(one);
    }
    if left.is_zero() {
        return Ok(BigInt::zero());
    }
    if left == &one {
        return Ok(one);
    }
    if left == &-one {
        return Ok(if right.bit(0) {
            -BigInt::one()
        } else {
            BigInt::one()
        });
    }
    let overflow = "Power constant exceeds Odin's 128-bit integer range.";
    if right > &BigInt::from(128) || left.bits() > 128 {
        return Err(overflow);
    }
    let exponent = right.to_u32().ok_or(overflow)?;
    let value = left.pow(exponent);
    if value < -(BigInt::one() << 127usize) || value >= (BigInt::one() << 128usize) {
        return Err(overflow);
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_powers_keep_signed_and_unsigned_boundaries() {
        for (left, right, expected) in [
            ("2", "100", Some("1267650600228229401496703205376")),
            ("2", "127", Some("170141183460469231731687303715884105728")),
            (
                "-2",
                "127",
                Some("-170141183460469231731687303715884105728"),
            ),
            ("2", "128", None),
            ("-2", "128", None),
            ("-3", "127", None),
            ("2", "-1", None),
        ] {
            let result = power(&left.parse().unwrap(), &right.parse().unwrap());
            let actual = result.ok().map(|value| value.to_string());
            let expected = expected.map(str::to_string);
            assert_eq!(actual, expected);
        }
    }
    #[test]
    fn trivial_bases_accept_arbitrarily_large_nonnegative_exponents() {
        let exponent = BigInt::parse_bytes(
            b"100000000000000000000000000000000000000000000000000000000000000001",
            10,
        )
        .unwrap();
        for (base, expected) in [(0, 0), (1, 1), (-1, -1)] {
            let result = power(&BigInt::from(base), &exponent);
            let actual = result.ok();
            let expected = Some(BigInt::from(expected));
            assert_eq!(actual, expected);
        }
        let huge = BigInt::one() << 1000usize;
        let result = power(&huge, &BigInt::zero());
        let actual = result.ok();
        let expected = Some(BigInt::one());
        assert_eq!(actual, expected);
    }
}
