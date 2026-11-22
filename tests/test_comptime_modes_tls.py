"""Live mode-aware diagnostics retain original types, ranges, and runtime barriers."""
import json
import unittest
from tests import test_tls as support

class ComptimeModeTLSTests(unittest.TestCase):
    setUpClass=classmethod(support.TLSTests.setUpClass.__func__)
    open=support.TLSTests.open
    hover=support.TLSTests.hover
    position=staticmethod(support.TLSTests.position)

    def test_ordinary_constant_is_checked_using_the_configured_mode(self):
        text='package main\nwork::proc(x:int)->int{return x*x}\nVALUE::work(7)\nmain::proc(){value:=VALUE;_=value}\n'
        for mode in ['required','normal','aggressive','override']:
            with self.subTest(mode=mode):
                client,path=self.open(text,checker_args='--comptime='+mode)
                if mode in ['aggressive','override']:
                    client.diagnostic(path.as_uri(),empty=True)
                    hover=json.dumps(self.hover(client,path,text,'value:='))
                    self.assertIn('int',hover)
                    self.assertNotIn('__thor_',hover)
                else:
                    errors=client.diagnostic(path.as_uri(),contains='context')
                    self.assertFalse(any('__thor_' in e['message'] for e in errors),errors)

    def test_override_ignores_barriers_in_explicit_evaluation_and_keeps_hover(self):
        text='package main\nwork::proc(x:int)->int{return #runtime (x+1)}\nVALUE::#run work(7)\nmain::proc(){value:=VALUE;_=value}\n'
        for mode in ['normal','override']:
            client,path=self.open(text,checker_args='--comptime='+mode)
            if mode=='override':
                client.diagnostic(path.as_uri(),empty=True)
                self.assertIn('int',json.dumps(self.hover(client,path,text,'value:=')))
            else:
                errors=client.diagnostic(path.as_uri(),contains='#runtime')
                self.assertNotIn('__thor_',json.dumps(errors))

    def test_required_suppresses_optional_work_while_normal_reports_timeout_at_call(self):
        text='package main\nwork::fn()->int{for{};return 7}\nmain::proc(){value:=work();_=value}\n'
        for mode in ['required','normal']:
            client,path=self.open(text,checker_args='--comptime='+mode+' --run-timeout=0.05')
            if mode=='required':client.diagnostic(path.as_uri(),empty=True)
            else:
                errors=client.diagnostic(path.as_uri(),contains='timeout')
                error=next(e for e in errors if 'timeout' in e['message'])
                self.assertEqual(error['range']['start'],self.position(text,'work();',0))

    def test_aggressive_io_keeps_proc_type_and_has_no_purity_diagnostic(self):
        text='package main\nimport "core:fmt"\nwork::proc(x:int)->int{fmt.println(x);return x}\nmain::proc(){value:=work(7);_=value}\n'
        client,path=self.open(text,checker_args='--comptime=aggressive')
        client.diagnostic(path.as_uri(),empty=True)
        self.assertIn('proc',json.dumps(self.hover(client,path,text,'work(7)')))

    def test_folded_aliased_struct_call_retains_field_completion(self):
        text='package main\nPair::struct{value:int}\nbuild::proc(x:int)->Pair{return {x}}\nalias::build\nVALUE::alias(7)\nmain::proc(){copy:=VALUE;_=copy.value}\n'
        for mode in ['aggressive','override']:
            client,path=self.open(text,checker_args='--comptime='+mode)
            client.diagnostic(path.as_uri(),empty=True)
            self.assertIn('Pair',json.dumps(self.hover(client,path,text,'copy:=')))
            completion=client.request('textDocument/completion',{
                'textDocument':{'uri':path.as_uri()},'position':self.position(text,'copy.value',len('copy.'))})
            items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
            self.assertIn('value',[item['label'] for item in items])
