// Optional integration with Odin's fmt registry. The caller owns the map and
// chooses when to install it, preserving native projects' formatter setup.
package thor_format
import "core:fmt"
import "thor:big"

format_value :: proc(fi:^fmt.Info,arg:any,verb:rune)->bool {
    text:string
    if arg.id==typeid_of(big.Integer) && (verb=='v'||verb=='d'||verb=='s') {
        text=big.to_string((cast(^big.Integer)arg.data)^)
    } else if arg.id==typeid_of(big.Rational) && (verb=='v'||verb=='s') {
        text=big.to_string((cast(^big.Rational)arg.data)^)
    } else {return false}
    defer delete(text)
    // Treat the already exact decimal representation as text for padding.
    fmt.fmt_string(fi,text,'s')
    return true
}

// Add these entries to the same caller-owned map as other custom formatters.
// Install it once with fmt.set_user_formatters(&registry), then delete it after
// the last print. This also formats big numbers nested in records/containers.
add_big_formatters :: proc(registry:^map[typeid]fmt.User_Formatter) {
    registry[typeid_of(big.Integer)]=format_value
    registry[typeid_of(big.Rational)]=format_value
}

// Detach before freeing: fmt may be used again by a later defer/finalizer.
// The caller decides the lifetime of the entire shared application registry.
destroy_formatter_registry :: proc(registry:^map[typeid]fmt.User_Formatter) {
    if fmt._user_formatters==registry {fmt._user_formatters=nil}
    delete(registry^)
    registry^={}
}
