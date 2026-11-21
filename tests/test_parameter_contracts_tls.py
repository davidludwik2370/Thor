"""Live TLS coverage of parameter contracts, metadata, and runtime barriers."""
import json
import unittest
from tests import test_tls as support


class ParameterContractTLSTests(unittest.TestCase):
    setUpClass=classmethod(support.TLSTests.setUpClass.__func__)
    open=support.TLSTests.open
    hover=support.TLSTests.hover
    position=staticmethod(support.TLSTests.position)

    def test_metadata_hover_signature_completion_and_unsaved_override(self):
        text='''// 😀 caller metadata
package main
capture::proc(value:int,expression:>#caller_expression(value),call:>#caller_expression)->string{return expression}
main::proc(){value:=capture(7);_=value}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        hover=json.dumps(self.hover(client,main,text,'capture(7)'))
        self.assertIn(':>',hover)
        self.assertNotIn('__thor_',hover)
        value_hover=json.dumps(self.hover(client,main,text,'value:='))
        self.assertIn('string',value_hover)
        signature=client.request('textDocument/signatureHelp',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'capture(7)',len('capture('))})
        self.assertIsNotNone(signature)
        self.assertEqual(len(signature['signatures'][0]['parameters']),1,signature)
        self.assertIn(':>',signature['signatures'][0]['label'])
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'capture(7)',len('capture('))})
        items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
        self.assertFalse(any(item.get('label') in {'expression','call'} for item in items),items)
        changed=text.replace('capture(7)','capture(7,expression="fake")')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='cannot be supplied explicitly')
        error=next(item for item in errors if 'cannot be supplied explicitly' in item['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'expression="fake"',0))
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_ro_signature_runtime_type_and_unsaved_write(self):
        text='''package main
read::proc(#ro values:[]int)->int{return values[0]}
main::proc(){values:>[]int{7};result:=#runtime read(values);_=result}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('#ro',json.dumps(self.hover(client,main,text,'read(values)')))
        self.assertIn('int',json.dumps(self.hover(client,main,text,'result:=')))
        changed=text.replace('return values[0]','values[0]=9;return values[0]')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='read-only')
        self.assertNotIn('__thor_',json.dumps(errors))

    def test_vet_ignore_and_source_diagnostics(self):
        text='''package main
import "core:fmt"
main::proc(){
 @(vet_ignore="immutable")
 value:=7
 fmt.println(value)
}
'''
        client,main=self.open(text,vet=True)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('vet_ignore="immutable"','vet_ignore="typo"')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='Unknown vet check')
        error=next(item for item in errors if 'Unknown vet check' in item['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'vet_ignore="typo"',0))

    def test_metadata_values_and_aliases_keep_types_and_signature_contract(self):
        text='''package main
capture::proc(value:int,text:>#caller_expression(value),call:>#caller_expression,location:>#caller_location)->string{
 _=call
 _=location.line
 return text
}
main::proc(){alias::capture;result:=alias(7);_=result}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        for needle in ['text\n','call\n']:
            self.assertIn('string',json.dumps(self.hover(client,path,text,needle)))
        self.assertIn('Source_Code_Location',json.dumps(self.hover(client,path,text,'location.line')))
        hover=json.dumps(self.hover(client,path,text,'alias(7)'))
        self.assertIn(':>',hover)
        self.assertNotIn('__thor_',hover)

    def test_ro_callback_signature_and_readonly_highlighting(self):
        text='''package main
Row::struct{value:int}
Reader::#type proc(#ro rows:[]Row)->int
read::proc(#ro rows:[]Row)->int{return rows[0].value}
invoke::proc(reader:Reader,#ro rows:[]Row)->int{return reader(rows)}
main::proc(){rows:>[]Row{{7}};_=invoke(read,rows)}
'''
        client,path=self.open(text)
        client.diagnostic(path.as_uri(),empty=True)
        hover=json.dumps(self.hover(client,path,text,'reader(rows)'))
        self.assertIn('#ro',hover)
        spans=support.TLSTests.semantic_spans(self,client,path,text)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        offset=len(text[:text.index('rows[0].value')+len('rows[0].')].encode())
        self.assertTrue(any(start==offset and flags&readonly for start,_,_,_,flags in spans),spans)
        hints=client.request('textDocument/inlayHint',{'textDocument':{'uri':path.as_uri()},
             'range':{'start':{'line':0,'character':0},'end':{'line':100,'character':0}}}) or []
        self.assertFalse(any('#mut' in hint['label'] for hint in hints),hints)
