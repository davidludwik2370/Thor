"""Nonreturning procedure types are bottom-valued switch arms, not void values."""
import json
import unittest

from tests import test_thor as base


class SwitchDivergingTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_builtin_user_alias_generic_and_indirect_calls_in_all_positions(self):
        declarations='''
stop::proc()->!{panic("unexpected stop")}
generic_stop::proc(value:$T)->!{panic("unexpected generic stop")}
Stop::proc()->!
alias::stop
builtin_alias::(panic)
choose::proc(n:int,callback:Stop)->int{
    return switch n{case 1:10;case:callback()}
}
Callbacks::struct{stop:Stop}
'''
        for arm in ['unreachable()','panic_contextless("unexpected")','stop()',
                    '((alias()))','builtin_alias("unexpected")','generic_stop(7)',
                    'callback()','callbacks.stop()', '(proc()->!{panic("inline")})()']:
            with self.subTest(arm=arm):
                self.invoke(self.program('''
callback:Stop=stop
callbacks:=Callbacks{stop}
for selector in 1..=2{
    first:=switch selector{case 0:'''+arm+''';case 1:10;case:20}
    last:=switch selector{case 1:10;case 2:20;case:'''+arm+'''}
    if first!=selector*10||last!=first||choose(1,callback)!=10{panic("wrong result")}
}
''',declarations))

    def test_imported_and_selected_procedures_including_native_library(self):
        library='package library\nstop::proc(value:int)->!{panic("unexpected") }\n'
        for imported,arm in [('import "library"','library.stop(1)'),
                             ('import "library" / stop','stop(1)'),
                             ('import "core:os"','os.exit(1)'),
                             ('import "core:os" / exit','exit(1)'),
                             ('import b "base:builtin"','b.unreachable()'),
                             ('import r "base:runtime"','r.panic("unexpected")')]:
            with self.subTest(imported=imported):
                self.invoke(self.program('value:=switch{case false:'+arm+';case:7};if value!=7{panic("wrong result")}',
                                         imported+'\n'),files={'library/main.odin':library})

    def test_overloads_choose_diverging_and_value_returning_signatures(self):
        declarations='''
stop_int::proc(value:int)->!{panic("unexpected")}
value_text::proc(value:string)->int{return len(value)}
overloaded::proc{stop_int,value_text}
'''
        self.invoke(self.program('''
value:=switch{case false:overloaded(1);case:overloaded("seven")}
if value!=5{panic("wrong overload result")}
''',declarations))
        self.invoke(self.program('value:int=switch{case true:7;case:overloaded("text")};_=value',
            declarations.replace('->int{return len(value)}','{}')),command='check',fail='does not return a value')

    def test_expected_record_enum_and_multi_result_types(self):
        self.invoke(self.program('''
n:=1
record:Record>switch{case n==1:{7};case:stop()}
color:Color=switch{case false:unreachable();case:.Red}
a,b:=switch n{case 1:7,"seven";case:stop()}
a,b=switch{case true:a+1,b;case:unreachable()}
if record.value!=7||color!=.Red||a!=8||b!="seven"{panic("wrong typed result")}
consume(switch{case true:{7};case:stop()})
c,d:=pair(n)
if c!=7||d!="seven"{panic("wrong returned results")}
''','''
Record::struct{value:int}
Color::enum{Red,Blue}
stop::proc()->!{panic("unexpected")}
consume::proc(value:Record){if value.value!=7{panic("wrong argument")}}
pair::proc(n:int)->(int,string){return switch n{case 1:7,"seven";case:stop()}}
'''))

    def test_all_nonreturning_arms_need_context_and_keep_native_argument_errors(self):
        for expression in ['switch{case:unreachable()}', 'switch{case:stop()}']:
            with self.subTest(expression=expression):
                self.invoke(self.program('value:='+expression+';_=value','stop::proc()->!{panic("stop")}\n'),
                            command='check',fail='requires an expected result type')
                self.invoke(self.program('value:int='+expression+';_=value','stop::proc()->!{panic("stop")}\n'),command='check')
        self.invoke(self.program('a,b:int=switch{case true:stop();case:unreachable()};_=a;_=b',
                    'stop::proc()->!{panic("stop")}\n'),command='check')
        self.invoke(self.program('value:int=switch{case true:7;case:stop("bad")};_=value',
                    'stop::proc(code:int)->!{panic("stop")}\n'),command='check',fail='Cannot')

    def test_void_and_value_returning_shadowed_names_keep_their_meaning(self):
        for name in ['unreachable','panic','panic_contextless']:
            with self.subTest(name=name):
                self.invoke(self.program('value:=switch{case:'+name+'()};if value!=7{panic("wrong value")}',
                    name+'::proc()->int{return 7}\n' if name!='panic' else 'panic::proc(args:..any)->int{return 7}\n'))
                self.invoke(self.program('value:int=switch{case true:7;case:'+name+'()};_=value',
                    name+'::proc(){}\n'),command='check',fail='does not return a value')

    def test_selected_call_executes_once_with_defer_and_in_unchecked_release(self):
        text=self.program('''
count:=0
defer count+=100
value:int=switch{case:stop(&count)}
_=value
''','''
import "core:fmt"
stop::proc(count:^int)->!{count^+=1;fmt.println(count^);panic("selected nonreturning call")}
''')
        for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(text,flags=flags,fail='selected nonreturning call')
                self.assertEqual(result.stdout,'1\n')

    def test_owned_results_and_skip_assignment_in_checked_and_unchecked_builds(self):
        text=self.program('''
n:=1
text:=switch n{case 1:f"value {n}";case:stop()}
defer delete(text)
values:=switch{case false:unreachable();case:set[int]{1,2}}
defer delete(values)
number:=Integer(7)
defer delete(number)
number=switch{case true:number+1;case:stop()}
a,b:=1,2
a,b=switch{case false:stop();case true:break;case:3,4}
if text!="value 1"||len(values)!=2||number!=8||a!=1||b!=2{panic("wrong owned result")}
''','stop::proc()->!{panic("unexpected")}\n')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(text,flags=flags)
                self.assertEqual(result.stderr,'')

    def test_compile_switch_laziness_typed_selection_and_global_multi_results(self):
        self.invoke(self.program('''
local:=#switch{case true:7;case:unreachable(missing)}
a,b:=#switch{case true:7,"seven";case:stop(missing)}
if local!=7||a!=7||b!="seven"||A!=7||B!="seven"{panic("wrong compile switch")}
''','''
stop::proc "contextless" ()->!{panic_contextless("unexpected")}
A,B:=switch{case true:7,"seven";case:stop()}
'''))
        self.invoke(self.program('value:int=#switch{case:stop()};_=value',
            'stop::proc()->!{panic("selected compile switch")}\n'),fail='selected compile switch')
        self.invoke(self.program('a,b:int=#switch{case:stop()};_=a;_=b',
            'stop::proc()->!{panic("selected multiple compile switch")}\n'),fail='selected multiple compile switch')

    def test_pure_execution_and_selected_failure_preserve_call_site(self):
        declarations='''
stop::proc()->!{panic("pure selected stop")}
choose::fn(n:int)->int{return switch n{case 1:7;case:stop()}}
AUTO::choose(1)
VALUE::#run choose(1)
'''
        self.invoke(self.program('n:=1;value:=choose(n);if value!=7||AUTO!=7||VALUE!=7{panic("wrong pure result")}',declarations))
        self.invoke(self.program('_=VALUE',declarations.replace('VALUE::#run choose(1)','VALUE::#run choose(0)')),
                    command='check',fail='pure selected stop')
        self.invoke(self.program('n:=1;_=choose(n)','''
state:int
stop::proc()->!{state=1;panic("stop")}
choose::fn(n:int)->int{return switch n{case 1:7;case:stop()}}
'''),command='check',fail='Pure code cannot')

    def test_bad_argument_location_remains_in_original_arm(self):
        text=self.program('value:int=switch{case true:7;case:stop("bad argument")};_=value',
                          '// 😀\nstop::proc(code:int)->!{panic("stop")}\n')
        _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='Cannot')
        errors=json.loads(result.stderr)['errors']
        start=text.encode().index(b'"bad argument"')
        self.assertTrue(any(error['pos']['offset']==start for error in errors),errors)
        self.assertNotIn('Thor expression',json.dumps(errors))

    def test_unconstrained_callbacks_specialize_without_treating_void_as_nonreturning(self):
        declarations='''
stop::proc()->!{panic("selected generic stop")}
seven::proc()->int{return 7}
void::proc(){}
Options::struct{callback:proc()->!}
inferred::proc(callback:$P)->int{value:=switch{case false:callback();case:7};return value}
typed::proc(callback:$P)->int{return switch{case true:7;case:callback()}}
record::proc(options:$O)->int{return switch{case true:7;case:options.callback()}}
multiple::proc(callback:$P)->(int,string){return switch{case true:7,"seven";case:callback()}}
compiled::proc(callback:$P)->int{return #switch{case false:callback();case:7}}
compile_selected::proc(callback:$P)->int{return #switch{case:callback()}}
compile_pair::proc(callback:$P)->(int,string){return #switch{case false:callback();case:7,"seven"}}
inferred_only::proc(callback:$P)->int{value:=switch{case:callback()};return value}
selected::proc(callback:$P)->int{return switch{case:callback()}}
'''
        self.invoke(self.program('''
a,b:=multiple(stop)
if a!=7||b!="seven"||inferred(stop)!=7||inferred(seven)!=7||typed(stop)!=7||record(Options{stop})!=7||compiled(stop)!=7{panic("wrong specialized result")}
if selected(seven)!=7{panic("wrong returning callback")}
if compile_selected(seven)!=7{panic("wrong selected compile callback")}
c,d:=compile_pair(stop)
if c!=7||d!="seven"{panic("wrong compile pair")}
''',declarations))
        self.invoke(self.program('_=selected(stop)',declarations),fail='selected generic stop')
        self.invoke(self.program('_=compile_selected(stop)',declarations),fail='selected generic stop')
        self.invoke(self.program('_=inferred_only(stop)',declarations),command='check',fail='requires an expected result type')
        for call in ['inferred(void)','typed(void)','selected(void)']:
            with self.subTest(call=call):
                text=self.program('_='+call,declarations)
                _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='does not return a value')
                errors=json.loads(result.stderr)['errors']
                offset=text.index('callback()',text.index('\n'+call.split('(')[0]+'::proc'))
                self.assertTrue(any(error['pos']['offset']==offset for error in errors),errors)
                self.assertNotIn('Thor expression',json.dumps(errors))

    def test_generic_specialization_queries_never_execute_callbacks(self):
        self.invoke(self.program('''
value:=choose(next)
if value!=7||calls!=1{panic("callback evaluated more than once")}
''','''
calls:int
next::proc()->int{calls+=1;return 7}
choose::proc(callback:$P)->int{value:=switch{case:callback()};return value}
'''))

    def test_native_when_selects_nonreturning_or_value_signature_through_alias(self):
        text=self.program('''
value:=switch{case false:alias();case:7}
if value!=7{panic("wrong result")}
when !#config(STOP,true){
    a,b:=switch{case true:alias(),"nine";case:7,"seven"}
    value=switch{case true:alias();case:7}
    if a!=9||b!="nine"||value!=9{panic("wrong active signature")}
}
''','''
when #config(STOP,true){stop::proc()->!{panic("unexpected")}}
else{stop::proc()->int{return 9}}
alias::stop
''')
        for flag in ['true','false']:
            with self.subTest(flag=flag):self.invoke(text,flags=['-define:STOP='+flag])


if __name__=='__main__':unittest.main()
