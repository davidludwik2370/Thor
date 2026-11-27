//! Portable canonical arbitrary-precision contents; no native addresses.
use num_bigint::BigInt;
use num_traits::{One, Signed, Zero};
use serde_json::{json, Value};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fraction {
    numerator: BigInt,
    denominator: BigInt,
}
pub type Result<T> = std::result::Result<T, String>;
impl Fraction {
    pub fn numerator(&self) -> &BigInt {
        &self.numerator
    }
    pub fn denominator(&self) -> &BigInt {
        &self.denominator
    }
    pub fn new(mut numerator: BigInt, mut denominator: BigInt) -> Result<Self> {
        if denominator.is_zero() {
            return Err("A big-number divisor must not be zero.".into());
        }
        if denominator.is_negative() {
            numerator = -numerator;
            denominator = -denominator;
        }
        let mut a = numerator.abs();
        let mut b = denominator.clone();
        while !b.is_zero() {
            let remainder = &a % &b;
            a = b;
            b = remainder;
        }
        numerator /= &a;
        denominator /= a;
        Ok(Self {
            numerator,
            denominator,
        })
    }
    pub fn integer(value: BigInt) -> Self {
        Self {
            numerator: value,
            denominator: BigInt::one(),
        }
    }
}
fn integer(text: &Value, positive: bool) -> Result<BigInt> {
    let Some(text) = text.as_str() else {
        return Err("Invalid canonical big-number digits.".into());
    };
    let Some(value) = BigInt::parse_bytes(text.as_bytes(), 10) else {
        return Err("Invalid canonical big-number digits.".into());
    };
    if value.to_string() != text || positive && !value.is_positive() {
        return Err("Invalid canonical big-number digits.".into());
    }
    Ok(value)
}
pub fn number(value: &Value) -> Result<Fraction> {
    if value["kind"] == "big_integer" {
        return Ok(Fraction::integer(integer(&value["value"], false)?));
    }
    if value["kind"] != "big_rational" {
        return Err("Invalid big-number contents kind.".into());
    }
    let numerator = integer(&value["numerator"], false)?;
    let denominator = integer(&value["denominator"], true)?;
    let reduced = Fraction::new(numerator.clone(), denominator.clone())?;
    if reduced.numerator != numerator || reduced.denominator != denominator {
        return Err("A Rational result must be reduced with a positive denominator.".into());
    }
    Ok(reduced)
}
pub fn packet(kind: &str, number: &Fraction) -> Result<Value> {
    if kind == "big_integer" {
        if !number.denominator.is_one() {
            return Err("An Integer constant must represent a whole number exactly.".into());
        }
        Ok(json!({"kind":kind,"value":number.numerator.to_string()}))
    } else if kind == "big_rational" {
        Ok(
            json!({"kind":kind,"numerator":number.numerator.to_string(),"denominator":number.denominator.to_string()}),
        )
    } else {
        Err("Invalid big-number contents kind.".into())
    }
}
const DEFAULT_LITERAL_DIGITS: u64 = 16_000_000;
const LIMIT_VARIABLE: &str = "THOR_MAX_LITERAL_DIGITS";

pub fn literal_limit() -> Result<u64> {
    static LIMIT: std::sync::OnceLock<Result<u64>> = std::sync::OnceLock::new();
    LIMIT
        .get_or_init(|| match std::env::var(LIMIT_VARIABLE) {
            Err(std::env::VarError::NotPresent) => Ok(DEFAULT_LITERAL_DIGITS),
            value => value
                .ok()
                .and_then(|s| s.parse::<u64>().ok())
                .filter(|n| *n > 0)
                .ok_or_else(|| format!("{LIMIT_VARIABLE} must be a positive whole number.")),
        })
        .clone()
}

// Describe the value using only space proportional to the source. Runtime
// literal checks also use this representation, without expanding a BigInt.
pub(crate) struct ExactLiteral {
    negative: bool,
    digits: String,
    radix: u32,
    scale: i128,
}
impl ExactLiteral {
    pub(crate) fn is_whole(&self) -> bool {
        self.scale <= 0
    }
    pub(crate) fn is_zero(&self) -> bool {
        self.digits.is_empty()
    }

