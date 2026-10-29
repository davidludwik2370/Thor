#+feature using-stmt
package main
Point :: struct {x: int}
Record :: struct {points: [2]Point}
read :: proc(using record: Record, i: int) -> int {
    return points[i].x
}
main :: proc() { _ = read({}, 0) }
