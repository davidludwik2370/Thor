package main
twice :: proc(value:int) -> int {
    doubled := value * 2
    return doubled
}
main :: proc() {
    values := [3]int{2,4,6}
    selected:int; switch values[len(values)-1] {
        case 6: selected=twice(values[len(values)-1])
        case: selected=0
    }
    if selected!=12 {panic("result")}
}
