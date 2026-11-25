"""Native execution policy, source eligibility, and unchanged runtime ownership."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import unittest
from tests import test_rust_compiler as native
from tests import test_rust_vet as setup

MODES=('required','normal','aggressive','override')

class ComptimeModeTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=setup.RustVetTests.setUp
    write=setup.RustVetTests.write

    def test_implicit_enum_constant_parameters_keep_their_annotation(self):
        self.write('''package main
Kind::enum{first=-3,second=11}
Alias::Kind
unrelated::fn(c:byte)->bool{return c==0}
choose::fn($kind:Alias)->int{when kind==.first{return 7}else{return 9}}
main::proc(){
 check::proc($kind:Alias,expected:int){value::choose(kind);if value!=expected{panic("enum")}}
 check(.first,7);check(kind=.second,expected=9)
 if #run choose(.second)!=9{panic("explicit run")}
}
''')
        for mode in MODES:
            for flags in [[],native.UNCHECKED]:
                with self.subTest(mode=mode,flags=flags):self.call(*flags,mode=mode)

    def test_imported_implicit_enum_specializations_and_invalid_members(self):
        (self.app/'kinds').mkdir()
        (self.app/'kinds'/'kinds.thor').write_text('package kinds\nKind::enum{first=5,second=17}\n')
        text='''package main
import "kinds"
unrelated::fn(c:byte)->bool{return c==0}
main::proc(){check::proc($kind:kinds.Kind){when kind==.first{value::int(kind);if value!=5{panic("enum")}}};check(.first);check(.second)}
'''
        self.write(text)
        for mode in MODES:self.call(mode=mode)
        self.write(text.replace('check(.second)','check(.absent)'))
        for mode in MODES:
            result,_=self.call(command='check',mode=mode,status=1)
            self.assertIn('absent',result.stderr)

    def call(self,*flags,command='run',status=0,args=(),mode='normal',cache=False):
        profile=self.root/'profile.json'
        result=subprocess.run([str(native.RUST),command,str(self.app),'--output-dir',str(self.output),
            '--cache-dir',str(self.root/'cache'),'--comptime='+mode,'-json-errors',
            *([] if cache else ['--no-cache']),*flags,*(['--',*args] if args else [])],
            cwd=self.root,env={**os.environ,'THOR_PROFILE':str(profile)},capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,status,result.stdout+result.stderr)
        counters=json.loads(profile.read_text()).get('counters',{}) if profile.is_file() else {}
        return result,counters

    def test_four_modes_fold_only_their_selected_calls(self):
        for spelling in ['proc','fn']:
            self.write('package main\nwork::'+spelling+'(x:int)->int{return x**2}\nmain::proc(){value:=work(137);if value!=18769{panic("value")}}\n')
            for mode in MODES:
                with self.subTest(spelling=spelling,mode=mode):
                    _,counts=self.call(mode=mode)
                    expected=mode in ('aggressive','override') or spelling=='fn' and mode=='normal'
                    self.assertEqual(counts.get('evaluation.helper_compilations',0),int(expected),counts)
                    if mode in ('required','normal'):
                        self.assertEqual(counts.get('evaluation.policy_verifications',0),0,counts)

    def test_required_constants_and_explicit_run_still_execute(self):
        for spelling,call in [('fn','work(7)'),('proc','#run work(7)')]:
            self.write('package main\nwork::'+spelling+'(x:int)->int{return x+1}\nSIZE::'+call+'\nmain::proc(){values:[SIZE]int;if len(values)!=8{panic("size")}}\n')
            for mode in MODES:
                _,counts=self.call(mode=mode)
                self.assertGreater(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_aggressive_constant_context_and_required_proc_rules(self):
        self.write('package main\nwork::proc(x:int)->int{return x+1}\nVALUE::work(7)\nmain::proc(){if VALUE!=8{panic("value")}}\n')
        for mode in MODES:
            self.call(mode=mode,status=0 if mode in ('aggressive','override') else 1)

    def test_constant_parameter_defaults_remain_required(self):
        self.write('package main\nseed::fn()->int{return 7}\nwork::proc(value:=seed())->int{return value}\nmain::proc(){if work()!=7{panic("default")}}\n')
        for mode in MODES:
            _,counts=self.call(mode=mode)
            self.assertGreater(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_local_and_transitive_runtime_barriers(self):
        for declarations,expression in [
            ('work::proc(x:int)->int{return x*x}','#runtime work(7)'),
            ('work::fn(x:int)->int{return #runtime (x*x)}','work(7)'),
            ('leaf::proc(x:int)->int{return #runtime (x*x)}\nwork::fn(x:int)->int{return leaf(x)}','work(7)'),
        ]:
            self.write('package main\n'+declarations+'\nmain::proc(){value:='+expression+';if value!=49{panic("value")}}\n')
            for mode in MODES:
                _,counts=self.call(mode=mode)
                self.assertEqual(counts.get('evaluation.helper_compilations',0),int(mode=='override'),counts)

    def test_override_allows_explicit_run_through_barrier_without_cache_leak(self):
        self.write('package main\nwork::proc(x:int)->int{return #runtime (x+1)}\nVALUE::#run work(7)\nmain::proc(){if VALUE!=8{panic("value")}}\n')
        self.call(mode='override',cache=True)
        self.call(mode='override',cache=True)
        for mode in ['normal','aggressive','required']:
            result,_=self.call(mode=mode,cache=True,status=1)
            self.assertIn('#runtime',result.stderr)

    def test_explicit_runtime_in_required_context_is_only_removed_by_override(self):
        self.write('package main\nwork::fn(x:int)->int{return x+1}\nVALUE::#runtime work(7)\nmain::proc(){if VALUE!=8{panic("value")}}\n')
        for mode in MODES:
            result,_=self.call(mode=mode,status=0 if mode=='override' else 1)
            if mode!='override':self.assertIn('#runtime',result.stderr)

    def test_ineligible_calls_keep_runtime_side_effects_and_values(self):
        self.write('''package main
import "core:fmt"
state:=1
read::proc()->int{return state}
print_value::proc(x:int)->int{fmt.println(x);return x}
main::proc(){
    a:=read();state=2;b:=read();c:=print_value(7)
    if a!=1 || b!=2 || c!=7{panic("effects")}
}
''')
        for mode in MODES:
            for flags in [[],native.UNCHECKED]:
                result,counts=self.call(*flags,mode=mode)
                self.assertEqual(result.stdout.strip(),'7')
                self.assertEqual(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_input_mutation_is_ineligible_for_optional_execution(self):
        self.write('''package main
change::proc(values:[]int)->int{values[0]=9;return values[0]}
main::proc(){value:=change([]int{7});if value!=9{panic("mutation")}}
''')
        for mode in MODES:
            for flags in [[],native.UNCHECKED]:self.call(*flags,mode=mode)

    def test_reference_results_preserve_allocations_aliasing_and_cleanup(self):
        self.write('''package main
import "core:mem"
borrow::proc(values:[]int)->[]int{return values}
text::proc()->string{return "borrowed"}
owned::proc()->[]int{items:=make([]int,1);items[0]=7;return items}
Box::struct{values:[]int}
box::proc(values:[]int)->Box{return {values}}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 values:=borrow([]int{7});value_box:=box(values)
 value_box.values[0]=9
 borrowed:=text()
 if values[0]!=9 || borrowed!="borrowed" || tracker.total_allocation_count!=0{panic("borrow contract")}
 items:=owned()
 if items[0]!=7 || tracker.total_allocation_count!=1{panic("owner contract")}
 delete(items)
 if tracker.current_memory_allocated!=0{panic("owner leak")}
 context.allocator=backing
}
''')
        for mode in MODES:
            for flags in [[],native.UNCHECKED]:
                _,counts=self.call(*flags,mode=mode)
                self.assertEqual(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_owned_fn_and_explicit_results_keep_independent_storage(self):
        self.write('''package main
build::fn(x:int)->[]int{values:=make([]int,1);values[0]=x;return values}
main::proc(){a:=build(7);b:=#run build(7);defer delete(a);defer delete(b);a[0]=9;if b[0]!=7{panic("alias")}}
''')
        for mode in MODES:
            for flags in [[],native.UNCHECKED]:self.call(*flags,mode=mode,cache=True)

    def test_temporary_allocations_remain_observable_for_ordinary_procs(self):
        self.write('''package main
import "core:mem"
work::proc(x:int)->int{values:=make([]int,1);values[0]=x;result:=values[0];delete(values);return result}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 value:=work(7)
 if value!=7 || tracker.total_allocation_count!=1 || tracker.current_memory_allocated!=0{panic("allocator effects")}
 context.allocator=backing
}
''')
        for mode in MODES:
            for flags in [[],native.UNCHECKED]:
                _,counts=self.call(*flags,mode=mode)
                self.assertEqual(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_unknown_values_remain_runtime_in_every_mode(self):
        self.write('package main\nwork::proc(x:int)->int{return x*x}\nmain::proc(){input:>7;value:=work(input);if value!=49{panic("value")}}\n')
        for mode in MODES:
            _,counts=self.call(mode=mode)
            self.assertEqual(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_polymorphic_aliases_defaults_and_nested_calls(self):
        self.write('''package main
work::proc(x:$T,y:T=1)->T{return x+y}
alias::work
main::proc(){a:=alias(i16(7));b:=work(work(7),y=2);if a!=8 || b!=10{panic("calls")}}
''')
        for mode in MODES:
            for flags in [[],native.UNCHECKED]:self.call(*flags,mode=mode)

    def test_fixed_value_results_include_structs_arrays_unions_and_generics(self):
        self.write('''package main
Box::struct($T:typeid){value:T}
Pair::struct{first:int,second:[2]int}
Choice::union{int,Pair}
make_pair::proc(x:int)->Pair{return {x,{x+1,x+2}}}
make_box::proc(x:$T)->Box(T){return {x}}
make_choice::proc()->Choice{return Pair{7,{8,9}}}
main::proc(){a:=make_pair(7);b:=make_box(i16(9));c:=make_choice();p,ok:=c.(Pair);if a.second[1]!=9 || b.value!=9 || !ok || p.first!=7{panic("aggregate")}}
''')
        for mode in MODES:
            for flags in [[],native.UNCHECKED]:self.call(*flags,mode=mode)

    def test_lazy_and_unknown_runtime_paths_do_not_evaluate_failures(self):
        self.write('''package main
bad::proc(x:int)->int{panic("unreached")}
choose::proc(condition:bool)->int{if condition{return bad(7)};return 9}
main::proc(){condition:=false;value:=choose(condition);when false{_=bad(7)};if false{_=bad(7)};if condition{_=bad(7)};if false && bad(7)==7{panic("lazy")};if value!=9{panic("value")}}
''')
        for mode in MODES:self.call(mode=mode)

    def test_type_queries_do_not_execute_the_queried_call(self):
        self.write('package main\nimport "base:intrinsics"\nbad::proc "contextless"(x:int)->int{if x==7 do intrinsics.trap();return x}\nmain::proc(){T::type_of(bad(7));if size_of(T)!=size_of(int){panic("type")}}\n')
        for mode in MODES:
            _,counts=self.call(mode=mode)
            self.assertEqual(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_actual_execution_timeout_remains_an_error(self):
        self.write('package main\nspin::proc()->int{for{};return 7}\nmain::proc(){_=spin()}\n')
        for mode in MODES:
            result,_=self.call('--run-timeout=0.05',mode=mode,command='check',status=1 if mode in ('aggressive','override') else 0)
            if mode in ('aggressive','override'):self.assertIn('timeout',result.stderr)

    def test_debug_default_stays_runtime_and_explicit_aggressive_is_honored(self):
        self.write('package main\nwork::fn(x:int)->int{return x*x}\nmain::proc(){_=work(7)}\n')
        for mode in MODES:
            _,counts=self.call('-debug',mode=mode)
            self.assertEqual(counts.get('evaluation.helper_compilations',0),int(mode in ('aggressive','override')),counts)

    def test_modes_are_not_forwarded_as_odin_flags_and_no_cache_keys_leak(self):
        self.write('package main\nwork::fn(x:int)->int{return x+1}\nmain::proc(){_=work(7)}\n')
        for mode in ['normal','required','normal','aggressive','override','required']:
            self.call(mode=mode,cache=True)
            manifest=json.loads((self.output/'thor-manifest.json').read_text())
            self.assertFalse(any('--comptime' in flag for flag in manifest['odin_flags']))

    def test_invalid_values_and_implicit_source_option_positions(self):
        self.write('package main\nmain::proc(){}\n')
        for value in ['', 'none','force','invalid']:
            self.call(mode=value,command='check',status=2)
        for args in [['check','--comptime','required',str(self.app)],['check','--comptime=aggressive',str(self.app)],['check','--comptime','normal']]:
            result=subprocess.run([str(native.RUST),*args,'--output-dir',str(self.output)],cwd=self.app,capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_many_ordinary_scalar_calls_share_verification_and_native_batch(self):
        body='\n'.join(f'value_{i}:=work({i});if value_{i}!={i+1}{{panic("batch")}}' for i in range(48))
        self.write('package main\nwork::proc(x:int)->int{return x+1}\nmain::proc(){'+body+'}\n')
        _,counts=self.call(mode='aggressive')
        self.assertEqual(counts.get('evaluation.policy_verifications'),1,counts)
        self.assertLessEqual(counts.get('evaluation.helper_compilations',0),2,counts)

    def test_sdk_calls_builtin_aliases_and_struct_inputs(self):
        self.write('''package main
import "core:math"
Pair::struct{x,y:int}
sum::proc(pair:Pair)->int{return pair.x+pair.y}
absolute::math.abs
main::proc(){
 a:=sum(Pair{3,4});b:=absolute(-7);c:=math.sqrt(f64(49))
 if a!=7 || b!=7 || c!=7{panic("SDK values")}
}
''')
        for mode in MODES:
            for flags in [[],native.UNCHECKED]:self.call(*flags,mode=mode)

    def test_runtime_callback_void_and_multiple_results_keep_native_behavior(self):
        self.write('''package main
state:=0
callback::proc(x:int)->int{return x+1}
noop::proc(){}
pair::proc(x:int)->(int,int){state+=1;return x,x+1}
main::proc(){
 cb:=callback;a:=cb(7);noop();b,c:=pair(7)
 if a!=8 || b!=7 || c!=8 || state!=1{panic("runtime forms")}
}
''')
        for mode in MODES:
            _,counts=self.call(mode=mode)
            self.assertEqual(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_required_rejection_is_clear_and_never_executes_impure_calls(self):
        for declarations in [
            'work::proc()->int{fmt.println("must not execute");return 7}',
            'work::proc()->int{values:=make([]int,1);defer delete(values);return 7}',
            'work::proc()->string{return "borrowed"}',
        ]:
            self.write('package main\nimport "core:fmt"\n'+declarations+'\nVALUE::work()\nmain::proc(){_=VALUE}\n')
            for mode in ['aggressive','override']:
                result,_=self.call(mode=mode,status=1,command='check')
                self.assertNotIn('must not execute',result.stdout)
                self.assertIn('not eligible',result.stderr)
                self.assertNotIn('__thor_',result.stderr)

    def test_panic_during_automatic_execution_is_an_error_at_the_call(self):
        sdk=Path(subprocess.check_output([os.environ.get('THOR_ODIN','odin'),'root'],text=True).strip())
        name='noalloc-io-sdk-windows.json' if os.name=='nt' else 'noalloc-io-sdk.json'
        reviewed=json.loads((native.ROOT/'rust/data'/name).read_text())['sdk']
        fatal=['base/runtime/core.odin','base/runtime/core_builtin.odin']
        if any(hashlib.sha256((sdk/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=reviewed[name] for name in fatal):
            self.skipTest('Ordinary-call folding needs the reviewed fatal-reporting SDK profile; explicit #run remains supported.')
        self.write('package main\nwork::proc(x:int)->int{if x==7{panic("policy panic")};return x}\nmain::proc(){_=work(7)}\n')
        for mode in ['aggressive','override']:
            result,_=self.call(mode=mode,status=1,command='check')
            data=json.loads(result.stderr)
            self.assertEqual(data['errors'][0]['pos']['line'],3,data)
            self.assertIn('policy panic',result.stderr)

    def test_unreviewed_fatal_reporting_sdk_keeps_ordinary_calls_at_runtime(self):
        sdk=Path(subprocess.check_output([os.environ.get('THOR_ODIN','odin'),'root'],text=True).strip())
        original=sdk/'base/runtime/core_builtin.odin'
        replacement=self.root/'changed-sdk.odin'
        replacement.write_bytes(original.read_bytes()+b'\n// unreviewed SDK snapshot\n')
        overlay=self.root/'sdk-overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(original):str(replacement)}}))
        self.write('package main\nwork::proc(x:int)->int{if x==7{panic("policy panic")};return x}\nmain::proc(){_=work(7)}\n')
        for mode in ['aggressive','override']:
            _,counts=self.call('--overlay',str(overlay),command='check',mode=mode)
            self.assertEqual(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_help_documents_all_modes_without_loading_a_project(self):
        result=subprocess.run([str(native.RUST),'help','build'],cwd=self.root,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        for text in ['--comptime MODE','required:','normal:','aggressive:','override:']:
            self.assertIn(text,result.stdout)

    def test_vet_apply_validates_with_the_selected_execution_policy(self):
        self.write('package main\nwork::fn(x:int)->int{return x+1}\nVALUE::#runtime work(7)\nmain::proc(){value:=VALUE;if value!=8{panic("value")}}\n')
        self.call('--apply','--vet=immutable',command='vet',mode='override')
        self.assertIn('value:>',self.source.read_text())
        self.call(mode='override')

    def test_required_keeps_compile_time_control_flow_and_comprehensions(self):
        self.write('''package main
size::fn(x:int)->int{return x+1}
VALUES::[size(x) for x in 0..<3]
main::proc(){
 when size(1)==2 {
  values:[size(3)]int
  if len(values)!=4 || VALUES!=[1,2,3]{panic("constants")}
 }else{#panic("wrong branch")}
}
''')
        for mode in MODES:self.call(mode=mode)

    def test_aggressive_proves_local_mutation_readonly_inputs_and_recursion(self):
        self.write('''package main
set::proc(value:^int){value^=7}
local::proc()->int{value:int;set(&value);return value}
sum::proc(#ro values:[]int)->(result:int){for value in values{result+=value};return}
recur::proc(x:int)->int{if x<=0{return 0};return x+recur(x-1)}
main::proc(){
 a:=local();b:=sum([]int{1,2,3});c:=recur(4)
 if a!=7 || b!=6 || c!=10{panic("pure helpers")}
}
''')
        for mode in ['aggressive','override']:
            for flags in [[],native.UNCHECKED]:
                _,counts=self.call(*flags,mode=mode)
                self.assertGreater(counts.get('evaluation.helper_compilations',0),0,counts)

    def test_unsupported_pure_input_can_defer_before_execution(self):
        self.write('''package main
Input::struct{value:int,pointer:^int}
read_only::proc(input:Input)->int{return input.value}
main::proc(){a:=read_only(Input{7,nil});b:=read_only(Input{9,nil});if a!=7 || b!=9{panic("runtime fallback")}}
''')
        for mode in ['aggressive','override']:
            for flags in [[],native.UNCHECKED]:self.call(*flags,mode=mode)

    def test_captured_generic_bindings_do_not_share_allocation_proofs(self):
        self.write('''package main
import "core:mem"
choose::proc($ALLOCATE:bool)->int{
 inner::proc()->int{
  when ALLOCATE{values:=make([]int,1);defer delete(values);return len(values)+6}
  else{return 7}
 }
 return inner()
}
local_size::proc($T:typeid)->int{
 Local::struct{value:T}
 inner::proc()->Local{return {}}
 value:=inner()
 return size_of(value)
}
main::proc(){
 backing:=context.allocator
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,backing,backing)
 defer mem.tracking_allocator_destroy(&tracker)
 context.allocator=mem.tracking_allocator(&tracker)
 a:=choose(false);b:=choose(true)
 c:=local_size(int);d:=local_size(string);e:=local_size(int)
 if a!=7 || b!=7 || c!=size_of(int) || d!=size_of(string) || e!=c || tracker.total_allocation_count!=1 || tracker.current_memory_allocated!=0{panic("captured allocation effect")}
 context.allocator=backing
}
''')
        for mode in ['aggressive','override']:
            for flags in [[],native.UNCHECKED]:self.call(*flags,mode=mode)