    pub(crate) fn check_size(&self) -> Result<()> {
        let limit = literal_limit()?;
        let numerator = self.digits.len() as i128 + (-self.scale).max(0);
        let denominator = 1 + self.scale.max(0);
        let required = numerator.max(denominator);
        if required > i128::from(limit) {
            return Err(format!("Exact numeric literal requires {required} digits in base {} before reduction; the limit is {limit}. Raise {LIMIT_VARIABLE} to allow it.", self.radix));
        }
        Ok(())
    }
}
pub(crate) fn parse_literal(text: &str) -> Result<ExactLiteral> {
    let text = text.replace('_', "");
    let (negative, digits) = text.strip_prefix('-').map_or_else(
        || (false, text.strip_prefix('+').unwrap_or(&text)),
        |value| (true, value),
    );
    if let Some(prefix) = digits.get(..2).map(str::to_ascii_lowercase) {
        let radix = match prefix.as_str() {
            "0b" => Some(2),
            "0o" => Some(8),
            "0d" => Some(10),
            "0z" => Some(12),
            "0x" => Some(16),
            _ => None,
        };
        if let Some(radix) = radix {
            let value = &digits[2..];
            if value.is_empty() || !value.chars().all(|c| c.is_digit(radix)) {
                return Err("Invalid numeric literal.".into());
            }
            return Ok(ExactLiteral {
                negative,
                digits: value.trim_start_matches('0').into(),
                radix,
                scale: 0,
            });
        }
    }
    let mut parts = digits.split(['e', 'E']);
    let mantissa = parts.next().unwrap();
    let exponent = parts
        .next()
        .map(str::parse::<i64>)
        .transpose()
        .map_err(|_| "Invalid numeric literal.")?
        .unwrap_or(0);
    if parts.next().is_some() {
        return Err("Invalid numeric literal.".into());
    }
    let mut parts = mantissa.split('.');
    let whole = parts.next().unwrap();
    let fractional = parts.next().unwrap_or("");
    if parts.next().is_some()
        || whole.is_empty() && fractional.is_empty()
        || !whole
            .bytes()
            .chain(fractional.bytes())
            .all(|ch| ch.is_ascii_digit())
    {
        return Err("Invalid numeric literal.".into());
    }
    let coefficient = format!("{whole}{fractional}");
    let significant = coefficient.trim_start_matches('0');
    let digits = significant.trim_end_matches('0');
    let scale = if digits.is_empty() {
        0
    } else {
        fractional.len() as i128 - i128::from(exponent) - (significant.len() - digits.len()) as i128
    };
    Ok(ExactLiteral {
        negative,
        digits: digits.into(),
        radix: 10,
        scale,
    })
}

pub fn literal(text: &str) -> Result<Fraction> {
    let value = parse_literal(text)?;
    value.check_size()?;
    if value.is_zero() {
        return Ok(Fraction::integer(BigInt::zero()));
    }
    let mut numerator = BigInt::parse_bytes(value.digits.as_bytes(), value.radix)
        .ok_or("Invalid numeric literal.")?;
    if value.negative {
        numerator = -numerator;
    }
    let mut exponent = value.scale.unsigned_abs();
    let mut power = BigInt::one();
    let mut factor = BigInt::from(10u8);
    while exponent != 0 {
        if exponent & 1 != 0 {
            power *= &factor;
        }
        exponent >>= 1;
        if exponent != 0 {
            factor = &factor * &factor;
        }
    }
    if value.scale > 0 {
        Fraction::new(numerator, power)
    } else {
        Ok(Fraction::integer(numerator * power))
    }
}
pub fn contains(value: &Value) -> bool {
    match value["kind"].as_str() {
        Some("big_integer" | "big_rational") => true,
        Some("specialized") => contains(&value["value"]),
        Some("sequence" | "bitset") => value["values"]
            .as_array()
            .is_some_and(|items| items.iter().any(contains)),
        Some("record" | "enum_array") => value["fields"]
            .as_array()
            .is_some_and(|fields| fields.iter().any(|field| contains(&field[1]))),
        Some("union") => {
            value["variant"]
                .as_i64()
                .is_some_and(|variant| variant >= 0)
                && contains(&value["value"])
        }
        _ => false,
    }
}
