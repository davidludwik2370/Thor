"""Differential hover-layout checks against Odin's cross-target constant data."""
import os
import re
import subprocess
import threading
import time
import unittest

from tests import test_tls as base

MARKER = '\n\n<!-- thor-hover-size -->\n'
TARGETS = [
    ('linux_arm64', 'arm64', 'linux'),
    ('linux_amd64', 'amd64', 'linux'),
    ('linux_i386', 'i386', 'linux'),
    ('linux_arm32', 'arm32', 'linux'),
    ('freestanding_wasm32', 'wasm32', 'freestanding'),
    ('freestanding_wasm64p32', 'wasm64p32', 'freestanding'),
]
if os.name=='nt':
    TARGETS=[('windows_amd64','amd64','windows'),('windows_i386','i386','windows'),*TARGETS]


def layout_fixture():
    definitions = [
        'Box::struct($T:typeid){value:T}',
        'Sized::struct($T:typeid,$N:int){value:[N]T}',
        'Choice::union($T:typeid){T,bool}',
        'Aligned::struct($T:typeid) #align(align_of(T)){tag:u8,value:T}',
        'Counted::struct($N:int){value:[N+1]u8}',
        'Bounded::struct($A:int) #min_field_align(A){tag:u8,value:i64}',
        'Outer::struct($T:typeid){value:Aligned(T)}',
        'OuterCounted::struct($N:int){value:Counted(N+2)}',
        'UnionCount::union($N:int){[N+1]u8,bool}',
        'T::i32',
        'E::enum{a,b,c}',
        'EmptyE::enum{}',
        'SparseE::enum{a=-2,b=3}',
        'Count::size_of(int)',
    ]
    types = {}

    def add(expr):
        name = f'A{len(types):03}'
        types[name] = expr
        definitions.append(f'{name}::{expr}')
        return name

    for scalar in ['u8', 'u16', 'i32', 'i64', 'i128', 'f64', 'complex128',
                   'quaternion256', 'int', 'uintptr', 'typeid', 'any']:
        for expr in [scalar, f'[0]{scalar}', f'[1]{scalar}', f'[3]{scalar}',
                     f'struct{{a,b:{scalar}}}', f'struct{{tag:u8,value:{scalar}}}',
                     f'struct #align(32){{value:{scalar}}}',
                     f'struct #raw_union{{value:{scalar},bytes:[32]u8}}',
                     f'Box({scalar})', f'Sized({scalar},3)', f'Choice({scalar})']:
            if expr not in ('any', 'typeid'):
                add(expr)
    for expr in ['struct{}', 'struct #align(32){}', '[E]f64', '#sparse[E]u16',
                 '[size_of(int)-size_of(int)+3]u8', '[size_of(int)/size_of(int)]u8',
                 '[Count]u8', '[Count*0]u8', 'bit_set[0..<64]', 'bit_set[0..<128]',
                 'bit_set[0..<size_of(int)]', 'struct #packed{a:u8,b:f64}',
                 'struct #max_field_align(2){a:u8,b:f64}',
                 'union #align(32){int,u8}', 'union #no_nil{f64,u8}',
                 '[]int', '[dynamic]int', '[dynamic;0]u8', '[dynamic;3]i128',
                 'map[int]int', '^int', '[^]int', 'proc(int)->int',
                 'matrix[3,4]f64', '#row_major matrix[3,4]f64',
                 '#simd[8]f32', '#simd[32]f64',
                 '#soa[3]struct{a:u8,b:f64}', '#soa[]struct{a,b:f64}',
                 '#soa[dynamic]struct{a,b:f64}', 'Sized(u8,size_of(int))',
                 'Sized(u8,Count)', 'Sized(u8,Count-Count+3)',
                 'Sized(Sized(u8,2),3)', 'union{^int}', 'union{rawptr}',
                 'struct{tag:u8,v:#simd[8]f32}', 'struct{tag:u8,v:matrix[4,3]f32}',
                 'struct{a:Box(u8),b:Box(i128),c:Box(f64),d:Box(uintptr)}',
                 'struct #min_field_align(8){}', 'struct #max_field_align(2){}',
                 'struct{a:u8,b:struct #min_field_align(8){}}',
                 'struct{a:u8,b:[0]i64}', 'struct #raw_union{a:[0]int,b:[16]u8}',
                 '#soa[3][2]f64', '#soa^#soa[]struct{a,b:f64}',
                 'struct #align(16){a:[0]int}', 'struct #packed{}']:
        add(expr)
    for expr in ['[EmptyE]i64', '#sparse[SparseE]u16',
                 'Aligned(f64)', 'Aligned(i128)', 'Aligned(uintptr)',
                 'Counted(size_of(int))', 'Bounded(2)', 'Bounded(16)',
                 'Aligned(T)', 'Outer(T)', 'Outer(i128)',
                 'OuterCounted(size_of(int))',
                 'struct{a:UnionCount(2),b:UnionCount(9),c:OuterCounted(3),d:OuterCounted(9)}',
                 'type_of([?]i64{1,2,3})', 'type_of(struct{a,b:f64}{1,2})',
                 'struct #align(32){value:[dynamic;3]u8}',
                 'bit_set[0..<64;u64]', 'bit_set[EmptyE]', 'bit_set[0..<0]', 'enum u64{a,b}',
                 'type_of([?]i64{3=7,0=1})', 'type_of([?]u8{2..<5=7})',
                 'struct{a:u8,b:union #align(16){[9]u8}}',
                 'struct{a:[32]u8,b:[0]struct #align(size_of(int)*8){}}',
                 'struct{a:[32]u8,b:[0]union{#simd[64]f64}}',
                 'struct{a:u8,b:bit_set[0..<128]}']:
        add(expr)
    for expr in list(types)[:]:
        # Aliases and wrapper generics must carry the same layout facts.
        add(expr)
        if not types[expr].startswith(('#simd', '#soa^')):
            add(f'distinct {expr}')
        add(f'Box({expr})')
        add(f'[3]{expr}')
    # Resolve different instances within a single hover to exercise cache keys.
    add('struct{a:Box(u8),b:Box(f64),c:Sized(i64,3),d:Sized(u8,9)}')
    add('struct{a:Choice(i64),b:Choice(u16),c:Choice(i128)}')
    return '\n'.join(definitions), types


