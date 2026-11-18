// Compile-time type queries supplied by Thor. Calls are never executed.
package thor_intrinsics

// Inspect a call's full result list. One result keeps its type; multiple results
// become a struct with declared names, or v0/v1/... for unnamed results.
// A call with no results has no result type.
result_type_of :: proc(call: $T) -> typeid {
    #assert(false, "result_type_of must be used as a Thor type query; runtime procedure values are not supported")
    return T
}
