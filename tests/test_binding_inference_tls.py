"""Binding colors survive inference gaps; valid container calls retain real types."""
import re
import subprocess
import unittest

from tests import test_tls as base


class BindingInferenceTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    position = staticmethod(base.TLSTests.position)
    hover = base.TLSTests.hover
    semantic_spans = base.TLSTests.semantic_spans

    def check_project(self, path):
        result = subprocess.run([str(base.ROOT / 'thor'), 'check', str(path),
                                 '--output-dir', str(path.parent / 'checked')],
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def check_bindings(self, client, path, text, expected):
        spans = self.semantic_spans(client, path, text)
        modifiers = client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        readonly, constant = 1 << modifiers.index('readonly'), 1 << modifiers.index('thorCompileTime')
        for name, (typ, immutable) in expected.items():
            with self.subTest(name=name):
                actual = [(kind, bool(mods & readonly), bool(mods & constant))
                          for _, _, kind, token, mods in spans if token == name]
                count = len(re.findall(r'\b' + re.escape(name) + r'\b', text))
                self.assertEqual(actual, [('variable', immutable, False)] * count)
                if typ is not None:
                    needle = re.search(r'\b' + re.escape(name) + r'\b.', text).group()
                    response = self.hover(client, path, text, needle)
                    self.assertIsNotNone(response)
                    value = response['contents']['value']
                    self.assertIn(name + ': ' + typ, value)
                    self.assertEqual('immutable storage' in value, immutable)
                    self.assertNotIn('$E', value)
                    self.assertNotIn('$N', value)

    def test_fixed_capacity_pop_family_pointer_aliases_and_mutable_controls(self):
        text = '''// 😀 Hilo-style loop and declared aliases
package main
import runtime "base:runtime"
LIMIT::8
Deck::[dynamic;LIMIT]int
take::pop
safe_take::pop_safe
main::proc(){
    deck:Deck
    dyn:[dynamic]int
    pointer:=&deck
    for len(deck)>0 {
        drawn_card :> pop(&deck)
        from_pointer :> pop(pointer)
        from_alias :> take(&deck)
        from_runtime :> runtime.pop(&deck)
        fixed_safe, fixed_ok :> pop_safe(&deck)
        aliased_safe, aliased_ok :> safe_take(&deck)
        dynamic_value :> pop(&dyn)
        dynamic_safe, dynamic_ok :> pop_safe(&dyn)
        mutable := pop(&deck)
        mutable_safe, mutable_ok := pop_safe(&deck)
        _=drawn_card;_=from_pointer;_=from_alias;_=from_runtime
        _=fixed_safe;_=fixed_ok;_=aliased_safe;_=aliased_ok
        _=dynamic_value;_=dynamic_safe;_=dynamic_ok;_=mutable;_=mutable_safe;_=mutable_ok
    }
}
'''
        client, path = self.open(text)
        self.check_project(path)
        names = ['drawn_card', 'from_pointer', 'from_alias', 'from_runtime', 'fixed_safe',
                 'fixed_ok', 'aliased_safe', 'aliased_ok', 'dynamic_value', 'dynamic_safe',
                 'dynamic_ok', 'mutable', 'mutable_safe', 'mutable_ok']
        expected = {name: ('bool' if name.endswith('_ok') else 'int', not name.startswith('mutable'))
                    for name in names}
        self.check_bindings(client, path, text, expected)
        broken = text.replace('        _=drawn_card;', '        drawn_card=2\n        _=drawn_card;')
        client.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': 2},
                                             'contentChanges': [{'text': broken}]})
        errors = client.diagnostic(path.as_uri(), contains="immutable storage 'drawn_card'")
        error = next(e for e in errors if "immutable storage 'drawn_card'" in e['message'])
        self.assertEqual(error['range']['start'], self.position(broken, 'drawn_card=2', 0))
        self.check_bindings(client, path, broken, expected)
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': 3},
                                             'contentChanges': [{'text': text}]})
        client.diagnostic(path.as_uri(), empty=True)
        self.check_bindings(client, path, text, expected)
        self.assertEqual(path.read_text(), text)

    def test_fixed_capacity_generic_elements_capacities_and_result_substitution(self):
        text = '''package main
peek::proc(a:^$A/[dynamic;$N]$E)->E{return a[0]}
whole::proc(a:$A/[dynamic;$N]$E)->A{return a}
copy_shape::proc(a:$A/[dynamic;$N]$E)->[dynamic;N]E{return a}
array_shape::proc(a:$A/[dynamic;$N]$E)->[N]E{return {}}
pair::proc(a:^$A/[dynamic;$N]$E)->(E,int){return a[0],N}
Box::struct($E:typeid,$N:int){values:[dynamic;N]E}
box::proc(a:$A/[dynamic;$N]$E)->Box(E,N){return {a}}
main::proc(){
    ints:[dynamic;8]int
    words:[dynamic;3]string
    first :> peek(&ints)
    word :> peek(&words)
    header :> whole(ints)
    copied :> copy_shape(words)
    array :> array_shape(ints)
    boxed :> box(words)
    value, capacity :> pair(&ints)
    _=first;_=word;_=header;_=copied;_=array;_=boxed;_=value;_=capacity
}
'''
        client, path = self.open(text, compiler=False)
        self.check_project(path)
        expected = {'first': 'int', 'word': 'string', 'header': '[dynamic; 8]int',
                    'copied': '[dynamic; 3]string', 'array': '[8]int', 'boxed': path.parent.name + '.Box(string, 3)',
                    'value': 'int', 'capacity': 'int'}
        self.check_bindings(client, path, text, {n: (t, True) for n, t in expected.items()})

    def test_imported_generic_helpers_and_overloads_distinguish_element_and_capacity(self):
        library = '''package library
peek::proc(a:^$A/[dynamic;$N]$E)->E{return a[0]}
choose_int::proc(a:[dynamic;4]int)->int{return 1}
choose_string::proc(a:[dynamic;4]string)->string{return "string"}
choose_other::proc(a:[dynamic;5]int)->bool{return true}
choose_dynamic::proc(a:[dynamic]int)->f64{return 1}
choose::proc{choose_int,choose_string,choose_other,choose_dynamic}
'''
        text = '''package main
import lib "library"
import _ "library" / peek
alias::lib.peek
main::proc(){
    ints:[dynamic;4]int
    words:[dynamic;4]string
    other:[dynamic;5]int
    dyn:[dynamic]int
    first :> lib.peek(&ints)
    second :> alias(&words)
    third :> peek(&ints)
    selected_int :> lib.choose(ints)
    selected_string :> lib.choose(words)
    selected_bool :> lib.choose(other)
    selected_float :> lib.choose(dyn)
    _=first;_=second;_=third;_=selected_int;_=selected_string;_=selected_bool;_=selected_float
}
'''
        client, path = self.open(text, files={'library/main.odin': library}, compiler=False)
        self.check_project(path)
        expected = {'first': 'int', 'second': 'string', 'third': 'int', 'selected_int': 'int',
                    'selected_string': 'string', 'selected_bool': 'bool', 'selected_float': 'f64'}
        self.check_bindings(client, path, text, {n: (t, True) for n, t in expected.items()})

    def test_runtime_fallback_does_not_export_unrelated_compiler_builtins(self):
        text = '''package main
import rt "base:runtime"
main::proc(){
    deck:[dynamic;4]int
    good :> rt.pop(&deck)
    invalid :> rt.len(deck)
    _=good;_=invalid
}
'''
        client, path = self.open(text)
        self.check_bindings(client, path, text, {'good': ('int', True), 'invalid': (None, True)})
        errors = client.diagnostic(path.as_uri(), contains="'len' is not declared by 'rt'")
        self.assertTrue(any("'len' is not declared by 'rt'" in e['message'] for e in errors))
        invalid_hover = self.hover(client, path, text, 'invalid :')
        self.assertIsNone(invalid_hover)

    def test_binding_colors_survive_unknown_calls_shadowing_and_unsaved_recovery(self):
        text = '''package main
main::proc(){
    unknown :> missing()
    mutable := missing()
    first, second :> missing_pair()
    _=unknown;_=mutable;_=first;_=second
    {
        unknown := missing()
        _=unknown
    }
    _=unknown
}
'''
        client, path = self.open(text)
        readonly = 1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        def check(source):
            spans = self.semantic_spans(client, path, source)
            self.assertEqual([bool(mods & readonly) for _, _, _, name, mods in spans if name == 'unknown'],
                             [True, True, False, False, True])
            for name, immutable in [('mutable', False), ('first', True), ('second', True)]:
                self.assertEqual([(kind, bool(mods & readonly)) for _, _, kind, word, mods in spans if word == name],
                                 [('variable', immutable)] * 2)
            self.assertFalse(any(name in {'missing', 'missing_pair'} for _, _, _, name, _ in spans))
        check(text)
        errors = client.diagnostic(path.as_uri(), contains='Undeclared name: missing')
        self.assertTrue(any('Undeclared name: missing' in e['message'] for e in errors))
        fixed = text + '\nmissing::proc()->int{return 1}\nmissing_pair::proc()->(int,string){return 1,"two"}\n'
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': 2},
                                             'contentChanges': [{'text': fixed}]})
        client.diagnostic(path.as_uri(), empty=True)
        check_spans = self.semantic_spans(client, path, fixed)
        self.assertEqual([bool(mods & readonly) for _, _, _, name, mods in check_spans if name == 'unknown'],
                         [True, True, False, False, True])
        self.assertIn('unknown: int', self.hover(client, path, fixed, 'unknown :')['contents']['value'])
        self.assertIn('second: string', self.hover(client, path, fixed, 'second :')['contents']['value'])
        client.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': 3},
                                             'contentChanges': [{'text': text}]})
        check(text)
        self.assertEqual(path.read_text(), text)

    def test_unknown_types_keep_parameters_global_constants_and_iteration_colors(self):
        text = '''package main
GLOBAL :> missing()
CONSTANT :: missing()
worker::proc(input:MissingType){
    _=input;_=GLOBAL;_=CONSTANT
    for item, index in input {
        _=item;_=index
    }
    for &reference in input {
        _=reference
    }
}
main::proc(){}
'''
        client, path = self.open(text, compiler=False)
        spans = self.semantic_spans(client, path, text)
        legend = client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        readonly, constant = 1 << legend.index('readonly'), 1 << legend.index('thorCompileTime')
        for name, kind, immutable, compile_time in [
            ('GLOBAL', 'variable', True, False), ('CONSTANT', 'variable', True, True),
            ('input', 'parameter', True, False), ('item', 'variable', True, False),
            ('index', 'variable', True, False), ('reference', 'variable', False, False),
        ]:
            with self.subTest(name=name):
                actual = [(k, bool(m & readonly), bool(m & constant)) for _, _, k, n, m in spans if n == name]
                self.assertEqual(actual, [(kind, immutable, compile_time)] * len(re.findall(r'\b' + name + r'\b', text)))
        self.assertFalse(any(name in {'MissingType', 'missing'} for _, _, _, name, _ in spans))
        # Unknown types must not turn into fabricated type hovers.
        global_hover = self.hover(client, path, text, 'GLOBAL :')
        item_hover = self.hover(client, path, text, 'item;')
        self.assertIsNone(global_hover)
        self.assertIsNone(item_hover)

    def test_builtin_numeric_results_do_not_mutate_operand_types(self):
        text = '''package main
import math "core:math"
import slice "core:slice"
constructor::complex
project::real
main::proc(){
    number:f32=2
    integer:=-2
    z :> complex(number,number)
    nested :> real(complex(f32(1),f32(2)))
    aliased :> constructor(f32(1),f32(2))
    re :> project(z)
    im :> imag(z)
    conjugate :> conj(z)
    q :> quaternion(real=f64(1),imag=f64(2),jmag=f64(3),kmag=f64(4))
    qre :> real(q)
    qj :> jmag(q)
    qk :> kmag(q)
    qconj :> conj(q)
    positive :> math.abs(f64(integer))
    deck:[dynamic;8]int
    raw :> raw_data(&deck)
    cloned :> slice.clone(deck[:])
    mutable := real(z)
    _=z;_=nested;_=aliased;_=re;_=im;_=conjugate;_=q;_=qre;_=qj;_=qk;_=qconj
    _=positive;_=raw;_=cloned;_=mutable;_=number
}
'''
        client, path = self.open(text, compiler=False)
        self.check_project(path)
        expected = {'z': 'complex64', 'nested': 'f32', 'aliased': 'complex64', 're': 'f32',
                    'im': 'f32', 'conjugate': 'complex64', 'q': 'quaternion256', 'qre': 'f64',
                    'qj': 'f64', 'qk': 'f64', 'qconj': 'quaternion256', 'positive': 'f64',
                    'raw': '[^]int', 'cloned': '[]int', 'mutable': 'f32'}
        for _ in range(2):
            self.check_bindings(client, path, text, {n: (t, n != 'mutable') for n, t in expected.items()})
            value = self.hover(client, path, text, 'number:f32')['contents']['value']
            self.assertIn('number: f32', value)
            self.assertNotIn('complex', value)

    def test_builtin_numeric_components_preserve_all_three_precision_levels(self):
        for typ, complex_type, quaternion_type in [('f16', 'complex32', 'quaternion64'),
                                                  ('f32', 'complex64', 'quaternion128'),
                                                  ('f64', 'complex128', 'quaternion256')]:
            with self.subTest(type=typ):
                text = f'''package main
import math "core:math"
main::proc(){{
    number:{typ}=1
    z :> complex(number,number)
    q :> quaternion(real=number,imag=number,jmag=number,kmag=number)
    real_z :> real(z)
    imag_z :> imag(z)
    magnitude :> math.abs(z)
    qpart :> kmag(q)
    qabs :> abs(q)
    _=z;_=q;_=real_z;_=imag_z;_=magnitude;_=qpart;_=qabs
}}
'''
                client, path = self.open(text, compiler=False)
                self.check_project(path)
                expected = {'z': complex_type, 'q': quaternion_type, 'real_z': typ, 'imag_z': typ,
                            'magnitude': typ, 'qpart': typ, 'qabs': typ}
                self.check_bindings(client, path, text, {n: (t, True) for n, t in expected.items()})


if __name__ == '__main__':
    unittest.main()
