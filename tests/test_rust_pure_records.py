"""Native exact record schemas and recursively independent checked results."""
import json
import unittest
from tests import test_rust_checked_calls as checked


class RustPureRecordTests(unittest.TestCase):
    setUpClass=classmethod(checked.RustCheckedCallTests.setUpClass.__func__)
    setUp=checked.RustCheckedCallTests.setUp
    invoke=checked.RustCheckedCallTests.invoke
    compare=checked.RustCheckedCallTests.compare
    program=checked.RustCheckedCallTests.program
    tracked=checked.RustCheckedCallTests.tracked

    def test_record_results_clone_every_repeated_reference(self):
        self.tracked(self.program('''source:=[]int{3}
out:=work(source);defer pure.destroy(out)
out.rows[0][0]=9
if out.rows[1][0]!=3||source[0]!=3||out.text!="value 3"{panic("record ownership")}
''','''import "thor:pure"
Report::struct{rows:[][]int,text:string}
leaf::proc(input:[]int)->Report{rows:=make([][]int,2);rows[0],rows[1]=input,input;return {rows,f"value {input[0]}"}}
work::fn(input:[]int)->Report{return leaf(input)}
'''))

    def test_generic_helpers_and_constant_record_dimensions(self):
        self.tracked(self.program('n:=3;result:=work(n);if sum(result)!=7{panic("record dimensions")}', '''
Row::struct($N:int){values:[N]int}
identity::proc(value:$T)->T{return value}
sum::fn(row:Row(2))->int{return row.values[0]+row.values[1]}
work::fn(n:int)->Row(2){row:=Row(2){{n,n+1}};return identity(row)}
'''))

    def test_owned_union_variants_survive_the_scratch_lifetime(self):
        self.tracked(self.program('''n:=3;out:=work(n);defer pure.destroy(out)
text,ok:=out.([]string)
if !ok||text[0]!="value 3"{panic("union ownership")}
empty:=work(0);defer pure.destroy(empty)
''','''import "thor:pure"
Choice::union{int,[]string}
leaf::proc(n:int)->Choice{if n==0{return 0};values:=make([]string,1);values[0]=f"value {n}";return values}
work::fn(n:int)->Choice{return leaf(n)}
'''))

    def test_promoted_record_fields_keep_their_real_borrowed_storage(self):
        for input_type in ('Outer','$T'):
            with self.subTest(input_type=input_type):
                prefix='''Inner::struct{values:[]int}
Outer::struct{using inner:Inner}
leaf::proc(input:'''+input_type+''')->int{return input.values[0]}
work::fn(input:Outer)->int{return leaf(input)}
'''
                self.tracked(self.program('source:=[]int{3};view :> Outer{Inner{source}};if work(view)!=3{panic("promoted record")}',prefix))
                for mutation in ('input.values[0]=9;return input.values[0]',
                                 'alias:=input;alias.values[0]=9;return input.values[0]'):
                    with self.subTest(mutation=mutation):
                        self.compare(self.program('source:=[]int{3};value:=Outer{Inner{source}};_=work(value)',
                                                  prefix.replace('return input.values[0]',mutation)),
                                     command='check',fail='borrowed from an input')

    def test_contextless_helpers_allow_scalar_records_and_reject_owned_fields(self):
        self.tracked(self.program('n:=3;if work(n)!=3{panic("contextless record")}', '''
Point::struct{x,y:int}
leaf::proc "contextless"(n:int)->Point{return {n,n+1}}
work::fn(n:int)->int{return leaf(n).x}
'''))
        self.compare(self.program('n:=3;_=work(n)', '''
Report::struct{text:string}
leaf::proc "contextless"(n:int)->Report{_=n;return {"hello"}}
work::fn(n:int)->Report{return leaf(n)}
'''),command='check',fail='Contextless compile-time procedures cannot return strings/slices')

    def test_internal_promoted_pointer_fields_can_mutate_local_storage(self):
        prefix='''Inner::struct{values:[]int}
Outer::struct{using inner:^Inner}
fill::proc(out:^Outer,n:int){out.values[0]=n}
'''
        self.tracked(self.program('n:=3;if work(n)!=3{panic("local promoted pointer")}',prefix+'''
work::fn(n:int)->int{values:=make([]int,1);inner:=Inner{values};outer:=Outer{&inner};fill(&outer,n);return values[0]}
'''))
        self.compare(self.program('values:=[]int{3};_=work(values)',prefix+'''
work::fn(values:[]int)->int{inner:=Inner{values};outer:=Outer{&inner};fill(&outer,9);return values[0]}
'''),command='check',fail='borrowed from an input')

    def test_unsafe_record_and_union_fields_are_rejected(self):
        for definition in ('struct{value:any}','struct{value:map[int]int}', 'union{int,rawptr}'):
            with self.subTest(definition=definition):
                self.compare(self.program('','Value::'+definition+'\nwork::fn()->Value{return {}}\n'),
                             command='check',fail='Unsupported pure value type')
        self.compare(self.program('','Value::struct #raw_union{x:int,y:f64}\nwork::fn()->Value{return {}}\n'),
                     command='check',fail='pure')

    def test_storing_a_borrowed_reference_does_not_hide_later_mutation(self):
        for write in ('rows[0][0]=9','delete(rows[0])'):
            with self.subTest(write=write):
                self.compare(self.program('values:=[]int{3};_=work(values)', '''
Report::struct{rows:[][]int}
leaf::proc(input:[]int)->Report{rows:=make([][]int,1);rows[0]=input;'''+write+''';return {rows}}
work::fn(input:[]int)->Report{return leaf(input)}
'''),command='check',fail='borrowed from an input')

    def test_pointer_record_inputs_are_not_made_public_pure_values(self):
        self.compare(self.program('','Value::struct{pointer:^int}\nwork::fn(value:Value)->int{return 0}\n'),
                     command='check',fail='Unsupported pure value type')

    def test_type_failures_share_a_validator_but_keep_their_own_source_sites(self):
        for typ in ('^int','[]^int','Value'):
            with self.subTest(typ=typ):
                source=self.program('','''// 😀 source offsets must stay in bytes
Value::struct{pointer:^int}
safe::fn(value:int)->int{return value}
bad::fn(value:'''+typ+''')->int{return 0}
''')
                self.source.write_text(source)
                result=self.invoke('rust',command='check',flags=['-json-errors'])
                self.assertEqual(result.returncode,1,result.stderr)
                errors=json.loads(result.stderr)['errors']
                rejected=[error for error in errors if 'Unsupported pure value type' in ' '.join(error['msgs'])]
                self.assertEqual({error['pos']['offset'] for error in rejected},
                                 {source.encode().index(('value:'+typ).encode())+len('value:')},errors)
                for error in rejected:
                    self.assertEqual(error['pos']['file'],str(self.source))
                    self.assertIn("While checking parameter 'value' in pure call 'bad'", ' '.join(error['msgs']))
                    self.assertNotIn('__thor_',json.dumps(error))

    def test_checked_copying_does_not_erase_explicit_immutable_alias_restrictions(self):
        self.compare(self.program('source :> []int{3};_=work(source)', '''
Report::struct{rows:[][]int}
leaf::proc(input:[]int)->Report{rows:=make([][]int,1);rows[0]=input;return {rows}}
work::fn(input:[]int)->Report{return leaf(input)}
'''),command='check',fail='read-only slice')
