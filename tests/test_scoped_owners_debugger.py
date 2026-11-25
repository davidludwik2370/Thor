"""Scoped declarations keep original breakpoints and ordinary local values."""
import shutil
import unittest
from tests import test_debugger as base

@unittest.skipUnless(base.backend_path() and shutil.which('node') and shutil.which('odin'),
                     'CodeLLDB, Node and Odin required')
class ScopedOwnerDebuggerTests(unittest.TestCase):
    build=base.DebuggerTests.build
    launch=base.DebuggerTests.launch
    frame=base.DebuggerTests.frame

    def test_original_bindings_at_source_breakpoint(self):
        text='''package main
main::proc(){
 values~:=make([]int,2)
 values[0]=7
 frozen ~:>make([]int,3)
 total:=values[0]+len(frozen)
 if total!=10 {panic("values")}
}
'''
        directory,source=self.build(text)
        client=self.launch(directory,source,[6])
        thread,frame=self.frame(client)
        self.assertEqual((frame['source']['path'],frame['line']),(str(source),6),frame)
        for expression,expected in [('values.len','2'),('values.data[0]','7'),('frozen.len','3')]:
            result=client.request('evaluate',{'frameId':frame['id'],'expression':expression,'context':'watch'})
            self.assertEqual(result['result'],expected,result)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

if __name__=='__main__':unittest.main()
