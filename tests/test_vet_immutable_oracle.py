"""Immutable advice checked against real compiler acceptance, not heuristics."""
import json
import os
import subprocess
import unittest
from tests import test_rust_compiler as native
from tests import test_rust_vet as fixtures


PREFIX='''package main
CHANGE::#config(CHANGE,false)
Hand::[dynamic;2]int
Box::struct($T:typeid){value:T}
Results::map[int]int
Row::struct{items:[]int}
make_hand::proc()->(hand:Hand){append(&hand,7);append(&hand,8);return}
pair::proc()->(a:Hand,b:[2]Hand){a=make_hand();b[0],b[1]=a,a;return}
read::proc(values:[]int)->int{return values[0]}
read_hands::proc(values:[]Hand)->int{return values[0][0]}
identity::proc(value:$T)->T{return value}
'''

# (declarations, main body, binding spelling(s), compile-accepted readonly,
#  manual-only). All runtime verdicts survive native assertion elision.
CASES={
 'global_read':('subject := 7','if subject!=7{panic("global")}', ['subject'],True,True),
 'global_written':('subject := 7\nchange::proc(){subject=9}', 'change();if subject!=9{panic("global write")}', ['subject'],False,False),
 'grouped_return':('','subject, companion := pair();if subject[0]+read_hands(companion[:])!=14{panic("pair")}', ['subject, companion'],True,False),
 'grouped_output_context':('record::proc(out:^Results){subject, companion := pair();out[0]=subject[0]+read_hands(companion[:])}', 'r:=make(Results);defer delete(r);record(&r);if r[0]!=14{panic("output")}', ['subject, companion'],True,False),
 'grouped_conditional_output':('record::proc(out:^Results){when CHANGE{out[99]=1};subject, companion := pair();out[0]=subject[0]+read_hands(companion[:])}', 'r:=make(Results);defer delete(r);record(&r);if r[0]!=14{panic("output")}', ['subject, companion'],True,False),
 'grouped_element_write':('','subject, companion := pair();subject[0]=9;if subject[0]+companion[0][0]!=16{panic("write")}', ['subject, companion'],True,False),
 'grouped_reassigned':('', 'subject, companion := pair();subject=make_hand();if subject[0]+companion[0][0]!=14{panic("reassign")}', ['subject, companion'],False,False),
 'slice_output_context':('record::proc(out:^Results){subject := []int{7};out[0]=read(subject)}', 'r:=make(Results);defer delete(r);record(&r);if r[0]!=7{panic("slice")}', ['subject'],True,False),
 'unrelated_slice_output':('record::proc(out:^Row){subject := []int{7};out^=Row{make([]int,read(subject))}}', 'row:Row;record(&row);defer delete(row.items);if len(row.items)!=7{panic("unrelated output")}', ['subject'],True,False),
 'copied_array_return':('create::proc()->[2]int{subject := [2]int{7,8};if read(subject[:])!=7{panic("read")};return subject}', 'copy:=create();copy[0]=9;if copy[0]!=9{panic("independent return")}', ['subject'],True,False),
 'copied_array_output':('record::proc(out:^[2]int){subject := [2]int{7,8};if read(subject[:])!=7{panic("read")};out^=subject}', 'copy:[2]int;record(&copy);copy[0]=9;if copy[0]!=9{panic("independent output")}', ['subject'],True,False),
 'copied_fixed_results':('choose::proc()->(best:Hand,rank:int){subject, companion := pair();if read_hands(companion[:])==7{return subject,2};return subject,1}', 'a,b:=choose();if a[0]!=7 || b!=2{panic("copied results")}', ['subject, companion'],True,False),
 'slice_alias_chain':('','subject := []int{7};companion := subject;last := identity(companion);if read(last)!=7{panic("aliases")}', ['subject','companion','last'],True,False),
 'nested_slice_aliases':('','subject := Box([]int){[]int{7}};companion := subject.value;if read(companion)!=7{panic("field")}', ['subject','companion'],True,False),
 'using_slice_alias':('','using subject := Box([]int){[]int{7}};companion := value;if read(companion)!=7{panic("using slice")}', ['subject','companion'],True,False),
 'inline_copy_mutated_independently':('','subject := make_hand();copy:=subject;copy[0]=9;if subject[0]!=7 || copy[0]!=9{panic("copy")}', ['subject'],True,False),
 'reference_alias_mutated':('','subject := []int{7};alias:=subject;alias[0]=9;if subject[0]!=9{panic("alias write")}', ['subject'],False,False),
 'pointer_pointee':('','value:=1;subject := &value;subject^=9;if value!=9{panic("pointer")}', ['subject'],True,False),
 'pointer_field':('','value:=1;subject := Box(^int){&value};subject.value^=9;if value!=9{panic("field pointer")}', ['subject'],True,False),
 'promoted_pointer_field':('Node::struct{number:int}\nLink::struct{using node:^Node}', 'node:=Node{1};subject := Link{&node};subject.number=9;if node.number!=9{panic("promoted pointer")}', ['subject'],True,False),
 'using_pointer_alias':('','pointed:=1;using subject := Box(^int){&pointed};value^=9;if pointed!=9{panic("using pointer")}', ['subject'],True,False),
 'pointer_array_projection':('','values:=[1,2];subject := &values;subject[0]=9;if values[0]!=9{panic("pointer array")}', ['subject'],True,False),
 'address_of_pointee':('','value:=1;subject := &value;q:=&subject^;q^=9;if value!=9{panic("pointee address")}', ['subject'],True,False),
 'dynamic_alias_data_write':('','owner:=make([dynamic]int,1);defer delete(owner);subject := owner;subject[0]=9;if owner[0]!=9{panic("dynamic alias")}', ['subject'],True,False),
 'map_alias_copy_write':('','owner:=make(Results);defer delete(owner);owner[0]=1;subject := owner;copy:=subject;copy[0]=9;if owner[0]!=9{panic("map alias")}', ['subject'],True,False),
 'map_direct_write':('','subject := make(Results);defer delete(subject);subject[0]=9;if subject[0]!=9{panic("map write")}', ['subject'],False,False),
 'direct_reassignment':('','subject := 7;subject=9;if subject!=9{panic("assignment")}', ['subject'],False,False),
 'inline_field_write':('','subject := Box(int){7};subject.value=9;if subject.value!=9{panic("field write")}', ['subject'],False,False),
 'direct_address':('','subject := 7;p:=&subject;p^=9;if subject!=9{panic("address")}', ['subject'],False,False),
 'active_when':('','when true{subject := 7;if subject!=7{panic("when")}}', ['subject'],True,False),
 'static_local':('','@(static) subject := 7;if subject!=7{panic("static")}', ['subject'],True,False),
 'conditional_write':('','subject := 7;when CHANGE{subject=9};if subject!=(9 when CHANGE else 7){panic("conditional")}', ['subject'],True,True),
 'conditional_slice_write':('','subject := []int{7};when CHANGE{subject[0]=9};if subject[0]!=(9 when CHANGE else 7){panic("conditional slice")}', ['subject'],True,True),
 'conditional_reader':('inspect::proc(values:[]int)->int{when CHANGE{return values[0]+1}else{return values[0]}}', 'subject := []int{7};if inspect(subject)!=(8 when CHANGE else 7){panic("conditional reader")}', ['subject'],True,False),
 'constant_dead_writer':('NEVER::false\ninspect::proc(values:[]int){when NEVER{values[0]=9}}', 'subject := []int{7};inspect(subject);if subject[0]!=7{panic("constant branch")}', ['subject'],True,False),
 'unrelated_conditional_call':('noise::proc()->int{when CHANGE{return 1}else{return 0}}', 'subject := []int{7};if read(subject)+noise()!=(8 when CHANGE else 7){panic("unrelated call")}', ['subject'],True,False),
 'transitive_conditional_writer':('modify::proc(values:[]int){when CHANGE{values[0]=9}}\nforward::proc(values:[]int){modify(values)}', 'subject := []int{7};forward(subject);if subject[0]!=(9 when CHANGE else 7){panic("transitive conditional write")}', ['subject'],True,True),
 'conditional_switch_alias':('route::proc(values,other:[]int)->[]int{return #switch{case CHANGE:values;case:other}}', 'subject := []int{7};other:=make([]int,1);defer delete(other);alias:=route(subject,other);alias[0]=9;if subject[0]!=(9 when CHANGE else 7){panic("conditional alias")}', ['subject'],True,True),
 'conditional_selected_switch_alias':('route::proc(values,other:[]int)->[]int{return #switch CHANGE{case true:values;case:other}}', 'subject := []int{7};other:=make([]int,1);defer delete(other);alias:=route(subject,other);alias[0]=9;if subject[0]!=(9 when CHANGE else 7){panic("selected alias")}', ['subject'],True,True),
 'constant_dead_switch_alias':('route::proc(values,other:[]int)->[]int{return #switch{case false:values;case:other}}', 'subject := []int{7};other:=make([]int,1);defer delete(other);alias:=route(subject,other);alias[0]=9;if subject[0]!=7{panic("dead alias")}', ['subject'],True,False),
 'conditional_opaque_call':('sink::proc(args:..any){_ = args}', 'subject := []int{7};companion := []int{8};when CHANGE{sink(subject,companion)};if subject[0]+companion[0]!=15{panic("conditional call")}', ['subject','companion'],True,True),
 'generic_conditional_reader':('work::proc(tag:$T){subject := 7;when T==int{subject=9};if subject!=7{panic("generic reader")}}', 'work(f64(1))', ['subject'],True,True),
 'generic_conditional_writer':('work::proc(tag:$T){subject := 7;when T==int{subject=9};if subject!=9{panic("generic writer")}}', 'work(1)', ['subject'],False,False),
 'escaping_result':('create::proc()->[]int{subject := make([]int,1);return subject}', 'view:>create();defer delete(view);if len(view)!=1{panic("returned view")}', ['subject'],True,True),
 'escaping_output':('create::proc(out:^Row){subject := make([]int,1);out^=Row{subject}}', 'row:Row;create(&row);defer delete(row.items);if len(row.items)!=1{panic("output view")}', ['subject'],True,True),
 'escaping_output_alias':('create::proc(out:^Row){destination:=out;subject := make([]int,1);destination^=Row{subject}}', 'row:Row;create(&row);defer delete(row.items);if len(row.items)!=1{panic("output alias")}', ['subject'],True,True),
 'escaping_output_helper':('store::proc(out:^Row,values:[]int){out^=Row{values}}\ncreate::proc(out:^Row){subject := make([]int,1);store(out,subject)}', 'row:Row;create(&row);defer delete(row.items);if len(row.items)!=1{panic("output helper")}', ['subject'],True,True),
 'escaping_deferred_output':('create::proc(out:^Row){subject := make([]int,1);defer out^=Row{subject}}', 'row:Row;create(&row);defer delete(row.items);if len(row.items)!=1{panic("deferred output")}', ['subject'],True,True),
 'escaping_union_variant':('Choice::union{Hand,int}\ncreate::proc(out:^Row){defer out.items=nil;subject := Choice(make_hand());switch &item in subject{case Hand:out.items=item[:];case int:};if len(out.items)!=2{panic("union view")}}', 'row:Row;create(&row);if row.items!=nil{panic("clear union view")}', ['subject'],True,True),
 'copied_union_variant':('Choice::union{Hand,int}\ncreate::proc(out:^Row){defer out.items=nil;subject := Choice(make_hand());copy:=subject;switch &item in copy{case Hand:out.items=item[:];case int:};if len(out.items)!=2{panic("union copy")}}', 'row:Row;create(&row);if row.items!=nil{panic("clear copy view")}', ['subject'],True,False),
}


class ImmutableOracleTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=fixtures.RustVetTests.setUp
    write=fixtures.RustVetTests.write
    invoke=fixtures.RustVetTests.invoke
    findings=fixtures.RustVetTests.findings

    def test_generic_shared_when_writes_do_not_probe_loop_induction_variables(self):
        self.write('''package main
work::proc(tag:$T){
 when T==int{
  total:=0
  for index:=0;index<4;index+=1{total+=index}
  if total!=6{panic("loop result")}
  read_only:=7
  if read_only!=7{panic("reader")}
 }else{
  count:=0
  for index:=0;index<3;index+=1{count+=index}
  if count!=3{panic("other branch")}
 }
}
main::proc(){work(1);work(f64(2))}
''')
        profile=self.root/'shared-when-profile.json'
        result=subprocess.run([str(native.RUST),'vet',str(self.app),'--apply','--vet=immutable','--comptime=required',
            '--output-dir',str(self.output)],capture_output=True,text=True,cwd=self.root,
            env={**os.environ,'THOR_PROFILE':str(profile)},timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(profile.read_text())['counters'].get('vet.validation_checks'),1)
        text=self.source.read_text()
        self.assertIn('read_only:>7',text)
        for name in ['total','count','index']:
            self.assertNotIn(name+':>',text)
        for flags in ([],native.UNCHECKED):
            built=self.invoke(*flags,command='run')
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)

    def test_testing_parameter_and_sdk_conditions_do_not_block_readonly_locals(self):
        self.write('''package main
import "core:testing"
import "core:math"
@(test)
check::proc(t:^testing.T){
    deck := [2]int{7,8}
    cases := []int{1,2}
    testing.expect(t,math.abs(deck[0])+len(cases)==9)
    if read(deck[:])+cases[1]!=9{panic("reader")}
}
read::proc(values:[]int)->int{return values[0]}
main::proc(){}
''')
        findings=self.findings('--vet=immutable')
        selected=[e for e in findings if e['msgs'][0] in [
            "Declare 'deck' immutable with ':>'.","Declare 'cases' immutable with ':>'."]]
        self.assertEqual(len(selected),2,findings)
        self.assertTrue(all('fix' in e and not e.get('manual_review') for e in selected),selected)
        self.invoke('--vet=immutable','--apply')
        self.assertIn('deck :>',self.source.read_text())
        self.assertIn('cases :>',self.source.read_text())
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='test')

    def test_target_specific_reader_is_only_a_barrier_for_values_passed_to_it(self):
        self.write('''package main
read::proc(values:[]int)->int{return values[0]}
main::proc(){
    exposed := []int{7}
    independent := []int{9}
    platform_read(exposed)
    if read(independent)!=9 || exposed[0]!=7{panic("platform")}
}
''')
        active='windows' if os.name=='nt' else '!windows'
        inactive='!windows' if os.name=='nt' else 'windows'
        (self.app/'selected.thor').write_text('#+build '+active+'\npackage main\nplatform_read::proc(values:[]int){_ = values[0]}\n')
        (self.app/'other.thor').write_text('#+build '+inactive+'\npackage main\nplatform_read::proc(values:[]int){values[0]=9}\n')
        findings=self.findings('--vet=immutable')
        exposed=next(e for e in findings if "'exposed'" in e['msgs'][0])
        independent=next(e for e in findings if "'independent'" in e['msgs'][0])
        self.assertTrue(exposed.get('manual_review'),exposed)
        self.assertIn('fix',independent)
        self.invoke('--vet=immutable','--apply')
        text=self.source.read_text()
        self.assertIn('exposed :=',text)
        self.assertIn('independent :>',text)
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='run')

    def test_manual_warnings_remain_after_safe_cleanup_and_apply_is_idempotent(self):
        text='''// 😀
package main
import "core:math"
create::proc()->[]int{
    subject := make([]int,1)
    return subject
}
main::proc(){
    view := create()
    defer delete(view)
    count := len(view);
    if count!=1{panic("length")}
}
'''
        self.write(text)
        result=self.invoke('--apply','-json-errors')
        errors=json.loads(result.stderr)['errors']
        updated=self.source.read_text()
        self.assertNotIn('import "core:math"',updated)
        self.assertIn('subject :=',updated)
        self.assertIn('view :>',updated)
        self.assertIn('count :> len(view)\n',updated)
        manual=[error for error in errors if error.get('manual_review')]
        self.assertEqual(len(manual),1,errors)
        self.assertNotIn('fix',manual[0])
        self.assertEqual(manual[0]['pos']['offset'],updated.encode().index(b'subject :='))
        self.invoke('--apply')
        self.assertEqual(self.source.read_text(),updated)
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='run')

    def profiled(self,text):
        self.write(text)
        profile=self.root/'profile.json'
        result=subprocess.run([str(native.RUST),'vet',str(self.app),'--vet=immutable',
            '-json-errors','--output-dir',str(self.output)],cwd=self.root,
            env={**os.environ,'THOR_PROFILE':str(profile)},capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return json.loads(result.stderr)['errors'],json.loads(profile.read_text())['counters']

    def test_large_manual_batch_uses_one_validation(self):
        values='\n'.join(f'v_{i}:=one({i});total+=v_{i}[0]' for i in range(80))
        text=('package main\none::proc(x:int)->[]int{items:=make([]int,1);items[0]=x;return items}\n'
            'create::proc()->(result:[80][]int){total:=0;\n'+values+
            '\nif total!=3160{panic("sum")};result=[80][]int{'+','.join(f'v_{i}' for i in range(80))+
            '};return\n}\nmain::proc(){views:>create();for view in views{delete(view)};if len(views)!=80{panic("views")}}\n')
        findings,counters=self.profiled(text)
        self.assertEqual(len(findings),80,findings)
        self.assertTrue(all(error.get('manual_review') and 'fix' not in error for error in findings))
        self.assertEqual(counters.get('vet.validation_checks'),1,counters)
        self.assertEqual(counters.get('vet.source_snapshots',0),0,counters)
        self.assertEqual(counters.get('vet.safety_roots'),1,counters)
        self.assertEqual(counters.get('vet.safety_seeds'),80,counters)
        self.invoke('--vet=immutable','--apply')
        self.assertEqual(self.source.read_text(),text)
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='run')

    def test_generic_when_scalar_writes_reject_the_entire_batch_once(self):
        count=24
        body='\n'.join(f'counter_{i}:=uint(0);when T==f32{{counter_{i}+=1}};total+=counter_{i}' for i in range(count))
        text='package main\ncompute::proc($T:typeid)->uint{total:=uint(0);\n'+body+'\nreturn total}\nmain::proc(){if compute(f32)!=24{panic("sum")}}\n'
        findings,counters=self.profiled(text)
        self.assertEqual(findings,[])
        self.assertEqual(counters.get('vet.validation_checks'),1,counters)
        self.assertEqual(counters.get('vet.readonly_corrections_rejected'),count,counters)
        self.invoke('--vet=immutable','--apply')
        self.assertEqual(self.source.read_text(),text)
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='run')

    def test_inconclusive_validation_never_publishes_or_applies_a_correction(self):
        text='package main\nmain::proc(){value:=7;if value!=7{panic("value")}}\n'
        self.write(text)
        result=subprocess.run([str(native.RUST),'vet',str(self.app),'--vet=immutable',
            '--apply','-json-errors'],env={**os.environ,'THOR_VET_VALIDATION_BUDGET':'0'},
            capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stderr)
        findings=json.loads(result.stderr)['errors']
        unknown=[item for item in findings if item.get('code')=='thor/correction-unknown']
        self.assertEqual(len(unknown),1,findings)
        self.assertIn("'value'",unknown[0]['msgs'][0])
        self.assertIn('Could not verify',unknown[0]['msgs'][0])
        self.assertTrue(unknown[0].get('manual_review'))
        self.assertNotIn('fix',unknown[0])
        self.assertEqual(self.source.read_text(),text)
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='run')

    def test_invalid_alias_candidates_are_rejected_together_in_large_batches(self):
        body='\n'.join(f'source_{i}:=[]int{{{i}}};alias_{i}:=source_{i};alias_{i}[0]+=1;'
            f'value_{i}:=alias_{i}[0];total+=value_{i}' for i in range(80))
        text='package main\nmain::proc(){total:=0;\n'+body+'\nif total!=3240{panic("sum")}}\n'
        findings,counters=self.profiled(text)
        self.assertEqual(len(findings),80,findings)
        self.assertTrue(all("Declare 'value_" in error['msgs'][0] for error in findings),findings)
        self.assertLessEqual(counters.get('vet.validation_checks',0),3,counters)
        self.assertEqual(counters.get('vet.source_snapshots'),1,counters)
        self.invoke('--vet=immutable','--apply')
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='run')

    def test_multiple_readonly_roots_at_one_call_are_rejected_in_one_batch(self):
        count=24
        body='\n'.join(f'view_{i}:=[]int{{{i}}}' for i in range(count))
        arguments=','.join(f'view_{i}' for i in range(count))
        text=('package main\nsink::proc(args:..any){_ = args}\nmain::proc(){\n'+body+
            '\nsink('+arguments+');value:=7;if value!=7{panic("value")}}\n')
        self.write(text)
        self.invoke(command='run')
        for _ in range(2):
            findings,counters=self.profiled(text)
            self.assertEqual([error['msgs'][0] for error in findings],
                ["Declare 'value' immutable with ':>'."])
            self.assertLessEqual(counters.get('vet.validation_checks',0),2,counters)
            self.assertEqual(counters.get('vet.readonly_corrections_rejected'),count,counters)
        readonly=text.replace('view_0:=','view_0:>')
        self.write(readonly)
        rejected=self.invoke('-json-errors',command='check',status=1)
        errors=json.loads(rejected.stderr)['errors']
        self.assertTrue(any('opaque parameter' in error['msgs'][0] for error in errors),errors)
        self.write(text)
        self.invoke('--vet=immutable','--apply')
        for flags in [[],native.UNCHECKED]:self.invoke(*flags,command='run')


