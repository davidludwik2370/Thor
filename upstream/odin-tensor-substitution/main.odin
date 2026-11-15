package repro
import "base:intrinsics"
import "core:fmt"
import "core:testing"
conv2d :: proc {
	conv2d_single,
	conv2d_grouped,
}
conv2d_single :: proc(
	input: ^Tensor($T),
	kernel: ^Tensor(T),
	stride, dilation, padding: uint,
	allocator := context.allocator,
	loc := #caller_location,
) -> ^Tensor(T) {
	b, c_in, h, w := input.shape[0], input.shape[1], input.shape[2], input.shape[3]
	c_out, _, k_h, k_w := kernel.shape[0], kernel.shape[1], kernel.shape[2], kernel.shape[3]
	h_out, w_out := get_hw(h, w, k_h, k_w, stride, dilation, padding)
	col := im2col(input, k_h, k_w, stride, dilation, padding, allocator)
	defer free_tensor(col, allocator)
	kernel_2d := reshape(kernel, []uint{c_out, c_in * k_h * k_w}, allocator)
	defer free_tensor(kernel_2d, allocator)
	kernel_transposed := transpose(kernel_2d, 0, 1, allocator, loc)
	defer free_tensor(kernel_transposed, allocator)
	result := matmul(missing_operand, kernel_transposed, allocator, loc)
	defer free_tensor(result, allocator)
	final := reshape_bhwc_to_bchw(result, b, h_out, w_out, c_out, allocator)
	return final
}
@(test)
test_conv2d_3x3_expected :: proc(t: ^testing.T) {
	@(static, rodata)
input_data := []f32{
		1, 2, 3,
		4, 5, 6,
		7, 8, 9,
	}
	input := new_with_init(input_data, []uint{1, 1, 3, 3}, context.temp_allocator)
	defer free_tensor(input, context.temp_allocator)
	@(static, rodata)
kernel_data := []f32{
		1, 0, -1,
		1, 0, -1,
		1, 0, -1,
	}
	kernel := new_with_init(kernel_data, []uint{1, 1, 3, 3}, context.temp_allocator)
	defer free_tensor(kernel, context.temp_allocator)
	result := conv2d(input, kernel, 1, 1, 0, context.temp_allocator)
	defer free_tensor(result, context.temp_allocator)
	expected := f32(-6)
	testing.expect(t, result.data[0] == expected,
		fmt.tprintf("3x3 conv value mismatch: got %f, expected %f", result.data[0], expected), expr="result.data[0] == expected")
}
Tensor :: struct($T: typeid) where intrinsics.type_is_numeric(T) {
	data:       []T,
	shape:      []uint,
	strides:    []uint,
	contiguous: bool,
	owns_data:  bool,
}
new_with_init :: proc(
	init: []$T,
	shape: []uint,
	allocator := context.allocator,
	loc := #caller_location,
) -> (
	res: ^Tensor(T),
) {
	res = tensor_alloc(T, shape, true, allocator, loc)
	if len(res.data) != len(init) {
		panic("Input data length must match tensor size computed from shape")
	}
	copy(res.data, init)
	return res
}
free_tensor :: proc {
	free_tensor_one,
	free_tensor_many,
}
@(private = "file")
free_tensor_many :: proc(arr: ^Tensor($T), rest: ..^Tensor(T), allocator := context.allocator) {
	free_tensor_one(arr, allocator)
	for r in rest {
		free_tensor_one(r, allocator)
	}
}
