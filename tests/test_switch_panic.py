"""Panic-only switch arms: native typing, laziness, ownership and source spans."""
import unittest

from tests import test_thor as base


class SwitchPanicTests(unittest.TestCase):
    invoke = base.ThorTests.invoke
    program = base.ThorTests.program

    def test_contextual_record_boolean_switch_matches_puzzle_example(self):
        self.invoke(self.program('''
move, empty := Coord{3, 0}, Coord{1, 0}
move_dir: Coord > switch {
 case move.x > empty.x: {-1, 0}
 case move.x < empty.x: {1, 0}
 case move.y > empty.y: {0, -1}
 case move.y < empty.y: {0, 1}
 case: panic("unreachable")
}
assert(move_dir == Coord{-1, 0})
''', 'Coord::struct{x,y:int}\n'))

    def test_inference_ignores_panic_in_every_arm_position_and_parentheses(self):
        self.invoke(self.program('''
for selector in 1..=2 {
 first := switch selector {case 0:panic("first");case 1:10;case:20}
 middle := switch selector {case 1:10;case 0:(panic("middle"));case:20}
 last := switch selector {case 1:10;case 2:20;case:((panic("last")))}
 default_first := switch selector {case:panic("default");case 1:10;case 2:20}
 assert(first == selector*10 && middle == first && last == first && default_first == first)
}
'''))

    def test_result_context_from_argument_return_and_generic(self):
        self.invoke(self.program('''
value := 2
consume(switch value {case 2:{7};case:panic("argument")})
assert(choose(value).x == 7 && generic(value) == 2)
''', '''
Record::struct{x:int}
consume::proc(value:Record){assert(value.x==7)}
choose::proc(value:int)->Record{return switch value{case 2:{7};case:panic("return")}}
generic::proc(value:$T)->T{return switch{case value>0:value;case:panic("generic")}}
'''))

    def test_contextual_named_array_and_enum_result(self):
        self.invoke(self.program('''
direction:Coord>switch{case true:{-1,0};case:panic("array")}
color:Color=switch{case false:panic("enum");case:.Red}
assert(direction==Coord{-1,0}&&color==.Red)
''', 'Coord::[2]i8\nColor::enum{Red,Blue}\n'))

    def test_selector_and_selected_result_execute_once(self):
        self.invoke(self.program('''
count:=0
value:=switch next(&count){case 1:next(&count);case:panic("unselected")}
assert(value==2&&count==2)
''', 'next::proc(count:^int)->int{count^+=1;return count^}\n'))

    def test_selected_panic_executes_instead_of_fabricating_a_value(self):
        self.invoke(self.program('value:int=switch{case:panic("selected switch arm")};_=value'),
                    fail='selected switch arm')

    def test_all_panic_arms_need_context_when_inferred(self):
        self.invoke(self.program('value:=switch{case:panic("no type")};_=value'),
                    command='check', fail='requires an expected result type')
        self.invoke(self.program('value:int=switch{case true:panic("a");case:panic("b")};_=value'),
                    command='check')

    def test_void_calls_and_other_diverging_procedures_are_not_result_arms(self):
        for declaration, arm in [
            ('work::proc(){}', 'work()'),
            ('stop::proc()->!{panic("stop")}', 'stop()'),
            ('', 'unreachable()'),
            ('panic::proc(message:string){}', 'panic("shadowed")'),
        ]:
            with self.subTest(arm=arm, declaration=declaration):
                self.invoke(self.program('value:int=switch{case true:7;case:'+arm+'};_=value',
                                         declaration+'\n'), command='check', fail='does not return a value')

    def test_value_returning_user_proc_named_panic_is_an_ordinary_result(self):
        self.invoke(self.program('value:=switch{case:panic("ordinary")};assert(value==9)',
                                 'panic::proc(message:string)->int{return 9}\n'))

    def test_imported_panic_and_alias_are_not_builtin_panic_arms(self):
        self.invoke(self.program('value:int=switch{case true:7;case:panic("shadowed")};_=value',
                                 'import "library" / panic\n'),
                    files={'library/main.thor':'package library\npanic::proc(message:string){}\n'},
                    command='check', fail='does not return a value')
        self.invoke(self.program('value:int=switch{case true:7;case:stop("alias")};_=value',
                                 'stop::panic\n'), command='check', fail='does not return a value')

    def test_panic_arguments_and_other_arm_types_are_still_checked(self):
        self.invoke(self.program('value:int=switch{case true:7;case:panic(123)};_=value'),
                    command='check', fail='Cannot')
        self.invoke(self.program('value:int=switch{case true:"wrong";case:panic("stop")};_=value'),
                    command='check', fail='Cannot')

    def test_owned_switch_results_in_nested_consumers_are_cleaned_up(self):
        self.invoke('''package main
import "core:testing"
@(test) owned::proc(t:^testing.T){
 for i in 0..<3 {
  testing.expect(t,len(switch i{case 0,1,2:f"item {i}";case:panic("string")})==6)
  testing.expect(t,len(switch{case false:panic("set");case:set[int]{1,2}})==2)
  text:=f"outer {switch i{case 0,1,2:f"inner {i}";case:panic("nested")}}"
  testing.expect(t,len(text)==13);delete(text)
  selected:=switch{case false:panic("inferred Set");case:set[int]{1,2}}
  combined:=selected|set[int]{3}
  testing.expect(t,len(combined)==3);delete(selected);delete(combined)
 }
}
''', command='test', flags=['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true'])

    def test_pure_runtime_and_compile_time_execution_agree(self):
        self.invoke(self.program('''
input:=2
assert(choose(input)==14&&AUTO==14&&EXPLICIT==14)
assert(INACTIVE==14)
''', '''
choose::fn(value:int)->int{return switch value{case 2:14;case:panic("bad input")}}
AUTO::choose(2)
EXPLICIT::#run choose(2)
INACTIVE::#switch 2{case 1:panic("unchecked");case:14}
'''))

    def test_selected_panic_in_compile_time_execution_is_an_error(self):
        self.invoke(self.program('_=VALUE', '''
choose::fn(value:int)->int{return switch value{case 2:14;case:panic("compile-time panic")}}
VALUE::#run choose(0)
'''), command='check', fail='compile-time panic')

    def test_compile_switch_panic_is_lazy_and_uses_selected_result_context(self):
        self.invoke(self.program('''
 first:int=#switch{case false:panic(missing);case:7}
 second:int=#switch 1{case 1:7;case:panic("unselected")}
 inferred:=#switch 1{case 1:7;case:panic(missing)}
 assert(first==7&&second==7&&inferred==7)
'''))
        self.invoke(self.program('value:int=#switch{case:panic("selected compile switch")};_=value'),
                    fail='selected compile switch')
        self.invoke(self.program('value:=#switch{case:panic("no type")};_=value'),
                    command='check', fail='requires an expected result type')


if __name__ == '__main__':
    unittest.main()
