"""Captured conversion targets keep source locations through unsaved TLS edits."""
import json
import unittest
from tests import test_tls as base


class PureConversionTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)

    def test_captured_pointer_target_reports_conversion_and_recovers(self):
        text='''// 😀 conversion locations
package main
outer::proc($T:typeid,n:int)->int{
    inner::fn(value:int)->int{_=T(uintptr(value));return value}
    return inner(n)
}
main::proc(){n:=3;_=outer(^int,n)}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='Pure code cannot convert values to pointers')
        relevant=[error for error in errors if 'Pure code cannot convert values to pointers' in error['message']]
        self.assertEqual(len(relevant),1,errors)
        self.assertEqual(relevant[0]['range']['start'],self.position(text,'T(uintptr(value))',0))
        for hidden in ('conversion_type','pure_type_site','Thor expression','pure_pointer_free_type'):
            self.assertNotIn(hidden,json.dumps(errors))
        fixed=text.replace('outer(^int,n)','outer(int,n)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),contains='Pure code cannot convert values to pointers')
        self.assertEqual(main.read_text(),text)


if __name__=='__main__':unittest.main()
