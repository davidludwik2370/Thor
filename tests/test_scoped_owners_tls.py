"""Scoped owners retain source names, types, privileges, and lifetime ranges."""
import json
import unittest
from tests import test_tls as base

class ScopedOwnerTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_owner_hover_completion_and_original_identifier_tokens(self):
        text='''package main
Item::struct{value:int}
main::proc(){
 nums~:=make([]Item,2)
 frozen ~:>make([]Item,1)
 nums[0].value=7
 _=frozen[0].value
}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        for word in ['nums~','frozen ~']:
            hover=json.dumps(self.hover(client,path,text,word))
            self.assertIn('scoped owner',hover)
            self.assertIn('Item',hover)
            self.assertNotIn('__thor_',hover)
        self.assertIn('immutable storage',json.dumps(self.hover(client,path,text,'frozen ~')))
        result=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':self.position(text,'nums[0].value',len('nums[0].')),'context':{'triggerKind':1}})
        self.assertIn('value',json.dumps(result))
        self.assert_original_identifier_tokens(path,text,self.semantic_spans(client,path,text))
        edits=client.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},'position':self.position(text,'nums~',1),'newName':'values'})
        self.assertNotIn('values~',json.dumps(edits))
        self.assertIn('values',json.dumps(edits))
        changed=text
        lines=text.splitlines(keepends=True)
        for edit in sorted(edits['changes'][path.as_uri()],key=lambda e:(e['range']['start']['line'],e['range']['start']['character']),reverse=True):
            def offset(pos):return sum(map(len,lines[:pos['line']]))+pos['character']
            start,end=offset(edit['range']['start']),offset(edit['range']['end'])
            changed=changed[:start]+edit['newText']+changed[end:]
        self.assertIn('values~:=',changed)
        self.assertIn('frozen ~:>',changed)
        readonly=1<<client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        by_start={start:(word,flags) for start,_,_,word,flags in self.semantic_spans(client,path,text)}
        for word,expected in [('nums~',False),('frozen ~',True)]:
            token=by_start[len(text[:text.index(word)].encode())]
            self.assertEqual(bool(token[1]&readonly),expected,token)

    def test_escape_diagnostic_and_unsaved_recovery(self):
        text='''// 😀 original source
package main
import "core:slice"
bad::proc()->[]int{
 nums~:=make([]int,2)
 alias:=nums[:]
 return alias
}
main::proc(){}
'''
        client,path=self.open(text)
        errors=client.diagnostic(path.as_uri(),contains='cannot escape its scope')
        error=next(e for e in errors if 'cannot escape its scope' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'return alias',len('return ')))
        self.assertIn('nums',json.dumps(error.get('relatedInformation')))
        self.assertNotIn('__thor_',json.dumps(errors))
        fixed=text.replace('return alias','return slice.clone(alias)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_immutable_owner_still_rejects_element_writes(self):
        text='package main\nmain::proc(){nums~:>make([]int,2);nums[0]=3}\n'
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),contains='read-only slice')

if __name__=='__main__':unittest.main()
