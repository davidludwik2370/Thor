package parallel_native

import "base:intrinsics"

// The dynamic path needs a constant placeholder of no particular signature.
// `is_static` discards that branch before checking callback arguments.
@(private)
dynamic_callback :: proc() {}

@(private)
call_value :: #force_inline proc(calculate:$F,job:$J,$R:typeid,
    $spread:bool,$is_static:bool,$known:$C,
)->R {
    when is_static {
        when !spread {return compress_values(known(job))}
        else when intrinsics.type_struct_field_count(J)==0 {return compress_values(known())}
        else {return compress_values(known(**job))}
    } else {
        when !spread {return compress_values(calculate(job))}
        else when intrinsics.type_struct_field_count(J)==0 {return compress_values(calculate())}
        else {return compress_values(calculate(**job))}
    }
}

@(private)
call_action :: #force_inline proc(calculate:$F,job:$J,
    $spread:bool,$is_static:bool,$known:$C,
){
    when is_static {
        when !spread {known(job)}
        else when intrinsics.type_struct_field_count(J)==0 {known()}
        else {known(**job)}
    } else {
        when !spread {calculate(job)}
        else when intrinsics.type_struct_field_count(J)==0 {calculate()}
        else {calculate(**job)}
    }
}
