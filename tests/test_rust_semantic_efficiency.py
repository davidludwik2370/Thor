"""Optimization contracts: bounded analysis and unchanged native behavior."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from tests import test_rust_compiler as native


class RustSemanticEfficiencyTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def generated(self):
        return next((self.root/'rust').rglob('main.odin')).read_text()

    def test_using_scope_queries_skip_unrelated_files(self):
        library=self.app/'library';library.mkdir()
        self.source.write_text(self.program('''
if read(Row{7})!=7||library.answer()!=9{panic("using file boundary")}
''','''import "library"
Row::struct{value:int}
read::proc(using row:Row)->int{return value}
'''))
        counts=[]
        for size in (0,200):
            (library/'library.thor').write_text('package library\nanswer::proc()->int{return 9}\n'+
                '\n'.join(f'unused_{n}::proc(x:int)->int{{return x+{n}}}' for n in range(size)))
            profile=self.root/'projection-profile.json'
            result=self.invoke('rust',command='check',
                env={**os.environ,'THOR_PROFILE':str(profile)})
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            counts.append(json.loads(profile.read_text())['counters']['projection.scope_queries'])
        self.assertEqual(counts[0],counts[1])
        for flags in (['-debug'],native.UNCHECKED):
            result=self.invoke('rust',flags=flags)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def sdk_overlay(self, entries):
        sdk=Path(subprocess.check_output([os.environ.get('THOR_ODIN','odin'),'root'],text=True).strip()).resolve()
        replacements={}
        for n,(name,code) in enumerate(entries.items()):
            original=sdk/name
            self.assertFalse(original.exists())
            replacement=self.root/f'overlay-{n}.odin'
            replacement.write_text(code)
            replacements[str(original)]=str(replacement)
        overlay=self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':replacements}))
        return overlay

    def test_record_dependency_chain_precedes_effect_analysis(self):
        overlay=self.sdk_overlay({
            'core/math/thor_efficiency_shapes.odin':'''package math
import time "core:time"
Thor_Efficiency_Box::struct{inner:time.Thor_Efficiency_Inner}
thor_efficiency_read::proc(value:Thor_Efficiency_Box)->int{return value.inner.value}
''',
            'core/time/thor_efficiency_shapes.odin':'''package time
import strconv "core:strconv"
Thor_Efficiency_Inner::struct{value:int,extra:strconv.Thor_Efficiency_Extra}
''',
            'core/strconv/thor_efficiency_shapes.odin':'package strconv\nThor_Efficiency_Extra::struct{flag:bool}\n',
        })
        self.source.write_text(self.program('''values:=[1]math.Thor_Efficiency_Box{}
view:>values[:]
copy:=view[0]
if work(copy)!=0{panic("record")}
''', 'import math "core:math"\nwork::fn(value:math.Thor_Efficiency_Box)->int{return math.thor_efficiency_read(value)}\n'))
        profile = self.root/'profile.json'
        result = self.invoke('rust', command='check', flags=['--overlay',str(overlay)],
                             env={**os.environ, 'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        counters = json.loads(profile.read_text())['counters']
        self.assertEqual(counters.get('permissions.dependency_retries', 0), 0)
        self.assertEqual(counters.get('permissions.analyses', 0), 1)

    def test_pointer_context_namespaces_are_ready_before_permission_graphs(self):
        overlay=self.sdk_overlay({
            'core/math/thor_efficiency_context.odin':'''package math
import "core:mem"
import "core:strconv"
Thor_Efficiency_Context::struct{values:[]int}
thor_efficiency_update::proc(ctx:^Thor_Efficiency_Context)->int{
    mem.zero_slice(ctx.values)
    value,ok:=strconv.parse_int("7")
    if !ok {return 0}
    return value
}
''',
        })
        for preloaded in (False,True):
            prefix='import math "core:math"\n'
            check=''
            if preloaded:
                prefix+='import "core:mem"\nimport "core:strconv"\nALLOCATOR_BYTES::size_of(mem.Allocator)\nPARSER_BYTES::size_of(type_of(strconv.parse_int))\n'
                check='if ALLOCATOR_BYTES<=0||PARSER_BYTES<=0 {panic("dependency shape")}\n'
            self.source.write_text(self.program(check+'''
values:=[]int{1,2};ctx:=math.Thor_Efficiency_Context{values}
answer:>math.thor_efficiency_update(&ctx)
if answer!=7||values[0]!=0 {panic("context")}
''',prefix))
            for flags in [[],native.UNCHECKED]:
                with self.subTest(preloaded=preloaded,flags=flags):
                    profile=self.root/'context-profile.json'
                    result=self.invoke('rust',flags=['--overlay',str(overlay),*flags],
                                       env={**os.environ,'THOR_PROFILE':str(profile)})
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    counters=json.loads(profile.read_text())['counters']
                    # The output-view check needs mem.zero_slice's body. An
                    # unseen namespace requires one bounded discovery retry.
                    retries=0 if preloaded else 1
                    self.assertEqual(counters.get('permissions.dependency_retries',0),retries,counters)
                    self.assertEqual(counters.get('permissions.analyses',0),retries+1,counters)
                    self.assertLessEqual(counters.get('permissions.summaries_built',0),retries+1,counters)
                    if not preloaded:
                        self.assertGreater(counters.get('discovery.call_plan_reuses',0),0,counters)

    def test_output_view_queries_keep_inactive_sdk_branches_unloaded(self):
        overlay=self.sdk_overlay({
            'core/math/thor_efficiency_inactive_view.odin':'''package math
import "core:time"
Thor_Efficiency_View::struct{values:[]int}
thor_efficiency_store_view::proc(out:^Thor_Efficiency_View){
    when #config(THOR_TEST_EXTRA_DEPENDENCY,false){
        time.sleep(0)
    }
    out.values=make([]int,1)
}
''',
        })
        self.source.write_text(self.program('''marker :> 0;_=marker
out:math.Thor_Efficiency_View
math.thor_efficiency_store_view(&out)
out.values[0]=9
if out.values[0]!=9{panic("owned output")}
delete(out.values)
''','import math "core:math"\n'))
        for selected in (False,True):
            for flags in ([],native.UNCHECKED):
                profile=self.root/'inactive-view-profile.json'
                defines=['-define:THOR_TEST_EXTRA_DEPENDENCY=true'] if selected else []
                result=self.invoke('rust',flags=['--overlay',str(overlay),*defines,*flags],
                    env={**os.environ,'THOR_PROFILE':str(profile)})
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                counters=json.loads(profile.read_text())['counters']
                retries=counters.get('permissions.dependency_retries',0)
                if selected:self.assertGreater(retries,0,counters)
                else:self.assertEqual(retries,0,counters)

    def test_generic_view_branches_keep_instantiation_dependent_outputs(self):
        prefix='''Row::struct{values:[]int}
fill::proc(out:^Row,$READONLY:bool){
    when READONLY{view :> []int{7};out.values=view}
    else{out.values=make([]int,1)}
}
'''
        for flags in ([],native.UNCHECKED):
            self.source.write_text(self.program('''out:Row;fill(&out,false)
out.values[0]=9
if out.values[0]!=9{panic("owned generic output")}
delete(out.values)
''',prefix))
            result=self.invoke('rust',flags=flags)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.source.write_text(self.program('out:Row;fill(&out,true);out.values[0]=9',prefix))
            result=self.invoke('rust',command='build',flags=flags)
            self.assertNotEqual(result.returncode,0,result.stdout)
            self.assertIn('read-only slice',result.stderr)

    def test_native_boolean_names_respect_lexical_shadowing(self):
        prefix='''GLOBAL :> []int{7}
read::proc()->int{
    ODIN_DEBUG::true
    ODIN_VALGRIND_SUPPORT::true
    when !ODIN_DEBUG||!ODIN_VALGRIND_SUPPORT{GLOBAL[0]=9}
    return GLOBAL[0]
}
'''
        for flags in (['-debug'],native.UNCHECKED):
            self.source.write_text(self.program('if read()!=7{panic("shadowed native booleans")}',prefix))
            result=self.invoke('rust',flags=flags)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.source.write_text(self.program('_=read()',prefix.replace('::true','::false')))
            result=self.invoke('rust',command='build',flags=flags)
            self.assertNotEqual(result.returncode,0,result.stdout)
            self.assertIn('read-only slice',result.stderr)

    def test_scalar_only_pointer_records_do_not_prefetch_unrelated_namespaces(self):
        counts=[]
        for attempt in range(2):
            overlay=self.sdk_overlay({
                'core/math/thor_efficiency_scalar_context.odin':'''package math
import "core:time"
Thor_Efficiency_Number::struct{value:f64}
thor_efficiency_read_number::proc(value:^Thor_Efficiency_Number)->f64{return value.value}
''',
                'core/time/thor_efficiency_scalar_unused.odin':'package time\n'+
                    '\n'.join(f'thor_efficiency_unused_{n}::proc()->int{{return {n}}}' for n in range(attempt*200)),
            })
            self.source.write_text(self.program('''
values:>[]math.Thor_Efficiency_Number{{7}}
copy:=values[0]
if math.thor_efficiency_read_number(&copy)!=7{panic("scalar context")}
''','import math "core:math"\n'))
            profile=self.root/'scalar-profile.json'
            result=self.invoke('rust',command='check',flags=['--overlay',str(overlay)],
                               env={**os.environ,'THOR_PROFILE':str(profile)})
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            counts.append(json.loads(profile.read_text())['counters']['parser.nodes'])
        self.assertEqual(counts[0],counts[1])

    def test_concrete_index_helpers_have_no_native_shape_branches(self):
        self.source.write_text(self.program('''values:=[3]int{1,2,3}
slice:=values[:];i:=1
if read(values,i)!=2||slice[-1]!=3{panic("index")}
''', 'read::proc(values:[3]int,i:int)->int{return values[i]}\n'))
        for flags in [[], native.UNCHECKED]:
            result = self.invoke('rust', flags=flags)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertNotIn('type_is_enumerated_array', self.generated())

    def test_inline_leaf_functions_do_not_create_scratch_allocators(self):
        self.source.write_text(self.program('if less(Card{1},Card{2})!=true{panic("leaf")}', '''Card::struct{num:int}
less::fn(a,b:Card)->bool{return a.num<b.num}
'''))
        for flags in [['-debug'], native.UNCHECKED]:
            result = self.invoke('rust', flags=flags)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertEqual(result.stderr, '')
            if '-debug' in flags:
                self.assertIn('pure_scratch_', self.generated())
            else:
                self.assertNotIn('pure_scratch_', self.generated())

    def test_allocating_and_unknown_callees_keep_scratch_cleanup(self):
        self.source.write_text(self.program('if outer(4)!=4{panic("scratch")}', '''allocate::fn(n:int)->int{
values:=make([]int,n)
return len(values)
}
outer::fn(n:int)->int{return allocate(n)}
'''))
        for flags in [['-debug'], native.UNCHECKED]:
            result = self.invoke('rust', flags=flags)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertEqual(result.stderr, '')
        self.assertIn('pure_scratch_', self.generated())

    def test_worker_counts_preserve_emission_and_maps(self):
        self.source.write_text(self.program('if other(-1)!=7{panic("other")}', ''))
        (self.app/'other.thor').write_text('''package main
other::proc(i:int)->int{values:>[2]int{3,7};return values[i]}
''')
        snapshots = []
        for jobs in ['1', '4']:
            result = self.invoke('rust', command='transpile', directory=True,
                                 env={**os.environ,'THOR_JOBS':jobs})
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            snapshots.append({str(p.relative_to(self.root/'rust')):p.read_bytes()
                              for p in (self.root/'rust').rglob('*')
                              if p.is_file() and (p.suffix=='.odin' or p.name.endswith('.map.json'))})
        self.assertEqual(snapshots[0], snapshots[1])

    def test_sibling_imports_parse_in_parallel_without_changing_depth_first_emission(self):
        imports=[]
        for n in range(4):
            directory=self.app/f'lib{n}'
            directory.mkdir()
            (directory/'lib.thor').write_text(f'''package lib{n}
import common "../common"
answer::proc()->int{{return common.VALUE+{n}}}
''')
            imports.append(f'import lib{n} "lib{n}"')
        (self.app/'common').mkdir()
        (self.app/'common'/'common.thor').write_text('package common\nVALUE::9\n')
        self.source.write_text(self.program('if lib0.answer()+lib1.answer()+lib2.answer()+lib3.answer()!=42{panic("frontier")}', '\n'.join(imports)))
        snapshots=[]
        for jobs in ['1','4']:
            profile=self.root/f'jobs{jobs}.json'
            result=self.invoke('rust',command='transpile',directory=True,
                               env={**os.environ,'THOR_JOBS':jobs,'THOR_PROFILE':str(profile)})
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            snapshots.append({str(p.relative_to(self.root/'rust')):p.read_bytes()
                              for p in (self.root/'rust').rglob('*')
                              if p.is_file() and (p.suffix=='.odin' or p.name.endswith('.map.json'))})
        self.assertEqual(snapshots[0],snapshots[1])
        profile=json.loads((self.root/'jobs4.json').read_text())
        self.assertGreaterEqual(profile['worker_stages']['parser.source']['calls'],4)
        result=self.invoke('rust',directory=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_big_intermediates_in_scalar_results_keep_scratch_cleanup(self):
        self.source.write_text(self.program('if !larger(){panic("big")}', '''BIG::Integer(123456789012345678901234567890)
larger::fn()->bool{return BIG+1>BIG}
'''))
        for flags in [['-debug'],native.UNCHECKED]:
            result=self.invoke('rust',flags=flags)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(result.stderr,'')
        self.assertIn('pure_scratch_',self.generated())

    def test_prefetched_parse_failures_keep_depth_first_error_order(self):
        for name in ['first','second','leaf']:
            (self.app/name).mkdir()
        self.source.write_text(self.program('', 'import "first"\nimport "second"\n'))
        (self.app/'first'/'first.thor').write_text('package first\nimport "../leaf"\n')
        for name in ['second','leaf']:
            (self.app/name/(name+'.thor')).write_text('package '+name+'\nbroken::proc( {\n')
        outputs=[]
        for jobs in ['1','4']:
            result=self.invoke('rust',command='check',directory=True,flags=['-json-errors'],
                               env={**os.environ,'THOR_JOBS':jobs})
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            packet=json.loads(result.stderr)
            self.assertTrue(all('leaf' in Path(row['pos']['file']).parts for row in packet['errors']))
            outputs.append(result.stderr)
        self.assertEqual(outputs[0],outputs[1])

    def test_record_preflight_does_not_load_unrelated_sdk_procedure_bodies(self):
        self.source.write_text(self.program('view:>[1]f64{0};if math.sin(view[0])!=0{panic("data")}',
                                            'import math "core:math"\n'))
        counts=[]
        for attempt in range(2):
            overlay=self.sdk_overlay({
                'core/math/thor_efficiency_unrelated.odin':'''package math
import time "core:time"
thor_efficiency_unrelated::proc()->time.Duration{return time.Second}
''',
                'core/time/thor_efficiency_unrelated.odin':'package time\n'+
                    '\n'.join(f'thor_efficiency_helper_{n}::proc()->int{{return {n}}}' for n in range(attempt*200)),
            })
            profile=self.root/'profile.json'
            result=self.invoke('rust',command='check',flags=['--overlay',str(overlay)],
                               env={**os.environ,'THOR_PROFILE':str(profile)})
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            counts.append(json.loads(profile.read_text())['counters']['parser.nodes'])
        self.assertEqual(counts[0],counts[1])

    def test_ordinary_sdk_overlay_reaches_odin_without_pure_adaptation(self):
        overlay=self.sdk_overlay({
            'core/math/thor_efficiency_ordinary.odin':'''package math
thor_efficiency_ordinary::proc(n:int)->int{return n+1}
''',
        })
        self.source.write_text(self.program('if math.thor_efficiency_ordinary(41)!=42{panic("SDK overlay")}',
                                            'import math "core:math"\n'))
        for flags in [[],native.UNCHECKED]:
            result=self.invoke('rust',flags=['--overlay',str(overlay),*flags])
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(result.stderr,'')

    def test_recursively_expanding_generic_pointer_preflight_is_bounded(self):
        self.source.write_text(self.program('head:^Node(int);view:>head;_=view',
                                            'Node::struct($T:typeid){value:T,next:^Node([]T)}\n'))
        profile=self.root/'profile.json'
        result=self.invoke('rust',command='transpile',env={**os.environ,'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        counters=json.loads(profile.read_text())['counters']
        self.assertLess(counters.get('analysis.record_shapes',0),256)
