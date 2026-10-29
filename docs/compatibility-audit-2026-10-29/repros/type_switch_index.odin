package main
Possibilities :: [3]bool
Cell :: union {int, Possibilities}
Board :: [2][2]Cell
reduce :: proc(board: ^Board, y, x, n: int) {
    switch &v in board[y][x] {
    case int:
        return
    case Possibilities:
        v[n] = false
    }
}
main :: proc() { board: Board; reduce(&board, 0, 0, 0) }
