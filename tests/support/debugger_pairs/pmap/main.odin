package main
import engine "thor:parallel/native"; import "base:runtime"; import "base:intrinsics"
Job::struct{left,right:int}; Result::struct{sum,product:int}; known::intrinsics.procedure_of(calculate(int{},int{}))
calculate::proc(a,b:$T)->(sum,product:T){
    sum=a+b
    product=a*b
    return
}
main::proc(){
    jobs:=[]Job{{2,3}}
    rows:=engine.map_values_static(jobs,known,Result,1,context.allocator,runtime.heap_allocator(),#location(),true)
    defer delete(rows)
    if rows[0].sum!=5 || rows[0].product!=6 {panic("mapped result")}
}
