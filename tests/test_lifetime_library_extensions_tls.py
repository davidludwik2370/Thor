"""New cached/native effects preserve source diagnostics and unsaved recovery."""
import unittest
from tests import test_tls as base

class LifetimeLibraryExtensionsTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)

    def test_cached_writer_reports_the_actual_freed_argument_call(self):
        text='// 😀\npackage main\nfill::proc(v:[]int){for &n in v{n+=1}}\nmain::proc(){v:=make([]int,2);fill(v);delete(v);fill(v)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if w.get('code')=='thor/lifetime')
        self.assertEqual(warning['range']['start'],self.position(text,'delete(v);fill(v)',len('delete(v);')))
        self.assertEqual(warning['range']['end'],self.position(text,'delete(v);fill(v)',len('delete(v);fill(v)')))
        fixed=text.replace('delete(v);fill(v)','fill(v);delete(v)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_foreign_reader_alias_reports_source_call_and_recovers(self):
        text='package main\nimport rl "vendor:raylib"\nimport "core:strings"\nmeasure::rl.MeasureText\nmain::proc(){s:=strings.clone_to_cstring("hello");delete(s);_=measure(s,12)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if w.get('code')=='thor/lifetime')
        self.assertEqual(warning['range']['start'],self.position(text,'measure(s,12)',0))
        self.assertEqual(warning['range']['end'],self.position(text,'measure(s,12)',len('measure(s,12)')))
        fixed=text.replace('delete(s);_=measure(s,12)','_=measure(s,12);delete(s)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_retained_title_outlives_local_buffer_until_close(self):
        text='package main\nimport rl "vendor:raylib"\nopen::proc(){buf:[32]byte;buf[0]=\'a\';rl.InitWindow(100,100,"first");rl.SetWindowTitle(cstring(&buf[0]))}\nmain::proc(){open()}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertTrue(any(w.get('code')=='thor/lifetime' for w in warnings),warnings)
        self.assertNotIn('__thor_',str(warnings))
        fixed=text.replace('cstring(&buf[0]))}', 'cstring(&buf[0]));rl.CloseWindow()}')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(path.as_uri(),empty=True)

if __name__=='__main__':unittest.main()
