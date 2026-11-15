package test_internal

import "core:math"
import "core:testing"

// %% operator (remainder/floored modulo)
// remainder = x - y * floor(x / y)

// reference floor_mod constructed from trunc division;
// y * q may wrap, but two's complement arithmetic is mod 2^n,
// so the wrapped x - y * q still gives the remainder
@(private="file")
floor_mod :: proc(x, y: $T) -> T {
	q := x / y
	if x % y != 0 && ((x < 0) != (y < 0)) {
		q -= 1
	}
	return x - y * q
}

@(test)
modmod_i8_exhaustive :: proc(t: ^testing.T) {
	for i in -128..=127 {
		for j in -128..=127 {
			if j == 0 { continue }
			// min(T) %% -1 == 0 is tested in modmod_exception,
			// floor_mod ref itself would result in exception here
			if i == -128 && j == -1 { continue }
			x, y := i8(i), i8(j)
			got := x %% y
			want := floor_mod(x, y)
			testing.expectf(t, got == want, "%v %%%% %v == %v, want %v", x, y, got, want)
		}
	}
}

// alternative reference floor mod using f64;
// exact for i32 x and y
@(private="file")
floor_mod_via_f64 :: proc(x, y: i32) -> i32 {
	return i32(f64(x) - f64(y)*math.floor(f64(x)/f64(y)))
}

@(test)
modmod_i32 :: proc(t: ^testing.T) {
	vals: [dynamic]i32
	defer delete(vals)
	append(&vals, 0, 1, -1, 2, -2, 3, -3, max(i32), max(i32)-1, min(i32), min(i32)+1)
	for shift in u32(3)..=30 {
		p := i32(1) << shift
		append(&vals, p-1, p, p+1, -p+1, -p, -p-1)
	}
	for x in vals {
		for y in vals {
			if y == 0 { continue }
			if x == min(i32) && y == -1 { continue } // covered in modmod_exception
			got := x %% y
			testing.expectf(t, got == floor_mod(x, y),
				"%v %%%% %v == %v, want %v", x, y, got, floor_mod(x, y))
			testing.expectf(t, got == floor_mod_via_f64(x, y),
				"%v %%%% %v == %v, f64 ref %v", x, y, got, floor_mod_via_f64(x, y))
		}
	}
}

@(test)
modmod_const_divisors :: proc(t: ^testing.T) {
	check :: proc(t: ^testing.T, x, got, want: $T, loc := #caller_location) {
		testing.expectf(t, got == want, "x=%v: got %v, want %v", x, got, want, loc = loc)
	}
	for i in -3000..=3000 {
		x := i32(i)
		check(t, x, x %% 7, floor_mod(x, i32(7)))
		check(t, x, x %% 1000, floor_mod(x, i32(1000)))
		check(t, x, x %% -42, floor_mod(x, i32(-42)))
		check(t, x, x %% -1, 0)
		check(t, x, x %% max(i32), floor_mod(x, max(i32)))
		check(t, x, x %% min(i32), floor_mod(x, min(i32)))
	}
	for i in -200..=200 {
		x := i64(i) * 1_000_000_007
		check(t, x, x %% 97,  floor_mod(x, i64(97)))
		check(t, x, x %% -97, floor_mod(x, i64(-97)))
	}
}

@(test)
modmod_const_fold :: proc(t: ^testing.T) {
	// must match the folded constants (arbitrary precision)
	{
		x, y: i8 = 126, 127
		testing.expect_value(t, x %% y, 126 %% 127)
		testing.expect_value(t, x %% y, i8(126))
	}
	{
		x, y := max(i32) - 1, max(i32)
		testing.expect_value(t, x %% y, (max(i32) - 1) %% max(i32))
		testing.expect_value(t, x %% y, max(i32) - 1)
	}
	{
		x, y := max(i64) - 1, max(i64)
		testing.expect_value(t, x %% y, (max(i64) - 1) %% max(i64))
		testing.expect_value(t, x %% y, max(i64) - 1)
	}
	{
		x, y := min(i32) + 1, min(i32)
		testing.expect_value(t, x %% y, (min(i32) + 1) %% min(i32))
		testing.expect_value(t, x %% y, min(i32) + 1)
	}
	// sign of remainder must match sign of divisor
	{
		x, y := -7, 3
		testing.expect_value(t, x %% y, -7 %% 3)
		testing.expect_value(t, x %% y, 2)
	}
	{
		x, y := 7, -3
		testing.expect_value(t, x %% y, 7 %% -3)
		testing.expect_value(t, x %% y, -2)
	}
	{
		x, y := -7, -3
		testing.expect_value(t, x %% y, -7 %% -3)
		testing.expect_value(t, x %% y, -1)
	}
}

