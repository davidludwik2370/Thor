package main
twice :: proc(value:int) -> int {
    doubled := value * 2
    return doubled
}
main :: proc() {
    values := [2,4,6]
    selected := switch values[-1] {
        case 6: twice(values[-1])
        case: 0
    }
    if selected!=12 {panic("result")}
}