def test_case(label,case):
    def run(self):
        declarations,body,names,allowed,manual=case
        original=PREFIX+declarations+'\nmain::proc(){'+body+'}\n'
        expected=original
        offsets=[]
        for name in names:
            marker=name+' :='
            offsets.append(original.index(marker))
            expected=expected.replace(marker,name+' :>')
        self.write(original)
        self.invoke(command='check')
        self.invoke(command='run')
        self.write(expected)
        self.invoke(command='check',status=0 if allowed else 1)
        self.write(original)
        findings=self.findings('--vet=immutable')
        target=[e for e in findings if e.get('code')=='thor/prefer-immutable' and e['pos']['offset'] in offsets]
        self.assertEqual(len(target),len(names) if allowed else 0,findings)
        for warning in target:
            self.assertEqual(bool(warning.get('manual_review')),manual,warning)
            self.assertEqual('fix' in warning,not manual and not warning.get('batch_only'),warning)
        self.invoke('--vet=immutable','--apply')
        updated=self.source.read_text()
        for name in names:
            self.assertIn(name+(' :>' if allowed and not manual else ' :='),updated)
        for flags in [[],native.UNCHECKED]:
            self.invoke(*flags,command='run')
        if 'conditional' in label:
            self.invoke(*native.UNCHECKED,'-define:CHANGE=true',command='run')
    return run


for name,case in CASES.items():
    setattr(ImmutableOracleTests,'test_'+name,test_case(name,case))
