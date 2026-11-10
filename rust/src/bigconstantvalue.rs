//! Exact host operations for logical constants; no machine floating-point path.
use crate::bigvalues::{self, Fraction};
use num_bigint::BigInt;
use num_traits::{One, Signed, ToPrimitive, Zero};
use serde_json::{json, Value};
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
    let comparison = (a.numerator() * b.denominator()).cmp(&(b.numerator() * a.denominator()));
    let boolean = match op {
        "==" => Some(comparison.is_eq()),
        "!=" => Some(!comparison.is_eq()),
        "<" => Some(comparison.is_lt()),
        "<=" => Some(!comparison.is_gt()),
        ">" => Some(comparison.is_gt()),
        ">=" => Some(!comparison.is_lt()),
        _ => None,
    };
    if let Some(value) = boolean {
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
                if b.numerator() > &BigInt::from(8 * 16 * 1024 * 1024) {
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
            if a.numerator().abs() > *a.denominator()
                && exponent > BigInt::from(8 * 16 * 1024 * 1024)
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
