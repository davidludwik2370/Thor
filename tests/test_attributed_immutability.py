"""Attributes must not erase immutable storage or slice permissions."""
import json
import unittest
from tests import test_rust_compiler as base


class AttributedImmutabilityTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)
    setUp=base.RustCompilerTests.setUp
    invoke=base.RustCompilerTests.invoke

    def rejected(self,body,prefix=''):
        text='package main\n'+prefix+'\n'+body+'\n'
        self.source.write_text(text)
        for flags in [[],base.UNCHECKED]:
            with self.subTest(flags=flags):
                result=self.invoke('rust','build',flags=[*flags,'-json-errors','--comptime=required'])
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                errors=json.loads(result.stderr)['errors']
                self.assertTrue(any('immutable' in str(e['msgs']).lower() or 'read-only' in str(e['msgs']).lower() for e in errors),errors)
                self.assertTrue(all(e['pos']['file']==str(self.source) for e in errors),errors)

    def test_static_scalar_mutations_and_addresses_are_rejected(self):
        for attr in ['@static','@(static)','@(static, rodata)','@(vet_ignore={.unused}) @static']:
            for operation in ['x+=y','x=y','_= &x']:
                with self.subTest(attr=attr,operation=operation):
                    self.rejected('danger::proc(y:int){'+attr+' x:>10;'+operation+'}\nmain::proc(){danger(1)}')
        self.rejected('danger::proc(y:int)->^int{@static x:>10;return &x}\nmain::proc(){_=danger(1)^}')

    def test_other_attributes_and_global_declarations_keep_immutability(self):
        for decl in ['@(vet_ignore={.unused}) x:>10','@(vet_ignore={.immutable}) x:>10','@(rodata) x:>10']:
            with self.subTest(decl=decl):
                self.rejected('main::proc(){x+=1}',decl)
        self.rejected('main::proc(){@(vet_ignore={.unused}) x:>10;x+=1}')

    def test_static_aggregate_and_slice_mutation_paths(self):
        for decl,operation in [
            ('x:>[2]int{1,2}','x[0]=3'),
            ('x:>struct{n:int}{1}','x.n=3'),
            ('x:>[]int{1,2}','x[0]=3'),
            ('x:>[]int{1,2}','_= &x[0]'),
            ('x:>[]int{1,2}','alias:=x;_=alias'),
            ('x:>[2]int{1,2}','for &n in x{n=3}'),
            ('x:>struct{n:int}{1}','using x;n=3'),
        ]:
            with self.subTest(decl=decl,operation=operation):
                self.rejected('main::proc(){@static '+decl+';'+operation+'}')

    def test_grouped_and_generic_static_bindings_remain_protected(self):
        self.rejected('main::proc(){@static a,b:>1,2;b+=1}')
        self.rejected('f::proc($T:typeid){@static x:T>0;x+=1}\nmain::proc(){f(int)}')

    def test_mutable_static_and_immutable_value_copies_still_work(self):
        text='''package main
next::proc()->^int{@static x:=10;x+=1;return &x}
main::proc(){
 if next()^!=11 || next()^!=12 || next()^!=13{panic("static persistence")}
 @static values:>[2]int{1,2}
 copy:=values;copy[0]=7
 if values[0]!=1 || copy[0]!=7{panic("value copy")}
 @static cell:=1
 @(vet_ignore={.unused}) pointer:> &cell
 pointer^=7
 if cell!=7{panic("pointer binding is shallow")}
}
'''
        self.source.write_text(text)
        for flags in [[],base.UNCHECKED]:
            result=self.invoke('rust','run',flags=flags)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_cached_parser_preserves_attributed_immutable_flags(self):
        text='package main\nmain::proc(){@static x:>10;_=x}\n'
        self.source.write_text(text)
        for _ in range(2):
            result=self.invoke('rust','ast')
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(result.stdout)
            def declarations(value):
                if isinstance(value,dict):
                    if value.get('kind')=='Declaration':yield value
                    for v in value.values():yield from declarations(v)
                elif isinstance(value,list):
                    for v in value:yield from declarations(v)
            rows=list(declarations(data))
            self.assertTrue(any(row.get('immutable') is True for row in rows),data)

    def test_mutable_static_is_not_pure_or_compiletime_eligible(self):
        for declaration,call in [('fn','_=next(1)'),('proc','_= #run next(1)')]:
            self.source.write_text('package main\nnext::'+declaration+'(n:int)->int{@static saved:=0;saved+=n;return saved}\nmain::proc(){'+call+'}\n')
            result=self.invoke('rust','check')
            self.assertNotEqual(result.returncode,0,result.stderr)
            self.assertIn('persistent static storage',result.stderr)

    def test_immutable_static_values_can_still_be_pure(self):
        self.source.write_text('package main\nnext::fn(n:int)->int{@static saved:>10;return saved+n}\nmain::proc(){if next(3)!=13{panic("pure static value")}}\n')
        result=self.invoke('rust','run')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_readonly_static_native_tables_remain_eligible_for_pure_reads(self):
        self.source.write_text('package main\nimport "core:math"\nread::fn(n:int)->int{@(static,rodata) table:=[2]int{3,4};return table[n]+int(math.pow10(f64(2)))}\nmain::proc(){if read(0)!=103{panic("read-only tables")}}\n')
        result=self.invoke('rust','run')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_mutation_of_a_native_readonly_static_is_not_pure(self):
        self.source.write_text('package main\nf::fn(n:int)->int{@(static,rodata) value:=0;value+=n;return value}\nmain::proc(){_=f(1)}\n')
        result=self.invoke('rust','check')
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertIn('persistent static storage',result.stderr)

    def test_static_state_is_not_folded_in_any_comptime_mode(self):
        self.source.write_text('package main\nnext::proc()->int{@static n:=0;n+=1;return n}\nmain::proc(){callback:=next;a:=next();b:=next();c:=callback();if a!=1 || b!=2 || c!=3{panic("static folding")}}\n')
        for mode in ['required','normal','aggressive','override']:
            with self.subTest(mode=mode):
                result=self.invoke('rust','run',flags=['--comptime='+mode])
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=self.invoke('rust','vet',flags=['--vet=pure','-json-errors'])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn("Procedure 'next' can be declared",result.stderr)


if __name__=='__main__':unittest.main()
