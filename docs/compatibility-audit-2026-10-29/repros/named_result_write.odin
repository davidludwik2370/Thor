package main
make_board :: proc() -> (board: [2]int) {
    i := 0
    board[i] = 7
    return
}
main :: proc() { _ = make_board() }
