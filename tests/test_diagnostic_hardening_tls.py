"""Diagnostic hardening through TLS's actual compiler/overlay transport."""
import json
import unittest
from tests import test_tls as base


class DiagnosticHardeningTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    position=staticmethod(base.TLSTests.position)
    hover=base.TLSTests.hover

    def test_native_keyword_ranges_cover_the_word_and_follow_unsaved_changes(self):
        text='// café 😀\r\npackage main\r\nmain::proc(){/* 😀 */for person in [1,2,3]{using person}}\r\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains="'using' can only be applied")
        start=self.position(text,'using',0)
        self.assertEqual(errors[0]['range'],{'start':start,'end':{**start,'character':start['character']+5}})
        changed=text.replace('using person','_=person')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('for person in [1,2,3]{using person}','continue')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains="'continue' only allowed")
        start=self.position(changed,'continue',0)
        self.assertEqual(errors[0]['range'],{'start':start,'end':{**start,'character':start['character']+8}})

    def test_inferred_owned_result_defaults_keep_unsaved_diagnostic_locations(self):
        text='''// café 🌩
package main
leaf::fn(n:int)->(result:=[]int{0}){a:=make([]int,1);a[0]=n;return a}
main::proc(){n:=3;a:=leaf(n);defer delete(a)}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        hover=self.hover(client,main,text,'a:=leaf')
        self.assertIn('[]int',hover['contents']['value'])
        self.assertNotIn('__thor_',hover['contents']['value'])
        changed=text.replace('[]int{0}','rawptr(nil)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='cannot convert values to pointers')
        self.assertTrue(any(error['range']['start']==self.position(changed,'rawptr(nil)',0) for error in errors))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_inactive_generic_run_recovers_after_unsaved_selected_branch_changes(self):
        text='''// 😀 source positions
