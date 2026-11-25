package main
import "base:runtime"
observe::proc(loc:runtime.Source_Code_Location){
    line:=loc.line
    if line!=11{panic("branch location")}
}
main::proc(){
    defer observe(#branch_location)
    value:=7
    _=value
    return
}