@(test)
modmod_128 :: proc(t: ^testing.T) {
	BIG :: i128(1) << 100
	{
		x, y: i128 = 5, -7
		testing.expect_value(t, x %% y, -2)
		testing.expect_value(t, x %% y, floor_mod(x, y))

		x = 3
		testing.expect_value(t, x %% -BIG, 3 - BIG)
		testing.expect_value(t, x %% -BIG, floor_mod(x, -BIG))

		y = -BIG
		testing.expect_value(t, x %% y, 3 - BIG)

		x = BIG + 3
		testing.expect_value(t, x %% BIG, 3)

		x, y = max(i128) - 1, max(i128)
		testing.expect_value(t, x %% y, max(i128) - 1)
	}
	{
		x := max(u128) - 1
		testing.expect_value(t, x %% max(u128), max(u128) - 1)
	}
}

@(test)
modmod_vec :: proc(t: ^testing.T) {
	// these should vectorize
	x := [4]i32{max(i32) - 1, -7, 7, 126}
	y := [4]i32{max(i32), 3, -3, 127}
	r := x %% y
	for i in 0..<4 {
__thor_7d7320d0a4c2_access_4246_4250 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_4246_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_4246_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_4246_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_4246_base^),__thor_7d7320d0a4c2_access_4246_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",162,24,"modmod_vec"},checked=true)]
}

__thor_7d7320d0a4c2_access_4264_4268 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_4264_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_4264_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_4264_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_4264_base^),__thor_7d7320d0a4c2_access_4264_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",162,42,"modmod_vec"},checked=true)]
}

__thor_7d7320d0a4c2_access_4270_4274 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_4270_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_4270_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_4270_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_4270_base^),__thor_7d7320d0a4c2_access_4270_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",162,48,"modmod_vec"},checked=true)]
}

__thor_7d7320d0a4c2_access_4327_4331 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_4327_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_4327_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_4327_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_4327_base^),__thor_7d7320d0a4c2_access_4327_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",163,53,"modmod_vec"},checked=true)]
}

__thor_7d7320d0a4c2_access_4333_4337 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_4333_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_4333_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_4333_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_4333_base^),__thor_7d7320d0a4c2_access_4333_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",163,59,"modmod_vec"},checked=true)]
}

__thor_7d7320d0a4c2_access_4339_4343 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_4339_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_4339_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_4339_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_4339_base^),__thor_7d7320d0a4c2_access_4339_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",163,65,"modmod_vec"},checked=true)]
}

__thor_7d7320d0a4c2_access_4355_4359 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_4355_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_4355_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_4355_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_4355_base^),__thor_7d7320d0a4c2_access_4355_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",163,81,"modmod_vec"},checked=true)]
}

__thor_7d7320d0a4c2_access_4361_4365 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_4361_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_4361_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_4361_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_4361_base^),__thor_7d7320d0a4c2_access_4361_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",163,87,"modmod_vec"},checked=true)]
}

		testing.expectf(t, __thor_7d7320d0a4c2_access_4246_4250(&(r), i, type_of(__thor_7d7320d0a4c2_sequence.element(r))) == floor_mod(__thor_7d7320d0a4c2_access_4264_4268(&(x), i, type_of(__thor_7d7320d0a4c2_sequence.element(x))), __thor_7d7320d0a4c2_access_4270_4274(&(y), i, type_of(__thor_7d7320d0a4c2_sequence.element(y)))),
			"[4]i32 idx %v: %v %%%% %v == %v, want %v", i, __thor_7d7320d0a4c2_access_4327_4331(&(x), i, type_of(__thor_7d7320d0a4c2_sequence.element(x))), __thor_7d7320d0a4c2_access_4333_4337(&(y), i, type_of(__thor_7d7320d0a4c2_sequence.element(y))), __thor_7d7320d0a4c2_access_4339_4343(&(r), i, type_of(__thor_7d7320d0a4c2_sequence.element(r))), floor_mod(__thor_7d7320d0a4c2_access_4355_4359(&(x), i, type_of(__thor_7d7320d0a4c2_sequence.element(x))), __thor_7d7320d0a4c2_access_4361_4365(&(y), i, type_of(__thor_7d7320d0a4c2_sequence.element(y)))))
	}
}

// this seems to prevent folding at least at -o:minimal
@(private="file")
not_const :: #force_no_inline proc(v: $T) -> T { return v }

