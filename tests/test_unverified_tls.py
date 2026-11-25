"""Summary counts, individual information, strict errors, and unsaved recovery."""
import unittest
from tests import test_tls as support
from tests import test_vet_tls as corrections

UNKNOWN='work::proc(callback:proc(v:[]int)){v~:=make([]int,2);callback(v)}\n'

class UnverifiedTLSTests(unittest.TestCase):
    setUpClass=classmethod(support.TLSTests.setUpClass.__func__)
    open=support.TLSTests.open
    position=staticmethod(support.TLSTests.position)
    actions=corrections.VetTLSTests.actions

    def change(self,client,path,text,version=2):
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':version},'contentChanges':[{'text':text}]})

    def test_default_summary_is_one_information_notice_and_recovers(self):
        text='// 😀\npackage main\n'+UNKNOWN+'main::proc(){}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required')
        ds=client.diagnostic(path.as_uri(),code='thor/unverified-summary')
        self.assertEqual(len(ds),1,ds)
        self.assertEqual(ds[0]['severity'],3)
        self.assertIn('1 finding at 1 source location',ds[0]['message'])
        self.assertIn('not confirmed bugs',ds[0]['message'])
        self.assertIn('tls.unverified',ds[0]['message'])
        self.assertEqual(self.actions(client,path,text,'callback(v)'),[])
        self.change(client,path,text.replace('callback(v)','_=v[0]'))
        client.diagnostic(path.as_uri(),empty=True)

    def test_explicit_details_are_information_and_keep_original_ranges(self):
        text='// 😀\npackage main\n'+UNKNOWN+'main::proc(){}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --unverified --comptime=required')
        ds=client.diagnostic(path.as_uri(),code='thor/lifetime-unknown')
        self.assertEqual(len(ds),1,ds)
        self.assertEqual(ds[0]['severity'],3)
        self.assertEqual(ds[0]['range']['start'],self.position(text,'callback(v)',0))
        self.assertEqual(ds[0]['range']['end'],self.position(text,'callback(v)',len('callback(v)')))
        self.assertIn('callee',ds[0]['message'])
        self.assertFalse(any(d.get('code')=='thor/unverified-summary'for d in ds))
        self.assertEqual(self.actions(client,path,text,'callback(v)'),[])

    def test_strict_details_are_errors_and_recover(self):
        text='package main\n'+UNKNOWN+'main::proc(){}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --unverified=error --comptime=required')
        ds=client.diagnostic(path.as_uri(),code='thor/lifetime-unknown')
        self.assertEqual(ds[0]['severity'],1)
        self.assertIn('Unverified',ds[0]['message'])
        self.change(client,path,text.replace('callback(v)','_=v[0]'))
        client.diagnostic(path.as_uri(),empty=True)

    def test_definite_issues_and_regular_vet_fixes_remain_visible_beside_summary(self):
        text='package main\n'+UNKNOWN+'main::proc(){v:=make([]int,2);delete(v);_=v[0];number:=7;if number!=7{panic("number")}}\n'
        client,path=self.open(text,checker_args='--vet=immutable,lifetime,leak --comptime=required')
        ds=client.diagnostic(path.as_uri(),code='thor/unverified-summary')
        self.assertTrue(any(d.get('code')=='thor/lifetime'and d['severity']==2 for d in ds),ds)
        self.assertTrue(any(d.get('code')=='thor/prefer-immutable'and d['severity']==2 for d in ds),ds)
        actions=self.actions(client,path,text,'number:=')
        self.assertTrue(any('immutable'in action.get('title','').lower()for action in actions),actions)

    def test_two_categories_at_one_location_have_one_summary_location(self):
        text='package main\nwork::proc(callback:proc(v:[]int)){v:=make([]int,2);callback(v)}\nmain::proc(){}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required')
        ds=client.diagnostic(path.as_uri(),code='thor/unverified-summary')
        self.assertEqual(len(ds),1)
        self.assertEqual(ds[0]['severity'],3)
        self.assertIn('findings at',ds[0]['message'])
        self.assertNotIn('Warning',ds[0]['message'])

    def test_acknowledgements_hide_unverified_not_definite_findings(self):
        text='package main\n'+UNKNOWN.replace('v~:=','@(vet_ignore={.lifetime_unknown,.leak_unknown}) v~:=')+'main::proc(){}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --unverified=error --comptime=required')
        client.diagnostic(path.as_uri(),empty=True)
        bad=text.replace('main::proc(){}','main::proc(){v:=make([]int,2);delete(v);_=v[0]}')
        self.change(client,path,bad)
        ds=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertTrue(any(d['severity']==2 for d in ds if d.get('code')=='thor/lifetime'),ds)

if __name__=='__main__':unittest.main()
