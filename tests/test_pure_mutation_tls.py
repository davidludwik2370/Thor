"""Confined mutation through the real TLS stdio/unsaved-document path."""
import json
import unittest
from tests import test_tls as base


class PureMutationTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover

    def test_owned_borrowed_and_corrected_unsaved_calls(self):
        text='''// 😀 source coordinates
package main
change::proc(data:[]int){/* 😀 */data[0]=9}
work::fn(input:[]int)->int{local:=[1,2];change(local[:]);return local[0]}
main::proc(){data:=[1,2];if work(data[:])!=9{panic("result")}}
'''
        broken=text.replace('change(local[:])','change(input)')
        client,main=self.open(broken)
        client.diagnostic(main.as_uri(),contains='borrowed from an input')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='borrowed from an input')
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['range']['start'],self.position(broken,'data[0]=9',0))
        for hidden in ['Thor expression','checked_call','pure_validate','SIGBUS']:
            self.assertNotIn(hidden,json.dumps(errors))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':4},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),broken)

    def test_pointer_helper_hover_definition_and_rename_keep_source_signature(self):
        text='''package main
change::proc(out:^int){out^+=1}
work::fn(n:int)->int{local:=n;change(&local);return local}
VALUE::#run work(7)
main::proc(){n:=3;_=work(n)}
'''
        client,main=self.open(text)
        hover=self.hover(client,main,text,'change(&')['contents']['value']
        self.assertIn('out:',hover)
        self.assertNotIn('ownership',hover)
        self.assertNotIn('checked_call',hover)
        definition=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'change(&')})
        location=definition[0] if isinstance(definition,list) else definition
        self.assertEqual(location['range']['start']['line'],1,definition)
        renamed=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'local:=n'),'newName':'owned'})
        edits=renamed.get('changes',{}).get(main.as_uri(),[])
        self.assertEqual(len(edits),3,renamed)

    def test_stack_escape_and_compile_time_helper_failures_recover(self):
        for helper,work,message,needle,fixed in [
            ('change::proc(out:^^int){local:=7;out^=&local}',
             'work::fn(n:int)->int{p:^int;change(&p);return n}',
             'local stack storage','out^=&local','change::proc(out:^^int){out^=nil}'),
            ('change::proc(out:^int){out^=9;panic("mutator failed")}',
             'work::fn(n:int)->int{local:=n;change(&local);return local}',
             'execution failed','#run work(7)','change::proc(out:^int){out^=9}'),
        ]:
            with self.subTest(message=message):
                text='// 😀\npackage main\n'+helper+'\n'+work+'\nVALUE::#run work(7)\nmain::proc(){}\n'
                client,main=self.open(text)
                errors=client.diagnostic(main.as_uri(),contains=message)
                relevant=[error for error in errors if message in error['message']]
                self.assertEqual(relevant[0]['range']['start'],self.position(text,needle,0),errors)
                self.assertNotIn('Thor expression',json.dumps(errors))
                client.notifications.clear()
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':text.replace(helper,fixed)}]})
                client.diagnostic(main.as_uri(),empty=True)
                self.doCleanups()


if __name__=='__main__':unittest.main()
