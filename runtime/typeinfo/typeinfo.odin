// RTTI payloads use values in older Odin SDKs and pointers in newer SDKs.
package thor_typeinfo

import "base:runtime"
import "base:intrinsics"

Variant :: type_of(runtime.Type_Info{}.variant)
POINTER_PAYLOADS :: intrinsics.type_is_variant_of(Variant, ^runtime.Type_Info_Integer)

Type_Info_Any :: (^runtime.Type_Info_Any) when POINTER_PAYLOADS else runtime.Type_Info_Any
Type_Info_Array :: (^runtime.Type_Info_Array) when POINTER_PAYLOADS else runtime.Type_Info_Array
Type_Info_Bit_Field :: (^runtime.Type_Info_Bit_Field) when POINTER_PAYLOADS else runtime.Type_Info_Bit_Field
Type_Info_Bit_Set :: (^runtime.Type_Info_Bit_Set) when POINTER_PAYLOADS else runtime.Type_Info_Bit_Set
Type_Info_Boolean :: (^runtime.Type_Info_Boolean) when POINTER_PAYLOADS else runtime.Type_Info_Boolean
Type_Info_Complex :: (^runtime.Type_Info_Complex) when POINTER_PAYLOADS else runtime.Type_Info_Complex
Type_Info_Dynamic_Array :: (^runtime.Type_Info_Dynamic_Array) when POINTER_PAYLOADS else runtime.Type_Info_Dynamic_Array
Type_Info_Enum :: (^runtime.Type_Info_Enum) when POINTER_PAYLOADS else runtime.Type_Info_Enum
Type_Info_Enumerated_Array :: (^runtime.Type_Info_Enumerated_Array) when POINTER_PAYLOADS else runtime.Type_Info_Enumerated_Array
Type_Info_Fixed_Capacity_Dynamic_Array :: (^runtime.Type_Info_Fixed_Capacity_Dynamic_Array) when POINTER_PAYLOADS else runtime.Type_Info_Fixed_Capacity_Dynamic_Array
Type_Info_Float :: (^runtime.Type_Info_Float) when POINTER_PAYLOADS else runtime.Type_Info_Float
Type_Info_Integer :: (^runtime.Type_Info_Integer) when POINTER_PAYLOADS else runtime.Type_Info_Integer
Type_Info_Map :: (^runtime.Type_Info_Map) when POINTER_PAYLOADS else runtime.Type_Info_Map
Type_Info_Matrix :: (^runtime.Type_Info_Matrix) when POINTER_PAYLOADS else runtime.Type_Info_Matrix
Type_Info_Multi_Pointer :: (^runtime.Type_Info_Multi_Pointer) when POINTER_PAYLOADS else runtime.Type_Info_Multi_Pointer
Type_Info_Named :: (^runtime.Type_Info_Named) when POINTER_PAYLOADS else runtime.Type_Info_Named
Type_Info_Pointer :: (^runtime.Type_Info_Pointer) when POINTER_PAYLOADS else runtime.Type_Info_Pointer
Type_Info_Quaternion :: (^runtime.Type_Info_Quaternion) when POINTER_PAYLOADS else runtime.Type_Info_Quaternion
Type_Info_Rune :: (^runtime.Type_Info_Rune) when POINTER_PAYLOADS else runtime.Type_Info_Rune
Type_Info_Simd_Vector :: (^runtime.Type_Info_Simd_Vector) when POINTER_PAYLOADS else runtime.Type_Info_Simd_Vector
Type_Info_Slice :: (^runtime.Type_Info_Slice) when POINTER_PAYLOADS else runtime.Type_Info_Slice
Type_Info_String :: (^runtime.Type_Info_String) when POINTER_PAYLOADS else runtime.Type_Info_String
Type_Info_Struct :: (^runtime.Type_Info_Struct) when POINTER_PAYLOADS else runtime.Type_Info_Struct
Type_Info_Type_Id :: (^runtime.Type_Info_Type_Id) when POINTER_PAYLOADS else runtime.Type_Info_Type_Id
Type_Info_Union :: (^runtime.Type_Info_Union) when POINTER_PAYLOADS else runtime.Type_Info_Union
