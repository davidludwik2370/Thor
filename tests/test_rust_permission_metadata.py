"""Per-analysis metadata caches include stable partially known record fields."""
import json
import os
import unittest
from tests import test_rust_compiler as native


class RustPermissionMetadataTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program
    compare = native.RustCompilerTests.compare

    def test_sdk_callback_record_copies_preserve_scalar_and_slice_permissions(self):
        prefix = '''import ui "vendor:microui"
read::proc(rows:[]ui.Context)->int{return int(rows[0].frame)}
write::proc(rows:[]ui.Context){rows[0].frame+=1}
'''
        body = '''rows:[1]ui.Context;rows[0].frame=7
view:>rows[:]
copy:=view[0];copy.frame=9
if read(view)!=7||int(copy.frame)!=9{panic("inline record copy")}
write(rows[:]);if read(view)!=8{panic("mutable alias")}
'''
        self.source.write_text(self.program(body,prefix))
        for flags in [['-debug'],native.UNCHECKED]:
            with self.subTest(flags=flags):
                result=self.invoke('rust',flags=[*flags,'-ignore-warnings'])
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(result.stderr,'')
        self.compare(self.program('rows:[1]ui.Context;view:>rows[:];write(view)',prefix),
                     command='check',fail='read-only slice')

    def test_generic_record_metadata_is_separate_for_each_element_type(self):
        prefix='''Holder::struct($T:typeid){values:[]T,callback:proc(rawptr)->bool}
first::proc(value:Holder($T))->T{return value.values[0]}
change::proc(value:Holder($T),x:T){value.values[0]=x}
'''
        source=self.program('''a:=[1]i32{7};b:=[1]f64{2.5}
ha:=Holder(i32){values=a[:]};hb:=Holder(f64){values=b[:]}
change(ha,9);change(hb,3.5)
if first(ha)!=9||first(hb)!=3.5{panic("metadata binding identity")}
''',prefix)
        for flags in [[],native.UNCHECKED]:self.compare(source,flags=flags)

    def test_sdk_metadata_queries_do_not_repeat_for_every_reference(self):
        prefix='''import ui "vendor:microui"
read::proc(rows:[]ui.Context)->int{return int(rows[0].frame)}
'''
        reads='\n'.join('total+=read(view)' for _ in range(24))
        self.source.write_text(self.program('rows:[1]ui.Context;view:>rows[:];total:=0\n'+reads+'\nif total!=0{panic("read")}',prefix))
        profile=self.root/'profile.json'
        result=self.invoke('rust',command='check',env={**os.environ,'THOR_PROFILE':str(profile)})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        counters=json.loads(profile.read_text())['counters']
        # Count work rather than asserting a machine-dependent wall-clock time.
        self.assertLess(counters.get('lookup.calls',0),1_000_000)
