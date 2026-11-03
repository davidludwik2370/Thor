"""Purity rejection categories, matched native proc controls and exact origins."""
import json
from pathlib import Path
import subprocess
import unittest
from tests import test_thor as base
from tests.purity_cases import EFFECTS, TYPES


class PurityHardeningTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def check_case(self,case,mode):
        text=case.source(kind='proc' if mode=='run' else 'fn',mode=mode)
        root,result=self.invoke(text,command='check',flags=['-json-errors',*(['-debug'] if mode=='runtime' else [])],fail=case.message)
        errors=json.loads(result.stderr)['errors']
        relevant=[e for e in errors if case.message in ' '.join(e['msgs'])]
        self.assertTrue(relevant,errors)
        offset=case.offset(text)
        self.assertTrue(any(e['pos'] and Path(e['pos']['file'])==root.resolve()/'input/main.thor'
                            and e['pos']['offset']<=offset<e['pos']['end_offset'] for e in relevant),errors)
        for forbidden in ['Thor expression','__thor_','thor-run-','pure_validate','Called within \'validate\'','invalid type','source location was provided','SIGBUS','Traceback']:
            self.assertNotIn(forbidden,result.stderr)
        return errors

    def test_effect_rejection_matrix_in_runtime_automatic_and_explicit_execution(self):
        for case in EFFECTS:
            for mode in ['runtime','automatic','run']:
                with self.subTest(case=case.name,mode=mode):self.check_case(case,mode)

    def test_signature_type_matrix_points_to_the_specific_input_or_result(self):
        for case in TYPES:
            for mode in ['runtime','automatic','run']:
                with self.subTest(case=case.name,mode=mode):
                    errors=self.check_case(case,mode)
                    self.assertTrue(any('While checking' in ' '.join(e['msgs']) for e in errors),errors)

    def test_equivalent_procedures_are_accepted_without_purity_contracts(self):
        for case in [*EFFECTS,*TYPES]:
            with self.subTest(case=case.name):
                # Check without executing I/O, deletion, undefined storage, or
                # foreign calls; this is an ordinary Odin validity control.
                text=case.source(kind='proc')
                self.invoke(text,command='check',flags=['-debug'])

    def test_unnamed_missing_and_mistyped_calls_keep_the_underlying_native_errors(self):
        for body,needle,message in [('return missing(n)','missing','Undeclared name: missing'),
                                    ('return "wrong"','"wrong"','Cannot convert'),
                                    ('return n+"wrong"','"wrong"','Cannot convert')]:
            text=self.program('n:=3;_=bad(n)','bad::fn(n:int)->int{'+body+'}\n')
            _,result=self.invoke(text,command='check',flags=['-debug','-json-errors'],fail=message)
            errors=json.loads(result.stderr)['errors'];offset=text.index(needle)
            self.assertTrue(any(e['pos'] and e['pos']['offset']<=offset<e['pos']['end_offset'] for e in errors),errors)
            self.assertNotIn('unknown calls are forbidden',result.stderr)

    def test_named_copy_propagates_nested_borrowing_and_clone_aliases_stay_shallow(self):
        for body in ['local:=make([][]int,1);copy(src=input,dst=local);local[0][0]=9',
                     'local:=clone(a=input);local[0][0]=9']:
            text=self.program('data:=[1,2];rows:=[data[:]];_=bad(rows[:])',
                'import "core:slice"\nclone::(slice.clone)\nbad::fn(input:[][]int)->int{'+body+';return 0}\n')
            self.invoke(text,command='check',flags=['-debug'],fail='borrowed from an input')

    def test_valid_named_copy_clone_aliases_and_panic_declarations_in_checked_and_release(self):
        text=self.program('''data:=[1,2];if work(data[:])!=9||data[0]!=1{panic("copy")}
if VALUE!=9{panic("compile-time copy")}
''','''import "core:slice"
clone::(slice.clone)
work::fn(input:[]int)->int{local:=clone(a=input);copy(src=[]int{9},dst=local);return local[0]}
VALUE::#run work([]int{7})
never::fn()->int{panic("not called")}
never_owned::fn()->string{panic("not called")}
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            with self.subTest(flags=flags):
                _,result=self.invoke(text,flags=flags,expected='')
                self.assertNotIn('still allocated',result.stderr)

    def test_native_random_and_time_effects_highlight_the_user_call(self):
        for prefix,expr in [('import "core:math/rand"','rand.int_max(n)'),('import "core:time"','time.tick_now()')]:
            text=self.program('n:=7;_=bad(n)',prefix+'\nbad::fn(n:int)->int{_='+expr+';return n}\n')
            _,result=self.invoke(text,command='check',flags=['-debug','-json-errors'],fail='Pure code')
            errors=json.loads(result.stderr)['errors'];offset=text.index(expr)
            self.assertTrue(all(e['pos'] and e['pos']['file'].endswith('/main.thor') for e in errors),errors)
            self.assertTrue(any(e['pos']['offset']<=offset<e['pos']['end_offset'] for e in errors),errors)
            self.assertTrue(any(e.get('related') for e in errors),errors)
            self.assertNotIn('invalid type',result.stderr)

    def test_restrictions_survive_assertion_disabled_unchecked_compilation(self):
        for case in [*EFFECTS,*TYPES]:
            with self.subTest(case=case.name):
                self.invoke(case.source(),command='build',flags=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'],fail=case.message)
        self.invoke(self.program('_=bad()', 'import "core:fmt"\nbad::fn()->int{assert(fmt.println("not allowed")>=0);return 7}\n'),
                    command='check',flags=['-disable-assert'],fail='performs I/O')

    def test_effect_inventory_is_enforced_through_ordinary_transitive_calls(self):
        for case in EFFECTS:
            with self.subTest(case=case.name):
                text=case.source('proc').split('main::proc()')[0]
                text+='work::fn()->int{return bad('+case.args+')}\nmain::proc(){_=work()}\n'
                _,result=self.invoke(text,command='check',flags=['-debug','-json-errors'],fail=case.message)
                errors=json.loads(result.stderr)['errors'];offset=case.offset(text)
                self.assertTrue(any(e['pos'] and e['pos']['offset']<=offset<e['pos']['end_offset']
                                    and case.message in ' '.join(e['msgs']) for e in errors),errors)
                self.assertNotIn('Thor expression',result.stderr)

    def test_generic_branch_laziness_for_multiple_effect_categories(self):
        for case in [case for case in EFFECTS if case.name in {'global_read','context','address','uninitialized','io','intrinsic'}]:
            with self.subTest(case=case.name):
                prefix=case.prefix+'\nwork::fn(value:$T)->int{when T==int{return 7}else{n:=7;'+case.body+'}}\n'
                self.invoke(self.program('number:=7;if work(number)!=7{panic("selected branch")}',prefix),expected='')
                self.invoke(self.program('text:="active";_=work(text)',prefix),command='check',fail=case.message)

    def test_bodyless_and_foreign_fn_declarations_are_located_errors_not_tracebacks(self):
        for declaration in ['bad::fn(n:int)->int---','foreign libc{bad::fn(n:int)->int---}']:
            text=self.program('',declaration+'\n')
            _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='require an inspectable body')
            errors=json.loads(result.stderr)['errors']
            self.assertEqual(len(errors),1,errors)
            self.assertEqual(errors[0]['pos']['offset'],text.index('fn('))
            self.assertNotIn('Traceback',result.stderr)

    def test_calling_conventions_deferred_callbacks_and_using_schema_contracts(self):
        for prefix,body,message,needle in [
            ('bad::fn "c"(n:int)->int{return n}','n:=7;_=bad(n)','Odin calling convention','fn "c"'),
            ('state:int\nnotify::proc(){state+=1}\n@(deferred_none=notify)bad::fn(n:int)->int{return n}',
             'n:=7;_=bad(n)','implicit deferred procedure callbacks','@(deferred_none'),
            ('Box::struct{value:int}\nbad::fn(using input:$T)->int{return value}',
             '_=bad(Box{7})','resolved record schema','input:$T'),
        ]:
            with self.subTest(message=message):
                text=self.program(body,prefix+'\n')
                _,result=self.invoke(text,command='check',flags=['-debug','-json-errors'],fail=message)
                errors=json.loads(result.stderr)['errors'];offset=text.index(needle)
                self.assertTrue(any(e['pos'] and e['pos']['offset']<=offset<e['pos']['end_offset'] for e in errors),errors)

    def test_unknown_signature_type_keeps_one_original_name_error(self):
        text=self.program('','bad::fn(input:Missing)->int{return 0}\n')
        _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='Undeclared name: Missing')
        errors=json.loads(result.stderr)['errors']
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(errors[0]['pos']['offset'],text.index('Missing'))

    def test_raw_union_identity_does_not_reject_an_ordinary_record_with_identical_field_names(self):
        prefix='Raw::struct #raw_union{x,y:int}\nPlain::struct{x,y:int}\n'
        for annotation in ['Plain','$T']:
            with self.subTest(annotation=annotation):
                self.invoke(self.program('value:=Plain{7,9};if work(value)!=7{panic("schema")}',
                    prefix+'work::fn(value:'+annotation+')->int{return value.x}\n'))
        for annotation in ['Raw','$T']:
            with self.subTest(annotation=annotation):
                self.invoke(self.program('value:Raw;_=work(value)',
                    prefix+'work::fn(value:'+annotation+')->int{return value.x}\n'),command='check',fail='Raw-union storage cannot be proved pure')

    def test_type_only_uses_and_local_delete_alias_remain_eligible(self):
        self.invoke(self.program('if VALUE!=7{panic("type query")}', '''
drop::(delete)
work::fn(n:int)->int{
 local:=make([]int,n);drop(array=local)
 _=size_of(type_of(rawptr(nil)))
 return n
}
VALUE::#run work(7)
'''))

    def test_delete_location_arguments_are_not_mistaken_for_allocator_overrides(self):
        text=self.program('if VALUE!=7{panic("delete")};n:=3;if work(n)!=3{panic("runtime delete")}', '''
work::fn(n:int)->int{
 local:=make([]int,1);delete(loc={},array=local)
 growing:=make([dynamic]int,0);delete(growing,{})
 text:=f"text {n}";delete(str=text,loc={})
 return n
}
VALUE::#run work(7)
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            _,result=self.invoke(text,flags=flags,expected='')
            self.assertNotIn('still allocated',result.stderr)

    def test_unnamed_owned_or_return_results_keep_their_native_behavior(self):
        text=self.program('''yes,no:=true,false
a,a_ok:=work(yes);defer delete(a)
b,b_ok:=work(no);defer delete(b)
if a!="text"||!a_ok||b!=""||b_ok||VALUE!=4{panic("or_return")}
''','''read::proc(ok:bool)->(string,bool)#optional_ok{return "text",ok}
work::fn(ok:bool)->(string,bool){text:=read(ok) or_return;return text,true}
consume::fn()->int{text,ok:=work(true);return ok?len(text):0}
VALUE::#run consume()
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            _,result=self.invoke(text,flags=flags,expected='')
            self.assertNotIn('still allocated',result.stderr)

    def test_package_lifecycle_attributes_cannot_enter_pure_declarations(self):
        for attribute in ['init','fini']:
            text=self.program('',f'@({attribute}) bad::fn(){{}}\n')
            _,result=self.invoke(text,command='check',flags=[f'-custom-attribute:{attribute}','-json-errors'],fail='initializers/finalizers')
            errors=json.loads(result.stderr)['errors']
            self.assertEqual(len(errors),1,errors)
            self.assertEqual(errors[0]['pos']['offset'],text.index('fn()'))

    def test_big_member_aliases_are_readonly_but_direct_numeric_copies_are_owned(self):
        for value in ['Integer(7)','Rational(7,3)']:
            for statement in ['local.value+=1','local.value=local.value+1','local.value,local.value=local.value,local.value+1']:
                with self.subTest(value=value,statement=statement):
                    text=self.program('_=bad(Box{'+value+'})',
                        'Box::struct{value:'+('Integer' if value.startswith('Integer') else 'Rational')+'}\nbad::fn(input:$B)->int{local:=input;'+statement+';return 0}\n')
                    self.invoke(text,command='check',flags=['-debug'],fail='borrowed from an input')
        text=self.program('''value:=Box(Integer){Integer(7)};defer delete(value.value)
result:=work(value);defer delete(result)
if result!=8||value.value!=7||VALUE!=8{panic("numeric clone")}
plain:=Box(int){7};if scalar(plain)!=8||plain.value!=7{panic("value copy")}
''','''Box::struct($T:typeid){value:T}
work::fn(input:$B)->Integer{
    local:=input.value;defer delete(local)
    alias:=local;defer delete(alias)
    local+=1
    if alias+1!=local{panic("alias clone")}
    return local
}
scalar::fn(input:$B)->int{local:=input;local.value+=1;return local.value}
consume::fn()->int{input:=Box(Integer){Integer(7)};result:=work(input);return result==8?8:0}
VALUE::#run consume()
''')
        for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
            _,result=self.invoke(text,flags=flags,expected='')
            self.assertNotIn('still allocated',result.stderr)

    def test_generic_numeric_properties_transfer_their_existing_copy(self):
        for kind in ['proc','fn']:
            text=self.program('''r:=Rational(22,13);defer delete(r)
a:=numer(r);defer delete(a)
b:=denom(r);defer delete(b)
record:=Record{Integer(7)};defer delete(record.numerator)
c:=numer(record);defer delete(c)
if a!=22||b!=13||c!=7||r!=Rational(22,13){panic("property copy")}
''','''Record::struct{numerator:Integer}
numer::'''+kind+'''(value:$T)->Integer{return (value.numerator)}
denom::'''+kind+'''(value:$T)->Integer{
    local:=value.denominator;defer delete(local)
    alias:=local;defer delete(alias)
    local+=1
    if alias+1!=local{panic("alias clone")}
    return alias
}
''')
            for flags in [['-debug'],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert','--track-allocations=basic']]:
                with self.subTest(kind=kind,flags=flags):
                    _,result=self.invoke(text,flags=flags,expected='')
                    self.assertEqual(result.stderr,'')

    def test_generic_numeric_representation_is_opaque_without_reserving_user_field_names(self):
        for kind,value,field in [('Integer','Integer(7)','_value'),('Rational','Rational(7,3)','_numerator')]:
            with self.subTest(kind=kind):
                text=self.program('_=bad('+value+')','bad::fn(input:$T)->int{_=input.'+field+';return 0}\n')
                self.invoke(text,command='check',flags=['-debug'],fail='implementation storage')
        self.invoke(self.program('value:=Box{7};if read(value)!=7||VALUE!=8{panic("user field")}', '''
USE_BIG::Integer(0)
Box::struct{_value:int}
read::fn(input:$T)->int{return input._value}
advance::fn(input:$T)->int{local:=input;local._value+=1;return local._value}
VALUE::#run advance(Box{7})
'''))
        self.invoke(self.program('value:=Integer(7);_=bad(value)', '''import "library"
bad::fn(input:Integer)->int{return library.read(input)}
'''),files={'library/main.odin':'package library\nread::proc(input:$T)->int{_=input._value;return 0}\n'},
                    command='check',flags=['-debug'],fail='implementation storage')

    def test_checked_procedures_do_not_reuse_unchecked_expression_helpers(self):
        for result_type,body,needle,message in [
            ('int','return switch{case n>0:state;case:0}','state','outside the function'),
            ('int','data:=[1,2];return data[state]','state]','outside the function'),
            ('string','return f"{read(n)}"','state+n','outside the function'),
            ('string','return f"{Record{n}}"','Record{n}','deterministic scalar/string'),
        ]:
            for mode in ['runtime','automatic','run']:
                with self.subTest(body=body,mode=mode):
                    prefix='state:int\nRecord::struct{x:int}\nread::proc(n:int)->int{return state+n}\n'
                    prefix+='leaf::proc(n:int)->'+result_type+'{'+body+'}\n'
                    prefix+='work::fn(n:int)->'+result_type+'{return leaf(n)}\n'
                    usage='n:=7;_=work(n)' if mode=='runtime' else '_=VALUE'
                    if mode!='runtime':prefix+='VALUE::'+('#run ' if mode=='run' else '')+'work(7)\n'
                    text=self.program(usage,prefix)
                    _,result=self.invoke(text,command='check',flags=['-json-errors',*(['-debug'] if mode=='runtime' else [])],fail=message)
                    errors=json.loads(result.stderr)['errors']
                    offset=text.index(needle,text.index('leaf::')) if needle!='state+n' else text.index(needle)
                    self.assertTrue(any(e['pos'] and e['pos']['offset']<=offset<e['pos']['end_offset'] for e in errors),errors)
                    ordinary=prefix.split('work::fn')[0]
                    self.invoke(self.program('n:=7;_=leaf(n)',ordinary),command='check',flags=['-debug'])


if __name__=='__main__':unittest.main()
