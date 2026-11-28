"""Opt-in fast fn diagnostics, proof gaps, original locations and evaluator ordering."""
import json
import os
from pathlib import Path
import unittest
from tests import test_rust_compiler as native


class FnSafetyTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    compare = native.RustCompilerTests.compare

    def check(self, body, *, mode='prototype', flags=(), prefix='', command='check'):
        self.source.write_text('package main\n'+prefix+'\n'+body+'\n')
        result = self.invoke('rust', command, flags=[f'--fn-safety={mode}', '--comptime=required', '-json-errors', '-thread-count:1', *flags])
        packet = json.loads(result.stderr) if result.stderr.strip() else {'errors': []}
        return result, packet['errors']

    def test_default_and_explicit_off_preserve_existing_builds(self):
        code='bad::fn()->int{a:=make([]int,1);delete(a);a[0]}\nmain::proc(){}'
        self.source.write_text('package main\n'+code)
        for flags in ([], ['--fn-safety=off']):
            result=self.invoke('rust','check',flags=['--comptime=required',*flags])
            self.assertEqual(result.returncode,0,result.stderr)
        result, findings=self.check(code)
        self.assertEqual(result.returncode,1,findings)
        self.assertIn('thor/fn-safety',[f.get('code') for f in findings])

    def test_aliases_views_records_and_multi_assignments(self):
        cases=[
            'b:=a;delete(a);return b[0]',
            'b:=a[:];delete(a);return b[0]',
            'b:=a[1:];delete(a);return b[0]',
            'b:=Box{a};delete(a);return b.rows[0]',
            'b:[]int;b=a;delete(a);return b[0]',
            'b,c:=a,a;delete(b);return c[0]',
            'b:=a;delete(a);delete(b);return 0',
        ]
        for case in cases:
            with self.subTest(case=case):
                result,findings=self.check('Box::struct{rows:[]int}\nf::fn()->int{a:=make([]int,2);'+case+'}\nmain::proc(){}')
                self.assertEqual(result.returncode,1,findings)
                self.assertTrue(any('released' in str(f) for f in findings),findings)

    def test_header_only_queries_and_independent_scalar_copies(self):
        code='''length::fn(a:[]int)->int{len(a)}
f::fn()->int{a:=make([]int,2);a[0]=7;x:=a[0];delete(a);return x+length(a)}
main::proc(){if f()!=9{panic("header/copy")}}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        self.assertEqual(findings,[])
        self.compare('package main\n'+code,flags=['--fn-safety=strict','--comptime=required'])

    def test_conditional_release_reinitialization_and_early_returns(self):
        bad='f::fn(flag:bool)->[]int{a:=make([]int,2);if flag{delete(a)};a}\nmain::proc(){}'
        result,findings=self.check(bad)
        self.assertEqual(result.returncode,1,findings)
        safe='''f::fn(flag:bool)->[]int{a:=make([]int,2);if flag{delete(a);a=make([]int,3)};a}
g::fn(flag:bool)->int{a:=make([]int,2);if flag{delete(a);return 1};x:=a[0];delete(a);return x}
main::proc(){}'''
        result,findings=self.check(safe)
        self.assertEqual(result.returncode,0,findings)
        self.assertTrue(any('correlated liveness' in str(f) for f in findings),findings)

    def test_defer_is_late_bound_and_scoped_cleanup_is_shallow(self):
        safe='''f::fn()->int{a:=make([]int,1);defer delete(a);a=make([]int,2);a[0]}
g::fn()->int{a~:=make([]int,2);a[0]}
main::proc(){}'''
        result,findings=self.check(safe,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        bad='f::fn()->[]int{a:=make([]int,1);defer delete(a);return a}\nmain::proc(){}'
        result,findings=self.check(bad)
        self.assertEqual(result.returncode,1,findings)

    def test_callback_aliases_defaults_and_record_initialization(self):
        code='''Callback::fn(int)->int
