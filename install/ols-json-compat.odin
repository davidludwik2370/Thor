package server

// Compatibility for the pinned upstream OLS JSON writer across reviewed Odin
// SDKs. This does not add Thor syntax or semantic highlighting to OLS.
import "base:intrinsics"
import "core:encoding/json"
import "core:io"

@(private="package")
thor_installer_marshal_json_string :: proc(w: io.Writer, text: string) -> Marshal_Error {
    when intrinsics.type_proc_parameter_type(type_of(io.write_quoted_string), 4) == bool {
        io.write_quoted_string(w, text, '"', nil, true) or_return
    } else {
        err := json.marshal_to_writer(w, text)
        switch value in err {
        case io.Error:
            return value
        case json.Marshal_Data_Error:
            return .Unsupported_Type
        }
    }
    return nil
}
