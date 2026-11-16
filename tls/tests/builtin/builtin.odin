package ols_builtin

// Procedures
@builtin len :: proc(array: Array_Type) -> int ---
@builtin cap :: proc(array: Array_Type) -> int ---

@builtin expand_values   :: proc(value: Struct_Or_Array) -> (A, B, C, ..) ---
@builtin compress_values :: proc(values: ..) -> Struct_Or_Array_Like_Type ---
@builtin soa_zip :: proc(arrays: ..) -> Soa_Slice ---
@builtin soa_unzip :: proc(array: Soa_Slice) -> (Slices, ..) ---

size_of      :: proc($T: typeid) -> int ---
@builtin align_of     :: proc($T: typeid) -> int ---
@builtin type_of      :: proc(x: expr) -> type ---
@builtin type_info_of :: proc($T: typeid) -> ^runtime.Type_Info ---
@builtin typeid_of    :: proc($T: typeid) -> typeid ---

@builtin offset_of :: proc(value_or_type: expr, member: Field_Name = nil) -> uintptr ---

@builtin offset_of_by_string :: proc($T: typeid, member: string) -> uintptr ---

@builtin swizzle :: proc(x: [N]T, indices: ..int) -> [len(indices)]T ---

complex    :: proc(real, imag: Float) -> Complex_Type ---
quaternion :: proc(real, imag, jmag, kmag: Float) -> Quaternion_Type ---
real       :: proc(value: Complex_Or_Quaternion) -> Float ---
imag       :: proc(value: Complex_Or_Quaternion) -> Float ---
jmag       :: proc(value: Quaternion) -> Float ---
kmag       :: proc(value: Quaternion) -> Float ---
conj       :: proc(value: Complex_Or_Quaternion) -> Complex_Or_Quaternion ---

@builtin min   :: proc(values: ..T) -> T ---
@builtin max   :: proc(values: ..T) -> T ---
@builtin abs   :: proc(value: T) -> T ---
@builtin clamp :: proc(value, minimum, maximum: T) -> T ---

@builtin unreachable :: proc() -> ! ---

@(private="file") _raw_data_slice   :: proc(value: []$E)         -> [^]E    ---
@(private="file") _raw_data_dynamic :: proc(value: [dynamic]$E)  -> [^]E    ---
@(private="file") _raw_data_fixed   :: proc(value: ^[dynamic; $N]$E) -> [^]E ---
@(private="file") _raw_data_array   :: proc(value: ^[$N]$E)      -> [^]E    ---
@(private="file") _raw_data_simd    :: proc(value: ^#simd[$N]$E) -> [^]E    ---
@(private="file") _raw_data_matrix  :: proc(value: ^matrix[$R,$C]$E) -> [^]E ---
@(private="file") _raw_data_multi_fixed :: proc(value: [^][dynamic; $N]$E) -> [^]E ---
@(private="file") _raw_data_multi_array :: proc(value: [^][$N]$E) -> [^]E ---
@(private="file") _raw_data_multi_simd :: proc(value: [^]#simd[$N]$E) -> [^]E ---
@(private="file") _raw_data_multi_matrix :: proc(value: [^]matrix[$R,$C]$E) -> [^]E ---
@(private="file") _raw_data_string  :: proc(value: string)       -> [^]byte ---
@(private="file") _raw_data_string16 :: proc(value: string16) -> [^]u16 ---
@builtin raw_data :: proc{_raw_data_slice, _raw_data_dynamic, _raw_data_fixed, _raw_data_array, _raw_data_simd, _raw_data_matrix, _raw_data_multi_fixed, _raw_data_multi_array, _raw_data_multi_simd, _raw_data_multi_matrix, _raw_data_string, _raw_data_string16}

/*
	This is interally from the compiler
*/

Odin_OS_Type :: enum int {
	Unknown,
	Windows,
	Darwin,
	Linux,
	FreeBSD,
	OpenBSD,
	WASI,
	JS,
	Freestanding,
}

@builtin
ODIN_OS: Odin_OS_Type

Odin_Arch_Type :: enum int {
	Unknown,
	amd64,
	i386,
	arm64,
	wasm32,
	wasm64p32,
}

@builtin
ODIN_ARCH:  Odin_Arch_Type

Odin_Build_Mode_Type :: enum int {
	Executable,
	Dynamic,
	Object,
	Assembly,
	LLVM_IR,
}

@builtin
ODIN_BUILD_MODE: Odin_Build_Mode_Type

Odin_Endian_Type :: enum int {
	Unknown,
	Little,
	Big,
}

@builtin
ODIN_ENDIAN: Odin_Endian_Type

Odin_Platform_Subtarget_Type :: enum int {
	Default,
	iOS,
}

@builtin
ODIN_PLATFORM_SUBTARGET: Odin_Platform_Subtarget_Type

Odin_Sanitizer_Flag :: enum u32 {
	Address,
	Memory,
	Thread,
}

@builtin
ODIN_SANITIZER_FLAGS: distinct bit_set[Odin_Sanitizer_Flag; u32]

@builtin
ODIN_DEBUG: bool
