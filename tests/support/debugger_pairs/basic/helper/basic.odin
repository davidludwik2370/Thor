package basic
import "core:os"
import "core:fmt"
import format "thor:format"

print :: proc(args:..any, sep:=" ", flush:=true, end:="\n"){
    // Write directly: fmt.print's temporary buffer cannot survive flush=false.
    writer := os.to_stream(os.stdout)
    for arg,i in args {
        if i>0 do fmt.wprint(writer,sep,flush=false)
        format.write_value(writer,arg)
    }
    fmt.wprint(writer, end, flush=flush)
}
