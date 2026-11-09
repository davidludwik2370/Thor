"""Only installed deterministic native intrinsics bypass body inspection."""
import unittest
from tests import test_rust_checked_calls as checked


class RustPureIntrinsicTests(unittest.TestCase):
    setUpClass=classmethod(checked.RustCheckedCallTests.setUpClass.__func__)
    setUp=checked.RustCheckedCallTests.setUp
    invoke=checked.RustCheckedCallTests.invoke
    compare=checked.RustCheckedCallTests.compare
    program=checked.RustCheckedCallTests.program
    tracked=checked.RustCheckedCallTests.tracked

    def test_deterministic_scalar_intrinsics_keep_their_native_operations(self):
        self.tracked(self.program('''x:=f64(9);n:=u32(7)
if root(x)!=3||bits(n)!=3||fma(x)!=19||sat(u8(250))!=255{panic("native intrinsics")}
''','''import "base:intrinsics"
root::fn(x:f64)->f64{return intrinsics.sqrt(x)}
bits::fn(x:u32)->u32{return intrinsics.count_ones(x)}
fma::fn(x:f64)->f64{return intrinsics.fused_mul_add(x,2,1)}
sat::fn(x:u8)->u8{return intrinsics.saturating_add(x,10)}
'''))

    def test_structural_intrinsics_use_compile_time_type_values(self):
        self.tracked(self.program('n:=3;if work(n)!=6{panic("structural intrinsics")}', '''
import "base:intrinsics"
Row::struct{value:int}
work::fn(n:int)->int{
    if !intrinsics.type_is_integer(type_of(n)){return -1}
    if !intrinsics.type_has_shared_fields(Row,struct{value:int}){return -2}
    return n+intrinsics.type_struct_field_count(Row)+intrinsics.type_proc_parameter_count(type_of(proc(a,b:int){}))
}
'''))

    def test_selected_imports_and_constant_aliases_remain_direct(self):
        self.tracked(self.program('n:=f64(9);if work(n)!=3{panic("intrinsic alias")}', '''
import _ "base:intrinsics" / sqrt
ROOT::(sqrt)
work::fn(value:f64)->f64{return ROOT(value)}
'''))

    def test_intrinsic_arguments_still_have_their_effects_checked(self):
        self.compare(self.program('n:=f64(9);_=work(n)', '''
import "base:intrinsics"
state:f64
work::fn(value:f64)->f64{return intrinsics.sqrt(state+value)}
'''),command='check',fail='outside the function')

    def test_namespace_using_retains_the_reference_purity_restriction(self):
        prefix='''import "base:intrinsics"
sqrt::proc(x:f64)->f64{using intrinsics;return sqrt(x)}
work::fn(x:f64)->f64{return sqrt(x)}
'''
        self.compare(self.program('n:=f64(9);_=work(n)',prefix),command='check',fail='Pure using requires a resolved record schema')
        self.compare(self.program('n:=f64(9);if work(n)!=3{panic("ordinary namespace")}',prefix.replace('work::fn','work::proc')))

    def test_nondeterministic_and_memory_intrinsics_are_rejected_at_the_call(self):
        for expression in ('int(intrinsics.read_cycle_counter())',
                           '(intrinsics.volatile_load(&local))'):
            with self.subTest(expression=expression):
                self.compare(self.program('n:=3;_=work(n)', '''
import "base:intrinsics"
work::fn(n:int)->int{local:=n;return '''+expression+'''}
'''),command='check',fail='not an eligible pure operation')

    def test_type_name_identity_is_not_a_structural_type_trait(self):
        self.compare(self.program('n:=3;_=work(n)', '''
import "base:intrinsics"
work::fn(n:int)->int{return len(intrinsics.type_canonical_name(type_of(n)))}
'''),command='check',fail='not an eligible pure operation')

    def test_overridden_intrinsics_packages_are_not_trusted_by_name(self):
        fake=self.app/'fake-base'
        files={'fake-base/intrinsics/main.odin':'''package lookalike
state:f64
sqrt::proc(x:f64)->f64{return x+state}
'''}
        self.source.write_text(self.program('n:=f64(9);_=work(n)', '''
import intrinsics "base:intrinsics"
work::fn(n:f64)->f64{return intrinsics.sqrt(n)}
'''))
        for name,content in files.items():
            path=self.app/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
        # This deliberately incomplete base override must be rejected during
        # purity analysis, before a backend can encounter its missing runtime.
        result=self.invoke('rust',flags=['-collection:base='+str(fake)],command='check')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn('outside the function',result.stderr)
        self.assertNotIn('does not exist',result.stderr)

    def test_sdk_math_wrappers_keep_native_llvm_primitives(self):
        self.tracked(self.program('n:=f64(0);if work(n)!=1{panic("math wrappers")}', '''
import "core:math"
work::fn(n:f64)->f64{return math.sin(n)+math.cos(n)}
'''))

    def test_user_foreign_link_names_do_not_acquire_sdk_trust(self):
        self.compare(self.program('n:=f64(0);_=work(n)', '''
@(default_calling_convention="none") foreign _ {
    @(link_name="llvm.sin.f64") fake::proc(n:f64)->f64---
}
work::fn(n:f64)->f64{return fake(n)}
'''),command='check',fail='Pure')
