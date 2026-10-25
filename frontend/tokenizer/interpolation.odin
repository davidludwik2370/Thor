package thor_tokenizer

Interpolation_Part :: struct {
    literal: bool,
    start, end: int,
    format_start, format_end: int,
}

// Resume scanning an original source buffer without changing byte positions.
seek :: proc(t: ^Tokenizer, offset: int) {
    t.read_offset = 0
    t.offset = 0
    t.line_offset = 0
    t.line_count = 1
    t.ch = ' '
    advance_rune(t)
    for t.offset < offset && t.ch >= 0 { advance_rune(t) }
    t.insert_semicolon = false
}

// Fields use the ordinary Thor lexer, including recursively nested f-strings.
// Only a top-level colon separates formatting; parentheses protect ternaries.
scan_interpolated :: proc(t: ^Tokenizer) -> (string, []Interpolation_Part) {
    start := t.offset
    advance_rune(t) // f
    quote := t.ch
    advance_rune(t)
    parts := make([dynamic]Interpolation_Part)
    literal_start := t.offset
    for t.ch >= 0 {
        if t.ch == quote {
            append(&parts, Interpolation_Part{literal=true, start=literal_start, end=t.offset})
            advance_rune(t)
            return t.src[start:t.offset], parts[:]
        }
        if quote == '"' && t.ch == '\n' {
            error(t, start, "Interpolated string literal not terminated")
            break
        }
        if t.ch == '\\' && quote == '"' {
            advance_rune(t)
            scan_escape(t)
            continue
        }
        if (t.ch == '{' || t.ch == '}') && peek_rune(t, 0) == t.ch {
            advance_rune(t); advance_rune(t)
            continue
        }
        if t.ch == '}' { error(t, t.offset, "Unmatched '}' in interpolated string; use '}}' for a literal brace") }
        if t.ch != '{' { advance_rune(t); continue }
        append(&parts, Interpolation_Part{literal=true, start=literal_start, end=t.offset})
        advance_rune(t)
        field := Interpolation_Part{start=t.offset, format_start=-1, format_end=-1}
        depth := 0
        for {
            token := scan(t)
            if token.kind == .EOF { error(t, field.start, "Unterminated interpolation field"); break }
            if depth == 0 && token.kind == .Colon {
                field.end = token.pos.offset
                field.format_start = t.offset
                // The fill is an ordinary rune literal. Its contents may
                // include braces, the outer delimiter, or escaped quotes.
                if t.ch == '\'' {
                    advance_rune(t)
                    _ = scan_rune(t)
                }
                for t.ch >= 0 && t.ch != '}' && t.ch != quote { advance_rune(t) }
                field.format_end = t.offset
                if t.ch != '}' { error(t, field.start, "Unterminated interpolation format"); break }
                advance_rune(t)
                break
            }
            if depth == 0 && token.kind == .Close_Brace { field.end = token.pos.offset; break }
            #partial switch token.kind {
            case .Open_Brace, .Open_Paren, .Open_Bracket: depth += 1
            case .Close_Brace, .Close_Paren, .Close_Bracket: depth -= 1
            }
            if depth < 0 { error(t, token.pos.offset, "Unbalanced interpolation expression"); break }
        }
        append(&parts, field)
        literal_start = t.offset
    }
    error(t, start, "Interpolated string literal not terminated")
    return t.src[start:t.offset], parts[:]
}
