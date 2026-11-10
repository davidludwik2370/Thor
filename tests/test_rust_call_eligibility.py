"""Compile-time call eligibility and reference diagnostic precedence."""
import unittest
import tempfile
from pathlib import Path
from tests import test_rust_scheduling as scheduling
from tests import test_rust_compiler as native


class RustCallEligibilityTests(unittest.TestCase):
    maxDiff = None
    setUpClass = classmethod(scheduling.RustSchedulingTests.setUpClass.__func__)
    compare = scheduling.RustSchedulingTests.compare
    program = scheduling.RustSchedulingTests.program

    def test_root_io_precedes_result_arity_and_runtime_arguments(self):
        for expression in ['fmt.println(7)', 'PRINT(7)', 'fmt.println(runtime)']:
            with self.subTest(expression=expression):
                self.compare(self.program('runtime:=3;_=runtime;_=#run '+expression,
                    'import "core:fmt"\nPRINT::fmt.println'), fail='because it performs I/O')

    def test_io_uses_the_requiring_pure_functions_name(self):
        self.compare(self.program('_=work()', 'import "core:fmt"\nwork::fn()->int{_=#run fmt.println(7);return 0}'),
                     fail="Pure function 'work' cannot call 'fmt.println'")

    def test_explicit_named_allocator_precedes_unknown_argument(self):
        self.compare(self.program('_=#run strings.clone("value",allocator=context.allocator)',
            'import "core:strings"'),fail='Compile-time calls cannot select an allocator')

    def test_explicit_positional_allocator_is_rejected(self):
        self.compare(self.program('_=#run strings.clone("value",context.allocator)',
            'import "core:strings"'),fail='Compile-time calls cannot select an allocator')

    def test_named_numeric_allocator_is_an_ordinary_parameter(self):
        for flags in (['-debug'], native.UNCHECKED):
            with self.subTest(flags=flags):
                self.compare(self.program('fmt.println(#run work(allocator=7))',
                    'import "core:fmt"\nwork::proc(allocator:int)->int{return allocator+1}'),
                    flags=flags,output='8\n')

    def test_named_enum_allocator_is_an_ordinary_parameter(self):
        self.compare(self.program('fmt.println(#run work(allocator=.two))',
            'import "core:fmt"\nKind::enum{one,two}\nwork::proc(allocator:Kind)->int{return int(allocator)+1}'),output='2\n')

    def test_unknown_named_allocator_contract_is_rejected(self):
        self.compare(self.program('_=#run work(allocator=nil)',
            'work::proc(allocator:rawptr)->int{return 0}'),fail='Compile-time calls cannot select an allocator')

    def test_local_procedure_name_does_not_inherit_a_clone_contract(self):
        self.compare(self.program('_=#run work()',
            'state:int\nclone::proc(x:string)->int{return len(x)+state}\nwork::proc()->int{return clone("x")}'),fail='runtime storage')

    def test_foreign_or_bodyless_entry_is_rejected(self):
        self.compare(self.program('_=#run work()', 'foreign {work::proc()->int ---}'),
                     fail='requires a resolved direct call; indirect and foreign calls are not eligible')

    def test_mutable_procedure_value_is_not_a_direct_entry(self):
        self.compare(self.program('value:=work;_=#run value()', 'work::proc()->int{return 3}'),
                     fail='requires a resolved direct call; indirect and foreign calls are not eligible')

    def test_mutable_native_primitive_alias_is_not_a_direct_entry(self):
        self.compare(self.program('value:=math.sin;_=#run value(f64(0))','import "core:math"'),
                     fail='requires a resolved direct call; indirect and foreign calls are not eligible')

    def test_indirect_nested_argument_is_not_compile_time_known(self):
        self.compare(self.program('value:=leaf;_=#run work(value())',
            'leaf::proc()->int{return 3}\nwork::proc(n:int)->int{return n}'),
            fail='runtime storage is not a constant')

    def test_unknown_entry_uses_the_direct_call_message(self):
        self.compare(self.program('_=#run missing()'),fail='requires a resolved direct call')

    def test_imported_bodyless_entry_names_the_missing_implementation(self):
        self.compare(self.program('_=#run lib.work()', 'import "lib"'),
            files={'lib/lib.thor':'package lib\nforeign {work::proc()->int ---}'},
            fail="cannot verify 'lib.work': no eligible procedure body is available")

    def test_disallowed_compiler_intrinsic_is_identified(self):
        self.compare(self.program('_=#run intrinsics.cycle_counter()', 'import "base:intrinsics"'),
            fail="cannot evaluate compiler intrinsic 'intrinsics.cycle_counter'")

    def test_reviewed_intrinsics_and_constant_aliases(self):
        self.compare(self.program('fmt.println(#run COUNT(u32(15)),#run intrinsics.sqrt(f64(9)))',
            'import "base:intrinsics"\nimport "core:fmt"\nCOUNT::intrinsics.count_ones'),output='4 3\n')

    def test_sdk_llvm_math_primitive_and_alias_remain_eligible(self):
        self.compare(self.program('fmt.println(#run SIN(f64(0)))',
            'import "core:math"\nimport "core:fmt"\nSIN::math.sin'),output='0\n')

    def test_user_llvm_link_name_is_not_a_reviewed_native_primitive(self):
        self.compare(self.program('_=#run work(f64(0))',
            'foreign { @(link_name="llvm.sin.f64") work::proc "none"(x:f64)->f64 ---}'),
            fail='requires a resolved direct call; indirect and foreign calls are not eligible')

    def test_collection_override_does_not_inherit_a_reviewed_formatter_contract(self):
        with tempfile.TemporaryDirectory(prefix='thor-review-override-') as directory:
            root=Path(directory).resolve()
            for support in (native.ROOT/'runtime').iterdir():
                if support.name!='format':
                    (root/support.name).symlink_to(support,target_is_directory=support.is_dir())
            (root/'format').mkdir()
            (root/'format/main.thor').write_text('package format\nforeign {format_plain::proc(value:int)->int ---}\n')
            self.compare(self.program('_=#run format.format_plain(7)','import "thor:format"'),
                         flags=['-debug','-collection:thor='+str(root)],
                         fail="cannot verify 'format.format_plain': no eligible procedure body is available")

    def test_allocator_restriction_in_nested_known_argument(self):
        self.compare(self.program('_=#run len(strings.clone("value",context.allocator))',
            'import "core:strings"'),fail='runtime storage is not a constant')

    def test_default_allocator_clone_is_a_known_nested_argument(self):
        self.compare(self.program('fmt.println(#run len(strings.clone("hello")))',
            'import "core:strings"\nimport "core:fmt"'),output='5\n')

    def test_reviewed_formatter_is_a_known_nested_argument(self):
        self.compare(self.program('fmt.println(#run len(format.format_plain(123)))',
            'import format "thor:format"\nimport "core:fmt"'),output='3\n')

    def test_formatter_aggregate_input_is_not_a_known_value(self):
        self.compare(self.program('_=#run len(format.format_plain([2]int{1,2}))',
            'import format "thor:format"'),fail='runtime storage is not a constant')

    def test_location_dependent_compound_is_not_a_known_value(self):
        self.compare(self.program('_=#run work(Location{})',
            'Location::[#line]int\nwork::proc(value:Location)->int{return len(value)}'),
            fail='runtime storage is not a constant')


if __name__ == '__main__':
    unittest.main()