@(test)
modmod_exception :: proc(t: ^testing.T) {
	// spec requires this explicitly
	// min(T) %% -1 == 0
	check :: proc(t: ^testing.T, $T: typeid, loc := #caller_location) {
		x, y := not_const(min(T)), not_const(T(-1))
		testing.expectf(t, x %% y == 0,  "min(%v) %%%% -1 (rt divisor) == %v, want 0", typeid_of(T), x %% y,  loc = loc)
		testing.expectf(t, x %% -1 == 0, "min(%v) %%%% -1 (const divisor) == %v, want 0", typeid_of(T), x %% -1, loc = loc)
	}
	check(t, i8)
	check(t, i16)
	check(t, i32)
	check(t, i64)
	check(t, i128)
	{
		// vector path
		x := not_const([4]i32{min(i32), 0, -7, 5})
		y := not_const([4]i32{-1, -1, -1, -1})
		testing.expect_value(t, x %% y, [4]i32{0, 0, 0, 0})
	}
}

@(test)
modmod_unsigned :: proc(t: ^testing.T) {
	// for unsigned types %% must match %
	{
		x, y: u32 = max(u32) - 1, max(u32)
		testing.expect_value(t, x %% y, max(u32) - 1)
		testing.expect_value(t, x %% y, x % y)
	}
	{
		x, y: u8 = 5, 3
		testing.expect_value(t, x %% y, 2)
		testing.expect_value(t, x %% y, x % y)
	}
}

@(test)
modmod_vec_wide :: proc(t: ^testing.T) {
	// a wider array takes the other lowering: [4]i32 emits `srem <4 x i32>`, [16]i32 emits
	// scalar `srem i32`. This reaches the call site `modmod_vec` does not
	x: [16]i32
	y: [16]i32
	for i in 0..<16 {
__thor_7d7320d0a4c2_address_5787_5791 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_5787_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_5787_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return &(__thor_7d7320d0a4c2_access_5787_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_5787_base^),__thor_7d7320d0a4c2_access_5787_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",215,5,"modmod_vec_wide"},checked=true)]
}

__thor_7d7320d0a4c2_address_5807_5811 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_5807_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_5807_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return &(__thor_7d7320d0a4c2_access_5807_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_5807_base^),__thor_7d7320d0a4c2_access_5807_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",216,5,"modmod_vec_wide"},checked=true)]
}

		(__thor_7d7320d0a4c2_address_5787_5791(&(x), i, type_of(&(x[i]))))^ = i32(i) - 8
		(__thor_7d7320d0a4c2_address_5807_5811(&(y), i, type_of(&(y[i]))))^ = ((i % 2 == 0) ? (max(i32)) : (-max(i32)))
	}
	x = not_const(x)
	y = not_const(y)
	r := x %% y
	for i in 0..<16 {
__thor_7d7320d0a4c2_access_5940_5944 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_5940_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_5940_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_5940_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_5940_base^),__thor_7d7320d0a4c2_access_5940_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",222,24,"modmod_vec_wide"},checked=true)]
}

__thor_7d7320d0a4c2_access_5958_5962 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_5958_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_5958_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_5958_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_5958_base^),__thor_7d7320d0a4c2_access_5958_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",222,42,"modmod_vec_wide"},checked=true)]
}

__thor_7d7320d0a4c2_access_5964_5968 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_5964_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_5964_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_5964_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_5964_base^),__thor_7d7320d0a4c2_access_5964_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",222,48,"modmod_vec_wide"},checked=true)]
}

__thor_7d7320d0a4c2_access_6022_6026 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_6022_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_6022_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_6022_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_6022_base^),__thor_7d7320d0a4c2_access_6022_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",223,54,"modmod_vec_wide"},checked=true)]
}

__thor_7d7320d0a4c2_access_6028_6032 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_6028_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_6028_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_6028_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_6028_base^),__thor_7d7320d0a4c2_access_6028_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",223,60,"modmod_vec_wide"},checked=true)]
}

__thor_7d7320d0a4c2_access_6034_6038 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_6034_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_6034_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_6034_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_6034_base^),__thor_7d7320d0a4c2_access_6034_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",223,66,"modmod_vec_wide"},checked=true)]
}

__thor_7d7320d0a4c2_access_6050_6054 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_6050_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_6050_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_6050_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_6050_base^),__thor_7d7320d0a4c2_access_6050_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",223,82,"modmod_vec_wide"},checked=true)]
}