Alias::Callback
Box::struct{callback:Alias}
square::fn(x:int)->int{x*x}
apply::fn(x:int,callback:Alias)->int{callback(x)}
through::fn(x:int)->int{b:=Box{square};cb:=b.callback;apply(x,cb)}
main::proc(){if through(3)!=9{panic("callback")}}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        self.assertEqual(findings,[])
        self.compare('package main\n'+code,flags=['--fn-safety=strict'])

    def test_runtime_selected_callback_bodies_are_checked(self):
        code='''first::fn(x:int)->int{x+1}
second::fn(x:int)->int{x*2}
apply::fn(flag:bool,x:int)->int{cb:fn(int)->int=first;if flag{cb=second};cb(x)}
main::proc(){}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        bad=code.replace('x*2','a:=make([]int,1);delete(a);a[0]+x')
        result,findings=self.check(bad)
        self.assertEqual(result.returncode,1,findings)

    def test_nil_callbacks_and_nil_guards(self):
        for declaration, call in [('cb:fn(int)->int','cb(1)'),('cb:fn(int)->int=nil','cb(1)'),('b:Box','b.callback(1)')]:
            with self.subTest(declaration=declaration):
                result,findings=self.check('Box::struct{callback:fn(int)->int}\nf::fn()->int{'+declaration+';return '+call+'}\nmain::proc(){}')
                self.assertEqual(result.returncode,1,findings)
                self.assertTrue(any('nil' in str(f) for f in findings),findings)
        code='''square::fn(x:int)->int{x*x}
f::fn(flag:bool)->int{cb:fn(int)->int;if flag{cb=square};if cb!=nil{return cb(3)};return 0}
main::proc(){}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)

    def test_generics_owned_helpers_and_named_arguments(self):
        code='''copy::fn(xs:[]$T)->[]T{slice.clone(xs)}
Alias::copy
forward::fn(xs:[]int)->[]int{Alias(xs=xs)}
main::proc(){a~:=forward([]int{1,2});if a[0]!=1{panic("owned")}}'''
        prefix='import "core:slice"'
        result,findings=self.check(code,prefix=prefix,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        self.compare('package main\n'+prefix+'\n'+code,flags=['--fn-safety=strict'])
        bad=code.replace('slice.clone(xs)','out:=slice.clone(xs);delete(out);return out')
        result,findings=self.check(bad,prefix=prefix)
        self.assertEqual(result.returncode,1,findings)

    def test_run_checks_before_execution_and_warm_cache_revalidates(self):
        code='''copy::fn(xs:[]$T)->[]T{slice.clone(xs)}
main::proc(){value~:=#run copy([]int{2,3});if value[0]!=2{panic("run")}}'''
        prefix='import "core:slice"'
        for _ in range(2):
            result,findings=self.check(code,prefix=prefix,mode='strict',command='build')
            self.assertEqual(result.returncode,0,findings)
        bad=code.replace('slice.clone(xs)','out:=slice.clone(xs);delete(out);return out')
        result,findings=self.check(bad,prefix=prefix,command='build')
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(any(f.get('code')=='thor/fn-safety' for f in findings),findings)

    def test_procedure_helper_summaries_and_purity_boundary(self):
        code='''peek::proc(xs:[]int)->int{return xs[0]}
f::fn()->int{xs:=make([]int,1);delete(xs);peek(xs)}
main::proc(){}'''
        result,findings=self.check(code)
        self.assertEqual(result.returncode,1,findings)
        result,findings=self.check(code.replace('delete(xs);peek(xs)','value:=peek(xs);delete(xs);return value'),mode='strict')
        self.assertEqual(result.returncode,0,findings)
        # An unrelated unrestricted proc remains outside the fn prototype.
        result,findings=self.check('unsafe_proc::proc()->int{a:=make([]int,1);delete(a);return a[0]}\nmain::proc(){}',mode='strict')
        self.assertEqual(result.returncode,0,findings)

    def test_unknowns_remain_visible_and_strict_rejects_them(self):
        code='f::fn(x:int)->int{switch x{case 0:1;case:2}}\nmain::proc(){}'
        result,findings=self.check(code)
        self.assertEqual(result.returncode,0,findings)
        self.assertTrue(any(f.get('code')=='thor/fn-safety-unverified' for f in findings),findings)
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(all(f['type']=='error' for f in findings),findings)

    def test_source_positions_and_assertion_disabled_safety_errors(self):
        code='// 😀 original source\nf::fn()->int{a:=make([]int,1);delete(a);a[0]}\nmain::proc(){}'
        for flags in ([], ['-disable-assert'], native.UNCHECKED):
            with self.subTest(flags=flags):
                result,findings=self.check(code,flags=flags,command='build' if '-o:speed' in flags else 'check')
                self.assertEqual(result.returncode,1,findings)
                issue=next(f for f in findings if f.get('code')=='thor/fn-safety')
                raw=self.source.read_bytes()
                self.assertEqual(raw[issue['pos']['offset']:issue['pos']['end_offset']].decode(),'a[0]')
                self.assertIn('related',issue)

    def test_unchecked_accesses_require_an_explicit_proof_gap(self):
        code='f::fn(xs:[]int)->int{xs[0]}\nmain::proc(){}'
        result,findings=self.check(code,mode='strict',flags=['-no-bounds-check'])
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(any('bounds checks are disabled' in str(f) for f in findings),findings)


    def test_callback_guards_short_circuit_and_record_copies(self):
        code='''square::fn(x:int)->int{x*x}
Box::struct{callback:fn(int)->int}
f::fn(flag:bool)->int{
 b:Box;if flag{b.callback=square}
 copy:=b
 if copy.callback!=nil && copy.callback(3)>0{return 1}
 return 0
}
main::proc(){}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        code='''f::fn()->int{cb:fn(int)->int;if cb!=nil{a:=make([]int,1);delete(a);return a[0]};return 0}
main::proc(){}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)

    def test_known_panic_paths_do_not_poison_live_continuations(self):
        code='''f::fn(flag:bool)->int{a:=make([]int,1);if flag{delete(a);panic("stop")};x:=a[0];delete(a);return x}
main::proc(){if f(false)!=0{panic("value")}}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)

    def test_opaque_owned_slice_callbacks_and_generic_readers(self):
        code='''copy::fn(xs:[]$T)->[]T{slice.clone(xs)}
