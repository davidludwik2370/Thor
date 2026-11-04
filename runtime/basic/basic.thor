// Small conveniences for Thor scripts. Import with import "thor:basic".
package basic

import "base:intrinsics"
import "core:fmt"
import "core:strconv"
import "core:strings"
import "core:unicode/utf8"
import "core:bufio"
import "core:os"
import "core:io"
import "base:runtime"
import "core:slice"
import "core:math/rand"
import "thor:sets"
import "thor:big"
import "thor:format"
import "thor:graph"

// Clone supported data graphs, preserving cycles and shared/interior references.
// Pass a pointer to the source; the result is a heap root. Check Deep_Error and
// release the whole result once with deepfree, using the same allocator.
deepclone :: graph.deepclone
deepfree  :: graph.deepfree
Deep_Error :: graph.Error

// Allocating, shallow collection conversions. Free each returned container;
// to_slice returns keys in unspecified order. Both accept allocator=.
to_set   :: sets.to_set
to_slice :: sets.to_slice

// Print each argument with sep between values, followed by end.
print :: proc(args:..any, sep:=" ", flush:=true, end:="\n"){
    // Write directly: fmt.print's temporary buffer cannot survive flush=false.
    writer := os.to_stream(os.stdout)
    for arg,i in args {
        if i>0 do fmt.wprint(writer,sep,flush=false)
        format.write_value(writer,arg)
    }
    fmt.wprint(writer, end, flush=flush)
}

type :: proc(_: $T) -> typeid { return typeid_of(T) }
size :: proc(_: $T) -> int    { return size_of  (T) }

swap :: proc(a, b: ^$T) { a^, b^ = b^, a^ }

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

