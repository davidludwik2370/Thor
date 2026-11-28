"""Experimental fn diagnostics preserve original LSP ranges and unsaved recovery."""
import json
import unittest
from tests import test_tls as base

class FnSafetyTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)

    def test_alias_lifetime_error_and_unsaved_recovery(self):
        text='// 😀 source\npackage main\nf::fn()->int{a:=make([]int,1);b:=a;delete(a);b[0]}\nmain::proc(){}\n'
        client,path=self.open(text,checker_args='--fn-safety=prototype --comptime=required')
        findings=client.diagnostic(path.as_uri(),code='thor/fn-safety')
        issue=next(f for f in findings if f.get('code')=='thor/fn-safety')
        self.assertEqual(issue['range']['start'],self.position(text,'b[0]',0))
        self.assertEqual(issue['range']['end'],self.position(text,'b[0]',4))
        changed=text.replace('delete(a);b[0]','x:=b[0];delete(a);return x')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertIn('fn',json.dumps(self.hover(client,path,changed,'f::')))

    def test_proof_gap_is_information_and_strict_gap_is_error(self):
        text='package main\nf::fn(x:int)->int{switch x{case 0:1;case:2}}\nmain::proc(){}\n'
        for mode,severity in [('prototype',3),('strict',1)]:
            client,path=self.open(text,checker_args='--fn-safety='+mode+' --comptime=required')
            findings=client.diagnostic(path.as_uri(),code='thor/fn-safety-unverified')
            issue=next(f for f in findings if f.get('code')=='thor/fn-safety-unverified')
            self.assertEqual(issue['severity'],severity)
            self.assertIn('incomplete check',issue['message'])

if __name__=='__main__':unittest.main()