apply::fn(xs:[]int,callback:fn([]int)->[]int)->[]int{callback(xs)}
read::fn(xs:[]$T)->T{xs[0]}
main::proc(){xs:=[2,3];out~:=apply(xs[:],copy);if read(out)!=2{panic("owned callback")}}'''
        prefix='import "core:slice"'
        result,findings=self.check(code,prefix=prefix,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        for flags in ([],['-disable-assert'],native.UNCHECKED):
            self.compare('package main\n'+prefix+'\n'+code,flags=['--fn-safety=prototype','--comptime=required',*flags])

    def test_selected_configuration_and_generic_when_branches(self):
        code='''when #config(BAD,false){
 bad::fn()->int{a:=make([]int,1);delete(a);a[0]}
 unused::fn(x:$T)->T{x}
}
choose::fn(xs:[]$T)->T{when T==int{return xs[0]}else{return xs[0]}}
main::proc(){a:=[3];_=choose(a[:])}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        result,findings=self.check(code,flags=['-define:BAD=true'])
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(any(f.get('code')=='thor/fn-safety' for f in findings),findings)

    def test_assertion_elision_preserves_original_user_semantics(self):
        code='''release::proc(xs:[]int)->bool{delete(xs);return true}
f::fn()->int{a:=make([]int,1);assert(release(a));a[0]}
main::proc(){}'''
        result,findings=self.check(code)
        self.assertEqual(result.returncode,1,findings)
        result,findings=self.check(code,mode='strict',flags=['-disable-assert'])
        self.assertEqual(result.returncode,0,findings)

    def test_budgets_and_recursive_summaries_cannot_silently_pass_strict(self):
        items=','.join('make([]int,1)' for _ in range(540))
        code='f::fn()->int{_=[][]int{'+items+'};return 0}\nmain::proc(){}'
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(any('budget' in str(f) for f in findings),findings)
        recursive='f::fn(n:int)->int{if n==0{return 0};f(n-1)}\nmain::proc(){}'
        result,findings=self.check(recursive,mode='strict')
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(any('fixed-point' in str(f) for f in findings),findings)

    def test_prototype_does_not_change_generated_native_code(self):
        code='package main\nsquare::fn(x:int)->int{x*x}\napply::fn(x:int,cb:fn(int)->int)->int{cb(x)}\nmain::proc(){_=apply(3,square)}'
        self.source.write_text(code)
        outputs=[]
        for mode in ('off','prototype'):
            result=self.invoke('rust','transpile',flags=['--comptime=required','--fn-safety='+mode])
            self.assertEqual(result.returncode,0,result.stderr)
            output=Path(result.stdout.strip())
            outputs.append(output.read_text())
        self.assertEqual(outputs[0],outputs[1])


    def test_named_actuals_are_evaluated_once_in_the_flow_model(self):
        code='''replace::proc(xs:[]int)->[]int{delete(xs);return make([]int,2)}
head::fn(xs:[]int)->int{xs[0]}
f::fn()->int{xs:=make([]int,1);head(xs=replace(xs=xs))}
main::proc(){}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)

    def test_zero_length_make_has_no_releasable_backing(self):
        code='f::fn()->int{xs:=make([]int,0);delete(xs);delete(xs);0}\nmain::proc(){if f()!=0{panic("zero")}}'
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        self.compare('package main\n'+code,flags=['--fn-safety=strict','--comptime=required'])

    def test_procedure_bounds_directives_are_not_certified(self):
        code='f::fn(xs:[]int)->int #no_bounds_check {xs[0]}\nmain::proc(){}'
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(any('bounds checks are disabled' in str(f) for f in findings),findings)

    def test_known_bad_run_is_rejected_before_a_native_helper_runs(self):
        code='bad::fn()->int{a:=make([]int,1);delete(a);a[0]}\nVALUE::#run bad()\nmain::proc(){_=VALUE}'
        self.source.write_text('package main\n'+code)
        profile=self.root/'profile.json'
        env=dict(os.environ,THOR_PROFILE=str(profile))
        result=self.invoke('rust','build',flags=['--fn-safety=prototype','-json-errors'],env=env)
        self.assertEqual(result.returncode,1,result.stderr)
        packet=json.loads(result.stderr)
        self.assertTrue(any(e.get('code')=='thor/fn-safety' for e in packet['errors']),packet)
        timings=json.loads(profile.read_text())
        self.assertNotIn('evaluation.native_helper',timings['stages'])


    def test_using_field_aliases_keep_owner_and_callback_identity(self):
        code='''Box::struct{rows:[]int,callback:fn(int)->int}
