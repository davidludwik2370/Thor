"""Bounded recursive propagation and build-local source-fact reuse."""
from collections import Counter
from pathlib import Path
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tests import test_thor as base
from thorlang.compiler import Source
from thorlang.driver import Project, frontend_binary, _main
from thorlang.semantics import Type, Resolver
from thorlang.sliceaccess import SliceAccess, Summary, Value, Origin


class SliceAnalysisPerformanceTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def project(self,text):
        directory=tempfile.TemporaryDirectory(prefix='thor-slice-inference-')
        self.addCleanup(directory.cleanup)
        root=Path(directory.name)
        path=root/'main.thor';path.write_text(text)
        frontend,_=frontend_binary(os.environ.get('THOR_ODIN','odin'))
        return Project(path,frontend,[],cache_directory=root/'cache')

    def test_recursive_work_does_not_replay_independent_procedures(self):
        helpers='\n'.join(f'independent_{i}::proc()->int{{return {i}}}' for i in range(100))
        text=self.program('source :> Row{[]int{1}};_=fresh(source);view :> []int{1};a(view,1)', '''Row::struct{items:[]int}
fresh::proc(source:Row)->int{copy:=source;copy.items=make([]int,1);return len(copy.items)}
a::proc(values:[]int,n:int){if n>0{b(values,n-1)}else{values[0]=9}}
b::proc(values:[]int,n:int){if n>0{a(values,n-1)}}
'''+helpers)
        project=self.project(text)
        counts=Counter();original=SliceAccess.statement
        def statement(analysis,node,context):
            if context.proc and context.complete and node is context.proc.role('body'):
                name=context.proc.parent.group('names')[0].text
                counts[name]+=1
            return original(analysis,node,context)
        with patch.object(SliceAccess,'statement',new=statement):
            project.slice_access.prepare()
        self.assertTrue(project.slice_access.recursive)
        self.assertTrue(counts,counts)
        self.assertFalse(any(name.startswith('independent_') for name in counts),counts)
        self.assertNotIn('fresh',counts)
        self.assertLessEqual(max(counts.values()),4,counts)

    def test_typed_declarations_refine_unknown_discovery_results_before_freezing(self):
        for annotation, literal, expected in [('Id','Id(7)','scalar'),
                                               ('[]int','[]int{7}','slice')]:
            with self.subTest(annotation=annotation):
                project=self.project(self.program('value, status :> produce();_=value;_=status',
                    f'Id::distinct int\nproduce::proc()->({annotation},int){{return {literal},0}}\n'))
                source=next(iter(project.sources.values()))
                declaration=next(node for node in source.nodes if node.kind=='Declaration'
                    and [name.text for name in node.group('names')]==['value','status'])
                name=declaration.group('names')[0]
                context=Summary(None,{})
                placeholder=Value(data={Origin(seed=name)})
                with patch.object(project.slice_access,'expression_results',return_value=[placeholder,Value()]):
                    project.slice_access.statement(declaration,context)
                value=context.env[declaration,'value']
                self.assertEqual(value.typ.kind,expected)
                self.assertEqual(bool(value.data),expected=='slice')
                self.assertFalse(context.env[declaration,'status'].data)

    def test_recursive_argument_rotation_preserves_both_mutation_requirements(self):
        prefix='''a::proc(left,right:[]int,n:int){if n>0{b(right,left,n-1)}else{left[0]=9}}
b::proc(left,right:[]int,n:int){if n>0{a(left,right,n-1)}}
'''
        for args in ['view,writable','writable,view']:
            with self.subTest(args=args):
                self.invoke(self.program('view :> []int{1};writable:=[]int{2};b('+args+',2)',prefix),
                            command='check',fail='read-only slice')

    def test_recursive_returned_views_recheck_their_callers(self):
        prefix='''a::proc(values:[]int,n:int)->[]int{if n>0{return b(values,n-1)};return values}
b::proc(values:[]int,n:int)->[]int{return a(values,n)}
'''
        self.invoke(self.program('values :> []int{7};copy:=b(values,1);copy[0]=9',prefix),
                    command='check',fail='read-only slice')
        text=self.program('values :> []int{7};view :> b(values,1);if view[0]!=7{panic("recursive read")}',prefix)
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
            with self.subTest(flags=flags):self.invoke(text,flags=flags)

    def test_unused_bodies_and_reference_producers_are_still_checked(self):
        for prefix in ['unused::proc(values:[]int){view :> values;view[0]=9}\n']:
            with self.subTest(prefix=prefix):
                self.invoke(self.program('marker :> 0;_=marker',prefix),command='check',fail='read-only slice')
        self.invoke(self.program('marker :> 0;_=marker',
                    'unused::proc(get:proc()->[]int){writable:=get();_=writable}\n'),command='check')
        self.invoke(self.program('copy:=produce();_=copy',
                    'produce::proc()->[]int{view :> []int{1};return view}\n'),
                    command='check',fail='read-only slice')

    def test_cached_and_uncached_source_queries_produce_the_same_diagnostics(self):
        cases=[('Box::struct($T:typeid){value:T}\n',
                'a :> []Box(int){{7}};b :> []Box([]int){{[]int{1}}};copy:=a[0];copy.value=9;b[0].value[0]=9',[]),
               ('Row::struct{items:[]int}\nread::proc(using row:Row)->int{return items[0]}\n',
                'row :> Row{[]int{1}};_=read(row)',[]),
               ('Value::[]int when #config(REF,true) else int\n',
                'value :> Value{1};copy:=value;_=copy',['-define:REF=true']),
               ('Value::[]int when #config(REF,true) else int\n',
                'value :> Value(1);copy:=value;_=copy',['-define:REF=false']),
               ('import "core:c"\n', 'a :> c.long(7);copy:=a;_=copy',[]),
               ('a::proc(values:[]int,n:int)->[]int{if n>0{return b(values,n-1)};return values}\n'
                'b::proc(values:[]int,n:int)->[]int{return a(values,n)}\n',
                'view :> []int{1};copy:=b(view,1);_=copy',[])]
        for prefix,body,flags in cases:
            with self.subTest(body=body):
                project=self.project(self.program(body,prefix))
                observed=[]
                for reuse,eager in [(True,False),(False,False),(True,True)]:
                    output,errors=io.StringIO(),io.StringIO()
                    manager=contextlib.nullcontext() if reuse else patch.object(Resolver,'cached_queries',lambda _:contextlib.nullcontext())
                    with manager,contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
                        status=_main(['check',str(project.entry),'--odin',os.environ.get('THOR_ODIN','odin'),
                                      '--output-dir',str(project.entry.parent/('eager' if eager else 'cached' if reuse else 'uncached')),
                                      *(['--editor-info'] if eager else []),
                                      '-json-errors',*flags])
                    payload=json.loads(errors.getvalue())
                    observed.append((status,[(item['type'],item.get('pos'),item['msgs']) for item in payload['errors']]))
                self.assertEqual(observed[0],observed[1])
                self.assertEqual(observed[0],observed[2])

    def test_query_reuse_respects_generic_bindings_discovery_and_scope(self):
        project=self.project('package main\nBox::struct($T:typeid){value:T}\nmain::proc(){_=Late}\n')
        resolver=project.resolver;source=next(iter(project.sources.values()))
        poly=next(node for node in source.nodes if node.kind=='PolyType')
        late=next(node for node in source.nodes if node.kind=='Ident' and node.text=='Late')
        with resolver.cached_queries():
            unresolved=resolver.type_node(poly)
            number=Type('scalar','int');resolver.bound_types[poly,'T']=number
            numeric=resolver.type_node(poly)
            sequence=Type('slice',elem=number);resolver.bound_types[poly,'T']=sequence
            sliced=resolver.type_node(poly)
            resolver.bound_types.clear()
            unbound=resolver.type_node(poly)
            missing=resolver.symbol(late)
            path=source.path.parent/'extra.thor'
            added=Source(path,project.frontend,raw=b'package main\nLate::42\n')
            resolver.add_sources([added])
            found=resolver.symbol(late)
        self.assertEqual(unresolved.kind,'generic')
        self.assertIs(numeric,number)
        self.assertIs(sliced,sequence)
        self.assertEqual(unbound.kind,'generic')
        self.assertIsNone(missing)
        self.assertEqual(found.value.text,'42')
        self.assertIsNone(resolver.query_caches)

    def test_import_resolution_cache_tracks_collection_changes_and_source_directories(self):
        project=self.project('package main\nmain::proc(){}\n')
        source=next(iter(project.sources.values()))
        root=source.path.parent
        source.collections['sample']=root/'one'
        first=source.import_dir('sample:child')
        source.collections['sample']=root/'two'
        second=source.import_dir('sample:child')
        sibling=Source(root/'nested/main.thor',project.frontend,raw=b'package main\nmain::proc(){}\n')
        here=source.import_dir('local')
        there=sibling.import_dir('local')
        self.assertEqual(first,root/'one/child')
        self.assertEqual(second,root/'two/child')
        self.assertEqual(here,root/'local')
        self.assertEqual(there,root/'nested/local')


if __name__=='__main__':unittest.main()
