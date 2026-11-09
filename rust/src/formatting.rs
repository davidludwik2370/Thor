//! Odin-format suffix grammar shared in behavior with the reference compiler.
use crate::diagnostic::{Error, Result};

pub const WIDTH_MARKER: &str = "\0width\0";
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FormatSpec {
    pub fill: String,
    pub flags: String,
    pub width: Option<u64>,
    pub dynamic_width: bool,
    pub grouping: bool,
    pub precision: i64,
    pub verb: char,
}
fn integer(digits: &str) -> Result<u64> {
    let digits = digits.trim_start_matches('0');
    let digits = if digits.is_empty() { "0" } else { digits };
    if digits.len() > 19 || digits.len() == 19 && digits > "9223372036854775407" {
        return Err(Error::new("Interpolation width or precision is too large."));
    }
    digits
        .parse()
        .map_err(|_| Error::new("Interpolation width or precision is too large."))
}
pub fn parse(mut text: &str) -> Result<FormatSpec> {
    let explicit = text.starts_with('\'');
    let mut fill = "' '".to_string();
    if explicit {
        let mut end = None;
        let mut escaped = false;
        for (i, ch) in text.char_indices().skip(1) {
            if escaped {
                escaped = false;
                continue;
            }
            if ch == '\\' {
                escaped = true;
                continue;
            }
            if ch == '\n' || ch == '\r' {
                break;
            }
            if ch == '\'' {
                end = Some(i + 1);
                break;
            }
        }
        let end =
            end.ok_or_else(|| Error::new("Interpolation fill must be a single-quoted rune."))?;
        fill = text[..end].into();
        text = &text[end..];
    }
    if text.contains(['<', '>']) {
        return Err(Error::new("Interpolation no longer uses < or >: use - for left alignment, or omit alignment for right alignment."));
    }
    let mut flags = String::new();
    while let Some(flag) = text.chars().next().filter(|ch| "+ -0#^".contains(*ch)) {
        if flags.contains(flag) {
            return Err(Error::new(format!(
                "Duplicate interpolation flag '{flag}'."
            )));
        }
        flags.push(flag);
        text = &text[flag.len_utf8()..];
    }
    if flags.contains('^') && flags.contains(['-', '0']) {
        return Err(Error::new(
            "Centered interpolation (^) cannot be combined with - or 0.",
        ));
    }
    if explicit && flags.contains('0') {
        return Err(Error::new("A quoted fill cannot be combined with the numeric 0 flag; use '0' for literal fill or 0 for numeric padding."));
    }
    let dynamic_width = text.starts_with(WIDTH_MARKER);
    let width = if dynamic_width {
        text = &text[WIDTH_MARKER.len()..];
        None
    } else {
        let end = text.bytes().take_while(u8::is_ascii_digit).count();
        let amount = integer(&text[..end])?;
        text = &text[end..];
        Some(amount)
    };
    let grouping = text.starts_with(',');
    if grouping {
        text = &text[1..];
    }
    let mut precision = -1;
    if text.starts_with('.') {
        text = &text[1..];
        if text.starts_with(WIDTH_MARKER) {
            return Err(Error::new(
                "Dynamic precision is not supported; use a literal precision such as .2f.",
            ));
        }
        let end = text.bytes().take_while(u8::is_ascii_digit).count();
        precision = integer(&text[..end])? as i64;
        text = &text[end..];
        if text.is_empty() {
            return Err(Error::new(
                "Interpolation precision requires an explicit verb, e.g. .2f or .10s.",
            ));
        }
    }
    if text.contains(WIDTH_MARKER) {
        return Err(Error::new(
            "A dynamic width belongs after flags and before grouping, precision, and the verb.",
        ));
    }
    if !text.is_ascii() {
        return Err(Error::new(
            "Interpolation fill must be a single-quoted rune, e.g. '界'^5.",
        ));
    }
    let verb = if text.is_empty() {
        'v'
    } else {
        text.chars().next().unwrap()
    };
    if text.len() > 1 || !"vwTtdibozxXcrUqfFeEgGhHspmM%".contains(verb) {
        let identifier = !text.is_empty()
            && text.chars().enumerate().all(|(i, ch)| {
                ch == '_' || ch.is_ascii_alphabetic() || i > 0 && ch.is_ascii_digit()
            });
        if identifier && width.unwrap_or(0) == 0 && precision < 0 {
            return Err(Error::new(
                "A variable width needs nested braces, e.g. f\"{value:{width}}\".",
            ));
        }
        return Err(Error::new("Unsupported interpolation format; expected [quoted fill][flags][width][,][.precision][verb]. Use {expression} for a dynamic width; argument indices are not supported."));
    }
    Ok(FormatSpec {
        fill,
        flags,
        width,
        dynamic_width,
        grouping,
        precision,
        verb,
    })
}
impl FormatSpec {
    pub fn options(&self, width: &str) -> String {
        let align = if self.flags.contains('^') {
            "Center"
        } else if self.flags.contains('-') {
            "Left"
        } else {
            "Right"
        };
        format!("grouping={}, width={}, align=.{align}, fill={}, verb='{}', precision={}, plus={}, space={}, zero={}, alternate={}",
            self.grouping,if self.dynamic_width{width.into()}else{self.width.unwrap_or(0).to_string()},self.fill,self.verb,self.precision,
            self.flags.contains('+'),self.flags.contains(' '),self.flags.contains('0'),self.flags.contains('#'))
    }
}
