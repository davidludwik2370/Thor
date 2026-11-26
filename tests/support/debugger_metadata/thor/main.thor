package main
capture::proc(#ro values:[]int, value:int,
    expression:>#caller_expression(value),location:>#caller_location)->int {
    result:=values[0]+value
    return result
}
main::proc(){
    values:>[]int{7}
    value:=2
    output:=#runtime capture(values,value+1)
    if output!=10 {panic("result")}
}
