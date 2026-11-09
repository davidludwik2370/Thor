"""Sparse native SDK adaptation keeps package identity and original sources."""
import json
from pathlib import Path
import unittest
from tests import test_rust_checked_calls as checked


class RustSdkTests(unittest.TestCase):
    setUpClass=classmethod(checked.RustCheckedCallTests.setUpClass.__func__)
    setUp=checked.RustCheckedCallTests.setUp
    invoke=checked.RustCheckedCallTests.invoke
    compare=checked.RustCheckedCallTests.compare
    program=checked.RustCheckedCallTests.program
    tracked=checked.RustCheckedCallTests.tracked

    def test_native_nominal_types_and_transitive_helpers_keep_one_identity(self):
        self.tracked(self.program('''d:=time.Second
if work(d)!=1000||time.duration_milliseconds(d)!=1000{panic("native nominal identity")}
''','''import "core:time"
work::fn(d:time.Duration)->f64{return time.duration_milliseconds(d)}
'''))

    def test_checked_sdk_calls_work_in_a_test_entry_package(self):
        self.source.write_text('''package main
import "core:testing"
import "core:time"
work::fn(d:time.Duration)->f64{return time.duration_seconds(d)}
@(test) check_duration::proc(t:^testing.T){d:=time.Second;testing.expect(t,work(d)==1)}
''')
        for engine in ('python','rust'):
            with self.subTest(engine=engine):
                result=self.invoke(engine,command='test')
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('Finished 1 test',result.stdout+result.stderr)

    def test_generic_sdk_mutation_is_allowed_only_on_local_storage(self):
        self.tracked(self.program('n:=3;if work(n)!=n+1{panic("local SDK mutation")}', '''
import "core:slice"
work::fn(n:int)->int{values:=[]int{n,n+1};slice.reverse(values);return values[0]}
'''))
        self.compare(self.program('values:=[]int{1,2};_=work(values)', '''
import "core:slice"
work::fn(values:[]int)->int{slice.reverse(values);return values[0]}
'''),command='check',fail='borrowed from an input')

    def test_native_required_results_are_preserved(self):
        self.compare(self.program('d:=time.Second;_=work(d)', '''
import "core:time"
work::fn(d:time.Duration)->int{time.duration_seconds(d);return 0}
'''),command='check',fail='must be handled')

    def test_checked_odin_helpers_keep_native_indexing_semantics(self):
        self.compare(self.program('values:=[1,2];_=work(values[:])', '''
import "library"
work::fn(values:[]int)->int{return library.last(values)}
'''),files={'library/main.odin':'package library\nlast::proc(values:[]int)->int{return values[-1]}\n'},
                     command='check',fail='-1')

    def test_native_owned_results_and_ordinary_views_keep_separate_lifetimes(self):
        self.tracked(self.program('''bytes:=make([]byte,7);defer delete(bytes)
copy(bytes,"value 3");text:=string(bytes)
owned:=work(text);defer delete(owned)
view:=strings.trim_prefix(text,"value ")
bytes[6]='4'
if owned!="3"||view!="4"{panic("SDK owned result")}
''','''import "core:strings"
work::fn(text:string)->string{return strings.trim_prefix(text,"value ")}
'''))

    def test_sdk_overlays_are_inputs_without_rewriting_the_installed_file(self):
        self.source.write_text(self.program('d:=time.Second;if work(d)!=2{panic("SDK overlay")}', '''
import "core:time"
work::fn(d:time.Duration)->f64{return time.duration_seconds(d)}
'''))
        # Query a generated tree to locate the actual matching SDK instead of
        # hard-coding the developer's Odin installation path.
        result=self.invoke('rust',command='transpile')
        self.assertEqual(result.returncode,0,result.stderr)
        manifest=json.loads((self.root/'rust/thor-manifest.json').read_text())
        sdk=Path(manifest['odin_environment']['ODIN_ROOT'])
        native_directory=next(path.resolve().parent for path in (sdk/'core/time').iterdir()
                              if path.is_symlink() and path.suffix=='.odin')
        original=native_directory/'time.odin'
        before=original.read_bytes()
        replacement=self.root/'time-replacement.odin'
        raw=before.decode()
        needle='return f64(sec) + f64(nsec)/1e9'
        self.assertIn(needle,raw)
        replacement.write_text(raw.replace('package time','package time\nimport "core:math"',1).replace(needle,'return math.cos(f64(0))*2*(f64(sec) + f64(nsec)/1e9)'))
        overlay=self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(original):str(replacement)}}))
        result=self.invoke('rust',flags=['--overlay',str(overlay)])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(before,original.read_bytes())

    def test_mirror_is_sparse_reusable_and_cleaned_without_touching_sdk(self):
        code=self.program('n:=f64(0);if work(n)!=0{panic("SDK result")}', '''
import "core:math"
work::fn(n:f64)->f64{return math.sin(n)}
''')
        self.compare(code,command='check')
        manifest_path=self.root/'rust/thor-manifest.json'
        manifest=json.loads(manifest_path.read_text())
        sdk=Path(manifest['odin_environment']['ODIN_ROOT'])
        generated=sdk/'core/math/math_basic.odin'
        self.assertTrue(generated.is_file())
        self.assertFalse(generated.is_symlink())
        self.assertTrue((sdk/'core/time').is_symlink())
        mapping=json.loads(Path(str(generated)+'.map.json').read_text()) if Path(str(generated)+'.map.json').exists() else None
        # check defaults to ephemeral maps; generated bytes still retain full
        # source origins in memory for native diagnostics.
        self.assertIsNone(mapping)
        originals={path.resolve():path.resolve().read_bytes() for path in (sdk/'core/math').iterdir() if path.is_symlink() and path.suffix=='.odin'}
        result=self.invoke('rust',command='check')
        self.assertEqual(result.returncode,0,result.stderr)
        old_generated=set(manifest['generated'])
        self.source.write_text(self.program('n:=3;_=n'))
        result=self.invoke('rust',command='check')
        self.assertEqual(result.returncode,0,result.stderr)
        latest=json.loads(manifest_path.read_text())
        self.assertEqual(latest['odin_environment'],{})
        for relative in old_generated-set(latest['generated']):
            path=self.root/'rust'/relative
            self.assertFalse(path.is_file() or path.is_symlink(),relative)
        self.assertEqual(originals,{path:path.read_bytes() for path in originals})

    def test_new_layout_does_not_write_through_old_links(self):
        self.source.write_text(self.program('n:=f64(0);_=work(n)', '''
import "core:math"
work::fn(n:f64)->f64{return math.sin(n)}
'''))
        result=self.invoke('rust',command='check')
        self.assertEqual(result.returncode,0,result.stderr)
        manifest=json.loads((self.root/'rust/thor-manifest.json').read_text())
        old=Path(manifest['odin_environment']['ODIN_ROOT'])
        target=(old/'core/time').resolve()
        before=(target/'time.odin').read_bytes()
        self.assertTrue((old/'core/time').is_symlink())
        self.source.write_text(self.program('d:=time.Second;_=work(d)', '''
import "core:time"
work::fn(d:time.Duration)->f64{return time.duration_seconds(d)}
'''))
        result=self.invoke('rust',command='check')
        self.assertEqual(result.returncode,0,result.stderr)
        manifest=json.loads((self.root/'rust/thor-manifest.json').read_text())
        new=Path(manifest['odin_environment']['ODIN_ROOT'])
        self.assertNotEqual(old,new)
        self.assertFalse((new/'core/time').is_symlink())
        self.assertFalse((new/'core/time/time.odin').is_symlink())
        self.assertEqual(before,(target/'time.odin').read_bytes())

    def test_generated_sdk_maps_point_to_original_native_files(self):
        self.source.write_text(self.program('d:=time.Second;_=work(d)', '''
import "core:time"
work::fn(d:time.Duration)->f64{return time.duration_seconds(d)}
'''))
        result=self.invoke('rust',command='transpile')
        self.assertEqual(result.returncode,0,result.stderr)
        manifest=json.loads((self.root/'rust/thor-manifest.json').read_text())
        sdk=Path(manifest['odin_environment']['ODIN_ROOT'])
        generated=sdk/'core/time/time.odin'
        mapping=json.loads(Path(str(generated)+'.map.json').read_text())
        self.assertIn('core/time/time.odin',json.dumps(mapping))
        self.assertIn('checked_',generated.read_text())

    def test_existing_symlink_parent_is_rejected_before_sdk_write(self):
        self.source.write_text(self.program('d:=time.Second;_=work(d)', '''
import "core:time"
work::fn(d:time.Duration)->f64{return time.duration_seconds(d)}
'''))
        result=self.invoke('rust',command='check')
        self.assertEqual(result.returncode,0,result.stderr)
        manifest=json.loads((self.root/'rust/thor-manifest.json').read_text())
        sdk=Path(manifest['odin_environment']['ODIN_ROOT'])
        parent=sdk/'core/time'
        kept=parent.with_name('saved-time')
        parent.rename(kept)
        external=self.root/'external';external.mkdir()
        sentinel=external/'time.odin';sentinel.write_text('untouched')
        parent.symlink_to(external,target_is_directory=True)
        result=self.invoke('rust',command='check')
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertIn('parent must not be a symlink',result.stderr)
        self.assertEqual(sentinel.read_text(),'untouched')
