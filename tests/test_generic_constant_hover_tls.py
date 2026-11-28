"""Nested generic inference through constants must remain available to editor requests."""
import json
import unittest

from tests import test_tls as base


class GenericConstantHoverTLSTests(unittest.TestCase):
    setUpClass=classmethod(base.TLSTests.setUpClass.__func__)
    open=base.TLSTests.open
    hover=base.TLSTests.hover
    position=staticmethod(base.TLSTests.position)

    def test_nested_generic_constants_forward_backward_aliases_and_explicit_run(self):
        text='''package main
square::fn(value:$T)->T{return value*value}
LATER::square(FIRST)
FIRST::square(3)
BACKWARD::square(FIRST)
NESTED::square(square(3))
ALIAS::LATER
EXPLICIT::#run square(FIRST)
main::proc(){later:=LATER;backward:=BACKWARD;nested:=NESTED;_=later;_=backward;_=nested;_=ALIAS;_=EXPLICIT}
'''
        client,main=self.open(text,compiler=False)
        for name in ['LATER','FIRST','BACKWARD','NESTED','ALIAS','EXPLICIT']:
            with self.subTest(name=name):
                value=self.hover(client,main,text,name+'::')
                self.assertIsNotNone(value,name)
                self.assertIn(name+' ::',value['contents']['value'])
        for name in ['later','backward','nested']:
            self.assertIn(name+': int',self.hover(client,main,text,name+':=')['contents']['value'])

    def test_nested_generic_types_stay_separate_and_support_callable_aliases(self):
        text='''package main
identity::proc(value:$T)->T{return value}
alias::identity
SIGNED::alias(identity(i32(3)))
REAL::identity(alias(f64(1.5)))
main::proc(){signed:=SIGNED;real:=REAL;_=signed;_=real}
'''
        client,main=self.open(text,compiler=False)
        for _ in range(2):
            self.assertIn('signed: i32',self.hover(client,main,text,'signed:=')['contents']['value'])
            self.assertIn('real: f64',self.hover(client,main,text,'real:=')['contents']['value'])

    def test_imported_generic_results_and_selected_aliases(self):
        text='''package main
import "library" / identity
LATER::identity(FIRST)
FIRST::identity(i16(3))
main::proc(){copy:=LATER;_=copy}
'''
        client,main=self.open(text,{'library/main.thor':'package library\nidentity::fn(value:$T)->T{return value}\n'},compiler=False)
        self.assertIn('LATER ::',self.hover(client,main,text,'LATER::')['contents']['value'])
        self.assertIn('copy: i16',self.hover(client,main,text,'copy:=')['contents']['value'])

    def test_unsaved_dependency_type_changes_invalidate_hover_inference(self):
        text='''package main
identity::fn(value:$T)->T{return value}
LATER::identity(FIRST)
FIRST::identity(3)
main::proc(){copy:=LATER;_=copy}
'''
        client,main=self.open(text,compiler=False)
        for version,initial,kind in [(1,'3','int'),(2,'"text"','string'),(3,'3','int')]:
            changed=text.replace('FIRST::identity(3)','FIRST::identity('+initial+')')
            if version>1:
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
            self.assertIn('copy: '+kind,self.hover(client,main,changed,'copy:=')['contents']['value'])
        self.assertEqual(main.read_text(),text)

    def test_cyclic_initializers_remain_bounded_and_other_hovers_keep_working(self):
        text='''package main
identity::fn(value:$T)->T{return value}
SELF::identity(SELF)
LEFT::identity(RIGHT)
RIGHT::identity(LEFT)
GOOD::identity(3)
main::proc(){_=GOOD}
'''
        client,main=self.open(text,compiler=False)
        for name in ['SELF','LEFT','RIGHT']:
            self.hover(client,main,text,name+'::')
            self.assertIn('GOOD ::',self.hover(client,main,text,'GOOD::')['contents']['value'])
        tokens=client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':main.as_uri()}})
        self.assertTrue(tokens['data'])

    def test_complete_demo_dependent_constants_have_hovers(self):
        main=base.ROOT/'demo/main.thor'
        text=main.read_text()
        client=base.Client(main.parent,compiler=False)
        self.addCleanup(client.close)
        client.send('textDocument/didOpen',{'textDocument':{'uri':main.as_uri(),'languageId':'thor','version':1,'text':text}})
        for name in ['AUTO_LATER','AUTO_FIRST','PURE_NEXT']:
            with self.subTest(name=name):
                value=self.hover(client,main,text,'\n'+name)
                self.assertIsNotNone(value,name)
                self.assertIn(name+' ::',value['contents']['value'])
                self.assertNotIn('__thor_',json.dumps(value))


if __name__=='__main__':unittest.main()
