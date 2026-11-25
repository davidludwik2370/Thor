package main
render :: fn(n:int) -> string {
    text := f"hello {n}"
    return text
}
main :: proc() {
    result := render(7)
    defer delete(result)
    if result!="hello 7" {panic("result")}
}
