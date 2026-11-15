"""The rejection inventory through TLS, including unsaved corrections."""
import json
import unittest
from tests import test_tls as base
from tests.purity_cases import EFFECTS, TYPES


class PurityHardeningTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)

    def matrix(self,cases):
        for case in cases:
            with self.subTest(case=case.name):
                fixed=case.source('proc')
                client,main=self.open(fixed)
                client.request('textDocument/documentSymbol',{'textDocument':{'uri':main.as_uri()}})
                for index,mode in enumerate(['runtime','automatic','run'],1):
                    text=case.source('proc' if mode=='run' else 'fn',mode)
                    client.notifications.clear()
                    client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':index*2},'contentChanges':[{'text':text}]})
                    errors=client.diagnostic(main.as_uri(),contains=case.message)
                    relevant=[e for e in errors if case.message in e['message']]
                    # Some names also occur in prelude declarations. Compute
                    # the source position from the shared byte-offset inventory.
                    before=text.encode()[:case.offset(text)].decode()
                    expected={'line':before.count('\n'),'character':len(before.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
                    key=lambda position:(position['line'],position['character'])
                    self.assertTrue(any(key(e['range']['start'])<=key(expected)<key(e['range']['end']) for e in relevant),errors)
                    for forbidden in ['Thor expression','pure_validate','invalid type','SIGBUS','source location was provided']:
                        self.assertNotIn(forbidden,json.dumps(errors))
                    client.notifications.clear()
                    client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':index*2+1},'contentChanges':[{'text':fixed}]})
                    client.diagnostic(main.as_uri(),empty=True)
                saved=main.read_text()
                self.assertEqual(saved,fixed)
                self.doCleanups()

    def test_effect_categories_and_unsaved_recovery(self):self.matrix(EFFECTS)

    def test_signature_categories_and_unsaved_recovery(self):self.matrix(TYPES)

    def test_native_effect_is_on_user_call_with_related_standard_library_location(self):
        text='''// 😀
package main
import "core:math/rand"
bad::fn(n:int)->int{/* 😀 */return rand.int_max(n)}
main::proc(){n:=7;_=bad(n)}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='ambient context')
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['range']['start'],self.position(text,'rand.int_max(n)',0))
        self.assertTrue(errors[0]['relatedInformation'],errors)
        fixed=text.replace('bad::fn','bad::proc')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_declaration_only_contracts_and_native_name_errors_recover(self):
        cases=[
            ('bad::fn(n:int)->int---','bad::proc(n:int)->int{return n}','inspectable body'),
            ('foreign libc{bad::fn(n:int)->int---}','bad::proc(n:int)->int{return n}','inspectable body'),
            ('bad::fn "c"(n:int)->int{return n}','bad::proc "c"(n:int)->int{return n}','Odin calling convention'),
            ('bad::fn(input:Missing)->int{return 0}','bad::fn(input:int)->int{return 0}','Undeclared name: Missing'),
            ('state:int\nnotify::proc(){state+=1}\n@(deferred_none=notify)bad::fn(n:int)->int{return n}',
             'state:int\nnotify::proc(){state+=1}\n@(deferred_none=notify)bad::proc(n:int)->int{return n}',
             'implicit deferred procedure callbacks'),
        ]
        for broken,corrected,message in cases:
            with self.subTest(message=message):
                text='// 😀\npackage main\n'+broken+'\nmain::proc(){}\n'
                client,main=self.open(text,checker_args='--run-timeout 0.2' if message=='timeout' else '')
                errors=client.diagnostic(main.as_uri(),contains=message)
                self.assertEqual(len(errors),1,errors)
                self.assertGreater(errors[0]['range']['start']['line'],1)
                fixed=text.replace(broken,corrected)
                client.notifications.clear()
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
                client.diagnostic(main.as_uri(),empty=True)
                self.doCleanups()

    def test_execution_errors_keep_both_request_and_failing_function_locations(self):
        for body,needle,reason,fixed_arg in [
            ('if n==7{panic("specific panic")};return n','panic(','specific panic','6'),
            ('assert(n!=7,"specific assertion");return n','assert(','specific assertion','6'),
            ('data:=[1,2];return data[n]','data[n]','out of range','1'),
        ]:
            with self.subTest(reason=reason):
                text='// 😀\npackage main\nbad::fn(n:int)->int{/* 😀 */'+body+'}\nVALUE::#run bad(7)\nmain::proc(){}\n'
                client,main=self.open(text)
                errors=client.diagnostic(main.as_uri(),contains='execution failed')
                self.assertEqual(len(errors),1,errors)
                error=errors[0]
                self.assertIn(reason,error['message'])
                self.assertEqual(error['range']['start'],self.position(text,'#run bad(7)',0))
                note=error['relatedInformation'][0]
                self.assertEqual(note['location']['uri'],main.as_uri())
                self.assertEqual(note['location']['range']['start']['line'],2)
                self.assertNotIn('thor-run-',json.dumps(errors))
                fixed=text.replace('bad(7)','bad('+fixed_arg+')')
                client.notifications.clear()
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
                client.diagnostic(main.as_uri(),empty=True)
                self.doCleanups()

    def test_transitive_switch_and_formatting_helpers_cannot_bypass_purity(self):
        for body,result,needle,message in [
            ('return switch{case n>0:state;case:0}','int','state;','outside the function'),
            ('return f"{Record{n}}"','string','Record{n}','deterministic scalar/string'),
        ]:
            with self.subTest(body=body):
                text=('// 😀\npackage main\nstate:int\nRecord::struct{x:int}\nleaf::proc(n:int)->'+result+'{/* 😀 */'+body+'}\n'
                      'work::fn(n:int)->'+result+'{return leaf(n)}\nmain::proc(){n:=7;_=work(n)}\n')
                client,main=self.open(text,checker_args='--run-timeout 0.2' if message=='timeout' else '')
                errors=client.diagnostic(main.as_uri(),contains=message)
                self.assertEqual(errors[0]['range']['start'],self.position(text,'leaf(n)',0))
                self.assertTrue(any(note['location']['range']['start']==self.position(text,needle,0)
                                    for note in errors[0]['relatedInformation']))
                self.assertNotIn('Thor expression',json.dumps(errors))
                fixed=text.replace('work::fn','work::proc')
                client.notifications.clear()
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
                client.diagnostic(main.as_uri(),empty=True)
                self.doCleanups()

    def test_run_argument_arity_and_timeout_errors_clear_after_unsaved_fixes(self):
        cases=[
            ('bad::proc()->(int,int){return 1,2}\nVALUE::#run bad()\nmain::proc(){}',
             'bad::proc()->int{return 1}\nVALUE::#run bad()\nmain::proc(){}','exactly one supported result'),
            ('bad::fn(n:int)->int{return n}\nmain::proc(){n:>7;_= #run bad(n)}',
             'bad::fn(n:int)->int{return n}\nmain::proc(){n:>7;_= #run bad(7);_=n}','runtime storage is not a constant'),
            ('spin::fn(n:int)->int{for{};return n}\nVALUE::#run spin(7)\nmain::proc(){}',
             'spin::fn(n:int)->int{return n}\nVALUE::#run spin(7)\nmain::proc(){}','timeout'),
        ]
        for broken,corrected,message in cases:
            with self.subTest(message=message):
                text='// 😀\npackage main\n'+broken+'\n'
                client,main=self.open(text,checker_args='--run-timeout 0.2' if message=='timeout' else '')
                errors=client.diagnostic(main.as_uri(),contains=message)
                self.assertTrue(all(e['range']['start']['line']>1 for e in errors),errors)
                fixed=text.replace(broken,corrected)
                client.notifications.clear()
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
                client.diagnostic(main.as_uri(),empty=True)
                self.doCleanups()


if __name__=='__main__':unittest.main()
