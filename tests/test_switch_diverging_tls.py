"""Nonreturning switch arms through real stdio LSP requests and unsaved edits."""
import json
import unittest

from tests import test_tls as base


class SwitchDivergingTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)
    semantic_spans=base.TLSTests.semantic_spans
    assert_original_identifier_tokens=base.TLSTests.assert_original_identifier_tokens

    def test_builtin_and_user_nonreturning_arms_leave_value_hovers_intact(self):
        text='''// 😀
package main
Record::struct{value:int}
Color::enum{Red,Blue}
stop::proc()->!{panic("unexpected")}
Stop::proc()->!
choose::proc(callback:Stop)->Record{return switch{case true:{7};case:callback()}}
main::proc(){
 record:=switch{case false:unreachable();case:Record{7}}
 contextual:Record>switch{case true:{7};case:stop()}
 color:Color=switch{case false:stop();case:.Red}
 count,label:=switch{case false:stop();case:7,"seven"}
 callback:Stop=stop
 indirect:=switch{case false:callback();case:7}
 _=record.value;_=contextual.value;_=color;_=count;_=label;_=indirect
}
'''
        client,main=self.open(text)
        for name,typ in [('record:=','Record'),('contextual:Record','Record'),('count,label','int'),
                         ('label:=','string'),('indirect:=','int')]:
            with self.subTest(name=name):
                needle='count,label' if name=='label:=' else name
                offset=len('count,')+1 if name=='label:=' else 1
                value=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
                    'position':self.position(text,needle,offset)})
                self.assertIn(typ,json.dumps(value))
        self.assertIn('Color',json.dumps(self.hover(client,main,text,'.Red')))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))

    def test_unsaved_signature_change_clears_and_restores_void_arm_error(self):
        text='''// 😀 source offsets
package main
stop::proc(code:int){}
main::proc(){value:=switch{case false:stop(1);case:7};_=value}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='does not return a value')
        self.assertNotIn('__thor_',json.dumps(errors))
        fixed=text.replace('proc(code:int){}','proc(code:int)->!{panic("stop")}')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('value: int',json.dumps(self.hover(client,main,fixed,'value:=')))
        broken=fixed.replace('stop(1)','stop("bad argument")')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        self.assertTrue(any(error['range']['start']==self.position(broken,'"bad argument"',0) for error in errors),errors)
        self.assertNotIn('Thor expression',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':4},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),contains='does not return a value')
        self.assertEqual(main.read_text(),text)

    def test_imported_selected_alias_and_overload_inference(self):
        text='''package main
import "library" / stop
import "core:os"
stop_int::proc(code:int)->!{panic("stop")}
value_text::proc(text:string)->i32{return i32(len(text))}
overloaded::proc{stop_int,value_text}
alias::stop
main::proc(){
 imported:=switch{case false:alias();case:7}
 native:=switch{case false:os.exit(1);case:7}
 selected:=switch{case false:overloaded(1);case:overloaded("seven")}
 _=imported;_=native;_=selected
}
'''
        client,main=self.open(text,files={'library/main.odin':'package library\nstop::proc()->!{panic("stop")}\n'})
        for name,typ in [('imported:=','int'),('native:=','int'),('selected:=','i32')]:
            with self.subTest(name=name):self.assertIn(typ,json.dumps(self.hover(client,main,text,name)))

    def test_generic_callback_arm_preserves_other_arm_type_and_mapped_void_error(self):
        text='''// 😀
package main
stop::proc()->!{panic("stop")}
void::proc(){}
choose::proc(callback:$P)->int{
    value:=switch{case false:callback();case:7}
    return value
}
main::proc(){_=choose(stop)}
'''
        client,main=self.open(text)
        self.assertIn('value: int',json.dumps(self.hover(client,main,text,'value:=')))
        broken=text.replace('choose(stop)','choose(void)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='does not return a value')
        self.assertTrue(any(error['range']['start']==self.position(broken,'callback()',0) for error in errors),errors)
        self.assertNotIn('Thor expression',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)


if __name__=='__main__':unittest.main()
