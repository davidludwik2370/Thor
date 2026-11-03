"""TLS publishes purity/I/O errors on original calls and clears unsaved fixes."""
import json
import unittest
from tests import test_tls as base


class PureIOTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover

    def test_unused_print_board_errors_have_precise_ranges_and_proc_fix_clears(self):
        text='''// 😀 header
package main
import "core:fmt"
Board::[9]int
print_board::fn(board:Board){
    for card in board{
        if card>0 {/* 😀 */fmt.print(card," ")}
        else {fmt.print("X ")}
    }
    fmt.println()
}
main::proc(){}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='performs I/O')
        self.assertEqual(len(errors),3,errors)
        for error,needle,name in zip(errors,['fmt.print(card','fmt.print("X','fmt.println()'],['fmt.print','fmt.print','fmt.println']):
            start=self.position(text,needle,0)
            self.assertEqual(error['range'],{'start':start,'end':{**start,'character':start['character']+len(name)}})
            self.assertIn("Pure function 'print_board'",error['message'])
            self.assertNotIn('source location',error['message'])
            self.assertNotIn('SIGBUS',error['message'])
        fixed=text.replace('print_board::fn','print_board::proc')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        hover=self.hover(client,main,fixed,'print_board::')
        self.assertIn('print_board',json.dumps(hover))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        errors=client.diagnostic(main.as_uri(),contains='performs I/O')
        self.assertEqual(len(errors),3,errors)
        saved=main.read_text()
        self.assertEqual(saved,text)

    def test_transitive_alias_error_points_to_the_leaf_call(self):
        text='''package main
import _ "core:fmt" / println
output::println
leaf::proc(n:int){/* 😀 */output(n)}
work::fn(n:int){leaf(n)}
main::proc(){n:=3;work(n)}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='performs I/O')
        error=next(e for e in errors if 'performs I/O' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'output(n)',0))
        self.assertNotIn('Thor expression',json.dumps(errors))
        fixed=text.replace('work::fn','work::proc')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_generic_when_only_checks_the_selected_branch_after_unsaved_type_change(self):
        text='''package main
import "core:fmt"
work::fn(value:$T)->T{
    when T==int{return value}
    else{fmt.println(value);return value}
}
main::proc(){number:=3;_=work(number)}
'''
        client,main=self.open(text)
        hover=self.hover(client,main,text,'number:=')
        self.assertIn('int',json.dumps(hover))
        broken=text.replace('number:=3','number:="active"')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='performs I/O')
        error=next(e for e in errors if 'performs I/O' in e['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'fmt.println(value)',0))
        self.assertNotIn('SIGBUS',json.dumps(errors))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
