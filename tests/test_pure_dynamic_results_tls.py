"""Dynamic pure-result hover types and original-source diagnostics in TLS."""
import json
import unittest

from tests import test_tls as base


class PureDynamicResultsTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)

    def test_runtime_and_run_dynamic_result_hovers_and_unsaved_signature_recovery(self):
        text='''// 😀
package main
Pos::[2]int
STARTING_POINTS::[]Pos{{1,2},{3,4}}
load_points::fn()->[dynamic]Pos{
    points:[dynamic]Pos
    for point in STARTING_POINTS{append(&points,point)}
    return points
}
main::proc(){
    runtime:=load_points();defer delete(runtime)
    folded:=#run load_points();defer delete(folded)
    point:=runtime[0];_=point
}
'''
        client,main=self.open(text)
        for name,typ in [('runtime:=','[dynamic]Pos'),('folded:=','[dynamic]Pos'),('point:=','[2]int')]:
            with self.subTest(name=name):
                value=json.dumps(self.hover(client,main,text,name))
                self.assertIn(typ,value)
                self.assertNotIn('__thor_',value)
        broken=text.replace('->[dynamic]Pos','->[dynamic]^Pos').replace('return points','return nil')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Unsupported pure value type')
        self.assertTrue(any(error['range']['start']==self.position(broken,'->[dynamic]^Pos',2) for error in errors),errors)
        self.assertNotIn('Thor expression',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_constant_result_binding_diagnostic_recovers_to_owned_runtime_binding(self):
        text='''// 😀
package main
load::fn()->[dynamic]int{result:[dynamic]int;append(&result,7);return result}
main::proc(){VALUE::#run load();_=VALUE}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='dynamic-array-containing')
        self.assertNotIn('Thor expression',json.dumps(errors))
        fixed=text.replace('VALUE::#run','VALUE:=#run').replace('_=VALUE','defer delete(VALUE)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('[dynamic]int',json.dumps(self.hover(client,main,fixed,'VALUE:=')))


if __name__=='__main__':unittest.main()
