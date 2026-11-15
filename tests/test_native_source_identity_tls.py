"""Source metadata lowering must retain editor positions after unsaved edits."""
import json
import unittest
from tests import test_tls as support


class NativeSourceIdentityTLSTests(unittest.TestCase):
    setUpClass=classmethod(support.TLSTests.setUpClass.__func__)
    open=support.TLSTests.open
    hover=support.TLSTests.hover
    position=staticmethod(support.TLSTests.position)

    def test_external_reserved_members_and_named_arguments(self):
        text='''package main
import "ext"
maybe::proc()->(int,bool){return 7,true}
main::proc(){
 item:=ext.make(or_panic=7)
 value:=item.or_panic
 kind:ext.Kind=.or_panic
 _=kind
 _=value
 selected:=maybe() or_panic
 _=selected
}
'''
        client,main=self.open(text,{'ext/ext.odin':'''package or_panic
Kind::enum{or_panic}
Item::struct{or_panic:int}
make::proc(or_panic:int)->Item{return {or_panic}}
'''})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('int',json.dumps(self.hover(client,main,text,'value:=')))

    def test_colliding_configuration_keys_remain_metadata(self):
        text='''package main
A::#config(fn,1)
B::#config(set,2)
C::#config(((or_panic)),3)
D::#config(Integer,4)
E::#config(Rational,5)
maybe::proc()->(int,bool){return 7,true}
main::proc(){_=A+B+C+D+E;value:=maybe() or_panic;_=value}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('_=A+B+C+D+E','_=missing')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='Undeclared name: missing')
        error=next(e for e in errors if 'Undeclared name: missing' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'missing',0))

    def test_default_metadata_and_generated_layout_preserve_unsaved_diagnostics(self):
        text='''// 😀 original source
package main
import "core:fmt"
FILE::#file
LINE::#line
record::proc(value:int,expression:=#caller_expression(value),loc:=#caller_location){fmt.println(value,expression,loc)}
main::proc(){values:=[2]int{3,7};index:=0;record(values[index])}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('int',json.dumps(self.hover(client,main,text,'index:=0')))
        changed=text.replace('record(values[index])','record(missing)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='Undeclared name: missing')
        error=next(e for e in errors if 'Undeclared name: missing' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'missing',0))
        self.assertNotIn('__thor_',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)
