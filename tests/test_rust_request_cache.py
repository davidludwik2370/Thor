"""Early evaluation aliases skip helper construction without hiding changes."""
import json
import os
from pathlib import Path
import unittest
from tests import test_rust_compiler as native


class RustRequestCacheTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def profile(self, *, flags=(), command='run', expected='', directory=False):
        path = self.root/'profile.json'
        result = self.invoke('rust', command=command, flags=flags, directory=directory,
                             env={**os.environ, 'THOR_PROFILE':str(path)})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        if command=='run':
            self.assertEqual(result.stdout.strip(),expected)
        return json.loads(path.read_text())

    @staticmethod
    def count(profile, name):
        return profile['counters'].get(name,0)

    def box(self, value=7, extra=''):
        return self.program(f'''box:=#run make_box({value})
if box.value!={value}{{panic("box")}}
{extra}
''','''Box :: struct($T:typeid){value:T}
make_box :: proc(value:$T)->Box(T){return {value}}
''')

    def test_generic_record_hit_skips_helper_construction(self):
        self.source.write_text(self.box())
        for flags in [['-debug'],native.UNCHECKED]:
            cold=self.profile(flags=flags)
            warm=self.profile(flags=flags)
            self.assertGreater(self.count(cold,'evaluation.helper_preparations'),0)
            self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
            self.assertEqual(self.count(warm,'evaluation.helper_preparations'),0)
            self.assertEqual(self.count(warm,'evaluation.helper_compilations'),0)

    def test_runtime_only_edits_keep_dependency_granular_reuse(self):
        prefix='work::proc(n:int)->int{return n+2}\nVALUE::#run work(4)\n'
        self.source.write_text(self.program('if VALUE!=6{panic("value")}',prefix))
        self.profile()
        self.source.write_text(self.program('unused:=10;_=unused;if VALUE!=6{panic("value")}',prefix))
        changed=self.profile()
        self.assertEqual(self.count(changed,'evaluation.helper_preparations'),0)
        self.assertEqual(self.count(changed,'evaluation.helper_compilations'),0)

    def test_output_path_changes_do_not_rebuild_evaluation_helpers(self):
        self.source.write_text(self.box())
        self.profile(flags=['-out:'+str(self.root/'first')])
        warm=self.profile(flags=['-out:'+str(self.root/'second')])
        self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
        self.assertEqual(self.count(warm,'evaluation.helper_preparations'),0)

    def test_arguments_and_generic_local_nominal_identities_are_distinct(self):
        self.source.write_text(self.program('''
Small::struct{x:int}
Large::struct{x,y:int}
a:=#run bytes(Small)
b:=#run bytes(Large)
if a!=8||b!=16{panic("nominal arguments")}
''','bytes::proc($T:typeid)->int{return size_of(T)}\n'))
        self.profile()
        warm=self.profile()
        self.assertGreaterEqual(self.count(warm,'evaluation.request_hits'),2)
        self.assertEqual(self.count(warm,'evaluation.helper_preparations'),0)

    def test_local_record_results_and_using_frames_keep_emission_and_maps(self):
        self.source.write_text(self.program('''
Local::struct{x:int,y:int}
result:=#run copy_value(Local{7,8})
if result.x!=7||result.y!=8||generic(result)!=17{panic("local record")}
''','''copy_value::proc(value:$T)->T{return value}
work::proc(n:int)->int{return n+1}
generic::proc(using value:$T)->int{return #run work(size_of(T))}
'''))
        self.profile(flags=['-debug'])
        def emitted():
            return {str(p.relative_to(self.root/'rust')):p.read_bytes() for p in (self.root/'rust').rglob('*')
                    if p.is_file() and (p.suffix=='.odin' or p.name.endswith('.map.json'))}
        cold=emitted()
        warm=self.profile(flags=['-debug'])
        self.assertEqual(self.count(warm,'evaluation.helper_preparations'),0)
        self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
        self.assertEqual(cold,emitted())

    def test_unsaved_overlays_and_overload_additions_change_request_identity(self):
        self.source.write_text(self.box())
        self.profile()
        changed=self.box().replace('return {value}','return {value+1}').replace('box.value!=7','box.value!=8')
        replacement=self.root/'unsaved.thor'
        replacement.write_text(changed)
        overlay=self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(self.source):str(replacement)}}))
        self.profile(flags=['--overlay',str(overlay)])
        warm=self.profile(flags=['--overlay',str(overlay)])
        self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
        self.assertEqual(self.source.read_text(),self.box())
        self.source.write_text(self.box().replace('make_box :: proc', '''specific::proc(value:int)->Box(int){return {value+1}}
make_box :: proc''').replace('make_box :: proc(value:$T)->Box(T)',
                              'fallback :: proc(value:$T)->Box(T)').replace('main :: proc()',
                              'make_box::proc{specific,fallback}\nmain :: proc()').replace('box.value!=7','box.value!=8'))
        changed=self.profile()
        self.assertGreater(self.count(changed,'evaluation.helper_compilations'),0)

    def test_changed_transitive_source_and_new_impurity_invalidate(self):
        self.source.write_text(self.box())
        self.profile()
        self.source.write_text(self.box().replace('return {value}','return {value+1}').replace('box.value!=7','box.value!=8'))
        changed=self.profile()
        self.assertGreater(self.count(changed,'evaluation.helper_compilations'),0)
        self.source.write_text(self.box().replace('Box ::','STATE:int\nBox ::').replace('return {value}','return {value+STATE}'))
        result=self.invoke('rust',command='check')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('runtime storage',result.stderr)

    def test_resource_contents_and_missing_exists_are_revalidated(self):
        asset=self.app/'asset.txt'
        asset.write_text('old')
        self.source.write_text(self.program('''v:=#run work();defer delete(v.text);if v.text!="fixed"{panic("asset")}
''','DATA::#load("asset.txt")\nBox::struct{text:string}\nwork::proc()->Box{return {"fixed"}}\n'))
        self.profile()
        warm=self.profile()
        self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
        asset.write_text('new')
        changed=self.profile()
        self.assertGreater(self.count(changed,'evaluation.helper_compilations'),0)
        self.source.write_text(self.program('if !(#run work()){panic("exists")}',
                                           'PRESENT::#exists("flag")\nwork::proc()->bool{return true}\n'))
        self.profile()
        (self.app/'flag').write_text('present')
        changed=self.profile()
        self.assertGreater(self.count(changed,'evaluation.helper_compilations'),0)

    def test_large_generic_type_constants_are_fingerprinted_without_json_integer_loss(self):
        self.source.write_text(self.program('if generic(Tag(1267650600228229401496703205376))!=1{panic("large binding")}',
            '''Tag::struct($N:u128){}
work::proc(n:int)->int{return n+1}
generic::proc($T:typeid)->int{return #run work(size_of(T))}
'''))
        self.profile()
        warm=self.profile()
        self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
        self.assertEqual(self.count(warm,'evaluation.helper_preparations'),0)

    def test_constant_resource_paths_reuse_results_and_revalidate_all_branches(self):
        asset=self.app/'asset.txt'
        other=self.app/'other.txt'
        asset.write_text('abc')
        other.write_text('other')
        prefix='''NAME::("asset.txt" when #config(USE_ASSET,true) else "other.txt")
ALIAS::NAME
DATA::#load(ALIAS)
PRESENT::#exists("missing" when ODIN_OS==.Darwin else "other-missing")
work::proc()->int{return len(DATA)}
'''
        for flags in [[],native.UNCHECKED]:
            with self.subTest(flags=flags):
                self.source.write_text(self.program('if (#run work())!=3{panic("data")}',prefix))
                self.profile(flags=flags)
                warm=self.profile(flags=flags)
                self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
                self.assertEqual(self.count(warm,'evaluation.helper_preparations'),0)
                other.write_text(other.read_text()+'changed')
                changed=self.profile(flags=flags)
                self.assertGreater(self.count(changed,'evaluation.helper_compilations'),0)
                selected=self.program('if (#run work())!=len(DATA){panic("selected data")}',prefix)
                self.source.write_text(selected)
                self.profile(flags=[*flags,'-define:USE_ASSET=false'])
                warm=self.profile(flags=[*flags,'-define:USE_ASSET=false'])
                self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
                asset.write_text('new data')
                self.source.write_text(self.program('if (#run work())!=8{panic("changed data")}',prefix))
                self.profile(flags=flags)
                asset.write_text('abc')

    def test_owned_cached_results_still_materialize_independently(self):
        self.source.write_text(self.program('''
a:=#run values();b:=#run values()
defer delete(a.data);defer delete(b.data)
a.data[0]=9
if a.data[0]!=9||b.data[0]!=3{panic("ownership")}
''','Box::struct{data:[]int}\nvalues::proc()->Box{v:=make([]int,1);v[0]=3;return {v}}\n'))
        self.profile(flags=['-debug'])
        warm=self.profile(flags=['-debug'])
        self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
        self.assertEqual(self.count(warm,'evaluation.helper_preparations'),0)

    def test_corrupt_alias_and_result_packets_fall_back(self):
        self.source.write_text(self.box())
        self.profile()
        for path in (self.root/'cache/request-v1').glob('*.json'):
            path.write_text('{')
        recovered=self.profile()
        self.assertGreater(self.count(recovered,'evaluation.helper_preparations'),0)
        self.assertEqual(self.count(recovered,'evaluation.helper_compilations'),0)
        for path in (self.root/'cache/eval-v1').glob('*.json'):
            path.write_text('{')
        recovered=self.profile()
        self.assertGreater(self.count(recovered,'evaluation.helper_compilations'),0)

    def test_clear_and_disabled_cache_do_not_use_aliases(self):
        self.source.write_text(self.box())
        self.profile()
        bypass=self.profile(flags=['--no-cache'])
        self.assertEqual(self.count(bypass,'evaluation.request_hits'),0)
        self.assertGreater(self.count(bypass,'evaluation.helper_compilations'),0)
        cleared=self.profile(flags=['--clear-cache'])
        self.assertEqual(self.count(cleared,'evaluation.request_hits'),0)
        self.assertGreater(self.count(cleared,'evaluation.helper_compilations'),0)

    def test_branch_modes_and_nested_generic_frames_keep_reference_order(self):
        self.source.write_text(self.program('''
if generic(true)!=5||generic(false)!=9{panic("branches")}
#switch 3{case 1:_=#run bad();case 2..=4:_=#run good();case:_=0}
''','''work::proc(n:int)->int{return n+2}
generic::proc($B:bool)->int{when B{return #run work(3)}else{return #run work(7)}}
bad::proc()->int{panic("inactive evaluation")}
good::proc()->int{return 1}
'''))
        self.profile()
        warm=self.profile()
        self.assertGreater(self.count(warm,'evaluation.request_hits'),0)
        self.assertEqual(self.count(warm,'evaluation.helper_preparations'),0)


if __name__=='__main__':
    unittest.main()
