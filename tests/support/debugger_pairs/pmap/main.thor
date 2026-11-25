package main
import "thor:basic"
Job::struct{left,right:int}
calculate::proc(a,b:$T)->(sum,product:T){
    sum=a+b
    product=a*b
    return
}
main::proc(){
    jobs:=[]Job{{2,3}}
    rows:=basic.pmap(jobs,calculate,workers=1)
    defer delete(rows)
    if rows[0].sum!=5 || rows[0].product!=6 {panic("mapped result")}
}
