"""Array reads preserve native readonly-parameter storage without extra copies."""
import shutil
import subprocess
import unittest
from tests import test_rust_compiler as native


class RustArrayCaptureReadTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def same_as_odin(self,source):
        self.source.write_text(source)
        odin_file=self.root/'reference.odin';odin_file.write_text('#+feature using-stmt\n'+source)
        for flags in [['-debug'],native.UNCHECKED]:
            with self.subTest(flags=flags):
                reference=subprocess.run([shutil.which('odin'),'run',str(odin_file),'-file',*flags],
                    cwd=self.root,capture_output=True,text=True,timeout=60)
                result=self.invoke('rust',flags=flags)
                self.assertEqual(reference.returncode,0,reference.stdout+reference.stderr)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(result.stdout,reference.stdout)

    def test_concrete_generic_and_promoted_array_parameters_keep_native_aliasing(self):
        prefix='''import "core:fmt"
change::proc(p:^[9]int)->int{p^[0]=9;return 0}
read::proc(values:[9]int,p:^[9]int)->int{return values[change(p)]}
generic::proc(values:$T,p:^[9]int)->int{return values[change(p)]}
Holder::struct{values:[9]int}
change_holder::proc(p:^Holder)->int{p.values[0]=9;return 0}
promoted::proc(using holder:Holder,p:^Holder)->int{return values[change_holder(p)]}
'''
        self.same_as_odin(self.program('''values:[9]int;values[0]=1
fmt.println(read(values,&values),values[0])
values[0]=1;fmt.println(generic(values,&values),values[0])
holder:Holder;holder.values[0]=1
fmt.println(promoted(holder,&holder),holder.values[0])
''',prefix))

    def test_explicit_array_copies_still_take_a_snapshot(self):
        self.same_as_odin(self.program('''values:[9]int;values[0]=1
fmt.println(read(values,&values),values[0])
''','''import "core:fmt"
change::proc(p:^[9]int)->int{p^[0]=9;return 0}
read::proc(values:[9]int,p:^[9]int)->int{snapshot:=values;return snapshot[change(p)]}
'''))

    def test_value_iteration_arrays_keep_native_storage_behavior(self):
        self.same_as_odin(self.program('''rows:[1][9]int;rows[0][0]=1
for row in rows{fmt.println(row[change(&rows)],rows[0][0])}
''','''import "core:fmt"
change::proc(rows:^[1][9]int)->int{rows^[0][0]=9;return 0}
'''))

    def test_slice_headers_remain_saved_before_the_bound_is_evaluated(self):
        self.same_as_odin(self.program('''a:[9]int;b:[9]int;a[0]=1;b[0]=2
values:=a[:]
fmt.println(read(values,&values,b[:]),values[0])
''','''import "core:fmt"
change::proc(p:^[]int,replacement:[]int)->int{p^=replacement;return 0}
read::proc(values:[]int,p:^[]int,replacement:[]int)->int{return values[change(p,replacement)]}
'''))

    def test_temporary_array_bases_and_bounds_are_evaluated_once(self):
        self.same_as_odin(self.program('''calls:=0;value:=produce(&calls)[bound(&calls)]
fmt.println(value,calls)
''','''import "core:fmt"
produce::proc(calls:^int)->[9]int{calls^+=1;result:[9]int;result[0]=calls^;return result}
bound::proc(calls:^int)->int{calls^+=1;return 0}
'''))

    def test_compile_time_execution_and_warm_cache_preserve_native_aliasing(self):
        source=self.program('fmt.println(#run compute())','''import "core:fmt"
change::proc(p:^[9]int)->int{p^[0]=9;return 0}
read::proc(values:[9]int,p:^[9]int)->int{return values[change(p)]}
compute::proc()->int{values:[9]int;values[0]=1;return read(values,&values)}
''')
        self.source.write_text(source)
        for flags in [['-debug'],native.UNCHECKED]:
            for attempt in range(2):
                with self.subTest(flags=flags,attempt=attempt):
                    result=self.invoke('rust',flags=flags)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertEqual(result.stdout,'9\n')
