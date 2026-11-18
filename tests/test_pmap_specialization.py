"""Constant callback identities reach native loops; runtime values stay dynamic."""
import re
from pathlib import Path
import tempfile
import unittest
from tests import test_pmap as base


class ParallelMapSpecializationTests(unittest.TestCase):
    invoke=base.ParallelMapTests.invoke
    program=base.ParallelMapTests.program

    def verify(self,source):
        for flags in ([],base.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,flags=flags)

    def test_known_declarations_aliases_groups_literals_and_fn_are_specialized(self):
        prefix='''import "thor:basic"
twice::proc(x:$T)->T{return x*2}
other::proc(x:f64)->f64{return x*3}
choose::proc{twice,other}
alias::twice
pure::fn(x:int)->int{return x*2}
'''
        for callback in ['twice','alias','(alias)','choose','pure','proc(x:$T)->T{return x*2}']:
            with self.subTest(callback=callback):
                source=self.program('rows:=basic.pmap([]int{2},'+callback+');defer delete(rows);if rows[0]!=4{panic("static callback")}',prefix)
                root,result=self.invoke(source,command='transpile')
                output=Path(result.stdout.strip()).read_text()
                self.assertIn('._pmap_static(',output)
                self.assertRegex(output,r'__thor_\w+_known_callback_\d+\s*::\s*__thor_\w+_meta.procedure_of')
                self.invoke(source)

    def test_runtime_variables_parameters_selectors_and_factories_keep_fallback(self):
        prefix='''import "thor:basic"
twice::proc(x:int)->int{return x*2}
triple::proc(x:int)->int{return x*3}
State::struct{callback:proc(int)->int}
make_callback::proc()->proc(int)->int{return triple}
'''
        for setup,callback in [('callback:=twice;callback=triple','callback'),
                               ('callback :> triple','callback'),
                               ('state:=State{triple}','state.callback'),
                               ('callbacks:=[]proc(int)->int{triple}','callbacks[0]'),
                               ('selected:=false;callback:=selected?twice:triple','callback'),
                               ('callback:=triple;pointer:=&callback','pointer^'),
                               ('','make_callback()')]:
            with self.subTest(callback=callback):
                source=self.program(setup+';rows:=basic.pmap([]int{2},'+callback+');defer delete(rows);if rows[0]!=6{panic("runtime callback")}',prefix)
                _,result=self.invoke(source,command='transpile')
                self.assertNotIn('._pmap_static(',Path(result.stdout.strip()).read_text())
                self.verify(source)
        self.verify(self.program('rows:=forward([]int{2},triple);defer delete(rows);if rows[0]!=6{panic("parameter")}',
            prefix+'forward::proc(jobs:[]int,callback:proc(int)->int)->[]int{return basic.pmap(jobs,callback)}\n'))

    def test_reassignment_is_observed_on_each_call(self):
        self.verify(self.program('''
callback:=twice
first:=basic.pmap([]int{2,3},callback);defer delete(first)
callback=triple
second:=basic.pmap([]int{2,3},callback);defer delete(second)
if first[1]!=6||second[1]!=9{panic("stale function identity")}
''','import "thor:basic"\ntwice::proc(x:int)->int{return x*2}\ntriple::proc(x:int)->int{return x*3}\n'))

    def test_factory_and_arguments_are_evaluated_once_in_written_order(self):
        self.verify(self.program('''
rows:=basic.pmap(workers=workers(),calculate=callback(),jobs=jobs())
defer delete(rows)
if order!=231||rows[1]!=9{panic("callback evaluation order")}
''','''import "thor:basic"
order:int
data:[2]int={2,3}
workers::proc()->int{order=order*10+2;return 2}
callback::proc()->proc(int)->int{order=order*10+3;return triple}
jobs::proc()->[]int{order=order*10+1;return data[:]}
triple::proc(x:int)->int{return x*3}
'''))

    def test_type_witnesses_do_not_execute_callback_with_defaults(self):
        self.verify(self.program('''
rows:=basic.pmap([]Job{{10},{20},{30}},calculate,workers=1)
defer delete(rows)
if calls!=3||rows[0]!=13||rows[1]!=23||rows[2]!=33{panic("callback evaluation")}
''','''import "thor:basic"
Job::struct{x:int}
calls:int
calculate::proc(x:int,y:=3)->int{calls+=1;return x+y}
'''))

    def test_imported_alias_and_reexport_keep_polymorphic_result_names(self):
        source=self.program('''
rows:=m.mapper([]Job{{2,3}},m.calculate,result_type=Result)
defer delete(rows)
if rows[0].total!=5||rows[0].product!=6{panic("reexport specialization")}
''','''import m "exports"
Job::struct{a,b:i16}
Result::struct{total,product:i16}
''')
        files={'exports/exports.thor':'''package exports
import "thor:parallel"
import c "calculate"
mapper::parallel.pmap
calculate::c.pair
''','exports/calculate/calculate.thor':'''package calculate
pair::proc(a,b:$T)->(sum,product:T){return a+b,a*b}
'''}
        for flags in ([],base.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(source,files=files,flags=flags)

    def test_static_and_dynamic_actions_preserve_pointer_effects(self):
        self.verify(self.program('''
values:[4]int
basic.pmap([]Job{{2,&values[0]},{3,&values[1]}},set)
callback:proc(int,^int)=set
basic.pmap([]Job{{4,&values[2]},{5,&values[3]}},callback)
if values!=[2,3,4,5]{panic("action specialization")}
''','''import "thor:basic"
Job::struct{value:int,out:^int}
set::proc(value:int,out:^int){out^=value}
'''))

    def test_generic_checked_callbacks_and_owned_results_work_with_run(self):
        source=self.program('''
VALUE::#run compute(i16(3))
runtime_value:i16=4
if VALUE!=11||compute(runtime_value)!=14{panic("checked specialization")}
''','''import "thor:basic"
allocate::proc(x:$T)->[]T{values:=make([]T,2);values[0],values[1]=x,x*2;return values}
compute::fn(x:i16)->i16{
    rows:=basic.pmap([]i16{x,x+1},allocate,workers=2)
    local:i16
    basic.pmap([]^i16{&local},proc(out:^$E){out^+=1})
    return rows[0][1]+rows[1][0]+local
}
''')
        for flags in (['-debug'],base.UNCHECKED):
            with self.subTest(flags=flags):
                _,result=self.invoke(source,flags=flags)
                self.assertNotIn('still allocated',result.stderr)

    def test_static_proof_does_not_bypass_purity_or_borrow_checks(self):
        for callback,message in [('impure','Pure'),('write','borrowed')]:
            with self.subTest(callback=callback):
                self.invoke(self.program('values:=[]int{1};_=compute(values)','''import "thor:basic"
global:int
impure::proc(out:^int){global+=1}
write::proc(out:^int){out^+=1}
compute::fn(values:[]int)->int{basic.pmap([]^int{&values[0]},CALLBACK);return values[0]}
'''.replace('CALLBACK',callback)),command='check',fail=message)

    def test_native_ir_inlines_known_callback_and_retains_runtime_indirection(self):
        with tempfile.TemporaryDirectory(prefix='pmap-static-ir-') as temporary:
            out=Path(temporary)
            source=self.program('''
a:=parallel.pmap([]int{1,2,3},calculate,workers=2)
b:=parallel.pmap([]int{1,2,3},callback,workers=2)
if a[2]!=16||b[2]!=16{panic("mapping")}
delete(a);delete(b)
''','''import "thor:parallel"
calculate::proc(x:int)->int{return x*3+7}
callback:proc(int)->int=calculate
''')
            self.invoke(source,command='build',flags=[*base.UNCHECKED,'-use-single-module','-build-mode:llvm-ir','-out:'+str(out)])
            ir='\n'.join(path.read_text() for path in out.glob('*.ll'))
            workers=[(head,body) for head,body in re.findall(r'^(define[^\n]+)\n(.*?)^}',ir,re.M|re.S)
                     if '_map_values' in head and '.run-' in head]
            static=[body for head,body in workers if 'is_static:$$true' in head]
            dynamic=[body for head,body in workers if 'is_static:$$false' in head]
            self.assertTrue(static,ir[:1000]);self.assertTrue(dynamic,ir[:1000])
            indirect=r'\bcall[^\n]* %[^\n]*\('
            for body in static:self.assertNotRegex(body,indirect)
            self.assertTrue(any(re.search(indirect,body) for body in dynamic))
