"""Public conversion API discovery, types and unsaved error repair in TLS."""
import json
import unittest
from tests import test_tls as base


class StringTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover

    def test_hover_completion_definition_and_optional_result_types(self):
        text='''package main
import "thor:basic"
main::proc(){text,err:=basic._string(42);defer delete(text);_=err}
'''
        client,main=self.open(text)
        hover=json.dumps(self.hover(client,main,text,'_string(42'))
        self.assertIn('Allocator_Error',hover)
        self.assertIn('optional_allocator_error',hover)
        self.assertIn('independent text',hover)
        result=json.dumps(self.hover(client,main,text,'text,err:='))
        self.assertIn('string',result)
        definition=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'_string(42',2)})
        self.assertIn('runtime/basic/basic.thor',json.dumps(definition))
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'basic._string',6),'context':{'triggerKind':1}})
        self.assertIn('_string',[item['label'] for item in completion['items']])
        # A clean didOpen need not publish an empty diagnostic list. The
        # error/repair case below verifies checker delivery and clearing.

    def test_wrong_argument_and_unsaved_repair(self):
        text='''// 😀 source offsets
package main
import "thor:basic"
main::proc(){text:=basic._string([]int{1,2});defer delete(text)}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='_string supports byte/rune sequences')
        self.assertTrue(any(e['range']['start']['line']==3 for e in errors))
        self.assertNotIn('__thor_',json.dumps(errors))
        fixed=text.replace('[]int{1,2}',"[]rune{'界','😀'}")
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},
            'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)


if __name__=='__main__':unittest.main()
