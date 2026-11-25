package main
get::proc()->(int,int,bool){return 0,0,false}
fallback::proc(x:int)->int{
    value:=x+1
    return value
}
main::proc(){
    _,value,ok:=get(); if !ok {value=fallback(7)}
    after:=value+1
    if after!=9{panic("fallback result")}
}
