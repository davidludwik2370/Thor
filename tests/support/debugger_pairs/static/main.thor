package main
GLOBAL :> []int{1,2}
main::proc(){
    values :> []int{7,8}
    total:=values[0]+values[1]+GLOBAL[0]
    if total!=16 {panic("literal values")}
}
