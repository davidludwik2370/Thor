"""Promoted fields supply their own value type to contextual expressions."""
import unittest

from tests import test_thor as base


class UsingContextTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_zip_row_using_fields_are_available_to_fstrings_and_lowered_expressions(self):
        prefix='import * "thor:basic"\n'
        body='''
zip_alias::(zip)
Gender::enum{male,female}
names:>[]string{"Tim","Sally","Tom"}
ages:>[]int{22,5,12,7}
genders:>[]Gender{.male,.female,.male}
people:>zip_alias(name=names,age=ages,gender=genders)
for person in people{
    using person
    print(f"{name} is a {age} year old {gender}")
    if age**2<0{panic("promoted numeric field")}
}
for row in soa_zip(names,ages){
    using row
    print(f"{_0} {_1}")
}
'''
        expected='Tim is a 22 year old male\nSally is a 5 year old female\nTom is a 12 year old male\nTim 22\nSally 5\nTom 12'
        for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):self.invoke(self.program(body,prefix),flags=flags,expected=expected)

    def test_zip_using_keeps_shadowing_and_nested_slice_permissions(self):
        prefix='import * "thor:basic"\n'
        self.invoke(self.program('''
soa_zip::proc()->[]Row{return nil}
rows:=soa_zip()
for row in rows{using row;print(f"{custom}")}
name:="outer"
for row in zip(name=[]string{"inner"}){
    using row
    print(f"{name}")
}
if name!="outer"{panic("projection scope")}
''',prefix+'Row::struct{custom:int}\n'),expected='inner')
        writable=self.program('''
scores:=[2]int{1,2}
for &row in zip(score=scores[:]){using row;score+=1;print(f"{score}")}
if (scores!=[2]int{2,3}){panic("promoted writable column")}
''',prefix)
        for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            self.invoke(writable,flags=flags,expected='2\n3')
        for arguments,member in [('items=values','items'),('values','_0')]:
            with self.subTest(member=member):
                self.invoke(self.program('values:>[][]int{{1,2}}\nrows:>zip('+arguments+')\n'
                    'for row in rows{using row;'+member+'[0]=9}\n',prefix),
                    command='check',fail='read-only slice')

    def test_triangle_classification_switch_with_ternary_enum_arms(self):
        self.invoke(self.program('''
tri:=Triangle{validity=.possible,a=3,b=3,c=3,aa=60,ab=60,ac=60}
classify(&tri);assert(tri.kind==.equilateral)
tri={validity=.possible,a=3,b=3,c=4,aa=90,ab=45,ac=45}
classify(&tri);assert(tri.kind==.isosceles_right)
tri={validity=.possible,a=3,b=4,c=5,aa=100,ab=40,ac=40}
classify(&tri);assert(tri.kind==.obtuse)
tri={validity=.possible,a=3,b=4,c=5,aa=80,ab=60,ac=40}
classify(&tri);assert(tri.kind==.acute)
tri.validity=.impossible;classify(&tri);assert(tri.kind==.invalid)
''','''
Validity::enum{possible,impossible}
Kind::enum{invalid,equilateral,right,obtuse,acute,isosceles_right,isosceles_obtuse,isosceles_acute}
Triangle::struct{validity:Validity,kind:Kind,a,b,c,aa,ab,ac:f64}
classify::proc(using tri:^Triangle){
 kind=switch{
  case validity!=.possible:.invalid
  case a==b&&b==c:.equilateral
  case aa==90||ab==90||ac==90:(a==b||b==c)?.isosceles_right:.right
  case aa>90||ab>90||ac>90:(a==b||b==c)?.isosceles_obtuse:.obtuse
  case:(a==b||b==c)?.isosceles_acute:.acute
 }
}
'''))

    def test_pointer_parameter_selector_switch_and_runtime_captures(self):
        self.invoke(self.program('r:=Record{};set(&r,2);assert(r.kind==.B)', '''
Kind::enum{A,B}
Record::struct{kind:Kind}
set::proc(using record:^Record,selector:int){
 kind=switch selector{case 1:.A;case:selector==2?.B:.A}
}
'''))

    def test_standalone_using_and_using_declaration_share_field_context(self):
        self.invoke(self.program('''
r:=Record{}
{using r;kind=switch{case true:.B;case:.A}}
assert(r.kind==.B)
{pointer:=&r;using pointer;kind=switch 1{case 1:.A;case:.B}}
assert(r.kind==.A)
{using local:=Record{};kind=switch{case true:.B;case:.A};assert(local.kind==.B)}
''','Kind::enum{A,B}\nRecord::struct{kind:Kind}\n'))

    def test_nested_promoted_pointer_path_retains_shallow_mutability(self):
        self.invoke(self.program('r:=Record{};wrapper:=Wrapper{&r};set(&wrapper);assert(r.kind==.B)', '''
Kind::enum{A,B}
Record::struct{kind:Kind}
Wrapper::struct{using record:^Record}
set::proc(using wrapper:^Wrapper){kind=switch{case true:.B;case:.A}}
'''))

    def test_generic_record_copy_and_pointer_specializations(self):
        self.invoke(self.program('''
r:=Record{};copy:=changed(r);assert(copy.kind==.B&&r.kind==.A)
write(&r);assert(r.kind==.B)
''','''
Kind::enum{A,B}
Record::struct{kind:Kind}
changed::proc(value:$T/Record)->T{local:=value;using local;kind=switch{case true:.B;case:.A};return local}
write::proc(using pointer:$P/^Record){kind=switch{case true:.B;case:.A}}
'''))

    def test_scalar_array_record_string_and_distinct_field_assignments(self):
        self.invoke(self.program('''
r:=Record{};write(&r)
assert(r.number==4&&r.amount==Amount(7)&&r.kind==.B)
assert(r.kinds==[2]Kind{.A,.B}&&r.entry.kind==.B&&r.text=="ok")
''','''
Kind::enum{A,B}
Amount::distinct i16
Entry::struct{kind:Kind}
Record::struct{number:f32,amount:Amount,kind:Kind,kinds:[2]Kind,entry:Entry,text:string}
write::proc(using record:^Record){
 number=switch{case true:4;case:8}
 amount=switch{case true:7;case:9}
 kind=switch{case true:.B;case:.A}
 kinds=switch{case true:[.A,.B];case:[.B,.A]}
 entry=switch{case true:{kind=.B};case:{kind=.A}}
 text=switch{case true:"ok";case:"other"}
}
'''))

    def test_private_imported_enum_uses_field_type_without_exposing_owner_type(self):
        self.invoke(self.program('record:=lib.Record{};set(&record);assert(lib.is_b(record))', '''
import "lib"
set::proc(using record:^lib.Record){kind=switch{case true:.B;case:.A}}
'''),files={'lib/main.thor':'''package lib
@(private) Hidden::enum{A,B}
Record::struct{kind:Hidden}
is_b::proc(record:Record)->bool{return record.kind==.B}
'''})

    def test_local_shadow_does_not_change_promoted_destination_type(self):
        self.invoke(self.program('r:=Record{};set(&r);assert(r.kind==.B)', '''
Kind::enum{A,B}
Record::struct{kind:Kind}
set::proc(using record:^Record){
 {kind:int=0;kind=switch{case true:7;case:9};assert(kind==7)}
 kind=switch{case true:.B;case:.A}
}
'''))

    def test_readonly_promoted_destinations_still_reject_assignment(self):
        for declaration,body in [
            ('write::proc(using record:Record){kind=switch{case true:.B;case:.A}}','write(Record{})'),
            ('write::proc(){record:>Record{};using record;kind=switch{case true:.B;case:.A}}','write()'),
        ]:
            with self.subTest(declaration=declaration):
                self.invoke(self.program(body,'Kind::enum{A,B}\nRecord::struct{kind:Kind}\n'+declaration+'\n'),
                            command='check',fail='Error:')

    def test_pure_local_projection_and_compile_switch_context(self):
        self.invoke(self.program('assert(AUTO.kind==.B&&EXPLICIT.kind==.B)', '''
Kind::enum{A,B}
Record::struct{kind:Kind}
changed::fn(value:Record)->Record{
 local:=value;using local
 kind=switch{case true:.B;case:.A}
 kind=#switch{case true:.B;case:.A}
 return local
}
AUTO::changed(Record{})
EXPLICIT::#run changed(Record{})
'''))


if __name__=='__main__':
    unittest.main()
