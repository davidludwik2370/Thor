"""Runtime interpolation widths use ordinary Thor expressions and source ranges."""
import json
import unittest
from tests import test_thor as base
from tests import test_diagnostics as diagnostic_base


class DynamicWidthTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_runtime_widths_all_alignments_formats_and_native_integer_types(self):
        self.invoke(self.program('''
width:=6
text:=f"{12:{width}}|{12:-{width}}|{12:^{width}}|{12:'_'^{width}}"
assert(text=="    12|12    |  12  |__12__");delete(text)
a:=f"{1.25:{width}.2f}|{1234:{width},d}|{0.125:{width}.1%}"
assert(a=="  1.25| 1,234| 12.5%");delete(a)
b:=f"{-42:0{u8(8)}d}|{42:#0{u128(8)}x}|{\"abcdef\":{width}.3s}"
assert(b=="-0000042|0x00002a|   abc");delete(b)
c:=f"{123:{0}}|{123:{2}}|{\"é😀\":'界'{i16(5)}s}"
assert(c=="123|123|界界界é😀");delete(c)
d:=f"{12:{Width(6)}}";assert(d=="    12");delete(d)
''','Width::distinct u16\n'),flags=['--track-allocations=basic'])

    def test_width_expressions_ternaries_calls_indexing_and_nested_literal_braces(self):
        self.invoke(self.program('''
widths:=[2,5]
a:=f"{1:{widths[-1]}}|{2:{true ? 4 : 8}}|{3:{len(\"{}\")+2}}|{4:{widths[0]>1 ? 3 : 8}}"
assert(a=="    1|   2|   3|  4");delete(a)
b:=f`raw {1:{len(f"{7}__")}}|{2:{(switch {case true:4;case:5})}}`
assert(b=="raw   1|   2");delete(b)
'''))

    def test_evaluation_order_once_and_value_snapshot_before_width_mutation(self):
        self.invoke(self.program('''
s:=f"{mark(1):{mark(2)+2}d}|{mark(3):{mark(4)}d}"
assert(s=="   1|   3" && order==1234);delete(s)
value:=7;width:=4
text:=f"{value:{change(&value,&width)}}"
assert(text=="   7" && value==99 && width==5);delete(text)
''','''order:int
mark::proc(n:int)->int{order=order*10+n;return n}
change::proc(value,width:^int)->int{value^=99;width^+=1;return width^-1}
'''))

    def test_big_value_snapshot_survives_width_overwriting_original_owner(self):
        self.invoke(self.program('''value:Integer=41;defer delete(value)
text:=f"{value:{bump(&value)}}";defer delete(text)
assert(text=="   41" && value==42)
ratio:=Rational(1,3);defer delete(ratio)
a:=f"{ratio:{change(&ratio)}.2f}";defer delete(a)
assert(a=="  0.33" && ratio==Rational(2,3))
''','''bump::proc(value:^Integer)->int{value^+=1;return 5}
change::proc(value:^Rational)->int{value^=Rational(2,3);return 6}
'''),flags=['-debug'])

    def test_lazy_branches_and_loop_widths_retain_runtime_evaluation(self):
        self.invoke(self.program('''
width:=3
s:=true ? "kept" : f"{1:{mark(&width)}}"
assert(s=="kept" && width==3)
for i in 1..=3 {
 text:=f"{i:{mark(&width)}}";assert(len(text)==width);delete(text)
}
assert(width==6)
''','mark::proc(n:^int)->int{n^+=1;return n^}\n'))

    def test_fn_run_and_generic_widths_match_runtime(self):
        self.invoke(self.program('''
width:=7
text:=label(width)
assert(text==AUTO && text==EXPLICIT && text=="   0.33");delete(text)
s:=generic(i8(4));assert(s=="   2");delete(s)
''','''label::fn(width:int)->string{return f"{Rational(1,3):{width}.2f}"}
generic::fn(width:$T)->string{return f"{2:{width}}"}
AUTO::label(7)
EXPLICIT::#run label(7)
'''))

    def test_nested_width_temporaries_and_manual_results_are_released(self):
        self.invoke('''package main
import "core:testing"
@(test) owned::proc(t:^testing.T){
 for i in 0..<4 {
  width:=len(f"{i}")+4
  text:=f"{f"value {i}":{width+4}}|{Integer(42):{len(f"width {i}")}d}"
  testing.expect(t,len(text)>0);delete(text)
  consume(f"{i:{len(f"{i}____")}}")
  manual:=f!`{i:{width}}`;delete(manual)
 }
}
consume::proc(text:string){assert(len(text)>0)}
''',command='test',flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_negative_and_overflowing_widths_fail_without_wrapping(self):
        for expression,message in [('-1','width must be nonnegative'),
                                   ('u128(max(int))+1','width exceeds the native int range')]:
            with self.subTest(expression=expression):
                self.invoke(self.program('width:='+expression+';_=f"{12:{width}}"'),fail=message)

    def test_invalid_dynamic_width_syntax_has_clear_errors(self):
        for expression,message in [('f"{1:round_pad}"','nested braces'),
                                   ('f"{1:{}}"','width requires an expression'),
                                   ('f"{1:{w w}}"','Unexpected token in interpolation width'),
                                   ('f"{1:{w}{w}}"','dynamic width belongs'),
                                   ('f"{1:.{w}f}"','Dynamic precision'),
                                   ('f"{1:8{w}}"','dynamic width belongs')]:
            with self.subTest(expression=expression):
                self.invoke(self.program('w:=4;_='+expression),command='check',fail=message)

    def test_unused_generic_and_inactive_width_validation_stay_unchecked(self):
        self.invoke(self.program('when false{_=f"{1:{\"not int\"}}"}\nassert(true)',
                                 'unused::proc($T:typeid){_=f"{1:{missing}}"}\n'))


class DynamicWidthDiagnosticTests(unittest.TestCase):
    compiler=base.THOR
    invoke=diagnostic_base.DiagnosticTests.invoke
    at=diagnostic_base.DiagnosticTests.at
    program=base.ThorTests.program

    def test_type_and_name_errors_point_to_original_width_expression(self):
        for expression,message in [('1.5','width must have an integer type'),
                                   ('true','width must have an integer type'),
                                   ('"bad"','width must have an integer type'),
                                   ('Integer(4)','width must have an integer type'),
                                   ('missing','Undeclared name: missing')]:
            with self.subTest(expression=expression):
                source=self.program('/* 😀 */ text:=f"{12:{'+expression+'}}";delete(text)')
                errors=self.at(source,expression,message=message)
                self.assertEqual(len(errors),1,errors)
                pos=errors[0]['pos']
                self.assertEqual(pos['end_offset']-pos['offset'],len(expression.encode()))
                self.assertNotIn('Thor expression',json.dumps(errors))

