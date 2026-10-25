#+test
package thor_tokenizer

import native "core:odin/tokenizer"
import "core:testing"

@(test)
native_token_and_position_compatibility :: proc(t: ^testing.T) {
    context.allocator = context.temp_allocator
    sources := []string{
        "package main\n// 😀 comment\nx := 0xff + 1_000\ny := `raw`\n",
        "package main\r\n/* nested /* comment */ */\np :: proc(x: $T) -> T where T == int {return x}\r\n",
        "package main\nfn := 3; set := [2]int{1,2}; when true {x:=1}\n",
        "package main\nx :> f(); y: proc(x: int)->int > f\n",
        "package main\ns := \"é😀\\n\"; r := '界'; p := &s; _ = p^\n",
        "#+build linux, darwin\npackage main\nimport \"core:fmt\"\n",
        "package main\np^=value;p^~=value;_=p^(value);a~=b;_=a~(b)&c\n",
    }
    for source in sources {
      for thor in ([2]bool{false,true}) {
        ours: Tokenizer
        original: native.Tokenizer
        init(&ours, source, "source.thor")
        ours.thor = thor
        native.init(&original, source, "source.thor")
        for {
            a, b := scan(&ours), native.scan(&original)
            testing.expect_value(t, a.kind, b.kind)
            testing.expect_value(t, a.text, b.text)
            testing.expect_value(t, a.pos.offset, b.pos.offset)
            testing.expect_value(t, a.pos.line, b.pos.line)
            testing.expect_value(t, a.pos.column, b.pos.column)
            if a.kind == .EOF { break }
        }
        testing.expect_value(t, ours.error_count, 0)
      }
    }
}

@(test)
thor_interpolation_nested_quotes_and_original_positions :: proc(t:^testing.T){
    context.allocator=context.temp_allocator
    source:="f\"😀 {echo(\"nested\")} {1:'}'>3}\"\nnext ~= other"
    lexer:Tokenizer
    init(&lexer,source,"source.thor")
    lexer.thor=true
    lexer.flags={.Insert_Semicolon}
    token:=scan(&lexer)
    testing.expect_value(t,token.kind,.String)
    testing.expect_value(t,token.text,source[:len(source)-len("\nnext ~= other")])
    testing.expect_value(t,scan(&lexer).kind,.Semicolon)
    next:=scan(&lexer)
    testing.expect_value(t,next.text,"next")
    testing.expect_value(t,next.pos.line,2)
    testing.expect_value(t,next.pos.column,1)
    testing.expect_value(t,scan(&lexer).text,"~=")
    testing.expect_value(t,lexer.error_count,0)
}
