"""Static Odin-style interpolation suffixes; type checking remains with Odin."""
import re

VERBS = frozenset('vwTtdibozxXcrUqfFeEgGhHspmM%')


def parse_format(text, error):
    def literal_integer(digits):
        digits=digits.lstrip('0') or '0'
        if len(digits)>19 or len(digits)==19 and digits>'9223372036854775407':
            error('Interpolation width or precision is too large.')
        return int(digits)

    explicit_fill=text.startswith("'")
    fill = "' '"
    if text.startswith("'"):
        literal = re.match(r"'(?:\\.|[^'\\\r\n])*'", text)
        if not literal:
            error('Interpolation fill must be a single-quoted rune.')
        fill, text = literal[0], text[literal.end():]
    if '<' in text or '>' in text:
        error("Interpolation no longer uses < or >: use - for left alignment, or omit alignment for right alignment.")
    flags = ''
    while text and text[0] in '+ -0#^':
        flag, text = text[0], text[1:]
        if flag in flags:
            error(f"Duplicate interpolation flag '{flag}'.")
        flags += flag
    if '^' in flags and ('-' in flags or '0' in flags):
        error('Centered interpolation (^) cannot be combined with - or 0.')
    if explicit_fill and '0' in flags:
        error("A quoted fill cannot be combined with the numeric 0 flag; use '0' for literal fill or 0 for numeric padding.")
    match = re.match(r'[0-9]*', text)
    width, text = literal_integer(match[0]), text[match.end():]
    grouping = text.startswith(',')
    if grouping:
        text = text[1:]
    precision = -1
    if text.startswith('.'):
        match = re.match(r'\.([0-9]*)', text)
        precision, text = literal_integer(match[1]), text[match.end():]
        if not text:
            error('Interpolation precision requires an explicit verb, e.g. .2f or .10s.')
    if text and not text.isascii():
        error("Interpolation fill must be a single-quoted rune, e.g. '界'^5.")
    verb = text or 'v'
    if len(verb) != 1 or verb not in VERBS:
        error('Unsupported interpolation format; expected [quoted fill][flags][width][,][.precision][verb]. Dynamic widths and argument indices are not supported.')
    align = 'Center' if '^' in flags else 'Left' if '-' in flags else 'Right'
    options = dict(grouping=str(grouping).lower(), width=str(width), align='.'+align,
                   fill=fill, verb=repr(verb), precision=str(precision),
                   plus=str('+' in flags).lower(), space=str(' ' in flags).lower(),
                   zero=str('0' in flags).lower(), alternate=str('#' in flags).lower())
    return ', '.join(f'{key}={value}' for key, value in options.items())
