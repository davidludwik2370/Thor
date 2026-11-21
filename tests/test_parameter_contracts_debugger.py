"""Native debugger keeps caller metadata and contract-annotated source visible."""
import shutil
import unittest
from tests import test_debugger as support


@unittest.skipUnless(support.backend_path() and shutil.which('node') and shutil.which('odin'),
                     'CodeLLDB, Node and Odin required')
class ParameterContractDebuggerTests(unittest.TestCase):
    build=support.DebuggerTests.build
    launch=support.DebuggerTests.launch
    frame=support.DebuggerTests.frame

    def test_runtime_step_into_metadata_and_ro_input_keeps_source_and_values(self):
        text='''package main
capture::proc(#ro values:[]int, value:int,
    expression:>#caller_expression(value),location:>#caller_location)->int {
    result:=values[0]+value
    return result
}
main::proc(){
    values:>[]int{7}
    value:=2
    output:=#runtime capture(values,value+1)
    if output!=10 {panic("result")}
}
'''
        directory,source=self.build(text)
        client=self.launch(directory,source,[10,11])
        thread,frame=self.frame(client)
        self.assertEqual((frame['source']['path'],frame['line']),(str(source),10),frame)
        client.request('stepIn',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual(frame['source']['path'],str(source),frame)
        # A native step-in can stop on the signature before parameter spills.
        # Inspect arguments at the first executable body statement.
        observed=[frame]
        for _ in range(8):
            if frame['line']==4:break
            client.request('next',{'threadId':thread})
            thread,frame=self.frame(client)
            observed.append(frame)
        self.assertEqual((frame['source']['path'],frame['line']),(str(source),4),observed)
        length=client.request('evaluate',{'frameId':frame['id'],'expression':'expression.len','context':'watch'})
        self.assertEqual(length['result'],'9',length)
        # Odin strings/slices retain their native descriptor representation.
        # Inspect the data pointer's text, without assuming a struct summary
        # or asking CodeLLDB's simple evaluator to parse a Thor conversion.
        captured=client.request('evaluate',{'frameId':frame['id'],
            'expression':'expression.data','context':'watch'})
        self.assertEqual(captured['result'],'"value + 1"',captured)
        location=client.request('evaluate',{'frameId':frame['id'],'expression':'location.line','context':'watch'})
        self.assertEqual(location['result'],'10',location)
        value=client.request('evaluate',{'frameId':frame['id'],'expression':'values.data[0]','context':'watch'})
        self.assertEqual(value['result'],'7',value)
        client.request('continue',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual((frame['source']['path'],frame['line']),(str(source),11),frame)
        output=client.request('evaluate',{'frameId':frame['id'],'expression':'output','context':'watch'})
        self.assertEqual(output['result'],'10',output)
        client.request('continue',{'threadId':thread})
        client.event('terminated')
