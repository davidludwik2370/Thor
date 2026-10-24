// Stable core token aliases keep the Thor lexer compatible with Odin's AST.
package thor_tokenizer

import "core:strings"
import odin "core:odin/tokenizer"

Token :: odin.Token
Pos :: odin.Pos
Token_Kind :: odin.Token_Kind

tokens := [Token_Kind.COUNT]string {
	"Invalid",
	"EOF",
	"Comment",
	"FileTag",

	"",
	"identifier",
	"integer",
	"float",
	"imaginary",
	"rune",
	"string",
	"",

	"",
	"=",
	"!",
	"#",
	"@",
	"$",
	"^",
	"?",
	"+",
	"-",
	"*",
	"/",
	"%",
	"%%",
	"&",
	"|",
	"~",
	"&~",
	"<<",
	">>",

	"&&",
	"||",

	"**",

	"",
	"+=",
	"-=",
	"*=",
	"/=",
	"%=",
	"%%=",
	"&=",
	"|=",
	"~=",
	"&~=",
	"<<=",
	">>=",
	"&&=",
	"||=",
	"",

	"++",
	"--",
	"->",
	"---",

	"",
	"==",
	"!=",
	"<",
	">",
	"<=",
	">=",
	"",

	"(",
	")",
	"[",
	"]",
	"{",
	"}",
	":",
	";",
	".",
	",",
	"..",
	"..<",
	"..=",
	"",

	"",
	"import",
	"foreign",
	"package",
	"typeid",
	"when",
	"where",
	"if",
	"else",
	"for",
	"switch",
	"in",
	"not_in",
	"do",
	"case",
	"break",
	"continue",
	"fallthrough",
	"defer",
	"return",
	"proc",
	"struct",
	"union",
	"enum",
	"bit_set",
	"bit_field",
	"map",
	"dynamic",
	"auto_cast",
	"cast",
	"transmute",
	"distinct",
	"using",
	"context",
	"or_else",
	"or_return",
	"or_break",
	"or_continue",
	"asm",
	"matrix",
	"",
}

custom_keyword_tokens: []string


is_newline :: proc(tok: Token) -> bool {
	return tok.kind == .Semicolon && tok.text == "\n"
}


token_to_string :: proc(tok: Token) -> string {
	if is_newline(tok) {
		return "newline"
	}
	return to_string(tok.kind)
}

to_string :: proc(kind: Token_Kind) -> string {
	if .Invalid <= kind && kind < .COUNT {
		return tokens[kind]
	}
	if .B_Custom_Keyword_Begin < kind {
		n := int(u16(kind)-u16(Token_Kind.B_Custom_Keyword_Begin))
		if n < len(custom_keyword_tokens) {
			return custom_keyword_tokens[n]
		}
	}

	return "Invalid"
}

is_literal  :: proc(kind: Token_Kind) -> bool {
	return .B_Literal_Begin  < kind && kind < .B_Literal_End
}
is_operator :: proc(kind: Token_Kind) -> bool {
	#partial switch kind {
	case .B_Operator_Begin ..= .B_Operator_End:
		return true
	case .In, .Not_In:
		return true
	case .If:
		return true
	}
	return false
}
is_assignment_operator :: proc(kind: Token_Kind) -> bool {
	return .B_Assign_Op_Begin < kind && kind < .B_Assign_Op_End || kind == .Eq
}
is_keyword :: proc(kind: Token_Kind) -> bool {
	switch {
	case .B_Keyword_Begin < kind && kind < .B_Keyword_End:
		return true
	case .B_Custom_Keyword_Begin < kind:
		return true
	}
	return false
}
