"""Original ranges, unsaved recovery, acknowledgements and absence of unsafe fixes."""
import json
import unittest
from tests import test_tls as base
from tests import test_vet_tls as vet_base


class LifetimeVetTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    actions=vet_base.VetTLSTests.actions

    def test_cached_using_reader_checks_live_pointee_at_original_call(self):
        text='// 😀\npackage main\nV::struct{x,y:int}\nread::proc(p:^V)->int{using p;return x+y}\nmain::proc(){p:=new(V);_=read(p);free(p);_=read(p)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if w.get('code')=='thor/lifetime')
        needle='free(p);_=read(p)';offset=len('free(p);_=')
        self.assertEqual(warning['range']['start'],self.position(text,needle,offset))
        self.assertEqual(warning['range']['end'],self.position(text,needle,offset+len('read(p)')))
        changed=text.replace('free(p);_=read(p)','_=read(p);free(p)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_extended_format_alias_reads_have_original_call_ranges(self):
        text='package main\nimport "core:fmt"\noutput::fmt.eprintln\nmain::proc(){v:=make([]int,2);delete(v);output(v)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if w.get('code')=='thor/lifetime')
        self.assertEqual(warning['range']['start'],self.position(text,'output(v)',0))
        self.assertEqual(warning['range']['end'],self.position(text,'output(v)',len('output(v)')))

    def test_using_native_scalar_call_does_not_invent_owned_result(self):
        text='package main\nimport rl "vendor:raylib"\nmain::proc(){using rl;_=MeasureText("hello",12)}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_borrowed_builder_iterator_and_pmap_proofs_in_unsaved_text(self):
        text='package main\nimport "core:fmt"\nimport "core:strings"\nimport basic "thor:basic"\ntwice::proc(x:$T)->T{return 2*x}\nmain::proc(){jobs~:=make([]int,2);out~:=basic.pmap(jobs,twice);_=basic.sum(out);buf:[64]byte;b:=strings.builder_from_bytes(buf[:]);remaining:=fmt.sbprintf(&b,"hello %d",7);for token in strings.fields_iterator(&remaining){_=token[0]}}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        client.diagnostic(path.as_uri(),empty=True)
        changed=text.replace('for token in strings.fields_iterator(&remaining){_=token[0]}','for token in strings.fields_iterator(&remaining){_=token[0]};delete(jobs);_=jobs[0]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertTrue(any('Definite lifetime' in w['message'] for w in warnings),warnings)
        self.assertEqual(path.read_text(),text)

    def test_reference_iterator_write_reports_original_use_range(self):
        text='package main\nmain::proc(){v:=make([]int,2);slots:[1][]int;for &slot in slots{slot=v};delete(v);_=slots[0][0]}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if 'after its lifetime' in w['message'])
        self.assertEqual(warning['range']['start'],self.position(text,'slots[0][0]',0))
        self.assertEqual(warning['range']['end'],self.position(text,'slots[0][0]',len('slots[0][0]')))
        changed=text.replace('delete(v);_=slots[0][0]','_=slots[0][0];delete(v)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)

    def test_short_circuit_and_slice_bound_effects_recover_in_unsaved_text(self):
        text='// 😀\npackage main\nrelease::proc(values:[]int)->int{delete(values);return 0}\nmain::proc(){values:=make([]int,2);alias:=values[release(values):];_=alias[0]}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        warning=next(w for w in warnings if w.get('code')=='thor/lifetime')
        self.assertEqual(warning['range']['start'],self.position(text,'alias[0]',0))
        self.assertEqual(warning['range']['end'],self.position(text,'alias[0]',len('alias[0]')))
        changed=text.replace('release::proc(values:[]int)->int{delete(values);return 0}','release::proc(values:[]int)->bool{delete(values);return true}').replace('alias:=values[release(values):];_=alias[0]','_=false && release(values);_=values[0];delete(values)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_scoped_formatting_copy_and_split_header_have_no_false_errors(self):
        text='package main\nimport "core:fmt"\nimport "core:strings"\nformat::proc(v:[]int)->cstring{return fmt.caprint(v)}\nmain::proc(){values~:=make([]int,2);alias:>values;text~:=format(alias);source~:=strings.clone("a b");parts~:=strings.fields(source);_=len(text);_=parts[0]}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_definite_warning_original_ranges_and_unsaved_recovery(self):
        text='// 😀\npackage main\nmain::proc(){values:=make([]int,2);delete(values);_=values[0]}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertEqual(len(warnings),1,warnings)
        warning=warnings[0]
        self.assertEqual(warning['severity'],2)
        self.assertIn('Definite lifetime',warning['message'])
        self.assertEqual(warning['range']['start'],self.position(text,'values[0]',0))
        self.assertEqual(warning['range']['end'],self.position(text,'values[0]',len('values[0]')))
        self.assertIn('originates here',json.dumps(warning['relatedInformation']))
        self.assertNotIn('__thor_',json.dumps(warnings))
        actions=self.actions(client,path,text,'values[0]')
        self.assertEqual(actions,[])
        changed=text.replace('delete(values);_=values[0]','_=values[0];delete(values)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(path.as_uri(),empty=True)
        self.assertEqual(path.read_text(),text)

    def test_unknown_warning_suppression_and_definite_issue_still_visible(self):
        text='package main\nmain::proc(){values:=make([]int,2);_=values}\n'
        client,path=self.open(text,checker_args='--vet=lifetime,leak --comptime=required --unverified')
        warnings=client.diagnostic(path.as_uri(),code='thor/leak-unknown')
        self.assertEqual(len(warnings),1,warnings)
        self.assertIn('Unverified leak',warnings[0]['message'])
        actions=self.actions(client,path,text,'values:=')
        self.assertEqual(actions,[])
        ignored=text.replace('values:=','@(vet_ignore="lifetime_unknown, leak_unknown") values:=')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':ignored}]})
        client.diagnostic(path.as_uri(),empty=True)
        definite=ignored.replace('_=values','delete(values);_=values[0]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':3},'contentChanges':[{'text':definite}]})
        warnings=client.diagnostic(path.as_uri(),code='thor/lifetime')
        self.assertIn('Definite lifetime',warnings[0]['message'])
        self.assertEqual(path.read_text(),text)

    def test_acknowledgement_completion_filtering_and_source_edits(self):
        for marked,expected in [
            ('@(vet_ignore="|")',{'immutable','pure','unused','semicolon','lifetime_unknown','leak_unknown'}),
            ('@(vet_ignore="immutable, li|fetime_unknown")',{'lifetime_unknown'}),
            ('@(vet_ignore=`leak_unknown, |`)',{'immutable','pure','unused','semicolon','lifetime_unknown'}),
        ]:
            with self.subTest(marked=marked):
                cursor=marked.index('|');text='// 😀\npackage main\nmain::proc(){'+marked.replace('|','')+' values:=7;_=values}\n'
                offset=text.index(marked.replace('|',''))+cursor
                client,path=self.open(text,compiler=False)
                position=self.position(text[:offset]+'CURSOR'+text[offset:],'CURSOR',0)
                result=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':position,'context':{'triggerKind':1}})
                self.assertEqual({i['label'] for i in result['items']},expected,result)
                for item in result['items']:
                    if item['label'].endswith('_unknown'):
                        self.assertIn('Definite',json.dumps(item['documentation']))
                    self.assertNotIn('additionalTextEdits',item)
                if 'li|fetime' in marked:
                    self.assertEqual(result['items'][0]['textEdit']['range']['start'],self.position(text,'lifetime_unknown',0))
                    self.assertEqual(result['items'][0]['textEdit']['range']['end'],self.position(text,'lifetime_unknown',len('lifetime_unknown')))
                self.assertEqual(path.read_text(),text)

    def test_comments_and_ordinary_strings_do_not_offer_suppression_names(self):
        for code in ['// @(vet_ignore="|")\n','text:="vet_ignore=|";_=text','/* @(vet_ignore="|") */']:
            at=code.index('|');code=code.replace('|','')
            text='package main\nmain::proc(){'+code+'}\n'
            offset=text.index(code)+at
            client,path=self.open(text,compiler=False)
            position=self.position(text[:offset]+'CURSOR'+text[offset:],'CURSOR',0)
            result=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':position,'context':{'triggerKind':1}})
            items=result.get('items',[]) if isinstance(result,dict) else result or []
            self.assertFalse(any(item['label'] in {'lifetime_unknown','leak_unknown'} for item in items),items)


if __name__=='__main__':unittest.main()
