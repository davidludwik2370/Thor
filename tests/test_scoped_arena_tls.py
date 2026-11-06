"""Bundled scoped allocator API and lifetime diagnostics in TLS."""
import json
import unittest
from tests import test_tls as base


class ScopedArenaTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover

    def test_hover_completion_definition_and_private_helpers(self):
        text='''package main
import "thor:basic"
main::proc(){context.allocator=basic.scoped_arena();values:=make([]int,3);_=values}
'''
        client,main=self.open(text)
        hover=self.hover(client,main,text,'scoped_arena()')
        self.assertIn('scoped_arena',json.dumps(hover))
        self.assertIn('automatically destroyed',json.dumps(hover))
        self.assertIn('Allocator',json.dumps(hover))
        completions=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'basic.scoped_arena',6),'context':{'triggerKind':1}})
        self.assertIn('scoped_arena',json.dumps(completions))
        self.assertNotIn('_destroy_scoped_arena',[item['label'] for item in completions['items']])
        definitions=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'scoped_arena()',2)})
        self.assertIn('runtime/basic/basic.thor',json.dumps(definitions))

    def test_unsafe_expression_points_to_call_and_named_local_fix_recovers(self):
        text='''// 😀 original source
package main
import "thor:basic"
main::proc(){
 outer:=context.allocator
 context.allocator=switch{case true:basic.scoped_arena();case:outer}
}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='automatic scope cleanup')
        error=next(e for e in errors if 'automatic scope cleanup' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'basic.scoped_arena()',0))
        self.assertNotIn('__thor_',json.dumps(errors))
        fixed=text.replace('outer:=context.allocator','outer:=context.allocator\n local:=basic.scoped_arena()')
        fixed=fixed.replace('case true:basic.scoped_arena()','case true:local')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},
                                             'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)


if __name__=='__main__':unittest.main()
