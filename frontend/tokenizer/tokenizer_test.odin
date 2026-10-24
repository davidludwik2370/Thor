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
    }
    for source in sources {
        ours: Tokenizer
        original: native.Tokenizer
        init(&ours, source, "source.thor")
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
