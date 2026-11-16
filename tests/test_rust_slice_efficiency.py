"""Readonly performance shortcuts preserve closed/open reference contracts."""
import json
import os
import unittest
from tests import test_rust_compiler as native


class RustSliceEfficiencyTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke
    program=native.RustCompilerTests.program

    def check(self,code,*,flags=(),error=None,command='run'):
        self.source.write_text(code)
        profile=self.root/'profile.json'
        result=self.invoke('rust',command=command,flags=flags,
            env={**os.environ,'THOR_PROFILE':str(profile)})
        if error:
            self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn(error,result.stderr)
            self.assertIn(str(self.source),result.stderr)
        else:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return json.loads(profile.read_text())['counters']

    def test_scalar_procedures_do_not_become_roots_because_another_proc_has_a_view(self):
        prefix='\n'.join(f'leaf_{n}::proc(x:int)->int{{return x+{n}}}' for n in range(100))
        code=self.program('a:=[1]int{7};view:>a[:];if view[0]!=7{panic("read")}',prefix)
        for flags in (['-debug'],native.UNCHECKED):
            counts=self.check(code,flags=flags)
            self.assertGreaterEqual(counts.get('permissions.roots_skipped',0),100,counts)

    def test_skipped_mutator_is_still_checked_when_called_with_a_readonly_view(self):
        prefix='change::proc(values:[]int){values[0]=9}'
        for flags in (['-debug'],native.UNCHECKED):
            counts=self.check(self.program('a:=[1]int{7};view:>a[:];change(view)',prefix),
                flags=flags,error='read-only slice')
            self.assertGreater(counts.get('permissions.roots_skipped',0),0,counts)

    def test_uncalled_global_view_writes_remain_invalid(self):
        code=self.program('', '''GLOBAL:>[]int{7}
unused::proc(){GLOBAL[0]=9}
''')
        self.check(code,command='check',error='read-only slice')

    def test_reference_results_cannot_introduce_a_writable_alias(self):
        code=self.program('values:=produce();_=values', '''
produce::proc()->[]int{view:>[]int{7};return view}
''')
        self.check(code,command='check',error='read-only slice')

    def test_explicit_contracts_are_preserved_and_invalid_annotations_are_not_skipped(self):
        code=self.program('a:=[1]int{7};view:>a[:];change(view)',
            'change::proc(#mut values:[]int){}')
        self.check(code,command='check',error='read-only slice')
        self.check(self.program('','unused::proc(#mut value:int){}'),command='check',
            error='#mut requires a slice parameter')

    def test_omitted_defaults_can_bring_readonly_values_into_void_calls(self):
        code=self.program('change()','''GLOBAL:>[]int{7}
change::proc(#mut values:[]int=GLOBAL){}
''')
        self.check(code,command='check',error='read-only slice')
        code=self.program('change()','''GLOBAL:>[]int{7}
change::proc(values:[]int=GLOBAL){values[0]=9}
''')
        self.check(code,command='check',error='read-only slice')

    def test_selected_import_with_a_readonly_default_keeps_the_caller_check(self):
        directory=self.app/'library';directory.mkdir()
        (directory/'library.thor').write_text('''package library
GLOBAL:>[]int{7}
change::proc(#mut values:[]int=GLOBAL){}
''')
        self.check(self.program('change()','import "library" / change'),
            command='check',error='read-only slice')

    def test_generic_and_overloaded_calls_keep_instantiation_dependent_checks(self):
        bodies=['view:>value;view[0]=9',
            'view:>value;for i:=0;i<1;view[0]=9{i+=1}',
            'view:>value;if modify(&view[0]){}']
        for body in bodies:
            prefix='modify::proc(value:^int)->bool{value^=9;return true}\n'+f'write::proc(value:$T){{{body}}}'
            self.check(self.program('items:=[1]int{7};write(items[:])',prefix),
                command='check',error='read-only slice')
        prefix='''generic::proc(value:$T){view:>value;view[0]=9}
scalar::proc(value:int){_=value}
write::proc{generic,scalar}
'''
        self.check(self.program('items:=[1]int{7};write(items[:])',prefix),
            command='check',error='read-only slice')

    def test_abstract_noop_merges_do_not_hide_real_writes(self):
        code=self.program('items:=[1]int{7};view:>items[:];view[0]=view[0]')
        for flags in (['-debug'],native.UNCHECKED):
            self.check(code,flags=flags,command='build',error='read-only slice')
        code=self.program('items:=[1]int{7};_=work(items[:])',
            'work::fn(input:[]int)->int{input[0]=input[0];return input[0]}')
        self.check(code,command='check',error='borrowed from an input')

    def test_explicit_parameter_contracts_keep_their_codegen_metadata(self):
        code=self.program('marker:>0;_=marker;items:=[1]int{7};accept(items[:])',
            'accept::proc(#mut values:[]int){}')
        counts=self.check(code)
        self.assertGreater(counts.get('permissions.explicit_roots',0),0,counts)

    def test_writable_recursive_builders_keep_shared_returned_storage(self):
        prefix=['GLOBAL:>[]int{7}', 'Node::struct{values:[]int,left,right:^Node}',
            'leaf::proc(values:[]int)->^Node{return new_clone(Node{values=values})}']
        for index in range(18):
            callee='leaf' if index==0 else f'layer_{index-1}'
            prefix.append(f'''layer_{index}::proc(values:[]int)->^Node{{
    child:={callee}(values)
    return new_clone(Node{{values=values,left=child,right=child}})
}}''')
        body='''_=GLOBAL;values:=[1]int{7};node:=layer_17(values[:])
if node.left!=node.right||node.values[0]!=7{panic("builder aliases")}
for node!=nil{next:=node.left;free(node);node=next}
'''
        code=self.program(body,'\n'.join(prefix))
        for flags in (['-debug'],native.UNCHECKED):
            counts=self.check(code,flags=flags)
            self.assertLessEqual(counts.get('permissions.summaries_built',0),24,counts)
            self.assertLess(counts.get('permissions.values',0),3500,counts)
        invalid=self.program('''values:=[1]int{7};node:=layer_17(values[:])
node.left^=Node{values=GLOBAL}
node.right.values[0]=9
''','\n'.join(prefix))
        self.check(invalid,command='check',error='read-only slice')

    def test_type_property_reuse_keeps_generic_slice_and_inline_values_distinct(self):
        code=self.program('''a:=[1]int{7};view:>a[:]
for _ in 0..<8{if first(a)!=7||first(view)!=7{panic("types")}}
copy:=view[0];copy=9
if a[0]!=7||copy!=9{panic("inline copy")}
''','''Holder::struct($T:typeid){value:T}
first::proc(values:$T)->int{return values[0]}
''')
        for flags in (['-debug'],native.UNCHECKED):self.check(code,flags=flags)

    def test_long_reference_propagation_skips_only_stable_independent_statements(self):
        declarations='\n'.join(f'fixed_{n}:=len(values)+{n};_=fixed_{n}' for n in range(48))
        names=', '.join(f'a{n}' for n in range(8))
        assignments=';'.join(f'a{n}=a{n+1}' for n in range(7))+';a7=values'
        helper=f'''propagate::proc(values:[]int)->[]int{{
read_only:>values;_=read_only
{declarations}
{names}:[]int
for _ in 0..<9 {{{assignments}}}
if a0!=nil{{a0[0]=9}}
return a0
}}'''
        code=self.program('''seed:>0;_=seed
items:=[1]int{7};result:=propagate(items[:])
if result[0]!=9||items[0]!=9{panic("reference propagation")}
''',helper)
        for flags in (['-debug'],native.UNCHECKED):
            counts=self.check(code,flags=flags)
            self.assertGreater(counts.get('permissions.statements_skipped',0),40,counts)
        invalid=code.replace('result:=propagate(items[:])','view:>items[:];result:=propagate(view)')
        self.check(invalid,command='check',error='read-only slice')


if __name__=='__main__':unittest.main()
