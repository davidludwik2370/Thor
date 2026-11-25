"""Static/attributed immutable storage keeps diagnostics and editor semantics."""
import unittest
from tests import test_tls as base
from tests import test_immutable_highlighting_tls as highlighting

class AttributedImmutabilityTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    semantic_spans=base.TLSTests.semantic_spans
    assert_accesses=highlighting.ImmutableHighlightingTLSTests.assert_accesses

    def test_static_write_error_and_unsaved_recovery(self):
        text='// 😀\npackage main\ndanger::proc(y:int)->int{\n @static x:>10\n x+=y\n return x\n}\nmain::proc(){_=danger(1)}\n'
        client,path=self.open(text,checker_args='--comptime=required')
        errors=client.diagnostic(path.as_uri(),contains='immutable')
        error=next(e for e in errors if 'immutable' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'x+=y',0))
        self.assertEqual(error['range']['end'],self.position(text,'x+=y',1))
        fixed=text.replace('@static x:>10','@static x:=10')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_static_using_and_fstring_tokens_remain_readonly(self):
        text='package main\nmain::proc(){@static row:>struct{n:int}{7};using row;_=f"{n} {row.n}"}\n'
        client,path=self.open(text,compiler=False)
        self.assert_accesses(client,path,text,[('{n}', 'n', True, False),('row.n}', 'n', True, False)])
        client.diagnostic(path.as_uri(),empty=True)

if __name__=='__main__':unittest.main()
