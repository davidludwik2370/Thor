package main
Cell :: enum {E, X}
Game :: struct {board: [2][2]Cell}
read :: proc(g: Game, y, x: int) -> bool {
    empty := g.board[y][x] == .E
    g := g
    _ = g
    return empty
}
main :: proc() { _ = read({}, 0, 0) }
