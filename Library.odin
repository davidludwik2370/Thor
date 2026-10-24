package main

import "base:intrinsics"
import "core:fmt"
import "core:strconv"
import "core:unicode/utf8"
import "core:strings"
import "core:c"
import "core:math"
import "core:math/big"
import "core:math/rand"
import "core:bufio"
import "core:os"
import "core:io"
import "base:runtime"

// Library Functions

print :: proc(args:..any, sep:=" ", flush:=true, end:="\n"){
    fmt.print(..args, sep=sep, flush=flush)
    fmt.print(end)
}
f :: proc(args:..any, allocator := context.allocator) -> string {
    return fmt.aprint(..args, sep="", allocator=allocator)
}

type :: proc(_: $T) -> typeid { return typeid_of(T) }
size :: proc(_: $T) -> int    { return size_of  (T) }

cst :: proc(#any_int n: int) -> string {
    strBuilder: strings.Builder
    numStr := fmt.aprint(n)
	defer delete(numStr)

    end := len(numStr) - 1
    j   := 2*len(numStr) % 3
    for r, i in numStr {
        strings.write_rune(&strBuilder, r)
        if j > 1 && i != end { 
            strings.write_rune(&strBuilder, ',')
            j = 0
        }
        else do j += 1
    }
    return strings.to_string(strBuilder)
}

asc :: proc(first: $T, rest: ..T) -> bool {
    previous := first
    for v in rest {
        if previous > v do return false
        previous = v
    }
    return true
}

desc :: proc(first: $T, rest: ..T) -> bool {
    previous := first
    for v in rest {
        if previous > v do return false
        previous = v
    }
    return true
}

ordered :: proc(vals: ..$T) -> bool {
    return asc(vals) || desc(vals)
}

str :: proc(args: ..any, allocator := context.allocator) -> string { return fmt.aprint(args, allocator=allocator) }

_int  :: proc(s: string, base:=0) -> (value: int,  ok: bool) #optional_ok { return strconv.parse_int (s, base) }
_i64  :: proc(s: string, base:=0) -> (value: i64,  ok: bool) #optional_ok { return strconv.parse_i64 (s, base) }
_uint :: proc(s: string, base:=0) -> (value: uint, ok: bool) #optional_ok { return strconv.parse_uint(s, base) }
_u64  :: proc(s: string, base:=0) -> (value: u64,  ok: bool) #optional_ok { return strconv.parse_u64 (s, base) }
_i128 :: proc(s: string, base:=0) -> (value: i128, ok: bool) #optional_ok { return strconv.parse_i128(s, base) }
_f32  :: proc(s: string, base:=0) -> (value: f32,  ok: bool) #optional_ok { return strconv.parse_f32 (s) }
_f64  :: proc(s: string, base:=0) -> (value: f64,  ok: bool) #optional_ok { return strconv.parse_f64 (s) }
_bool :: proc(s: string, base:=0) -> (value: bool, ok: bool) #optional_ok { return strconv.parse_bool(s) }

_parse_int :: proc(s: string, $T: typeid, base:=0) -> (value: T, ok: bool)
where intrinsics.type_is_integer(T) #optional_ok {
    x_int := _int(s, base) or_return  
    if asc(int(min(T)), x_int, int(max(T))) {
        value, ok = T(x_int), true
    } return
}

_i32 :: proc(s: string, base:=0) -> (value: i32, ok: bool) #optional_ok { return _parse_int(s, i32, base) }
_i16 :: proc(s: string, base:=0) -> (value: i16, ok: bool) #optional_ok { return _parse_int(s, i16, base) }
_i8  :: proc(s: string, base:=0) -> (value: i8,  ok: bool) #optional_ok { return _parse_int(s, i8,  base) }
_u32 :: proc(s: string, base:=0) -> (value: u32, ok: bool) #optional_ok { return _parse_int(s, u32, base) }
_u16 :: proc(s: string, base:=0) -> (value: u16, ok: bool) #optional_ok { return _parse_int(s, u16, base) }
_u8  :: proc(s: string, base:=0) -> (value: u8,  ok: bool) #optional_ok { return _parse_int(s, u8,  base) }

pow_float :: proc(a, b: $T) -> T 
where intrinsics.type_is_float(T) {
    return math.pow(a, b)
}

pow_int :: proc(a, b: $T) -> T 
where intrinsics.type_is_integer(T) { 
    total: T = 1
    for i in 0..<b {
        assert(a <= max(T) / total)
        total *= a
    }
    return total
}

pow :: proc {pow_float, pow_int}

format_commas :: proc(n: int, allocator := context.allocator) -> string {
    context.allocator = allocator
	s := fmt.aprint(n)
    defer delete(s)

	negative := len(s) > 0 && s[0] == '-'
	start := negative ? 1 : 0
	digits := len(s) - start

	commas := (digits - 1) / 3
	out := make([]u8, len(s) + commas)

	i := len(s) - 1
	j := len(out) - 1
	count := 0

	for i >= start {
		if count == 3 {
			out[j] = ','
			j -= 1
			count = 0
		}
		out[j] = s[i]
		i -= 1
		j -= 1
		count += 1
	}

	if negative {
		out[0] = '-'
	}

	return string(out)
}

isin :: proc(x: $T, vals: []T) -> bool {
    for v in vals {
        if x == v do return true
    } return false
}

CONCAT :: intrinsics.concatenate

find_slice :: proc(v: $T, vals: []T) -> (leftmost_index := -1) {
    for val, i in vals {
        if v == val do return i
    } return
}

find_array :: proc(v: $T, vals: [$N]T) -> (leftmost_index := -1) {
    for val, i in vals {
        if v == val do return i
    } return
}

find :: proc {
    find_slice, 
    find_array
}

input :: proc(message := "", allocator := context.allocator) -> (res: string, err: io.Error) {
    if len(message) > 0 do fmt.print(message)

	r: bufio.Reader
	bufio.reader_init(&r, os.to_stream(os.stdin))
	defer bufio.reader_destroy(&r)
    
    res, err = bufio.reader_read_string(&r, '\n')
    return res[:len(res)-1], err
}

read :: proc(file_path: string, allocator := context.allocator) -> (text: string, ok: bool) #optional_ok {
    file, open_err := os.open("some_text.txt")
    defer os.close(file)
    if open_err != nil do return
    data, read_err := os.read_entire_file(file, allocator)
    if read_err != nil do return
    
    return string(data), true   
}

write :: proc(file_path, text: string) -> (n: int, err: os.Error) {
    file := os.open(file_path, {.Write, .Create}) or_return
    defer os.close(file)
    return os.write(file, transmute([]byte)(text))
}

@require_results
clone :: proc(data: $T, allocator := context.allocator, loc := #caller_location) -> 
(t: T, err: runtime.Allocator_Error) #optional_allocator_error {
    new_val_address := new_clone(data, allocator, loc) or_return
    return new_val_address^, .None
}

fmap :: proc(vals: []$T, func: proc(x: T) -> T, allocator:=context.allocator) -> 
(res: []T, err: runtime.Allocator_Error) #optional_allocator_error {
    res = make_slice([]T, len(vals), allocator) or_return
    
    for v, i in vals {
        res[i] = func(v)
    }
    
    return
}

split :: proc(s: string, sep := "\n", allocator := context.allocator) -> 
(res: []string, err: runtime.Allocator_Error) #optional_allocator_error {
    return strings.split(s, sep, allocator)
}

join :: proc(a: []string, sep:="", allocator := context.allocator) -> 
(res: string, err: runtime.Allocator_Error) #no_bounds_check #optional_allocator_error {
    return strings.join(a, sep, allocator)
}