__thor_7d7320d0a4c2_access_6056_6060 :: proc "odin"(__thor_7d7320d0a4c2_capture_0: $__thor_7d7320d0a4c2_Capture_0, __thor_7d7320d0a4c2_capture_1: $__thor_7d7320d0a4c2_Capture_1, $__thor_7d7320d0a4c2_Result: typeid) -> __thor_7d7320d0a4c2_Result {
__thor_7d7320d0a4c2_access_6056_base := &((__thor_7d7320d0a4c2_capture_0^))
__thor_7d7320d0a4c2_access_6056_bound_0 := (__thor_7d7320d0a4c2_capture_1)
return (__thor_7d7320d0a4c2_access_6056_base^)[__thor_7d7320d0a4c2_sequence.normalize(len(__thor_7d7320d0a4c2_access_6056_base^),__thor_7d7320d0a4c2_access_6056_bound_0,{"/Users/davidludwik/Thor/integration-projects/results/repros/sdk-internal/migrated/thor-migrate-86020-1794738827200259000/internal/test_modmod.thor",223,88,"modmod_vec_wide"},checked=true)]
}

		testing.expectf(t, __thor_7d7320d0a4c2_access_5940_5944(&(r), i, type_of(__thor_7d7320d0a4c2_sequence.element(r))) == floor_mod(__thor_7d7320d0a4c2_access_5958_5962(&(x), i, type_of(__thor_7d7320d0a4c2_sequence.element(x))), __thor_7d7320d0a4c2_access_5964_5968(&(y), i, type_of(__thor_7d7320d0a4c2_sequence.element(y)))),
			"[16]i32 idx %v: %v %%%% %v == %v, want %v", i, __thor_7d7320d0a4c2_access_6022_6026(&(x), i, type_of(__thor_7d7320d0a4c2_sequence.element(x))), __thor_7d7320d0a4c2_access_6028_6032(&(y), i, type_of(__thor_7d7320d0a4c2_sequence.element(y))), __thor_7d7320d0a4c2_access_6034_6038(&(r), i, type_of(__thor_7d7320d0a4c2_sequence.element(r))), floor_mod(__thor_7d7320d0a4c2_access_6050_6054(&(x), i, type_of(__thor_7d7320d0a4c2_sequence.element(x))), __thor_7d7320d0a4c2_access_6056_6060(&(y), i, type_of(__thor_7d7320d0a4c2_sequence.element(y)))))
	}
}

@(test)
modmod_assign :: proc(t: ^testing.T) {
	// %%= must agree with %%
	{
		x := not_const(i32(1))
		x %%= not_const(max(i32))
		testing.expect_value(t, x, 1)
	}
	{
		x := not_const(i32(-3))
		x %%= not_const(min(i32))
		testing.expect_value(t, x, -3)
	}
	{
		x := not_const(i8(-7))
		x %%= not_const(i8(3))
		testing.expect_value(t, x, 2)
	}
	{
		// vector form
		x := not_const([4]i32{1, 3, -3, 12})
		y := not_const([4]i32{max(i32), max(i32), 7, max(i32)})
		x %%= y
		testing.expect_value(t, x, [4]i32{1, 3, 4, 12})
	}
}

@(test)
modmod_i16_boundaries :: proc(t: ^testing.T) {
	vals := [?]i16{min(i16), min(i16) + 1, -32000, -300, -7, -3, -1, 1, 3, 7, 300, 32000, max(i16) - 1, max(i16)}
	for x in vals {
		for y in vals {
			if y == 0 { continue }
			if x == min(i16) && y == -1 { continue }
			got := x %% y
			want := floor_mod(x, y)
			testing.expectf(t, got == want, "%v %%%% %v == %v, want %v", x, y, got, want)
		}
	}
}

@(test)
modmod_unsigned_widths :: proc(t: ^testing.T) {
	// for unsigned types %% must match % at every width, including near the maximum
	check :: proc(t: ^testing.T, $T: typeid, loc := #caller_location) {
		vals := [?]T{1, 2, 3, 7, max(T) / 2, max(T) / 2 + 1, max(T) - 1, max(T)}
		for x in vals {
			for y in vals {
				if y == 0 { continue }
				a, b := not_const(x), not_const(y)
				testing.expectf(t, a %% b == a % b,
					"%v: %v %%%% %v == %v, want %v", typeid_of(T), a, b, a %% b, a % b, loc = loc)
			}
		}
	}
	check(t, u8)
	check(t, u16)
	check(t, u32)
	check(t, u64)
}

@(test)
modmod_endian :: proc(t: ^testing.T) {
	// endian-annotated types reach the same lowering through a conversion
	{
		x, y := not_const(i32le(1)), not_const(i32le(max(i32)))
		testing.expect_value(t, x %% y, 1)
	}
	{
		x, y := not_const(i32be(1)), not_const(i32be(max(i32)))
		testing.expect_value(t, x %% y, 1)
	}
	{
		x, y := not_const(i64le(-3)), not_const(i64le(min(i64)))
		testing.expect_value(t, x %% y, -3)
	}
}
import __thor_7d7320d0a4c2_meta "base:intrinsics"

import __thor_7d7320d0a4c2_sequence "__thor_runtime:sequence"

import __thor_7d7320d0a4c2_source_runtime "base:runtime"