package main
inner::proc($T:typeid)->int{when T==int{return 3}else{return #run missing()}}
outer::proc()->int{return inner(int)}
VALUE::#run outer()
main::proc(){_=VALUE}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        changed=text.replace('inner(int)','inner(string)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='#run requires a resolved direct call')
        self.assertTrue(any(error['range']['start']==self.position(changed,'#run missing()',0) for error in errors))
        self.assertNotIn('Rust compiler limitation',json.dumps(errors))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_runtime_collection_constant_errors_keep_original_ranges_and_recover(self):
        for expression,message,needle in [
            ('[x for x in values]','Compile-time comprehension inputs','values]'),
            ('max(**values)','Constant min/max expansion inputs','**values'),
        ]:
            with self.subTest(expression=expression):
                text='// café 🌩\npackage main\nmain::proc(){values:=[1,2];COPY::'+expression+';_=COPY}\n'
                client,main=self.open(text)
                errors=client.diagnostic(main.as_uri(),contains=message)
                self.assertEqual(errors[0]['range']['start'],self.position(text,needle,0))
                self.assertNotIn('Rust compiler limitation',json.dumps(errors))
                changed=text.replace('values:=','values::')
                client.notifications.clear()
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
                client.diagnostic(main.as_uri(),empty=True)

    def test_many_independent_index_roots_and_unsaved_recovery(self):
        names=[f'missing_{i}' for i in range(12)]
        text='// 😀\npackage main\nmain::proc(){i:=1;'+''.join(name+'[i]=2;' for name in names)+'}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='Undeclared name: missing_11')
        self.assertEqual({e['message'] for e in errors},{'Undeclared name: '+name for name in names})
        for name in names:
            error=next(e for e in errors if e['message']=='Undeclared name: '+name)
            self.assertEqual(error['range']['start'],self.position(text,name+'[',0))
        fixed=text.replace('i:=1;','i:=1;'+''.join(name+':=[1,2];' for name in names))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_interpolation_root_error_is_precise_and_unsaved_fix_clears_all_cascades(self):
        text='''// 😀 header
package main
import * "thor:basic"
main::proc(){
 prob_loss:f64
 p_win_pad:>len(f"{(1-p_loss) * 100:.2f}")
 _=p_win_pad
}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='Undeclared name: p_loss')
        self.assertEqual(len(errors),1,errors)
        start=self.position(text,'p_loss',0)
        self.assertEqual(errors[0]['range'],{'start':start,'end':{**start,'character':start['character']+len('p_loss')}})
        self.assertNotIn('polymorphic',errors[0]['message']);self.assertNotIn('Thor expression',errors[0]['message'])
        literal=self.position(text,'100',0)
        self.assertFalse(any(e['range']['start']['line']==literal['line'] and e['range']['start']['character']<=literal['character']<e['range']['end']['character'] for e in errors))
        for version in range(2,6):
            changed=text.replace('1-p_loss','1-prob_loss') if version%2==0 else text
            client.notifications.clear()
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
            if version%2==0:
                client.diagnostic(main.as_uri(),empty=True)
                self.assertIn('p_win_pad: int',json.dumps(self.hover(client,main,changed,'p_win_pad:>')))
            else:
                errors=client.diagnostic(main.as_uri(),contains='Undeclared name: p_loss')
                self.assertEqual(len(errors),1,errors)
        self.assertEqual(main.read_text(),text)

    def test_unsaved_index_root_type_and_result_errors_recover(self):
        text='// 😀 header\npackage main\nmain::proc(){/* 😀 */ values:=[1,2];i:=1;value:=values[i];_=value}\n'
        client,main=self.open(text)
        self.assertIn('value: int',json.dumps(self.hover(client,main,text,'value:=')))
        cases=[
            (text.replace('values[i]','values[missing]'),'Undeclared name: missing','missing',len('missing')),
            (text.replace('i:=1','i:f64=1'),'Sequence indices must have an integer type','i]',1),
            (text.replace('value:=','value:string='),"value 'values[i]'",'values[i]',len('values[i]')),
            (text.replace('i:=1','i:="bad"').replace('value:=values[i]','value:=values[:i]'),'The upper slice bound','i]',1),
        ]
        for version,(changed,message,needle,length) in enumerate(cases,2):
            client.notifications.clear()
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version*2},'contentChanges':[{'text':changed}]})
            errors=client.diagnostic(main.as_uri(),contains=message)
            self.assertEqual(len(errors),1,errors)
            start=self.position(changed,needle,0)
            self.assertEqual(errors[0]['range'],{'start':start,'end':{**start,'character':start['character']+length}})
            self.assertNotIn('Thor expression',errors[0]['message'])
            client.notifications.clear()
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version*2+1},'contentChanges':[{'text':text}]})
            client.diagnostic(main.as_uri(),empty=True)

    def test_membership_error_explains_context_and_links_original_needle(self):
        text='package main\nRecord::struct{x:int}\nmain::proc(){/* 😀 */ record:=Record{1};_=record in [1,2]}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='In this membership test')
        self.assertEqual(len(errors),2,errors)
        expected=self.position(text,'record in',0)
        for error in errors:
            self.assertIn("comparable with 'record' (type 'Record')",error['message'])
            note=error['relatedInformation'][0]
            self.assertEqual(note['location']['uri'],main.as_uri())
            self.assertEqual(note['location']['range']['start'],expected)
        changed=text.replace('[1,2]','[Record{1},Record{2}]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_user_builtin_spelling_has_hover_rename_and_no_false_compiler_errors(self):
        text='''package main
import native "base:builtin"
type_of::proc()->[2]int{return {1,2}}
main::proc(){i:=-1;result:=type_of()[i];native.assert(result==2)}
'''
        client,main=self.open(text)
        self.assertIn('result: int',json.dumps(self.hover(client,main,text,'result:=')))
        self.assertIn('proc()',json.dumps(self.hover(client,main,text,'type_of::')))
        edit=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'type_of::'),'newName':'make_values'})
        self.assertEqual(len(edit['changes'][main.as_uri()]),2,edit)
        broken=text.replace('result==2','result==missing')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        client.diagnostic(main.as_uri(),contains='Undeclared name: missing')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_generic_library_bound_error_stays_in_its_original_file(self):
        library='// 😀 library\npackage library\nread::proc(values:$T,index:$I)->int{return values[index]}\n'
        text='package main\nimport "library"\nmain::proc(){values:=[1,2];_=library.read(values[:],f64(1))}\n'
        client,main=self.open(text,files={'library/read.thor':library})
        uri=(main.parent/'library/read.thor').as_uri()
        errors=client.diagnostic(uri,contains="has type 'f64'")
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['range']['start'],self.position(library,'index]}',0))
        self.assertNotIn('Thor expression',errors[0]['message'])

