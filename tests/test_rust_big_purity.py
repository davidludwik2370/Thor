"""Numeric direct copies own storage; shallow aggregate fields remain borrowed."""
import unittest
from tests import test_rust_big_lowering as runtime

class RustBigPurityTests(unittest.TestCase):
    setUpClass=classmethod(runtime.RustBigLoweringTests.setUpClass.__func__)
    setUp=runtime.RustBigLoweringTests.setUp
    invoke=runtime.RustBigLoweringTests.invoke
    compare=runtime.RustBigLoweringTests.compare
    tracked=runtime.RustBigLoweringTests.tracked
    numeric=runtime.RustBigLoweringTests.numeric
    program=runtime.RustBigLoweringTests.program

    def test_direct_numeric_copies_can_be_updated_and_deleted(self):
        self.tracked(self.numeric('''a:=Integer(12);b:=advance(a);expect(a,"12");expect(b,"13");delete(a);delete(b)
''','advance::fn(value:Integer)->Integer{copy:=value;defer delete(copy);copy+=1;return copy}\n'))

    def test_generic_direct_copies_preserve_native_and_numeric_permissions(self):
        self.tracked(self.numeric('''a:=Integer(12);b:=advance(a);expect(a,"12");expect(b,"13");delete(a);delete(b)
if advance(12)!=13{panic("native")}
''','''advance::fn(value:$T)->T{
 when T==Integer||T==Rational{copy:=value;defer delete(copy);copy+=1;return copy}
 else{return value+1}
}
'''))

    def test_numeric_member_copies_are_independent_inside_pure_functions(self):
        self.tracked(self.numeric('''row:=Row{Integer(12)};value:=advance(row);expect(value,"13");expect(row.value,"12");delete(value);delete(row.value)
''','''Row::struct{value:Integer}
advance::fn(row:$T)->Integer{copy:=row.value;defer delete(copy);copy+=1;return copy}
'''))

    def test_shallow_numeric_members_remain_borrowed(self):
        prefix='Row::struct{value:Integer}\n'
        for operation in ['copy:=row;copy.value+=1','delete(row.value)']:
            with self.subTest(operation=operation):
                self.compare(self.numeric('row:=Row{Integer(12)};_=work(row)',prefix+'work::fn(row:Row)->int{'+operation+';return 0}\n'),command='check',fail='borrowed')
        self.compare(self.numeric('row:=Row{Integer(12)};_=work(row)',prefix+'work::fn(row:$T)->int{copy:=row;copy.value+=1;return 0}\n'),command='check',fail='borrowed')

    def test_contextless_compile_time_results_reject_allocated_numbers(self):
        self.compare(self.numeric('_=#run work()','work::proc "contextless"()->Integer{return {}}\n'),command='check',fail='cannot return allocated numbers')

    def test_pure_numeric_formatting_keeps_output_owned(self):
        self.tracked(self.numeric('''n:=12;text:=describe(n);if text!="value 12/13"{panic("format")};delete(text)
''','describe::fn(n:int)->string{return f"value {Rational(n,13)}"}\n'))

    def test_private_storage_guards_follow_exact_generic_receiver(self):
        prefix='''Record::struct{_value:int}
read::fn(value:$T)->int{_=value._value;return 1}
'''
        self.tracked(self.numeric('record:=Record{2};if read(record)!=1{panic("ordinary record")} ',prefix))
        self.compare(self.numeric('number:=Integer(2);_=read(number)',prefix),command='check',fail='cannot inspect Integer/Rational implementation storage')
        self.compare(self.numeric('number:=Integer(2);_=read(number)','read::fn(value:Integer)->int{_=value._value;return 1}\n'),command='check',fail='cannot inspect Integer/Rational implementation storage')

    def test_private_storage_write_guard_allows_ordinary_local_records(self):
        prefix='''Record::struct{_value:int}
touch::proc(value:^$T){value._value=value._value}
work::fn()->int{value:=Record{2};touch(&value);return value._value}
'''
        self.tracked(self.numeric('if work()!=2{panic("ordinary field")}',prefix))
        self.compare(self.numeric('_=work()','''touch::proc(value:^$T){value._value=value._value}
work::fn()->int{value:=Integer(2);defer delete(value);touch(&value);return 1}
'''),command='check',fail='cannot inspect Integer/Rational implementation storage')

    def test_private_storage_is_not_a_public_type_query_escape(self):
        self.compare(self.numeric('value:=Integer(2);_=inspect(value)',
            'inspect::fn(value:Integer)->int{_=type_of(value._value);return 1}\n'),command='check',fail='backing storage is private')

if __name__=='__main__':unittest.main()
