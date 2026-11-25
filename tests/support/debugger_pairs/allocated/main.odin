package main; import "core:fmt"
render :: proc(n:int) -> string {
    text := fmt.aprintf("hello %d",n)
    return text
}
main :: proc() {
    result := render(7)
    defer delete(result)
    if result!="hello 7" {panic("result")}
}
