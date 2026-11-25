"""Completed nested queries are reusable; incomplete effects remain checked."""
import json
import os
import subprocess
import unittest
from tests import test_rust_compiler as native


class SharedAnalysisEfficiencyTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def test_all_native_analysis_harnesses_compile_against_the_current_options(self):
        result=subprocess.run(['cargo','check','--release','--examples','--offline','--locked',
            '--manifest-path',str(native.ROOT/'rust/Cargo.toml')],capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def checked(self, body, prefix):
        self.source.write_text(self.program(body, prefix))
        for flags in ([], native.UNCHECKED):
            result = self.invoke('rust', flags=['--comptime=required', *flags])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_nested_generic_calls_keep_distinct_overloads_and_named_results(self):
        self.checked('''
if outer(7)!=8 || outer(f64(2.5))!=3.5 {panic("overload specialization")}
if outer(9)!=10 || outer(f64(3.5))!=4.5 {panic("cached specialization")}
''', '''
integer::proc(x:int)->(value:int){return x+1}
real::proc(x:f64)->(value:f64){return x+1}
pick::proc{integer,real}
alias::pick
inner::proc(x:$T)->T{return alias(x=x)}
outer::fn(x:$T)->T{return inner(x)}
''')

    def test_recursion_does_not_publish_a_partial_reader_effect(self):
        prefix='''
GLOBAL :> []int{7}
a::proc(v:[]int,n:int)->int{if n>0{return b(v,n-1)};return v[0]}
b::proc(v:[]int,n:int)->int{if n>0{return a(v,n-1)};v[0]+=1;return v[0]}
work::proc(v:[]int)->int{return a(v,1)}
'''
        self.source.write_text(self.program('_=work(GLOBAL)', prefix))
        for flags in ([], native.UNCHECKED):
            result=self.invoke('rust',command='build',flags=flags)
            self.assertNotEqual(result.returncode,0,result.stdout)
            self.assertIn('read-only slice',result.stderr)

    def test_generic_value_copy_and_reference_alias_have_different_permissions(self):
        prefix='''
replace::proc(value:$T)->T{copy:=value;copy[0]=9;return copy}
read::proc(value:$T)->int{return int(value[0])}
'''
        self.checked('''
original :> [1,2,3]
copy:=replace(original)
if read(original)!=1||read(copy)!=9{panic("array copy")}
''',prefix)
        self.source.write_text(self.program('original :> []int{1,2,3};_=replace(original)',prefix))
        result=self.invoke('rust',command='check')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('read-only slice',result.stderr)

    def test_flag_selected_effects_do_not_reuse_a_previous_build_verdict(self):
        prefix='''
GLOBAL :> []int{7}
work::proc(v:[]int)->int{
    when #config(THOR_CACHE_WRITE,false){v[0]+=1}
    return v[0]
}
'''
        self.source.write_text(self.program('if work(GLOBAL)!=7{panic("reader")}',prefix))
        for flags in ([], native.UNCHECKED):
            clean=self.invoke('rust',flags=flags)
            self.assertEqual(clean.returncode,0,clean.stdout+clean.stderr)
            bad=self.invoke('rust',command='build',flags=[*flags,'-define:THOR_CACHE_WRITE=true'])
            self.assertNotEqual(bad.returncode,0)
            self.assertIn('read-only slice',bad.stderr)

    def test_completed_nested_queries_reduce_repeated_planning(self):
        prefix='''
read::proc(v:[]int)->int{return v[0]}
inner::proc(v:$T)->int{return read(v)}
outer::fn(v:[]int)->int{return inner(v)}
'''
        self.source.write_text(self.program('values:>[1,2,3];total:=0;'+
            ''.join('total+=outer(values[:]);' for _ in range(48))+
            'if total!=48{panic("repeated reader")}',prefix))
        path=self.root/'profile.json'
        result=self.invoke('rust',command='check',flags=['--comptime=required'],
            env={**os.environ,'THOR_PROFILE':str(path)})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        counts=json.loads(path.read_text())['counters']
        self.assertGreater(counts.get('resolver.call_plan_hits',0),48,counts)
        self.assertGreater(counts.get('resolver.call_plans_cached',0),0,counts)


if __name__=='__main__':unittest.main()
