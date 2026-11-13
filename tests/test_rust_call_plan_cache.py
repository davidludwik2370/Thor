"""Complete call plans stay separate across bindings and semantic changes."""
import json
import os
import unittest
from tests import test_rust_compiler as native


class RustCallPlanCacheTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    program=native.RustCompilerTests.program

    def profile(self,code,flags=(),command='run'):
        self.source.write_text(code)
        path=self.root/'profile.json'
        result=self.invoke('rust',command=command,flags=flags,
            env={**os.environ,'THOR_PROFILE':str(path)})
        return result,json.loads(path.read_text())['counters']

    def test_repeated_plans_preserve_overloads_generic_types_and_inline_copies(self):
        code=self.program('''
items:=[2]int{3,4};view:>items[:]
for _ in 0..<4 {
    if first(view)!=3||identity(7)!=7||identity(2.5)!=2.5{panic("generic plans")}
    if choose(7)!=1||choose("seven")!=2{panic("overload plan")}
    copied:=identity(items);copied[0]=9
    if view[0]!=3||copied[0]!=9{panic("array copy")}
}
''','''
identity::proc(x:$T)->T{return x}
first::proc(x:[]$T)->T{return x[0]}
as_int::proc(x:int)->int{return 1}
as_text::proc(x:string)->int{return 2}
choose::proc{as_int,as_text}
''')
        for flags in (['-debug'],native.UNCHECKED):
            result,counts=self.profile(code,flags)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertGreater(counts.get('resolver.call_plan_hits',0),10,counts)

    def test_defaults_named_arguments_and_variadic_values_keep_their_arrangement(self):
        code=self.program('''
marker:>0;_=marker
if sum(1,2,3)!=6||pair(b=4,a=3)!=34||pair(2)!=25{panic("arguments")}
''','''
sum::proc(values:..int)->int{n:=0;for x in values{n+=x};return n}
pair::proc(a:int,b:int=5)->int{return a*10+b}
''')
        for flags in (['-debug'],native.UNCHECKED):
            result,_=self.profile(code,flags)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_evaluated_conditions_and_contextual_arrays_invalidate_earlier_plans(self):
        code=self.program('''
items:>[2]int{3,4};if read(items)!=7||array_total([3,4])!=7{panic("context")}
''','''
flag::fn()->bool{return true}
CHOICE::#run flag()
when CHOICE { read::proc(x:[2]int)->int{return x[0]+x[1]} }
else { read::proc(x:[2]int)->int{return 99} }
array_total::proc(x:[2]i8)->int{return int(x[0]+x[1])}
''')
        for flags in (['-debug'],native.UNCHECKED):
            for _ in range(2):
                result,_=self.profile(code,flags)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_writes_through_generic_aliases_are_still_rejected(self):
        code=self.program('items:=[1]int{3};view:>items[:];alias:>identity(view);write(alias)','''
identity::proc(x:$T)->T{return x}
write::proc(x:[]int){x[0]=9}
''')
        for flags in (['-debug'],native.UNCHECKED):
            result,_=self.profile(code,flags,command='build')
            self.assertNotEqual(result.returncode,0)
            self.assertIn('read-only slice',result.stderr)
            self.assertIn(str(self.source),result.stderr)


if __name__=='__main__':unittest.main()
