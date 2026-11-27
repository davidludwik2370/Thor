//! Exact host operations for logical constants; no machine floating-point path.
use crate::bigvalues::{self, Fraction};
use num_bigint::BigInt;
use num_traits::{One, Signed, ToPrimitive, Zero};
use serde_json::{json, Value};
const MAX_RESULT_BITS: u64 = 16 * 1024 * 1024 * 8;
fn add(a: &Fraction, b: &Fraction, subtract: bool) -> bigvalues::Result<Fraction> {
    let right = b.numerator() * a.denominator();
    Fraction::new(
        a.numerator() * b.denominator() + if subtract { -right } else { right },
        a.denominator() * b.denominator(),
    )
}
fn mul(a: &Fraction, b: &Fraction) -> bigvalues::Result<Fraction> {
    Fraction::new(
        a.numerator() * b.numerator(),
        a.denominator() * b.denominator(),
    )
}
fn divide(a: &Fraction, b: &Fraction) -> bigvalues::Result<Fraction> {
    Fraction::new(
        a.numerator() * b.denominator(),
        a.denominator() * b.numerator(),
    )
}
pub fn unary(value: &Value, op: &str) -> bigvalues::Result<Value> {
    let kind = value["kind"]
        .as_str()
        .ok_or("Invalid big-number contents kind.")?;
    let a = bigvalues::number(value)?;
    let result = match op {
        "+" => a,
        "-" => Fraction::new(-a.numerator(), a.denominator().clone())?,
        "~" if kind == "big_integer" => Fraction::integer(!a.numerator()),
        _ => return Err("Unsupported constant numeric operation.".into()),
    };
    bigvalues::packet(kind, &result)
}
pub fn binary(left: &Value, right: &Value, op: &str) -> bigvalues::Result<Value> {
    let kind = left["kind"]
        .as_str()
        .ok_or("Invalid big-number contents kind.")?;
    if right["kind"] != kind {
        return Err(
            "Integer/Rational operands must have matching types; use an explicit conversion."
                .into(),
        );
    }
    let a = bigvalues::number(left)?;
    let b = bigvalues::number(right)?;
    if matches!(op, "==" | "!=" | "<" | "<=" | ">" | ">=") {
        let comparison = (a.numerator() * b.denominator()).cmp(&(b.numerator() * a.denominator()));
        let value = match op {
            "==" => comparison.is_eq(),
            "!=" => !comparison.is_eq(),
            "<" => comparison.is_lt(),
            "<=" => !comparison.is_gt(),
            ">" => comparison.is_gt(),
            _ => !comparison.is_lt(),
        };
        return Ok(json!({"kind":"bool","value":value}));
    }
    if matches!(op, "&" | "|" | "~" | "&~" | "<<" | ">>") && kind != "big_integer" {
        return Err("Rational does not support bitwise operators or shifts.".into());
    }
    if matches!(op, "/" | "%" | "%%") && b.numerator().is_zero() {
        return Err("A big-number divisor must not be zero.".into());
    }
    let result = match op {
        "+" => add(&a, &b, false)?,
        "-" => add(&a, &b, true)?,
        "*" => mul(&a, &b)?,
        "/" => {
            let ratio = divide(&a, &b)?;
            if kind == "big_integer" {
                Fraction::integer(ratio.numerator() / ratio.denominator())
            } else {
                ratio
            }
        }
        "%" | "%%" => {
            let ratio = divide(&a, &b)?;
            let mut quotient = ratio.numerator() / ratio.denominator();
            if op == "%%"
                && ratio.numerator().is_negative()
                && !(ratio.numerator() % ratio.denominator()).is_zero()
            {
                quotient -= 1;
            }
            add(&a, &mul(&Fraction::integer(quotient), &b)?, true)?
        }
        "&" => Fraction::integer(a.numerator() & b.numerator()),
        "|" => Fraction::integer(a.numerator() | b.numerator()),
        "~" => Fraction::integer(a.numerator() ^ b.numerator()),
        "&~" => Fraction::integer(a.numerator() & !b.numerator()),
        "<<" | ">>" => {
            if b.numerator().is_negative() {
                return Err("Integer shift counts must be nonnegative.".into());
            }
            if a.numerator().is_zero() {
                Fraction::integer(BigInt::zero())
            } else if op == "<<" {
                if a.numerator().bits() > MAX_RESULT_BITS ||
                    b.numerator() > &BigInt::from(MAX_RESULT_BITS - a.numerator().bits()) {
                    return Err("Compile-time result exceeds 16 MiB.".into());
                }
                Fraction::integer(
                    a.numerator()
                        << b.numerator()
                            .to_usize()
                            .ok_or("Compile-time result exceeds 16 MiB.")?,
                )
            } else if let Some(count) = b.numerator().to_usize() {
                Fraction::integer(a.numerator() >> count)
            } else {
                Fraction::integer(if a.numerator().is_negative() {
                    -BigInt::one()
                } else {
                    BigInt::zero()
                })
            }
        }
        "**" => {
            if !b.denominator().is_one() {
                return Err("Rational powers require whole exponents.".into());
            }
            let negative = b.numerator().is_negative();
            let exponent = b.numerator().abs();
            if kind == "big_integer" && negative {
                return Err("Integer powers require a nonnegative exponent; use Rational for negative powers.".into());
            }
            if a.numerator().is_zero() && negative {
                return Err("A big-number divisor must not be zero.".into());
            }
            // The fraction is reduced, so powers of its numerator and
            // denominator cannot cancel. Each k-bit component contributes
            // at least (k - 1) bits of growth per factor in either direction.
            let bits = a.numerator().bits().max(a.denominator().bits());
            if bits > 1 && exponent > BigInt::from((MAX_RESULT_BITS - 1) / (bits - 1))
            {
                return Err("Compile-time result exceeds 16 MiB.".into());
            }
            if a.numerator().is_zero() && !exponent.is_zero() {
                Fraction::integer(BigInt::zero())
            } else if a.numerator().abs() == *a.denominator() {
                Fraction::integer(
                    if a.numerator().is_negative() && (&exponent % 2u8) == BigInt::one() {
                        -BigInt::one()
                    } else {
                        BigInt::one()
                    },
                )
            } else {
                let exponent = exponent
                    .to_u32()
                    .ok_or("Compile-time result exceeds 16 MiB.")?;
                let (n, d) = (a.numerator().pow(exponent), a.denominator().pow(exponent));
                if n.bits().max(d.bits()) > MAX_RESULT_BITS {
                    return Err("Compile-time result exceeds 16 MiB.".into());
                }
                if negative {
                    Fraction::new(d, n)?
                } else {
                    Fraction::new(n, d)?
                }
            }
        }
        _ => return Err("Unsupported constant numeric operation.".into()),
    };
    bigvalues::packet(kind, &result)
}