square::fn(x:int)->int{x*x}
f::fn()->int{box:=Box{make([]int,2),square};using box;alias:=rows;value:=alias[0];delete(rows);if callback!=nil{return callback(value)};return 0}
main::proc(){}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        bad=code.replace('value:=alias[0];delete(rows)','delete(rows);value:=alias[0]')
        result,findings=self.check(bad)
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(any(f.get('code')=='thor/fn-safety' for f in findings),findings)


    def test_pure_string_results_are_owned_and_plain_proc_aliases_stay_shared(self):
        code='''import "core:strings"
label::fn()->string{strings.clone("hello")}
f::fn()->int{value:=label();n:=len(value);delete(value);return n}
main::proc(){if f()!=5{panic("owned string")}}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        self.compare('package main\n'+code,flags=['--fn-safety=strict','--comptime=required'])
        bad='''pair::proc()->([]int,[]int){xs:=make([]int,1);return xs,xs}
f::fn()->int{a,b:=pair();delete(a);delete(b);0}
main::proc(){}'''
        result,findings=self.check(bad)
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(any('released' in str(f) for f in findings),findings)

    def test_duplicated_fn_results_keep_independent_callers(self):
        code='''pair::fn()->([]int,[]int){xs:=make([]int,1);return xs,xs}
f::fn()->int{a,b:=pair();delete(a);x:=b[0];delete(b);return x}
main::proc(){if f()!=0{panic("independent")}}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        self.compare('package main\n'+code,flags=['--fn-safety=strict','--comptime=required'])


    def test_conditional_expression_owners_callbacks_and_release_effects(self):
        code='''square::fn(x:int)->int{x*x}
triple::fn(x:int)->int{x*3}
f::fn(flag:bool)->int{cb:=flag?square:triple;cb(2)}
g::fn(flag:bool)->int{cb:fn(int)->int=flag?square:nil;if cb!=nil{return cb(3)};return 0}
main::proc(){}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        bad='''drop::proc(xs:[]int)->int{delete(xs);return 0}
f::fn(flag:bool)->int{xs:=make([]int,1);_=flag?drop(xs):0;xs[0]}
main::proc(){}'''
        result,findings=self.check(bad)
        self.assertEqual(result.returncode,1,findings)
        self.assertTrue(any(f.get('code')=='thor/fn-safety' for f in findings),findings)
        bad='''f::fn(flag:bool)->int{xs:=make([]int,1);delete(xs);alias:=flag?xs:xs;alias[0]}
