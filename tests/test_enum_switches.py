"""Native enum coverage and #partial in value-producing switches."""
import json
import unittest
from tests import test_thor as base


class EnumSwitchTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program
    outcome = 'Outcome::enum u8{inGame,blackWon,whiteWon,draw}\n'

    def test_othello_forms_with_panic_or_empty_string(self):
        for prefix, tail in [('#partial ', 'case:""'),
                             ('#partial ', 'case:panic("unreachable")'),
                             ('', 'case .inGame:panic("unreachable")'),
                             ('', 'case .inGame:""')]:
            with self.subTest(prefix=prefix, tail=tail):
                self.invoke(self.program('''
for outcome in ([3]Outcome{.blackWon,.whiteWon,.draw}) {
    message := PREFIXswitch outcome {
        case .blackWon:"BLACK WINS"
        case .whiteWon:"WHITE WINS"
        case .draw:"DRAW"
        TAIL
    }
    expected := outcome==.blackWon ? "BLACK WINS" : outcome==.whiteWon ? "WHITE WINS" : "DRAW"
    if message!=expected {panic("incorrect message")}
}
'''.replace('PREFIX',prefix).replace('TAIL',tail),self.outcome))

    def test_missing_enum_cases_are_errors_and_partial_values_still_need_defaults(self):
        for expression, message in [
            ('switch outcome{case .blackWon:1;case .whiteWon:2;case .draw:3}', 'Unhandled'),
            ('#partial switch outcome{case .blackWon:1}', '#partial switch expression requires a default'),
            ('switch{case true:1}', 'requires a default'),
            ('switch 1{case 1:1}', 'requires a default'),
        ]:
            with self.subTest(expression=expression):
                self.invoke(self.program('outcome:Outcome;_= '+expression,self.outcome),
                            command='check',fail=message)

    def test_enum_aliases_sparse_values_ranges_and_grouped_cases(self):
        self.invoke(self.program('''
for kind in ([3]Kind{.A,.B,.C}) {
    first:=switch kind{case .A:1;case .B,.C:2}
    second:=switch kind{case .A..=.B:1;case .C:2}
    if first!=(kind==.A ? 1 : 2) || second!=(kind==.C ? 2 : 1) {panic("enum matching")}
}
value:Alias=.A
result:=switch value{case .A:1;case .B:2;case .C:3}
if result!=1 {panic("alias")}
''','Kind::enum{A=0,B=4,C=19}\nAlias::Kind\n'))

    def test_generic_and_imported_enum_selectors(self):
        self.invoke(self.program('''
value:colors.Color=.Red
if choose(value)!=1 || choose(colors.Color.Blue)!=2 {panic("generic enum")}
''','''import "colors"
choose::proc(value:$T)->int{return switch value{case .Red:1;case .Blue:2}}
'''),files={'colors/colors.odin':'package colors\nColor::enum{Red,Blue}\n'})
        self.invoke(self.program('_=choose(1)',
            'choose::proc(value:$T)->int{return switch value{case 1:1;case 2:2}}\n'),
            command='check',fail='requires a default')

    def test_selector_is_evaluated_once_and_panic_arms_stay_lazy(self):
        self.invoke(self.program('''
count:=0
answer:=switch next(&count){case .A:mark(&count);case .B:panic("not selected")}
if answer!=2 || count!=2 {panic("evaluation count")}
''','''Kind::enum{A,B}
next::proc(count:^int)->Kind{count^+=1;return .A}
mark::proc(count:^int)->int{count^+=1;return count^}
'''))

    def test_contextual_records_multiple_results_and_skip_assignment(self):
        self.invoke(self.program('''
kind:Kind=.A
record:Record=switch kind{case .A:{7};case .B:panic("record")}
number,text:=switch kind{case .A:7,"ok";case .B:panic("multi")}
if record.value!=7 || number!=7 || text!="ok" {panic("values")}
number,text=switch kind{case .A:break;case .B:9,"bad"}
if number!=7 || text!="ok" {panic("skip")}
number,text=switch kind{case .A:9,"changed";case .B:break}
if number!=9 || text!="changed" {panic("assignment")}
''','Kind::enum{A,B}\nRecord::struct{value:int}\n'))

    def test_owned_fstring_set_and_big_results_cleanup(self):
        _,result=self.invoke(self.program('''
kind:Kind=.A
borrowed:="borrowed"
text:=f"outer {switch kind{case .A:f\"inner {7}\";case .B:borrowed}}"
if text!="outer inner 7" {panic("nested ownership")}
delete(text)
if len(switch kind{case .A:set[int]{1,2};case .B:set[int]{3}})!=2 {panic("set")}
number:=switch kind{case .A:Integer(123);case .B:Integer(456)}
if number!=123 {panic("integer")}
delete(number)
a,_:=switch kind{case .A:7,f"discard {1}";case .B:8,f"discard {2}"}
if a!=7 {panic("blank")}
''','Kind::enum{A,B}\n'),flags=['-debug'])
        self.assertNotIn('still allocated',result.stderr)

    def test_pure_automatic_and_explicit_compile_time_execution(self):
        self.invoke(self.program('''
value:Kind=.A
if choose(value)!=7 || AUTO!=7 || EXPLICIT!=8 {panic("compile time")}
''','''Kind::enum{A,B}
choose::fn(value:Kind)->int{return switch value{case .A:7;case .B:8}}
AUTO::choose(.A)
EXPLICIT::#run choose(.B)
'''))

    def test_global_multiple_results_keep_initialization_order(self):
        self.invoke(self.program('''
if calls!=1 || global_number!=7 || global_text!="ok" || partial_global!="a" {
    panic("global initialization")
}
''','''Kind::enum{A,B}
calls:int
next::proc "contextless"()->Kind{calls+=1;return .A}
global_number,global_text:=switch next(){case .A:7,"ok";case .B:8,"bad"}
partial_global:=#partial switch Kind.A{case .A:"a";case:"b"}
'''))

    def test_contextless_helpers_use_a_contextless_fallback_panic(self):
        self.invoke(self.program('if choose(.A)!=7 || partial(.A)!=9 {panic("contextless results")}', '''
Kind::enum{A,B}
choose::proc "contextless"(value:Kind)->int{return switch value{case .A:7;case .B:8}}
partial::proc "contextless"(value:Kind)->int{return #partial switch value{case .A:9;case:10}}
'''))
        self.invoke(self.program('_=choose(Kind(7))', '''
Kind::enum{A,B}
choose::proc "contextless"(value:Kind)->int{return switch value{case .A:7;case .B:8}}
'''),flags=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'],
            fail='Unmatched enum value in switch expression')

    def test_invalid_enum_value_panics_instead_of_returning_zero_in_release(self):
        for expression in [
            'switch value{case .A:1;case .B:2}',
            'switch value{case .A:1,"a";case .B:2,"b"}',
        ]:
            with self.subTest(expression=expression):
                lhs='a,b' if '"a"' in expression else 'a'
                self.invoke(self.program('value:=Kind(7);'+lhs+':='+expression,
                    'Kind::enum{A,B}\n'),flags=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'],
                    fail='Unmatched enum value in switch expression')
        self.invoke(self.program('''
value:=Kind(7)
a:=1
a=switch value{case .A:2;case .B:break}
''','Kind::enum{A,B}\n'),flags=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'],
            fail='Unmatched enum value in switch expression')
        self.invoke(self.program('''
value:=Kind(7)
_=f"outer {switch value{case .A:f"inner {1}";case .B:"borrowed"}}"
''','Kind::enum{A,B}\n'),flags=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'],
            fail='Unmatched enum value in switch expression')

    def test_partial_native_statements_and_enumerated_arrays_are_unchanged(self):
        self.invoke(self.program('''
value:Kind=.A
count:=0
#partial switch value{case .A:count+=1}
array:=#partial [Kind]int{.A=7}
message:=(#partial switch value{case .A:"a";case:"b"})
if count!=1 || array[.A]!=7 || array[.B]!=0 || message!="a" {panic("native partial")}
''','Kind::enum{A,B}\n'))

    def test_generated_coverage_and_fallback_do_not_call_shadowed_builtins(self):
        prefix='''import native "base:builtin"
Kind::enum{A,B}
panic::proc(message:string)->int{return 9}
type_of::proc(value:Kind)->int{return 88}
'''
        self.invoke(self.program('''value:Kind=.A
number:=switch value{case .A:panic("ordinary value");case .B:8}
if number!=9 {native.panic("user panic changed")}
''',prefix))
        self.invoke(self.program('value:=Kind(7);_=switch value{case .A:1;case .B:2}',prefix),
                    fail='Unmatched enum value in switch expression')

    def test_missing_case_diagnostic_stays_on_original_unicode_source(self):
        text=self.program('value:Kind; /* 😀 */ message:=switch value{case .A:"a"};_=message',
                          'Kind::enum{A,B}\n')
        _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='Unhandled')
        errors=json.loads(result.stderr)['errors']
        error=next(item for item in errors if 'Unhandled' in '\n'.join(item['msgs']))
        self.assertTrue(error['pos']['file'].endswith('main.thor'),error)
        self.assertEqual(error['pos']['line'],5)
        self.assertNotIn('__thor_',json.dumps(errors))


if __name__=='__main__': unittest.main()
