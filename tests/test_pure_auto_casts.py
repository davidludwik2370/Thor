"""Contextual pure casts preserve numeric semantics and reject pointer forging."""
import unittest
from tests import test_thor as base


class PureAutoCastTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def tracked(self,code,files=None):
        for flags in (['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']):
            with self.subTest(flags=flags):
                _,result=self.invoke(code,flags=flags,files=files)
                self.assertEqual(result.stderr,'')

    def test_pointer_targets_are_rejected_in_all_typed_positions(self):
        for body in ('pointer:^int=auto_cast value;pointer^=9',
                     'change(auto_cast value)',
                     'holder:=Holder{pointer=auto_cast value};holder.pointer^=9',
                     'values:[dynamic]^int;append(&values,auto_cast value);values[0]^=9'):
            with self.subTest(body=body):
                self.invoke(self.program('local:=1;_=work(uintptr(&local))','''
Holder::struct{pointer:^int}
change::proc(out:^int){out^=9}
work::fn(value:uintptr)->int{'''+body+''';return 0}
'''),command='check',fail='Pure code cannot convert values to pointers')

    def test_pointer_operands_cannot_be_hidden_by_cast_or_auto_cast(self):
        for expression in ('cast(uintptr)&local','auto_cast &local'):
            with self.subTest(expression=expression):
                self.invoke(self.program('n:=3;_=work(n)','''
work::fn(value:int)->uintptr{local:=value;return '''+expression+'''}
'''),command='check',fail='pointer')

    def test_numeric_contexts_preserve_values_and_operand_evaluation_count(self):
        self.tracked(self.program('n:=3;if work(n)!=11{panic("numeric auto_cast")}', '''
helper::proc(n:i16)->int{return int(n)}
work::fn(n:int)->int{
 a:i16=auto_cast n
 a=auto_cast (n+1)
 b:=helper(auto_cast n)
 for i in 0..<auto_cast n{_=i}
 values:=[2]i16{auto_cast n,auto_cast (n+1)}
 if (auto_cast a)==n{b+=1}
 calls:=0
 converted:i16=auto_cast step(&calls,n)
 if calls!=1||converted!=i16(n){panic("cast operand count")}
 return auto_cast (int(a)+b+int(values[1]))
}
step::proc(counter:^int,n:int)->int{counter^+=1;return n}
'''))

    def test_generic_call_keeps_context_from_preceding_arguments(self):
        self.tracked(self.program('n:=3;if work(n)!=3{panic("generic cast context")}', '''
choose::proc(seed:$T,value:T)->T{_=seed;return value}
work::fn(n:int)->f64{seed:f64;return choose(seed,auto_cast n)}
'''))

    def test_private_formal_types_stay_in_the_callee_scope(self):
        self.tracked(self.program('n:=3;if work(n)!=3{panic("private cast type")}', '''
import "dep"
work::fn(n:int)->int{return dep.convert(auto_cast n)}
'''),files={'dep/native.odin':'''package dep
@(private="file")
Hidden::distinct i16
convert::proc(value:Hidden)->int{return int(value)}
'''})

    def test_sdk_generic_formals_can_supply_the_exact_cast_type(self):
        self.tracked(self.program('n:=3;if work(n)!=7{panic("native cast context")}', '''
import "core:slice"
work::fn(n:int)->int{local:=[n,n+1];a,b:=slice.split_at(local[:],auto_cast i16(1));return a[0]+b[0]}
'''))

    def test_wide_untyped_literals_and_enum_targets_keep_their_context(self):
        self.tracked(self.program('''n:u128=1
if wide(n)!=123456789012345678901234567891{panic("wide cast context")}
i:=3;if enumerated(i)!=.three{panic("enum cast context")}
''','''
Choice::enum{three=3,five=5}
wide::fn(n:u128)->u128{return (auto_cast 123456789012345678901234567890)+n}
enumerated::fn(n:int)->Choice{return auto_cast n}
'''))

    def test_ordinary_procedures_keep_native_pointer_casts(self):
        self.tracked(self.program('local:=1;change(uintptr(&local));if local!=9{panic("ordinary native cast")}', '''
change::proc(value:uintptr){pointer:^int=auto_cast value;pointer^=9}
'''))

    def test_unused_ordinary_defaults_keep_their_native_contract(self):
        self.tracked(self.program('n:=3;if work(n)!=0{panic("overridden cast default")}', '''
helper::proc(out:^int=auto_cast uintptr(1))->int{_=out;return 0}
work::fn(n:int)->int{local:=n;return helper(&local)}
'''))

    def test_aggregate_pointer_conversions_are_rejected(self):
        for expression in ('cast([1]^int)words','([1]^int)(words)','auto_cast words'):
            with self.subTest(expression=expression):
                self.invoke(self.program('n:=3;_=work(uintptr(&n))','''
work::fn(value:uintptr)->int{words:=[1]uintptr{value};pointers:[1]^int='''+expression+''';pointers[0]^=9;return 0}
'''),command='check',fail='Pure code cannot convert values to pointers')
        for expression in ('cast([1]uintptr)pointers','auto_cast pointers'):
            with self.subTest(expression=expression):
                self.invoke(self.program('n:=3;_=work(n)','''
work::fn(value:int)->uintptr{local:=value;pointers:=[1]^int{&local};words:[1]uintptr='''+expression+''';return words[0]}
'''),command='check',fail='pointer')

    def test_numeric_aggregate_casts_remain_valid(self):
        self.tracked(self.program('n:=3;if work(n)!=6{panic("numeric arrays")}','''
work::fn(n:int)->int{a:=[2]int{n,n};b:=cast([2]i16)a;c:[2]i32=auto_cast b;return int(c[0]+c[1])}
'''))

    def test_auto_cast_keeps_native_generic_and_overload_inference(self):
        self.tracked(self.program('n:=3;if work(n)!=12{panic("native cast selection")}','''
choose_int::proc(n:int)->int{return n}
choose_float::proc(n:f64)->int{return 100+int(n)}
choose::proc{choose_int,choose_float}
id::proc(n:$T)->T{return n}
typed::proc($T:typeid,n:T)->T{return n}
work::fn(n:int)->int{return choose(auto_cast n)+id(auto_cast n)+typed(int,auto_cast n)+choose_int(auto_cast n)}
'''))

    def test_named_variadic_and_multiple_results_preserve_context(self):
        self.tracked(self.program('n:=3;a,b:=work(n);if a!=3||b!=6{panic("native contexts")}','''
add::proc(values:..i16)->int{result:=0;for value in values{result+=int(value)};return result}
narrow::proc(value:i16)->i16{return value}
work::fn(n:int)->(i16,int){return auto_cast narrow(value=auto_cast n),add(auto_cast n,auto_cast n)}
'''))

    def test_compile_time_contexts_and_omitted_defaults_are_checked(self):
        self.invoke(self.program('if AUTO!=3||EXPLICIT!=5{panic("compile-time auto_cast")}', '''
helper::proc(value:i16)->int{return int(value)}
work::fn(n:int)->int{return helper(auto_cast n)}
AUTO::work(3)
EXPLICIT::#run work(5)
'''))
        self.invoke(self.program('n:=3;_=work(n)', '''
helper::proc(out:^int=auto_cast uintptr(1))->int{_=out;return 0}
work::fn(n:int)->int{_=n;return helper()}
'''),command='check',fail='Pure code cannot convert values to pointers')


if __name__=='__main__':unittest.main()
