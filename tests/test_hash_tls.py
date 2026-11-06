"""The basic value hash API is available to normal TLS library discovery."""
import json
import unittest
from tests import test_tls as base


class HashTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover

    def test_hash_hover_definition_completion_and_result_type(self):
        text='''package main
import "thor:basic"
main::proc(){result:=basic.hash("hello",algorithm=.FNV64a);_=result}
'''
        client,main=self.open(text)
        hover=json.dumps(self.hover(client,main,text,'hash("hello"'))
        self.assertIn('u64',hover)
        self.assertIn('algorithm',hover)
        self.assertIn('No heap allocation',hover)
        result=json.dumps(self.hover(client,main,text,'result:='))
        self.assertIn('u64',result)
        definitions=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'hash("hello"',2)})
        self.assertIn('runtime/basic/basic.thor',json.dumps(definitions))
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'basic.hash',6),'context':{'triggerKind':1}})
        labels=[item['label'] for item in completion['items']]
        for name in ['hash','try_hash','Hash_Algorithm','Hash_Error']:self.assertIn(name,labels)

    def test_algorithm_type_error_and_unsaved_fix(self):
        text='''// 😀 source coordinates
package main
import "thor:basic"
main::proc(){_=basic.hash(1,algorithm="wrong")}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='Cannot convert')
        self.assertTrue(any(e['range']['start']['line']==3 for e in errors))
        self.assertNotIn('__thor_',json.dumps(errors))
        fixed=text.replace('"wrong"','.XXH64')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)


if __name__=='__main__':unittest.main()