def native_layouts(directory, definitions, names, target, files=None):
    for name, contents in (files or {}).items():
        destination = directory / name.replace('.thor', '.odin')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents)
    source = directory / 'oracle.odin'
    expressions = [f'{op}({name})' for name in names for op in ['size_of', 'align_of']]
    source.write_text('package main\n' + definitions +
        f'\n@(export) layout_data: [{len(expressions)}]i64 = {{' + ','.join(expressions) + '}\n')
    output = directory / target
    output.mkdir()
    result = subprocess.run([os.environ.get('THOR_ODIN', 'odin'), 'build', str(source), '-file',
        '-no-entry-point', '-build-mode:llvm-ir', '-o:none', '-thread-count:1',
        '-out:' + str(output), '-target:' + target], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    exported = next(line for file in output.glob('*.ll') for line in file.read_text().splitlines()
                    if line.startswith('@layout_data ='))
    values = [int(v) for v in re.findall(r'i64 (-?\d+)', exported)]
    if len(values) != len(expressions):
        raise RuntimeError(exported)
    return dict(zip(names, zip(values[::2], values[1::2])))


class HoverLayoutAuditTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    position = staticmethod(base.TLSTests.position)

    def test_cross_target_sizes_and_labels_match_native_layouts(self):
        definitions, types = layout_fixture()
        self.check_fixture(definitions, types)

    def test_imported_aliases_and_generic_counts_preserve_target_information(self):
        definitions = '''import "model"
Alias::model.Pair
Distinct::distinct Alias
Other::model.Record(f64,2)
Native::model.Record(uintptr,2)
Sized::model.Record(u8,model.COUNT)
Choice::model.Selection(i128)
Independent::model.Record(u8,model.COUNT-model.COUNT+3)
Grouped::struct{a:model.Record(u8,3),b:model.Record(i128,2),c:model.Selection(f64),d:model.Selection(u16)}
'''
        types = {name: expr for name, expr in (line.split('::', 1) for line in definitions.splitlines() if '::' in line)}
        self.check_fixture(definitions, types, files={'model/types.thor': '''package model
Pair::struct{a,b:f64}
Record::struct($T:typeid,$N:int){tag:u8,value:[N]T}
Selection::union($T:typeid){T,bool}
COUNT::size_of(uintptr)
'''})

    def check_fixture(self, definitions, types, files=None):
        names = list(types)
        text = 'package main\n' + definitions + '\nmain::proc(){\n' + '\n'.join(
            f'v{i}:{name};_=v{i}' for i, name in enumerate(names)) + '\n}\n'
        clients = {}
        native = {}
        for target, arch, os_name in TARGETS:
            client, main = self.open(text, compiler=False, initialization_options={
                'profile': 'target', 'profiles': [{'name': 'target', 'arch': arch, 'os': os_name}],
                'enable_hover_layout': True}, files=files)
            directory = main.parent / 'oracle'
            directory.mkdir()
            native[target] = native_layouts(directory, definitions, names, target, files)
            clients[target] = client, main
        problems = []
        for target, _, _ in TARGETS:
            client, main = clients[target]
            for i, name in enumerate(names):
                result = client.request('textDocument/hover', {'textDocument': {'uri': main.as_uri()},
                    'position': self.position(text, f'v{i}:{name}')})
                value = result['contents']['value'] if result else ''
                detail = value.split(MARKER, 1)[1].split('\n', 1)[0] if MARKER in value else ''
                size, alignment = native[target][name]
                dependent = len({profile[name][0] for profile in native.values()}) != 1
                actual_size = int(detail.split()[0]) if detail else None
                actual_dependent = 'target dependent' in detail
                actual_alignment = re.search(r'size=\d+, align=(\d+)', value)
                if actual_size != size or actual_dependent != dependent or not actual_alignment or int(actual_alignment[1]) != alignment:
                    problems.append(f'{target} {name} ({types[name]}): expected size={size}, align={alignment}, '
                        f'dependent={dependent}; got {detail!r}, alignment={actual_alignment[1] if actual_alignment else None}')
        self.assertEqual(len(problems), 0, f'{len(problems)} mismatches:\n' + '\n'.join(problems[:45]))

    def test_repeated_shared_graphs_and_unsaved_target_changes_remain_bounded(self):
        definitions = 'Tree::struct($T:typeid){a,b:T}\nT0::struct #align(32){value:int}\n' + '\n'.join(
            f'T{i}::struct{{a:T{i-1},b:T{i-1}}}' for i in range(1,19))
        definitions+='\nG0::T0\n'+'\n'.join(f'G{i}::Tree(G{i-1})' for i in range(1,13))
        text='package main\n'+definitions+'\nmain::proc(){value:T18;generic:G12;_=value;_=generic}\n'
        client,main=self.open(text,compiler=False)
        watchdog=threading.Timer(4,lambda: client.process.kill() if client.process.poll() is None else None)
        self.addCleanup(watchdog.cancel)
        watchdog.start()
        start=time.monotonic()
        for _ in range(5):
            result=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,'value:T18')})
            detail=result['contents']['value'].split(MARKER,1)[1].split('\n',1)[0]
            self.assertEqual(detail, f'{32*(2**18)} bytes')
        self.assertLess(time.monotonic()-start,3)
        generic=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'generic:G12')})
        self.assertIn(MARKER+f'{32*(2**12)} bytes',generic['contents']['value'])
        edited=text.replace('struct #align(32){value:int}', 'struct{value:int}')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},
            'contentChanges':[{'text':edited}]})
        result=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(edited,'value:T18')})
        self.assertIn('bytes · target dependent',result['contents']['value'])
        watchdog.cancel()

    def test_budget_and_invalid_size_arithmetic_do_not_invent_layouts(self):
        cases = ['Bad::union{[9223372036854775807]u8}',
            'Bad::[(9223372036854775807+9223372036854775807+4)]u8',
            'Bad::[(9223372036854775807*4)]u8',
            'Bad::[(-9223372036854775807-1)/-1]u8',
            'Bad::[1<<64]u8', 'Bad::[3/0]u8',
            'Bad::bit_set[-9223372036854775807..<9223372036854775807]',
            'Bad::struct{'+','.join(f'field{i}:i32' for i in range(4200))+'}']
        for definition in cases:
            with self.subTest(definition=definition[:70]):
                text='package main\n'+definition+'\nmain::proc(){value:Bad;_=value}\n'
                client,main=self.open(text,compiler=False)
                watchdog=threading.Timer(4,lambda: client.process.kill() if client.process.poll() is None else None)
                watchdog.start()
                try:
                    result=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
                        'position':self.position(text,'value:Bad')})
                    self.assertNotIn(MARKER,str(result))
                    self.assertIsNone(client.process.poll())
                finally:
                    watchdog.cancel()

    def test_completion_inference_for_indexed_literals_is_bounded_too(self):
        definitions='T0::struct #align(32){value:int}\n'+'\n'.join(
            f'T{i}::struct{{a:T{i-1},b:T{i-1}}}' for i in range(1,19))
        text='package main\n'+definitions+'''\nPoint::struct{x,y:i64}
main::proc(){
 values:=[?]Point{size_of(T18)={1,2}}
 values[0].
}\n'''
        client,main=self.open(text,compiler=False)
        watchdog=threading.Timer(4,lambda: client.process.kill() if client.process.poll() is None else None)
        watchdog.start()
        try:
            result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,'values[0].',len('values[0].')),
                'context':{'triggerKind':2,'triggerCharacter':'.'}})
            items=result.get('items',[]) if isinstance(result,dict) else result
            names={item['label'] for item in items}
            self.assertTrue({'x','y'}<=names,names)
            self.assertIsNone(client.process.poll())
        finally:
            watchdog.cancel()


if __name__ == '__main__':
    unittest.main()