// Read one line, removing its LF or CRLF terminator. The result is caller-owned,
// including a partial line returned with EOF. Delete it with allocator.
input :: proc(message := "", allocator := context.allocator) -> (res: string, err: io.Error) {
    if len(message) > 0 do fmt.print(message)

    // A local reader must not read ahead and discard the next input line.
    buffer: [1]byte
    r: bufio.Reader
    bufio.reader_init_with_buf(&r, os.to_stream(os.stdin), buffer[:])
    // Keep capacity until the temporary buffer is freed. Odin's read_string
    // returns a slice of a dynamic buffer without retaining its allocation
    // size; trimming that string further also loses the newline's byte count.
    // An exact-size copy makes the caller's delete valid for sized allocators.
    line: [dynamic]byte
    line.allocator = allocator
    defer delete(line)
    for {
        c, read_err := bufio.reader_read_byte(&r)
        if read_err != nil { err = read_err; break }
        if c == '\n' {
            if len(line) > 0 && line[len(line)-1] == '\r' {
                resize(&line, len(line)-1)
            }
            break
        }
        count, allocation_err := append(&line, c)
        if allocation_err != nil || count != 1 { return "", .Unknown }
    }
    allocation_err: runtime.Allocator_Error
    res, allocation_err = strings.clone(string(line[:]), allocator)
    if allocation_err != nil { return "", .Unknown }
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


// Preserve input order in a caller-owned slice. Elements are shallow copies;
// delete the result with allocator, keeping borrowed element storage alive.
filter :: proc(vals: []$T, f: proc(x: T)->bool, allocator:=context.allocator) -> []T {
    ret := make([dynamic]T, allocator)

    for val in vals {
        if f(val) do append(&ret, val)
    }

    // Match capacity to length before handing the allocation to the caller.
    // The allocator may be able to shrink in place, avoiding a copy.
    shrink_ok, err := shrink(&ret)
    if shrink_ok && err == nil do return ret[:]

    // Preserve correct allocation sizes if the allocator cannot shrink.
    result := make([]T, len(ret), allocator)
    copy(result, ret[:])
    delete(ret)
    return result
}

// Empty inputs yield T's zero value. Arithmetic keeps Odin's overflow rules.
sum :: proc(vals: []$T) -> T
where intrinsics.type_is_numeric(T) || T==Integer || T==Rational {
    when T==Integer || T==Rational {return big.sum(vals)}
    else {
    total: T
    for val in vals {
        total += val
    }
    return total
    }
}

// Half-open integer ranges. Wrong direction, equal bounds, or a zero step
// produce an empty slice. Delete nonempty results with allocator.
range_end :: proc(#any_int end: int, allocator:=context.allocator) -> []int {
    return range_start_end_step(0, end, 1, allocator)
}

range_start_end :: proc(#any_int start, end: int, allocator:=context.allocator) -> []int {
    return range_start_end_step(start, end, 1, allocator)
}

range_start_end_step :: proc(#any_int start, end, step: int, allocator:=context.allocator) -> []int {
    if step == 0 do return {}
    if step > 0 && start >= end do return {}
    if step < 0 && start <= end do return {}

    // Widen before arithmetic so even min(int), max(int), and min(int) as
    // the step are safe. Count rounds up for either direction of travel.
    distance := i128(end) - i128(start)
    stride := i128(step)
    if stride < 0 {
        distance = -distance
        stride = -stride
    }
    count := 1 + (distance - 1) / stride
    assert(count <= i128(max(int) / size_of(int)), "range: result is too large to allocate")

    ret := make([]int, int(count), allocator)

    for &v, i in ret do v = int(i128(start) + i128(i) * i128(step))

    return ret
}

range :: proc {
    range_end,
    range_start_end,
    range_start_end_step
}

// Returns one Unicode scalar value as a rune, without allocating.
chr :: proc(code_point: $T) -> rune
where intrinsics.type_is_integer(T) {
    // Validate before narrowing to rune, including for 128-bit input types.
    assert(code_point >= 0 && u128(code_point) <= u128(utf8.MAX_RUNE), "chr: code point must be in 0..0x10ffff")
    r := rune(code_point)
    assert(utf8.valid_rune(r), "chr: expected a valid Unicode scalar value")
    return r
}

ord_string :: proc(s: string) -> int {
    r, width := utf8.decode_rune(s)
    assert(width > 0 && width == len(s) && !(r == utf8.RUNE_ERROR && width == 1),
           "ord: expected exactly one valid UTF-8 code point")
    return int(r)
}

ord_rune :: proc(r: rune) -> int {
    assert(utf8.valid_rune(r), "ord: expected a valid Unicode scalar value")
    return int(r)
}

ord_bytes :: proc(bytes: []u8) -> int {
    assert(len(bytes) == 1, "ord: expected exactly one byte")
    return int(bytes[0])
}

// Strings and runes yield a Unicode code point; a byte slice yields its byte.
ord :: proc {ord_string, ord_rune, ord_bytes}

// Returns a value in [lo, hi], with both bounds inclusive.
// Omitted bounds default to min(T) and max(T); float bounds must be finite.
random :: proc(
    $T: typeid,
    lo: T = 0,
    hi: T = 0,
    gen := context.random_generator,
    _lo_expr := #caller_expression(lo),
    _hi_expr := #caller_expression(hi),
) -> T where intrinsics.type_is_integer(T) || intrinsics.type_is_float(T) {

    // An omitted argument has an empty caller expression. This distinguishes
    // omission from an explicit zero while keeping bounds typed as T.
    lower := (_lo_expr == "") ? min(T) : lo
    upper := (_hi_expr == "") ? max(T) : hi
    assert(lower <= upper, "random: lo must be less than or equal to hi")

    when intrinsics.type_is_integer(T) {
        if lower == upper do return lower

        // Unsigned arithmetic wraps, so signed ranges work too. A zero count
        // represents the full 128-bit range, whose size cannot fit in u128.
        count := u128(upper) - u128(lower) + 1
        offset: u128
        if count == 0 {
            offset = rand.uint128(gen)
        } else {
            offset = rand.uint128_max(count, gen)
        }
        return T(u128(lower) + offset)
    } else {
        assert(lower >= min(T) && upper <= max(T), "random: float bounds must be finite")
        if lower == upper do return lower

        // Sample a fraction in [0, 1], including both endpoints. Adding 1 to
        // the floating-point upper bound would change the requested range.
        PRECISION :: 11 when size_of(T) == 2 else 24 when size_of(T) == 4 else 53
        STEPS :: u64(1) << PRECISION
        fraction := f64(rand.uint64_range(0, STEPS + 1, gen)) / f64(STEPS)
        a, b := f64(lower), f64(upper)
        value: f64
        if a < 0 && b > 0 {
            // Avoid overflow in b - a for ranges such as min(f64)..max(f64).
            value = (1 - fraction) * a + fraction * b
        } else {
            value = a + fraction * (b - a)
        }
        return T(clamp(value, a, b))
    }
}


// Return a caller-owned table string; delete it with context.allocator.
// Widths count terminal cells, excluding the single space on either side.
// Zero means unlimited. Wrapping preserves graphemes and indents continuations
// by two spaces (reduced for narrow columns). Headers are always left-aligned;
// integer, float, complex and quaternion values are right-aligned.
// Tags such as `Price` override field names. Positive width limits must fit
// each displayed character; the returned table includes a final newline.
table :: proc(data: []$T, max_col_width:=0, float_format:="%.2f") -> string
where intrinsics.type_is_struct(T) {
    assert(max_col_width >= 0, "table: max_col_width must be nonnegative")

    // Normalize control characters so embedded tabs/escapes cannot move borders.
    // The whole field tag, when nonempty, is the column's display name.
    normalize :: proc(text: string) -> string {
        out: strings.Builder
        for r, i in text {
            switch r {
            case '\t': strings.write_string(&out, "    ")
            case '\r':
                if i+1 == len(text) || text[i+1] != '\n' {
                    strings.write_byte(&out, '\n')
                }
            case '\n': strings.write_byte(&out, '\n')
            case:
                if r < 32 || r == 127 {
                    fmt.sbprintf(&out, "\\x%02x", u32(r))
                } else {
                    strings.write_rune(&out, r)
                }
            }
        }
        return strings.to_string(out)
    }

    measure :: proc(text: string) -> (width, widest: int) {
        current := 0
        it := utf8.decode_grapheme_iterator_make(text)
        for part, grapheme in utf8.decode_grapheme_iterate(&it) {
            if part == "\n" {
                width = max(width, current)
                current = 0
            } else {
                current += grapheme.width
                widest = max(widest, grapheme.width)
            }
        }
        return max(width, current), widest
    }

    // offset=-1 means exhausted. Keep a trailing explicit newline as an empty
    // final line, but consume a newline directly after an exactly full line.
    next_line :: proc(text: string, offset: ^int, limit: int) -> (part: string, width: int) {
        start := offset^
        if start < 0 do return
        it := utf8.decode_grapheme_iterator_make(text[start:])
        for value, grapheme in utf8.decode_grapheme_iterate(&it) {
            end := start + grapheme.byte_index
            if value == "\n" {
                offset^ = end + len(value)
                return text[start:end], width
            }
            if width + grapheme.width > limit {
                offset^ = end
                return text[start:end], width
            }
            width += grapheme.width
        }
        offset^ = -1
        return text[start:], width
    }

    spaces :: proc(out: ^strings.Builder, count: int) {
        for _ in 0..<count { strings.write_byte(out, ' ') }
    }

    border :: proc(out: ^strings.Builder, widths: []int) {
        strings.write_byte(out, '+')
        for width in widths {
            for _ in 0..<width+2 { strings.write_byte(out, '-') }
            strings.write_byte(out, '+')
        }
        strings.write_byte(out, '\n')
    }

    info := runtime.type_info_base(type_info_of(T)).variant.(runtime.Type_Info_Struct)
    columns := int(info.field_count)
    if columns == 0 do return ""

    widths := make([]int, columns)
    widest := make([]int, columns)
    numeric := make([]bool, columns)
    offsets := make([]int, columns)
    cells := make([]string, (len(data)+1)*columns)
    defer delete(widths)
    defer delete(widest)
    defer delete(numeric)
    defer delete(offsets)
    defer {
        for cell in cells { delete(cell) }
        delete(cells)
    }

    for col in 0..<columns {
        name := info.names[col]
        if info.tags[col] != "" { name = info.tags[col] }
        cells[col] = normalize(name)

        floating := false
        if info.types[col].id==typeid_of(Integer) || info.types[col].id==typeid_of(Rational) {numeric[col]=true}
        #partial switch _ in runtime.type_info_base(info.types[col]).variant {
        case runtime.Type_Info_Integer, runtime.Type_Info_Complex, runtime.Type_Info_Quaternion:
            numeric[col] = true
        case runtime.Type_Info_Float:
            numeric[col], floating = true, true
        }

        for row in 0..<len(data) {
            value := any{rawptr(uintptr(&data[row]) + info.offsets[col]), info.types[col].id}
            text := floating ? fmt.aprintf(float_format, value) : format.value_string(value)
            cells[(row+1)*columns+col] = normalize(text)
            delete(text)
        }
        for row in 0..=len(data) {
            width, glyph_width := measure(cells[row*columns+col])
            widths[col] = max(widths[col], width)
            widest[col] = max(widest[col], glyph_width)
        }
        widths[col] = max(1, widths[col])
        if max_col_width > 0 {
            assert(widest[col] <= max_col_width,
                   "table: max_col_width is narrower than a displayed character")
            widths[col] = min(widths[col], max_col_width)
        }
    }

    out: strings.Builder
    border(&out, widths)
    for row in 0..=len(data) {
        for col in 0..<columns { offsets[col] = 0 }
        line := 0
        for {
            more := false
            strings.write_byte(&out, '|')
            for col in 0..<columns {
                active := offsets[col] >= 0
                indent := 0
                if active && line > 0 {
                    indent = min(2, max(0, widths[col]-widest[col]))
                }
                part, used := next_line(cells[row*columns+col], &offsets[col], widths[col]-indent)
                padding := widths[col] - indent - used
                right := row > 0 && numeric[col]
                strings.write_byte(&out, ' ')
                spaces(&out, indent)
                if right { spaces(&out, padding) }
                strings.write_string(&out, part)
                if !right { spaces(&out, padding) }
                strings.write_string(&out, " |")
                more = more || offsets[col] >= 0
            }
            strings.write_byte(&out, '\n')
            if !more do break
            line += 1
        }
        if row == 0 { border(&out, widths) }
    }
    if len(data) > 0 { border(&out, widths) }
    return strings.to_string(out)
}
/*
format: when called with max_col_width=7
+---------+---------+---------+
| field_a | field_b | field_c |
+---------+---------+---------+
| data 1  | asd     | asdfa   |
| dta 2   | asdff   | asdfsd  |
| jdkn    | asdfasa | asd     | // spillover example, indents 2 spaces
|         |   dfa   |         |
| asasdf  | f       | pf      |
+---------+---------+---------+
*/