main::proc(){}'''
        result,findings=self.check(bad)
        self.assertEqual(result.returncode,1,findings)

    def test_simple_loop_break_and_continue_have_finite_flow(self):
        code='''f::fn(xs:[]int)->int{total:=0;for value in xs{if value<0{continue};if value==0{break};total+=value};return total}
main::proc(){xs:=[1,2,0];if f(xs[:])!=3{panic("loop")}}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        self.compare('package main\n'+code,flags=['--fn-safety=strict','--comptime=required'])

    def test_loop_source_is_evaluated_once_in_the_flow_model(self):
        code='''replace::proc(xs:[]int)->[]int{delete(xs);return make([]int,1)}
f::fn()->int{xs:=make([]int,1);total:=0;for value in replace(xs){total+=value};return total}
main::proc(){if f()!=0{panic("loop source")}}'''
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,0,findings)
        self.compare('package main\n'+code,flags=['--fn-safety=strict','--comptime=required'])

    def test_generic_compile_time_conditional_expression_is_selected(self):
        code='''choose::fn(xs:[]$T)->T{xs[0] when T==int else xs[1]}
VALUE::#run choose([]int{3})
main::proc(){if VALUE!=3{panic("compile-time selection")}}'''
        result,findings=self.check(code,mode='strict',command='build')
        self.assertEqual(result.returncode,0,findings)
        self.compare('package main\n'+code,flags=['--fn-safety=strict','--comptime=required'])

    def test_nested_opaque_results_are_gaps_instead_of_invented_aliases(self):
        code='f::fn(cb:fn()->[][]int)->int{out:=cb();delete(out[0]);delete(out);0}\nmain::proc(){}'
        result,findings=self.check(code)
        self.assertEqual(result.returncode,0,findings)
        self.assertTrue(any('deep ownership shape' in str(f) for f in findings),findings)
        self.assertFalse(any(f.get('code')=='thor/fn-safety' for f in findings),findings)
        result,findings=self.check(code,mode='strict')
        self.assertEqual(result.returncode,1,findings)

    def test_repeated_stack_views_are_unverified_without_false_use_after_free(self):
        code='f::fn()->int{for i in 0..<2{values:[1]int;view:=values[:];_=view[0]};return 0}\nmain::proc(){}'
        result,findings=self.check(code)
        self.assertEqual(result.returncode,0,findings)
        self.assertTrue(any('generation tracking' in str(f) for f in findings),findings)
        self.assertFalse(any(f.get('code')=='thor/fn-safety' for f in findings),findings)

    def test_modes_help_and_separate_value_spelling(self):
        self.source.write_text('package main\nf::fn(x:int)->int{x*x}\nmain::proc(){}')
        result=self.invoke('rust','check',flags=['--fn-safety','strict'])
        self.assertEqual(result.returncode,0,result.stderr)
        result=self.invoke('rust','check',flags=['--fn-safety=wrong'])
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertIn('choose off, prototype, or strict',result.stderr)


if __name__=='__main__':unittest.main()
