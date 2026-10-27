// Small conveniences for Thor scripts. Import with import "thor:basic".
package basic

import "base:intrinsics"
import "core:fmt"
import "core:strconv"
import "core:strings"
import "core:bufio"
import "core:os"
import "core:io"
import "base:runtime"
import "core:slice"

// Print each argument with sep between values, followed by end.
print :: proc(args:..any, sep:=" ", flush:=true, end:="\n"){
    // Write directly: fmt.print's temporary buffer cannot survive flush=false.
    writer := os.to_stream(os.stdout)
    fmt.wprint(writer, ..args, sep=sep, flush=false)
    fmt.wprint(writer, end, flush=flush)
}

type :: proc(_: $T) -> typeid { return typeid_of(T) }
size :: proc(_: $T) -> int    { return size_of  (T) }

asc :: proc(first: $T, rest: ..T) -> bool {
    previous := first
    for v in rest {
        if previous > v do return false
        previous = v
    }
    return true
}

// Equal adjacent values are allowed by both asc and desc.
desc :: proc(first: $T, rest: ..T) -> bool {
    previous := first
    for v in rest {
        if previous < v do return false
        previous = v
    }
    return true
}

ordered :: proc(vals: ..$T) -> bool {
    if len(vals) < 2 do return true
    return asc(vals[0], ..vals[1:]) || desc(vals[0], ..vals[1:])
}

// Return a sorted copy without changing data. Delete the result with allocator.
sorted :: proc(
    data: $T/[]$E,
    less := proc(i, j: E) -> bool { return i < j },
    allocator := context.allocator
) -> []E {
    ret := make([]E, len(data), allocator)
    copy(ret, data)
    slice.sort_by(ret, less)
    return ret
}

_int  :: proc(s: string, base:=0) -> (value: int,  ok: bool) #optional_ok { return strconv.parse_int (s, base) }
_i64 :: proc(s: string, base:=0) -> (value: i64, ok: bool) #optional_ok {
    if base == 0 do return strconv.parse_i64(s)
    return strconv.parse_i64(s, base)
}
_uint :: proc(s: string, base:=0) -> (value: uint, ok: bool) #optional_ok { return strconv.parse_uint(s, base) }
_u64 :: proc(s: string, base:=0) -> (value: u64, ok: bool) #optional_ok {
    if base == 0 do return strconv.parse_u64(s)
    return strconv.parse_u64(s, base)
}
_i128 :: proc(s: string, base:=0) -> (value: i128, ok: bool) #optional_ok {
    if base == 0 do return strconv.parse_i128(s)
    return strconv.parse_i128(s, base)
}
_f32  :: proc(s: string, base:=0) -> (value: f32,  ok: bool) #optional_ok { return strconv.parse_f32 (s) }
_f64  :: proc(s: string, base:=0) -> (value: f64,  ok: bool) #optional_ok { return strconv.parse_f64 (s) }
_bool :: proc(s: string, base:=0) -> (value: bool, ok: bool) #optional_ok { return strconv.parse_bool(s) }

@private
_parse_int :: proc(s: string, $T: typeid, base:=0) -> (value: T, ok: bool)
where intrinsics.type_is_integer(T) #optional_ok {
    x_int := _i128(s, base) or_return
    if i128(min(T)) <= x_int && x_int <= i128(max(T)) {
        value, ok = T(x_int), true
    } return
}

_i32 :: proc(s: string, base:=0) -> (value: i32, ok: bool) #optional_ok { return _parse_int(s, i32, base) }
_i16 :: proc(s: string, base:=0) -> (value: i16, ok: bool) #optional_ok { return _parse_int(s, i16, base) }
_i8  :: proc(s: string, base:=0) -> (value: i8,  ok: bool) #optional_ok { return _parse_int(s, i8,  base) }
_u32 :: proc(s: string, base:=0) -> (value: u32, ok: bool) #optional_ok { return _parse_int(s, u32, base) }
_u16 :: proc(s: string, base:=0) -> (value: u16, ok: bool) #optional_ok { return _parse_int(s, u16, base) }
_u8  :: proc(s: string, base:=0) -> (value: u8,  ok: bool) #optional_ok { return _parse_int(s, u8,  base) }

CONCAT :: intrinsics.concatenate

// Read one line, removing its LF or CRLF terminator. The result is caller-owned,
// including a partial line returned with EOF. Delete it with allocator.
input :: proc(message := "", allocator := context.allocator) -> (res: string, err: io.Error) {
    if len(message) > 0 do fmt.print(message)

    // A local reader must not read ahead and discard the next input line.
    buffer: [1]byte
    r: bufio.Reader
    bufio.reader_init_with_buf(&r, os.to_stream(os.stdin), buffer[:])
    res, err = bufio.reader_read_string(&r, '\n', allocator)
    if len(res) > 0 && res[len(res)-1] == '\n' {
        res = res[:len(res)-1]
        if len(res) > 0 && res[len(res)-1] == '\r' {
            res = res[:len(res)-1]
        }
    }
    return
}

// Read the supplied file into a caller-owned string. Delete it with allocator.
read :: proc(file_path: string, allocator := context.allocator) -> (text: string, ok: bool) #optional_ok {
    data, read_err := os.read_entire_file(file_path, allocator)
    if read_err != nil {
        delete(data, allocator)
        return
    }
    return string(data), true
}

// Create or replace a file's contents. Return the number of bytes written.
write :: proc(file_path, text: string) -> (n: int, err: os.Error) {
    file := os.open(file_path, {.Write, .Create, .Trunc}) or_return
    defer os.close(file)
    return os.write(file, transmute([]byte)(text))
}

// A shallow value copy: referenced storage is shared, just as with assignment.
@require_results
clone :: proc(data: $T, allocator := context.allocator, loc := #caller_location) ->
(t: T, err: runtime.Allocator_Error) #optional_allocator_error {
    new_val_address := new_clone(data, allocator, loc) or_return
    defer free(new_val_address, allocator)
    return new_val_address^, .None
}

// Allocate an element-wise mapped slice. Delete the result with allocator.
fmap :: proc(vals: []$T, func: proc(x: T) -> T, allocator:=context.allocator) ->
(res: []T, err: runtime.Allocator_Error) #optional_allocator_error {
    res = make_slice([]T, len(vals), allocator) or_return

    for v, i in vals {
        res[i] = func(v)
    }

    return
}

// Allocate a slice of views into s. Delete the slice; keep s alive for its views.
split :: proc(s: string, sep := "\n", allocator := context.allocator) ->
(res: []string, err: runtime.Allocator_Error) #optional_allocator_error {
    return strings.split(s, sep, allocator)
}

// Allocate a joined string. Delete the result with allocator.
join :: proc(a: []string, sep:="", allocator := context.allocator) ->
(res: string, err: runtime.Allocator_Error) #no_bounds_check #optional_allocator_error {
    return strings.join(a, sep, allocator)
}
