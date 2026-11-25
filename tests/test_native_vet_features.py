"""Native policies on Thor expressions must observe original reads and scopes."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from tests import test_rust_compiler as base

class NativeFeatureVetTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp

    def check(self,body,flag,*,declarations='',fail=None,header='',command='check',flags=()):
        self.source.write_text(header+'package main\nimport "core:fmt"\n'+declarations+'\nmain::proc(){'+body+'}\n')
        result=subprocess.run([str(getattr(self,'compiler',base.RUST)),command,str(self.app),'--output-dir',str(self.root/'generated'),
                               '-thread-count:1','-json-errors',flag,*flags],capture_output=True,text=True,cwd=self.root,timeout=90,env={**os.environ,'THOR_NATIVE_CHECK_COMPILER':str(base.RUST)})
        self.assertEqual(result.returncode,1 if fail else 0,result.stdout+result.stderr)
        packet=json.loads(result.stderr or '{"errors":[]}')
        if fail:
            self.assertTrue(any(fail.lower() in ' '.join(e['msgs']).lower() for e in packet['errors']),packet)
            self.assertTrue(all(e.get('pos',{}).get('file')==str(self.source) for e in packet['errors']),packet)
        return result,packet

    def test_native_tab_policy_excludes_support_but_checks_original_sources(self):
        declarations='Mode :: enum {A, B}\nFlags :: bit_set[Mode]\nmerge :: proc(a, b: $T) -> T {return a | b}\n'
        body='\n\tbits := merge(Flags{.A}, Flags{.B})\n\tvalues := [x for x in 0..<2]\n\tfmt.println(f"{bits} {values}")\n'
        flags=['-vet','-strict-style','-vet-style','-warnings-as-errors','-disallow-do']
        self.check(body,'-vet-tabs',declarations=declarations,flags=flags)
        self.check(body.replace('\tbits','    bits'),'-vet-tabs',declarations=declarations,flags=flags,fail='tabs')
        self.source.write_text('package main\nmain :: proc() {extra()}\n')
        extra=self.app/'extra.odin'
        extra.write_text('package main\nextra :: proc() {\n    value := 7\n    _ = value\n}\n')
        result=subprocess.run([str(base.RUST),'check',str(self.app),'-vet-tabs','-json-errors',
            '--output-dir',str(self.root/'native-tabs')],capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        errors=json.loads(result.stderr)['errors']
        self.assertTrue(any('tabs'in str(e['msgs'])and e.get('pos',{}).get('file')==str(extra)for e in errors),errors)

    def test_fstring_reads_and_casts_are_checked_in_caller(self):
        for flag,decl,expr,error in [('-vet-nil-deref','p:^int','p^','always nil'),
                                    ('-vet-uninitialized','x:int=---','x','before anything'),
                                    ('-vet-cast','x:=1','cast(int)x','unneeded cast')]:
            with self.subTest(flag=flag):self.check(decl+';fmt.println(f"{'+expr+'}")',flag,fail=error)

    def test_comprehension_reads_and_shadowing_are_checked_in_caller(self):
        for flag,decl,expr,error in [('-vet-nil-deref','p:^int','p^','always nil'),
                                    ('-vet-uninitialized','x:int=---','x','before anything'),
                                    ('-vet-cast','x:=1','cast(int)x','unneeded cast')]:
            with self.subTest(flag=flag):self.check(decl+';v:=['+expr+' for _ in 0..<3];fmt.println(v)',flag,fail=error)
        self.check('x:=10;v:=[x for x in 0..<3];fmt.println(x,v)','-vet-shadowing',fail='shadows')

    def test_short_circuit_keeps_construction_guarded(self):
        self.check('p:^int;ok:=false && len([p^ for _ in 0..<3])>0;fmt.println(ok)','-vet-nil-deref',fail='always nil')
        self.check('p:^int;ok:=true || len([p^ for _ in 0..<3])>0;fmt.println(ok)','-vet-nil-deref',fail='always nil')
        self.check('p:^int;ok:=true && len([p^ for _ in 0..<3])>0;fmt.println(ok)','-vet-nil-deref',fail='always nil')

    def test_conditional_construction_keeps_branches_guarded(self):
        self.check('p:^int;v:=true ? []int{} : [p^ for _ in 0..<3];fmt.println(v)','-vet-nil-deref',fail='always nil')
        self.check('p:^int;v:=false ? []int{} : [p^ for _ in 0..<3];fmt.println(v)','-vet-nil-deref',fail='always nil')

    def test_native_nil_guards_and_compile_time_branches_are_preserved(self):
        self.check('p:^int;if p!=nil {v:=[p^ for _ in 0..<3];fmt.println(v)};fmt.println(p)','-vet-nil-deref')
        self.check('p:^int;ok:=p!=nil && len([p^ for _ in 0..<3])>0;fmt.println(ok)','-vet-nil-deref')
        self.check('p:^int;when false {fmt.println([p^ for _ in 0..<3])};fmt.println(p)','-vet-nil-deref')

    def test_if_initializer_is_in_scope_before_condition_construction(self):
        self.check('if n:=3;len([x for x in 0..<n])>0 {fmt.println(n)}','-vet-nil-deref')

    def test_iteration_condition_and_post_keep_their_control_flow(self):
        self.check('x:=1;p:=&x;for i:=0;i<2 && len([p^ for _ in 0..<1])>0;i+=1 {continue};fmt.println(x)','-vet-nil-deref')
        self.check('p:^int;for i:=0;i<2 && len([p^ for _ in 0..<1])>0;i+=1 {break};fmt.println(p)','-vet-nil-deref',fail='always nil')

    def test_construction_in_control_initializers_and_post_statements(self):
        for body in [
            'if n:=len([x for x in 0..<3]);n>0 {fmt.println(n)}',
            'for i:=len([x for x in 0..<3]);i>0;i-=1 {fmt.println(i)}',
            'for i:=0;i<2;i=len([x for x in 0..<3]) {continue};fmt.println("ok")',
            'for n:=3;x in [i for i in 0..<n] {fmt.println(x)}',
            'switch n:=3;len([x for x in 0..<n]) {case 3:fmt.println(n)}',
        ]:
            with self.subTest(body=body):self.check(body,'-vet-cast')

    def test_fallback_lists_with_discarded_results_keep_context(self):
        self.check('_,v:=get() or_else (_, [x for x in 0..<3]);fmt.println(v)','-vet-cast',declarations='get::proc()->(int,[]int,bool){return 0,nil,false}\n')

    def test_construction_in_switch_matches_and_values(self):
        self.check('p:^int;switch {case len([p^ for _ in 0..<1])>0:fmt.println("yes");case:fmt.println("no")}','-vet-nil-deref',fail='always nil')
        self.check('p:^int;fmt.println(f"{switch {case len([p^ for _ in 0..<1])>0:1;case:0}}")','-vet-nil-deref',fail='always nil')
        self.check('switch len([x for x in 0..<3]) {case 3:fmt.println("yes");fallthrough;case:fmt.println("done")}','-vet-cast')

    def test_optional_fallback_construction_and_named_arguments(self):
        decl='get::proc()->([]int,bool){return nil,false}\n'
        self.check('v:=get() or_else [x for x in 0..<3];fmt.println(v)','-vet-cast',declarations=decl)
        self.check('fmt.println(v=[x for x in 0..<3])','-vet-cast',fail='parameter',declarations='')
        self.check('take(a=1,b=[x for x in 0..<3])','-vet-cast',declarations='take::proc(a:int,b:[]int){fmt.println(a,b)}\n')

    def test_comprehension_collections_and_spreads(self):
        for expression in ['[]int{x for x in 0..<3}', '[dynamic]int{x for x in 0..<3}',
                           'map[int]int{x=x*x for x in 0..<3}', 'set[int]{x for x in 0..<3}',
                           '[..a for a in ([][]int{{1,2},{3}})]']:
            with self.subTest(expression=expression):self.check('v:='+expression+';fmt.println(v)','-vet-cast')

    def test_native_usage_and_imports_ignore_compiler_support(self):
        for flag in ['-vet-unused','-vet-unused-imports','-vet-unused-procedures']:
            self.check('v:=[x for x in 0..<3];fmt.println(v)',flag,flags=['-vet-packages:main'])
        self.check('unused:=[x for x in 0..<3];fmt.println("ok")','-vet-unused-variables',fail='not used')

    def test_deferred_construction_stays_in_deferred_body(self):
        self.check('x:=1;p:=&x;defer fmt.println([p^ for _ in 0..<1]);fmt.println(x)','-vet-nil-deref')

    def test_flow_inside_dynamic_fstring_widths(self):
        self.check('p:^int;fmt.println(f"{1:{p^}}")','-vet-nil-deref',fail='always nil')

    def test_original_caller_reads_precede_later_construction(self):
        decl='storage:int\nchange::proc(p:^^int)->int{p^=&storage;return 1}\ntake::proc(a:int,b:[]int){fmt.println(a,b)}\n'
        self.check('p:^int;take(p^,[change(&p) for _ in 0..<1])','-vet-nil-deref',declarations=decl,fail='always nil')


    def test_typed_loops_and_collection_sources(self):
        self.check('a:=[1,2];v:=[x for x:f64 in 0..<3];w:=[i for x,(i:i8) in a];for i:f64 in 0..<2 {fmt.println(i)};fmt.println(v,w)','-vet-cast')
        self.check('s:=set[int]{1,2};v:=[x for x in s];fmt.println(v)','-vet-cast')
        self.check('m:map[int]int;m[1]=2;v:=map[int]int{..part for part in [m]};s:=set[int]{1,2};w:=set[int]{..part for part in [s]};fmt.println(v,w)','-vet-cast')

    def test_assignment_addresses_precede_rhs_construction(self):
        declarations='slot:int\nchange::proc(p:^^int)->int{p^=&slot;return 1}\n'
        self.check('p:^int;p^=len([change(&p) for _ in 0..<1]);fmt.println(p)','-vet-nil-deref',declarations=declarations,fail='always nil')

    def test_compile_time_collections_and_interpolation(self):
        self.check('fmt.println(VALUES,TEXT)','-vet-unused',declarations='VALUES :: [x*x for x in 0..<3]\nTEXT :: #run text()\ntext::fn()->string{return f"value {VALUES[1]}"}\n')
        self.check('fmt.println(typeid_of(type_of([x for x in 0..<3])))','-vet-unused')

    def test_unevaluated_queries_do_not_read_or_construct_values(self):
        self.check('p:^int;fmt.println(size_of([p^ for _ in 0..<3]),p)','-vet-nil-deref')
        self.check('p:^int;s::size_of;fmt.println(s([p^ for _ in 0..<3]),p)','-vet-nil-deref')

    def test_other_thor_operators_keep_user_reads_visible(self):
        for expression in ['p^**2','p^ in [1,2]','[p^,2][-1]','Row{p^}+Row{1}']:
            with self.subTest(expression=expression):self.check('p:^int;fmt.println(f"{'+expression+'}")','-vet-nil-deref',declarations='Row::struct{x:int}\n',fail='always nil')

    def test_switch_expressions_in_formatting_and_comprehensions(self):
        self.check('x:=1;fmt.println(f"{switch {case x>0:x;case:0}}");v:=[switch {case x>0:x;case:0} for x in 0..<3];fmt.println(v)','-vet-cast')
        self.check('p:^int;fmt.println(f"{switch {case true:p^;case:0}}")','-vet-nil-deref',fail='always nil')

    def test_source_style_checks_see_original_not_generated_layout(self):
        for flag,body in [('-vet-tabs','\n    fmt.println(f"hello")\n'),
                          ('-vet-style','\n\tv := []int{\n\t\t1\n\t}\n\tfmt.println(f"{v}")\n'),
                          ('-strict-style','\n\tx:=1;\n\tfmt.println(f"{x}")\n')]:
            with self.subTest(flag=flag):self.check(body,flag,fail={'-vet-tabs':'tabs','-vet-style':"Missing ','",'-strict-style':'semicolon'}[flag])
        self.check('\n\tfmt.println(f"hello")\n','-vet-tabs')
        self.check('\n\tswitch len([x for x in 0..<3]) {\n\t\tcase 3:fmt.println(3)\n\t}\n','-strict-style',fail='case')
        self.check('fmt.println(f"hello")','-vet-style',flags=['-vet-packages:other'])
        self.check('\n    fmt.println(f"hello")\n','-vet-tabs',header='#+vet !tabs\n')

    def test_shadowing_secondary_line_and_unicode_positions_are_original(self):
        _,packet=self.check('\n x:=1\n values:=[x for x in 0..<3]\n fmt.println(x,values)\n','-vet-shadowing',header='// 😀 header\n',fail='shadows')
        self.assertTrue(any('at line 6' in ' '.join(e['msgs']) for e in packet['errors']),packet)
        self.assertTrue(any(e['pos']['line']==7 for e in packet['errors']),packet)

    def test_checked_and_unchecked_runtime_output_is_unchanged(self):
        self.source.write_text('''package main
import "core:fmt"
main::proc(){
    total:=0
    for n:=3;x in [i for i in 0..<n] {total+=x}
    values:=[x*x for x in 0..<3]
    if total!=3||len(values)!=3||values[2]!=4 {panic("result")}
    fmt.println(f"sum {total}")
    delete(values)
}
''')
        compiler=getattr(self,'compiler',base.RUST)
        binary=self.root/('program.exe' if os.name=='nt' else 'program')
        for config in [[],base.UNCHECKED]:
            original=None
            for policies in [[],['-vet']]:
                result=subprocess.run([str(compiler),'build',str(self.app),'--output-dir',str(self.root/'runtime-generated'),
                    '--track-allocations','off','--no-sanitize','-thread-count:1','-out:'+str(binary),*config,*policies],
                    cwd=self.root,capture_output=True,text=True,timeout=90,
                    env={**os.environ,'THOR_NATIVE_CHECK_COMPILER':str(base.RUST)})
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                native={str(p.relative_to(self.root/'runtime-generated')):p.read_bytes() for p in (self.root/'runtime-generated').rglob('*.odin')}
                run=subprocess.run([str(binary)],cwd=self.root,capture_output=True,text=True,timeout=10)
                self.assertEqual((run.returncode,run.stdout),(0,'sum 3\n'),run.stderr)
                current=native
                if original is None:original=current
                else:self.assertEqual(current,original)

    def test_explicit_unused_declarations_still_suppress_unused_policies(self):
        self.check('@(unused) x:=1;fmt.println(f"ok")','-vet-unused')
        self.check('@(unused) x:int=---;fmt.println(f"ok")','-vet-unused',flags=['-vet-uninitialized'])
        self.check('@(unused) x:=1;fmt.println("ok")','-vet-unused')
        self.check('fmt.println(f"ok")','-vet-unused-procedures',declarations='@(unused) dead::proc(){}\n',flags=['-vet-packages:main'])

    def test_using_and_polymorphic_aliases_keep_their_meaning(self):
        declarations='Row::struct{x:int}\ndouble::proc(x:$T)->T{return x*2}\nshow::proc(x:$T,expression:>#caller_expression(x)){fmt.println(f"{expression} {x}")}\n'
        self.check('row:=Row{1};using row;v:=[double(x) for _ in 0..<3];alias::show;alias(v);fmt.println(f"{x}")','-vet-cast',declarations=declarations)
        self.check('row:=Row{1};using row;fmt.println(f"{x}")','-vet-using-stmt',declarations='Row::struct{x:int}\n',fail='using')
        self.check('row:=Row{1};using row;fmt.println(f"{x}")','-vet-using-stmt',header='#+feature using-stmt\n',declarations='Row::struct{x:int}\n')
        self.check('row:=Row{1};using row;fmt.println(f"{x}")','-vet-using-stmt',header='#+vet !using-stmt\n',declarations='Row::struct{x:int}\n')

    def test_contextual_literals_keep_declared_field_and_parameter_types(self):
        for body,decl in [
            ('v:[2]u8=[1,2];fmt.println(f"{v}")',''),
            ('v:=Row{values=[1,2]};fmt.println(f"{v}")','Row::struct{values:[2]u8}\n'),
            ('take([1,2]);fmt.println(f"ok")','take::proc(v:[2]u8){fmt.println(v)}\n'),
            ('v:[2]Color=[.red,.blue];fmt.println(f"{v}")','Color::enum{red,blue}\n'),
            ('flag:=true;v:[1]Color=flag ? [.red] : [.blue];fmt.println(f"{v}")','Color::enum{red,blue}\n'),
            ('v:=[][2]int{[x,x] for x in 0..<3};fmt.println(f"{v}")',''),
            ('fmt.println(make_values(),f"ok")','make_values::proc()->[2]u8{return [1,2]}\n'),
        ]:
            with self.subTest(body=body):self.check(body,'-vet-cast',declarations=decl)

    def test_imported_generic_context_keeps_private_element_type(self):
        library=self.app/'library';library.mkdir()
        (library/'library.thor').write_text('package library\n@(private="file") Color::enum{red,blue}\nsample::proc()->Color{return .red}\nconsume::proc(values:[2]$T,example:T){_=values;_=example}\n')
        self.check('example:=library.sample();library.consume([.red,.blue],example);fmt.println(f"ok")','-vet-cast',declarations='import "library"\n')

    def test_type_switch_sources_are_expanded_before_the_binding_header(self):
        self.check('switch value in ([Choice(x) for x in 0..<3])[0] {case int:fmt.println(value);case string:fmt.println(value)}','-vet-cast',declarations='Choice::union{int,string}\n')
        self.check('p:^int;switch value in ([Choice(p^) for _ in 0..<3])[0] {case int:fmt.println(value);case string:fmt.println(value)}','-vet-nil-deref',declarations='Choice::union{int,string}\n',fail='always nil')

    def test_case_local_type_witnesses_keep_their_scope(self):
        self.check('switch value in Choice(1) {case int:v:=true ? [value for _ in 0..<3] : []int{};fmt.println(v);case string:fmt.println(value)}','-vet-cast',declarations='Choice::union{int,string}\n')
        self.check('switch len([x for x in 0..<3]) {case 3:v:=true ? [x for x in 0..<3] : []int{};fmt.println(v);case:fmt.println("none")}','-vet-cast')

    def test_native_package_scope_survives_generated_header_tags(self):
        self.check('x:=1;fmt.println(f"{cast(int)x}")','-vet-cast',flags=['-vet-packages:other'])
        self.check('x:=1;fmt.println(f"{cast(int)x}")','-vet-cast',header='#+vet !unused\n',flags=['-vet-packages:other'],fail='unneeded cast')

    def test_original_style_verdicts_match_native_for_mixed_thor_files(self):
        cases=[
            ('-vet-tabs','', '\n    fmt.println("value")\n'),
            ('-vet-tabs','', '\n\tfmt.println("value")\n'),
            ('-vet-style','Row::struct{\n\tx:int\n}\n',''),
            ('-strict-style','', '\n\tx:=1;\n\t_=x\n'),
            ('-strict-style','', '\n\tx:=1;_=x\n'),
            ('-strict-style','', '\n\tif true {fmt.println(1)}\n\telse {fmt.println(2)}\n'),
            ('-strict-style','', '\n\tif true\n\t{fmt.println(1)}\n'),
            ('-vet-style','', '\n\tfmt.println(1,)\n'),
        ]
        oracle=self.root/'oracle.odin'
        for flag,declarations,body in cases:
            with self.subTest(flag=flag,body=body,declarations=declarations):
                oracle.write_text('package main\nimport "core:fmt"\n'+declarations+'\nmain::proc(){fmt.println("hello");'+body+'}\n')
                result=subprocess.run([os.environ.get('THOR_ODIN','odin'),'check',str(oracle),'-file',flag,'-thread-count:1'],capture_output=True,text=True,timeout=30)
                self.assertIn(result.returncode,[0,1],result.stderr)
                self.source.write_text(oracle.read_text().replace('fmt.println("hello")','fmt.println(f"hello")'))
                thor=subprocess.run([str(getattr(self,'compiler',base.RUST)),'check',str(self.app),flag,'-thread-count:1','--output-dir',str(self.root/'style-generated')],
                    capture_output=True,text=True,timeout=60,env={**os.environ,'THOR_NATIVE_CHECK_COMPILER':str(base.RUST)})
                self.assertEqual(thor.returncode,result.returncode,thor.stderr+'\nOdin: '+result.stderr)

    def test_explicit_allocator_policy_checks_user_calls_only(self):
        self.check('v:=[x for x in 0..<3];fmt.println(f"{v}")','-vet-cast',header='#+vet explicit-allocators\n')
        self.check('v:=[x for x in 0..<3];delete(v);fmt.println(f"done")','-vet-cast',header='#+vet explicit-allocators\n',fail='allocator')

class PythonNativeFeatureVetTests(NativeFeatureVetTests):
    compiler=base.PYTHON

if __name__=='__main__':unittest.main()
