// 😀 original header
package main
Record::struct{x:int}
read::proc(using record:Record)->int{
    value:=x+1
    return value
}
main::proc(){
    record:=Record{4}
    using record
    result:=read(record)
    if result!=5||x!=4 {panic("result")}
}
