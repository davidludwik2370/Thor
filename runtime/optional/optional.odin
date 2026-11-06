// Internal support for Thor's postfix or_panic operator.
package optional

import "base:intrinsics"
import "base:runtime"
import "core:fmt"

succeeded :: proc "contextless" (status: $S) -> bool {
    when intrinsics.type_is_boolean(S) { return bool(status) }
    else when intrinsics.type_has_nil(S) { return status == nil }
    else { #panic("or_panic requires an optional result or a final boolean/error value comparable to nil") }
}

failure :: proc(status: $S, summary: string, loc: runtime.Source_Code_Location, $Contextless: bool) -> ! {
    // Formatting runs only on failure. Use the temporary allocator so a failing
    // user allocator does not prevent the diagnostic from being constructed.
    message := summary
    when !ODIN_NO_RTTI {
        message = fmt.aprintf("%s\n  error: %v", summary, status, allocator=context.temp_allocator)
        if len(message) == 0 { message = summary }
    }
    // Odin suppresses this prefix when assertions are disabled, but or_panic's
    // explicit source location remains part of its release-build contract.
    when ODIN_DISABLE_ASSERT {
        runtime.print_caller_location(loc)
        runtime.print_string(" ")
    }
    when Contextless { runtime.panic_contextless(message, loc) }
    else { runtime.panic(message, loc) }
}

failure_contextless :: proc "contextless" (status: $S, summary: string, loc: runtime.Source_Code_Location) -> ! {
    context = runtime.default_context()
    failure(status, summary, loc, true)
}
