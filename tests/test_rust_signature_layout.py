"""Cached signature syntax retains call ordering, defaults and generic contexts."""
import json
import os
import unittest
from tests import test_rust_compiler as native


class RustSignatureLayoutTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program

    def run_code(self, code, flags, command='run'):
        self.source.write_text(code)
        path = self.root/'profile.json'
        result = self.invoke('rust', command=command, flags=flags,
                             env={**os.environ, 'THOR_PROFILE': str(path)})
        return result, json.loads(path.read_text())['counters']

    def test_grouped_names_defaults_and_named_variadics_keep_their_slots(self):
        code = self.program('''
for _ in 0..<3 {
    low,high:=pair(2,3)
    if low!=5||high!=10{panic("grouped defaults")}
    low,high=pair(b=3,a=2,scale=4,rest=[]int{5,6})
    if low!=16||high!=64{panic("named variadics")}
    low,high=pair(2,3,4,5,6)
    if low!=16||high!=64{panic("positional variadics")}
}
''', '''
pair::proc(a,b:int,scale:int=2,rest:..int)->(low,high:int){
    low=a+b;for n in rest{low+=n};high=low*scale;return
}
''')
        for flags in (['-debug'], native.UNCHECKED):
            result, counts = self.run_code(code, flags)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreater(counts.get('resolver.signature_layouts', 0), 0)

    def test_constant_and_type_parameters_remain_bound_per_call(self):
        code = self.program('''
items:=[2]int{3,4};view:>items[:]
if choose(view,1)!=3||choose(N=2,values=view)!=4{panic("constant params")}
if converted(int,3)!=3||converted(f64,4)!=4{panic("type params")}
box:=Box(2,int){values=items};if box.values[1]!=4{panic("struct params")}
''', '''
choose::proc(values:[]$T,$N:int)->T{when N==1{return values[0]}else{return values[-1]}}
converted::proc($T:typeid,value:T)->T{return value}
Box::struct($N:int,$T:typeid){values:[N]T}
''')
        for flags in (['-debug'], native.UNCHECKED):
            result, _ = self.run_code(code, flags)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_named_arguments_still_report_the_original_call(self):
        for call, expected in [('pair(a=1,a=2)', 'duplicate'),
                               ('pair(missing=2)', 'missing'),
                               ('pair(a=1,2)', 'non-named')]:
            code = self.program('_='+call, 'pair::proc(a:int,b:int=3)->int{return a+b}\n')
            result, _ = self.run_code(code, ['-json-errors'], command='check')
            self.assertNotEqual(result.returncode, 0)
            packet = json.loads(result.stderr)
            messages = [error for error in packet['errors'] if expected in str(error['msgs']).lower()]
            self.assertTrue(messages, packet)
            self.assertTrue(all(error['pos']['file'] == str(self.source) for error in messages), packet)
            start = code.index(call)
            self.assertTrue(all(start <= error['pos']['offset'] < start+len(call)
                                for error in messages), packet)


if __name__ == '__main__':
    unittest.main()
